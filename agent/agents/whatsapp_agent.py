"""
WhatsApp Agent für FabBot – Phase 81.
Phase 99: last_agent_result + last_agent_name in allen Returns.
"""

import json
import logging
import re

from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.audit import log_action
from agent.llm import get_llm
from agent.protocol import Proto

logger = logging.getLogger(__name__)

_PARSER_PROMPT = """Du bist ein WhatsApp-Nachrichten-Parser.

Analysiere die Anfrage und extrahiere Kontaktname und Nachrichtentext.

Antworte NUR mit reinem JSON:
{
  "contact": "Name des Empfängers",
  "message": "Der zu sendende Nachrichtentext"
}

Regeln:
- contact: Genau der Name wie der User ihn nennt (z.B. "Steffi", "Amalia", "Fabio")
- message: Nur der eigentliche Nachrichtentext, natürlich formuliert
- Wenn Kontakt oder Nachricht unklar: {"contact": "", "message": ""}

Beispiele:
"Schick Steffi dass ich 10 Minuten später komme"
→ {"contact": "Steffi", "message": "Ich komme 10 Minuten später"}

"WhatsApp an Amalia: bin gleich da"
→ {"contact": "Amalia", "message": "Bin gleich da"}

"sende mir selbst eine Test-Nachricht"
→ {"contact": "Fabio", "message": "Test"}

Kein Markdown, nur reines JSON.
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _make_result(msg: str) -> AgentState:
    return {"messages": [AIMessage(content=msg)], "last_agent_result": msg, "last_agent_name": "whatsapp_agent"}


async def whatsapp_agent(state: AgentState) -> AgentState:
    """Phase 99: last_agent_result in allen Returns."""
    from bot.whatsapp import find_contact, is_session_ready, load_whatsapp_contacts

    if not is_session_ready():
        return _make_result("WhatsApp nicht eingerichtet. Bitte /wa_setup ausführen.")

    llm = get_llm()
    human_msgs = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    last_msg = [human_msgs[-1]] if human_msgs else state["messages"][-1:]
    messages = [SystemMessage(content=_PARSER_PROMPT)] + last_msg

    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        )
    content = _extract_json(content)

    if not content.strip().startswith("{"):
        return _make_result(content.strip())

    try:
        parsed = json.loads(content)
        contact_name = parsed.get("contact", "").strip()
        message_text = parsed.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return _make_result(
            "Konnte Kontakt und Nachricht nicht erkennen. "
            "Beispiel: 'Schick Steffi dass ich 10 Minuten später komme'"
        )

    if not contact_name:
        return _make_result("Welchen Kontakt soll ich anschreiben?")

    if not message_text:
        return _make_result(f"Was soll ich {contact_name} schreiben?")

    message_text = message_text[:500]

    contact = find_contact(contact_name)
    if contact is None:
        log_action(
            "whatsapp_agent", "send",
            f"blocked: '{contact_name}' nicht in Whitelist",
            state.get("telegram_chat_id"),
            status="blocked",
        )
        return _make_result(f"'{contact_name}' ist nicht in der WhatsApp-Kontaktliste.")

    whatsapp_name = contact.get("whatsapp_name", contact_name)

    log_action(
        "whatsapp_agent", "send",
        f"contact={contact_name} whatsapp_name={whatsapp_name} len={len(message_text)}",
        state.get("telegram_chat_id"),
        status="pending",
    )

    return {
        "messages": [AIMessage(content=f"{Proto.CONFIRM_WHATSAPP}{whatsapp_name}::{message_text}")],
        "next_agent": None,
        "_confirm_display": f"WhatsApp an {contact_name}: {message_text}",
        "last_agent_result": None,
        "last_agent_name": "whatsapp_agent",
    }
