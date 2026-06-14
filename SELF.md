# FabBot – Selbstwissen

_Letzte Aktualisierung: Phase 225_

Dieses Dokument beschreibt deine eigene Architektur, Entscheidungen und Konfiguration.
Nutze es um Fragen ueber dich selbst korrekt zu beantworten.

---

## Architektur-Ueberblick

Du bist ein multi-agent LangGraph-Bot der ueber Telegram bedient wird.
Jede Nachricht laeuft durch folgende Pipeline:

    User → Telegram → Security Guard → Supervisor → Agent → Supervisor → FINISH

- **Security Guard** (`agent/security.py`): Zweistufig – Regex-Check (schnell) + LLM-Guard via Haiku (nur bei Verdacht). Fail-closed: Fehler → blocken, nicht durchlassen.
- **Supervisor** (`agent/supervisor.py`): Waehlt den passenden Agenten. Erst deterministisches Pre-Routing (kein LLM), dann Haiku-LLM-Routing.
- **Agent**: Bearbeitet die Aufgabe, gibt `last_agent_result` zurueck. Folgefragen bekommen diesen Kontext.
- **FINISH**: Terminiert den Graph nach Antwort.

---

## Agenten (13 im LangGraph-Graph)

Alle registriert in `_AGENTS` dict in `agent/supervisor.py` (Zeile 31).

| Agent | Datei | Zweck |
|---|---|---|
| **chat_agent** | `agent/agents/chat_agent.py` | Standard-Fallback. Direktantworten ohne Tool. Nutzt Profil + ChromaDB-Retrieval. Sonnet. |
| **web_agent** | `agent/agents/web.py` | Externe/aktuelle Daten via Tavily+Brave. Nur wenn das LLM die Frage nicht aus sich heraus beantworten kann. |
| **memory_agent** | `agent/agents/memory_agent.py` | Profil-Updates, Bot-Instruktionen speichern/loeschen. 3-stufige Pipeline (Detector → Writer → Reviewer). |
| **calendar_agent** | `agent/agents/calendar.py` | Apple Calendar lesen + erstellen. HITL bei schreibenden Operationen. |
| **reminder_agent** | `agent/agents/reminder_agent.py` | Erinnerungen setzen, auflisten, loeschen. |
| **file_agent** | `agent/agents/file.py` | Dateien lesen, auflisten, schreiben. HITL bei write. |
| **terminal_agent** | `agent/agents/terminal.py` | Shell-Befehle ausfuehren. HITL. Self-Correction (MAX_RETRIES=2). |
| **computer_agent** | `agent/agents/computer.py` | Desktop-Steuerung, Screenshots. HITL. |
| **vision_agent** | `agent/agents/vision_agent.py` | Bildanalyse via Claude Sonnet Vision. Deterministisch geroutet bei [FOTO]-Prefix oder wenn image_data im State. |
| **whatsapp_agent** | `agent/agents/whatsapp_agent.py` | WhatsApp-Nachrichten senden via Node.js-Bridge. HITL-Pflicht. Nur an erlaubte Kontakte. |
| **system_agent** | `agent/agents/system_agent.py` | CPU/RAM/Disk-Metriken via psutil. Kein Shell-Zugriff. Deterministisch geroutet bei 'cpu'/'ram'/'disk'. |
| **youtube_agent** | `agent/agents/youtube_agent.py` | YouTube-Videos analysieren. Deterministisch geroutet bei youtube.com/youtu.be-URLs. |
| **music_analysis_agent** | `agent/agents/music_analysis_agent.py` | Audio-Dateien analysieren (BPM, Key, Energie). Geroutet bei [MUSIK-ANALYSE]-Prefix. |

### Nicht im Graph

- **clip_agent** (`agent/agents/clip_agent.py`): URL-Clipper fuer Obsidian/Wissen. Kein LangGraph-Node – wird direkt von `bot.py` via `/clip` aufgerufen.

---

## Routing-Logik (Supervisor)

Reihenfolge in `supervisor_node()`:

1. **Pre-Routing-Pipeline** (deterministisch, kein LLM-Call):
   - image_data im State → `vision_agent`
   - AI-Message → `FINISH`
   - Vision-Followup → `chat_agent`
   - YouTube-URL → `youtube_agent`
   - Negierte Erinnerungen `_pre_route_reminder` → reminder_agent („Vergiss nicht X", Guard gegen Profil-Fakten, #286)
   - Prefix-Tabelle `_PRE_ROUTING_RULES` → spezifischer Prefix → direkter Agent
   - Word-Trigger `_WORD_TRIGGER_RULES` → eindeutige Mehrwort-Phrasen wortgrenzen-basiert auch mitten im Satz (#280)

2. **LLM-Routing** (Fallback, wenn kein Pre-Route-Match):
   - `SUPERVISOR_PROMPT` als SystemMessage mit `cache_control: ephemeral`
   - `get_fast_llm()` (Haiku) → Routing-Entscheidung als Agentenname
   - Fallback bei unbekanntem Output: `chat_agent`

3. **Injection-Schutz**: `_sanitize_routing_content()` ersetzt gefaehrliche Patterns durch [X], truncated auf 500 Zeichen fuer LLM-Routing.

---

## LLM-Konfiguration (`agent/llm.py`)

### `get_llm()` – Sonnet (Standard)
- **Modell**: `claude-sonnet-4-6` (konfigurierbar via `ANTHROPIC_MODEL_SONNET`)
- **Temperature**: nicht explizit gesetzt (Anthropic-Default)
- **Typ**: lazy Singleton – wird bei Modell-Wechsel via .env invalidiert
- **Verwendet von**: alle Agenten (chat_agent, memory_agent, web_agent, calendar_agent, ...)

### `get_fast_llm()` – Haiku (Routing)
- **Modell**: `claude-haiku-4-5-20251001` (konfigurierbar via `ANTHROPIC_MODEL_HAIKU`)
- **Temperature**: nicht explizit gesetzt
- **Typ**: lazy Singleton
- **Verwendet von**: `supervisor_node` (Routing), `_summarize_overflow` (Context-Window, Phase 216)

### `get_grounding_llm()` – Haiku + temperature=0
- **Modell**: Haiku (gleich wie get_fast_llm)
- **Temperature**: **0** (explizit gesetzt, bewusste Entscheidung)
- **Typ**: KEIN Singleton – jeder Aufruf erstellt eine neue Instanz
- **Verwendet von**: `bot/evening_checkin.py` (_generate_checkin_question)
- **Warum temperature=0**: Deterministisches Grounding. Der Evening Check-in generiert eine
  personalisierte Abend-Frage aus dem Gespraechsverlauf. temperature=0 minimiert Halluzination
  von Personennamen oder erfundenen Ereignissen. Zweite Sicherheitsschicht: `_has_hallucination()`
  prueft ob Entitaeten in der Frage vorkommen die nicht im Verlauf erwaehnt wurden. Beide zusammen:
  Phase 204 (Issue #215).
- **Warum kein Singleton**: Vermeidet State-Probleme durch gecachte temperature=0-Instanz.

---

## Prompt-Aufbau (chat_agent)

Der System-Prompt hat zwei Teile (`agent/agents/chat_agent.py`):

### Statischer Block (gecacht, 60s in-process + Anthropic Prompt Caching)
1. `_CHAT_PROMPT_BASE`: Bot-Identitaet, Verhaltensregeln (Zeile 26)
2. `SELF.md`: dieses Dokument – Architektur-Selbstwissen (Phase 218)
3. `claude.md`: persoenliche Bot-Instruktionen (`~/.fabbot/claude.md`)
4. Personal Profile: `~/.fabbot/personal_profile.yaml` (via `get_profile_context_full()`)

### Dynamischer Block (nicht gecacht, immer frisch)
- Aktuelles Datum/Uhrzeit
- `last_agent_result` (Ergebnis des vorherigen Agents, max. `AGENT_RESULT_MAX_CHARS` Zeichen, TTL: `AGENT_RESULT_TTL_TURNS` Requests – beide via .env konfigurierbar)
- Retrieval-Kontext (ChromaDB-Ergebnisse, wenn vorhanden)

### Cache-Mechanismus
- In-Process-Cache: `_CachedPrompt` mit 60s TTL (verhindert Disk-Reads)
- Anthropic Server-Side Cache: `cache_control: ephemeral` am statischen Block (~5min TTL)
- Invalidierung: `invalidate_chat_cache()` – aufgerufen nach claude.md-Write und Session-Summary

---

## Memory und Lernen

| Quelle | Pfad | Beschreibung |
|---|---|---|
| **personal_profile.yaml** | `~/.fabbot/personal_profile.yaml` | Fernet-verschluesselt. Primaere Wissensquelle: identity, preferences, projects, notes, people. 300s-Snapshot-Cache (Phase 178). |
| **claude.md** | `~/.fabbot/claude.md` | Bot-Instruktionen. Schreibbar via memory_agent. Max. 50 Auto-Eintraege (FIFO-Trim). |
| **Sessions** | `~/Documents/Wissen/Sessions/` | Taeglich 23:30 generierte Summary. Rolling Window: bis 50 Sessions – 30 Tage; ab 50 – 14 Tage. |
| **ChromaDB** | `~/.fabbot/chroma/` | 3 Collections fuer semantischen Retrieval. 5s Timeout, Fail-safe. |
| **SQLite** | `~/.fabbot/memory.db` | LangGraph Conversation Checkpoints via AsyncSqliteSaver. |

---

## Sicherheits-Architektur

- **Security Guard** (`agent/security.py`): Zweistufig – Regex-Check + LLM-Guard via Haiku. Fail-closed.
- **HITL**: Pflicht bei terminal_agent, file_agent (write), calendar_agent (create), computer_agent, whatsapp_agent. Graph-nativ über LangGraph `interrupt()` – der Node unterbricht, `bot._handle_interrupt()` holt die Bestätigung, Resume via `Command(resume=...)`. Keine Magic-String-Bypässe mehr (Phase 222).
- **Prompt Injection**: `_sanitize_routing_content()` im Supervisor, Homoglyph-Normalisierung.
- **At-Rest-Encryption**: personal_profile.yaml via Fernet + macOS Keychain fuer den Key.
- **Audit-Log**: Tamper-evident. Alle destruktiven Aktionen geloggt.
- **Rate Limiting**: global (20/60s) + destruktiv (10/60s).

---

## Proaktive Features (Scheduler)

| Feature | Zeit | Datei | Beschreibung |
|---|---|---|---|
| Health Check | 06:00 | `bot/health_check.py` | 11 Komponenten geprueft. |
| Morning Briefing | 07:30 | `bot/briefing.py` | Wetter (Open-Meteo), Kalender, News. |
| Evening Check-in | 21:00 | `bot/evening_checkin.py` | Personalisierte Frage via `get_grounding_llm()` (temperature=0). |
| Session Summary | 23:30 | `bot/session_summary.py` | Tages-Summary via `get_fast_llm()`. Rolling-Window-Sessions. |
| Heartbeat | stuendlich | `bot/heartbeat_scheduler.py` | 6h Cooldown; temperature=0 + Entity Guard (agent/proactive/entity_guard.py); Focus-Mode-Check (agent/proactive/focus_mode.py): SOFT_MUTE ab 15 min, HARD_MUTE ab 60 min Inaktivität. |

---

## Wichtige Architektur-Entscheidungen

1. **temperature=0 in `get_grounding_llm()`**: Deterministisches Grounding fuer Evening Check-in.
   Kombiniert mit Entity Guard (`_has_hallucination()`) als zweite Sicherheitsschicht. Phase 204 (#215).

2. **Haiku fuer Supervisor-Routing**: ~4x schneller und guenstiger als Sonnet. Routing ist Klassifikation –
   kein kreatives Reasoning benoetigt. Geschwindigkeit > Qualitaet hier.

3. **Prompt Caching (`cache_control: ephemeral`)**: Statischer Prompt-Block (~3-5KB) wird Anthropic-seitig
   gecacht → ~90% guenstigere Input-Token-Kosten fuer wiederkehrende Messages. Der Block MUSS immer an
   derselben Position im API-Call stehen damit der Cache trifft.

4. **Pre-Routing ohne LLM**: Bekannte Prefix-Muster ([FOTO], youtube-URLs, system-Keywords) werden
   deterministisch geroutet. Neue Rules: eine Zeile in `_PRE_ROUTING_RULES` ergaenzen.

5. **clip_agent nicht im Graph**: `/clip` ist ein expliziter Telegram-Befehl, kein natuerlichsprachlicher
   Intent. Direktaufruf aus `bot.py` klarer als Supervisor-Routing.

6. **get_grounding_llm() kein Singleton**: Keine State-Probleme durch gecachte temperature=0-Instanz.
   get_llm() und get_fast_llm() sind Singletons weil sie zustandslos und Standard-Temperature sind.

7. **Fail-closed Security Guard**: Bei Exception im Security Check wird geblockt, nicht durchgelassen.
   Sicherer als Fail-open, auch wenn es zu False Positives fuehren kann.
