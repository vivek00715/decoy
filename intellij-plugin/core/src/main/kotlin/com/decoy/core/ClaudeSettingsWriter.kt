package com.decoy.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File

/**
 * Writes/merges into `.claude/settings.json`'s `env` block -- confirmed
 * directly against a real `claude` CLI install (not assumed from
 * documentation): a project-level `.claude/settings.json` containing
 * `{"env": {"ANTHROPIC_BASE_URL": "..."}}` is read and actually applied
 * at launch, with NO shell export required. Verified by pointing it at a
 * local HTTP listener with the real env var explicitly unset (`env -u
 * ANTHROPIC_BASE_URL claude -p ...`) and observing the request land on
 * that listener anyway -- see vscode-extension/src/claudeSettingsConfig.ts,
 * which this mirrors exactly (same verification, same design), for the
 * VS Code side.
 *
 * This replaces the manual "export ANTHROPIC_BASE_URL=... in a shell
 * Claude Code is then launched from" step. Kept SDK-free, same reasoning
 * as McpConfigWriter.kt: the merge logic is the riskiest part (this file
 * may have unrelated hand-set settings already) and is unit-testable
 * with plain JUnit.
 */

const val CLAUDE_SETTINGS_RELATIVE_PATH = ".claude/settings.json"
const val ANTHROPIC_BASE_URL_KEY = "ANTHROPIC_BASE_URL"

private val json = Json { prettyPrint = true; prettyPrintIndent = "  " }

fun emptyClaudeSettingsJson(): JsonObject = buildJsonObject { }

/** Parse an existing `.claude/settings.json`. Blank/missing text fails
 * safe to an empty object, matching McpConfigWriter.kt's convention.
 * Malformed JSON throws -- callers surface this as a warning rather than
 * clobbering a file they can't understand. */
fun parseClaudeSettings(text: String?): JsonObject {
    if (text.isNullOrBlank()) return emptyClaudeSettingsJson()
    val parsed = json.parseToJsonElement(text)
    require(parsed is JsonObject) { "$CLAUDE_SETTINGS_RELATIVE_PATH does not contain a JSON object" }
    return parsed
}

fun readClaudeSettings(rootDir: File): JsonObject {
    val file = File(rootDir, CLAUDE_SETTINGS_RELATIVE_PATH)
    if (!file.exists()) return emptyClaudeSettingsJson()
    return parseClaudeSettings(file.readText(Charsets.UTF_8))
}

data class ClaudeSettingsUpdateResult(val updated: JsonObject, val overwritingExisting: Boolean)

/** Merge ONE env var into `existing`'s `env` block WITHOUT touching any
 * other env var or any other top-level settings key. */
fun buildClaudeSettingsUpdate(existing: JsonObject, envKey: String, envValue: String): ClaudeSettingsUpdateResult {
    val currentEnv = (existing["env"] as? JsonObject) ?: buildJsonObject { }
    val existingValue = (currentEnv[envKey] as? JsonPrimitive)?.content
    val overwritingExisting = currentEnv.containsKey(envKey) && existingValue != envValue

    val updatedEnv = buildJsonObject {
        currentEnv.forEach { (k, v) -> if (k != envKey) put(k, v) }
        put(envKey, envValue)
    }
    val updated = buildJsonObject {
        existing.forEach { (k, v) -> if (k != "env") put(k, v) }
        put("env", updatedEnv)
    }
    return ClaudeSettingsUpdateResult(updated, overwritingExisting)
}

/** Removes ONE env var, leaving everything else untouched -- used when
 * stopping the chat proxy so Claude Code doesn't keep pointing at a dead
 * port. No-op if the key isn't present. */
fun removeClaudeSettingsEnvKey(existing: JsonObject, envKey: String): JsonObject {
    val currentEnv = existing["env"] as? JsonObject ?: return existing
    if (!currentEnv.containsKey(envKey)) return existing
    val updatedEnv = buildJsonObject { currentEnv.forEach { (k, v) -> if (k != envKey) put(k, v) } }
    return buildJsonObject {
        existing.forEach { (k, v) -> if (k != "env") put(k, v) }
        put("env", updatedEnv)
    }
}

fun serializeClaudeSettings(settings: JsonObject): String = json.encodeToString(JsonObject.serializer(), settings) + "\n"

/** Full write path: read whatever's on disk (or nothing), merge in the
 * env var, write atomically. */
fun writeAnthropicBaseUrl(rootDir: File, baseUrl: String): ClaudeSettingsUpdateResult {
    val existing = readClaudeSettings(rootDir)
    val result = buildClaudeSettingsUpdate(existing, ANTHROPIC_BASE_URL_KEY, baseUrl)
    writeFileAtomically(File(rootDir, CLAUDE_SETTINGS_RELATIVE_PATH), serializeClaudeSettings(result.updated))
    return result
}

/** Removes ANTHROPIC_BASE_URL if present. No-op (no write at all) if
 * `.claude/settings.json` doesn't exist yet. */
fun removeAnthropicBaseUrl(rootDir: File) {
    val file = File(rootDir, CLAUDE_SETTINGS_RELATIVE_PATH)
    if (!file.exists()) return
    val existing = parseClaudeSettings(file.readText(Charsets.UTF_8))
    val updated = removeClaudeSettingsEnvKey(existing, ANTHROPIC_BASE_URL_KEY)
    writeFileAtomically(file, serializeClaudeSettings(updated))
}
