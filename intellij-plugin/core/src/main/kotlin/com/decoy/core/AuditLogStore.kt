package com.decoy.core

import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

const val AUDIT_LOG_RELATIVE_PATH = ".decoy/audit.enc"

private val json = Json { ignoreUnknownKeys = true }
private val EMPTY_LOG = AuditLogFile(format_version = SUPPORTED_FORMAT_VERSION, entries = emptyList())

/**
 * Decrypt and parse the audit log at .decoy/audit.enc under `rootDir`.
 * Fails safe: any missing file, missing key, decryption failure, malformed
 * JSON, or unsupported format_version returns an EMPTY log (never throws
 * into the caller, never guesses at a shape it doesn't recognize) --
 * matching decoy.audit_log.AuditLog._load() and the VS Code extension's
 * readAuditLog().
 */
fun readAuditLog(rootDir: File, env: Map<String, String> = System.getenv()): AuditLogFile {
    val logFile = File(rootDir, AUDIT_LOG_RELATIVE_PATH)
    if (!logFile.exists()) return EMPTY_LOG

    val key = loadKey(rootDir, env) ?: return EMPTY_LOG

    val decrypted = decryptToken(logFile.readText(Charsets.UTF_8), key) ?: return EMPTY_LOG

    val parsed = try {
        json.decodeFromString<AuditLogFile>(decrypted)
    } catch (_: SerializationException) {
        return EMPTY_LOG
    } catch (_: IllegalArgumentException) {
        return EMPTY_LOG
    }

    if (parsed.format_version != SUPPORTED_FORMAT_VERSION) {
        // Deliberate compatibility gate -- see AUDIT_LOG_FORMAT.md's
        // "Versioning / migration story". Never guess at an unrecognized shape.
        return EMPTY_LOG
    }
    return parsed
}

/**
 * Encrypt and atomically write an AuditLogFile back to .decoy/audit.enc.
 * No-op (returns false) if there is no key available -- clearing a log
 * that doesn't exist yet is not an error.
 */
fun writeAuditLog(rootDir: File, data: AuditLogFile, env: Map<String, String> = System.getenv()): Boolean {
    val key = loadKey(rootDir, env) ?: return false
    val encoded = encryptToken(json.encodeToString(data), key)
    writeFileAtomically(File(rootDir, AUDIT_LOG_RELATIVE_PATH), encoded)
    return true
}

/** Remove all entries for one session_id. Returns the number removed. */
fun clearAuditSession(rootDir: File, sessionId: String, env: Map<String, String> = System.getenv()): Int {
    val current = readAuditLog(rootDir, env)
    val filtered = current.entries.filter { it.session_id != sessionId }
    writeAuditLog(rootDir, current.copy(entries = filtered), env)
    return current.entries.size - filtered.size
}

/** Remove every entry. Returns the number removed. */
fun clearAuditAll(rootDir: File, env: Map<String, String> = System.getenv()): Int {
    val current = readAuditLog(rootDir, env)
    writeAuditLog(rootDir, current.copy(entries = emptyList()), env)
    return current.entries.size
}

/** Group flat entries by request_id, newest request first. */
fun groupByRequest(entries: List<AuditEntry>): List<RequestGroup> {
    return entries
        .groupBy { it.request_id }
        .map { (requestId, groupEntries) ->
            RequestGroup(
                requestId = requestId,
                sessionId = groupEntries.first().session_id,
                timestamp = groupEntries.first().timestamp,
                entries = groupEntries,
            )
        }
        .sortedByDescending { it.timestamp }
}
