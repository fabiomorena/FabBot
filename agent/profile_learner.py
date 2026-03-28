"""
Profile Learner für FabBot – Phase 3.

Drei-Stufen-Pipeline:
1. Haiku-Detector  – erkennt ob neue persönliche Info in der User-Nachricht steckt
2. Python-Writer   – strukturierter Update (kein LLM schreibt YAML direkt)
3. Haiku-Reviewer  – validiert das neue YAML vor dem Schreiben
Fallback: add_note_to_profile() wenn Reviewer ablehnt oder Fehler auftritt

Wird als non-blocking asyncio.create_task() aus chat_agent aufgerufen.
Alle Fehler werden geloggt aber nie nach oben weitergereicht.
"""

import copy
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detector-Prompt (Haiku – Stufe 1)
# ---------------------------------------------------------------------------

_DETECTOR_PROMPT = """Du bist ein Profil-Analysator. Prüfe ob die User-Nachricht neue dauerhafte persönliche Informationen enthält.

Antworte NUR mit reinem JSON – kein Markdown, keine Erklärung.

Format wenn neue Info vorhanden:
{"learned": true, "type": "person|project|job|location|preference|note", "data": {...}}

Typen und data-Format:
- person:     {"name": "Vollständiger Name", "context": "Kurze Beschreibung der Person"}
- project:    {"name": "Projektname", "description": "Kurze Beschreibung", "stack": [], "priority": "high|medium|low"}
- job:        {"employer": "Firmenname", "role": "Jobtitel", "context": "Optionale Zusatzinfo"}
- location:   {"location": "Stadt, Land"}
- preference: {"key": "communication", "value": "neue Präferenz als Text"}
- note:       {"text": "Freier Text – Fallback für alles andere"}

Format wenn KEINE neue Info:
{"learned": false}

Wichtige Unterscheidung:
- Erwähnung eines neuen ARBEITGEBERS / neuen JOBS → type=job (NICHT project)
- Eigenes neues Softwareprojekt das der User baut → type=project
- Firmen wo der User arbeitet → IMMER type=job

Speichern: neue Personen, neue Projekte, neuer Job/Arbeitgeber, Umzug, dauerhafte Präferenzänderungen.
NICHT speichern: Fragen, temporäre Zustände, technische Diskussionen, Smalltalk, Danke.

Beispiele:
"Ich habe einen neuen Kollegen, heißt Marco Müller" → {"learned": true, "type": "person", "data": {"name": "Marco Müller", "context": "Kollege"}}
"Ich bin nach München gezogen" → {"learned": true, "type": "location", "data": {"location": "München, Deutschland"}}
"Ich habe einen neuen Job als Teamlead bei Bonial" → {"learned": true, "type": "job", "data": {"employer": "Bonial", "role": "Teamlead"}}
"Ich arbeite jetzt bei Google als Senior Engineer" → {"learned": true, "type": "job", "data": {"employer": "Google", "role": "Senior Engineer"}}
"Was ist das Wetter?" → {"learned": false}
"Danke" → {"learned": false}"""

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
VALID   – YAML-Syntax korrekt, alle Original-Daten erhalten, nur sinnvolle Ergänzungen
INVALID – YAML kaputt, Daten fehlen, oder verdächtige Inhalte

Nur VALID oder INVALID."""


# ---------------------------------------------------------------------------
# Stufe 1: Detector
# ---------------------------------------------------------------------------

async def _detect_new_info(human_message: str) -> dict[str, Any]:
    """
    Haiku erkennt ob neue persönliche Info vorliegt.
    Fail-safe: Bei jedem Fehler → {"learned": False}
    """
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


# ---------------------------------------------------------------------------
# Stufe 2: Python-seitiger strukturierter Update (kein LLM schreibt YAML)
# ---------------------------------------------------------------------------

def _apply_update(profile: dict, info_type: str, data: dict) -> dict | None:
    """
    Wendet das erkannte Update auf das Profil-Dict an.
    Gibt updated dict zurück, oder None wenn Duplikat / nicht unterstützt.
    Modifiziert das Original nicht (deepcopy).
    """
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
                p["context"] = context  # Existierenden Eintrag updaten
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
                return None  # Duplikat – nichts tun
        new_project: dict[str, Any] = {"name": name}
        if desc := data.get("description", "").strip():
            new_project["description"] = desc
        if stack := data.get("stack", []):
            new_project["stack"] = stack if isinstance(stack, list) else []
        new_project["priority"] = data.get("priority", "medium")
        updated["projects"]["active"].append(new_project)
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

    return None  # Unbekannter Typ → Fallback zu Note


# ---------------------------------------------------------------------------
# Stufe 3: Reviewer
# ---------------------------------------------------------------------------

async def _review_yaml(original_yaml: str, new_yaml: str) -> bool:
    """
    Haiku reviewt das neue YAML.
    Gibt True zurück wenn VALID, False bei INVALID oder Fehler.
    Fail-safe: Bei Fehler → False (kein Schreiben).
    """
    try:
        from agent.llm import get_fast_llm
        from langchain_core.messages import HumanMessage

        llm = get_fast_llm()
        prompt = _REVIEWER_PROMPT.format(
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
            logger.warning(f"ProfileLearner Reviewer: INVALID – kein Schreiben")
        return is_valid

    except Exception as e:
        logger.error(f"ProfileLearner Reviewer Fehler (fail-safe: kein Schreiben): {e}")
        return False


# ---------------------------------------------------------------------------
# Haupt-Einstiegspunkt
# ---------------------------------------------------------------------------

async def apply_learning(human_message: str) -> None:
    """
    Vollständige 3-Stufen-Pipeline.
    Komplett fail-safe – kein Fehler wird nach oben weitergereicht.
    Wird als asyncio.create_task() aus chat_agent aufgerufen.
    """
    try:
        import yaml
        from agent.profile import load_profile, add_note_to_profile, write_profile

        # Stufe 1: Detector
        info = await _detect_new_info(human_message)
        if not info.get("learned"):
            return

        info_type = info.get("type", "note")
        data = info.get("data", {})
        logger.info(f"ProfileLearner: neue Info erkannt – type={info_type} data={str(data)[:100]}")

        # type=note direkt als Note speichern
        if info_type == "note":
            text = data.get("text", human_message[:200])
            add_note_to_profile(text)
            logger.info(f"ProfileLearner: Note gespeichert: {text[:80]}")
            return

        # Stufe 2: Python-seitiger strukturierter Update
        current_profile = load_profile()
        updated_profile = _apply_update(current_profile, info_type, data)

        if updated_profile is None:
            # Duplikat oder nicht unterstützt → Note als Fallback
            fallback = f"[Auto] {info_type}: {json.dumps(data, ensure_ascii=False)[:150]}"
            add_note_to_profile(fallback)
            logger.info(f"ProfileLearner: Duplikat/nicht unterstützt – Note: {fallback[:80]}")
            return

        # YAML serialisieren für Review
        original_yaml = yaml.dump(
            current_profile, allow_unicode=True, default_flow_style=False, sort_keys=False
        )
        new_yaml = yaml.dump(
            updated_profile, allow_unicode=True, default_flow_style=False, sort_keys=False
        )

        # Stufe 3: Haiku-Reviewer
        is_valid = await _review_yaml(original_yaml, new_yaml)
        if not is_valid:
            fallback = f"[Auto] {info_type}: {json.dumps(data, ensure_ascii=False)[:150]}"
            add_note_to_profile(fallback)
            logger.warning(f"ProfileLearner: Reviewer abgelehnt – Fallback zu Note")
            return

        # Python-seitige finale YAML-Validierung (TOCTOU-Schutz)
        try:
            yaml.safe_load(new_yaml)
        except yaml.YAMLError as e:
            logger.error(f"ProfileLearner: finale YAML-Validierung fehlgeschlagen: {e}")
            fallback = f"[Auto] {info_type}: {json.dumps(data, ensure_ascii=False)[:150]}"
            add_note_to_profile(fallback)
            return

        # Schreiben via profile.write_profile()
        write_profile(updated_profile)
        logger.info(f"ProfileLearner: Profil aktualisiert – type={info_type}")

    except Exception as e:
        logger.error(f"ProfileLearner: unerwarteter Fehler (ignoriert): {e}")