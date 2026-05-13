from typing import Annotated, Literal
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict


AgentName = Literal[
    "computer_agent",
    "terminal_agent",
    "file_agent",
    "web_agent",
    "calendar_agent",
    "chat_agent",
    "memory_agent",
    "vision_agent",
    "reminder_agent",
    "whatsapp_agent",
    "FINISH",
]


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    telegram_chat_id: NotRequired[int | None]
    next_agent: NotRequired[AgentName | None]
    image_data: NotRequired[str | None]
    image_caption: NotRequired[str | None]
    image_media_type: NotRequired[str | None]
    last_agent_result: NotRequired[str | None]
    last_agent_name: NotRequired[str | None]
    _confirm_display: NotRequired[str | None]
