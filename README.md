# FabBot – Personal Mac AI Assistant

A personal AI assistant that runs locally on macOS, controlled via Telegram and a native menubar app. Built with Claude (Anthropic), LangGraph, and a multi-agent architecture.

---

## Overview

FabBot lets you control your Mac using natural language – from anywhere, via Telegram. A supervisor agent analyzes incoming requests and routes them to the appropriate specialist agent.

```
You → Telegram → Security Guard → Supervisor → calendar_agent / terminal_agent / file_agent / web_agent / ...
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
| ✅ | Web – search (Tavily + Brave) and fetch URLs |
| ✅ | Calendar – read and create events (Apple Calendar) |
| ✅ | Security layer – prompt injection guard, audit log, human-in-the-loop |
| ✅ | macOS menubar app – start/stop bot, audit log |
| ✅ | Computer Use – screenshot + desktop control with HITL |

---

## Architecture

```
FabBot/
├── main.py                  # Entrypoint
├── menubar.py               # macOS menubar app
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
- [Tavily](https://tavily.com) + [Brave Search](https://brave.com/search/api/) – web search
- [rumps](https://github.com/jaredks/rumps) – macOS menubar app
- Python 3.11+, macOS

---

## Setup

### Prerequisites

- Python 3.11+
- Anthropic API key
- Telegram bot token (via [@BotFather](https://t.me/BotFather))
- Your Telegram user ID (via [@userinfobot](https://t.me/userinfobot))
- Tavily API key (optional, for web search)
- Brave Search API key (optional, for web search)

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
TAVILY_API_KEY=tvly-...
BRAVE_API_KEY=BSA...
```

### macOS Permissions

For Apple Calendar access, grant Terminal and/or PyCharm automation permissions:

**System Settings → Privacy & Security → Automation → Terminal → Calendar → Enable**

### Run

**Bot only (Telegram):**
```bash
python main.py
```

**With menubar app:**
```bash
python menubar.py
```

Then click "Starten" in the menubar to start the bot.

---

## Usage

Send any natural language message to your bot on Telegram:

| Message | Routed to |
|--------|-----------|
| "Was steht morgen in meinem Kalender?" | `calendar_agent` |
| "Erstelle einen Termin morgen um 14 Uhr: Meeting" | `calendar_agent` |
| "Zeig mir den Inhalt von ~/Downloads" | `file_agent` |
| "Wie viel freier Speicher ist noch?" | `terminal_agent` |
| "Suche nach den neuesten KI News" | `web_agent` |
| "Fetch https://example.com" | `web_agent` |
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
- **Terminal allowlist** – only 20 explicitly permitted read-only shell commands can be executed
- **Shell operator blocking** – `;`, `&&`, `|`, `>`, `$()` and similar operators are always rejected
- **Path traversal guard** – `..` in arguments and paths is always blocked
- **Dangerous argument blacklist** – `--exec`, `.ssh/id_rsa`, `/etc/passwd` and similar are always rejected
- **File path sandbox** – file operations are restricted to explicit allowed directories
- **SSRF protection** – web agent blocks requests to localhost and private IP ranges
- **TOCTOU protection** – paths and commands are re-validated immediately before execution

### Confirmation layer
- **Human-in-the-loop** – every terminal command, file write, calendar event creation, and computer use action requires explicit confirmation via Telegram inline button
- **60-second timeout** – unconfirmed actions are automatically cancelled
- **pyautogui FAILSAFE** – moving mouse to screen corner immediately stops any computer use action

### Local API
- **Shared secret token** – local API on `127.0.0.1:8766` is secured with a token stored at `~/.fabbot/local_api_token` (chmod 600)
- **Localhost only** – API is not reachable from outside the machine

### Audit layer
- **Local audit log** – every action is logged to `~/.fabbot/audit.log`
- **Sensitive data redacting** – API keys, tokens, passwords, and email addresses are automatically redacted
- **No content logging** – file contents and command outputs are never written to the log

---

## Roadmap

- **Phase 1** ✅ Foundation – Telegram bot, LangGraph supervisor, multi-agent structure
- **Phase 2** ✅ Core tools – Terminal agent, File agent, full security layer
- **Phase 3** ✅ Web & Calendar – Tavily + Brave search, fetch, Apple Calendar integration
- **Phase 4** ✅ Menubar app + Calendar event creation with HITL confirmation
- **Phase 5** ✅ Computer Use – screenshot, click, type, open app with HITL confirmation

---

## License

Private project – not licensed for public use.