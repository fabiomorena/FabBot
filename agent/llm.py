"""
Zentraler LLM-Client fuer FabBot.

- get_llm()       → Sonnet (Qualitaet, fuer alle Agents)
- get_fast_llm()  → Haiku  (Geschwindigkeit, fuer Supervisor-Routing)

Phase 71: Modelle via .env konfigurierbar.
ANTHROPIC_MODEL_SONNET – default: claude-sonnet-4-20250514
ANTHROPIC_MODEL_HAIKU  – default: claude-haiku-4-5-20251001

Der Modell-String wird direkt an die Anthropic API uebergeben.
Ungueltige Strings fuehren beim ersten API-Call zu einem Fehler, nicht beim Start.
"""
import os
from langchain_anthropic import ChatAnthropic

_DEFAULT_SONNET = "claude-sonnet-4-20250514"
_DEFAULT_HAIKU  = "claude-haiku-4-5-20251001"

_llm: ChatAnthropic | None = None
_fast_llm: ChatAnthropic | None = None


def get_sonnet_model() -> str:
    """Gibt den konfigurierten Sonnet-Modell-String zurueck."""
    return os.getenv("ANTHROPIC_MODEL_SONNET", _DEFAULT_SONNET).strip()


def get_haiku_model() -> str:
    """Gibt den konfigurierten Haiku-Modell-String zurueck."""
    return os.getenv("ANTHROPIC_MODEL_HAIKU", _DEFAULT_HAIKU).strip()


def get_llm() -> ChatAnthropic:
    """Gibt den Sonnet-Client zurueck (lazy singleton).
    Singleton wird invalidiert wenn ANTHROPIC_MODEL_SONNET sich aendert.
    """
    global _llm
    model = get_sonnet_model()
    if _llm is None or _llm.model != model:
        _llm = ChatAnthropic(model=model)
    return _llm


def get_fast_llm() -> ChatAnthropic:
    """Gibt den Haiku-Client zurueck (lazy singleton).
    Fuer den Supervisor – schnelles Routing, ~4x schneller als Sonnet.
    Singleton wird invalidiert wenn ANTHROPIC_MODEL_HAIKU sich aendert.
    """
    global _fast_llm
    model = get_haiku_model()
    if _fast_llm is None or _fast_llm.model != model:
        _fast_llm = ChatAnthropic(model=model)
    return _fast_llm
