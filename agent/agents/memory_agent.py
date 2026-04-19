"""
Memory Agent fuer FabBot – Phase 45/46/63/64/65/89/99/115/119/121.

Phase 121: Typisiertes MemoryUpdateResult statt dict | None (Closes #44).
  - _apply_memory_update gibt MemoryUpdateResult zurück statt dict | None
  - Drei semantisch unterschiedliche Outcomes klar getrennt:
      success=True  → Profil wurde korrekt verändert
      success=False, allow_fallback=True  → ungültige Eingabe, Fallback-Save ok
      success=False, allow_fallback=False → explizit abgelehnte Operation,
                                            kein Fallback, user_message wird gezeigt
  - bot_instruction delete: klare Fehlermeldung statt falsches 🗑️ Feedback
  - project delete (leerer name): ebenfalls kein Fallback mehr
  - Caller memory_agent() vereinfacht und semantisch korrekt
"""

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.llm import get_llm, get_fast_llm
from agent.profile import load_profile, add_note_to_profile, write_profile
from agent.utils import get_current_datetime

logger = logging.getLogger(__name__)

_INSTRUCTION_MAX_LEN = 200
_INSTRUCTION_FORBIDDEN = re.compile(
    r"(ignore|vergiss|system\s*prompt|prompt|instruction|override|jailbreak|"
    r"anweisung.*ignorier|ignorier.*anweisung)",
    re.I,
)

MERKE_DIR_DAS_TRIGGERS = frozenset({
    "merke dir das",
    "merk dir das",
    "merke das",
    "merk das",
    "das merken",
    "bitte merken",
    "kannst du dir merken",
    "bitte merk dir das",
    "merke dir das bitte",
    "das kannst du dir merken",
    "das solltest du dir merken",
    "merk dir das bitte",
})

_MERKE_DIR_DAS_TRIGGERS = MERKE_DIR_DAS_TRIGGERS

_FORMULATE_PROMPT = """Du bist ein Assistent der Bot-Instruktionen formuliert.

Der User hat folgendes gesagt:
"{context}"

Formuliere daraus eine praezise Bot-Instruktion (1 Satz, max. 20 Woerter).
Die Instruktion beschreibt wie der Bot sich verhalten soll basierend auf dieser Info.
Fokus: Kommunikationsstil, Reaktion auf den User, Anpassung an seine Gewohnheiten.

Antworte NUR mit der Bot-Instruktion – kein Markdown, keine Erklaerung, kein Prefix.

Gute Beispiele:
- "Fabio antwortet morgens kurz – er ist im Flow, kurz und praezise bleiben"
- "Fabio hoert beim Coden Techno – bei Musik-Empfehlungen elektronische Musik bevorzugen"
- "Fabio bevorzugt direkte Empfehlungen statt 'es kommt drauf an'"
"""

# ---------------------------------------------------------------------------
# Phase 119: Preference-Subcategories
# ---------------------------------------------------------------------------

_PREF_SUBCATEGORY_KEYWORDS: dict[str, list[str]] = {
    "entertainment": [
        "serie", "film", "musik", "song", "album", "künstler", "podcast",
        "buch", "fantasy", "sci-fi", "genre", "lieblings", "favorite",
        "star trek", "star wars", "anime", "game", "spiel",
    ],
    "lifestyle": [
        "sport", "ernährung", "vegan", "vegetarisch", "schlaf", "hobby",
        "fitness", "laufen", "yoga", "meditation", "trinken", "essen",
    ],
    "tech": [
        "editor", "ide", "os", "betriebssystem", "sprache", "framework",
        "tool", "browser", "terminal", "shell", "keyboard", "maus",
    ],
    "work": [
        "arbeitszeit", "meeting", "fokus", "produktivität", "remote",
        "büro", "pause", "kalender",
    ],
}

_DEFAULT_SUBCATEGORY = "persoenlich"


# ---------------------------------------------------------------------------
# Phase 121: Typisiertes Result-Objekt für _apply_memory_update
# ---------------------------------------------------------------------------

@dataclass
class MemoryUpdateResult:
    """
    Kapselt das Ergebnis von _apply_memory_update mit klarer Semantik:

    success=True:
        updated_profile enthält das veränderte Profil.

    success=False, allow_fallback=True:
        Ungültige Eingabe (z.B. leerer Name). Caller darf als Notiz speichern.
        user_message ist None → generische Fallback-Meldung.

    success=False, allow_fallback=False:
        Explizit abgelehnte Operation (z.B. bot_instruction delete).
        Kein Fallback-Save. user_message wird direkt an den User gezeigt.
    """
    success: bool
    updated_profile: dict | None = None
    allow_fallback: bool = True
    user_message: str | None = None


def _reject(message: str) -> MemoryUpdateResult:
    """Shorthand: explizit abgelehnte Operation, kein Fallback."""
    return MemoryUpdateResult(success=False, allow_fallback=False, user_message=message)


def _invalid() -> MemoryUpdateResult:
    """Shorthand: ungültige Eingabe, Fallback erlaubt."""
    return MemoryUpdateResult(success=False, allow_fallback=True)


def _ok(profile: dict) -> MemoryUpdateResult:
    """Shorthand: erfolgreiche Aktualisierung."""
    return MemoryUpdateResult(success=True, updated_profile=profile)


# ---------------------------------------------------------------------------
# Preference helpers (Phase 119, unverändert)
# ---------------------------------------------------------------------------

def _infer_subcategory(key: str, value: str) -> str:
    combined = (key + " " + value).lower()
    for subcategory, keywords in _PREF_SUBCATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return subcategory
    return _DEFAULT_SUBCATEGORY


def _flatten_profile_preferences(profile: dict) -> list[tuple[str, str, str]]:
    prefs = profile.get("preferences", {})
    if not isinstance(prefs, dict):
        return []

    result = []
    for k, v in prefs.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, str):
                    result.append((f"preferences.{k}.{sub_k}", sub_k, sub_v))
        elif isinstance(v, str):
            result.append((f"preferences.{k}", k, v))
    return result


def _build_profile_context_for_parser(profile: dict) -> str:
    lines = []

    prefs = _flatten_profile_preferences(profile)
    if prefs:
        lines.append("=== Gespeicherte Preferences ===")
        for path, key, value in prefs:
            lines.append(f"  {path} = \"{value}\"")

    media = profile.get("media", [])
    if isinstance(media, list) and media:
        lines.append("=== Gespeicherte Media ===")
        for m in media[:10]:
            if isinstance(m, dict):
                title = m.get("title", "")
                mtype = m.get("type", "")
                lines.append(f"  media: \"{title}\" ({mtype})")

    people = profile.get("people", [])
    if isinstance(people, list) and people:
        lines.append("=== Gespeicherte Personen ===")
        for p in people[:5]:
            if isinstance(p, dict):
                lines.append(f"  people: \"{p.get('name', '')}\"")

    custom = profile.get("custom", [])
    if isinstance(custom, list) and custom:
        lines.append("=== Custom-Einträge ===")
        for c in custom[:5]:
            if isinstance(c, dict):
                lines.append(f"  custom.{c.get('key', '')} = \"{c.get('value', '')}\"")

    result = "\n".join(lines)
    return result[:900]


def _is_merke_dir_das(text: str) -> bool:
    normalized = text.strip().lower().rstrip("!.?").strip()
    return normalized in MERKE_DIR_DAS_TRIGGERS


def _get_current_human_message(messages: list) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content).strip()
            return str(content).strip()
    return ""


def _get_prev_human_message(messages: list) -> str:
    human_texts = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                text = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content).strip()
            else:
                text = str(content).strip()
            if text:
                human_texts.append(text)

    if len(human_texts) >= 2:
        candidate = human_texts[-2]
        if _is_merke_dir_das(candidate):
            logger.debug("_get_prev_human_message: Kandidat ist selbst ein Trigger – kein Kontext.")
            return ""
        return candidate
    return ""


def _validate_instruction(text: str) -> tuple[bool, str]:
    if not text or not text.strip():
        return False, "Leere Instruktion."
    if len(text) > _INSTRUCTION_MAX_LEN:
        return False, f"Instruktion zu lang (max {_INSTRUCTION_MAX_LEN} Zeichen)."
    if _INSTRUCTION_FORBIDDEN.search(text):
        return False, "Ungültige Bot-Instruktion erkannt."
    return True, ""


async def _formulate_bot_instruction_from_context(context: str) -> str:
    try:
        llm = get_fast_llm()
        prompt = _FORMULATE_PROMPT.format(context=context[:500])
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        result = content.strip().replace("\n", " ").replace("\r", "").strip()
        result = result[:_INSTRUCTION_MAX_LEN]
        logger.info(f"MemoryAgent Phase64: Instruktion formuliert: {result[:80]}")
        return result
    except Exception as e:
        logger.error(f"MemoryAgent Phase64: Formulierung fehlgeschlagen: {e}")
        return ""


# ---------------------------------------------------------------------------
# Parser & Reviewer Prompts (unverändert)
# ---------------------------------------------------------------------------

_PARSER_PROMPT_BASE = """Du bist ein Profil-Manager. Analysiere die Anfrage des Users und den Gesprächskontext.
Bestimme was gespeichert, aktualisiert oder gelöscht werden soll.

Antworte NUR mit reinem JSON – kein Markdown, keine Erklärung.

Format:
{
  "action": "save|update|delete|clarify",
  "category": "people|project|place|media|preference|job|location|custom|bot_instruction",
  "data": { ... }
}

Kategorien und data-Format:

people:
  {"name": "Vollständiger Name", "context": "Beschreibung der Person und Beziehung"}

project:
  {"name": "Projektname", "description": "Kurze Beschreibung", "stack": ["Python"], "priority": "high|medium|low"}

place:
  {"name": "Ortsname", "type": "restaurant|bar|cafe|gym|shop|sonstige", "location": "Stadtteil, Stadt", "context": "Warum relevant"}

media:
  {"title": "Titel", "type": "song|album|film|serie|podcast|buch|künstler", "artist": "optional", "context": "Warum relevant"}

preference:
  {"key": "schluessel", "value": "Wert", "subcategory": "entertainment|lifestyle|tech|work|persoenlich"}

job:
  {"employer": "Firmenname", "role": "Jobtitel", "context": "Zusatzinfo"}

location:
  {"location": "Stadt, Land"}

custom:
  {"key": "schluessel", "value": "Wert"}

bot_instruction:
  {"text": "Die vollständige Bot-Instruktion als präziser Satz"}

Für delete:
  {"name": "Name"} oder {"key": "exakter_schluessel_aus_profil"} oder {"title": "Titel"}

Für clarify (bei Ambiguität):
  {"question": "Meinst du X oder Y?", "options": ["dotted.path.1", "dotted.path.2"]}

Wichtige Regeln:
- Restaurants, Bars, Cafés, Gyms → category=place
- Lieder, Alben, Filme, Serien, Podcasts, Bücher → category=media
- Firmen wo der User arbeitet → category=job
- Eigene Software-Projekte → category=project
- Bot-Verhalten, Antwort-Stil für den Bot → category=bot_instruction
  Trigger: "grundsätzlich", "von jetzt an", "du sollst immer", "dein Verhalten"
- Persönliche Infos über den User → preference oder custom
- Wenn unklar: category=custom

WICHTIG bei delete + preference/custom:
- Schau zuerst in den Profil-Kontext unten.
- Wenn der User einen Wert nennt (z.B. "Star Trek"), such den zugehörigen Key im Profil.
- Verwende IMMER den exakten Key aus dem Profil, nicht den genannten Wert.
- Wenn mehrere Keys auf den Begriff passen → action=clarify mit options-Liste.
- Wenn kein Treffer im Profil → key = genannter Begriff (Fallback).

WICHTIG bei save + preference:
- Wähle eine passende subcategory: entertainment, lifestyle, tech, work, persoenlich
- Beispiele: Lieblingsfilm → entertainment, Sport → lifestyle, Editor → tech
"""

_REVIEWER_PROMPT = """Du bist ein YAML-Validator. Vergleiche Original- und neues YAML.

Aktion: {action}

Original:
<original>
{original}
</original>

Neu:
<new>
{new}
</new>

Antworte NUR mit einem einzigen Wort:
VALID   – Bei save/update: YAML-Syntax korrekt, alle Original-Daten erhalten, nur sinnvolle Ergänzungen/Änderungen.
          Bei delete: YAML-Syntax korrekt, mindestens ein Eintrag/Feld/Wert wurde entfernt oder ein Block
          wurde geleert, alle nicht betroffenen Daten sind unverändert erhalten.
INVALID – YAML kaputt, unerwartete Daten fehlen (bei save/update), oder verdächtige Inhalte.

Nur VALID oder INVALID."""


async def _parse_memory_intent(messages: list, profile: dict | None = None) -> dict[str, Any]:
    """
    Phase 119d: Profil-Kontext als HumanMessage statt im System-Prompt.
    """
    try:
        llm = get_llm()
        all_filtered = []
        for m in messages:
            c = m.content if hasattr(m, "content") else ""
            if isinstance(c, str) and c.startswith(("__CONFIRM_", "__SCREENSHOT__", "__MEMORY__")):
                continue
            all_filtered.append(m)
        context_msgs = all_filtered[-6:]

        system_prompt = f"[Aktuelles Datum/Uhrzeit: {get_current_datetime()}]\n" + _PARSER_PROMPT_BASE

        extra_msgs = []
        if profile is not None:
            profile_context = _build_profile_context_for_parser(profile)
            if profile_context.strip():
                extra_msgs = [HumanMessage(content=f"[Profil-Kontext für diesen Request]\n{profile_context}")]

        response = await llm.ainvoke([SystemMessage(content=system_prompt)] + extra_msgs + context_msgs)
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        content = content.strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
        logger.debug(f"MemoryAgent Parser raw: {content[:120]}")
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or "action" not in parsed:
            return {"action": "error"}
        return parsed
    except Exception as e:
        logger.error(f"MemoryAgent Parser Fehler: {e!r} | raw={locals().get('content', 'N/A')[:80]}")
        return {"action": "error"}


def _is_valid_delete(original: dict, updated: dict) -> bool:
    """
    Phase 115: Strukturelle Subset-Prüfung für delete-Operationen.
    Unverändert – funktioniert für nested preferences genauso.
    """
    try:
        original_str = yaml.dump(original, allow_unicode=True, sort_keys=True)
        updated_str = yaml.dump(updated, allow_unicode=True, sort_keys=True)

        if original_str == updated_str:
            logger.warning("MemoryAgent _is_valid_delete: original == updated, kein Eintrag entfernt")
            return False

        def is_subset(new: Any, orig: Any) -> bool:
            if isinstance(new, dict) and isinstance(orig, dict):
                for k, v in new.items():
                    if k not in orig:
                        return False
                    if not is_subset(v, orig[k]):
                        return False
                return True
            elif isinstance(new, list) and isinstance(orig, list):
                # dict.__eq__ ist reihenfolge-unabhängig – korrekt für people/places/media
                for item in new:
                    if item not in orig:
                        return False
                return True
            else:
                return new == orig

        result = is_subset(updated, original)
        if not result:
            logger.warning("MemoryAgent _is_valid_delete: neue Daten gefunden – kein valider Delete")
        return result

    except Exception as e:
        logger.error(f"MemoryAgent _is_valid_delete: Fehler bei Subset-Prüfung: {e}")
        return False


async def _review_yaml(original_yaml: str, new_yaml: str, action: str = "save") -> bool:
    """
    Phase 115: Bei delete → strukturelle Subset-Vorprüfung ohne LLM-Call.
    Bei save/update → LLM-Reviewer wie bisher.
    """
    try:
        if action == "delete":
            try:
                original_dict = yaml.safe_load(original_yaml)
                updated_dict = yaml.safe_load(new_yaml)
            except yaml.YAMLError as e:
                logger.error(f"MemoryAgent _review_yaml delete: YAML-Parse-Fehler: {e}")
                return False

            if not isinstance(original_dict, dict) or not isinstance(updated_dict, dict):
                logger.error("MemoryAgent _review_yaml delete: kein dict nach YAML-Parse")
                return False

            result = _is_valid_delete(original_dict, updated_dict)
            logger.info(f"MemoryAgent _review_yaml delete: strukturelle Prüfung → {'VALID' if result else 'INVALID'}")
            return result

        llm = get_fast_llm()
        prompt = _REVIEWER_PROMPT.format(
            action=action,
            original=original_yaml[:2000],
            new=new_yaml[:2000],
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        verdict = content.strip().upper()
        is_valid = verdict == "VALID"
        if not is_valid:
            logger.warning(f"MemoryAgent Reviewer: INVALID – verdict='{verdict}'")
        return is_valid

    except Exception as e:
        logger.error(f"MemoryAgent Reviewer Fehler: {e}")
        return False


# ---------------------------------------------------------------------------
# Phase 121: _apply_memory_update gibt MemoryUpdateResult zurück
# ---------------------------------------------------------------------------

def _apply_memory_update(profile: dict, action: str, category: str, data: dict) -> MemoryUpdateResult:
    """
    Wendet eine Memory-Operation auf das Profil an.

    Returns:
        MemoryUpdateResult mit klarer Semantik:
        - success=True:  updated_profile enthält das veränderte Profil
        - success=False, allow_fallback=True:   ungültige Eingabe, Fallback ok
        - success=False, allow_fallback=False:  abgelehnte Operation, user_message zeigen
    """
    updated = copy.deepcopy(profile)

    if action in ("save", "update"):
        if category == "people":
            name = data.get("name", "").strip()
            context = data.get("context", "").strip()
            if not name:
                return _invalid()
            if "people" not in updated or not isinstance(updated["people"], list):
                updated["people"] = []
            for p in updated["people"]:
                if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                    if context:
                        p["context"] = context
                    return _ok(updated)
            updated["people"].append({"name": name, "context": context})
            return _ok(updated)

        elif category == "project":
            name = data.get("name", "").strip()
            if not name:
                return _invalid()
            if "projects" not in updated or not isinstance(updated["projects"], dict):
                updated["projects"] = {}
            if "active" not in updated["projects"] or not isinstance(updated["projects"]["active"], list):
                updated["projects"]["active"] = []
            for p in updated["projects"]["active"]:
                if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                    if desc := data.get("description", ""):
                        p["description"] = desc
                    if stack := data.get("stack", []):
                        p["stack"] = stack
                    if priority := data.get("priority", ""):
                        p["priority"] = priority
                    return _ok(updated)
            new_project: dict[str, Any] = {"name": name}
            if desc := data.get("description", "").strip():
                new_project["description"] = desc
            if stack := data.get("stack", []):
                new_project["stack"] = stack if isinstance(stack, list) else []
            new_project["priority"] = data.get("priority", "medium")
            updated["projects"]["active"].append(new_project)
            return _ok(updated)

        elif category == "place":
            name = data.get("name", "").strip()
            if not name:
                return _invalid()
            if "places" not in updated or not isinstance(updated["places"], list):
                updated["places"] = []
            for p in updated["places"]:
                if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                    for key in ("type", "location", "context"):
                        if v := data.get(key, "").strip():
                            p[key] = v
                    return _ok(updated)
            new_place: dict[str, Any] = {"name": name}
            for key in ("type", "location", "context"):
                if v := data.get(key, "").strip():
                    new_place[key] = v
            updated["places"].append(new_place)
            return _ok(updated)

        elif category == "media":
            title = data.get("title", "").strip()
            if not title:
                return _invalid()
            media_type = data.get("type", "").strip()
            artist = data.get("artist", "").strip()
            context = data.get("context", "").strip()
            if "media" not in updated or not isinstance(updated["media"], list):
                updated["media"] = []
            for m in updated["media"]:
                if isinstance(m, dict) and m.get("title", "").lower() == title.lower():
                    if media_type:
                        m["type"] = media_type
                    if artist:
                        m["artist"] = artist
                    if context:
                        m["context"] = context
                    return _ok(updated)
            new_media: dict[str, Any] = {"title": title}
            if media_type:
                new_media["type"] = media_type
            if artist:
                new_media["artist"] = artist
            if context:
                new_media["context"] = context
            updated["media"].append(new_media)
            return _ok(updated)

        elif category == "preference":
            key = data.get("key", "").strip()
            value = data.get("value", "").strip()
            if not key or not value:
                return _invalid()
            subcategory = data.get("subcategory", "").strip()
            if not subcategory:
                subcategory = _infer_subcategory(key, value)

            if "preferences" not in updated or not isinstance(updated["preferences"], dict):
                updated["preferences"] = {}

            prefs = updated["preferences"]

            if subcategory not in prefs or not isinstance(prefs.get(subcategory), dict):
                if subcategory in prefs and not isinstance(prefs[subcategory], dict):
                    prefs[key] = value
                    logger.info(f"MemoryAgent save preference (flat legacy): '{key}' = '{value}'")
                else:
                    prefs[subcategory] = {}
                    prefs[subcategory][key] = value
                    logger.info(f"MemoryAgent save preference (nested): preferences.{subcategory}.{key} = '{value}'")
            else:
                prefs[subcategory][key] = value
                logger.info(f"MemoryAgent save preference (nested update): preferences.{subcategory}.{key} = '{value}'")

            return _ok(updated)

        elif category == "job":
            employer = data.get("employer", "").strip()
            role = data.get("role", "").strip()
            if not employer:
                return _invalid()
            if "work" not in updated or not isinstance(updated["work"], dict):
                updated["work"] = {}
            updated["work"]["employer"] = employer
            if role:
                updated["work"]["role"] = role
            if ctx := data.get("context", "").strip():
                updated["work"]["job_context"] = ctx
            return _ok(updated)

        elif category == "location":
            location = data.get("location", "").strip()
            if not location:
                return _invalid()
            if "identity" not in updated or not isinstance(updated["identity"], dict):
                updated["identity"] = {}
            updated["identity"]["location"] = location
            return _ok(updated)

        elif category == "custom":
            key = data.get("key", "").strip()
            value = data.get("value", "").strip()
            if not key or not value:
                return _invalid()
            if "custom" not in updated or not isinstance(updated["custom"], list):
                updated["custom"] = []
            for item in updated["custom"]:
                if isinstance(item, dict) and item.get("key", "").lower() == key.lower():
                    item["value"] = value
                    return _ok(updated)
            updated["custom"].append({"key": key, "value": value})
            return _ok(updated)

    elif action == "delete":
        if category == "people":
            name = data.get("name", "").strip().lower()
            before = len(updated.get("people", []))
            if "people" in updated and isinstance(updated["people"], list):
                updated["people"] = [
                    p for p in updated["people"]
                    if not (isinstance(p, dict) and p.get("name", "").lower() == name)
                ]
            after = len(updated.get("people", []))
            if before == after:
                logger.warning(f"MemoryAgent delete people: kein Match für '{name}'")
            return _ok(updated)

        elif category == "project":
            name = data.get("name", "").strip().lower()
            if not name:
                logger.warning("MemoryAgent delete project: leerer name – Operation abgelehnt")
                return _reject("Welches Projekt soll gelöscht werden? Bitte den Namen nennen.")
            active_before = (
                updated.get("projects", {}).get("active", [])
                if isinstance(updated.get("projects"), dict) else []
            )
            before = len(active_before)
            if "projects" in updated and isinstance(updated["projects"], dict):
                active = updated["projects"].get("active", [])
                if isinstance(active, list):
                    updated["projects"]["active"] = [
                        p for p in active
                        if not (isinstance(p, dict) and p.get("name", "").lower() == name)
                    ]
            after = len(updated.get("projects", {}).get("active", []))
            if before == after:
                logger.warning(f"MemoryAgent delete project: kein Match für '{name}'")
            return _ok(updated)

        elif category == "place":
            name = data.get("name", "").strip().lower()
            before = len(updated.get("places", []))
            if "places" in updated and isinstance(updated["places"], list):
                updated["places"] = [
                    p for p in updated["places"]
                    if not (isinstance(p, dict) and p.get("name", "").lower() == name)
                ]
            after = len(updated.get("places", []))
            if before == after:
                logger.warning(f"MemoryAgent delete place: kein Match für '{name}'")
            return _ok(updated)

        elif category == "media":
            title = data.get("title", "").strip().lower()
            before = len(updated.get("media", []))
            if "media" in updated and isinstance(updated["media"], list):
                updated["media"] = [
                    m for m in updated["media"]
                    if not (isinstance(m, dict) and m.get("title", "").lower() == title)
                ]
            after = len(updated.get("media", []))
            if before == after:
                logger.warning(f"MemoryAgent delete media: kein Match für '{title}'")
            return _ok(updated)

        elif category == "preference":
            key = data.get("key", "").strip().lower()
            if not key:
                logger.warning("MemoryAgent delete preference: kein 'key' in data")
                return _invalid()

            if "preferences" not in updated or not isinstance(updated["preferences"], dict):
                logger.warning("MemoryAgent delete preference: keine preferences-Sektion vorhanden")
                return _ok(updated)

            prefs = updated["preferences"]

            # 1. Exakter Key-Match auf flacher Ebene
            matched_flat = next(
                (k for k in prefs if isinstance(prefs[k], str) and k.lower() == key), None
            )
            if matched_flat:
                del prefs[matched_flat]
                logger.info(f"MemoryAgent delete preference (flat): '{matched_flat}' entfernt")
                return _ok(updated)

            # 2. Key-Match in nested Subcategories
            for subcat, subdict in list(prefs.items()):
                if isinstance(subdict, dict):
                    matched_nested = next((k for k in subdict if k.lower() == key), None)
                    if matched_nested:
                        del subdict[matched_nested]
                        if not subdict:
                            del prefs[subcat]
                        logger.info(f"MemoryAgent delete preference (nested): preferences.{subcat}.{matched_nested} entfernt")
                        return _ok(updated)

            # 3. Wert-basierte Suche (Phase 119): "Star Trek" → findet favorite_fantasy_series
            matched_by_value_flat = next(
                (k for k, v in prefs.items() if isinstance(v, str) and v.lower() == key), None
            )
            if matched_by_value_flat:
                del prefs[matched_by_value_flat]
                logger.info(f"MemoryAgent delete preference (flat value-match): '{matched_by_value_flat}' entfernt (Wert='{key}')")
                return _ok(updated)

            for subcat, subdict in list(prefs.items()):
                if isinstance(subdict, dict):
                    matched_by_value_nested = next(
                        (k for k, v in subdict.items() if isinstance(v, str) and v.lower() == key), None
                    )
                    if matched_by_value_nested:
                        del subdict[matched_by_value_nested]
                        if not subdict:
                            del prefs[subcat]
                        logger.info(f"MemoryAgent delete preference (nested value-match): preferences.{subcat}.{matched_by_value_nested} entfernt (Wert='{key}')")
                        return _ok(updated)

            logger.warning(f"MemoryAgent delete preference: kein Match für Key oder Wert '{key}'")
            return _ok(updated)

        elif category == "location":
            if "identity" in updated and isinstance(updated["identity"], dict):
                if "location" in updated["identity"]:
                    del updated["identity"]["location"]
                    logger.info("MemoryAgent delete location: location aus identity entfernt")
                else:
                    logger.warning("MemoryAgent delete location: kein location-Feld in identity")
            else:
                logger.warning("MemoryAgent delete location: keine identity-Sektion vorhanden")
            return _ok(updated)

        elif category == "job":
            key = data.get("key", "").strip().lower()
            if "work" in updated and isinstance(updated["work"], dict):
                if key:
                    matched = next((k for k in updated["work"] if k.lower() == key), None)
                    if matched:
                        del updated["work"][matched]
                        logger.info(f"MemoryAgent delete job field: '{matched}' entfernt")
                    else:
                        logger.warning(f"MemoryAgent delete job: kein Feld '{key}' in work")
                else:
                    updated["work"] = {}
                    logger.info("MemoryAgent delete job: work-Block geleert")
            else:
                logger.warning("MemoryAgent delete job: keine work-Sektion vorhanden")
            return _ok(updated)

        elif category == "custom":
            key = data.get("key", "").strip().lower()
            before = len(updated.get("custom", []))
            if "custom" in updated and isinstance(updated["custom"], list):
                updated["custom"] = [
                    item for item in updated["custom"]
                    if not (isinstance(item, dict) and item.get("key", "").lower() == key)
                ]
            after = len(updated.get("custom", []))
            if before == after:
                logger.warning(f"MemoryAgent delete custom: kein Match für '{key}'")
            return _ok(updated)

        elif category == "bot_instruction":
            # Bot-Instruktionen werden in claude.md verwaltet, nicht im Profil.
            # Kein Fallback-Save – explizite Ablehnung mit User-Hinweis.
            logger.warning("MemoryAgent delete bot_instruction: nicht via memory_agent löschbar")
            return _reject("Bot-Instruktionen können nur manuell in claude.md gelöscht werden.")

    # Unbekannte category oder action – Fallback erlaubt
    logger.warning(f"MemoryAgent _apply_memory_update: unbekannte action='{action}' category='{category}'")
    return _invalid()


# ---------------------------------------------------------------------------
# Confirmation builder (unverändert)
# ---------------------------------------------------------------------------

def _build_confirmation(action: str, category: str, data: dict) -> str:
    icons = {
        "people": "👤", "project": "🚀", "place": "📍",
        "media": "🎵", "preference": "⚙️", "job": "💼",
        "location": "🏠", "custom": "📝", "bot_instruction": "🤖",
    }
    icon = icons.get(category, "✅")

    if category == "bot_instruction":
        text = data.get("text", "")
        return f"🤖 Bot-Instruktion gespeichert:\n_{text}_\n\nAb sofort aktiv – kein Neustart nötig."
    if action == "delete":
        name = data.get("name") or data.get("title") or data.get("employer") or data.get("key", "Eintrag")
        return f"🗑️ Gelöscht: {name}"
    if category == "place":
        name = data.get("name", "")
        parts = [f"{icon} Ort gespeichert: **{name}**"]
        if v := data.get("type", ""):
            parts.append(f"Typ: {v}")
        if v := data.get("location", ""):
            parts.append(f"Wo: {v}")
        if v := data.get("context", ""):
            parts.append(f"Kontext: {v}")
        return "\n".join(parts)
    elif category == "media":
        parts = [f"{icon} Gespeichert: **{data.get('title', '')}**"]
        if v := data.get("artist", ""):
            parts.append(f"von {v}")
        if v := data.get("type", ""):
            parts.append(f"({v})")
        if v := data.get("context", ""):
            parts.append(f"– {v}")
        return " ".join(parts)
    elif category == "people":
        return f"{icon} Person gespeichert: **{data.get('name', '')}** – {data.get('context', '')}"
    elif category == "project":
        return f"{icon} Projekt gespeichert: **{data.get('name', '')}** – {data.get('description', '')}"
    elif category == "job":
        return f"{icon} Job aktualisiert: **{data.get('role', '')}** bei {data.get('employer', '')}"
    elif category == "location":
        return f"🏠 Standort aktualisiert: {data.get('location', '')}"
    elif category == "preference":
        subcat = data.get("subcategory", "")
        key = data.get("key", "")
        value = data.get("value", "")
        if subcat:
            return f"{icon} Präferenz gespeichert [{subcat}]: {key} = {value}"
        return f"{icon} Präferenz gespeichert: {key} = {value}"
    elif category == "custom":
        return f"{icon} Notiert: {data.get('value', '')}"
    return "✅ Gespeichert."


def _make_result(msg: str) -> AgentState:
    """Phase 99: Einheitlicher Return mit last_agent_result."""
    return {
        "messages": [AIMessage(content=msg)],
        "last_agent_result": msg,
        "last_agent_name": "memory_agent",
    }


def _build_clarify_message(data: dict) -> str:
    question = data.get("question", "Welchen Eintrag meinst du?")
    options = data.get("options", [])
    if options:
        opts_str = "\n".join(f"  • {o}" for o in options[:5])
        return f"❓ {question}\n\n{opts_str}"
    return f"❓ {question}"


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

async def memory_agent(state: AgentState) -> AgentState:
    """
    Vollständige Memory-Pipeline.
    Phase 99:  last_agent_result + last_agent_name in allen Returns.
    Phase 115: _review_yaml generisch delete-aware (Closes #39).
    Phase 119: Profil-Kontext im Parser, nested preferences, clarify-Action (Closes #40).
    Phase 121: MemoryUpdateResult – typisiertes Result-Objekt, kein falsches Feedback (Closes #44).
    """
    try:
        current_human = _get_current_human_message(state["messages"])
        if _is_merke_dir_das(current_human):
            prev_human = _get_prev_human_message(state["messages"])
            if not prev_human:
                return _make_result("Worauf beziehst du dich? Ich brauche eine vorherige Aussage von dir als Kontext.")
            instruction = await _formulate_bot_instruction_from_context(prev_human)
            if not instruction:
                return _make_result("Konnte keine Bot-Instruktion formulieren. Bitte beschreibe konkret was ich mir merken soll.")

            valid, reason = _validate_instruction(instruction)
            if not valid:
                logger.warning(f"MemoryAgent Phase89: formulierte Instruktion abgelehnt: {reason}")
                return _make_result("Konnte keine gültige Bot-Instruktion formulieren. Bitte formuliere es anders.")

            from agent.claude_md import append_to_claude_md
            success = await append_to_claude_md(instruction)
            if success:
                return _make_result(f"🤖 Bot-Instruktion gespeichert:\n_{instruction}_\n\nAb sofort aktiv – kein Neustart nötig.")
            else:
                return _make_result("Fehler beim Speichern der Bot-Instruktion.")

        current_profile = load_profile()

        parsed = await _parse_memory_intent(state["messages"], profile=current_profile)
        action = parsed.get("action", "error")
        category = parsed.get("category", "custom")
        data = parsed.get("data", {})

        if action == "error" or not data:
            return _make_result("Möchtest du dass ich mir etwas Bestimmtes merke? Falls ja, sag z.B.: 'Merke dir dass ich gerne House-Musik höre.' 😊")

        if action == "clarify":
            msg = _build_clarify_message(data)
            return _make_result(msg)

        if action in ("save", "update") and category == "bot_instruction":
            text = data.get("text", "").strip()
            if not text:
                return _make_result("Was soll ich mir grundsätzlich merken? Bitte etwas konkreter formulieren.")

            valid, reason = _validate_instruction(text)
            if not valid:
                logger.warning(f"MemoryAgent Phase89: bot_instruction abgelehnt – {reason}: {text[:80]}")
                return _make_result(f"Bot-Instruktion konnte nicht gespeichert werden: {reason}")

            from agent.claude_md import append_to_claude_md
            success = await append_to_claude_md(text)
            if success:
                confirmation = _build_confirmation(action, category, data)
                return _make_result(confirmation)
            else:
                return _make_result("Fehler beim Speichern der Bot-Instruktion in claude.md.")

        # Phase 121: MemoryUpdateResult mit klarer Semantik
        result = _apply_memory_update(current_profile, action, category, data)

        if not result.success:
            if not result.allow_fallback:
                # Explizit abgelehnte Operation (z.B. bot_instruction delete) –
                # user_message direkt zeigen, kein Profil-Schreibzugriff.
                logger.info(f"MemoryAgent: Operation abgelehnt (action={action} category={category})")
                return _make_result(result.user_message or "Diese Operation wird nicht unterstützt.")

            # Ungültige Eingabe – Fallback-Save als Notiz
            fallback = f"[Memory] {action} {category}: {json.dumps(data, ensure_ascii=False)[:150]}"
            await add_note_to_profile(fallback)
            confirmation = _build_confirmation(action, category, data)
            return _make_result(f"{confirmation}\n_(als Notiz gespeichert)_")

        updated_profile = result.updated_profile
        original_yaml = yaml.dump(current_profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
        new_yaml = yaml.dump(updated_profile, allow_unicode=True, default_flow_style=False, sort_keys=False)

        is_valid = await _review_yaml(original_yaml, new_yaml, action=action)
        if not is_valid:
            logger.warning(f"MemoryAgent Phase89: YAML-Review INVALID – kein Schreiben (action={action} category={category})")
            return _make_result("Konnte nicht gespeichert werden – bitte nochmal versuchen.")

        try:
            yaml.safe_load(new_yaml)
        except yaml.YAMLError as e:
            logger.error(f"MemoryAgent: finale YAML-Validierung fehlgeschlagen: {e}")
            return _make_result("Fehler bei der YAML-Validierung – bitte nochmal versuchen.")

        success = await write_profile(updated_profile)
        if not success:
            return _make_result("Fehler beim Schreiben des Profils. Bitte versuche es nochmal.")

        logger.info(f"MemoryAgent: {action} {category} erfolgreich – data={str(data)[:80]}")
        confirmation = _build_confirmation(action, category, data)
        return _make_result(confirmation)

    except Exception as e:
        logger.error(f"MemoryAgent: unerwarteter Fehler: {e}")
        return _make_result("Ein unerwarteter Fehler ist aufgetreten. Bitte versuche es nochmal.")


def _build_memory_prompt() -> str:
    """Ph.98 Kompatibilitäts-Alias – Ph.99: get_current_datetime() direkt in _parse_memory_intent()."""
    from agent.utils import get_current_datetime
    return f"[Aktuelles Datum/Uhrzeit: {get_current_datetime()}]"
