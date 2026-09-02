package com.decoy.core

import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

const val OVERRIDES_RELATIVE_PATH = ".decoy/overrides.json"

private val json = Json { ignoreUnknownKeys = true; prettyPrint = true }

/**
 * Read .decoy/overrides.json. Missing or malformed files fail safe to an
 * empty OverridesFile -- matching decoy.overrides.OverrideStore's Python
 * behavior and the VS Code extension's readOverrides(): an unreadable
 * overrides file means no manual overrides are active, not a crash.
 */
fun readOverrides(rootDir: File): OverridesFile {
    val file = File(rootDir, OVERRIDES_RELATIVE_PATH)
    if (!file.exists()) return emptyOverridesFile()
    return try {
        json.decodeFromString<OverridesFile>(file.readText(Charsets.UTF_8))
    } catch (_: SerializationException) {
        emptyOverridesFile()
    } catch (_: IllegalArgumentException) {
        emptyOverridesFile()
    }
}

/**
 * Write .decoy/overrides.json atomically (write-then-rename), the same
 * pattern used elsewhere in this project, so a reader (this plugin's own
 * file watcher, or the Python OverrideStore's mtime-based reload) never
 * sees a half-written file.
 */
fun writeOverrides(rootDir: File, overrides: OverridesFile) {
    writeFileAtomically(File(rootDir, OVERRIDES_RELATIVE_PATH), json.encodeToString(overrides))
}

/**
 * Reset .decoy/overrides.json to empty. Used by the "Clear All Local
 * Data" action -- unlike clearing the audit log/vault, this is
 * destructive on the user's own configured rules, so callers must only
 * invoke this after explicit confirmation. No-op if no overrides file
 * exists yet.
 */
fun clearOverrides(rootDir: File) {
    if (!File(rootDir, OVERRIDES_RELATIVE_PATH).exists()) return
    writeOverrides(rootDir, emptyOverridesFile())
}
