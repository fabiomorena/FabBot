"""
Persönliches Profil für FabBot.

Phase 91 Fix: Migration thread-safe via threading.Lock + _migration_done Flag.
Vorher: load_profile() hatte bewusst keinen Lock – aber beim ersten Start konnten
memory_agent und profile_learner gleichzeitig load_profile() aufrufen. Beide erkannten
das unverschlüsselte File, beide verschlüsselten es, beide schrieben es. Im schlechtesten
Fall korruptes File. Fix: _migration_lock (threading.Lock) + _migration_done Flag
machen die Migration idempotent. threading.Lock statt asyncio.Lock weil load_profile()
synchron ist und nicht awaiten kann.

Phase 93 Fix (Issue #1): Backup vor jedem destruktiven Schreibvorgang.
_write_profile_bytes() kopiert personal_profile.yaml → personal_profile.yaml.bak
bevor die Datei überschrieben wird. Gilt für Migration, add_note_to_profile()
und write_profile(). Das Backup enthält immer den letzten guten Stand.

Phase 95 Fix (Issue #2): invalidate_chat_cache() nach jedem Schreibvorgang.
add_note_to_profile() und write_profile() rufen nach reload_profile()
invalidate_chat_cache() auf – zwingt chat_agent beim nächsten Aufruf das
Profil neu einzulesen statt veraltete Daten aus dem Prompt-Cache zu liefern.

Lädt personal_profile.yaml aus dem Projektwurzelverzeichnis und stellt
formatierte Kontext-Strings für die Agents bereit.

Fail-safe: Alle Fehler werden geloggt, niemals weitergereicht.
Ein fehlendes oder kaputtes Profil unterbricht den Bot nicht.

Zwei Kontext-Varianten:
- get_profile_context_short() → für Supervisor/Haiku (minimaler Overhead)
- get_profile_context_full()  → für chat_agent (voller Kontext)

Phase 2: /remember Command
- add_note_to_profile() → schreibt Note in YAML
- reload_profile()      → Cache leeren nach Update

Phase 3: Auto-Learning + Memory Agent
- write_profile()       → vollständiger YAML-Update (für profile_learner + memory_agent)

YAML-Struktur (Option 3 – Hybrid):
Feste Sektionen: identity, work, projects, people, preferences, places, hardware, routines
Freie Sektion:   custom (key/value Paare für alles andere)
Notes:           via /remember oder Auto-Learning

Thread-Safety:
- _profile_write_lock (asyncio.Lock) schützt alle YAML Read-Write-Operationen
  gegen gleichzeitige Updates (TOCTOU-Problem).
- load_profile() ist read-only und benötigt keinen Lock.
- _migration_lock (threading.Lock, Phase 91) schützt die einmalige Migrations-Write-Operation.
"""

import asyncio
import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROFILE_PATH = Path(__file__).parent.parent / "personal_profile.yaml"
_BACKUP_PATH = _PROFILE_PATH.with_suffix(".yaml.bak")
_profile_cache: dict[str, Any] | None = None

# Lock für alle async Schreiboperationen auf personal_profile.yaml
_profile_write_lock = asyncio.Lock()

# Phase 91: Thread-safe Migration plain YAML → verschlüsselt.
# threading.Lock (nicht asyncio.Lock) weil load_profile() sync ist.
# _migration_done verhindert Doppel-Schreiben bei gleichzeitigen Aufrufen.
_migration_lock = threading.Lock()
_migration_done: bool = False


def _write_profile_bytes(data: bytes) -> None:
    """
    Phase 93 (Issue #1): Zentraler Schreibpunkt für personal_profile.yaml.
    Erstellt immer zuerst ein Backup (personal_profile.yaml.bak) bevor
    die Originaldatei überschrieben wird.

    Das Backup enthält den letzten erfolgreich geschriebenen Stand.
    Bei Fehler während des Schreibens bleibt das Backup erhalten und
    kann manuell wiederhergestellt werden.

    Wirft IOError / OSError wenn Backup oder Schreiben fehlschlägt –
    Caller entscheidet über Exception-Handling.
    """
    if _PROFILE_PATH.exists():
        shutil.copy2(_PROFILE_PATH, _BACKUP_PATH)
        logger.debug(f"Profil-Backup erstellt: {_BACKUP_PATH}")
    _PROFILE_PATH.write_bytes(data)


def load_profile() -> dict[str, Any]:
    """
    Lädt personal_profile.yaml. Cached nach erstem Aufruf.
    Read-only – kein async Lock nötig.
    Gibt leeres Dict zurück bei Fehler oder fehlendem File.

    Phase 91: Migration ist jetzt thread-safe via _migration_lock + _migration_done.
    Vorher: Zwei gleichzeitige Aufrufe konnten beide schreiben → korruptes File möglich.
    Jetzt:  Nur der erste Aufrufer schreibt; alle weiteren überspringen die Migration.

    Phase 93: Migration nutzt _write_profile_bytes() → Backup vor Verschlüsselung.
    """
    global _profile_cache, _migration_done
    if _profile_cache is not None:
        return _profile_cache
    if not _PROFILE_PATH.exists():
        logger.warning(f"personal_profile.yaml nicht gefunden: {_PROFILE_PATH}")
        _profile_cache = {}
        return _profile_cache
    try:
        import yaml
        from agent.crypto import decrypt, is_encrypted, encrypt
        raw = _PROFILE_PATH.read_bytes()
        if is_encrypted(raw):
            yaml_text = decrypt(raw)
        else:
            yaml_text = raw.decode("utf-8")
            # Phase 91: Thread-safe Migration.
            # Mit Lock prüfen ob Migration schon erfolgt ist (Flag).
            # Nur der erste Caller schreibt – alle weiteren sehen _migration_done=True.
            with _migration_lock:
                if not _migration_done:
                    logger.info("Migration: personal_profile.yaml wird verschlüsselt...")
                    _write_profile_bytes(encrypt(yaml_text))
                    _migration_done = True
                    logger.info("Migration abgeschlossen – Profil ist jetzt verschlüsselt.")
        loaded = yaml.safe_load(yaml_text)
        _profile_cache = loaded if isinstance(loaded, dict) else {}
        logger.info(f"Persönliches Profil geladen: {_PROFILE_PATH}")
        return _profile_cache
    except Exception as e:
        logger.error(f"Fehler beim Laden von personal_profile.yaml: {e}")
        _profile_cache = {}
        return _profile_cache


def reload_profile() -> dict[str, Any]:
    """
    Erzwingt Neu-Laden des Profils aus der YAML-Datei.
    Wird nach Schreiboperationen aufgerufen.
    """
    global _profile_cache
    _profile_cache = None
    return load_profile()


async def add_note_to_profile(text: str) -> bool:
    """
    Fügt eine neue Note zum 'notes' Abschnitt in personal_profile.yaml hinzu.
    Verwendet _profile_write_lock gegen gleichzeitige Schreibzugriffe.
    Gibt True zurück bei Erfolg, False bei Fehler.

    Phase 93: _write_profile_bytes() erstellt Backup vor dem Schreiben.
    Phase 95: invalidate_chat_cache() nach reload_profile() – Prompt-Cache sofort
    ungültig machen damit chat_agent die neue Note beim nächsten Aufruf sieht.
    """
    if not text or not text.strip():
        return False
    if not _PROFILE_PATH.exists():
        logger.error("personal_profile.yaml nicht gefunden – Note kann nicht gespeichert werden.")
        return False
    try:
        import yaml
        from agent.crypto import decrypt, is_encrypted, encrypt
        async with _profile_write_lock:
            raw = _PROFILE_PATH.read_bytes()
            yaml_text = decrypt(raw) if is_encrypted(raw) else raw.decode("utf-8")
            profile = yaml.safe_load(yaml_text) or {}
            if "notes" not in profile or not isinstance(profile["notes"], list):
                profile["notes"] = []
            timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
            profile["notes"].append(f"[{timestamp}] {text.strip()}")
            serialized = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
            _write_profile_bytes(encrypt(serialized))
        reload_profile()
        # Phase 95: Prompt-Cache sofort ungültig – nächster chat_agent-Aufruf
        # liest das aktualisierte Profil neu ein.
        try:
            from agent.agents.chat_agent import invalidate_chat_cache
            invalidate_chat_cache()
        except Exception as e:
            logger.debug(f"invalidate_chat_cache (add_note) fehlgeschlagen (ignoriert): {e}")
        logger.info(f"Note zu Profil hinzugefügt: {text[:80]}")
        return True
    except Exception as e:
        logger.error(f"Fehler beim Schreiben der Note: {e}")
        return False


async def write_profile(profile: dict[str, Any]) -> bool:
    """
    Schreibt ein vollständiges Profil-Dict in personal_profile.yaml.
    Verwendet _profile_write_lock gegen gleichzeitige Schreibzugriffe.
    Gibt True zurück bei Erfolg, False bei Fehler.

    Phase 93: _write_profile_bytes() erstellt Backup vor dem Schreiben.
    Phase 95: invalidate_chat_cache() nach reload_profile() – Prompt-Cache sofort
    ungültig machen damit chat_agent das neue Profil beim nächsten Aufruf sieht.
    """
    if not profile or not isinstance(profile, dict):
        return False
    if not _PROFILE_PATH.exists():
        logger.error("personal_profile.yaml nicht gefunden – write_profile abgebrochen.")
        return False
    try:
        import yaml
        async with _profile_write_lock:
            serialized = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
            round_tripped = yaml.safe_load(serialized)
            if round_tripped != profile:
                logger.error(
                    f"write_profile: Round-Trip Mismatch – YAML-Coercion erkannt, Schreiben abgebrochen. "
                    f"Diff-Keys: {set(round_tripped.keys()) ^ set(profile.keys())}"
                )
                return False
            from agent.crypto import encrypt
            _write_profile_bytes(encrypt(serialized))
        reload_profile()
        # Phase 95: Prompt-Cache sofort ungültig – nächster chat_agent-Aufruf
        # liest das aktualisierte Profil neu ein.
        try:
            from agent.agents.chat_agent import invalidate_chat_cache
            invalidate_chat_cache()
        except Exception as e:
            logger.debug(f"invalidate_chat_cache (write_profile) fehlgeschlagen (ignoriert): {e}")
        logger.info("write_profile: Profil erfolgreich verschlüsselt gespeichert.")
        return True
    except Exception as e:
        logger.error(f"write_profile Fehler: {e}")
        return False


def get_profile_context_short() -> str:
    """
    Kurzer Kontext-String für den Supervisor (Haiku-Routing).
    Nur Name, Standort und aktive High-Priority-Projekte.
    """
    try:
        profile = load_profile()
        if not profile:
            return ""
        parts: list[str] = []
        identity = profile.get("identity", {})
        if isinstance(identity, dict):
            name = identity.get("name", "")
            location = identity.get("location", "")
            if name:
                parts.append(f"User: {name}" + (f" ({location})" if location else ""))
        projects = profile.get("projects", {})
        active = projects.get("active", []) if isinstance(projects, dict) else []
        if isinstance(active, list):
            high = [
                p["name"] for p in active
                if isinstance(p, dict) and p.get("priority") == "high" and p.get("name")
            ]
            if high:
                parts.append(f"Aktive Projekte: {', '.join(high)}")
        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"get_profile_context_short Fehler (ignoriert): {e}")
        return ""


def get_profile_context_full() -> str:
    """
    Vollständiger Kontext-String für den chat_agent.
    Enthält alle Sektionen: Identität, Arbeit, Projekte, Präferenzen,
    Hardware, Routinen, Personen, Orte, Media, Custom, Notes.
    """
    try:
        profile = load_profile()
        if not profile:
            return ""

        lines: list[str] = ["=== Persönlicher Kontext des Users ==="]

        # Identität
        identity = profile.get("identity", {})
        if isinstance(identity, dict) and identity:
            if v := identity.get("name"):
                lines.append(f"Name: {v}")
            if v := identity.get("location"):
                lines.append(f"Standort: {v}")
            if v := identity.get("language"):
                lines.append(f"Sprache: {v}")

        # Arbeit
        work = profile.get("work", {})
        if isinstance(work, dict) and work:
            if v := work.get("employer"):
                lines.append(f"Arbeitgeber: {v}")
            if v := work.get("role"):
                lines.append(f"Rolle: {v}")
            if v := work.get("focus"):
                lines.append(f"Fokus: {v}")
            if v := work.get("job_context"):
                lines.append(f"Job-Kontext: {v}")

        # Projekte
        projects = profile.get("projects", {})
        active = projects.get("active", []) if isinstance(projects, dict) else []
        if isinstance(active, list) and active:
            lines.append("Aktive Projekte:")
            for p in active:
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "")
                desc = p.get("description", "")
                stack = p.get("stack", [])
                priority = p.get("priority", "")
                stack_str = ", ".join(stack) if isinstance(stack, list) else str(stack)
                line = f"  – {name}"
                if desc:
                    line += f": {desc}"
                if stack_str:
                    line += f" [{stack_str}]"
                if priority == "high":
                    line += " ★"
                lines.append(line)

        # Präferenzen
        prefs = profile.get("preferences", {})
        if isinstance(prefs, dict) and prefs:
            if v := prefs.get("communication"):
                lines.append(f"Kommunikationsstil: {v}")
            if v := prefs.get("response_style"):
                lines.append(f"Antwortstil: {v}")
            dislikes = prefs.get("dislikes", [])
            if isinstance(dislikes, list) and dislikes:
                lines.append(f"Vermeiden: {'; '.join(dislikes)}")
            skip_keys = {"communication", "response_style", "dislikes", "language_for_answers"}
            extra = {k: v for k, v in prefs.items() if k not in skip_keys and isinstance(v, str)}
            for k, v in extra.items():
                lines.append(f"Präferenz – {k}: {v}")

        # Hardware
        hw = profile.get("hardware", {})
        if isinstance(hw, dict) and hw:
            if v := hw.get("main_machine"):
                lines.append(f"Gerät: {v}")

        # Routinen
        routines = profile.get("routines", {})
        if isinstance(routines, dict) and routines:
            if v := routines.get("deep_work"):
                lines.append(f"Deep Work: {v}")
            if v := routines.get("preferred_no_interruptions"):
                lines.append(f"Keine Unterbrechungen: {v}")

        # Personen
        people = profile.get("people", [])
        if isinstance(people, list) and people:
            lines.append("Personen die du kennst:")
            for p in people:
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "")
                context = p.get("context", "")
                if name:
                    line = f"  – {name}"
                    if context:
                        line += f": {context}"
                    lines.append(line)

        # Orte / Places
        places = profile.get("places", [])
        if isinstance(places, list) and places:
            lines.append("Orte und Lieblingsplätze:")
            for p in places:
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "")
                place_type = p.get("type", "")
                location = p.get("location", "")
                context = p.get("context", "")
                if name:
                    line = f"  – {name}"
                    if place_type:
                        line += f" ({place_type})"
                    if location:
                        line += f" in {location}"
                    if context:
                        line += f": {context}"
                    lines.append(line)

        # Media
        media = profile.get("media", [])
        if isinstance(media, list) and media:
            lines.append("Lieblingsmedien:")
            for m in media:
                if not isinstance(m, dict):
                    continue
                title = m.get("title", "")
                media_type = m.get("type", "")
                artist = m.get("artist", "")
                context = m.get("context", "")
                if title:
                    line = f"  – {title}"
                    if artist:
                        line += f" von {artist}"
                    if media_type:
                        line += f" ({media_type})"
                    if context:
                        line += f": {context}"
                    lines.append(line)

        # Custom
        custom = profile.get("custom", [])
        if isinstance(custom, list) and custom:
            lines.append("Weitere persönliche Infos:")
            for item in custom:
                if isinstance(item, dict):
                    key = item.get("key", "")
                    value = item.get("value", "")
                    if key and value:
                        lines.append(f"  • {key}: {value}")

        # Notes
        notes = profile.get("notes", [])
        if isinstance(notes, list) and notes:
            lines.append("Persönliche Notizen:")
            for note in notes[-20:]:
                lines.append(f"  • {note}")

        lines.append("=== Ende Kontext ===")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"get_profile_context_full Fehler (ignoriert): {e}")
        return ""
