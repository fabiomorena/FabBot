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

### Run

```bash
python main.py        # Bot only
python menubar.py     # With menubar app
pytest tests/ -v      # Run tests (74 tests)
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

---

## License

Private project – not licensed for public use.