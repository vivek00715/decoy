"""Real two-(or-more)-OS-process tests for VaultManager's persisted
cross-process behavior -- see vault.py's module docstring for the two
bugs these prove are fixed: (1) a blind whole-file overwrite on save
silently losing another process's session, and (2) two processes
independently minting two DIFFERENT fakes for the same real value because
the mint decision only ever consulted local in-memory state.

Every test here launches REAL separate `python` subprocesses (see
_vault_worker.py) rather than threads or multiprocessing.Process --
threads within one process already share the same VaultManager instance
by construction and would pass even against the old, broken code; the
whole point is to exercise the actual cross-process file-lock path.
"""

import subprocess
import sys
import uuid
from pathlib import Path

from decoy.vault import VaultManager

WORKER = str(Path(__file__).resolve().parent / "_vault_worker.py")


def _run_worker(vault_path, key_path, session_id, original, candidate_fake, out_path):
    return subprocess.Popen(
        [sys.executable, WORKER, str(vault_path), str(key_path), session_id, original, candidate_fake, str(out_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_concurrent_processes_minting_the_same_value_converge_on_one_fake(tmp_path):
    """Bug (2): without the fix, this is a real (if probabilistic) race --
    N processes started together, each proposing its OWN distinct
    candidate fake for the identical real value in the SAME session. If
    the mint check only consults local memory (the old behavior), more
    than one process can decide "not present yet, mint mine" before any
    of them has saved -- this test would then observe more than one
    distinct fake across the N output files. With the fix, every mint
    goes through one file-locked reload-check-mint-save critical
    section, so exactly one candidate wins and every other process
    observes and reuses it.
    """
    vault_path = tmp_path / "vault.enc"
    key_path = tmp_path / "vault.key"
    session_id = str(uuid.uuid4())
    original = "alice@example.com"

    n = 12
    out_paths = [tmp_path / f"out_{i}.txt" for i in range(n)]
    candidates = [f"candidate-{i}@example.net" for i in range(n)]

    procs = [
        _run_worker(vault_path, key_path, session_id, original, candidates[i], out_paths[i]) for i in range(n)
    ]
    results = [p.communicate(timeout=30) for p in procs]
    for i, (p, (out, err)) in enumerate(zip(procs, results)):
        assert p.returncode == 0, f"worker {i} failed: stdout={out!r} stderr={err!r}"

    fakes = [p.read_text(encoding="utf-8") for p in out_paths]
    unique_fakes = set(fakes)
    assert len(unique_fakes) == 1, (
        f"expected all {n} concurrent processes to converge on exactly one fake for the same "
        f"original value, got {len(unique_fakes)} distinct fakes: {unique_fakes} -- this means the "
        f"cross-process mint race (vault.py docstring bug 2) is NOT actually closed"
    )

    # And the winning fake is genuinely one of the candidates that was
    # actually proposed, not something else entirely.
    assert unique_fakes.issubset(set(candidates))

    # A fresh VaultManager reading the final on-disk state agrees.
    final_manager = VaultManager(persist=True, vault_path=vault_path, key_path=key_path)
    assert final_manager.get_vault(session_id).lookup_fake(original) == fakes[0]


def test_concurrent_processes_on_different_sessions_do_not_clobber_each_other(tmp_path):
    """Bug (1): without the fix, whichever process saves LAST overwrites
    the whole file with only its own in-memory sessions, silently
    dropping any other session it never loaded. Two processes here each
    mint into their OWN distinct session; both sessions must survive on
    disk regardless of save ordering.
    """
    vault_path = tmp_path / "vault.enc"
    key_path = tmp_path / "vault.key"
    session_a = f"session-a-{uuid.uuid4()}"
    session_b = f"session-b-{uuid.uuid4()}"

    out_a = tmp_path / "out_a.txt"
    out_b = tmp_path / "out_b.txt"

    proc_a = _run_worker(vault_path, key_path, session_a, "bob@example.com", "fake-bob@example.net", out_a)
    proc_b = _run_worker(vault_path, key_path, session_b, "carol@example.com", "fake-carol@example.net", out_b)

    out_a_result, err_a = proc_a.communicate(timeout=30)
    out_b_result, err_b = proc_b.communicate(timeout=30)
    assert proc_a.returncode == 0, f"worker A failed: {out_a_result!r} {err_a!r}"
    assert proc_b.returncode == 0, f"worker B failed: {out_b_result!r} {err_b!r}"

    final_manager = VaultManager(persist=True, vault_path=vault_path, key_path=key_path)
    assert final_manager.get_vault(session_a).lookup_fake("bob@example.com") == "fake-bob@example.net"
    assert final_manager.get_vault(session_b).lookup_fake("carol@example.com") == "fake-carol@example.net"
