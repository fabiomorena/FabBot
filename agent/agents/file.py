import os
import re
import json
from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState
from agent.audit import log_action
from agent.llm import get_llm
from agent.protocol import Proto

# Phase 88: Maximale Verzeichnistiefe ab Allowlist-Basis
# Verhindert beliebig tiefe LLM-generierte Verzeichnisbäume via mkdir(parents=True)
MAX_PATH_DEPTH = 5


def _build_allowed_paths() -> list[Path]:
    paths = [
        Path.home() / "Downloads",
        Path.home() / "Documents",
        Path.home() / "Desktop",
        Path.home() / "Projects",
        Path.home() / "PythonProject",
    ]
    extra = os.getenv("FABBOT_EXTRA_PATHS", "")
    for ep in extra.split(":") if extra else []:
        ep = ep.strip()
        if not ep:
            continue
        extra_path = Path(ep)
        if not extra_path.exists():
            import logging
            logging.getLogger(__name__).warning(f"FABBOT_EXTRA_PATHS: Pfad existiert nicht – ignoriert: {ep}")
            continue
        if any(str(extra_path.resolve()).startswith(str(b.resolve())) for b in [
            Path.home() / ".ssh",
            Path.home() / ".fabbot",
            Path.home() / "Library",
            Path("/etc"),
            Path("/private"),
        ]):
            import logging
            logging.getLogger(__name__).warning(f"FABBOT_EXTRA_PATHS: Blockierter Pfad – ignoriert: {ep}")
            continue
        paths.append(extra_path)
    return paths


ALLOWED_BASE_PATHS = _build_allowed_paths()

EXPLICITLY_BLOCKED_PATHS = [
    Path.home() / ".ssh",
    Path.home() / ".fabbot",
    Path.home() / ".env",
    Path.home() / ".zshrc",
    Path.home() / ".bashrc",
    Path.home() / ".bash_profile",
    Path.home() / ".zprofile",
    Path.home() / "Library",
]

MAX_FILE_SIZE_BYTES = 1_000_000

PROMPT = """Du bist ein spezialisierter File-Agent auf einem Mac.

Analysiere die Anfrage und antworte NUR mit reinem JSON ohne Markdown-Formatierung:
{"action": "read|list|write", "path": "/absoluter/pfad", "content": "nur bei write"}

Wichtige Pfade auf diesem Mac:
- Downloads: ~/Downloads
- Documents: ~/Documents
- Desktop: ~/Desktop
- PythonProject: ~/PythonProject

Kein ```json, keine Erklaerung, nur das rohe JSON-Objekt.
Wenn nicht unterstuetzt: UNSUPPORTED
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def is_path_allowed(path: Path) -> tuple[bool, str]:
    try:
        resolved = path.resolve()
    except Exception:
        return False, "Pfad konnte nicht aufgeloest werden."

    if ".." in path.parts:
        return False, "Path-Traversal nicht erlaubt."

    # Phase 88: Symlink-Schutz
    # Wenn der Pfad ein Symlink ist, muss das aufgelöste Ziel ebenfalls in der Allowlist liegen.
    # Verhindert: ~/Downloads/evil_link -> ~/.ssh/id_rsa (Symlink in erlaubtem Ordner)
    # resolve() folgt Symlinks – deshalb muss das _Ziel_ geprüft werden, nicht nur der Pfad.
    if path.is_symlink():
        symlink_target_allowed = False
        for base in ALLOWED_BASE_PATHS:
            try:
                resolved.relative_to(base.resolve())
                symlink_target_allowed = True
                break
            except ValueError:
                continue
        if not symlink_target_allowed:
            return False, f"Symlink-Ziel liegt außerhalb der erlaubten Pfade: {resolved}"

    for blocked in EXPLICITLY_BLOCKED_PATHS:
        try:
            resolved.relative_to(blocked.resolve())
            return False, f"Zugriff auf `{blocked}` ist nicht erlaubt."
        except ValueError:
            continue

    for base in ALLOWED_BASE_PATHS:
        try:
            relative = resolved.relative_to(base.resolve())
            # Phase 88: Tiefenbegrenzung
            # Verhindert LLM-generierte Pfade wie ~/Documents/a/b/c/d/e/f/g/file.txt
            # die mkdir(parents=True) mit beliebig vielen Ebenen auslösen könnten.
            if len(relative.parts) > MAX_PATH_DEPTH:
                return False, (
                    f"Pfad zu tief verschachtelt (max. {MAX_PATH_DEPTH} Ebenen ab Basis, "
                    f"gefunden: {len(relative.parts)})."
                )
            return True, str(resolved)
        except ValueError:
            continue

    return False, f"Pfad nicht erlaubt: {resolved}"


async def file_agent(state: AgentState) -> AgentState:
    """Phase 88: async – verhindert Event-Loop-Blockierung durch sync llm.invoke()."""
    llm = get_llm()
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = await llm.ainvoke(messages)  # Phase 88: ainvoke statt invoke
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    content = _extract_json(content)

    if content == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Aktion wird vom File-Agent nicht unterstuetzt.")]}

    # Phase 75: Natürliche Sprache abfangen
    if not content.strip().startswith("{"):
        return {"messages": [AIMessage(content=content.strip())]}

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
        log_action("file_agent", action, f"blocked: {reason}", state.get("telegram_chat_id"), status="blocked")
        return {"messages": [AIMessage(content=f"Blockiert: {reason}")]}

    if action == "list":
        return _list_dir(path, state)
    elif action == "read":
        return _read_file(path, state)
    elif action == "write":
        preview = f"{len(file_content)} Zeichen" if file_content else "leer"
        return {
            "messages": [AIMessage(content=f"{Proto.CONFIRM_FILE_WRITE}{path}::{file_content}")],
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
        log_action("file_agent", "read", f"path={path} size={path.stat().st_size}b",
                   state.get("telegram_chat_id"), status="executed")
        return {"messages": [AIMessage(content=f"Inhalt von {path.name}:\n\n{text}")]}
    except PermissionError:
        return {"messages": [AIMessage(content=f"Kein Zugriff auf: {path}")]}


def file_agent_write(path: Path, content: str, chat_id: int) -> str:
    """Wird nach Benutzerbestaetigung aufgerufen. Re-validiert vor Ausfuehrung (TOCTOU-Schutz).
    Phase 88: Symlink- und Tiefencheck greifen über is_path_allowed().
    """
    allowed, reason = is_path_allowed(path)
    if not allowed:
        log_action("file_agent", "write", f"toctou-blocked: {reason}", chat_id, status="blocked")
        return f"Blockiert (Re-Validierung): {reason}"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        log_action("file_agent", "write", f"path={path} size={len(content)}b", chat_id, status="executed")
        return f"Datei gespeichert: {path.name}"
    except PermissionError:
        return f"Kein Zugriff auf: {path}"
    except Exception as e:
        return f"Fehler beim Schreiben: {e}"
