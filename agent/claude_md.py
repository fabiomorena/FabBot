"""
claude.md Loader fuer FabBot – Phase 62/63/65/66/67.

Phase 67 Fixes:
- reload_claude_md(): load_claude_md() jetzt innerhalb des Locks
- _trim_auto_section(): robusterer Regex fuer alle Heading-Level
- _trim_auto_section(): Entry-Detection erkennt jetzt auch * und +
- load_claude_md(): Kommentar erklaert warum kein Lock noetig ist
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CLAUDE_MD_PATH = Path(__file__).parent.parent / "claude.md"
_claude_md_cache: str | None = None
_write_lock = asyncio.Lock()

_AUTO_SECTION = "## Automatisch gelernt"
_SIZE_WARNING_CHARS = 5000
_MAX_AUTO_ENTRIES = 50


def load_claude_md() -> str:
    """
    Laedt claude.md. Gecacht nach erstem Aufruf.

    Phase 67: Kein Lock noetig – asyncio ist single-threaded,
    einfache Zuweisungen zu Modul-Globals sind unter CPythons GIL atomar.
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
        # GIL macht diese Zuweisung atomar; asyncio single-threaded = kein Race
        _claude_md_cache = content
        if content:
            logger.info(f"claude.md geladen: {len(content)} Zeichen aus {_CLAUDE_MD_PATH}")
        return _claude_md_cache
    except Exception as e:
        logger.error(f"Fehler beim Laden von claude.md (ignoriert): {e}")
        _claude_md_cache = ""
        return _claude_md_cache


async def reload_claude_md() -> str:
    """
    Erzwingt Neu-Laden von claude.md. Thread-safe via _write_lock.

    Phase 66: async + Lock.
    Phase 67 Fix: load_claude_md() jetzt innerhalb des Locks –
    verhindert Race zwischen Lock-Release und Read.
    """
    global _claude_md_cache
    async with _write_lock:
        _claude_md_cache = None
        return load_claude_md()  # Read direkt im Lock – konsistente Lock-Semantik


def _trim_auto_section(content: str, max_entries: int = _MAX_AUTO_ENTRIES) -> str:
    """
    Trimmt ## Automatisch gelernt auf max. max_entries Eintraege (FIFO).
    Aelteste Eintraege werden entfernt, neueste bleiben erhalten.
    Andere Sektionen bleiben unveraendert.

    Phase 67 Fixes:
    - Robusterer Heading-Regex: erkennt alle Heading-Level (# bis ######)
    - Entry-Detection: erkennt jetzt auch * und + als Listenmarker
    """
    if _AUTO_SECTION not in content:
        return content

    # Content an der Auto-Sektion aufteilen
    before_section, _, section_and_rest = content.partition(_AUTO_SECTION)

    # Phase 67 Fix: robuster Heading-Regex fuer alle Heading-Level
    # Vorher: r'\n## ' → erkannte nur H2 mit exaktem Leerzeichen
    # Jetzt:  r'\n#{1,6} ' → erkennt H1-H6 + verhindert false-positives bei ###Sub
    next_match = re.search(r'\n#{1,6} ', section_and_rest)
    if next_match:
        section_body = section_and_rest[:next_match.start()]
        after_section = section_and_rest[next_match.start():]
    else:
        section_body = section_and_rest
        after_section = ""

    # Phase 67 Fix: Entry-Detection erkennt -, * und + als Listenmarker
    # append_to_claude_md() schreibt immer "- ", aber manuelle Eintraege
    # koennten auch "* " oder "+ " nutzen – alle werden jetzt korrekt gezaehlt
    lines = section_body.split('\n')
    entry_indices = [
        i for i, l in enumerate(lines)
        if re.match(r'\s*[-*+]\s', l)
    ]

    if len(entry_indices) <= max_entries:
        return content  # Kein Trim noetig

    # FIFO: aelteste Eintraege (erste) entfernen
    to_remove_count = len(entry_indices) - max_entries
    indices_to_remove = set(entry_indices[:to_remove_count])
    trimmed_lines = [l for i, l in enumerate(lines) if i not in indices_to_remove]

    logger.info(
        f"claude.md FIFO-Trim: {to_remove_count} alte Eintraege entfernt "
        f"(max. {max_entries} in {_AUTO_SECTION})"
    )
    return before_section + _AUTO_SECTION + '\n'.join(trimmed_lines) + after_section


async def append_to_claude_md(text: str) -> bool:
    """
    Haengt eine neue Bot-Instruktion an claude.md an.

    - Newline-Sanitizing vor dem Schreiben
    - TOCTOU: exists()-Check innerhalb des Locks
    - FIFO-Trim: max. 50 Eintraege in ## Automatisch gelernt
    - Cache-Invalidierung innerhalb des Locks
    - Size-Warning bei > 5000 Zeichen
    """
    if not text or not text.strip():
        return False

    # Sanitize: Newlines entfernen (Injection-Schutz)
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

            # FIFO-Trim: max. 50 Eintraege in ## Automatisch gelernt
            content = _trim_auto_section(content)

            _CLAUDE_MD_PATH.write_text(content, encoding="utf-8")

            # Cache-Invalidierung innerhalb des Locks
            _claude_md_cache = None

            if len(content) > _SIZE_WARNING_CHARS:
                logger.warning(
                    f"claude.md ist sehr lang ({len(content)} Zeichen) – "
                    f"manuelle Bereinigung empfohlen (> {_SIZE_WARNING_CHARS} Zeichen)"
                )

        # Cache-Miss erzwingen – naechster Aufruf liest frischen Inhalt
        load_claude_md()
        logger.info(f"claude.md: Bot-Instruktion gespeichert: {clean_text[:80]}")
        return True

    except Exception as e:
        logger.error(f"Fehler beim Schreiben in claude.md: {e}")
        return False
