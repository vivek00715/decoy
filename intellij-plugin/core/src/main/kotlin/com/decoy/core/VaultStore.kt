package com.decoy.core

import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

const val VAULT_RELATIVE_PATH = ".decoy/vault.enc"

private val json = Json { ignoreUnknownKeys = true }

// The vault's decrypted shape: session_id -> { original: fake }. This
// plugin never reads original/fake VALUES for display -- it only needs
// the ability to remove a whole session's entry (or all of them) when the
// user clears data from the UI, mirroring
// decoy.vault.VaultManager.clear_session/clear_all on the Python side.
private typealias VaultData = Map<String, Map<String, String>>

private fun readVaultRaw(rootDir: File, env: Map<String, String>): VaultData {
    val vaultFile = File(rootDir, VAULT_RELATIVE_PATH)
    if (!vaultFile.exists()) return emptyMap()
    val key = loadKey(rootDir, env) ?: return emptyMap()
    val decrypted = decryptToken(vaultFile.readText(Charsets.UTF_8), key) ?: return emptyMap()
    return try {
        json.decodeFromString<VaultData>(decrypted)
    } catch (_: SerializationException) {
        emptyMap()
    } catch (_: IllegalArgumentException) {
        emptyMap()
    }
}

private fun writeVaultRaw(rootDir: File, data: VaultData, env: Map<String, String>): Boolean {
    val key = loadKey(rootDir, env) ?: return false
    val encoded = encryptToken(json.encodeToString(data), key)
    writeFileAtomically(File(rootDir, VAULT_RELATIVE_PATH), encoded)
    return true
}

/** Remove one session's original<->fake mappings from the vault, if a
 * persisted vault file exists at all (in-memory-only vaults have nothing
 * on disk for this plugin to touch). */
fun clearVaultSession(rootDir: File, sessionId: String, env: Map<String, String> = System.getenv()) {
    if (!File(rootDir, VAULT_RELATIVE_PATH).exists()) return
    val data = readVaultRaw(rootDir, env).toMutableMap()
    data.remove(sessionId)
    writeVaultRaw(rootDir, data, env)
}

/** Remove every session's mappings from the vault, if a persisted vault
 * file exists. Writes back an empty map rather than deleting the file,
 * matching VaultManager.clear_all()'s Python-side behavior. */
fun clearVaultAll(rootDir: File, env: Map<String, String> = System.getenv()) {
    if (!File(rootDir, VAULT_RELATIVE_PATH).exists()) return
    writeVaultRaw(rootDir, emptyMap(), env)
}
