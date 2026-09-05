"""Free-text PII masking: Masker(session_id).mask_text() / unmask_text().

Detection is layered, never a single method:
  1. Manual overrides (overrides.py) -- checked first, always win.
  2. Regex layer -- fast, precise, for well-formatted PII.
  3. Pluggable NER layer (ner.py) -- no-op by default in this phase.

Every decision records which layer made the call and why, so Phase 7's
audit trail can attribute it correctly. This module itself is allowed to
carry real/fake values in its return types (MaskResult/Detection) because
it's the module producing them -- callers that persist decisions (the
audit log) must not store `original`/`fake`, only metadata.
"""

from __future__ import annotations

import os
import random
import re
import string
import threading
from dataclasses import dataclass, field
from typing import Optional

from faker import Faker

from .logging_config import get_logger
from .ner import NERBackend, NERSpan, NoOpNERBackend, get_default_ner_backend
from .overrides import OverrideStore, get_default_store
from .vault import Vault, VaultManager, get_default_manager

logger = get_logger("masker")

# --- Regex layer -----------------------------------------------------------

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zA-Z]{2,}")

PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)"
)

SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")

CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d{4}[- ]?){3}\d{4}(?!\d)")

IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)

API_KEY_RE = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}"  # AWS access key
    r"|gh[pousr]_[A-Za-z0-9]{36}"  # GitHub tokens
    r"|sk-(?:ant-)?[A-Za-z0-9]{20,}"  # OpenAI/Anthropic-style secret keys
    r"|xox[baprs]-[A-Za-z0-9-]{10,})\b"  # Slack tokens
)

DEFAULT_PNR_RE = re.compile(
    r"\b(?=[A-Z0-9]{5,8}\b)(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,8}\b",
    re.IGNORECASE,
)
# CONFIRMED GAP (found empirically, then fixed here): DEFAULT_PNR_RE had
# no re.IGNORECASE. `mask_text("my pnr fghty6")` returned detections=[]
# -- a lowercase PNR-shaped code was invisible to the regex layer
# entirely. Real PNRs/booking codes are usually rendered uppercase, but
# nothing stops a user from typing one in lowercase (autocomplete,
# copy-paste from a lowercase confirmation email, etc.), and the
# consequence of missing one is a real code reaching an LLM unmasked --
# worth the (small) extra false-positive surface of also catching
# lowercase alphanumeric codes that happen to fit the same shape.

# Context-aware PNR detection: catches a PNR-like code that does NOT fit
# DEFAULT_PNR_RE's strict shape (too short, no digit, etc.) but sits right
# next to an explicit keyword naming it as one. This is a DELIBERATE
# false-positive-tolerant tradeoff, not a silent regex loosening --
# documented as such in WHAT_THIS_PROTECTS_AGAINST.md. It only fires near
# one of these keywords, so its false-positive surface is bounded to text
# that already talks about a booking/confirmation code, not arbitrary
# alphanumeric words anywhere in a message.
PNR_CONTEXT_KEYWORD_RE = re.compile(r"\b(?:pnr|booking\s+reference|confirmation\s+number)\b", re.IGNORECASE)
_CONTEXT_TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,12}")
# Words that could immediately follow a context keyword without THEMSELVES
# being the code (e.g. "confirmation number is XY12", "booking reference:
# ABCDEF") -- skipped so the scan doesn't misfire on the sentence's own
# grammar/punctuation before it reaches the actual code.
_CONTEXT_STOPWORDS = {
    "is", "was", "the", "a", "an", "your", "my", "please", "number", "reference", "code", "id", "for", "of",
}
# How far past a keyword match to look for the code -- generous enough
# for "booking reference number: ABC123" (keyword + 2 filler words +
# punctuation) without scanning the whole rest of the message.
_CONTEXT_WINDOW_CHARS = 40

_LAYER_OVERRIDE = "override"
_LAYER_REGEX = "regex"
_LAYER_NER = "ner"
_LAYER_CONTEXT = "context"

_PRIORITY = {_LAYER_OVERRIDE: 3, _LAYER_REGEX: 2, _LAYER_NER: 1, _LAYER_CONTEXT: 0}


@dataclass(frozen=True)
class Detection:
    start: int
    end: int
    original: str
    fake: str
    label: str
    layer: str
    reason: str


@dataclass
class MaskResult:
    masked_text: str
    detections: list[Detection] = field(default_factory=list)


def _shape_preserving_fake(original: str, rng: random.Random) -> str:
    """Generate a same-shape random string: letters->letters (case kept),
    digits->digits, everything else kept as-is. Used for PNR/API-key/
    unknown-override matches where there's no dedicated Faker provider,
    while still looking plausibly "real" rather than an obvious placeholder.
    """
    out = []
    for ch in original:
        if ch.isdigit():
            out.append(rng.choice(string.digits))
        elif ch.isupper():
            out.append(rng.choice(string.ascii_uppercase))
        elif ch.islower():
            out.append(rng.choice(string.ascii_lowercase))
        else:
            out.append(ch)
    return "".join(out)


def _classify_unknown(value: str) -> str:
    """Best-effort label guess for an override-forced match with no known
    detector type, so we can still pick a semantically plausible fake."""
    if EMAIL_RE.fullmatch(value):
        return "EMAIL"
    if SSN_RE.fullmatch(value):
        return "SSN"
    if PHONE_RE.fullmatch(value):
        return "PHONE"
    if IPV4_RE.fullmatch(value):
        return "IP_ADDRESS"
    return "OVERRIDE"


class Masker:
    def __init__(
        self,
        session_id: str,
        vault_manager: Optional[VaultManager] = None,
        override_store: Optional[OverrideStore] = None,
        ner_backend: Optional[NERBackend] = None,
        pnr_pattern: Optional[str] = None,
        faker_seed: Optional[int] = None,
    ):
        self.session_id = session_id
        self.vault_manager = vault_manager or get_default_manager()
        self.override_store = override_store or get_default_store()
        # DECOY_USE_PRESIDIO=true opts into Presidio-backed name/address
        # detection via a process-wide cached backend (get_default_ner_backend()
        # -- see ner.py's module docstring: constructing a fresh Presidio
        # backend takes ~13s, so it must be built once per process, not
        # once per Masker, and Masker IS constructed fresh per request
        # throughout this codebase). Explicit `ner_backend=` always wins,
        # matching every other constructor param's override pattern here.
        self.ner_backend = ner_backend or get_default_ner_backend()
        pnr_env = pnr_pattern or os.environ.get("DECOY_PNR_PATTERN")
        self.pnr_re = re.compile(pnr_env) if pnr_env else DEFAULT_PNR_RE
        # Faker/random are not thread-safe for a shared instance under
        # concurrent mask calls on the same Masker; guard with a lock.
        self._faker = Faker()
        if faker_seed is not None:
            Faker.seed(faker_seed)
        self._rng = random.Random(faker_seed)
        self._lock = threading.Lock()

    @property
    def vault(self) -> Vault:
        return self.vault_manager.get_vault(self.session_id)

    # -- detection ------------------------------------------------------

    def _regex_detections(self, text: str) -> list[tuple[int, int, str, str]]:
        """Returns (start, end, label, reason) tuples from the regex layer."""
        found = []
        for pattern, label in (
            (EMAIL_RE, "EMAIL"),
            (API_KEY_RE, "API_KEY"),
            (SSN_RE, "SSN"),
            (CREDIT_CARD_RE, "CREDIT_CARD"),
            (PHONE_RE, "PHONE"),
            (IPV4_RE, "IP_ADDRESS"),
            (self.pnr_re, "PNR"),
        ):
            for m in pattern.finditer(text):
                if m.start() == m.end():
                    continue
                found.append((m.start(), m.end(), label, f"matched {label} regex"))
        return found

    def _context_detections(self, text: str) -> list[tuple[int, int, str, str]]:
        """Returns (start, end, label, reason) tuples for a PNR-like code
        found near a keyword naming it, even when it doesn't fit
        DEFAULT_PNR_RE's strict shape -- see this module's top-level
        comment above PNR_CONTEXT_KEYWORD_RE for why this is a deliberate
        false-positive-tolerant tradeoff, not a silent loosening of the
        regex layer.
        """
        found = []
        for keyword_match in PNR_CONTEXT_KEYWORD_RE.finditer(text):
            window_start = keyword_match.end()
            window_end = min(len(text), window_start + _CONTEXT_WINDOW_CHARS)
            for token_match in _CONTEXT_TOKEN_RE.finditer(text, window_start, window_end):
                token = token_match.group()
                if token.lower() in _CONTEXT_STOPWORDS:
                    continue
                found.append(
                    (
                        token_match.start(),
                        token_match.end(),
                        "PNR",
                        f"alphanumeric code near {keyword_match.group()!r} keyword (context-aware, "
                        f"doesn't match the strict PNR shape)",
                    )
                )
                break  # only the first plausible token after each keyword
        return found

    def _ner_detections(self, text: str) -> list[NERSpan]:
        try:
            return self.ner_backend.detect(text)
        except Exception as exc:  # noqa: BLE001 - fail safe, never crash masking
            logger.error("NER backend raised %s; continuing without NER for this call", exc)
            return []

    # -- fake generation --------------------------------------------------

    def _generate_fake(self, label: str, original: str) -> str:
        with self._lock:
            if label == "EMAIL":
                return self._faker.email()
            if label == "PHONE":
                return self._faker.phone_number()
            if label == "SSN":
                return self._faker.ssn()
            if label == "CREDIT_CARD":
                return self._faker.credit_card_number()
            if label == "IP_ADDRESS":
                return self._faker.ipv4()
            if label == "PERSON":
                return self._faker.name()
            if label == "LOCATION" or label == "ADDRESS":
                return self._faker.address().replace("\n", ", ")
            # PNR, API_KEY, OVERRIDE, and any unrecognized NER label:
            # shape-preserving random string of the same length/pattern.
            return _shape_preserving_fake(original, self._rng)

    # -- public API -------------------------------------------------------

    def mask_text(self, text: str, audit_log=None, request_id: Optional[str] = None) -> MaskResult:
        """Mask free text. If `audit_log` (an audit_log.AuditLog) is
        given, every masked span is logged with source="prompt_text" and
        `field` set to the detection label (e.g. "EMAIL") -- free text has
        no enumerable field set, so only what was actually masked is
        logged, never a per-possible-field "left_as_is" entry (see
        AUDIT_LOG_FORMAT.md's note on this). Audit logging is opt-in and
        off by default so calling mask_text() never touches disk unless
        the caller explicitly asks it to.
        """
        if not text:
            return MaskResult(masked_text=text, detections=[])

        raw_spans: list[tuple[int, int, str, str, str]] = []  # start,end,label,layer,reason

        for start, end in self.override_store.find_always_mask_spans(text):
            label = _classify_unknown(text[start:end])
            raw_spans.append((start, end, label, _LAYER_OVERRIDE, "manual override: always_mask"))

        for start, end, label, reason in self._regex_detections(text):
            raw_spans.append((start, end, label, _LAYER_REGEX, reason))

        for span in self._ner_detections(text):
            raw_spans.append(
                (span.start, span.end, span.label, _LAYER_NER, f"matched via NER ({span.label})")
            )

        for start, end, label, reason in self._context_detections(text):
            raw_spans.append((start, end, label, _LAYER_CONTEXT, reason))

        # never_mask suppresses regex/NER matches, but not explicit
        # always_mask matches on the exact same span (an explicit
        # instruction to mask wins over a pattern-based exemption).
        filtered = []
        for start, end, label, layer, reason in raw_spans:
            value = text[start:end]
            if layer != _LAYER_OVERRIDE and self.override_store.matches_never_mask_pattern(value):
                continue
            filtered.append((start, end, label, layer, reason))

        # Resolve overlaps: higher-priority layer wins, then longer match,
        # then earliest start.
        chosen: list[tuple[int, int, str, str, str]] = []
        occupied_until = -1
        for start, end, label, layer, reason in sorted(
            filtered, key=lambda s: (-_PRIORITY[s[3]], -(s[1] - s[0]), s[0])
        ):
            if any(not (end <= c_start or start >= c_end) for c_start, c_end, *_ in chosen):
                continue
            chosen.append((start, end, label, layer, reason))
        chosen.sort(key=lambda s: s[0])

        detections: list[Detection] = []
        result_parts = []
        cursor = 0
        for start, end, label, layer, reason in chosen:
            original = text[start:end]
            fake = self.vault.get_or_create_fake(
                original, lambda label=label, original=original: self._generate_fake(label, original)
            )
            result_parts.append(text[cursor:start])
            result_parts.append(fake)
            cursor = end
            detections.append(
                Detection(
                    start=start,
                    end=end,
                    original=original,
                    fake=fake,
                    label=label,
                    layer=layer,
                    reason=reason,
                )
            )
        result_parts.append(text[cursor:])

        if chosen:
            self.vault_manager.notify_mutated()

        if audit_log is not None and detections:
            audit_log.log_decisions(
                request_id=request_id or "unknown",
                session_id=self.session_id,
                source="prompt_text",
                decisions=[(d.label, "masked", d.reason, d.layer) for d in detections],
            )

        return MaskResult(masked_text="".join(result_parts), detections=detections)

    def mask_value(self, value: str) -> tuple[str, str, str]:
        """Mask a single scalar value as one unit (e.g. a DB cell), rather
        than scanning it for embedded PII within a larger text.

        Tries to match a known regex type against the *whole* value first
        (for a semantically realistic fake, e.g. a real-looking email);
        falls back to a shape-preserving fake otherwise. Used by
        record_masker.py for any column that doesn't qualify for a
        safe-by-shape exemption (boolean/date/numeric/low-cardinality
        enum) -- reusing this module's vault means a value seen both in
        free text and in a DB record gets the same fake either way.

        Returns (fake_value, label, layer) for audit attribution.
        """
        if not value:
            return value, "EMPTY", _LAYER_REGEX
        label = "OVERRIDE"
        layer = "shape-default"
        for pattern, cand_label in (
            (EMAIL_RE, "EMAIL"),
            (API_KEY_RE, "API_KEY"),
            (SSN_RE, "SSN"),
            (CREDIT_CARD_RE, "CREDIT_CARD"),
            (PHONE_RE, "PHONE"),
            (IPV4_RE, "IP_ADDRESS"),
            (self.pnr_re, "PNR"),
        ):
            if pattern.fullmatch(value):
                label = cand_label
                layer = _LAYER_REGEX
                break
        fake = self.vault.get_or_create_fake(
            value, lambda label=label, value=value: self._generate_fake(label, value)
        )
        self.vault_manager.notify_mutated()
        return fake, label, layer

    def unmask_text(self, text: str) -> str:
        if not text:
            return text
        result = text
        for fake in self.vault.all_fakes():
            original = self.vault.lookup_original(fake)
            if original is not None and fake in result:
                result = result.replace(fake, original)
        return result
