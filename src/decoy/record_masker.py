"""DB-agnostic structured data masking: deny-by-default, resolved by
column SHAPE alone, never by name or content-sampling.

Design (see project spec): every column is masked unless it is safe by
construction:
  - boolean columns             -> kept as real values
  - low-cardinality categorical -> kept as real labels (ambiguous cardinality
                                    falls through to the masked default,
                                    never the other way)
  - date/timestamp columns      -> shifted by a consistent per-session offset
  - numeric columns             -> order-and-spread-preserving affine transform
  - everything else             -> masked via consistent fake substitution
                                    (same shared vault as free-text masking,
                                    so a value seen in both places gets the
                                    same fake either way)

There is deliberately no column-name keyword list and no "does this look
sensitive" classifier here: those are guesses that can be wrong in either
direction, and under-masking a sensitive column by guessing wrong is a
silent leak while over-masking a safe one is a minor, correctable
annoyance (via the never_mask override). The two failure modes are not
treated as equally bad.

Works identically whether records come from a live SQLAlchemy-inspected
table (mask_table) or an arbitrary list of dicts with no schema at all,
e.g. an Elasticsearch `_source` payload reached via MCP (mask_records) --
same function, same rules, dialect/source-agnostic.

One cross-cutting layer sits on top of the per-column rules above:
quasi-identifier redaction (see `_find_quasi_identifier_risk` and the
`quasi_identifier_check` parameter below). Two individually-safe
kept-real columns (e.g. a low-cardinality title and a low-cardinality
office) can jointly single out one row even when no individual field's
masking decision was wrong -- this is the SPIA-style residual-context
finding from the Phase 10 benchmark (arXiv 2604.21211), and this is the
project's v1 mitigation for it: on by default, row-scoped (only the
identifying row's contributing columns are redacted, not the whole
column), and an existing never_mask override on a contributing column is
still the correction mechanism if this flags something a user has
decided isn't actually a risk.
"""

from __future__ import annotations

import datetime
import random
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    Table,
    Time,
    select,
)
from sqlalchemy.engine import Engine

from .flatten import flatten_record, unflatten_record
from .logging_config import get_logger
from .masker import Masker
from .overrides import OverrideStore, get_default_store
from .vault import VaultManager, get_default_manager

logger = get_logger("record_masker")

LOW_CARD_MAX_DISTINCT = 10
LOW_CARD_MAX_RATIO = 0.2
LOW_CARD_MIN_ROWS = 5

CATEGORY_BOOLEAN = "boolean"
CATEGORY_DATE = "date"
CATEGORY_NUMERIC = "numeric"
CATEGORY_TEXT = "text"

TREATMENT_BOOLEAN_KEPT = "boolean_kept"
TREATMENT_ENUM_KEPT = "enum_kept"
TREATMENT_DATE_SHIFTED = "date_shifted"
TREATMENT_NUMERIC_AFFINE = "numeric_affine"
TREATMENT_DEFAULT_MASKED = "default_masked"
TREATMENT_REDACTED = "redacted"
TREATMENT_OVERRIDE_NEVER_MASK = "override_never_mask"
TREATMENT_OVERRIDE_ALWAYS_MASK = "override_always_mask"

REDACTED_VALUE = "[REDACTED]"

DEFAULT_QUASI_IDENTIFIER_MAX_GROUP_SIZE = 1
MIN_QUASI_IDENTIFIER_CANDIDATE_FIELDS = 2
MIN_QUASI_IDENTIFIER_ROWS = 2

# Maps each treatment to one of the three audit-log decision categories
# (audit_log.py's VALID_DECISIONS) -- see AUDIT_LOG_FORMAT.md.
TREATMENT_TO_AUDIT_DECISION = {
    TREATMENT_BOOLEAN_KEPT: "left_as_is",
    TREATMENT_ENUM_KEPT: "left_as_is",
    TREATMENT_OVERRIDE_NEVER_MASK: "left_as_is",
    TREATMENT_DATE_SHIFTED: "masked",
    TREATMENT_NUMERIC_AFFINE: "masked",
    TREATMENT_DEFAULT_MASKED: "masked",
    TREATMENT_OVERRIDE_ALWAYS_MASK: "masked",
    TREATMENT_REDACTED: "redacted",
}


@dataclass(frozen=True)
class ColumnDecision:
    field: str
    treatment: str
    reason: str
    layer: str


def _sa_type_category(sa_type: Any) -> str:
    if isinstance(sa_type, Boolean):
        return CATEGORY_BOOLEAN
    if isinstance(sa_type, (Date, DateTime, Time)):
        return CATEGORY_DATE
    if isinstance(sa_type, (Integer, Numeric, Float)):
        return CATEGORY_NUMERIC
    return CATEGORY_TEXT


def _infer_category(values: list) -> str:
    """Best-effort category from Python value types when no explicit
    field_types/schema is available (e.g. raw MCP/Elasticsearch records).
    Bias toward TEXT (the masked-by-default category) on any ambiguity.
    """
    non_null = [v for v in values if v is not None]
    if not non_null:
        return CATEGORY_TEXT
    if all(isinstance(v, bool) for v in non_null):
        return CATEGORY_BOOLEAN
    if all(isinstance(v, (datetime.date, datetime.datetime)) for v in non_null):
        return CATEGORY_DATE
    # bool is a subclass of int -- already excluded above by the boolean check
    # only when *all* values are bool; a mixed bool/int column falls through
    # to text, which is the safe direction.
    if all(
        isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
        for v in non_null
    ):
        return CATEGORY_NUMERIC
    return CATEGORY_TEXT


def _numeric_transform_params(session_id: str, field: str) -> tuple[float, float]:
    """Deterministic per (session, field) affine params -- derived from a
    seeded RNG rather than stored, so repeated calls in the same session
    are consistent with no shared mutable state to guard."""
    rng = random.Random(f"{session_id}:{field}:numeric")
    scale = rng.uniform(0.7, 1.5)
    offset = rng.uniform(-500, 500)
    return scale, offset


def _date_offset_days(session_id: str, field: str) -> int:
    rng = random.Random(f"{session_id}:{field}:date")
    magnitude = rng.randint(30, 1000)
    sign = rng.choice([-1, 1])
    return sign * magnitude


def _apply_numeric_affine(value: Any, scale: float, offset: float) -> Optional[Any]:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, Decimal)):
        return None
    result = float(value) * scale + offset
    if isinstance(value, int):
        return int(round(result))
    if isinstance(value, Decimal):
        return Decimal(str(round(result, 2)))
    return round(result, 4)


def _apply_date_shift(value: Any, offset_days: int) -> Optional[Any]:
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value + datetime.timedelta(days=offset_days)
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
        shifted = parsed + datetime.timedelta(days=offset_days)
        return shifted.isoformat()
    return None


def _is_low_cardinality(values: list) -> tuple[bool, int, int]:
    non_null = [v for v in values if v is not None]
    total = len(non_null)
    distinct = len({str(v) for v in non_null})
    if total < LOW_CARD_MIN_ROWS:
        return False, distinct, total
    if distinct > LOW_CARD_MAX_DISTINCT:
        return False, distinct, total
    if distinct / total > LOW_CARD_MAX_RATIO:
        return False, distinct, total
    return True, distinct, total


def classify_field(
    field: str,
    values: list,
    override_store: OverrideStore,
    declared_category: Optional[str] = None,
) -> ColumnDecision:
    """Decide the treatment for one column/field, shape-only, deny-by-default.

    Manual overrides (field-name based) are checked before any shape
    inference and always win, per the project's override precedence rule.
    """
    if override_store.is_always_mask_field(field):
        return ColumnDecision(
            field, TREATMENT_OVERRIDE_ALWAYS_MASK, "manual override: always_mask", "override"
        )
    if override_store.is_never_mask_field(field):
        return ColumnDecision(
            field, TREATMENT_OVERRIDE_NEVER_MASK, "manual override: never_mask", "override"
        )

    category = declared_category or _infer_category(values)

    if category == CATEGORY_BOOLEAN:
        return ColumnDecision(
            field,
            TREATMENT_BOOLEAN_KEPT,
            "boolean column: kept as real value (a two-value domain carries "
            "no meaningful information to hide)",
            "shape",
        )
    if category == CATEGORY_DATE:
        return ColumnDecision(
            field,
            TREATMENT_DATE_SHIFTED,
            "date/timestamp column: shifted by a consistent per-session offset "
            "to preserve chronology and durations without exposing real dates",
            "shape",
        )
    if category == CATEGORY_NUMERIC:
        return ColumnDecision(
            field,
            TREATMENT_NUMERIC_AFFINE,
            "numeric column: order-and-spread-preserving affine transform applied",
            "shape",
        )

    is_enum, distinct, total = _is_low_cardinality(values)
    if is_enum:
        return ColumnDecision(
            field,
            TREATMENT_ENUM_KEPT,
            f"low-cardinality categorical column ({distinct} distinct values "
            f"across {total} rows): kept as real business-logic labels",
            "shape",
        )
    return ColumnDecision(
        field,
        TREATMENT_DEFAULT_MASKED,
        "no safe-by-shape exemption applies (not boolean/date/numeric/"
        "low-cardinality categorical): masked by default",
        "shape-default",
    )


def _mask_scalar_value(masker: Masker, value: Any) -> Any:
    if value is None:
        return None
    fake, _label, _layer = masker.mask_value(str(value))
    return fake


def _find_quasi_identifier_risk(
    flat_records: list[dict],
    field_decision: dict[str, ColumnDecision],
    max_group_size: int,
) -> tuple[list[str], set[int]]:
    """Identify rows whose joint combination of kept-real (boolean_kept/
    enum_kept) column values is unique or near-unique across this batch --
    a residual re-identification risk even though every individual
    field's masking decision, taken in isolation, is correct (this is the
    SPIA-style finding: literal PII absent, but low-cardinality attributes
    combine to single out a row).

    Checks the FULL joint tuple of every currently kept-real column
    together, not every subset of them -- deliberately narrow (no general
    k-anonymity/l-diversity machinery), but not an incomplete shortcut:
    agreement on a superset of columns is always at least as fine-grained
    as agreement on any subset, so if some subset of these columns were
    already unique for a row, the full combination is unique too. One
    full-tuple pass therefore catches every case a subset-by-subset sweep
    would.

    Only ENUM_KEPT/BOOLEAN_KEPT fields are candidates -- a field the user
    has override_never_mask'd is excluded from the joint check entirely,
    not just left untouched by the redaction step. This means an existing
    never_mask override on a contributing column already serves as the
    correction mechanism for a false positive here, consistent with
    "manual overrides beat automated classification, always" -- no
    separate opt-out mechanism was added for that reason.

    Returns (candidate_fields, flagged_row_indices). Both are empty if
    fewer than MIN_QUASI_IDENTIFIER_CANDIDATE_FIELDS columns qualify (a
    "combination" needs at least two columns to combine) or fewer than
    MIN_QUASI_IDENTIFIER_ROWS rows exist (uniqueness is meaningless with
    nothing to compare against).
    """
    candidate_fields = sorted(
        field
        for field, decision in field_decision.items()
        if decision.treatment in (TREATMENT_BOOLEAN_KEPT, TREATMENT_ENUM_KEPT)
    )
    if len(candidate_fields) < MIN_QUASI_IDENTIFIER_CANDIDATE_FIELDS:
        return [], set()
    if len(flat_records) < MIN_QUASI_IDENTIFIER_ROWS:
        return [], set()

    keys = [tuple(fr.get(f) for f in candidate_fields) for fr in flat_records]
    counts = Counter(keys)
    flagged = {i for i, key in enumerate(keys) if counts[key] <= max_group_size}
    return candidate_fields, flagged


def mask_records(
    records: list[dict],
    field_types: Optional[dict[str, str]] = None,
    session_id: str = "default",
    override_store: Optional[OverrideStore] = None,
    vault_manager: Optional[VaultManager] = None,
    relevance: Optional[dict[str, Any]] = None,
    row_overrides: Optional[set[tuple[int, str]]] = None,
    audit_log: Optional[Any] = None,
    request_id: Optional[str] = None,
    audit_source: str = "db_record",
    quasi_identifier_check: bool = True,
    quasi_identifier_max_group_size: int = DEFAULT_QUASI_IDENTIFIER_MAX_GROUP_SIZE,
) -> tuple[list[dict], list[ColumnDecision]]:
    """Mask a list of (possibly nested) dict records.

    quasi_identifier_check, on by default (fail-toward-more-redaction is
    this project's default posture), catches the SPIA-style residual-
    context risk: two or more kept-real columns (enum_kept/boolean_kept)
    whose joint combination is unique or near-unique for one row get
    redacted for THAT ROW ONLY, even though each column's masking
    decision was individually correct. See
    `_find_quasi_identifier_risk`'s docstring for exactly what is and
    isn't checked. `quasi_identifier_max_group_size` (default 1, i.e.
    strict uniqueness) can be raised to also flag near-unique groups (a
    combination shared by only 2-3 rows, say). Set
    quasi_identifier_check=False to disable entirely for a given call --
    a never_mask override on a specific contributing column is the
    intended way to exempt just that column instead.

    audit_log, if given (an audit_log.AuditLog), logs every column's
    decision -- including ones left real (boolean/enum/never_mask) -- with
    source=audit_source ("db_record" or "mcp_tool_result"). Unlike
    prompt-text masking, EVERY examined column gets an entry here, since
    DB/record columns are enumerable and a silently-missing one would be
    an undetectable gap in the audit trail. Off by default (no disk I/O
    unless explicitly opted into).

    field_types, if given, maps flattened field name -> one of
    "boolean"/"date"/"numeric"/"text" (declared/known type takes
    precedence over inference from the record values, e.g. from
    SQLAlchemy schema introspection via mask_table below). If omitted,
    category is inferred per-field from the Python value types present.

    relevance, if given (a dict of field -> query_aware.ColumnRelevance,
    see query_aware.classify_relevant_columns), varies HOW a
    default-masked field is masked: relevant fields still get a
    realistic, consistent fake; fields classified not relevant to the
    current question are flatly redacted instead. This never changes
    WHETHER a field is masked -- boolean/enum/date/numeric fields stay
    factual regardless of relevance, since that's a Phase 2 shape
    decision, not a query-aware one. Omitting `relevance` entirely
    preserves prior behavior (always a realistic fake).

    row_overrides, if given, is a set of (row_index, field_name) pairs
    that get a realistic fake even if `relevance` says that field should
    be redacted -- used by unified_pipeline.py when a specific row's
    value is directly quoted in the question. This is deliberately
    row-scoped, not column-scoped: one row's value appearing in the
    question is evidence about that row, not about every other row
    sharing the same column, so it must not upgrade fidelity for
    unrelated rows that happen to share a field name. It has no effect on
    fields whose baseline treatment isn't already TREATMENT_REDACTED
    (e.g. it can't weaken a never_mask/boolean/enum/date/numeric field,
    nor does it do anything for a field the relevance layer already
    treated as relevant).
    """
    override_store = override_store or get_default_store()
    vault_manager = vault_manager or get_default_manager()
    masker = Masker(session_id=session_id, vault_manager=vault_manager, override_store=override_store)

    flat_records = [flatten_record(r) for r in records]
    all_fields: list[str] = []
    seen = set()
    for fr in flat_records:
        for k in fr.keys():
            if k not in seen:
                seen.add(k)
                all_fields.append(k)

    decisions: list[ColumnDecision] = []
    field_decision: dict[str, ColumnDecision] = {}
    for field in all_fields:
        values = [fr.get(field) for fr in flat_records if field in fr]
        declared = (field_types or {}).get(field)
        decision = classify_field(field, values, override_store, declared_category=declared)

        # Relevance only ever downgrades a DEFAULT-masked field to redacted
        # -- an always_mask override is a manual instruction, and the
        # project's own precedence rule ("manual overrides beat automated
        # classification, always ... independent of what any ... relevance
        # classifier decides") names the relevance classifier explicitly as
        # one of the things overrides must beat. So an always_mask field
        # always gets the normal realistic-fake treatment, never redacted
        # just because the automated relevance layer judged it irrelevant.
        if relevance is not None and decision.treatment == TREATMENT_DEFAULT_MASKED:
            field_relevance = relevance.get(field)
            if field_relevance is not None and not field_relevance.relevant:
                decision = ColumnDecision(
                    field,
                    TREATMENT_REDACTED,
                    f"masked-by-default field also classified not relevant to the "
                    f"current question ({field_relevance.reason}); flatly redacted "
                    f"rather than given a realistic fake",
                    f"{decision.layer}+{field_relevance.layer}",
                )

        decisions.append(decision)
        field_decision[field] = decision

    quasi_identifier_fields: list[str] = []
    quasi_identifier_flagged_rows: set[int] = set()
    if quasi_identifier_check:
        quasi_identifier_fields, quasi_identifier_flagged_rows = _find_quasi_identifier_risk(
            flat_records, field_decision, quasi_identifier_max_group_size
        )

    row_override_counts: dict[str, int] = {}
    quasi_identifier_redaction_counts: dict[str, int] = {}
    masked_flat_records = []
    for row_idx, fr in enumerate(flat_records):
        masked = {}
        for field, value in fr.items():
            decision = field_decision[field]
            treatment = decision.treatment

            if value is None:
                masked[field] = None
                continue

            if (
                treatment == TREATMENT_REDACTED
                and row_overrides is not None
                and (row_idx, field) in row_overrides
            ):
                treatment = TREATMENT_DEFAULT_MASKED
                row_override_counts[field] = row_override_counts.get(field, 0) + 1

            if (
                treatment in (TREATMENT_BOOLEAN_KEPT, TREATMENT_ENUM_KEPT)
                and field in quasi_identifier_fields
                and row_idx in quasi_identifier_flagged_rows
            ):
                treatment = TREATMENT_REDACTED
                quasi_identifier_redaction_counts[field] = quasi_identifier_redaction_counts.get(field, 0) + 1

            try:
                if treatment in (TREATMENT_BOOLEAN_KEPT, TREATMENT_ENUM_KEPT, TREATMENT_OVERRIDE_NEVER_MASK):
                    masked[field] = value
                elif treatment == TREATMENT_REDACTED:
                    masked[field] = REDACTED_VALUE
                elif treatment == TREATMENT_DATE_SHIFTED:
                    offset_days = _date_offset_days(session_id, field)
                    shifted = _apply_date_shift(value, offset_days)
                    if shifted is None:
                        logger.warning(
                            "field %r classified as date but value could not be "
                            "shifted; falling back to default masking (fail-safe)",
                            field,
                        )
                        masked[field] = _mask_scalar_value(masker, value)
                    else:
                        masked[field] = shifted
                elif treatment == TREATMENT_NUMERIC_AFFINE:
                    scale, offset = _numeric_transform_params(session_id, field)
                    transformed = _apply_numeric_affine(value, scale, offset)
                    if transformed is None:
                        logger.warning(
                            "field %r classified as numeric but value could not be "
                            "transformed; falling back to default masking (fail-safe)",
                            field,
                        )
                        masked[field] = _mask_scalar_value(masker, value)
                    else:
                        masked[field] = transformed
                else:
                    # default_masked or override:always_mask
                    masked[field] = _mask_scalar_value(masker, value)
            except Exception as exc:  # noqa: BLE001 - never leak a real value on error
                logger.error(
                    "unexpected error masking field %r (%s); redacting flatly (fail-safe)",
                    field,
                    exc,
                )
                masked[field] = "[REDACTED]"
        masked_flat_records.append(masked)

    if row_override_counts:
        for i, decision in enumerate(decisions):
            count = row_override_counts.get(decision.field)
            if count:
                decisions[i] = ColumnDecision(
                    decision.field,
                    decision.treatment,
                    decision.reason
                    + f" (note: {count} of {len(flat_records)} row(s) in this batch were "
                    f"individually given a realistic fake instead, because their exact "
                    f"value was directly quoted in the question)",
                    decision.layer,
                )

    if quasi_identifier_redaction_counts:
        for i, decision in enumerate(decisions):
            count = quasi_identifier_redaction_counts.get(decision.field)
            if count:
                other_fields = [f for f in quasi_identifier_fields if f != decision.field]
                decisions[i] = ColumnDecision(
                    decision.field,
                    decision.treatment,
                    decision.reason
                    + f" (note: {count} of {len(flat_records)} row(s) in this batch had "
                    f"this column's value redacted for that row only, due to a "
                    f"quasi-identifier re-identification risk when combined with: "
                    f"{', '.join(other_fields)})",
                    decision.layer,
                )

    masked_records = [unflatten_record(mf) for mf in masked_flat_records]

    quasi_identifier_audit_entries: list[tuple[str, str, str, str]] = []
    if quasi_identifier_flagged_rows and quasi_identifier_fields:
        combo_label = "+".join(quasi_identifier_fields)
        n_rows = len(flat_records)
        for row_idx in sorted(quasi_identifier_flagged_rows):
            quasi_identifier_audit_entries.append(
                (
                    f"quasi_identifier:{combo_label}",
                    "redacted",
                    f"quasi-identifier risk: row {row_idx}'s {combo_label} combination is "
                    f"unique (or near-unique) among {n_rows} rows in this batch; "
                    f"contributing columns redacted for this row only",
                    "quasi-identifier-check",
                )
            )

    if audit_log is not None and (decisions or quasi_identifier_audit_entries):
        audit_log.log_decisions(
            request_id=request_id or "unknown",
            session_id=session_id,
            source=audit_source,
            decisions=[
                (d.field, TREATMENT_TO_AUDIT_DECISION.get(d.treatment, "masked"), d.reason, d.layer)
                for d in decisions
            ]
            + quasi_identifier_audit_entries,
        )

    return masked_records, decisions


def mask_table(
    engine: Engine,
    table_name: str,
    session_id: str = "default",
    override_store: Optional[OverrideStore] = None,
    vault_manager: Optional[VaultManager] = None,
    relevance: Optional[dict[str, Any]] = None,
    row_overrides: Optional[set[tuple[int, str]]] = None,
    audit_log: Optional[Any] = None,
    request_id: Optional[str] = None,
    quasi_identifier_check: bool = True,
    quasi_identifier_max_group_size: int = DEFAULT_QUASI_IDENTIFIER_MAX_GROUP_SIZE,
) -> tuple[list[dict], list[ColumnDecision]]:
    """Reflect a table via SQLAlchemy (dialect-agnostic), fetch all rows,
    and mask them with schema-declared type categories taking precedence
    over value-based inference.

    Any DB error (connection failure, missing table, etc.) is raised as-is
    to the caller rather than silently swallowed -- but produces no partial/
    unmasked data: this function returns masked data or raises, never a mix.
    """
    try:
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=engine)
        with engine.connect() as conn:
            result = conn.execute(select(table))
            rows = [dict(row._mapping) for row in result]
    except Exception as exc:
        logger.error("failed to introspect/read table %r (%s)", table_name, exc)
        raise

    field_types = {col.name: _sa_type_category(col.type) for col in table.columns}
    return mask_records(
        rows,
        field_types=field_types,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        relevance=relevance,
        row_overrides=row_overrides,
        audit_log=audit_log,
        request_id=request_id,
        audit_source="db_record",
        quasi_identifier_check=quasi_identifier_check,
        quasi_identifier_max_group_size=quasi_identifier_max_group_size,
    )
