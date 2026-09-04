package com.decoy.core

/**
 * Builds the tool window's HTML content for a JCEF (Chromium Embedded
 * Framework) browser -- the professional-UI counterpart to
 * [renderHtml]'s read-only [PanelContent]-based Swing `JEditorPane`
 * rendering. See `plugin/DecoyToolWindowFactory.kt` for the runtime
 * choice between the two: JCEF when `JBCefApp.isSupported()`, this
 * Swing fallback otherwise (custom JDK without the bundled JetBrains
 * Runtime, or a JCEF-less Linux distro).
 *
 * This deliberately mirrors the VS Code extension's
 * `webviewContent.ts`/`buildWebviewHtml()` design closely -- same
 * layout (collapsible request cards, icon-based decision/override
 * status via the bundled Codicon font, a real override-editing form
 * with inline regex validation, an empty state, a pending/updating
 * indicator) -- but is a genuinely separate Kotlin string builder, not
 * literally the same file: there is no practical way to share one
 * physical HTML/CSS/JS asset between this project's Node/TypeScript
 * build and its Kotlin/Gradle build without introducing new
 * cross-language build tooling, which was judged out of scope for this
 * phase. What IS shared is the design system (CSS class names,
 * layout, icon choices) and the client-side message contract: this
 * page posts `{command, ...}` JSON objects shaped exactly like
 * panelController.ts's `WebviewMessage` union (see [WebviewMessage] in
 * this package), so `plugin/DecoyProjectService.kt`'s handler and
 * `PanelController`'s five operations need no new server-side surface
 * beyond parsing that same shape.
 *
 * Colors are NOT hardcoded: [WebviewTheme] carries plain hex-string
 * theme tokens that `plugin/DecoyProjectService.kt` computes from
 * IntelliJ's actual current Look & Feel (via `JBColor`/`UIManager`) --
 * this file has zero IntelliJ Platform SDK dependency (see this
 * module's other core files' doc comments for why that split matters:
 * it keeps this HTML-building logic covered by real, run-in-this-
 * environment JUnit tests instead of living entirely in the untested
 * `plugin` module).
 *
 * Unlike the VS Code webview, this HTML is loaded via
 * `JBCefBrowser.loadHTML(html, baseUrl)` with a `file://` base URL
 * pointing at a directory containing this module's bundled
 * `codicon.css`/`codicon.ttf` (see `plugin/src/main/resources/webview/`),
 * so the `<link rel="stylesheet" href="codicon.css">` below resolves
 * as a normal relative asset -- no CSP/nonce/webview-URI machinery is
 * needed the way VS Code's webview sandboxing requires, since JCEF has
 * no VS Code webview equivalent of `asWebviewUri`.
 */
data class WebviewTheme(
    val background: String,
    val foreground: String,
    val descriptionForeground: String,
    val border: String,
    val sideBarBackground: String,
    val inputBackground: String,
    val inputForeground: String,
    val inputBorder: String,
    val buttonBackground: String,
    val buttonForeground: String,
    val buttonHoverBackground: String,
    val secondaryButtonBackground: String,
    val secondaryButtonForeground: String,
    val hoverBackground: String,
    val badgeBackground: String,
    val focusBorder: String,
    val accentBlue: String,
    val accentRed: String,
    val accentGreen: String,
    val accentOrange: String,
) {
    companion object {
        /** A reasonable dark-theme-shaped default, used only by tests/
         * previews that don't have a real IntelliJ LaF to query. The
         * `plugin` module always computes a real one at render time. */
        fun fallback(): WebviewTheme = WebviewTheme(
            background = "#1e1e1e",
            foreground = "#bbbbbb",
            descriptionForeground = "#8a8a8a",
            border = "#323232",
            sideBarBackground = "#252526",
            inputBackground = "#3c3c3c",
            inputForeground = "#cccccc",
            inputBorder = "#5a5a5a",
            buttonBackground = "#365880",
            buttonForeground = "#ffffff",
            buttonHoverBackground = "#4a6d94",
            secondaryButtonBackground = "#3a3d41",
            secondaryButtonForeground = "#cccccc",
            hoverBackground = "#2a2d2e",
            badgeBackground = "#4d4d4d",
            focusBorder = "#4a9eff",
            accentBlue = "#3794ff",
            accentRed = "#f14c4c",
            accentGreen = "#89d185",
            accentOrange = "#cca700",
        )
    }
}

private const val DISCLAIMER =
    "Decoy masks values it detects via regex/NER, manual overrides, and shape-based rules " +
        "before they reach an LLM. It does not guarantee protection against re-identification " +
        "from surrounding context, and automated relevance classification is imperfect -- manual " +
        "overrides exist precisely because of these limits. Nothing here ever shows a real or fake value."

private val DECISION_ICON = mapOf(
    "masked" to "check",
    "redacted" to "eye-closed",
    "left_as_is" to "circle-large-outline",
)

private fun decisionClass(decision: String): String = when (decision) {
    "masked" -> "decision-masked"
    "redacted" -> "decision-redacted"
    "left_as_is" -> "decision-left"
    else -> "decision-unknown"
}

private fun decisionLabel(decision: String): String = if (decision == "left_as_is") "left as-is" else decision

private fun summaryChips(group: RequestGroup): String {
    val counts = linkedMapOf<String, Int>()
    for (e in group.entries) counts[e.decision] = (counts[e.decision] ?: 0) + 1
    val sb = StringBuilder()
    for (decision in listOf("masked", "redacted", "left_as_is")) {
        val count = counts[decision] ?: continue
        val icon = DECISION_ICON[decision] ?: "circle-large-outline"
        sb.append("<span class='summary-chip ${decisionClass(decision)}'>")
            .append("<span class='codicon codicon-$icon'></span>$count</span>")
    }
    return sb.toString()
}

private fun renderWebviewRequestGroup(group: RequestGroup, index: Int): String {
    val sb = StringBuilder()
    val openAttr = if (index == 0) " open" else ""
    sb.append("<details class='request-group'$openAttr><summary>")
    sb.append("<span class='codicon codicon-chevron-right chevron'></span>")
    sb.append("<span class='request-summary'>")
    sb.append("<span class='ts'>").append(escapeHtml(group.timestamp)).append("</span>")
    sb.append("<span class='session' title='").append(escapeHtml(group.sessionId)).append("'>session ")
        .append(escapeHtml(group.sessionId.take(8))).append("</span>")
    sb.append("<span class='summary-chips'>").append(summaryChips(group)).append("</span>")
    sb.append("</span>")
    sb.append("<button class='icon-btn clear-session-btn' data-session-id='")
        .append(escapeHtml(group.sessionId)).append("' title='Clear this session\\'s audit trail and vault mappings'>")
        .append("<span class='codicon codicon-trash'></span></button>")
    sb.append("</summary>")
    sb.append("<table><thead><tr><th>Field</th><th>Decision</th><th>Layer</th><th>Reason</th></tr></thead><tbody>")
    for (e in group.entries) {
        val icon = DECISION_ICON[e.decision] ?: "circle-large-outline"
        val overrideBadge = if (e.layer == "override") {
            "<span class='codicon codicon-warning override-flag' title='Changed by a manual override rule'></span>"
        } else {
            ""
        }
        sb.append("<tr>")
        sb.append("<td class='col-field'>").append(escapeHtml(e.field)).append("</td>")
        sb.append("<td class='col-decision'><span class='badge ${decisionClass(e.decision)}'>")
            .append("<span class='codicon codicon-$icon'></span> ").append(escapeHtml(decisionLabel(e.decision)))
            .append("</span>").append(overrideBadge).append("</td>")
        sb.append("<td>").append(escapeHtml(e.layer)).append("</td>")
        sb.append("<td class='col-reason'>").append(escapeHtml(e.reason)).append("</td>")
        sb.append("</tr>")
    }
    sb.append("</tbody></table></details>")
    return sb.toString()
}

private fun renderWebviewOverrideSection(kind: String, label: String, rules: OverrideRules): String {
    val sb = StringBuilder()
    sb.append("<fieldset class='override-section'><legend>").append(escapeHtml(label)).append("</legend>")
    sb.append("<ul class='override-list'>")
    val fieldEntries = rules.field_names.map { it to false }
    val patternEntries = rules.patterns.map { it to true }
    val entries = fieldEntries + patternEntries
    if (entries.isEmpty()) {
        sb.append("<li class='override-empty'>None yet.</li>")
    } else {
        for ((value, isPattern) in entries) {
            val list = if (isPattern) "patterns" else "field_names"
            val icon = if (isPattern) "regex" else "symbol-field"
            sb.append("<li class='override-row'>")
            sb.append("<span class='override-kind-icon codicon codicon-$icon'></span>")
            sb.append("<span class='override-value'>")
            if (isPattern) sb.append("<code>").append(escapeHtml(value)).append("</code>") else sb.append(escapeHtml(value))
            sb.append("</span>")
            sb.append("<button class='icon-btn remove-override-btn' data-kind='$kind' data-list='$list' data-value='")
                .append(escapeHtml(value)).append("' title='Remove this override'>")
                .append("<span class='codicon codicon-close'></span></button>")
            sb.append("</li>")
        }
    }
    sb.append("</ul>")
    sb.append(
        """
        <div class='override-form'>
          <div class='form-row'>
            <label for='field-input-$kind'>Field name</label>
            <div class='form-input-group'>
              <input type='text' id='field-input-$kind' class='add-field-input' data-kind='$kind' placeholder='field_name' />
              <button class='btn-secondary add-field-btn' data-kind='$kind'><span class='codicon codicon-add'></span>Add</button>
            </div>
            <p class='field-error' id='field-error-$kind' hidden></p>
          </div>
          <div class='form-row'>
            <label for='pattern-input-$kind'>Pattern (regex)</label>
            <div class='form-input-group'>
              <input type='text' id='pattern-input-$kind' class='add-pattern-input' data-kind='$kind' placeholder='e.g. EMP-\d{6}' />
              <button class='btn-secondary add-pattern-btn' data-kind='$kind'><span class='codicon codicon-add'></span>Add</button>
            </div>
            <p class='field-error' id='pattern-error-$kind' hidden></p>
          </div>
        </div>
        """.trimIndent(),
    )
    sb.append("</fieldset>")
    return sb.toString()
}

fun renderWebviewHtml(groups: List<RequestGroup>, overrides: OverridesFile, theme: WebviewTheme): String {
    val requestsHtml = if (groups.isEmpty()) {
        """
        <div class="empty-state">
          <span class="codicon codicon-inbox empty-icon"></span>
          <p class="empty-title">No requests yet</p>
          <p class="empty-body">Once Claude Code sends a prompt through Decoy, you'll see what got masked here.</p>
        </div>
        """.trimIndent()
    } else {
        groups.mapIndexed { i, g -> renderWebviewRequestGroup(g, i) }.joinToString("")
    }

    val summaryLine = if (groups.isEmpty()) "Watching for requests" else "${groups.size} request${if (groups.size == 1) "" else "s"} recorded"

    // language=HTML
    return """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <link href="codicon.css" rel="stylesheet" />
      <style>
        :root {
          --bg: ${theme.background};
          --fg: ${theme.foreground};
          --desc-fg: ${theme.descriptionForeground};
          --border: ${theme.border};
          --sidebar-bg: ${theme.sideBarBackground};
          --input-bg: ${theme.inputBackground};
          --input-fg: ${theme.inputForeground};
          --input-border: ${theme.inputBorder};
          --btn-bg: ${theme.buttonBackground};
          --btn-fg: ${theme.buttonForeground};
          --btn-hover-bg: ${theme.buttonHoverBackground};
          --btn2-bg: ${theme.secondaryButtonBackground};
          --btn2-fg: ${theme.secondaryButtonForeground};
          --hover-bg: ${theme.hoverBackground};
          --badge-bg: ${theme.badgeBackground};
          --focus-border: ${theme.focusBorder};
          --blue: ${theme.accentBlue};
          --red: ${theme.accentRed};
          --green: ${theme.accentGreen};
          --orange: ${theme.accentOrange};
          --gap-xs: 4px; --gap-s: 8px; --gap-m: 12px; --radius: 4px;
        }
        * { box-sizing: border-box; }
        body { font-family: -apple-system, "Segoe UI", sans-serif; font-size: 13px; color: var(--fg); background: var(--bg); margin: 0; }
        .panel { padding: 8px 12px 12px; }
        header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--border); margin-bottom: 12px; }
        .header-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
        .header-title .codicon { font-size: 16px; color: var(--blue); }
        .header-text h1 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; margin: 0; }
        .header-text .summary { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--desc-fg); margin-top: 2px; }
        .live-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--green); display: inline-block; animation: pulse 2.4s ease-in-out infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
        .toolbar { display: flex; gap: 2px; }
        button { cursor: pointer; font-family: inherit; }
        .icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; padding: 0; border: none; border-radius: var(--radius); background: transparent; color: var(--fg); }
        .icon-btn:hover { background: var(--hover-bg); }
        .icon-btn.danger:hover { color: var(--red); }
        .btn-secondary { display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--border); border-radius: var(--radius); padding: 3px 10px; font-size: 12px; background: var(--btn2-bg); color: var(--btn2-fg); }
        .btn-secondary:hover { background: var(--hover-bg); }
        section { margin-bottom: 12px; }
        h2 { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--desc-fg); margin: 0 0 8px; }
        .data-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
        .actions-hint { font-size: 11px; color: var(--desc-fg); margin: 0 0 12px; }
        .empty-state { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 6px; padding: 28px 16px; color: var(--desc-fg); border: 1px dashed var(--border); border-radius: var(--radius); }
        .empty-icon { font-size: 28px; opacity: .6; margin-bottom: 4px; }
        .empty-title { font-weight: 600; color: var(--fg); margin: 0; }
        .empty-body { font-size: 12px; margin: 0; max-width: 32ch; }
        .request-group { border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 8px; background: var(--sidebar-bg); }
        .request-group summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 8px; padding: 6px 8px; }
        .request-group summary::-webkit-details-marker { display: none; }
        .chevron { transition: transform .1s ease; font-size: 14px; color: var(--desc-fg); }
        .request-group[open] > summary .chevron { transform: rotate(90deg); }
        .request-summary { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex: 1; min-width: 0; }
        .ts { font-weight: 600; font-size: 12px; }
        .session { font-size: 11px; font-family: monospace; color: var(--desc-fg); background: var(--badge-bg); padding: 1px 6px; border-radius: 10px; }
        .summary-chips { display: flex; gap: 4px; }
        .summary-chip { display: inline-flex; align-items: center; gap: 3px; font-size: 11px; padding: 1px 5px; border-radius: 8px; }
        .summary-chip .codicon { font-size: 12px; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; border-top: 1px solid var(--border); }
        th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
        th { color: var(--desc-fg); font-weight: 600; font-size: 11px; }
        .col-field { font-family: monospace; white-space: nowrap; }
        .col-reason { color: var(--desc-fg); }
        .badge { display: inline-flex; align-items: center; gap: 4px; padding: 1px 7px; border-radius: 8px; font-size: 11px; white-space: nowrap; }
        .badge .codicon { font-size: 12px; }
        .decision-masked { background: color-mix(in srgb, var(--blue) 22%, transparent); color: var(--blue); }
        .decision-redacted { background: color-mix(in srgb, var(--red) 22%, transparent); color: var(--red); }
        .decision-left { background: color-mix(in srgb, var(--green) 22%, transparent); color: var(--green); }
        .override-flag { font-size: 12px; color: var(--orange); margin-left: 4px; }
        .override-section { border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 12px 12px; margin: 0 0 8px; }
        .override-section legend { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--desc-fg); padding: 0 4px; }
        .override-list { list-style: none; padding: 0; margin: 0 0 8px; }
        .override-row { display: flex; align-items: center; gap: 6px; padding: 3px 4px; border-radius: var(--radius); }
        .override-row:hover { background: var(--hover-bg); }
        .override-kind-icon { font-size: 13px; color: var(--desc-fg); }
        .override-value { flex: 1; min-width: 0; overflow-wrap: anywhere; font-size: 12px; }
        .override-empty { font-size: 12px; color: var(--desc-fg); font-style: italic; padding: 3px 4px; }
        .override-form { display: flex; flex-direction: column; gap: 8px; }
        .form-row { display: flex; flex-direction: column; gap: 3px; }
        .form-row label { font-size: 11px; color: var(--desc-fg); }
        .form-input-group { display: flex; gap: 6px; }
        .form-input-group input { flex: 1; min-width: 0; background: var(--input-bg); color: var(--input-fg); border: 1px solid var(--input-border); border-radius: var(--radius); padding: 3px 6px; font-size: 12px; }
        .form-input-group input:focus { outline: 1px solid var(--focus-border); outline-offset: -1px; }
        .form-input-group input.invalid { border-color: var(--red); }
        .field-error { font-size: 11px; color: var(--red); margin: 0; }
        .pending-banner { display: none; align-items: center; gap: 6px; font-size: 11px; color: var(--desc-fg); padding: 4px 0; }
        .pending-banner.visible { display: flex; }
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
        .disclaimer { font-size: 11px; color: var(--desc-fg); border-top: 1px solid var(--border); padding-top: 8px; margin-top: 12px; }
      </style>
    </head>
    <body>
      <div class="panel">
        <header>
          <div class="header-title">
            <span class="codicon codicon-shield"></span>
            <div class="header-text">
              <h1>Decoy</h1>
              <div class="summary"><span class="live-dot" title="Live: this panel updates automatically"></span>${escapeHtml(summaryLine)}</div>
            </div>
          </div>
          <div class="toolbar">
            <button id="refresh-btn" class="icon-btn" title="Refresh"><span class="codicon codicon-refresh"></span></button>
            <button id="clear-session-data-btn" class="icon-btn danger" title="Clear Session Data Only -- removes the audit trail and vault mappings for every session. Keeps your configured overrides."><span class="codicon codicon-clear-all"></span></button>
            <button id="clear-all-btn" class="icon-btn danger" title="Clear All Local Data -- removes EVERYTHING, including your configured overrides."><span class="codicon codicon-trash"></span></button>
          </div>
        </header>

        <p class="pending-banner" id="pending-banner"><span class="codicon codicon-loading spin"></span>Updating&hellip;</p>

        <p class="actions-hint">"Clear Session Data Only" keeps your always_mask/never_mask overrides. "Clear All Local Data" removes those too, with nothing kept back.</p>

        <section>
          <h2>Recent requests</h2>
          <div id="requests">$requestsHtml</div>
        </section>

        <section>
          <h2>Overrides</h2>
          <div id="overrides">
            ${renderWebviewOverrideSection("ALWAYS_MASK", "Always mask", overrides.always_mask)}
            ${renderWebviewOverrideSection("NEVER_MASK", "Never mask", overrides.never_mask)}
          </div>
        </section>

        <div class="disclaimer">${escapeHtml(DISCLAIMER)}</div>
      </div>

      <script>
        var pendingBanner = document.getElementById("pending-banner");
        function showPending() { pendingBanner.classList.add("visible"); }
        // window.__decoyPost is injected by DecoyProjectService.kt via a
        // JBCefJSQuery bridge script prepended before this document loads
        // -- see WebviewContent.kt's class-level doc comment.
        function post(message) {
          showPending();
          window.__decoyPost(JSON.stringify(message));
        }

        document.getElementById("refresh-btn").addEventListener("click", function () { post({ command: "refresh" }); });
        document.getElementById("clear-session-data-btn").addEventListener("click", function () { post({ command: "clearSessionDataOnly" }); });
        document.getElementById("clear-all-btn").addEventListener("click", function () { post({ command: "clearAll" }); });

        document.querySelectorAll(".clear-session-btn").forEach(function (btn) {
          btn.addEventListener("click", function (ev) {
            ev.preventDefault();
            post({ command: "clearSession", sessionId: btn.getAttribute("data-session-id") });
          });
        });

        var pendingOverrides = ${overridesToJs(overrides)};

        document.querySelectorAll(".remove-override-btn").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var kind = btn.getAttribute("data-kind") === "ALWAYS_MASK" ? "always_mask" : "never_mask";
            var list = btn.getAttribute("data-list");
            var value = btn.getAttribute("data-value");
            pendingOverrides[kind][list] = pendingOverrides[kind][list].filter(function (v) { return v !== value; });
            post({ command: "saveOverrides", overrides: pendingOverrides });
          });
        });

        function showFieldError(id, message) {
          var el = document.getElementById(id);
          if (!el) return;
          if (message) { el.textContent = message; el.hidden = false; } else { el.textContent = ""; el.hidden = true; }
        }

        document.querySelectorAll(".add-field-btn").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var kindAttr = btn.getAttribute("data-kind");
            var kind = kindAttr === "ALWAYS_MASK" ? "always_mask" : "never_mask";
            var input = document.querySelector('.add-field-input[data-kind="' + kindAttr + '"]');
            var value = input.value.trim();
            showFieldError("field-error-" + kindAttr, null);
            input.classList.remove("invalid");
            if (!value) {
              input.classList.add("invalid");
              showFieldError("field-error-" + kindAttr, "Field name can't be empty.");
              return;
            }
            pendingOverrides[kind].field_names.push(value);
            input.value = "";
            post({ command: "saveOverrides", overrides: pendingOverrides });
          });
        });

        document.querySelectorAll(".add-pattern-btn").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var kindAttr = btn.getAttribute("data-kind");
            var kind = kindAttr === "ALWAYS_MASK" ? "always_mask" : "never_mask";
            var input = document.querySelector('.add-pattern-input[data-kind="' + kindAttr + '"]');
            var value = input.value.trim();
            showFieldError("pattern-error-" + kindAttr, null);
            input.classList.remove("invalid");
            if (!value) {
              input.classList.add("invalid");
              showFieldError("pattern-error-" + kindAttr, "Pattern can't be empty.");
              return;
            }
            try { new RegExp(value); } catch (err) {
              input.classList.add("invalid");
              showFieldError("pattern-error-" + kindAttr, "Not a valid regular expression: " + err.message);
              return;
            }
            pendingOverrides[kind].patterns.push(value);
            input.value = "";
            post({ command: "saveOverrides", overrides: pendingOverrides });
          });
        });
      </script>
    </body>
    </html>
    """.trimIndent()
}

/** Serializes an [OverridesFile] as a JS object literal for embedding
 * directly into the page's initial `<script>` (mirrors webviewContent.ts's
 * `JSON.stringify(overrides)` -- values are escaped for both HTML and JS
 * string contexts since this ultimately sits inside an HTML document). */
private fun jsStringLiteral(value: String): String {
    val jsEscaped = value
        .replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("</", "<\\/") // avoid prematurely closing the enclosing <script> tag
    return "\"$jsEscaped\""
}

private fun jsStringArray(values: List<String>): String = values.joinToString(",", "[", "]") { jsStringLiteral(it) }

private fun overridesToJs(overrides: OverridesFile): String {
    fun rules(r: OverrideRules) = "{\"patterns\":${jsStringArray(r.patterns)},\"field_names\":${jsStringArray(r.field_names)}}"
    return "{\"always_mask\":${rules(overrides.always_mask)},\"never_mask\":${rules(overrides.never_mask)}}"
}
