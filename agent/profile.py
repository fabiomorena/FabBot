"""
Persönliches Profil für FabBot.

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
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROFILE_PATH = Path(__file__).parent.parent / "personal_profile.yaml"
_profile_cache: dict[str, Any] | None = None


def load_profile() -> dict[str, Any]:
    global _profile_cache
    if _profile_cache is not None:
        return _profile_cache
    if not _PROFILE_PATH.exists():
        logger.warning(f"personal_profile.yaml nicht gefunden: {_PROFILE_PATH}")
        _profile_cache = {}
        return _profile_cache
    try:
        import yaml
        with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        _profile_cache = loaded if isinstance(loaded, dict) else {}
        logger.info(f"Persönliches Profil geladen: {_PROFILE_PATH}")
        return _profile_cache
    except Exception as e:
        logger.error(f"Fehler beim Laden von personal_profile.yaml: {e}")
        _profile_cache = {}
        return _profile_cache


def reload_profile() -> dict[str, Any]:
    global _profile_cache
    _profile_cache = None
    return load_profile()


def add_note_to_profile(text: str) -> bool:
    if not text or not text.strip():
        return False
    if not _PROFILE_PATH.exists():
        logger.error("personal_profile.yaml nicht gefunden – Note kann nicht gespeichert werden.")
        return False
    try:
        import yaml
        with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f) or {}
        if "notes" not in profile or not isinstance(profile["notes"], list):
            profile["notes"] = []
        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        profile["notes"].append(f"[{timestamp}] {text.strip()}")
        with open(_PROFILE_PATH, "w", encoding="utf-8") as f:
            yaml.dump(profile, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        reload_profile()
        logger.info(f"Note zu Profil hinzugefügt: {text[:80]}")
        return True
    except Exception as e:
        logger.error(f"Fehler beim Schreiben der Note: {e}")
        return False


def write_profile(profile: dict[str, Any]) -> bool:
    if not profile or not isinstance(profile, dict):
        return False
    if not _PROFILE_PATH.exists():
        logger.error("personal_profile.yaml nicht gefunden – write_profile abgebrochen.")
        return False
    try:
        import yaml
        serialized = yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
        yaml.safe_load(serialized)  # Finale Validierung
        with open(_PROFILE_PATH, "w", encoding="utf-8") as f:
            f.write(serialized)
        reload_profile()
        logger.info("write_profile: Profil erfolgreich geschrieben.")
        return True
    except Exception as e:
        logger.error(f"write_profile Fehler: {e}")
        return False


def get_profile_context_short() -> str:
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
        logger.error(f"get_profile_context_short Fehler (ignoriert): {e}")
        return ""


def get_profile_context_full() -> str:
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
            # Weitere Präferenzen (dynamisch gespeichert)
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

        # Media – Lieblingslieder, Filme, Bücher etc.
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

        # Custom – freie Sektion
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
        logger.error(f"get_profile_context_full Fehler (ignoriert): {e}")
        return ""