package com.decoy.core

import kotlinx.serialization.Serializable

// Mirrors AUDIT_LOG_FORMAT.md exactly (same schema the VS Code extension's
// types.ts uses) -- keep these three files in sync by hand; there is no
// shared schema generator across the Python/TS/Kotlin sides.

@Serializable
data class AuditEntry(
    val id: String,
    val request_id: String,
    val session_id: String,
    val timestamp: String, // ISO-8601 UTC, e.g. "2026-08-30T21:15:03.482Z"
    val source: String, // "prompt_text" | "db_record" | "mcp_tool_result"
    val field: String,
    val decision: String, // "masked" | "redacted" | "left_as_is"
    val reason: String,
    val layer: String,
)

@Serializable
data class AuditLogFile(
    val format_version: Int,
    val entries: List<AuditEntry> = emptyList(),
)

const val SUPPORTED_FORMAT_VERSION = 1

@Serializable
data class OverrideRules(
    val patterns: List<String> = emptyList(),
    val field_names: List<String> = emptyList(),
)

@Serializable
data class OverridesFile(
    val always_mask: OverrideRules = OverrideRules(),
    val never_mask: OverrideRules = OverrideRules(),
)

fun emptyOverridesFile(): OverridesFile = OverridesFile()

/** One "request" as grouped for the tool window: every AuditEntry sharing
 * a request_id, in the order they were logged. */
data class RequestGroup(
    val requestId: String,
    val sessionId: String,
    val timestamp: String,
    val entries: List<AuditEntry>,
)
