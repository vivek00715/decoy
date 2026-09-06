package com.decoy.plugin

import com.decoy.core.DEFAULT_MCP_SERVER_NAME
import com.decoy.core.MCP_CONFIG_FILENAME
import com.decoy.core.OverrideKind
import com.decoy.core.OverrideListKind
import com.decoy.core.addOverrideEntry
import com.decoy.core.buildMcpServerEntry
import com.decoy.core.readOverrides
import com.decoy.core.writeMcpProxyConfig
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.components.service
import com.intellij.openapi.extensions.PluginId
import com.intellij.openapi.ui.Messages
import com.intellij.util.system.CpuArch
import java.io.File
import kotlinx.serialization.json.Json

/**
 * UNVERIFIED (see plugin/build.gradle.kts): these AnAction subclasses
 * follow the standard IntelliJ Platform pattern (override actionPerformed,
 * pull the current Project off the event, look up the project service),
 * but were never compiled against the real Platform SDK in this
 * environment.
 */
class RefreshAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        project.service<DecoyProjectService>().controller.refresh()
    }
}

class ClearSessionDataOnlyAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        project.service<DecoyProjectService>().controller.clearSessionDataOnly()
    }
}

class ClearAllAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        project.service<DecoyProjectService>().controller.clearAll()
    }
}

/** Shared by all four "Add ..." actions below: prompt for a value via a
 * simple input dialog, and if non-blank, add it via core.addOverrideEntry
 * (which already handles blank/duplicate as a no-op) then save+refresh. */
private fun promptAndAdd(e: AnActionEvent, kind: OverrideKind, list: OverrideListKind, prompt: String, title: String) {
    val project = e.project ?: return
    val value = Messages.showInputDialog(project, prompt, title, Messages.getQuestionIcon())?.trim()
    if (value.isNullOrEmpty()) return

    val service = project.service<DecoyProjectService>()
    val current = readOverrides(service.rootDir)
    val updated = addOverrideEntry(current, kind, list, value)
    service.controller.saveOverrides(updated)
}

class AddAlwaysMaskFieldAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) =
        promptAndAdd(e, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "Field name to always mask:", "Decoy: Add Always-Mask Field Override")
}

class AddNeverMaskFieldAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) =
        promptAndAdd(e, OverrideKind.NEVER_MASK, OverrideListKind.FIELD_NAMES, "Field name to never mask:", "Decoy: Add Never-Mask Field Override")
}

class AddAlwaysMaskPatternAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) =
        promptAndAdd(e, OverrideKind.ALWAYS_MASK, OverrideListKind.PATTERNS, "Regex pattern to always mask:", "Decoy: Add Always-Mask Pattern Override")
}

class AddNeverMaskPatternAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) =
        promptAndAdd(e, OverrideKind.NEVER_MASK, OverrideListKind.PATTERNS, "Regex pattern to never mask:", "Decoy: Add Never-Mask Pattern Override")
}

/** The plugin's own id, matching plugin.xml's <id>. Kept as a constant
 * here rather than looked up some other way -- PluginManagerCore needs
 * it as a PluginId to find where THIS plugin was installed, which is how
 * the bundled decoy-proxy binary is located at runtime (see
 * findBundledDecoyProxyBinary below). */
private const val PLUGIN_ID = "com.decoy.intellij"

/**
 * UNVERIFIED (see plugin/build.gradle.kts): locates the bundled
 * `decoy-proxy` binary for the current OS/arch inside this plugin's own
 * installation directory, following the same
 * bin/<platform>-<arch>/decoy-proxy(.exe) naming convention as the VS
 * Code extension's scripts/bundle-binaries.js (see also
 * plugin/build.gradle.kts's `bundleBinaries` task, which copies built
 * binaries into plugin/src/main/resources/bin/ so the IntelliJ Platform
 * Gradle plugin packages them into the installed plugin directory as-is).
 *
 * `<platform>` here is a lowercase OS name (`darwin`/`win32`/`linux`) --
 * deliberately matching Node's `process.platform` strings (not Java's own
 * `os.name` values like "Mac OS X") so both extensions share ONE naming
 * convention and one binary tree could in principle be reused between
 * them. `<arch>` is `x64`/`arm64`, from CpuArch.
 */
fun findBundledDecoyProxyBinary(pluginId: String = PLUGIN_ID): File? {
    val plugin = PluginManagerCore.getPlugin(PluginId.getId(pluginId)) ?: return null
    val platform = when {
        com.intellij.openapi.util.SystemInfo.isMac -> "darwin"
        com.intellij.openapi.util.SystemInfo.isWindows -> "win32"
        else -> "linux"
    }
    val arch = if (CpuArch.CURRENT == CpuArch.ARM64) "arm64" else "x64"
    val binaryName = if (platform == "win32") "decoy-proxy.exe" else "decoy-proxy"
    val candidate = plugin.pluginPath.resolve("bin").resolve("$platform-$arch").resolve(binaryName).toFile()
    return if (candidate.exists()) candidate else null
}

/**
 * UNVERIFIED (see plugin/build.gradle.kts): triggers the same
 * config-write flow as the VS Code extension's `decoy.configureMcpProxy`
 * command, using the exact same core logic (McpConfigWriter.kt,
 * genuinely tested via ./gradlew :core:test) for the actual merge/write
 * -- this class only does the Swing/IntelliJ-specific prompting and
 * confirmation UI, following this file's existing promptAndAdd pattern.
 */
class ConfigureMcpProxyAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val service = project.service<DecoyProjectService>()

        val binary = findBundledDecoyProxyBinary()
        if (binary == null) {
            Messages.showErrorDialog(
                project,
                "No bundled decoy-proxy binary found for this platform. This build of the plugin " +
                    "may not include a binary for your OS/architecture.",
                "Decoy: Configure MCP Proxy",
            )
            return
        }

        val targetCommand = Messages.showInputDialog(
            project,
            "decoy-proxy sits in front of a REAL target MCP server and masks its results -- " +
                "enter the command that launches that real server (e.g. `npx`):",
            "Decoy: Real Target MCP Server Command",
            Messages.getQuestionIcon(),
        )?.trim()
        if (targetCommand.isNullOrEmpty()) return

        val targetArgsRaw = Messages.showInputDialog(
            project,
            "Space-separated arguments passed through to the target command above (optional):",
            "Decoy: Target MCP Server Arguments",
            Messages.getQuestionIcon(),
        )
        val targetArgs = targetArgsRaw?.trim()?.takeIf { it.isNotEmpty() }?.split(Regex("\\s+")) ?: emptyList()

        val sessionId = "intellij-${project.name}"
        val entryPreview = buildMcpServerEntry(binary.absolutePath, sessionId, targetCommand, targetArgs)
        val prettyEntry = Json { prettyPrint = true }.encodeToString(kotlinx.serialization.json.JsonObject.serializer(), entryPreview)

        // Writing .mcp.json does NOT auto-connect the server -- confirmed
        // directly against a real `claude` CLI: an entry added this way
        // shows as "Pending approval" until a human approves it
        // interactively. Naming the exact fix here, not just that
        // something is needed -- "restart Claude Code" alone would
        // reasonably (and wrongly) sound sufficient on its own.
        val approvalInstruction =
            "This does NOT connect it automatically -- $MCP_CONFIG_FILENAME entries require one-time " +
                "approval. Run \"claude\" interactively in this directory and approve the " +
                "\"$DEFAULT_MCP_SERVER_NAME\" server when prompted."

        val confirmed = Messages.showYesNoDialog(
            project,
            "Decoy will write the following MCP server entry to $MCP_CONFIG_FILENAME at the project root:\n\n" +
                "\"$DEFAULT_MCP_SERVER_NAME\": $prettyEntry\n\n$approvalInstruction",
            "Decoy: Write $MCP_CONFIG_FILENAME?",
            "Write Config",
            "Cancel",
            Messages.getQuestionIcon(),
        )
        if (confirmed != Messages.YES) return

        val result = writeMcpProxyConfig(
            rootDir = service.rootDir,
            binaryPath = binary.absolutePath,
            sessionId = sessionId,
            targetCommand = targetCommand,
            targetArgs = targetArgs,
        )
        Messages.showInfoMessage(
            project,
            "Wrote \"$DEFAULT_MCP_SERVER_NAME\" MCP server entry to ${File(service.rootDir, MCP_CONFIG_FILENAME).absolutePath}. " +
                (if (result.overwritingExisting) "This replaced an existing entry. " else "") +
                approvalInstruction,
            "Decoy",
        )
    }
}

/**
 * Chat proxy lifecycle + API key toolbar actions -- functional-parity
 * pass with VS Code's Start/Stop/Restart/Set-API-Key buttons, done as
 * toolbar AnActions (matching this plugin's existing pattern -- Refresh,
 * Clear Session, Configure MCP Proxy above are all toolbar actions too)
 * rather than in-webview buttons; visual parity with VS Code's in-panel
 * buttons is an explicit, separate fast-follow, not part of this pass.
 * All four are thin: the actual logic lives in DecoyProjectService,
 * itself built on the genuinely-tested `core` modules (ApiKeyStore.kt,
 * ChatProxyProcessManager.kt, ClaudeSettingsWriter.kt, McpApproval.kt).
 */
class StartChatProxyAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        project.service<DecoyProjectService>().startChatProxy()
    }
}

class StopChatProxyAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        project.service<DecoyProjectService>().stopChatProxy()
    }
}

class RestartChatProxyAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        project.service<DecoyProjectService>().restartChatProxy()
    }
}

class SetApiKeyAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        project.service<DecoyProjectService>().setApiKey()
    }
}
