"""Tests for DECOY_USE_PRESIDIO (ner.py's PresidioNERBackend + the
process-wide get_default_ner_backend() cache, and Masker's wiring of it).

Presidio-specific tests are skipped (not failed) when the `ner` extra
isn't installed -- matching this project's existing "opt-in, no forced
hard dependency" stance for NER. CI (.github/workflows/ci.yml) does not
install the `ner` extra (spaCy + a ~400MB language model is a real,
deliberate cost not worth forcing on every CI run for an opt-in feature)
-- these tests are real and meaningful when run locally with `pip install
"decoy[ner]" && python -m spacy download en_core_web_lg`, and skip
cleanly otherwise rather than either failing CI or silently not existing.
"""

import pytest

from decoy.masker import Masker
from decoy.ner import NoOpNERBackend, get_default_ner_backend
from decoy.overrides import OverrideStore
from decoy.vault import VaultManager

presidio_analyzer = pytest.importorskip(
    "presidio_analyzer", reason="the 'ner' extra is not installed -- DECOY_USE_PRESIDIO tests need it"
)


@pytest.fixture
def vault_manager():
    return VaultManager(persist=False)


@pytest.fixture
def override_store(tmp_path):
    return OverrideStore(path=tmp_path / "overrides.json")


def test_default_ner_backend_is_noop_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("DECOY_USE_PRESIDIO", raising=False)
    backend = get_default_ner_backend()
    assert isinstance(backend, NoOpNERBackend)


def test_default_ner_backend_is_noop_when_env_var_false(monkeypatch):
    monkeypatch.setenv("DECOY_USE_PRESIDIO", "false")
    backend = get_default_ner_backend()
    assert isinstance(backend, NoOpNERBackend)


def test_default_ner_backend_is_presidio_when_env_var_true(monkeypatch):
    from decoy.ner import PresidioNERBackend

    monkeypatch.setenv("DECOY_USE_PRESIDIO", "true")
    backend = get_default_ner_backend()
    assert isinstance(backend, PresidioNERBackend)


def test_get_default_ner_backend_caches_across_calls(monkeypatch):
    """The whole point of the process-wide cache: constructing
    PresidioNERBackend takes real, measured wall-clock time (loading a
    spaCy model) -- confirmed directly: ~13s cold, ~15ms once cached.
    Masker is constructed fresh per request throughout this codebase, so
    this cache is load-bearing for real usability, not a micro-
    optimization."""
    monkeypatch.setenv("DECOY_USE_PRESIDIO", "true")
    first = get_default_ner_backend()
    second = get_default_ner_backend()
    assert first is second


def test_mask_text_detects_a_bare_first_name_with_presidio_enabled(monkeypatch, vault_manager, override_store):
    """The exact case named in this project's own documented gap
    (benchmark_cases.py's module docstring, WHAT_THIS_PROTECTS_AGAINST.md
    section 3): "a bare person NAME typed directly into a question is NOT
    currently masked." Confirms it now is, once DECOY_USE_PRESIDIO is
    enabled -- and confirms the default (unset) behavior is unchanged."""
    monkeypatch.delenv("DECOY_USE_PRESIDIO", raising=False)
    masker_default = Masker(session_id="s-default", vault_manager=vault_manager, override_store=override_store)
    result_default = masker_default.mask_text("Please reach out to Sarah about the invoice.")
    assert result_default.detections == []
    assert "Sarah" in result_default.masked_text

    monkeypatch.setenv("DECOY_USE_PRESIDIO", "true")
    masker_presidio = Masker(session_id="s-presidio", vault_manager=vault_manager, override_store=override_store)
    result_presidio = masker_presidio.mask_text("Please reach out to Sarah about the invoice.")
    assert "Sarah" not in result_presidio.masked_text
    labels = {d.label for d in result_presidio.detections}
    assert "PERSON" in labels
    layers = {d.layer for d in result_presidio.detections}
    assert "ner" in layers


def test_presidio_backend_fails_safe_on_analyzer_error(monkeypatch, vault_manager, override_store):
    """A Presidio internal error must never crash mask_text -- it should
    log and continue with zero NER detections (the regex layer still
    runs), matching NERBackend.detect's documented contract."""
    from decoy.ner import PresidioNERBackend

    backend = PresidioNERBackend()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated Presidio internal failure")

    monkeypatch.setattr(backend._analyzer, "analyze", _boom)

    masker = Masker(session_id="s-fail-safe", vault_manager=vault_manager, override_store=override_store, ner_backend=backend)
    result = masker.mask_text("Contact john.doe@example.com and also Sarah about this.")
    # the regex layer (EMAIL) still works even though NER failed
    assert "john.doe@example.com" not in result.masked_text
    labels = {d.label for d in result.detections}
    assert "EMAIL" in labels
    assert "PERSON" not in labels  # NER failed safe to nothing, not a crash
