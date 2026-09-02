# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project follows [Semantic Versioning](https://semver.org/) from v0.1.0.

## [Unreleased]

Everything below was built across Phases 1–11. Not yet tagged — see
`RELEASE_CHECKLIST.md` for what must be true before `v0.1.0` is tagged.

### Added

- **Core masking engine** (`masker.py`, `vault.py`, `overrides.py`):
  regex-based free-text PII detection (email, phone, SSN, credit card,
  IP, API keys, configurable PNR pattern), session-scoped fake-value
  vault with collision avoidance, `always_mask`/`never_mask` manual
  overrides checked before any automated detection.
- **Structured/DB masking** (`record_masker.py`): deny-by-default,
  shape-only column classification (boolean/low-cardinality-enum kept
  real; date/numeric transformed preserving structure; everything else
  masked). Dialect-agnostic via SQLAlchemy `inspect()`.
- **Quasi-identifier redaction** (`record_masker.py`,
  `_find_quasi_identifier_risk`): v1 mitigation for the SPIA-style
  residual-context finding — a unique/near-unique combination of
  kept-real columns gets those columns redacted for that row only.
  On by default; a `never_mask` override on a contributing column is the
  intended escape hatch. See `WHAT_THIS_PROTECTS_AGAINST.md` §1 for the
  documented scope limits.
- **Query-aware relevance classification** (`query_aware.py`):
  local keyword matching by default; optional LLM-assisted mode
  (explicitly opt-in, masks the question before sending it, never sends
  row values). Manual overrides always take precedence.
- **Unified pipeline** (`unified_pipeline.py`): masks a question and
  fetched records under one shared session/vault, so the same real value
  gets the same fake everywhere. Row-scoped consistency fix: a value
  directly quoted in the question is treated as relevant for that
  specific row only, not the whole column.
- **Edge-case-aware sampling** (`edge_case_sampler.py`): masked samples
  deliberately including nulls, numeric outliers, duplicate IDs, and
  empty strings, dialect-aware random fill with a Python-side fallback.
- **MCP masking proxy** (`mcp_proxy.py`, `decoy-proxy`): dynamic tool
  discovery and forwarding to a real target MCP server, masking the
  result before returning it. Configurable timeouts on both `call_tool`
  and discovery, so a hung target fails cleanly instead of hanging the
  caller. Outgoing tool call *arguments* are reversed back to their real
  values (vault lookup) before forwarding, whenever the argument matches
  a value already masked earlier in the same session — the target is not
  assumed to be local (Snowflake/Elasticsearch are named examples in this
  project's own spec, and neither is local-only). Masking the argument
  instead was considered and rejected: it would break a target's own real
  lookup, which can't match a fake value. **This narrows, but does not
  close, the outgoing-argument gap**: it only helps when the same real
  value was independently masked through a Decoy-controlled channel
  earlier in the same session. The single most likely real-world path —
  a value typed directly into an MCP host's chat (Claude Code or any
  other MCP client) and used to construct a tool call — is
  architecturally invisible to any MCP server, `decoy-proxy` included:
  the host calls its own LLM directly with the raw prompt and only
  contacts the MCP server once a tool call has already been decided and
  built. Closing that specific gap requires the host application itself
  to route the prompt through Decoy's masking first; `decoy-proxy` has no
  hook into that step from its position in the pipeline. See
  `WHAT_THIS_PROTECTS_AGAINST.md` §5's item 3 for the full reasoning.
- **Local encrypted audit trail** (`audit_log.py`, `crypto.py`):
  every masking decision logged with reason and detection layer, never
  real/fake values, encrypted at rest (Fernet), shared on-disk format
  documented in `AUDIT_LOG_FORMAT.md` for cross-language (Python/
  TypeScript/Kotlin) consumption.
- **VS Code extension** (`vscode-extension/`): masking trace panel,
  override editor (add/remove/edit-in-place, field names and patterns),
  "Clear Session Data Only" vs "Clear All Local Data" (the latter also
  clears overrides — no unstated exception), first-run privacy notice,
  status bar indicator.
- **IntelliJ plugin** (`intellij-plugin/`): equivalent functionality to
  the VS Code extension, reading/writing the same local audit log and
  override files. `core/` module is pure Kotlin with no IntelliJ SDK
  dependency and is fully tested; `plugin/` module (IntelliJ Platform SDK
  wiring) is unverified — never compiled in the authoring environment.
- **Benchmark suite** (`benchmark.py`, `benchmark_cases.py`,
  `tests/test_benchmark_suite.py`): PII-Bench-methodology Privacy/
  Utility/Balanced scoring across No Mask / All-PII Mask / Query-Aware
  Mask conditions, a hard pass/fail test for manual overrides, and the
  SPIA-style residual-context probe. Wired to a local pre-commit hook
  (`.pre-commit-config.yaml`, `scripts/install-git-hooks.sh`) that runs
  automatically on changes to `masker.py`/`record_masker.py`/
  `query_aware.py`. See `BENCHMARK_REPORT.md` for the actual measured
  results as of this writing.
- **`decoy` CLI** (`cli.py`): headless audit-log/override inspection and
  clearing, for use without either IDE open.

### Fixed along the way (kept here since each was a real bug, not cosmetic)

- Email regex truncating multi-level domains (Phase 1).
- LLM-assisted relevance classification sending the raw, unmasked
  question to the classifier (Phase 3).
- Query-aware redaction upgrading fidelity for an entire column instead
  of the one row whose value was actually quoted in the question
  (Phase 4).
- `always_mask`-overridden fields being silently redacted by query-aware
  relevance classification instead of getting their normal realistic-fake
  treatment (Phase 7).
- No timeout on the MCP proxy's forwarded `call_tool`/`list_tools` calls
  — a hung target could hang the caller indefinitely (Phase 6 follow-up).
- "Clear All Local Data" not actually clearing overrides, contradicting
  its own label (Phase 8 follow-up, applied to both IDEs).
- A stale/retired hardcoded default model ID
  (`claude-3-5-haiku-20241022`) in `query_aware.py` and
  `unified_pipeline.py`, updated to the current model ID.
- A live API key committed to `.env.example` (meant to hold placeholders
  only) instead of gitignored `.env`, corrected before any commit was
  made.
- A declared-but-missing `decoy` CLI console-script entry point in
  `pyproject.toml` (Phase 11).

### Known limitations (see `WHAT_THIS_PROTECTS_AGAINST.md` for full detail)

- No NER backend is actually wired for free text — bare names/addresses
  in a prompt are not masked by default.
- Quasi-identifier redaction only covers automatically-kept-real
  (enum/boolean) DB columns; it has no free-text equivalent and is not
  general k-anonymity tooling.
- Utility Score / Balanced Score are not yet measured with a real LLM
  judge (attempted; blocked on account API credits at time of writing).
- The MCP proxy's outgoing-argument fix only reverses a value already
  masked earlier in the same session. **The most likely real-world way
  this doesn't apply: a value typed directly into an MCP host's chat
  (Claude Code or any other MCP client) and used by the host to construct
  a tool call, without that prompt ever being routed through Decoy's own
  masking first.** This is architecturally outside what `decoy-proxy` (or
  any MCP server) can see — the host calls its own LLM directly with the
  raw prompt and only contacts the MCP server once a tool call has
  already been built. Closing it requires the host application itself to
  mask the prompt before constructing tool calls.
