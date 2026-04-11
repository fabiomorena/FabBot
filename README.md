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
| ✅ | Knowledge Clipper – /clip saves articles as Markdown to Obsidian vault |
| ✅ | Knowledge Search – /search searches saved notes locally |
| ✅ | Persistent Conversation Memory – SQLite via AsyncSqliteSaver, survives restarts |
| ✅ | Chat Agent – answers follow-up questions directly from conversation history |
| ✅ | Text-to-Speech – OpenAI TTS (primär) + edge-tts (Fallback), Mac speaker + Telegram voice |
| ✅ | TTS Toggle – /tts on|off or TTS_ENABLED env var |
| ✅ | TTS Stop – /stop kills running afplay immediately |
| ✅ | German date format – 18.03.2026, 19:06 Uhr |
| ✅ | GitHub Actions CI – runs 748 pytest tests on every push |
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
| ✅ | claude.md – persistente Bot-Instruktionen, in chat_agent System-Prompt injiziert, überlebt Context Trim |
| ✅ | Bot-Instruktionen lernbar – "Merke dir grundsätzlich..." schreibt direkt in claude.md, sofort aktiv |
| ✅ | "Merke dir das" – Bot formuliert aus vorheriger Aussage eine Bot-Instruktion → claude.md |
| ✅ | Security Hardening – TOCTOU-Fix in claude_md, Newline-Sanitizing, Haiku für Formulierung, Size-Warning |
| ✅ | claude.md Hardening – reload_claude_md async+Lock, FIFO-Trim max. 50 Einträge, Kommentar-Fix |
| ✅ | claude.md Hardening II – Lock-Granularität, robuster Heading-Regex H1-H6, Entry-Detection -, * und + |
| ✅ | OpenAI TTS – primärer Provider (nova/shimmer/...), edge-tts Fallback |
| ✅ | TTS Hardening – tmp_path Safety, gather return_exceptions, Startup-Validierung, Retry 429/503, lazy API-Key |
| ✅ | TTS Config Cleanup – _validate_tts_config nach Logger, lazy getters, Retry-Log spezifischer |
| ✅ | Modell via .env – ANTHROPIC_MODEL_SONNET/HAIKU konfigurierbar, lazy singleton |
| ✅ | Session Summary – tägliche Konversationszusammenfassung (23:30), Cross-Session-Kontext im chat_agent |
| ✅ | Immer aktuelle Antworten – web_agent als Fallback für Faktenfragen, chat_agent nur konversationell |
| ✅ | Fail-Closed LLM-Guard – Guard-Fehler blockiert statt durchzulassen |
| ✅ | Security Input-Handling – sanitize_input_async immer im try/except (text, photo, document) |
| ✅ | Dynamischer Chat-Prompt – claude.md + Session-Summaries + Profil pro Aufruf aktualisiert |
| ✅ | Natural Language Passthrough – LLM-Rückfragen in Agents direkt durchgeben statt Parse-Fehler |
| ✅ | Morning Briefing News – Haiku formatiert Tavily-Ergebnisse zu sauberen Bullets (keine Artefakte) |
| ✅ | Second Brain – ChromaDB + OpenAI text-embedding-3-small, semantisches Retrieval aus Notizen/Sessions/Profil |
| ✅ | /reindex – manuelle Neu-Indexierung der Wissensbasis |
| ✅ | WhatsApp Agent – Nachrichten senden via whatsapp-web.js Node.js Service (Whitelist-gesichert, HITL, QR via Telegram)
| ✅ | Stability Fixes – session_summary TOCTOU (Lock), _post_init ValueError-Guard, on_document Größen-Limit |
| ✅ | Auth Fail-Closed – ALLOWED_IDS leer → RuntimeError statt frozenset() |
| ✅ | handle_message_text aufgeteilt – _sanitize_and_validate, _invoke_and_extract, _dispatch_response |
| ✅ | _delete_thinking() – contextlib.suppress statt repetitiver try/except-Blöcke |
| ✅ | on_document Resize-Fix – _resize_image() konsistent wie on_photo |
| ✅ | TELEGRAM_CHAT_ID Env-Var – semantisch korrekte Chat-ID für Scheduler |
| ✅ | watchdog.py TELEGRAM_CHAT_ID – User-ID ≠ Chat-ID Fix |
| ✅ | watchdog.py python-dotenv – Quote-sicherer .env Parser |
| ✅ | start_service() HTTP-Polling – aktiv statt blindem sleep(3) |
| ✅ | agent_graph Type-Annotation + get_graph() Guard – RuntimeError statt AttributeError |
| ✅ | Rate-Limit-Stores thread-safe – threading.Lock für Read-Modify-Write |
| ✅ | Symlink-Schutz – file_agent blockiert Symlinks die aus der Allowlist herauszeigen |
| ✅ | DNS-Rebinding-Schutz – web_agent + clip_agent lösen Hostnamen auf und prüfen die IP |
| ✅ | Async Agents – alle Agents nutzen ainvoke, kein Event-Loop-Block mehr |
| ✅ | MAX_PATH_DEPTH=5 – begrenzt LLM-generierte Verzeichnistiefe in file_agent |
| ✅ | Query-Sanitization – web_agent begrenzt LLM-transformierte Suchanfragen auf 200 Zeichen |
| ✅ | YAML-Review fail-closed – INVALID blockiert Schreiben komplett (kein add_note Fallback) |
| ✅ | bot_instruction Validierung – Längenlimit 200 Zeichen + Forbidden-Pattern (Injection-Schutz) |
| ✅ | asyncio Task-Registry – create_task mit _background_tasks verhindert stilles GC-Killing |
| ✅ | English SHORT_CONFIRMATIONS – "thanks", "got it", "sounds good" etc. erkannt |
| ✅ | memory_agent Top-Level-Imports – yaml + profile imports auf Modulebene |
| ✅ | claude.md Path – ~/.fabbot/claude.md, Auto-Migration, Lock Race-Fix, Heading-Injection-Validierung |
| ✅ | profile.py Migration thread-safe – threading.Lock verhindert Doppel-Verschlüsselung beim Start |
| ✅ | cmd_clip Task-Registry – index_file Task in _background_tasks, kein stilles GC-Killing mehr |
| ✅ | Proto.MEMORY_VISION_MARKER – Magic String aus supervisor.py extrahiert, Single Source of Truth |
| ✅ | Proto.is_any_confirm() – CONFIRM_VISION ergänzt (war vergessen) |
| ✅ | crypto.py Keyring-Fehlerbehandlung – RuntimeError mit klarer Meldung statt kryptischer DBusException |
| ✅ | audit.py setup_audit_logger() – Module-Level FileHandler entfernt, Initialisierung in _post_init() |
| ✅ | llm.py Model-String-Validierung – _warn_if_unusual() loggt Warning bei Tippfehlern in .env |
| ✅ | .env.example vervollständigt – TELEGRAM_CHAT_ID, OPENAI_API_KEY, LangSmith-Vars dokumentiert |
| ✅ | Profil-Backup – `personal_profile.yaml.bak` vor jedem Schreibvorgang (Issue #1) |
| ✅ | Party Report – RA-Direktfetch, konkrete Datum-Queries, lockerere LLM-Extraktion, Trailing-Filter (Issue #5) |
| ✅ | Prompt-Cache – chat_agent cached claude.md + Sessions + Profil (TTL 60s), invalidate_chat_cache() aus profile/claude_md/session_summary (Issue #2) |
| ✅ | Model-Validierung beim Start – validate_models_on_startup() in _post_init(), RuntimeError bei ungültigem ANTHROPIC_MODEL_SONNET/HAIKU (Issue #6) |
| ✅ | stop_service() async – kein Event-Loop-Block im Shutdown-Hook (Issue #7) |
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
├── tests/test_security_terminal.py  # pytest suite (748 tests)
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
    ├── bot.py               # Telegram handlers, HITL, sanitize_input_async im try/except
    ├── auth.py              # User whitelist
    ├── confirm.py           # HITL confirmation
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
- Claude Sonnet – AI backbone (konfigurierbar via `ANTHROPIC_MODEL_SONNET`, default: `claude-sonnet-4-20250514`)
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
- rumps – macOS menubar app
- cryptography + keyring – At-Rest-Encryption via Fernet
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

### Run

```bash
python main.py        # Bot only
python menubar.py     # With menubar app
.venv/bin/python -m pytest tests/ -v      # Run tests (748 tests)
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
- **Phase 64** ✅ "Merke dir das" – kontextbasiertes Lernen von Bot-Instruktionen via Sonnet
- **Phase 65** ✅ Security & Hardening – TOCTOU, Newline-Sanitizing, get_fast_llm, Rekursions-Schutz, Import-Cleanup
- **Phase 66** ✅ claude.md Hardening – reload async, FIFO-Trim, thread-safety
- **Phase 67** ✅ claude.md Hardening II – Lock-Granularität, Regex, Entry-Detection, GIL-Kommentar
- **Phase 68** ✅ OpenAI TTS – ElevenLabs ersetzt, OPENAI_TTS_VOICE/MODEL konfigurierbar
- **Phase 69** ✅ TTS Hardening – tmp_path, gather, Validierung, Retry, lazy API-Key
- **Phase 70** ✅ TTS Config Cleanup – Validierung nach Logger, lazy getters, Retry-Log
- **Phase 71** ✅ Modell via .env – ANTHROPIC_MODEL_SONNET/HAIKU, lazy singleton, kein Neustart bei Modellwechsel nötig
- **Phase 72** ✅ Supervisor-Routing – web_agent als Fallback für alle Faktenfragen, chat_agent nur noch für rein konversationelle Nachrichten
- **Phase 73** ✅ Session Summary Writer – tägliche Zusammenfassung, Cross-Session-Kontext
- **Phase 74** ✅ Security & Prompt-Fix – fail-closed LLM-Guard, sanitize_input_async im try/except, dynamischer chat_agent-Prompt
- **Phase 75** ✅ Natural Language Passthrough – LLM-Rückfragen in terminal/web/file/calendar/reminder direkt durchgeben statt Parse-Fehler
- **Phase 76** ✅ Morning Briefing News-Fix – Haiku formatiert Tavily-Ergebnisse, filtert Artefakte (!Image, Bild-Labels)
- **Phase 77** ✅ Second Brain – ChromaDB + OpenAI text-embedding-3-small, semantisches Retrieval (Profil, Notizen, Sessions), /reindex Command
- **Phase 77b** ✅ Supervisor Routing Fix – Fragen über Notizen/Sessions korrekt zu chat_agent geroutet
- **Phase 78** ✅ retrieval.py Code Quality – Semaphore-Kommentar, httpx Client außerhalb Batch-Loop, SHA256-Hash für virtuelle Quellen
- **Phase 79** ✅ claude.md aus ChromaDB entfernt – direkte Prompt-Injektion übernimmt vollständig, keine Doppel-Injektion
- **Phase 80** ✅ Stability Fixes
- **Phase 81** ✅ WhatsApp Agent – Playwright, Session-persistent, Whitelist, HITL – session_summary TOCTOU (Double-Checked Locking), _post_init ValueError-Guard, on_document Größen-Limit
- **Phase 82** ✅ /wa_contact Command – add/remove/list WhatsApp-Kontakte, verschlüsselte YAML
- **Phase 83** ✅ whatsapp-web.js Microservice – Node.js ersetzt Playwright, Session via ~/.fabbot/wa_ready, QR-Code via Telegram, Markdown-Fix vor Download
- **Phase 84** ✅ Security & Code Quality – auth fail-closed, handle_message_text aufgeteilt, contextlib.suppress, on_document resize-fix, TELEGRAM_CHAT_ID
- **Phase 85** ✅ Rate Limiting nach Aktionstyp (destructive: max 10/min) + LangSmith Telemetry opt-in via .env
- **Phase 86** ✅ Watchdog Fixes (Chat-ID, python-dotenv, _ALERT_DELAY_MINUTES) + start_service() Polling
- **Phase 87** ✅ agent_graph Type-Annotation, get_graph() Guard, threading.Lock Rate-Limit
- **Phase 88** ✅ Security & Async Hardening – Symlink-Bypass, DNS-Rebinding, ainvoke in allen Agents, Pfadtiefe, Query-Sanitization
- **Phase 89** ✅ Security Fixes – YAML fail-closed, bot_instruction Validation, Task-Registry, English Confirmations
- **Phase 90** ✅ claude_md Hardening – Path ~/.fabbot/, Migration, Lock Race-Fix, Heading-Injection-Schutz
- **Phase 91** ✅ Stability & Protocol Fixes – Migration Lock, Task-Registry, MEMORY_VISION_MARKER, is_any_confirm CONFIRM_VISION
- **Phase 92** ✅ Security & Code Quality – crypto.py Keyring-Fehlerbehandlung, audit.py setup_audit_logger(), llm.py Model-String-Warn, .env.example vervollständigt
- **Phase 93** ✅ Code Quality – Profil-Backup vor destruktivem Schreiben (`profile.py`), Issue #1
- **Phase 94** ✅ Code Quality & Fixes – Profil-Backup (#1), api_key.strip() (#9), sudo-Pattern (#4), Party Report verbessert (#5)
- **Phase 95** ✅ Performance – Prompt-Cache in chat_agent (TTL 60s, 0 Disk-Reads nach erstem Build, invalidate_chat_cache() aus profile.py / claude_md.py / session_summary.py), Issue #2
- **Phase 95b** ✅ Security – Harte Model-String-Validierung beim Start (validate_models_on_startup(), RuntimeError fail-closed), Issue #6
- **Phase 95c** ✅ Code Quality – stop_service() async def, await in _post_shutdown(), Issue #7
---




## License

Private project – not licensed for public use.
