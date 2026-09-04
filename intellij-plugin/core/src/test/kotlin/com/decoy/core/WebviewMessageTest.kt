package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Test

class WebviewMessageTest {

    @Test
    fun `parses a bare refresh command`() {
        val msg = parseWebviewMessage("""{"command":"refresh"}""")
        assertEquals(WebviewMessage(command = "refresh"), msg)
    }

    @Test
    fun `parses clearSession with its sessionId`() {
        val msg = parseWebviewMessage("""{"command":"clearSession","sessionId":"s1"}""")
        assertEquals(WebviewMessage(command = "clearSession", sessionId = "s1"), msg)
    }

    @Test
    fun `parses saveOverrides with a full OverridesFile payload`() {
        val msg = parseWebviewMessage(
            """{"command":"saveOverrides","overrides":{"always_mask":{"patterns":["a"],"field_names":["b"]},"never_mask":{"patterns":[],"field_names":[]}}}""",
        )
        assertEquals(
            WebviewMessage(
                command = "saveOverrides",
                overrides = OverridesFile(
                    always_mask = OverrideRules(patterns = listOf("a"), field_names = listOf("b")),
                ),
            ),
            msg,
        )
    }

    @Test
    fun `malformed JSON fails safe to null rather than throwing`() {
        assertNull(parseWebviewMessage("not json at all"))
    }

    @Test
    fun `missing required command field fails safe to null`() {
        assertNull(parseWebviewMessage("""{"sessionId":"s1"}"""))
    }

    @Test
    fun `unknown extra fields are ignored rather than rejected`() {
        val msg = parseWebviewMessage("""{"command":"refresh","somethingNew":123}""")
        assertEquals(WebviewMessage(command = "refresh"), msg)
    }
}
