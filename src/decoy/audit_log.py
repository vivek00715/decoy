"""Local, encrypted-at-rest audit trail of masking decisions -- the
shared backend for both the VS Code extension (Phase 8) and IntelliJ
plugin (Phase 9). NEVER logs real or fake sensitive values, only metadata
about the masking decision itself (field name, decision, reason, layer).

On-disk format (after decryption) is documented in AUDIT_LOG_FORMAT.md:
flat JSON objects with only primitive fields (strings, one top-level
number), specifically so both a TypeScript and a Kotlin client can parse
it without any Python-specific serialization quirks.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .crypto import DEFAULT_KEY_PATH, make_fernet, read_encrypted_json, write_encrypted_json
from .logging_config import get_logger

logger = get_logger("audit_log")

FORMAT_VERSION = 1
DEFAULT_AUDIT_LOG_PATH = Path(".decoy") / "audit.enc"

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

    def _load(self) -> None:
        if self._fernet is None or not self.path.exists():
            return
        try:
            data = read_encrypted_json(self.path, self._fernet)
            if data.get("format_version") != FORMAT_VERSION:
                logger.warning(
                    "audit log format_version %r is not the version this build understands "
                    "(%r); starting from an empty log rather than misreading it",
                    data.get("format_version"),
                    FORMAT_VERSION,
                )
                self._entries = []
                return
            self._entries = list(data.get("entries", []))
        except Exception as exc:  # noqa: BLE001 - fail safe, not silent
            logger.error(
                "failed to load audit log from %s (%s); starting with an empty log rather than crashing",
                self.path,
                exc,
            )
            self._entries = []

    def _save(self) -> None:
        if self._fernet is None:
            return
        try:
            write_encrypted_json(
                self.path, {"format_version": FORMAT_VERSION, "entries": self._entries}, self._fernet
            )
        except OSError as exc:
            logger.error(
                "failed to persist audit log to %s (%s); entries for this run remain in-memory only",
                self.path,
                exc,
            )

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
        with self._lock:
            self._entries.append(entry.to_dict())
            self._save()
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
        with self._lock:
            self._entries.extend(e.to_dict() for e in entries)
            self._save()
        return entries

    def get_entries(self, session_id: Optional[str] = None, request_id: Optional[str] = None) -> list[dict]:
        """Return matching entries, re-reading from disk first so a
        separate process's writes (e.g. another Python process, or a
        concurrent request) are reflected -- audit reads are not on the
        masking hot path, so re-reading each call is an acceptable cost
        for correctness."""
        with self._lock:
            self._load()
            entries = list(self._entries)
        if session_id is not None:
            entries = [e for e in entries if e["session_id"] == session_id]
        if request_id is not None:
            entries = [e for e in entries if e["request_id"] == request_id]
        return entries

    def clear(self, session_id: Optional[str] = None) -> int:
        """Clear all entries (session_id=None) or only one session's
        entries. Returns the number of entries removed."""
        with self._lock:
            self._load()
            before = len(self._entries)
            if session_id is None:
                self._entries = []
            else:
                self._entries = [e for e in self._entries if e["session_id"] != session_id]
            removed = before - len(self._entries)
            self._save()
        return removed


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
