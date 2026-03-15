# FabBot – Personal Mac AI Assistant

A personal AI assistant that runs locally on macOS, controlled via Telegram and a native menubar app. Built with Claude (Anthropic), LangGraph, and a multi-agent architecture.

---

## Overview

FabBot lets you control your Mac using natural language – from anywhere, via Telegram. A supervisor agent analyzes incoming requests and routes them to the appropriate specialist agent.

```
You → Telegram → Security Guard → Supervisor → calendar_agent / terminal_agent / file_agent / ...
```

---

## Features

| Status | Feature |
|--------|---------|
| ✅ | Telegram bot interface |
| ✅ | User authentication (whitelist) |
| ✅ | Multi-agent supervisor routing |
| ✅ | Terminal – execute shell commands |
| ✅ | File – read, write, list files |
| ✅ | Security layer – prompt injection guard, audit log, human-in-the-loop |
| 🔜 | Computer Use – desktop control via Anthropic API |
| 🔜 | Web – search and fetch information |
| 🔜 | Calendar – read and create events |
| 🔜 | macOS menubar app |

---

## Architecture

```
FabBot/
├── main.py                  # Entrypoint
├── agent/
│   ├── supervisor.py        # Supervisor – routes to sub-agents
│   ├── state.py             # LangGraph AgentState
│   ├── security.py          # Prompt injection guard & input sanitization
│   ├── audit.py             # Tamper-evident audit log
│   └── agents/
│       ├── computer.py      # Desktop control (Computer Use API)
│       ├── terminal.py      # Shell command execution
│       ├── file.py          # File operations
│       ├── web.py           # Web search & fetch
│       └── calendar.py      # Calendar management
└── bot/
    ├── bot.py               # Telegram handlers
    ├── auth.py              # User whitelist
    └── confirm.py           # Human-in-the-loop confirmation
```

**Stack:**
- [Claude](https://anthropic.com) – claude-sonnet as the AI backbone
- [LangGraph](https://github.com/langchain-ai/langgraph) – multi-agent state machine
- [python-telegram-bot](https://python-telegram-bot.org) – Telegram interface
- Python 3.11+, macOS

---

## Setup

### Prerequisites

- Python 3.11+
- Anthropic API key
- Telegram bot token (via [@BotFather](https://t.me/BotFather))
- Your Telegram user ID (via [@userinfobot](https://t.me/userinfobot))

### Installation

```bash
git clone https://github.com/fabiomorena/FabBot.git
cd FabBot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=123456789
```

### Run

```bash
python main.py
```

---

## Usage

Send any natural language message to your bot on Telegram:

| Message | Routed to |
|--------|-----------|
| "Was steht morgen in meinem Kalender?" | `calendar_agent` |
| "Zeig mir den Inhalt von ~/Downloads" | `file_agent` |
| "Wie viel freier Speicher ist noch?" | `terminal_agent` |
| "Suche nach den neuesten Python News" | `web_agent` |
| "Mach einen Screenshot" | `computer_agent` |

**Commands:**

```
/start      – Start the bot
/status     – Check agent status
/ask        – Direct query
/auditlog   – Show last 10 executed actions
```

---

## Security

FabBot has a multi-layered security architecture designed for a locally-running agent with deep system access.

### Input layer
- **User whitelist** – only explicitly allowed Telegram user IDs can interact with the bot
- **Prompt injection guard** – known injection patterns are detected and blocked before reaching the LLM
- **Input length limit** – maximum 2,000 characters per message

### Execution layer
- **Terminal allowlist** – only 20 explicitly permitted read-only shell commands can be executed (`df`, `ls`, `ps`, etc.)
- **Shell operator blocking** – `;`, `&&`, `|`, `>`, `$()` and similar operators are always rejected
- **Path traversal guard** – `..` in arguments and paths is always blocked
- **Dangerous argument blacklist** – `--exec`, `.ssh/id_rsa`, `/etc/passwd` and similar are always rejected
- **File path sandbox** – file operations are restricted to explicit allowed directories (`~/Downloads`, `~/Documents`, `~/Desktop`, etc.)
- **TOCTOU protection** – paths and commands are re-validated immediately before execution, after user confirmation

### Confirmation layer
- **Human-in-the-loop** – every terminal command and every file write requires explicit confirmation via Telegram inline button before execution
- **60-second timeout** – unconfirmed actions are automatically cancelled

### Audit layer
- **Local audit log** – every action is logged to `~/.fabbot/audit.log` with timestamp, agent, action, and status
- **Sensitive data redacting** – API keys, tokens, passwords, and email addresses are automatically redacted from all log entries
- **No content logging** – file contents and command outputs are never written to the log, only metadata (path, size, status)

---

## Roadmap

- **Phase 1** ✅ Foundation – Telegram bot, LangGraph supervisor, multi-agent structure
- **Phase 2** ✅ Core tools – Terminal agent, File agent, full security layer
- **Phase 3** 🔜 Web & Calendar integration
- **Phase 4** 🔜 macOS menubar app
- **Phase 5** 🔜 Computer Use API (desktop control)

---

## License

Private project – not licensed for public use.