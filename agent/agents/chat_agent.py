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


async def chat_agent(state: AgentState) -> AgentState:
    """Antwortet direkt aus dem Gesprächsverlauf ohne externe Tools."""
    llm = get_llm()
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return {"messages": [AIMessage(content=content.strip())]}