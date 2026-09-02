"""Tests for the `decoy` CLI entry point declared in pyproject.toml
(decoy.cli:main). Runs the CLI's own argument parser/dispatch directly
(no subprocess) against a real cwd-scoped .decoy/ directory, since every
default_store/default_audit_log/default_manager in this project resolves
paths relative to the current working directory.
"""

import json
import os

import pytest

from decoy.cli import main


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # each of the module-level get_default_* singletons must be reset so
    # a previous test's cwd-bound instance isn't reused across tests
    import decoy.audit_log as audit_log_module
    import decoy.overrides as overrides_module
    import decoy.vault as vault_module

    audit_log_module._default_audit_log = None
    overrides_module._default_store = None
    vault_module._default_manager = None
    yield tmp_path


def test_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_audit_list_empty(capsys):
    assert main(["audit", "list"]) == 0
    assert "No audit entries recorded" in capsys.readouterr().out


def test_audit_list_json_and_text(capsys):
    from decoy.audit_log import get_default_audit_log

    get_default_audit_log().log_decision("req-1", "s1", "prompt_text", "EMAIL", "masked", "matched EMAIL regex", "regex")

    assert main(["audit", "list"]) == 0
    text_out = capsys.readouterr().out
    assert "EMAIL" in text_out and "masked" in text_out

    assert main(["audit", "list", "--json"]) == 0
    json_out = capsys.readouterr().out
    parsed = json.loads(json_out)
    assert len(parsed) == 1
    assert parsed[0]["field"] == "EMAIL"


def test_audit_clear_session_keeps_overrides(capsys, tmp_path):
    from decoy.audit_log import get_default_audit_log
    from decoy.overrides import get_default_store

    get_default_audit_log().log_decision("req-1", "session-a", "prompt_text", "EMAIL", "masked", "r", "regex")
    (tmp_path / ".decoy").mkdir(exist_ok=True)
    (tmp_path / ".decoy" / "overrides.json").write_text(
        json.dumps({"always_mask": {"field_names": ["employee_id"]}, "never_mask": {"field_names": []}})
    )
    get_default_store().reload(force=True)

    assert main(["audit", "clear", "--session", "session-a"]) == 0
    out = capsys.readouterr().out
    assert "Cleared 1 audit entr" in out
    assert "Overrides were NOT touched" in out

    assert get_default_audit_log().get_entries(session_id="session-a") == []
    assert get_default_store().is_always_mask_field("employee_id")  # kept


def test_audit_clear_all_removes_overrides_too(capsys, tmp_path):
    from decoy.audit_log import get_default_audit_log
    from decoy.overrides import get_default_store

    get_default_audit_log().log_decision("req-1", "s1", "prompt_text", "EMAIL", "masked", "r", "regex")
    (tmp_path / ".decoy").mkdir(exist_ok=True)
    (tmp_path / ".decoy" / "overrides.json").write_text(
        json.dumps({"always_mask": {"field_names": ["employee_id"]}, "never_mask": {"field_names": []}})
    )
    get_default_store().reload(force=True)
    assert get_default_store().is_always_mask_field("employee_id")

    assert main(["audit", "clear-all"]) == 0
    out = capsys.readouterr().out
    assert "all overrides" in out

    assert get_default_audit_log().get_entries() == []
    get_default_store().reload(force=True)
    assert not get_default_store().is_always_mask_field("employee_id")


def test_overrides_show(capsys, tmp_path):
    (tmp_path / ".decoy").mkdir(exist_ok=True)
    (tmp_path / ".decoy" / "overrides.json").write_text(
        json.dumps({"always_mask": {"field_names": ["ssn"]}, "never_mask": {"field_names": ["id"]}})
    )
    assert main(["overrides", "show"]) == 0
    out = capsys.readouterr().out
    assert "ssn" in out
    assert "id" in out


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        main(["not-a-real-command"])


def test_error_in_handler_returns_1_not_a_stack_trace(capsys, monkeypatch):
    def _raise(_args):
        raise RuntimeError("boom")

    from decoy import cli

    monkeypatch.setattr(cli, "_cmd_version", _raise)
    assert main(["version"]) == 1
    err = capsys.readouterr().err
    assert "decoy: error: boom" in err
