"""Edge-case-aware sampling: given any SQLAlchemy engine + table, return
the exact unmasked schema plus a MASKED sample that deliberately includes
nulls, numeric min/max, duplicates on id-like columns, and empty strings
-- not just a random sample -- each row tagged with why it's there.

This exists because a random sample is a poor way to hand an LLM (or a
developer) a representative picture of a table for writing correct code
against it: it can easily miss the null, the outlier, or the duplicate
that would otherwise break generated code the first time it runs against
real data. The schema itself is never masked (Decoy's schema/value split
applies here too); only the sampled row VALUES are.

SQL dialect differences for random ordering are handled by branching on
`engine.dialect.name`; an unrecognized dialect falls back to fetching all
rows and sampling in Python rather than guessing at dialect-specific SQL.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field as dc_field
from typing import Any, Optional

from sqlalchemy import Boolean, Column, Float, Integer, MetaData, Numeric, String, Table, func, select
from sqlalchemy.engine import Engine

from .logging_config import get_logger
from .overrides import OverrideStore, get_default_store
from .record_masker import ColumnDecision, _sa_type_category, mask_records
from .vault import VaultManager, get_default_manager

logger = get_logger("edge_case_sampler")

DEFAULT_SAMPLE_SIZE = 20
MAX_ROWS_PER_REASON = 3


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    type: str
    nullable: bool
    primary_key: bool


@dataclass
class TaggedRow:
    row: dict
    reasons: list[str] = dc_field(default_factory=list)


@dataclass
class EdgeCaseSample:
    table_name: str
    schema: list[ColumnSchema]
    rows: list[TaggedRow]
    column_decisions: list[ColumnDecision] = dc_field(default_factory=list)


def _is_id_like(column: Column) -> bool:
    """Heuristic used only to decide which columns to check for duplicate
    anomalies during sampling -- NOT a privacy-masking decision (that
    stays shape-only per record_masker.py's deny-by-default rule). A false
    positive/negative here just means we check one extra/fewer column for
    a data-quality edge case, not a privacy exposure either way."""
    if column.primary_key:
        return True
    name = column.name.lower()
    return name == "id" or name.endswith("_id") or name.startswith("id_")


def _dialect_random_order(engine: Engine):
    """Dialect-appropriate SQL for random ordering, or None if the dialect
    is unrecognized (caller should fall back to Python-side sampling)."""
    name = engine.dialect.name
    if name in ("sqlite", "postgresql"):
        return func.random()
    if name == "mysql":
        return func.rand()
    if name == "mssql":
        return func.newid()
    return None


def sample_table_with_edge_cases(
    engine: Engine,
    table_name: str,
    session_id: str = "default",
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    override_store: Optional[OverrideStore] = None,
    vault_manager: Optional[VaultManager] = None,
    audit_log: Optional[Any] = None,
    request_id: Optional[str] = None,
) -> EdgeCaseSample:
    """Reflect `table_name` (dialect-agnostic via SQLAlchemy), then build a
    masked sample deliberately including nulls, numeric min/max rows,
    duplicate id-like values, and empty strings, topped up with a
    dialect-aware random sample to reach `sample_size`. Raises on any DB
    error rather than returning partial/unmasked data.
    """
    try:
        metadata = MetaData()
        table = Table(table_name, metadata, autoload_with=engine)
    except Exception as exc:
        logger.error("failed to reflect table %r (%s)", table_name, exc)
        raise

    schema = [
        ColumnSchema(
            name=col.name,
            type=str(col.type),
            nullable=bool(col.nullable) if col.nullable is not None else True,
            primary_key=bool(col.primary_key),
        )
        for col in table.columns
    ]

    pk_cols = [c.name for c in table.columns if c.primary_key]

    def _row_key(row: dict) -> tuple:
        if pk_cols:
            return tuple(row.get(c) for c in pk_cols)
        return tuple(sorted(row.items(), key=lambda kv: kv[0]))

    tagged_by_key: dict[tuple, TaggedRow] = {}

    def _add_row(row: dict, reason: str) -> None:
        key = _row_key(row)
        existing = tagged_by_key.get(key)
        if existing is not None:
            if reason not in existing.reasons:
                existing.reasons.append(reason)
        else:
            tagged_by_key[key] = TaggedRow(row=dict(row), reasons=[reason])

    try:
        with engine.connect() as conn:
            # 1. Rows containing a null in a nullable column
            for col in table.columns:
                if not col.nullable:
                    continue
                stmt = select(table).where(table.c[col.name].is_(None)).limit(MAX_ROWS_PER_REASON)
                for r in conn.execute(stmt):
                    _add_row(dict(r._mapping), f"null value in column '{col.name}'")

            # 2. Numeric min/max rows per numeric column
            for col in table.columns:
                if isinstance(col.type, Boolean):
                    continue
                if not isinstance(col.type, (Integer, Numeric, Float)):
                    continue
                for label, order_clause in (
                    ("min", table.c[col.name].asc()),
                    ("max", table.c[col.name].desc()),
                ):
                    stmt = (
                        select(table)
                        .where(table.c[col.name].isnot(None))
                        .order_by(order_clause)
                        .limit(1)
                    )
                    result = conn.execute(stmt).first()
                    if result is not None:
                        _add_row(dict(result._mapping), f"numeric {label} for column '{col.name}'")

            # 3. Rows with a duplicated value in an id-like column
            for col in table.columns:
                if not _is_id_like(col):
                    continue
                dup_stmt = (
                    select(table.c[col.name])
                    .where(table.c[col.name].isnot(None))
                    .group_by(table.c[col.name])
                    .having(func.count() > 1)
                )
                dup_values = [row[0] for row in conn.execute(dup_stmt)]
                for val in dup_values[:MAX_ROWS_PER_REASON]:
                    stmt = select(table).where(table.c[col.name] == val).limit(MAX_ROWS_PER_REASON)
                    for r in conn.execute(stmt):
                        _add_row(dict(r._mapping), f"duplicate value in id-like column '{col.name}'")

            # 4. Rows with an empty string
            for col in table.columns:
                if not isinstance(col.type, String):
                    continue
                stmt = select(table).where(table.c[col.name] == "").limit(MAX_ROWS_PER_REASON)
                for r in conn.execute(stmt):
                    _add_row(dict(r._mapping), f"empty string in column '{col.name}'")

            # 5. Dialect-aware random baseline sample, topping up to sample_size
            remaining = max(0, sample_size - len(tagged_by_key))
            if remaining > 0:
                random_order = _dialect_random_order(engine)
                if random_order is not None:
                    stmt = select(table).order_by(random_order).limit(remaining)
                    for r in conn.execute(stmt):
                        _add_row(dict(r._mapping), "general sample")
                else:
                    logger.warning(
                        "unrecognized SQL dialect %r for random ordering; falling back to "
                        "Python-side sampling (fetch all rows, shuffle in-process)",
                        engine.dialect.name,
                    )
                    all_rows = [dict(r._mapping) for r in conn.execute(select(table))]
                    random.shuffle(all_rows)
                    for r in all_rows[:remaining]:
                        _add_row(r, "general sample")
    except Exception as exc:
        logger.error("failed to sample table %r (%s)", table_name, exc)
        raise

    field_types = {col.name: _sa_type_category(col.type) for col in table.columns}
    tagged_rows = list(tagged_by_key.values())
    raw_rows = [tr.row for tr in tagged_rows]

    masked_rows, decisions = mask_records(
        raw_rows,
        field_types=field_types,
        session_id=session_id,
        override_store=override_store or get_default_store(),
        vault_manager=vault_manager or get_default_manager(),
        audit_log=audit_log,
        request_id=request_id,
        audit_source="db_record",
    )

    final_rows = [
        TaggedRow(row=masked, reasons=tr.reasons) for masked, tr in zip(masked_rows, tagged_rows)
    ]

    if len(final_rows) > sample_size:
        edge_rows = [r for r in final_rows if r.reasons != ["general sample"]]
        general_rows = [r for r in final_rows if r.reasons == ["general sample"]]
        final_rows = (edge_rows + general_rows)[:sample_size]

    return EdgeCaseSample(table_name=table_name, schema=schema, rows=final_rows, column_decisions=decisions)
