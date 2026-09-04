package com.decoy.core

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

class McpConfigWriterTest {

    @Test
    fun `parseMcpConfig returns empty config for null or blank text`() {
        assertEquals(emptyMcpConfigJson(), parseMcpConfig(null))
        assertEquals(emptyMcpConfigJson(), parseMcpConfig(""))
        assertEquals(emptyMcpConfigJson(), parseMcpConfig("   "))
    }

    @Test
    fun `parseMcpConfig parses a well-formed existing file`() {
        val text = """{"mcpServers": {"other": {"command": "npx", "args": ["-y", "some-server"]}}}"""
        val result = parseMcpConfig(text)
        val other = result["mcpServers"]!!.jsonObject["other"]!!.jsonObject
        assertEquals("npx", other["command"]!!.jsonPrimitive.content)
    }

    @Test
    fun `parseMcpConfig defaults mcpServers to empty object when missing`() {
        val result = parseMcpConfig("""{"someOtherKey": true}""")
        assertTrue(result["mcpServers"]!!.jsonObject.isEmpty())
        assertEquals(true, result["someOtherKey"]!!.jsonPrimitive.content.toBoolean())
    }

    @Test
    fun `parseMcpConfig throws on malformed JSON`() {
        assertThrows(Exception::class.java) { parseMcpConfig("{ not valid json") }
    }

    @Test
    fun `parseMcpConfig throws when top-level value is not an object`() {
        assertThrows(IllegalArgumentException::class.java) { parseMcpConfig("[1,2,3]") }
    }

    @Test
    fun `buildDecoyProxyArgs matches the real CLI shape`() {
        assertEquals(
            listOf("proxy", "--session", "my-session", "npx", "-y", "@some/mcp-server"),
            buildDecoyProxyArgs("my-session", "npx", listOf("-y", "@some/mcp-server")),
        )
    }

    @Test
    fun `buildDecoyProxyArgs handles a target with no extra args`() {
        assertEquals(
            listOf("proxy", "--session", "s1", "/usr/local/bin/some-server"),
            buildDecoyProxyArgs("s1", "/usr/local/bin/some-server", emptyList()),
        )
    }

    @Test
    fun `buildMcpConfigUpdate adds a new entry to an empty config`() {
        val result = buildMcpConfigUpdate(
            existing = emptyMcpConfigJson(),
            serverName = "decoy",
            binaryPath = "/plugin/bin/decoy-proxy",
            sessionId = "intellij-session",
            targetCommand = "npx",
            targetArgs = listOf("-y", "@acme/server"),
        )
        assertFalse(result.overwritingExisting)
        val decoyEntry = result.updated["mcpServers"]!!.jsonObject["decoy"]!!.jsonObject
        assertEquals("/plugin/bin/decoy-proxy", decoyEntry["command"]!!.jsonPrimitive.content)
        assertEquals(
            listOf("proxy", "--session", "intellij-session", "npx", "-y", "@acme/server"),
            decoyEntry["args"]!!.jsonArray.map { it.jsonPrimitive.content },
        )
    }

    @Test
    fun `buildMcpConfigUpdate preserves unrelated existing mcpServers entries`() {
        val existing = parseMcpConfig("""{"mcpServers": {"unrelated": {"command": "foo", "args": []}}}""")
        val result = buildMcpConfigUpdate(existing, "decoy", "/bin/decoy-proxy", "s", "t", emptyList())
        val servers = result.updated["mcpServers"]!!.jsonObject
        assertEquals("foo", servers["unrelated"]!!.jsonObject["command"]!!.jsonPrimitive.content)
        assertTrue(servers.containsKey("decoy"))
    }

    @Test
    fun `buildMcpConfigUpdate preserves unrelated top-level keys`() {
        val existing = parseMcpConfig("""{"mcpServers": {}, "someFutureKey": 42}""")
        val result = buildMcpConfigUpdate(existing, "decoy", "/bin/decoy-proxy", "s", "t", emptyList())
        assertEquals(42, result.updated["someFutureKey"]!!.jsonPrimitive.content.toInt())
    }

    @Test
    fun `buildMcpConfigUpdate flags overwritingExisting and replaces the entry`() {
        val existing = parseMcpConfig(
            """{"mcpServers": {"decoy": {"command": "/old/path/decoy-proxy", "args": ["proxy", "--session", "old"]}}}""",
        )
        val result = buildMcpConfigUpdate(existing, "decoy", "/new/path/decoy-proxy", "new-session", "npx", emptyList())
        assertTrue(result.overwritingExisting)
        val decoyEntry = result.updated["mcpServers"]!!.jsonObject["decoy"]!!.jsonObject
        assertEquals("/new/path/decoy-proxy", decoyEntry["command"]!!.jsonPrimitive.content)
    }

    @Test
    fun `buildMcpConfigUpdate does not mutate the input existing config`() {
        val existing = parseMcpConfig("""{"mcpServers": {"a": {"command": "x", "args": []}}}""")
        val snapshot = existing.toString()
        buildMcpConfigUpdate(existing, "decoy", "/bin/decoy-proxy", "s", "t", emptyList())
        assertEquals(snapshot, existing.toString())
    }

    @Test
    fun `serializeMcpConfig round-trips through parseMcpConfig`() {
        val result = buildMcpConfigUpdate(emptyMcpConfigJson(), "decoy", "/bin/decoy-proxy", "s1", "npx", listOf("-y", "srv"))
        val text = serializeMcpConfig(result.updated)
        assertTrue(text.endsWith("\n"))
        assertEquals(result.updated, parseMcpConfig(text))
    }

    @Test
    fun `writeMcpProxyConfig writes a new file and reads it back correctly`(@TempDir tmp: File) {
        val result = writeMcpProxyConfig(
            rootDir = tmp,
            binaryPath = "/plugin/bin/decoy-proxy",
            sessionId = "s1",
            targetCommand = "npx",
            targetArgs = listOf("-y", "server"),
        )
        val onDisk = File(tmp, MCP_CONFIG_FILENAME)
        assertTrue(onDisk.exists())
        assertEquals(result.updated, parseMcpConfig(onDisk.readText()))
    }

    @Test
    fun `writeMcpProxyConfig merges into an existing file without clobbering unrelated entries`(@TempDir tmp: File) {
        File(tmp, MCP_CONFIG_FILENAME).writeText(
            """{"mcpServers": {"other-server": {"command": "foo", "args": ["bar"]}}}""",
        )
        writeMcpProxyConfig(tmp, "/plugin/bin/decoy-proxy", "s1", "npx", listOf("-y", "server"))
        val onDisk = parseMcpConfig(File(tmp, MCP_CONFIG_FILENAME).readText())
        val servers = onDisk["mcpServers"]!!.jsonObject
        assertTrue(servers.containsKey("other-server"))
        assertTrue(servers.containsKey("decoy"))
        assertEquals("foo", servers["other-server"]!!.jsonObject["command"]!!.jsonPrimitive.content)
    }

    @Test
    fun `writeMcpProxyConfig writes plain parseable JSON with no atomic-write artifacts left behind`(@TempDir tmp: File) {
        writeMcpProxyConfig(tmp, "/bin/decoy-proxy", "s", "t", emptyList())
        val finalFile = File(tmp, MCP_CONFIG_FILENAME)
        val tmpFile = File(finalFile.parentFile, finalFile.name + ".tmp")
        assertTrue(finalFile.exists())
        assertFalse(tmpFile.exists())
    }
}
