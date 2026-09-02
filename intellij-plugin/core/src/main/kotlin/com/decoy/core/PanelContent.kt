package com.decoy.core

/**
 * Builds the tool window's HTML content for a read-only Swing JEditorPane
 * (text/html). This is pure Kotlin with zero IntelliJ Platform SDK
 * dependency, deliberately kept in `core` (not `plugin`) so it's covered
 * by real unit tests here rather than living entirely in the untested
 * IntelliJ-dependent module -- the same rigor webviewContent.ts gets on
 * the VS Code side.
 *
 * Per-session "Clear this session" actions, and per-override-entry
 * "remove"/"edit" actions, are plain `<a href="...">` links using the
 * *_LINK_PREFIX constants below; the plugin module's HyperlinkListener
 * intercepts and dispatches them, since a read-only HTML pane can't embed
 * real Swing buttons per row.
 *
 * The remove/edit link value is encoded as `<prefix><kind.name>:<list.name>:<value>`
 * (kind/list from OverrideEdits.kt's OverrideKind/OverrideListKind enums,
 * e.g. "ALWAYS_MASK:FIELD_NAMES:employee_id"). A pattern override's value
 * can itself contain a literal ':' (e.g. a regex like `\d{2}:\d{2}`), so
 * the plugin-side decoder MUST split on ':' with a limit of 3 (kind,
 * list, then everything else as the value) -- an unlimited split would
 * truncate any such pattern at its first colon. This is only encoded
 * here, not decoded (decoding lives in the plugin module's
 * HyperlinkListener), but the constraint is documented at both ends since
 * it's an easy correctness bug to reintroduce later.
 *
 * Deliberately no absolute-privacy language anywhere in this copy -- see
 * the project's core design principles and Phase 11's forthcoming "what
 * this protects against, and what it doesn't" doc, matching the VS Code
 * extension's DISCLAIMER text in webviewContent.ts word-for-word.
 */
const val CLEAR_SESSION_LINK_PREFIX = "decoy-clear-session:"
const val REMOVE_OVERRIDE_LINK_PREFIX = "decoy-remove-override:"
const val EDIT_OVERRIDE_LINK_PREFIX = "decoy-edit-override:"

private const val DISCLAIMER =
    "Decoy masks values it detects via regex/NER, manual overrides, and shape-based rules " +
        "before they reach an LLM. It does not guarantee protection against re-identification " +
        "from surrounding context, and automated relevance classification is imperfect -- manual " +
        "overrides exist precisely because of these limits. Nothing here ever shows a real or fake value."

fun escapeHtml(value: String): String = value
    .replace("&", "&amp;")
    .replace("<", "&lt;")
    .replace(">", "&gt;")
    .replace("\"", "&quot;")
    .replace("'", "&#39;")

/** Renders one override list entry as `<li>` with [edit]/[remove] links.
 * `label` is "field" or "pattern" (display text only); `kind`/`list`
 * select which OverridesFile section this entry lives in, encoded into
 * the hyperlink for the plugin module's decoder -- see the class-level
 * doc comment above for the encoding/decoding contract. */
private fun appendOverrideItem(
    sb: StringBuilder,
    label: String,
    value: String,
    kind: OverrideKind,
    list: OverrideListKind,
) {
    val escapedValue = escapeHtml(value)
    val linkSuffix = "${kind.name}:${list.name}:$escapedValue"
    sb.append("<li>").append(label).append(": ")
    if (label == "pattern") sb.append("<code>").append(escapedValue).append("</code>") else sb.append(escapedValue)
    sb.append(" <a href='").append(EDIT_OVERRIDE_LINK_PREFIX).append(linkSuffix).append("'>[edit]</a>")
    sb.append(" <a href='").append(REMOVE_OVERRIDE_LINK_PREFIX).append(linkSuffix).append("'>[remove]</a>")
    sb.append("</li>")
}

fun renderHtml(groups: List<RequestGroup>, overrides: OverridesFile): String {
    val sb = StringBuilder()
    sb.append("<html><body style='font-family: sans-serif; font-size: 12px;'>")

    sb.append("<h3>Recent requests</h3>")
    if (groups.isEmpty()) {
        sb.append("<p><i>No requests recorded yet in this project.</i></p>")
    } else {
        for (group in groups) {
            sb.append("<div style='margin-bottom: 10px; border: 1px solid #888; padding: 4px 8px;'>")
            sb.append("<b>").append(escapeHtml(group.timestamp)).append("</b> &mdash; ")
            sb.append("session: ").append(escapeHtml(group.sessionId))
            sb.append(" &mdash; ").append(group.entries.size).append(" decision(s)")
            sb.append(" &mdash; <a href='")
            sb.append(CLEAR_SESSION_LINK_PREFIX).append(escapeHtml(group.sessionId))
            sb.append("'>Clear this session</a>")
            sb.append("<table border='1' cellspacing='0' cellpadding='3' style='width:100%; margin-top:4px;'>")
            sb.append("<tr><th>Field</th><th>Decision</th><th>Layer</th><th>Reason</th></tr>")
            for (e: AuditEntry in group.entries) {
                sb.append("<tr>")
                sb.append("<td>").append(escapeHtml(e.field)).append("</td>")
                sb.append("<td>").append(escapeHtml(e.decision)).append("</td>")
                sb.append("<td>").append(escapeHtml(e.layer)).append("</td>")
                sb.append("<td>").append(escapeHtml(e.reason)).append("</td>")
                sb.append("</tr>")
            }
            sb.append("</table></div>")
        }
    }

    sb.append("<h3>Overrides</h3>")
    sb.append("<b>Always mask</b><ul>")
    for (f in overrides.always_mask.field_names) appendOverrideItem(sb, "field", f, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES)
    for (p in overrides.always_mask.patterns) appendOverrideItem(sb, "pattern", p, OverrideKind.ALWAYS_MASK, OverrideListKind.PATTERNS)
    sb.append("</ul><b>Never mask</b><ul>")
    for (f in overrides.never_mask.field_names) appendOverrideItem(sb, "field", f, OverrideKind.NEVER_MASK, OverrideListKind.FIELD_NAMES)
    for (p in overrides.never_mask.patterns) appendOverrideItem(sb, "pattern", p, OverrideKind.NEVER_MASK, OverrideListKind.PATTERNS)
    sb.append("</ul>")
    sb.append(
        "<p><i>Use the \"Add Always-Mask Field/Pattern\" / \"Add Never-Mask Field/Pattern\" toolbar " +
            "actions to add an override; use the [edit] / [remove] links next to an existing entry above " +
            "to change or delete it. \"Clear Session Data Only\" keeps these overrides; " +
            "\"Clear All Local Data\" removes them too.</i></p>",
    )

    sb.append("<hr/><p style='font-size:10px; opacity:0.8;'>").append(DISCLAIMER).append("</p>")

    sb.append("</body></html>")
    return sb.toString()
}
