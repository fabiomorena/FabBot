from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent.state import AgentState, AgentName
from agent.llm import get_llm
from agent.agents.computer import computer_agent
from agent.agents.terminal import terminal_agent
from agent.agents.file import file_agent
from agent.agents.web import web_agent
from agent.agents.calendar import calendar_agent
from agent.agents.chat_agent import chat_agent

_DB_PATH = Path.home() / ".fabbot" / "memory.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Globale Referenzen – werden in init_graph() gesetzt
agent_graph = None
_db_conn = None

SUPERVISOR_PROMPT = """Du bist ein Supervisor-Agent. Du koordinierst spezialisierte Sub-Agenten.

Verfuegbare Agenten und ihre genauen Zustaendigkeiten:
- file_agent: Dateien und Ordner LESEN, AUFLISTEN oder SCHREIBEN
- terminal_agent: Shell-Befehle, Systeminformationen wie Speicher, CPU, laufende Prozesse
- web_agent: Im Internet suchen, Webseiten abrufen, aktuelle Nachrichten
- calendar_agent: Kalendertermine lesen oder erstellen
- computer_agent: NUR fuer echte Desktop-Steuerung (Klicks, Screenshots, Apps oeffnen per UI)
- chat_agent: Folgefragen zum bisherigen Gespraech, Meta-Fragen, Zusammenfassungen des Verlaufs,
  Hoeflichkeiten. Beispiele: "was habe ich dich gefragt?", "fass das zusammen",
  "erklaer das nochmal", "danke", "was meintest du mit X?"

Regeln:
1. Wenn die letzte Nachricht eine Antwort eines Sub-Agenten ist (enthaelt Ergebnisse/Daten), antworte mit: FINISH
2. Fuer Folgefragen oder Meta-Fragen zum bisherigen Gespraech: chat_agent
3. Fuer neue externe Aufgaben: passenden Spezialagenten waehlen
4. Antworte NUR mit einem dieser Woerter:
computer_agent | terminal_agent | file_agent | web_agent | calendar_agent | chat_agent | FINISH
"""


def supervisor_node(state: AgentState) -> AgentState:
    """Analysiert den aktuellen State und entscheidet welcher Agent als naechstes aktiv wird."""
    llm = get_llm()
    messages = state["messages"]

    if messages and isinstance(messages[-1], AIMessage):
        content = messages[-1].content
        if isinstance(content, list):
            content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        content = content.strip()
        if not content.startswith("__CONFIRM_"):
            return {"next_agent": "FINISH"}

    all_messages = [SystemMessage(content=SUPERVISOR_PROMPT)] + messages
    response = llm.invoke(all_messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    next_agent = content.strip()

    valid = {
        "computer_agent", "terminal_agent", "file_agent",
        "web_agent", "calendar_agent", "chat_agent", "FINISH"
    }
    if next_agent not in valid:
        next_agent = "FINISH"

    return {"next_agent": next_agent}


def route(state: AgentState) -> AgentName:
    """Gibt den naechsten Agent-Namen aus dem State zurueck."""
    return state["next_agent"]


def _build_graph() -> StateGraph:
    """Erstellt den StateGraph ohne Checkpointer."""
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("computer_agent", computer_agent)
    graph.add_node("terminal_agent", terminal_agent)
    graph.add_node("file_agent", file_agent)
    graph.add_node("web_agent", web_agent)
    graph.add_node("calendar_agent", calendar_agent)
    graph.add_node("chat_agent", chat_agent)

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
            "chat_agent": "chat_agent",
            "FINISH": END,
        },
    )

    for agent in ["computer_agent", "terminal_agent", "file_agent",
                  "web_agent", "calendar_agent", "chat_agent"]:
        graph.add_edge(agent, "supervisor")

    return graph


async def init_graph() -> None:
    """Initialisiert den Graphen mit persistentem AsyncSqliteSaver.
    Wird via post_init Hook von python-telegram-bot aufgerufen.
    """
    global agent_graph, _db_conn
    import aiosqlite
    _db_conn = await aiosqlite.connect(str(_DB_PATH))
    checkpointer = AsyncSqliteSaver(_db_conn)
    agent_graph = _build_graph().compile(checkpointer=checkpointer)


async def close_graph() -> None:
    """Schliesst die SQLite-Verbindung sauber beim Shutdown.
    Wird via post_shutdown Hook von python-telegram-bot aufgerufen.
    """
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        _db_conn = None