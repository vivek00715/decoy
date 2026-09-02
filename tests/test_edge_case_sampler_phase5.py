import json
import uuid

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, insert

from decoy.edge_case_sampler import sample_table_with_edge_cases
from decoy.overrides import OverrideStore
from decoy.vault import VaultManager


@pytest.fixture
def vault_manager():
    return VaultManager(persist=False)


@pytest.fixture
def override_store(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": []}}))
    return OverrideStore(path=path)


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    table = Table(
        "widgets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("external_id", String),  # id-like by name, will have a duplicate
        Column("label", String),
        Column("quantity", Integer),
    )
    metadata.create_all(engine)

    rows = [
        {"id": 1, "external_id": "REF-A", "label": "widget one", "quantity": 5},
        {"id": 2, "external_id": "REF-B", "label": "widget two", "quantity": 12},
        {"id": 3, "external_id": "REF-A", "label": "widget three", "quantity": 999},  # dup ref + numeric outlier max
        {"id": 4, "external_id": "REF-C", "label": None, "quantity": 1},  # null label
        {"id": 5, "external_id": "REF-D", "label": "", "quantity": 0},  # empty string label, numeric min
        {"id": 6, "external_id": "REF-E", "label": "widget six", "quantity": 7},
        {"id": 7, "external_id": "REF-F", "label": "widget seven", "quantity": 8},
    ]
    with engine.begin() as conn:
        conn.execute(insert(table), rows)
    return engine


def test_edge_cases_present_tagged_and_masked(sqlite_engine, vault_manager, override_store):
    session_id = str(uuid.uuid4())
    sample = sample_table_with_edge_cases(
        sqlite_engine, "widgets", session_id=session_id,
        override_store=override_store, vault_manager=vault_manager, sample_size=10,
    )

    # schema is exact/unmasked
    schema_by_name = {c.name: c for c in sample.schema}
    assert schema_by_name["id"].primary_key is True
    assert "INTEGER" in schema_by_name["quantity"].type.upper()

    all_reasons = [reason for tr in sample.rows for reason in tr.reasons]
    assert any("null value in column 'label'" in r for r in all_reasons)
    assert any("numeric max for column 'quantity'" in r for r in all_reasons)
    assert any("numeric min for column 'quantity'" in r for r in all_reasons)
    assert any("duplicate value in id-like column 'external_id'" in r for r in all_reasons)
    assert any("empty string in column 'label'" in r for r in all_reasons)

    # the row with the numeric max (quantity=999, id=3) must be masked --
    # id-like/text fields masked, numeric preserved via affine transform
    # (not necessarily equal to 999, but present and tagged)
    max_row = next(tr for tr in sample.rows if any("numeric max" in r for r in tr.reasons))
    assert max_row.row["quantity"] != 0  # sanity: affine-transformed, not zeroed
    assert max_row.row["external_id"] != "REF-A"  # default_masked field: never real value
    assert max_row.row["label"] != "widget three"

    # duplicate-tagged rows must include both id=1 and id=3 (both REF-A)
    dup_rows = [tr for tr in sample.rows if any("duplicate value" in r for r in tr.reasons)]
    assert len(dup_rows) >= 2

    # no real external_id value ever leaks into the masked sample
    dumped = json.dumps([tr.row for tr in sample.rows])
    for real_ref in ("REF-A", "REF-B", "REF-C", "REF-D", "REF-E", "REF-F"):
        assert real_ref not in dumped
    assert "widget one" not in dumped
    assert "widget three" not in dumped


def test_unrecognized_dialect_falls_back_to_python_sampling(sqlite_engine, vault_manager, override_store, monkeypatch):
    # Force the dialect name to something unrecognized to exercise the
    # Python-side fallback path explicitly.
    monkeypatch.setattr(type(sqlite_engine.dialect), "name", "made_up_dialect", raising=False)
    session_id = str(uuid.uuid4())
    sample = sample_table_with_edge_cases(
        sqlite_engine, "widgets", session_id=session_id,
        override_store=override_store, vault_manager=vault_manager, sample_size=10,
    )
    assert len(sample.rows) > 0


def test_never_mask_override_respected_by_edge_case_sampler(sqlite_engine, vault_manager, tmp_path):
    # edge_case_sampler.py builds its own SQL queries directly (a separate
    # entry point from ask_over_data/mask_records' normal callers), so it
    # would be easy for it to call mask_records() correctly while still
    # silently using a different/default override_store or vault_manager
    # than the one the caller configured. Prove that a never_mask override
    # on the caller's own override_store is actually respected here.
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": ["external_id"]}})
    )
    store = OverrideStore(path=path)

    session_id = str(uuid.uuid4())
    sample = sample_table_with_edge_cases(
        sqlite_engine, "widgets", session_id=session_id,
        override_store=store, vault_manager=vault_manager, sample_size=10,
    )

    real_external_ids = {"REF-A", "REF-B", "REF-C", "REF-D", "REF-E", "REF-F"}
    seen_external_ids = {tr.row["external_id"] for tr in sample.rows}
    # never_mask means these must appear REAL and unmasked in the sample --
    # if the sampler were using a different/default override_store, these
    # would come back as fakes instead, and this assertion would catch it.
    assert seen_external_ids <= real_external_ids
    assert seen_external_ids  # sanity: we did get some rows back

    decision = next(d for d in sample.column_decisions if d.field == "external_id")
    assert decision.treatment == "override_never_mask"

    # meanwhile "label" (no override) is still masked as normal, proving
    # the override_store is scoped correctly rather than disabling masking
    # wholesale
    for tr in sample.rows:
        if tr.row["label"] is not None:
            assert tr.row["label"] not in ("widget one", "widget two", "widget three")


def test_sample_size_cap_prioritizes_edge_cases(sqlite_engine, vault_manager, override_store):
    session_id = str(uuid.uuid4())
    sample = sample_table_with_edge_cases(
        sqlite_engine, "widgets", session_id=session_id,
        override_store=override_store, vault_manager=vault_manager, sample_size=2,
    )
    assert len(sample.rows) <= 2
    # with such a small cap, every included row should carry a real edge-case
    # tag rather than being a generic filler row, since edge rows are
    # prioritized over "general sample" rows when trimming to the cap.
    assert all(tr.reasons != ["general sample"] for tr in sample.rows)
