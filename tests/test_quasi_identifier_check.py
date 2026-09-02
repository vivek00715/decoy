"""Tests for the quasi-identifier redaction mitigation (v1 response to the
Phase 10 SPIA-style residual-context finding): two or more kept-real
(enum_kept/boolean_kept) columns whose joint combination uniquely (or
near-uniquely) identifies a row get redacted for that row only.

See record_masker.py's module docstring and `_find_quasi_identifier_risk`
for the design; tests/test_benchmark_suite.py's
test_spia_residual_context_probe exercises this through the full
benchmark case this mitigation was built to address.
"""

import json
import uuid

import pytest

from decoy.overrides import OverrideStore
from decoy.record_masker import (
    REDACTED_VALUE,
    mask_records,
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


def _employee_rows():
    # 10 rows: two people share "VP", five share "Austin", but only one
    # row combines both -- neither field alone is unique.
    rows = [
        {"name": "Alice", "title": "VP", "office": "Austin"},
        {"name": "Bob", "title": "VP", "office": "Remote"},
    ]
    for i in range(8):
        rows.append({"name": f"Person{i}", "title": "IC", "office": "Austin" if i < 4 else "Remote"})
    return rows


def test_unique_combination_of_kept_real_columns_gets_redacted_for_that_row_only(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    records = _employee_rows()
    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store, vault_manager=vault_manager
    )

    assert masked[0]["title"] == REDACTED_VALUE
    assert masked[0]["office"] == REDACTED_VALUE
    assert masked[1]["title"] == REDACTED_VALUE
    assert masked[1]["office"] == REDACTED_VALUE

    for row in masked[2:]:
        assert row["title"] == "IC"
        assert row["office"] in ("Austin", "Remote")

    title_decision = next(d for d in decisions if d.field == "title")
    office_decision = next(d for d in decisions if d.field == "office")
    assert title_decision.treatment == "enum_kept"  # column-level treatment unchanged
    assert "quasi-identifier" in title_decision.reason
    assert "office" in title_decision.reason
    assert "quasi-identifier" in office_decision.reason


def test_no_false_positive_when_no_combination_is_actually_unique(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    # 10 rows, 2 titles x 2 offices, each combination shared by >=2 rows.
    records = []
    for i in range(10):
        records.append({"title": "A" if i % 2 == 0 else "B", "office": "X" if i % 4 < 2 else "Y"})
    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store, vault_manager=vault_manager
    )
    assert all(row["title"] != REDACTED_VALUE for row in masked)
    assert all(row["office"] != REDACTED_VALUE for row in masked)


def test_single_candidate_column_never_triggers_the_check(vault_manager, override_store):
    # Only one enum_kept-eligible column exists -- a "combination" needs
    # at least two columns, so nothing should be flagged even though the
    # single column has a unique value.
    session_id = str(uuid.uuid4())
    records = [{"status": "unique_value"}] + [{"status": "common"} for _ in range(9)]
    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store, vault_manager=vault_manager
    )
    assert masked[0]["status"] == "unique_value"  # not redacted -- no combination to check


def test_never_mask_override_on_a_contributing_column_excludes_it_from_the_check(vault_manager, tmp_path):
    # The escape hatch: a never_mask override on "office" removes it from
    # candidacy entirely, so title's uniqueness alone (now the only
    # candidate column) can't trigger a combination check either --
    # consistent with never_mask overrides beating automated
    # classification, always.
    session_id = str(uuid.uuid4())
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": ["office"]}}))
    store = OverrideStore(path=path)

    records = _employee_rows()
    masked, decisions = mask_records(records, session_id=session_id, override_store=store)

    # office is kept real everywhere (never_mask), and with only one
    # remaining candidate column (title), no combination check can run.
    assert masked[0]["office"] == "Austin"
    assert masked[1]["office"] == "Remote"
    assert masked[0]["title"] != REDACTED_VALUE
    assert masked[1]["title"] != REDACTED_VALUE


def test_quasi_identifier_check_can_be_disabled(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    records = _employee_rows()
    masked, decisions = mask_records(
        records,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        quasi_identifier_check=False,
    )
    assert masked[0]["title"] == "VP"
    assert masked[0]["office"] == "Austin"


def test_max_group_size_can_flag_near_unique_groups_not_just_strict_uniqueness(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    # 3 people share (title="Lead", office="Berlin"); with
    # max_group_size=1 that's safe (not strictly unique); with
    # max_group_size=3 it should be flagged as "near-unique".
    records = [{"title": "Lead", "office": "Berlin"} for _ in range(3)]
    records += [{"title": "IC", "office": "Berlin" if i % 2 == 0 else "Remote"} for i in range(7)]

    masked_strict, _ = mask_records(
        records, session_id=str(uuid.uuid4()), override_store=override_store,
        vault_manager=vault_manager, quasi_identifier_max_group_size=1,
    )
    assert all(row["title"] != REDACTED_VALUE for row in masked_strict[:3])

    masked_lenient, _ = mask_records(
        records, session_id=str(uuid.uuid4()), override_store=override_store,
        vault_manager=vault_manager, quasi_identifier_max_group_size=3,
    )
    assert all(row["title"] == REDACTED_VALUE for row in masked_lenient[:3])


def test_boolean_columns_participate_in_the_combination_check(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    # is_manager (boolean) + title (enum) jointly identify row 0.
    records = [{"is_manager": True, "title": "VP"}]
    records += [{"is_manager": False, "title": "VP"} for _ in range(4)]
    records += [{"is_manager": False, "title": "IC"} for _ in range(5)]

    masked, decisions = mask_records(
        records, session_id=session_id, override_store=override_store, vault_manager=vault_manager
    )
    assert masked[0]["is_manager"] == REDACTED_VALUE
    assert masked[0]["title"] == REDACTED_VALUE
    # rows 1-4 (is_manager=False, title=VP, group size 4) are untouched
    for row in masked[1:5]:
        assert row["is_manager"] is False
        assert row["title"] == "VP"


def test_audit_log_gets_a_dedicated_per_row_entry_with_no_real_values(vault_manager, override_store, tmp_path):
    from decoy.audit_log import AuditLog

    session_id = str(uuid.uuid4())
    audit_log = AuditLog(path=tmp_path / "audit.enc", key_path=tmp_path / "vault.key")
    request_id = str(uuid.uuid4())
    records = _employee_rows()

    mask_records(
        records, session_id=session_id, override_store=override_store,
        vault_manager=vault_manager, audit_log=audit_log, request_id=request_id,
    )

    entries = audit_log.get_entries(request_id=request_id)
    qi_entries = [e for e in entries if e["layer"] == "quasi-identifier-check"]
    assert len(qi_entries) == 2  # rows 0 and 1
    for e in qi_entries:
        assert e["decision"] == "redacted"
        assert "quasi-identifier risk" in e["reason"]
        assert "Alice" not in e["reason"] and "Bob" not in e["reason"]
        assert "Austin" not in e["reason"] and "Remote" not in e["reason"]
    dumped = json.dumps(entries)
    assert "Alice" not in dumped
    assert "Austin" not in dumped
