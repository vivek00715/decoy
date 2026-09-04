"""Shared local-encryption helpers for Decoy's on-disk state.

The vault (vault.py) and the audit log (audit_log.py) both persist
locally-encrypted JSON and share the SAME key file (.decoy/vault.key by
default, or DECOY_ENCRYPTION_KEY) -- they live under the same local trust
boundary, so there is one key for the user to manage/back up/rotate, not
two. Encryption is Fernet (AES-128-CBC + HMAC), matching what was already
used for the vault before this was extracted into a shared module.

CONFIRMED RACE (found empirically while building cross-process vault
persistence for the chat proxy, before it could bite silently in
production): `load_or_create_key`'s "generate if missing" branch had NO
locking. Ten real concurrent processes racing to create the same key
file for the first time produced FIVE different keys -- most of those
processes wrote their own key over each other's, and any process still
holding one of the four losing keys in memory can no longer decrypt what
the winner wrote to disk. Reproduced with:

    for i in $(seq 1 10); do python3 -c "
    from decoy.crypto import load_or_create_key; print(load_or_create_key())" & done; wait

This is worse than a lost update: it looks like silent, confusing data
loss on a cold start (an existing session's vault/audit-log entries
becoming permanently undecryptable to a process that lost the race), not
just a stale value. Fixed the same way as vault.py's cross-process
save/mint race: a `filelock.FileLock` (Windows- and POSIX-safe, unlike
`fcntl`) guards the entire read-if-exists-else-generate-and-write
decision so only one process ever actually generates a key for a given
path, and every other concurrent caller reads back that same file
instead of racing to write its own. Permanent regression test:
tests/test_crypto_key_cross_process.py (real subprocesses, not threads).

filelock's Windows (`msvcrt`) path specifically has only ever been
exercised on macOS/POSIX in the environment that wrote this fix -- see
README.md's "Verifying filelock on Windows" section for the exact
command to confirm this (and the vault/audit-log equivalents) on a real
Windows machine before trusting it there. This is disclosed explicitly
rather than assumed correct from filelock's own cross-platform claims.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from filelock import FileLock

from .logging_config import get_logger

logger = get_logger("crypto")

DEFAULT_KEY_PATH = Path(".decoy") / "vault.key"

# Matches vault.py's FILE_LOCK_TIMEOUT_SECONDS -- kept as a separate
# constant (not imported from vault.py) since crypto.py is the lower-level
# module (vault.py imports FROM crypto.py, not the reverse) and key
# creation happens well before any vault-specific state exists.
KEY_LOCK_TIMEOUT_SECONDS = 10.0


def load_or_create_key(key_path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Return the local encryption key: DECOY_ENCRYPTION_KEY env var if
    set, else the key file at `key_path` if it exists, else a freshly
    generated key written to `key_path` (0600 permissions).

    The exists-check/read and the generate/write are done inside one
    `FileLock` critical section (see this module's docstring for the
    confirmed race this closes) -- every caller, including the one that
    ends up generating the key, goes through the same lock, so a caller
    that loses the "who generates it" race simply reads back the winner's
    key instead of racing to write its own.
    """
    env_key = os.environ.get("DECOY_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode("utf-8")

    lock = FileLock(str(key_path) + ".lock", timeout=KEY_LOCK_TIMEOUT_SECONDS)
    try:
        with lock:
            if key_path.exists():
                return key_path.read_bytes()

            from cryptography.fernet import Fernet

            key = Fernet.generate_key()
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(key)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                logger.warning("could not set 0600 permissions on %s", key_path)
            logger.info("generated new local encryption key at %s", key_path)
            return key
    except TimeoutError:
        # Fail toward NOT silently generating a second, divergent key --
        # if the lock can't be acquired, either read whatever already
        # exists (another process is presumably mid-generation and will
        # finish soon) or raise, rather than proceeding unlocked.
        if key_path.exists():
            return key_path.read_bytes()
        raise VaultKeyLockTimeoutError(
            f"could not acquire the key-creation lock for {key_path} within "
            f"{KEY_LOCK_TIMEOUT_SECONDS:.0f}s, and no key exists yet to fall back to reading"
        )


class VaultKeyLockTimeoutError(RuntimeError):
    """Raised when the cross-process key-creation lock could not be
    acquired in time AND no key was already on disk to read instead."""


def make_fernet(key_path: Path = DEFAULT_KEY_PATH):
    from cryptography.fernet import Fernet

    key = load_or_create_key(key_path)
    return Fernet(key)


def write_encrypted_json(path: Path, data: Any, fernet: Any) -> None:
    """Encrypt `data` as JSON and write it atomically (write-then-rename,
    so a reader never sees a half-written file mid-save)."""
    payload = json.dumps(data).encode("utf-8")
    encrypted = fernet.encrypt(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(encrypted)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    tmp_path.replace(path)


def read_encrypted_json(path: Path, fernet: Any) -> Any:
    encrypted = path.read_bytes()
    decrypted = fernet.decrypt(encrypted)
    return json.loads(decrypted.decode("utf-8"))
