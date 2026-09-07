package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/** A real in-memory implementation, not a mocking-framework double --
 * PasswordSafe itself is never referenced here (see ApiKeyStore.kt's
 * module docstring for why it can't be, this deep in a plain JUnit
 * test), so this fake stands in for "some credential store," exactly
 * the role vscode-extension/src/apiKeyCommand.test.ts's fake host plays
 * on the other side. */
class FakeSecretsHost(
    initial: MutableMap<String, String> = mutableMapOf(),
    private val promptValue: String?,
    private val choiceValue: String? = null,
    private val confirmValue: Boolean = false,
) : SecretsHost {
    val store: MutableMap<String, String> = initial
    val infoMessages = mutableListOf<String>()
    val errorMessages = mutableListOf<String>()
    val promptCalls = mutableListOf<String>()

    override fun getSecret(key: String): String? = store[key]
    override fun storeSecret(key: String, value: String) { store[key] = value }
    override fun deleteSecret(key: String) { store.remove(key) }
    override fun promptForApiKey(promptText: String): String? {
        promptCalls.add(promptText)
        return promptValue
    }
    override fun promptForChoice(promptText: String, choices: List<String>): String? {
        promptCalls.add(promptText)
        return choiceValue
    }
    override fun promptForText(promptText: String, password: Boolean): String? {
        promptCalls.add(promptText)
        return promptValue
    }
    override fun confirmDangerousToggle(message: String): Boolean {
        promptCalls.add(message)
        return confirmValue
    }
    override fun showInfo(message: String) { infoMessages.add(message) }
    override fun showError(message: String) { errorMessages.add(message) }
}

class ApiKeyStoreTest {

    @Test
    fun `runSetApiKey stores a trimmed non-empty key and confirms without echoing the key itself`() {
        val host = FakeSecretsHost(promptValue = "  sk-ant-abc123  ")
        val stored = runSetApiKey(host)
        assertTrue(stored)
        assertEquals("sk-ant-abc123", host.store[SECRET_KEY_ANTHROPIC_API_KEY])
        assertEquals(1, host.infoMessages.size)
        assertFalse(host.infoMessages[0].contains("sk-ant-abc123"))
    }

    @Test
    fun `runSetApiKey does nothing on cancel (null) and reports no key stored`() {
        val host = FakeSecretsHost(promptValue = null)
        val stored = runSetApiKey(host)
        assertFalse(stored)
        assertNull(host.store[SECRET_KEY_ANTHROPIC_API_KEY])
        assertTrue(host.infoMessages.isEmpty())
    }

    @Test
    fun `runSetApiKey treats a blank whitespace-only entry the same as cancel`() {
        val host = FakeSecretsHost(promptValue = "   ")
        val stored = runSetApiKey(host)
        assertFalse(stored)
        assertNull(host.store[SECRET_KEY_ANTHROPIC_API_KEY])
    }

    @Test
    fun `runSetApiKey prompt text names the Pro-subscription-does-not-work distinction`() {
        val host = FakeSecretsHost(promptValue = "sk-ant-x")
        runSetApiKey(host)
        assertTrue(host.promptCalls[0].contains("Pro"))
        assertTrue(host.promptCalls[0].contains("billing", ignoreCase = true))
    }

    @Test
    fun `clearApiKey removes the stored key`() {
        val host = FakeSecretsHost(mutableMapOf(SECRET_KEY_ANTHROPIC_API_KEY to "sk-ant-existing"), promptValue = null)
        clearApiKey(host)
        assertNull(host.store[SECRET_KEY_ANTHROPIC_API_KEY])
        assertEquals(1, host.infoMessages.size)
    }

    @Test
    fun `ensureApiKey returns the existing key without prompting when one is already stored`() {
        val host = FakeSecretsHost(mutableMapOf(SECRET_KEY_ANTHROPIC_API_KEY to "sk-ant-existing"), promptValue = null)
        val key = ensureApiKey(host)
        assertEquals("sk-ant-existing", key)
        assertTrue(host.promptCalls.isEmpty())
    }

    @Test
    fun `ensureApiKey prompts and stores when no key exists yet, then returns it`() {
        val host = FakeSecretsHost(promptValue = "sk-ant-first-time")
        val key = ensureApiKey(host)
        assertEquals("sk-ant-first-time", key)
        assertEquals(1, host.promptCalls.size)
        assertEquals("sk-ant-first-time", host.store[SECRET_KEY_ANTHROPIC_API_KEY])
    }

    @Test
    fun `ensureApiKey returns null if the user cancels the first-run prompt, storing nothing`() {
        val host = FakeSecretsHost(promptValue = null)
        val key = ensureApiKey(host)
        assertNull(key)
        assertNull(host.store[SECRET_KEY_ANTHROPIC_API_KEY])
    }
}
