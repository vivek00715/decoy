package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

private const val TEST_KEY = "nhtXQTnXQzKW_0m_5r-ffK8dcl03BLSzJOOdmF8bDk4="
private val ENV = mapOf("DECOY_ENCRYPTION_KEY" to TEST_KEY)

private fun entry(
    id: String = "e1",
    requestId: String = "req-1",
    sessionId: String = "session-1",
    timestamp: String = "2026-08-30T21:15:03.482Z",
    source: String = "prompt_text",
    field: String = "EMAIL",
    decision: String = "masked",
    reason: String = "matched EMAIL regex",
    layer: String = "regex",
) = AuditEntry(id, requestId, sessionId, timestamp, source, field, decision, reason, layer)

class AuditLogStoreTest {

    @Test
    fun `write then read round-trips exactly, encrypted at rest`(@TempDir tmp: File) {
        val data = AuditLogFile(1, listOf(entry()))
        writeAuditLog(tmp, data, ENV)

        val rawBytes = File(tmp, AUDIT_LOG_RELATIVE_PATH).readText()
        assertTrue(!rawBytes.contains("EMAIL"))
        assertEquals(data, readAuditLog(tmp, ENV))
    }

    @Test
    fun `fails safe to empty on a missing file`(@TempDir tmp: File) {
        assertEquals(AuditLogFile(1, emptyList()), readAuditLog(tmp, ENV))
    }

    @Test
    fun `fails safe to empty on a corrupted file rather than throwing`(@TempDir tmp: File) {
        File(tmp, ".decoy").mkdirs()
        File(tmp, AUDIT_LOG_RELATIVE_PATH).writeText("not a valid fernet token")
        assertEquals(emptyList<AuditEntry>(), readAuditLog(tmp, ENV).entries)
    }

    @Test
    fun `fails safe to empty with the wrong key rather than misreading garbage`(@TempDir tmp: File) {
        writeAuditLog(tmp, AuditLogFile(1, listOf(entry())), ENV)
        val wrongEnv = mapOf("DECOY_ENCRYPTION_KEY" to "b3RoZXJrZXlvdGhlcmtleW90aGVya2V5b3RoZXJrZXk=")
        assertEquals(emptyList<AuditEntry>(), readAuditLog(tmp, wrongEnv).entries)
    }

    @Test
    fun `fails safe to empty on an unrecognized format_version`(@TempDir tmp: File) {
        writeAuditLog(tmp, AuditLogFile(999, listOf(entry())), ENV)
        assertEquals(emptyList<AuditEntry>(), readAuditLog(tmp, ENV).entries)
    }

    @Test
    fun `clearAuditSession only removes entries for that session`(@TempDir tmp: File) {
        writeAuditLog(
            tmp,
            AuditLogFile(1, listOf(entry(id = "a", sessionId = "session-a"), entry(id = "b", sessionId = "session-b"))),
            ENV,
        )

        val removed = clearAuditSession(tmp, "session-a", ENV)

        assertEquals(1, removed)
        val remaining = readAuditLog(tmp, ENV).entries
        assertEquals(1, remaining.size)
        assertEquals("session-b", remaining[0].session_id)
    }

    @Test
    fun `clearAuditAll removes everything`(@TempDir tmp: File) {
        writeAuditLog(tmp, AuditLogFile(1, listOf(entry(id = "a"), entry(id = "b"))), ENV)

        val removed = clearAuditAll(tmp, ENV)

        assertEquals(2, removed)
        assertEquals(emptyList<AuditEntry>(), readAuditLog(tmp, ENV).entries)
    }

    @Test
    fun `groupByRequest groups by request_id and sorts newest first`() {
        val entries = listOf(
            entry(id = "1", requestId = "req-old", timestamp = "2026-01-01T00:00:00.000Z"),
            entry(id = "2", requestId = "req-new", timestamp = "2026-06-01T00:00:00.000Z"),
            entry(id = "3", requestId = "req-old", field = "PHONE", timestamp = "2026-01-01T00:00:00.001Z"),
        )
        val groups = groupByRequest(entries)

        assertEquals(2, groups.size)
        assertEquals("req-new", groups[0].requestId)
        assertEquals("req-old", groups[1].requestId)
        assertEquals(2, groups[1].entries.size)
    }
}
