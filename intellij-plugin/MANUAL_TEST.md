# Decoy IntelliJ plugin — manual test walkthrough

This file targets ONLY the surface a real, interactive IntelliJ instance
can prove. The `core` module (89 JUnit tests, all passing — run
`./gradlew :core:test` yourself to confirm) already proves: audit log
decrypt/parse/format-version handling, real cross-language Fernet
interop (a genuine Python-produced ciphertext is a hardcoded test
fixture in `CryptoTest.kt`), override read/write/round-trip PLUS
remove/edit-in-place transform logic (`OverrideEditsTest.kt`), MCP config
merge/write logic (`McpConfigWriterTest.kt`), `PanelController`'s
message-handling logic for all five operations via an in-memory fake
`PanelHost`, the Swing-fallback HTML-rendering logic in `PanelContent.kt`,
AND (Phase 13, new) the JCEF webview's HTML-rendering logic in
`WebviewContentTest.kt` and its JS→Kotlin message-parsing logic in
`WebviewMessageTest.kt` — escaping/XSS safety (including a value that
tries to break out of the embedded JS override-state literal, not just
out of HTML), the no-absolute-privacy disclaimer text, theme-color
substitution, and Codicon usage are all covered there. None of that needs
re-checking below.

**Phase 13 update — the `plugin` module now HAS been compiled and
packaged for real, in the environment that did the JCEF migration.**
Both `./gradlew :plugin:compileKotlin` and `./gradlew :plugin:buildPlugin`
ran to `BUILD SUCCESSFUL` against a real downloaded `IC-2024.2`
distribution, producing a real, installable
`plugin/build/distributions/plugin.zip`. `buildPlugin`'s
`buildSearchableOptions` step even launches a real (headless) IDE
instance to introspect the plugin, and its log confirmed
`JBCefApp.isSupported()` correctly reports `false` in a headless
environment (`"JCEF is manually disabled in headless env via
'ide.browser.jcef.headless.enabled=false'"`), meaning
`DecoyProjectService`'s Swing/`JEditorPane` fallback path was actually
exercised, not just theorized.

**What this does NOT prove, and is still what this file exists to check:**
no real GUI session with a display was run. Nobody has actually SEEN the
tool window, clicked a button in it, or watched the JCEF webview render
interactively. Compiling and packaging catches type errors and missing
APIs; it cannot catch a wrong color, a JS handler that's wired to the
wrong element, or a webview that renders as a blank white box. Treat
every "Expected" below as still genuinely unchecked.

## JCEF vs Swing — the investigation and decision (read this first)

Before doing any cosmetic work, Phase 13 investigated whether this
module's original design — a read-only `JEditorPane` with `<a
href="decoy-clear-session:...">`-style hyperlinks standing in for real
buttons (a limitation disclosed back in Phase 9) — could be replaced with
JCEF (JetBrains' bundled Chromium Embedded Framework), which would allow
reusing the same rich HTML/CSS/JS design as the VS Code extension's
webview with REAL clickable buttons instead of the hyperlink workaround.

**Decision: yes, JCEF is used, with an automatic fallback.** Findings:
- JCEF (`com.intellij.ui.jcef.*`) has shipped with the IntelliJ Platform's
  bundled JetBrains Runtime since 2020.1 — well before this plugin's
  `sinceBuild="242"` (2024.2) floor.
- The downloaded `IC-2024.2` distribution's JBR physically contains a
  JCEF helper app (`jbr/Contents/Frameworks/jcef Helper*.app` on macOS),
  confirming it's actually bundled for this target, not just
  documented as available in general.
- `com.intellij.ui.jcef.JBCefBrowser`/`JBCefApp`/`JBCefJSQuery` compiled
  cleanly against the real SDK in this environment.
- CEF/Chromium's license is permissive (BSD-style); using it from a
  third-party plugin via the Platform's own bundled copy is a standard,
  widely-used pattern (e.g. the Markdown plugin's preview pane).
- At RUNTIME, JCEF can still be genuinely unavailable (a custom JDK
  instead of the bundled JBR, certain minimal Linux JBR builds) — this is
  exactly what `JBCefApp.isSupported()` exists to detect, and
  `DecoyProjectService` checks it once and falls back to the original
  `JEditorPane` + hyperlink rendering (`renderHtml()` in `PanelContent.kt`,
  unchanged) when it's false, rather than assuming JCEF is always there.

**What is NOT literally shared** between the two IDE extensions: there is
no single physical HTML/CSS/JS file loaded by both a Node/TypeScript
build and a Kotlin/Gradle build — introducing that cross-language build
tooling was judged out of scope for this phase. What IS shared is the
design system (CSS class names, layout, Codicon icon choices — the exact
same `codicon.ttf`/`codicon.css` files are bundled into both extensions)
and the client→host JS message contract (`{command, ...}` JSON objects
shaped identically to the VS Code webview's `WebviewMessage` union).
`WebviewContent.kt`'s class-level doc comment has the full detail.

## Setup

1. You need a real IntelliJ IDEA (Community is fine, matches the `IC`
   platform type this plugin targets) installed, or let the Gradle
   IntelliJ Platform plugin download one via the `runIde` task below —
   `./gradlew :plugin:buildPlugin` has already confirmed this downloads
   and resolves correctly in a similar sandboxed environment.
2. From `intellij-plugin/`, run `./gradlew :plugin:runIde`. Compilation
   itself is no longer the risk it once was (see above) — this step is
   now about actually watching the IDE window come up.
3. Once it launches, open (or create) any project as the sandbox IDE's
   workspace — this becomes the project whose root the plugin treats as
   `rootDir` (via `project.basePath`).

## Test 1 — the plugin loads and shows its tool window

**Expected:** a second IntelliJ window opens with Decoy's tool window
icon visible on the right-hand tool window bar (anchor="right" in
plugin.xml). Compilation succeeding is now a known-good baseline (see
above); this step is about the window actually appearing and the JCEF
webview rendering something other than a blank page — if it's blank,
check `webviewBaseUrl()` in `DecoyProjectService.kt` (the `file://` base
URL used to resolve `codicon.css`/`codicon.ttf`) is resolving to a real
bundled resource, not `about:blank`.

## Test 2 — first-run notification (new surface: `ProjectActivity` + `NotificationGroupManager` + `PropertiesComponent`)

**Expected:** On the sandbox IDE's first project open, a notification
balloon appears with the same disclaimer wording as the VS Code
extension's first-run notice (plain language, no "100% private" /
"never leaves your machine" claims).

Close the sandbox IDE and relaunch `runIde`, opening a *different*
project. **Expected:** the notice does NOT reappear — confirming
`PropertiesComponent.getInstance()` (the **application-level** instance,
not project-level — a deliberate choice to match the VS Code extension's
machine-global `globalState`) actually persisted across both the
IDE restart and the project change. If it reappears per-project, that
specific judgment call (documented in `DecoyFirstRunActivity.kt`) was
wrong and needs the project-level `getInstance(project)` overload
instead, or the coordinator's confirmation that project-scoped is
actually preferred.

## Test 3 — tool window opens and the live JCEF webview actually renders (visual check)

**Expected:** the tool window shows the same design as the VS Code
extension's panel — a "DECOY" header with a pulsing live-status dot, an
empty state reading "No requests yet — once Claude Code sends a prompt
through Decoy, you'll see what got masked here." with a Codicon inbox
icon (not plain text), and "Always mask"/"Never mask" override sections
each with a labeled "Field name" / "Pattern (regex)" input and a real
`+ Add` button — this is `WebviewContentTest.kt`'s `renderWebviewHtml()`
output (already proven correct as an HTML string) actually rendering
inside a live JCEF/Chromium view, which JUnit cannot execute.

- **Visual check, light theme:** switch the sandbox IDE to a light theme
  (Settings → Appearance & Behavior → Appearance → Theme) and reopen/
  refresh the tool window. Confirm the panel's background, text, borders,
  and button colors all switch to match — these come from
  `computeWebviewTheme()` reading real `UIManager` colors at render time,
  not hardcoded values, so a color that stays dark-themed in a light IDE
  (or vice versa) is a real bug in that function.
- **Visual check, dark theme:** switch back to Darcula (or your default
  dark theme) and confirm the same.
- Confirm the Codicon icons actually render as icons (a checkmark, an
  eye-slash, a warning triangle, a trash can) — if you instead see empty
  boxes/tofu characters, the `codicon.ttf` font failed to load via the
  `file://` base URL `webviewBaseUrl()` computes; check the browser's
  devtools (right-click → "Inspect", available on internal JCEF builds)
  for a 404 on `codicon.ttf`/`codicon.css`.
- Confirm the request cards render as `<details>`/`<summary>` elements
  that actually expand/collapse when clicked, not everything shown flat.

If JCEF is unavailable in your environment (`JBCefApp.isSupported()` is
false — check the IDE log for the same headless-disable message this
was confirmed to produce in this project's own sandboxed build, or for a
genuine "JCEF not supported" message on an unusual platform/JDK), the
tool window instead shows the ORIGINAL Swing `JEditorPane` fallback
(`renderHtml()`'s output — plain text links, no icons, no collapsible
sections). This is expected, not a bug — but note in your report which
path you actually exercised, since the rest of this file's steps below
describe the JCEF/webview path specifically.

## Test 4 — trigger a masked request, confirm the live pane updates

From the **repo root** (not `intellij-plugin/`), with the Python venv
active:

```bash
source .venv/bin/activate
python3 - <<'PY'
import sys
sys.path.insert(0, "src")
from decoy.audit_log import AuditLog
from decoy.unified_pipeline import ask_over_data

audit_log = AuditLog()  # uses the default .decoy/audit.enc + .decoy/vault.key

def fetch_records():
    return [{"employee_id": "EMP-778899", "notes": "under performance review"}]

result = ask_over_data(
    question="Please email bob@example.com about this case",
    fetch_records_fn=fetch_records,
    session_id="manual-test-session",
    audit_log=audit_log,
    llm_call=lambda p: "Acknowledged.",
)
print("Request ID:", result.request_id)
PY
```

This must be run against the same folder the sandbox IDE has open as its
project root, or the plugin's `project.basePath`-derived `rootDir` looks
in the wrong place.

Click the **Refresh** toolbar action. **Expected (the new thing):** the
`RefreshAction` → `project.service<DecoyProjectService>()` →
`PanelController.refresh()` chain actually fires end-to-end and the live
pane updates to show one request group. **Expected (sanity spot-check
only, already proven by `PanelContentTest.kt`):** no real value
(`bob@example.com`, `EMP-778899`, "performance review") appears anywhere
in the pane.

## Test 5 — "Clear this session" is now a REAL button (fixes the Phase 9 limitation)

Before Phase 13, the per-session clear action was a plain `<a
href="decoy-clear-session:...">` link inside the read-only `JEditorPane`,
intercepted by a `HyperlinkListener` — a workaround for not being able to
embed real Swing buttons per table row, disclosed as a limitation back in
Phase 9. Under JCEF, it's a real `<button class="icon-btn
clear-session-btn">` with a real `addEventListener("click", ...)` in
`WebviewContent.kt`'s generated JS, posting `{command: "clearSession",
sessionId}` through the `JBCefJSQuery` bridge — the same message shape
`WebviewMessageTest.kt` already proves parses correctly.

1. Click the trash-can icon button on the request group's row from Test 4
   (hover it first — **expected:** a hover-highlight background appears,
   confirming real `:hover`/mouse-event handling, not just a static
   image).
2. **Expected:** the "Updating…" pending banner (a spinning Codicon
   loading icon) briefly appears — this is `WebviewContent.kt`'s
   `showPending()` firing immediately on click, before the
   `JBCefJSQuery` round-trip to `DecoyProjectService.handleWebviewMessage`
   and back completes; if you never see it (the swap happens too fast to
   perceive, or it never appears at all), that specific gap-covering
   affordance isn't working — note which.
3. **Expected:** the request group disappears and the panel returns to
   the empty state, no page reload/flash beyond the pending banner.
4. If clicking the button does nothing, open the browser devtools
   (right-click → Inspect, if available) and check the console for a
   JS error — the most likely failure is `window.__decoyPost` being
   undefined, which would mean `DecoyProjectService.withBridgeInjected()`
   didn't successfully splice its bridge `<script>` before the page's own
   scripts ran.

## Test 6 — "Clear Session Data Only" vs "Clear All Local Data", including the real modal dialog

1. Re-run Test 4's snippet to have data again. Click **"Clear Session
   Data Only"**. **Expected:** `Messages.showYesNoDialog` shows a real
   modal dialog (title "Decoy") with wording that explicitly says
   overrides are KEPT. Click **Cancel** — **expected:** nothing changes.
2. First add an override: click **"Add Always-Mask Field"**, enter
   `employee_id` in the input dialog. **Expected:** `.decoy/overrides.json`
   on disk now lists it under `always_mask.field_names`, and the pane's
   "Always mask" list shows it.
3. Confirm **"Clear Session Data Only"** this time. **Expected:** the
   pane empties to "No requests recorded yet" but `employee_id` is still
   listed under "Always mask" — confirming overrides really were kept.
4. Re-run Test 4's snippet again. Click **"Clear All Local Data
   (Including Overrides)"**. **Expected:** the modal wording explicitly
   says overrides are REMOVED too, pointing at "Clear Session Data Only"
   as the alternative. Confirm it.
5. **Expected:** the pane empties AND `employee_id` disappears from the
   override list — confirming `clearOverrides()` actually ran from a
   real confirmed IntelliJ modal dialog.

## Test 7 — add/remove a pattern override via the real in-page form, with inline validation (JCEF path)

Phase 13 added a real override-editing FORM directly in the webview
(matching the VS Code panel exactly), in addition to the pre-existing
toolbar `AnAction`s (`AddAlwaysMaskPatternAction` etc., driven by
`Messages.showInputDialog` — those still work unchanged and are a
separate code path, not exercised by this test). The in-page form has no
"edit in place" — only add and remove, exactly like the VS Code
extension's — so this test does NOT cover editing an existing entry's
value (that only exists via the toolbar's separate `Messages` dialogs, or
under the Swing fallback's `[edit]` hyperlink, both unchanged from
before).

1. In the "Always mask" section's "Pattern (regex)" input, type `(bad`
   (a deliberately invalid, unclosed-group regex) and click **Add**.
   **Expected:** the input gets a red border and an inline error message
   appears below it (something like "Not a valid regular expression:
   ...") — **and nothing is sent to the backend** (no pending banner, no
   change to `.decoy/overrides.json`). This is `new RegExp(value)`
   throwing client-side in `WebviewContent.kt`'s generated JS, exactly
   mirroring the VS Code webview's same validation.
2. Clear it and type `EMP-\d{6}`, click **Add**. **Expected:**
   `.decoy/overrides.json` now lists it under `always_mask.patterns`, and
   the pane shows it in `<code>` styling with a real remove (×) button
   next to it — no page reload, the pending banner briefly shows then the
   new entry appears.
3. Click the **×** button next to that pattern. **Expected:** the entry
   disappears from both the pane and `.decoy/overrides.json`
   immediately, no confirmation dialog (removing a single override entry
   was deliberately NOT made a "destructive, confirm first" action,
   unlike Clear Session Data Only / Clear All).
4. Repeat step 1's invalid-input check for the "Field name" input with an
   empty value (click **Add** with the field blank) — **expected:** the
   same red-border + inline-error treatment, "Field name can't be
   empty.", and again nothing posted.
5. If nothing happens on any of the above (no error shown, no entry
   added/removed), open the browser devtools and check for a JS error —
   the most likely failure is a `document.querySelector` mismatch between
   an input's `id`/`data-kind` attribute and what the click handler looks
   up, since this form's JS was written fresh for Phase 13 and only
   string-level-tested via `WebviewContentTest.kt`, never executed in a
   real DOM before this manual test.

**Swing-fallback-only:** if you're on the `JEditorPane` fallback path
(Test 3 didn't show the webview design), the override list instead uses
`[edit]`/`[remove]` hyperlinks exactly as before Phase 13 — see
`DecoyProjectService.kt`'s `handleHyperlink`/`decodeOverrideLink` for
that unchanged logic, still covered by `PanelContentTest.kt` as a string
and still never exercised in a real `JEditorPane` in this environment.

## Test 8 — configure MCP proxy on a machine with zero Python installed (new surface: `ConfigureMcpProxyAction`, `bundleBinaries` Gradle task)

The actual config-merge LOGIC is genuinely JUnit-tested and passing:
`McpConfigWriterTest.kt` (16 tests) covers parsing/merging/round-tripping
a `.mcp.json`, preserving unrelated `mcpServers` entries and unrelated
top-level keys, flagging/overwriting an existing `decoy` entry, and the
real atomic-write path (`writeMcpProxyConfig`) against a real temp
filesystem — run `./gradlew :core:test` yourself to confirm (89 tests
total across `core`, all passing as of this writing). None of that needs
re-checking below. What's untested — the whole `plugin` module's Swing
wiring (`ConfigureMcpProxyAction`, `findBundledDecoyProxyBinary`'s real
`PluginManagerCore`/`CpuArch`/`SystemInfo` calls, and whether the
`bundleBinaries` Gradle task actually produces a plugin ZIP with the
binary inside it at the path the action expects) is exactly what this
test exercises.

1. Before `./gradlew :plugin:runIde` / `:plugin:buildPlugin`, build at
   least one platform's binary via `python scripts/build_binary.py` at
   the repo root (Python is needed for this BUILD-time step only, not by
   the plugin at runtime). Confirm `<repo root>/dist/decoy-proxy` (or
   `.exe`) exists.
2. Run `./gradlew :plugin:bundleBinaries` and confirm
   `plugin/build/generated-resources/bin/<platform>-<arch>/decoy-proxy`
   exists and matches the file from step 1 (this directory is added as a
   resources `srcDir`, so its *contents* become the resources root —
   `bin/<platform>-<arch>/decoy-proxy` is the final path packaged into
   the plugin, matching what `findBundledDecoyProxyBinary` looks for).
3. Run `./gradlew :plugin:runIde`. If it fails to compile, that is
   itself the most important finding — `Actions.kt`'s new imports
   (`PluginManagerCore`, `PluginId`, `CpuArch`, `SystemInfo`) are
   UNVERIFIED against the real Platform SDK, same as everything else in
   `plugin/`.
4. In the sandbox IDE, open a project with no existing `.mcp.json`. Run
   **Configure MCP Proxy (Claude Code)** from the Decoy toolbar group.
5. If `findBundledDecoyProxyBinary` returns null (no binary bundled for
   this platform, or `PluginManagerCore.getPlugin` doesn't resolve this
   dev-mode sandbox plugin the way it would a real installed one),
   **expected:** a clear error dialog naming the problem, not a crash —
   this specific failure mode (dev-mode `runIde` plugin path vs. a real
   installed plugin path) could not be checked without a real IDE and is
   flagged as a genuine open risk.
6. Otherwise: enter a target command (e.g. `npx`) and args (e.g.
   `-y @modelcontextprotocol/server-filesystem /tmp`). **Expected:** a
   Yes/No dialog shows the exact JSON entry before anything is written.
   Confirm it.
7. **Expected:** `.mcp.json` now exists at the project root with a
   `decoy` entry under `mcpServers`, `command` pointing at the bundled
   binary's absolute path inside the plugin's install directory, `args`
   starting with `["proxy", "--session", ...]`.
8. Manually add an unrelated entry to `.mcp.json` and re-run the action
   with a different target. **Expected:** the unrelated entry survives
   unchanged and the `decoy` entry is replaced, not duplicated — matching
   `McpConfigWriterTest.kt`'s merge tests.
9. If you have Claude Code available, point it at this project and
   confirm it can launch the bundled binary as an MCP server subprocess
   with no Python required on the machine at all.

## Things to watch for that would indicate a real bug, not just "needs polish"

- **The tool window rendering as a blank white box** (Test 1/3) — most
  likely a `codicon.css`/`codicon.ttf` resource-resolution failure via
  `webviewBaseUrl()`, or `renderWebviewHtml()` producing malformed HTML
  that Chromium can't parse (unlikely given `WebviewContentTest.kt`, but
  JUnit only checks string content, never that a real browser accepts it).
- Any real email/name/ID value appearing anywhere in the live view —
  `WebviewContentTest.kt`/`PanelContentTest.kt` already prove the
  HTML-building functions can't produce this from correct input, so
  seeing it live would mean `DecoyProjectService.kt`'s wiring is
  bypassing those functions or passing them something unexpected.
- The first-run notice reappearing per-project (see Test 2) or on every
  restart — a `PropertiesComponent` scope/persistence bug with zero
  JUnit coverage.
- Colors that don't change when you switch the IDE theme (Test 3's
  light/dark check) — would mean `computeWebviewTheme()` in
  `DecoyProjectService.kt` is reading a `UIManager` key that doesn't
  actually update with the theme, or fell through to
  `WebviewTheme.fallback()`'s hardcoded values.
- Codicon icons rendering as empty boxes/tofu (Test 3) — a font-loading
  failure via the `file://` base URL.
- The real "Clear this session" / override-form buttons not firing at
  all (Tests 5/7) — check for a JS console error naming
  `window.__decoyPost` as undefined, meaning the `JBCefJSQuery` bridge
  script never got spliced into the page before its own scripts ran.
- The pending "Updating…" banner never appearing on any button click
  (Test 5 step 2) — the one new "not frozen/broken" affordance Phase 13
  added; if it silently never shows, that specific UX goal wasn't met
  even if the underlying action still works.
