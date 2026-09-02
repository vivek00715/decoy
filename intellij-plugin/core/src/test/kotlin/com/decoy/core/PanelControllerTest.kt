package com.decoy.core

import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

private const val TEST_KEY = "nhtXQTnXQzKW_0m_5r-ffK8dcl03BLSzJOOdmF8bDk4="
private val ENV = mapOf("DECOY_ENCRYPTION_KEY" to TEST_KEY)

private class FakeHost(override val rootDir: File, private val confirmResult: Boolean = true) : PanelHost {
    val updates = mutableListOf<Pair<List<RequestGroup>, OverridesFile>>()
    var confirmMessages = mutableListOf<String>()

    override fun updateView(groups: List<RequestGroup>, overrides: OverridesFile) {
        updates.add(groups to overrides)
    }

    override fun confirmDestructive(message: String): Boolean {
        confirmMessages.add(message)
        return confirmResult
    }
}

private fun entry(
    id: String,
    requestId: String,
    sessionId: String,
    field: String = "EMAIL",
) = AuditEntry(id, requestId, sessionId, "2026-08-30T21:15:03.482Z", "prompt_text", field, "masked", "r", "regex")

private fun writeVaultFixture(root: File, data: Map<String, Map<String, String>>) {
    File(root, ".decoy").mkdirs()
    File(root, VAULT_RELATIVE_PATH).writeText(encryptToken(Json.encodeToString(data), TEST_KEY))
}

private fun readVaultFixture(root: File): Map<String, Map<String, String>> {
    val decrypted = decryptToken(File(root, VAULT_RELATIVE_PATH).readText(), TEST_KEY)!!
    return Json.decodeFromString(decrypted)
}

class PanelControllerTest {

    @Test
    fun `refresh pushes current audit log and overrides to the host`(@TempDir tmp: File) {
        writeAuditLog(tmp, AuditLogFile(1, listOf(entry("e1", "req-1", "session-1"))), ENV)
        withEnv(ENV) {
            val host = FakeHost(tmp)
            PanelController(host).refresh()
            assertEquals(1, host.updates.size)
            assertEquals(1, host.updates[0].first.size)
        }
    }

    @Test
    fun `clearSession clears audit and vault for that session only, no confirmation needed`(@TempDir tmp: File) {
        writeAuditLog(
            tmp,
            AuditLogFile(1, listOf(entry("e1", "r1", "session-a"), entry("e2", "r2", "session-b", "PHONE"))),
            ENV,
        )
        writeVaultFixture(
            tmp,
            mapOf(
                "session-a" to mapOf("alice@example.com" to "fake-a@example.net"),
                "session-b" to mapOf("bob@example.com" to "fake-b@example.net"),
            ),
        )
        withEnv(ENV) {
            val host = FakeHost(tmp)
            PanelController(host).clearSession("session-a")

            assertEquals(mapOf("session-b" to mapOf("bob@example.com" to "fake-b@example.net")), readVaultFixture(tmp))
            val lastGroups = host.updates.last().first
            assertEquals(1, lastGroups.size)
            assertEquals("session-b", lastGroups[0].sessionId)
            assertTrue(host.confirmMessages.isEmpty()) // per-session clear doesn't need a confirm dialog
        }
    }

    @Test
    fun `clearSessionDataOnly does nothing when the host declines confirmation`(@TempDir tmp: File) {
        writeAuditLog(tmp, AuditLogFile(1, listOf(entry("e1", "r1", "s1"))), ENV)
        withEnv(ENV) {
            val host = FakeHost(tmp, confirmResult = false)
            PanelController(host).clearSessionDataOnly()
            assertTrue(host.updates.isEmpty())
        }
    }

    @Test
    fun `clearSessionDataOnly clears audit and vault for every session but KEEPS overrides`(@TempDir tmp: File) {
        writeAuditLog(tmp, AuditLogFile(1, listOf(entry("e1", "r1", "s1"))), ENV)
        writeVaultFixture(tmp, mapOf("s1" to mapOf("a@example.com" to "b@example.net")))
        val overrides = OverridesFile(always_mask = OverrideRules(field_names = listOf("employee_id")))
        writeOverrides(tmp, overrides)

        withEnv(ENV) {
            val host = FakeHost(tmp, confirmResult = true)
            PanelController(host).clearSessionDataOnly()

            assertEquals(emptyMap<String, Map<String, String>>(), readVaultFixture(tmp))
            assertEquals(overrides, readOverrides(tmp)) // kept
            assertTrue(host.updates.last().first.isEmpty())
            assertEquals(overrides, host.updates.last().second)
        }
    }

    @Test
    fun `clearAll removes EVERYTHING when confirmed, including overrides -- no unstated exception`(@TempDir tmp: File) {
        writeAuditLog(tmp, AuditLogFile(1, listOf(entry("e1", "r1", "s1"))), ENV)
        writeVaultFixture(tmp, mapOf("s1" to mapOf("a@example.com" to "b@example.net")))
        writeOverrides(
            tmp,
            OverridesFile(
                always_mask = OverrideRules(field_names = listOf("employee_id")),
                never_mask = OverrideRules(field_names = listOf("status")),
            ),
        )

        withEnv(ENV) {
            val host = FakeHost(tmp, confirmResult = true)
            PanelController(host).clearAll()

            assertEquals(emptyMap<String, Map<String, String>>(), readVaultFixture(tmp))
            assertEquals(emptyOverridesFile(), readOverrides(tmp))
            assertTrue(host.updates.last().first.isEmpty())
            assertEquals(emptyOverridesFile(), host.updates.last().second)
        }
    }

    @Test
    fun `saveOverrides persists and is reflected in the next update`(@TempDir tmp: File) {
        withEnv(ENV) {
            val host = FakeHost(tmp)
            val overrides = OverridesFile(always_mask = OverrideRules(field_names = listOf("employee_id")))
            PanelController(host).saveOverrides(overrides)

            assertEquals(overrides, readOverrides(tmp))
            assertEquals(overrides, host.updates.last().second)
        }
    }
}

/**
 * JVM processes can't mutate real environment variables per-test safely,
 * so key loading in these tests goes through the `env` parameter directly
 * wherever the code under test accepts one. `withEnv` exists only for
 * readability at call sites that don't thread it explicitly (readAuditLog
 * etc. inside PanelController always use System.getenv() by default) --
 * this project passes DECOY_ENCRYPTION_KEY via the JVM's real environment
 * when running these tests (see core/build.gradle.kts test task) so
 * PanelController's calls into readAuditLog/writeAuditLog/etc, which have
 * no way to receive a custom `env` from PanelHost, still resolve the key.
 */
private inline fun withEnv(env: Map<String, String>, block: () -> Unit) {
    require(System.getenv("DECOY_ENCRYPTION_KEY") == env["DECOY_ENCRYPTION_KEY"]) {
        "Test JVM must be launched with DECOY_ENCRYPTION_KEY=$TEST_KEY " +
            "(configured in core/build.gradle.kts's test task) for PanelControllerTest to pass."
    }
    block()
}
