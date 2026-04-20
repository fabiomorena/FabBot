import re
import json
import subprocess
from datetime import datetime, timedelta, date
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.audit import log_action
from agent.llm import get_llm
from agent.protocol import Proto
from agent.utils import extract_llm_text


def _build_prompt() -> str:
    from agent.utils import get_current_datetime
    today = date.today().strftime("%d.%m.%Y")
    weekday = datetime.now().strftime("%A")
    dt = get_current_datetime()
    return f"""Du bist ein spezialisierter Kalender-Agent. Aktuelles Datum/Uhrzeit: {dt} ({weekday})

Analysiere die Anfrage und antworte NUR mit JSON:
{{
  "action": "list_events|create_event",
  "source": "apple|google|both",
  "date_from": "YYYY-MM-DD",
  "date_to": "YYYY-MM-DD",
  "title": "Nur bei create_event",
  "start_time": "YYYY-MM-DDTHH:MM:SS Nur bei create_event",
  "end_time": "YYYY-MM-DDTHH:MM:SS Nur bei create_event"
}}

- Fuer 'heute': date_from und date_to = {date.today().isoformat()}
- Fuer 'morgen': date_from und date_to = {(date.today() + timedelta(days=1)).isoformat()}
- Fuer 'diese Woche': date_from = {date.today().isoformat()}, date_to = {(date.today() + timedelta(days=7)).isoformat()}
- source=both: Beide Kalender abfragen (Standard)

Kein Markdown, keine Erklaerung, nur reines JSON.
Wenn nicht unterstuetzt: UNSUPPORTED
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _get_apple_events(date_from: str, date_to: str) -> list[dict]:
    try:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to = datetime.strptime(date_to, "%Y-%m-%d")
        apple_from = dt_from.strftime("%d.%m.%Y")
        apple_to = dt_to.strftime("%d.%m.%Y")
    except ValueError:
        apple_from = date_from
        apple_to = date_to

    cmd = [
        "osascript",
        "-e", f'set startDate to date "{apple_from}"',
        "-e", f'set endDate to date "{apple_to}" + (23 * hours) + (59 * minutes)',
        "-e", 'set output to ""',
        "-e", 'tell application "Calendar"',
        "-e", '    repeat with cal in calendars',
        "-e", '        set evts to (every event of cal whose start date >= startDate and start date <= endDate)',
        "-e", '        repeat with evt in evts',
        "-e", '            set evtStart to start date of evt',
        "-e", '            set h to hours of evtStart as string',
        "-e", '            set m to minutes of evtStart',
        "-e", '            if m < 10 then',
        "-e", '                set mStr to "0" & (m as string)',
        "-e", '            else',
        "-e", '                set mStr to m as string',
        "-e", '            end if',
        "-e", '            set output to output & (summary of evt) & "|" & h & ":" & mStr & "|" & (name of cal) & linefeed',
        "-e", '        end repeat',
        "-e", '    end repeat',
        "-e", 'end tell',
        "-e", 'return output',
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        events = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) >= 3:
                events.append({
                    "title": parts[0].strip(),
                    "start": parts[1].strip(),
                    "calendar": parts[2].strip(),
                    "source": "Apple",
                })
        return events
    except Exception:
        return []


def _format_events(events: list[dict]) -> str:
    if not events:
        return "Keine Termine gefunden."
    events.sort(key=lambda e: e.get("start", "").zfill(5))
    return "\n".join(f"{e.get('start', '')}  {e['title']}" for e in events)


def _sanitize_applescript_string(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    text = text.replace("\n", " ").replace("\r", " ")
    return text


def calendar_event_create(title: str, start_time: str, end_time: str, chat_id: int) -> str:
    try:
        dt_start = datetime.fromisoformat(start_time)
        apple_start = dt_start.strftime("%d.%m.%Y %H:%M")

        if end_time:
            dt_end = datetime.fromisoformat(end_time)
            apple_end = dt_end.strftime("%d.%m.%Y %H:%M")
        else:
            dt_end = dt_start.replace(hour=dt_start.hour + 1)
            apple_end = dt_end.strftime("%d.%m.%Y %H:%M")

        safe_title = _sanitize_applescript_string(title)
        cmd = [
            "osascript",
            "-e", 'tell application "Calendar"',
            "-e", '    tell calendar "Kalender"',
            "-e", f'        set newEvent to make new event with properties {{summary:"{safe_title}", start date:date "{apple_start}", end date:date "{apple_end}"}}',
            "-e", '    end tell',
            "-e", 'end tell',
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return f"Fehler beim Erstellen des Termins: {result.stderr}"

        log_action("calendar_agent", "create_event", f"title={title} start={start_time}", chat_id, status="executed")
        return f"Termin erstellt:\n{title}\n{apple_start} bis {apple_end}"
    except Exception as e:
        return f"Fehler: {e}"


async def calendar_agent(state: AgentState) -> AgentState:
    """Phase 88: ainvoke. Phase 99: last_agent_result in allen Returns."""
    llm = get_llm()
    human_msgs = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    last_msg = [human_msgs[-1]] if human_msgs else state["messages"][-1:]
    messages = [SystemMessage(content=_build_prompt())] + last_msg
    response = await llm.ainvoke(messages)
    content = extract_llm_text(response.content)
    content = _extract_json(content)

    def _err(msg: str) -> AgentState:
        return {"messages": [AIMessage(content=msg)], "last_agent_result": msg, "last_agent_name": "calendar_agent"}

    if not content or content == "UNSUPPORTED":
        return _err("Diese Aktion wird vom Kalender-Agent nicht unterstuetzt.")

    if not content.strip().startswith("{"):
        return _err(content.strip())

    try:
        parsed = json.loads(content)
        action = parsed.get("action")
        date_from = parsed.get("date_from", date.today().isoformat())
        date_to = parsed.get("date_to", date_from)
    except (json.JSONDecodeError, AttributeError):
        action = "list_events"
        date_from = date.today().isoformat()
        date_to = date_from

    log_action("calendar_agent", action, f"{date_from} to {date_to}",
               state.get("telegram_chat_id"), status="executed")

    if action == "list_events":
        events = _get_apple_events(date_from, date_to)
        formatted = _format_events(events)
        period = date_from if date_from == date_to else f"{date_from} bis {date_to}"
        result = f"Termine {period}:\n\n{formatted}"
        return {"messages": [AIMessage(content=result)], "last_agent_result": result, "last_agent_name": "calendar_agent"}

    elif action == "create_event":
        title = parsed.get("title", "").strip() if isinstance(parsed, dict) else ""
        start_time = parsed.get("start_time", "").strip() if isinstance(parsed, dict) else ""
        end_time = parsed.get("end_time", "").strip() if isinstance(parsed, dict) else ""

        if not title or not start_time:
            return _err("Fehler: Titel und Startzeit sind erforderlich.")

        try:
            datetime.fromisoformat(start_time)
            if end_time:
                datetime.fromisoformat(end_time)
        except ValueError:
            return _err("Fehler: Ungültiges Zeitformat. Erwartet: YYYY-MM-DDTHH:MM:SS")

        confirm_text = f"Neuer Termin:\n{title}\n{start_time}"
        if end_time:
            confirm_text += f" bis {end_time}"

        return {
            "messages": [AIMessage(content=f"{Proto.CONFIRM_CREATE_EVENT}{title}::{start_time}::{end_time}")],
            "next_agent": None,
            "_confirm_display": confirm_text,
            "last_agent_result": None,
            "last_agent_name": "calendar_agent",
        }

    else:
        return _err(f"Unbekannte Aktion: {action}")
