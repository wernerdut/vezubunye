"""Integration secrets at rest (Yoco keys, webhook secrets, Xero tokens). Fernet keyed from
SECRETS_ENC_KEY (a Fernet key or any passphrase, stretched). Ported from RIPS xero_api/crypto.
Never logged, never serialised to any client."""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class SecretCryptoError(RuntimeError):
    pass


def _key() -> str:
    return os.getenv("SECRETS_ENC_KEY") or os.getenv("XERO_TOKEN_ENC_KEY") or "dev-only-secrets-key"


def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except Exception:  # noqa: BLE001
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt(plain: str) -> str:
    return _fernet(_key()).encrypt(plain.encode()).decode()


def decrypt(cipher: str) -> str:
    try:
        return _fernet(_key()).decrypt(cipher.encode()).decode()
    except InvalidToken as e:
        raise SecretCryptoError("Stored secret cannot be decrypted with the current key") from e
