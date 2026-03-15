import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from agent.state import AgentState

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

PROMPT = """Du bist ein spezialisierter Agent für Terminal-Befehle auf einem Mac.
Du kannst Shell-Befehle ausführen, Systeminformationen abrufen und Scripts starten.

Phase 1: Beschreibe welchen Befehl du ausführen würdest (Terminal-Tool kommt in Phase 2).
"""

def terminal_agent(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response], "next_agent": None}
