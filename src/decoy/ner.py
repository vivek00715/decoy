"""Pluggable NER (Named Entity Recognition) detection interface.

Phase 1 ships only the interface plus a no-op implementation, so the
regex layer in masker.py isn't the only detection method long-term:
`NoOpNERBackend` is the default (adds nothing, no dependency required),
and a Presidio-backed implementation can be dropped in later (a later
phase) without changing masker.py's call site.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class NERSpan:
    start: int
    end: int
    label: str  # e.g. "PERSON", "LOCATION", "ADDRESS"
    score: float = 1.0


class NERBackend(ABC):
    """Interface for a pluggable free-text entity detector."""

    @abstractmethod
    def detect(self, text: str) -> list[NERSpan]:
        """Return non-overlapping-or-not spans of detected entities.

        Must never raise on malformed input -- on internal failure,
        implementations should log and return an empty list (fail toward
        the regex layer still catching what it can, not toward crashing
        the whole masking pipeline).
        """
        raise NotImplementedError


class NoOpNERBackend(NERBackend):
    """Default backend: detects nothing. Keeps Decoy fully local-first
    with zero extra dependencies until an NER backend is explicitly
    configured."""

    def detect(self, text: str) -> list[NERSpan]:
        return []
