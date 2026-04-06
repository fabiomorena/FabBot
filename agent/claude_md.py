"""
claude.md Loader fuer FabBot – Phase 62/63/65/66/67/90.

Phase 90 Fixes:
- _CLAUDE_MD_PATH: ~/.fabbot/claude.md statt Repo-Root
  Verhindert git-Leak persönlicher Bot-Instruktionen.
- _migrate_claude_md_if_needed(): automatische einmalige Migration beim Import.
- append_to_claude_md(): load_claude_md() jetzt innerhalb des Locks.
  Vorher: Race-Window zwischen Cache-Invalidierung (im Lock) und
  Cache-Befüllung (außerhalb Lock) — andere Coroutine konnte alten Stand lesen.
  Fix: konsistent mit reload_claude_md() (dort bereits Phase 66/67 korrekt).
- append_to_claude_md(): Defense-in-Depth Content-Validierung.
  Schicht 1: memory_agent._validate_instruction() (Phase 89).
  Schicht 2: hier — filtert Markdown-Heading-Injection (## …) und
  Forbidden-Patterns auch bei direkten Aufrufen ohne memory_agent.

Phase 67 Fixes (bestehend):
- reload_claude_md(): load_claude_md() innerhalb des Locks
- _trim_auto_section(): robuster Regex fuer alle Heading-Level
- _trim_auto_section(): Entry-Detection erkennt -, * und +
- load_claude_md(): Kommentar erklaert warum kein Lock noetig ist
"""

import asyncio
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Phase 90: ~/.fabbot/claude.md statt Repo-Root.
# Vorher: Path(__file__).parent.parent / "claude.md" → direkt im Repo neben main.py.
# Risiko: `git push` ohne korrektes .gitignore → persönliche Instruktionen im Remote.
# Jetzt:  ~/.fabbot/ wie memory.db, audit.log und alle anderen User-Daten.
_CLAUDE_MD_PATH = Path.home() / ".fabbot" / "claude.md"

# Alter Pfad als Modul-Konstante – patchbar in Tests.
# _migrate_claude_md_if_needed() liest diesen Wert statt ihn hardzukodieren.
_CLAUDE_MD_OLD_PATH = Path(__file__).parent.parent / "claude.md"
_claude_md_cache: str | None = None
_write_lock = asyncio.Lock()

_AUTO_SECTION = "## Automatisch gelernt"
_SIZE_WARNING_CHARS = 5000
_MAX_AUTO_ENTRIES = 50

# Phase 90: Defense-in-Depth – zweite Validierungsschicht in append_to_claude_md().
# Schicht 1 (memory_agent.py, Phase 89): _validate_instruction() vor dem Aufruf.
# Schicht 2 (hier): fängt direkte Aufrufe und umgangene Validierungen ab.
# Muster:
#   #{1,6}\s  – Markdown-Heading (## System, ### Admin …) anywhere in string
#   ignore / vergiss / system prompt / override / jailbreak – Injection-Klassiker
#   anweisung.*ignorier / ignorier.*anweisung – deutsche Varianten
# re.IGNORECASE: Groß-/Kleinschreibung irrelevant.
# Kein re.MULTILINE nötig: clean_text hat nach replace("\n", " ") keine Zeilenumbrüche.
_APPEND_MAX_LEN = 200
_APPEND_FORBIDDEN = re.compile(
    r"#{1,6}\s"
    r"|ignore"
    r"|vergiss"
    r"|system\s*prompt"
    r"|override"
    r"|jailbreak"
    r"|anweisung.*ignorier"
    r"|ignorier.*anweisung",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Phase 90: Einmalige Pfad-Migration beim Modulimport
# ---------------------------------------------------------------------------

def _migrate_claude_md_if_needed() -> None:
    """
    Phase 90: Kopiert claude.md vom alten Repo-Root-Pfad nach ~/.fabbot/.

    Ist idempotent: läuft nur wenn ~/.fabbot/claude.md noch nicht existiert
    und die alte Datei vorhanden ist. Nach der Migration zeigt _CLAUDE_MD_PATH
    auf den neuen Ort – alle weiteren Operationen sind transparent.

    Empfehlung nach Migration:
    - Alte claude.md aus Repo-Root entfernen
    - `claude.md` in .gitignore eintragen (falls noch nicht vorhanden)
    """
    if _CLAUDE_MD_PATH.exists():
        return  # Bereits migriert oder Neuinstallation – nichts tun

    old_path = _CLAUDE_MD_OLD_PATH
    if not old_path.exists():
        return  # Keine alte Datei → keine Migration nötig

    try:
        _CLAUDE_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_path, _CLAUDE_MD_PATH)
        logger.info(
            f"claude.md migriert: {old_path} → {_CLAUDE_MD_PATH}\n"
            f"Empfehlung: alte Datei entfernen und 'claude.md' in .gitignore aufnehmen."
        )
    except Exception as e:
        logger.error(f"claude.md Migration fehlgeschlagen (ignoriert): {e}")


# Migration beim ersten Import ausführen – vor allen anderen Operationen.
_migrate_claude_md_if_needed()


# ---------------------------------------------------------------------------
# Cache-Operationen
# ---------------------------------------------------------------------------

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
    Phase 67 Fix: load_claude_md() innerhalb des Locks –
    verhindert Race zwischen Lock-Release und Read.
    """
    global _claude_md_cache
    async with _write_lock:
        _claude_md_cache = None
        return load_claude_md()  # Read direkt im Lock – konsistente Lock-Semantik


# ---------------------------------------------------------------------------
# FIFO-Trim
# ---------------------------------------------------------------------------

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
    next_match = re.search(r'\n#{1,6} ', section_and_rest)
    if next_match:
        section_body = section_and_rest[:next_match.start()]
        after_section = section_and_rest[next_match.start():]
    else:
        section_body = section_and_rest
        after_section = ""

    # Phase 67 Fix: Entry-Detection erkennt -, * und + als Listenmarker
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


# ---------------------------------------------------------------------------
# Schreib-Operation
# ---------------------------------------------------------------------------

async def append_to_claude_md(text: str) -> bool:
    """
    Haengt eine neue Bot-Instruktion an claude.md an.

    Phase 90 Fixes:
    1. load_claude_md() jetzt innerhalb des Locks (Race-Window-Fix).
       Vorher: Cache wurde außerhalb des Locks befüllt → andere Coroutine
       konnte zwischen Invalidierung und Befüllung den alten Stand lesen.
       Nanosekunden-Fenster, aber real. Jetzt: konsistent mit reload_claude_md().
    2. Defense-in-Depth Content-Validierung:
       - Länge max. 200 Zeichen (konsistent mit memory_agent._INSTRUCTION_MAX_LEN)
       - Forbidden-Pattern: Markdown-Headings (## …) und Injection-Klassiker
       Schicht 1 bleibt in memory_agent._validate_instruction() (Phase 89).

    Bestehende Garantien (unverändert):
    - Newline-Sanitizing vor dem Schreiben
    - TOCTOU: exists()-Check innerhalb des Locks
    - FIFO-Trim: max. 50 Eintraege in ## Automatisch gelernt
    - Size-Warning bei > 5000 Zeichen
    """
    if not text or not text.strip():
        return False

    # Sanitize: Newlines entfernen (Injection-Schutz Schicht 0)
    clean_text = text.strip().replace("\n", " ").replace("\r", "").strip()
    if not clean_text:
        return False

    # Phase 90: Defense-in-Depth Validierung (Schicht 2)
    if len(clean_text) > _APPEND_MAX_LEN:
        logger.warning(
            f"append_to_claude_md: Text zu lang ({len(clean_text)} Zeichen, "
            f"max. {_APPEND_MAX_LEN}) – abgelehnt."
        )
        return False
    if _APPEND_FORBIDDEN.search(clean_text):
        logger.warning(
            f"append_to_claude_md: Verdächtiger Inhalt erkannt – abgelehnt: "
            f"{clean_text[:80]}"
        )
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

            if len(content) > _SIZE_WARNING_CHARS:
                logger.warning(
                    f"claude.md ist sehr lang ({len(content)} Zeichen) – "
                    f"manuelle Bereinigung empfohlen (> {_SIZE_WARNING_CHARS} Zeichen)"
                )

            # Phase 90 Race-Fix: Cache-Invalidierung UND Neu-Befüllung innerhalb des Locks.
            # Vorher: load_claude_md() nach dem `async with`-Block → Race-Window.
            # Jetzt:  identisches Muster wie reload_claude_md() (Phase 66/67).
            _claude_md_cache = None
            load_claude_md()

        logger.info(f"claude.md: Bot-Instruktion gespeichert: {clean_text[:80]}")
        return True

    except Exception as e:
        logger.error(f"Fehler beim Schreiben in claude.md: {e}")
        return False
