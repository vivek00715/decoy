# Decoy audit log — on-disk format (v1)

This is the plain JSON structure **after decryption** of `.decoy/audit.enc`
(Fernet-encrypted at rest, same local key as the vault — see
`decoy/crypto.py`). Both the VS Code extension (Phase 8) and the IntelliJ
plugin (Phase 9) read this same decrypted structure directly, so it uses
only primitive JSON types: strings, numbers, booleans, arrays, objects.
No tuples, no language-specific date objects, no enums serialized as
anything but plain strings.

## Top-level shape

```json
{
  "format_version": 1,
  "entries": [ /* array of entry objects, oldest first */ ]
}
```

- `format_version` (number): bumped only if the entry shape changes in a
  way old readers can't handle. See "Versioning" below.
- `entries` (array of objects): flat, no nesting. Every entry has exactly
  the same 9 fields, all primitives.

## Entry object

| Field        | Type   | Notes |
|--------------|--------|-------|
| `id`         | string | UUID v4, unique per entry. |
| `request_id` | string | UUID v4, shared by every entry produced by one logical request (one `ask_over_data()` call, one MCP tool call). Lets the UI group field-level rows under "recent requests". |
| `session_id` | string | The masking session this decision belongs to. |
| `timestamp`  | string | ISO-8601, UTC, millisecond precision, `Z` suffix — e.g. `"2026-08-30T21:15:03.482Z"`. Parses directly with `new Date(s)` in TypeScript and `Instant.parse(s)` in Kotlin; no custom parsing needed. |
| `source`     | string | One of `"prompt_text"`, `"db_record"`, `"mcp_tool_result"` — which of Decoy's three entry points produced this decision. |
| `field`      | string | A DB/record column name for `db_record`/`mcp_tool_result`; a detection label (e.g. `"EMAIL"`, `"PHONE"`, `"PNR"`) for `prompt_text`, since free text has no column name — see note below. |
| `decision`   | string | One of `"masked"`, `"redacted"`, `"left_as_is"`. |
| `reason`     | string | Human-readable explanation, e.g. `"matched EMAIL regex"`, `"manual override: always_mask"`, `"boolean column: kept as real value..."`. |
| `layer`      | string | Which detection layer decided this, e.g. `"regex"`, `"override"`, `"ner"`, `"shape"`, `"keyword"`, `"llm"`, or a combined layer like `"shape-default+keyword"` when two layers jointly produced the outcome. |

**Never present:** the real value, the fake value, or anything derived
from them. Only metadata about the decision.

## Concrete example

Three entries from one request: an email masked via regex, a field
force-masked via a manual override, and a field redacted because it was
judged irrelevant to the question:

```json
{
  "format_version": 1,
  "entries": [
    {
      "id": "6e2f9e2a-2b31-4e2a-9a2e-2f6a5e6a1a11",
      "request_id": "b1a6a0d2-2222-4b11-9c9c-3b7e2a1a0c01",
      "session_id": "demo-session-123",
      "timestamp": "2026-08-30T21:15:03.482Z",
      "source": "prompt_text",
      "field": "EMAIL",
      "decision": "masked",
      "reason": "matched EMAIL regex",
      "layer": "regex"
    },
    {
      "id": "6e2f9e2a-2b31-4e2a-9a2e-2f6a5e6a1a12",
      "request_id": "b1a6a0d2-2222-4b11-9c9c-3b7e2a1a0c01",
      "session_id": "demo-session-123",
      "timestamp": "2026-08-30T21:15:03.483Z",
      "source": "db_record",
      "field": "employee_id",
      "decision": "masked",
      "reason": "manual override: always_mask",
      "layer": "override"
    },
    {
      "id": "6e2f9e2a-2b31-4e2a-9a2e-2f6a5e6a1a13",
      "request_id": "b1a6a0d2-2222-4b11-9c9c-3b7e2a1a0c01",
      "session_id": "demo-session-123",
      "timestamp": "2026-08-30T21:15:03.484Z",
      "source": "db_record",
      "field": "notes",
      "decision": "redacted",
      "reason": "masked-by-default field also classified not relevant to the current question (no keyword overlap...); flatly redacted rather than given a realistic fake",
      "layer": "shape-default+keyword"
    }
  ]
}
```

## Reading it from TypeScript

```ts
type AuditEntry = {
  id: string; request_id: string; session_id: string; timestamp: string;
  source: "prompt_text" | "db_record" | "mcp_tool_result";
  field: string; decision: "masked" | "redacted" | "left_as_is";
  reason: string; layer: string;
};
type AuditLogFile = { format_version: number; entries: AuditEntry[] };

const data: AuditLogFile = JSON.parse(decryptedText);
const when = new Date(data.entries[0].timestamp); // works directly
```

## Reading it from Kotlin (kotlinx.serialization)

```kotlin
@Serializable
data class AuditEntry(
    val id: String, val request_id: String, val session_id: String,
    val timestamp: String, val source: String, val field: String,
    val decision: String, val reason: String, val layer: String,
)
@Serializable
data class AuditLogFile(val format_version: Int, val entries: List<AuditEntry>)

val data = Json.decodeFromString<AuditLogFile>(decryptedText)
val when = Instant.parse(data.entries[0].timestamp) // works directly
```

## Versioning / migration story

`format_version` starts at 1. If a future Decoy release needs to change
the entry shape (add a required field, rename one, change a type), it
bumps `format_version` and ships a migration in `audit_log.py` that
rewrites existing files to the new shape on next load. A reader (IDE
extension) encountering a `format_version` it doesn't recognize should
show a clear "audit log format not supported by this version of the
extension, please update" message rather than guessing at the shape --
this is a deliberate compatibility gate, not an oversight to fix later.

## Note on `field` for free-text (`prompt_text`) entries

Free text has no enumerable set of "columns" the way a DB record does --
you can't list "every field that could have appeared and wasn't masked,"
only what was actually detected (see `masker.py`'s design notes on why
overrides work differently for free text vs. structured data). So a
`prompt_text` entry only exists for things that WERE masked, and `field`
holds the detection label (`"EMAIL"`, `"PHONE"`, `"SSN"`, `"PNR"`, etc.)
rather than a column name. `db_record`/`mcp_tool_result` entries, by
contrast, exist for every column examined, including ones left real
(`decision: "left_as_is"`), since DB columns are enumerable and a missed
one would be a silent, discoverable gap in the audit trail.
