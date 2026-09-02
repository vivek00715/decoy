package com.decoy.plugin

import com.decoy.core.OverrideKind
import com.decoy.core.OverrideListKind
import com.decoy.core.addOverrideEntry
import com.decoy.core.readOverrides
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.components.service
import com.intellij.openapi.ui.Messages

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
