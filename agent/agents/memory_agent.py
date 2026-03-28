"""
Memory Agent für FabBot – Phase 45.

Verarbeitet explizite Befehle des Users zum Speichern, Aktualisieren
oder Löschen persönlicher Informationen im Profil.

Unterstützte Kategorien (Option 3 – Hybrid):
Feste Sektionen: people, projects, places, preferences, work, identity
Freie Sektion:   custom (key/value Paare für alles andere)

Pipeline:
1. Sonnet versteht die Anfrage + Gesprächskontext → strukturiertes JSON
2. Python wendet Update an (kein LLM schreibt YAML direkt)
3. Haiku reviewt das neue YAML
4. Schreiben via write_profile()
5. Antwort an User mit Bestätigung was gespeichert wurde

Fail-safe: Bei Fehler → Fallback zu add_note_to_profile() + Fehlermeldung an User.
"""

import copy
import json
import logging
import re
from typing import Any

from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.llm import get_llm, get_fast_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parser-Prompt (Sonnet – Stufe 1)
# Sonnet weil er Kontext + Nuancen versteht
# ---------------------------------------------------------------------------

_PARSER_PROMPT = """Du bist ein Profil-Manager. Analysiere die Anfrage des Users und den Gesprächskontext.
Bestimme was gespeichert, aktualisiert oder gelöscht werden soll.

Antworte NUR mit reinem JSON – kein Markdown, keine Erklärung.

Format:
{
  "action": "save|update|delete",
  "category": "people|project|place|preference|job|location|custom",
  "data": { ... }
}

Kategorien und data-Format:

people:
  {"name": "Vollständiger Name", "context": "Beschreibung der Person und Beziehung"}

project:
  {"name": "Projektname", "description": "Kurze Beschreibung", "stack": ["Python"], "priority": "high|medium|low"}

place:
  {"name": "Ortsname", "type": "restaurant|bar|cafe|gym|shop|sonstige", "location": "Stadtteil, Stadt", "context": "Warum relevant, mit wem, wie oft"}

preference:
  {"key": "aussagekraeftiger_schluessel", "value": "Wert als Text"}

job:
  {"employer": "Firmenname", "role": "Jobtitel", "context": "Zusatzinfo"}

location:
  {"location": "Stadt, Land"}

custom:
  {"key": "aussagekraeftiger_schluessel", "value": "Wert als Text"}

Für delete:
  {"name": "Name des Eintrags" } oder {"key": "Schlüssel"}

Wichtige Regeln:
- Restaurants, Bars, Cafés, Gyms, Lieblingsläden → IMMER category=place
- Firmen wo der User arbeitet → category=job (NICHT project)
- Eigene Software-Projekte die der User baut → category=project
- Wenn unklar welche Kategorie: category=custom mit sinnvollem key
- Extrahiere alle relevanten Details aus dem Gesprächskontext

Beispiele:
"füge Saporito zum Kontext hinzu" (Kontext: Saporito ist ein Italiener in Friedrichshain, gehe mit Steffi hin)
→ {"action": "save", "category": "place", "data": {"name": "Saporito", "type": "restaurant", "location": "Friedrichshain, Berlin", "context": "Lieblings-Italiener, gehe oft mit Steffi hin"}}

"merke dir dass ich gerne Yoga mache"
→ {"action": "save", "category": "custom", "data": {"key": "hobby_yoga", "value": "macht gerne Yoga"}}

"aktualisiere Marco – er ist jetzt mein Vorgesetzter"
→ {"action": "update", "category": "people", "data": {"name": "Marco", "context": "Vorgesetzter bei Bonial"}}

"vergiss den Eintrag über Bonial als Projekt"
→ {"action": "delete", "category": "project", "data": {"name": "Bonial"}}
"""

# ---------------------------------------------------------------------------
# Reviewer-Prompt (Haiku – Stufe 3)
# ---------------------------------------------------------------------------

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
VALID   – YAML-Syntax korrekt, alle Original-Daten erhalten, nur sinnvolle Ergänzungen/Änderungen
INVALID – YAML kaputt, wichtige Daten fehlen, oder verdächtige Inhalte

Nur VALID oder INVALID."""


# ---------------------------------------------------------------------------
# Stufe 1: Parser (Sonnet)
# ---------------------------------------------------------------------------

async def _parse_memory_intent(messages: list) -> dict[str, Any]:
    """
    Sonnet versteht die Anfrage + Kontext → strukturiertes JSON.
    Fail-safe: Bei Fehler → {"action": "error"}
    """
    try:
        llm = get_llm()
        # Letzte HumanMessage + etwas Kontext für Sonnet
        human_msgs = [m for m in messages if isinstance(m, HumanMessage)]
        last_msg = [human_msgs[-1]] if human_msgs else []
        # Letzten 3 Messages als Kontext (ohne HITL-Prefixes)
        context_msgs = []
        for m in messages[-6:]:
            content = m.content if hasattr(m, "content") else ""
            if isinstance(content, str) and content.startswith(("__CONFIRM_", "__SCREENSHOT__", "__MEMORY__")):
                continue
            context_msgs.append(m)

        response = await llm.ainvoke(
            [SystemMessage(content=_PARSER_PROMPT)] + context_msgs
        )
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

        content = content.strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()

        parsed = json.loads(content)
        if not isinstance(parsed, dict) or "action" not in parsed:
            return {"action": "error"}
        return parsed

    except Exception as e:
        logger.error(f"MemoryAgent Parser Fehler: {e}")
        return {"action": "error"}


# ---------------------------------------------------------------------------
# Stufe 2: Python-seitiger Update
# ---------------------------------------------------------------------------

def _apply_memory_update(profile: dict, action: str, category: str, data: dict) -> dict | None:
    """
    Wendet das geparste Update auf das Profil-Dict an.
    Gibt updated dict zurück, oder None bei Fehler/Duplikat.
    Modifiziert das Original nicht (deepcopy).
    """
    updated = copy.deepcopy(profile)

    if action == "save" or action == "update":

        if category == "people":
            name = data.get("name", "").strip()
            context = data.get("context", "").strip()
            if not name:
                return None
            if "people" not in updated or not isinstance(updated["people"], list):
                updated["people"] = []
            for p in updated["people"]:
                if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                    if context:
                        p["context"] = context
                    return updated
            updated["people"].append({"name": name, "context": context})
            return updated

        elif category == "project":
            name = data.get("name", "").strip()
            if not name:
                return None
            if "projects" not in updated or not isinstance(updated["projects"], dict):
                updated["projects"] = {}
            if "active" not in updated["projects"] or not isinstance(updated["projects"]["active"], list):
                updated["projects"]["active"] = []
            for p in updated["projects"]["active"]:
                if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                    # Update existing
                    if desc := data.get("description", ""):
                        p["description"] = desc
                    if stack := data.get("stack", []):
                        p["stack"] = stack
                    if priority := data.get("priority", ""):
                        p["priority"] = priority
                    return updated
            new_project: dict[str, Any] = {"name": name}
            if desc := data.get("description", "").strip():
                new_project["description"] = desc
            if stack := data.get("stack", []):
                new_project["stack"] = stack if isinstance(stack, list) else []
            new_project["priority"] = data.get("priority", "medium")
            updated["projects"]["active"].append(new_project)
            return updated

        elif category == "place":
            name = data.get("name", "").strip()
            if not name:
                return None
            if "places" not in updated or not isinstance(updated["places"], list):
                updated["places"] = []
            for p in updated["places"]:
                if isinstance(p, dict) and p.get("name", "").lower() == name.lower():
                    # Update existing place
                    for key in ("type", "location", "context"):
                        if v := data.get(key, "").strip():
                            p[key] = v
                    return updated
            new_place: dict[str, Any] = {"name": name}
            for key in ("type", "location", "context"):
                if v := data.get(key, "").strip():
                    new_place[key] = v
            updated["places"].append(new_place)
            return updated

        elif category == "preference":
            key = data.get("key", "").strip()
            value = data.get("value", "").strip()
            if not key or not value:
                return None
            if "preferences" not in updated or not isinstance(updated["preferences"], dict):
                updated["preferences"] = {}
            updated["preferences"][key] = value
            return updated

        elif category == "job":
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

        elif category == "location":
            location = data.get("location", "").strip()
            if not location:
                return None
            if "identity" not in updated or not isinstance(updated["identity"], dict):
                updated["identity"] = {}
            updated["identity"]["location"] = location
            return updated

        elif category == "custom":
            key = data.get("key", "").strip()
            value = data.get("value", "").strip()
            if not key or not value:
                return None
            if "custom" not in updated or not isinstance(updated["custom"], list):
                updated["custom"] = []
            for item in updated["custom"]:
                if isinstance(item, dict) and item.get("key", "").lower() == key.lower():
                    item["value"] = value
                    return updated
            updated["custom"].append({"key": key, "value": value})
            return updated

    elif action == "delete":

        if category == "people":
            name = data.get("name", "").strip().lower()
            if "people" in updated and isinstance(updated["people"], list):
                updated["people"] = [
                    p for p in updated["people"]
                    if not (isinstance(p, dict) and p.get("name", "").lower() == name)
                ]
            return updated

        elif category == "project":
            name = data.get("name", "").strip().lower()
            if "projects" in updated and isinstance(updated["projects"], dict):
                active = updated["projects"].get("active", [])
                if isinstance(active, list):
                    updated["projects"]["active"] = [
                        p for p in active
                        if not (isinstance(p, dict) and p.get("name", "").lower() == name)
                    ]
            return updated

        elif category == "place":
            name = data.get("name", "").strip().lower()
            if "places" in updated and isinstance(updated["places"], list):
                updated["places"] = [
                    p for p in updated["places"]
                    if not (isinstance(p, dict) and p.get("name", "").lower() == name)
                ]
            return updated

        elif category == "custom":
            key = data.get("key", "").strip().lower()
            if "custom" in updated and isinstance(updated["custom"], list):
                updated["custom"] = [
                    item for item in updated["custom"]
                    if not (isinstance(item, dict) and item.get("key", "").lower() == key)
                ]
            return updated

    return None


# ---------------------------------------------------------------------------
# Stufe 3: Reviewer (Haiku)
# ---------------------------------------------------------------------------

async def _review_yaml(original_yaml: str, new_yaml: str) -> bool:
    """
    Haiku reviewt das neue YAML.
    Fail-safe: Bei Fehler → False (kein Schreiben).
    """
    try:
        llm = get_fast_llm()
        from langchain_core.messages import HumanMessage as HM
        prompt = _REVIEWER_PROMPT.format(
            original=original_yaml[:2000],
            new=new_yaml[:2000],
        )
        response = await llm.ainvoke([HM(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        verdict = content.strip().upper()
        is_valid = verdict == "VALID"
        if not is_valid:
            logger.warning("MemoryAgent Reviewer: INVALID")
        return is_valid
    except Exception as e:
        logger.error(f"MemoryAgent Reviewer Fehler: {e}")
        return False


# ---------------------------------------------------------------------------
# Bestätigungstext generieren
# ---------------------------------------------------------------------------

def _build_confirmation(action: str, category: str, data: dict) -> str:
    """Baut eine lesbare Bestätigungsnachricht für den User."""
    icons = {
        "people": "👤", "project": "🚀", "place": "📍",
        "preference": "⚙️", "job": "💼", "location": "🏠",
        "custom": "📝",
    }
    icon = icons.get(category, "✅")

    if action == "delete":
        name = data.get("name") or data.get("key", "Eintrag")
        return f"🗑️ Gelöscht: {name}"

    if category == "place":
        name = data.get("name", "")
        place_type = data.get("type", "")
        location = data.get("location", "")
        context = data.get("context", "")
        parts = [f"{icon} Ort gespeichert: **{name}**"]
        if place_type:
            parts.append(f"Typ: {place_type}")
        if location:
            parts.append(f"Wo: {location}")
        if context:
            parts.append(f"Kontext: {context}")
        return "\n".join(parts)

    elif category == "people":
        return f"{icon} Person gespeichert: **{data.get('name', '')}** – {data.get('context', '')}"

    elif category == "project":
        return f"{icon} Projekt gespeichert: **{data.get('name', '')}** – {data.get('description', '')}"

    elif category == "job":
        return f"{icon} Job aktualisiert: **{data.get('role', '')}** bei {data.get('employer', '')}"

    elif category == "location":
        return f"🏠 Standort aktualisiert: {data.get('location', '')}"

    elif category == "preference":
        return f"{icon} Präferenz gespeichert: {data.get('key', '')} = {data.get('value', '')}"

    elif category == "custom":
        return f"{icon} Notiert: {data.get('value', '')}"

    return "✅ Gespeichert."


# ---------------------------------------------------------------------------
# Haupt-Agent
# ---------------------------------------------------------------------------

async def memory_agent(state: AgentState) -> AgentState:
    """
    Vollständige Memory-Pipeline:
    Parser → Python-Update → Reviewer → Schreiben → Bestätigung an User.
    Fail-safe: Bei jedem Fehler → Fallback zu Note + Fehlermeldung.
    """
    try:
        import yaml
        from agent.profile import load_profile, add_note_to_profile, write_profile

        # Stufe 1: Sonnet parst die Anfrage
        parsed = await _parse_memory_intent(state["messages"])
        action = parsed.get("action", "error")
        category = parsed.get("category", "custom")
        data = parsed.get("data", {})

        if action == "error" or not data:
            return {"messages": [AIMessage(content="Ich konnte nicht verstehen was gespeichert werden soll. Bitte formuliere es klarer, z.B. 'Merke dir dass Saporito mein Lieblings-Restaurant in Friedrichshain ist'.")]}

        # Stufe 2: Python-seitiger Update
        current_profile = load_profile()
        updated_profile = _apply_memory_update(current_profile, action, category, data)

        if updated_profile is None:
            fallback = f"[Memory] {action} {category}: {json.dumps(data, ensure_ascii=False)[:150]}"
            add_note_to_profile(fallback)
            logger.warning(f"MemoryAgent: _apply_memory_update returned None – Fallback zu Note")
            confirmation = _build_confirmation(action, category, data)
            return {"messages": [AIMessage(content=f"{confirmation}\n_(als Notiz gespeichert)_")]}

        # YAML serialisieren
        original_yaml = yaml.dump(
            current_profile, allow_unicode=True, default_flow_style=False, sort_keys=False
        )
        new_yaml = yaml.dump(
            updated_profile, allow_unicode=True, default_flow_style=False, sort_keys=False
        )

        # Stufe 3: Haiku-Reviewer
        is_valid = await _review_yaml(original_yaml, new_yaml)
        if not is_valid:
            fallback = f"[Memory] {action} {category}: {json.dumps(data, ensure_ascii=False)[:150]}"
            add_note_to_profile(fallback)
            logger.warning("MemoryAgent: Reviewer abgelehnt – Fallback zu Note")
            confirmation = _build_confirmation(action, category, data)
            return {"messages": [AIMessage(content=f"{confirmation}\n_(als Notiz gespeichert – YAML-Review fehlgeschlagen)_")]}

        # Python-seitige finale YAML-Validierung
        try:
            yaml.safe_load(new_yaml)
        except yaml.YAMLError as e:
            logger.error(f"MemoryAgent: finale YAML-Validierung fehlgeschlagen: {e}")
            add_note_to_profile(f"[Memory] {action} {category}: {json.dumps(data, ensure_ascii=False)[:150]}")
            return {"messages": [AIMessage(content="Fehler bei der YAML-Validierung – als Notiz gespeichert.")]}

        # Schreiben
        success = write_profile(updated_profile)
        if not success:
            return {"messages": [AIMessage(content="Fehler beim Schreiben des Profils. Bitte versuche es nochmal.")]}

        logger.info(f"MemoryAgent: {action} {category} erfolgreich – data={str(data)[:80]}")
        confirmation = _build_confirmation(action, category, data)
        return {"messages": [AIMessage(content=confirmation)]}

    except Exception as e:
        logger.error(f"MemoryAgent: unerwarteter Fehler: {e}")
        return {"messages": [AIMessage(content="Ein unerwarteter Fehler ist aufgetreten. Bitte versuche es nochmal.")]}