# FabBot - Personal Companion

![CI](https://github.com/fabiomorena/FabBot/actions/workflows/test.yml/badge.svg)

A personal AI companion that runs locally on macOS, controlled via Telegram. Built with Claude (Anthropic), LangGraph, and a multi-agent architecture.

---

## Overview

```
You → Telegram (text or voice or photo) → Security Guard → Supervisor (Haiku) → calendar_agent / terminal_agent / file_agent / web_agent / chat_agent / vision_agent / ...
```

---

## Features

**Interface & Control** – Telegram bot (text/voice/photo), user authentication (whitelist), human-in-the-loop confirmation for all destructive actions, German date format

**Agents** – Terminal (shell commands), File (read/write/list), Web (Tavily+Brave search + fetch), Calendar (Apple), Chat (conversation history + follow-ups), Vision (Claude Sonnet, objects/OCR/scene), Computer Use (screenshot + desktop control), WhatsApp (whatsapp-web.js, HITL, QR via Telegram), Knowledge Clipper (`/clip <URL>` → Obsidian), Knowledge Search (`/search <term>`)

**Memory & Learning** – Persistent conversation memory (SQLite), `personal_profile.yaml` injected into all agents, `/remember` + "Merke dir das" live learning, 3-stage auto-learning pipeline (Detector → Writer → Reviewer), Memory Agent (natural language profile updates), nested Preferences system (`preferences.<subcategory>.<key>`), Session Summary (daily 23:30), Second Brain (ChromaDB semantic retrieval), persistent `claude.md` bot instructions (learnable, survives context trim)

**Voice & Media** – Voice notes (Whisper, local transcription), TTS (OpenAI nova/shimmer + edge-tts fallback, Mac speaker + Telegram voice, `/tts on|off`, `/stop`), media tracking (songs/films/podcasts/books), Weekend Party Report (weekly, 7 Berliner Clubs, Wednesdays 20:00)

**Security** – Two-stage prompt injection guard (pattern + LLM-Guard via Haiku, fail-closed), content isolation for web/clip agents, tamper-evident audit log, at-rest encryption (`personal_profile.yaml` via Fernet + macOS Keychain), SSRF + DNS-Rebinding protection (IPv4 + IPv6 via `getaddrinfo`), SSL validation, path/symlink traversal prevention, subprocess env isolation (no API-key leakage)

**Operations** – GitHub Actions CI (1245 tests), 529 retry (exponential backoff 2s/4s/8s), prompt caching (claude.md + sessions + profile, TTL 60s), context trim (`CHAT_CONTEXT_WINDOW`, default 40), Whisper preload at startup, daily health check (06:00, 11 components), proactive heartbeat (hourly, 6h cooldown), model config via `.env` (`ANTHROPIC_MODEL_SONNET/HAIKU`)

---

## Architecture

```
FabBot/
├── main.py                  # Entrypoint
├── personal_profile.yaml    # Personal profile (local only, not in repo)
├── requirements.txt         # Direct dependencies
├── requirements.lock        # Pinned lock file (pip-compile)
├── requirements-ci.txt      # CI dependencies (no macOS-only packages)
├── .env.example             # Environment variable template
├── review_log.sh            # Daily log summary script
├── .github/workflows/test.yml
├── tests/                   # pytest suite (1245 tests)
├── agent/
│   ├── supervisor.py        # Supervisor – Haiku routing, AsyncSqliteSaver, _PRE_ROUTING_RULES
│   ├── state.py             # LangGraph AgentState
│   ├── llm.py               # get_llm() Sonnet + get_fast_llm() Haiku
│   ├── protocol.py          # Protocol constants (HITL magic strings)
│   ├── security.py          # Two-stage injection guard, weighted scoring, fail-closed
│   ├── audit.py             # Tamper-evident audit log (setup_audit_logger)
│   ├── claude_md.py         # claude.md loader – persistent bot instructions
│   ├── crypto.py            # At-rest encryption via Fernet + macOS Keychain
│   ├── profile.py           # Personal context loader
│   ├── profile_learner.py   # Auto-learning pipeline (Detector → Writer → Reviewer)
│   ├── retrieval.py         # Second Brain – ChromaDB + OpenAI Embeddings
│   ├── node_utils.py        # wrap_agent_node Decorator – last_agent_result/name
│   ├── utils.py             # extract_llm_text + shared helpers
│   ├── telemetry.py         # LangSmith tracing (optional)
│   ├── proactive/
│   │   ├── collector.py     # Entity extraction via Haiku, SHA256-upsert into ChromaDB
│   │   ├── pending.py       # Pending Items Tracker – priority score (due_date/mentions)
│   │   ├── linker.py        # Context Linking – entity_links collection, Cluster-API
│   │   ├── briefing_agent.py# Multi-Agent Briefing Orchestrator (asyncio.gather, 5s timeout)
│   │   ├── heartbeat.py     # Hourly heartbeat, time-triggered, 6h cooldown
│   │   └── context.py       # Proactive context aggregator for chat_agent
│   └── agents/
│       ├── chat_agent.py    # Dynamic prompt – claude.md + sessions + profile + retrieval + proactive
│       ├── memory_agent.py  # Profile updates, delete-aware _review_yaml
│       ├── vision_agent.py  # Image analysis via Claude Sonnet Vision
│       ├── computer.py      # Desktop control (screenshot, apps)
│       ├── terminal.py      # Shell commands, self-correction (MAX_RETRIES=2)
│       ├── file.py          # File operations (read/write/list)
│       ├── web.py           # Web search (Tavily+Brave) + fetch
│       ├── calendar.py      # Apple Calendar (read/create)
│       ├── reminder_agent.py
│       ├── whatsapp_agent.py# WhatsApp via whatsapp-web.js, HITL
│       └── clip_agent.py    # Knowledge Clipper → Obsidian
└── bot/
    ├── bot.py               # Telegram handler, HITL, retry logic, exception handler
    ├── auth.py              # User-Whitelist (fail-closed, RuntimeError if empty)
    ├── confirm.py           # HITL confirmation (full UUID)
    ├── transcribe.py        # Local Whisper transcription
    ├── tts.py               # OpenAI TTS (primary) + edge-tts (fallback)
    ├── search.py            # Local knowledge search
    ├── briefing.py          # Morning Briefing Scheduler (07:30)
    ├── reminders.py         # Reminder storage + proactive delivery
    ├── heartbeat_scheduler.py # Hourly proactivity scheduler
    ├── health_check.py      # Daily health check (06:00, 11 components)
    ├── session_summary.py   # Daily session summary (23:30), TOCTOU-safe
    ├── party_report.py      # Weekend party report (Wednesday 20:00)
    ├── whatsapp.py          # WhatsApp bridge (Node.js process, QR via Telegram)
    └── local_api.py         # Local bot API (status, diagnostics)
```

**Stack:**
- Claude Sonnet – AI backbone (configurable via `ANTHROPIC_MODEL_SONNET`, default: `claude-sonnet-4-6`)
- Claude Haiku – supervisor routing + LLM-Guard (configurable via `ANTHROPIC_MODEL_HAIKU`, default: `claude-haiku-4-5-20251001`)
- LangGraph `1.1.x` – multi-agent state machine with AsyncSqliteSaver
- python-telegram-bot `22.x` – Telegram interface
- openai-whisper – local speech transcription (preloaded at startup)
- OpenAI TTS API – primary TTS (nova, configurable via `OPENAI_TTS_VOICE`, directly via httpx)
- OpenAI Embeddings API – text-embedding-3-small for Second Brain (directly via httpx)
- edge-tts – TTS fallback (de-DE-KatjaNeural)
- ChromaDB `1.5.x` – local vector database for Second Brain (~/.fabbot/chroma/)
- aiosqlite – async SQLite for persistent memory
- Tavily + Brave Search – web search (directly via httpx REST)
- Google Calendar API – calendar_agent via google-api-python-client
- cryptography + keyring – At-Rest-Encryption via Fernet + macOS Keychain
- Python 3.11+, macOS

### Data Stores

FabBot distributes persistent state across 6 stores:

| Store | Path | Content | Written by | Backup |
|-------|------|---------|------------|--------|
| `personal_profile.yaml` | `~/personal_profile.yaml` | Profile, preferences, learning entries (Fernet-encrypted) | `memory_agent`, `profile_learner` | yes |
| LangGraph Checkpoints | `~/.fabbot/memory.db` | Conversation checkpoints (SQLite, AsyncSqliteSaver) | LangGraph internal | yes |
| ChromaDB | `~/.fabbot/chroma/` | Embeddings, entities, entity_links (3 collections) | `retrieval`, `collector`, `linker` | yes |
| Bot Instructions | `~/Documents/Wissen/claude.md` | Persistent system instructions for chat_agent | manual / `memory_agent` | optional |
| Sessions | `~/Documents/Wissen/sessions/` | Daily conversation summaries (Markdown) | `session_summary` | optional |
| Reminders | `~/.fabbot/reminders.json` | Due reminders with timestamp | `reminder_agent` | optional |

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
python main.py        # start bot
.venv/bin/python -m pytest tests/ -v      # Run tests (1245 tests)
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
| "Was ist heute für ein Datum?" | `chat_agent` → `26.04.2026, 14:30 Uhr` |
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
/start /ask /clip /search /remember /briefing /done /mute_proactive /tts on|off /stop /status /auditlog
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

- **Phase 1–19** ✅ Foundation – Telegram bot, multi-agent supervisor, terminal/file/web/calendar agents, security guard, audit log, CI, TTS, persistent memory
- **Phase 20–30** ✅ Hardening – async fixes, morning briefing, HITL improvements, code quality, watchdog
- **Phase 31–40** ✅ Personal Context – personal_profile.yaml, /remember, auto-learning pipeline, 529 retry
- **Phase 41–50** ✅ Security & Memory – security test suite, memory agent, media tracking, at-rest encryption
- **Phase 51–60** ✅ Vision & TTS – Vision Agent, session summary, ElevenLabs→OpenAI TTS migration, weekend party report, dedup fix
- **Phase 61–70** ✅ claude.md & TTS – persistent bot instructions, learnable via "Merke dir das", TTS hardening, model via .env
- **Phase 71–80** ✅ Routing & Knowledge – supervisor routing fix, Second Brain (ChromaDB), natural language passthrough, morning briefing fix, stability fixes
- **Phase 81–90** ✅ WhatsApp & Security – WhatsApp Agent (whatsapp-web.js), auth fail-closed, rate limiting, LangSmith telemetry, watchdog fixes
- **Phase 91–99** ✅ Hardening & Refactor – crypto/audit/llm hardening, GitHub Issues workflow, Prompt-Cache TTL 60s, model validation at startup, memory_agent Registry-Pattern, deque dedup, get_current_datetime() Europe/Berlin, State-Transfer last_agent_result/last_agent_name
- **Phase 100–116** ✅ Stabilization & Bug-Fixes – Duplicate Responses fix, weather via wttr.in, drop_pending_updates + ThrottleInterval, _invoke_locks Race Condition, web_agent weather routing, Supervisor Early-Return, memory_agent generic delete, computer_agent Regex-Intent-Parse, _review_yaml delete-aware (all categories), Sonnet default to claude-sonnet-4-6, _MODEL_PATTERN optional date; 881 tests green
- **Phase 117–124** ✅ Bug-Fixes & Refactoring – screenshot context for chat_agent, web_agent AIMessage-Fix, Preferences system with auto-categorization, Supervisor routing refactor + prompt leak fix, MemoryUpdateResult-Refactor, bot_instruction delete routing, memory_agent clarify-Fix, Duplicate-Scheduler-Fix (launchd/caffeinate)
- **Phase 125–129** ✅ Code-Review & Hardening – file_agent expanduser + launchd HOME, terminal_agent free-text block, GraphRecursionError handler, Scheduler done_callbacks, web.py prompt injection escaping, subprocess env isolation, watchdog/auditlog/file size fixes, weather location from profile
- **Phase 130–139** ✅ Security & Routing Hardening – DNS-Rebinding IPv6, web.py Exception-Handler (404/503/DNS), LLM-Guard Weighted Scoring (strong/weak patterns), PID-File instance check, Health Check expanded to 11 components, _PRE_ROUTING_RULES table, wrap_agent_node Decorator, _invoke_with_retry backoff on APIConnectionError + RateLimitError
- **Phase 140–149** ✅ Second Brain & Proactivity – Context Collector (Haiku, ChromaDB entities), Pending Items Tracker (priority score), Morning Briefing on ChromaDB, Context Linking (entity_links), Multi-Agent Briefing Orchestrator (asyncio.gather + 5s timeout), Heartbeat + trigger-based proactivity (6h cooldown), Proactive Context Aggregator, terminal_agent Self-Correction (MAX_RETRIES=2), Retrieval Hardening (rolling window, sessions from index)
- **Phase 150–157** ✅ Stabilization & Hardening – briefing timeouts per section, calendar system filter, model IDs centralized (.env), Heartbeat with profile/memory/session context, /phase bot restart, forget article pattern fix, PHOTO pre-routing deterministic + agent registration consolidated, RuntimeError handler + Proto-Import top-level + cleanup_checkpoints concurrency guard; 1245 tests green
- **Phase 158** ✅ system_agent via psutil (#37) + restricted None-Check (#106) – CPU/RAM/Disk metrics without shell, alert thresholds, pre-routing, restricted decorator secured against anonymous updates; 1257 tests green
- **Phase 159** ✅ API Health-Check in Heartbeat (#102) – HEAD ping on Anthropic/Tavily/Brave, state-change alerts (up→down, down→up), fail-safe, independent of proactive cooldown; 1272 tests green
- **Phase 160** ✅ Startup message on bot restart – "🔄 Bot gestartet." after full initialization, fail-safe; #103/#104 set to low-priority, #105 already covered via launchd
- **Phase 161** ✅ Bug-Fixes #110–#114 – _caff atexit cleanup, bare except with log in watchdog._load_state(), _ALERT_DELAY_MINUTES off-by-one, duplicate subprocess calls cached, validate_tts_config public API; 1272 tests green
- **Phase 162** ✅ Intent extraction (#107) – Haiku extracts commitments from user messages ("ich muss/sollte/wollte X"), stores as Pending Items with due-date in ChromaDB; fire-and-forget, fail-safe, deduplication via SHA256; 1291 tests green
- **Phase 163** ✅ Collector Refactor – "intent" removed from ENTITY_TYPES, Collector responsible for person/place/event/task, IntentExtractor exclusive for commitments; clean separation of concerns, no more noise from trivial intents; 1291 tests green
- **Phase 164** ✅ Anthropic Prompt Caching – static system prompt (chat_agent + supervisor) marked with cache_control; Anthropic caches server-side ~90% cheaper; CHAT_CONTEXT_WINDOW reduced to 20; 1296 tests green
- **Phase 165** ✅ Context-Injection-Fix – proactive messages (Morning Briefing, Heartbeat) written into LangGraph state via aupdate_state after sending; chat_agent now aware of its own proactive context; emoji ban and filler-sentence ban explicit in _CHAT_PROMPT_BASE; 1296 tests green
- **Phase 166** ✅ Supervisor Context Routing – last_agent_name injected as [Last Agent: X] prefix into routing HumanMessage; Supervisor correctly identifies follow-up questions as chat_agent instead of re-calling specialized agents; cache optimization from Phase 164 preserved; 1296 tests green
- **Phase 167** ✅ Weather Forecast by Day – _get_weather() detects today/tomorrow/day-after-tomorrow from query and returns correct wttr.in weather[] index; previously "Wie wird das Wetter morgen?" always returned today's data; 1296 tests green
- **Phase 168** ✅ web.py hourly Bounds-Check + _forecast_day_index Docstring – hourly[4] with len()-Guard against IndexError on incomplete wttr.in data; implicit gate dependency to _is_weather_query() documented in docstring; 1296 tests green
- **Phase 169** ✅ Photo follow-up questions: Supervisor-Guard + vision_agent_name in State – deterministic guard prevents misrouting to memory_agent after photo context; _update_vision_memory sets last_agent_name=vision_agent so follow-up questions go to chat_agent (with history) instead of stub; 1296 tests green
- **Phase 170** ✅ Security Fixes + Log Cleanup – prompt injection protection in supervisor.py (German patterns, list-length limit, last_agent_name whitelist), grep file path check in terminal_agent, injection guard in memory_agent, WhatsApp whitelist leak removed; httpx/Telegram log spam reduced, conflict traceback filter; 1296 tests green
- **Phase 171** ✅ Bandit scan in CI + weekly pip-audit agent – bandit (HIGH severity) as static security scan step in test.yml; weekly-audit.yml automatically opens GitHub Issue on Mondays on pip-audit findings; 1296 tests green
- **Phase 172** ✅ Multi-instance fix + news freshness – fcntl.flock instead of PID-File prevents race condition on parallel starts; conflict handler terminates instance via app.stop() instead of just logging; Tavily news query with current date + topic=news; cmd_briefing logging added; 1296 tests green
- **Phase 173** ✅ _invoke_locks LRU-Eviction + Scheduler Liveness + Data Store Diagram – _invoke_locks as OrderedDict with LRU-Eviction (max 100 entries); Scheduler tasks named (scheduler:briefing etc.); _check_schedulers() as 13th health check component; data store table (6 stores) added to README; 1296 tests green
- **Phase 174** ✅ ruff CI + codebase formatting – lint.yml with ruff check + ruff format --check; pyproject.toml with E402/E741 ignore rules; 130 auto-fixed violations + 17 manual fixes; entire codebase reformatted (91 files); 1296 tests green
- **Phase 175** ✅ Supervisor routing pipeline refactor – supervisor_node() split into ordered _pre_route() strategy pipeline (image_data → AIMessage early-return → vision follow-up → pre-routing table → LLM); each strategy is its own function; _PRE_ROUTE_PIPELINE list makes order explicit; 1296 tests green
- **Phase 176** ✅ Self-Healing Watchdog Auto-Restart – watchdog.py extended with _attempt_restart() via launchctl kickstart; configurable delay (WATCHDOG_RESTART_DELAY_MIN), max attempts (WATCHDOG_MAX_RESTARTS), feature flag (WATCHDOG_AUTO_RESTART); Telegram alerts on attempt/success/failure; state tracks restart_count + last_restart_at; 1310 tests green

---

## License

Private project – not licensed for public use.
