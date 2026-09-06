# Decoy

Decoy masks PII in prompts and database values before they reach any
LLM, and unmasks the response — with realistic, session-consistent fake
values (not just `[REDACTED]`), so the LLM can still reason about your
data correctly. It works identically whether the sensitive data was
typed directly into a prompt, pulled from a database (Postgres, MySQL,
SQL Server, SQLite, Snowflake), or reached via an MCP server.

**Read [`WHAT_THIS_PROTECTS_AGAINST.md`](WHAT_THIS_PROTECTS_AGAINST.md)
before routing real data through this** — it states plainly what is and
isn't covered, including a residual re-identification finding this
project probed for itself, exactly what network calls are made and when,
and why manual overrides exist as the correction mechanism for automated
detection's known limits. No absolute-privacy claims are made anywhere
in this project, including there.

## Who this is actually for, as currently architected

**Developers and teams who already have (or are willing to get) a
separate, billed Anthropic API key** — not every Claude subscriber.
Stated plainly rather than left implicit: a **Claude Pro or Max
subscription's login does not authenticate the chat proxy's onward call
to Anthropic.** Confirmed directly on a real Claude Code install: the
subscription's credentials live under a completely separate `oauthAccount`
identity (its own billing type, seat tier, organization) that Claude
Code itself uses for *its own* interactive sessions — the chat proxy
(below) makes its own, separate HTTP calls to Anthropic's API, which
requires an `ANTHROPIC_API_KEY` from console.anthropic.com, billed
per-token independently of any Pro/Max subscription. If you only have a
Pro/Max subscription and no interest in a separate metered API key, the
chat proxy genuinely isn't for you yet — the MCP proxy (`decoy proxy`,
which masks tool-call results Claude Code already fetches through its
own subscribed session) is likely still useful on its own.

This is a real cost and setup barrier, not just a UX rough edge that
automation removes — the IDE extensions can spawn the proxy, store the
key securely, and wire the config for you (see below), but they cannot
make the API key itself free or optional.

## ⚠ The single largest known risk before a real release: Windows is unverified

Every piece of cross-process coordination this project depends on —
sharing one vault between `decoy proxy` and `decoy chat-proxy`, the
audit log, the encryption key file — is guarded by `filelock`, chosen
specifically because `fcntl` (the POSIX-only alternative) fails to
*import* at all on Windows. That choice has only ever been exercised on
macOS/POSIX in every environment that has worked on this project so far
— **no Windows environment has ever been available to actually run it
on.** This is not a minor footnote or an edge case: Windows is a primary
target platform for this project's real end-to-end use, a large fraction
of real users will be on it, and this specific mechanism has never once
been confirmed there.

**If you're on Windows, run this before trusting any of the above:**

```powershell
git clone <this-repo-url>
cd decoy
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest tests/test_vault_cross_process.py tests/test_audit_log_cross_process.py tests/test_crypto_key_cross_process.py -v
```

Expected: **4 passed** (10-12 real separate `python` subprocesses per
test, racing to mutate/create the same file — the exact repro shape that
originally found three real cross-process bugs on macOS). If any fail
specifically on Windows, that is a genuine, actionable `filelock`/
`msvcrt` finding, not a flake, and should block treating this as
release-ready on that platform until resolved.

## What's in this repo

| Component | Language | Status |
|---|---|---|
| `src/decoy/` — core masking engine, CLI, chat proxy, benchmark suite | Python | Built and tested: 108 tests passing (includes real multi-process regression tests for the vault/audit-log cross-process fixes, and real HTTP-level tests of the chat proxy) |
| `vscode-extension/` — masking trace panel, override editor | TypeScript | Built and tested: 39 tests passing. Extension-host wiring (webview rendering, real VS Code API calls) requires manual verification — see `vscode-extension/MANUAL_TEST.md` |
| `intellij-plugin/` — masking trace tool window, override editor | Kotlin | `core/` module built and tested: 89 tests passing. `plugin/` module now compiles and packages against a real IntelliJ Platform SDK (`./gradlew :plugin:buildPlugin` succeeds) — no real GUI session has been run yet, though; see `intellij-plugin/MANUAL_TEST.md` for what's still unverified |

183 automated tests across all three, all independently re-run and
confirmed passing as of this writing — see each component's own test
suite for what's covered, and the two `MANUAL_TEST.md` files for what
still needs a real IDE to verify.

## Core design principles

- **Query-aware fidelity**: masking varies based on relevance to the
  current question — relevant sensitive fields get realistic, consistent
  fake values; irrelevant sensitive fields get flatly redacted. Relevance
  never changes *whether* a field is masked, only *how*.
- **Manual overrides beat automated classification, always** — checked
  before any regex, NER, shape-based, or relevance decision, anywhere in
  the system.
- **Local-first by default** — regex, shape-based rules, and keyword
  matching require no network call. Any cloud-LLM-assisted mode (relevance
  classification, or actually answering a question) is explicitly opt-in.
- **Layered detection, never a single method** — regex for well-formatted
  PII, a pluggable NER hook (not yet wired to a real backend — see
  `WHAT_THIS_PROTECTS_AGAINST.md` §3), and schema-aware structured masking
  for DB columns, each attributed in the audit trail.
- **Deny-by-default for structured data** — every DB/record column is
  masked unless it's safe by *shape* (boolean, low-cardinality
  categorical, date, numeric), never by column name or content-sampling
  guesses.
- **No absolute claims** — nowhere in this codebase, this documentation,
  or either IDE's UI.

## Installing the MCP proxy: standalone binary (no Python required)

If the only thing you need is `decoy-proxy` — the MCP server that sits
between an MCP client (e.g. Claude Code) and a real target MCP server,
masking results in transit — you don't need Python, pip, or a venv at
all. Prebuilt `decoy-proxy` binaries are attached to each tagged
[GitHub Release](../../releases), one per platform (Windows/macOS/Linux):

```bash
# 1. Download the binary matching your OS/arch from the release's assets,
#    e.g. decoy-proxy-<version>-<platform>(.exe)

# 2. Verify it before running it. Always check the downloaded binary's
#    SHA-256 checksum against the value published alongside it on the
#    release page — running an unverified downloaded binary is a
#    supply-chain risk. Example (macOS/Linux):
shasum -a 256 decoy-proxy-<version>-<platform>
#    ...and compare the output to the published checksum by hand.

# 3. Point your MCP client's config at the binary directly, e.g. in
#    Claude Code's MCP server settings, set `command` to the full path
#    of the downloaded (and verified) decoy-proxy binary instead of a
#    `python`/`pip`-installed entry point.
```

This path only covers `decoy-proxy` as a standalone MCP server process.
It does not give you the `decoy` Python library or CLI described below —
if you want to `import decoy` in your own code, or use `decoy audit` /
`decoy overrides` locally, install the Python package instead.

Both the VS Code extension and the IntelliJ plugin bundle these binaries
directly and can write the `.mcp.json` entry above for you — see
`decoy.configureMcpProxy` in the VS Code extension or "Configure MCP
Proxy (Claude Code)" in the IntelliJ plugin's Decoy toolbar. No manual
download/checksum steps needed in that path since the binary ships with
the extension/plugin itself.

**Writing `.mcp.json` does not connect the server automatically.**
Confirmed directly against a real `claude` CLI install: an entry added
this way shows as `⏸ Pending approval (run 'claude' to approve)` until a
human approves it interactively — both extensions' confirmation dialogs
name the exact fix, but it's worth stating here too: run `claude`
interactively in the project directory and approve the server when
prompted.

## Chat proxy: masking free text typed directly into a chat interface

`decoy proxy` (above) only covers data flowing through MCP tool calls.
`decoy chat-proxy` closes the other gap: text typed directly into a chat
interface (Claude Code's own prompt, or any OpenAI-SDK-based tool) never
went through any masking layer before this existed. It's a local HTTP
proxy exposing Anthropic's `/v1/messages` and OpenAI's
`/v1/chat/completions` shapes (both streaming and non-streaming): masks
outgoing message text, forwards to the real provider using a
server-side-only API key (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY` — read
from the proxy's own environment, never from the client request), and
unmasks the response before returning it.

```bash
pip install -e ".[chat-proxy]"
ANTHROPIC_API_KEY=sk-ant-... decoy chat-proxy --port 8787
# then point Claude Code at it:
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
```

**Shares one vault with `decoy proxy`, not a separate one** — but only if
you opt in and coordinate two things yourself, neither of which happens
automatically:

- `DECOY_VAULT_PERSIST=true` on BOTH processes, pointed at the same
  `.decoy/` directory (they're separate OS processes; this is the only
  cross-process sharing mechanism — see `decoy.vault`'s module docstring
  for the two dormant cross-process races this required fixing first,
  now covered by real multi-process regression tests).
- The SAME session id on both sides: `decoy proxy --session <id>` and
  either the chat proxy's `DECOY_SESSION_ID` env var or an
  `X-Decoy-Session-Id` request header set to the same `<id>`. There is no
  automatic way to correlate a chat-completions request with a specific
  MCP proxy invocation — this is a manual coordination step, stated
  plainly rather than implied automatic.

Without both of the above, the chat proxy still works, but its vault is
private to that process and will diverge from `decoy proxy`'s.

**If `decoy proxy` is registered via `.mcp.json`, one more step applies
no matter how that entry got there** — by hand, via `claude mcp add
--scope project`, or via an IDE extension's auto-config: confirmed
directly against a real `claude` CLI, a `.mcp.json` entry shows as `⏸
Pending approval (run 'claude' to approve)` and does not connect until a
human runs `claude` interactively in that directory and approves it. If
vault-sharing with the chat proxy doesn't seem to be working, check
`claude mcp list` for a pending entry before assuming the setup above is
broken.

This proxy has no authentication of its own — see `decoy.chat_proxy`'s
module docstring for that trust boundary stated precisely (it's meant
for localhost use, same trust model as any other local dev proxy).

### Verifying `filelock` on Windows

The cross-process safety this section depends on (`DECOY_VAULT_PERSIST`
sharing one vault/audit-log/key file between `decoy proxy` and `decoy
chat-proxy`) is guarded by `filelock`, which wraps `msvcrt` on Windows
and `fcntl` on POSIX. **This has only ever been exercised on macOS/POSIX
in the environment that wrote it — the Windows (`msvcrt`) path has never
actually been run.** This is stated plainly rather than assumed to work
because `filelock` "supports Windows" per its own documentation; nothing
in this repo has independently confirmed that on a real Windows machine,
and this project's own real target machine for its end-to-end test is
Windows.

If you're on Windows, run this before trusting any of the above — it's
the exact same real-multi-process concurrency repro used to find and fix
the three races these locks close (a vault mint race, an audit-log save
race, and a key-creation race), just run against your own OS:

```powershell
git clone <this-repo-url>
cd decoy
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest tests/test_vault_cross_process.py tests/test_audit_log_cross_process.py tests/test_crypto_key_cross_process.py -v
```

**Expected: all 4 tests pass.** Each one spawns 10-12 real separate
`python` subprocesses (not threads) racing to mutate/create the same
file, and asserts they converge on one consistent result instead of
losing or diverging data — see each test file's own docstring for
exactly what would print if `filelock` behaved differently on Windows
(e.g. a `unique_fakes`/`unique_keys` count greater than 1, or a missing
entry in the audit log's recovered set). If any of the three fail here
specifically (and they don't fail on macOS/Linux), that is a genuine,
actionable finding about `filelock`'s Windows behavior in this
environment, not a flake — please report the exact assertion output.

## Quick start (Python library + CLI)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# mask a paragraph and round-trip it
python3 -c "
from decoy.masker import Masker
m = Masker(session_id='demo')
result = m.mask_text('Contact jane@example.com or 415-555-2671')
print(result.masked_text)
print(m.unmask_text(result.masked_text))
"

# the decoy CLI: inspect/clear local state without an IDE open
decoy version
decoy audit list
decoy overrides show
decoy audit clear --session my-session   # keeps overrides
decoy audit clear-all                     # removes overrides too
```

Optional extras: `pip install -e ".[llm]"` (Anthropic SDK, for
LLM-assisted relevance classification or answering questions for real),
`pip install -e ".[mcp]"` (the MCP masking proxy), `pip install -e ".[chat-proxy]"`
(the chat-completions masking proxy — see above), `pip install -e ".[ner]"`
(Presidio-backed name/address detection for free text). This `pip
install` path pulls in Python and these dependencies as usual — it is
not a zero-dependency install; only the standalone binary above avoids
needing a Python interpreter at all.

**Enabling name/address detection in free text** (bare names typed
directly into a prompt are NOT masked otherwise — see
`WHAT_THIS_PROTECTS_AGAINST.md` section 3):

```bash
pip install -e ".[ner]"
python -m spacy download en_core_web_lg   # one-time, ~400MB, downloads a language model
DECOY_USE_PRESIDIO=true decoy chat-proxy ...   # or however you invoke the masking pipeline
```

Stays opt-in by default (unset `DECOY_USE_PRESIDIO`, or any value other
than `true`) since it's a real, heavy dependency chain most usage
doesn't need — not a hidden feature flag with no cost.

## Running the tests / dependency scans yourself

```bash
# Python
source .venv/bin/activate
python -m pytest tests/ -v
pip-audit

# VS Code extension
cd vscode-extension && npm install
npm run compile && npm run lint && npm test
npm audit

# IntelliJ plugin core module (pure Kotlin, no SDK needed)
cd intellij-plugin && ./gradlew :core:test
# the `plugin` module needs a real IntelliJ Platform SDK download to
# compile at all -- see intellij-plugin/MANUAL_TEST.md
```

## Project layout

```
src/decoy/              Python package: masker.py, record_masker.py,
                         query_aware.py, unified_pipeline.py,
                         edge_case_sampler.py, mcp_proxy.py, audit_log.py,
                         overrides.py, vault.py, crypto.py, benchmark.py,
                         cli.py
tests/                  pytest suite (87 tests)
vscode-extension/       VS Code extension (TypeScript, 39 tests)
intellij-plugin/        IntelliJ plugin (Kotlin, core/ 57 tests + plugin/)
AUDIT_LOG_FORMAT.md      cross-language on-disk audit log schema (v1)
WHAT_THIS_PROTECTS_AGAINST.md   read this before routing real data through Decoy
BENCHMARK_REPORT.md      Phase 10 privacy/utility benchmark results
CHANGELOG.md
```

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
