package com.decoy.core

/**
 * Credential-MODE layer on top of ApiKeyStore.kt -- mirrors
 * vscode-extension/src/credentialConfig.ts exactly (same design, same
 * env vars on the other end). "Set API Key" is now a choice between two
 * ways of reaching an LLM API, not just a single key prompt:
 *
 *   - Direct (API Key): unchanged behavior from ApiKeyStore.kt --
 *     ANTHROPIC_API_KEY sent as `x-api-key` straight to api.anthropic.com.
 *   - Org Gateway (Auth Token): routes through an internal AI gateway
 *     instead, authenticated via `Authorization: Bearer <token>` (see
 *     chat_proxy.py's ANTHROPIC_AUTH_TOKEN support).
 *
 * Same host-free testable pattern as ApiKeyStore.kt: only calls
 * SecretsHost, never PasswordSafe directly -- DecoyProjectService.kt
 * wires the real PasswordSafe-backed implementation; tests use a fake.
 */

const val SECRET_KEY_CREDENTIAL_MODE = "decoy.credentialMode"
const val SECRET_KEY_GATEWAY_AUTH_TOKEN = "decoy.gatewayAuthToken"
const val SECRET_KEY_GATEWAY_BASE_URL = "decoy.gatewayBaseUrl"
const val SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY = "decoy.gatewaySkipTlsVerify"

const val MODE_LABEL_DIRECT = "Direct (API Key)"
const val MODE_LABEL_GATEWAY = "Org Gateway (Auth Token)"

const val GATEWAY_TOKEN_PROMPT =
    "Enter your org gateway auth token (sent as \"Authorization: Bearer <token>\") -- stored in " +
        "your OS's secure credential store, never written to a file."

const val GATEWAY_URL_PROMPT =
    "Enter your org's AI gateway base URL (e.g. https://api-eu1.aigateway.example.com) -- the " +
        "chat proxy will send requests here instead of api.anthropic.com directly."

const val TLS_SKIP_WARNING =
    "This disables certificate verification for outbound requests to your gateway -- only enable " +
        "this if your organization's network requires it (e.g. a TLS-inspecting corporate proxy). " +
        "Leaving this OFF is correct and safe for most setups."

sealed class ResolvedCredentials {
    data class Direct(val apiKey: String) : ResolvedCredentials()
    data class Gateway(val authToken: String, val baseUrl: String, val skipTlsVerify: Boolean) : ResolvedCredentials()
}

/** Runs the "Set Credentials" action: choose a mode, then prompt for
 * that mode's fields. Returns true only if credentials were actually
 * stored (false on cancel at any step). Never mutates anything on
 * cancel. */
fun runSetCredentials(host: SecretsHost): Boolean {
    val choice = host.promptForChoice(
        "How does Decoy's chat proxy reach the LLM API?",
        listOf(MODE_LABEL_DIRECT, MODE_LABEL_GATEWAY),
    ) ?: return false

    if (choice == MODE_LABEL_DIRECT) {
        val stored = runSetApiKey(host)
        if (stored) {
            host.storeSecret(SECRET_KEY_CREDENTIAL_MODE, "direct")
        }
        return stored
    }

    val token = host.promptForText(GATEWAY_TOKEN_PROMPT, password = true)
    if (token.isNullOrBlank()) {
        return false
    }

    val baseUrl = host.promptForText(GATEWAY_URL_PROMPT)
    if (baseUrl.isNullOrBlank()) {
        return false
    }

    // Defaults to OFF and requires an explicit, separately-worded
    // confirmation -- never inferred from the URL prompt.
    val skipTlsVerify = host.confirmDangerousToggle(
        "Skip TLS certificate verification for requests to ${baseUrl.trim()}?\n\n$TLS_SKIP_WARNING",
    )

    host.storeSecret(SECRET_KEY_GATEWAY_AUTH_TOKEN, token.trim())
    host.storeSecret(SECRET_KEY_GATEWAY_BASE_URL, baseUrl.trim())
    host.storeSecret(SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY, if (skipTlsVerify) "true" else "false")
    host.storeSecret(SECRET_KEY_CREDENTIAL_MODE, "gateway")
    host.showInfo(
        "Decoy: org gateway credentials stored securely (${baseUrl.trim()})." +
            if (skipTlsVerify) " TLS verification is DISABLED for this gateway." else "",
    )
    return true
}

fun clearCredentials(host: SecretsHost) {
    host.deleteSecret(SECRET_KEY_ANTHROPIC_API_KEY)
    host.deleteSecret(SECRET_KEY_GATEWAY_AUTH_TOKEN)
    host.deleteSecret(SECRET_KEY_GATEWAY_BASE_URL)
    host.deleteSecret(SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY)
    host.deleteSecret(SECRET_KEY_CREDENTIAL_MODE)
    host.showInfo("Decoy: stored credentials removed.")
}

/**
 * Resolves whichever credentials are already stored (mode-aware), or
 * runs the full "Set Credentials" flow on the spot if none exist yet --
 * same "Start just works" first-run behavior as ApiKeyStore.kt's
 * ensureApiKey, extended to cover both modes.
 *
 * If SECRET_KEY_CREDENTIAL_MODE says "gateway" but the token/URL are
 * missing, this falls through to re-prompting rather than silently
 * resolving to direct mode -- a stale mode flag must never cause
 * requests to silently go straight to Anthropic instead of the org's
 * approved gateway.
 */
fun ensureCredentials(host: SecretsHost): ResolvedCredentials? {
    val mode = host.getSecret(SECRET_KEY_CREDENTIAL_MODE) ?: "direct"

    if (mode == "gateway") {
        val authToken = host.getSecret(SECRET_KEY_GATEWAY_AUTH_TOKEN)
        val baseUrl = host.getSecret(SECRET_KEY_GATEWAY_BASE_URL)
        if (authToken != null && baseUrl != null) {
            val skipTlsVerify = host.getSecret(SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY) == "true"
            return ResolvedCredentials.Gateway(authToken, baseUrl, skipTlsVerify)
        }
    } else {
        val apiKey = host.getSecret(SECRET_KEY_ANTHROPIC_API_KEY)
        if (apiKey != null) {
            return ResolvedCredentials.Direct(apiKey)
        }
    }

    val stored = runSetCredentials(host)
    if (!stored) {
        return null
    }
    return ensureCredentials(host)
}

/** Maps resolved credentials onto the env vars chat_proxy.py actually
 * reads (ChatProxyConfig.from_env(): ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
 * + DECOY_ANTHROPIC_UPSTREAM_URL + DECOY_UPSTREAM_SKIP_TLS_VERIFY) -- kept
 * as its own function so a mismatch between what this plugin sends and
 * what the proxy backend expects is a one-function diff. */
fun buildProxyEnv(creds: ResolvedCredentials, baseEnv: Map<String, String>): Map<String, String> {
    return when (creds) {
        is ResolvedCredentials.Direct -> baseEnv + mapOf("ANTHROPIC_API_KEY" to creds.apiKey)
        is ResolvedCredentials.Gateway -> baseEnv + mapOf(
            "ANTHROPIC_AUTH_TOKEN" to creds.authToken,
            "DECOY_ANTHROPIC_UPSTREAM_URL" to creds.baseUrl,
            "DECOY_UPSTREAM_SKIP_TLS_VERIFY" to if (creds.skipTlsVerify) "true" else "false",
        )
    }
}
