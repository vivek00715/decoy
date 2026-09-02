"""Unified pipeline: one shared session across BOTH masking paths.

ask_over_data() masks the question (masker.py), fetches and masks DB/MCP
records with query-aware fidelity (record_masker.py + query_aware.py),
sends both to an LLM, and unmasks the response -- all under one
session_id/vault, so a value appearing both in what the user typed and in
retrieved records gets the SAME fake value in both places.

Network calls made by this module, and only when triggered by the caller:
  - The masked prompt (question + masked records) to the configured LLM
    provider, to produce the answer. Real PII values are never included --
    only their fakes/redactions, and the exact table above the schema.
  - If `relevance_client` is passed, a masked version of the question plus
    column NAMES ONLY (never values) to that client for query-aware
    relevance classification (see query_aware.py). Omit `relevance_client`
    to keep that step fully local (keyword-based).
No other network calls are made by this module.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Optional

from .flatten import flatten_record
from .logging_config import get_logger
from .masker import Masker
from .overrides import OverrideStore, get_default_store
from .query_aware import classify_relevant_columns
from .record_masker import ColumnDecision, mask_records
from .vault import VaultManager, get_default_manager

logger = get_logger("unified_pipeline")


class PipelineError(RuntimeError):
    """Raised when the LLM call itself fails. Masking/unmasking already
    happened correctly by the time this can be raised -- this signals the
    answer could not be produced, not a privacy failure."""


@dataclass
class PipelineResult:
    answer: str
    masked_question: str
    masked_records_sent: list[dict]
    column_decisions: list[ColumnDecision] = dc_field(default_factory=list)
    request_id: str = ""


def _default_anthropic_call(prompt: str, model: str, client: Any) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in getattr(response, "content", []) if getattr(block, "type", None) == "text"
    )


MIN_VALUE_MENTION_LEN = 4


def _row_field_overrides_from_question(
    question: str, flat_records: list[dict[str, Any]]
) -> set[tuple[int, str]]:
    """(row_index, field) pairs whose exact value also appears verbatim in
    the raw question.

    This catches a case column-name keyword matching alone cannot: the
    user directly names a specific value (e.g. an email address) in their
    question. Without this, query-aware classification might judge that
    field "not relevant" by column name alone and flatly redact it in the
    fetched record -- which would both under-serve the user (they already
    typed that value themselves) and break the "same value, same fake
    everywhere" guarantee.

    Deliberately scoped to the specific (row, field) pair, not the whole
    column: this is evidence about one row, not about every other row
    that happens to share a field name. Applying it column-wide would let
    one disclosed value quietly raise fidelity for unrelated rows with no
    supporting evidence -- see record_masker.mask_records' `row_overrides`
    docstring for how this is applied downstream. Short values
    (len < MIN_VALUE_MENTION_LEN) are skipped to avoid trivial/coincidental
    matches (e.g. "1", "True").
    """
    overrides = set()
    for row_idx, flat in enumerate(flat_records):
        for field, value in flat.items():
            if value is None:
                continue
            value_str = str(value)
            if len(value_str) < MIN_VALUE_MENTION_LEN:
                continue
            if value_str in question:
                overrides.add((row_idx, field))
    return overrides


def _build_prompt(masked_question: str, masked_records: list[dict]) -> str:
    parts = [masked_question]
    if masked_records:
        parts.append("")
        parts.append(
            "Relevant data (row values have been masked/redacted for privacy; "
            "column names and types are exact):"
        )
        parts.append(json.dumps(masked_records, indent=2, default=str))
    return "\n".join(parts)


def ask_over_data(
    question: str,
    fetch_records_fn: Callable[[], list[dict]],
    session_id: str,
    provider: str = "anthropic",
    model: str = "claude-haiku-4-5",
    client: Optional[Any] = None,
    relevance_client: Optional[Any] = None,
    field_types: Optional[dict[str, str]] = None,
    override_store: Optional[OverrideStore] = None,
    vault_manager: Optional[VaultManager] = None,
    llm_call: Optional[Callable[[str], str]] = None,
    audit_log: Optional[Any] = None,
) -> PipelineResult:
    """Ask a question over both free text and fetched records, one session.

    `fetch_records_fn` is called with no arguments and must return a list
    of (possibly nested) dict records -- e.g. a DB query result or an MCP
    tool response. On failure it's logged and treated as zero records
    (fail toward less data reaching the LLM, not a crash).

    `llm_call`, if given, replaces the real LLM call with
    `(masked_prompt: str) -> str` -- used by tests and for providers other
    than Anthropic. Otherwise `provider="anthropic"` requires `client`
    (an `anthropic.Anthropic` instance) to be passed explicitly; Decoy
    never constructs one itself or reads API keys on your behalf.

    `relevance_client`, if given, opts into LLM-assisted relevance
    classification (see query_aware.py) instead of the local keyword-only
    default -- entirely separate from `client`/`provider`, which answer
    the actual question.

    `audit_log`, if given (an audit_log.AuditLog), logs every masking
    decision from both the question and the fetched records under one
    `request_id` shared across all of them, so a VS Code/IntelliJ trace
    panel can group them as one request. Off by default -- no disk I/O
    unless explicitly opted into.
    """
    override_store = override_store or get_default_store()
    vault_manager = vault_manager or get_default_manager()
    masker = Masker(session_id=session_id, vault_manager=vault_manager, override_store=override_store)
    request_id = str(uuid.uuid4())

    masked_question = masker.mask_text(question, audit_log=audit_log, request_id=request_id).masked_text

    try:
        records = fetch_records_fn()
    except Exception as exc:  # noqa: BLE001 - never let a fetch failure crash the pipeline
        logger.error(
            "fetch_records_fn failed (%s); proceeding with zero records rather than crashing",
            exc,
        )
        records = []

    flat_records = [flatten_record(record) for record in records]
    column_names: list[str] = []
    seen = set()
    for flat in flat_records:
        for key in flat.keys():
            if key not in seen:
                seen.add(key)
                column_names.append(key)

    relevance = None
    row_overrides = None
    if column_names:
        relevance = classify_relevant_columns(
            question,
            column_names,
            client=relevance_client,
            override_store=override_store,
            session_id=session_id,
            vault_manager=vault_manager,
        )
        row_overrides = _row_field_overrides_from_question(question, flat_records)

    masked_records, decisions = mask_records(
        records,
        field_types=field_types,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        relevance=relevance,
        row_overrides=row_overrides,
        audit_log=audit_log,
        request_id=request_id,
        audit_source="db_record",
    )

    prompt = _build_prompt(masked_question, masked_records)

    try:
        if llm_call is not None:
            raw_answer = llm_call(prompt)
        elif provider == "anthropic":
            if client is None:
                raise PipelineError(
                    "provider='anthropic' requires a `client` (anthropic.Anthropic instance) "
                    "to be passed explicitly -- Decoy does not construct one for you"
                )
            raw_answer = _default_anthropic_call(prompt, model, client)
        else:
            raise PipelineError(f"unsupported provider: {provider!r}")
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as a clear pipeline error, never raw
        logger.error("LLM call failed (%s)", exc)
        raise PipelineError(f"LLM call failed: {exc}") from exc

    answer = masker.unmask_text(raw_answer)

    return PipelineResult(
        answer=answer,
        masked_question=masked_question,
        masked_records_sent=masked_records,
        column_decisions=decisions,
        request_id=request_id,
    )
