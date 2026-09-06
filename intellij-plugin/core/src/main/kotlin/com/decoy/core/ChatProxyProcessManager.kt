package com.decoy.core

import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Manages the chat proxy's ENTIRE process lifecycle from inside the
 * plugin -- the manual step this replaces: before this, getting a masked
 * request to show up in the tool window required opening a terminal,
 * `export ANTHROPIC_API_KEY=...`, running `decoy chat-proxy` by hand, and
 * keeping that terminal open for the life of the IDE session.
 *
 * Deliberately built on plain `java.lang.ProcessBuilder`, not any
 * IntelliJ Platform process API (`GeneralCommandLine`/`ProcessHandler`)
 * -- this keeps the actual lifecycle logic (spawn, track state, kill
 * cleanly with no orphan) SDK-free and genuinely testable with plain
 * JUnit against a REAL child process, the same way
 * vscode-extension/src/chatProxyManager.ts is tested on the VS Code
 * side. The `plugin` module wraps an instance of this class in whatever
 * IntelliJ-idiomatic disposal hook is appropriate (a `Disposable`
 * registered against the project, per Phase 13's established pattern)
 * -- that wiring is SDK-dependent and covered by compile verification
 * only, not a real IDE run (see MANUAL_TEST.md).
 */

enum class ProxyState { STOPPED, STARTING, RUNNING, STOPPING, ERROR }

class ChatProxyProcessManager(
    private val command: String,
    private val args: List<String>,
) {
    @Volatile private var process: Process? = null
    @Volatile var state: ProxyState = ProxyState.STOPPED
        private set
    @Volatile var lastError: String? = null
        private set

    val pid: Long?
        get() = process?.takeIf { it.isAlive }?.pid()

    /** No-op if already running/starting -- calling start() twice must
     * never spawn two processes. */
    fun start(env: Map<String, String>, workDir: File? = null) {
        if (state == ProxyState.RUNNING || state == ProxyState.STARTING) return
        state = ProxyState.STARTING
        lastError = null

        try {
            val builder = ProcessBuilder(listOf(command) + args)
            builder.environment().putAll(env)
            if (workDir != null) builder.directory(workDir)
            builder.redirectErrorStream(false)
            val proc = builder.start()
            process = proc
            // ProcessBuilder.start() either succeeds (process exists,
            // even if it exits moments later) or throws -- there is no
            // separate "spawn" event to wait for the way Node's
            // child_process has, so RUNNING is reported as soon as the
            // OS process object exists. A process that dies immediately
            // after (bad port, missing extra) is caught by the reaper
            // thread below, same as Node's 'exit' handler.
            state = ProxyState.RUNNING

            Thread({
                val exitCode = proc.waitFor()
                if (process === proc) {
                    process = null
                    state = if (state == ProxyState.STOPPING || exitCode == 0) {
                        ProxyState.STOPPED
                    } else {
                        lastError = "proxy exited unexpectedly (code=$exitCode)"
                        ProxyState.ERROR
                    }
                }
            }, "decoy-chat-proxy-reaper").apply { isDaemon = true; start() }
        } catch (exc: Exception) {
            state = ProxyState.ERROR
            lastError = exc.message ?: exc.toString()
        }
    }

    /** Sends SIGTERM (Process.destroy()) and waits up to [timeoutSeconds]
     * for real exit, escalating to SIGKILL (destroyForcibly()) if the
     * process ignores it -- never returns while the process is still
     * alive on a normal path, so a caller awaiting this can safely assume
     * no orphan is left once it returns. */
    fun stopAndWait(timeoutSeconds: Long = 5): Boolean {
        val proc = process ?: run { state = ProxyState.STOPPED; return true }
        state = ProxyState.STOPPING
        proc.destroy()
        val exited = proc.waitFor(timeoutSeconds, TimeUnit.SECONDS)
        if (!exited) {
            proc.destroyForcibly()
            proc.waitFor(timeoutSeconds, TimeUnit.SECONDS)
        }
        val stillAlive = proc.isAlive
        if (!stillAlive) {
            process = null
            state = ProxyState.STOPPED
        }
        return !stillAlive
    }

    fun restart(env: Map<String, String>, workDir: File? = null) {
        stopAndWait()
        start(env, workDir)
    }

    /** Call from the plugin's Disposable teardown. Must be waited on by
     * the caller, same reasoning as ChatProxyManager.dispose() on the VS
     * Code side: an IDE that finishes shutting down before a killed
     * process is reaped can leave it orphaned. */
    fun dispose() {
        stopAndWait()
    }
}
