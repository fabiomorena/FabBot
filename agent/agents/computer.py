import json
import re
import base64
import subprocess
from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.audit import log_action
from agent.llm import get_llm
from agent.protocol import Proto

SCREENSHOT_PATH = Path.home() / ".fabbot" / "screenshot.png"
SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

TYPEWRITE_MAX_CHARS = 500
TYPEWRITE_ALLOWED_PATTERN = re.compile(r'^[\x20-\x7E\s]+$')
APP_NAME_PATTERN = re.compile(r'^[A-Za-z0-9\s\-\.]+$')
APP_NAME_MAX_LENGTH = 64

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
    try:
        import pyautogui
        screenshot = pyautogui.screenshot()
        screenshot.save(str(SCREENSHOT_PATH))
        with open(SCREENSHOT_PATH, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def _screenshot_to_telegram_bytes() -> bytes | None:
    try:
        if SCREENSHOT_PATH.exists():
            return SCREENSHOT_PATH.read_bytes()
        return None
    except Exception:
        return None


def _validate_typewrite_text(text: str) -> tuple[bool, str]:
    if not text:
        return False, "Leerer Text."
    if len(text) > TYPEWRITE_MAX_CHARS:
        return False, f"Text zu lang (max. {TYPEWRITE_MAX_CHARS} Zeichen)."
    if not TYPEWRITE_ALLOWED_PATTERN.match(text):
        return False, "Text enthaelt unerlaubte Steuerzeichen."
    return True, text


def _validate_app_name(app: str) -> tuple[bool, str]:
    if not app or not app.strip():
        return False, "Leerer App-Name."
    app = app.strip()
    if len(app) > APP_NAME_MAX_LENGTH:
        return False, f"App-Name zu lang (max. {APP_NAME_MAX_LENGTH} Zeichen)."
    if not APP_NAME_PATTERN.match(app):
        return False, "App-Name enthaelt unerlaubte Zeichen."
    return True, app


async def computer_agent(state: AgentState) -> AgentState:
    """Phase 88: ainvoke. Phase 99: last_agent_result in allen Returns."""
    llm = get_llm()
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)

    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content).strip()

    def _err(msg: str) -> AgentState:
        return {"messages": [AIMessage(content=msg)], "last_agent_result": msg, "last_agent_name": "computer_agent"}

    if content == "UNSUPPORTED":
        return _err("Diese Aktion wird vom Computer-Agent nicht unterstuetzt.")

    try:
        parsed = json.loads(content)
        action = parsed.get("action")
    except (json.JSONDecodeError, AttributeError) as e:
        return _err(f"Fehler beim Parsen: {e}")

    if action == "screenshot":
        log_action("computer_agent", "screenshot", "taking screenshot",
                   state.get("telegram_chat_id"), status="executed")
        img_b64 = _take_screenshot()
        if not img_b64:
            return _err("Fehler beim Erstellen des Screenshots.")

        analysis_response = await llm.ainvoke([
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
            "messages": [AIMessage(content=f"{Proto.SCREENSHOT}{analysis.strip()}")],
            "next_agent": None,
            "last_agent_result": None,
            "last_agent_name": "computer_agent",
        }

    elif action == "click":
        x = parsed.get("x", 0)
        y = parsed.get("y", 0)
        return {
            "messages": [AIMessage(content=f"{Proto.CONFIRM_COMPUTER}click:{x}:{y}:")],
            "next_agent": None,
            "last_agent_result": None,
            "last_agent_name": "computer_agent",
        }

    elif action == "type":
        text = parsed.get("text", "")
        valid, reason = _validate_typewrite_text(text)
        if not valid:
            return _err(f"Ungültiger Text: {reason}")
        return {
            "messages": [AIMessage(content=f"{Proto.CONFIRM_COMPUTER}type:0:0:{text}")],
            "next_agent": None,
            "last_agent_result": None,
            "last_agent_name": "computer_agent",
        }

    elif action == "open_app":
        app = parsed.get("app", "")
        valid, clean_app = _validate_app_name(app)
        if not valid:
            return _err(f"Ungültiger App-Name: {clean_app}")
        return {
            "messages": [AIMessage(content=f"{Proto.CONFIRM_COMPUTER}open_app:0:0:{clean_app}")],
            "next_agent": None,
            "last_agent_result": None,
            "last_agent_name": "computer_agent",
        }

    else:
        return _err(f"Unbekannte Aktion: {action}")


def computer_agent_execute(action: str, x: int, y: int, text: str, chat_id: int) -> str:
    try:
        import pyautogui
        pyautogui.FAILSAFE = True

        if action == "click":
            pyautogui.click(x, y)
            log_action("computer_agent", "click", f"x={x} y={y}", chat_id, status="executed")
            return f"Geklickt auf ({x}, {y})."

        elif action == "type":
            valid, reason = _validate_typewrite_text(text)
            if not valid:
                return f"Blockiert: {reason}"
            pyautogui.typewrite(text, interval=0.05)
            log_action("computer_agent", "type", f"len={len(text)}", chat_id, status="executed")
            return f"Text getippt: {text[:50]}"

        elif action == "open_app":
            valid, clean_app = _validate_app_name(text)
            if not valid:
                return f"Blockiert: {clean_app}"
            subprocess.run(["open", "-a", clean_app], check=True)
            log_action("computer_agent", "open_app", clean_app, chat_id, status="executed")
            return f"App geoeffnet: {clean_app}"

        else:
            return f"Unbekannte Aktion: {action}"

    except Exception as e:
        return f"Fehler: {e}"
