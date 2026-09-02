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
UI (not just a string in a test assertion) showing "No requests recorded
yet in this workspace." and empty override lists. Open the Webview
Developer Tools (Command Palette → "Developer: Open Webview Developer
Tools") and confirm there are no console errors — this is the only way
to catch a real webview-context JS error (a bad DOM selector, a CSP
violation blocking the inline script), since Jest's `webviewContent`
tests only check the HTML *string*, never execute it.

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

Click **Refresh** in the live panel. **Expected (the new thing):** the
status bar text updates from `Decoy: 0 request(s)` to
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
   button in the live DOM, not a simulated event).
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
