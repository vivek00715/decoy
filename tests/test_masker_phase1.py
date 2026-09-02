import json
import uuid

import pytest

from decoy.masker import Masker
from decoy.overrides import OverrideStore
from decoy.vault import VaultManager


@pytest.fixture
def vault_manager():
    return VaultManager(persist=False)


@pytest.fixture
def overrides_file(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps(
            {
                "always_mask": {"patterns": [r"EMP-\d{6}"], "field_names": []},
                "never_mask": {
                    "patterns": [r"support@decoy\.example\.com"],
                    "field_names": [],
                },
            }
        )
    )
    return path


@pytest.fixture
def override_store(overrides_file):
    return OverrideStore(path=overrides_file)


def make_masker(vault_manager, override_store, session_id=None):
    return Masker(
        session_id=session_id or str(uuid.uuid4()),
        vault_manager=vault_manager,
        override_store=override_store,
    )


def test_round_trip_paragraph(vault_manager, override_store):
    masker = make_masker(vault_manager, override_store)
    text = (
        "Please reach out to jane.doe@example.com or call 415-555-2671 "
        "about booking ref AB12CD before Friday."
    )
    result = masker.mask_text(text)

    assert "jane.doe@example.com" not in result.masked_text
    assert "415-555-2671" not in result.masked_text
    assert "AB12CD" not in result.masked_text

    labels = {d.label for d in result.detections}
    assert "EMAIL" in labels
    assert "PHONE" in labels
    assert "PNR" in labels

    unmasked = masker.unmask_text(result.masked_text)
    assert unmasked == text


def test_consistent_fake_across_calls(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    masker1 = make_masker(vault_manager, override_store, session_id)
    masker2 = make_masker(vault_manager, override_store, session_id)

    r1 = masker1.mask_text("Contact: alice@example.com")
    r2 = masker2.mask_text("Email alice@example.com again")

    fake1 = next(d.fake for d in r1.detections if d.label == "EMAIL")
    fake2 = next(d.fake for d in r2.detections if d.label == "EMAIL")
    assert fake1 == fake2


def test_always_mask_override_forces_masking(vault_manager, override_store):
    masker = make_masker(vault_manager, override_store)
    text = "Internal reference EMP-123456 should not leak."
    result = masker.mask_text(text)

    assert "EMP-123456" not in result.masked_text
    override_detections = [d for d in result.detections if d.layer == "override"]
    assert len(override_detections) == 1
    assert override_detections[0].original == "EMP-123456"
    assert "always_mask" in override_detections[0].reason

    unmasked = masker.unmask_text(result.masked_text)
    assert unmasked == text


def test_never_mask_override_prevents_masking(vault_manager, override_store):
    masker = make_masker(vault_manager, override_store)
    text = "For help, email support@decoy.example.com right away."
    result = masker.mask_text(text)

    assert "support@decoy.example.com" in result.masked_text
    assert result.detections == []


def test_never_mask_does_not_suppress_always_mask_on_same_value(vault_manager):
    import json as _json

    store = OverrideStore.__new__(OverrideStore)
    # Build store manually with both rules matching the same literal text.
    from decoy.overrides import OverrideRules
    import re

    rules = OverrideRules(
        always_mask_patterns=[re.compile(r"SECRET-1")],
        never_mask_patterns=[re.compile(r"SECRET-1")],
    )
    store._rules = rules
    store._mtime = 0
    store._lock = __import__("threading").RLock()
    store.path = __import__("pathlib").Path("/nonexistent")
    store.reload = lambda force=False: None

    masker = Masker(session_id=str(uuid.uuid4()), vault_manager=vault_manager, override_store=store)
    result = masker.mask_text("Value SECRET-1 must be masked.")
    assert "SECRET-1" not in result.masked_text


def test_clear_session_forgets_mapping(vault_manager, override_store):
    session_id = str(uuid.uuid4())
    masker = make_masker(vault_manager, override_store, session_id)
    result = masker.mask_text("alice@example.com")
    fake = result.detections[0].fake

    vault_manager.clear_session(session_id)

    masker2 = make_masker(vault_manager, override_store, session_id)
    result2 = masker2.mask_text("alice@example.com")
    # After clearing, a fresh fake is minted (session forgot the prior mapping).
    new_fake = result2.detections[0].fake
    assert masker2.vault.lookup_original(fake) is None
    assert isinstance(new_fake, str) and new_fake != ""
