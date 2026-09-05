# Decoy VS Code extension — manual test walkthrough

This file targets ONLY the surface that a real VS Code instance can
prove and the Jest suite structurally cannot: real `vscode.*` API wiring
(`createStatusBarItem`, `registerWebviewViewProvider`,
`showInformationMessage`, `showWarningMessage` with `modal: true`,
`globalState`), a webview actually rendering its HTML and executing its
injected `<script>` in a live DOM, and the postMessage bridge between
that live webview and the extension host's `onDidReceiveMessage`.

**What is already proven by the 39/39 passing Jest tests, and is
therefore NOT what these steps are testing, even where a step involves
looking at the same data:** audit log decrypt/parse/format-version
handling, cross-language Fernet interop (a real Python-produced
ciphertext is a hardcoded test fixture), override read/write/round-trip,
`PanelController`'s message-handling logic for all five commands
(`refresh`, `clearSession`, `clearSessionDataOnly`, `clearAll`,
`saveOverrides`) via an in-memory fake host, HTML-escaping/XSS safety,
CSP nonce correctness, the "Clear All includes overrides, Clear Session
Data Only doesn't" distinction, and that no real/fake value ever appears
in the generated HTML string. None of that logic needs re-checking by
hand below — where a step would otherwise just be re-confirming one of
those facts, it says so and moves on.

`extension.ts` itself (~100 lines: `activate`, `DecoyViewProvider`,
command registration) is the only source file with zero automated test
coverage. Every step below exists specifically to exercise something in
that file.

## Setup

1. Open the `decoy` repo root (the folder containing both `src/` (Python)
   and `vscode-extension/`) as a VS Code workspace folder.
2. In `vscode-extension/`, run `npm install` if you haven't already.
3. Open `vscode-extension/src/extension.ts` and press **F5** (or Run →
   Start Debugging). This launches a new "Extension Development Host"
   window with Decoy loaded.

## Test 1 — first-run notice (100% new surface: `globalState` + `showInformationMessage`)

Jest never touches `extension.ts`, so nothing about this is pre-verified.

**Expected:** On first activation, a real VS Code information message
popup appears with the `FIRST_RUN_NOTICE_TEXT` wording — plain language,
no "100% private" / "never leaves your machine" absolute claims.

Restart the Extension Development Host (stop and press F5 again) —
**expected:** the notice does NOT reappear, proving `context.globalState`
actually persisted `decoy.firstRunNoticeShown` across activations (this
is real host state; there is no way to fake this in Jest). Then run
`Decoy: Show Privacy Notice` from the Command Palette — **expected:** it
shows the notice again regardless of the stored flag, proving that
command path is wired independently of the first-run check.

## Test 2 — status bar item + webview view actually resolve (new surface: `createStatusBarItem`, `registerWebviewViewProvider`)

**Expected:** A status bar item reading `Decoy: 0 request(s)` appears in
the status bar. Click the Decoy icon in the activity bar — **expected:**
`resolveWebviewView` fires and a live webview renders inside VS Code's
UI (not just a string in a test assertion) showing the Phase 13 empty
state (a Codicon inbox icon, "No requests yet", "Once Claude Code sends
a prompt through Decoy, you'll see what got masked here.") and empty
"Always mask"/"Never mask" override sections, each with a real labeled
form (Field name / Pattern (regex) inputs + Add buttons), not the older
bare "No requests recorded yet." text. Open the Webview Developer Tools
(Command Palette → "Developer: Open Webview Developer Tools") and
confirm there are no console errors — this is the only way to catch a
real webview-context JS error (a bad DOM selector, a CSP violation
blocking the inline script), since Jest's `webviewContent` tests only
check the HTML *string*, never execute it.

**Visual check — light and dark theme (Phase 13):** Toggle VS Code's
color theme (Command Palette → "Preferences: Color Theme") between a
light theme (e.g. "Light+") and a dark theme (e.g. "Dark+"). **Expected:**
the panel's background, text, borders, badges, and buttons all switch to
match — every color in `webviewContent.ts` comes from a `--vscode-*` CSS
variable (`--vscode-editor-background`, `--vscode-foreground`,
`--vscode-button-background`, etc.), never a hardcoded hex value, so a
color that doesn't follow the theme switch is a real bug. Also try a High
Contrast theme (Command Palette → "Preferences: Color Theme" → a
"High Contrast" entry) and confirm text stays legible against its
background. Confirm the Codicon icons (checkmark for masked, eye-slash
for redacted, warning triangle next to an override-layer entry, refresh/
trash-can toolbar icons) render as real icons in all three themes, not
empty boxes/tofu — this would mean `media/codicons/codicon.ttf` failed
to load via the webview's `asWebviewUri`-resolved `codiconsUri`.

## Test 3 — live rendering pipeline connects end-to-end (thin new surface on top of Jest-covered content)

Everything about *what* should appear in the panel (correct field names,
decisions, reasons, layers, and that no real/fake value ever leaks into
the HTML) is already proven by Jest. This step only confirms the LIVE
pipeline — real file write → real `resolveWebviewView`/refresh → real
webview DOM — actually connects; skim the content, don't re-audit it
field by field.

From the **repo root**, with the Python venv active:

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

This must be run against the same folder VS Code has open as its
workspace root, or the extension looks in the wrong place.

**Expected (Phase 13, new — live auto-refresh, no click needed):** within
a second or two of the script finishing, the panel updates ON ITS OWN —
this is the new `vscode.workspace.createFileSystemWatcher` on
`.decoy/{audit.enc,overrides.json}` in `extension.ts` firing
`provider.refresh()` without any user action, which is what makes the
header's pulsing "live" status dot true rather than decorative. If it
does NOT update on its own, click **Refresh** manually as a fallback and
separately report the auto-refresh as broken.

**Expected (the request card):** the new request renders as a collapsible
`<details>` card (open by default, since it's the most recent), with a
header row showing the timestamp, a short session-id badge, and small
decision-count "chips" (e.g. a blue check-mark chip showing count 2,
etc.) — clicking the header's chevron collapses/expands the field-level
table underneath rather than dumping it flat. **Expected (the new
thing):** the status bar text updates from `Decoy: 0 request(s)` to
`Decoy: 1 request(s)` — this proves `updateStatusBar()` (never called
from Jest, since it needs a real `vscode.StatusBarItem`) actually fires
on a real postHtml callback. **Expected (sanity spot-check only, already
proven structurally by Jest):** one request group appears with 3
decisions and no real value visible anywhere.

## Test 4 — add an override via the live webview, confirm it changes real masking (new surface: the actual postMessage bridge)

Jest's `panelController.test.ts` proves `saveOverrides` handling logic
works when called directly against a fake host. It CANNOT prove the real
webview's `<button>` click actually fires, actually calls
`vscode.postMessage`, and that the extension host's real
`webview.onDidReceiveMessage` actually receives it — that bridge only
exists in a live webview.

1. In the live panel's "Always mask" section, type `employee_id` into
   the field-name input and click **Add** (a real mouse click on a real
   button in the live DOM, not a simulated event). **Expected (Phase 13,
   new):** a brief "Updating…" indicator with a spinning icon appears
   right where the disclaimer/hint text sits — this is `showPending()`
   firing synchronously on click, before the round-trip to the extension
   host and back completes, so the panel never looks frozen during that
   gap. Also try clicking **Add** with the field-name input left blank —
   **expected:** the input gets a red border and an inline error appears
   ("Field name can't be empty."), and nothing is sent (no pending
   indicator, no disk write) — client-side validation, not a round trip.
2. **Expected:** `<repo-root>/.decoy/overrides.json` on disk now lists
   `employee_id` under `always_mask.field_names` — confirming the click
   → postMessage → extension host → disk write chain works end-to-end.
3. Re-run the Python snippet from Test 3.
4. **Expected:** the new request's `employee_id` entry shows
   `decision: masked`, `layer: override`, reason mentioning
   `manual override: always_mask` — confirming the UI-added override
   changed real Python-side masking behavior, not just the display.

## Test 5 — "Clear this session" button inside a request group (new surface: dynamically-generated buttons + their listeners)

The per-request "Clear this session" buttons are generated inside a
`.map()` over live data with `data-session-id` attributes and listeners
attached via `querySelectorAll` in the injected `<script>`. Jest checks
the resulting HTML *string* contains the right markup; it never executes
that script in a DOM, so it can't prove the listeners actually attach to
the right elements or read the right `data-session-id`.

1. Click **"Clear this session"** on the request group from Test 3/4.
2. **Expected:** that request group disappears from the live panel
   immediately, no manual refresh needed. Requests from other sessions
   (if you have any) remain visible.
3. **Expected (sanity spot-check, logic already proven by Jest):** the
   underlying audit/vault removal actually happened — e.g. a fresh
   `ask_over_data` call with the same real email under a *new* session
   should mint a *different* fake, since the old vault mapping is gone.

## Test 6 — "Clear Session Data Only" vs "Clear All Local Data", including the real modal dialog (new surface: `showWarningMessage` with `modal: true`)

Jest's fake host's `confirmDestructive` just returns a boolean instantly
— it never renders VS Code's actual native modal dialog, never exercises
a real "Cancel" default, and can't catch a UI bug where the wrong message
text shows for the wrong button.

1. Click **"Clear Session Data Only"**. **Expected:** VS Code's real
   modal warning dialog appears (not a webview popup) with wording that
   explicitly says overrides are KEPT. Click **Cancel** — **expected:**
   nothing changes; re-open the panel and confirm your override from
   Test 4 and any request data are both still present.
2. Click **"Clear Session Data Only"** again and confirm this time.
   **Expected:** the panel empties to "No requests recorded yet", but
   the "Always mask: employee_id" entry from Test 4 is still listed.
3. Add a request again (re-run Test 3's snippet) so there's something to
   clear. Click **"Clear All Local Data (incl. overrides)"**.
   **Expected:** the modal wording explicitly says overrides are
   REMOVED too and points you to "Clear Session Data Only" as the
   alternative if you want to keep them. Confirm it.
4. **Expected:** the panel empties to "No requests recorded yet" AND the
   override list is now empty — confirming `clearOverrides()` actually
   ran from a real confirmed modal dialog, not just when called directly
   in a test.

## Test 7 — configure MCP proxy on a machine with zero Python installed (new surface: `decoy.configureMcpProxy`, `mcpConfigCommand.ts`)

Jest's `mcpConfig.test.ts` (14 tests) already proves the pure merge logic
in `mcpConfig.ts`: adding a new entry, preserving unrelated `mcpServers`
entries and unrelated top-level keys, flagging/overwriting an existing
`decoy` entry, and round-tripping through `serializeMcpConfig`/
`parseMcpConfig`. None of that needs re-checking below. What's untested
by Jest (no real `vscode.*` APIs, no real filesystem write via the
extension host, no bundled binary lookup on a real machine) is exactly
what this walkthrough exercises.

This is also the one part of this extension's manual test plan that is
meaningfully different from before: `extension.ts` previously never
invoked Python at all (the extension only reads/writes local JSON/audit
files that a separately-run Python process produces), so there was no
"requires Python" step to remove here. This test's whole point is
confirming that claim stays true even for the NEW MCP-proxy-configuring
functionality -- i.e. that a machine with no Python, pip, or venv at all
can still get a working `decoy proxy` MCP server wired into Claude Code,
because the extension bundles a prebuilt `decoy-proxy` binary instead of
shelling out to a Python interpreter.

**Setup:** ideally run this on a machine (or a container/VM) that
genuinely has no `python`/`python3` on `PATH`, to prove the "no Python
required" claim for real rather than by absence of a call in the code.
If that's not available, at minimum confirm nothing in this flow shells
out to `python`/`pip` (grep `mcpConfigCommand.ts` and `mcpConfig.ts` --
there should be zero references).

1. Before packaging, run `npm run bundle-binaries` from `vscode-extension/`
   after building at least one platform's binary via
   `python scripts/build_binary.py` at the repo root (this step itself
   does need Python -- it's a BUILD-time step for whoever packages the
   extension, not something the extension does at runtime). Confirm
   `vscode-extension/bin/<platform>-<arch>/decoy-proxy` (or `.exe`)
   exists and is executable afterward.
2. Launch the Extension Development Host (F5) with a workspace folder
   open that has no existing `.mcp.json`.
3. Run **Decoy: Configure MCP Proxy (Claude Code)** from the Command
   Palette.
4. When prompted for the target MCP server command, enter something
   realistic, e.g. `npx` with args `-y @modelcontextprotocol/server-filesystem /tmp`.
5. **Expected:** a modal confirmation appears showing the exact JSON
   entry that will be written (`command` pointing at the bundled
   binary's absolute path, `args` starting with
   `["proxy", "--session", ...]`) before anything touches disk, AND
   explicitly states that this does not connect the server automatically
   -- it should name the exact fix: run `claude` interactively in this
   directory and approve the "decoy" server when prompted. Click
   **Write Config**.
6. **Expected:** `.mcp.json` now exists at the workspace root containing
   a `decoy` entry under `mcpServers` matching what was shown, and the
   confirmation message names the exact path written AND repeats the
   same approval instruction (run `claude` interactively in this
   directory, approve "decoy" when prompted) -- not just "restart Claude
   Code", which on its own would leave the server permanently pending,
   confirmed directly against a real `claude` CLI (see Test 9 below).
7. Re-run the command with a different target command. **Expected:** the
   confirmation message explicitly says the existing `decoy` entry will
   be REPLACED, and after confirming, `.mcp.json`'s `decoy` entry
   reflects only the new target -- no stray duplicate entries.
8. Manually add an unrelated entry to `.mcp.json`'s `mcpServers` (e.g.
   `"other-server": {"command": "foo", "args": []}`) and re-run the
   command. **Expected:** after writing, `other-server` is still present
   unchanged -- confirming the merge doesn't clobber unrelated config.
9. If you have Claude Code available: from this same workspace directory,
   run `claude` interactively (plain `claude`, no flags) and approve the
   "decoy" server when it prompts you -- this is the exact, concrete step
   named in the confirmation/info messages above, not paraphrased here.
   Confirmed directly against a real `claude` CLI install: an entry
   written to `.mcp.json` shows as `⏸ Pending approval (run 'claude' to
   approve)` via `claude mcp list`/`claude mcp get decoy` until this step
   happens -- it is not optional polish, the server will not connect
   without it. After approving, confirm it can actually launch the
   bundled `decoy-proxy` binary as an MCP server subprocess (no Python
   error, no "command not found") and that tool calls through it produce
   masked results matching the rest of this project's masking behavior.
   This is the strongest real-world confirmation of the "no Python
   required" story.
10. On a platform with no bundled binary for the running machine's
    `process.platform`-`process.arch` (e.g. only macOS was bundled, but
    you're running the Extension Development Host on Linux), running the
    command should show a clear error naming the missing platform/arch
    rather than crashing or silently writing a broken `command` path.

## Things to watch for that would indicate a real bug, not just "needs polish"

- Any real email/name/ID value appearing anywhere in the live panel —
  Jest already proves the HTML-building function can't produce this, so
  seeing it live would mean something in `extension.ts`'s wiring (not
  covered by Jest) is bypassing that function entirely.
- The panel silently failing to update after a button click — would mean
  the real webview→extension `postMessage`/`onDidReceiveMessage` bridge
  is broken in a way `PanelController`'s fake-host tests structurally
  cannot catch.
- The first-run notice reappearing on every activation, or the modal
  dialogs showing the wrong confirm/cancel button — both are real
  `vscode.*` API usage bugs with zero Jest coverage.
