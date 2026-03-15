import os
import json
from pathlib import Path
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState
from agent.audit import log_action

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

ALLOWED_BASE_PATHS = [
    Path.home() / "Downloads",
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Projects",
    Path.home() / "PythonProject",
]

MAX_FILE_SIZE_BYTES = 1_000_000

PROMPT = """Du bist ein spezialisierter File-Agent auf einem Mac.

Analysiere die Anfrage und antworte NUR mit JSON:
{"action": "read|list|write", "path": "/absoluter/pfad", "content": "nur bei write"}

Kein Markdown, keine Erklaerung. Wenn nicht unterstuetzt: UNSUPPORTED
"""


def is_path_allowed(path: Path) -> tuple[bool, str]:
    try:
        resolved = path.resolve()
    except Exception:
        return False, "Pfad konnte nicht aufgeloest werden."
    for base in ALLOWED_BASE_PATHS:
        try:
            resolved.relative_to(base.resolve())
            return True, str(resolved)
        except ValueError:
            continue
    allowed_str = ", ".join(str(p) for p in ALLOWED_BASE_PATHS)
    return False, f"Pfad nicht erlaubt. Erlaubte Verzeichnisse: {allowed_str}"


def file_agent(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    content = content.strip()

    if content == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Aktion wird vom File-Agent nicht unterstuetzt.")]}

    try:
        parsed = json.loads(content)
        action = parsed.get("action")
        path_str = parsed.get("path", "")
        file_content = parsed.get("content", "")
    except (json.JSONDecodeError, AttributeError):
        return {"messages": [AIMessage(content="Fehler: Ungueltige Antwort vom File-Agent.")]}

    path = Path(path_str)
    allowed, reason = is_path_allowed(path)
    if not allowed:
        log_action("file_agent", action, reason, state.get("telegram_chat_id"), status="blocked")
        return {"messages": [AIMessage(content=f"Blockiert: {reason}")]}

    if action == "list":
        return _list_dir(path, state)
    elif action == "read":
        return _read_file(path, state)
    elif action == "write":
        return {
            "messages": [AIMessage(content=f"__CONFIRM_FILE_WRITE__:{path}::{file_content[:200]}")],
            "next_agent": None,
        }
    else:
        return {"messages": [AIMessage(content=f"Unbekannte Aktion: {action}")]}


def _list_dir(path: Path, state: AgentState) -> AgentState:
    try:
        if not path.exists():
            return {"messages": [AIMessage(content=f"Verzeichnis nicht gefunden: {path}")]}
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = []
        for e in entries[:50]:
            prefix = "Datei" if e.is_file() else "Ordner"
            lines.append(f"[{prefix}] {e.name}")
        result = f"Inhalt von {path}:\n" + "\n".join(lines)
        if len(entries) > 50:
            result += f"\n... und {len(entries) - 50} weitere"
        log_action("file_agent", "list", str(path), state.get("telegram_chat_id"), status="executed")
        return {"messages": [AIMessage(content=result)]}
    except PermissionError:
        return {"messages": [AIMessage(content=f"Kein Zugriff auf: {path}")]}


def _read_file(path: Path, state: AgentState) -> AgentState:
    try:
        if not path.exists():
            return {"messages": [AIMessage(content=f"Datei nicht gefunden: {path}")]}
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            return {"messages": [AIMessage(content="Datei zu gross (max. 1 MB).")]}
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > 3000:
            text = text[:3000] + "\n... (Inhalt gekuerzt)"
        log_action("file_agent", "read", str(path), state.get("telegram_chat_id"), status="executed")
        return {"messages": [AIMessage(content=f"Inhalt von {path.name}:\n\n{text}")]}
    except PermissionError:
        return {"messages": [AIMessage(content=f"Kein Zugriff auf: {path}")]}


def file_agent_write(path: Path, content: str, chat_id: int) -> str:
    try:
        path.write_text(content, encoding="utf-8")
        log_action("file_agent", "write", str(path), chat_id, status="executed")
        return f"Datei gespeichert: {path.name}"
    except Exception as e:
        return f"Fehler beim Schreiben: {e}"
