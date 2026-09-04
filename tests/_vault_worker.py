"""Standalone worker process for test_vault_cross_process.py.

Deliberately a real separate `python` invocation (via subprocess), not a
`threading`/`multiprocessing.Process` simulation -- the whole point of
these tests is to catch a race that only exists ACROSS OS processes (see
vault.py's module docstring), and threads within one process share the
same VaultManager singleton by construction, which would make the test
pass even with the old, broken save-only-at-batch-end code.

Usage: python _vault_worker.py <vault_path> <key_path> <session_id> <original> <candidate_fake> <out_path>

Mints (or reuses) a fake for `original` in `session_id`'s vault, using
`candidate_fake` as this process's own proposed value -- different worker
processes in the same test run are given DIFFERENT candidate_fake values
specifically so that, if the cross-process mint race were NOT fixed, two
concurrently-run workers could plausibly end up minting two different
fakes for the same `original` (each winning its own local race before
ever seeing the other's write). Writes the ACTUALLY RETURNED fake to
`out_path` for the test to compare across all workers afterward.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from decoy.vault import VaultManager  # noqa: E402

vault_path, key_path, session_id, original, candidate_fake, out_path = sys.argv[1:7]

manager = VaultManager(persist=True, vault_path=Path(vault_path), key_path=Path(key_path))
vault = manager.get_vault(session_id)
fake = vault.get_or_create_fake(original, lambda: candidate_fake)

Path(out_path).write_text(fake, encoding="utf-8")
