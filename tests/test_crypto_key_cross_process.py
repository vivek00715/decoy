"""Real multi-OS-process regression test for the key-creation race found
empirically while building the chat proxy (see crypto.py's module
docstring): `load_or_create_key`'s generate-if-missing branch had no
locking, so 10 concurrent processes racing to create the same key file
for the first time produced 5 different keys -- confirmed by direct
repro before the fix. This must never silently reappear.

This is also THE test to run on a platform this project has not been
verified on directly (see this repo's own disclosed gap: `filelock`'s
Windows path, via `msvcrt`, has only ever been exercised on macOS/POSIX
in the environment that wrote this code). Running just this file's test
(and test_vault_cross_process.py / test_audit_log_cross_process.py,
which exercise the same filelock machinery for the vault and audit log
specifically) on a real Windows machine is the concrete verification
step for that gap -- see README.md's "Verifying filelock on Windows"
section for the exact command.
"""

import subprocess
import sys
from pathlib import Path

WORKER = str(Path(__file__).resolve().parent / "_key_race_worker.py")


def test_concurrent_processes_creating_the_same_key_converge_on_one_key(tmp_path):
    key_path = tmp_path / "vault.key"

    n = 10
    out_paths = [tmp_path / f"key_out_{i}.bin" for i in range(n)]
    procs = [
        subprocess.Popen([sys.executable, WORKER, str(key_path), str(out_paths[i])], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for i in range(n)
    ]
    results = [p.communicate(timeout=30) for p in procs]
    for i, (p, (out, err)) in enumerate(zip(procs, results)):
        assert p.returncode == 0, f"worker {i} failed: stdout={out!r} stderr={err!r}"

    keys = [p.read_bytes() for p in out_paths]
    unique_keys = set(keys)
    assert len(unique_keys) == 1, (
        f"expected all {n} concurrent processes to converge on exactly one generated key, got "
        f"{len(unique_keys)} distinct keys -- this means the cross-process key-creation race "
        f"(crypto.py docstring) is NOT actually closed on this platform"
    )
