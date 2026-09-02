package com.decoy.plugin

import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.content.ContentFactory

/**
 * UNVERIFIED (see plugin/build.gradle.kts's top comment): the
 * ToolWindowFactory + ContentFactory + DefaultActionGroup-as-title-actions
 * pattern below follows current IntelliJ Platform documentation, but was
 * never compiled against the real Platform SDK.
 */
class DecoyToolWindowFactory : ToolWindowFactory {

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val service = project.service<DecoyProjectService>()
        val scrollPane = JBScrollPane(service.editorPane)

        val content = ContentFactory.getInstance().createContent(scrollPane, "", false)
        toolWindow.contentManager.addContent(content)

        val toolbarGroup = ActionManager.getInstance().getAction("Decoy.ToolbarActions")
        if (toolbarGroup is com.intellij.openapi.actionSystem.ActionGroup) {
            toolWindow.setTitleActions(toolbarGroup.getChildren(null).toList())
        }

        service.controller.refresh()
    }
}
