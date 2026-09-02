package com.decoy.core

import java.io.File

/**
 * Everything this controller needs from the real IntelliJ Platform tool
 * window, abstracted behind a plain interface so the actual
 * message-handling LOGIC (what happens when the user clicks "clear all")
 * can be unit tested without any IntelliJ SDK class -- the plugin
 * module's ToolWindowFactory/AnAction classes implement this interface
 * with real Swing/IntelliJ calls; tests implement it with plain in-memory
 * fakes. This mirrors panelController.ts's design in the VS Code
 * extension exactly, including the "Clear All means ALL, including
 * overrides -- use Clear Session Data Only to keep them" distinction
 * that was corrected there after review.
 */
interface PanelHost {
    val rootDir: File

    /** Push a fresh view state to the real UI. */
    fun updateView(groups: List<RequestGroup>, overrides: OverridesFile)

    /** Show a modal confirmation before a destructive action, with
     * `message` describing exactly what will happen. Returns whether the
     * user confirmed. Synchronous, matching idiomatic Swing/IntelliJ
     * modal dialogs (e.g. Messages.showYesNoDialog), unlike the VS Code
     * extension's async webview equivalent. */
    fun confirmDestructive(message: String): Boolean
}

const val CLEAR_SESSION_DATA_ONLY_CONFIRM_MESSAGE =
    "Clear session data for EVERY session in this project? This removes the audit trail and " +
        "vault mappings for all sessions. Your configured always_mask/never_mask overrides are KEPT. " +
        "This cannot be undone."

const val CLEAR_ALL_CONFIRM_MESSAGE =
    "Clear ALL Decoy local data? This removes the audit trail, vault mappings, AND your " +
        "configured always_mask/never_mask overrides for every session in this project -- " +
        "nothing is kept. This cannot be undone. To keep your overrides, use " +
        "\"Clear Session Data Only\" instead."

class PanelController(private val host: PanelHost) {

    fun refresh() {
        val log = readAuditLog(host.rootDir)
        val groups = groupByRequest(log.entries)
        val overrides = readOverrides(host.rootDir)
        host.updateView(groups, overrides)
    }

    fun clearSession(sessionId: String) {
        clearAuditSession(host.rootDir, sessionId)
        clearVaultSession(host.rootDir, sessionId)
        refresh()
    }

    fun clearSessionDataOnly() {
        if (!host.confirmDestructive(CLEAR_SESSION_DATA_ONLY_CONFIRM_MESSAGE)) return
        clearAuditAll(host.rootDir)
        clearVaultAll(host.rootDir)
        refresh()
    }

    fun clearAll() {
        if (!host.confirmDestructive(CLEAR_ALL_CONFIRM_MESSAGE)) return
        clearAuditAll(host.rootDir)
        clearVaultAll(host.rootDir)
        clearOverrides(host.rootDir)
        refresh()
    }

    fun saveOverrides(overrides: OverridesFile) {
        writeOverrides(host.rootDir, overrides)
        refresh()
    }
}
