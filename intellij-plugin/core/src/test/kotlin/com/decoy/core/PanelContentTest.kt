package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

private fun group(
    requestId: String = "req-1",
    sessionId: String = "session-1",
    entries: List<AuditEntry> = listOf(
        AuditEntry(
            "e1", requestId, sessionId, "2026-08-30T21:15:03.482Z",
            "prompt_text", "EMAIL", "masked", "matched EMAIL regex", "regex",
        ),
    ),
) = RequestGroup(requestId, sessionId, entries.first().timestamp, entries)

class PanelContentTest {

    @Test
    fun `escapeHtml escapes all five special characters`() {
        assertEquals("&lt;script&gt;&amp;&quot;&#39;&lt;/script&gt;", escapeHtml("<script>&\"'</script>"))
    }

    @Test
    fun `renders field, decision, reason, layer for each entry`() {
        val html = renderHtml(listOf(group()), emptyOverridesFile())
        assertTrue(html.contains("EMAIL"))
        assertTrue(html.contains("masked"))
        assertTrue(html.contains("matched EMAIL regex"))
        assertTrue(html.contains("regex"))
        assertTrue(html.contains("session-1"))
    }

    @Test
    fun `shows an empty state when there are no requests`() {
        val html = renderHtml(emptyList(), emptyOverridesFile())
        assertTrue(html.contains("No requests recorded yet"))
    }

    @Test
    fun `includes the no-absolute-privacy disclaimer text`() {
        val html = renderHtml(emptyList(), emptyOverridesFile())
        assertFalse(html.lowercase().contains("100% private"))
        assertFalse(html.lowercase().contains("never leaves your machine"))
        assertTrue(html.contains("does not guarantee protection against"))
        assertTrue(html.contains("automated relevance classification is imperfect"))
    }

    @Test
    fun `HTML-escapes untrusted entry content so it cannot break out of markup`() {
        val malicious = group(
            entries = listOf(
                AuditEntry(
                    "e1", "req-1", "session-1", "2026-08-30T21:15:03.482Z", "db_record",
                    "</td><script>alert(1)</script>", "masked",
                    "\"><img src=x onerror=alert(1)>", "regex",
                ),
            ),
        )
        val html = renderHtml(listOf(malicious), emptyOverridesFile())
        assertFalse(html.contains("<script>alert(1)</script>"))
        assertFalse(html.contains("<img src=x onerror=alert(1)>"))
    }

    @Test
    fun `renders configured overrides for both always_mask and never_mask`() {
        val overrides = OverridesFile(
            always_mask = OverrideRules(patterns = listOf("EMP-\\d{6}"), field_names = listOf("employee_id")),
            never_mask = OverrideRules(field_names = listOf("status")),
        )
        val html = renderHtml(emptyList(), overrides)
        assertTrue(html.contains("employee_id"))
        assertTrue(html.contains("EMP-\\d{6}"))
        assertTrue(html.contains("status"))
    }

    @Test
    fun `never renders anything resembling a real or fake value field -- only known metadata keys`() {
        val html = renderHtml(listOf(group()), emptyOverridesFile())
        assertFalse(Regex("original|fake_value|realValue", RegexOption.IGNORE_CASE).containsMatchIn(html))
    }

    @Test
    fun `each session's clear link uses the shared CLEAR_SESSION_LINK_PREFIX the plugin module intercepts`() {
        val html = renderHtml(listOf(group(sessionId = "session-xyz")), emptyOverridesFile())
        assertTrue(html.contains("href='${CLEAR_SESSION_LINK_PREFIX}session-xyz'"))
    }

    @Test
    fun `each override entry gets an edit and remove link encoding kind, list, and value`() {
        val overrides = OverridesFile(
            always_mask = OverrideRules(patterns = listOf("EMP-\\d{6}"), field_names = listOf("employee_id")),
            never_mask = OverrideRules(field_names = listOf("status")),
        )
        val html = renderHtml(emptyList(), overrides)

        assertTrue(html.contains("href='${EDIT_OVERRIDE_LINK_PREFIX}ALWAYS_MASK:FIELD_NAMES:employee_id'"))
        assertTrue(html.contains("href='${REMOVE_OVERRIDE_LINK_PREFIX}ALWAYS_MASK:FIELD_NAMES:employee_id'"))
        assertTrue(html.contains("href='${EDIT_OVERRIDE_LINK_PREFIX}ALWAYS_MASK:PATTERNS:EMP-\\d{6}'"))
        assertTrue(html.contains("href='${REMOVE_OVERRIDE_LINK_PREFIX}ALWAYS_MASK:PATTERNS:EMP-\\d{6}'"))
        assertTrue(html.contains("href='${EDIT_OVERRIDE_LINK_PREFIX}NEVER_MASK:FIELD_NAMES:status'"))
        assertTrue(html.contains("href='${REMOVE_OVERRIDE_LINK_PREFIX}NEVER_MASK:FIELD_NAMES:status'"))
    }

    @Test
    fun `a pattern value containing a literal colon is preserved verbatim in the link (decoder must split with limit 3)`() {
        val overrides = OverridesFile(always_mask = OverrideRules(patterns = listOf("\\d{2}:\\d{2}")))
        val html = renderHtml(emptyList(), overrides)
        assertTrue(html.contains("href='${EDIT_OVERRIDE_LINK_PREFIX}ALWAYS_MASK:PATTERNS:\\d{2}:\\d{2}'"))
    }

    @Test
    fun `overrides section no longer claims there is no remove or edit-in-place control`() {
        val html = renderHtml(emptyList(), emptyOverridesFile())
        assertFalse(html.contains("no remove/edit-in-place control"))
    }
}
