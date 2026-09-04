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

## What's in this repo

| Component | Language | Status |
|---|---|---|
| `src/decoy/` — core masking engine, CLI, benchmark suite | Python | Built and tested: 87 tests passing |
| `vscode-extension/` — masking trace panel, override editor | TypeScript | Built and tested: 39 tests passing. Extension-host wiring (webview rendering, real VS Code API calls) requires manual verification — see `vscode-extension/MANUAL_TEST.md` |
| `intellij-plugin/` — masking trace tool window, override editor | Kotlin | `core/` module built and tested: 57 tests passing. `plugin/` module (IntelliJ Platform SDK wiring) has **never been compiled** in the environment that wrote it — see `intellij-plugin/MANUAL_TEST.md` |

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
`pip install -e ".[mcp]"` (the MCP masking proxy), `pip install -e ".[ner]"`
(Presidio — the NER backend interface exists but has no wired
implementation shipped yet). This `pip install` path pulls in Python and
these dependencies as usual — it is not a zero-dependency install; only
the standalone binary above avoids needing a Python interpreter at all.

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
