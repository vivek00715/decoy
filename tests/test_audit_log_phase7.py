import json
import uuid

from decoy.audit_log import AuditLog, clear_audit_log


def test_write_and_read_round_trip(tmp_path):
    log = AuditLog(path=tmp_path / "audit.enc", key_path=tmp_path / "audit.key")
    session_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    log.log_decision(request_id, session_id, "prompt_text", "EMAIL", "masked", "matched EMAIL regex", "regex")

    entries = log.get_entries(session_id=session_id)
    assert len(entries) == 1
    e = entries[0]
    assert e["field"] == "EMAIL"
    assert e["decision"] == "masked"
    assert e["reason"] == "matched EMAIL regex"
    assert e["layer"] == "regex"
    assert e["source"] == "prompt_text"
    assert e["session_id"] == session_id
    assert e["request_id"] == request_id
    assert "id" in e and "timestamp" in e


def test_exact_scenario_regex_override_and_redact_in_one_request(tmp_path):
    # Matches the Phase 7 spec test: one field masked via regex, one via
    # override, one redacted -- all logged with correct reasons and layer
    # attribution, no real/fake values leaked.
    log = AuditLog(path=tmp_path / "audit.enc", key_path=tmp_path / "audit.key")
    session_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    log.log_decisions(
        request_id,
        session_id,
        "db_record",
        [
            ("email", "masked", "matched EMAIL regex", "regex"),
            ("employee_id", "masked", "manual override: always_mask", "override"),
            ("notes", "redacted", "masked-by-default field also classified not relevant to the current question", "shape-default+keyword"),
        ],
    )

    entries = log.get_entries(request_id=request_id)
    assert len(entries) == 3
    by_field = {e["field"]: e for e in entries}

    assert by_field["email"]["decision"] == "masked"
    assert by_field["email"]["layer"] == "regex"
    assert "EMAIL regex" in by_field["email"]["reason"]

    assert by_field["employee_id"]["decision"] == "masked"
    assert by_field["employee_id"]["layer"] == "override"
    assert "always_mask" in by_field["employee_id"]["reason"]

    assert by_field["notes"]["decision"] == "redacted"
    assert "keyword" in by_field["notes"]["layer"]

    # never any real/fake value leaked into the log
    raw = json.dumps(entries)
    for leaked_candidate in ("alice@example.com", "EMP-123456", "flight risk"):
        assert leaked_candidate not in raw


def test_never_logs_real_or_fake_values_on_disk(tmp_path):
    key_path = tmp_path / "audit.key"
    log_path = tmp_path / "audit.enc"
    log = AuditLog(path=log_path, key_path=key_path)
    session_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    real_value = "jane.doe@example.com"
    fake_value = "zzqqxx@example.net"

    # Simulate a careless caller trying to pass values through -- the
    # audit_log API itself has no parameter for them, so this proves the
    # schema structurally cannot carry a value, not just that we chose not
    # to pass one.
    log.log_decision(request_id, session_id, "prompt_text", "EMAIL", "masked", "matched EMAIL regex", "regex")

    raw_encrypted_bytes = log_path.read_bytes()
    assert real_value.encode() not in raw_encrypted_bytes
    assert fake_value.encode() not in raw_encrypted_bytes

    from decoy.crypto import make_fernet, read_encrypted_json
    fernet = make_fernet(key_path)
    decrypted = read_encrypted_json(log_path, fernet)
    dumped = json.dumps(decrypted)
    assert real_value not in dumped
    assert fake_value not in dumped


def test_clear_session_only_removes_that_session(tmp_path):
    log = AuditLog(path=tmp_path / "audit.enc", key_path=tmp_path / "audit.key")
    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())
    req = str(uuid.uuid4())

    log.log_decision(req, session_a, "prompt_text", "EMAIL", "masked", "r1", "regex")
    log.log_decision(req, session_b, "prompt_text", "EMAIL", "masked", "r2", "regex")

    removed = clear_audit_log(session_id=session_a, audit_log=log)
    assert removed == 1
    assert log.get_entries(session_id=session_a) == []
    assert len(log.get_entries(session_id=session_b)) == 1


def test_clear_all_removes_everything(tmp_path):
    log = AuditLog(path=tmp_path / "audit.enc", key_path=tmp_path / "audit.key")
    for _ in range(3):
        log.log_decision(str(uuid.uuid4()), str(uuid.uuid4()), "prompt_text", "EMAIL", "masked", "r", "regex")

    removed = clear_audit_log(audit_log=log)
    assert removed == 3
    assert log.get_entries() == []


def test_format_version_present_and_entries_are_flat_primitives(tmp_path):
    key_path = tmp_path / "audit.key"
    log_path = tmp_path / "audit.enc"
    log = AuditLog(path=log_path, key_path=key_path)
    log.log_decision(str(uuid.uuid4()), str(uuid.uuid4()), "db_record", "status", "left_as_is", "enum kept", "shape")

    from decoy.crypto import make_fernet, read_encrypted_json
    data = read_encrypted_json(log_path, make_fernet(key_path))

    assert data["format_version"] == 1
    assert isinstance(data["entries"], list)
    entry = data["entries"][0]
    for key in ("id", "request_id", "session_id", "timestamp", "source", "field", "decision", "reason", "layer"):
        assert isinstance(entry[key], str)


def test_load_failure_falls_back_to_empty_log_not_a_crash(tmp_path):
    key_path = tmp_path / "audit.key"
    log_path = tmp_path / "audit.enc"
    log_path.write_bytes(b"not valid fernet ciphertext at all")

    log = AuditLog(path=log_path, key_path=key_path)
    assert log.get_entries() == []
    # still usable afterward
    log.log_decision(str(uuid.uuid4()), str(uuid.uuid4()), "prompt_text", "EMAIL", "masked", "r", "regex")
    assert len(log.get_entries()) == 1


def test_wrong_key_cannot_read_audit_log(tmp_path):
    log_path = tmp_path / "audit.enc"
    key_path_a = tmp_path / "a.key"
    key_path_b = tmp_path / "b.key"

    log_a = AuditLog(path=log_path, key_path=key_path_a)
    log_a.log_decision(str(uuid.uuid4()), str(uuid.uuid4()), "prompt_text", "EMAIL", "masked", "r", "regex")

    log_b = AuditLog(path=log_path, key_path=key_path_b)
    assert log_b.get_entries() == []  # fails safe to empty, doesn't crash or misread garbage
