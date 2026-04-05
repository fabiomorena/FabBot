"""
Reminder Agent fuer FabBot.
Versteht natuerliche Sprache fuer Erinnerungen und speichert sie in SQLite.
"""
import json
import logging
import re
from datetime import datetime, date, timedelta
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState
from agent.llm import get_llm

logger = logging.getLogger(__name__)


def _build_prompt() -> str:
    now = datetime.now()
    today = date.today().isoformat()
    time_str = now.strftime("%H:%M")
    return f"""Du bist ein spezialisierter Erinnerungs-Agent. Aktuelles Datum: {today}, Uhrzeit: {time_str}

Analysiere die Anfrage und antworte NUR mit JSON:

Fuer neue Erinnerung:
{{
  "action": "create",
  "text": "Worum geht es bei der Erinnerung",
  "remind_at": "YYYY-MM-DDTHH:MM:00"
}}

Fuer Erinnerungen auflisten:
{{
  "action": "list"
}}

Fuer Erinnerung loeschen:
{{
  "action": "delete",
  "id": 123
}}

Zeitangaben:
- "in 10 Minuten" → jetzt + 10 Minuten
- "in 2 Stunden" → jetzt + 2 Stunden
- "heute um 18 Uhr" → {today}T18:00:00
- "morgen um 9 Uhr" → {(date.today() + timedelta(days=1)).isoformat()}T09:00:00
- "morgen früh" → {(date.today() + timedelta(days=1)).isoformat()}T08:00:00

Kein Markdown, keine Erklaerung, nur reines JSON.
Wenn nicht unterstuetzt: UNSUPPORTED
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    if not text:
        return "UNSUPPORTED"
    match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


async def reminder_agent(state: AgentState) -> AgentState:
    """Verarbeitet Erinnerungs-Anfragen."""
    from bot.reminders import add_reminder, list_reminders, delete_reminder

    llm = get_llm()
    filtered = [m for m in state["messages"] if not (
        hasattr(m, "content") and isinstance(m.content, str) and
        m.content.startswith(("__MEMORY__:", "__CONFIRM_", "__SCREENSHOT__"))
    )]
    human_msgs = [m for m in filtered if hasattr(m, 'type') and m.type == 'human']
    last_msg = [human_msgs[-1]] if human_msgs else filtered[-1:]
    messages = [SystemMessage(content=_build_prompt())] + last_msg
    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    content = _extract_json(content)

    if content == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Erinnerung konnte ich nicht verstehen. Bitte formuliere es anders, z.B. 'Erinnere mich morgen um 9 Uhr ans Meeting'.")]}

    # Phase 75: Natürliche Sprache abfangen – LLM hat Rückfrage statt JSON geliefert.
    # Alle validen Routing-Antworten dieses Agents beginnen mit '{'.
    if not content.strip().startswith("{"):
        return {"messages": [AIMessage(content=content.strip())]}

    try:
        parsed = json.loads(content)
        action = parsed.get("action")
    except (json.JSONDecodeError, AttributeError):
        return {"messages": [AIMessage(content="Fehler beim Verarbeiten der Erinnerung.")]}

    chat_id = state.get("telegram_chat_id", 0)

    if action == "create":
        text = parsed.get("text", "").strip()
        remind_at_str = parsed.get("remind_at", "")
        if not text or not remind_at_str:
            return {"messages": [AIMessage(content="Bitte gib an woran und wann ich dich erinnern soll.")]}
        try:
            remind_at = datetime.fromisoformat(remind_at_str)
        except ValueError:
            return {"messages": [AIMessage(content="Ungültiges Zeitformat. Bitte versuche es nochmal.")]}
        reminder_id = add_reminder(chat_id, text, remind_at)
        time_str = remind_at.strftime("%d.%m.%Y um %H:%M Uhr")
        return {"messages": [AIMessage(content=f"✅ Erinnerung gesetzt! Ich erinnere dich am {time_str} an: {text}")]}

    elif action == "list":
        reminders = list_reminders(chat_id)
        if not reminders:
            return {"messages": [AIMessage(content="Du hast keine offenen Erinnerungen.")]}
        lines = ["📋 *Deine Erinnerungen:*\n"]
        for r in reminders:
            dt = datetime.fromisoformat(r["remind_at"])
            time_str = dt.strftime("%d.%m.%Y, %H:%M Uhr")
            lines.append(f"• ID {r['id']}: {r['text']} – {time_str}")
        return {"messages": [AIMessage(content="\n".join(lines))]}

    elif action == "delete":
        reminder_id = parsed.get("id")
        if not reminder_id:
            return {"messages": [AIMessage(content="Bitte gib die ID der Erinnerung an.")]}
        success = delete_reminder(int(reminder_id), chat_id)
        if success:
            return {"messages": [AIMessage(content=f"✅ Erinnerung #{reminder_id} gelöscht.")]}
        else:
            return {"messages": [AIMessage(content=f"Erinnerung #{reminder_id} nicht gefunden.")]}

    return {"messages": [AIMessage(content="Unbekannte Aktion.")]}
