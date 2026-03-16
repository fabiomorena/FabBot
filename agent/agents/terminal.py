import os
import subprocess
import shlex
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, AIMessage
from agent.state import AgentState
from agent.audit import log_action

llm = ChatAnthropic(
    model="claude-sonnet-4-20250514",
    api_key=os.getenv("ANTHROPIC_API_KEY"),
)

ALLOWED_COMMANDS = {
    "ls", "pwd", "echo", "cat", "head", "tail", "grep",
    "df", "du", "top", "ps", "uname", "whoami", "date",
    "find", "wc", "sort", "uniq", "uptime", "sw_vers",
    "diskutil", "system_profiler",
}

# Argumente die niemals erlaubt sind
FORBIDDEN_ARGS = {
    "--exec", "-exec", "--delete", "-delete",
    "/etc/passwd", "/etc/shadow", "/etc/sudoers",
    "~/.ssh", ".ssh/id_rsa", ".ssh/id_ed25519",
    ".ssh/authorized_keys", ".ssh/config",
    "/private/etc", "/System", "/Library/LaunchDaemons",
    ".fabbot/local_api_token", "local_api_token",
    ".env", "id_rsa", "id_ed25519",
}

# Pfade die niemals als Argument übergeben werden dürfen
FORBIDDEN_PATH_PREFIXES = (
    "/etc/",
    "/private/etc/",
    "/System/",
    "/Library/LaunchDaemons/",
    "/Library/LaunchAgents/",
)

# Erlaubte system_profiler Datatypes (alle anderen sind zu sensitiv)
ALLOWED_SYSTEM_PROFILER_TYPES = {
    "SPHardwareDataType",
    "SPSoftwareDataType",
    "SPStorageDataType",
    "SPMemoryDataType",
    "SPDisplaysDataType",
}

TIMEOUT_SECONDS = 15

PROMPT = """Du bist ein spezialisierter Terminal-Agent auf einem Mac.

Deine Aufgabe: Analysiere die Anfrage und antworte mit einem einzigen, sicheren Shell-Befehl.
Antworte NUR mit dem Befehl - keine Erklaerung, kein Markdown, keine Backticks.

Erlaubte Befehle: ls, pwd, echo, cat, head, tail, grep, df, du, top, ps, uname,
whoami, date, find, wc, sort, uniq, uptime, sw_vers, diskutil, system_profiler

Wenn die Anfrage keinen erlaubten Befehl erfordert, antworte mit: UNSUPPORTED
"""


def is_command_allowed(command: str) -> tuple[bool, str]:
    """Prueft ob der Befehl auf der Allowlist steht und keine gefaehrlichen Argumente enthaelt."""
    try:
        parts = shlex.split(command.strip())
    except ValueError as e:
        return False, f"Ungueltige Befehlssyntax: {e}"

    if not parts:
        return False, "Leerer Befehl."

    base_cmd = os.path.basename(parts[0])
    if base_cmd not in ALLOWED_COMMANDS:
        return False, f"Befehl `{base_cmd}` ist nicht erlaubt."

    # Shell-Operatoren blockieren
    forbidden_chars = [";", "&&", "||", "|", ">", "<", "`", "$(", "\\"]
    for char in forbidden_chars:
        if char in command:
            return False, f"Operator `{char}` ist nicht erlaubt."

    args = parts[1:]
    args_str = " ".join(args).lower()

    # Gefaehrliche Argumente blockieren
    for forbidden in FORBIDDEN_ARGS:
        if forbidden.lower() in args_str:
            return False, f"Argument `{forbidden}` ist nicht erlaubt."

    # Path-Traversal in Argumenten blockieren
    for part in args:
        if ".." in part:
            return False, "Path-Traversal (..) in Argumenten nicht erlaubt."

    # Verbotene Pfad-Prefixe prüfen
    for part in args:
        expanded = os.path.expanduser(part)
        for prefix in FORBIDDEN_PATH_PREFIXES:
            if expanded.startswith(prefix):
                return False, f"Zugriff auf `{prefix}` ist nicht erlaubt."

    # system_profiler: nur erlaubte Datatypes
    if base_cmd == "system_profiler":
        if not args:
            return False, "system_profiler benötigt einen Datatype-Parameter."
        if args[0] not in ALLOWED_SYSTEM_PROFILER_TYPES:
            allowed = ", ".join(sorted(ALLOWED_SYSTEM_PROFILER_TYPES))
            return False, f"system_profiler Datatype nicht erlaubt. Erlaubt: {allowed}"

    # find: Root-Suche und sensitive Verzeichnisse blockieren
    if base_cmd == "find":
        if args:
            search_path = os.path.expanduser(args[0])
            blocked_find_paths = ("/", "/etc", "/private", "/System", "/Library",
                                  os.path.expanduser("~/.ssh"),
                                  os.path.expanduser("~/.fabbot"))
            for blocked in blocked_find_paths:
                if search_path == blocked or search_path.startswith(blocked + "/"):
                    return False, f"find in `{args[0]}` ist nicht erlaubt."

    # cat/head/tail: sensitive Dateien blockieren
    if base_cmd in ("cat", "head", "tail"):
        for part in args:
            expanded = os.path.expanduser(part)
            blocked_files = (
                os.path.expanduser("~/.ssh/"),
                os.path.expanduser("~/.fabbot/local_api_token"),
                os.path.expanduser("~/.fabbot/audit.log"),
            )
            # Audit log darf nur via /auditlog Command gelesen werden, nicht via cat
            for blocked in blocked_files:
                if expanded.startswith(blocked) or expanded == blocked.rstrip("/"):
                    return False, f"Zugriff auf `{part}` ist nicht erlaubt."

    return True, command


def execute_command(command: str) -> str:
    """Fuehrt einen Befehl sicher aus und gibt den Output zurueck."""
    try:
        parts = shlex.split(command.strip())
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            # Kein Shell=True – verhindert Shell-Injection
        )
        output = result.stdout.strip() or result.stderr.strip() or "(kein Output)"
        if len(output) > 3000:
            output = output[:3000] + "\n... (Output gekuerzt)"
        return output
    except subprocess.TimeoutExpired:
        return f"Timeout nach {TIMEOUT_SECONDS}s."
    except Exception as e:
        return f"Fehler: {e}"


def terminal_agent(state: AgentState) -> AgentState:
    messages = [SystemMessage(content=PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    command = content.strip().strip("`")

    if command == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Aktion wird vom Terminal-Agent nicht unterstuetzt.")]}

    allowed, reason = is_command_allowed(command)
    if not allowed:
        log_action("terminal_agent", command[:200], reason,
                   state.get("telegram_chat_id"), status="blocked")
        return {"messages": [AIMessage(content=f"Blockiert: {reason}")]}

    return {
        "messages": [AIMessage(content=f"__CONFIRM_TERMINAL__:{command}")],
        "next_agent": None,
    }


def terminal_agent_execute(command: str, chat_id: int) -> str:
    """Wird nach Benutzerbestaetigung aufgerufen.
    Re-validiert den Befehl direkt vor der Ausfuehrung (TOCTOU-Schutz).
    """
    # Re-Validierung direkt vor Ausfuehrung
    allowed, reason = is_command_allowed(command)
    if not allowed:
        log_action("terminal_agent", command[:200], f"toctou-blocked: {reason}", chat_id, status="blocked")
        return f"Blockiert (Re-Validierung): {reason}"

    # Nur Befehl loggen, nie Output (koennte sensible Daten enthalten)
    log_action("terminal_agent", command[:200], "executing", chat_id, status="confirmed")
    output = execute_command(command)
    log_action("terminal_agent", command[:200], f"done, {len(output)}b output", chat_id, status="executed")
    return output