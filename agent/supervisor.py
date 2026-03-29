from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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

_DB_PATH = Path.home() / ".fabbot" / "memory.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

agent_graph = None
_db_conn = None

SUPERVISOR_PROMPT = """Du bist ein Routing-Agent. Deine einzige Aufgabe ist es, eine der folgenden Antworten zurueckzugeben.

Verfuegbare Agenten:
- file_agent: Dateien und Ordner lesen, auflisten oder schreiben
- terminal_agent: Shell-Befehle, aktuelles Datum/Uhrzeit abrufen, Speicher, CPU, Prozesse – NUR technische Systemabfragen
- web_agent: Internet suchen, Webseiten abrufen, aktuelle Nachrichten, Wetter, ALLE Fragen die aktuelle oder externe Informationen erfordern
- calendar_agent: Kalendertermine lesen oder erstellen
- computer_agent: Desktop-Steuerung, Screenshots, Apps oeffnen
- reminder_agent: Erinnerungen setzen, auflisten oder loeschen (z.B. 'Erinnere mich um 18 Uhr', 'Was sind meine Erinnerungen?')
- memory_agent: Persoenliche Informationen ins Profil speichern, aktualisieren oder loeschen.
  NUR bei expliziten Speicher-Befehlen MIT konkretem Inhalt:
  JA: 'merke dir dass ich Yoga mache', 'fuege Saporito als Restaurant hinzu', 'speichere Marco als Kollegen', 'vergiss den Eintrag X', 'fuege X zum Kontext hinzu'
  NEIN: 'ich habe X verbessert', 'ich war bei X', 'X funktioniert jetzt', 'ich habe X gemacht', allgemeine Berichte oder Mitteilungen ohne Speicher-Absicht
- chat_agent: Smalltalk, Folgefragen, Zusammenfassungen, Hoeflichkeiten, persoenliche Berichte und Mitteilungen ('ich habe X gemacht', 'X funktioniert jetzt', 'ich war bei X'), persoenliche Fragen ueber den User (Projekte, Standort, Praeferenzen), alles was kein konkreter Systembefehl oder externe Suche ist

Regeln:
- Wenn die letzte Nachricht bereits eine Antwort eines Agenten enthaelt: FINISH
- Sonst: waehle den passenden Agenten
- Im Zweifel zwischen memory_agent und chat_agent: chat_agent waehlen

WICHTIG: Antworte AUSSCHLIESSLICH mit einem dieser Woerter (nichts anderes, keine Erklaerung):
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
    """Entfernt HITL-Nachrichten aus dem Kontext bevor sie an den LLM uebergeben werden."""
    filtered = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""
        if isinstance(content, str) and content.startswith(("__CONFIRM_", "__SCREENSHOT__", "__MEMORY__")):
            if isinstance(msg, AIMessage):
                filtered.append(AIMessage(content="[Aktion wurde ausgefuehrt]"))
            continue
        filtered.append(msg)
    return filtered


async def supervisor_node(state: AgentState) -> AgentState:
    """Routing via Haiku – schnell und kostenguenstig."""
    llm = get_fast_llm()
    messages = state["messages"]

    if messages and isinstance(messages[-1], AIMessage) and not messages[-1].content.startswith("__MEMORY__:"):
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
        "memory_agent", "chat_agent", "FINISH"
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
            "FINISH": END,
        },
    )

    for agent in ["computer_agent", "terminal_agent", "file_agent",
                  "web_agent", "calendar_agent", "reminder_agent",
                  "memory_agent", "chat_agent"]:
        graph.add_edge(agent, "supervisor")

    return graph


async def init_graph() -> None:
    global agent_graph, _db_conn
    import aiosqlite
    _db_conn = await aiosqlite.connect(str(_DB_PATH))
    checkpointer = AsyncSqliteSaver(_db_conn)
    agent_graph = _build_graph().compile(checkpointer=checkpointer)


async def close_graph() -> None:
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        _db_conn = None