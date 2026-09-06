import { execFile } from "child_process";

/**
 * Detects whether the "decoy" MCP server registered in .mcp.json is
 * sitting in Claude Code's "Pending approval" state -- the exact silent
 * failure mode this module exists to catch, described precisely by the
 * person who hit it: "nothing happens, no error", discoverable only by
 * re-reading old documentation. This surfaces it in the panel instead.
 *
 * Every string this module matches against was captured from a REAL
 * `claude` CLI install, not guessed from documentation:
 *
 *   $ claude mcp get decoy       (a real, just-added, unapproved entry)
 *   decoy:
 *     Scope: Project config (shared via .mcp.json)
 *     Status: ⏸ Pending approval (run `claude` to approve)
 *     ...
 *
 *   $ claude mcp get nonexistent-server-xyz    (a .mcp.json exists, with OTHER unapproved entries)
 *   No MCP server named "nonexistent-server-xyz". .mcp.json servers are
 *   awaiting approval — run `claude` in this directory to review them.
 *   (exit code 1)
 *
 *   $ claude mcp get nonexistent-server-xyz    (no .mcp.json exists at all)
 *   No MCP server named "nonexistent-server-xyz". Run `claude mcp add` to add one.
 *   (exit code 1)
 *
 * What is NOT independently confirmed here: the exact text for an
 * APPROVED server, healthy or unhealthy -- approving one requires
 * interactive TTY input this environment can't safely script. Anything
 * that matches neither pattern above is reported as "approved" (the
 * optimistic default -- if this module can't positively identify a
 * problem, it says nothing, rather than guessing at unverified failure
 * text and crying wolf).
 */

export type McpApprovalStatus = "pending" | "not_configured" | "approved" | "check_failed";

export interface McpApprovalResult {
  status: McpApprovalStatus;
  /** Raw stdout+stderr, kept for a "show details" affordance / bug reports. */
  raw: string;
}

const PENDING_MARKER = "Pending approval";
// Both real "not registered" messages share this prefix regardless of
// whether .mcp.json exists at all yet -- confirmed directly, see above.
const NOT_CONFIGURED_MARKER = "No MCP server named";

/** Pure parser -- the part that's actually risky to get wrong, kept
 * separate from the child_process call so it's unit-testable against
 * the exact real captured strings above without shelling out. */
export function parseMcpGetOutput(stdout: string, exitCode: number): McpApprovalStatus {
  if (stdout.includes(PENDING_MARKER)) {
    return "pending";
  }
  if (exitCode !== 0 && stdout.includes(NOT_CONFIGURED_MARKER)) {
    return "not_configured";
  }
  if (exitCode !== 0) {
    return "check_failed";
  }
  return "approved";
}

/**
 * Runs `claude mcp get <serverName>` in `cwd` and classifies the result.
 * Never throws -- a missing `claude` binary, a timeout, or any other
 * failure to run the check at all comes back as "check_failed" with the
 * error message in `raw`, since "we couldn't check" must never be
 * confused with "it's pending" or "it's fine" in the panel.
 */
export function checkMcpApproval(
  serverName: string,
  cwd: string,
  execFileFn: typeof execFile = execFile,
  timeoutMs = 10000,
): Promise<McpApprovalResult> {
  return new Promise((resolve) => {
    execFileFn("claude", ["mcp", "get", serverName], { cwd, timeout: timeoutMs }, (error, stdout, stderr) => {
      const combined = `${stdout ?? ""}${stderr ?? ""}`;
      if (error && typeof (error as NodeJS.ErrnoException).code === "string" && (error as NodeJS.ErrnoException).code === "ENOENT") {
        resolve({ status: "check_failed", raw: "the `claude` CLI was not found on PATH" });
        return;
      }
      const exitCode = error && typeof error.code === "number" ? error.code : error ? 1 : 0;
      resolve({ status: parseMcpGetOutput(combined, exitCode), raw: combined.trim() });
    });
  });
}

/** The exact, concrete next step for the panel to display -- naming the
 * fix, not just the problem, per this project's established standard for
 * this exact gap (see mcpConfigCommand.ts's confirmation dialog). */
export function approvalActionMessage(serverName: string): string {
  return (
    `Decoy's MCP server ("${serverName}") is registered but not connected yet. ` +
    `Run "claude" interactively in this project's directory and approve it when prompted -- ` +
    `Claude Code will not use it until you do.`
  );
}
