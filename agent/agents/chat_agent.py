import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.llm import get_llm

logger = logging.getLogger(__name__)

# Phase 89: Task-Registry verhindert stilles GC-Killing von Background-Tasks.
_background_tasks: set[asyncio.Task] = set()

_CHAT_PROMPT_BASE = """Du bist ein hilfreicher persoenlicher Assistent mit Zugriff auf den bisherigen Gespraechsverlauf.

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

# ---------------------------------------------------------------------------
# Phase 95: Prompt-Cache – verhindert 3 Disk-Reads pro Message (Issue #2).
# TTL: 60 Sekunden. Explizite Invalidierung via invalidate_chat_cache().
#
# Phase 99 (Issue #12 + #15): get_current_datetime() und last_agent_result
# werden AUSSERHALB des Caches injiziert – sie sind immer frisch.
# Der Cache enthält nur statische Teile: Base-Prompt, claude.md, Sessions, Profil.
# ---------------------------------------------------------------------------

_PROMPT_CACHE_TTL = 60.0  # Sekunden


@dataclass
class _CachedPrompt:
    value: str
    timestamp: float = field(default_factory=time.monotonic)

    def is_valid(self) -> bool:
        return (time.monotonic() - self.timestamp) < _PROMPT_CACHE_TTL


_prompt_cache: _CachedPrompt | None = None


def invalidate_chat_cache() -> None:
    """
    Invalidiert den Prompt-Cache sofort.
    Muss von memory_agent und profile.py aufgerufen werden,
    wenn Profil, claude.md oder Session-Summaries geschrieben werden.
    """
    global _prompt_cache
    _prompt_cache = None
    logger.debug("chat_agent: Prompt-Cache invalidiert.")


def _build_chat_prompt() -> str:
    """
    Baut den statischen Teil des Chat-Prompts – mit Cache (TTL 60s).

    Phase 95: Ergebnis wird gecacht um 3 Disk-Reads pro Message zu vermeiden.
    Phase 99: get_current_datetime() und last_agent_result werden NICHT gecacht –
              sie werden in chat_agent() dynamisch außerhalb dieses Caches angehängt.
              Damit ist Issue #12 (Uhrzeit im Cache) und Issue #15 (State-Transfer)
              gleichzeitig gelöst.
    """
    global _prompt_cache

    if _prompt_cache is not None and _prompt_cache.is_valid():
        logger.debug("chat_agent: Prompt aus Cache (kein Disk-Read).")
        return _prompt_cache.value

    logger.debug("chat_agent: Prompt-Cache miss – baue neu.")
    parts = [_CHAT_PROMPT_BASE]

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

    result = "\n".join(parts)
    _prompt_cache = _CachedPrompt(value=result)
    return result


def _build_dynamic_prompt_suffix(
    last_agent_result: str | None,
    last_agent_name: str | None,
) -> str:
    """
    Phase 99: Baut den dynamischen Teil des System-Prompts.

    Enthält:
    - Aktuelles Datum/Uhrzeit (immer frisch – nicht gecacht, löst Issue #12)
    - last_agent_result (Ergebnis des vorherigen Agents – löst Issue #15)

    Wird in chat_agent() NACH dem Cache-Lookup angehängt.
    """
    from agent.utils import get_current_datetime
    parts = [f"\n[Aktuelles Datum/Uhrzeit: {get_current_datetime()}]"]

    if last_agent_result and last_agent_result.strip():
        agent_label = last_agent_name or "vorheriger Agent"
        parts.append(
            f"\n## Kontext: Ergebnis des {agent_label}\n"
            f"{last_agent_result.strip()}\n"
            f"WICHTIG: Wiederhole diese Information NICHT in deiner Antwort. "
            f"Nutze sie nur als Hintergrundwissen wenn der User explizit "
            f"eine inhaltliche Folgefrage dazu stellt. "
            f"Bei kurzen Reaktionen wie 'ok', 'stimmt', 'haha', 'danke' "
            f"antworte NUR mit 1-2 Saetzen ohne den Kontext zu wiederholen."
        )

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
            doc = r.get("document", "")[:500]
            parts.append(f"[{label}]\n{doc}")
        return "\n\n".join(parts)
    except asyncio.TimeoutError:
        logger.debug("Retrieval: Timeout nach 5s – übersprungen")
        return ""
    except ImportError:
        return ""
    except Exception as e:
        logger.debug(f"Retrieval: fehlgeschlagen (ignoriert): {e}")
        return ""


async def chat_agent(state: AgentState) -> AgentState:
    """
    Antwortet direkt aus dem Gesprächsverlauf ohne externe Tools.

    Phase 77: Retrieval-Context wird für echte Fragen in den Prompt injiziert.
    Phase 89: asyncio.create_task() nutzt _background_tasks Registry.
    Phase 95: _build_chat_prompt() nutzt Cache (TTL 60s) für statische Teile.

    Phase 99 (Issue #12 + #15):
    - get_current_datetime() wird NICHT mehr gecacht – immer frisch via
      _build_dynamic_prompt_suffix(). Löst Issue #12.
    - last_agent_result aus AgentState wird in den Prompt injiziert wenn vorhanden.
      chat_agent kennt damit das Ergebnis von web_agent, file_agent etc.
      Löst Issue #15.
    """
    llm = get_llm()
    clean_messages = _clean_messages_for_chat(state["messages"])
    context_window = _get_context_window_size()
    trimmed_messages = clean_messages[-context_window:]

    human_text = _get_last_human_message(state["messages"])

    # Phase 77: Retrieval – nur für echte Fragen
    retrieval_ctx = ""
    if human_text and not _is_short_confirmation(human_text):
        retrieval_ctx = await _get_retrieval_context(human_text)

    # Phase 95: Statischer Prompt aus Cache
    prompt = _build_chat_prompt()

    # Phase 99: Dynamische Teile AUSSERHALB des Caches anhängen
    # - Aktuelles Datum/Uhrzeit (immer frisch – Issue #12)
    # - last_agent_result (State-Transfer – Issue #15)
    last_agent_result = state.get("last_agent_result")
    last_agent_name = state.get("last_agent_name")
    dynamic_suffix = _build_dynamic_prompt_suffix(last_agent_result, last_agent_name)
    prompt = prompt + dynamic_suffix

    if retrieval_ctx:
        prompt = prompt + retrieval_ctx

    messages = [SystemMessage(content=prompt)] + trimmed_messages

    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

    result = content.strip()
    if not result:
        logger.warning("chat_agent: leere Antwort vom LLM erhalten.")
        result = "Keine Antwort erhalten."

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

    # Phase 89: Auto-Learn mit Task-Registry
    try:
        if human_text:
            from agent.profile_learner import apply_learning
            task = asyncio.create_task(apply_learning(human_text))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.debug(f"Auto-Learn Task konnte nicht gestartet werden (ignoriert): {e}")

    # Phase 99: last_agent_result nach Verarbeitung zurücksetzen
    # Verhindert dass veraltete Ergebnisse in Folge-Requests auftauchen
    return {
        "messages": [AIMessage(content=result)],
        "last_agent_result": None,
        "last_agent_name": None,
    }
