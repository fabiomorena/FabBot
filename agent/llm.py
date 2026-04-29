"""
Zentraler LLM-Client fuer FabBot.

- get_llm()       → Sonnet (Qualitaet, fuer alle Agents)
- get_fast_llm()  → Haiku  (Geschwindigkeit, fuer Supervisor-Routing)

Phase 71: Modelle via .env konfigurierbar.
ANTHROPIC_MODEL_SONNET – default: claude-sonnet-4-6
ANTHROPIC_MODEL_HAIKU  – default: claude-haiku-4-5-20251001

Phase 92: _warn_if_unusual() loggt eine Warning bei ungewöhnlichem Modell-String.
Kein hard crash – dokumentierte Tech-Debt bleibt bestehen, aber ein Tippfehler
in .env fällt jetzt beim Start auf statt erst beim ersten API-Call.
Format: claude-<name>[-<YYYYMMDD>]
Beispiele mit Datum:    claude-haiku-4-5-20251001
Beispiele ohne Datum:   claude-sonnet-4-6, claude-opus-4-7

Phase 95 (Issue #6): validate_models_on_startup() – harte Validierung beim Start.
Wird in _post_init() aufgerufen. RuntimeError wenn der Model-String komplett
ungültig ist (leer, kein 'claude-' Prefix).
_warn_if_unusual() bleibt als zweite Schicht für Laufzeit-Änderungen via .env.

Phase 116: _DEFAULT_SONNET auf claude-sonnet-4-6 aktualisiert (claude-sonnet-4-20250514
deprecated seit 14.04.2026, Retirement 15.06.2026).
_MODEL_PATTERN erweitert: optionales YYYYMMDD-Suffix (neue Modelle wie sonnet-4-6
und opus-4-7 haben kein Datums-Suffix mehr).
"""

import logging
import os
import re
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)

_DEFAULT_SONNET = "claude-sonnet-4-6"
_DEFAULT_HAIKU = "claude-haiku-4-5-20251001"

_llm: ChatAnthropic | None = None
_fast_llm: ChatAnthropic | None = None

# Phase 92/116: Pattern erkennt offensichtliche Tippfehler.
# Optionales YYYYMMDD-Suffix – neue Modelle (sonnet-4-6, opus-4-7) haben keins mehr.
# Gültig:   claude-sonnet-4-6, claude-opus-4-7, claude-haiku-4-5-20251001
# Ungültig:  claud-sonnet-4-6, "" (leer), claude- (kein Name)
_MODEL_PATTERN = re.compile(r"^claude-.+(-\d{8})?$")


def _warn_if_unusual(model: str) -> None:
    """
    Loggt eine WARNING wenn der Modell-String ungewöhnlich aussieht.
    Kein Exception – nur Hinweis für den Nutzer beim Start.
    Zweite Schicht hinter validate_models_on_startup() (Phase 95).
    """
    if not _MODEL_PATTERN.match(model):
        logger.warning(
            f"llm.py: Ungewöhnlicher Modell-String '{model}' – "
            "Tippfehler in ANTHROPIC_MODEL_SONNET/HAIKU? "
            f"Erwartetes Format: claude-<name>[-<YYYYMMDD>] "
            f"(Beispiele: {_DEFAULT_SONNET}, {_DEFAULT_HAIKU})"
        )


def get_sonnet_model() -> str:
    """Gibt den konfigurierten Sonnet-Modell-String zurueck."""
    return os.getenv("ANTHROPIC_MODEL_SONNET", _DEFAULT_SONNET).strip()


def get_haiku_model() -> str:
    """Gibt den konfigurierten Haiku-Modell-String zurueck."""
    return os.getenv("ANTHROPIC_MODEL_HAIKU", _DEFAULT_HAIKU).strip()


def validate_models_on_startup() -> None:
    """
    Phase 95 (Issue #6): Harte Validierung beider Model-Strings beim Start.

    Wird in _post_init() aufgerufen – vor den Schedulern und dem ersten LLM-Call.
    Wirft RuntimeError wenn ein Model-String:
    - leer ist
    - nicht mit 'claude-' beginnt
    - nicht dem Pattern claude-<name>[-<YYYYMMDD>] entspricht

    Warum RuntimeError statt Warning:
    Ein ungültiger Model-String macht den Bot komplett unbrauchbar –
    jeder LLM-Call würde mit einem kryptischen Anthropic-API-Fehler scheitern.
    Fail-closed beim Start ist sicherer als ein Bot der scheinbar läuft aber
    nichts kann. Konsistent mit ALLOWED_IDS-Pattern (Phase 84).

    _warn_if_unusual() bleibt als zweite Schicht aktiv für den Fall dass
    jemand das Modell zur Laufzeit via .env ändert (get_llm/get_fast_llm).
    """
    errors: list[str] = []

    sonnet = get_sonnet_model()
    haiku = get_haiku_model()

    for name, model in [("ANTHROPIC_MODEL_SONNET", sonnet), ("ANTHROPIC_MODEL_HAIKU", haiku)]:
        if not model:
            errors.append(f"{name} ist leer.")
        elif not _MODEL_PATTERN.match(model):
            errors.append(
                f"{name}='{model}' ist ungültig – "
                f"Erwartetes Format: claude-<name>[-<YYYYMMDD>] "
                f"(Beispiele: {_DEFAULT_SONNET}, {_DEFAULT_HAIKU})"
            )

    if errors:
        msg = "llm.py: Ungültige Model-Konfiguration – Bot wird nicht gestartet:\n" + "\n".join(
            f"  • {e}" for e in errors
        )
        logger.critical(msg)
        raise RuntimeError(msg)

    logger.info(f"llm.py: Model-Validierung OK – Sonnet='{sonnet}', Haiku='{haiku}'")


def get_llm() -> ChatAnthropic:
    """Gibt den Sonnet-Client zurueck (lazy singleton).
    Singleton wird invalidiert wenn ANTHROPIC_MODEL_SONNET sich aendert.
    Phase 92: Loggt Warning bei ungewöhnlichem Modell-String.
    """
    global _llm
    model = get_sonnet_model()
    if _llm is None or _llm.model != model:
        _warn_if_unusual(model)
        _llm = ChatAnthropic(model=model)
    return _llm


def get_fast_llm() -> ChatAnthropic:
    """Gibt den Haiku-Client zurueck (lazy singleton).
    Fuer den Supervisor – schnelles Routing, ~4x schneller als Sonnet.
    Singleton wird invalidiert wenn ANTHROPIC_MODEL_HAIKU sich aendert.
    Phase 92: Loggt Warning bei ungewöhnlichem Modell-String.
    """
    global _fast_llm
    model = get_haiku_model()
    if _fast_llm is None or _fast_llm.model != model:
        _warn_if_unusual(model)
        _fast_llm = ChatAnthropic(model=model)
    return _fast_llm
