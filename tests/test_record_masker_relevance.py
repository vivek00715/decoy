import json
import uuid

import pytest

from decoy.overrides import OverrideStore
from decoy.query_aware import ColumnRelevance
from decoy.record_masker import TREATMENT_DEFAULT_MASKED, TREATMENT_REDACTED, mask_records
from decoy.vault import VaultManager


@pytest.fixture
def vault_manager():
    return VaultManager(persist=False)


@pytest.fixture
def override_store(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": []}}))
    return OverrideStore(path=path)


def test_relevant_default_masked_field_gets_realistic_fake_irrelevant_gets_redacted(
    vault_manager, override_store
):
    session_id = str(uuid.uuid4())
    records = [
        {"employee_name": "Alice Smith", "notes": "flight risk, high performer"},
        {"employee_name": "Bob Jones", "notes": "recently promoted"},
    ]
    relevance = {
        "employee_name": ColumnRelevance("employee_name", False, "not mentioned in question", "keyword"),
        "notes": ColumnRelevance("notes", True, "question is about notes", "keyword"),
    }

    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store,
        vault_manager=vault_manager, relevance=relevance,
    )

    name_decision = next(d for d in decisions if d.field == "employee_name")
    notes_decision = next(d for d in decisions if d.field == "notes")
    assert name_decision.treatment == TREATMENT_REDACTED
    assert notes_decision.treatment == TREATMENT_DEFAULT_MASKED

    for row in masked:
        assert row["employee_name"] == "[REDACTED]"
        assert row["notes"] not in ("flight risk, high performer", "recently promoted")
        assert row["notes"] != "[REDACTED]"


def test_no_relevance_arg_preserves_prior_behavior(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    records = [{"employee_name": "Alice Smith"}]
    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store, vault_manager=vault_manager
    )
    decision = next(d for d in decisions if d.field == "employee_name")
    assert decision.treatment == TREATMENT_DEFAULT_MASKED
    assert masked[0]["employee_name"] != "Alice Smith"
    assert masked[0]["employee_name"] != "[REDACTED]"


def test_row_overrides_only_elevate_the_specific_row(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    records = [
        {"contact_email": "match@example.com"},
        {"contact_email": "other@example.com"},
    ]
    relevance = {
        "contact_email": ColumnRelevance("contact_email", False, "not mentioned in question", "keyword"),
    }
    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store,
        vault_manager=vault_manager, relevance=relevance,
        row_overrides={(0, "contact_email")},
    )
    assert masked[0]["contact_email"] not in ("match@example.com", "[REDACTED]")
    assert masked[1]["contact_email"] == "[REDACTED]"
    decision = next(d for d in decisions if d.field == "contact_email")
    assert decision.treatment == TREATMENT_REDACTED
    assert "1 of 2 row(s)" in decision.reason


def test_row_overrides_have_no_effect_on_never_mask_field(vault_manager, tmp_path):
    session_id = str(uuid.uuid4())
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": ["cust_id"]}}))
    store = OverrideStore(path=path)

    records = [{"cust_id": "C1"}, {"cust_id": "C2"}]
    masked, decisions = mask_records(
        records, session_id=session_id, override_store=store, vault_manager=vault_manager,
        row_overrides={(0, "cust_id"), (1, "cust_id")},
    )
    decision = next(d for d in decisions if d.field == "cust_id")
    assert decision.treatment == "override_never_mask"
    assert "row(s) in this batch" not in decision.reason
    assert masked[0]["cust_id"] == "C1"
    assert masked[1]["cust_id"] == "C2"


def test_relevance_never_redacts_shape_safe_columns(vault_manager, override_store):
    # A boolean column classified "not relevant" must still be kept real --
    # relevance only ever governs default_masked/always_mask fields.
    session_id = str(uuid.uuid4())
    records = [{"is_active": True}, {"is_active": False}]
    relevance = {"is_active": ColumnRelevance("is_active", False, "irrelevant", "keyword")}
    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store,
        vault_manager=vault_manager, relevance=relevance,
    )
    decision = next(d for d in decisions if d.field == "is_active")
    assert decision.treatment == "boolean_kept"
    assert masked[0]["is_active"] is True
    assert masked[1]["is_active"] is False
