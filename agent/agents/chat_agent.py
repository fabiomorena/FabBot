import asyncio
import logging
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.llm import get_llm

logger = logging.getLogger(__name__)

_CHAT_PROMPT_BASE = """Du bist ein hilfreicher persoenlicher Assistent mit Zugriff auf den bisherigen Gespraechsverlauf.

Beantworte die Frage des Users. Du hast Zugriff auf:
1. Den persoenlichen Kontext des Users (Profil) – das ist deine primaere Wissensquelle
2. Den bisherigen Gespraechsverlauf – fuer Folgefragen und Kontext
Kein Tool-Aufruf, kein Suchen – nur direkte, praezise Antworten.

Typische Faelle fuer dich:
- Bildanalyse-Ergebnisse in natuerlicher Sprache zusammenfassen und kommentieren
- "Was habe ich dich gerade gefragt?"
- "Fass das zusammen"
- "Erklaer das nochmal anders"
- "Was meintest du mit X?"
- "Danke" / allgemeine Hoeflichkeiten
- Kurze Folgefragen zum vorherigen Thema
- Persoenliche Fragen ueber den User (Wohnort, Projekte, Geraete, Praeferenzen)
"""


def _build_chat_prompt() -> str:
    """
    Baut den Chat-Prompt mit vollständigem persönlichem Kontext.
    Fail-safe: Bei jedem Fehler wird der Basis-Prompt zurückgegeben.
    """
    try:
        from agent.profile import get_profile_context_full
        ctx = get_profile_context_full()
        if ctx:
            return (
                _CHAT_PROMPT_BASE
                + "\nDer folgende Kontext ist deine primaere Wissensquelle ueber den User. "
                + "Nutze ihn bevorzugt gegenueber dem Gespraechsverlauf:\n\n"
                + ctx
            )
    except Exception:
        pass
    return _CHAT_PROMPT_BASE


# Einmalig beim Modulimport gebaut – kein Overhead pro Aufruf
PROMPT = _build_chat_prompt()

_HITL_PREFIXES = ("__CONFIRM_", "__SCREENSHOT__", "__MEMORY__", "__VISION_RESULT__")


def _clean_messages_for_chat(messages: list) -> list:
    """Ersetzt HITL-Nachrichten durch lesbare Platzhalter fuer den chat_agent.
    Der chat_agent soll wissen dass eine Aktion stattfand, aber nicht den rohen Prefix sehen.
    """
    cleaned = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""
        if isinstance(content, str) and content.startswith(_HITL_PREFIXES):
            if isinstance(msg, AIMessage):
                if content.startswith("__CONFIRM_TERMINAL__:"):
                    cmd = content[len("__CONFIRM_TERMINAL__:"):]
                    cleaned.append(AIMessage(content=f"[Terminal-Befehl ausgefuehrt: {cmd}]"))
                elif content.startswith("__CONFIRM_CREATE_EVENT__:"):
                    cleaned.append(AIMessage(content="[Kalendereintrag erstellt]"))
                elif content.startswith("__CONFIRM_FILE_WRITE__:"):
                    cleaned.append(AIMessage(content="[Datei geschrieben]"))
                elif content.startswith("__CONFIRM_COMPUTER__:"):
                    cleaned.append(AIMessage(content="[Desktop-Aktion ausgefuehrt]"))
                elif content.startswith("__SCREENSHOT__:"):
                    cleaned.append(AIMessage(content="[Screenshot erstellt]"))
                else:
                    cleaned.append(AIMessage(content="[Aktion ausgefuehrt]"))
        else:
            cleaned.append(msg)
    return cleaned


def _get_last_human_message(messages: list) -> str:
    """Extrahiert den Text der letzten HumanMessage für den Auto-Learn-Hook."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content).strip()
            return str(content).strip()
    return ""


async def chat_agent(state: AgentState) -> AgentState:
    """Antwortet direkt aus dem Gesprächsverlauf ohne externe Tools.
    HITL-Nachrichten werden durch lesbare Platzhalter ersetzt.
    Nach der Antwort: Auto-Learn-Hook als non-blocking Background-Task.
    """
    llm = get_llm()
    clean_messages = _clean_messages_for_chat(state["messages"])
    messages = [SystemMessage(content=PROMPT)] + clean_messages
    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

    # Auto-Learn: letzte HumanMessage als Background-Task analysieren
    # Non-blocking – Antwort an User wird nicht verzögert
    # Fail-safe – Fehler im Learner beeinflussen den Bot nicht
    try:
        human_text = _get_last_human_message(state["messages"])
        if human_text:
            from agent.profile_learner import apply_learning
            asyncio.create_task(apply_learning(human_text))
    except Exception as e:
        logger.debug(f"Auto-Learn Task konnte nicht gestartet werden (ignoriert): {e}")

    return {"messages": [AIMessage(content=content.strip())]}