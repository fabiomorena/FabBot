import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from agent.state import AgentState

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

PROMPT = """Du bist ein spezialisierter Agent für Dateioperationen auf einem Mac.
Du kannst Dateien lesen, schreiben, suchen und umbenennen.

Phase 1: Beschreibe was du tun würdest (File-Tool kommt in Phase 2).
"""

def file_agent(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response], "next_agent": None}

