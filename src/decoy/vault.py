"""The vault: session-scoped original <-> fake value mappings.

One vault per session_id. All masking paths (free-text prompts in
masker.py, DB records in record_masker.py) share the same vault for a
given session_id, so the same real value always gets the same fake value
everywhere -- that's what lets Phase 4 unify prompt-text and DB-record
masking, and what the chat-completions proxy (chat_proxy.py) relies on to
stay consistent with the MCP proxy (mcp_proxy.py) even though those two
run as SEPARATE OS PROCESSES in real usage (the MCP proxy is a stdio
subprocess Claude Code spawns per its .mcp.json config; the chat proxy is
a separately-started long-running local HTTP daemon) -- see
"Cross-process persistence" below for how that's made safe.

Persistence is optional and off by default (in-memory only). When
enabled (DECOY_VAULT_PERSIST=true), the vault is encrypted at rest with
Fernet (AES-128-CBC + HMAC) using a key from DECOY_ENCRYPTION_KEY or a
generated key file at .decoy/vault.key (created with 0600 permissions).
Never mask/unmask real values in a way that could leak them into logs --
this module raises plain exceptions on I/O failure but never logs vault
contents.

Cross-process persistence (added alongside the chat proxy): a dormant
assumption in this module, true only because nothing had ever exercised
it, was that at most ONE process (one VaultManager) would ever have a
given persisted vault file open at a time. Before the chat proxy, that
was accurate -- nothing ran two long-lived Decoy processes against the
same .decoy/ directory concurrently. Two bugs followed directly from that
assumption and are fixed here, verified by test_vault_cross_process.py's
real two-OS-process test (not just a unit test of the merge function in
isolation):

  1. Lost updates on save: the old `_save_to_disk()` dumped this
     process's ENTIRE in-memory `_vaults` state and overwrote the file
     unconditionally. Two processes doing that is last-writer-wins: if
     process A creates session "s1" while process B (which never loaded
     "s1") later saves, B's blind overwrite erases "s1" entirely, even
     though B never touched it. Fixed by making every disk write a
     read-merge-write under `_file_lock` (see `_save_to_disk_locked`):
     re-read whatever's currently on disk, overlay this process's own
     in-memory sessions on top, write the union. A session neither
     process holds in memory is preserved untouched.

  2. Divergent fakes for the same real value: even with (1) fixed, a
     save-time-only merge does not stop two processes from *independently
     minting two different fakes* for the same original value if both
     process a request containing it before either has saved -- a
     classic check-then-act race, and the more serious bug of the two
     (it would silently break the "same real value, same fake
     everywhere" guarantee this whole vault exists to provide). Fixed by
     making the mint decision itself cross-process-safe: when
     persistence is on, `Vault.get_or_create_fake` no longer decides
     "mint a new fake" using only its own in-memory state -- see
     `VaultManager._synchronized_get_or_create_fake`, which reloads the
     latest on-disk mapping for that session, merges it in, and only
     THEN checks/mints, all inside one `_file_lock` critical section
     that also persists the result before releasing the lock.

`filelock.FileLock` (not `fcntl.flock`) is used for the cross-process
lock specifically because this project's real target platforms include
Windows -- `fcntl` fails to *import* at all there, not just behave
differently. `filelock` wraps `msvcrt` on Windows and `fcntl` on POSIX
and is a core (not optional-extra) dependency: as soon as more than one
process can share a persisted vault, correct locking is not an optional
feature.

Disclosed gap, not assumed away: the `msvcrt` (Windows) path has only
ever been exercised on macOS/POSIX in the environment that wrote this
fix -- no Windows environment was available to test against directly
(checked: no Docker/Wine/QEMU/Lima/Multipass in that environment). See
README.md's "Verifying filelock on Windows" section for the exact
`pytest` command to confirm this (and the crypto.py/audit_log.py
equivalents) on a real Windows machine -- this project's own real
end-to-end target -- before trusting it there.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Optional

from filelock import FileLock

from .crypto import DEFAULT_KEY_PATH, make_fernet, read_encrypted_json, write_encrypted_json
from .logging_config import get_logger

logger = get_logger("vault")

DEFAULT_VAULT_PATH = Path(".decoy") / "vault.enc"
MAX_FAKE_RETRIES = 25

# How long to wait to acquire the cross-process file lock before giving
# up. A real crash while holding the OS-level lock still releases it
# immediately (both msvcrt and fcntl locks are tied to the holding
# process/file descriptor, not held open past process exit) -- this
# timeout exists only to turn a *misbehaving* holder (e.g. something
# stuck retrying inside the critical section) into a clear, loud error
# instead of an indefinite hang for every other process sharing this
# vault file.
FILE_LOCK_TIMEOUT_SECONDS = 10.0


class VaultPersistenceError(RuntimeError):
    """Raised when the vault cannot be loaded or saved to disk."""


class VaultLockTimeoutError(VaultPersistenceError):
    """Raised when the cross-process file lock could not be acquired
    within FILE_LOCK_TIMEOUT_SECONDS -- surfaced distinctly from a
    generic persistence error since the fix is different (investigate
    what's holding the lock) from a plain I/O failure."""


class Vault:
    """Holds the bidirectional mapping for a single session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._original_to_fake: dict[str, str] = {}
        self._fake_to_original: dict[str, str] = {}
        self._used_fakes: set[str] = set()
        self._lock = threading.RLock()
        # Set by VaultManager.get_vault() when persistence is enabled --
        # see this module's docstring, point (2). None (the default)
        # means "no cross-process coordination," i.e. the original
        # in-memory-only behavior, unchanged for every non-persisting
        # caller (which is most tests and most usage).
        self._sync_fn: Optional[Callable[["Vault", str, Callable[[], str]], str]] = None

    def _mint_local(self, original: str, generator: Callable[[], str]) -> str:
        """The actual collision-avoiding mint algorithm, operating ONLY
        on this Vault's own in-memory state. Must be called with
        `self._lock` already held (it does not acquire it itself) --
        both `get_or_create_fake`'s local path and
        VaultManager._synchronized_get_or_create_fake's cross-process
        path call this after their own (different) pre-mint checks.
        """
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

    def get_or_create_fake(self, original: str, generator: Callable[[], str]) -> str:
        """Return the existing fake for `original`, or mint a new one.

        `generator` is called (and re-called on collision) to produce a
        candidate fake value; collision avoidance ensures no two distinct
        original values ever map to the same fake within a session.

        When this Vault came from a persisting VaultManager, the "mint a
        new one" branch is delegated to `_sync_fn` (cross-process-safe)
        rather than minting purely from local state -- see this module's
        docstring, point (2).
        """
        with self._lock:
            existing = self._original_to_fake.get(original)
            if existing is not None:
                return existing
            if self._sync_fn is not None:
                return self._sync_fn(self, original, generator)
            return self._mint_local(original, generator)

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

    def merge_from(self, mapping: dict[str, str]) -> None:
        """Add any (original -> fake) pairs from `mapping` this Vault
        doesn't already have. Never overwrites an existing in-memory
        entry -- by construction (all mints go through the synchronized
        check-then-mint path when persistence is on) there should never
        be a genuine conflict, but "never overwrite what's already been
        handed to a caller" is the safe direction if that invariant is
        ever violated some other way."""
        with self._lock:
            for original, fake in mapping.items():
                if original in self._original_to_fake:
                    continue
                self._original_to_fake[original] = fake
                self._fake_to_original[fake] = original
                self._used_fakes.add(fake)

    def clear(self) -> None:
        with self._lock:
            self._original_to_fake.clear()
            self._fake_to_original.clear()
            self._used_fakes.clear()


class VaultManager:
    """Process-wide registry of vaults, keyed by session_id.

    Thread-safe: `_lock` guards session creation/lookup/clear within THIS
    process; each Vault guards its own mapping independently so
    concurrent requests against *different* sessions never contend, and
    concurrent requests against the *same* session are serialized safely.

    When `persist` is enabled, `_file_lock` (a `filelock.FileLock`, safe
    on both Windows and POSIX) additionally guards the on-disk vault file
    against concurrent access from OTHER PROCESSES -- see this module's
    docstring for the two bugs this closes and why a save-only fix isn't
    enough.
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
        self._file_lock: Optional[FileLock] = None
        if self.persist:
            self._init_crypto()
            # The lock file is separate from the data file itself
            # (`<vault_path>.lock`) so acquiring the lock never requires
            # the data file to already exist, and so the lock file's own
            # (unencrypted, empty) content is never confused with vault
            # data.
            self._file_lock = FileLock(str(self.vault_path) + ".lock", timeout=FILE_LOCK_TIMEOUT_SECONDS)
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

    def _read_disk_state_unlocked(self) -> dict[str, dict[str, str]]:
        """Read+decrypt the full on-disk `{session_id: {original: fake}}`
        map. Caller must already hold `_file_lock`. Returns `{}` (not an
        exception) for a missing or unreadable file -- a concurrent
        writer's in-progress atomic replace is never observed as a
        partial read (crypto.write_encrypted_json writes then renames),
        but a genuinely corrupt/foreign file should not block every
        other process sharing it from making progress; it's treated the
        same as "nothing persisted yet."
        """
        if not self.vault_path.exists():
            return {}
        try:
            return read_encrypted_json(self.vault_path, self._fernet)
        except Exception as exc:  # noqa: BLE001 - fail safe, see docstring
            logger.error(
                "failed to read persisted vault at %s (%s); treating as empty for this operation",
                self.vault_path,
                exc,
            )
            return {}

    def _load_from_disk(self) -> None:
        if not self.persist or self._file_lock is None:
            return
        try:
            with self._file_lock:
                data = self._read_disk_state_unlocked()
            with self._lock:
                for session_id, mapping in data.items():
                    vault = Vault(session_id)
                    vault.merge_from(mapping)
                    vault._sync_fn = self._synchronized_get_or_create_fake
                    self._vaults[session_id] = vault
        except TimeoutError as exc:
            logger.error(
                "could not acquire vault lock within %.1fs on startup (%s); "
                "starting with an empty in-memory vault rather than blocking indefinitely",
                FILE_LOCK_TIMEOUT_SECONDS,
                exc,
            )
        except Exception as exc:  # noqa: BLE001 - fail safe, not silent
            logger.error(
                "failed to load persisted vault from %s (%s); "
                "starting with an empty vault rather than crashing",
                self.vault_path,
                exc,
            )

    def _save_to_disk_locked(self) -> None:
        """Read-merge-write the on-disk file. Caller must already hold
        `_file_lock`. This is the fix for docstring bug (1): sessions on
        disk that this process doesn't hold in memory are preserved,
        not clobbered; sessions this process DOES hold are written from
        its own in-memory state (which, for any session reached via the
        synchronized mint path, is itself already merged with the latest
        disk state -- see `_synchronized_get_or_create_fake`)."""
        if self._fernet is None:
            return
        try:
            disk_data = self._read_disk_state_unlocked()
            with self._lock:
                for session_id, vault in self._vaults.items():
                    disk_data[session_id] = vault.to_dict()
            write_encrypted_json(self.vault_path, disk_data, self._fernet)
        except OSError as exc:
            logger.error(
                "failed to persist vault to %s (%s); "
                "session data for this run remains in-memory only",
                self.vault_path,
                exc,
            )

    def _save_to_disk(self) -> None:
        """Acquire the lock and save. Prefer this over
        `_save_to_disk_locked` when the caller does NOT already hold
        `_file_lock` (the common case: `notify_mutated()`, `clear_session`,
        `clear_all`)."""
        if not self.persist or self._file_lock is None:
            return
        try:
            with self._file_lock:
                self._save_to_disk_locked()
        except TimeoutError as exc:
            logger.error(
                "could not acquire vault lock within %.1fs to save (%s); "
                "this change was NOT persisted to disk (still applied in-memory for this process)",
                FILE_LOCK_TIMEOUT_SECONDS,
                exc,
            )

    def _synchronized_get_or_create_fake(
        self, vault: Vault, original: str, generator: Callable[[], str]
    ) -> str:
        """The cross-process-safe mint path -- see this module's
        docstring, point (2). Called by `Vault.get_or_create_fake` (with
        `vault._lock` already held; safe to re-enter since it's an
        RLock) only once the vault's OWN in-memory state has already
        been checked and doesn't have `original`.

        Order matters: reload+merge from disk BEFORE checking again,
        because another process may have minted a fake for this exact
        `original` since this process last saw the vault -- checking
        only local state first (as a save-only fix would) is exactly the
        check-then-act race this method exists to close.
        """
        if self._file_lock is None:
            # persist was disabled after this Vault's _sync_fn was
            # wired up (e.g. crypto init failed) -- fall back to the
            # plain local mint rather than crash.
            return vault._mint_local(original, generator)

        try:
            with self._file_lock:
                disk_data = self._read_disk_state_unlocked()
                vault.merge_from(disk_data.get(vault.session_id, {}))

                existing = vault._original_to_fake.get(original)
                if existing is not None:
                    return existing

                fake = vault._mint_local(original, generator)
                self._save_to_disk_locked()
                return fake
        except TimeoutError as exc:
            logger.error(
                "could not acquire vault lock within %.1fs to mint a new fake for session %r (%s); "
                "minting in-memory only for this process -- another process sharing this vault may "
                "independently mint a DIFFERENT fake for the same value until the lock is available "
                "again, which is a real (if rare) risk of this fallback, not a silent guarantee",
                FILE_LOCK_TIMEOUT_SECONDS,
                vault.session_id,
                exc,
            )
            return vault._mint_local(original, generator)

    def get_vault(self, session_id: str) -> Vault:
        with self._lock:
            vault = self._vaults.get(session_id)
            if vault is None:
                vault = Vault(session_id)
                if self.persist:
                    vault._sync_fn = self._synchronized_get_or_create_fake
                self._vaults[session_id] = vault
            return vault

    def notify_mutated(self) -> None:
        """Call after mutating a vault to persist the change, if enabled.

        With the synchronized mint path above, every individual mint is
        already immediately persisted -- this additional batch-level save
        is now technically redundant on that specific path, but is kept
        (and kept merge-safe, not a blind overwrite) as a safety net for
        any other mutation path, present or future, that doesn't go
        through `get_or_create_fake`."""
        self._save_to_disk()

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._vaults:
                self._vaults[session_id].clear()
                del self._vaults[session_id]
        if not self.persist or self._file_lock is None:
            return
        try:
            with self._file_lock:
                disk_data = self._read_disk_state_unlocked()
                disk_data.pop(session_id, None)
                with self._lock:
                    for sid, vault in self._vaults.items():
                        disk_data[sid] = vault.to_dict()
                if self._fernet is not None:
                    write_encrypted_json(self.vault_path, disk_data, self._fernet)
        except TimeoutError as exc:
            logger.error(
                "could not acquire vault lock within %.1fs to clear session %r on disk (%s); "
                "cleared in-memory for this process only",
                FILE_LOCK_TIMEOUT_SECONDS,
                session_id,
                exc,
            )
        except OSError as exc:
            logger.error("failed to persist session clear for %r (%s)", session_id, exc)

    def clear_all(self) -> None:
        with self._lock:
            self._vaults.clear()
        if not self.persist or self._file_lock is None:
            return
        try:
            with self._file_lock:
                if self._fernet is not None:
                    write_encrypted_json(self.vault_path, {}, self._fernet)
        except TimeoutError as exc:
            logger.error(
                "could not acquire vault lock within %.1fs to clear all vault data on disk (%s); "
                "cleared in-memory for this process only",
                FILE_LOCK_TIMEOUT_SECONDS,
                exc,
            )
        except OSError as exc:
            logger.error("failed to persist clear-all (%s)", exc)


_default_manager_lock = threading.Lock()
_default_manager: Optional[VaultManager] = None


def get_default_manager() -> VaultManager:
    global _default_manager
    with _default_manager_lock:
        if _default_manager is None:
            _default_manager = VaultManager()
        return _default_manager
