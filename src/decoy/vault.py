"""The vault: session-scoped original <-> fake value mappings.

One vault per session_id. All masking paths (free-text prompts in
masker.py, DB records in record_masker.py) share the same vault for a
given session_id, so the same real value always gets the same fake value
everywhere -- that's what lets Phase 4 unify prompt-text and DB-record
masking.

Persistence is optional and off by default (in-memory only). When
enabled (DECOY_VAULT_PERSIST=true), the vault is encrypted at rest with
Fernet (AES-128-CBC + HMAC) using a key from DECOY_ENCRYPTION_KEY or a
generated key file at .decoy/vault.key (created with 0600 permissions).
Never mask/unmask real values in a way that could leak them into logs --
this module raises plain exceptions on I/O failure but never logs vault
contents.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Optional

from .crypto import DEFAULT_KEY_PATH, make_fernet, read_encrypted_json, write_encrypted_json
from .logging_config import get_logger

logger = get_logger("vault")

DEFAULT_VAULT_PATH = Path(".decoy") / "vault.enc"
MAX_FAKE_RETRIES = 25


class VaultPersistenceError(RuntimeError):
    """Raised when the vault cannot be loaded or saved to disk."""


class Vault:
    """Holds the bidirectional mapping for a single session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._original_to_fake: dict[str, str] = {}
        self._fake_to_original: dict[str, str] = {}
        self._used_fakes: set[str] = set()
        self._lock = threading.RLock()

    def get_or_create_fake(self, original: str, generator: Callable[[], str]) -> str:
        """Return the existing fake for `original`, or mint a new one.

        `generator` is called (and re-called on collision) to produce a
        candidate fake value; collision avoidance ensures no two distinct
        original values ever map to the same fake within a session.
        """
        with self._lock:
            existing = self._original_to_fake.get(original)
            if existing is not None:
                return existing

            for _ in range(MAX_FAKE_RETRIES):
                candidate = generator()
                if candidate == original:
                    continue
                if candidate not in self._used_fakes:
                    self._original_to_fake[original] = candidate
                    self._fake_to_original[candidate] = original
                    self._used_fakes.add(candidate)
                    return candidate

            # Exhausted retries: disambiguate deterministically rather than
            # fail open with a colliding (and thus ambiguous) fake value.
            base = generator()
            candidate = f"{base}-{len(self._used_fakes)}"
            self._original_to_fake[original] = candidate
            self._fake_to_original[candidate] = original
            self._used_fakes.add(candidate)
            return candidate

    def lookup_fake(self, original: str) -> Optional[str]:
        with self._lock:
            return self._original_to_fake.get(original)

    def lookup_original(self, fake: str) -> Optional[str]:
        with self._lock:
            return self._fake_to_original.get(fake)

    def all_fakes(self) -> list[str]:
        """Fake values currently known, longest first (for safe substring
        replacement during unmask -- longer fakes are replaced before
        their substrings could accidentally match a shorter one)."""
        with self._lock:
            return sorted(self._fake_to_original.keys(), key=len, reverse=True)

    def to_dict(self) -> dict:
        with self._lock:
            return dict(self._original_to_fake)

    def clear(self) -> None:
        with self._lock:
            self._original_to_fake.clear()
            self._fake_to_original.clear()
            self._used_fakes.clear()


class VaultManager:
    """Process-wide registry of vaults, keyed by session_id.

    Thread-safe: a lock guards session creation/lookup/clear; each Vault
    guards its own mapping independently so concurrent requests against
    *different* sessions never contend, and concurrent requests against
    the *same* session are serialized safely.
    """

    def __init__(
        self,
        persist: Optional[bool] = None,
        vault_path: Optional[str | Path] = None,
        key_path: Optional[str | Path] = None,
    ):
        self._lock = threading.RLock()
        self._vaults: dict[str, Vault] = {}
        self.persist = (
            persist
            if persist is not None
            else os.environ.get("DECOY_VAULT_PERSIST", "false").lower() == "true"
        )
        self.vault_path = Path(vault_path or DEFAULT_VAULT_PATH)
        self.key_path = Path(key_path or DEFAULT_KEY_PATH)
        self._fernet = None
        if self.persist:
            self._init_crypto()
            self._load_from_disk()

    def _init_crypto(self) -> None:
        try:
            self._fernet = make_fernet(self.key_path)
        except Exception as exc:  # noqa: BLE001 - fail safe, not silent
            logger.error(
                "failed to initialize vault encryption (%s); "
                "disabling persistence, vault will be in-memory only this run",
                exc,
            )
            self.persist = False
            self._fernet = None

    def _load_from_disk(self) -> None:
        if not self.persist or not self.vault_path.exists():
            return
        try:
            data = read_encrypted_json(self.vault_path, self._fernet)
            with self._lock:
                for session_id, mapping in data.items():
                    vault = Vault(session_id)
                    for original, fake in mapping.items():
                        vault._original_to_fake[original] = fake
                        vault._fake_to_original[fake] = original
                        vault._used_fakes.add(fake)
                    self._vaults[session_id] = vault
        except Exception as exc:  # noqa: BLE001 - fail safe, not silent
            logger.error(
                "failed to load persisted vault from %s (%s); "
                "starting with an empty vault rather than crashing",
                self.vault_path,
                exc,
            )

    def _save_to_disk(self) -> None:
        if not self.persist or self._fernet is None:
            return
        try:
            with self._lock:
                data = {sid: v.to_dict() for sid, v in self._vaults.items()}
            write_encrypted_json(self.vault_path, data, self._fernet)
        except OSError as exc:
            logger.error(
                "failed to persist vault to %s (%s); "
                "session data for this run remains in-memory only",
                self.vault_path,
                exc,
            )

    def get_vault(self, session_id: str) -> Vault:
        with self._lock:
            vault = self._vaults.get(session_id)
            if vault is None:
                vault = Vault(session_id)
                self._vaults[session_id] = vault
            return vault

    def notify_mutated(self) -> None:
        """Call after mutating a vault to persist the change, if enabled."""
        self._save_to_disk()

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._vaults:
                self._vaults[session_id].clear()
                del self._vaults[session_id]
        self._save_to_disk()

    def clear_all(self) -> None:
        with self._lock:
            self._vaults.clear()
        self._save_to_disk()


_default_manager_lock = threading.Lock()
_default_manager: Optional[VaultManager] = None


def get_default_manager() -> VaultManager:
    global _default_manager
    with _default_manager_lock:
        if _default_manager is None:
            _default_manager = VaultManager()
        return _default_manager
