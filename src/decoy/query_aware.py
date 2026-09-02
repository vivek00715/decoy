"""Query-aware relevance classification: decide which columns matter to
the current question, so record_masker/unified_pipeline can vary HOW a
field is masked (realistic consistent fake vs. flat redaction) -- never
WHETHER it's masked. A column judged irrelevant is still masked if it
isn't safe-by-shape (Phase 2); it's just masked more bluntly.

Two modes:
  - keyword (default): fully local, no network call. Matches question
    text and column names against a small synonym table.
  - LLM-assisted (opt-in only): pass an Anthropic client explicitly.
    Sends ONLY column names and the question text to the API -- never
    actual row values. Never enabled unless the caller passes a client.

On any failure or ambiguity, this module fails SAFE: an unmapped column
or a failed LLM call is treated as NOT relevant, which drives stronger
redaction downstream, never weaker. Manual overrides from overrides.py
always take precedence over whatever this module decides.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from .logging_config import get_logger
from .masker import Masker
from .overrides import OverrideStore, get_default_store
from .vault import VaultManager, get_default_manager

logger = get_logger("query_aware")

DEFAULT_LLM_MODEL = "claude-haiku-4-5"

# Synonym groups used for local keyword matching. Deliberately small and
# editable -- this is a heuristic, not a classifier, and is expected to
# miss things; overrides.py is the correction mechanism for that, not this
# list growing without bound.
_CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "salary": {"salary", "salaries", "pay", "payroll", "compensation", "wage", "wages", "income", "earnings"},
    "department": {"department", "departments", "dept", "team", "teams", "division", "org", "organization"},
    "name": {"name", "names", "fullname"},
    "email": {"email", "emails", "e-mail"},
    "ssn": {"ssn", "socialsecurity", "social security"},
    "phone": {"phone", "phones", "telephone", "mobile", "cell"},
    "address": {"address", "addresses", "location", "residence"},
    "money": {"balance", "balances", "revenue", "amount", "amounts", "price", "prices", "cost", "costs"},
    "status": {"status", "statuses", "state"},
    "date": {"date", "dates", "when", "timeline"},
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    return "".join(_WORD_RE.findall(text.lower()))


@dataclass(frozen=True)
class ColumnRelevance:
    column: str
    relevant: bool
    reason: str
    layer: str  # "override" | "keyword" | "llm" | "keyword-fallback"


def _column_categories(column_name: str) -> set[str]:
    normalized = _normalize(column_name)
    categories = set()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if _normalize(kw) in normalized:
                categories.add(category)
                break
    return categories


def _question_categories(question: str) -> set[str]:
    q_lower = question.lower()
    categories = set()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in q_lower:
                categories.add(category)
                break
    return categories


def _keyword_classify(question: str, column_names: list[str]) -> dict[str, ColumnRelevance]:
    q_categories = _question_categories(question)
    result = {}
    for col in column_names:
        col_categories = _column_categories(col)
        overlap = col_categories & q_categories
        if overlap:
            result[col] = ColumnRelevance(
                col, True,
                f"question mentions {', '.join(sorted(overlap))}; column name matches via keyword",
                "keyword",
            )
        else:
            result[col] = ColumnRelevance(
                col, False,
                "no keyword overlap between question and column name; "
                "treated as not relevant (fail-safe default)",
                "keyword",
            )
    return result


_LLM_SYSTEM_PROMPT = (
    "You decide which database column NAMES (not values -- you are never "
    "given real data) are relevant to answering a user's question. This is "
    "used to decide masking fidelity, not to answer the question itself. "
    "Respond with strict JSON only, no prose: "
    '{"relevant_columns": ["col_a", "col_b"]} '
    "using only names drawn from the provided column list."
)


def _llm_classify(
    question: str, column_names: list[str], client: Any, model: str
) -> Optional[dict[str, ColumnRelevance]]:
    try:
        prompt = (
            f"Question: {question}\n"
            f"Column names: {json.dumps(column_names)}\n"
            "Which of these columns are relevant to answering the question?"
        )
        response = client.messages.create(
            model=model,
            max_tokens=512,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text_parts = [
            block.text for block in getattr(response, "content", []) if getattr(block, "type", None) == "text"
        ]
        raw = "".join(text_parts).strip()
        # tolerate a fenced code block if the model wraps its JSON
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        relevant_set = set(parsed.get("relevant_columns", []))
        # never trust a hallucinated column name outside what we asked about
        relevant_set &= set(column_names)
    except Exception as exc:  # noqa: BLE001 - any failure here must fail safe, not crash
        logger.error(
            "LLM-assisted relevance classification failed (%s); "
            "falling back to local keyword-only classification",
            exc,
        )
        return None

    result = {}
    for col in column_names:
        if col in relevant_set:
            result[col] = ColumnRelevance(
                col, True, "classified relevant to question via LLM-assisted classification", "llm"
            )
        else:
            result[col] = ColumnRelevance(
                col, False, "classified not relevant to question via LLM-assisted classification", "llm"
            )
    return result


def classify_relevant_columns(
    question: str,
    column_names: list[str],
    client: Optional[Any] = None,
    override_store: Optional[OverrideStore] = None,
    model: str = DEFAULT_LLM_MODEL,
    session_id: str = "default",
    vault_manager: Optional[VaultManager] = None,
) -> dict[str, ColumnRelevance]:
    """Classify each column as relevant/not-relevant to `question`.

    client=None (default): keyword-only, fully local, no network call --
    `question` is matched locally and never leaves the process.
    client=<anthropic.Anthropic instance>: opt-in LLM-assisted mode --
    sends only `column_names` and a MASKED version of `question` to the
    API (via masker.py, same shared vault/session as the rest of the
    pipeline), never the raw question text or any row values. On any
    failure in LLM mode, falls back to keyword-only classification for
    that call (still local, still fail-safe) rather than raising.

    Manual overrides always win: a never_mask field is reported as
    relevant/kept-as-is regardless of automated classification (it won't
    be masked at all downstream, so "relevant" here means "shown as real
    either way"). An always_mask field's relevance is left to the
    automated layers, since always_mask governs WHETHER it's masked, not
    HOW -- that's this module's job.
    """
    override_store = override_store or get_default_store()

    if client is not None:
        vault_manager = vault_manager or get_default_manager()
        masker = Masker(session_id=session_id, vault_manager=vault_manager, override_store=override_store)
        masked_question = masker.mask_text(question).masked_text
        llm_result = _llm_classify(masked_question, column_names, client, model)
        if llm_result is not None:
            base = llm_result
        else:
            base = {
                col: ColumnRelevance(
                    col, r.relevant,
                    r.reason + " (LLM-assisted mode was requested but failed; used local keyword fallback)",
                    "keyword-fallback",
                )
                for col, r in _keyword_classify(question, column_names).items()
            }
    else:
        base = _keyword_classify(question, column_names)

    final: dict[str, ColumnRelevance] = {}
    for col in column_names:
        if override_store.is_never_mask_field(col):
            final[col] = ColumnRelevance(
                col, True,
                "manual override: never_mask (kept as real value, overriding automated relevance classification)",
                "override",
            )
        else:
            final[col] = base[col]
    return final
