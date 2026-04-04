import asyncio
import logging
import os
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

WICHTIGE VERHALTENSREGELN:
- Bei kurzen Bestaetigungen oder Reaktionen wie "Genau", "Ok", "Alles klar", "Danke",
  "Gut", "Super", "Verstanden", "Ja", "Stimmt", "Cool", "Perfekt":
  Antworte NUR mit einem kurzen Satz (max. 1-2 Saetze).
  NIEMALS den Inhalt der vorigen Antwort wiederholen oder zusammenfassen.
  Beispiele:
    "Genau" → "Freut mich, dass es klar ist!"
    "Danke" → "Gern!"
    "Ok" → "Super. Noch etwas?"
- Wiederhole NIEMALS den Inhalt deiner unmittelbar vorigen Antwort, egal wie die
  Nachricht des Users formuliert ist.
- Wenn unklar ob der User eine Wiederholung moechte: lieber kurz nachfragen als
  denselben Text nochmal ausgeben.
"""


def _build_chat_prompt() -> str:
    """
    Baut den Chat-Prompt dynamisch aus drei Quellen – alle ueberleben den Context Trim:
    1. _CHAT_PROMPT_BASE        – Basis-Instruktionen
    2. claude.md                – persistente Bot-Instruktionen (load_claude_md() gecacht)
    3. personal_profile.yaml    – persoenlicher Kontext des Users

    Dynamisch statt Singleton: neue Bot-Instruktionen via append_to_claude_md()
    wirken beim naechsten Call sofort – kein Bot-Neustart noetig.
    Kein wesentlicher Overhead da load_claude_md() und get_profile_context_full()
    gecacht sind (nur String-Konkatenation pro Call).

    Fail-safe: Bei jedem Fehler wird der Basis-Prompt zurueckgegeben.
    """
    try:
        from agent.claude_md import load_claude_md
        from agent.profile import get_profile_context_full

        parts = [_CHAT_PROMPT_BASE]

        # claude.md – persistente Bot-Instruktionen (gecacht, reload nach append)
        claude_ctx = load_claude_md()
        if claude_ctx:
            parts.append(
                "\n=== Persistente Bot-Instruktionen (claude.md) ===\n"
                + claude_ctx
                + "\n=== Ende Bot-Instruktionen ==="
            )

        # personal_profile.yaml – Kontext ueber den User (gecacht)
        profile_ctx = get_profile_context_full()
        if profile_ctx:
            parts.append(
                "\nDer folgende Kontext ist deine primaere Wissensquelle ueber den User. "
                "Nutze ihn bevorzugt gegenueber dem Gespraechsverlauf:\n\n"
                + profile_ctx
            )

        return "\n\n".join(parts)

    except Exception:
        pass
    return _CHAT_PROMPT_BASE


_HITL_PREFIXES = ("__CONFIRM_", "__SCREENSHOT__", "__MEMORY__", "__VISION_RESULT__")


def _clean_messages_for_chat(messages: list) -> list:
    """Ersetzt HITL-Nachrichten durch lesbare Platzhalter fuer den chat_agent."""
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
                elif content.startswith("__VISION_RESULT__:"):
                    vision_text = content[len("__VISION_RESULT__:"):]
                    cleaned.append(AIMessage(content=f"[Bildanalyse: {vision_text[:300]}]"))
                else:
                    cleaned.append(AIMessage(content="[Aktion ausgefuehrt]"))
        else:
            cleaned.append(msg)
    return cleaned


def _get_last_human_message(messages: list) -> str:
    """Extrahiert den Text der letzten HumanMessage fuer den Auto-Learn-Hook."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content).strip()
            return str(content).strip()
    return ""


def _get_context_window_size() -> int:
    """Liest CHAT_CONTEXT_WINDOW aus .env. Default: 40. Bereich: 10–200."""
    try:
        raw = os.getenv("CHAT_CONTEXT_WINDOW", "40")
        val = int(raw)
        return max(10, min(200, val))
    except (ValueError, TypeError):
        return 40


_SHORT_CONFIRMATIONS = frozenset({
    "genau", "ok", "okay", "alles klar", "danke", "danke schoen", "danke schon",
    "gut", "super", "verstanden", "ja", "stimmt", "cool", "perfekt", "top",
    "prima", "toll", "nice", "passt", "klar", "ack", "👍", "ok danke",
})


def _is_short_confirmation(text: str) -> bool:
    return text.strip().lower().rstrip("!.") in _SHORT_CONFIRMATIONS


async def chat_agent(state: AgentState) -> AgentState:
    """Antwortet direkt aus dem Gesprächsverlauf ohne externe Tools.

    Phase 63: _build_chat_prompt() wird dynamisch aufgerufen statt als Singleton.
    load_claude_md() ist gecacht → minimaler Overhead.
    Neue Bot-Instruktionen via append_to_claude_md() wirken sofort.
    """
    llm = get_llm()
    clean_messages = _clean_messages_for_chat(state["messages"])
    context_window = _get_context_window_size()
    trimmed_messages = clean_messages[-context_window:]

    # Dynamisch – neue claude.md Eintraege wirken sofort ohne Neustart
    prompt = _build_chat_prompt()
    messages = [SystemMessage(content=prompt)] + trimmed_messages

    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

    result = content.strip()

    # Dedup-Sicherheitsnetz
    prev_ai_messages = [m for m in trimmed_messages if isinstance(m, AIMessage)]
    if prev_ai_messages:
        last_ai_content = prev_ai_messages[-1].content
        if isinstance(last_ai_content, list):
            last_ai_content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in last_ai_content
            )
        if result == last_ai_content.strip():
            logger.warning("chat_agent: Dedup-Sicherheitsnetz – Wiederholung verhindert.")
            result = "Noch etwas, womit ich helfen kann?"

    # Auto-Learn Hook
    try:
        human_text = _get_last_human_message(state["messages"])
        if human_text:
            from agent.profile_learner import apply_learning
            asyncio.create_task(apply_learning(human_text))
    except Exception as e:
        logger.debug(f"Auto-Learn Task konnte nicht gestartet werden (ignoriert): {e}")

    return {"messages": [AIMessage(content=result)]}
