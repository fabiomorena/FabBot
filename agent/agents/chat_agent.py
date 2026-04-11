import asyncio
import logging
import os
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.utils import get_current_datetime
from agent.llm import get_llm

logger = logging.getLogger(__name__)

# Phase 89: Task-Registry verhindert stilles GC-Killing von Background-Tasks.
# Python docs Best Practice: create_task() ohne Referenz → GC kann Task abbrechen.
# https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_background_tasks: set[asyncio.Task] = set()

_CHAT_PROMPT_BASE = """[Aktuelles Datum/Uhrzeit: {datetime}]
Du bist ein hilfreicher persoenlicher Assistent mit Zugriff auf den bisherigen Gespraechsverlauf.

Beantworte die Frage des Users. Du hast Zugriff auf:
1. Den persoenlichen Kontext des Users (Profil) – das ist deine primaere Wissensquelle
2. Den bisherigen Gespraechsverlauf – fuer Folgefragen und Kontext
3. Relevante Inhalte aus der Wissensbasis (wenn vorhanden, am Ende des Prompts)
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
- Fragen ueber gespeicherte Notizen oder frueheres Wissen des Users

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
- Wenn du Inhalte aus der Wissensbasis verwendest: erwaehne kurz woher die Info stammt
  (z.B. "Laut deiner Notiz vom 12.03...")
"""


def _build_chat_prompt() -> str:
    """
    Baut den Chat-Prompt dynamisch pro Aufruf:
    Reihenfolge: Base → Bot-Instruktionen (claude.md) → Letzte Sessions → Profil.
    Jeder Block hat eigenes try/except – ein Fehler bricht nicht den ganzen Prompt.
    Imports innerhalb der Funktion damit unittest.mock.patch() greift.
    """
    dt = get_current_datetime()
    parts = [_CHAT_PROMPT_BASE.replace('{datetime}', dt)]

    # Block 1: Bot-Instruktionen aus claude.md
    try:
        from agent.claude_md import load_claude_md
        claude_md = load_claude_md()
        if claude_md:
            parts.append("\n## Bot-Instruktionen\n" + claude_md)
    except Exception as e:
        logger.debug(f"claude.md konnte nicht geladen werden (ignoriert): {e}")

    # Block 2: Letzte Session-Summaries
    try:
        from bot.session_summary import load_session_summaries
        sessions = load_session_summaries(n=5)
        if sessions:
            parts.append("\n## Letzte Sessions\n" + sessions)
    except Exception as e:
        logger.debug(f"Session-Summaries konnten nicht geladen werden (ignoriert): {e}")

    # Block 3: Persoenliches Profil
    try:
        from agent.profile import get_profile_context_full
        ctx = get_profile_context_full()
        if ctx:
            parts.append(
                "\nDer folgende Kontext ist deine primaere Wissensquelle ueber den User. "
                "Nutze ihn bevorzugt gegenueber dem Gespraechsverlauf:\n\n" + ctx
            )
    except Exception as e:
        logger.debug(f"Profil konnte nicht geladen werden (ignoriert): {e}")

    return "\n".join(parts)


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
    """Extrahiert den Text der letzten HumanMessage."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, list):
                return " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content).strip()
            return str(content).strip()
    return ""


def _get_context_window_size() -> int:
    """Liest CHAT_CONTEXT_WINDOW aus .env. Default: 40."""
    try:
        raw = os.getenv("CHAT_CONTEXT_WINDOW", "40")
        val = int(raw)
        return max(10, min(200, val))
    except (ValueError, TypeError):
        return 40


# Phase 89: Deutsch + Englisch – verhindert unnötiges Retrieval bei
# internationalen Bestätigungen. Name suggeriert Sprachunabhängigkeit,
# also sollte die Menge das auch einhalten.
_SHORT_CONFIRMATIONS = frozenset({
    # Deutsch
    "genau", "ok", "okay", "alles klar", "danke", "danke schoen", "danke schon",
    "gut", "super", "verstanden", "ja", "stimmt", "cool", "perfekt", "top",
    "prima", "toll", "nice", "passt", "klar", "ack", "👍", "ok danke",
    # Englisch
    "thanks", "thank you", "got it", "sure", "great", "perfect", "noted",
    "makes sense", "understood", "sounds good", "good", "awesome", "nice",
    "yep", "yeah", "yes", "right", "correct",
})


def _is_short_confirmation(text: str) -> bool:
    """Gibt True zurueck wenn die Nachricht eine kurze Bestaetigung ist."""
    return text.strip().lower().rstrip("!.") in _SHORT_CONFIRMATIONS


async def _get_retrieval_context(query: str) -> str:
    """
    Phase 77: Holt semantisch relevante Chunks aus der Wissensbasis.

    Fail-safe: Bei Fehler, Timeout oder chromadb nicht installiert → leerer String.
    Timeout: 5 Sekunden – blockiert nie die Antwort.
    Gibt nur Ergebnisse zurück die über dem Ähnlichkeits-Threshold liegen.
    """
    if not query or len(query) < 5:
        return ""
    try:
        from agent.retrieval import search
        results = await asyncio.wait_for(search(query, n_results=3), timeout=5.0)
        if not results:
            return ""
        parts = ["\n## Relevantes aus deiner Wissensbasis:"]
        for r in results:
            label = r.get("label", "Unbekannt")
            doc = r.get("document", "")[:500]  # max 500 Zeichen pro Chunk
            parts.append(f"[{label}]\n{doc}")
        return "\n\n".join(parts)
    except asyncio.TimeoutError:
        logger.debug("Retrieval: Timeout nach 5s – übersprungen")
        return ""
    except ImportError:
        return ""  # chromadb nicht installiert – kein Log nötig
    except Exception as e:
        logger.debug(f"Retrieval: fehlgeschlagen (ignoriert): {e}")
        return ""


async def chat_agent(state: AgentState) -> AgentState:
    """
    Antwortet direkt aus dem Gesprächsverlauf ohne externe Tools.

    Phase 77: Retrieval-Context wird für echte Fragen in den Prompt injiziert.
    Kurze Bestätigungen überspringen das Retrieval (Latenz-Optimierung).

    Phase 89: asyncio.create_task() nutzt jetzt _background_tasks Registry.
    Verhindert stilles GC-Killing von Auto-Learn-Tasks (Python docs Best Practice).
    """
    llm = get_llm()
    clean_messages = _clean_messages_for_chat(state["messages"])
    context_window = _get_context_window_size()
    trimmed_messages = clean_messages[-context_window:]

    # Letzte Human-Message für Retrieval + Auto-Learn extrahieren
    human_text = _get_last_human_message(state["messages"])

    # Phase 77: Retrieval – nur für echte Fragen, nicht für kurze Bestätigungen.
    retrieval_ctx = ""
    if human_text and not _is_short_confirmation(human_text):
        retrieval_ctx = await _get_retrieval_context(human_text)

    # Prompt dynamisch pro Aufruf – holt aktuelles claude.md, Sessions und Profil
    prompt = _build_chat_prompt()
    if retrieval_ctx:
        prompt = prompt + retrieval_ctx

    messages = [SystemMessage(content=prompt)] + trimmed_messages

    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

    result = content.strip()

    # Dedup-Sicherheitsnetz: verhindert exakte Wiederholung der letzten AI-Antwort.
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

    # Phase 89: Auto-Learn mit Task-Registry – verhindert GC-Killing.
    # Vorher: asyncio.create_task() ohne Referenz → GC konnte Task still beenden.
    # Jetzt:  Task wird in _background_tasks gehalten bis er fertig ist.
    try:
        if human_text:
            from agent.profile_learner import apply_learning
            task = asyncio.create_task(apply_learning(human_text))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.debug(f"Auto-Learn Task konnte nicht gestartet werden (ignoriert): {e}")

    return {"messages": [AIMessage(content=result)]}