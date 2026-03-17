from typing import Annotated, Literal
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


AgentName = Literal[
    "computer_agent",
    "terminal_agent",
    "file_agent",
    "web_agent",
    "calendar_agent",
    "chat_agent",
    "FINISH",
]


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    telegram_chat_id: int | None
    next_agent: AgentName | None