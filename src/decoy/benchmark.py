"""Phase 10 benchmark suite: privacy vs utility tradeoff, PII-Bench
methodology (arXiv 2502.18545).

Three conditions are compared:
  - "no_mask": raw question + raw records sent as-is. The deliberate
    zero-privacy baseline PII-Bench itself compares against -- not a bug,
    a reference point.
  - "all_pii_mask": everything masker.py/record_masker.py would mask,
    masked -- but query_aware.py's relevance classification is bypassed
    entirely (relevance=None), so every masked-by-default field gets a
    realistic fake regardless of whether it's relevant to the question.
    This matches PII-Bench's "All-PII Mask" condition.
  - "query_aware_mask": this project's real approach, exercised through
    the actual unified_pipeline.ask_over_data() end-to-end -- query-aware
    fidelity varies HOW a field is masked (realistic fake vs. flat
    redaction), never WHETHER.

Privacy Score is 100% mechanical: the proportion of a case's planted
ground-truth PII strings that are ABSENT (verbatim) from what was
actually sent to the LLM. No model call needed, no judgment involved --
this is checked by direct substring search against the masked
question/records, the same way a Phase 1-9 test would assert a value
"not in" the masked output.

Utility Score requires an LLM judge and is measured ONLY if a
`judge_call: Callable[[str], str]` is supplied by the caller (mirroring
unified_pipeline.ask_over_data's `llm_call` injection pattern). Without
one, `utility_score` is None and `utility_note` states plainly that it
was not measured -- never a fabricated or estimated number. Balanced
Score is only computed when both halves are present.
"""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .benchmark_cases import BenchmarkCase
from .masker import Masker
from .overrides import OverrideStore, get_default_store
from .record_masker import mask_records
from .unified_pipeline import ask_over_data
from .vault import VaultManager

CONDITIONS = ("no_mask", "all_pii_mask", "query_aware_mask")

UTILITY_NOT_MEASURED = "not measured -- requires ANTHROPIC_API_KEY (or an injected judge_call)"

_JUDGE_PROMPT_TEMPLATE = (
    "You are evaluating the quality of an AI assistant's answer to a question, "
    "given the ORIGINAL unmasked context (the assistant may not have seen this "
    "directly if privacy masking was applied before its own turn).\n\n"
    "Original unmasked context:\n"
    "Question: {question}\n"
    "Records: {records}\n\n"
    "Assistant's answer: {answer}\n\n"
    "On a scale from 0.0 (useless, wrong, or evasive) to 1.0 (fully correct and "
    "helpful, as if the assistant had the real unmasked data), how well does the "
    "assistant's answer address the question? Respond with ONLY a single number "
    "between 0.0 and 1.0, nothing else."
)

_NUMBER_RE = re.compile(r"(\d*\.\d+|\d+)")


@dataclass
class BenchmarkResult:
    case_id: str
    condition: str
    privacy_score: float
    leaked_pii: list[str] = field(default_factory=list)
    utility_score: Optional[float] = None
    utility_note: str = UTILITY_NOT_MEASURED
    balanced_score: Optional[float] = None
    answer: str = ""
    sent_question: str = ""
    sent_records: list[dict] = field(default_factory=list)


def _sent_text(question: str, records: list[dict]) -> str:
    """The literal text/JSON that was actually sent -- used both for
    mechanical Privacy Score checking and to reconstruct a judge prompt,
    so all three conditions are scored against an equivalent artifact."""
    return question + "\n" + json.dumps(records, default=str)


def _compute_privacy_score(case: BenchmarkCase, sent_question: str, sent_records: list[dict]) -> tuple[float, list[str]]:
    if not case.planted_pii:
        # No planted ground truth means this case measures something else
        # (e.g. a pure relevance/shape sanity check) -- vacuously perfect
        # rather than penalizing or rewarding a condition for nothing.
        return 1.0, []
    sent = _sent_text(sent_question, sent_records)
    leaked = [value for value in case.planted_pii if value in sent]
    missing_count = len(case.planted_pii) - len(leaked)
    return missing_count / len(case.planted_pii), leaked


def _compute_utility_score(case: BenchmarkCase, answer: str, judge_call: Callable[[str], str]) -> Optional[float]:
    prompt = _JUDGE_PROMPT_TEMPLATE.format(
        question=case.question,
        records=json.dumps(case.records, default=str),
        answer=answer,
    )
    try:
        raw = judge_call(prompt)
    except Exception:  # noqa: BLE001 - a broken judge must not crash the benchmark run
        return None
    if not isinstance(raw, str):
        return None
    match = _NUMBER_RE.search(raw)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    if not (0.0 <= value <= 1.0):
        return None
    return value


def _run_no_mask(case: BenchmarkCase, llm_call: Callable[[str], str]) -> tuple[str, list[dict], str]:
    answer = llm_call(_sent_text(case.question, case.records))
    return case.question, case.records, answer


def _run_all_pii_mask(
    case: BenchmarkCase,
    session_id: str,
    override_store: OverrideStore,
    vault_manager: VaultManager,
    llm_call: Callable[[str], str],
) -> tuple[str, list[dict], str]:
    masker = Masker(session_id=session_id, vault_manager=vault_manager, override_store=override_store)
    masked_question = masker.mask_text(case.question).masked_text
    masked_records, _decisions = mask_records(
        case.records,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        relevance=None,  # bypassed entirely -> every masked-by-default field gets a realistic fake, matching PII-Bench's All-PII Mask
    )
    raw_answer = llm_call(_sent_text(masked_question, masked_records))
    answer = masker.unmask_text(raw_answer)
    return masked_question, masked_records, answer


def _run_query_aware_mask(
    case: BenchmarkCase,
    session_id: str,
    override_store: OverrideStore,
    vault_manager: VaultManager,
    llm_call: Callable[[str], str],
    relevance_client: Optional[Any] = None,
) -> tuple[str, list[dict], str]:
    result = ask_over_data(
        question=case.question,
        fetch_records_fn=lambda: case.records,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        llm_call=llm_call,
        relevance_client=relevance_client,
    )
    return result.masked_question, result.masked_records_sent, result.answer


def run_benchmark_case(
    case: BenchmarkCase,
    condition: str,
    session_id: Optional[str] = None,
    judge_call: Optional[Callable[[str], str]] = None,
    override_store: Optional[OverrideStore] = None,
    vault_manager: Optional[VaultManager] = None,
    llm_call: Optional[Callable[[str], str]] = None,
    relevance_client: Optional[Any] = None,
) -> BenchmarkResult:
    """Run one case under one condition. `llm_call` defaults to a fixed
    stub answer ("Acknowledged.") when omitted -- Privacy Score never
    depends on the actual answer content, only on what was SENT, so a
    stub is sufficient unless the caller also wants a real/judged answer.
    `judge_call`, if given, measures Utility Score for real; omitted, it
    stays None with `utility_note` stating why.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; must be one of {CONDITIONS}")

    session_id = session_id or f"bench-{case.id}-{condition}-{uuid.uuid4()}"
    override_store = override_store or get_default_store()
    vault_manager = vault_manager or VaultManager(persist=False)
    effective_llm_call = llm_call or (lambda _prompt: "Acknowledged.")

    if condition == "no_mask":
        sent_question, sent_records, answer = _run_no_mask(case, effective_llm_call)
    elif condition == "all_pii_mask":
        sent_question, sent_records, answer = _run_all_pii_mask(
            case, session_id, override_store, vault_manager, effective_llm_call
        )
    else:
        sent_question, sent_records, answer = _run_query_aware_mask(
            case, session_id, override_store, vault_manager, effective_llm_call, relevance_client
        )

    privacy_score, leaked = _compute_privacy_score(case, sent_question, sent_records)

    utility_score = None
    utility_note = UTILITY_NOT_MEASURED
    balanced_score = None
    if judge_call is not None:
        utility_score = _compute_utility_score(case, answer, judge_call)
        if utility_score is not None:
            utility_note = "measured in this run via the supplied judge_call"
            balanced_score = (privacy_score + utility_score) / 2
        else:
            utility_note = "judge_call was supplied but returned an unparseable response; not measured"

    return BenchmarkResult(
        case_id=case.id,
        condition=condition,
        privacy_score=privacy_score,
        leaked_pii=leaked,
        utility_score=utility_score,
        utility_note=utility_note,
        balanced_score=balanced_score,
        answer=answer,
        sent_question=sent_question,
        sent_records=sent_records,
    )


def _empty_override_store() -> OverrideStore:
    """A no-overrides-configured store, backed by a real (but empty)
    overrides file in a temp dir -- avoids both a log-warning flood from
    repeatedly constructing OverrideStore against a nonexistent path
    (once per case) and any risk of colliding with a real path on disk."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="decoy-benchmark-overrides-"))
    path = tmp_dir / "overrides.json"
    path.write_text(json.dumps({"always_mask": {}, "never_mask": {}}))
    return OverrideStore(path=path)


def run_suite(
    cases: list[BenchmarkCase],
    condition: str,
    judge_call: Optional[Callable[[str], str]] = None,
    llm_call: Optional[Callable[[str], str]] = None,
    relevance_client: Optional[Any] = None,
    override_store: Optional[OverrideStore] = None,
) -> list[BenchmarkResult]:
    """Run every case under one condition, each in its own fresh
    session/vault so cases never leak fakes into each other. All cases
    share one override_store (no manual overrides configured, unless the
    caller supplies one) -- per-case override behavior is exercised
    directly via run_benchmark_case in the hard pass/fail tests instead."""
    shared_override_store = override_store or _empty_override_store()
    results = []
    for case in cases:
        vault_manager = VaultManager(persist=False)
        results.append(
            run_benchmark_case(
                case,
                condition,
                override_store=shared_override_store,
                vault_manager=vault_manager,
                judge_call=judge_call,
                llm_call=llm_call,
                relevance_client=relevance_client,
            )
        )
    return results


@dataclass
class ConditionSummary:
    condition: str
    n_cases: int
    mean_privacy_score: float
    mean_utility_score: Optional[float]
    mean_balanced_score: Optional[float]
    utility_measured: bool


def summarize(results: list[BenchmarkResult]) -> ConditionSummary:
    if not results:
        raise ValueError("cannot summarize an empty result list")
    condition = results[0].condition
    n = len(results)
    mean_privacy = sum(r.privacy_score for r in results) / n

    utility_values = [r.utility_score for r in results if r.utility_score is not None]
    if utility_values:
        mean_utility = sum(utility_values) / len(utility_values)
        balanced_values = [r.balanced_score for r in results if r.balanced_score is not None]
        mean_balanced = sum(balanced_values) / len(balanced_values) if balanced_values else None
        utility_measured = True
    else:
        mean_utility = None
        mean_balanced = None
        utility_measured = False

    return ConditionSummary(
        condition=condition,
        n_cases=n,
        mean_privacy_score=mean_privacy,
        mean_utility_score=mean_utility,
        mean_balanced_score=mean_balanced,
        utility_measured=utility_measured,
    )


# PII-Bench's own published numbers (arXiv 2502.18545) -- kept here as a
# labeled constant, never blended with this project's measured numbers.
PII_BENCH_PUBLISHED_NUMBERS = {
    "no_mask": {"privacy": 0.00, "utility": 1.00},
    "all_pii_mask": {"privacy": 1.00, "utility": 0.52},
    "query_unrelated_mask": {"privacy": 0.83, "utility": 0.89},  # PII-Bench's closest analog to query_aware_mask
}


def _fmt(value: Optional[float]) -> str:
    return f"{value:.3f}" if value is not None else "not measured"


def generate_report(summaries: dict[str, ConditionSummary]) -> str:
    """Build a plain-text comparison table. This project's numbers are
    labeled "measured in this run"; PII-Bench's are labeled "published in
    arXiv 2502.18545, not independently reproduced here" -- the two are
    never rendered in a way that implies they were computed the same way
    or are directly interchangeable, since PII-Bench's benchmark cases,
    detection methods, and judge model are all different from this run's.
    """
    lines = []
    lines.append("=" * 78)
    lines.append("Decoy Phase 10 benchmark report")
    lines.append("=" * 78)
    lines.append("")
    lines.append("THIS PROJECT -- measured in this run:")
    lines.append(f"{'Condition':<20}{'N':<6}{'Privacy':<10}{'Balanced':<16}Utility")
    for cond in CONDITIONS:
        s = summaries.get(cond)
        if s is None:
            continue
        utility_str = _fmt(s.mean_utility_score) if s.utility_measured else UTILITY_NOT_MEASURED
        lines.append(
            f"{s.condition:<20}{s.n_cases:<6}{_fmt(s.mean_privacy_score):<10}"
            f"{_fmt(s.mean_balanced_score):<16}{utility_str}"
        )
    lines.append("")
    lines.append(
        "PII-Bench (arXiv 2502.18545) -- published numbers, NOT independently "
        "reproduced here (different benchmark cases, detectors, and judge model):"
    )
    lines.append(f"{'Condition':<24}{'Privacy':<10}Utility")
    for cond, vals in PII_BENCH_PUBLISHED_NUMBERS.items():
        lines.append(f"{cond:<24}{vals['privacy']:<10.2f}{vals['utility']:.2f}")
    lines.append("")
    lines.append(
        "Comparison caveat: this project's 'query_aware_mask' condition and "
        "PII-Bench's 'query_unrelated_mask' condition are the closest analogs "
        "between the two suites, but are NOT the same benchmark -- different "
        "planted-PII cases, different detectors, different judge model (if any). "
        "Any similarity or difference in the numbers above is suggestive, not "
        "a controlled comparison."
    )
    lines.append("=" * 78)
    return "\n".join(lines)
