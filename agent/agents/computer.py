import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

PROMPT = """Du bist ein spezialisierter Agent für Desktop-Steuerung auf einem Mac.
Du kannst Screenshots machen, Apps öffnen, klicken und tippen.

Phase 1: Beschreibe was du tun würdest (Computer Use API kommt in Phase 2).
"""

def computer_agent(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response], "next_agent": None}

