"""Real multi-OS-process regression test for the audit log clobber race
found empirically while building the chat proxy (see audit_log.py's
module docstring): `log_decision`/`log_decisions` used to save without
ever reloading from disk first, so N concurrent processes each logging
one entry to the same audit log lost entries to whichever process's
whole-file overwrite landed last -- confirmed as 10 processes -> 5
recorded entries before the fix. This must never silently reappear.
"""

import subprocess
import sys
from pathlib import Path

from decoy.audit_log import AuditLog

WORKER = str(Path(__file__).resolve().parent / "_audit_log_worker.py")


def test_concurrent_processes_logging_to_the_same_audit_log_lose_no_entries(tmp_path):
    path = tmp_path / "audit.enc"
    key_path = tmp_path / "vault.key"

    n = 10
    procs = [
        subprocess.Popen(
            [sys.executable, WORKER, str(path), str(key_path), str(i)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(n)
    ]
    results = [p.communicate(timeout=30) for p in procs]
    for i, (p, (out, err)) in enumerate(zip(procs, results)):
        assert p.returncode == 0, f"worker {i} failed: stdout={out!r} stderr={err!r}"

    log = AuditLog(path=path, key_path=key_path)
    entries = log.get_entries(session_id="shared-session")
    fields = {e["field"] for e in entries}
    expected = {f"FIELD_{i}" for i in range(n)}

    assert fields == expected, (
        f"expected all {n} concurrently-logged entries to survive, got {len(entries)} "
        f"({sorted(fields)}) -- missing: {expected - fields} -- this means the audit log "
        f"cross-process clobber race is NOT actually closed"
    )
