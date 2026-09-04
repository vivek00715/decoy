"""Local, encrypted-at-rest audit trail of masking decisions -- the
shared backend for both the VS Code extension (Phase 8) and IntelliJ
plugin (Phase 9), and now (chat_proxy.py) a second local process writing
to the same file concurrently with mcp_proxy.py.

On-disk format (after decryption) is documented in AUDIT_LOG_FORMAT.md:
flat JSON objects with only primitive fields (strings, one top-level
number), specifically so both a TypeScript and a Kotlin client can parse
it without any Python-specific serialization quirks.

CONFIRMED RACE (found empirically, same class of bug as vault.py's
cross-process save race -- see that module's docstring): `log_decision`/
`log_decisions` appended to `self._entries` and called `_save()` WITHOUT
ever reloading from disk first (unlike `get_entries()`/`clear()`, which
already did reload). Ten real concurrent processes each logging one
distinct entry to the same fresh audit log produced only 5 recorded
entries -- half were silently clobbered by whichever process's
whole-file overwrite happened to land last. Reproduced with 10 parallel
`python -c "AuditLog(...).log_decision(...)"` invocations against one
path. Fixed the same way as vault.py: a `filelock.FileLock` guards a
read-merge(append)-write critical section for every write, and reads
also take the lock for consistency (append-only log, so "merge" here is
simply "union by entry id," not the general merge vault.py needs).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from filelock import FileLock

from .crypto import DEFAULT_KEY_PATH, make_fernet, read_encrypted_json, write_encrypted_json
from .logging_config import get_logger

logger = get_logger("audit_log")

FORMAT_VERSION = 1
DEFAULT_AUDIT_LOG_PATH = Path(".decoy") / "audit.enc"

# Matches vault.py's FILE_LOCK_TIMEOUT_SECONDS -- see that module for why
# this value and why filelock (not fcntl) at all.
FILE_LOCK_TIMEOUT_SECONDS = 10.0

VALID_DECISIONS = {"masked", "redacted", "left_as_is"}
VALID_SOURCES = {"prompt_text", "db_record", "mcp_tool_result"}


@dataclass(frozen=True)
class AuditEntry:
    id: str
    request_id: str
    session_id: str
    timestamp: str
    source: str
    field: str
    decision: str
    reason: str
    layer: str

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AuditLog:
    """Thread-safe, encrypted-at-rest log of masking decisions.

    On any load/save failure, fails toward an empty in-memory log rather
    than crashing the caller -- an audit trail that can't be read/written
    should degrade to "no history available," never to a crash that takes
    down the masking pipeline it's supposed to be observing.
    """

    def __init__(self, path: Optional[str | Path] = None, key_path: Optional[str | Path] = None):
        self.path = Path(path or DEFAULT_AUDIT_LOG_PATH)
        self._lock = threading.RLock()
        self._file_lock = FileLock(str(self.path) + ".lock", timeout=FILE_LOCK_TIMEOUT_SECONDS)
        try:
            self._fernet = make_fernet(Path(key_path) if key_path else DEFAULT_KEY_PATH)
        except Exception as exc:  # noqa: BLE001 - fail safe, not silent
            logger.error(
                "failed to initialize audit log encryption (%s); "
                "audit log will be in-memory only this run",
                exc,
            )
            self._fernet = None
        self._entries: list[dict] = []
        self._load()

    def _read_disk_entries_unlocked(self) -> list[dict]:
        """Caller must already hold `_file_lock`. Returns [] (not an
        exception) for a missing/unreadable/wrong-format-version file --
        see this class's docstring for why a corrupt/foreign file must
        not block every other process sharing it."""
        if self._fernet is None or not self.path.exists():
            return []
        try:
            data = read_encrypted_json(self.path, self._fernet)
            if data.get("format_version") != FORMAT_VERSION:
                logger.warning(
                    "audit log format_version %r is not the version this build understands "
                    "(%r); treating as empty rather than misreading it",
                    data.get("format_version"),
                    FORMAT_VERSION,
                )
                return []
            return list(data.get("entries", []))
        except Exception as exc:  # noqa: BLE001 - fail safe, not silent
            logger.error(
                "failed to load audit log from %s (%s); treating as empty rather than crashing",
                self.path,
                exc,
            )
            return []

    def _load(self) -> None:
        try:
            with self._file_lock:
                self._entries = self._read_disk_entries_unlocked()
        except TimeoutError as exc:
            logger.error(
                "could not acquire audit log lock within %.1fs to load (%s); "
                "starting with an empty in-memory log rather than blocking indefinitely",
                FILE_LOCK_TIMEOUT_SECONDS,
                exc,
            )
            self._entries = []

    def _write_disk_entries_unlocked(self, entries: list[dict]) -> None:
        """Caller must already hold `_file_lock`."""
        if self._fernet is None:
            return
        write_encrypted_json(self.path, {"format_version": FORMAT_VERSION, "entries": entries}, self._fernet)

    def _append_and_save(self, new_entries: list[dict]) -> None:
        """Read-merge(append-by-id)-write under the cross-process lock --
        the fix for this module's confirmed race. `self._entries` is
        updated to the merged result too, so this process's own next read
        reflects both what it just wrote AND anything another process
        wrote in the meantime."""
        if self._fernet is None:
            with self._lock:
                self._entries.extend(new_entries)
            return
        try:
            with self._file_lock:
                disk_entries = self._read_disk_entries_unlocked()
                known_ids = {e["id"] for e in disk_entries}
                merged = disk_entries + [e for e in new_entries if e["id"] not in known_ids]
                self._write_disk_entries_unlocked(merged)
                with self._lock:
                    self._entries = merged
        except TimeoutError as exc:
            logger.error(
                "could not acquire audit log lock within %.1fs to save (%s); "
                "these entries were NOT persisted to disk (kept in-memory for this process only)",
                FILE_LOCK_TIMEOUT_SECONDS,
                exc,
            )
            with self._lock:
                self._entries.extend(new_entries)
        except OSError as exc:
            logger.error(
                "failed to persist audit log to %s (%s); entries for this run remain in-memory only",
                self.path,
                exc,
            )
            with self._lock:
                self._entries.extend(new_entries)

    def _make_entry(
        self, request_id: str, session_id: str, source: str, field: str, decision: str, reason: str, layer: str
    ) -> AuditEntry:
        if decision not in VALID_DECISIONS:
            logger.warning("unrecognized decision %r for field %r; recording as given", decision, field)
        if source not in VALID_SOURCES:
            logger.warning("unrecognized source %r for field %r; recording as given", source, field)
        return AuditEntry(
            id=str(uuid.uuid4()),
            request_id=request_id,
            session_id=session_id,
            timestamp=_now_iso(),
            source=source,
            field=field,
            decision=decision,
            reason=reason,
            layer=layer,
        )

    def log_decision(
        self, request_id: str, session_id: str, source: str, field: str, decision: str, reason: str, layer: str
    ) -> AuditEntry:
        entry = self._make_entry(request_id, session_id, source, field, decision, reason, layer)
        self._append_and_save([entry.to_dict()])
        return entry

    def log_decisions(
        self, request_id: str, session_id: str, source: str, decisions: list[tuple[str, str, str, str]]
    ) -> list[AuditEntry]:
        """Bulk version of log_decision: `decisions` is a list of
        (field, decision, reason, layer) tuples, persisted with a single
        disk write rather than one write per entry."""
        entries = [
            self._make_entry(request_id, session_id, source, field, decision, reason, layer)
            for field, decision, reason, layer in decisions
        ]
        self._append_and_save([e.to_dict() for e in entries])
        return entries

    def get_entries(self, session_id: Optional[str] = None, request_id: Optional[str] = None) -> list[dict]:
        """Return matching entries, re-reading from disk first so a
        separate process's writes (e.g. another Python process, or a
        concurrent request) are reflected -- audit reads are not on the
        masking hot path, so re-reading each call is an acceptable cost
        for correctness."""
        self._load()
        with self._lock:
            entries = list(self._entries)
        if session_id is not None:
            entries = [e for e in entries if e["session_id"] == session_id]
        if request_id is not None:
            entries = [e for e in entries if e["request_id"] == request_id]
        return entries

    def clear(self, session_id: Optional[str] = None) -> int:
        """Clear all entries (session_id=None) or only one session's
        entries. Returns the number of entries removed."""
        try:
            with self._file_lock:
                disk_entries = self._read_disk_entries_unlocked()
                before = len(disk_entries)
                if session_id is None:
                    remaining: list[dict] = []
                else:
                    remaining = [e for e in disk_entries if e["session_id"] != session_id]
                self._write_disk_entries_unlocked(remaining)
                with self._lock:
                    self._entries = remaining
                return before - len(remaining)
        except TimeoutError as exc:
            logger.error(
                "could not acquire audit log lock within %.1fs to clear (%s); "
                "no entries were removed on disk (in-memory state for this process left unchanged)",
                FILE_LOCK_TIMEOUT_SECONDS,
                exc,
            )
            return 0


_default_lock = threading.Lock()
_default_audit_log: Optional[AuditLog] = None


def get_default_audit_log() -> AuditLog:
    global _default_audit_log
    with _default_lock:
        if _default_audit_log is None:
            _default_audit_log = AuditLog()
        return _default_audit_log


def clear_audit_log(session_id: Optional[str] = None, audit_log: Optional[AuditLog] = None) -> int:
    """Module-level convenience matching the spec's clear_audit_log(session_id=None)."""
    log = audit_log or get_default_audit_log()
    return log.clear(session_id)
