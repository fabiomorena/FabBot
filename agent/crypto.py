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

Phase 92: _get_fernet() fängt keyring-Fehler ab und wirft RuntimeError mit
klarer Meldung statt kryptischer DBusException / NoKeyringError.
"""

import logging
from cryptography.fernet import Fernet, InvalidToken
import keyring

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "fabbot"
_KEYRING_USERNAME = "profile_key"
_ENC_HEADER = b"FABBOT_ENC_V1:"

# Singleton – einmal geladen, für die Laufzeit gecacht.
# Key-Rotation (z.B. nach Security-Incident) erfordert Bot-Neustart.
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """
    Gibt den Fernet-Instance zurück. Lädt oder generiert den Key aus dem Keychain.

    Phase 92: Keyring-Fehler werden abgefangen und als RuntimeError mit
    klarer Meldung weitergegeben. Verhindert kryptische DBusException /
    NoKeyringError / AttributeError bei fehlender Keychain-Umgebung.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    try:
        key_str = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception as e:
        raise RuntimeError(
            f"Keychain nicht verfügbar ({type(e).__name__}: {e}). "
            "FabBot benötigt macOS Keychain oder einen kompatiblen Secret Store. "
            "Auf macOS: Keychain Access prüfen. Unter Linux: gnome-keyring oder "
            "pass als Keyring-Backend konfigurieren."
        ) from e

    if key_str is None:
        key = Fernet.generate_key()
        try:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key.decode())
        except Exception as e:
            raise RuntimeError(
                f"Fernet-Key konnte nicht im Keychain gespeichert werden "
                f"({type(e).__name__}: {e}). "
                "Keychain-Schreibzugriff prüfen."
            ) from e
        logger.warning(
            "Neuer Fernet-Key generiert (fabbot/profile_key). "
            "Falls zuvor ein anderes Keychain-Backend aktiv war, sind bestehende "
            "verschlüsselte Daten (personal_profile.yaml) mit diesem Key NICHT lesbar – "
            "Backup prüfen oder Datei neu verschlüsseln."
        )
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
    token = data[len(_ENC_HEADER) :]
    try:
        return f.decrypt(token).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Entschlüsselung fehlgeschlagen – falscher Key oder korrupte Daten.") from e
