# FabBot – Personal Mac AI Assistant

![CI](https://github.com/fabiomorena/FabBot/actions/workflows/test.yml/badge.svg)

A personal AI assistant that runs locally on macOS, controlled via Telegram and a native menubar app. Built with Claude (Anthropic), LangGraph, and a multi-agent architecture.

---

## Overview

FabBot lets you control your Mac using natural language – from anywhere, via Telegram. A supervisor agent analyzes incoming requests and routes them to the appropriate specialist agent.

```
You → Telegram (text or voice) → Security Guard → Supervisor → calendar_agent / terminal_agent / file_agent / web_agent / chat_agent / ...
```

---

## Features

| Status | Feature |
|--------|---------|
| ✅ | Telegram bot interface |
| ✅ | User authentication (whitelist, cached at startup) |
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
| ✅ | Persistent Conversation Memory – SQLite via AsyncSqliteSaver, survives restarts |
| ✅ | Chat Agent – answers follow-up questions directly from conversation history |
| ✅ | Text-to-Speech – responses spoken via Mac speaker + Telegram voice message |
| ✅ | TTS Toggle – `/tts on\|off` or `TTS_ENABLED` env var |
| ✅ | TTS Stop – `/stop` kills running afplay immediately |
| ✅ | German date format – `18.03.2026, 19:06 Uhr` |
| ✅ | GitHub Actions CI – runs 69 pytest tests on every push, with pip cache |
| ✅ | Test suite – 69 pytest tests |

---

## Architecture

```
FabBot/
├── main.py                  # Entrypoint
├── menubar.py               # macOS menubar app
├── requirements.txt         # Direct dependencies
├── requirements.lock        # Pinned lock file (pip-compile)
├── requirements-ci.txt      # CI dependencies (no macOS-only packages)
├── .env.example             # Environment variable template
├── .github/
│   └── workflows/
│       └── test.yml         # GitHub Actions CI – pip cache + pytest
├── tests/
│   └── test_security_terminal.py  # pytest suite (69 tests)
├── agent/
│   ├── supervisor.py        # Supervisor – AsyncSqliteSaver, init_graph/close_graph
│   ├── state.py             # LangGraph AgentState
│   ├── llm.py               # Centralized LLM client (lazy singleton)
│   ├── protocol.py          # Protocol constants (HITL magic strings)
│   ├── security.py          # Prompt injection guard, rate limiting, homoglyph normalization
│   ├── audit.py             # Tamper-evident audit log
│   └── agents/
│       ├── chat_agent.py    # Context-aware conversation agent (no tools)
│       ├── computer.py      # Desktop control (validated input)
│       ├── terminal.py      # Shell command execution, German date format
│       ├── file.py          # File operations
│       ├── web.py           # Web search & fetch
│       ├── calendar.py      # Calendar management
│       └── clip_agent.py    # URL clipper – fetch, summarize, save as Markdown
└── bot/
    ├── bot.py               # Telegram handlers, HITL TTS, post_init/post_shutdown hooks
    ├── auth.py              # User whitelist (cached at startup, warns if empty)
    ├── confirm.py           # Human-in-the-loop confirmation (full UUID)
    ├── transcribe.py        # Local Whisper transcription (voice → text)
    ├── tts.py               # Text-to-Speech (edge-tts + afplay + send_voice + stop)
    └── search.py            # Local knowledge base search
```

**Stack:**
- [Claude](https://anthropic.com) – claude-sonnet as the AI backbone
- [LangGraph](https://github.com/langchain-ai/langgraph) – multi-agent state machine with AsyncSqliteSaver
- [python-telegram-bot](https://python-telegram-bot.org) – Telegram interface
- [Whisper](https://github.com/openai/whisper) – local voice transcription (openai-whisper)
- [edge-tts](https://github.com/rany2/edge-tts) – text-to-speech via Microsoft Neural Voices
- [aiosqlite](https://github.com/omnilib/aiosqlite) – async SQLite for persistent memory
- [Tavily](https://tavily.com) + [Brave Search](https://brave.com/search/api/) – web search
- [rumps](https://github.com/jaredks/rumps) – macOS menubar app
- [Obsidian](https://obsidian.md) – knowledge base viewer (optional)
- [pytest](https://pytest.org) – test suite
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
pip install -r requirements.lock
brew install ffmpeg
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env` with your API keys. To disable TTS by default:
```env
TTS_ENABLED=false
```

### macOS Permissions

For Apple Calendar access:

**System Settings → Privacy & Security → Automation → Terminal → Calendar → Enable**

### Run

```bash
python main.py        # Bot only
python menubar.py     # With menubar app
pytest tests/ -v      # Run tests
```

---

## Usage

| Message | Routed to |
|--------|-----------|
| "Was steht morgen in meinem Kalender?" | `calendar_agent` |
| "Erstelle einen Termin morgen um 14 Uhr: Meeting" | `calendar_agent` |
| "Zeig mir den Inhalt von ~/Downloads" | `file_agent` |
| "Wie viel freier Speicher ist noch?" | `terminal_agent` |
| "Was ist heute für ein Datum?" | `terminal_agent` → `18.03.2026, 19:06 Uhr` |
| "Suche nach den neuesten KI News" | `web_agent` |
| "Mach einen Screenshot" | `computer_agent` |
| "Was habe ich dich gerade gefragt?" | `chat_agent` |
| 🎤 Voice note | Whisper → any agent |

**Commands:**

```
/start              – Start the bot & show help
/ask <Frage>        – Direct query
/clip <URL>         – Save URL as Markdown note
/search             – List all saved notes
/search <Begriff>   – Search notes by keyword
/search #Tag        – Search notes by tag
/tts on|off         – Enable or disable text-to-speech
/stop               – Stop current voice output immediately
/status             – Check agent status (shows TTS state)
/auditlog           – Show last 10 executed actions
```

---

## Voice Notes

```
Voice note (OGG) → Whisper (local, small model) → transcribed text → Supervisor → agent
```

Whisper `small` model (~460 MB) downloaded on first use, cached locally. No audio leaves your machine.

---

## Text-to-Speech

Every bot response is spoken aloud simultaneously:

```
Bot response (text)
  → edge-tts (de-DE-KatjaNeural) → MP3
  ├── afplay → Mac speaker (immediate)
  └── send_voice() → Telegram voice message
```

Text is cleaned before synthesis – URLs, Markdown, and source sections stripped automatically. For HITL-confirmed actions, TTS fires only for short outputs ≤ 300 characters.

```
/tts off    → silent mode
/tts on     → re-enable
/stop       → kill running afplay immediately
```

---

## Conversation Memory

Persistent across bot restarts via AsyncSqliteSaver (`~/.fabbot/memory.db`). Each Telegram chat has its own isolated thread. Connection opened via `post_init` hook and closed cleanly via `post_shutdown` hook.

---

## Security

Multi-layered security: user whitelist → prompt injection guard → homoglyph normalization → rate limiting → terminal allowlist → shell operator blocking → path traversal guard → SSRF protection → TOCTOU re-validation → HITL confirmation → tamper-evident audit log.

---

## CI

GitHub Actions runs on every push and pull request to `master`:

```yaml
- Python 3.11, ubuntu-latest
- pip cache keyed on requirements-ci.txt
- pytest tests/ -v  (69 tests)
```

`requirements-ci.txt` excludes macOS-only packages (`pyobjc`, `rumps`, `pyautogui`, `rubicon-objc`) that cannot build on Linux.

---

## Testing

```bash
pytest tests/ -v
```

69 tests: security, rate limiting, terminal allowlist, TTS cleaning, TTS toggle.

---

## Roadmap

- **Phase 1–9** ✅ Foundation, agents, security hardening
- **Phase 10–11** ✅ Engineering & code quality
- **Phase 12** ✅ Conversation memory (MemorySaver → AsyncSqliteSaver)
- **Phase 13** ✅ Text-to-Speech (edge-tts, Mac speaker + Telegram)
- **Phase 14** ✅ TTS polish – toggle, source detection, 69 tests, /stop command
- **Phase 15** ✅ Persistent memory, clean shutdown, German date format, HITL TTS
- **Phase 16** ✅ GitHub Actions CI – green on every push, pip cache, rubicon-objc fix

---

## License

Private project – not licensed for public use.