"""Pluggable NER (Named Entity Recognition) detection interface.

Phase 1 shipped only the interface plus a no-op implementation, so the
regex layer in masker.py isn't the only detection method long-term:
`NoOpNERBackend` is the default (adds nothing, no dependency required).

DECOY_USE_PRESIDIO (added here -- did not exist before this; see
masker.py's Masker.__init__ for the exact opt-in wiring): a
Presidio-backed `NERBackend` implementation, for bare names/addresses
typed directly into free text -- the gap named explicitly in
benchmark_cases.py's module docstring ("a bare person NAME typed directly
into a question is NOT currently masked by this project at all") and in
WHAT_THIS_PROTECTS_AGAINST.md's section 3. Verified directly:
`mask_text("Please reach out to John Smith about the invoice.")` returned
`detections=[]` with the default NoOpNERBackend, and correctly detected
"John Smith" as PERSON once `PresidioNERBackend` was wired in and enabled.

Deliberately still opt-in, not a new forced hard dependency: importing
this module, or using `NoOpNERBackend`, never requires `presidio-analyzer`
or `spacy` at all -- `PresidioNERBackend` imports them lazily inside its
own `__init__`, so `pip install decoy` (no extras) and every other
NERBackend stay completely unaffected. Enabling it is one line:

    DECOY_USE_PRESIDIO=true   # plus: pip install "decoy[ner]" && python -m spacy download en_core_web_lg

No network call happens at mask-time either way -- `python -m spacy
download` is a one-time, explicit, out-of-band setup step (like installing
any other model/dependency), not something this module or Masker ever
triggers on its own. If the extra/model isn't installed and
DECOY_USE_PRESIDIO=true anyway, Masker fails safe to NoOpNERBackend with a
loud log message (see masker.py) rather than crashing the whole pipeline
over an optional detector.
"""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .logging_config import get_logger

logger = get_logger("ner")


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


# Presidio's own entity labels that masker.py's Masker._generate_fake
# already knows how to produce a realistic fake for (PERSON -> a name,
# LOCATION -- Presidio's label for what masker.py calls ADDRESS -- ->
# an address). Every other Presidio entity type Presidio might also
# report (e.g. DATE_TIME, NRP, URL) is passed through as an NERSpan too
# -- masker.py's _generate_fake falls back to a shape-preserving fake for
# any label it doesn't specifically recognize, so nothing is silently
# dropped, but only these two get a semantically realistic fake.
_SUPPORTED_PRESIDIO_ENTITIES = ["PERSON", "LOCATION"]


class PresidioNERBackend(NERBackend):
    """Presidio-backed NER: detects PERSON and LOCATION entities in free
    text via `presidio_analyzer.AnalyzerEngine` (spaCy under the hood).

    Requires the `ner` extra (`pip install "decoy[ner]"`) AND a
    downloaded spaCy model (`python -m spacy download en_core_web_lg`,
    or a smaller `en_core_web_sm` if you pass `spacy_model=`) -- neither
    is imported or required unless this class is actually instantiated.
    Construction itself does the (one-time, in-process) model load, which
    takes real wall-clock time (spaCy model loading, not a network call)
    -- construct one `PresidioNERBackend` and reuse it across `Masker`
    instances/requests rather than creating a fresh one per call.
    """

    def __init__(self, spacy_model: str = "en_core_web_lg", score_threshold: float = 0.5):
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
        except ImportError as exc:
            raise ImportError(
                "PresidioNERBackend requires the 'ner' extra: pip install \"decoy[ner]\" "
                "(and a spaCy model: python -m spacy download en_core_web_lg)"
            ) from exc

        nlp_configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": spacy_model}],
        }
        try:
            nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()
            self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        except OSError as exc:
            raise ImportError(
                f"PresidioNERBackend could not load spaCy model {spacy_model!r} -- download it "
                f"first: python -m spacy download {spacy_model}"
            ) from exc

        self.score_threshold = score_threshold

    def detect(self, text: str) -> list[NERSpan]:
        if not text:
            return []
        try:
            results = self._analyzer.analyze(
                text=text, language="en", entities=_SUPPORTED_PRESIDIO_ENTITIES, score_threshold=self.score_threshold
            )
        except Exception as exc:  # noqa: BLE001 - fail safe, never crash masking over an NER bug
            logger.error("Presidio analysis raised %s; continuing without NER for this call", exc)
            return []
        return [NERSpan(start=r.start, end=r.end, label=r.entity_type, score=r.score) for r in results]


# CONFIRMED PERFORMANCE FINDING (checked before wiring DECOY_USE_PRESIDIO
# into Masker, not assumed): constructing a fresh `AnalyzerEngine`
# (PresidioNERBackend.__init__) takes ~13 SECONDS -- it loads a spaCy
# language model from disk every time. `Masker` is constructed fresh per
# request throughout this codebase (mcp_proxy.py's `_on_call_tool`,
# unified_pipeline.ask_over_data, chat_proxy.py's `_masker_for` all build
# a new `Masker(...)` per call) -- if `Masker.__init__` naively built a
# new `PresidioNERBackend()` every time DECOY_USE_PRESIDIO=true, every
# single masked request would pay a 13-second penalty. `Masker` must
# reuse ONE cached backend instance across calls, the same way
# `get_default_manager()`/`get_default_store()` are process-wide
# singletons -- this function is that cache for the NER backend.
_default_backend_lock = threading.Lock()
_default_backend: Optional[NERBackend] = None
_default_backend_env_snapshot: Optional[str] = None


def get_default_ner_backend() -> NERBackend:
    """Process-wide cached NER backend, selected by DECOY_USE_PRESIDIO
    (case-insensitive "true" to opt in; anything else, including unset,
    keeps the zero-dependency NoOpNERBackend default).

    Re-checks the env var on every call (cheap) but only ever
    constructs/loads the expensive PresidioNERBackend ONCE per process,
    and only if the env var's value actually changes between calls does
    it reconsider -- mainly so tests that flip DECOY_USE_PRESIDIO via
    monkeypatch between cases get the backend their own env var actually
    asks for, rather than a stale cached choice from an earlier test.

    Fails safe to NoOpNERBackend (with a loud error log, not a silent
    fallback) if DECOY_USE_PRESIDIO=true but the `ner` extra/spaCy model
    isn't actually installed -- an optional detector being unavailable
    must never crash the whole masking pipeline.
    """
    global _default_backend, _default_backend_env_snapshot

    use_presidio = os.environ.get("DECOY_USE_PRESIDIO", "false").lower() == "true"
    env_marker = "presidio" if use_presidio else "noop"

    with _default_backend_lock:
        if _default_backend is not None and _default_backend_env_snapshot == env_marker:
            return _default_backend

        if not use_presidio:
            _default_backend = NoOpNERBackend()
            _default_backend_env_snapshot = env_marker
            return _default_backend

        try:
            _default_backend = PresidioNERBackend()
            _default_backend_env_snapshot = env_marker
            logger.info("DECOY_USE_PRESIDIO=true: Presidio NER backend loaded and active")
        except ImportError as exc:
            logger.error(
                "DECOY_USE_PRESIDIO=true but Presidio could not be loaded (%s); falling back to "
                "NoOpNERBackend for this process -- install the 'ner' extra and a spaCy model "
                "(pip install \"decoy[ner]\" && python -m spacy download en_core_web_lg) to fix this",
                exc,
            )
            _default_backend = NoOpNERBackend()
            _default_backend_env_snapshot = env_marker
        return _default_backend
