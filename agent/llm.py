"""
Zentraler LLM-Client fuer FabBot.

- get_llm()       → Sonnet (Qualitaet, fuer alle Agents)
- get_fast_llm()  → Haiku  (Geschwindigkeit, fuer Supervisor-Routing)

Phase 71: Modelle via .env konfigurierbar.
ANTHROPIC_MODEL_SONNET – default: claude-sonnet-4-20250514
ANTHROPIC_MODEL_HAIKU  – default: claude-haiku-4-5-20251001

Phase 92: _warn_if_unusual() loggt eine Warning bei ungewöhnlichem Modell-String.
Kein hard crash – dokumentierte Tech-Debt bleibt bestehen, aber ein Tippfehler
in .env fällt jetzt beim Start auf statt erst beim ersten API-Call.
Format: claude-<name>-<version(s)>-<YYYYMMDD>
"""
import logging
import os
import re
from langchain_anthropic import ChatAnthropic

logger = logging.getLogger(__name__)

_DEFAULT_SONNET = "claude-sonnet-4-20250514"
_DEFAULT_HAIKU  = "claude-haiku-4-5-20251001"

_llm: ChatAnthropic | None = None
_fast_llm: ChatAnthropic | None = None

# Phase 92: Einfaches Pattern – erkennt offensichtliche Tippfehler.
# claude-<nicht-leer>-<8-stellige-Zahl>
# Gültig: claude-sonnet-4-20250514, claude-haiku-4-5-20251001
# Ungültig: claud-sonnet-4-20250514, claude-sonnet, "" (leer)
_MODEL_PATTERN = re.compile(r"^claude-.+-\d{8}$")


def _warn_if_unusual(model: str) -> None:
    """
    Loggt eine WARNING wenn der Modell-String ungewöhnlich aussieht.
    Kein Exception – nur Hinweis für den Nutzer beim Start.
    """
    if not _MODEL_PATTERN.match(model):
        logger.warning(
            f"llm.py: Ungewöhnlicher Modell-String '{model}' – "
            "Tippfehler in ANTHROPIC_MODEL_SONNET/HAIKU? "
            f"Erwartetes Format: claude-<name>-<YYYYMMDD> "
            f"(Beispiel: {_DEFAULT_SONNET})"
        )


def get_sonnet_model() -> str:
    """Gibt den konfigurierten Sonnet-Modell-String zurueck."""
    return os.getenv("ANTHROPIC_MODEL_SONNET", _DEFAULT_SONNET).strip()


def get_haiku_model() -> str:
    """Gibt den konfigurierten Haiku-Modell-String zurueck."""
    return os.getenv("ANTHROPIC_MODEL_HAIKU", _DEFAULT_HAIKU).strip()


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
