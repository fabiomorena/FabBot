# FabBot – Personal Mac AI Assistant

![CI](https://github.com/fabiomorena/FabBot/actions/workflows/test.yml/badge.svg)

A personal AI assistant that runs locally on macOS, controlled via Telegram and a native menubar app. Built with Claude (Anthropic), LangGraph, and a multi-agent architecture.

---

## Overview

```
You → Telegram (text or voice) → Security Guard → Supervisor (Haiku) → calendar_agent / terminal_agent / file_agent / web_agent / chat_agent / ...
```

---

## Features

| Status | Feature |
|--------|---------|
| ✅ | Telegram bot interface |
| ✅ | User authentication (whitelist, cached at startup) |
| ✅ | Multi-agent supervisor routing (claude-haiku for speed) |
| ✅ | Terminal – execute shell commands |
| ✅ | File – read, write, list files |
| ✅ | Web – search (Tavily + Brave) and fetch URLs |
| ✅ | Calendar – read and create events (Apple Calendar) |
| ✅ | Two-stage prompt injection guard (pattern + LLM-Guard via Haiku) |
| ✅ | Content isolation for indirect injection (web/clip agents) |
| ✅ | Human-in-the-loop confirmation for all destructive actions |
| ✅ | Tamper-evident audit log |
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
| ✅ | GitHub Actions CI – runs 74 pytest tests on every push |
| ✅ | Test suite – 74 pytest tests |

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
├── review_log.sh            # Daily log summary script
├── .github/
│   └── workflows/
│       └── test.yml         # GitHub Actions CI – pip cache + pytest
├── tests/
│   └── test_security_terminal.py  # pytest suite (74 tests)
├── agent/
│   ├── supervisor.py        # Supervisor – Haiku routing, AsyncSqliteSaver
│   ├── state.py             # LangGraph AgentState
│   ├── llm.py               # get_llm() Sonnet + get_fast_llm() Haiku
│   ├── protocol.py          # Protocol constants (HITL magic strings)
│   ├── security.py          # Two-stage injection guard, rate limiting, homoglyph normalization
│   ├── audit.py             # Tamper-evident audit log
│   └── agents/
│       ├── chat_agent.py    # Context-aware conversation agent (no tools)
│       ├── computer.py      # Desktop control (validated input)
│       ├── terminal.py      # Shell command execution, German date format
│       ├── file.py          # File operations
│       ├── web.py           # Web search & fetch with content isolation
│       ├── calendar.py      # Calendar management
│       └── clip_agent.py    # URL clipper with content isolation
└── bot/
    ├── bot.py               # Telegram handlers, HITL TTS, post_init/post_shutdown hooks
    ├── auth.py              # User whitelist (cached at startup, warns if empty)
    ├── confirm.py           # Human-in-the-loop confirmation (full UUID)
    ├── transcribe.py        # Local Whisper transcription (voice → text)
    ├── tts.py               # Text-to-Speech (edge-tts + afplay + send_voice + stop)
    └── search.py            # Local knowledge base search
```

**Stack:**
- [Claude Sonnet 4](https://anthropic.com) – AI backbone for all agents (`claude-sonnet-4-20250514`)
- [Claude Haiku 4.5](https://anthropic.com) – supervisor routing + LLM-Guard (`claude-haiku-4-5-20251001`)
- [LangGraph](https://github.com/langchain-ai/langgraph) – multi-agent state machine with AsyncSqliteSaver
- [python-telegram-bot](https://python-telegram-bot.org) – Telegram interface
- [Whisper](https://github.com/openai/whisper) – local voice transcription (openai-whisper)
- [edge-tts](https://github.com/rany2/edge-tts) – text-to-speech via Microsoft Neural Voices
- [aiosqlite](https://github.com/omnilib/aiosqlite) – async SQLite for persistent memory
- [Tavily](https://tavily.com) + [Brave Search](https://brave.com/search/api/) – web search
- [rumps](https://github.com/jaredks/rumps) – macOS menubar app
- Python 3.11+, macOS

---

## Setup

### Prerequisites

- Python 3.11+, Anthropic API key, Telegram bot token, ffmpeg

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
cp .env.example .env   # fill in API keys
```

### macOS Permissions (required)

FabBot runs as a background process and needs explicit permissions to access files and folders.

**Full Disk Access** (for `/search`, `file_agent`, `terminal_agent`):
`System Settings → Privacy & Security → Full Disk Access → + → .venv/bin/python`

**Prevent idle sleep** (to keep bot running while away):
```bash
caffeinate -i &   # prevents idle sleep, allows screen lock
```
Note: closing the laptop lid will still suspend the bot. Keep lid open or connect an external display.

### Run

```bash
python main.py        # Bot only
python menubar.py     # With menubar app
pytest tests/ -v      # Run tests (74 tests)
```

### Run as Launch Agent (auto-start on login)

```bash
# Install
cp com.fabbot.agent.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fabbot.agent.plist

# Control
launchctl start com.fabbot.agent
launchctl stop com.fabbot.agent

# Logs
tail -f ~/.fabbot/fabbot.log
./review_log.sh          # daily summary
./review_log.sh 2026-03-25  # specific date
```

---

## Usage

| Message | Routed to |
|--------|-----------|
| "Was steht morgen in meinem Kalender?" | `calendar_agent` |
| "Zeig mir den Inhalt von ~/Downloads" | `file_agent` |
| "Wie viel freier Speicher ist noch?" | `terminal_agent` |
| "Was ist heute für ein Datum?" | `terminal_agent` → `18.03.2026, 19:06 Uhr` |
| "Suche nach den neuesten KI News" | `web_agent` |
| "Wie ist das Wetter in Berlin?" | `web_agent` |
| "Mach einen Screenshot" | `computer_agent` |
| "Was habe ich dich gerade gefragt?" | `chat_agent` |
| 🎤 Voice note | Whisper → any agent |

**Commands:**
```
/start /ask /clip /search /tts on|off /stop /status /auditlog
```

---

## Security

### Two-stage prompt injection guard

**Stage 1 – Pattern check (free, instant):** Known patterns hard-blocked. Softer patterns increase suspicion score.

**Stage 2 – LLM-Guard via Haiku (only when score > 0):** Returns `SAFE` or `INJECTION`. Fail-open: Guard errors never block legitimate messages.

### Content isolation

Fetched web content is wrapped in `<document>` tags before LLM processing. HTML comments stripped. Explicit instruction to ignore content inside document tags.

### Additional layers
User whitelist · Homoglyph normalization · Rate limiting · Terminal allowlist · Shell operator blocking · Path traversal guard · SSRF protection · TOCTOU re-validation · HITL confirmation · Audit log

---

## Performance

| Component | Model | Reason |
|---|---|---|
| Supervisor (routing) | claude-haiku-4-5 | ~4x faster, simple classification |
| LLM-Guard (security) | claude-haiku-4-5 | fast, cost-efficient screening |
| All agents (answers) | claude-sonnet-4 | full quality for responses |

~40% faster response time vs. Sonnet-only.

---

## Testing

```bash
pytest tests/ -v   # 74 tests
```

Coverage: security patterns · rate limiting · terminal allowlist · TTS cleaning · TTS toggle · stop_speaking() with mocked Popen

---

## Logging

Logs are written to `~/.fabbot/fabbot.log` with daily rotation (7 days kept).

```bash
tail -f ~/.fabbot/fabbot.log      # live log
./review_log.sh                   # today's summary
./review_log.sh 2026-03-25        # specific date summary
```

---

## Roadmap

- **Phase 1–9** ✅ Foundation, agents, security hardening
- **Phase 10–11** ✅ Engineering & code quality
- **Phase 12** ✅ Conversation memory (AsyncSqliteSaver)
- **Phase 13** ✅ Text-to-Speech (edge-tts, Mac speaker + Telegram)
- **Phase 14** ✅ TTS polish – toggle, /stop, tests
- **Phase 15** ✅ Persistent memory, clean shutdown, German date format
- **Phase 16** ✅ GitHub Actions CI
- **Phase 17** ✅ Performance – Haiku supervisor, ~40% faster
- **Phase 18** ✅ Security – two-stage LLM-Guard + content isolation
- **Phase 19** ✅ stop_speaking() tests, precise suspicious patterns
- **Phase 20** ✅ Bug fixes – AIMessage echo fix, HITL context isolation
- **Phase 21** ✅ Supervisor routing fix – last HumanMessage only, Launch Agent setup
- **Phase 22** ✅ Persistent logging – TimedRotatingFileHandler, 7-day rotation, review_log.sh
- **Phase 23** ✅ macOS permissions – Full Disk Access for Launch Agent, caffeinate docs
- **Phase 24** ✅ Bug fixes – terminal HITL prefix, web_agent JSON logging, _filter_hitl_messages tests, review_log.sh precise API count
- **Phase 25** ✅ UX fixes – TTS interrupt on new message, df path blocking, supervisor routing precision
- **Phase 26** ✅ Local CD – post-merge Git Hook auto-restarts Launch Agent after git pull
- **Phase 27** ✅ Bug fixes – web_agent robust JSON parsing, calendar macOS permissions, df path argument blocking
- **Phase 28** ✅ CI fixes – langgraph-checkpoint-sqlite + aiosqlite in requirements-ci.txt, actions v5
- **Phase 29** ✅ Stability – moved project to internal SSD (no more SIGBUS crashes), emoji filter in TTS
- **Phase 30** ✅ Quality – 84 tests, emoji-stripping tests, df /System fix, TTS emoji filter
- **Phase 31** ✅ HITL memory fix – __MEMORY__ prefix prevents old results leaking into confirmation flow
- **Phase 32** ✅ Async – supervisor_node fully async (ainvoke), all agents now non-blocking

---

## License

Private project – not licensed for public use.