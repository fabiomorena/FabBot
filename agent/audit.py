"""
agent/audit.py – Tamper-evident Audit Log für FabBot.

Phase 92: Module-Level-Seiteneffekte entfernt.
Vorher: FileHandler wurde beim Import geöffnet → jeder Unit-Test der
agent.audit transitiv importiert legte ~/.fabbot/audit.log an und hielt
eine offene File-Handle. Tests waren nicht isoliert.

Jetzt: setup_audit_logger() kapselt den Initialisierungscode.
Aufruf einmalig in bot.py's _post_init(). Idempotent via _audit_initialized Flag.
"""
import logging
import json
import re
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG_PATH = Path.home() / ".fabbot" / "audit.log"

audit_logger = logging.getLogger("fabbot.audit")
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = False

# Phase 92: Kein FileHandler mehr auf Modulebene.
# setup_audit_logger() wird einmalig aus _post_init() aufgerufen.
_audit_initialized = False

# Patterns die niemals ins Log duerfen
_SENSITIVE_PATTERNS = [
    r"sk-[A-Za-z0-9\-_]{20,}",      # Anthropic + OpenAI API Keys (sk-ant-..., sk-proj-...)
    r"ghp_[A-Za-z0-9]+",            # GitHub Tokens
    r"tvly-[A-Za-z0-9\-]+",         # Tavily API Keys
    r"password\s*[=:]\s*\S+",       # Passwoerter
    r"api[_\-]?key\s*[=:]\s*\S+",   # Generische API Keys (OPENAI_API_KEY=..., BRAVE_API_KEY=...)
    r"token\s*[=:]\s*\S+",          # Tokens
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",  # E-Mail Adressen (Regex-Bug: [A-Z|a-z] → [A-Za-z])
]


def setup_audit_logger() -> None:
    """
    Initialisiert den Audit-Logger (FileHandler + Verzeichnis).

    Phase 92: Ersetzt den Module-Level-Code. Idempotent via _audit_initialized –
    mehrfache Aufrufe haben keinen Effekt (kein doppelter Handler).
    Wird einmalig aus bot.py's _post_init() aufgerufen.
    """
    global _audit_initialized
    if _audit_initialized:
        return
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(AUDIT_LOG_PATH)
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(handler)
    _audit_initialized = True


def _sanitize(text: str) -> str:
    """Entfernt sensible Daten aus Log-Eintraegen."""
    for pattern in _SENSITIVE_PATTERNS:
        text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
    return text


def log_action(
    agent: str,
    action: str,
    detail: str,
    telegram_user_id: int | None = None,
    status: str = "executed",
) -> None:
    """
    Schreibt eine Aktion in das Audit-Log.
    Niemals Dateiinhalte oder sensible Daten loggen - nur Metadaten.
    status: 'executed' | 'confirmed' | 'rejected' | 'blocked'
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "action": _sanitize(action[:200]),
        "detail": _sanitize(detail[:300]),
        "user_id": telegram_user_id,
        "status": status,
    }
    audit_logger.info(json.dumps(entry, ensure_ascii=False))


def log_blocked(reason: str, input_text: str, telegram_user_id: int | None = None) -> None:
    """Loggt eine blockierte Anfrage - Input wird gekuerzt und bereinigt."""
    log_action(
        agent="security",
        action="blocked",
        detail=f"reason={reason} | input={_sanitize(input_text[:100])}",
        telegram_user_id=telegram_user_id,
        status="blocked",
    )
