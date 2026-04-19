import os
import subprocess
import shlex
from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState
from agent.audit import log_action
from agent.llm import get_llm
from agent.protocol import Proto

ALLOWED_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "grep",
    "df", "du", "top", "ps", "uname", "whoami", "date",
    "find", "wc", "sort", "uniq", "uptime", "sw_vers",
    "diskutil", "system_profiler",
}

FORBIDDEN_ARGS = {
    "--exec", "-exec", "--delete", "-delete",
    "/etc/passwd", "/etc/shadow", "/etc/sudoers",
    "~/.ssh", ".ssh/id_rsa", ".ssh/id_ed25519",
    ".ssh/authorized_keys", ".ssh/config",
    "/private/etc", "/Library/LaunchDaemons",
    ".fabbot/local_api_token", "local_api_token",
    ".env", "id_rsa", "id_ed25519",
}

FORBIDDEN_PATH_PREFIXES = (
    "/etc/",
    "/private/etc/",
    "/Library/LaunchDaemons/",
    "/Library/LaunchAgents/",
)

ALLOWED_SYSTEM_PROFILER_TYPES = {
    "SPHardwareDataType",
    "SPSoftwareDataType",
    "SPStorageDataType",
    "SPMemoryDataType",
    "SPDisplaysDataType",
}

TIMEOUT_SECONDS = 15
TERMINAL_MAX_OUTPUT = 3000

PROMPT = """Du bist ein spezialisierter Terminal-Agent auf einem Mac.

Deine Aufgabe: Analysiere die Anfrage und antworte mit einem einzigen, sicheren Shell-Befehl.
Antworte NUR mit dem Befehl - keine Erklaerung, kein Markdown, keine Backticks.

Erlaubte Befehle: ls, pwd, cat, head, tail, grep, df, du, top, ps, uname,
whoami, date, find, wc, sort, uniq, uptime, sw_vers, diskutil, system_profiler

Wichtige Regeln fuer bestimmte Befehle:
- Fuer Datumsabfragen IMMER dieses Format verwenden: date "+%d.%m.%Y, %H:%M Uhr"
  Beispiel-Output: 18.03.2026, 19:02 Uhr
- Fuer Festplattenplatz IMMER nur: df -h (NIEMALS mit Pfad-Argument wie /System)

Wenn die Anfrage keinen erlaubten Befehl erfordert, antworte mit: UNSUPPORTED
"""


def is_command_allowed(command: str) -> tuple[bool, str]:
    try:
        parts = shlex.split(command.strip())
    except ValueError as e:
        return False, f"Ungueltige Befehlssyntax: {e}"

    if not parts:
        return False, "Leerer Befehl."

    base_cmd = os.path.basename(parts[0])
    if base_cmd not in ALLOWED_COMMANDS:
        return False, f"Befehl `{base_cmd}` ist nicht erlaubt."

    forbidden_chars = [";", "&&", "||", "|", ">", "<", "`", "$(", "\\"]
    for char in forbidden_chars:
        if char in command:
            return False, f"Operator `{char}` ist nicht erlaubt."

    args = parts[1:]
    args_lower = [a.lower() for a in args]

    for forbidden in FORBIDDEN_ARGS:
        if forbidden.lower() in args_lower:
            return False, f"Argument `{forbidden}` ist nicht erlaubt."

    for part in args:
        if ".." in Path(os.path.expanduser(part)).parts:
            return False, "Path-Traversal (..) in Argumenten nicht erlaubt."

    for part in args:
        expanded = os.path.expanduser(part)
        for prefix in FORBIDDEN_PATH_PREFIXES:
            if expanded.startswith(prefix):
                return False, f"Zugriff auf `{prefix}` ist nicht erlaubt."

    if base_cmd == "system_profiler":
        if not args:
            return False, "system_profiler benoetigt einen Datatype-Parameter."
        if args[0] not in ALLOWED_SYSTEM_PROFILER_TYPES:
            allowed = ", ".join(sorted(ALLOWED_SYSTEM_PROFILER_TYPES))
            return False, f"system_profiler Datatype nicht erlaubt. Erlaubt: {allowed}"

    if base_cmd == "df":
        return True, command
    if base_cmd == "find":
        if args:
            search_path = os.path.expanduser(args[0])
            blocked_find_paths = ("/", "/etc", "/private", "/System", "/Library",
                                  os.path.expanduser("~/.ssh"),
                                  os.path.expanduser("~/.fabbot"))
            for blocked in blocked_find_paths:
                if search_path == blocked or search_path.startswith(blocked + "/"):
                    return False, f"find in `{args[0]}` ist nicht erlaubt."

    if base_cmd in ("cat", "head", "tail"):
        for part in args:
            expanded = os.path.expanduser(part)
            blocked_files = (
                os.path.expanduser("~/.ssh/"),
                os.path.expanduser("~/.fabbot/local_api_token"),
                os.path.expanduser("~/.fabbot/audit.log"),
            )
            for blocked in blocked_files:
                if expanded.startswith(blocked) or expanded == blocked.rstrip("/"):
                    return False, f"Zugriff auf `{part}` ist nicht erlaubt."

    return True, command


def sanitize_command(command: str) -> str:
    try:
        parts = shlex.split(command.strip())
    except ValueError:
        return command

    if not parts:
        return command

    base_cmd = os.path.basename(parts[0])
    args = parts[1:]

    if base_cmd == "df":
        clean_args = [a for a in args if not a.startswith("/")]
        return "df " + " ".join(clean_args) if clean_args else "df -h"

    return command


def execute_command(command: str) -> str:
    try:
        parts = shlex.split(command.strip())
        import pathlib
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=str(pathlib.Path.home()),
        )
        output = result.stdout.strip() or result.stderr.strip() or "(kein Output)"
        if len(output) > TERMINAL_MAX_OUTPUT:
            output = output[:TERMINAL_MAX_OUTPUT] + "\n... (Output gekuerzt)"
        return output
    except subprocess.TimeoutExpired:
        return f"Timeout nach {TIMEOUT_SECONDS}s."
    except Exception as e:
        return f"Fehler: {e}"


async def terminal_agent(state: AgentState) -> AgentState:
    """Phase 88: async. Phase 99: last_agent_result im Return."""
    llm = get_llm()
    filtered = [m for m in state["messages"] if not (
        hasattr(m, "content") and isinstance(m.content, str)
        and m.content.startswith(("__MEMORY__:", "__CONFIRM_", "__SCREENSHOT__"))
    )]
    messages = [SystemMessage(content=PROMPT)] + filtered
    response = await llm.ainvoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    command = content.strip().strip("`")
    if command.startswith("__CONFIRM_TERMINAL__:"):
        command = command[len("__CONFIRM_TERMINAL__:"):]

    if command == "UNSUPPORTED":
        msg = "Diese Aktion wird vom Terminal-Agent nicht unterstuetzt."
        return {
            "messages": [AIMessage(content=msg)],
            "last_agent_result": msg,
            "last_agent_name": "terminal_agent",
        }

    try:
        _first = os.path.basename(shlex.split(command)[0]) if command.split() else ""
    except ValueError:
        _first = ""
    if _first not in ALLOWED_COMMANDS:
        return {
            "messages": [AIMessage(content=command)],
            "last_agent_result": command,
            "last_agent_name": "terminal_agent",
        }

    allowed, reason = is_command_allowed(command)
    if not allowed:
        log_action("terminal_agent", command[:200], reason,
                   state.get("telegram_chat_id"), status="blocked")
        msg = f"Blockiert: {reason}"
        return {
            "messages": [AIMessage(content=msg)],
            "last_agent_result": msg,
            "last_agent_name": "terminal_agent",
        }

    # HITL – kein last_agent_result (Ergebnis kommt erst nach Bestätigung)
    return {
        "messages": [AIMessage(content=f"{Proto.CONFIRM_TERMINAL}{command}")],
        "next_agent": None,
        "last_agent_result": None,
        "last_agent_name": "terminal_agent",
    }


def terminal_agent_execute(command: str, chat_id: int) -> str:
    allowed, reason = is_command_allowed(command)
    if not allowed:
        log_action("terminal_agent", command[:200], f"toctou-blocked: {reason}", chat_id, status="blocked")
        return f"Blockiert (Re-Validierung): {reason}"

    command = sanitize_command(command)
    log_action("terminal_agent", command[:200], "executing", chat_id, status="confirmed")
    output = execute_command(command)
    log_action("terminal_agent", command[:200], f"done, {len(output)}b output", chat_id, status="executed")
    return output


def _build_terminal_prompt() -> str:
    """Ph.98 Kompatibilitäts-Alias."""
    from agent.utils import get_current_datetime
    return f"[Aktuelles Datum/Uhrzeit: {get_current_datetime()}]"
