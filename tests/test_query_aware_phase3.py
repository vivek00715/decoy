import json
from types import SimpleNamespace

import pytest

from decoy.overrides import OverrideStore
from decoy.query_aware import classify_relevant_columns


@pytest.fixture
def override_store(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps(
            {
                "always_mask": {"field_names": ["employee_id"]},
                "never_mask": {"field_names": ["status"]},
            }
        )
    )
    return OverrideStore(path=path)


COLUMNS = ["name", "email", "ssn", "salary", "department", "status", "employee_id"]
QUESTION = "What is the average salary by department?"


def test_keyword_mode_marks_salary_and_department_relevant(override_store):
    result = classify_relevant_columns(QUESTION, COLUMNS, override_store=override_store)

    assert result["salary"].relevant is True
    assert result["department"].relevant is True
    assert result["name"].relevant is False
    assert result["email"].relevant is False
    assert result["ssn"].relevant is False
    assert all(r.layer in ("keyword", "override") for r in result.values())


def test_never_mask_override_forces_relevant_kept(override_store):
    result = classify_relevant_columns(QUESTION, COLUMNS, override_store=override_store)
    assert result["status"].relevant is True
    assert result["status"].layer == "override"
    assert "never_mask" in result["status"].reason


def test_always_mask_override_does_not_dictate_relevance(override_store):
    # employee_id has no keyword overlap with the salary/department question,
    # so relevance should still be decided by the automated layer (False),
    # even though always_mask separately guarantees it gets masked.
    result = classify_relevant_columns(QUESTION, COLUMNS, override_store=override_store)
    assert result["employee_id"].relevant is False
    assert result["employee_id"].layer == "keyword"


def test_unmapped_column_defaults_to_not_relevant(override_store):
    result = classify_relevant_columns(
        "How many rows are there?", ["mystery_col_xyz"], override_store=override_store
    )
    assert result["mystery_col_xyz"].relevant is False


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self._response_text = response_text
        self.messages = SimpleNamespace(create=self._create)
        self.calls = []

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[_FakeTextBlock(self._response_text)])


def test_llm_assisted_mode_with_mocked_client(override_store):
    fake_client = _FakeAnthropicClient(
        json.dumps({"relevant_columns": ["salary", "department"]})
    )
    result = classify_relevant_columns(
        QUESTION, COLUMNS, client=fake_client, override_store=override_store
    )
    assert result["salary"].relevant is True
    assert result["department"].relevant is True
    assert result["name"].relevant is False
    assert result["ssn"].relevant is False
    assert result["salary"].layer == "llm"
    assert len(fake_client.calls) == 1
    # only column names and the question are sent -- never row values
    sent_prompt = fake_client.calls[0]["messages"][0]["content"]
    assert QUESTION in sent_prompt
    for col in COLUMNS:
        assert col in sent_prompt


def test_llm_assisted_mode_falls_back_safely_on_malformed_response(override_store):
    fake_client = _FakeAnthropicClient("not valid json at all")
    result = classify_relevant_columns(
        QUESTION, COLUMNS, client=fake_client, override_store=override_store
    )
    # falls back to keyword classification rather than crashing or failing open
    assert result["salary"].relevant is True
    assert result["ssn"].relevant is False
    assert result["salary"].layer == "keyword-fallback"


def test_llm_assisted_mode_ignores_hallucinated_column_names(override_store):
    fake_client = _FakeAnthropicClient(
        json.dumps({"relevant_columns": ["salary", "not_a_real_column"]})
    )
    result = classify_relevant_columns(
        QUESTION, COLUMNS, client=fake_client, override_store=override_store
    )
    assert set(result.keys()) == set(COLUMNS)
    assert result["salary"].relevant is True


def test_llm_assisted_mode_masks_question_before_sending(override_store):
    # The question may itself contain PII (e.g. a name/email typed by the
    # user). classify_relevant_columns must mask it via masker.py before
    # it ever reaches the LLM client -- same rule as any other prompt text.
    question_with_pii = (
        "What is the salary for jane.doe@example.com in the engineering department?"
    )
    fake_client = _FakeAnthropicClient(
        json.dumps({"relevant_columns": ["salary", "department"]})
    )
    session_id = "phase3-pii-question-test"
    result = classify_relevant_columns(
        question_with_pii,
        COLUMNS,
        client=fake_client,
        override_store=override_store,
        session_id=session_id,
    )

    sent_prompt = fake_client.calls[0]["messages"][0]["content"]
    assert "jane.doe@example.com" not in sent_prompt
    # non-PII keywords needed for classification must survive masking
    assert "salary" in sent_prompt
    assert "department" in sent_prompt
    # classification result should be unaffected by masking the name/email
    assert result["salary"].relevant is True
    assert result["department"].relevant is True

    # round-trip sanity: the masked email is recoverable via the same
    # shared vault/session, proving this used the real masking pipeline
    # rather than e.g. silently stripping the email.
    from decoy.masker import Masker
    masker = Masker(session_id=session_id, override_store=override_store)
    assert masker.vault.lookup_original(
        masker.vault.lookup_fake("jane.doe@example.com")
    ) == "jane.doe@example.com"


def test_llm_assisted_mode_raising_client_falls_back(override_store):
    class _RaisingClient:
        def __init__(self):
            def _raise(**kwargs):
                raise RuntimeError("network error")
            self.messages = SimpleNamespace(create=_raise)

    result = classify_relevant_columns(
        QUESTION, COLUMNS, client=_RaisingClient(), override_store=override_store
    )
    assert result["salary"].relevant is True
    assert result["salary"].layer == "keyword-fallback"
