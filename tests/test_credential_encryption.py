import pytest

import machreach_core.db as odb


def test_encrypted_credentials_fail_explicitly_with_wrong_key(monkeypatch):
    encrypted = odb.encrypt_password("smtp-secret")
    monkeypatch.setattr(odb, "ENCRYPTION_KEY", "different-deployment-key")

    with pytest.raises(odb.CredentialDecryptionError, match="shared ENCRYPTION_KEY"):
        odb.decrypt_password(encrypted)


def test_legacy_plaintext_credentials_remain_readable():
    assert odb.decrypt_password("legacy-password") == "legacy-password"
