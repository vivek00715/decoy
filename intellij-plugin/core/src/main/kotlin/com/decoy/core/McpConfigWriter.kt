package com.decoy.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File

/**
 * Writes/merges Decoy's bundled `decoy-proxy` binary into a project's
 * `.mcp.json` -- Claude Code's project-scoped MCP server config file,
 * read from the project root (see README.md's "Installing the MCP
 * proxy" section for the general "point your MCP client's `command` at
 * the binary" shape this mirrors; the exact `.mcp.json` filename/
 * location convention is Claude Code's own documented convention
 * (docs.claude.com/en/docs/claude-code/mcp), not something this repo's
 * own docs pin down further -- flagged, not guessed silently).
 *
 * Mirrors vscode-extension/src/mcpConfig.ts's design exactly: kept
 * SDK-free (no IntelliJ Platform imports) so this is unit-testable with
 * plain JUnit, the way OverridesStore.kt/AuditLogStore.kt already are.
 * The Swing/AnAction wiring that calls this lives in `plugin` (unverified
 * like the rest of that module -- see MANUAL_TEST.md).
 *
 * JSON is handled at the `JsonObject`/`JsonElement` level rather than a
 * fully-typed data class for the whole file, specifically so that
 * unrelated top-level keys and unrelated `mcpServers` entries a user (or
 * another tool) already has in `.mcp.json` round-trip byte-for-byte
 * structurally untouched -- only the one entry this plugin owns is ever
 * replaced.
 */

const val MCP_CONFIG_FILENAME = ".mcp.json"
const val DEFAULT_MCP_SERVER_NAME = "decoy"

private val json = Json { prettyPrint = true; prettyPrintIndent = "  " }

/** Parse an existing `.mcp.json`'s text into a JsonObject. Blank/missing
 * text fails safe to an empty config (`{"mcpServers": {}}`), matching
 * OverridesStore.kt's/AuditLogStore.kt's fail-safe-on-missing convention.
 * Malformed JSON throws -- callers should surface that as a warning
 * rather than silently overwriting a file they couldn't actually parse. */
fun parseMcpConfig(text: String?): JsonObject {
    if (text.isNullOrBlank()) return emptyMcpConfigJson()
    val parsed = json.parseToJsonElement(text)
    require(parsed is JsonObject) { "$MCP_CONFIG_FILENAME does not contain a JSON object" }
    return if (parsed.containsKey("mcpServers") && parsed["mcpServers"] is JsonObject) {
        parsed
    } else {
        buildJsonObject {
            parsed.forEach { (k, v) -> if (k != "mcpServers") put(k, v) }
            put("mcpServers", buildJsonObject { })
        }
    }
}

fun emptyMcpConfigJson(): JsonObject = buildJsonObject { put("mcpServers", buildJsonObject { }) }

fun readMcpConfig(rootDir: File): JsonObject {
    val file = File(rootDir, MCP_CONFIG_FILENAME)
    if (!file.exists()) return emptyMcpConfigJson()
    return parseMcpConfig(file.readText(Charsets.UTF_8))
}

/** decoy-proxy's real CLI shape (src/decoy/cli.py's `proxy` subparser;
 * the packaged binary's entry point is `decoy.cli:main`, per
 * scripts/build_binary.py, so the FULL `decoy` CLI -- including the
 * `proxy` subcommand name -- is what a bundled binary invocation needs):
 * `decoy-proxy proxy --session SESSION target_command [target_args...]`.
 * No `--` separator before target_command -- it's a plain positional. */
fun buildDecoyProxyArgs(sessionId: String, targetCommand: String, targetArgs: List<String>): List<String> =
    listOf("proxy", "--session", sessionId, targetCommand) + targetArgs

fun buildMcpServerEntry(binaryPath: String, sessionId: String, targetCommand: String, targetArgs: List<String>): JsonObject =
    buildJsonObject {
        put("command", binaryPath)
        put("args", JsonArray(buildDecoyProxyArgs(sessionId, targetCommand, targetArgs).map { JsonPrimitive(it) }))
    }

data class McpConfigUpdateResult(
    val updated: JsonObject,
    val overwritingExisting: Boolean,
    val entry: JsonObject,
)

/**
 * Merge a Decoy server entry into `existing` WITHOUT touching any other
 * key in `mcpServers` or any other top-level key in the file.
 */
fun buildMcpConfigUpdate(
    existing: JsonObject,
    serverName: String,
    binaryPath: String,
    sessionId: String,
    targetCommand: String,
    targetArgs: List<String>,
): McpConfigUpdateResult {
    val existingServers = (existing["mcpServers"] as? JsonObject) ?: buildJsonObject { }
    val overwritingExisting = existingServers.containsKey(serverName)
    val entry = buildMcpServerEntry(binaryPath, sessionId, targetCommand, targetArgs)

    val updatedServers = buildJsonObject {
        existingServers.forEach { (k, v) -> if (k != serverName) put(k, v) }
        put(serverName, entry)
    }
    val updated = buildJsonObject {
        existing.forEach { (k, v) -> if (k != "mcpServers") put(k, v) }
        put("mcpServers", updatedServers)
    }
    return McpConfigUpdateResult(updated, overwritingExisting, entry)
}

fun serializeMcpConfig(config: JsonObject): String = json.encodeToString(JsonObject.serializer(), config) + "\n"

/**
 * Full write path: read whatever's on disk (or nothing), merge in
 * Decoy's entry, write atomically. Callers (the `plugin` module's
 * AnAction) are responsible for user confirmation BEFORE calling this --
 * this function itself performs the write unconditionally, matching
 * OverridesStore.kt's writeOverrides()/clearOverrides() split of
 * "confirm in the UI layer, write unconditionally in core".
 */
fun writeMcpProxyConfig(
    rootDir: File,
    binaryPath: String,
    sessionId: String,
    targetCommand: String,
    targetArgs: List<String>,
    serverName: String = DEFAULT_MCP_SERVER_NAME,
): McpConfigUpdateResult {
    val existing = readMcpConfig(rootDir)
    val result = buildMcpConfigUpdate(existing, serverName, binaryPath, sessionId, targetCommand, targetArgs)
    writeFileAtomically(File(rootDir, MCP_CONFIG_FILENAME), serializeMcpConfig(result.updated))
    return result
}
