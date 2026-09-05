<!-- Title: What this protects against, and what it doesn't -->

# What this protects against, and what it doesn't

This document exists because Decoy makes no absolute claims anywhere — not
here, not in code comments, not in the IDE UI, not in error messages. No
"100% private." No "your data never leaves your machine." Those claims
would be false the moment LLM-assisted relevance classification is
enabled, and even with it off, format-preserving masking has real,
measured limits described below. Read this before deciding what data you
route through Decoy.

Every claim in this document is either something actually measured in
this project's own benchmark run (`BENCHMARK_REPORT.md`), a specific,
named limitation of a specific mechanism, or an exact statement of what
network calls happen and when. Nothing here is aspirational.

## 1. Literal PII vs. re-identification by inference

**What masking does:** the regex layer (`masker.py`) detects and replaces
well-formatted PII (email, phone, SSN, credit card, IP, API keys, a
configurable PNR/booking-ref pattern) with realistic, session-consistent
fakes. `record_masker.py` masks or redacts every database/record column
that isn't safe-by-construction (boolean, low-cardinality categorical,
date, numeric — see below). Literal PII strings are reliably absent from
what's actually sent to an LLM — this is Privacy Score in the Phase 10
benchmark, and it measured **1.000** for both `all_pii_mask` and
`query_aware_mask` across all 36 test cases (`BENCHMARK_REPORT.md`).

**What masking does not guarantee:** hiding a literal string does not
guarantee a person can't be re-identified by *inference* from surrounding
context. This is the SPIA finding (arXiv 2604.21211), and this project
probed it directly rather than assuming: a 10-row employee dataset was
constructed where two individually-common attributes (`title`, `office`)
combine to uniquely identify one row, even though neither field alone is
unique and the row's literal name/email are masked correctly. Full
details: `BENCHMARK_REPORT.md`'s "SPIA-style residual-context probe
finding" section.

**v1 response to this, added before shipping, not deferred:**
`record_masker.py` now checks the joint combination of every
automatically-kept-real (`enum_kept`/`boolean_kept`) column across a
batch of rows; a row whose combination is unique or near-unique gets
those specific columns redacted *for that row only*. This is real,
tested (`tests/test_quasi_identifier_check.py`), and verified against the
actual SPIA probe case. **It is not a general fix, and its scope is
explicit:**

- It only considers columns already classified safe-by-shape
  (`enum_kept`/`boolean_kept`) — it has no opinion on masked/numeric/date
  columns, which are already transformed regardless.
- It checks one joint combination (every currently-kept-real column
  together), not arbitrary configurable subsets — it is not general
  k-anonymity or l-diversity tooling.
- A `never_mask` override on a contributing column removes that column
  from consideration entirely, by design (manual overrides beat
  automated classification, always). A user-forced-real column can still
  combine with others to create a residual risk this check will not
  catch.
- It only reasons about uniqueness **within the batch actually sent** in
  one request. It has no model of a larger population, no access to
  external knowledge a reader might bring to bear, and does not persist
  uniqueness state across separate requests.
- Free text has no equivalent mechanism at all — this check exists in
  `record_masker.py` (structured data) only.

## 2. Automated relevance classification is imperfect

Query-aware fidelity (`query_aware.py`) decides whether a field is
*relevant* to the current question, which changes HOW it's masked
(realistic fake vs. flat redaction) — never WHETHER, per this project's
core design principle. The default classifier is local keyword matching
against a small, editable synonym table; it is expected to have both
false positives and false negatives, by construction, not as a bug to
eventually fix. The measured numbers from `BENCHMARK_REPORT.md`:

| Condition | Privacy Score (measured) |
|---|---|
| `no_mask` | 0.000 |
| `all_pii_mask` | 1.000 |
| `query_aware_mask` | 1.000 |

For comparison, PII-Bench's own published numbers (arXiv 2502.18545, **copied
verbatim from their paper, not independently reproduced by this
project** — different cases, different detectors, no shared judge model):
No Mask P=0.00/U=1.00; All-PII Mask P=1.00/U=0.52; Query-unrelated Mask
P=0.83/U=0.89. `query_aware_mask`'s Privacy Score matching
`all_pii_mask` (both 1.000) is the *intended* result of this project's
design, not a fluke: relevance changes fidelity, never masking coverage,
so a detected PII field is masked the same fraction of the time
regardless of relevance. This is a structurally different guarantee than
PII-Bench's `Query-unrelated Mask` condition, which is why the numbers
aren't directly comparable — see `BENCHMARK_REPORT.md`'s explicit
comparison caveat.

**Utility Score and Balanced Score are not measured as of this writing** —
they require a real LLM-as-judge call, and the account configured for
this run had insufficient API credits. `BENCHMARK_REPORT.md` states this
precisely (a real client was configured and a real call was attempted and
failed on billing, not silently skipped) and will be updated with actual
numbers once a working judge call is available.

An LLM-assisted relevance classification mode also exists
(`query_aware.classify_relevant_columns(client=...)`) as a strictly
opt-in alternative to keyword matching — see the Network Calls section
below for exactly what it sends.

## 3. Free-text NER: opt-in, not on by default

`masker.py`'s pluggable NER interface (`ner.py`) has a real, working
Presidio-backed implementation (`PresidioNERBackend`), but
`NoOpNERBackend` — a placeholder that detects nothing — remains the
DEFAULT. **A bare person name or street address typed directly into a
prompt is NOT masked unless you explicitly opt in** with
`DECOY_USE_PRESIDIO=true` (plus `pip install "decoy[ner]"` and a
downloaded spaCy model — see README.md). Verified directly:
`mask_text("Please reach out to Sarah about the invoice.")` returns
`detections=[]` by default, and correctly detects `"Sarah"` as `PERSON`
once Presidio is enabled. It stays opt-in rather than on-by-default for
two concrete reasons, not just caution: (1) it's a genuinely heavy
dependency chain (spaCy plus a ~400MB language model, confirmed by
actually installing it), not something every `pip install decoy` user
should be forced to pull in; (2) a fresh `PresidioNERBackend` takes
~13 real seconds to construct (it loads that model) — Decoy's own
`Masker` is built fresh per request throughout this codebase, so this
is cached process-wide (`ner.get_default_ner_backend()`), but the
underlying cost is real and worth knowing about before enabling it on a
latency-sensitive path.

This is why the benchmark suite's free-text test cases don't plant a
bare name as PII in a DEFAULT-condition question — doing so would report
a false "leak" for a documented, opt-in gap, not a regression. Database/
record values don't have this gap: `record_masker.py`'s deny-by-default
rule masks every non-shape-safe column by content, name detector or not.

**PNR/booking-code detection: two fixes, one of them a deliberate
false-positive-tolerant tradeoff.** `masker.py`'s PNR regex was
confirmed case-sensitive-only (`mask_text("my pnr fghty6")` returned
`detections=[]` before this was fixed) and is now case-insensitive.
Separately, a NEW context-aware detector
(`PNR_CONTEXT_KEYWORD_RE`/`Masker._context_detections`) masks an
alphanumeric code that does NOT fit the strict PNR shape (too short, or
missing a digit) whenever it sits next to an explicit keyword — "PNR",
"booking reference", "confirmation number" — naming it as one.
This is intentionally loose: it will occasionally mask a word that
happens to follow one of those keywords but isn't actually a code, in
exchange for not missing a real one. The false-positive surface is
bounded to text that already talks about a booking/confirmation code
(gated on keyword proximity, not a blanket scan of every alphanumeric
word), and this tradeoff is a deliberate choice, not an accidental
loosening of the regex layer's normal precision — see
`tests/test_masker_phase1.py`'s
`test_context_aware_pnr_detection_does_not_fire_without_a_keyword` for
the boundary this is held to.

## 4. Manual overrides: the correction mechanism, not a footnote

Both of the limitations above have the same fix: `overrides.py`'s
`always_mask`/`never_mask` lists, checked before any automated detection
or classification, anywhere in the system, and always winning. If
automated classification gets something wrong in either direction —
under-masks a field it should have caught, or over-masks a field that's
actually safe — an override is how you correct it, immediately, without
waiting for a smarter detector. This is a deliberate design decision, not
a stopgap: automated PII/relevance detection is a known-imperfect,
actively-researched problem (Sections 1–2 above are this project's own
evidence of that), and a human-set override is the only mechanism in this
system with zero false-positive/false-negative rate by construction.

## 5. Exact network calls — no vague language

Decoy makes network calls only in these specific, named situations. If
none of these are true for your usage, no data leaves your machine
through Decoy's own code.

1. **LLM-assisted relevance classification** — only if you explicitly
   pass a `client` to `query_aware.classify_relevant_columns()` (or
   `unified_pipeline.ask_over_data()`'s `relevance_client` parameter).
   Sends: the question, **masked first** via `masker.py` before it's
   included in the prompt, plus column **names only**, never row values.
   Goes to: Anthropic's API, at the model you configure. Off by default.

2. **The actual LLM call to answer a question** —
   `unified_pipeline.ask_over_data()` with `provider="anthropic"`
   requires you to pass a `client` explicitly; Decoy never constructs
   one or reads an API key on your behalf. Sends: the masked question and
   masked/redacted records (never real values, per Sections 1–2's
   coverage). Goes to: Anthropic's API, at the model you configure.

3. **The MCP proxy forwarding to its target server** — the `decoy-proxy`
   MCP server forwards every `call_tool` request to whatever real target
   MCP server it's configured to sit in front of, and returns that
   target's masked result. **The target is not assumed to be local** —
   this project's own top-level design names Snowflake (a cloud-only
   data warehouse with no local/on-prem deployment mode at all) and
   Elasticsearch (commonly a remote/hosted cluster) as expected targets
   reachable via MCP, so "the target might be remote, over the network,
   outside your control" is an intended case, not an edge case.

   **Outgoing tool call arguments are reversed back to their real values
   before forwarding, when — and only when — the argument matches a fake
   value already established in this session's shared vault.** The fix
   here is *unmasking*, not masking: masking an outgoing argument would
   break the target's own real lookup (a target running, say, `WHERE
   email = ?` needs the REAL email to find a row — no fake value can
   match one, so "mask the argument instead" was considered and rejected
   as not viable in the general case, not merely undone). Because every
   masking path in this project — free-text prompts, DB records, and this
   proxy's own results — shares one vault per session, an argument value
   the LLM currently holds is, if it originated from something Decoy
   already masked earlier in this same session, the *fake* value from
   that vault. Reversing it (vault lookup: fake → real) immediately
   before the outgoing call recovers what the target needs without the
   real value ever having reached the LLM. The result coming back is
   still masked as before.

   **Residual limitation, stated precisely, not implied fixed:** this
   only recovers a value that was *already masked earlier in this exact
   session* — free text, a DB record, or a prior proxy call sharing the
   same `session_id`/vault. See
   `tests/test_mcp_proxy_phase6.py`'s
   `test_outgoing_argument_matching_a_previously_masked_value_is_unmasked_before_forwarding`
   and `test_outgoing_argument_never_previously_masked_passes_through_unchanged`
   for both cases verified directly.

   **The single most likely way a value bypasses this entirely — named
   specifically, not left as an abstract "any value Decoy never saw":**
   MCP is a *tool-provider* protocol. When a user types a prompt directly
   into an MCP host application (Claude Code, or any other MCP client),
   that host calls its own LLM backend directly with the raw,
   unmasked prompt — `decoy-proxy` is registered as an available toolset
   for that call, but the conversation itself never passes through the
   MCP server. The host's own LLM reads the unmasked prompt, decides on
   its own to invoke a tool, and constructs the arguments from what it
   read — all of this happens entirely inside the host, before
   `decoy-proxy` is contacted at all. `decoy-proxy` only receives a
   `tools/call` request *after* that decision and argument construction
   are already done — confirmed directly against this project's own
   proxy implementation, which registers exactly two MCP handlers
   (`on_list_tools`, `on_call_tool`) and no others; there is no protocol
   message that would carry the host's raw conversational prompt to an
   MCP server to intercept.

   Concretely: if you use `decoy-proxy` exactly as Phase 6 describes it
   — "an MCP server sitting between Claude Code and a real target MCP
   server," with no other Decoy integration — and you type "look up
   jane.doe@acme.com" directly into that host's chat, that email has
   never touched Decoy's vault by the time it becomes a tool-call
   argument. It reaches `decoy-proxy` already unmasked, gets forwarded
   unchanged (there's nothing in the vault to reverse it from), and
   reaches the target in the clear. **This is not a bug to fix in
   `decoy-proxy` — it is architecturally outside what any MCP server can
   see**, in the same way no tool provider can see the conversation that
   led to it being called. Closing this gap requires the *host
   application itself* to route the user's prompt through Decoy's own
   masking (`masker.mask_text()`, or `unified_pipeline.ask_over_data()`)
   before constructing tool calls — an integration choice for whoever
   controls the host, not something `decoy-proxy` can do from its
   position in the pipeline.

3a. **`decoy-chat-proxy` forwarding to the real LLM API** — this is the
   fix for the gap named directly above in item 3: a chat-completions
   proxy (`decoy.chat_proxy`, run via `decoy chat-proxy`) sits in front
   of the real Anthropic/OpenAI API itself, not in front of an MCP tool
   provider, so it sees the actual conversational prompt before any host
   application's own LLM call — the thing item 3 states plainly that
   `decoy-proxy` architecturally cannot see. Point `ANTHROPIC_BASE_URL`
   (or `OPENAI_BASE_URL`) at it instead of the real provider. Sends: the
   masked request body (system + all message text, masked via the same
   `masker.py` layer as everything else). Goes to: the real
   Anthropic/OpenAI API, using an API key read from the PROXY's own
   environment (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`) — never accepted
   from or logged from the client request. The response is unmasked
   (fake → real) before being returned to the client, including
   streaming responses (see `decoy.chat_proxy`'s `StreamUnmaskBuffer` for
   how a fake value split across two streamed chunks is still correctly
   reassembled before being unmasked, rather than risking either half
   leaking through un-reversed).

   **This still requires the host application to actually be configured
   to send its traffic through this proxy** (`ANTHROPIC_BASE_URL`/
   `OPENAI_BASE_URL` set correctly) — a host that talks to the real
   provider directly, or via some other configured base URL, still
   bypasses Decoy entirely, for the same fundamental reason item 3
   describes: Decoy can only mask traffic that is actually routed through
   it.

   **Vault sharing with `decoy-proxy` (item 3) is opt-in and manual, not
   automatic** — they are separate OS processes (this proxy is a
   long-running daemon; `decoy-proxy` is a stdio subprocess Claude Code
   itself spawns), so they only share one vault if you set
   `DECOY_VAULT_PERSIST=true` on both AND coordinate the same session id
   between them. See the README's "Chat proxy" section for the exact
   mechanism and `decoy.vault`'s module docstring for two real
   cross-process data-loss bugs (a lost-update save race, and a race that
   could mint two different fakes for the same real value) that had to be
   found and fixed before this sharing was actually safe — both are now
   covered by real multi-process regression tests
   (`tests/test_vault_cross_process.py`, `tests/test_audit_log_cross_process.py`),
   not just unit tests of the merge logic in isolation.

4. **Benchmark suite judge calls** (`benchmark.py`'s `judge_call`) — only
   when you supply a real judge function wired to a real LLM client, used
   solely to compute Utility Score for the benchmark report. Never runs
   as part of normal masking; it's a development/CI tool.

**No other network calls exist in this codebase.** The vault, audit log,
and override store are local files only (`.decoy/`), encrypted at rest
where noted in `AUDIT_LOG_FORMAT.md`. Neither IDE extension calls out to
anything beyond reading/writing those same local files. `decoy-chat-proxy`
(item 3a) has no authentication of its own on the local port it listens
on — it is meant for localhost use and trusts any local process that can
reach it, the same trust model as any other local dev proxy; this is a
stated design boundary, not an oversight.

## 5a. Standalone `decoy-proxy` binary: a packaging change, not a masking change

`decoy-proxy` — the same MCP proxy described in §5.3, with the same
network-call behavior described there — is also distributed as a
prebuilt, standalone binary attached to tagged GitHub Releases, as an
alternative to `pip install decoy`. The binary bundles a Python
interpreter and this project's pinned dependencies inside it so it runs
without a separately installed Python, pip, or venv.

**What this does not change:** none of it. The binary runs the exact
same `decoy-proxy` code described in §5.3 — same masking logic, same
vault, same unmasking-of-matched-outgoing-arguments behavior, same
residual limitations, same "conversation never passes through the MCP
server" gap. Packaging the interpreter alongside the code changes how
you obtain and run the process; it sends nothing anywhere that the
`pip install`-run process didn't already send, and closes none of the
gaps named in §5.3.

**What this adds, named plainly:** a supply-chain risk that doesn't
exist when you install from source or from a package index with its own
signing/provenance chain. A downloaded binary is an opaque artifact —
running one you haven't verified means trusting that the bytes you
received are actually what the release process built, not something
substituted in transit or by a compromised mirror. Mitigate this by
verifying the published SHA-256 checksum for your platform's binary
against the one on the release page *before* running it, every time you
download a new version. This document does not claim checksum
verification eliminates the risk (a compromised release pipeline could
publish a matching checksum for a compromised binary) — only that
skipping verification accepts a risk that verification meaningfully
reduces.

**Bundled into both IDE extensions:** the VS Code extension and the
IntelliJ plugin now ship these same platform binaries directly and can
write the `.mcp.json` entry pointing at the bundled copy for you (an
explicit, confirmed action — it never runs without you triggering it and
reviewing the exact entry first). This sidesteps the download/checksum
step above entirely, since the binary arrives as part of the
already-installed, already-trusted extension/plugin rather than a
separate download; it does not change any of the masking behavior
described above or in §5.3.

## 6. Clearing data: what "Clear Session Data Only" and "Clear All" actually do

Both IDE extensions expose two distinct destructive actions, deliberately
not one:

- **"Clear Session Data Only"** removes the audit trail and vault
  mappings for every session, but **keeps** your configured
  `always_mask`/`never_mask` overrides.
- **"Clear All Local Data"** removes everything, including your
  configured overrides — no unstated exception.

This split exists because a configured override can itself be sensitive
(a `never_mask` entry naming a specific real identifier, an `always_mask`
pattern that reveals what kind of data you're worried about) — leaving
overrides untouched under a button literally labeled "Clear All" would
contradict what the button says it does. If you want to reset detection
history without losing your configured rules, use "Clear Session Data
Only"; if you want nothing left behind, "Clear All" means all.
