import logging
import re
from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
import asyncio

from agent.state import AgentState, AgentName
from agent.llm import get_fast_llm
from agent.utils import extract_llm_text
from agent.agents.computer import computer_agent
from agent.agents.terminal import terminal_agent
from agent.agents.file import file_agent
from agent.agents.web import web_agent
from agent.agents.calendar import calendar_agent
from agent.agents.chat_agent import chat_agent
from agent.agents.reminder_agent import reminder_agent
from agent.agents.memory_agent import memory_agent
from agent.agents.vision_agent import vision_agent
from agent.agents.whatsapp_agent import whatsapp_agent

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".fabbot" / "memory.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

agent_graph: CompiledStateGraph | None = None
_db_conn = None
_init_lock = asyncio.Lock()

SUPERVISOR_PROMPT = """Du bist ein Routing-Agent. Deine einzige Aufgabe ist es, eine der folgenden Antworten zurueckzugeben.

Verfuegbare Agenten:

- chat_agent: STANDARD-FALLBACK fuer alles was das LLM aus sich selbst beantworten kann.
  Nutze chat_agent bei:
  - Meinungsfragen ("was haelst du von X", "wie findest du Y", "magst du Z")
  - Erklaerungen und Definitionen von stabilen Konzepten ("was ist Philosophie", "erklaer mir Quantenmechanik auf Grundschulniveau")
  - Smalltalk, Reaktionen, Hoeflichkeiten ("danke", "ok", "cool", "super", "alles klar")
  - Folgefragen zum bisherigen Gespraech ("fass das zusammen", "erklaer das nochmal")
  - Persoenliche Fragen ueber den User aus dem Profil (Projekte, Standort, Geraete, Praeferenzen)
  - Fragen ueber gespeicherte Notizen, Sessions oder Wissen
  - Statische Fakten die sich nie aendern ("wer hat die Relativitaetstheorie entwickelt")
  - Reine Datum/Uhrzeit-Fragen ("wieviel uhr", "welches datum")
  - ALLE Folgefragen zu einem Foto oder Bild
  - Im Zweifel: chat_agent ist der sichere Fallback

- web_agent: NUR wenn externe oder aktuelle Daten benoetigt werden – Daten die das LLM
  nicht zuverlaessig aus sich selbst beantworten kann.
  Nutze web_agent EXPLIZIT bei:
  - Aktuellen Nachrichten, Ereignissen, Politik, Sport, Wirtschaft
  - Wetter-Fragen ("wetter heute", "wie warm ist es", "regnet es", "wetter berlin", "forecast")
  - Preise, Kurse, Boerse
  - Aktuellem Status von Personen (lebt X noch? aktuelles Amt, aktuelle Rolle, CEO, Kanzler, Minister)
  - Schnell aendernden Fakten (aktuelle Rekorde, aktuelle Zahlen, aktuelle Ranglisten)
  - Erklaerungen zu aktuellen oder sich schnell entwickelnden Themen (KI-Fortschritt, neue Technologien)
  - NICHT fuer Meinungsfragen, Erklaerungen stabiler Konzepte, Smalltalk oder Konversation
  - NICHT fuer Fragen zu einem Foto oder Bild

- memory_agent: Persoenliche Informationen oder Bot-Instruktionen speichern, aktualisieren oder loeschen.
  NUR bei expliziten Speicher-Befehlen:
  JA: 'merke dir dass...', 'speichere...', 'fuge ... hinzu'
  JA: 'vergiss X', 'vergiss den X', 'vergiss den Eintrag X', 'loesche X aus dem Profil'
  JA: 'merke dir grundsaetzlich...', 'von jetzt an sollst du...', 'du sollst immer...'
  JA: 'merke dir das', 'merk dir das', 'das merken', 'merk das' (Referenz auf vorherige Aussage)
  JA: 'vergiss die instruktion', 'loesch die instruktion', 'alle instruktionen loeschen'
  NEIN: alle normalen Aussagen, Antworten auf Fragen, Erzaehlungen ohne explizites Speicher-Wort
  NEIN: 'ich mag X', 'ich war bei X', kurze Antworten ohne Speicher-Absicht
  NEIN: Fragen ueber gespeicherte Notizen, Sessions oder Wissen

- calendar_agent: Kalendertermine lesen oder erstellen
- reminder_agent: Erinnerungen setzen, auflisten oder loeschen (z.B. 'Erinnere mich um 18 Uhr')
- file_agent: Dateien und Ordner lesen, auflisten oder schreiben
- terminal_agent: Shell-Befehle, Speicher, CPU, Prozesse – NUR technische Systemabfragen, NICHT fuer Datum/Uhrzeit
- computer_agent: Desktop-Steuerung, Screenshots, Apps oeffnen
- vision_agent: Bildanalyse von Fotos. Wird automatisch geroutet wenn Nachricht mit [FOTO] beginnt.
- whatsapp_agent: WhatsApp-Nachricht senden. NUR bei expliziten Sende-Befehlen an erlaubte Kontakte.

Regeln:
- Wenn die letzte Nachricht bereits eine Antwort eines Agenten enthaelt: FINISH
- Smalltalk, Reaktionen, Hoeflichkeiten: IMMER chat_agent, NIE FINISH
- Wetter-Fragen: IMMER web_agent
- Meinungsfragen ("was haelst du", "wie findest du", "deine meinung"): IMMER chat_agent
- Im Zweifel zwischen web_agent und chat_agent: chat_agent waehlen
- Im Zweifel zwischen memory_agent und chat_agent: chat_agent waehlen
- Fragen mit 'wo', 'wer', 'was' die sich auf ein Foto beziehen: IMMER chat_agent
- Fragen ueber eigene Notizen/Sessions/Wissen: IMMER chat_agent

WICHTIG: Antworte AUSSCHLIESSLICH mit einem dieser Woerter (nichts anderes):
computer_agent
terminal_agent
file_agent
web_agent
calendar_agent
reminder_agent
memory_agent
chat_agent
FINISH
"""

# ---------------------------------------------------------------------------
# Deterministisches Pre-Routing – Tabelle (Issue #55)
# Reihenfolge ist semantisch: spezifischer vor generischem.
# Neue Rules: nur eine Zeile in _PRE_ROUTING_RULES ergänzen.
# ---------------------------------------------------------------------------

_PRE_ROUTING_RULES: list[tuple[tuple[str, ...], str, str]] = [
    # (prefixes, target_agent, log_label)
    (
        (
            "was hälst du", "was haelst du", "was denkst du",
            "was findest du", "wie findest du", "magst du",
            "gefällt dir", "gefaellt dir", "deine meinung",
            "dein urteil", "was ist deine meinung",
        ),
        "chat_agent",
        "opinion-trigger",
    ),
    (
        # Spezifischer als memory-delete – muss davor stehen
        (
            "vergiss die instruktion", "vergiss alle instruktionen",
            "lösch die instruktion", "loesch die instruktion",
            "lösche die instruktion", "loesche die instruktion",
            "entferne die instruktion", "alle instruktionen löschen",
            "alle instruktionen loeschen", "instruktionen zurücksetzen",
            "instruktionen zuruecksetzen", "instruktion löschen",
            "instruktion loeschen", "setze instruktionen zurück",
            "setze instruktionen zurueck",
        ),
        "memory_agent",
        "bot-instruction-delete-trigger",
    ),
    (
        (
            "vergiss ", "vergiss,", "lösche aus dem profil",
            "loesche aus dem profil", "entferne aus dem profil",
            "aus dem profil löschen", "aus dem profil loeschen",
            "aus meinem profil löschen", "aus meinem profil loeschen",
            "profil eintrag löschen", "profil eintrag loeschen",
        ),
        "memory_agent",
        "delete-trigger",
    ),
    (
        (
            "merke dir dass", "merke dir:", "speichere dass",
            "füge hinzu:", "fuege hinzu:", "merke dir grundsätzlich",
            "merke dir grundsaetzlich", "von jetzt an sollst du",
        ),
        "memory_agent",
        "save-trigger",
    ),
]


def _match_pre_routing(text: str) -> tuple[str, str] | None:
    """Gibt (agent_name, log_label) zurück wenn ein Prefix-Rule greift, sonst None."""
    lower = text.strip().lower()
    for prefixes, agent, label in _PRE_ROUTING_RULES:
        if any(lower.startswith(p) for p in prefixes):
            return agent, label
    return None


_MAX_ROUTING_LEN = 500

_INJECTION_RE = re.compile(
    r'(?i)'
    r'(ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?))'
    r'|(you\s+are\s+now\s+\w+)'
    r'|(system\s*:\s)'
    r'|(<\s*/?\s*system\s*>)'
    r'|(\[system\])'
)


def _sanitize_routing_content(content):
    if isinstance(content, str):
        return _INJECTION_RE.sub("[X]", content[:_MAX_ROUTING_LEN])
    if isinstance(content, list):
        return [
            {**b, "text": _INJECTION_RE.sub("[X]", b["text"][:_MAX_ROUTING_LEN])}
            if isinstance(b, dict) and "text" in b else b
            for b in content
        ]
    return content


def _filter_hitl_messages(messages: list) -> list:
    """
    Phase 91: Proto.MEMORY_VISION_MARKER statt hardcoded "Bildbeschreibung".
    """
    from agent.protocol import Proto
    filtered = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""
        if (isinstance(content, str) and content.startswith(("__CONFIRM_", "__SCREENSHOT__"))) or \
                (isinstance(content, str) and content.startswith("__MEMORY__") and Proto.MEMORY_VISION_MARKER not in content):
            if isinstance(msg, AIMessage):
                filtered.append(AIMessage(content="[Aktion wurde ausgefuehrt]"))
            continue
        filtered.append(msg)
    return filtered


async def supervisor_node(state: AgentState) -> AgentState:
    llm = get_fast_llm()
    messages = state["messages"]

    # Phase 109: Early-Return NUR wenn letzte Message eine AIMessage ist
    # UND keine neue HumanMessage danach kommt.
    last_msg = messages[-1] if messages else None
    if last_msg and isinstance(last_msg, AIMessage):
        content = last_msg.content if isinstance(last_msg.content, str) else ""
        if not content.startswith("__MEMORY__:"):
            logger.debug("supervisor: letzte Message ist AIMessage → FINISH")
            return {"next_agent": "FINISH"}

    clean_messages = _filter_hitl_messages(messages)
    last_human = [m for m in clean_messages if isinstance(m, HumanMessage)]
    routing_messages = [last_human[-1]] if last_human else clean_messages[-1:]

    if last_human:
        last_text = last_human[-1].content
        if isinstance(last_text, list):
            last_text = str(last_text)[:100]
        logger.info(f"supervisor routing: '{last_text[:100]}' → ?")

    # ---------------------------------------------------------------------------
    # Deterministisches Pre-Routing vor dem LLM-Call (Issue #55)
    # ---------------------------------------------------------------------------
    if routing_messages:
        last_content = routing_messages[-1].content if hasattr(routing_messages[-1], "content") else ""
        if isinstance(last_content, list):
            last_content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in last_content)

        match = _match_pre_routing(last_content)
        if match:
            agent, label = match
            logger.info(f"supervisor: Pre-Routing → {agent} ({label}: '{last_content.strip()[:60]}')")
            return {"next_agent": agent}

    sanitized = [
        HumanMessage(content=_sanitize_routing_content(m.content)) if isinstance(m, HumanMessage) else m
        for m in routing_messages
    ]
    all_messages = [SystemMessage(content=SUPERVISOR_PROMPT)] + sanitized
    response = await llm.ainvoke(all_messages)
    content = extract_llm_text(response.content)
    next_agent = content.strip()

    valid = {
        "computer_agent", "terminal_agent", "file_agent",
        "web_agent", "calendar_agent", "reminder_agent",
        "memory_agent", "chat_agent", "vision_agent", "whatsapp_agent", "FINISH"
    }
    if next_agent not in valid:
        logger.warning(f"supervisor: ungültiges Routing '{next_agent}' → fallback chat_agent")
        next_agent = "chat_agent"

    logger.info(f"supervisor → {next_agent}")
    return {"next_agent": next_agent}


def route(state: AgentState) -> AgentName:
    return state["next_agent"]


def _build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("computer_agent", computer_agent)
    graph.add_node("terminal_agent", terminal_agent)
    graph.add_node("file_agent", file_agent)
    graph.add_node("web_agent", web_agent)
    graph.add_node("calendar_agent", calendar_agent)
    graph.add_node("chat_agent", chat_agent)
    graph.add_node("reminder_agent", reminder_agent)
    graph.add_node("memory_agent", memory_agent)
    graph.add_node("vision_agent", vision_agent)
    graph.add_node("whatsapp_agent", whatsapp_agent)

    graph.set_entry_point("supervisor")

    graph.add_conditional_edges(
        "supervisor",
        route,
        {
            "computer_agent": "computer_agent",
            "terminal_agent": "terminal_agent",
            "file_agent": "file_agent",
            "web_agent": "web_agent",
            "calendar_agent": "calendar_agent",
            "reminder_agent": "reminder_agent",
            "memory_agent": "memory_agent",
            "chat_agent": "chat_agent",
            "vision_agent": "vision_agent",
            "whatsapp_agent": "whatsapp_agent",
            "FINISH": END,
        },
    )

    for agent in ["computer_agent", "terminal_agent", "file_agent",
                  "web_agent", "calendar_agent", "reminder_agent",
                  "memory_agent", "chat_agent", "vision_agent", "whatsapp_agent"]:
        graph.add_edge(agent, "supervisor")

    return graph


def get_graph() -> CompiledStateGraph:
    if agent_graph is None:
        raise RuntimeError(
            "LangGraph nicht initialisiert – init_graph() muss zuerst aufgerufen werden. "
            "Sicherstellen dass _post_init() in bot.py vollständig abgeschlossen ist."
        )
    return agent_graph


async def init_graph() -> None:
    global agent_graph, _db_conn

    async with _init_lock:
        if agent_graph is not None:
            return

        import aiosqlite
        _db_conn = await aiosqlite.connect(str(_DB_PATH))
        checkpointer = AsyncSqliteSaver(_db_conn)
        agent_graph = _build_graph().compile(checkpointer=checkpointer)


async def cleanup_checkpoints(max_per_thread: int = 200) -> None:
    """Löscht alte Checkpoints – behält nur die letzten max_per_thread pro thread_id."""
    if _db_conn is None:
        return
    deleted = await _db_conn.execute(
        """
        DELETE FROM checkpoints
        WHERE rowid NOT IN (
            SELECT rowid FROM (
                SELECT rowid,
                       ROW_NUMBER() OVER (
                           PARTITION BY thread_id ORDER BY checkpoint_id DESC
                       ) AS rn
                FROM checkpoints
            ) WHERE rn <= ?
        )
        """,
        (max_per_thread,),
    )
    await _db_conn.execute(
        "DELETE FROM writes WHERE checkpoint_id NOT IN (SELECT checkpoint_id FROM checkpoints)"
    )
    await _db_conn.commit()
    logger.info(f"Checkpoint-Bereinigung: {deleted.rowcount} Einträge entfernt.")


async def close_graph() -> None:
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        _db_conn = None
