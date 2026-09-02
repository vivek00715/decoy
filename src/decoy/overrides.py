"""Manual override rules: always_mask / never_mask.

Manual overrides beat automated classification, always. Every detection
path in Decoy (regex, NER, query-relevance classification, column-name
heuristics) MUST consult this module before finalizing a decision, and a
matching override always wins.

On any failure to load the overrides file (missing, malformed, unreadable),
this module fails toward MORE masking: always_mask rules are treated as
empty (nothing extra gets force-masked, which is safe) but this is logged
as a warning, and callers should *not* treat a load failure as "never_mask
also empty" being risky -- a broken never_mask file simply means nothing
gets an exemption, which is the safe direction.

Format (JSON or YAML), see decoy.config.example.json:
{
  "always_mask": {"patterns": [...regex...], "field_names": [...]},
  "never_mask":  {"patterns": [...regex...], "field_names": [...]}
}
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .logging_config import get_logger

logger = get_logger("overrides")

DEFAULT_PATH = Path(".decoy") / "overrides.json"


@dataclass
class OverrideRules:
    always_mask_patterns: list[re.Pattern] = field(default_factory=list)
    always_mask_field_names: set[str] = field(default_factory=set)
    never_mask_patterns: list[re.Pattern] = field(default_factory=list)
    never_mask_field_names: set[str] = field(default_factory=set)


class OverrideStore:
    """Thread-safe, hot-reloading loader for the overrides file.

    Reload is based on file mtime so concurrent requests never read a
    half-written file mid-save (the caller writing the file is expected
    to write-then-rename, which the VS Code / IntelliJ UIs do in Phase 8/9).
    """

    def __init__(self, path: Optional[str | Path] = None):
        raw_path = path or os.environ.get("DECOY_OVERRIDES_PATH") or DEFAULT_PATH
        self.path = Path(raw_path)
        self._lock = threading.RLock()
        self._rules = OverrideRules()
        self._mtime: Optional[float] = None
        self.reload(force=True)

    def reload(self, force: bool = False) -> None:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                if self._mtime is not None or force:
                    logger.warning(
                        "overrides file not found at %s; no manual overrides active",
                        self.path,
                    )
                self._rules = OverrideRules()
                self._mtime = None
                return

            if not force and mtime == self._mtime:
                return

            try:
                raw = self.path.read_text(encoding="utf-8")
                data = json.loads(raw)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "failed to parse overrides file %s (%s); "
                    "treating overrides as empty for this load",
                    self.path,
                    exc,
                )
                self._rules = OverrideRules()
                self._mtime = mtime
                return

            self._rules = self._parse(data)
            self._mtime = mtime

    def _parse(self, data: dict) -> OverrideRules:
        rules = OverrideRules()
        for section, pattern_attr, field_attr in (
            ("always_mask", "always_mask_patterns", "always_mask_field_names"),
            ("never_mask", "never_mask_patterns", "never_mask_field_names"),
        ):
            section_data = data.get(section) or {}
            patterns = []
            for p in section_data.get("patterns", []) or []:
                try:
                    patterns.append(re.compile(p))
                except re.error as exc:
                    logger.warning(
                        "invalid regex %r in overrides[%s].patterns (%s); skipping",
                        p,
                        section,
                        exc,
                    )
            setattr(rules, pattern_attr, patterns)
            names = {
                str(n).strip().lower()
                for n in (section_data.get("field_names", []) or [])
            }
            setattr(rules, field_attr, names)
        return rules

    def rules(self) -> OverrideRules:
        with self._lock:
            self.reload()
            return self._rules

    def is_always_mask_field(self, field_name: str) -> bool:
        return field_name.strip().lower() in self.rules().always_mask_field_names

    def is_never_mask_field(self, field_name: str) -> bool:
        return field_name.strip().lower() in self.rules().never_mask_field_names

    def find_always_mask_spans(self, text: str) -> list[tuple[int, int]]:
        spans = []
        for pattern in self.rules().always_mask_patterns:
            for m in pattern.finditer(text):
                if m.start() != m.end():
                    spans.append((m.start(), m.end()))
        return spans

    def matches_never_mask_pattern(self, value: str) -> bool:
        return any(p.search(value) for p in self.rules().never_mask_patterns)


_default_store_lock = threading.Lock()
_default_store: Optional[OverrideStore] = None


def get_default_store() -> OverrideStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = OverrideStore()
        return _default_store
