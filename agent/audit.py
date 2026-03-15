import logging
import json
import os
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


def log_action(
    agent: str,
    action: str,
    detail: str,
    telegram_user_id: int | None = None,
    status: str = "executed",
) -> None:
    """
    Schreibt eine Aktion in das Audit-Log.
    status: 'executed' | 'confirmed' | 'rejected' | 'blocked'
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "action": action,
        "detail": detail[:500],  # Länge begrenzen
        "user_id": telegram_user_id,
        "status": status,
    }
    audit_logger.info(json.dumps(entry, ensure_ascii=False))


def log_blocked(reason: str, input_text: str, telegram_user_id: int | None = None) -> None:
    log_action(
        agent="security",
        action="blocked",
        detail=f"reason={reason} | input={input_text[:200]}",
        telegram_user_id=telegram_user_id,
        status="blocked",
    )
