"""
agent/crypto.py – At-Rest-Encryption für FabBot.

Verschlüsselt personal_profile.yaml mit Fernet (AES-128-CBC + HMAC-SHA256).
Der Encryption-Key wird im macOS Keychain gespeichert – nie auf Disk.

Ablauf:
- Erster Start: Fernet-Key generieren → Keychain (fabbot/profile_key)
- Danach: Key immer aus Keychain lesen
- encrypt(text) → FABBOT_ENC_V1:<fernet_token>
- decrypt(bytes) → plaintext

Migration:
- Bestehende plain-YAML-Dateien werden beim ersten Load automatisch verschlüsselt.
- is_encrypted() erkennt ob eine Datei bereits verschlüsselt ist.
"""

import logging
from cryptography.fernet import Fernet, InvalidToken
import keyring

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "fabbot"
_KEYRING_USERNAME = "profile_key"
_ENC_HEADER = b"FABBOT_ENC_V1:"

# Singleton – einmal geladen, für die Laufzeit gecacht
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Gibt den Fernet-Instance zurück. Lädt oder generiert den Key aus dem Keychain."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key_str = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)

    if key_str is None:
        key = Fernet.generate_key()
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key.decode())
        logger.info("Neuer Fernet-Key generiert und im Keychain gespeichert (fabbot/profile_key).")
        _fernet = Fernet(key)
    else:
        _fernet = Fernet(key_str.encode())
        logger.debug("Fernet-Key aus Keychain geladen.")

    return _fernet


def is_encrypted(data: bytes) -> bool:
    """Gibt True zurück wenn die Daten mit FabBot-Encryption verschlüsselt sind."""
    return data.startswith(_ENC_HEADER)


def encrypt(plaintext: str) -> bytes:
    """Verschlüsselt einen String → FABBOT_ENC_V1:<fernet_token>."""
    f = _get_fernet()
    token = f.encrypt(plaintext.encode("utf-8"))
    return _ENC_HEADER + token


def decrypt(data: bytes) -> str:
    """Entschlüsselt einen FabBot-Blob → plaintext String.
    Wirft ValueError wenn die Daten kein gültiger FabBot-Blob sind.
    Wirft InvalidToken wenn der Key nicht passt.
    """
    if not is_encrypted(data):
        raise ValueError("Kein gültiger FabBot-Encrypted-Blob (fehlendes Header).")
    f = _get_fernet()
    token = data[len(_ENC_HEADER):]
    try:
        return f.decrypt(token).decode("utf-8")
    except InvalidToken as e:
        raise InvalidToken(f"Entschlüsselung fehlgeschlagen – falscher Key oder korrupte Daten: {e}") from e
