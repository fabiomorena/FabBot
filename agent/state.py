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
    "memory_agent",
    "vision_agent",
    "reminder_agent",
    "whatsapp_agent",
    "FINISH",
]


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    telegram_chat_id: int | None
    next_agent: AgentName | None
    image_data: str | None  # base64-kodiertes Bild für vision_agent
    image_caption: str | None  # User-Caption zum Bild
    image_media_type: str | None  # MIME-Type z.B. image/jpeg, image/png
    # Phase 99: State-Transfer zwischen Agents (Issue #15)
    # Jeder Sub-Agent schreibt sein Ergebnis hier rein.
    # chat_agent injiziert es außerhalb des Prompt-Cache in den System-Prompt.
    last_agent_result: str | None  # Plaintext-Ergebnis des zuletzt ausgeführten Agents
    last_agent_name: str | None  # Name des Agents z.B. "web_agent", "file_agent"
