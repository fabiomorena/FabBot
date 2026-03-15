import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
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

Verfügbare Agenten:
- computer_agent: Desktop steuern, Screenshots machen, Apps öffnen, UI-Interaktion
- terminal_agent: Shell-Befehle ausführen, Scripts starten, Systeminformationen
- file_agent: Dateien lesen, schreiben, suchen, umbenennen
- web_agent: Im Web suchen, URLs abrufen, Informationen recherchieren
- calendar_agent: Kalendereinträge lesen, Events erstellen, Termine verwalten

Analysiere die Anfrage und antworte NUR mit dem Namen des passenden Agenten.
Wenn die Aufgabe erledigt ist oder keine Weiterleitung nötig ist, antworte mit: FINISH

Antworte ausschließlich mit einem dieser Wörter:
computer_agent | terminal_agent | file_agent | web_agent | calendar_agent | FINISH
"""


def supervisor_node(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=SUPERVISOR_PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    next_agent = response.content.strip()

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

    # Nach jedem Sub-Agenten zurück zum Supervisor
    for agent in ["computer_agent", "terminal_agent", "file_agent", "web_agent", "calendar_agent"]:
        graph.add_edge(agent, "supervisor")

    return graph.compile()


agent_graph = build_graph()

