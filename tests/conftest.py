"""
tests/conftest.py – Globale Test-Fixtures für FabBot.

Isoliert alle Modul-Level-Singletons zwischen Tests via autouse Fixtures.
Verhindert State-Leaks die zu flaky Tests führen können.

Betroffene Singletons:
- _rate_limit_store  (agent/security.py)
- _tts_enabled       (bot/tts.py)
- _current_afplay    (bot/tts.py)
- _profile_cache     (agent/profile.py)
- _pending           (bot/confirm.py)

Phase 87 Fix: TELEGRAM_ALLOWED_USER_IDS muss vor dem Import von bot.auth
gesetzt sein, da _load_allowed_ids() auf Modul-Ebene ausgeführt wird.
setdefault() überschreibt keine echte .env-Werte (lokal/CI-Secrets).
"""

import os

# Muss VOR allen anderen Imports stehen – bot.auth lädt ALLOWED_IDS beim Import.
os.environ.setdefault("TELEGRAM_ALLOWED_USER_IDS", "123456789")

import copy
import pytest


@pytest.fixture(autouse=True)
def reset_rate_limit_store():
    """Leert den Rate-Limit-Store vor und nach jedem Test.
    Verhindert dass Rate-Limit-Tests sich gegenseitig beeinflussen.
    """
    from agent.security import _rate_limit_store

    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()


@pytest.fixture(autouse=True)
def reset_tts_state():
    """Stellt TTS-State vor und nach jedem Test wieder her.
    Verhindert dass toggle-Tests den State für nachfolgende Tests verändern.
    """
    import bot.tts as tts_module

    original_enabled = tts_module._tts_enabled
    tts_module._current_afplay = None
    yield
    tts_module._tts_enabled = original_enabled
    tts_module._current_afplay = None


@pytest.fixture(autouse=True)
def reset_profile_cache():
    """Stellt den Profil-Cache vor und nach jedem Test wieder her.
    Verhindert dass gemockte Profile in nachfolgenden Tests sichtbar bleiben.
    deepcopy verhindert dass in-place Mutationen (z.B. .update()) den
    gespeicherten Original-Cache verändern.
    """
    import agent.profile as profile_module

    original_cache = copy.deepcopy(profile_module._profile_cache)
    yield
    profile_module._profile_cache = original_cache


@pytest.fixture(autouse=True)
def reset_confirm_pending():
    """Leert den HITL-Pending-Dict vor und nach jedem Test.
    Verhindert dass hängende Futures aus einem Test den nächsten blockieren.
    """
    from bot.confirm import _pending

    _pending.clear()
    yield
    _pending.clear()


@pytest.fixture(autouse=True)
def mock_keychain():
    """Verhindert echte Keychain-Zugriffe in Tests.
    Nutzt einen zufälligen Fernet-Key pro Test-Session – kein Keychain nötig.
    """
    from cryptography.fernet import Fernet
    import agent.crypto as crypto_module

    original_fernet = crypto_module._fernet
    crypto_module._fernet = Fernet(Fernet.generate_key())
    yield
    crypto_module._fernet = original_fernet
