package com.decoy.core

import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

class ClaudeSettingsWriterTest {

    @Test
    fun `parseClaudeSettings returns empty for null or blank text`() {
        assertEquals(emptyClaudeSettingsJson(), parseClaudeSettings(null))
        assertEquals(emptyClaudeSettingsJson(), parseClaudeSettings(""))
        assertEquals(emptyClaudeSettingsJson(), parseClaudeSettings("   "))
    }

    @Test
    fun `parseClaudeSettings preserves unrelated top-level keys and existing env entries`() {
        val parsed = parseClaudeSettings("""{"theme":"dark","env":{"EXISTING":"1"}}""")
        assertEquals("dark", parsed["theme"]!!.jsonPrimitive.content)
        assertEquals("1", parsed["env"]!!.jsonObject["EXISTING"]!!.jsonPrimitive.content)
    }

    @Test
    fun `buildClaudeSettingsUpdate adds ANTHROPIC_BASE_URL without touching unrelated keys`() {
        val existing = parseClaudeSettings("""{"theme":"dark","env":{"SOME_OTHER_VAR":"keep-me"}}""")
        val result = buildClaudeSettingsUpdate(existing, "ANTHROPIC_BASE_URL", "http://127.0.0.1:8787")
        val env = result.updated["env"]!!.jsonObject
        assertEquals("keep-me", env["SOME_OTHER_VAR"]!!.jsonPrimitive.content)
        assertEquals("http://127.0.0.1:8787", env["ANTHROPIC_BASE_URL"]!!.jsonPrimitive.content)
        assertEquals("dark", result.updated["theme"]!!.jsonPrimitive.content)
        assertFalse(result.overwritingExisting)
    }

    @Test
    fun `buildClaudeSettingsUpdate flags overwritingExisting only when the value actually changes`() {
        val existing = parseClaudeSettings("""{"env":{"ANTHROPIC_BASE_URL":"http://old:1"}}""")
        val same = buildClaudeSettingsUpdate(existing, "ANTHROPIC_BASE_URL", "http://old:1")
        assertFalse(same.overwritingExisting)

        val changed = buildClaudeSettingsUpdate(existing, "ANTHROPIC_BASE_URL", "http://new:2")
        assertTrue(changed.overwritingExisting)
    }

    @Test
    fun `removeClaudeSettingsEnvKey removes only the named key`() {
        val existing = parseClaudeSettings("""{"theme":"dark","env":{"ANTHROPIC_BASE_URL":"http://x","KEEP":"1"}}""")
        val result = removeClaudeSettingsEnvKey(existing, "ANTHROPIC_BASE_URL")
        val env = result["env"]!!.jsonObject
        assertFalse(env.containsKey("ANTHROPIC_BASE_URL"))
        assertEquals("1", env["KEEP"]!!.jsonPrimitive.content)
        assertEquals("dark", result["theme"]!!.jsonPrimitive.content)
    }

    @Test
    fun `removeClaudeSettingsEnvKey is a no-op when the key is not present`() {
        val existing = parseClaudeSettings("""{"env":{"KEEP":"1"}}""")
        val result = removeClaudeSettingsEnvKey(existing, "ANTHROPIC_BASE_URL")
        assertEquals(existing, result)
    }

    @Test
    fun `writeAnthropicBaseUrl writes a real file that round-trips`(@TempDir tmpDir: File) {
        val result = writeAnthropicBaseUrl(tmpDir, "http://127.0.0.1:8787")
        assertFalse(result.overwritingExisting)

        val written = File(tmpDir, CLAUDE_SETTINGS_RELATIVE_PATH)
        assertTrue(written.exists())
        val reread = readClaudeSettings(tmpDir)
        assertEquals("http://127.0.0.1:8787", reread["env"]!!.jsonObject["ANTHROPIC_BASE_URL"]!!.jsonPrimitive.content)
    }

    @Test
    fun `removeAnthropicBaseUrl is a no-op when no settings file exists yet`(@TempDir tmpDir: File) {
        removeAnthropicBaseUrl(tmpDir) // must not throw
        assertFalse(File(tmpDir, CLAUDE_SETTINGS_RELATIVE_PATH).exists())
    }

    @Test
    fun `writeAnthropicBaseUrl then removeAnthropicBaseUrl leaves other keys intact`(@TempDir tmpDir: File) {
        val settingsFile = File(tmpDir, CLAUDE_SETTINGS_RELATIVE_PATH)
        settingsFile.parentFile.mkdirs()
        settingsFile.writeText("""{"theme":"dark"}""")

        writeAnthropicBaseUrl(tmpDir, "http://127.0.0.1:8787")
        removeAnthropicBaseUrl(tmpDir)

        val finalSettings = readClaudeSettings(tmpDir)
        assertEquals("dark", finalSettings["theme"]!!.jsonPrimitive.content)
        assertFalse((finalSettings["env"] as? kotlinx.serialization.json.JsonObject)?.containsKey("ANTHROPIC_BASE_URL") ?: false)
    }
}
