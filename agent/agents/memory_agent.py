"""
Memory Agent fuer FabBot – Phase 45/46/63/64.

Phase 64: "Merke dir das" – vorherige Aussage wird als Bot-Instruktion gespeichert.

Workflow:
    User: "Ich antworte morgens meistens kurz weil ich im Flow bin"
    Bot:  [normale Antwort]
    User: "Merke dir das"
    Bot:  🤖 Bot-Instruktion gespeichert:
          _Fabio antwortet morgens kurz – er ist im Flow, kurz bleiben_
          Ab sofort aktiv – kein Neustart nötig.

Trigger-Phrases (exakter Match nach Normalisierung):
    "merke dir das", "merk dir das", "merke das", "merk das",
    "das merken", "bitte merk dir das", etc.

Pipeline:
- "merke dir das" → _is_merke_dir_das() → vorherige HumanMessage holen
  → Sonnet formuliert Bot-Instruktion → append_to_claude_md()
- Alle anderen Memory-Befehle → normaler Pfad (profile.yaml oder claude.md)
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
# Phase 64: "Merke dir das" Trigger
# ---------------------------------------------------------------------------

_MERKE_DIR_DAS_TRIGGERS = frozenset({
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
- "Wenn Fabio beschaeftigt klingt, Antworten besonders kurz halten"
"""


def _is_merke_dir_das(text: str) -> bool:
    """Erkennt kurze 'merke dir das' Nachrichten ohne weiteren spezifischen Inhalt."""
    normalized = text.strip().lower().rstrip("!.?").strip()
    return normalized in _MERKE_DIR_DAS_TRIGGERS


def _get_current_human_message(messages: list) -> str:
    """Gibt den Text der letzten HumanMessage zurueck."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content).strip()
            return str(content).strip()
    return ""


def _get_prev_human_message(messages: list) -> str:
    """Gibt den Text der vorletzten HumanMessage zurueck (vor 'merke dir das')."""
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
    # Letzte = "merke dir das", vorletzte = der Kontext
    if len(human_texts) >= 2:
        return human_texts[-2]
    return ""


async def _formulate_bot_instruction_from_context(context: str) -> str:
    """
    Sonnet formuliert aus einer Aussage des Users eine Bot-Instruktion.
    Gibt leeren String zurueck bei Fehler.
    """
    try:
        llm = get_llm()
        prompt = _FORMULATE_PROMPT.format(context=context[:500])
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        result = content.strip()
        logger.info(f"MemoryAgent Phase64: Instruktion formuliert: {result[:80]}")
        return result
    except Exception as e:
        logger.error(f"MemoryAgent Phase64: Formulierung fehlgeschlagen: {e}")
        return ""


# ---------------------------------------------------------------------------
# Parser-Prompt (Sonnet – Stufe 1)
# ---------------------------------------------------------------------------

_PARSER_PROMPT = """Du bist ein Profil-Manager. Analysiere die Anfrage des Users und den Gesprächskontext.
Bestimme was gespeichert, aktualisiert oder gelöscht werden soll.

Antworte NUR mit reinem JSON – kein Markdown, keine Erklärung.

Format:
{
  "action": "save|update|delete",
  "category": "people|project|place|media|preference|job|location|custom|bot_instruction",
  "data": { ... }
}

Kategorien und data-Format:

people:
  {"name": "Vollständiger Name", "context": "Beschreibung der Person und Beziehung"}

project:
  {"name": "Projektname", "description": "Kurze Beschreibung", "stack": ["Python"], "priority": "high|medium|low"}

place:
  {"name": "Ortsname", "type": "restaurant|bar|cafe|gym|shop|sonstige", "location": "Stadtteil, Stadt", "context": "Warum relevant, mit wem, wie oft"}

media:
  {"title": "Titel", "type": "song|album|film|serie|podcast|buch|künstler", "artist": "Künstler/Regisseur/Autor (optional)", "context": "Warum relevant"}

preference:
  {"key": "aussagekraeftiger_schluessel", "value": "Wert als Text"}

job:
  {"employer": "Firmenname", "role": "Jobtitel", "context": "Zusatzinfo"}

location:
  {"location": "Stadt, Land"}

custom:
  {"key": "aussagekraeftiger_schluessel", "value": "Wert als Text"}

bot_instruction:
  {"text": "Die vollständige Bot-Instruktion als präziser, aktionsorientierter Satz"}

Für delete:
  {"name": "Name"} oder {"key": "Schlüssel"} oder {"title": "Titel"}

Wichtige Regeln:
- Restaurants, Bars, Cafés, Gyms → category=place
- Lieder, Alben, Filme, Serien, Podcasts, Bücher → category=media
- Firmen wo der User arbeitet → category=job
- Eigene Software-Projekte → category=project
- Bot-Verhalten, Antwort-Stil, dauerhafte Instruktionen FÜR DEN BOT → category=bot_instruction
  Trigger: "grundsätzlich", "von jetzt an", "du sollst immer", "dein Verhalten"
- Persönliche Infos ÜBER DEN USER → preference oder custom
- Wenn unklar: category=custom

Beispiele:
"füge Saporito zum Kontext hinzu – Lieblings-Italiener in Friedrichshain"
→ {"action": "save", "category": "place", "data": {"name": "Saporito", "type": "restaurant", "location": "Friedrichshain, Berlin", "context": "Lieblings-Italiener"}}

"merke dir grundsätzlich dass du immer vollständige Dateien lieferst"
→ {"action": "save", "category": "bot_instruction", "data": {"text": "Immer vollständige Dateien liefern, keine Snippets"}}

"von jetzt an sollst du kürzer antworten"
→ {"action": "save", "category": "bot_instruction", "data": {"text": "Antworten kurz halten, auf den Punkt kommen"}}

"merke dir dass ich gerne Yoga mache"
→ {"action": "save", "category": "custom", "data": {"key": "hobby_yoga", "value": "macht gerne Yoga"}}
"""

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


async def _parse_memory_intent(messages: list) -> dict[str, Any]:
    """Sonnet versteht die Anfrage + Kontext → strukturiertes JSON."""
    try:
        llm = get_llm()
        all_filtered = []
        for m in messages:
            c = m.content if hasattr(m, "content") else ""
            if isinstance(c, str) and c.startswith(("__CONFIRM_", "__SCREENSHOT__", "__MEMORY__")):
                continue
            all_filtered.append(m)
        context_msgs = all_filtered[-6:]

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


def _apply_memory_update(profile: dict, action: str, category: str, data: dict) -> dict | None:
    """Wendet das geparste Update auf das Profil-Dict an."""
    updated = copy.deepcopy(profile)

    if action in ("save", "update"):

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

        elif category == "media":
            title = data.get("title", "").strip()
            if not title:
                return None
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
                    return updated
            new_media: dict[str, Any] = {"title": title}
            if media_type:
                new_media["type"] = media_type
            if artist:
                new_media["artist"] = artist
            if context:
                new_media["context"] = context
            updated["media"].append(new_media)
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

        elif category == "media":
            title = data.get("title", "").strip().lower()
            if "media" in updated and isinstance(updated["media"], list):
                updated["media"] = [
                    m for m in updated["media"]
                    if not (isinstance(m, dict) and m.get("title", "").lower() == title)
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


async def _review_yaml(original_yaml: str, new_yaml: str) -> bool:
    """Haiku reviewt das neue YAML."""
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
            logger.warning(f"MemoryAgent Reviewer: INVALID – verdict='{verdict}'")
        return is_valid
    except Exception as e:
        logger.error(f"MemoryAgent Reviewer Fehler: {e}")
        return False


def _build_confirmation(action: str, category: str, data: dict) -> str:
    """Baut eine lesbare Bestätigungsnachricht für den User."""
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
        name = data.get("name") or data.get("title") or data.get("key", "Eintrag")
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

    elif category == "media":
        title = data.get("title", "")
        media_type = data.get("type", "")
        artist = data.get("artist", "")
        context = data.get("context", "")
        parts = [f"{icon} Gespeichert: **{title}**"]
        if artist:
            parts.append(f"von {artist}")
        if media_type:
            parts.append(f"({media_type})")
        if context:
            parts.append(f"– {context}")
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
        return f"{icon} Präferenz gespeichert: {data.get('key', '')} = {data.get('value', '')}"

    elif category == "custom":
        return f"{icon} Notiert: {data.get('value', '')}"

    return "✅ Gespeichert."


async def memory_agent(state: AgentState) -> AgentState:
    """
    Vollständige Memory-Pipeline.
    Phase 63: bot_instruction → claude.md
    Phase 64: 'merke dir das' → vorherige Aussage als Bot-Instruktion
    """
    try:
        import yaml
        from agent.profile import load_profile, add_note_to_profile, write_profile

        # ── Phase 64: "Merke dir das" → vorherige Message als Bot-Instruktion ──
        current_human = _get_current_human_message(state["messages"])
        if _is_merke_dir_das(current_human):
            prev_human = _get_prev_human_message(state["messages"])
            if not prev_human:
                return {"messages": [AIMessage(content="Worauf beziehst du dich? Ich brauche eine vorherige Aussage von dir als Kontext.")]}

            instruction = await _formulate_bot_instruction_from_context(prev_human)
            if not instruction:
                return {"messages": [AIMessage(content="Konnte keine Bot-Instruktion formulieren. Bitte beschreibe konkret was ich mir merken soll.")]}

            from agent.claude_md import append_to_claude_md
            success = await append_to_claude_md(instruction)
            if success:
                logger.info(f"MemoryAgent Phase64: gespeichert aus Kontext: {instruction[:80]}")
                return {"messages": [AIMessage(content=f"🤖 Bot-Instruktion gespeichert:\n_{instruction}_\n\nAb sofort aktiv – kein Neustart nötig.")]}
            else:
                return {"messages": [AIMessage(content="Fehler beim Speichern der Bot-Instruktion.")]}
        # ────────────────────────────────────────────────────────────────────────

        # Stufe 1: Sonnet parst die Anfrage
        parsed = await _parse_memory_intent(state["messages"])
        action = parsed.get("action", "error")
        category = parsed.get("category", "custom")
        data = parsed.get("data", {})

        if action == "error" or not data:
            return {"messages": [AIMessage(content="Möchtest du dass ich mir etwas Bestimmtes merke? Falls ja, sag z.B.: 'Merke dir dass ich gerne House-Musik höre.' 😊")]}

        # ── Phase 63: bot_instruction → claude.md ────────────────────────────
        if action in ("save", "update") and category == "bot_instruction":
            text = data.get("text", "").strip()
            if not text:
                return {"messages": [AIMessage(content="Was soll ich mir grundsätzlich merken? Bitte etwas konkreter formulieren.")]}
            from agent.claude_md import append_to_claude_md
            success = await append_to_claude_md(text)
            if success:
                logger.info(f"MemoryAgent: bot_instruction gespeichert: {text[:80]}")
                return {"messages": [AIMessage(content=_build_confirmation(action, category, data))]}
            else:
                return {"messages": [AIMessage(content="Fehler beim Speichern der Bot-Instruktion in claude.md.")]}
        # ─────────────────────────────────────────────────────────────────────

        # Stufe 2: Python-seitiger Update (profile.yaml)
        current_profile = load_profile()
        updated_profile = _apply_memory_update(current_profile, action, category, data)

        if updated_profile is None:
            fallback = f"[Memory] {action} {category}: {json.dumps(data, ensure_ascii=False)[:150]}"
            await add_note_to_profile(fallback)
            logger.warning(f"MemoryAgent: _apply_memory_update returned None – Fallback zu Note")
            confirmation = _build_confirmation(action, category, data)
            return {"messages": [AIMessage(content=f"{confirmation}\n_(als Notiz gespeichert)_")]}

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
            await add_note_to_profile(fallback)
            logger.warning("MemoryAgent: Reviewer abgelehnt – Fallback zu Note")
            confirmation = _build_confirmation(action, category, data)
            return {"messages": [AIMessage(content=f"{confirmation}\n_(als Notiz gespeichert – YAML-Review fehlgeschlagen)_")]}

        try:
            yaml.safe_load(new_yaml)
        except yaml.YAMLError as e:
            logger.error(f"MemoryAgent: finale YAML-Validierung fehlgeschlagen: {e}")
            await add_note_to_profile(f"[Memory] {action} {category}: {json.dumps(data, ensure_ascii=False)[:150]}")
            return {"messages": [AIMessage(content="Fehler bei der YAML-Validierung – als Notiz gespeichert.")]}

        success = await write_profile(updated_profile)
        if not success:
            return {"messages": [AIMessage(content="Fehler beim Schreiben des Profils. Bitte versuche es nochmal.")]}

        logger.info(f"MemoryAgent: {action} {category} erfolgreich – data={str(data)[:80]}")
        return {"messages": [AIMessage(content=_build_confirmation(action, category, data))]}

    except Exception as e:
        logger.error(f"MemoryAgent: unerwarteter Fehler: {e}")
        return {"messages": [AIMessage(content="Ein unerwarteter Fehler ist aufgetreten. Bitte versuche es nochmal.")]}
