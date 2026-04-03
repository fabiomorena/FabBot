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
| ✅ | Knowledge Clipper – `/clip <URL>` saves articles as Markdown to Obsidian vault |
| ✅ | Knowledge Search – `/search <term>` searches saved notes locally |
| ✅ | Persistent Conversation Memory – SQLite via AsyncSqliteSaver, survives restarts |
| ✅ | Chat Agent – answers follow-up questions directly from conversation history |
| ✅ | Text-to-Speech – responses spoken via Mac speaker + Telegram voice message |
| ✅ | TTS Toggle – `/tts on\|off` or `TTS_ENABLED` env var |
| ✅ | TTS Stop – `/stop` kills running afplay immediately |
| ✅ | German date format – `18.03.2026, 19:06 Uhr` |
| ✅ | GitHub Actions CI – runs 329 pytest tests on every push |
| ✅ | Code Quality – `__SUSPICIOUS__`-Präfix entfernt, Double-Init Guard, YAML Lock, Rate-Limit Eviction |
| ✅ | Security Hardening – FORBIDDEN_ARGS per-Token, `echo` entfernt, `sanitize_command()`, `cwd=home` |
| ✅ | Test suite – 351 pytest tests |
| ✅ | Personal Context Layer – `personal_profile.yaml` injected into all agents |
| ✅ | `/remember` – save personal notes to profile live from Telegram |
| ✅ | Auto-Learning – 3-stage pipeline (Detector → Writer → Reviewer) updates profile automatically |
| ✅ | 529 Retry – exponential backoff (2s/4s/8s) on Anthropic overload |
| ✅ | Memory Agent – explicit profile updates via natural language (places, people, projects, custom) |
| ✅ | Hybrid profile structure – fixed sections + free `custom` section + `places` + `media` |
| ✅ | Media tracking – songs, films, podcasts, books stored as structured `media` entries |
| ✅ | Health Check – daily 06:00 system status report (Terminal, API, Web, Calendar, Profile, DB) |
| ✅ | Vision Agent – photo analysis via Claude Sonnet Vision with HITL (objects, OCR, scene description) |
| ✅ | At-Rest-Encryption – `personal_profile.yaml` verschlüsselt via Fernet, Key im macOS Keychain |
| ✅ | Context Trim – `chat_agent` begrenzt LLM-Call auf `CHAT_CONTEXT_WINDOW` Messages (default 40, via `.env`) |

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
├── .github/
│   └── workflows/
│       └── test.yml         # GitHub Actions CI – pip cache + pytest
├── tests/
│   └── test_security_terminal.py  # pytest suite (329 tests)
├── agent/
│   ├── supervisor.py        # Supervisor – Haiku routing, AsyncSqliteSaver
│   ├── state.py             # LangGraph AgentState
│   ├── llm.py               # get_llm() Sonnet + get_fast_llm() Haiku
│   ├── protocol.py          # Protocol constants (HITL magic strings)
│   ├── security.py          # Two-stage injection guard, rate limiting, homoglyph normalization
│   ├── audit.py             # Tamper-evident audit log
│   ├── profile.py           # Personal context loader (YAML → agent prompts)
│   ├── profile_learner.py   # Auto-learning pipeline (Detector/Writer/Reviewer)
│   └── agents/
│       ├── chat_agent.py    # Context-aware conversation agent (no tools)
│       ├── memory_agent.py  # Explicit profile updates (places, people, custom, delete)
│       ├── vision_agent.py  # Photo analysis via Claude Sonnet Vision
│       ├── computer.py      # Desktop control (validated input)
│       ├── terminal.py      # Shell command execution, German date format
│       ├── file.py          # File operations
│       ├── web.py           # Web search & fetch with content isolation
│       ├── calendar.py      # Calendar management
│       ├── reminder_agent.py # SQLite-based reminders, natural language
│       └── clip_agent.py    # URL clipper with content isolation
└── bot/
    ├── bot.py               # Telegram handlers, HITL TTS, post_init/post_shutdown hooks
    ├── auth.py              # User whitelist (cached at startup, RuntimeError if empty)
    ├── confirm.py           # Human-in-the-loop confirmation (full UUID)
    ├── transcribe.py        # Local Whisper transcription (voice → text)
    ├── tts.py               # Text-to-Speech (edge-tts + afplay + send_voice + stop)
    ├── search.py            # Local knowledge base search
    ├── briefing.py          # Morning briefing scheduler (07:30 daily)
    ├── reminders.py         # Reminder storage + proactive delivery
    └── health_check.py      # Daily health check scheduler (06:00, 6 components)
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
- [cryptography](https://cryptography.io) + [keyring](https://github.com/jaraco/keyring) – At-Rest-Encryption via Fernet, Key im macOS Keychain
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
pytest tests/ -v      # Run tests (329 tests)
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

**Stage 2 – LLM-Guard via Haiku (only when score > 0):** Returns `SAFE` or `INJECTION`. Fail-open: Guard errors never block legitimate messages.

### Content isolation

Fetched web content is wrapped in `<document>` tags before LLM processing. HTML comments stripped. Explicit instruction to ignore content inside document tags.

### Vision Agent security
Photo captions are sanitized through the full injection guard pipeline before analysis. No identification of private individuals. Audit log records metadata only (no image data).

### Additional layers
User whitelist · Homoglyph normalization · Rate limiting · Terminal allowlist · Shell operator blocking · Path traversal guard · SSRF protection · TOCTOU re-validation · HITL confirmation · Audit log

---

## Performance

| Component | Model | Reason |
|---|---|---|
| Supervisor (routing) | claude-haiku-4-5 | ~4x faster, simple classification |
| LLM-Guard (security) | claude-haiku-4-5 | fast, cost-efficient screening |
| All agents (answers) | claude-sonnet-4 | full quality for responses |
| Vision Agent | claude-sonnet-4 | multimodal vision capability |

~40% faster response time vs. Sonnet-only.

---

## Testing

```bash
pytest tests/ -v   # 351 tests
```

**Test-Infrastruktur:**
- `tests/conftest.py` – autouse Fixtures isolieren globalen State zwischen Tests (`_rate_limit_store`, `_tts_enabled`, `_current_afplay`, `_profile_cache`, `_pending`)
- Async-Tests mit Event-Poll-Loop statt `asyncio.sleep()` – keine Race Conditions unter CI-Last
- File-basierte Tests nutzen pytest `tmp_path` – automatisches Cleanup auch bei Testfehler

Coverage: security patterns · rate limiting · terminal allowlist · TTS · HITL filtering · memory prefix · _is_safe_output_path · _invoke_with_retry 529 · memory_agent · profile context · SSRF (web+clip) · sanitize_input_async LLM-Guard · calendar · reminders DB · auth decorator · synthesize · file path validation · computer input validation · web search format · slugify · execute_command · search/briefing · profile_learner _detect_new_info · confirm.py callback + timeout · health_check · chat_agent _clean_messages_for_chat Vision Safety Net

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
- **Phase 33** ✅ Morning Briefing – daily 07:30 Uhr, Wetter + Kalender + News + TTS
- **Phase 34** ✅ Quality – 88 tests, MEMORY filter tests, briefing TTS fix, BRIEFING_TIME validation
- **Phase 35** ✅ Reminder Agent – SQLite-based reminders, natural language, proactive delivery
- **Phase 36** ✅ Calendar fix – last HumanMessage only prevents list/create confusion
- **Phase 37** ✅ Reminder Agent fixes – correct time calculation, last HumanMessage only
- **Phase 38** ✅ Personal Context Layer – personal_profile.yaml, agent/profile.py, short+full context injection
- **Phase 39** ✅ /remember command – live note-saving to profile from Telegram, instant activation
- **Phase 40** ✅ Bug fixes – chat_agent profile priority, terminal last HumanMessage only, people section in context
- **Phase 41** ✅ Security tests – 11 Tests für _is_safe_output_path() Path-Traversal-Validierung
- **Phase 42** ✅ 529 Retry-Mechanismus – exponential backoff (2s/4s/8s), 3 Versuche, 6 Tests
- **Phase 43** ✅ Auto-Learning Pipeline – Haiku Detector + Python Writer + Haiku Reviewer + Fallback zu Note
- **Phase 44** ✅ Bug fix – web_agent last HumanMessage only, verhindert Natural-Language statt JSON
- **Phase 45** ✅ Memory Agent – explizite Profil-Updates via Sprache, Hybrid-Struktur (places/custom), 140 Tests
- **Phase 46** ✅ Media-Kategorie – Lieder/Filme/Podcasts/Bücher korrekt als `media` speichern
- **Phase 47** ✅ Supervisor Fix – memory_agent False-Positives mit JA/NEIN-Beispielen und Fallback-Regel
- **Phase 48** ✅ Health Check – täglich 06:00 Uhr, 6 Komponenten parallel geprüft, Telegram-Report
- **Phase 49** ✅ Stabilität + Code Quality – 329 Tests, security fixes, asyncio.Lock YAML, Rate-Limit Eviction, Round-Trip Check
- **Phase 50** ✅ Security Hardening – FORBIDDEN_ARGS per-Token, echo entfernt, sanitize_command(), cwd=home, Reviewer YAML 8000, filter-then-slice
- **Phase 51** ✅ Vision Agent – Foto-Analyse via Claude Sonnet Vision mit HITL, Objekterkennung, OCR, Szenenbeschreibung + Bug fixes (Briefing Kalender, auth RuntimeError, task refs, profile lock)
- **Phase 52** ✅ Watchdog – externer Bot-Monitor via cron, wttr.in Wetter, homoglyphs Library, pip-audit in CI, portable Pfade via Path.home(), CVE Fixes
- **Phase 53** ✅ Test-Resilienz – conftest.py autouse Fixtures, Event-Poll-Loop in async Tests, pytest tmp_path für file-basierte Tests
- **Phase 54** ✅ At-Rest-Encryption – personal_profile.yaml via Fernet (AES-128-CBC), Key im macOS Keychain, transparente Migration, 11 neue Tests
- **Phase 55** ✅ Vision System Fix – as_node Checkpoint-Fix, Supervisor Routing für Bild-Folgefragen (→ chat_agent), __VISION_RESULT__ Safety Net, 4 neue Tests (344 gesamt)
- **Phase 55b** ✅ Code Quality – Klammern in _filter_hitl_messages (Operator-Precedenz), toter __VISION_RESULT__ Branch in supervisor_node entfernt
- **Phase 56** ✅ AIMessage Echo-Fix – Folgefragen wiederholten letzte Antwort (result_state Index-Slice statt letzter AIMessage aus gesamtem State)
- **Phase 57** ✅ Context Trim – chat_agent begrenzt LLM-Call auf CHAT_CONTEXT_WINDOW Messages (default 40, via .env), SQLite bleibt vollständig

---

## License

Private project – not licensed for public use.
