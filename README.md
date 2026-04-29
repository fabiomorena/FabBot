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

**Operations** – GitHub Actions CI (1245 tests), 529 retry (exponential backoff 2s/4s/8s), prompt caching (claude.md + sessions + profile, TTL 60s), context trim (`CHAT_CONTEXT_WINDOW`, default 40), Whisper preload at startup, daily health check (06:00, 11 components), proactive heartbeat (stündlich, Cooldown 6h), model config via `.env` (`ANTHROPIC_MODEL_SONNET/HAIKU`)

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
│   ├── claude_md.py         # claude.md loader – persistente Bot-Instruktionen
│   ├── crypto.py            # At-rest encryption via Fernet + macOS Keychain
│   ├── profile.py           # Personal context loader
│   ├── profile_learner.py   # Auto-learning pipeline (Detector → Writer → Reviewer)
│   ├── retrieval.py         # Second Brain – ChromaDB + OpenAI Embeddings
│   ├── node_utils.py        # wrap_agent_node Decorator – last_agent_result/name
│   ├── utils.py             # extract_llm_text + shared helpers
│   ├── telemetry.py         # LangSmith tracing (optional)
│   ├── proactive/
│   │   ├── collector.py     # Entitäten-Extraktion via Haiku, SHA256-Upsert in ChromaDB
│   │   ├── pending.py       # Pending Items Tracker – Prioritätsscore (due_date/mentions)
│   │   ├── linker.py        # Context Linking – entity_links Collection, Cluster-API
│   │   ├── briefing_agent.py# Multi-Agent Briefing Orchestrator (asyncio.gather, 5s Timeout)
│   │   ├── heartbeat.py     # Stündlicher Heartbeat, Zeit-Trigger, Cooldown 6h
│   │   └── context.py       # Proaktiver Kontext-Aggregator für chat_agent
│   └── agents/
│       ├── chat_agent.py    # Dynamic prompt – claude.md + sessions + profile + retrieval + proactive
│       ├── memory_agent.py  # Profil-Updates, delete-aware _review_yaml
│       ├── vision_agent.py  # Bildanalyse via Claude Sonnet Vision
│       ├── computer.py      # Desktop-Steuerung (Screenshot, Apps)
│       ├── terminal.py      # Shell-Befehle, Self-Correction (MAX_RETRIES=2)
│       ├── file.py          # Dateioperationen (read/write/list)
│       ├── web.py           # Web-Suche (Tavily+Brave) + Fetch
│       ├── calendar.py      # Apple Kalender (lesen/erstellen)
│       ├── reminder_agent.py
│       ├── whatsapp_agent.py# WhatsApp via whatsapp-web.js, HITL
│       └── clip_agent.py    # Knowledge Clipper → Obsidian
└── bot/
    ├── bot.py               # Telegram-Handler, HITL, Retry-Logik, Exception-Handler
    ├── auth.py              # User-Whitelist (fail-closed, RuntimeError if empty)
    ├── confirm.py           # HITL-Bestätigung (full UUID)
    ├── transcribe.py        # Lokale Whisper-Transkription
    ├── tts.py               # OpenAI TTS (primär) + edge-tts (Fallback)
    ├── search.py            # Lokale Wissenssuche
    ├── briefing.py          # Morning Briefing Scheduler (07:30)
    ├── reminders.py         # Reminder-Storage + proaktive Zustellung
    ├── heartbeat_scheduler.py # Stündlicher Proaktivitäts-Scheduler
    ├── health_check.py      # Daily Health Check (06:00, 11 Komponenten)
    ├── session_summary.py   # Daily Session Summary (23:30), TOCTOU-sicher
    ├── party_report.py      # Weekend Party Report (Mittwoch 20:00)
    ├── whatsapp.py          # WhatsApp Bridge (Node.js-Prozess, QR via Telegram)
    └── local_api.py         # Lokale Bot-API (Status, Diagnose)
```

**Stack:**
- Claude Sonnet – AI backbone (konfigurierbar via `ANTHROPIC_MODEL_SONNET`, default: `claude-sonnet-4-6`)
- Claude Haiku – supervisor routing + LLM-Guard (konfigurierbar via `ANTHROPIC_MODEL_HAIKU`, default: `claude-haiku-4-5-20251001`)
- LangGraph `1.1.x` – multi-agent state machine with AsyncSqliteSaver
- python-telegram-bot `22.x` – Telegram interface
- openai-whisper – lokale Sprachtranskription (preloaded at startup)
- OpenAI TTS API – primary TTS (nova, konfigurierbar via `OPENAI_TTS_VOICE`, direkt via httpx)
- OpenAI Embeddings API – text-embedding-3-small für Second Brain (direkt via httpx)
- edge-tts – TTS fallback (de-DE-KatjaNeural)
- ChromaDB `1.5.x` – lokale Vektordatenbank für Second Brain (~/.fabbot/chroma/)
- aiosqlite – async SQLite for persistent memory
- Tavily + Brave Search – web search (direkt via httpx REST)
- Google Calendar API – calendar_agent via google-api-python-client
- cryptography + keyring – At-Rest-Encryption via Fernet + macOS Keychain
- Python 3.11+, macOS

### Datenspeicher

FabBot verteilt persistenten State auf 6 Speicher:

| Store | Pfad | Inhalt | Wer schreibt | Backup |
|-------|------|--------|--------------|--------|
| `personal_profile.yaml` | `~/personal_profile.yaml` | Profil, Präferenzen, Lerneinträge (Fernet-verschlüsselt) | `memory_agent`, `profile_learner` | ja |
| LangGraph Checkpoints | `~/.fabbot/memory.db` | Gesprächs-Checkpoints (SQLite, AsyncSqliteSaver) | LangGraph intern | ja |
| ChromaDB | `~/.fabbot/chroma/` | Embeddings, Entitäten, entity_links (3 Collections) | `retrieval`, `collector`, `linker` | ja |
| Bot-Instruktionen | `~/Documents/Wissen/claude.md` | Persistente System-Instruktionen für chat_agent | manuell / `memory_agent` | optional |
| Sessions | `~/Documents/Wissen/sessions/` | Tägliche Gesprächszusammenfassungen (Markdown) | `session_summary` | optional |
| Reminder | `~/.fabbot/reminders.json` | Fällige Erinnerungen mit Zeitstempel | `reminder_agent` | optional |

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
python main.py        # Bot starten
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
- **Phase 61–70** ✅ claude.md & TTS – persistente Bot-Instruktionen, lernbar via "Merke dir das", TTS hardening, model via .env
- **Phase 71–80** ✅ Routing & Knowledge – supervisor routing fix, Second Brain (ChromaDB), natural language passthrough, morning briefing fix, stability fixes
- **Phase 81–90** ✅ WhatsApp & Security – WhatsApp Agent (whatsapp-web.js), auth fail-closed, rate limiting, LangSmith telemetry, watchdog fixes
- **Phase 91–99** ✅ Hardening & Refactor – crypto/audit/llm hardening, GitHub Issues workflow, Prompt-Cache TTL 60s, model-validierung beim Start, memory_agent Registry-Pattern, deque dedup, get_current_datetime() Europe/Berlin, State-Transfer last_agent_result/last_agent_name
- **Phase 100–116** ✅ Stabilisierung & Bug-Fixes – Duplicate Responses fix, Wetter via wttr.in, drop_pending_updates + ThrottleInterval, _invoke_locks Race Condition, web_agent Wetter-Routing, Supervisor Early-Return, memory_agent delete generisch, computer_agent Regex-Intent-Parse, _review_yaml delete-aware (alle Kategorien), Sonnet-Default auf claude-sonnet-4-6, _MODEL_PATTERN optional Datum; 881 Tests grün
- **Phase 117–124** ✅ Bug-Fixes & Refactoring – Screenshot-Kontext für chat_agent, web_agent AIMessage-Fix, Preferences-System mit Auto-Kategorisierung, Supervisor-Routing-Umbau + Prompt-Leak-Fix, MemoryUpdateResult-Refactor, bot_instruction-Delete-Routing, memory_agent clarify-Fix, Duplicate-Scheduler-Fix (launchd/caffeinate)
- **Phase 125–129** ✅ Code-Review & Hardening – file_agent expanduser + launchd HOME, terminal_agent Freitext-Block, GraphRecursionError-Handler, Scheduler done_callbacks, web.py Prompt-Injection-Escaping, subprocess Env-Isolation, watchdog/auditlog/file-Größe-Fixes, Wetter-Standort aus Profil
- **Phase 130–139** ✅ Security & Routing Hardening – DNS-Rebinding IPv6, web.py Exception-Handler (404/503/DNS), LLM-Guard Weighted Scoring (starke/schwache Patterns), PID-File Instanzencheck, Health Check auf 11 Komponenten, _PRE_ROUTING_RULES-Tabelle, wrap_agent_node Decorator, _invoke_with_retry Backoff auf APIConnectionError + RateLimitError
- **Phase 140–149** ✅ Second Brain & Proaktivität – Context Collector (Haiku, ChromaDB entities), Pending Items Tracker (Prioritätsscore), Morning Briefing auf ChromaDB, Context Linking (entity_links), Multi-Agent Briefing Orchestrator (asyncio.gather + 5s Timeout), Heartbeat + Trigger-basierte Proaktivität (Cooldown 6h), Proaktiver Kontext-Aggregator, terminal_agent Self-Correction (MAX_RETRIES=2), Retrieval-Hardening (Rolling Window, Sessions aus Index)
- **Phase 150–157** ✅ Stabilisierung & Hardening – Briefing-Timeouts sektionsspezifisch, Kalender System-Filter, Modell-IDs zentral (.env), Heartbeat mit Profil/Memory/Session-Kontext, /phase Bot-Neustart, vergiss-Artikel-Pattern-Fix, FOTO-Pre-Routing deterministisch + Agent-Registrierung konsolidiert, RuntimeError-Handler + Proto-Import Top-Level + cleanup_checkpoints Concurrency-Guard; 1245 Tests grün
- **Phase 158** ✅ system_agent via psutil (#37) + restricted None-Check (#106) – CPU/RAM/Disk-Metriken ohne Shell, Alert-Schwellwerte, Pre-Routing, restricted-Decorator gegen anonymous updates abgesichert; 1257 Tests grün
- **Phase 159** ✅ API-Health-Check im Heartbeat (#102) – HEAD-Ping auf Anthropic/Tavily/Brave, Zustandsänderungs-Alerts (up→down, down→up), fail-safe, unabhängig vom proaktiven Cooldown; 1272 Tests grün
- **Phase 160** ✅ Startup-Nachricht bei Bot-Neustart – "🔄 Bot gestartet." nach vollständiger Initialisierung, fail-safe; #103/#104 auf low-priority, #105 via launchd bereits abgedeckt
- **Phase 161** ✅ Bug-Fixes #110–#114 – _caff atexit-Cleanup, bare except mit Log in watchdog._load_state(), _ALERT_DELAY_MINUTES Off-by-one, doppelte subprocess-Calls gecacht, validate_tts_config öffentliche API; 1272 Tests grün
- **Phase 162** ✅ Intentions-Extraktion (#107) – Haiku extrahiert Commitments aus User-Nachrichten ("ich muss/sollte/wollte X"), speichert als Pending Items mit Due-Date in ChromaDB; fire-and-forget, fail-safe, Deduplizierung via SHA256; 1291 Tests grün
- **Phase 163** ✅ Collector-Refactor – "intent" aus ENTITY_TYPES entfernt, Collector zuständig für person/place/event/task, IntentExtractor exklusiv für Commitments; saubere Aufgabentrennung, kein Rauschen mehr durch triviiale Intents; 1291 Tests grün
- **Phase 164** ✅ Anthropic Prompt Caching – statischer System-Prompt (chat_agent + supervisor) mit cache_control markiert; Anthropic cached server-seitig ~90% günstiger; CHAT_CONTEXT_WINDOW auf 20 reduziert; 1296 Tests grün
- **Phase 165** ✅ Context-Injection-Fix – proaktive Nachrichten (Morning Briefing, Heartbeat) werden nach dem Senden via aupdate_state in den LangGraph-State geschrieben; chat_agent kennt nun seinen eigenen proaktiven Kontext; Emoji-Verbot und Füllsatz-Verbot explizit in _CHAT_PROMPT_BASE; 1296 Tests grün
- **Phase 166** ✅ Supervisor Kontext-Routing – last_agent_name wird als [Letzter Agent: X]-Präfix in die Routing-HumanMessage injiziert; Supervisor erkennt Folgefragen korrekt als chat_agent statt erneut spezialisierte Agents zu rufen; Cache-Optimierung aus Phase 164 bleibt erhalten; 1296 Tests grün
- **Phase 167** ✅ Wetter-Forecast nach Tag – _get_weather() erkennt heute/morgen/übermorgen aus dem Query und liefert den richtigen wttr.in weather[]-Index; vorher lieferte "Wie wird das Wetter morgen?" immer nur Heute-Daten; 1296 Tests grün
- **Phase 168** ✅ web.py hourly Bounds-Check + _forecast_day_index Docstring – hourly[4] mit len()-Guard gegen IndexError bei unvollständigen wttr.in-Daten; implizite Gate-Abhängigkeit zu _is_weather_query() im Docstring dokumentiert; 1296 Tests grün
- **Phase 169** ✅ Foto-Folgefragen: Supervisor-Guard + vision_agent_name in State – deterministischer Guard verhindert Fehlrouting zu memory_agent nach Foto-Kontext; _update_vision_memory setzt last_agent_name=vision_agent damit Folgefragen zu chat_agent (mit History) gehen statt zum Stub; 1296 Tests grün
- **Phase 170** ✅ Security-Fixes + Log-Cleanup – Prompt-Injection-Schutz in supervisor.py (deutsche Pattern, list-Längen-Limit, last_agent_name-Whitelist), grep-Datei-Pfad-Check in terminal_agent, Injection-Guard in memory_agent, WhatsApp-Whitelist-Leak entfernt; httpx/Telegram-Log-Spam reduziert, Conflict-Traceback-Filter; 1296 Tests grün
- **Phase 171** ✅ Bandit-Scan in CI + wöchentlicher pip-audit-Agent – bandit (HIGH severity) als statischer Security-Scan-Step in test.yml; weekly-audit.yml öffnet montags automatisch GitHub Issue bei pip-audit-Findings; 1296 Tests grün
- **Phase 172** ✅ Multi-Instanz-Fix + News-Aktualität – fcntl.flock statt PID-File verhindert Race-Condition bei parallelen Starts; Conflict-Handler beendet Instanz via app.stop() statt nur zu loggen; Tavily-News-Query mit tagesaktuellem Datum + topic=news; cmd_briefing-Logging ergänzt; 1296 Tests grün

---

## License

Private project – not licensed for public use.
