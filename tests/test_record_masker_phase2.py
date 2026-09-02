import datetime
import json
import uuid

import pytest
from sqlalchemy import Boolean, Column, Date, Integer, MetaData, String, Table, create_engine, insert

from decoy.overrides import OverrideStore
from decoy.record_masker import (
    TREATMENT_BOOLEAN_KEPT,
    TREATMENT_DATE_SHIFTED,
    TREATMENT_DEFAULT_MASKED,
    TREATMENT_ENUM_KEPT,
    TREATMENT_NUMERIC_AFFINE,
    mask_records,
    mask_table,
)
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
        "customers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("is_active", Boolean),
        Column("order_status", String),
        Column("signup_date", Date),
        Column("balance", Integer),
        Column("sky_number", String),  # meaningless name, high-cardinality alnum
    )
    metadata.create_all(engine)

    # 15 rows cycling 3 status values -> distinct/total ratio 0.2, clearly
    # low-cardinality (a realistic status column in a real table would have
    # an even lower ratio; kept small here for a fast in-memory test).
    statuses = ["open", "shipped", "cancelled"]
    sky_numbers = [
        "ZQ8841XR", "PL02JJ90", "MZ19QW77", "AB55CD10", "XX77YY22",
        "QW11ER33", "TY44UI55", "OP66AS77", "DF88GH99", "JK10LZ21",
        "XC32VB43", "NM54QW65", "ER76TY87", "UI98OP09", "AS21DF32",
    ]
    rows = []
    for i in range(15):
        rows.append(
            {
                "id": i + 1,
                "is_active": bool(i % 2 == 0),
                "order_status": statuses[i % 3],
                "signup_date": datetime.date(2023, 1, 1) + datetime.timedelta(days=i * 12),
                "balance": 10 + i * 37,
                "sky_number": sky_numbers[i],
            }
        )
    with engine.begin() as conn:
        conn.execute(insert(table), rows)
    return engine


def test_zero_config_column_treatment_by_shape(sqlite_engine, vault_manager, override_store):
    session_id = str(uuid.uuid4())
    masked_rows, decisions = mask_table(
        sqlite_engine, "customers", session_id=session_id,
        override_store=override_store, vault_manager=vault_manager,
    )

    decision_by_field = {d.field: d for d in decisions}
    assert decision_by_field["is_active"].treatment == TREATMENT_BOOLEAN_KEPT
    assert decision_by_field["order_status"].treatment == TREATMENT_ENUM_KEPT
    assert decision_by_field["signup_date"].treatment == TREATMENT_DATE_SHIFTED
    assert decision_by_field["balance"].treatment == TREATMENT_NUMERIC_AFFINE
    assert decision_by_field["sky_number"].treatment == TREATMENT_DEFAULT_MASKED

    statuses = ["open", "shipped", "cancelled"]
    sky_numbers = [
        "ZQ8841XR", "PL02JJ90", "MZ19QW77", "AB55CD10", "XX77YY22",
        "QW11ER33", "TY44UI55", "OP66AS77", "DF88GH99", "JK10LZ21",
        "XC32VB43", "NM54QW65", "ER76TY87", "UI98OP09", "AS21DF32",
    ]
    original_active = [bool(i % 2 == 0) for i in range(15)]
    original_statuses = [statuses[i % 3] for i in range(15)]
    original_dates = [datetime.date(2023, 1, 1) + datetime.timedelta(days=i * 12) for i in range(15)]
    original_balances = [10 + i * 37 for i in range(15)]

    for i, masked in enumerate(masked_rows):
        assert masked["is_active"] == original_active[i]
        assert masked["order_status"] == original_statuses[i]

    # sky_number fully masked, no classifier -- just never equals original
    for orig, row in zip(sky_numbers, masked_rows):
        assert row["sky_number"] != orig
        assert len(row["sky_number"]) == len(orig)

    # date shift preserves chronology (relative order) and exact durations
    masked_dates = [row["signup_date"] for row in masked_rows]

    def _as_date(d):
        return d if isinstance(d, datetime.date) else datetime.date.fromisoformat(d)

    parsed_masked = [_as_date(d) for d in masked_dates]
    orig_order = sorted(range(len(original_dates)), key=lambda i: original_dates[i])
    masked_order = sorted(range(len(parsed_masked)), key=lambda i: parsed_masked[i])
    assert orig_order == masked_order
    delta_orig = (original_dates[1] - original_dates[0]).days
    delta_masked = (parsed_masked[1] - parsed_masked[0]).days
    assert delta_orig == delta_masked
    assert all(d != o for d, o in zip(parsed_masked, original_dates))

    # numeric affine preserves relative order
    masked_balances = [row["balance"] for row in masked_rows]
    orig_rank = sorted(range(len(original_balances)), key=lambda i: original_balances[i])
    masked_rank = sorted(range(len(masked_balances)), key=lambda i: masked_balances[i])
    assert orig_rank == masked_rank
    assert masked_balances != original_balances


def test_ambiguous_cardinality_defaults_to_masked(vault_manager, override_store):
    # 5 rows, 5 distinct string values -> ratio 1.0, NOT low-cardinality:
    # ambiguous case must default to masked, not kept.
    session_id = str(uuid.uuid4())
    records = [{"color": c} for c in ["red", "blue", "green", "purple", "teal"]]
    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store, vault_manager=vault_manager
    )
    decision = next(d for d in decisions if d.field == "color")
    assert decision.treatment == TREATMENT_DEFAULT_MASKED
    for orig, row in zip(records, masked):
        assert row["color"] != orig["color"]


def test_never_mask_override_keeps_column_real(vault_manager, tmp_path):
    session_id = str(uuid.uuid4())
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": ["color"]}}))
    store = OverrideStore(path=path)

    records = [{"color": c} for c in ["red", "blue", "green", "purple", "teal"]]
    masked, decisions = mask_records(records, session_id=session_id, override_store=store, vault_manager=vault_manager)
    decision = next(d for d in decisions if d.field == "color")
    assert decision.treatment == "override_never_mask"
    for orig, row in zip(records, masked):
        assert row["color"] == orig["color"]


def test_always_mask_override_forces_masking_of_boolean(vault_manager, tmp_path):
    session_id = str(uuid.uuid4())
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"always_mask": {"field_names": ["is_flagged"]}, "never_mask": {"field_names": []}}))
    store = OverrideStore(path=path)

    records = [{"is_flagged": True}, {"is_flagged": False}]
    masked, decisions = mask_records(records, session_id=session_id, override_store=store, vault_manager=vault_manager)
    decision = next(d for d in decisions if d.field == "is_flagged")
    assert decision.treatment == "override_always_mask"
    assert masked[0]["is_flagged"] != True or isinstance(masked[0]["is_flagged"], str)


def test_nested_elasticsearch_style_record_round_trips_structure(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    records = [
        {
            "_id": "doc1",
            "user": {
                "email": "person@example.com",
                "profile": {"nickname": "xX_coder_Xx", "verified": True},
            },
            "tags": ["alpha", "beta"],
            "score": 42,
        }
    ]
    masked, decisions = mask_records(records, session_id=session_id, override_store=override_store, vault_manager=vault_manager)

    row = masked[0]
    assert "user" in row and "profile" in row["user"]
    assert row["user"]["email"] != "person@example.com"
    assert "@" in row["user"]["email"]  # regex-detected email -> realistic fake email
    assert row["user"]["profile"]["verified"] is True  # boolean, single value, kept
    assert row["user"]["profile"]["nickname"] != "xX_coder_Xx"
    assert isinstance(row["tags"], list) and len(row["tags"]) == 2
    assert row["tags"][0] != "alpha"


def test_multipart_email_value_masked_correctly_not_truncated(vault_manager, override_store):
    # Regression guard for the Phase 1 lesson: multi-level domains must not
    # be truncated by the regex layer when masking a DB cell as a whole value.
    session_id = str(uuid.uuid4())
    records = [{"contact": "person@mail.decoy.example.co.uk"}]
    masked, decisions = mask_records(records, session_id=session_id, override_store=override_store, vault_manager=vault_manager)
    fake = masked[0]["contact"]
    assert fake != "person@mail.decoy.example.co.uk"
    assert "@" in fake
    # shape-preserving/email fake should not retain any suffix of the real domain
    assert "decoy.example.co.uk" not in fake
