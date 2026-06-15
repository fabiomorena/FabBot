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

Phase 178 (Issue #142): Race-Condition-Fix + Frozen-Snapshot + Prefix-Cache.

Race-Condition-Fix:
  write_profile() nimmt optionalen expected_base_hash. Innerhalb des Locks
  wird der Disk-Stand frisch gelesen und dessen Hash verglichen. Stimmt der
  Hash nicht → WriteResult.STALE. Caller kann dann neu laden und erneut versuchen.
  load_profile() gibt jetzt immer copy.deepcopy() zurück um Cache-Mutation zu
  verhindern. load_profile_with_hash() liefert Profil + Hash in einem Aufruf.

Frozen-Snapshot:
  _profile_snapshot bleibt für PROFILE_SNAPSHOT_TTL (Standard 300s) stabil.
  get_profile_context_full() nutzt den Snapshot statt load_profile() direkt.
  invalidate_chat_cache() wird nur noch beim Snapshot-Refresh aufgerufen,
  nicht mehr nach jedem einzelnen write_profile(). Das hält den Anthropic
  Prefix-Cache innerhalb einer Session warm und senkt Token-Kosten.

Atomic Write:
  _write_profile_bytes() schreibt zuerst in eine .tmp-Datei und macht dann
  os.replace() – POSIX-atomares Rename verhindert Halb-Schreiben bei Crash.

Lädt personal_profile.yaml aus dem Projektwurzelverzeichnis und stellt
formatierte Kontext-Strings für die Agents bereit.

Fail-safe: Alle Fehler werden geloggt, niemals weitergereicht.
Ein fehlendes oder kaputtes Profil unterbricht den Bot nicht.

Zwei Kontext-Varianten:
- get_profile_context_short() → für Supervisor/Haiku (minimaler Overhead)
- get_profile_context_full()  → für chat_agent (voller Kontext, via Snapshot)

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
import copy
import hashlib
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from agent.config import get_settings

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

# Phase 178: Frozen-Snapshot für stabilen Prefix-Cache innerhalb einer Session.
_SNAPSHOT_TTL: float = get_settings().profile_snapshot_ttl
_profile_snapshot: dict[str, Any] | None = None
_snapshot_expires_at: float = 0.0


class WriteResult(Enum):
    """Rückgabewert von write_profile(). bool()-konvertierbar: OK → True, Rest → False."""

    OK = "ok"
    STALE = "stale"
    INVALID = "invalid"
    IO_ERROR = "io_error"

    def __bool__(self) -> bool:
        return self is WriteResult.OK


def _write_profile_bytes(data: bytes) -> None:
    """
    Phase 93 (Issue #1): Zentraler Schreibpunkt für personal_profile.yaml.
    Erstellt immer zuerst ein Backup (personal_profile.yaml.bak) bevor
    die Originaldatei überschrieben wird.

    Phase 178: Atomic write via tempfile + os.replace() – verhindert
    halb-geschriebene Dateien bei Prozessabbruch.

    Wirft IOError / OSError wenn Backup oder Schreiben fehlschlägt –
    Caller entscheidet über Exception-Handling.
    """
    if _PROFILE_PATH.exists():
        shutil.copy2(_PROFILE_PATH, _BACKUP_PATH)
        logger.debug(f"Profil-Backup erstellt: {_BACKUP_PATH}")
    tmp_path = _PROFILE_PATH.with_suffix(".yaml.tmp")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, _PROFILE_PATH)


def _compute_profile_hash(profile: dict[str, Any]) -> str:
    """SHA-256 (16 Hex-Zeichen) über stabiles YAML-Dump (sort_keys=True)."""
    import yaml

    serialized = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def _validate_profile(loaded: dict[str, Any]) -> dict[str, Any]:
    """
    Issue #198: Weiche Pydantic-Validierung des geladenen Profils.

    - Erfolg → model_dump(exclude_unset=True). exclude_unset verhindert, dass
      None-Defaults für nicht gesetzte Felder eingefügt werden; so bleibt der
      Profil-Hash (für Optimistic-Concurrency in write_profile) stabil.
      extra="allow"-Felder gelten als gesetzt und bleiben im Dump erhalten.
    - Unbekannte Top-Level-Felder → DEBUG-Log, kein Fehler.
    - Typfehler (z.B. name: 123) → WARNING-Log + Fallback auf das Roh-dict.
    """
    from pydantic import ValidationError

    from agent.profile_schema import PersonalProfile

    try:
        model = PersonalProfile.model_validate(loaded)
        unknown = set(loaded) - set(PersonalProfile.model_fields)
        if unknown:
            logger.debug(f"Profil: unbekannte Felder (extra=allow): {sorted(unknown)}")
        return model.model_dump(exclude_unset=True)
    except ValidationError as e:
        logger.warning(f"Profil-Validierung fehlgeschlagen, Fallback auf Rohwert: {e}")
        return loaded


def load_profile() -> dict[str, Any]:
    """
    Lädt personal_profile.yaml. Cached nach erstem Aufruf.
    Read-only – kein async Lock nötig.
    Gibt leeres Dict zurück bei Fehler oder fehlendem File.

    Phase 91: Migration ist jetzt thread-safe via _migration_lock + _migration_done.
    Phase 93: Migration nutzt _write_profile_bytes() → Backup vor Verschlüsselung.
    Phase 178: Gibt copy.deepcopy() zurück – verhindert Cache-Mutation durch Caller.
    """
    global _profile_cache, _migration_done
    if _profile_cache is not None:
        return copy.deepcopy(_profile_cache)
    if not _PROFILE_PATH.exists():
        logger.warning(f"personal_profile.yaml nicht gefunden: {_PROFILE_PATH}")
        _profile_cache = {}
        return {}
    try:
        import yaml
        from agent.crypto import decrypt, is_encrypted, encrypt

        raw = _PROFILE_PATH.read_bytes()
        if is_encrypted(raw):
            yaml_text = decrypt(raw)
        else:
            yaml_text = raw.decode("utf-8")
            with _migration_lock:
                if not _migration_done:
                    logger.info("Migration: personal_profile.yaml wird verschlüsselt...")
                    _write_profile_bytes(encrypt(yaml_text))
                    _migration_done = True
                    logger.info("Migration abgeschlossen – Profil ist jetzt verschlüsselt.")
        loaded = yaml.safe_load(yaml_text)
        loaded = _validate_profile(loaded) if isinstance(loaded, dict) else {}
        _profile_cache = loaded
        logger.info(f"Persönliches Profil geladen: {_PROFILE_PATH}")
        return copy.deepcopy(_profile_cache)
    except Exception as e:
        logger.error(f"Fehler beim Laden von personal_profile.yaml: {e}")
        _profile_cache = {}
        return {}


def load_profile_with_hash() -> tuple[dict[str, Any], str]:
    """
    Phase 178: Lädt Profil + Base-Hash in einem Aufruf.
    Basis für Optimistic-Concurrency-Control in write_profile().
    """
    profile = load_profile()
    return profile, _compute_profile_hash(profile)


def reload_profile() -> dict[str, Any]:
    """
    Erzwingt Neu-Laden des Profils aus der YAML-Datei.
    Wird nach Schreiboperationen aufgerufen.
    """
    global _profile_cache
    _profile_cache = None
    return load_profile()


def get_active_snapshot() -> dict[str, Any]:
    """
    Phase 178: Gibt den aktuellen Frozen-Snapshot zurück.
    Wird für get_profile_context_full() verwendet – bleibt innerhalb einer
    Session stabil (TTL = PROFILE_SNAPSHOT_TTL, Standard 300s) damit
    der Anthropic Prefix-Cache nicht mid-Session invalidiert wird.
    Bei Ablauf oder fehlendem Snapshot wird refresh_snapshot() aufgerufen.
    """
    if _profile_snapshot is None or time.monotonic() >= _snapshot_expires_at:
        refresh_snapshot()
    return copy.deepcopy(_profile_snapshot)  # type: ignore[arg-type]


def refresh_snapshot() -> None:
    """
    Phase 178: Aktualisiert den Frozen-Snapshot vom aktuellen Disk-Stand.
    Ruft invalidate_chat_cache() auf damit der nächste Chat-Prompt neu
    gebaut wird. Wird automatisch nach TTL-Ablauf oder explizit via
    /reload aufgerufen – aber nie nach einzelnen write_profile()-Aufrufen.
    """
    global _profile_snapshot, _snapshot_expires_at
    _profile_snapshot = copy.deepcopy(reload_profile())
    _snapshot_expires_at = time.monotonic() + _SNAPSHOT_TTL
    logger.debug(f"Profil-Snapshot aktualisiert (TTL={_SNAPSHOT_TTL}s).")
    try:
        from agent.agents.chat_agent import invalidate_chat_cache

        invalidate_chat_cache()
    except Exception as e:
        logger.debug(f"invalidate_chat_cache (refresh_snapshot) fehlgeschlagen (ignoriert): {e}")


async def add_note_to_profile(text: str) -> bool:
    """
    Fügt eine neue Note zum 'notes' Abschnitt in personal_profile.yaml hinzu.
    Verwendet _profile_write_lock gegen gleichzeitige Schreibzugriffe.
    Gibt True zurück bei Erfolg, False bei Fehler.

    Phase 93: _write_profile_bytes() erstellt Backup vor dem Schreiben.
    Phase 178: Kein invalidate_chat_cache() mehr – Snapshot refresht beim
    nächsten TTL-Ablauf. Verhindert Prefix-Cache-Invalidation mid-Session.
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
        logger.info(f"Note zu Profil hinzugefügt: {text[:80]}")
        return True
    except Exception as e:
        logger.error(f"Fehler beim Schreiben der Note: {e}")
        return False


async def write_profile(
    profile: dict[str, Any],
    *,
    expected_base_hash: str | None = None,
) -> WriteResult:
    """
    Schreibt ein vollständiges Profil-Dict in personal_profile.yaml.
    Verwendet _profile_write_lock gegen gleichzeitige Schreibzugriffe.

    Phase 93: _write_profile_bytes() erstellt Backup vor dem Schreiben.
    Phase 178: Optimistic-Concurrency-Control via expected_base_hash.
      Wenn angegeben, wird der Disk-Stand innerhalb des Locks frisch gelesen
      und dessen Hash verglichen. Bei Mismatch → WriteResult.STALE – Caller
      soll neu laden und erneut versuchen (kein blinder Überschreib-Verlust).
      Kein invalidate_chat_cache() mehr – nur noch in refresh_snapshot().

    Rückgabe: WriteResult (bool()-konvertierbar: OK → True, Rest → False).
    """
    if not profile or not isinstance(profile, dict):
        return WriteResult.INVALID
    if not _PROFILE_PATH.exists():
        logger.error("personal_profile.yaml nicht gefunden – write_profile abgebrochen.")
        return WriteResult.IO_ERROR
    try:
        import yaml
        from agent.crypto import decrypt, is_encrypted, encrypt

        async with _profile_write_lock:
            if expected_base_hash is not None:
                raw = _PROFILE_PATH.read_bytes()
                yaml_text = decrypt(raw) if is_encrypted(raw) else raw.decode("utf-8")
                current_on_disk = yaml.safe_load(yaml_text) or {}
                current_hash = _compute_profile_hash(current_on_disk)
                if current_hash != expected_base_hash:
                    logger.warning(
                        "write_profile: STALE – Disk-Hash weicht von expected_base_hash ab "
                        f"(expected={expected_base_hash} current={current_hash})"
                    )
                    return WriteResult.STALE

            serialized = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
            round_tripped = yaml.safe_load(serialized)
            if round_tripped != profile:
                logger.error(
                    f"write_profile: Round-Trip Mismatch – YAML-Coercion erkannt, Schreiben abgebrochen. "
                    f"Diff-Keys: {set(round_tripped.keys()) ^ set(profile.keys())}"
                )
                return WriteResult.INVALID

            _write_profile_bytes(encrypt(serialized))

        reload_profile()
        logger.info("write_profile: Profil erfolgreich verschlüsselt gespeichert.")
        return WriteResult.OK
    except Exception as e:
        logger.error(f"write_profile Fehler: {e}")
        return WriteResult.IO_ERROR


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
            high = [p["name"] for p in active if isinstance(p, dict) and p.get("priority") == "high" and p.get("name")]
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

    Phase 178: Nutzt get_active_snapshot() statt load_profile() direkt –
    der Snapshot bleibt innerhalb einer Session stabil und verhindert
    Anthropic Prefix-Cache-Invalidation nach Profil-Writes.
    """
    try:
        profile = get_active_snapshot()
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
