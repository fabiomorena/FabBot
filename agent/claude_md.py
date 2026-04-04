"""
claude.md Loader fuer FabBot – Phase 62/63.

Phase 62: Laden + Cachen persistenter Bot-Instruktionen
Phase 63: append_to_claude_md() – Bot kann neue Instruktionen dauerhaft speichern

Workflow fuer neue Bot-Instruktionen:
    User: "Merke dir grundsaetzlich dass du immer X"
    → memory_agent erkennt Kategorie bot_instruction
    → append_to_claude_md() schreibt in ## Automatisch gelernt
    → reload_claude_md() leert Cache sofort
    → _build_chat_prompt() in chat_agent laeuft dynamisch → naechster Call aktiv

Aenderungen wirken SOFORT (kein Bot-Neustart noetig).
Manuelle Aenderungen → Bot neu starten.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CLAUDE_MD_PATH = Path(__file__).parent.parent / "claude.md"
_claude_md_cache: str | None = None
_write_lock = asyncio.Lock()

_AUTO_SECTION = "## Automatisch gelernt"


def load_claude_md() -> str:
    """
    Laedt claude.md. Gecacht nach erstem Aufruf.
    Gibt leeren String zurueck wenn Datei fehlt oder Fehler auftritt.
    """
    global _claude_md_cache
    if _claude_md_cache is not None:
        return _claude_md_cache

    if not _CLAUDE_MD_PATH.exists():
        logger.debug("claude.md nicht gefunden – kein persistenter Bot-Kontext geladen.")
        _claude_md_cache = ""
        return _claude_md_cache

    try:
        content = _CLAUDE_MD_PATH.read_text(encoding="utf-8").strip()
        _claude_md_cache = content
        if content:
            logger.info(f"claude.md geladen: {len(content)} Zeichen aus {_CLAUDE_MD_PATH}")
        else:
            logger.debug("claude.md ist leer.")
        return _claude_md_cache
    except Exception as e:
        logger.error(f"Fehler beim Laden von claude.md (ignoriert): {e}")
        _claude_md_cache = ""
        return _claude_md_cache


def reload_claude_md() -> str:
    """Erzwingt Neu-Laden. Wird nach append_to_claude_md() automatisch aufgerufen."""
    global _claude_md_cache
    _claude_md_cache = None
    return load_claude_md()


async def append_to_claude_md(text: str) -> bool:
    """
    Haengt eine neue Bot-Instruktion an claude.md an.
    Erstellt ## Automatisch gelernt falls nicht vorhanden.
    Leert Cache sofort – wirkt beim naechsten chat_agent-Call.
    Thread-safe via asyncio.Lock.
    """
    if not text or not text.strip():
        return False

    if not _CLAUDE_MD_PATH.exists():
        logger.error("claude.md nicht gefunden – append_to_claude_md abgebrochen.")
        return False

    try:
        async with _write_lock:
            content = _CLAUDE_MD_PATH.read_text(encoding="utf-8")
            timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
            new_line = f"- {text.strip()} _(gelernt {timestamp})_"

            if _AUTO_SECTION in content:
                content = content.rstrip() + "\n" + new_line + "\n"
            else:
                content = content.rstrip() + f"\n\n{_AUTO_SECTION}\n" + new_line + "\n"

            _CLAUDE_MD_PATH.write_text(content, encoding="utf-8")

        reload_claude_md()
        logger.info(f"claude.md: Bot-Instruktion gespeichert: {text[:80]}")
        return True

    except Exception as e:
        logger.error(f"Fehler beim Schreiben in claude.md: {e}")
        return False
