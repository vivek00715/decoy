package com.decoy.plugin

import com.decoy.core.CLEAR_SESSION_LINK_PREFIX
import com.decoy.core.EDIT_OVERRIDE_LINK_PREFIX
import com.decoy.core.OverrideKind
import com.decoy.core.OverrideListKind
import com.decoy.core.OverridesFile
import com.decoy.core.PanelController
import com.decoy.core.PanelHost
import com.decoy.core.REMOVE_OVERRIDE_LINK_PREFIX
import com.decoy.core.RequestGroup
import com.decoy.core.editOverrideEntry
import com.decoy.core.readOverrides
import com.decoy.core.removeOverrideEntry
import com.decoy.core.renderHtml
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import java.io.File
import javax.swing.JEditorPane

/**
 * Project-level service holding the one PanelController + rendered
 * JEditorPane content for this project's Decoy tool window. A project
 * service (rather than a static/companion registry) is the idiomatic
 * IntelliJ Platform way to share state between a ToolWindowFactory and
 * the AnAction classes that need to act on the same project's data --
 * both retrieve this via `project.service<DecoyProjectService>()`.
 *
 * UNVERIFIED: this file was written without compiling against the real
 * IntelliJ Platform SDK (see plugin/build.gradle.kts's top comment). The
 * `@Service(Service.Level.PROJECT)` + constructor-injected `Project`
 * pattern matches current (2024.x) IntelliJ Platform documentation, but
 * has not been confirmed against a real IDE/compiler here.
 */
@Service(Service.Level.PROJECT)
class DecoyProjectService(private val project: Project) {

    /** Exposed so the override-editing actions (Actions.kt) can read the
     * current overrides before writing an updated copy via
     * controller.saveOverrides() -- PanelController itself only exposes
     * write+refresh, not a read-current-state accessor, since the VS Code
     * side never needed one (its webview keeps its own copy client-side). */
    val rootDir: File
        get() = File(project.basePath ?: ".")

    val editorPane: JEditorPane = JEditorPane("text/html", "").apply {
        isEditable = false
        addHyperlinkListener { event ->
            if (event.eventType == javax.swing.event.HyperlinkEvent.EventType.ACTIVATED) {
                handleHyperlink(event.description)
            }
        }
    }

    val controller: PanelController = PanelController(object : PanelHost {
        override val rootDir: File
            get() = this@DecoyProjectService.rootDir

        override fun updateView(groups: List<RequestGroup>, overrides: OverridesFile) {
            val html = renderHtml(groups, overrides)
            // updateView can be invoked from an AnAction's actionPerformed,
            // which already runs on the EDT in the IntelliJ Platform --
            // invokeLater is used defensively anyway in case a future
            // caller (e.g. a background refresh timer) invokes this off
            // the EDT, since JEditorPane.setText() must happen on the EDT.
            ApplicationManager.getApplication().invokeLater {
                editorPane.text = html
                editorPane.caretPosition = 0
            }
        }

        override fun confirmDestructive(message: String): Boolean {
            val result = Messages.showYesNoDialog(
                project,
                message,
                "Decoy",
                "Confirm",
                "Cancel",
                Messages.getWarningIcon(),
            )
            return result == Messages.YES
        }
    })

    private fun handleHyperlink(description: String?) {
        if (description == null) return

        if (description.startsWith(CLEAR_SESSION_LINK_PREFIX)) {
            controller.clearSession(description.removePrefix(CLEAR_SESSION_LINK_PREFIX))
            return
        }

        if (description.startsWith(REMOVE_OVERRIDE_LINK_PREFIX)) {
            val (kind, list, value) = decodeOverrideLink(description.removePrefix(REMOVE_OVERRIDE_LINK_PREFIX)) ?: return
            val current = readOverrides(rootDir)
            controller.saveOverrides(removeOverrideEntry(current, kind, list, value))
            return
        }

        if (description.startsWith(EDIT_OVERRIDE_LINK_PREFIX)) {
            val (kind, list, oldValue) = decodeOverrideLink(description.removePrefix(EDIT_OVERRIDE_LINK_PREFIX)) ?: return
            // UNVERIFIED: Messages.showInputDialog(Project, message, title, icon,
            // initialValue, validator) -- the 6-arg overload with an
            // `initialValue` parameter to pre-fill the dialog's text field --
            // was confirmed to exist with this exact parameter order by
            // reading the real intellij-community source for Messages.java
            // (platform/platform-api/src/com/intellij/openapi/ui/Messages.java)
            // during development, NOT by compiling against it. `validator` is
            // passed null (no live input validation) since editOverrideEntry
            // already handles blank/duplicate/collision cases safely -- see
            // its KDoc in core/OverrideEdits.kt.
            val newValue = Messages.showInputDialog(
                project,
                "New value (leave blank to remove this override):",
                "Decoy: Edit Override",
                Messages.getQuestionIcon(),
                oldValue,
                null,
            )
            if (newValue == null) return // user cancelled -- do nothing, not even a blank-value removal
            val current = readOverrides(rootDir)
            controller.saveOverrides(editOverrideEntry(current, kind, list, oldValue, newValue.trim()))
        }
    }

    /** Decodes a `<kind.name>:<list.name>:<value>` link suffix (see
     * PanelContent.kt's class-level doc comment for the encoding
     * contract). Splits with a limit of 3 so a pattern value containing a
     * literal ':' is preserved intact rather than truncated at its first
     * colon -- this limit is load-bearing, not incidental; do not change
     * it to an unlimited split. Returns null (and does nothing) on any
     * unrecognized kind/list name rather than throwing, since a malformed
     * link here would otherwise be a hard crash from a single bad click. */
    private fun decodeOverrideLink(suffix: String): Triple<OverrideKind, OverrideListKind, String>? {
        val parts = suffix.split(":", limit = 3)
        if (parts.size != 3) return null
        val kind = runCatching { OverrideKind.valueOf(parts[0]) }.getOrNull() ?: return null
        val list = runCatching { OverrideListKind.valueOf(parts[1]) }.getOrNull() ?: return null
        return Triple(kind, list, parts[2])
    }
}
