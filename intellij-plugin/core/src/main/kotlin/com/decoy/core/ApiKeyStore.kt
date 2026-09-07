package com.decoy.core

/**
 * Stores the Anthropic API key using IntelliJ's PasswordSafe (backed by
 * the OS-native credential store -- Keychain on macOS, Credential
 * Manager on Windows, libsecret/kwallet on Linux), not a manually-
 * exported shell variable or a plaintext file. Manual step this
 * replaces: before this, getting a masked request through required
 * opening a terminal and `export ANTHROPIC_API_KEY=...` by hand, every
 * session. Mirrors vscode-extension/src/apiKeyCommand.ts exactly (same
 * design, same manual step being removed).
 *
 * `SecretsHost` is deliberately the ONLY thing this file knows about --
 * no `com.intellij.ide.passwordSafe.PasswordSafe` import here, no other
 * IntelliJ Platform SDK reference at all. This is not incidental: unlike
 * chatProxyManager/claudeSettingsConfig/mcpApproval's Kotlin ports (all
 * of which wrap something that runs identically with or without the
 * IDE -- a subprocess, a file, another CLI), PasswordSafe ITSELF only
 * exists inside a running IntelliJ Application (`PasswordSafe.getInstance()`
 * requires `ApplicationManager.getApplication()` to be a real,
 * initialized IDE application) -- there is no way to construct or call
 * the real PasswordSafe from a plain JUnit test in this SDK-free `core`
 * module, full stop. That's WHY this file takes the same shape as the
 * VS Code side's `SecretsHost` abstraction: the actual credential-store
 * call is pushed behind an interface, so the LOGIC around it (prompt if
 * missing, treat blank as cancel, don't re-prompt if already set) is
 * still genuinely unit-tested here with a fake in-memory implementation
 * -- not because PasswordSafe itself is being mocked (it never appears
 * in this file at all), but because the interface boundary is exactly
 * where core/plugin have always split in this project. The REAL
 * PasswordSafe-backed `SecretsHost` implementation lives in `plugin`
 * (DecoyProjectService.kt) and is compile-verified only -- confirming it
 * actually round-trips through the OS keychain needs a real running IDE,
 * which is disclosed plainly, not glossed over.
 */

const val SECRET_KEY_ANTHROPIC_API_KEY = "decoy.anthropicApiKey"

interface SecretsHost {
    fun getSecret(key: String): String?
    fun storeSecret(key: String, value: String)
    fun deleteSecret(key: String)
    /** Returns the entered value, or null if the user cancelled. */
    fun promptForApiKey(promptText: String): String?
    /** Fixed-choice prompt (e.g. Messages.showChooseDialog) -- null if
     * the user cancelled/escaped. Used by CredentialConfig.kt's mode
     * choice; mirrors vscode-extension's SecretsHost.promptForChoice. */
    fun promptForChoice(promptText: String, choices: List<String>): String?
    /** Free-text prompt (gateway token/URL) -- null if cancelled.
     * `password = true` masks input the same way promptForApiKey does. */
    fun promptForText(promptText: String, password: Boolean = false): String?
    /** Explicit yes/no confirmation for a risky toggle (TLS verification
     * skip) -- returns false on cancel or an explicit "No". */
    fun confirmDangerousToggle(message: String): Boolean
    fun showInfo(message: String)
    fun showError(message: String)
}

const val API_KEY_PROMPT_TEXT =
    "Enter your Anthropic API key (from console.anthropic.com) -- stored in your OS's secure " +
        "credential store, never written to a file. This is a separate, billed API key: a Claude " +
        "Pro/Max subscription's login does not work here (different credential system, different " +
        "billing) -- see README.md's chat-proxy section."

/** Runs the "Set API Key" action: prompt, validate non-empty, store.
 * Returns true if a key was actually stored (false on cancel/blank). */
fun runSetApiKey(host: SecretsHost): Boolean {
    val value = host.promptForApiKey(API_KEY_PROMPT_TEXT)
    if (value.isNullOrBlank()) {
        return false
    }
    host.storeSecret(SECRET_KEY_ANTHROPIC_API_KEY, value.trim())
    host.showInfo("Decoy: Anthropic API key stored securely. The chat proxy can now be started.")
    return true
}

fun clearApiKey(host: SecretsHost) {
    host.deleteSecret(SECRET_KEY_ANTHROPIC_API_KEY)
    host.showInfo("Decoy: stored Anthropic API key removed.")
}

/**
 * Used right before starting the chat proxy: returns the stored key, or
 * prompts for one on the spot if none is set yet (so "Start Proxy" works
 * as a single action for a first-time user instead of requiring a
 * separate "Set API Key" step first). Returns null only if the user
 * cancels the prompt.
 */
fun ensureApiKey(host: SecretsHost): String? {
    val existing = host.getSecret(SECRET_KEY_ANTHROPIC_API_KEY)
    if (existing != null) {
        return existing
    }
    val stored = runSetApiKey(host)
    if (!stored) {
        return null
    }
    return host.getSecret(SECRET_KEY_ANTHROPIC_API_KEY)
}
