import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import { writeAuditLog } from "./auditLog";
// eslint-disable-next-line @typescript-eslint/no-require-imports
import fernet = require("fernet");
import { PanelController, PanelHost } from "./panelController";
import { readOverrides, writeOverrides } from "./overrides";

const ENV = { DECOY_ENCRYPTION_KEY: "nhtXQTnXQzKW_0m_5r-ffK8dcl03BLSzJOOdmF8bDk4=" } as NodeJS.ProcessEnv;

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "decoy-panel-test-"));
}

function makeFakeHost(rootDir: string, confirmResult = true): PanelHost & { htmlHistory: string[] } {
  const htmlHistory: string[] = [];
  return {
    rootDir,
    cspSource: "vscode-webview://test",
    codiconsUri: "vscode-webview://test/codicon.css",
    generateNonce: () => "fixed-nonce",
    postHtml: (html: string) => {
      htmlHistory.push(html);
    },
    confirmDestructive: async () => confirmResult,
    htmlHistory,
  };
}

function writeVaultFixture(root: string, data: Record<string, Record<string, string>>): void {
  fs.mkdirSync(path.join(root, ".decoy"), { recursive: true });
  const secret = new fernet.Secret(ENV.DECOY_ENCRYPTION_KEY as string);
  const token = new fernet.Token({ secret }).encode(JSON.stringify(data));
  fs.writeFileSync(path.join(root, ".decoy", "vault.enc"), token, "utf8");
}

function readVaultFixture(root: string): Record<string, Record<string, string>> {
  const raw = fs.readFileSync(path.join(root, ".decoy", "vault.enc"), "utf8");
  const secret = new fernet.Secret(ENV.DECOY_ENCRYPTION_KEY as string);
  const token = new fernet.Token({ secret, token: raw, ttl: 0 });
  return JSON.parse(token.decode() as string);
}

describe("PanelController.refresh", () => {
  it("posts HTML reflecting the current audit log and overrides", async () => {
    const root = makeTmpDir();
    writeAuditLog(
      root,
      {
        format_version: 1,
        entries: [
          {
            id: "e1",
            request_id: "req-1",
            session_id: "session-1",
            timestamp: "2026-08-30T21:15:03.482Z",
            source: "prompt_text",
            field: "EMAIL",
            decision: "masked",
            reason: "matched EMAIL regex",
            layer: "regex",
          },
        ],
      },
      ENV,
    );
    const host = makeFakeHost(root);
    process.env.DECOY_ENCRYPTION_KEY = ENV.DECOY_ENCRYPTION_KEY;
    try {
      new PanelController(host).refresh();
    } finally {
      delete process.env.DECOY_ENCRYPTION_KEY;
    }

    expect(host.htmlHistory).toHaveLength(1);
    expect(host.htmlHistory[0]).toContain("EMAIL");
  });
});

describe("PanelController.handleMessage", () => {
  it("clearSession clears both the audit log entry and the vault mapping for that session", async () => {
    const root = makeTmpDir();
    writeAuditLog(
      root,
      {
        format_version: 1,
        entries: [
          {
            id: "e1",
            request_id: "req-1",
            session_id: "session-a",
            timestamp: "2026-08-30T21:15:03.482Z",
            source: "prompt_text",
            field: "EMAIL",
            decision: "masked",
            reason: "matched EMAIL regex",
            layer: "regex",
          },
          {
            id: "e2",
            request_id: "req-2",
            session_id: "session-b",
            timestamp: "2026-08-30T21:16:00.000Z",
            source: "prompt_text",
            field: "PHONE",
            decision: "masked",
            reason: "matched PHONE regex",
            layer: "regex",
          },
        ],
      },
      ENV,
    );
    writeVaultFixture(root, {
      "session-a": { "alice@example.com": "fake-a@example.net" },
      "session-b": { "bob@example.com": "fake-b@example.net" },
    });

    const host = makeFakeHost(root);
    process.env.DECOY_ENCRYPTION_KEY = ENV.DECOY_ENCRYPTION_KEY;
    try {
      await new PanelController(host).handleMessage({ command: "clearSession", sessionId: "session-a" });
    } finally {
      delete process.env.DECOY_ENCRYPTION_KEY;
    }

    expect(readVaultFixture(root)).toEqual({ "session-b": { "bob@example.com": "fake-b@example.net" } });
    const lastHtml = host.htmlHistory[host.htmlHistory.length - 1];
    expect(lastHtml).toContain("PHONE");
    expect(lastHtml).not.toContain("session-a");
  });

  it("clearSessionDataOnly does nothing if the host declines confirmation", async () => {
    const root = makeTmpDir();
    writeAuditLog(
      root,
      { format_version: 1, entries: [{ id: "e1", request_id: "r1", session_id: "s1", timestamp: "t", source: "prompt_text", field: "EMAIL", decision: "masked", reason: "r", layer: "regex" }] },
      ENV,
    );
    const host = makeFakeHost(root, /* confirmResult */ false);
    process.env.DECOY_ENCRYPTION_KEY = ENV.DECOY_ENCRYPTION_KEY;
    try {
      await new PanelController(host).handleMessage({ command: "clearSessionDataOnly" });
    } finally {
      delete process.env.DECOY_ENCRYPTION_KEY;
    }

    // no refresh should have been posted at all since nothing changed
    expect(host.htmlHistory).toHaveLength(0);
  });

  it("clearSessionDataOnly removes audit/vault data for every session but KEEPS overrides", async () => {
    const root = makeTmpDir();
    writeAuditLog(
      root,
      { format_version: 1, entries: [{ id: "e1", request_id: "r1", session_id: "s1", timestamp: "t", source: "prompt_text", field: "EMAIL", decision: "masked", reason: "r", layer: "regex" }] },
      ENV,
    );
    writeVaultFixture(root, { s1: { "a@example.com": "b@example.net" } });
    const overrides = { always_mask: { patterns: [], field_names: ["employee_id"] }, never_mask: { patterns: [], field_names: [] } };
    writeOverrides(root, overrides);

    const host = makeFakeHost(root, true);
    process.env.DECOY_ENCRYPTION_KEY = ENV.DECOY_ENCRYPTION_KEY;
    try {
      await new PanelController(host).handleMessage({ command: "clearSessionDataOnly" });
    } finally {
      delete process.env.DECOY_ENCRYPTION_KEY;
    }

    expect(readVaultFixture(root)).toEqual({});
    expect(readOverrides(root)).toEqual(overrides); // kept, not cleared
    expect(host.htmlHistory[host.htmlHistory.length - 1]).toContain("No requests yet");
    expect(host.htmlHistory[host.htmlHistory.length - 1]).toContain("employee_id");
  });

  it("clearAll does nothing if the host declines confirmation", async () => {
    const root = makeTmpDir();
    writeAuditLog(
      root,
      { format_version: 1, entries: [{ id: "e1", request_id: "r1", session_id: "s1", timestamp: "t", source: "prompt_text", field: "EMAIL", decision: "masked", reason: "r", layer: "regex" }] },
      ENV,
    );
    const host = makeFakeHost(root, /* confirmResult */ false);
    process.env.DECOY_ENCRYPTION_KEY = ENV.DECOY_ENCRYPTION_KEY;
    try {
      await new PanelController(host).handleMessage({ command: "clearAll" });
    } finally {
      delete process.env.DECOY_ENCRYPTION_KEY;
    }

    // no refresh should have been posted at all since nothing changed
    expect(host.htmlHistory).toHaveLength(0);
  });

  it("clearAll removes EVERYTHING when confirmed, including overrides -- no unstated exception", async () => {
    const root = makeTmpDir();
    writeAuditLog(
      root,
      { format_version: 1, entries: [{ id: "e1", request_id: "r1", session_id: "s1", timestamp: "t", source: "prompt_text", field: "EMAIL", decision: "masked", reason: "r", layer: "regex" }] },
      ENV,
    );
    writeVaultFixture(root, { s1: { "a@example.com": "b@example.net" } });
    writeOverrides(root, {
      always_mask: { patterns: [], field_names: ["employee_id"] },
      never_mask: { patterns: [], field_names: ["status"] },
    });

    const host = makeFakeHost(root, true);
    process.env.DECOY_ENCRYPTION_KEY = ENV.DECOY_ENCRYPTION_KEY;
    try {
      await new PanelController(host).handleMessage({ command: "clearAll" });
    } finally {
      delete process.env.DECOY_ENCRYPTION_KEY;
    }

    expect(readVaultFixture(root)).toEqual({});
    expect(readOverrides(root)).toEqual({
      always_mask: { patterns: [], field_names: [] },
      never_mask: { patterns: [], field_names: [] },
    });
    const lastHtml = host.htmlHistory[host.htmlHistory.length - 1];
    expect(lastHtml).toContain("No requests yet");
    expect(lastHtml).not.toContain("employee_id");
    expect(lastHtml).not.toContain("status");
  });

  it("saveOverrides persists and is reflected in the next refresh", async () => {
    const root = makeTmpDir();
    const host = makeFakeHost(root);
    const overrides = {
      always_mask: { patterns: [], field_names: ["employee_id"] },
      never_mask: { patterns: [], field_names: [] },
    };
    await new PanelController(host).handleMessage({ command: "saveOverrides", overrides });

    expect(readOverrides(root)).toEqual(overrides);
    expect(host.htmlHistory[host.htmlHistory.length - 1]).toContain("employee_id");
  });
});
