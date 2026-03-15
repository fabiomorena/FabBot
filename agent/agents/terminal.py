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

# Nur diese Befehle sind erlaubt
ALLOWED_COMMANDS = {
    "ls", "pwd", "echo", "cat", "head", "tail", "grep",
    "df", "du", "top", "ps", "uname", "whoami", "date",
    "find", "wc", "sort", "uniq", "uptime", "sw_vers",
    "diskutil", "system_profiler",
}

TIMEOUT_SECONDS = 15

PROMPT = """Du bist ein spezialisierter Terminal-Agent auf einem Mac.

Deine Aufgabe: Analysiere die Anfrage und antworte mit einem einzigen, sicheren Shell-Befehl.
Antworte NUR mit dem Befehl - keine Erklarung, kein Markdown, keine Backticks.

Erlaubte Befehle: ls, pwd, echo, cat, head, tail, grep, df, du, top, ps, uname,
whoami, date, find, wc, sort, uniq, uptime, sw_vers, diskutil, system_profiler

Wenn die Anfrage keinen erlaubten Befehl erfordert, antworte mit: UNSUPPORTED
"""


def is_command_allowed(command: str) -> tuple[bool, str]:
    try:
        parts = shlex.split(command.strip())
    except ValueError:
        return False, "Ungultiger Befehl."

    if not parts:
        return False, "Leerer Befehl."

    base_cmd = os.path.basename(parts[0])

    if base_cmd not in ALLOWED_COMMANDS:
        return False, f"Befehl `{base_cmd}` ist nicht erlaubt."

    forbidden_chars = [";", "&&", "||", "|", ">", "<", "`", "$(", "\\"]
    for char in forbidden_chars:
        if char in command:
            return False, f"Operator `{char}` ist nicht erlaubt."

    return True, command


def execute_command(command: str) -> str:
    try:
        parts = shlex.split(command.strip())
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(kein Output)"
        if len(output) > 3000:
            output = output[:3000] + "\n... (Output gekurzt)"
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
    command = content.strip()

    if command == "UNSUPPORTED":
        return {"messages": [AIMessage(content="Diese Aktion wird vom Terminal-Agent nicht unterstuzt.")]}

    allowed, reason = is_command_allowed(command)
    if not allowed:
        log_action("terminal_agent", command, reason,
                   state.get("telegram_chat_id"), status="blocked")
        return {"messages": [AIMessage(content=f"Blockiert: {reason}")]}

    return {
        "messages": [AIMessage(content=f"__CONFIRM_TERMINAL__:{command}")],
        "next_agent": None,
    }


def terminal_agent_execute(command: str, chat_id: int) -> str:
    log_action("terminal_agent", command, command, chat_id, status="confirmed")
    output = execute_command(command)
    log_action("terminal_agent", command, f"output={output[:200]}", chat_id, status="executed")
    return output
