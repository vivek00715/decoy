# Contributing to Decoy

## Before you start

Read [`WHAT_THIS_PROTECTS_AGAINST.md`](WHAT_THIS_PROTECTS_AGAINST.md) —
it states this project's actual guarantees and limits. Any change to
detection logic (`masker.py`, `record_masker.py`, `query_aware.py`)
should be weighed against those stated limits: does it close a gap
mentioned there, or introduce a new one that needs to be documented?

## Core design principles (apply to any change, not just new features)

- **Manual overrides beat automated classification, always.** If you add
  a new detection or classification layer, it must be checked *after*
  `overrides.py`'s `always_mask`/`never_mask`, never before.
- **Fail toward more redaction, never less.** Any error path (a failed DB
  connection, a malformed value, an unexpected exception) should redact
  or fall back to a safer default, not silently pass through unmasked
  data.
- **Never log real or fake sensitive values.** This applies independently
  to both application logs (`logging_config.py`) and the audit trail
  (`audit_log.py`) — a change to one doesn't get a pass on the other.
- **No absolute claims.** No "100% private," "your data never leaves
  your machine," or similar unqualified language in code comments, UI
  copy, docstrings, or documentation.
- **Deny-by-default for structured data, never by column name.**
  `record_masker.py`'s column classification is shape-only (boolean,
  low-cardinality, date, numeric) — don't add column-name keyword
  matching or content-sampling heuristics; that reintroduces the guess-
  based classification this project deliberately avoids.

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,llm,mcp,ner]"

cd vscode-extension && npm install && cd ..
cd intellij-plugin  # ./gradlew :core:test works without a full IDE download
```

## Before opening a PR

```bash
# Python: tests + dependency scan
source .venv/bin/activate
python -m pytest tests/ -v
pip-audit

# If you touched masker.py, record_masker.py, or query_aware.py, also:
python -m pytest tests/test_benchmark_suite.py -v
# (a pre-commit hook runs this automatically for you if installed --
#  see scripts/install-git-hooks.sh)

# VS Code extension changes
cd vscode-extension
npm run compile && npm run lint && npm test && npm audit

# IntelliJ plugin `core/` module changes (pure Kotlin, always testable)
cd intellij-plugin && ./gradlew :core:test
# `plugin/` module changes: write by hand against IntelliJ Platform SDK
# conventions, mark any judgment call inline (see existing files for the
# "UNVERIFIED" comment style), and note in your PR description that it's
# unverified until someone with a real IntelliJ instance confirms it via
# intellij-plugin/MANUAL_TEST.md.
```

## Testing discipline

- New detection logic needs a test with a **realistic, multi-part input**
  (a Phase 1 regression once truncated multi-level email domains —
  simple test cases alone didn't catch it).
- A new masking decision path needs both: a unit test for the logic
  itself, and — if it's reachable through `mask_records`/`ask_over_data`
  — an audit-log test confirming no real/fake value leaks into the trail.
- If you change `masker.py`, `record_masker.py`, or `query_aware.py`,
  add or update a case in `benchmark_cases.py` so the change is
  continuously checked, not verified once and left to drift.
- Don't accept a sub-agent's or a colleague's report of "tests pass" at
  face value in review — re-run the suite yourself before merging.

## Versioning

This project follows [Semantic Versioning](https://semver.org/) starting
at v0.1.0. Update `CHANGELOG.md` under `[Unreleased]` as part of your PR;
a maintainer moves entries under a version heading at release time. If
your change alters the on-disk vault or audit log format, update
`AUDIT_LOG_FORMAT.md`'s `format_version` and add a migration note.

## License

By contributing, you agree your contribution is licensed under this
project's [Apache 2.0 license](LICENSE).
