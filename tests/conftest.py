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
"""

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
    """
    import agent.profile as profile_module
    original_cache = profile_module._profile_cache
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