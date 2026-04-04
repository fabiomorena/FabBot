"""
claude.md Loader fuer FabBot – Phase 62.

Laedt persistente Bot-Instruktionen aus claude.md im Projektwurzel.
Wird in den chat_agent System-Prompt injiziert und ueberlebt den Context Trim.

Eigenschaften:
- Fail-safe: fehlendes oder kaputtes File → leerer String, kein Crash
- Gecacht nach erstem Aufruf (wie profile.py)
- Kein LLM, keine Verschluesselung – plain Markdown
- Reload via reload_claude_md() nach manueller Aenderung (oder Bot-Neustart)

Aenderungen an claude.md erfordern einen Bot-Neustart:
    launchctl stop com.fabbot.agent && launchctl start com.fabbot.agent
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CLAUDE_MD_PATH = Path(__file__).parent.parent / "claude.md"
_claude_md_cache: str | None = None


def load_claude_md() -> str:
    """
    Laedt claude.md. Gecacht nach erstem Aufruf.
    Gibt leeren String zurueck wenn Datei fehlt, leer ist oder ein Fehler auftritt.
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
            logger.debug("claude.md ist leer – kein persistenter Bot-Kontext.")
        return _claude_md_cache
    except Exception as e:
        logger.error(f"Fehler beim Laden von claude.md (ignoriert): {e}")
        _claude_md_cache = ""
        return _claude_md_cache


def reload_claude_md() -> str:
    """
    Erzwingt Neu-Laden von claude.md aus der Datei.
    Wird nach manueller Aenderung aufgerufen wenn kein Bot-Neustart gewuenscht ist.
    """
    global _claude_md_cache
    _claude_md_cache = None
    return load_claude_md()
