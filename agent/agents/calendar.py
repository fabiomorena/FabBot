import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from agent.state import AgentState

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

PROMPT = """Du bist ein spezialisierter Agent für Kalender-Management auf einem Mac.
Du kannst Termine lesen, erstellen und verwalten.

Phase 1: Beschreibe was du tun würdest (Calendar-Tool kommt in Phase 3).
"""

def calendar_agent(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response], "next_agent": None}
