package com.decoy.plugin

import com.intellij.ide.util.PropertiesComponent
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity

private const val FIRST_RUN_NOTICE_KEY = "decoy.firstRunNoticeShown"

// Mirrors the VS Code extension's FIRST_RUN_NOTICE_TEXT in extension.ts
// word-for-word, so both IDEs disclose the same thing in the same terms.
// Deliberately no absolute-privacy claims -- see the project's core
// design principles and Phase 11's forthcoming "what this protects
// against, and what it doesn't" doc.
private const val FIRST_RUN_NOTICE_TEXT =
    "Decoy masks PII it detects (via regex/NER, manual overrides, and shape-based rules) before " +
        "it reaches an LLM, and unmasks the response. It does not guarantee protection against " +
        "re-identification from surrounding context, and automated relevance classification is " +
        "imperfect -- that's why manual overrides exist. All audit/session data is stored locally " +
        "only, and you can clear it any time from the Decoy tool window."

/**
 * UNVERIFIED (see plugin/build.gradle.kts's top comment). `ProjectActivity`
 * (registered as a `postStartupActivity` extension point in plugin.xml) is
 * the current (2023.1+) replacement for the older `StartupActivity`
 * interface; this targets that current API based on documentation, not a
 * real compiled/run check.
 *
 * Judgment call: uses `PropertiesComponent.getInstance()` with NO project
 * argument -- the application-level instance -- rather than the
 * project-level one, so the notice is "seen" at most once ever across all
 * projects, matching the VS Code extension's `context.globalState` (which
 * is also user/machine-global, not per-workspace). Using the project-level
 * `PropertiesComponent.getInstance(project)` instead would show the notice
 * once per project, which would NOT match the VS Code side's behavior --
 * worth double-checking this is really what's wanted once this can be
 * tested in a real IDE.
 */
class DecoyFirstRunActivity : ProjectActivity {
    override suspend fun execute(project: Project) {
        val properties = PropertiesComponent.getInstance()
        if (properties.getBoolean(FIRST_RUN_NOTICE_KEY, false)) {
            return
        }
        properties.setValue(FIRST_RUN_NOTICE_KEY, true)

        NotificationGroupManager.getInstance()
            .getNotificationGroup("Decoy Notifications")
            .createNotification(FIRST_RUN_NOTICE_TEXT, NotificationType.INFORMATION)
            .notify(project)
    }
}
