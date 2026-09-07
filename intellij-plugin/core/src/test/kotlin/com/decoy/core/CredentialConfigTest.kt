package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/** A real in-memory implementation, not a mocking-framework double --
 * mirrors vscode-extension/src/credentialConfig.test.ts's makeFakeHost.
 * `store` doubles as the "secure storage" for persistence-across-restart
 * assertions: a fresh FakeCredentialsHost pointed at the SAME store map
 * simulates "the IDE restarted, PasswordSafe is still there". */
class FakeCredentialsHost(
    val store: MutableMap<String, String> = mutableMapOf(),
    private val choice: String? = null,
    private val gatewayToken: String? = null,
    private val gatewayUrl: String? = null,
    private val skipTlsVerify: Boolean = false,
    private val apiKey: String? = null,
) : SecretsHost {
    val infoMessages = mutableListOf<String>()
    val errorMessages = mutableListOf<String>()

    override fun getSecret(key: String): String? = store[key]
    override fun storeSecret(key: String, value: String) { store[key] = value }
    override fun deleteSecret(key: String) { store.remove(key) }
    override fun promptForApiKey(promptText: String): String? = apiKey
    override fun promptForChoice(promptText: String, choices: List<String>): String? = choice
    override fun promptForText(promptText: String, password: Boolean): String? =
        if (promptText.contains("gateway URL") || promptText.contains("base URL")) gatewayUrl else gatewayToken
    override fun confirmDangerousToggle(message: String): Boolean = skipTlsVerify
    override fun showInfo(message: String) { infoMessages.add(message) }
    override fun showError(message: String) { errorMessages.add(message) }
}

class CredentialConfigTest {

    @Test
    fun `runSetCredentials cancel at the mode choice stores nothing`() {
        val host = FakeCredentialsHost(choice = null)
        assertFalse(runSetCredentials(host))
        assertTrue(host.store.isEmpty())
    }

    @Test
    fun `runSetCredentials Direct mode stores the API key and mode flag`() {
        val host = FakeCredentialsHost(choice = MODE_LABEL_DIRECT, apiKey = "sk-ant-abc")
        assertTrue(runSetCredentials(host))
        assertEquals("sk-ant-abc", host.store[SECRET_KEY_ANTHROPIC_API_KEY])
        assertEquals("direct", host.store[SECRET_KEY_CREDENTIAL_MODE])
    }

    @Test
    fun `runSetCredentials Org Gateway mode stores token, URL, mode flag, and TLS-skip=false by default`() {
        val host = FakeCredentialsHost(
            choice = MODE_LABEL_GATEWAY,
            gatewayToken = "  gw-token-123  ",
            gatewayUrl = "https://api-eu1.aigateway.example.com",
        )
        assertTrue(runSetCredentials(host))
        assertEquals("gw-token-123", host.store[SECRET_KEY_GATEWAY_AUTH_TOKEN])
        assertEquals("https://api-eu1.aigateway.example.com", host.store[SECRET_KEY_GATEWAY_BASE_URL])
        assertEquals("false", host.store[SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY])
        assertEquals("gateway", host.store[SECRET_KEY_CREDENTIAL_MODE])
        assertFalse(host.infoMessages[0].contains("gw-token-123"))
    }

    @Test
    fun `runSetCredentials Org Gateway mode with TLS-skip explicitly confirmed stores true and warns in the confirmation`() {
        val host = FakeCredentialsHost(
            choice = MODE_LABEL_GATEWAY, gatewayToken = "gw-token", gatewayUrl = "https://gw.example.com", skipTlsVerify = true,
        )
        runSetCredentials(host)
        assertEquals("true", host.store[SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY])
        assertTrue(host.infoMessages[0].contains("DISABLED"))
    }

    @Test
    fun `runSetCredentials cancelling the token prompt in gateway mode stores nothing`() {
        val host = FakeCredentialsHost(choice = MODE_LABEL_GATEWAY, gatewayToken = null, gatewayUrl = "https://gw.example.com")
        assertFalse(runSetCredentials(host))
        assertTrue(host.store.isEmpty())
    }

    @Test
    fun `runSetCredentials cancelling the URL prompt in gateway mode stores nothing, not even the already-entered token`() {
        val host = FakeCredentialsHost(choice = MODE_LABEL_GATEWAY, gatewayToken = "gw-token", gatewayUrl = null)
        assertFalse(runSetCredentials(host))
        assertTrue(host.store.isEmpty())
    }

    @Test
    fun `ensureCredentials resolves stored Direct-mode credentials without prompting`() {
        val host = FakeCredentialsHost(
            store = mutableMapOf(SECRET_KEY_ANTHROPIC_API_KEY to "sk-ant-existing", SECRET_KEY_CREDENTIAL_MODE to "direct"),
        )
        val creds = ensureCredentials(host)
        assertEquals(ResolvedCredentials.Direct("sk-ant-existing"), creds)
    }

    @Test
    fun `ensureCredentials resolves stored Org Gateway credentials without prompting`() {
        val host = FakeCredentialsHost(
            store = mutableMapOf(
                SECRET_KEY_CREDENTIAL_MODE to "gateway",
                SECRET_KEY_GATEWAY_AUTH_TOKEN to "gw-token",
                SECRET_KEY_GATEWAY_BASE_URL to "https://gw.example.com",
                SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY to "true",
            ),
        )
        val creds = ensureCredentials(host)
        assertEquals(ResolvedCredentials.Gateway("gw-token", "https://gw.example.com", true), creds)
    }

    @Test
    fun `credential-mode choice persists across a simulated IDE restart (fresh host, same backing store)`() {
        val sharedStore = mutableMapOf<String, String>()
        val firstSessionHost = FakeCredentialsHost(
            store = sharedStore, choice = MODE_LABEL_GATEWAY, gatewayToken = "gw-token-persisted", gatewayUrl = "https://gw.example.com",
        )
        runSetCredentials(firstSessionHost)

        // A brand-new host instance pointed at the same store simulates a
        // fresh plugin/IDE activation after a restart -- no prompt values
        // are configured, so if this had to prompt again the assertion
        // below would fail (only getSecret is exercised).
        val secondSessionHost = FakeCredentialsHost(store = sharedStore)
        val creds = ensureCredentials(secondSessionHost)
        assertEquals(ResolvedCredentials.Gateway("gw-token-persisted", "https://gw.example.com", false), creds)
    }

    @Test
    fun `ensureCredentials falls through to re-prompting if mode says gateway but credentials are incomplete`() {
        val host = FakeCredentialsHost(
            store = mutableMapOf(SECRET_KEY_CREDENTIAL_MODE to "gateway"), // token/URL missing
            choice = MODE_LABEL_GATEWAY, gatewayToken = "recovered-token", gatewayUrl = "https://gw.example.com",
        )
        val creds = ensureCredentials(host)
        assertTrue(creds is ResolvedCredentials.Gateway)
        assertEquals("recovered-token", (creds as ResolvedCredentials.Gateway).authToken)
    }

    @Test
    fun `ensureCredentials returns null if the user cancels the prompt with nothing stored yet`() {
        val host = FakeCredentialsHost(choice = null)
        assertNull(ensureCredentials(host))
    }

    @Test
    fun `clearCredentials removes all credential keys for both modes`() {
        val host = FakeCredentialsHost(
            store = mutableMapOf(
                SECRET_KEY_ANTHROPIC_API_KEY to "sk-ant",
                SECRET_KEY_GATEWAY_AUTH_TOKEN to "gw-token",
                SECRET_KEY_GATEWAY_BASE_URL to "https://gw.example.com",
                SECRET_KEY_GATEWAY_SKIP_TLS_VERIFY to "true",
                SECRET_KEY_CREDENTIAL_MODE to "gateway",
            ),
        )
        clearCredentials(host)
        assertTrue(host.store.isEmpty())
    }

    @Test
    fun `buildProxyEnv Direct mode sets ANTHROPIC_API_KEY only, matching chat_proxy py's ChatProxyConfig from_env`() {
        val env = buildProxyEnv(ResolvedCredentials.Direct("sk-ant-abc"), mapOf("PATH" to "/usr/bin"))
        assertEquals("sk-ant-abc", env["ANTHROPIC_API_KEY"])
        assertNull(env["ANTHROPIC_AUTH_TOKEN"])
        assertNull(env["DECOY_ANTHROPIC_UPSTREAM_URL"])
        assertEquals("/usr/bin", env["PATH"])
    }

    @Test
    fun `buildProxyEnv Org Gateway mode sets ANTHROPIC_AUTH_TOKEN, DECOY_ANTHROPIC_UPSTREAM_URL, and DECOY_UPSTREAM_SKIP_TLS_VERIFY`() {
        val creds = ResolvedCredentials.Gateway("gw-token-xyz", "https://api-eu1.aigateway.emirates.group", true)
        val env = buildProxyEnv(creds, emptyMap())
        assertEquals("gw-token-xyz", env["ANTHROPIC_AUTH_TOKEN"])
        assertEquals("https://api-eu1.aigateway.emirates.group", env["DECOY_ANTHROPIC_UPSTREAM_URL"])
        assertEquals("true", env["DECOY_UPSTREAM_SKIP_TLS_VERIFY"])
        assertNull(env["ANTHROPIC_API_KEY"])
    }

    @Test
    fun `buildProxyEnv Org Gateway mode with TLS-skip off sends the flag as the literal string 'false', not omitted`() {
        val creds = ResolvedCredentials.Gateway("t", "https://gw.example.com", false)
        val env = buildProxyEnv(creds, emptyMap())
        assertEquals("false", env["DECOY_UPSTREAM_SKIP_TLS_VERIFY"])
    }
}
