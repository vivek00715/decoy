// Mirrors AUDIT_LOG_FORMAT.md exactly -- keep these two in sync by hand;
// there is no shared schema generator across the Python/TS/Kotlin sides.

export type AuditSource = "prompt_text" | "db_record" | "mcp_tool_result";
export type AuditDecision = "masked" | "redacted" | "left_as_is";

export interface AuditEntry {
  id: string;
  request_id: string;
  session_id: string;
  timestamp: string; // ISO-8601 UTC, e.g. "2026-08-30T21:15:03.482Z"
  source: AuditSource;
  field: string;
  decision: AuditDecision;
  reason: string;
  layer: string;
}

export interface AuditLogFile {
  format_version: number;
  entries: AuditEntry[];
}

export const SUPPORTED_FORMAT_VERSION = 1;

export interface OverrideRules {
  patterns: string[];
  field_names: string[];
}

export interface OverridesFile {
  always_mask: OverrideRules;
  never_mask: OverrideRules;
}

export function emptyOverridesFile(): OverridesFile {
  return {
    always_mask: { patterns: [], field_names: [] },
    never_mask: { patterns: [], field_names: [] },
  };
}

// One "request" as grouped for the trace panel: every AuditEntry sharing
// a request_id, in the order they were logged.
export interface RequestGroup {
  request_id: string;
  session_id: string;
  timestamp: string; // timestamp of the first entry in the group
  entries: AuditEntry[];
}
