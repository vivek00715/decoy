"""Shared local-encryption helpers for Decoy's on-disk state.

The vault (vault.py) and the audit log (audit_log.py) both persist
locally-encrypted JSON and share the SAME key file (.decoy/vault.key by
default, or DECOY_ENCRYPTION_KEY) -- they live under the same local trust
boundary, so there is one key for the user to manage/back up/rotate, not
two. Encryption is Fernet (AES-128-CBC + HMAC), matching what was already
used for the vault before this was extracted into a shared module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger("crypto")

DEFAULT_KEY_PATH = Path(".decoy") / "vault.key"


def load_or_create_key(key_path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Return the local encryption key: DECOY_ENCRYPTION_KEY env var if
    set, else the key file at `key_path` if it exists, else a freshly
    generated key written to `key_path` (0600 permissions)."""
    env_key = os.environ.get("DECOY_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode("utf-8")

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
