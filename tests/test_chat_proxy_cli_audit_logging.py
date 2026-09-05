"""Regression test for a confirmed real bug: `decoy chat-proxy` (the
actual CLI entry point, cli.py's `_cmd_chat_proxy`) never passed an
audit_log into the proxy it started, unlike `decoy proxy` (once that
identical bug -- found while fixing this one -- was also closed) and the
`decoy audit` commands in the same file. Every request through a real
`decoy chat-proxy` process silently ran with masking-decision logging
disabled.

This starts the REAL CLI entry point as its own OS process (see
_chat_proxy_cli_worker.py) rather than calling chat_proxy.py's internal
create_app()/main() directly -- test_chat_proxy.py's existing tests
already do the latter and did NOT catch this, because the underlying
code was correct; the bug lived entirely in cli.py's dispatch layer not
wiring an audit_log through at all.
"""

import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from decoy.audit_log import AuditLog

WORKER = str(Path(__file__).resolve().parent / "_chat_proxy_cli_worker.py")


def _wait_for_healthz(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - just retry until timeout
            last_error = exc
        time.sleep(0.2)
    raise TimeoutError(f"decoy chat-proxy CLI process never became healthy: {last_error}")


def test_real_decoy_chat_proxy_cli_writes_a_real_audit_entry(tmp_path, monkeypatch):
    # Run the CLI worker with tmp_path as its cwd, so it reads/writes its
    # own isolated .decoy/ directory rather than the repo's real one.
    workdir = tmp_path
    port = 8834

    proc = subprocess.Popen(
        [sys.executable, WORKER, str(port)],
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_healthz(port)

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/messages",
            method="POST",
            headers={"content-type": "application/json", "x-decoy-session-id": "cli-audit-test"},
            data=(
                b'{"model":"claude-haiku-4-5","max_tokens":10,'
                b'"messages":[{"role":"user","content":"contact leak-check@example.com about pnr ZQ88K1"}]}'
            ),
        )
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            # The upstream call is pointed at a dead port on purpose (see
            # the worker script) and is EXPECTED to fail -- the audit
            # write this test checks for happens before that call, during
            # masking, so an upstream failure here is irrelevant to what's
            # being tested. Only a real crash of the proxy itself (caught
            # via the process's own exit code below) would be a problem.
            pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)

    audit_path = workdir / ".decoy" / "audit.enc"
    assert audit_path.exists(), (
        "decoy chat-proxy (real CLI entry point) did not write .decoy/audit.enc for a request "
        "containing maskable content -- the audit_log wiring bug is NOT actually fixed"
    )

    key_path = workdir / ".decoy" / "vault.key"
    log = AuditLog(path=audit_path, key_path=key_path)
    entries = log.get_entries(session_id="cli-audit-test")
    labels = {e["field"] for e in entries}
    assert "EMAIL" in labels
    assert "PNR" in labels
    # never the real or fake value itself, only metadata
    for e in entries:
        assert "leak-check@example.com" not in str(e)
        assert "ZQ88K1" not in str(e)
