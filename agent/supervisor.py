from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
import asyncio

from agent.state import AgentState, AgentName
from agent.llm import get_fast_llm
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

_DB_PATH = Path.home() / ".fabbot" / "memory.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Phase 87: Explizite Type-Annotationen statt implizitem None.
agent_graph: CompiledStateGraph | None = None
_db_conn = None
_init_lock = asyncio.Lock()

SUPERVISOR_PROMPT = """Du bist ein Routing-Agent. Deine einzige Aufgabe ist es, eine der folgenden Antworten zurueckzugeben.

Verfuegbare Agenten:
- file_agent: Dateien und Ordner lesen, auflisten oder schreiben
- terminal_agent: Shell-Befehle, aktuelles Datum/Uhrzeit abrufen, Speicher, CPU, Prozesse – NUR technische Systemabfragen
- web_agent: STANDARD-AGENT fuer alle Fragen ueber die Welt, Personen, Ereignisse, Fakten.
  Nutze web_agent IMMER bei:
  - Fragen ueber reale Personen (lebt X noch? was macht X? wer ist X?)
  - Aktuelle Aemter, Positionen, Rollen (Kanzler, CEO, Praesident, Minister...)
  - Aktuelle Ereignisse, Nachrichten, Politik, Sport, Wirtschaft
  - Preise, Kurse, Wetter
  - Fragen die mit "was ist", "wer ist", "wie ist", "was denkst du ueber [Person/Ereignis]" beginnen
  - Im Zweifel ob chat oder web: IMMER web_agent waehlen
  - NICHT fuer Fragen zu einem Foto oder Bild
- calendar_agent: Kalendertermine lesen oder erstellen
- computer_agent: Desktop-Steuerung, Screenshots, Apps oeffnen
- reminder_agent: Erinnerungen setzen, auflisten oder loeschen (z.B. 'Erinnere mich um 18 Uhr', 'Was sind meine Erinnerungen?')
- memory_agent: Persoenliche Informationen oder Bot-Instruktionen speichern, aktualisieren oder loeschen.
  NUR bei expliziten Speicher-Befehlen:
  JA: 'merke dir dass...', 'speichere...', 'füge ... hinzu', 'vergiss den Eintrag...'
  JA: 'merke dir grundsaetzlich...', 'von jetzt an sollst du...', 'du sollst immer...'
  JA: 'merke dir das', 'merk dir das', 'das merken', 'merk das' (Referenz auf vorherige Aussage)
  NEIN: alle normalen Aussagen, Antworten auf Fragen, Erzaehlungen ohne explizites Speicher-Wort
  NEIN: 'ich mag X', 'ich war bei X', kurze Antworten ohne Speicher-Absicht
  NEIN: Fragen ueber gespeicherte Notizen, Sessions oder Wissen ('was steht in...', 'was habe ich notiert...')
- chat_agent: NUR fuer rein konversationelle Nachrichten OHNE Faktenbezug zur Welt:
  - Folgefragen zum bisherigen Gespraech ("fass das zusammen", "erklaer das nochmal")
  - Persoenliche Fragen ueber den User aus dem Profil (Projekte, Standort, Geraete)
  - Fragen ueber gespeicherte Notizen, Sessions oder Wissen ("was steht in meinen Sessions", "was habe ich ueber X notiert", "was weisst du ueber mein Projekt")
  - Smalltalk ohne Faktenbezug ("danke", "ok", "cool")
  - Hoeflichkeiten und kurze Reaktionen
  - ALLE Folgefragen zu einem Foto oder Bild
  - vision_agent: Bildanalyse – wird automatisch geroutet wenn die Nachricht mit [FOTO] beginnt
- whatsapp_agent: WhatsApp-Nachricht senden. NUR bei expliziten Sende-Befehlen an erlaubte Kontakte:
  JA: "schick Steffi dass...", "WhatsApp an Amalia: ...", "sende Fabio eine Nachricht"
  NEIN: Fragen über WhatsApp, allgemeine Kommunikation

Regeln:
- Wenn die letzte Nachricht bereits eine Antwort eines Agenten enthaelt: FINISH
- Sonst: waehle den passenden Agenten
- Im Zweifel zwischen web_agent und chat_agent: IMMER web_agent
- Im Zweifel zwischen memory_agent und chat_agent: chat_agent waehlen
- Fragen mit 'wo', 'wer', 'was' die sich auf ein Foto beziehen: IMMER chat_agent
- Fragen ueber eigene Notizen/Sessions/Wissen: IMMER chat_agent (nicht memory_agent)

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


def _filter_hitl_messages(messages: list) -> list:
    """
    Phase 91: Proto.MEMORY_VISION_MARKER statt hardcoded "Bildbeschreibung".
    Vorher: Magic String direkt im Code – Format-Änderung hätte still gebrochen.
    Jetzt:  Single Source of Truth in protocol.py.
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

    if messages and isinstance(messages[-1], AIMessage):
        last = messages[-1].content
        if not last.startswith("__MEMORY__:"):
            return {"next_agent": "FINISH"}

    clean_messages = _filter_hitl_messages(messages)
    last_human = [m for m in clean_messages if isinstance(m, HumanMessage)]
    routing_messages = [last_human[-1]] if last_human else clean_messages[-1:]
    all_messages = [SystemMessage(content=SUPERVISOR_PROMPT)] + routing_messages
    response = await llm.ainvoke(all_messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    next_agent = content.strip()

    valid = {
        "computer_agent", "terminal_agent", "file_agent",
        "web_agent", "calendar_agent", "reminder_agent",
        "memory_agent", "chat_agent", "vision_agent", "whatsapp_agent", "FINISH"
    }
    if next_agent not in valid:
        next_agent = "chat_agent"

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
    """
    Gibt den initialisierten CompiledStateGraph zurück.
    Wirft RuntimeError wenn init_graph() noch nicht abgeschlossen ist.
    """
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


async def close_graph() -> None:
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        _db_conn = None

def _build_supervisor_prompt() -> str:
    # Ph.98: Datum fuer Tests
    from agent.utils import get_current_datetime
    return '[Aktuelles Datum/Uhrzeit: ' + get_current_datetime() + ']'
