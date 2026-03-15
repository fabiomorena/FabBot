# FabBot – Personal Mac AI Assistant

A personal AI assistant that runs locally on macOS, controlled via Telegram and a native menubar app. Built with Claude (Anthropic), LangGraph, and a multi-agent architecture.

---

## Overview

FabBot lets you control your Mac using natural language – from anywhere, via Telegram. A supervisor agent analyzes incoming requests and routes them to the appropriate specialist agent.

```
You → Telegram → Supervisor → calendar_agent / terminal_agent / file_agent / ...
```

---

## Features

| Status | Feature |
|--------|---------|
| ✅ | Telegram bot interface |
| ✅ | User authentication (whitelist) |
| ✅ | Multi-agent supervisor routing |
| 🔜 | Terminal – execute shell commands |
| 🔜 | File – read, write, search files |
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
│   └── agents/
│       ├── computer.py      # Desktop control (Computer Use API)
│       ├── terminal.py      # Shell command execution
│       ├── file.py          # File operations
│       ├── web.py           # Web search & fetch
│       └── calendar.py      # Calendar management
└── bot/
    ├── bot.py               # Telegram handlers
    └── auth.py              # User whitelist
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
/start   – Start the bot
/status  – Check agent status
/ask     – Direct query
```

---

## Security

- Only whitelisted Telegram user IDs can interact with the bot
- `.env` is excluded from version control
- Dangerous shell operations require confirmation (coming in Phase 5)
- Bot runs locally – no external server involved

---

## Roadmap

- **Phase 1** ✅ Foundation – Telegram bot, LangGraph supervisor, multi-agent structure
- **Phase 2** 🔜 Core tools – Terminal, File, Computer Use API
- **Phase 3** 🔜 Web & Calendar integration
- **Phase 4** 🔜 macOS menubar app
- **Phase 5** 🔜 Security hardening & polish

---

## License

Private project – not licensed for public use.