import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import { clearVaultAll, clearVaultSession } from "./vault";

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "decoy-vault-test-"));
}

const ENV = { DECOY_ENCRYPTION_KEY: "nhtXQTnXQzKW_0m_5r-ffK8dcl03BLSzJOOdmF8bDk4=" } as NodeJS.ProcessEnv;

// eslint-disable-next-line @typescript-eslint/no-require-imports
import fernet = require("fernet");

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

describe("clearVaultSession", () => {
  it("removes only the named session's mappings", () => {
    const root = makeTmpDir();
    writeVaultFixture(root, {
      "session-a": { "alice@example.com": "fake-a@example.net" },
      "session-b": { "bob@example.com": "fake-b@example.net" },
    });

    clearVaultSession(root, "session-a", ENV);

    const result = readVaultFixture(root);
    expect(result).toEqual({ "session-b": { "bob@example.com": "fake-b@example.net" } });
  });

  it("does nothing if no vault file exists (not an error)", () => {
    const root = makeTmpDir();
    expect(() => clearVaultSession(root, "session-a", ENV)).not.toThrow();
    expect(fs.existsSync(path.join(root, ".decoy", "vault.enc"))).toBe(false);
  });
});

describe("clearVaultAll", () => {
  it("empties every session's mappings but keeps the file (matches Python's clear_all semantics)", () => {
    const root = makeTmpDir();
    writeVaultFixture(root, { "session-a": { "x@example.com": "y@example.net" } });

    clearVaultAll(root, ENV);

    expect(fs.existsSync(path.join(root, ".decoy", "vault.enc"))).toBe(true);
    expect(readVaultFixture(root)).toEqual({});
  });
});
