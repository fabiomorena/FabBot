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
| ✅ | Two-stage prompt injection guard (pattern + LLM-Guard via Haiku, fail-closed) |
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
| ✅ | Text-to-Speech – OpenAI TTS (primär) + edge-tts (Fallback), Mac speaker + Telegram voice |
| ✅ | TTS Toggle – `/tts on\|off` or `TTS_ENABLED` env var |
| ✅ | TTS Stop – `/stop` kills running afplay immediately |
| ✅ | German date format – `18.03.2026, 19:06 Uhr` |
| ✅ | GitHub Actions CI – runs 881 pytest tests on every push |
| ✅ | Personal Context Layer – `personal_profile.yaml` injected into all agents |
| ✅ | `/remember` – save personal notes to profile live from Telegram |
| ✅ | Auto-Learning – 3-stage pipeline (Detector → Writer → Reviewer) updates profile automatically |
| ✅ | 529 Retry – exponential backoff (2s/4s/8s) on Anthropic overload |
| ✅ | Memory Agent – explicit profile updates via natural language |
| ✅ | Hybrid profile structure – fixed sections + free `custom` section + `places` + `media` |
| ✅ | Media tracking – songs, films, podcasts, books stored as structured `media` entries |
| ✅ | Health Check – daily 06:00 system status report (6 components) |
| ✅ | Vision Agent – photo analysis via Claude Sonnet Vision (objects, OCR, scene description) |
| ✅ | At-Rest-Encryption – personal_profile.yaml via Fernet, Key im macOS Keychain |
| ✅ | Context Trim – chat_agent limits LLM-Call to CHAT_CONTEXT_WINDOW messages (default 40) |
| ✅ | Weekend Party Report – jeden Mittwoch 20:00, 7 Berliner Clubs, Tavily + Homepage-Fetch |
| ✅ | Dedup-Fix – chat_agent never repeats answers on short confirmations (Genau, Ok, Danke) |
| ✅ | claude.md – persistente Bot-Instruktionen, in chat_agent System-Prompt injiziert, überlebt Context Trim |
| ✅ | Bot-Instruktionen lernbar – "Merke dir grundsätzlich..." schreibt direkt in claude.md, sofort aktiv |
| ✅ | "Merke dir das" – Bot formuliert aus vorheriger Aussage eine Bot-Instruktion → claude.md |
| ✅ | OpenAI TTS – primärer Provider (nova/shimmer/...), edge-tts Fallback |
| ✅ | Modell via .env – ANTHROPIC_MODEL_SONNET/HAIKU konfigurierbar, lazy singleton |
| ✅ | Session Summary – tägliche Konversationszusammenfassung (23:30), Cross-Session-Kontext im chat_agent |
| ✅ | Second Brain – ChromaDB + OpenAI text-embedding-3-small, semantisches Retrieval aus Notizen/Sessions/Profil |
| ✅ | WhatsApp Agent – Nachrichten senden via whatsapp-web.js Node.js Service (Whitelist-gesichert, HITL, QR via Telegram) |
| ✅ | Prompt-Cache – chat_agent cached claude.md + Sessions + Profil (TTL 60s), invalidate_chat_cache() |
| ✅ | Datetime-Awareness – get_current_datetime() Europe/Berlin, alle Agenten-Prompts |
| ✅ | State-Transfer – last_agent_result/last_agent_name zwischen Agents, dynamischer Suffix außerhalb Cache |
| ✅ | memory_agent delete-aware – _is_valid_delete() strukturelle Subset-Prüfung, alle Kategorien generisch |
| ✅ | Modell-Validierung – _MODEL_PATTERN optional Datum, claude-sonnet-4-6 + opus-4-7 ohne Suffix valide |

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
├── tests/test_security_terminal.py  # pytest suite (881 tests)
├── agent/
│   ├── supervisor.py        # Supervisor – Haiku routing, AsyncSqliteSaver
│   ├── state.py             # LangGraph AgentState
│   ├── llm.py               # get_llm() Sonnet + get_fast_llm() Haiku
│   ├── protocol.py          # Protocol constants (HITL magic strings)
│   ├── security.py          # Two-stage injection guard, rate limiting, fail-closed
│   ├── audit.py             # Tamper-evident audit log (setup_audit_logger)
│   ├── profile.py           # Personal context loader
│   ├── profile_learner.py   # Auto-learning pipeline
│   ├── retrieval.py         # Second Brain – ChromaDB + OpenAI Embeddings
│   └── agents/
│       ├── chat_agent.py    # Dynamic prompt, claude.md + sessions + profile + retrieval per call
│       ├── memory_agent.py  # Explicit profile updates, delete-aware _review_yaml
│       ├── vision_agent.py  # Photo analysis via Claude Sonnet Vision
│       ├── computer.py      # Desktop control
│       ├── terminal.py      # Shell command execution
│       ├── file.py          # File operations
│       ├── web.py           # Web search & fetch
│       ├── calendar.py      # Calendar management
│       ├── reminder_agent.py
│       └── clip_agent.py
└── bot/
    ├── bot.py               # Telegram handlers, HITL, sanitize_input_async im try/except
    ├── auth.py              # User whitelist (cached at startup, RuntimeError if empty)
    ├── confirm.py           # HITL confirmation (full UUID)
    ├── transcribe.py        # Local Whisper transcription
    ├── tts.py               # OpenAI TTS (primär) + edge-tts (Fallback)
    ├── search.py            # Local knowledge base search
    ├── briefing.py          # Morning briefing scheduler (07:30)
    ├── reminders.py         # Reminder storage + proactive delivery
    ├── health_check.py      # Daily health check scheduler (06:00)
    ├── session_summary.py   # Daily session summary (23:30), TOCTOU-sicher
    └── party_report.py      # Weekend Party Report (Mittwoch 20:00)
```

**Stack:**
- Claude Sonnet – AI backbone (konfigurierbar via `ANTHROPIC_MODEL_SONNET`, default: `claude-sonnet-4-6`)
- Claude Haiku – supervisor routing + LLM-Guard (konfigurierbar via `ANTHROPIC_MODEL_HAIKU`, default: `claude-haiku-4-5-20251001`)
- LangGraph – multi-agent state machine with AsyncSqliteSaver
- python-telegram-bot – Telegram interface
- Whisper – local voice transcription
- OpenAI TTS – primary TTS (nova, konfigurierbar via OPENAI_TTS_VOICE)
- OpenAI Embeddings – text-embedding-3-small für Second Brain Retrieval
- edge-tts – TTS fallback (de-DE-KatjaNeural)
- ChromaDB – lokale Vektordatenbank für Second Brain (~/.fabbot/chroma/)
- aiosqlite – async SQLite for persistent memory
- Tavily + Brave Search – web search
- cryptography + keyring – At-Rest-Encryption via Fernet
- rumps – macOS menubar app
- Python 3.11+, macOS

---

## Setup

### Prerequisites

- Python 3.11+, Anthropic API key, OpenAI API key, Telegram bot token, ffmpeg

### Installation

```bash
git clone https://github.com/fabiomorena/FabBot.git
cd FabBot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install chromadb
brew install ffmpeg
```

### Configuration

```bash
cp .env.example .env   # fill in API keys
```

Create your personal profile (not included in repo):

```bash
cp personal_profile.yaml.example personal_profile.yaml   # then edit with your details
```

### macOS Permissions (required)

FabBot runs as a background process and needs explicit permissions to access files and folders.

**Full Disk Access** (for `/search`, `file_agent`, `terminal_agent`):
`System Settings → Privacy & Security → Full Disk Access → + → .venv/bin/python`

**Calendar Access** (for `calendar_agent`, `briefing`):
Start the bot once directly from Terminal (`python main.py`) and send a calendar request via Telegram to trigger the permission dialog.

**Prevent idle sleep** (to keep bot running while away):
```bash
caffeinate -i &   # prevents idle sleep, allows screen lock
```
Note: closing the laptop lid will still suspend the bot. Keep lid open or connect an external display.

### Run

```bash
python main.py        # Bot only
python menubar.py     # With menubar app
.venv/bin/python -m pytest tests/ -v      # Run tests (881 tests)
```

### Run as Launch Agent

```bash
cp com.fabbot.agent.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fabbot.agent.plist
launchctl start com.fabbot.agent
tail -f ~/.fabbot/fabbot.log
```

---

## Usage

| Message | Routed to |
|--------|-----------|
| "Was steht morgen in meinem Kalender?" | `calendar_agent` |
| "Erstelle einen Termin morgen um 10 Uhr" | `calendar_agent` |
| "Zeig mir den Inhalt von ~/Downloads" | `file_agent` |
| "Schreibe eine Datei nach ~/Desktop/notiz.txt" | `file_agent` |
| "Wie viel freier Speicher ist noch?" | `terminal_agent` |
| "Was ist heute für ein Datum?" | `terminal_agent` → `18.03.2026, 19:06 Uhr` |
| "Welche Prozesse laufen gerade?" | `terminal_agent` |
| "Suche nach den neuesten KI News" | `web_agent` |
| "Wie ist das Wetter in Berlin?" | `web_agent` |
| "Ruf mir die Seite example.com ab" | `web_agent` |
| "Mach einen Screenshot" | `computer_agent` |
| "Öffne Safari" | `computer_agent` |
| "Was habe ich dich gerade gefragt?" | `chat_agent` |
| "Fass das nochmal zusammen" | `chat_agent` |
| "Wo wohne ich?" / "Was sind meine Projekte?" | `chat_agent` → aus Profil |
| "Ich habe heute gut geschlafen" | `chat_agent` |
| "Erinnere mich morgen um 9 Uhr ans Meeting" | `reminder_agent` |
| "Was sind meine offenen Erinnerungen?" | `reminder_agent` |
| "Lösche Erinnerung #3" | `reminder_agent` |
| "Merke dir dass Saporito mein Lieblings-Italiener ist" | `memory_agent` |
| "Füge Marco als Kollegen hinzu" | `memory_agent` |
| "Speichere Insieme von Valentino Vivace als Lieblingslied" | `memory_agent` |
| "Vergiss den Eintrag über Bonial als Projekt" | `memory_agent` |
| 📷 Foto + "Was siehst du?" | `vision_agent` → Objekterkennung, OCR, Beschreibung |
| 📷 Foto + "Was steht hier?" | `vision_agent` → Texterkennung (OCR) |
| 🎤 Voice note | Whisper → any agent |

**Commands:**
```
/start /ask /clip /search /remember /tts on|off /stop /status /auditlog
```

---

## Personal Context Layer

FabBot uses a local `personal_profile.yaml` to give all agents persistent knowledge about you – projects, preferences, people, routines. This file is not committed to the repo.

```yaml
identity:
  name: Fabio
  location: Berlin, Deutschland

projects:
  active:
    - name: FabBot
      stack: [Python, LangGraph, Telegram]
      priority: high

people:
  - name: Stephanie Priller
    context: Steffi ist Fabios Freundin

preferences:
  communication: prägnant, direkt, technisch
```

**Two context levels:**
- **Short** (Supervisor/Haiku): name + active projects – minimal overhead, routing unaffected
- **Full** (chat_agent/Sonnet): everything including people, notes, preferences

**Live updates via `/remember`:**
```
/remember ich arbeite gerade auch an Projekt X
```
Writes a timestamped note to `personal_profile.yaml`, active immediately without restart.

---

## Security

### Two-stage prompt injection guard

**Stage 1 – Pattern check (free, instant):** Known patterns hard-blocked. Softer patterns increase suspicion score.

**Stage 2 – LLM-Guard via Haiku (only when score > 0):** Returns `SAFE` or `INJECTION`. Fail-closed: Guard errors never block legitimate messages.

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
| All agents (answers) | claude-sonnet-4-6 | full quality for responses |
| Vision Agent | claude-sonnet-4-6 | multimodal vision capability |

~40% faster response time vs. Sonnet-only.

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

- **Phase 1–19** ✅ Foundation – Telegram bot, multi-agent supervisor, terminal/file/web/calendar agents, security guard, audit log, menubar app, CI, TTS, persistent memory
- **Phase 20–30** ✅ Hardening – async fixes, morning briefing, HITL improvements, code quality, watchdog
- **Phase 31–40** ✅ Personal Context – personal_profile.yaml, /remember, auto-learning pipeline, 529 retry
- **Phase 41–50** ✅ Security & Memory – security test suite, memory agent, media tracking, at-rest encryption
- **Phase 51–60** ✅ Vision & TTS – Vision Agent, session summary, ElevenLabs→OpenAI TTS migration, weekend party report, dedup fix
- **Phase 61–70** ✅ claude.md & TTS – persistente Bot-Instruktionen, lernbar via "Merke dir das", TTS hardening, model via .env
- **Phase 71–80** ✅ Routing & Knowledge – supervisor routing fix, Second Brain (ChromaDB), natural language passthrough, morning briefing fix, stability fixes
- **Phase 81–90** ✅ WhatsApp & Security – WhatsApp Agent (whatsapp-web.js), auth fail-closed, rate limiting, LangSmith telemetry, watchdog fixes
- **Phase 91–99** ✅ Hardening & Refactor – crypto/audit/llm hardening, GitHub Issues workflow, Prompt-Cache TTL 60s, model-validierung beim Start, memory_agent Registry-Pattern, deque dedup, get_current_datetime() Europe/Berlin, State-Transfer last_agent_result/last_agent_name
- **Phase 100–116** ✅ Stabilisierung & Bug-Fixes – Duplicate Responses fix, Wetter via wttr.in, drop_pending_updates + ThrottleInterval, _invoke_locks Race Condition, web_agent Wetter-Routing, Supervisor Early-Return, memory_agent delete generisch, computer_agent Regex-Intent-Parse, _review_yaml delete-aware (alle Kategorien), Sonnet-Default auf claude-sonnet-4-6, _MODEL_PATTERN optional Datum; 881 Tests grün

---

## License

Private project – not licensed for public use.
