import os
import base64
import subprocess
from pathlib import Path
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.audit import log_action

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

SCREENSHOT_PATH = Path.home() / ".fabbot" / "screenshot.png"
SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

PROMPT = """Du bist ein spezialisierter Computer-Use-Agent auf einem Mac.

Du kannst folgende Aktionen ausfuehren:
- screenshot: Einen Screenshot machen und analysieren
- click: An einer Position klicken (x, y Koordinaten)
- type: Text tippen
- open_app: Eine App per Name oeffnen

Analysiere die Anfrage und antworte NUR mit JSON:
{
  "action": "screenshot|click|type|open_app",
  "x": 100,
  "y": 200,
  "text": "Text zum Tippen",
  "app": "App-Name"
}

Fuer 'screenshot': nur {"action": "screenshot"}
Fuer 'click': {"action": "click", "x": X, "y": Y}
Fuer 'type': {"action": "type", "text": "..."}
Fuer 'open_app': {"action": "open_app", "app": "Safari"}

Kein Markdown, kein Text – nur reines JSON.
Wenn nicht unterstuetzt: UNSUPPORTED
"""


def _take_screenshot() -> str | None:
    """Macht einen Screenshot und gibt den Base64-String zurueck."""
    try:
        import pyautogui
        screenshot = pyautogui.screenshot()
        screenshot.save(str(SCREENSHOT_PATH))
        with open(SCREENSHOT_PATH, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return None


def _screenshot_to_telegram_bytes() -> bytes | None:
    """Gibt Screenshot als Bytes fuer Telegram zurueck."""
    try:
        if SCREENSHOT_PATH.exists():
            return SCREENSHOT_PATH.read_bytes()
        return None
    except Exception:
        return None


async def computer_agent(state: AgentState) -> AgentState:
    import json
    import re

    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

    # JSON extrahieren
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content).strip()

    if content == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Aktion wird vom Computer-Agent nicht unterstuetzt.")]}

    try:
        parsed = json.loads(content)
        action = parsed.get("action")
    except (json.JSONDecodeError, AttributeError) as e:
        return {"messages": [AIMessage(content=f"Fehler beim Parsen: {e}")]}

    if action == "screenshot":
        log_action("computer_agent", "screenshot", "taking screenshot",
                   state.get("telegram_chat_id"), status="executed")
        img_b64 = _take_screenshot()
        if not img_b64:
            return {"messages": [AIMessage(content="Fehler beim Erstellen des Screenshots.")]}

        # Screenshot mit Claude analysieren
        analysis_response = llm.invoke([
            HumanMessage(content=[
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                },
                {"type": "text", "text": "Beschreibe kurz was auf dem Screenshot zu sehen ist."},
            ])
        ])
        analysis = analysis_response.content
        if isinstance(analysis, list):
            analysis = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in analysis)

        return {
            "messages": [AIMessage(content=f"__SCREENSHOT__:{analysis.strip()}")],
            "next_agent": None,
        }

    elif action == "click":
        x = parsed.get("x", 0)
        y = parsed.get("y", 0)
        return {
            "messages": [AIMessage(content=f"__CONFIRM_COMPUTER__:click:{x}:{y}:")],
            "next_agent": None,
        }

    elif action == "type":
        text = parsed.get("text", "")
        if not text:
            return {"messages": [AIMessage(content="Kein Text zum Tippen angegeben.")]}
        return {
            "messages": [AIMessage(content=f"__CONFIRM_COMPUTER__:type:0:0:{text}")],
            "next_agent": None,
        }

    elif action == "open_app":
        app = parsed.get("app", "")
        if not app:
            return {"messages": [AIMessage(content="Kein App-Name angegeben.")]}
        return {
            "messages": [AIMessage(content=f"__CONFIRM_COMPUTER__:open_app:0:0:{app}")],
            "next_agent": None,
        }

    else:
        return {"messages": [AIMessage(content=f"Unbekannte Aktion: {action}")]}


def computer_agent_execute(action: str, x: int, y: int, text: str, chat_id: int) -> str:
    """Wird nach Benutzerbestaetigung ausgefuehrt."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = True  # Maus in Ecke = Notaus

        if action == "click":
            pyautogui.click(x, y)
            log_action("computer_agent", "click", f"x={x} y={y}", chat_id, status="executed")
            return f"Geklickt auf ({x}, {y})."

        elif action == "type":
            pyautogui.typewrite(text, interval=0.05)
            log_action("computer_agent", "type", f"len={len(text)}", chat_id, status="executed")
            return f"Text getippt: {text[:50]}"

        elif action == "open_app":
            subprocess.run(["open", "-a", text], check=True)
            log_action("computer_agent", "open_app", text, chat_id, status="executed")
            return f"App geoeffnet: {text}"

        else:
            return f"Unbekannte Aktion: {action}"

    except Exception as e:
        return f"Fehler: {e}"
