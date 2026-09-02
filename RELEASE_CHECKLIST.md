# Release checklist

Use this for every release, starting with v0.1.0. **A version is tagged
only once every item below is actually true, verified in this run — not
because it was true in an earlier session, and not because the checklist
itself was written.** Where an item can't be verified from this
environment, it's marked so explicitly; those need a human to confirm
before tagging.

## v0.1.0 — status as of this writing

### Version bump

- [x] `pyproject.toml` — `version = "0.1.0"`
- [x] `src/decoy/__init__.py` — `__version__ = "0.1.0"`
- [x] `vscode-extension/package.json` — `"version": "0.1.0"`
- [ ] IntelliJ plugin version — **not yet set**; add a version to
      `intellij-plugin/plugin/build.gradle.kts`'s `pluginConfiguration`
      block before tagging (currently unset since that module has never
      been built).

### CHANGELOG entry

- [x] `CHANGELOG.md` has a `[Unreleased]` section covering every phase's
      work. **Before tagging:** rename `[Unreleased]` to `[0.1.0] —
      <release date>` and start a fresh empty `[Unreleased]` section.

### Tests, verified in this session (re-run yourself before tagging — don't trust this list past today)

- [x] Python: `python -m pytest tests/ -v` — 89 passed (includes two new
      MCP proxy tests added to close the outgoing-argument gap below)
- [x] VS Code extension: `npm run compile && npm run lint && npm test` —
      39 passed (Node.js had to be reinstalled mid-session after an
      unrelated Homebrew dependency conflict broke it — re-confirm node
      itself works in whatever environment you release from)
- [x] IntelliJ `core/` module: `./gradlew :core:test` — 57 passed
- [ ] IntelliJ `plugin/` module — **never compiled**. This is not a
      "probably fine" gap; `./gradlew :plugin:runIde` has never been run
      successfully (or at all) anywhere in this project's history.
      **Must be done by a human with a real IntelliJ instance before
      this module ships** — see `intellij-plugin/MANUAL_TEST.md` Test 1.

### MCP proxy outgoing-argument gap — narrowed, NOT fully closed; the likely case is still open

- [x] Confirmed the target MCP server is explicitly NOT assumed local by
      this project's own design (the spec names Snowflake — cloud-only,
      no local deployment mode — and Elasticsearch — commonly a
      remote/hosted cluster — as expected targets), so the prior
      "outgoing arguments forwarded unmasked" behavior was a real gap in
      the core guarantee, not a documentation nuance.
- [x] Fixed for one specific case: outgoing tool call arguments are
      reversed (vault lookup: fake → real) before forwarding to the
      target, when the argument matches a value already masked earlier
      in the same session — the correct direction, since masking the
      argument would break a target's real lookup against its own real
      data, which cannot work against a fake value in the general case.
      Verified with two new tests:
      `test_outgoing_argument_matching_a_previously_masked_value_is_unmasked_before_forwarding`
      and `test_outgoing_argument_never_previously_masked_passes_through_unchanged`.
- [ ] **NOT fixed, and not fixable from `decoy-proxy`'s position: the
      single most likely real-world case.** A value typed directly into
      an MCP host's chat (Claude Code, or any other MCP client) and used
      by the host to construct a tool call never touches Decoy's vault —
      the host calls its own LLM directly with the raw prompt and only
      contacts `decoy-proxy` once a tool call has already been decided
      and built. This is architecturally outside what any MCP server can
      see, confirmed against `decoy-proxy`'s actual registered handlers
      (`on_list_tools`/`on_call_tool` only — no protocol path carries the
      host's own conversational prompt to an MCP server). Closing this
      requires the *host application* to route prompts through Decoy's
      masking before constructing tool calls — an integration decision
      for whoever controls the host, not something to check off here.
      **Before recommending `decoy-proxy` as sufficient protection for a
      Claude-Code-style deployment, make sure whoever is evaluating it
      understands this boundary** — `WHAT_THIS_PROTECTS_AGAINST.md` §5
      item 3 states it in full, but a reader skimming only the proxy's
      one-line pitch could still miss it.
- [x] `WHAT_THIS_PROTECTS_AGAINST.md` §5 item 3 updated with the fix, the
      narrower case it actually covers, and the specific unfixed gap
      named above — not folded into generic "any value Decoy never saw"
      language.

### Dependency scans, verified in this session

- [x] `pip-audit` — clean, no known vulnerabilities
- [x] `npm audit` (vscode-extension) — clean, 0 vulnerabilities
- [ ] Kotlin/Gradle dependencies — no automated scanner is wired in
      (OWASP dependency-check or similar was never installed in this
      environment). A manual check during Phase 9 found no published
      CVEs for the exact pinned versions (`kotlin-stdlib:2.4.10`,
      `kotlinx-serialization-json:1.11.0`, `com.macasaet.fernet:
      fernet-java8:1.5.0`) at that time — re-check before release, since
      that check has an expiration date by nature.

### Benchmark results attached

- [x] `BENCHMARK_REPORT.md` reflects the current code (regenerated after
      the quasi-identifier mitigation was added — not stale from an
      earlier run)
- [x] Privacy Score measured for real: no_mask=0.000, all_pii_mask=1.000,
      query_aware_mask=1.000 (36 cases)
- [x] SPIA-style probe measured for real, finding documented, v1
      mitigation built and tested
- [x] Manual-override hard pass/fail tests passing
- [ ] **Utility Score / Balanced Score — NOT measured.** A real API key
      was configured and a real call was attempted; it failed on
      insufficient account credits, not a code defect. **Must be
      re-attempted with a funded account before this line can be
      checked** — do not tag v0.1.0 claiming a Balanced Score exists when
      it doesn't.

### Cross-platform smoke test

- [x] macOS — this entire project was built and tested on macOS
      (Darwin) in this session.
- [ ] Windows — **not tested anywhere in this project's history.** Path
      handling (`.decoy/` paths, file permission calls like `os.chmod`
      and `Files.setPosixFilePermissions`), process spawning (MCP
      subprocess handling), and file permission semantics all need
      verification on Windows before release.
- [ ] Linux — **not tested anywhere in this project's history**, same
      categories as Windows.

### Documentation

- [x] `WHAT_THIS_PROTECTS_AGAINST.md` — written, cites actual measured
      Phase 10 numbers and the SPIA finding specifically, states exact
      network-call conditions, no absolute claims
- [x] Linked from `README.md`'s first section
- [ ] Surfaced in-app via each IDE's first-run notice — **already true**
      for both (`extension.ts`'s `FIRST_RUN_NOTICE_TEXT`,
      `DecoyFirstRunActivity.kt`'s equivalent), but re-confirm the exact
      wording still matches this doc's current content before release,
      since the doc has been edited since those were first written.
- [x] `AUDIT_LOG_FORMAT.md` — cross-language schema, reviewed
- [x] `CONTRIBUTING.md`, `LICENSE` (Apache 2.0)

### Packaging

- [ ] VS Code extension packaged via `vsce package` — **not yet run**.
      `vsce` itself was never installed/invoked in this session; do this
      and confirm the resulting `.vsix` installs locally
      (`code --install-extension decoy-0.1.0.vsix`) before release.
- [ ] IntelliJ plugin packaged via `./gradlew :plugin:buildPlugin` —
      blocked on the same unverified-compile gap noted above; cannot
      produce a real plugin ZIP until that module compiles at all.

### Marketplace prep — genuinely requires you, not something I can do from here

- [ ] VS Code Marketplace publisher account created, `vsce publish`
      credentials configured
- [ ] JetBrains Marketplace publisher account created
- [ ] Store listing metadata (description, icons, screenshots) for both
      marketplaces — check each marketplace's *current* submission
      requirements directly rather than assuming, since these change
- [ ] Privacy-practices disclosures for both marketplaces, informed by
      `WHAT_THIS_PROTECTS_AGAINST.md`'s content but written to each
      store's specific required format

### CI

- [x] `.github/workflows/ci.yml` written — Python (3-OS matrix), VS Code
      extension, IntelliJ `core/` module
- [ ] **Never actually triggered** — this repo has no remote and no
      commits yet as of this writing. Push to a real GitHub repository
      and confirm all jobs go green as a genuine first-run verification,
      not a formality.

## Tagging

Do not run `git tag v0.1.0` until every unchecked box above is checked
for real, in an actual run, by whoever is doing the release — re-reading
this checklist and agreeing it looks plausible is not the same as
re-running the commands.
