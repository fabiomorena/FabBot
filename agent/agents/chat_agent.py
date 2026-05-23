import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.messages import SystemMessage, AIMessage, HumanMessage

from agent.config import get_settings
from agent.state import AgentState
from agent.llm import get_llm

logger = logging.getLogger(__name__)

# Phase 89: Task-Registry verhindert stilles GC-Killing von Background-Tasks.
_background_tasks: set[asyncio.Task] = set()

# Phase 179: Fork-Agent Learning Loop – Batch-Analyse alle N Turns
_turn_counter: int = 0
_MEMORY_NUDGE_INTERVAL: int = get_settings().memory_nudge_interval


# Phase 218 (#225): SELF.md – Architektur-Selbstwissen für den Bot
_SELF_MD_PATH = Path(__file__).parent.parent.parent / "SELF.md"

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
- Keine Emojis. Niemals – ausser der User fordert sie explizit an.
- Keine Fuellsaetze, keine ueberschwenglichen Formulierungen, kein Kommentieren
  von Entscheidungen des Users (z.B. "Gut dass du..." / "Der Klassiker!" / "Kluge Wahl!").
- Praezise, direkte Antworten. Keine Bewertungen die nicht explizit angefragt wurden.
- Bei kurzen Bestaetigungen oder Reaktionen wie "Genau", "Ok", "Alles klar", "Danke",
  "Gut", "Super", "Verstanden", "Ja", "Stimmt", "Cool", "Perfekt":
  Antworte NUR mit einem kurzen Satz (max. 1-2 Saetze).
  NIEMALS den Inhalt der vorigen Antwort wiederholen oder zusammenfassen.
  Beispiele:
    "Genau" → "Ok."
    "Danke" → "Gern."
    "Ok" → "Noch etwas?"
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
_self_md_cache: str | None = None


def invalidate_chat_cache() -> None:
    """
    Invalidiert den Prompt-Cache sofort.
    Muss von memory_agent und profile.py aufgerufen werden,
    wenn Profil, claude.md oder Session-Summaries geschrieben werden.
    """
    global _prompt_cache, _self_md_cache
    _prompt_cache = None
    _self_md_cache = None
    logger.debug("chat_agent: Prompt-Cache invalidiert.")


def load_self_md() -> str:
    """Phase 218 (#225): Laedt SELF.md aus dem Projektroot.

    Read-only, kein Lock noetig. Gecacht nach erstem Aufruf da SELF.md sich
    nur bei Code-Updates aendert. Cache wird via invalidate_chat_cache()
    zurueckgesetzt (konsistent mit _prompt_cache).
    """
    global _self_md_cache
    if _self_md_cache is not None:
        return _self_md_cache
    if not _SELF_MD_PATH.exists():
        logger.debug("SELF.md nicht gefunden – kein Architektur-Selbstwissen geladen.")
        _self_md_cache = ""
        return _self_md_cache
    try:
        content = _SELF_MD_PATH.read_text(encoding="utf-8").strip()
        _self_md_cache = content
        logger.info("SELF.md geladen: %d Zeichen aus %s", len(content), _SELF_MD_PATH)
        return _self_md_cache
    except Exception as e:
        logger.warning("Fehler beim Laden von SELF.md (ignoriert): %s", e)
        _self_md_cache = ""
        return _self_md_cache


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

    # Block 0b: Architektur-Selbstwissen aus SELF.md (Phase 218, Issue #225)
    try:
        self_md = load_self_md()
        if self_md:
            parts.append("\n## Architektur-Selbstwissen\n" + self_md)
    except Exception as e:
        logger.debug("SELF.md konnte nicht geladen werden (ignoriert): %s", e)

    # Block 1: Bot-Instruktionen aus claude.md
    try:
        from agent.claude_md import load_claude_md

        claude_md = load_claude_md()
        if claude_md:
            parts.append("\n## Bot-Instruktionen\n" + claude_md)
    except Exception as e:
        logger.debug(f"claude.md konnte nicht geladen werden (ignoriert): {e}")

    # Block 2: Persoenliches Profil
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
    from agent.proactive.context import get_proactive_context

    parts = [f"\n[Aktuelles Datum/Uhrzeit: {get_current_datetime()}]"]

    proactive_ctx = get_proactive_context()
    if proactive_ctx:
        parts.append(proactive_ctx)

    if last_agent_result and last_agent_result.strip():
        agent_label = last_agent_name or "vorheriger Agent"
        result_text = last_agent_result.strip()
        max_chars = get_settings().agent_result_max_chars
        if len(result_text) > max_chars:
            result_text = result_text[:max_chars] + "\n…[gekürzt]"
        parts.append(
            f"\n## Kontext: Ergebnis des {agent_label}\n"
            f"{result_text}\n"
            f"WICHTIG: Wiederhole diese Information NICHT in deiner Antwort. "
            f"Nutze sie nur als Hintergrundwissen wenn der User explizit "
            f"eine inhaltliche Folgefrage dazu stellt. "
            f"Bei kurzen Reaktionen wie 'ok', 'stimmt', 'haha', 'danke' "
            f"antworte NUR mit 1-2 Saetzen ohne den Kontext zu wiederholen."
        )

    return "\n".join(parts)


_HITL_PREFIXES = ("__CONFIRM_", "__SCREENSHOT__", "__MEMORY__", "__VISION_RESULT__")


def _clean_messages_for_chat(messages: list) -> list:
    """Ersetzt HITL-Nachrichten durch lesbare Platzhalter fuer den chat_agent.

    Phase 212 (Issue #129): terminal + file produzieren keine __CONFIRM_*-Strings
    mehr (HITL über LangGraph interrupt()). Die elif-Branches dafür bleiben für
    backward-compat mit alten SQLite-Checkpoints im State.
    """
    cleaned = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""
        if isinstance(content, str) and content.startswith(_HITL_PREFIXES):
            if isinstance(msg, AIMessage):
                if content.startswith("__CONFIRM_TERMINAL__:"):
                    cmd = content[len("__CONFIRM_TERMINAL__:") :]
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
                    vision_text = content[len("__VISION_RESULT__:") :]
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
    """Liest CHAT_CONTEXT_WINDOW aus .env. Default: 20."""
    try:
        val = int(get_settings().chat_context_window)
        return max(10, min(200, val))
    except (ValueError, TypeError):
        return 20


async def _summarize_overflow(messages: list) -> AIMessage:
    """Phase 216 (#232): Komprimiert aus dem Context-Window gefallene Messages.

    Gibt AIMessage zurück (nicht SystemMessage) – Anthropic erlaubt keine
    nicht-konsekutiven System-Messages (würde ValueError auslösen).
    """
    from agent.llm import get_fast_llm

    lines = []
    for m in messages:
        if isinstance(m, HumanMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            lines.append(f"User: {content[:300]}")
        elif isinstance(m, AIMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            lines.append(f"Bot: {content[:300]}")

    if not lines:
        return AIMessage(content="[Früherer Kontext: keine Details]")

    conversation_text = "\n".join(lines)
    prompt = f"""Fasse diese frühere Konversation in maximal 3 Sätzen zusammen. Nur die wichtigsten Fakten, Entscheidungen und Themen. Keine Einleitung, kein Kommentar.

{conversation_text}"""

    try:
        llm = get_fast_llm()
        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=8.0,
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return AIMessage(content=f"[Früherer Kontext]\n{content.strip()}")
    except Exception as e:
        logger.debug(f"Overflow-Komprimierung fehlgeschlagen: {e}")
        return AIMessage(content=f"[Früherer Kontext]\n{conversation_text[:500]}")


async def _apply_context_window(messages: list, context_window: int) -> list:
    """Phase 216 (#231 + #232): Anchor-Messages + Inline-Komprimierung.

    - Erste Message bleibt immer erhalten (Gesprächsthema, #231)
    - Overflow zwischen anchor und recent wird zu Summary komprimiert (#232)
    """
    if len(messages) <= context_window:
        return messages
    anchor = messages[:1]
    recent_count = context_window - 1
    recent = messages[-recent_count:]
    overflow = messages[1 : len(messages) - recent_count]
    if not overflow:
        return anchor + recent
    summary_msg = await _summarize_overflow(overflow)
    return anchor + [summary_msg] + recent


_SHORT_CONFIRMATIONS = frozenset(
    {
        # Deutsch
        "genau",
        "ok",
        "okay",
        "alles klar",
        "danke",
        "danke schoen",
        "danke schon",
        "gut",
        "super",
        "verstanden",
        "ja",
        "stimmt",
        "cool",
        "perfekt",
        "top",
        "prima",
        "toll",
        "nice",
        "passt",
        "klar",
        "ack",
        "👍",
        "ok danke",
        # Englisch
        "thanks",
        "thank you",
        "got it",
        "sure",
        "great",
        "perfect",
        "noted",
        "makes sense",
        "understood",
        "sounds good",
        "good",
        "awesome",
        "nice",
        "yep",
        "yeah",
        "yes",
        "right",
        "correct",
    }
)


def _is_short_confirmation(text: str) -> bool:
    """Gibt True zurueck wenn die Nachricht eine kurze Bestaetigung ist."""
    return text.strip().lower().rstrip("!.") in _SHORT_CONFIRMATIONS


_sessions_cache: tuple[float, str] | None = None  # (max_mtime, result)

_SESSION_ALL_THRESHOLD = 20  # bis hier: alle Sessions laden
_SESSION_SHORT_THRESHOLD = 50  # ab hier: kürzeres Rolling Window
_SESSION_DAYS_DEFAULT = 30  # Rolling Window Standard (20–49 Sessions)
_SESSION_DAYS_SHORT = 14  # Rolling Window kurz (50+ Sessions)


def _parse_session_date(stem: str) -> date | None:
    try:
        return datetime.strptime(stem, "%Y-%m-%d").date()
    except ValueError:
        return None


def _load_all_sessions(max_days: int | None = None) -> str:
    """
    Laedt Session-Summaries direkt aus SESSIONS_DIR (sortiert nach Datum).
    Issue #33: Rolling Window bei wachsendem Vault.
      - bis 20 Sessions: alle laden
      - ab 20 Sessions: Rolling 30 Tage (max_days überschreibbar)
      - ab 50 Sessions: Rolling 14 Tage
    mtime-Cache: neu lesen nur wenn sich Dateien geaendert haben.
    Fail-safe: Bei Fehler leerer String.
    """
    global _sessions_cache
    try:
        from agent.retrieval import _SESSIONS_DIR

        sessions_dir = _SESSIONS_DIR
        if not sessions_dir.exists():
            return ""
        files = sorted(sessions_dir.glob("*.md"))
        if not files:
            return ""

        n = len(files)
        effective_days: int | None = None
        if n <= _SESSION_ALL_THRESHOLD:
            active_files = files
        else:
            effective_days = max_days or (
                _SESSION_DAYS_SHORT if n >= _SESSION_SHORT_THRESHOLD else _SESSION_DAYS_DEFAULT
            )
            cutoff = date.today() - timedelta(days=effective_days)
            active_files = [f for f in files if (d := _parse_session_date(f.stem)) is not None and d >= cutoff]
            if not active_files:
                active_files = [files[-1]]  # mindestens die letzte Session

        max_mtime = max(f.stat().st_mtime for f in files)
        if _sessions_cache is not None and _sessions_cache[0] == max_mtime:
            return _sessions_cache[1]

        max_chars = get_settings().session_summary_max_chars
        label = "alle" if effective_days is None else f"letzte {effective_days} Tage"
        parts = [f"\n## Deine Session-Erinnerungen ({label}):"]
        for f in active_files:
            try:
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    if len(content) > max_chars:
                        content = content[:max_chars] + "…"
                    parts.append(f"[{f.stem}]\n{content}")
            except Exception:
                continue
        result = "\n\n".join(parts) if len(parts) > 1 else ""
        _sessions_cache = (max_mtime, result)
        return result
    except Exception as e:
        logger.debug(f"Session-Load: fehlgeschlagen (ignoriert): {e}")
        return ""


def load_all_sessions(max_days: int | None = None) -> str:
    """Public-API für externe Module – delegiert an _load_all_sessions."""
    return _load_all_sessions(max_days)


async def _get_retrieval_context(query: str) -> str:
    """
    Phase 77: Holt semantisch relevante Chunks aus der Wissensbasis.
    Hotfix 18.04: Sessions werden immer vollstaendig vorangestellt (nicht via Ranking).
    Fail-safe: Bei Fehler, Timeout oder chromadb nicht installiert → leerer String.
    """
    if not query or len(query) < 5:
        return ""
    session_ctx = _load_all_sessions()
    try:
        from agent.retrieval import search

        results = await asyncio.wait_for(search(query, n_results=3), timeout=5.0)
        if not results:
            return session_ctx
        parts = ["\n## Relevantes aus deiner Wissensbasis:"]
        for r in results:
            label = r.get("label", "Unbekannt")
            doc = r.get("document", "")[:500]
            parts.append(f"[{label}]\n{doc}")
        knowledge_ctx = "\n\n".join(parts)
        return session_ctx + "\n" + knowledge_ctx if session_ctx else knowledge_ctx
    except asyncio.TimeoutError:
        logger.debug("Retrieval: Timeout nach 5s – übersprungen")
        return session_ctx
    except ImportError:
        return session_ctx
    except Exception as e:
        logger.debug(f"Retrieval: fehlgeschlagen (ignoriert): {e}")
        return session_ctx


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
    trimmed_messages = await _apply_context_window(clean_messages, context_window)

    human_text = _get_last_human_message(state["messages"])

    # Phase 77: Retrieval – nur für echte Fragen
    retrieval_ctx = ""
    if human_text and not _is_short_confirmation(human_text):
        retrieval_ctx = await _get_retrieval_context(human_text)

    # Phase 95: Statischer Prompt aus in-memory Cache
    static_prompt = _build_chat_prompt()

    # Phase 99: Dynamische Teile AUSSERHALB des Caches anhängen
    # - Aktuelles Datum/Uhrzeit (immer frisch – Issue #12)
    # - last_agent_result (State-Transfer – Issue #15)
    last_agent_result = state.get("last_agent_result")
    last_agent_name = state.get("last_agent_name")
    dynamic_suffix = _build_dynamic_prompt_suffix(last_agent_result, last_agent_name)
    dynamic_content = dynamic_suffix + (retrieval_ctx if retrieval_ctx else "")

    # Phase 164: Anthropic Prompt Caching – statischer Block wird server-seitig gecacht.
    # cache_control auf Block 1 → Anthropic cached alles bis hier (~90% günstiger).
    # Dynamische Inhalte (Uhrzeit, Retrieval, last_agent_result) bleiben unkecacht.
    content_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    if dynamic_content.strip():
        content_blocks.append({"type": "text", "text": dynamic_content})

    messages = [SystemMessage(content=content_blocks)] + trimmed_messages  # type: ignore[arg-type]

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
            last_ai_content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in last_ai_content)
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

    # Phase 179: Fork-Agent Learning Loop – Batch-Analyse alle N Turns
    global _turn_counter
    _turn_counter += 1
    if _turn_counter % _MEMORY_NUDGE_INTERVAL == 0:
        try:
            human_messages = [
                m.content for m in state["messages"] if isinstance(m, HumanMessage) and isinstance(m.content, str)
            ][-_MEMORY_NUDGE_INTERVAL:]
            if human_messages:
                from agent.profile_learner import apply_learning as _apply_learning

                batch_text = "\n".join(human_messages)
                task = asyncio.create_task(_apply_learning(batch_text))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
                logger.info(
                    f"Fork-Agent Batch-Learning gestartet (Turn {_turn_counter}, {len(human_messages)} Nachrichten)."
                )
        except Exception as e:
            logger.debug(f"Fork-Agent Batch-Learning konnte nicht gestartet werden (ignoriert): {e}")

    # Phase 99: last_agent_result nach TTL-Ablauf zurücksetzen
    ttl = state.get("last_agent_result_turn") or 1
    if ttl <= 1:
        cleanup: dict = {"last_agent_result": None, "last_agent_name": None, "last_agent_result_turn": None}
    else:
        cleanup = {"last_agent_result_turn": ttl - 1}
    return {"messages": [AIMessage(content=result)], **cleanup}
