package com.decoy.core

import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Detects whether the "decoy" MCP server registered in .mcp.json is
 * sitting in Claude Code's "Pending approval" state -- mirrors
 * vscode-extension/src/mcpApproval.ts exactly, including the real
 * captured CLI output both parsers match against (see that file's
 * module docstring for exactly how `claude mcp get <name>` was run and
 * what it printed for each case). Re-verifying those exact strings here
 * would just re-run the same real CLI calls already captured once on the
 * VS Code side; this Kotlin parser is tested against the SAME captured
 * strings rather than re-deriving them, since the underlying `claude`
 * CLI's output format is shared, not IDE-specific.
 */

enum class McpApprovalStatus { PENDING, NOT_CONFIGURED, APPROVED, CHECK_FAILED }

data class McpApprovalResult(val status: McpApprovalStatus, val raw: String)

private const val PENDING_MARKER = "Pending approval"
private const val NOT_CONFIGURED_MARKER = "No MCP server named"

/** Pure parser -- unit-testable without shelling out. */
fun parseMcpGetOutput(stdout: String, exitCode: Int): McpApprovalStatus {
    if (stdout.contains(PENDING_MARKER)) return McpApprovalStatus.PENDING
    if (exitCode != 0 && stdout.contains(NOT_CONFIGURED_MARKER)) return McpApprovalStatus.NOT_CONFIGURED
    if (exitCode != 0) return McpApprovalStatus.CHECK_FAILED
    return McpApprovalStatus.APPROVED
}

/** Runs `claude mcp get <serverName>` in `cwd` and classifies the
 * result. Never throws -- a missing `claude` binary, a timeout, or any
 * other failure to run the check at all comes back as CHECK_FAILED with
 * the error in `raw`. */
fun checkMcpApproval(serverName: String, cwd: File, timeoutSeconds: Long = 10): McpApprovalResult {
    return try {
        val process = ProcessBuilder("claude", "mcp", "get", serverName)
            .directory(cwd)
            .redirectErrorStream(true)
            .start()
        val finished = process.waitFor(timeoutSeconds, TimeUnit.SECONDS)
        if (!finished) {
            process.destroyForcibly()
            return McpApprovalResult(McpApprovalStatus.CHECK_FAILED, "claude mcp get timed out after ${timeoutSeconds}s")
        }
        val output = process.inputStream.bufferedReader().readText()
        McpApprovalResult(parseMcpGetOutput(output, process.exitValue()), output.trim())
    } catch (exc: java.io.IOException) {
        McpApprovalResult(McpApprovalStatus.CHECK_FAILED, "could not run the `claude` CLI: ${exc.message}")
    }
}

/** The exact, concrete next step for the tool window to display --
 * naming the fix, not just the problem, matching the VS Code side's
 * approvalActionMessage() word for word. */
fun approvalActionMessage(serverName: String): String =
    "Decoy's MCP server (\"$serverName\") is registered but not connected yet. " +
        "Run \"claude\" interactively in this project's directory and approve it when prompted -- " +
        "Claude Code will not use it until you do."
