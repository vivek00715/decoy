package com.decoy.core

import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

private const val TEST_KEY = "nhtXQTnXQzKW_0m_5r-ffK8dcl03BLSzJOOdmF8bDk4="
private val ENV = mapOf("DECOY_ENCRYPTION_KEY" to TEST_KEY)
private val json = Json

private fun writeVaultFixture(root: File, data: Map<String, Map<String, String>>) {
    File(root, ".decoy").mkdirs()
    val token = encryptToken(json.encodeToString(data), TEST_KEY)
    File(root, VAULT_RELATIVE_PATH).writeText(token)
}

private fun readVaultFixture(root: File): Map<String, Map<String, String>> {
    val raw = File(root, VAULT_RELATIVE_PATH).readText()
    val decrypted = decryptToken(raw, TEST_KEY)!!
    return json.decodeFromString(decrypted)
}

class VaultStoreTest {

    @Test
    fun `clearVaultSession removes only the named session's mappings`(@TempDir tmp: File) {
        writeVaultFixture(
            tmp,
            mapOf(
                "session-a" to mapOf("alice@example.com" to "fake-a@example.net"),
                "session-b" to mapOf("bob@example.com" to "fake-b@example.net"),
            ),
        )

        clearVaultSession(tmp, "session-a", ENV)

        assertEquals(mapOf("session-b" to mapOf("bob@example.com" to "fake-b@example.net")), readVaultFixture(tmp))
    }

    @Test
    fun `clearVaultSession does nothing if no vault file exists`(@TempDir tmp: File) {
        clearVaultSession(tmp, "session-a", ENV)
        assertFalse(File(tmp, VAULT_RELATIVE_PATH).exists())
    }

    @Test
    fun `clearVaultAll empties every session but keeps the file (matches Python's clear_all semantics)`(@TempDir tmp: File) {
        writeVaultFixture(tmp, mapOf("session-a" to mapOf("x@example.com" to "y@example.net")))

        clearVaultAll(tmp, ENV)

        assert(File(tmp, VAULT_RELATIVE_PATH).exists())
        assertEquals(emptyMap<String, Map<String, String>>(), readVaultFixture(tmp))
    }
}
