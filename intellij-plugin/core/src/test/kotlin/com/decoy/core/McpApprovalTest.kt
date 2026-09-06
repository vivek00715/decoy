package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

// Exact strings captured from a real `claude` CLI install -- see
// mcpApproval.ts's (VS Code side) module docstring for how each was
// produced; this Kotlin parser matches the same real output, not a
// separately-guessed IntelliJ-specific format (the `claude` CLI's output
// doesn't depend on which IDE is asking).
private const val REAL_PENDING_OUTPUT = """decoy:
  Scope: Project config (shared via .mcp.json)
  Status: ⏸ Pending approval (run `claude` to approve)
  Type: stdio
  Command: echo
  Args: hello
  Environment:
"""

private const val REAL_NOT_CONFIGURED_OUTPUT =
    "No MCP server named \"nonexistent-server-xyz\". .mcp.json servers are awaiting approval — run `claude` in this directory to review them."

private const val REAL_NOT_CONFIGURED_NO_FILE_OUTPUT =
    "No MCP server named \"totally-unregistered-server\". Run `claude mcp add` to add one."

class McpApprovalTest {

    @Test
    fun `parseMcpGetOutput detects Pending approval from real captured output`() {
        assertEquals(McpApprovalStatus.PENDING, parseMcpGetOutput(REAL_PENDING_OUTPUT, 0))
    }

    @Test
    fun `parseMcpGetOutput detects not-configured from real captured output (mcp json exists)`() {
        assertEquals(McpApprovalStatus.NOT_CONFIGURED, parseMcpGetOutput(REAL_NOT_CONFIGURED_OUTPUT, 1))
    }

    @Test
    fun `parseMcpGetOutput detects not-configured from real captured output (no mcp json at all)`() {
        assertEquals(McpApprovalStatus.NOT_CONFIGURED, parseMcpGetOutput(REAL_NOT_CONFIGURED_NO_FILE_OUTPUT, 1))
    }

    @Test
    fun `parseMcpGetOutput treats an unrecognized non-zero exit as CHECK_FAILED, not a guess`() {
        assertEquals(McpApprovalStatus.CHECK_FAILED, parseMcpGetOutput("some unexpected error", 127))
    }

    @Test
    fun `parseMcpGetOutput treats exit 0 with neither marker as APPROVED`() {
        assertEquals(McpApprovalStatus.APPROVED, parseMcpGetOutput("decoy:\n  Status: Connected\n", 0))
    }

    private fun claudeAvailable(): Boolean = try {
        ProcessBuilder("claude", "--version").start().waitFor() == 0
    } catch (_: Exception) {
        false
    }

    @Test
    fun `checkMcpApproval end-to-end reports PENDING for a real just-added unapproved entry`(@TempDir dir: File) {
        org.junit.jupiter.api.Assumptions.assumeTrue(claudeAvailable(), "claude CLI not available in this environment")
        ProcessBuilder("git", "init", "-q").directory(dir).start().waitFor()
        val add = ProcessBuilder("claude", "mcp", "add", "--scope", "project", "decoy", "--", "echo", "hello")
            .directory(dir).start()
        add.waitFor()

        val result = checkMcpApproval("decoy", dir)
        assertEquals(McpApprovalStatus.PENDING, result.status)
        assertTrue(result.raw.contains("Pending approval"))
    }

    @Test
    fun `checkMcpApproval end-to-end reports NOT_CONFIGURED for an unregistered name`(@TempDir dir: File) {
        org.junit.jupiter.api.Assumptions.assumeTrue(claudeAvailable(), "claude CLI not available in this environment")
        ProcessBuilder("git", "init", "-q").directory(dir).start().waitFor()

        val result = checkMcpApproval("totally-unregistered-server", dir)
        assertEquals(McpApprovalStatus.NOT_CONFIGURED, result.status)
    }
}
