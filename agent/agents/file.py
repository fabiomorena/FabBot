import os
import re
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
    Path("/Volumes/McAir SSD/fmorena"),
    Path("/Users/fmorena"),
]

MAX_FILE_SIZE_BYTES = 1_000_000

PROMPT = """Du bist ein spezialisierter File-Agent auf einem Mac.

Analysiere die Anfrage und antworte NUR mit reinem JSON ohne Markdown-Formatierung:
{"action": "read|list|write", "path": "/absoluter/pfad", "content": "nur bei write"}

Wichtige Pfade auf diesem Mac:
- Downloads: /Users/fmorena/Downloads
- Documents: /Users/fmorena/Documents
- Desktop: /Users/fmorena/Desktop
- PythonProject: /Volumes/McAir SSD/fmorena/PythonProject

Kein ```json, keine Erklaerung, nur das rohe JSON-Objekt.
Wenn nicht unterstuetzt: UNSUPPORTED
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def is_path_allowed(path: Path) -> tuple[bool, str]:
    """Prueft ob der Pfad in einem erlaubten Verzeichnis liegt.
    Loest Symlinks auf um TOCTOU-Angriffe zu verhindern.
    """
    try:
        # resolve() loest Symlinks auf – verhindert TOCTOU via Symlink-Swap
        resolved = path.resolve()
    except Exception:
        return False, "Pfad konnte nicht aufgeloest werden."

    # Sicherheitscheck: Keine Path-Traversal-Muster
    if ".." in path.parts:
        return False, "Path-Traversal nicht erlaubt."

    for base in ALLOWED_BASE_PATHS:
        try:
            resolved.relative_to(base.resolve())
            return True, str(resolved)
        except ValueError:
            continue
    return False, f"Pfad nicht erlaubt: {resolved}"


def file_agent(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    content = _extract_json(content)

    if content == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Aktion wird vom File-Agent nicht unterstuetzt.")]}

    try:
        parsed = json.loads(content)
        action = parsed.get("action")
        path_str = parsed.get("path", "")
        file_content = parsed.get("content", "")
    except (json.JSONDecodeError, AttributeError) as e:
        return {"messages": [AIMessage(content=f"Fehler beim Parsen: {e}\nAntwort war: {content[:200]}")]}

    if not action or not path_str:
        return {"messages": [AIMessage(content="Ungueltige Anfrage: action oder path fehlt.")]}

    path = Path(path_str)
    allowed, reason = is_path_allowed(path)
    if not allowed:
        # Nur Metadaten loggen, niemals file_content
        log_action("file_agent", action, f"blocked: {reason}", state.get("telegram_chat_id"), status="blocked")
        return {"messages": [AIMessage(content=f"Blockiert: {reason}")]}

    if action == "list":
        return _list_dir(path, state)
    elif action == "read":
        return _read_file(path, state)
    elif action == "write":
        # Dateiinhalt NICHT in Bestaetigungsnachricht an Telegram schicken
        preview = f"{len(file_content)} Zeichen" if file_content else "leer"
        return {
            "messages": [AIMessage(content=f"__CONFIRM_FILE_WRITE__:{path}::{file_content}")],
            "next_agent": None,
            "_confirm_display": f"Schreibe {preview} nach: {path}",
        }
    else:
        return {"messages": [AIMessage(content=f"Unbekannte Aktion: {action}")]}


def _list_dir(path: Path, state: AgentState) -> AgentState:
    try:
        if not path.exists():
            return {"messages": [AIMessage(content=f"Verzeichnis nicht gefunden: {path}")]}
        if not path.is_dir():
            return {"messages": [AIMessage(content=f"Kein Verzeichnis: {path}")]}
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = []
        for e in entries[:50]:
            prefix = "Datei" if e.is_file() else "Ordner"
            lines.append(f"[{prefix}] {e.name}")
        result = f"Inhalt von {path}:\n\n" + "\n".join(lines)
        if len(entries) > 50:
            result += f"\n... und {len(entries) - 50} weitere"
        # Nur Pfad loggen, nie Dateiinhalt
        log_action("file_agent", "list", str(path), state.get("telegram_chat_id"), status="executed")
        return {"messages": [AIMessage(content=result)]}
    except PermissionError:
        return {"messages": [AIMessage(content=f"Kein Zugriff auf: {path}")]}


def _read_file(path: Path, state: AgentState) -> AgentState:
    try:
        if not path.exists():
            return {"messages": [AIMessage(content=f"Datei nicht gefunden: {path}")]}
        if not path.is_file():
            return {"messages": [AIMessage(content=f"Kein File: {path}")]}
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            return {"messages": [AIMessage(content="Datei zu gross (max. 1 MB).")]}
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > 3000:
            text = text[:3000] + "\n... (Inhalt gekuerzt)"
        # Nur Pfad und Groesse loggen, nie Inhalt
        log_action("file_agent", "read", f"path={path} size={path.stat().st_size}b",
                   state.get("telegram_chat_id"), status="executed")
        return {"messages": [AIMessage(content=f"Inhalt von {path.name}:\n\n{text}")]}
    except PermissionError:
        return {"messages": [AIMessage(content=f"Kein Zugriff auf: {path}")]}


def file_agent_write(path: Path, content: str, chat_id: int) -> str:
    """Wird nach Benutzerbestaetigung aufgerufen.
    Re-validiert den Pfad direkt vor der Ausfuehrung (TOCTOU-Schutz).
    """
    # TOCTOU-Fix: Pfad nochmal validieren direkt vor dem Schreiben
    allowed, reason = is_path_allowed(path)
    if not allowed:
        log_action("file_agent", "write", f"toctou-blocked: {reason}", chat_id, status="blocked")
        return f"Blockiert (Re-Validierung): {reason}"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        # Nur Pfad und Groesse loggen, nie Inhalt
        log_action("file_agent", "write", f"path={path} size={len(content)}b", chat_id, status="executed")
        return f"Datei gespeichert: {path.name}"
    except PermissionError:
        return f"Kein Zugriff auf: {path}"
    except Exception as e:
        return f"Fehler beim Schreiben: {e}"

