"""Standalone worker process for test_audit_log_cross_process.py -- see
that file and vault.py's module docstring for why real subprocesses, not
threads, are used to exercise this.

Usage: python _audit_log_worker.py <path> <key_path> <tag>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from decoy.audit_log import AuditLog  # noqa: E402

path, key_path, tag = sys.argv[1:4]

log = AuditLog(path=path, key_path=key_path)
log.log_decision(
    request_id=f"req-{tag}",
    session_id="shared-session",
    source="prompt_text",
    field=f"FIELD_{tag}",
    decision="masked",
    reason="cross-process test entry",
    layer="regex",
)
