import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, AIMessage
from langgraph.graph import StateGraph, END

from agent.state import AgentState, AgentName
from agent.agents.computer import computer_agent
from agent.agents.terminal import terminal_agent
from agent.agents.file import file_agent
from agent.agents.web import web_agent
from agent.agents.calendar import calendar_agent

MODEL = "claude-sonnet-4-20250514"

llm = ChatAnthropic(
    model=MODEL,
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

SUPERVISOR_PROMPT = """Du bist ein Supervisor-Agent. Du koordinierst spezialisierte Sub-Agenten.

Verfuegbare Agenten und ihre genauen Zustaendigkeiten:
- file_agent: Dateien und Ordner LESEN, AUFLISTEN oder SCHREIBEN. Zustaendig fuer: "zeig mir Ordner X", "liste Dateien in...", "lese Datei...", "was ist in meinem Downloads-Ordner"
- terminal_agent: Shell-Befehle, Systeminformationen wie Speicher, CPU, laufende Prozesse
- web_agent: Im Internet suchen, Webseiten abrufen, aktuelle Nachrichten
- calendar_agent: Kalendertermine lesen oder erstellen
- computer_agent: NUR fuer echte Desktop-Steuerung (Klicks, Screenshots, Apps oeffnen per UI). NICHT fuer Datei-Operationen.

Wichtig: "zeig mir meinen Downloads-Ordner" oder "liste Dateien" -> file_agent, NICHT computer_agent

Regeln:
1. Wenn die letzte Nachricht eine Antwort eines Sub-Agenten ist (enthaelt Ergebnisse/Daten), antworte mit: FINISH
2. Waehle den passenden Agenten fuer die urspruengliche Anfrage
3. Antworte NUR mit einem dieser Woerter:
computer_agent | terminal_agent | file_agent | web_agent | calendar_agent | FINISH
"""


def supervisor_node(state: AgentState) -> AgentState:
    messages = state["messages"]

    # Trailing Whitespace aus letzter AI-Nachricht entfernen
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

    valid = {"computer_agent", "terminal_agent", "file_agent", "web_agent", "calendar_agent", "FINISH"}
    if next_agent not in valid:
        next_agent = "FINISH"

    return {"next_agent": next_agent}


def route(state: AgentState) -> AgentName:
    return state["next_agent"]


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("computer_agent", computer_agent)
    graph.add_node("terminal_agent", terminal_agent)
    graph.add_node("file_agent", file_agent)
    graph.add_node("web_agent", web_agent)
    graph.add_node("calendar_agent", calendar_agent)

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
            "FINISH": END,
        },
    )

    for agent in ["computer_agent", "terminal_agent", "file_agent", "web_agent", "calendar_agent"]:
        graph.add_edge(agent, "supervisor")

    return graph.compile()


agent_graph = build_graph()