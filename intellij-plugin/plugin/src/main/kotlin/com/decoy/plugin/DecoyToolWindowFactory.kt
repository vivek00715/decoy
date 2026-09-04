package com.decoy.plugin

import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.content.ContentFactory

/**
 * The ToolWindowFactory + ContentFactory + DefaultActionGroup-as-title-
 * actions pattern below has been confirmed against the real Platform SDK
 * (`./gradlew :plugin:compileKotlin` against IC-2024.2 in this
 * environment -- see DecoyProjectService.kt's class-level doc comment for
 * the full JCEF-availability writeup).
 *
 * `service.viewComponent` is either the JCEF browser's component or the
 * JEditorPane fallback, decided once by DecoyProjectService at
 * construction time based on `JBCefApp.isSupported()`. Only the JEditorPane
 * fallback path needs the JBScrollPane wrapper below -- JBCefBrowser's
 * component is a real embedded Chromium view with its own internal
 * scrolling, and double-wrapping it in a JBScrollPane would add a second,
 * redundant scrollbar.
 */
class DecoyToolWindowFactory : ToolWindowFactory {

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val service = project.service<DecoyProjectService>()
        val view = service.viewComponent
        val displayed = if (view === service.editorPane) JBScrollPane(view) else view

        val content = ContentFactory.getInstance().createContent(displayed, "", false)
        toolWindow.contentManager.addContent(content)

        val toolbarGroup = ActionManager.getInstance().getAction("Decoy.ToolbarActions")
        if (toolbarGroup is com.intellij.openapi.actionSystem.ActionGroup) {
            toolWindow.setTitleActions(toolbarGroup.getChildren(null).toList())
        }

        service.controller.refresh()
    }
}
