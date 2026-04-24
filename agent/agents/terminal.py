import logging
import os
import subprocess
import shlex
from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from agent.state import AgentState
from agent.audit import log_action
from agent.llm import get_llm
from agent.protocol import Proto
from agent.utils import extract_llm_text

logger = logging.getLogger(__name__)

MAX_RETRIES = 2  # max. Korrektur-Versuche nach ungültigem Befehl

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

_SECRET_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USER_IDS",
    "TELEGRAM_CHAT_ID",
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
    "LANGCHAIN_API_KEY",
    "FABBOT_WA_TOKEN",
})

PROMPT = """Du bist ein spezialisierter Terminal-Agent auf einem Mac.

Deine Aufgabe: Analysiere die Anfrage und antworte mit einem einzigen, sicheren Shell-Befehl.
Antworte AUSSCHLIESSLICH mit dem Befehl oder UNSUPPORTED – kein Text, keine Erklaerung, kein Markdown, keine Backticks.

Erlaubte Befehle: ls, pwd, cat, head, tail, grep, df, du, top, ps, uname,
whoami, date, find, wc, sort, uniq, uptime, sw_vers, diskutil, system_profiler

Wichtige Regeln fuer bestimmte Befehle:
- Fuer Datumsabfragen IMMER dieses Format verwenden: date "+%d.%m.%Y, %H:%M Uhr"
  Beispiel-Output: 18.03.2026, 19:02 Uhr
- Fuer Festplattenplatz IMMER nur: df -h (NIEMALS mit Pfad-Argument wie /System)

Wenn die Anfrage keinen erlaubten Befehl erfordert, antworte mit: UNSUPPORTED
Jede andere Antwort ausser einem gueltigen Befehl oder UNSUPPORTED ist ein Fehler.
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
        safe_env = {k: v for k, v in os.environ.items() if k not in _SECRET_ENV_VARS}
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=str(pathlib.Path.home()),
            env=safe_env,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(kein Output)"
        if len(output) > TERMINAL_MAX_OUTPUT:
            output = output[:TERMINAL_MAX_OUTPUT] + "\n... (Output gekuerzt)"
        return output
    except subprocess.TimeoutExpired:
        return f"Timeout nach {TIMEOUT_SECONDS}s."
    except Exception as e:
        return f"Fehler: {e}"


def _extract_command(response_content) -> str:
    content = extract_llm_text(response_content)
    command = content.strip().strip("`")
    if command.startswith("__CONFIRM_TERMINAL__:"):
        command = command[len("__CONFIRM_TERMINAL__:"):]
    return command


def _is_base_cmd_allowed(command: str) -> bool:
    try:
        first = os.path.basename(shlex.split(command)[0]) if command.split() else ""
    except ValueError:
        first = ""
    return first in ALLOWED_COMMANDS


async def terminal_agent(state: AgentState) -> AgentState:
    """Phase 88: async. Phase 99: last_agent_result im Return.
    Phase 146 (Issue #38): Self-Correction – bei ungültigem Befehl bis zu
    MAX_RETRIES Korrektur-Versuche vor dem HITL.
    """
    llm = get_llm()
    filtered = [m for m in state["messages"] if not (
        hasattr(m, "content") and isinstance(m.content, str)
        and m.content.startswith(("__MEMORY__:", "__CONFIRM_", "__SCREENSHOT__"))
    )]
    messages = [SystemMessage(content=PROMPT)] + filtered

    command = ""
    allowed = False
    reason = ""

    for attempt in range(1, MAX_RETRIES + 2):
        response = await llm.ainvoke(messages)
        command = _extract_command(response.content)

        if command == "UNSUPPORTED":
            msg = "Diese Aktion wird vom Terminal-Agent nicht unterstuetzt."
            return {
                "messages": [AIMessage(content=msg)],
                "last_agent_result": msg,
                "last_agent_name": "terminal_agent",
            }

        if not _is_base_cmd_allowed(command):
            if attempt <= MAX_RETRIES:
                logger.info(f"terminal_agent: Versuch {attempt} – Basisbefehl nicht erlaubt: {command!r}")
                messages = messages + [
                    AIMessage(content=command),
                    HumanMessage(content=(
                        f"Fehler: Der Befehl '{command.split()[0] if command.split() else command}' "
                        f"ist nicht in der Erlaubt-Liste. "
                        f"Erlaubte Befehle: {', '.join(sorted(ALLOWED_COMMANDS))}. "
                        f"Bitte antworte nur mit einem erlaubten Befehl oder UNSUPPORTED."
                    )),
                ]
                continue
            msg = "Diese Aktion wird vom Terminal-Agent nicht unterstuetzt."
            return {
                "messages": [AIMessage(content=msg)],
                "last_agent_result": msg,
                "last_agent_name": "terminal_agent",
            }

        allowed, reason = is_command_allowed(command)
        if allowed:
            break

        if attempt <= MAX_RETRIES:
            logger.info(f"terminal_agent: Versuch {attempt} – Sicherheitscheck fehlgeschlagen: {reason}")
            messages = messages + [
                AIMessage(content=command),
                HumanMessage(content=(
                    f"Fehler: Der Befehl wurde aus Sicherheitsgründen abgelehnt: {reason}. "
                    f"Bitte generiere einen korrekten, erlaubten Befehl oder antworte mit UNSUPPORTED."
                )),
            ]

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
