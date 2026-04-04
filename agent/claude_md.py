"""
claude.md Loader fuer FabBot – Phase 62/63/65.

Phase 65 Fixes:
- TOCTOU: _CLAUDE_MD_PATH.exists() jetzt innerhalb des Locks
- reload_claude_md(): Cache-Invalidierung innerhalb des Locks
- Newline-Sanitizing: text wird vor dem Schreiben bereinigt (Defense-in-Depth)
- Size-Warning: Warnung wenn claude.md > 5000 Zeichen
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
_SIZE_WARNING_CHARS = 5000


def load_claude_md() -> str:
    """Laedt claude.md. Gecacht nach erstem Aufruf."""
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
        return _claude_md_cache
    except Exception as e:
        logger.error(f"Fehler beim Laden von claude.md (ignoriert): {e}")
        _claude_md_cache = ""
        return _claude_md_cache


def reload_claude_md() -> str:
    """Erzwingt Neu-Laden von claude.md. Cache wird geleert."""
    global _claude_md_cache
    _claude_md_cache = None
    return load_claude_md()


async def append_to_claude_md(text: str) -> bool:
    """
    Haengt eine neue Bot-Instruktion an claude.md an.

    Phase 65 Fixes:
    - Newline-Sanitizing: Zeilenumbrueche in text werden entfernt (Injection-Schutz)
    - TOCTOU: exists()-Check und write() innerhalb desselben Locks
    - Cache-Invalidierung innerhalb des Locks (vor dem Release)
    - Size-Warning wenn claude.md > 5000 Zeichen
    """
    if not text or not text.strip():
        return False

    # Sanitize: Newlines entfernen – verhindert Struktur-Injection in claude.md
    clean_text = text.strip().replace("\n", " ").replace("\r", "").strip()
    if not clean_text:
        return False

    try:
        global _claude_md_cache
        async with _write_lock:
            # TOCTOU-Fix: exists()-Check innerhalb des Locks
            if not _CLAUDE_MD_PATH.exists():
                logger.error("claude.md nicht gefunden – append_to_claude_md abgebrochen.")
                return False

            content = _CLAUDE_MD_PATH.read_text(encoding="utf-8")
            timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
            new_line = f"- {clean_text} _(gelernt {timestamp})_"

            if _AUTO_SECTION in content:
                content = content.rstrip() + "\n" + new_line + "\n"
            else:
                content = content.rstrip() + f"\n\n{_AUTO_SECTION}\n" + new_line + "\n"

            _CLAUDE_MD_PATH.write_text(content, encoding="utf-8")

            # Cache-Invalidierung innerhalb des Locks (vor dem Release)
            _claude_md_cache = None

            # Size-Warning
            if len(content) > _SIZE_WARNING_CHARS:
                logger.warning(
                    f"claude.md ist sehr lang ({len(content)} Zeichen) – "
                    f"manuelle Bereinigung empfohlen (> {_SIZE_WARNING_CHARS} Zeichen)"
                )

        # Frischen Inhalt laden (ausserhalb des Locks – atomic read nach Write)
        load_claude_md()
        logger.info(f"claude.md: Bot-Instruktion gespeichert: {clean_text[:80]}")
        return True

    except Exception as e:
        logger.error(f"Fehler beim Schreiben in claude.md: {e}")
        return False
