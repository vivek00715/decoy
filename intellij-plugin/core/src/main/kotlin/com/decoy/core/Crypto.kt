package com.decoy.core

import com.macasaet.fernet.Key
import com.macasaet.fernet.StringValidator
import com.macasaet.fernet.Token
import com.macasaet.fernet.TokenValidationException
import java.io.File
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermission
import java.time.Duration

/** Relative path (under the workspace/project root) of the local
 * encryption key file -- matches decoy.crypto.DEFAULT_KEY_PATH on the
 * Python side and DEFAULT_KEY_RELATIVE_PATH in the VS Code extension. */
const val KEY_RELATIVE_PATH = ".decoy/vault.key"

/**
 * A Fernet Validator with no TTL enforcement -- matches Python's
 * `Fernet.decrypt(token)` (no ttl kwarg = no expiration check) and the VS
 * Code extension's `ttl: 0`. fernet-java8's Validator interface defaults
 * to a real TTL, so this must be overridden explicitly; a 100-year TTL is
 * used rather than Long.MAX_VALUE to avoid any risk of Instant arithmetic
 * overflow inside the library.
 */
private val NO_TTL_VALIDATOR = object : StringValidator {
    override fun getTimeToLive(): Duration = Duration.ofDays(36_500)
}

/**
 * Load the local encryption key the same way decoy.crypto.load_or_create_key
 * does on the Python side: DECOY_ENCRYPTION_KEY env var first, else the key
 * file at .decoy/vault.key under `rootDir`. This reader never GENERATES a
 * key -- if neither is present, there is nothing to read yet (no audit log
 * has been written), which callers should treat as "empty", not an error.
 */
fun loadKey(rootDir: File, env: Map<String, String> = System.getenv()): String? {
    env["DECOY_ENCRYPTION_KEY"]?.let { return it }
    val keyFile = File(rootDir, KEY_RELATIVE_PATH)
    if (!keyFile.exists()) return null
    return keyFile.readText(Charsets.UTF_8).trim()
}

/**
 * Decrypt a Fernet token to its plaintext string, or null on ANY failure
 * (missing key, wrong key, corrupt/malformed token) -- fails safe to
 * "nothing readable" rather than throwing, matching the Python and
 * TypeScript readers' behavior.
 */
fun decryptToken(tokenText: String, base64Key: String): String? {
    return try {
        val key = Key(base64Key)
        val token = Token.fromString(tokenText)
        token.validateAndDecrypt(key, NO_TTL_VALIDATOR)
    } catch (_: TokenValidationException) {
        null
    } catch (_: IllegalArgumentException) {
        null
    } catch (_: Exception) {
        null
    }
}

/** Encrypt `plaintext` into a fresh Fernet token using `base64Key`. */
fun encryptToken(plaintext: String, base64Key: String): String {
    val key = Key(base64Key)
    return Token.generate(key, plaintext).serialise()
}

/**
 * Write `text` to `file` atomically (write-then-rename), the same pattern
 * used by decoy.crypto.write_encrypted_json and the VS Code extension's
 * writers, so a reader never sees a half-written file mid-save.
 */
fun writeFileAtomically(file: File, text: String) {
    file.parentFile?.mkdirs()
    val tmp = File(file.parentFile, file.name + ".tmp")
    tmp.writeText(text, Charsets.UTF_8)
    try {
        Files.setPosixFilePermissions(tmp.toPath(), setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE))
    } catch (_: UnsupportedOperationException) {
        // Non-POSIX filesystem (e.g. Windows) -- best-effort permission
        // hardening only; not a correctness requirement.
    }
    Files.move(tmp.toPath(), file.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING)
}
