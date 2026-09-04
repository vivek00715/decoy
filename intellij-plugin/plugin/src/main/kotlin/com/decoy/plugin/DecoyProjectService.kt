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
import com.decoy.core.WebviewTheme
import com.decoy.core.editOverrideEntry
import com.decoy.core.parseWebviewMessage
import com.decoy.core.readOverrides
import com.decoy.core.removeOverrideEntry
import com.decoy.core.renderHtml
import com.decoy.core.renderWebviewHtml
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.ui.jcef.JBCefApp
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.ui.jcef.JBCefBrowserBase
import com.intellij.ui.jcef.JBCefJSQuery
import java.awt.Color
import java.io.File
import javax.swing.JComponent
import javax.swing.JEditorPane
import javax.swing.UIManager

/**
 * Project-level service holding the one PanelController + rendered view
 * content for this project's Decoy tool window. A project service
 * (rather than a static/companion registry) is the idiomatic IntelliJ
 * Platform way to share state between a ToolWindowFactory and the
 * AnAction classes that need to act on the same project's data -- both
 * retrieve this via `project.service<DecoyProjectService>()`.
 *
 * Phase 13 JCEF investigation and decision (see MANUAL_TEST.md's
 * "JCEF vs Swing" section for the full writeup): **JCEF is available and
 * used here.** `JBCefApp` and `JBCefBrowser` (package
 * `com.intellij.ui.jcef`) compiled cleanly in this environment against
 * the real `IC-2024.2` Platform SDK (`./gradlew :plugin:compileKotlin`
 * succeeded -- the FIRST time any of this module's Kotlin was ever
 * compiled against the real SDK; see plugin/build.gradle.kts's top
 * comment for that module's prior "never compiled" history), and the
 * downloaded `IC-2024.2` distribution's bundled JetBrains Runtime
 * physically contains a JCEF helper app
 * (`jbr/Contents/Frameworks/jcef Helper*.app` on macOS), confirming
 * JCEF ships with this platform version rather than being an optional
 * component that might be missing. JCEF has been part of the IntelliJ
 * Platform (bundled via the JetBrains Runtime) since 2020.1, well before
 * this plugin's `sinceBuild = "242"` (2024.2) floor, and its Chromium/CEF
 * license is permissive (BSD-style) with no restriction on third-party
 * plugin use -- this is a standard, widely-used pattern (e.g. the
 * Markdown plugin's HTML preview, many AI-assistant tool windows).
 *
 * At RUNTIME (not build time) JCEF can still be unavailable -- a custom
 * JDK instead of the bundled JBR, or a minimal Linux JBR build without
 * JCEF compiled in -- which is exactly what `JBCefApp.isSupported()`
 * exists to detect. This class checks it once per tool window creation
 * and falls back to the original read-only `JEditorPane` +
 * hyperlink-interception rendering ([renderHtml]) when JCEF isn't
 * available, rather than assuming it always is.
 *
 * When JCEF IS available, this uses [renderWebviewHtml] -- the same
 * design (CSS layout, Codicon-based status icons, a real override-
 * editing form with inline regex validation, collapsible request cards)
 * as the VS Code extension's webview -- rendered as REAL clickable
 * `<button>` elements with real JS event listeners, replacing the old
 * `<a href="decoy-clear-session:...">`-style hyperlink workaround this
 * module used under `JEditorPane` (see [renderHtml]'s doc comment and
 * Phase 9's disclosed limitation) -- this is the concrete fix for that
 * limitation, not just a cosmetic pass.
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

    private val useJcef: Boolean = JBCefApp.isSupported()

    // -------- JCEF path --------
    private var jcefBrowser: JBCefBrowser? = null
    private var jcefBridge: JBCefJSQuery? = null

    // -------- Swing/JEditorPane fallback path (unchanged from before
    // Phase 13's JCEF work; see renderHtml's own doc comment) --------
    val editorPane: JEditorPane = JEditorPane("text/html", "").apply {
        isEditable = false
        addHyperlinkListener { event ->
            if (event.eventType == javax.swing.event.HyperlinkEvent.EventType.ACTIVATED) {
                handleHyperlink(event.description)
            }
        }
    }

    /** The Swing component the tool window should actually display --
     * the JCEF browser's component when supported, otherwise the
     * JEditorPane above wrapped by the caller in a JBScrollPane like
     * before. `DecoyToolWindowFactory` picks this rather than assuming
     * either path. */
    val viewComponent: JComponent
        get() = jcefBrowser?.component ?: editorPane

    val controller: PanelController = PanelController(object : PanelHost {
        override val rootDir: File
            get() = this@DecoyProjectService.rootDir

        override fun updateView(groups: List<RequestGroup>, overrides: OverridesFile) {
            // updateView can be invoked from an AnAction's actionPerformed,
            // which already runs on the EDT in the IntelliJ Platform --
            // invokeLater is used defensively anyway in case a future
            // caller (e.g. a background refresh timer) invokes this off
            // the EDT, since both JEditorPane.setText() and JCEF browser
            // calls are expected to happen on the EDT.
            ApplicationManager.getApplication().invokeLater {
                if (useJcef) {
                    ensureJcefBrowser()
                    val html = renderWebviewHtml(groups, overrides, computeWebviewTheme())
                    jcefBrowser?.loadHTML(withBridgeInjected(html), webviewBaseUrl())
                } else {
                    editorPane.text = renderHtml(groups, overrides)
                    editorPane.caretPosition = 0
                }
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

    /** Lazily creates the JCEF browser and its JS message bridge on
     * first use, rather than in the constructor -- a project service is
     * constructed on first `project.service<...>()` access, which can
     * happen before the tool window (and therefore before there's
     * anywhere to put a browser component) is ever opened; building a
     * real Chromium browser instance for a tool window the user may
     * never open would be wasteful. */
    private fun ensureJcefBrowser() {
        if (jcefBrowser != null) return
        val browser = JBCefBrowser()
        val bridge = JBCefJSQuery.create(browser as JBCefBrowserBase)
        bridge.addHandler { raw ->
            handleWebviewMessage(raw)
            null
        }
        jcefBrowser = browser
        jcefBridge = bridge
    }

    /** Prepends a `<script>` defining `window.__decoyPost` (called by
     * WebviewContent.kt's generated page) that forwards to the
     * [JBCefJSQuery] bridge -- inserted before the page's own scripts run
     * so it's always defined by the time a user could click anything. */
    private fun withBridgeInjected(html: String): String {
        val bridge = jcefBridge ?: return html
        val bridgeScript = "<script>window.__decoyPost = function(msg) { ${bridge.inject("msg")} };</script>"
        return html.replaceFirst("<head>", "<head>\n$bridgeScript")
    }

    /** `file://` base URL pointing at this plugin's bundled
     * `codicon.css`/`codicon.ttf` (see `plugin/src/main/resources/webview/`),
     * so the webview HTML's relative `<link href="codicon.css">` resolves
     * -- JCEF's `loadHTML(html, url)` overload uses `url` purely as a base
     * for resolving relative resources, it does not need to be reachable
     * as an actual page. */
    private fun webviewBaseUrl(): String {
        val resource = javaClass.classLoader.getResource("webview/codicon.css")
            ?: return "about:blank"
        // The resource's own URL already ends in "codicon.css"; JCEF
        // resolves relative hrefs against this URL's directory the same
        // way a browser resolves them against a page's URL, so passing
        // the file itself (not its parent directory) as the base is
        // correct and matches how the query string is written in
        // WebviewContent.kt (a bare "codicon.css" relative reference).
        return resource.toExternalForm()
    }

    private fun handleWebviewMessage(raw: String) {
        val message = parseWebviewMessage(raw) ?: return
        when (message.command) {
            "refresh" -> controller.refresh()
            "clearSession" -> message.sessionId?.let { controller.clearSession(it) }
            "clearSessionDataOnly" -> controller.clearSessionDataOnly()
            "clearAll" -> controller.clearAll()
            "saveOverrides" -> message.overrides?.let { controller.saveOverrides(it) }
        }
    }

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

/** Reads the current Look & Feel's actual colors (via Swing's
 * `UIManager`, which IntelliJ's Darcula/IntelliJ Light L&Fs populate
 * with real theme colors under standard Swing keys) rather than
 * hardcoding any color -- so the JCEF webview looks native to whichever
 * IDE theme the user has, light or dark, the same requirement the VS
 * Code panel has for its `--vscode-*` variables. Falls back to
 * [WebviewTheme.fallback] color-by-color when a given `UIManager` key is
 * unset (defensive: some minimal/plugin-provided L&Fs may not populate
 * every key IntelliJ's own themes do). */
private fun computeWebviewTheme(): WebviewTheme {
    val fallback = WebviewTheme.fallback()
    fun hex(key: String, default: String): String {
        val color = UIManager.getColor(key) ?: return default
        return "#%02x%02x%02x".format(color.red, color.green, color.blue)
    }

    val background = hex("Panel.background", fallback.background)
    val foreground = hex("Label.foreground", fallback.foreground)
    val buttonBackground = hex("Button.background", fallback.buttonBackground)

    return WebviewTheme(
        background = background,
        foreground = foreground,
        descriptionForeground = hex("Component.infoForeground", fallback.descriptionForeground),
        border = hex("Component.borderColor", fallback.border),
        sideBarBackground = hex("Tree.background", fallback.sideBarBackground),
        inputBackground = hex("TextField.background", fallback.inputBackground),
        inputForeground = hex("TextField.foreground", fallback.inputForeground),
        inputBorder = hex("Component.borderColor", fallback.inputBorder),
        buttonBackground = buttonBackground,
        buttonForeground = hex("Button.foreground", fallback.buttonForeground),
        buttonHoverBackground = blendHex(buttonBackground, foreground, 0.15),
        secondaryButtonBackground = hex("Panel.background", fallback.secondaryButtonBackground),
        secondaryButtonForeground = foreground,
        hoverBackground = blendHex(background, foreground, 0.08),
        badgeBackground = blendHex(background, foreground, 0.16),
        focusBorder = hex("Component.focusColor", fallback.focusBorder),
        accentBlue = hex("Component.focusColor", fallback.accentBlue),
        accentRed = "#e05555",
        accentGreen = "#59a869",
        accentOrange = "#cca700",
    )
}

/** Mixes `amount` of `mixInHex` into `baseHex` -- used to derive hover/
 * badge shades from a real theme color without needing to know whether
 * the current theme is light or dark (mixing toward the foreground color
 * always moves "away from the background" regardless of which theme is
 * active, unlike a fixed lighten/darken which would go the wrong
 * direction in the other theme). */
private fun blendHex(baseHex: String, mixInHex: String, amount: Double): String {
    val base = parseHex(baseHex)
    val mixIn = parseHex(mixInHex)
    fun mix(a: Int, b: Int) = (a * (1 - amount) + b * amount).toInt().coerceIn(0, 255)
    val r = mix(base.red, mixIn.red)
    val g = mix(base.green, mixIn.green)
    val b = mix(base.blue, mixIn.blue)
    return "#%02x%02x%02x".format(r, g, b)
}

private fun parseHex(hex: String): Color {
    val clean = hex.removePrefix("#")
    return Color(clean.substring(0, 2).toInt(16), clean.substring(2, 4).toInt(16), clean.substring(4, 6).toInt(16))
}
