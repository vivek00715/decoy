# Production readiness checklist

Honest gap list between "compiles and passes the tests I can run without a
display" and "a stranger could install this and trust it." Written fresh
after the org-gateway/auth-token feature (chat_proxy.py backend +
credential-mode UI in both IDEs) landed, using that feature's own new
gaps as input -- not written before the feature existed.

Every item is marked one of:
- **verified-by-me-in-this-environment** -- I ran the actual command/test
  and saw the actual result, in this sandbox, this session.
- **needs-human-verification** -- cannot be exercised from here (no
  display, no real IDE, no real org network) -- someone has to actually
  do this and confirm.
- **not-yet-attempted** -- no one has done this yet, in any session.

No item is marked done because code exists or because a *different*,
adjacent thing was tested.

---

## 1. IntelliJ: the three items already flagged as needing hands-on confirmation

Still true tonight -- nothing in this session's work touched them, and
the new gateway-mode code path runs through the exact same
PasswordSafe/AnAction/JCEF machinery, so it inherits the same
unverified-in-a-real-IDE status, now for more fields than before.

- [ ] **needs-human-verification** -- PasswordSafe's real OS-keychain
      round-trip (store now, read back after an actual IDE restart, not
      a simulated one). This session added a real fix to
      `DecoyProjectService.kt`'s `secretsHost()`: the previous
      implementation used ONE fixed `CredentialAttributes` for every key,
      so storing the new gateway token/URL/mode fields would have
      silently overwritten the same PasswordSafe entry as the API key.
      Fixed by deriving per-key `CredentialAttributes`
      (`credentialAttributesFor(key)`) -- compile-verified only; the
      actual macOS Keychain / Windows Credential Manager / Linux
      libsecret round-trip for all 5 keys still needs a real IDE run.
- [ ] **needs-human-verification** -- the toolbar actions (now including
      the renamed "Set Credentials (API Key or Org Gateway)" action)
      actually rendering in the tool window's title-action bar and
      firing correctly on click, including the new mode-choice dialog
      (`Messages.showChooseDialog`), the gateway token/URL prompts, and
      the TLS-skip Yes/No confirmation dialog with its two custom button
      labels ("Skip Verification" / "Keep Verification On").
- [ ] **needs-human-verification** -- the approval banner and proxy
      status line actually rendering visibly and updating in a real
      interactive JCEF webview -- now including the NEW error line this
      session added (`proxyStatusLine()`'s gateway-check-failure
      override), which has never been seen rendered in an actual browser
      view, only reasoned about from the Kotlin source.

## 2. Windows filelock cross-process test

- [ ] **not-yet-attempted** -- still true, still the largest unresolved
      risk in the whole project. No Docker/Wine/QEMU/Lima/Multipass
      available in this sandbox to even attempt a stand-in. `filelock`
      (not `fcntl`) was chosen specifically because `fcntl` fails to
      import on Windows, and the three real cross-process races this
      session's predecessor fixed (`vault.py`/`crypto.py`/`audit_log.py`)
      were all found and fixed on macOS -- none of that has been run
      against a real Windows filesystem/locking implementation, which
      has different semantics (mandatory vs. advisory locking,
      `MoveFileEx` vs. POSIX `rename()` atomicity) that could hide a
      Windows-specific bug this session cannot see. Someone with a real
      Windows machine needs to run the cross-process test suite there
      before this project can honestly claim Windows support.

## 3. Error-handling coverage for the new gateway path (Part A)

- [x] **verified-by-me-in-this-environment** -- `checkGatewayConnectivity`
      (both `gatewayConnectivity.ts` and `GatewayConnectivity.kt`, kept
      in parity) classifies 401/403 as `unauthorized`, other 4xx/5xx as
      `http_error` (with response body included), and a thrown network
      exception as `unreachable` -- each with a specific, actionable
      message, not a generic failure. Verified via injected-fake-request
      unit tests: 6 tests each side, all passing (`npx jest
      gatewayConnectivity.test.ts`, `GatewayConnectivityTest.kt` via
      `./gradlew :core:test`).
- [x] **verified-by-me-in-this-environment** -- both `ChatProxyController`
      (VS Code) and `DecoyProjectService` (IntelliJ) now run this check
      BEFORE spawning the local proxy process when Org Gateway mode is
      active, and surface a failure via `getProxyStatus()`/
      `proxyStatusLine()`'s existing "error: ..." rendering path (no new
      UI plumbing needed -- reused the panel's existing error-display
      hook) rather than leaving an unhandled exception. Compile/type
      verified on both sides.
- [ ] **needs-human-verification** -- the connectivity check itself has
      only ever been run against a fake in-process request function in
      tests. It has NEVER made a real HTTP call, on either side --
      `nodeGatewayRequest` (Node's `https`/`http` module) and
      `httpClientGatewayRequest` (Java's `java.net.http.HttpClient`) are
      both compile-verified only. In particular: the TLS-skip path
      (`rejectUnauthorized: false` / a `TrustAllManager` SSLContext) has
      never been exercised against a real TLS-inspecting proxy -- if your
      org's inspection proxy does something Node/Java's TLS stack
      doesn't expect (a nonstandard cert chain, a corporate root CA that
      needs to be trusted rather than skipped entirely), this could
      still fail in a way no test here would catch. This is exactly the
      real-org-gateway verification you said you'd do yourself,
      separately -- flagging it here as the concrete first thing to check
      when you do.
- [ ] **not-yet-attempted** -- no test exercises what happens if the
      gateway accepts the connectivity check's single test request but
      then rejects or errors on later, real traffic once Claude Code is
      actually talking through the proxy (e.g. a gateway that
      rate-limits, or one whose auth token has a short TTL that expires
      mid-session). `chat_proxy.py`'s per-request error handling
      (upstream 4xx/5xx passed through as-is to the client, per
      `messages()`'s existing `if upstream_response.status_code >= 400`
      branch) was NOT changed or newly tested for gateway mode
      specifically in this pass -- it was already generic to any
      upstream, so it should behave the same way, but that's an
      assumption, not something re-verified here.
- [ ] **not-yet-attempted** -- no timeout/retry policy exists for the
      connectivity check itself beyond a flat 10s timeout on both sides.
      A slow-but-eventually-working gateway would report "unreachable"
      and block Start, with no retry option surfaced in the UI other
      than clicking Start again.

## 4. Installer / update / versioning story

- [ ] **not-yet-attempted, and the honest answer is: there isn't one.**
      Both extensions are "Install from Disk"/unpacked-directory only:
      - VS Code: no `vsce package` has ever been run in this project's
        history (confirmed absent from `RELEASE_CHECKLIST.md`'s own
        packaging section, still true tonight -- `vsce` was never
        installed or invoked). No `.vsix` has ever been produced.
      - IntelliJ: `./gradlew :plugin:buildPlugin` now DOES succeed and
        produces a real `plugin.zip` (103MB, confirmed this session,
        including `buildSearchableOptions` genuinely loading it in a
        headless IDE) -- this is real progress since the last checklist
        was written, when the plugin module had never even compiled. But
        that zip has never been installed via "Install Plugin from Disk"
        in a real, interactive IntelliJ and confirmed to actually load
        and run -- see item 1 above.
      - No version is set anywhere for the IntelliJ plugin specifically
        (`intellij-plugin/plugin/build.gradle.kts`'s `pluginConfiguration`
        block has no `version =` line -- confirmed by reading it this
        session). VS Code (`package.json`) and the Python core
        (`pyproject.toml`) are both pinned at `0.1.0`; IntelliJ has no
        equivalent.
      - No marketplace publisher account exists for either the VS Code
        Marketplace or the JetBrains Marketplace (unchanged from the
        prior `RELEASE_CHECKLIST.md` -- this genuinely requires a human
        with billing/identity, not something checkable from here).
      - No auto-update mechanism exists in either extension. A user who
        installs today gets zero update notifications ever, for any
        future release, until marketplace distribution exists (which
        provides update-checking for free) or a manual update mechanism
        is built.
      - **Bottom line: today, "install" means a developer manually
        building from source and sideloading the result. There is no
        path yet for a non-technical stranger to discover, install, or
        update this software.** Do not imply otherwise in any
        user-facing description.

## 5. CI

- [ ] **verified-by-me-in-this-environment (and the finding is bad news):**
      `gh run list --repo vivek00715/decoy --limit 5` returns ZERO
      workflow runs, despite `.github/workflows/ci.yml` and
      `release-binaries.yml` existing in the repo and a real `origin`
      remote with pushed commits (`git log origin/feature/phase1` shows
      real history, contradicting the older `RELEASE_CHECKLIST.md`'s
      claim of "no remote and no commits yet"). Either the workflow
      triggers don't match how this repo is actually being pushed to
      (e.g. a branch filter that excludes `feature/phase1`), or GitHub
      Actions isn't enabled for this repository. Either way: **CI has
      never actually run, even once, on real GitHub infrastructure.**
      This needs a human to check the Actions tab directly and fix
      whatever is blocking it -- I cannot diagnose past `gh run list`
      returning empty from here.

## 6. Dependency / security scanning currency

- [ ] **needs-human-verification** -- `pip-audit` and `npm audit` were
      last run clean in the session that produced `RELEASE_CHECKLIST.md`
      (undated relative to today) -- not re-run in this session. Kotlin/
      Gradle dependencies have never had an automated scanner wired in at
      all (manual check only, also undated relative to today). Re-run
      all three before any release; do not trust the old checkmarks past
      their own staleness warning in `RELEASE_CHECKLIST.md`.
- [ ] **not-yet-attempted** -- the new IntelliJ gateway-connectivity code
      added this session (`GatewayConnectivity.kt`) introduces a
      `TrustAllManager` (`X509TrustManager` that accepts every
      certificate) used when TLS-skip is enabled. This is INTENTIONAL
      and gated behind an explicit user opt-in with a loud warning (see
      `chat_proxy.py`'s `DECOY_UPSTREAM_SKIP_TLS_VERIFY` and the IDE-side
      `confirmDangerousToggle` dialogs) -- but it is exactly the kind of
      code pattern a security scanner or a security-focused code review
      would (correctly) flag on sight if it doesn't already know the
      context. Worth a deliberate note in any future security audit of
      this codebase so it isn't mistaken for an unintentional
      vulnerability, and worth a human specifically checking that no
      other code path can reach `skipTlsVerify = true` except through
      the explicit, logged, opt-in flow.

## 7. Other gaps separating "works when I test it carefully" from "a stranger could trust this"

- [ ] **not-yet-attempted** -- no `Set Credentials` UI escape hatch exists
      to switch FROM gateway mode BACK to Direct mode, or to clear
      stored credentials, from the panel/toolbar. `clearCredentials()`
      (both `credentialConfig.ts` and `CredentialConfig.kt`) exists and
      is unit-tested, but is not wired to any command, action, or button
      in either extension -- a user who configures gateway mode and later
      wants to go back to Direct mode has no in-app way to do it; they'd
      have to re-run "Set Credentials" and pick Direct again, which DOES
      work (it just re-prompts and overwrites), but there's no visible
      "current mode: X" indicator anywhere in the panel to tell them
      which mode is currently active before they do that.
- [ ] **not-yet-attempted** -- neither panel currently displays WHICH
      credential mode is active (Direct vs. Org Gateway) or the
      configured gateway URL anywhere in the UI. `proxyStatusLine()`/
      `ProxyStatusInfo` show process state and port, not credential mode.
      A user mid-troubleshooting has no way to visually confirm "yes,
      I'm in gateway mode, pointed at the right URL" without re-running
      Set Credentials or checking PasswordSafe/SecretStorage directly.
- [ ] **needs-human-verification** -- the TLS-skip warning dialog wording
      and button labels ("Skip Verification" / "Keep Verification On")
      have only been read, never seen rendered as an actual native
      dialog in either IDE -- confirm the wording reads clearly at
      dialog width and the default/focused button is the SAFE one (Keep
      Verification On), not the dangerous one, in both VS Code's
      `showWarningMessage` and IntelliJ's `showYesNoDialog`.
- [ ] **not-yet-attempted** -- no test (in either extension, or in
      `chat_proxy.py`) exercises the FULL real path end-to-end: a real
      chat-proxy process, actually spawned, actually configured with
      `ANTHROPIC_AUTH_TOKEN`/`DECOY_ANTHROPIC_UPSTREAM_URL`, actually
      receiving a real HTTP request from a client, actually forwarding
      to a gateway, actually returning a response through mask/unmask.
      `test_chat_proxy.py`'s new gateway tests use `httpx.ASGITransport`
      (an in-process fake upstream) and prove the header/precedence
      logic is correct; the IDE-side tests prove the credential
      resolution and env-var mapping are correct. Nothing proves those
      two halves correctly meet in the middle when a REAL OS process is
      spawned with these REAL env vars and REAL network calls happen --
      that gap is structural (this environment cannot safely make
      outbound calls to an arbitrary org's gateway), not an oversight.
- [ ] **not-yet-attempted** -- no rate-limiting, retry, or backoff policy
      exists anywhere in `chat_proxy.py` for upstream failures (gateway
      or direct). A flaky or overloaded gateway will surface every
      individual request's failure to the end client with no smoothing.
- [ ] **needs-human-verification** -- this project has never been used by
      anyone other than the person operating this session. Every
      "manual step removed" claim in this codebase's own comments is
      about a step ONE person confirmed was removed for THEMSELVES, not
      about a fresh install by someone unfamiliar with the project's
      internals. The single largest gap between "works when I test it
      carefully" and "a stranger could install this and trust it" is
      exactly that: **zero external users, ever.**

---

## Summary table

| Area | Verified here | Needs human | Not attempted |
|---|---|---|---|
| IntelliJ hands-on (keychain, actions, banner) | 0 | 3 | 0 |
| Windows filelock | 0 | 0 | 1 |
| Gateway error handling | 2 | 2 | 2 |
| Installer/update/versioning | 0 | 0 | 1 (confirmed absent) |
| CI | 1 (confirmed broken) | 0 | 0 |
| Dependency scanning | 0 | 1 | 1 |
| Other stranger-trust gaps | 0 | 2 | 4 |

**Nothing in this table should be read as close to done.** The gateway
feature itself is real, tested where testable, and compiles/loads
against real SDKs on both sides -- but "a stranger could install this and
trust it" needs, at minimum: a real Windows run, a real interactive
IntelliJ session, a real packaged `.vsix`/`plugin.zip` installed the way
an end user would install it, a working CI pipeline, and at least one
real external user who isn't the person who wrote the code.
