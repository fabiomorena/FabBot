import os
import re
import json
import subprocess
from datetime import datetime, timedelta, date
from pathlib import Path
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState
from agent.audit import log_action

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

CREDENTIALS_PATH = Path(__file__).parent.parent.parent / "credentials.json"
TOKEN_PATH = Path.home() / ".fabbot" / "google_token.json"

SCOPES = []  # Google Calendar via Apple Calendar Sync eingebunden


def _build_prompt() -> str:
    today = date.today().strftime("%d.%m.%Y")
    weekday = datetime.now().strftime("%A")
    return f"""Du bist ein spezialisierter Kalender-Agent. Heutiges Datum: {today} ({weekday})

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
    """Liest Events aus Apple Calendar via AppleScript."""
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
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            import logging
            logging.getLogger(__name__).error(f"AppleScript error: {result.stderr}")
            return []
        if not result.stdout.strip():
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
    except subprocess.TimeoutExpired:
        import logging
        logging.getLogger(__name__).error("AppleScript timeout")
        return []
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"AppleScript exception: {e}")
        return []


def _format_events(events: list[dict]) -> str:
    """Formatiert Events als lesbaren Text."""
    if not events:
        return "Keine Termine gefunden."

    # Sortieren nach Startzeit
    events.sort(key=lambda e: e.get("start", "").zfill(5))

    lines = []
    for e in events:
        start = e.get("start", "")
        lines.append(f"{start}  {e['title']}")

    return "\n".join(lines)


async def calendar_agent(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=_build_prompt())] + state["messages"]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    content = _extract_json(content)

    if not content or content == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Aktion wird vom Kalender-Agent nicht unterstuetzt.")]}

    try:
        parsed = json.loads(content)
        action = parsed.get("action")
        source = parsed.get("source", "apple")
        date_from = parsed.get("date_from", date.today().isoformat())
        date_to = parsed.get("date_to", date_from)
    except (json.JSONDecodeError, AttributeError):
        # Fallback: heute anzeigen
        action = "list_events"
        source = "apple"
        date_from = date.today().isoformat()
        date_to = date_from

    log_action("calendar_agent", action, f"{date_from} to {date_to}",
               state.get("telegram_chat_id"), status="executed")

    import logging
    logging.getLogger(__name__).info(f"Calendar query: action={action} source={source} from={date_from} to={date_to} apple_format={datetime.strptime(date_from, '%Y-%m-%d').strftime('%d.%m.%Y')}")

    if action == "list_events":
        events = _get_apple_events(date_from, date_to)
        formatted = _format_events(events)
        period = f"{date_from}" if date_from == date_to else f"{date_from} bis {date_to}"
        return {"messages": [AIMessage(content=f"Termine {period}:\n\n{formatted}")]}

    elif action == "create_event":
        # Create kommt in einem spaeteren Update mit Bestaetigung
        return {"messages": [AIMessage(content="Event-Erstellung kommt in Kuerze.")]}

    else:
        return {"messages": [AIMessage(content=f"Unbekannte Aktion: {action}")]}