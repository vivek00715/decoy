import json
import uuid

import pytest

from decoy.audit_log import AuditLog
from decoy.overrides import OverrideStore
from decoy.unified_pipeline import PipelineError, ask_over_data
from decoy.vault import VaultManager


@pytest.fixture
def vault_manager():
    return VaultManager(persist=False)


@pytest.fixture
def override_store(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": []}}))
    return OverrideStore(path=path)


def _echo_llm_call(prompt: str) -> str:
    return "OK, I reviewed the data. " + prompt


def test_same_email_in_question_and_records_gets_same_fake(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    email = "alice@example.com"
    other_email = "carol@example.com"

    def fetch_records():
        return [
            {"contact_email": email, "department": "engineering"},
            {"contact_email": other_email, "department": "engineering"},
        ]

    captured_prompt = {}

    def capturing_llm_call(prompt: str) -> str:
        captured_prompt["value"] = prompt
        return "done"

    result = ask_over_data(
        question=f"Has {email} filed any tickets recently?",
        fetch_records_fn=fetch_records,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        llm_call=capturing_llm_call,
    )

    assert email not in result.masked_question
    assert email not in json.dumps(result.masked_records_sent)
    assert other_email not in json.dumps(result.masked_records_sent)
    assert email not in captured_prompt["value"]

    # extract the fake email from the masked question and confirm the same
    # fake also appears in the masked record sent for the SAME row
    import re
    fake_in_question = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,}", result.masked_question)
    assert fake_in_question is not None
    fake_email = fake_in_question.group(0)
    assert result.masked_records_sent[0]["contact_email"] == fake_email
    assert fake_email in captured_prompt["value"]

    # row-level scoping: the unrelated second row's email was NOT mentioned
    # in the question, so it must stay flatly redacted, not get realistic-
    # fake fidelity just because it shares a column with the matched row.
    assert result.masked_records_sent[1]["contact_email"] == "[REDACTED]"

    decision = next(d for d in result.column_decisions if d.field == "contact_email")
    assert decision.treatment == "redacted"
    assert "1 of 2 row(s)" in decision.reason


def test_answer_is_unmasked_back_to_real_values(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    email = "bob@example.com"

    def fetch_records():
        return [{"contact_email": email}]

    def llm_call_that_echoes_the_fake(prompt: str) -> str:
        # simulate an LLM response that references the fake value it saw
        import re
        m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,}", prompt)
        fake = m.group(0)
        return f"The customer's email on file is {fake}."

    result = ask_over_data(
        question="What email is on file?",
        fetch_records_fn=fetch_records,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        llm_call=llm_call_that_echoes_the_fake,
    )

    assert email in result.answer
    assert "The customer's email on file is" in result.answer


def test_fetch_failure_does_not_crash_pipeline(vault_manager, override_store):
    session_id = str(uuid.uuid4())

    def failing_fetch():
        raise ConnectionError("db unreachable")

    result = ask_over_data(
        question="How many rows are there?",
        fetch_records_fn=failing_fetch,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        llm_call=_echo_llm_call,
    )
    assert result.masked_records_sent == []
    assert result.column_decisions == []


def test_missing_client_for_anthropic_provider_raises_pipeline_error(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    with pytest.raises(PipelineError):
        ask_over_data(
            question="anything",
            fetch_records_fn=lambda: [],
            session_id=session_id,
            override_store=override_store,
            vault_manager=vault_manager,
            provider="anthropic",
            client=None,
        )


def test_never_mask_field_unaffected_by_value_mentioned_in_question(vault_manager, tmp_path):
    # A field already covered by a never_mask override is kept real
    # regardless of automated classification (Phase 1/2/3 behavior). The
    # row-level "value mentioned in question" signal (added in Phase 4)
    # must not change that in any way -- confirm the override path is
    # still exactly as untouched as before this signal existed.
    session_id = str(uuid.uuid4())
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps({"always_mask": {"field_names": []}, "never_mask": {"field_names": ["customer_id"]}})
    )
    store = OverrideStore(path=path)

    matched_id = "CUST-4471"

    def fetch_records():
        return [
            {"customer_id": matched_id, "notes": "called about billing"},
            {"customer_id": "CUST-9982", "notes": "asked for a refund"},
        ]

    result = ask_over_data(
        question=f"What did customer {matched_id} contact us about?",
        fetch_records_fn=fetch_records,
        session_id=session_id,
        override_store=store,
        vault_manager=vault_manager,
        llm_call=_echo_llm_call,
    )

    decision = next(d for d in result.column_decisions if d.field == "customer_id")
    assert decision.treatment == "override_never_mask"
    # no row-override note should be attached -- never_mask fields never
    # reach TREATMENT_REDACTED, so row_overrides has nothing to act on
    assert "row(s) in this batch" not in decision.reason

    # both rows keep their real customer_id, matched row included
    assert result.masked_records_sent[0]["customer_id"] == matched_id
    assert result.masked_records_sent[1]["customer_id"] == "CUST-9982"

    # notes column (default_masked, not mentioned, no override) still
    # redacts as usual -- proving the never_mask path didn't leak into or
    # get affected by unrelated masking decisions in the same batch
    notes_decision = next(d for d in result.column_decisions if d.field == "notes")
    assert notes_decision.treatment == "redacted"
    assert result.masked_records_sent[0]["notes"] == "[REDACTED]"


def test_query_aware_redaction_applied_within_pipeline(vault_manager, override_store):
    session_id = str(uuid.uuid4())

    def fetch_records():
        rows = []
        for i in range(10):
            rows.append(
                {
                    "employee_name": f"Person {i}",
                    "department": ["engineering", "sales"][i % 2],
                }
            )
        return rows

    result = ask_over_data(
        question="What is the department breakdown of employees?",
        fetch_records_fn=fetch_records,
        session_id=session_id,
        override_store=override_store,
        vault_manager=vault_manager,
        llm_call=_echo_llm_call,
    )

    name_decision = next(d for d in result.column_decisions if d.field == "employee_name")
    dept_decision = next(d for d in result.column_decisions if d.field == "department")
    assert name_decision.treatment == "redacted"
    assert dept_decision.treatment == "enum_kept"
    for row in result.masked_records_sent:
        assert row["employee_name"] == "[REDACTED]"
        assert row["department"] in ("engineering", "sales")


def test_audit_log_wired_end_to_end_through_ask_over_data(tmp_path, vault_manager, tmp_path_factory):
    override_path = tmp_path / "overrides.json"
    override_path.write_text(
        json.dumps({"always_mask": {"field_names": ["employee_id"]}, "never_mask": {"field_names": []}})
    )
    store = OverrideStore(path=override_path)
    audit_log = AuditLog(path=tmp_path / "audit.enc", key_path=tmp_path / "audit.key")
    session_id = str(uuid.uuid4())

    def fetch_records():
        return [{"employee_id": "EMP-1", "notes": "flagged for review"}]

    result = ask_over_data(
        question="Contact bob@example.com about this",
        fetch_records_fn=fetch_records,
        session_id=session_id,
        override_store=store,
        vault_manager=vault_manager,
        llm_call=_echo_llm_call,
        audit_log=audit_log,
    )

    entries = audit_log.get_entries(request_id=result.request_id)
    by_field = {e["field"]: e for e in entries}

    # regex-detected email from the question
    assert by_field["EMAIL"]["source"] == "prompt_text"
    assert by_field["EMAIL"]["decision"] == "masked"
    assert by_field["EMAIL"]["layer"] == "regex"

    # override-forced masking of employee_id
    assert by_field["employee_id"]["source"] == "db_record"
    assert by_field["employee_id"]["decision"] == "masked"
    assert "always_mask" in by_field["employee_id"]["reason"]

    # notes: masked-by-default, not mentioned in the question -> redacted
    assert by_field["notes"]["decision"] == "redacted"

    # never a real or fake value anywhere in the log
    dumped = json.dumps(entries)
    assert "bob@example.com" not in dumped
    assert "EMP-1" not in dumped
    assert "flagged for review" not in dumped
