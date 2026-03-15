import logging
import json
import re
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG_PATH = Path.home() / ".fabbot" / "audit.log"
AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

audit_logger = logging.getLogger("fabbot.audit")
audit_logger.setLevel(logging.INFO)

_handler = logging.FileHandler(AUDIT_LOG_PATH)
_handler.setFormatter(logging.Formatter("%(message)s"))
audit_logger.addHandler(_handler)
audit_logger.propagate = False

# Patterns die niemals ins Log duerfen
_SENSITIVE_PATTERNS = [
    r"sk-ant-[A-Za-z0-9\-]+",       # Anthropic API Keys
    r"ghp_[A-Za-z0-9]+",            # GitHub Tokens
    r"password\s*[=:]\s*\S+",       # Passwoerter
    r"token\s*[=:]\s*\S+",          # Tokens
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # E-Mail Adressen
]


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
