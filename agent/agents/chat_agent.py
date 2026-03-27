from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState
from agent.llm import get_llm

PROMPT = """Du bist ein hilfreicher persoenlicher Assistent mit Zugriff auf den bisherigen Gespraechsverlauf.

Beantworte die Frage des Users direkt aus dem Kontext des bisherigen Gespraechs.
Kein Tool-Aufruf, kein Suchen – nur direkte, praezise Antworten basierend auf dem was bereits bekannt ist.

Typische Faelle fuer dich:
- "Was habe ich dich gerade gefragt?"
- "Fass das zusammen"
- "Erklaer das nochmal anders"
- "Was meintest du mit X?"
- "Danke" / allgemeine Hoeflichkeiten
- Kurze Folgefragen zum vorherigen Thema
"""

_HITL_PREFIXES = ("__CONFIRM_", "__SCREENSHOT__", "__MEMORY__")


def _clean_messages_for_chat(messages: list) -> list:
    """Ersetzt HITL-Nachrichten durch lesbare Platzhalter fuer den chat_agent.
    Der chat_agent soll wissen dass eine Aktion stattfand, aber nicht den rohen Prefix sehen.
    """
    cleaned = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""
        if isinstance(content, str) and content.startswith(_HITL_PREFIXES):
            if isinstance(msg, AIMessage):
                # HITL-Prefix durch lesbaren Text ersetzen
                if content.startswith("__CONFIRM_TERMINAL__:"):
                    cmd = content[len("__CONFIRM_TERMINAL__:"):]
                    cleaned.append(AIMessage(content=f"[Terminal-Befehl ausgefuehrt: {cmd}]"))
                elif content.startswith("__CONFIRM_CREATE_EVENT__:"):
                    cleaned.append(AIMessage(content="[Kalendereintrag erstellt]"))
                elif content.startswith("__CONFIRM_FILE_WRITE__:"):
                    cleaned.append(AIMessage(content="[Datei geschrieben]"))
                elif content.startswith("__CONFIRM_COMPUTER__:"):
                    cleaned.append(AIMessage(content="[Desktop-Aktion ausgefuehrt]"))
                elif content.startswith("__SCREENSHOT__:"):
                    cleaned.append(AIMessage(content="[Screenshot erstellt]"))
                else:
                    cleaned.append(AIMessage(content="[Aktion ausgefuehrt]"))
        else:
            cleaned.append(msg)
    return cleaned


async def chat_agent(state: AgentState) -> AgentState:
    """Antwortet direkt aus dem Gesprächsverlauf ohne externe Tools.
    HITL-Nachrichten werden durch lesbare Platzhalter ersetzt.
    Nutzt ainvoke() um den asyncio Event-Loop nicht zu blockieren.
    """
    llm = get_llm()
    clean_messages = _clean_messages_for_chat(state["messages"])
    messages = [SystemMessage(content=PROMPT)] + clean_messages
    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return {"messages": [AIMessage(content=content.strip())]}