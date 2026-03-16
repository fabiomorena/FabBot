# FabBot – Personal Mac AI Assistant

A personal AI assistant that runs locally on macOS, controlled via Telegram and a native menubar app. Built with Claude (Anthropic), LangGraph, and a multi-agent architecture.

---

## Overview

FabBot lets you control your Mac using natural language – from anywhere, via Telegram. A supervisor agent analyzes incoming requests and routes them to the appropriate specialist agent.

```
You → Telegram (text or voice) → Security Guard → Supervisor → calendar_agent / terminal_agent / file_agent / web_agent / ...
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
| ✅ | Voice Notes – send voice messages, transcribed locally via Whisper |
| ✅ | Knowledge Clipper – `/clip <URL>` saves articles as Markdown to Obsidian vault |
| ✅ | Knowledge Search – `/search <term>` searches saved notes locally |

---

## Architecture

```
FabBot/
├── main.py                  # Entrypoint
├── menubar.py               # macOS menubar app
├── agent/
│   ├── supervisor.py        # Supervisor – routes to sub-agents
│   ├── state.py             # LangGraph AgentState
│   ├── security.py          # Prompt injection guard, rate limiting, input sanitization
│   ├── audit.py             # Tamper-evident audit log
│   └── agents/
│       ├── computer.py      # Desktop control (Computer Use API)
│       ├── terminal.py      # Shell command execution
│       ├── file.py          # File operations
│       ├── web.py           # Web search & fetch
│       ├── calendar.py      # Calendar management
│       └── clip_agent.py    # URL clipper – fetch, summarize, save as Markdown
└── bot/
    ├── bot.py               # Telegram handlers (text, voice, commands)
    ├── auth.py              # User whitelist
    ├── confirm.py           # Human-in-the-loop confirmation
    ├── transcribe.py        # Local Whisper transcription
    └── search.py            # Local knowledge base search
```

**Stack:**
- [Claude](https://anthropic.com) – claude-sonnet as the AI backbone
- [LangGraph](https://github.com/langchain-ai/langgraph) – multi-agent state machine
- [python-telegram-bot](https://python-telegram-bot.org) – Telegram interface
- [Whisper](https://github.com/openai/whisper) – local voice transcription (openai-whisper)
- [Tavily](https://tavily.com) + [Brave Search](https://brave.com/search/api/) – web search
- [rumps](https://github.com/jaredks/rumps) – macOS menubar app
- [Obsidian](https://obsidian.md) – knowledge base viewer (optional)
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
- ffmpeg (required for Whisper voice transcription)

### Installation

```bash
git clone https://github.com/fabiomorena/FabBot.git
cd FabBot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
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

For Apple Calendar access, grant Terminal automation permissions:

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

Send any natural language message or voice note to your bot on Telegram:

| Message | Routed to |
|--------|-----------|
| "Was steht morgen in meinem Kalender?" | `calendar_agent` |
| "Erstelle einen Termin morgen um 14 Uhr: Meeting" | `calendar_agent` |
| "Zeig mir den Inhalt von ~/Downloads" | `file_agent` |
| "Wie viel freier Speicher ist noch?" | `terminal_agent` |
| "Suche nach den neuesten KI News" | `web_agent` |
| "Fetch https://example.com" | `web_agent` |
| "Mach einen Screenshot" | `computer_agent` |
| 🎤 Voice note with any of the above | transcribed via Whisper → any agent |

**Commands:**

```
/start              – Start the bot & show help
/ask <Frage>        – Direct query
/clip <URL>         – Save URL as Markdown note to ~/Documents/Wissen/
/search             – List all saved notes
/search <Begriff>   – Search notes by keyword
/search #Tag        – Search notes by tag
/status             – Check agent status
/auditlog           – Show last 10 executed actions
```

---

## Voice Notes

FabBot supports Telegram voice messages out of the box. Send a voice note instead of typing – Whisper transcribes it locally on your Mac, then the result is passed to the normal agent pipeline.

```
Voice note (OGG) → Whisper (local, small model) → transcribed text → Supervisor → agent
```

The transcribed text is shown as a reply before the agent response. The Whisper `small` model (~460 MB) is downloaded on first use and cached locally. No audio data leaves your machine.

---

## Knowledge Clipper

Save any article or webpage to your local Obsidian-compatible knowledge base:

```
/clip https://example.com/article
→ FabBot fetches & summarizes the page
→ Shows preview with title, tags, and summary
→ After confirmation: saved to ~/Documents/Wissen/YYYY-MM-DD-title.md
```

Notes are saved in Markdown with structured frontmatter (source URL, date, tags, summary, key points). Open `~/Documents/Wissen/` as an Obsidian vault to browse and link notes.

Search your knowledge base directly from Telegram:

```
/search              → list all notes
/search Berlin       → find notes containing "Berlin"
/search #Tech        → find notes tagged #Tech
```

---

## Security

FabBot has a multi-layered security architecture designed for a locally-running agent with deep system access.

### Input layer
- **User whitelist** – only explicitly allowed Telegram user IDs can interact with the bot
- **Prompt injection guard** – known injection patterns detected and blocked before reaching the LLM; Unicode normalization prevents homoglyph bypasses (e.g. Cyrillic lookalikes)
- **Rate limiting** – max 20 messages per 60 seconds per user
- **Input length limit** – maximum 2,000 characters per message

### Execution layer
- **Terminal allowlist** – only 20 explicitly permitted shell commands can be executed
- **Shell operator blocking** – `;`, `&&`, `|`, `>`, `$()` and similar always rejected
- **Path traversal guard** – `..` in arguments always blocked
- **Dangerous argument blocklist** – `--exec`, `.ssh/id_rsa`, `.ssh/config`, `.env`, `local_api_token` and similar always rejected
- **system_profiler whitelist** – only 5 safe datatypes permitted (hardware, software, storage, memory, displays)
- **find sandboxing** – blocked at `/`, `/etc`, `~/.ssh`, `~/.fabbot`
- **cat/head/tail protection** – blocked for `~/.ssh/` and sensitive token files
- **File path sandbox** – file operations restricted to explicit allowed directories (Downloads, Documents, Desktop, Projects); broad home directory access removed
- **Explicit path blocklist** – `~/.ssh`, `~/.fabbot`, `.env`, shell configs always blocked regardless of base path
- **SSRF protection** – web agent blocks loopback, private IPs, link-local (169.254.x.x / AWS metadata), IPv6 loopback (::1), multicast, reserved ranges, and `.local`/`.internal` hostnames via Python `ipaddress` module
- **TOCTOU protection** – paths and commands re-validated immediately before execution

### Confirmation layer
- **Human-in-the-loop** – every terminal command, file write, calendar event creation, computer use action, and clip save requires explicit confirmation via Telegram inline button
- **60-second timeout** – unconfirmed actions automatically cancelled
- **pyautogui FAILSAFE** – moving mouse to screen corner immediately stops any computer use action

### Local API
- **Shared secret token** – local API on `127.0.0.1:8766` secured with token at `~/.fabbot/local_api_token` (chmod 600)
- **Localhost only** – API not reachable from outside the machine

### Audit layer
- **Local audit log** – every action logged to `~/.fabbot/audit.log`
- **Sensitive data redacting** – API keys, tokens, passwords, and email addresses automatically redacted
- **No content logging** – file contents and command outputs never written to the log

---

## Roadmap

- **Phase 1** ✅ Foundation – Telegram bot, LangGraph supervisor, multi-agent structure
- **Phase 2** ✅ Core tools – Terminal agent, File agent, full security layer
- **Phase 3** ✅ Web & Calendar – Tavily + Brave search, fetch, Apple Calendar integration
- **Phase 4** ✅ Menubar app + Calendar event creation with HITL confirmation
- **Phase 5** ✅ Computer Use – screenshot, click, type, open app with HITL confirmation
- **Phase 6** ✅ Voice Notes – local Whisper transcription, OGG support, no external API needed
- **Phase 7** ✅ Knowledge Clipper – `/clip` saves URLs as structured Markdown, Obsidian-compatible
- **Phase 8** ✅ Knowledge Search – `/search` searches local notes by keyword and tag
- **Phase 9** ✅ Security hardening – Unicode normalization, rate limiting, IPv6 SSRF, tightened sandboxes

---

## License

Private project – not licensed for public use.