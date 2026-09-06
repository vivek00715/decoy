package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Assumptions.assumeTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import java.io.File
import java.net.HttpURLConnection
import java.net.URI

/**
 * Uses a REAL `python -m decoy.cli chat-proxy` process, not a mock --
 * standing in for the bundled binary the real plugin will spawn. Mirrors
 * vscode-extension/src/chatProxyManager.test.ts's approach exactly (same
 * reasoning: the point is proving the manual "run decoy chat-proxy in a
 * terminal yourself" step is actually gone).
 *
 * Set DECOY_TEST_PYTHON to a Python interpreter with this repo's `mcp`
 * and `chat-proxy` extras installed (e.g. its canonical `.venv`).
 * Without it, these tests SKIP (not fail) rather than falsely report a
 * process-lifecycle bug that's actually just a bare `python3` on PATH
 * missing decoy's dependencies -- confirmed directly: running this suite
 * without DECOY_TEST_PYTHON against a dependency-less `python3` produced
 * ConnectException failures that had nothing to do with
 * ChatProxyProcessManager itself.
 */
class ChatProxyProcessManagerTest {

    private val python = System.getenv("DECOY_TEST_PYTHON") ?: "python3"
    private val repoRoot = File(System.getProperty("user.dir")).parentFile // intellij-plugin/ -> repo root
    private val srcPath = File(repoRoot, "src").absolutePath

    @BeforeEach
    fun checkPythonHasDecoyDeps() {
        val check = ProcessBuilder(python, "-c", "import anyio, mcp, starlette, uvicorn, httpx, filelock")
            .redirectErrorStream(true)
            .start()
        val ok = check.waitFor() == 0
        assumeTrue(ok, "$python is missing decoy's chat-proxy dependencies -- set DECOY_TEST_PYTHON to a venv that has them")
    }

    private fun baseEnv(): Map<String, String> =
        System.getenv() + mapOf("PYTHONPATH" to srcPath, "ANTHROPIC_API_KEY" to "sk-ant-test-not-real")

    private fun waitForState(manager: ChatProxyProcessManager, target: ProxyState, timeoutMs: Long = 15000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (manager.state == target) return
            Thread.sleep(100)
        }
        throw AssertionError("timed out waiting for state $target, got ${manager.state}: ${manager.lastError}")
    }

    private fun isAlive(pid: Long): Boolean = ProcessHandle.of(pid).map { it.isAlive }.orElse(false)

    private fun httpGetOnce(url: String): Int {
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        connection.connectTimeout = 2000
        connection.readTimeout = 2000
        return try {
            connection.responseCode
        } finally {
            connection.disconnect()
        }
    }

    /** "running" means the OS process object exists, not that uvicorn has
     * finished binding the port yet -- same real gap confirmed on the VS
     * Code side, same fix (poll, don't assume). */
    private fun httpGetWithRetry(url: String, timeoutMs: Long = 10000): Int {
        val deadline = System.currentTimeMillis() + timeoutMs
        var lastError: Exception? = null
        while (System.currentTimeMillis() < deadline) {
            try {
                return httpGetOnce(url)
            } catch (e: Exception) {
                lastError = e
                Thread.sleep(150)
            }
        }
        throw lastError ?: AssertionError("unreachable")
    }

    @Test
    fun `start spawns a real process that actually serves healthz, replacing the manual terminal step`() {
        val manager = ChatProxyProcessManager(python, listOf("-m", "decoy.cli", "chat-proxy", "--port", "8934"))
        manager.start(baseEnv())
        waitForState(manager, ProxyState.RUNNING)
        val pid = manager.pid
        assertNotNull(pid)
        assertTrue(isAlive(pid!!))

        val status = httpGetWithRetry("http://127.0.0.1:8934/healthz")
        assertEquals(200, status)

        assertTrue(manager.stopAndWait())
        assertEquals(ProxyState.STOPPED, manager.state)
        assertFalse(isAlive(pid))
    }

    @Test
    fun `dispose leaves no orphaned process`() {
        val manager = ChatProxyProcessManager(python, listOf("-m", "decoy.cli", "chat-proxy", "--port", "8935"))
        manager.start(baseEnv())
        waitForState(manager, ProxyState.RUNNING)
        val pid = manager.pid!!

        manager.dispose()

        assertEquals(ProxyState.STOPPED, manager.state)
        assertFalse(isAlive(pid))
    }

    @Test
    fun `restart actually cycles the process -- old pid gone, new pid serving`() {
        val manager = ChatProxyProcessManager(python, listOf("-m", "decoy.cli", "chat-proxy", "--port", "8936"))
        manager.start(baseEnv())
        waitForState(manager, ProxyState.RUNNING)
        val firstPid = manager.pid!!

        manager.restart(baseEnv())
        waitForState(manager, ProxyState.RUNNING)
        val secondPid = manager.pid!!

        assertNotEquals(firstPid, secondPid)
        assertFalse(isAlive(firstPid))
        assertTrue(isAlive(secondPid))
        assertEquals(200, httpGetWithRetry("http://127.0.0.1:8936/healthz"))

        manager.dispose()
    }

    @Test
    fun `calling start twice while running does not spawn a second process`() {
        val manager = ChatProxyProcessManager(python, listOf("-m", "decoy.cli", "chat-proxy", "--port", "8937"))
        manager.start(baseEnv())
        waitForState(manager, ProxyState.RUNNING)
        val pid = manager.pid

        manager.start(baseEnv())
        assertEquals(pid, manager.pid)

        manager.dispose()
    }

    @Test
    fun `a bogus command reports state ERROR instead of hanging or throwing uncaught`() {
        val manager = ChatProxyProcessManager("this-command-does-not-exist-xyz", listOf("--port", "1"))
        manager.start(emptyMap())
        waitForState(manager, ProxyState.ERROR)
        assertNotNull(manager.lastError)
    }
}
