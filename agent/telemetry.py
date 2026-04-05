"""
LangSmith Telemetry für FabBot – Phase 85.

Aktivierung via .env:
  LANGCHAIN_TRACING_V2=true
  LANGCHAIN_API_KEY=ls__...
  LANGCHAIN_PROJECT=FabBot        (optional, default: "FabBot")
  LANGCHAIN_ENDPOINT=...          (optional, default: LangSmith Cloud)

Wenn LANGCHAIN_TRACING_V2 nicht "true": kein-op, kein Overhead.

LangChain/LangGraph liest die Env-Vars automatisch – setup_telemetry()
validiert die Konfiguration und loggt den Status beim Start.

Fail-safe: Fehler werden geloggt, nie weitergereicht.
"""

import logging
import os

logger = logging.getLogger(__name__)


def setup_telemetry() -> None:
    """
    Validiert und aktiviert LangSmith Telemetry.
    Wird einmalig beim Bot-Start in _post_init aufgerufen.

    LangChain/LangGraph liest LANGCHAIN_* Env-Vars automatisch –
    diese Funktion prüft nur ob die Konfiguration vollständig ist
    und gibt einen klaren Log-Status aus.
    """
    tracing = os.getenv("LANGCHAIN_TRACING_V2", "").strip().lower()

    if tracing != "true":
        logger.debug(
            "LangSmith Telemetry deaktiviert "
            "(LANGCHAIN_TRACING_V2 nicht gesetzt oder nicht 'true')."
        )
        return

    api_key = os.getenv("LANGCHAIN_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "LANGCHAIN_TRACING_V2=true aber LANGCHAIN_API_KEY fehlt – "
            "Telemetry wird nicht aktiviert. "
            "API-Key unter https://smith.langchain.com erstellen."
        )
        return

    project  = os.getenv("LANGCHAIN_PROJECT", "FabBot")
    endpoint = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")

    try:
        import langsmith  # noqa: F401 – prüft ob Paket installiert ist
        logger.info(
            f"LangSmith Telemetry aktiv – "
            f"Projekt: '{project}' | Endpoint: {endpoint}"
        )
    except ImportError:
        logger.warning(
            "langsmith-Paket nicht installiert – Telemetry nicht verfügbar. "
            "Installieren: .venv/bin/pip install langsmith"
        )
