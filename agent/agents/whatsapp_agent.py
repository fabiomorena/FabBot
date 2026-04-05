"""
WhatsApp Agent für FabBot – Phase 81.

Parst Kontakt + Nachricht via LLM.
Lookup in whatsapp_contacts aus personal_profile.yaml (Whitelist).
Gibt HITL-Confirmation zurück.
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


async def whatsapp_agent(state: AgentState) -> AgentState:
    """
    Pipeline:
    1. LLM parst Kontakt + Nachricht
    2. Whitelist-Check gegen whatsapp_contacts im Profil
    3. HITL-Confirmation zurückgeben
    """
    from bot.whatsapp import find_contact, is_session_ready, load_whatsapp_contacts

    # Session-Check zuerst
    if not is_session_ready():
        return {"messages": [AIMessage(
            content="WhatsApp nicht eingerichtet. Bitte /wa_setup ausführen."
        )]}

    # LLM parst Kontakt + Nachricht
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

    # Natürliche Sprache / Rückfrage abfangen
    if not content.strip().startswith("{"):
        return {"messages": [AIMessage(content=content.strip())]}

    try:
        parsed = json.loads(content)
        contact_name = parsed.get("contact", "").strip()
        message_text = parsed.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return {"messages": [AIMessage(
            content="Konnte Kontakt und Nachricht nicht erkennen. "
                    "Beispiel: 'Schick Steffi dass ich 10 Minuten später komme'"
        )]}

    if not contact_name:
        return {"messages": [AIMessage(
            content="Welchen Kontakt soll ich anschreiben?"
        )]}

    if not message_text:
        return {"messages": [AIMessage(
            content=f"Was soll ich {contact_name} schreiben?"
        )]}

    # Whitelist-Check – nur erlaubte Kontakte
    contact = find_contact(contact_name)
    if contact is None:
        allowed = [
            c.get("name", "") for c in load_whatsapp_contacts()
            if isinstance(c, dict)
        ]
        allowed_str = ", ".join(allowed) if allowed else "keine Kontakte konfiguriert"
        log_action(
            "whatsapp_agent", "send",
            f"blocked: '{contact_name}' nicht in Whitelist",
            state.get("telegram_chat_id"),
            status="blocked",
        )
        return {"messages": [AIMessage(
            content=(
                f"'{contact_name}' ist nicht in der WhatsApp-Whitelist.\n"
                f"Erlaubte Kontakte: {allowed_str}"
            )
        )]}

    # Exakter WhatsApp-Anzeigename (inkl. Emoji)
    whatsapp_name = contact.get("whatsapp_name", contact_name)

    log_action(
        "whatsapp_agent", "send",
        f"contact={contact_name} whatsapp_name={whatsapp_name} len={len(message_text)}",
        state.get("telegram_chat_id"),
        status="pending",
    )

    return {
        "messages": [AIMessage(
            content=f"{Proto.CONFIRM_WHATSAPP}{whatsapp_name}::{message_text}"
        )],
        "next_agent": None,
        "_confirm_display": f"WhatsApp an {contact_name}: {message_text}",
    }
