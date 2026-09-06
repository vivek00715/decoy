package com.decoy.core

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

class WebviewContentTest {

    @Test
    fun `renders field, decision, reason, layer for each entry`() {
        val html = renderWebviewHtml(listOf(group()), emptyOverridesFile(), WebviewTheme.fallback())
        assertTrue(html.contains("EMAIL"))
        assertTrue(html.contains("masked"))
        assertTrue(html.contains("matched EMAIL regex"))
        assertTrue(html.contains("regex"))
        assertTrue(html.contains("session-1"))
    }

    @Test
    fun `shows an empty state when there are no requests`() {
        val html = renderWebviewHtml(emptyList(), emptyOverridesFile(), WebviewTheme.fallback())
        assertTrue(html.contains("No requests yet"))
        assertTrue(html.contains("Once Claude Code sends a prompt through Decoy"))
    }

    @Test
    fun `includes the no-absolute-privacy disclaimer text`() {
        val html = renderWebviewHtml(emptyList(), emptyOverridesFile(), WebviewTheme.fallback())
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
        val html = renderWebviewHtml(listOf(malicious), emptyOverridesFile(), WebviewTheme.fallback())
        assertFalse(html.contains("<script>alert(1)</script>"))
        assertFalse(html.contains("<img src=x onerror=alert(1)>"))
    }

    @Test
    fun `a value containing script-closing characters cannot break out of the embedded JS override state`() {
        val overrides = OverridesFile(
            always_mask = OverrideRules(field_names = listOf("</script><script>alert(1)</script>")),
        )
        val html = renderWebviewHtml(emptyList(), overrides, WebviewTheme.fallback())
        assertFalse(html.contains("</script><script>alert(1)</script>"))
    }

    @Test
    fun `renders configured overrides for both always_mask and never_mask`() {
        val overrides = OverridesFile(
            always_mask = OverrideRules(patterns = listOf("EMP-\\d{6}"), field_names = listOf("employee_id")),
            never_mask = OverrideRules(field_names = listOf("status")),
        )
        val html = renderWebviewHtml(emptyList(), overrides, WebviewTheme.fallback())
        assertTrue(html.contains("employee_id"))
        assertTrue(html.contains("EMP-\\d{6}"))
        assertTrue(html.contains("status"))
    }

    @Test
    fun `never renders anything resembling a real or fake value field -- only known metadata keys`() {
        val html = renderWebviewHtml(listOf(group()), emptyOverridesFile(), WebviewTheme.fallback())
        assertFalse(Regex("original|fake_value|realValue", RegexOption.IGNORE_CASE).containsMatchIn(html))
    }

    @Test
    fun `uses Codicon classes and the theme's colors instead of hardcoded ones`() {
        val theme = WebviewTheme.fallback().copy(accentBlue = "#123456")
        val html = renderWebviewHtml(listOf(group()), emptyOverridesFile(), theme)
        assertTrue(html.contains("codicon-check")) // masked
        assertTrue(html.contains("#123456"))
        assertFalse(html.contains("#3794ff")) // the old VS Code hardcoded fallback should not leak in
    }

    @Test
    fun `flags override-layer entries with the warning triangle`() {
        val overrideEntry = group(
            entries = listOf(
                AuditEntry("e1", "req-1", "session-1", "t", "prompt_text", "EMAIL", "masked", "manual override: always_mask", "override"),
            ),
        )
        val html = renderWebviewHtml(listOf(overrideEntry), emptyOverridesFile(), WebviewTheme.fallback())
        assertTrue(html.contains("codicon-warning"))
    }

    @Test
    fun `posts JSON messages matching the VS Code webview's WebviewMessage shape`() {
        val html = renderWebviewHtml(emptyList(), emptyOverridesFile(), WebviewTheme.fallback())
        assertTrue(html.contains("window.__decoyPost"))
        assertTrue(html.contains("\"command\": \"refresh\"") || html.contains("command: \"refresh\""))
        assertTrue(html.contains("clearSessionDataOnly"))
        assertTrue(html.contains("clearAll"))
        assertTrue(html.contains("saveOverrides"))
    }

    @Test
    fun `renders the proxy status line and approval banner when provided, and omits them when not`() {
        val withBoth = renderWebviewHtml(
            emptyList(), emptyOverridesFile(), WebviewTheme.fallback(),
            proxyStatusLine = "Chat proxy: running (port 8787)",
            approvalMessage = "The Decoy MCP server is registered but not connected yet.",
        )
        assertTrue(withBoth.contains("Chat proxy: running (port 8787)"))
        assertTrue(withBoth.contains("approval-banner"))
        // the real approvalActionMessage() text contains an apostrophe
        // ("Decoy's"), which escapeHtml() correctly turns into &#39; --
        // this test message avoids one so the assertion checks the
        // banner mechanism itself, not escaping (already covered
        // elsewhere by the HTML-escaping test above).
        assertTrue(withBoth.contains("The Decoy MCP server is registered but not connected yet."))

        val withNeither = renderWebviewHtml(emptyList(), emptyOverridesFile(), WebviewTheme.fallback())
        assertFalse(withNeither.contains("proxy-status-line\">"))
        assertFalse(withNeither.contains("class=\"approval-banner\""))
    }
}
