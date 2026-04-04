# FabBot – Personal Mac AI Assistant

![CI](https://github.com/fabiomorena/FabBot/actions/workflows/test.yml/badge.svg)

A personal AI assistant that runs locally on macOS, controlled via Telegram and a native menubar app. Built with Claude (Anthropic), LangGraph, and a multi-agent architecture.

---

## Overview

```
You → Telegram (text or voice or photo) → Security Guard → Supervisor (Haiku) → calendar_agent / terminal_agent / file_agent / web_agent / chat_agent / vision_agent / ...
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
| ✅ | Knowledge Clipper – /clip saves articles as Markdown to Obsidian vault |
| ✅ | Knowledge Search – /search searches saved notes locally |
| ✅ | Persistent Conversation Memory – SQLite via AsyncSqliteSaver, survives restarts |
| ✅ | Chat Agent – answers follow-up questions directly from conversation history |
| ✅ | Text-to-Speech – ElevenLabs (primär) + edge-tts (Fallback), Mac speaker + Telegram voice |
| ✅ | TTS Toggle – /tts on|off or TTS_ENABLED env var |
| ✅ | TTS Stop – /stop kills running afplay immediately |
| ✅ | German date format – 18.03.2026, 19:06 Uhr |
| ✅ | GitHub Actions CI – runs 351 pytest tests on every push |
| ✅ | Personal Context Layer – personal_profile.yaml injected into all agents |
| ✅ | /remember – save personal notes to profile live from Telegram |
| ✅ | Auto-Learning – 3-stage pipeline (Detector → Writer → Reviewer) updates profile automatically |
| ✅ | 529 Retry – exponential backoff (2s/4s/8s) on Anthropic overload |
| ✅ | Memory Agent – explicit profile updates via natural language |
| ✅ | Hybrid profile structure – fixed sections + free custom section + places + media |
| ✅ | Media tracking – songs, films, podcasts, books stored as structured media entries |
| ✅ | Health Check – daily 06:00 system status report (6 components) |
| ✅ | Vision Agent – photo analysis via Claude Sonnet Vision (objects, OCR, scene description) |
| ✅ | At-Rest-Encryption – personal_profile.yaml via Fernet, Key im macOS Keychain |
| ✅ | Context Trim – chat_agent limits LLM-Call to CHAT_CONTEXT_WINDOW messages (default 40) |
| ✅ | Weekend Party Report – jeden Mittwoch 20:00, 7 Berliner Clubs, Tavily + Homepage-Fetch |
| ✅ | Dedup-Fix – chat_agent never repeats answers on short confirmations (Genau, Ok, Danke) |
| ✅ | ElevenLabs TTS – Stimme Ami (eleven_multilingual_v2), edge-tts als Fallback, Voice ID via .env |
| ✅ | claude.md – persistente Bot-Instruktionen, in chat_agent System-Prompt injiziert, überlebt Context Trim |
| ✅ | Bot-Instruktionen lernbar – "Merke dir grundsätzlich..." schreibt direkt in claude.md, sofort aktiv |
---


## Architecture

```
FabBot/
├── main.py                  # Entrypoint
├── menubar.py               # macOS menubar app
├── personal_profile.yaml    # Personal profile (local only, not in repo)
├── requirements.txt         # Direct dependencies
├── requirements.lock        # Pinned lock file (pip-compile)
├── requirements-ci.txt      # CI dependencies (no macOS-only packages)
├── .env.example             # Environment variable template
├── review_log.sh            # Daily log summary script
├── .github/workflows/test.yml
├── tests/test_security_terminal.py  # pytest suite (351 tests)
├── agent/
│   ├── supervisor.py        # Supervisor – Haiku routing, AsyncSqliteSaver
│   ├── state.py             # LangGraph AgentState
│   ├── llm.py               # get_llm() Sonnet + get_fast_llm() Haiku
│   ├── protocol.py          # Protocol constants (HITL magic strings)
│   ├── security.py          # Two-stage injection guard, rate limiting
│   ├── audit.py             # Tamper-evident audit log
│   ├── profile.py           # Personal context loader
│   ├── profile_learner.py   # Auto-learning pipeline
│   └── agents/
│       ├── chat_agent.py    # Context-aware conversation agent, Dedup-Fix
│       ├── memory_agent.py  # Explicit profile updates
│       ├── vision_agent.py  # Photo analysis via Claude Sonnet Vision
│       ├── computer.py      # Desktop control
│       ├── terminal.py      # Shell command execution
│       ├── file.py          # File operations
│       ├── web.py           # Web search & fetch
│       ├── calendar.py      # Calendar management
│       ├── reminder_agent.py
│       └── clip_agent.py
└── bot/
    ├── bot.py               # Telegram handlers, HITL, Dedup-Sicherheitsnetz
    ├── auth.py              # User whitelist
    ├── confirm.py           # HITL confirmation
    ├── transcribe.py        # Local Whisper transcription
    ├── tts.py               # ElevenLabs (primär) + edge-tts (Fallback)
    ├── search.py            # Local knowledge base search
    ├── briefing.py          # Morning briefing scheduler (07:30)
    ├── reminders.py         # Reminder storage + proactive delivery
    ├── health_check.py      # Daily health check scheduler (06:00)
    └── party_report.py      # Weekend Party Report (Mittwoch 20:00)
```

**Stack:**
- Claude Sonnet 4 – AI backbone (`claude-sonnet-4-20250514`)
- Claude Haiku 4.5 – supervisor routing + LLM-Guard (`claude-haiku-4-5-20251001`)
- LangGraph – multi-agent state machine with AsyncSqliteSaver
- python-telegram-bot – Telegram interface
- Whisper – local voice transcription
- ElevenLabs – primary TTS (Ami, eleven_multilingual_v2)
- edge-tts – TTS fallback (de-DE-KatjaNeural)
- aiosqlite – async SQLite for persistent memory
- Tavily + Brave Search – web search
- rumps – macOS menubar app
- cryptography + keyring – At-Rest-Encryption via Fernet
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
pytest tests/ -v      # Run tests (351 tests)
```

### Run as Launch Agent

```bash
cp com.fabbot.agent.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fabbot.agent.plist
launchctl start com.fabbot.agent
tail -f ~/.fabbot/fabbot.log
```

---

## Roadmap

- **Phase 1–19** ✅ Foundation, security, TTS, memory, CI, performance
- **Phase 20–30** ✅ Bug fixes, async, briefing, HITL, code quality
- **Phase 31–40** ✅ Personal context, /remember, auto-learning, 529 retry
- **Phase 41–50** ✅ Security tests, memory agent, media tracking, hardening
- **Phase 51** ✅ Vision Agent – Foto-Analyse via Claude Sonnet Vision
- **Phase 52** ✅ Watchdog – externer Bot-Monitor via cron
- **Phase 53** ✅ Test-Resilienz – conftest.py autouse Fixtures
- **Phase 54** ✅ At-Rest-Encryption – Fernet + macOS Keychain
- **Phase 55** ✅ Vision System Fix – as_node Checkpoint, Supervisor Routing
- **Phase 55b** ✅ Code Quality – Operator-Precedenz Fix
- **Phase 56** ✅ AIMessage Echo-Fix – result_state Index-Slice
- **Phase 57** ✅ Context Trim – CHAT_CONTEXT_WINDOW (default 40)
- **Phase 58** ✅ Weekend Party Report – 7 Berliner Clubs, Mittwoch 20:00
- **Phase 59** ✅ Dedup-Fix – keine Wiederholungen bei kurzen Bestätigungen
- **Phase 60** ✅ ElevenLabs TTS – Stimme Ami, edge-tts Fallback, Voice ID via .env
- **Phase 61** ✅ TTS Logger – Truncation-Logging + ElevenLabs voice_settings via .env
- **Phase 62** ✅ claude.md – persistente Bot-Instruktionen für Charakter, Verhalten und Arbeitsweise
- **Phase 63** ✅ Bot-Instruktionen lernbar – memory_agent erkennt bot_instruction, schreibt in claude.md, sofort aktiv ohne Neustart
---

## License

Private project – not licensed for public use.
