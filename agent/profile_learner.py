"""
Profile Learner für FabBot – Phase 3/45.

Drei-Stufen-Pipeline für ORGANISCHES Lernen (automatisch aus Gesprächen).
Für EXPLIZITE Befehle → memory_agent.

Kategorien: person, project, place, job, location, preference, custom, note
"""

import copy
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_DETECTOR_PROMPT = """Du bist ein Profil-Analysator. Prüfe ob die User-Nachricht neue dauerhafte persönliche Informationen enthält die ORGANISCH erwähnt wurden (nicht als expliziter Befehl).

Antworte NUR mit reinem JSON – kein Markdown, keine Erklärung.

Format wenn neue Info vorhanden:
{"learned": true, "type": "person|project|place|job|location|preference|custom|note", "data": {...}}

Typen und data-Format:
- person:     {"name": "Vollständiger Name", "context": "Kurze Beschreibung"}
- project:    {"name": "Projektname", "description": "Kurze Beschreibung", "stack": [], "priority": "high|medium|low"}
- place:      {"name": "Ortsname", "type": "restaurant|bar|cafe|gym|shop|sonstige", "location": "Stadtteil, Stadt", "context": "Warum relevant"}
- job:        {"employer": "Firmenname", "role": "Jobtitel", "context": "Optionale Zusatzinfo"}
- location:   {"location": "Stadt, Land"}
- preference: {"key": "schluessel", "value": "Wert"}
- custom:     {"key": "schluessel", "value": "Wert"}
- note:       {"text": "Freier Text – Fallback"}

Format wenn KEINE neue Info:
{"learned": false}

Wichtige Unterscheidungen:
- Restaurants, Bars, Cafés, Gyms → type=place
- Arbeitgeber/Job → type=job (NICHT project)
- Eigene Software-Projekte → type=project
- Explizite Befehle ("füge hinzu", "merke dir") → {"learned": false} (handled by memory_agent)

Speichern: organische Erwähnungen von Personen, Orten, Jobs, Projekten.
NICHT speichern: Fragen, explizite Befehle, Smalltalk, Danke, technische Diskussionen.

Beispiele:
"Ich war gestern mit Steffi im Saporito, richtig gutes Essen" → {"learned": true, "type": "place", "data": {"name": "Saporito", "type": "restaurant", "context": "Gutes Essen, war mit Steffi dort"}}
"Ich habe einen neuen Job als Teamlead bei Bonial" → {"learned": true, "type": "job", "data": {"employer": "Bonial", "role": "Teamlead"}}
"füge Saporito hinzu" → {"learned": false}
"Was ist das Wetter?" → {"learned": false}"""

_REVIEWER_PROMPT = """Du bist ein YAML-Validator. Vergleiche Original- und neues YAML.

Original:
<original>
{original}
</original>

Neu:
<new>
{new}
</new>

Antworte NUR mit einem einzigen Wort:
VALID   – YAML-Syntax korrekt, alle Original-Daten erhalten, nur sinnvolle Ergänzungen
INVALID – YAML kaputt, Daten fehlen, oder verdächtige Inhalte

Nur VALID oder INVALID."""


async def _detect_new_info(human_message: str) -> dict[str, Any]:
    try:
        from agent.llm import get_fast_llm
        from langchain_core.messages import HumanMessage, SystemMessage
        llm = get_fast_llm()
        response = await llm.ainvoke([
            SystemMessage(content=_DETECTOR_PROMPT),
            HumanMessage(content=f"User-Nachricht: {human_message[:500]}"),
        ])
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        content = content.strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or "learned" not in parsed:
            return {"learned": False}
        return parsed
    except Exception as e:
        logger.debug(f"ProfileLearner Detector Fehler (ignoriert): {e}")
        return {"learned": False}


def _apply_update(profile: dict, info_type: str, data: dict) -> dict | None:
    """Python-seitiger Update – kein LLM schreibt YAML direkt."""
    updated = copy.deepcopy(profile)

    if info_type == "person":
        name = data.get("name", "").strip()
        context = data.get("context", "").strip()
        if not name:
            return None
        if "people" not in updated or not isinstance(updated["people"], list):
            updated["people"] = []
        for p in updated["people"]:
            if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                p["context"] = context
                return updated
        updated["people"].append({"name": name, "context": context})
        return updated

    elif info_type == "project":
        name = data.get("name", "").strip()
        if not name:
            return None
        if "projects" not in updated or not isinstance(updated["projects"], dict):
            updated["projects"] = {}
        if "active" not in updated["projects"] or not isinstance(updated["projects"]["active"], list):
            updated["projects"]["active"] = []
        for p in updated["projects"]["active"]:
            if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                return None  # Duplikat
        new_project: dict[str, Any] = {"name": name}
        if desc := data.get("description", "").strip():
            new_project["description"] = desc
        if stack := data.get("stack", []):
            new_project["stack"] = stack if isinstance(stack, list) else []
        new_project["priority"] = data.get("priority", "medium")
        updated["projects"]["active"].append(new_project)
        return updated

    elif info_type == "place":
        name = data.get("name", "").strip()
        if not name:
            return None
        if "places" not in updated or not isinstance(updated["places"], list):
            updated["places"] = []
        for p in updated["places"]:
            if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                return None  # Duplikat
        new_place: dict[str, Any] = {"name": name}
        for key in ("type", "location", "context"):
            if v := data.get(key, "").strip():
                new_place[key] = v
        updated["places"].append(new_place)
        return updated

    elif info_type == "job":
        employer = data.get("employer", "").strip()
        role = data.get("role", "").strip()
        if not employer:
            return None
        if "work" not in updated or not isinstance(updated["work"], dict):
            updated["work"] = {}
        updated["work"]["employer"] = employer
        if role:
            updated["work"]["role"] = role
        if ctx := data.get("context", "").strip():
            updated["work"]["job_context"] = ctx
        return updated

    elif info_type == "location":
        location = data.get("location", "").strip()
        if not location:
            return None
        if "identity" not in updated or not isinstance(updated["identity"], dict):
            updated["identity"] = {}
        updated["identity"]["location"] = location
        return updated

    elif info_type == "preference":
        key = data.get("key", "").strip()
        value = data.get("value", "").strip()
        if not key or not value:
            return None
        if "preferences" not in updated or not isinstance(updated["preferences"], dict):
            updated["preferences"] = {}
        updated["preferences"][key] = value
        return updated

    elif info_type == "custom":
        key = data.get("key", "").strip()
        value = data.get("value", "").strip()
        if not key or not value:
            return None
        if "custom" not in updated or not isinstance(updated["custom"], list):
            updated["custom"] = []
        for item in updated["custom"]:
            if isinstance(item, dict) and item.get("key", "").lower() == key.lower():
                return None  # Duplikat
        updated["custom"].append({"key": key, "value": value})
        return updated

    return None


async def _review_yaml(original_yaml: str, new_yaml: str) -> bool:
    try:
        from agent.llm import get_fast_llm
        from langchain_core.messages import HumanMessage
        llm = get_fast_llm()
        prompt = _REVIEWER_PROMPT.format(original=original_yaml[:2000], new=new_yaml[:2000])
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        is_valid = content.strip().upper() == "VALID"
        if not is_valid:
            logger.warning("ProfileLearner Reviewer: INVALID")
        return is_valid
    except Exception as e:
        logger.error(f"ProfileLearner Reviewer Fehler: {e}")
        return False


async def apply_learning(human_message: str) -> None:
    """
    Organisches Lernen als Background-Task.
    Explizite Befehle werden vom Detector ignoriert → memory_agent zuständig.
    """
    try:
        import yaml
        from agent.profile import load_profile, add_note_to_profile, write_profile

        info = await _detect_new_info(human_message)
        if not info.get("learned"):
            return

        info_type = info.get("type", "note")
        data = info.get("data", {})
        logger.info(f"ProfileLearner: neue Info erkannt – type={info_type} data={str(data)[:100]}")

        if info_type == "note":
            text = data.get("text", human_message[:200])
            await add_note_to_profile(text)
            return

        current_profile = load_profile()
        updated_profile = _apply_update(current_profile, info_type, data)

        if updated_profile is None:
            fallback = f"[Auto] {info_type}: {json.dumps(data, ensure_ascii=False)[:150]}"
            await add_note_to_profile(fallback)
            return

        original_yaml = yaml.dump(current_profile, allow_unicode=True, default_flow_style=False, sort_keys=False)
        new_yaml = yaml.dump(updated_profile, allow_unicode=True, default_flow_style=False, sort_keys=False)

        is_valid = await _review_yaml(original_yaml, new_yaml)
        if not is_valid:
            fallback = f"[Auto] {info_type}: {json.dumps(data, ensure_ascii=False)[:150]}"
            await add_note_to_profile(fallback)
            return

        try:
            yaml.safe_load(new_yaml)
        except yaml.YAMLError as e:
            logger.error(f"ProfileLearner: YAML-Validierung fehlgeschlagen: {e}")
            await add_note_to_profile(f"[Auto] {info_type}: {json.dumps(data, ensure_ascii=False)[:150]}")
            return

        await write_profile(updated_profile)
        logger.info(f"ProfileLearner: Profil aktualisiert – type={info_type}")

    except Exception as e:
        logger.warning(f"ProfileLearner: unerwarteter Fehler (ignoriert): {e}")