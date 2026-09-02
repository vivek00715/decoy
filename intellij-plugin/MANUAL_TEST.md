# Decoy IntelliJ plugin — manual test walkthrough

This file targets ONLY the surface a real IntelliJ instance can prove.
The `core` module (57 JUnit tests, all passing — run `./gradlew :core:test`
yourself to confirm) already proves: audit log decrypt/parse/format-version
handling, real cross-language Fernet interop (a genuine Python-produced
ciphertext is a hardcoded test fixture in `CryptoTest.kt`), override
read/write/round-trip PLUS remove/edit-in-place transform logic
(`OverrideEditsTest.kt`, including the documented edge cases: editing a
value that collides with an existing entry merges rather than duplicates,
editing to a blank value removes, editing a missing value appends),
`PanelController`'s message-handling logic for all five operations
(`refresh`, `clearSession`, `clearSessionDataOnly`, `clearAll`,
`saveOverrides`) via an in-memory fake `PanelHost`, and the HTML-rendering
logic in `PanelContent.kt` (escaping/XSS safety, the no-absolute-privacy
disclaimer text, the "Clear All includes overrides" distinction, the
edit/remove hyperlink encoding for every override entry including
patterns containing a literal colon, no real/fake value ever appearing in
the rendered HTML string). None of that needs re-checking below.

**The entire `plugin` module (`DecoyToolWindowFactory.kt`,
`DecoyProjectService.kt`, `Actions.kt`, `DecoyFirstRunActivity.kt`,
`plugin.xml`) was never compiled in the environment that wrote it.**
Getting the IntelliJ Platform Gradle Plugin to resolve a full IDE
distribution here was judged disproportionate to the value it would add
without a real IDE to actually run the result in anyway. Every claim
below about this module is therefore unverified until you do the steps
in this file — not "probably fine," genuinely unknown until checked.

## Setup

1. You need a real IntelliJ IDEA (Community is fine, matches the `IC`
   platform type this plugin targets) installed, or let the Gradle
   IntelliJ Platform plugin download one via the `runIde` task below.
2. From `intellij-plugin/`, run `./gradlew :plugin:runIde`. **This is the
   first time this module's Kotlin will ever be compiled against the
   real IntelliJ Platform SDK.** Expect this to surface compile errors
   the hand-written code couldn't be checked for — read them before
   assuming anything else in this file is reachable. If it fails to
   compile, that is itself the most important finding from this whole
   walkthrough; report the exact error back before going further.
3. Once it launches, open (or create) any project as the sandbox IDE's
   workspace — this becomes the project whose root the plugin treats as
   `rootDir` (via `project.basePath`).

## Test 1 — the plugin actually compiles and loads (this is not a formality)

**Expected:** `./gradlew :plugin:runIde` succeeds and a second IntelliJ
window opens with Decoy's tool window icon visible on the right-hand
tool window bar (anchor="right" in plugin.xml). If this step fails,
every judgment call flagged in the source files (see each file's
"UNVERIFIED" doc comment) is a candidate cause — check the compiler
error against the specific API call it names.

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

## Test 3 — tool window opens and the live JEditorPane actually renders

**Expected:** Clicking the Decoy tool window icon opens a panel showing
"No requests recorded yet in this project." and empty "Always mask" /
"Never mask" lists — this is `PanelContent.kt`'s `renderHtml()` output
(already proven correct as a string by `PanelContentTest.kt`) actually
displaying inside a live `JEditorPane`, which JUnit cannot execute.
Right-click inside the pane → check for any Swing rendering artifacts
(broken table borders, HTML not parsing) that a string-level test
can't catch.

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

## Test 5 — "Clear this session" hyperlink (new, unusual-pattern surface)

The per-session clear action is a plain `<a href="decoy-clear-session:...">`
link inside the read-only `JEditorPane`, intercepted by a
`HyperlinkListener` in `DecoyProjectService.kt` — this specific
pattern (using a custom URI scheme to route an in-app action through an
HTML hyperlink) has no equivalent in the VS Code extension and is the
least-precedented IntelliJ UI choice in this module.

1. Click the "Clear this session" link on the request group from Test 4.
2. **Expected:** the `HyperlinkListener` fires, `handleHyperlink()`
   correctly strips the `decoy-clear-session:` prefix to recover the
   session ID, and `PanelController.clearSession(sessionId)` runs — the
   pane should update to "No requests recorded yet" immediately.
3. If clicking the link does nothing, or throws, this specific pattern
   was the wrong approach and the tool window needs a real component
   (e.g. a `JBTable` cell with an embedded button) instead of an HTML
   link inside a read-only pane — report which happened.

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

## Test 7 — edit and remove an override, and add a pattern override (new surface: `Messages.showInputDialog`'s pre-filled `initialValue` overload)

Adding a *field name* override was already covered by Test 6, and its
underlying transform logic (`addOverrideEntry`) is JUnit-tested. This
test targets what's new: removing/editing an existing entry via its
hyperlink, and adding a *pattern* override (not just a field name).

1. Click **"Add Always-Mask Pattern"**, enter `EMP-\d{6}`. **Expected:**
   `.decoy/overrides.json` now lists it under `always_mask.patterns`
   (not `field_names`), and the pane shows it in `<code>` styling with
   `[edit]`/`[remove]` links next to it.
2. Click **`[edit]`** next to that pattern. **Expected:** an input dialog
   opens with the text field **pre-filled** with `EMP-\d{6}` (this is the
   specific new/unverified behavior — `Messages.showInputDialog`'s
   6-argument overload with an `initialValue` parameter was confirmed to
   exist with this exact signature by reading IntelliJ Community's real
   `Messages.java` source during development, but was never exercised by
   an actual compiler or a real dialog). Change it to `EMP-\d{7}` and
   confirm. **Expected:** the pane now shows `EMP-\d{7}`, and
   `.decoy/overrides.json` reflects the change with the entry's list
   position unchanged (there was only one entry, so position isn't a
   strong signal here — if you have multiple patterns, edit the middle
   one and confirm the others don't reorder, matching
   `OverrideEditsTest.kt`'s `` `edit replaces in place preserving
   position` `` test).
3. Click **`[remove]`** next to it. **Expected:** the entry disappears
   from both the pane and `.decoy/overrides.json` immediately, no
   confirmation dialog (removing a single override entry was deliberately
   NOT made a "destructive, confirm first" action, unlike Clear Session
   Data Only / Clear All — it's a single easily-reversible edit, not a
   bulk irreversible wipe; flag to the coordinator if a confirmation step
   is wanted here after all).
4. If clicking `[edit]` or `[remove]` does nothing, or throws: check
   `DecoyProjectService.kt`'s `decodeOverrideLink` — the most likely
   failure is the link-splitting logic not matching what
   `PanelContent.kt`'s `appendOverrideItem` actually encoded (both sides
   were written to agree on a `kind.name:list.name:value` format split
   with a limit of 3, but this agreement was never checked by a compiler
   that understands both files together).

## Things to watch for that would indicate a real bug, not just "needs polish"

- **The module failing to compile at all** (Test 1) — the single most
  likely outcome given zero prior compilation, and the reason this
  whole file exists.
- Any real email/name/ID value appearing anywhere in the live pane —
  `PanelContentTest.kt` already proves the HTML-building function can't
  produce this from correct input, so seeing it live would mean
  `DecoyProjectService.kt`'s wiring is bypassing that function or passing
  it something unexpected.
- The first-run notice reappearing per-project (see Test 2) or on every
  restart — a `PropertiesComponent` scope/persistence bug with zero
  JUnit coverage.
- The hyperlink-based "Clear this session" control not firing at all
  (see Test 5) — the specific IntelliJ UI pattern used here is the least
  precedented choice in the whole module.
- The edit dialog opening with an EMPTY text field instead of pre-filled
  with the current value (see Test 7 step 2) — this would mean the
  `initialValue` overload of `Messages.showInputDialog` doesn't behave as
  the read-but-uncompiled source suggested.
- An edited or removed override reappearing after a refresh, or a
  different entry than the one clicked being changed — would mean the
  `kind:list:value` link encoding/decoding contract between
  `PanelContent.kt` and `DecoyProjectService.kt` has drifted out of sync.
