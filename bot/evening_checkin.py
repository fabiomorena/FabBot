"""
bot/evening_checkin.py – Phase 195 (Issue #109)

Täglicher Abend-Check-in um 21:00 Uhr (Berlin-Zeit). Sendet Fabio eine persönliche
Frage basierend auf dem heutigen Gesprächsverlauf. Unabhängig vom Proaktiv-Cooldown.

State: ~/.fabbot/evening_checkin_state.json → { "last_sent_date": "YYYY-MM-DD" }

API:
  run_evening_checkin_scheduler(bot, chat_id) → None  (async)
"""

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta

from langchain_core.runnables import RunnableConfig
from pathlib import Path
from zoneinfo import ZoneInfo

from agent.config import get_settings

logger = logging.getLogger(__name__)

_TZ_BERLIN = ZoneInfo("Europe/Berlin")
_CHECKIN_STATE_FILE = Path.home() / ".fabbot" / "evening_checkin_state.json"
_LLM_TIMEOUT = 8.0
_FALLBACK_QUESTION = "Wie war dein Tag? Was hat dich heute beschäftigt?"

# Phase 204 (#214/#216): Wörter die im Entity Guard und der Whitelist-Extraktion
# übersprungen werden – häufige deutsche Substantive und Funktionswörter, die
# regelmäßig in Check-in-Fragen auftauchen, aber keine Eigennamen sind.
_COMMON_GERMAN_WORDS: frozenset[str] = frozenset(
    {
        "Der",
        "Die",
        "Das",
        "Ein",
        "Eine",
        "Einen",
        "Einem",
        "Einer",
        "Eines",
        "Ich",
        "Du",
        "Er",
        "Sie",
        "Es",
        "Wir",
        "Ihr",
        "Mein",
        "Meine",
        "Dein",
        "Deine",
        "Sein",
        "Ihre",
        "Was",
        "Wie",
        "Wo",
        "Wann",
        "Warum",
        "Welche",
        "Welcher",
        "Welches",
        "Wer",
        "Heute",
        "Gestern",
        "Morgen",
        "Tag",
        "Nacht",
        "Woche",
        "Monat",
        "Jahr",
        "Uhr",
        "Zeit",
        "Stunden",
        "Minuten",
        "Arbeit",
        "Musik",
        "Projekt",
        "Projekte",
        "Session",
        "Thema",
        "Themen",
        "Gespräch",
        "Frage",
        "Antwort",
        "Plan",
        "Idee",
        "Fortschritt",
        "Stand",
        "Nachrichten",
        "Gedanken",
        "Gedanke",
        "Gefühl",
        "Gefühle",
        "Dinge",
        "Ding",
        "Sache",
        "Sachen",
        "Bereich",
        "Teil",
        "Weg",
        "Studio",
        "Mix",
        "Track",
        "Tracks",
        "Beat",
        "Beats",
        "FabBot",
        "Fabio",
    }
)

# Gesprächs-Inaktivitätsfenster: Check-in wird verzögert, solange die letzte
# Nachricht weniger als N Minuten zurückliegt.
_ACTIVE_CONV_WINDOW_MINUTES = 20
_ACTIVE_CONV_RETRY_SECONDS = 15 * 60  # 15 Min warten vor erneutem Versuch
_ACTIVE_CONV_MAX_RETRIES = 3


def _load_state() -> dict:
    try:
        return json.loads(_CHECKIN_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(data: dict) -> None:
    _CHECKIN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CHECKIN_STATE_FILE.write_text(json.dumps(data))


def _is_conversation_active(chat_id: int) -> bool:
    from datetime import datetime

    try:
        from bot.bot import get_last_activity

        last = get_last_activity(chat_id)
        if last is None:
            return False
        return (datetime.now() - last).total_seconds() < _ACTIVE_CONV_WINDOW_MINUTES * 60
    except Exception:
        return False


def _already_sent_today() -> bool:
    last_date = _load_state().get("last_sent_date")
    if not last_date:
        return False
    try:
        return date.fromisoformat(last_date) == datetime.now(_TZ_BERLIN).date()
    except ValueError:
        return False


def _is_briefing_message(content: str) -> bool:
    return content.startswith("*Guten Morgen") or content.startswith("Guten Morgen")


def _filter_checkin_context(messages: list) -> list:
    """Nur Human-Messages + AI-Antworten; keine Briefings, nur letzte 30 Nachrichten."""
    result = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        if _is_briefing_message(str(content)):
            continue
        if getattr(msg, "type", "") not in ("human", "ai"):
            continue
        result.append(msg)
    return result[-30:]


def _build_context_word_set(text: str) -> frozenset[str]:
    """Alle Tokens aus text (lowercase) als Whitelist für den Entity Guard."""
    return frozenset(w.lower() for w in re.findall(r"\b[a-zA-ZäöüÄÖÜß]{2,}\b", text))


def _mid_sentence_caps(text: str) -> list[str]:
    """Großgeschriebene Wörter die NICHT Satzanfang sind (potenzielle Eigennamen)."""
    tokens = list(re.finditer(r"\b([A-ZÄÖÜ][a-zäöüß]{2,})\b", text))
    result = []
    for m in tokens:
        prefix = text[: m.start()].rstrip()
        if not prefix or prefix[-1] in ".!?":
            continue
        result.append(m.group(1))
    return result


def _has_hallucination(response: str, context_words: frozenset[str]) -> bool:
    """True wenn response kapitalisierte Wörter enthält die nicht im Kontext stehen.
    Phase 204 (Issue #214): Post-Generation Entity Guard.
    """
    for word in _mid_sentence_caps(response):
        if word in _COMMON_GERMAN_WORDS:
            continue
        if word.lower() not in context_words:
            logger.warning("Entity Guard: potenzielle Halluzination – '%s' nicht im Kontext", word)
            return True
    return False


def _extract_named_entities(text: str) -> list[str]:
    """Extrahiert potenzielle Eigennamen aus text für Whitelist-Injection im Prompt.
    Phase 204 (Issue #216).
    """
    seen: set[str] = set()
    result = []
    for w in re.findall(r"\b([A-ZÄÖÜ][a-zäöüß]{2,})\b", text):
        if w not in _COMMON_GERMAN_WORDS and w not in seen:
            seen.add(w)
            result.append(w)
    return result[:20]


async def _generate_checkin_question(chat_id: int) -> str:
    try:
        from agent.llm import get_grounding_llm
        from bot.session_summary import _filter_messages, _format_for_summary, _get_messages_from_state
        from langchain_core.messages import HumanMessage

        messages = await _get_messages_from_state(chat_id)
        filtered = _filter_messages(messages)
        filtered = _filter_checkin_context(filtered)
        chat_context = _format_for_summary(filtered) if filtered else ""

        if not chat_context.strip():
            return _FALLBACK_QUESTION

        context_words = _build_context_word_set(chat_context)
        named_entities = _extract_named_entities(chat_context)
        entity_list = ", ".join(named_entities) if named_entities else "keine"

        prompt = f"""Du bist FabBot. Schreibe eine kurze Abend-Frage für Fabio.

=== Heutiger Gesprächsverlauf ===
{chat_context}

STRENGE REGELN:
- 1–2 Sätze, direkt und warm, kein Begrüßungswort
- Deutsch, kein Emoji
- NUR Themen verwenden die explizit im Gesprächsverlauf oben stehen
- Erlaubte Entitäten aus dem Gespräch: {entity_list}
- NIEMALS Namen, Personen, Ereignisse oder Beziehungen erfinden die nicht wörtlich im Verlauf vorkommen
- Falls zu wenig Kontext: genau zurückgeben: "{_FALLBACK_QUESTION}" """

        llm = get_grounding_llm()
        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=_LLM_TIMEOUT,
        )
        content = response.content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        result = content.strip() or _FALLBACK_QUESTION

        if _has_hallucination(result, context_words):
            logger.warning("Entity Guard: Fallback wegen Halluzination in Check-in-Antwort")
            return _FALLBACK_QUESTION

        return result
    except Exception as e:
        logger.warning(f"Evening Check-in Generierung fehlgeschlagen: {e}")
        return _FALLBACK_QUESTION


async def run_evening_checkin_scheduler(bot, chat_id: int) -> None:
    """Läuft als Background-Task und sendet täglich den Abend-Check-in."""
    settings = get_settings()
    checkin_time = settings.evening_checkin_time
    # hour/minute werden einmalig gecacht; eine Änderung von EVENING_CHECKIN_TIME
    # zur Laufzeit (+ cache_clear()) greift erst nach Bot-Neustart.
    hour, minute = map(int, checkin_time.split(":"))
    logger.info(f"Evening Check-in Scheduler gestartet – täglich um {checkin_time} Uhr")

    while True:
        if not get_settings().evening_checkin_enabled:
            await asyncio.sleep(3600)
            continue

        now = datetime.now(_TZ_BERLIN)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(f"Nächster Abend-Check-in in {wait_seconds / 3600:.1f} Stunden")
        await asyncio.sleep(wait_seconds)

        if _already_sent_today():
            logger.info("Abend-Check-in: heute bereits gesendet – skip")
            await asyncio.sleep(60)
            continue

        for attempt in range(_ACTIVE_CONV_MAX_RETRIES + 1):
            if not _is_conversation_active(chat_id):
                break
            if attempt < _ACTIVE_CONV_MAX_RETRIES:
                logger.info(
                    f"Abend-Check-in: Gespräch aktiv – warte {_ACTIVE_CONV_RETRY_SECONDS // 60} Min "
                    f"(Versuch {attempt + 1}/{_ACTIVE_CONV_MAX_RETRIES})"
                )
                await asyncio.sleep(_ACTIVE_CONV_RETRY_SECONDS)
            else:
                logger.info("Abend-Check-in: Gespräch immer noch aktiv nach max. Retries – sende trotzdem")

        try:
            question = await _generate_checkin_question(chat_id)
            await bot.send_message(chat_id=chat_id, text=question)
            _save_state({"last_sent_date": datetime.now(_TZ_BERLIN).date().isoformat()})

            try:
                from agent.supervisor import get_graph
                from langchain_core.messages import AIMessage

                config: RunnableConfig = {"configurable": {"thread_id": str(chat_id)}}
                await get_graph().aupdate_state(
                    config,
                    {"messages": [AIMessage(content=question)]},
                    as_node="supervisor",
                )
            except Exception as state_err:
                logger.warning(f"Check-in state update fehlgeschlagen (nicht kritisch): {state_err}")

            logger.info("Abend-Check-in gesendet.")
        except Exception as e:
            logger.error(f"Evening Check-in Fehler: {e}")

        await asyncio.sleep(60)
