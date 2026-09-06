import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import { execFileSync } from "child_process";

import { checkMcpApproval, parseMcpGetOutput } from "./mcpApproval";

// Exact strings captured from a real `claude` CLI install -- see
// mcpApproval.ts's module docstring for how each was produced.
const REAL_PENDING_OUTPUT = `decoy:
  Scope: Project config (shared via .mcp.json)
  Status: ⏸ Pending approval (run \`claude\` to approve)
  Type: stdio
  Command: echo
  Args: hello
  Environment:
`;

const REAL_NOT_CONFIGURED_OUTPUT =
  'No MCP server named "nonexistent-server-xyz". .mcp.json servers are awaiting approval — run `claude` in this directory to review them.';

// The OTHER real "not found" message -- when no .mcp.json exists at all
// yet, rather than one existing with other unapproved entries. Both must
// classify as not_configured; confirmed these are worded differently
// enough that a marker matching only the first one would miss this case
// (verified directly: this exact gap failed once before the parser's
// marker was broadened from "awaiting approval" to "No MCP server named").
const REAL_NOT_CONFIGURED_NO_FILE_OUTPUT = 'No MCP server named "totally-unregistered-server". Run `claude mcp add` to add one.';

describe("parseMcpGetOutput (against real captured CLI output)", () => {
  it("detects Pending approval from a real `claude mcp get` response", () => {
    expect(parseMcpGetOutput(REAL_PENDING_OUTPUT, 0)).toBe("pending");
  });

  it("detects the not-configured case from a real error response (exit 1)", () => {
    expect(parseMcpGetOutput(REAL_NOT_CONFIGURED_OUTPUT, 1)).toBe("not_configured");
  });

  it("detects the OTHER real not-configured wording (no .mcp.json at all yet)", () => {
    expect(parseMcpGetOutput(REAL_NOT_CONFIGURED_NO_FILE_OUTPUT, 1)).toBe("not_configured");
  });

  it("treats an unrecognized non-zero exit as check_failed, not a guess", () => {
    expect(parseMcpGetOutput("some unexpected error we've never seen", 127)).toBe("check_failed");
  });

  it("treats exit 0 with neither marker as approved", () => {
    expect(parseMcpGetOutput("decoy:\n  Status: ✓ Connected\n", 0)).toBe("approved");
  });
});

describe("checkMcpApproval end-to-end against the REAL claude CLI", () => {
  const claudeAvailable = (() => {
    try {
      execFileSync("claude", ["--version"], { timeout: 5000 });
      return true;
    } catch {
      return false;
    }
  })();

  const maybeIt = claudeAvailable ? it : it.skip;

  maybeIt("reports 'pending' for a real, just-added, unapproved .mcp.json entry", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "decoy-mcp-approval-test-"));
    try {
      execFileSync("git", ["init", "-q"], { cwd: dir });
      execFileSync("claude", ["mcp", "add", "--scope", "project", "decoy", "--", "echo", "hello"], {
        cwd: dir,
      });

      const result = await checkMcpApproval("decoy", dir);
      expect(result.status).toBe("pending");
      expect(result.raw).toContain("Pending approval");
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }, 20000);

  maybeIt("reports 'not_configured' for a server name that was never registered", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "decoy-mcp-approval-test-"));
    try {
      execFileSync("git", ["init", "-q"], { cwd: dir });
      const result = await checkMcpApproval("totally-unregistered-server", dir);
      expect(result.status).toBe("not_configured");
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }, 20000);
});
