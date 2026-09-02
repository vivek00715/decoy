import uuid

from decoy.vault import VaultManager


def test_persisted_vault_round_trips_across_instances(tmp_path):
    vault_path = tmp_path / "vault.enc"
    key_path = tmp_path / "vault.key"
    session_id = str(uuid.uuid4())

    manager1 = VaultManager(persist=True, vault_path=vault_path, key_path=key_path)
    vault1 = manager1.get_vault(session_id)
    fake = vault1.get_or_create_fake("alice@example.com", lambda: "zzz@example.com")
    manager1.notify_mutated()

    assert vault_path.exists()
    assert key_path.exists()
    raw = vault_path.read_bytes()
    assert b"alice@example.com" not in raw
    assert b"zzz@example.com" not in raw

    manager2 = VaultManager(persist=True, vault_path=vault_path, key_path=key_path)
    vault2 = manager2.get_vault(session_id)
    assert vault2.lookup_fake("alice@example.com") == fake
    assert vault2.lookup_original(fake) == "alice@example.com"


def test_wrong_key_cannot_decrypt_persisted_vault(tmp_path):
    vault_path = tmp_path / "vault.enc"
    key_path = tmp_path / "vault.key"
    session_id = str(uuid.uuid4())

    manager1 = VaultManager(persist=True, vault_path=vault_path, key_path=key_path)
    manager1.get_vault(session_id).get_or_create_fake("bob@example.com", lambda: "yyy@example.com")
    manager1.notify_mutated()

    # a different key path (so a fresh key is generated) must not be able
    # to read the file encrypted under the first key -- fails safe to an
    # empty vault rather than crashing or silently decrypting garbage
    other_key_path = tmp_path / "other_vault.key"
    manager2 = VaultManager(persist=True, vault_path=vault_path, key_path=other_key_path)
    assert manager2.get_vault(session_id).lookup_fake("bob@example.com") is None
