import logging
import re
from pathlib import Path
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
import asyncio

from agent.state import AgentState, AgentName
from agent.llm import get_fast_llm
from agent.utils import extract_llm_text
from agent.node_utils import wrap_agent_node
from agent.agents.computer import computer_agent
from agent.agents.terminal import terminal_agent
from agent.agents.file import file_agent
from agent.agents.web import web_agent
from agent.agents.calendar import calendar_agent
from agent.agents.chat_agent import chat_agent
from agent.agents.reminder_agent import reminder_agent
from agent.agents.memory_agent import memory_agent
from agent.agents.vision_agent import vision_agent
from agent.agents.whatsapp_agent import whatsapp_agent
from agent.agents.system_agent import system_agent
from agent.agents.youtube_agent import youtube_agent
from agent.agents.music_analysis_agent import music_analysis_agent
from agent.protocol import Proto

# Issue #98: Single Source of Truth für alle Agenten.
# Neue Agenten: nur hier eintragen – supervisor_node und _build_graph werden automatisch aktualisiert.
_AGENTS: dict[str, object] = {
    "computer_agent": computer_agent,
    "terminal_agent": terminal_agent,
    "file_agent": file_agent,
    "web_agent": web_agent,
    "calendar_agent": calendar_agent,
    "reminder_agent": reminder_agent,
    "memory_agent": memory_agent,
    "chat_agent": chat_agent,
    "vision_agent": vision_agent,
    "whatsapp_agent": whatsapp_agent,
    "system_agent": system_agent,
    "youtube_agent": youtube_agent,
    "music_analysis_agent": music_analysis_agent,
}

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".fabbot" / "memory.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

agent_graph: CompiledStateGraph | None = None
_db_conn = None
_init_lock = asyncio.Lock()
_cleanup_lock = asyncio.Lock()

SUPERVISOR_PROMPT = """Du bist ein Routing-Agent. Deine einzige Aufgabe ist es, eine der folgenden Antworten zurueckzugeben.

Verfuegbare Agenten:

- chat_agent: STANDARD-FALLBACK fuer alles was das LLM aus sich selbst beantworten kann.
  Nutze chat_agent bei:
  - Meinungsfragen ("was haelst du von X", "wie findest du Y", "magst du Z")
  - Erklaerungen und Definitionen von stabilen Konzepten ("was ist Philosophie", "erklaer mir Quantenmechanik auf Grundschulniveau")
  - Smalltalk, Reaktionen, Hoeflichkeiten ("danke", "ok", "cool", "super", "alles klar")
  - Folgefragen zum bisherigen Gespraech ("fass das zusammen", "erklaer das nochmal")
  - Persoenliche Fragen ueber den User aus dem Profil (Projekte, Standort, Geraete, Praeferenzen)
  - Fragen ueber gespeicherte Notizen, Sessions oder Wissen
  - Statische Fakten die sich nie aendern ("wer hat die Relativitaetstheorie entwickelt")
  - Reine Datum/Uhrzeit-Fragen ("wieviel uhr", "welches datum")
  - ALLE Folgefragen zu einem Foto oder Bild
  - Im Zweifel: chat_agent ist der sichere Fallback

- web_agent: NUR wenn externe oder aktuelle Daten benoetigt werden – Daten die das LLM
  nicht zuverlaessig aus sich selbst beantworten kann.
  Nutze web_agent EXPLIZIT bei:
  - Aktuellen Nachrichten, Ereignissen, Politik, Sport, Wirtschaft
  - Wetter-Fragen ("wetter heute", "wie warm ist es", "regnet es", "wetter berlin", "forecast")
  - Preise, Kurse, Boerse
  - Aktuellem Status von Personen (lebt X noch? aktuelles Amt, aktuelle Rolle, CEO, Kanzler, Minister)
  - Schnell aendernden Fakten (aktuelle Rekorde, aktuelle Zahlen, aktuelle Ranglisten)
  - Erklaerungen zu aktuellen oder sich schnell entwickelnden Themen (KI-Fortschritt, neue Technologien)
  - NICHT fuer Meinungsfragen, Erklaerungen stabiler Konzepte, Smalltalk oder Konversation
  - NICHT fuer Fragen zu einem Foto oder Bild

- memory_agent: Persoenliche Informationen oder Bot-Instruktionen speichern, aktualisieren oder loeschen.
  Bei expliziten Speicher-Befehlen UND bei biographischen Fakten ueber Personen:
  JA: 'merke dir dass...', 'speichere...', 'fuge ... hinzu', 'notiere dass...'
  JA: 'vergiss X', 'vergiss den X', 'vergiss den Eintrag X', 'loesche X aus dem Profil'
  JA: 'merke dir grundsaetzlich...', 'von jetzt an sollst du...', 'du sollst immer...'
  JA: 'merke dir das', 'merk dir das', 'das merken', 'merk das' (Referenz auf vorherige Aussage)
  JA: 'vergiss die instruktion', 'loesch die instruktion', 'alle instruktionen loeschen'
  JA: Biographische Fakten ueber Personen mit Name + Beziehung/Funktion/Ort (auch ohne 'merke dir'):
      'Mein Kollege Sven aus Muenchen', 'Jana ist meine beste Freundin',
      'Mein Chef heisst Marco', 'Mein Bruder wohnt in Hamburg',
      'Lisa arbeitet bei Google', 'Tom ist mein neuer Mitbewohner'
  NEIN: Transiente Ereignisse ohne identifizierende Information ('mein Kollege war heute krank',
        'ich war gestern in Hamburg', 'heute war schoenes Wetter')
  NEIN: Transiente Sozialereignisse mit Namen ohne biographische Beziehung ('Max und Anka kommen
        heute zum Essen', 'ich treffe gleich Steffi', 'wir fahren nachher zu Mario')
  NEIN: Negierte Erinnerungen ('vergiss nicht X', 'vergiss morgen nicht das Meeting',
        'nicht vergessen: X', 'denk dran X') → reminder_agent, KEIN Loeschbefehl
  NEIN: Fragen ueber gespeicherte Notizen, Sessions oder Wissen
  ZWEIFEL: Wenn unklar ob temporaer oder dauerhaft → chat_agent

- calendar_agent: Kalendertermine lesen oder erstellen
- reminder_agent: Erinnerungen setzen, auflisten oder loeschen (z.B. 'Erinnere mich um 18 Uhr',
  'Vergiss nicht X', 'Denk dran: X', 'Nicht vergessen: X')
- file_agent: Dateien und Ordner lesen, auflisten oder schreiben
- system_agent: CPU, RAM, Disk-Auslastung abfragen – NUR fuer Systemmetriken, kein Shell-Zugriff
- terminal_agent: Shell-Befehle, Prozesse – NUR technische Systemabfragen, NICHT fuer CPU/RAM/Disk, NICHT fuer Datum/Uhrzeit
- computer_agent: Desktop-Steuerung, Screenshots, Apps oeffnen
- vision_agent: Bildanalyse von Fotos. Wird automatisch geroutet wenn Nachricht mit [FOTO] beginnt.
- whatsapp_agent: WhatsApp-Nachricht senden. NUR bei expliziten Sende-Befehlen an erlaubte Kontakte.
- youtube_agent: YouTube-Video analysieren, zusammenfassen oder Fragen beantworten. Wird automatisch geroutet wenn eine youtube.com- oder youtu.be-URL erkannt wird.
- music_analysis_agent: Musik-Datei analysieren (BPM, Key, Energie). NUR bei expliziten Analyse-Anfragen mit Dateipfad zu einer Audio-Datei (.mp3, .wav, .flac, .aiff, .ogg, .m4a).

Regeln:
- Wenn die letzte Nachricht bereits eine Antwort eines Agenten enthaelt: FINISH
- Smalltalk, Reaktionen, Hoeflichkeiten: IMMER chat_agent, NIE FINISH
- Wetter-Fragen: IMMER web_agent
- Meinungsfragen ("was haelst du", "wie findest du", "deine meinung"): IMMER chat_agent
- Im Zweifel zwischen web_agent und chat_agent: chat_agent waehlen
- Im Zweifel zwischen memory_agent und chat_agent: chat_agent waehlen (AUSSER bei klaren biographischen Fakten mit Name)
- Fragen mit 'wo', 'wer', 'was' die sich auf ein Foto beziehen: IMMER chat_agent
- Fragen ueber eigene Notizen/Sessions/Wissen: IMMER chat_agent
- Wenn [Letzter Agent: X] im Input steht und die Frage eine kurze Reaktion oder Folgefrage
  auf das Ergebnis dieses Agents ist (kein neues Thema): IMMER chat_agent
- Wenn [Letzter Agent: vision_agent] und KEIN explizites Speicher-Wort ('merke dir', 'speichere',
  'vergiss', 'loesche', 'notiere'): IMMER chat_agent, NIE memory_agent – Kontext-Aussagen
  wie 'die Person heisst X', 'das ist ein Y', 'er arbeitet bei Z' sind Gespraechskontext, kein Speicher-Befehl

WICHTIG: Antworte AUSSCHLIESSLICH mit einem dieser Woerter (nichts anderes):
computer_agent
terminal_agent
file_agent
web_agent
calendar_agent
reminder_agent
memory_agent
chat_agent
system_agent
youtube_agent
music_analysis_agent
FINISH
"""

# ---------------------------------------------------------------------------
# Deterministisches Pre-Routing – Tabelle (Issue #55)
# Reihenfolge ist semantisch: spezifischer vor generischem.
# Neue Rules: nur eine Zeile in _PRE_ROUTING_RULES ergänzen.
# ---------------------------------------------------------------------------

_PRE_ROUTING_RULES: list[tuple[tuple[str, ...], str, str]] = [
    # (prefixes, target_agent, log_label)
    # Issue #97: [FOTO]-Prefix deterministisch, kein LLM-Call nötig
    (
        ("[foto]",),
        "vision_agent",
        "foto-trigger",
    ),
    # Issue #180: Musik-Analyse deterministisch routen
    (
        ("[musik-analyse]",),
        "music_analysis_agent",
        "musik-analyse-trigger",
    ),
    # Phase 189: Standort- und PDF-Anhänge → chat_agent
    (
        ("[standort]", "[pdf:"),
        "chat_agent",
        "anhang-trigger",
    ),
    # Issue #37: CPU/RAM/Disk direkt → system_agent, kein LLM-Call nötig
    (
        (
            "cpu",
            "ram ",
            "ram-",
            "arbeitsspeicher",
            "speicherauslastung",
            "festplattenplatz",
            "disk ",
            "disk-",
            "system-status",
            "systemstatus",
            "wie viel ram",
            "wie viel cpu",
            "wie viel disk",
            "wie viel speicher",
            "system status",
            "speicher auslastung",
        ),
        "system_agent",
        "system-stats-trigger",
    ),
    (
        (
            "was hälst du",
            "was haelst du",
            "was denkst du",
            "was findest du",
            "wie findest du",
            "magst du",
            "gefällt dir",
            "gefaellt dir",
            "deine meinung",
            "dein urteil",
            "was ist deine meinung",
        ),
        "chat_agent",
        "opinion-trigger",
    ),
    (
        # Spezifischer als memory-delete – muss davor stehen
        (
            "vergiss die instruktion",
            "vergiss alle instruktionen",
            "lösch die instruktion",
            "loesch die instruktion",
            "lösche die instruktion",
            "loesche die instruktion",
            "entferne die instruktion",
            "alle instruktionen löschen",
            "alle instruktionen loeschen",
            "instruktionen zurücksetzen",
            "instruktionen zuruecksetzen",
            "instruktion löschen",
            "instruktion loeschen",
            "setze instruktionen zurück",
            "setze instruktionen zurueck",
        ),
        "memory_agent",
        "bot-instruction-delete-trigger",
    ),
    (
        (
            # Artikel/Konjunktions-Pattern: explizite Lösch-Absicht (Issue #96: "vergiss " war zu breit)
            "vergiss den ",
            "vergiss die ",
            "vergiss das ",
            "vergiss diesen ",
            "vergiss diese ",
            "vergiss diesem ",
            "vergiss alles",
            "vergiss bitte",
            "vergiss dass ",
            "lösche aus dem profil",
            "loesche aus dem profil",
            "entferne aus dem profil",
            "aus dem profil löschen",
            "aus dem profil loeschen",
            "aus meinem profil löschen",
            "aus meinem profil loeschen",
            "profil eintrag löschen",
            "profil eintrag loeschen",
        ),
        "memory_agent",
        "delete-trigger",
    ),
    (
        (
            "merke dir dass",
            "merke dir das",
            "merk dir das",
            "merk das",
            "merke dir:",
            "speichere dass",
            "speichere das",
            "füge hinzu:",
            "fuege hinzu:",
            "merke dir grundsätzlich",
            "merke dir grundsaetzlich",
            "von jetzt an sollst du",
            "notiere dass",
            "notiere das",
            "notier dass",
            "notier das",
            "notiere dir",
            "bitte merke dir",
            "bitte merk dir",
        ),
        "memory_agent",
        "save-trigger",
    ),
]


# Phase 223 (#280): Eindeutige Mehrwort-Phrasen, die auch mitten im Satz greifen
# sollen ("Hey FabBot, wie viel cpu ..."). Bewusst eine kuratierte, sichere
# Teilmenge – nackte Tokens wie "cpu"/"ram" bleiben prefix-only, da sie als
# Substring False-Positives erzeugen ("Instagram", "wie funktioniert eine CPU").
_WORD_TRIGGER_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (
        (
            "wie viel cpu",
            "wie viel ram",
            "wie viel arbeitsspeicher",
            "wie viel speicher",
            "wie viel disk",
            "system status",
            "systemstatus",
            "system-status",
            "speicher auslastung",
            "speicherauslastung",
        ),
        "system_agent",
        "system-stats-trigger-word",
    ),
    (
        (
            "was denkst du",
            "was hälst du",
            "was haelst du",
            "was findest du",
            "wie findest du",
            "deine meinung",
            "was ist deine meinung",
            "dein urteil",
            "magst du",
            "gefällt dir",
            "gefaellt dir",
        ),
        "chat_agent",
        "opinion-trigger-word",
    ),
]


def _compile_word_triggers(
    rules: list[tuple[tuple[str, ...], str, str]],
) -> list[tuple[re.Pattern[str], str, str]]:
    """Kompiliert Phrasen-Gruppen zu wortgrenzen-basierten Regex-Patterns."""
    return [
        (re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b"), agent, label)
        for phrases, agent, label in rules
    ]


_WORD_TRIGGER_PATTERNS = _compile_word_triggers(_WORD_TRIGGER_RULES)


def _match_pre_routing(text: str) -> tuple[str, str] | None:
    """Gibt (agent_name, log_label) zurück wenn eine Rule greift, sonst None.

    Zwei Schichten: zuerst Prefix-Rules (Satzanfang, Reihenfolge spezifisch→generisch,
    Issue #96), dann wortgrenzen-basierte Word-Trigger für eindeutige Phrasen mitten
    im Satz (Issue #280).
    """
    lower = text.strip().strip("\"'").lower()
    for prefixes, agent, label in _PRE_ROUTING_RULES:
        if any(lower.startswith(p) for p in prefixes):
            return agent, label
    for pattern, agent, label in _WORD_TRIGGER_PATTERNS:
        if pattern.search(lower):
            return agent, label
    return None


_MAX_ROUTING_LEN = 500

_INJECTION_RE = re.compile(
    r"(?i)"
    r"(ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?))"
    r"|(you\s+are\s+now\s+\w+)"
    r"|(system\s*:\s)"
    r"|(<\s*/?\s*system\s*>)"
    r"|(\[system\])"
    r"|(vergiss\s+(alle?\s+)?(vorherigen?|obigen?)\s+(anweisungen?|regeln?|instruktionen?))"
    r"|(ignorier\w*\s+(alle?\s+)?(anweisungen?|regeln?|instruktionen?))"
    r"|(du\s+bist\s+jetzt\s+\w+)"
    r"|(als\s+(administrator|system|root)\s+(sage|befehle|weise))"
    r"|(neue\s+instruktion\s*:)"
    r"|(system\s*-\s*anweisung)"
)


def _sanitize_routing_content(content):
    if isinstance(content, str):
        return _INJECTION_RE.sub("[X]", content[:_MAX_ROUTING_LEN])
    if isinstance(content, list):
        return [
            {**b, "text": _INJECTION_RE.sub("[X]", b["text"][:_MAX_ROUTING_LEN])}
            if isinstance(b, dict) and "text" in b
            else b
            for b in content
        ]
    return content


def _filter_hitl_messages(messages: list) -> list:
    filtered = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""
        if (isinstance(content, str) and content.startswith(("__CONFIRM_", "__SCREENSHOT__"))) or (
            isinstance(content, str) and content.startswith("__MEMORY__") and Proto.MEMORY_VISION_MARKER not in content
        ):
            if isinstance(msg, AIMessage):
                filtered.append(AIMessage(content="[Aktion wurde ausgefuehrt]"))
            continue
        filtered.append(msg)
    return filtered


# ---------------------------------------------------------------------------
# Pre-Route-Pipeline (Issue #127)
# Jede Strategie ist eine eigene Funktion. Die Pipeline-Liste bestimmt die Reihenfolge.
# Neue Sonderfälle: nur eine Funktion ergänzen und in _PRE_ROUTE_PIPELINE eintragen.
# ---------------------------------------------------------------------------


def _pre_route_image_data(state: AgentState, _messages: list, _routing: list) -> str | None:
    if state.get("image_data"):
        logger.info("supervisor: Pre-Routing → vision_agent (image_data)")
        return "vision_agent"
    return None


def _pre_route_ai_message(_state: AgentState, messages: list, _routing: list) -> str | None:
    last_msg = messages[-1] if messages else None
    if last_msg and isinstance(last_msg, AIMessage):
        content = last_msg.content if isinstance(last_msg.content, str) else ""
        if not content.startswith("__MEMORY__:"):
            logger.debug("supervisor: letzte Message ist AIMessage → FINISH")
            return "FINISH"
    return None


def _pre_route_vision_followup(state: AgentState, _messages: list, routing: list) -> str | None:
    if state.get("last_agent_name") != "vision_agent" or not routing:
        return None
    last = routing[-1]
    text = (
        last.content
        if isinstance(last.content, str)
        else " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in last.content)
    )
    _memory_kw = ("merke dir", "speichere", "vergiss", "loesche", "lösche", "notiere", "füge hinzu", "fuege hinzu")
    if not any(kw in text.lower() for kw in _memory_kw):
        logger.info("supervisor: Pre-Routing → chat_agent (vision-Kontext, kein Memory-Keyword)")
        return "chat_agent"
    return None


def _pre_route_table(_state: AgentState, _messages: list, routing: list) -> str | None:
    if not routing:
        return None
    last_content = routing[-1].content if hasattr(routing[-1], "content") else ""
    if isinstance(last_content, list):
        last_content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in last_content)
    last_content = last_content[:_MAX_ROUTING_LEN]
    match = _match_pre_routing(last_content)
    if match:
        agent, label = match
        sanitized = _INJECTION_RE.sub("[X]", last_content)
        logger.info(f"supervisor: Pre-Routing → {agent} ({label}: '{sanitized.strip()[:60]}')")
        return agent
    return None


_YT_URL_RE = re.compile(r"youtube\.com/watch|youtu\.be/")


def _pre_route_youtube(_state: AgentState, _messages: list, routing: list) -> str | None:
    if not routing:
        return None
    last_content = routing[-1].content if hasattr(routing[-1], "content") else ""
    if isinstance(last_content, list):
        last_content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in last_content)
    if _YT_URL_RE.search(last_content):
        logger.info("supervisor: Pre-Routing → youtube_agent (YouTube-URL erkannt)")
        return "youtube_agent"
    return None


# Phase 225 (#286): Negierte Erinnerungen ("Vergiss nicht X", "Nicht vergessen: X",
# "Denk dran: X") deterministisch → reminder_agent, statt sie dem nicht-deterministischen
# LLM-Routing zu überlassen (das mal memory_agent, mal reminder_agent wählte).
_NEGATION_REMINDER_RE = re.compile(r"\bvergiss\b.{0,15}?\bnicht\b|\bnicht\s+vergessen\b|\bdenk\b.{0,8}?\bda?ran\b")
# Guard: echte Profil-Fakten ("Vergiss nicht dass ich Jazz mag") sind keine Erinnerung.
_MEMORY_FACT_RE = re.compile(r"\bdass\s+(?:ich|mir|mein|meine)\b")


def _pre_route_reminder(_state: AgentState, _messages: list, routing: list) -> str | None:
    if not routing:
        return None
    last_content = routing[-1].content if hasattr(routing[-1], "content") else ""
    if isinstance(last_content, list):
        last_content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in last_content)
    text = last_content.lower()
    if _NEGATION_REMINDER_RE.search(text) and not _MEMORY_FACT_RE.search(text):
        logger.info("supervisor: Pre-Routing → reminder_agent (negation-reminder)")
        return "reminder_agent"
    return None


_PRE_ROUTE_PIPELINE = [
    _pre_route_image_data,
    _pre_route_ai_message,
    _pre_route_vision_followup,
    _pre_route_youtube,
    _pre_route_reminder,
    _pre_route_table,
]


def _pre_route(state: AgentState, messages: list, routing: list) -> str | None:
    for fn in _PRE_ROUTE_PIPELINE:
        result = fn(state, messages, routing)
        if result is not None:
            return result
    return None


async def supervisor_node(state: AgentState) -> dict:
    messages = state["messages"]
    clean_messages = _filter_hitl_messages(messages)
    last_human = [m for m in clean_messages if isinstance(m, HumanMessage)]
    routing_messages = [last_human[-1]] if last_human else clean_messages[-1:]

    if last_human:
        last_text = last_human[-1].content
        if isinstance(last_text, list):
            last_text = str(last_text)[:100]
        logger.info(f"supervisor routing: '{last_text[:100]}' → ?")

    pre_routed = _pre_route(state, messages, routing_messages)
    if pre_routed is not None:
        return {"next_agent": pre_routed}

    raw_last_agent = state.get("last_agent_name")
    valid_agent_names = set(_AGENTS.keys()) | {"FINISH"}
    last_agent_name = raw_last_agent if raw_last_agent in valid_agent_names else None
    agent_prefix = f"[Letzter Agent: {last_agent_name}]\n" if last_agent_name else ""

    sanitized = []
    for m in routing_messages:
        if isinstance(m, HumanMessage):
            content = _sanitize_routing_content(m.content)
            sanitized.append(HumanMessage(content=agent_prefix + content))
        else:
            sanitized.append(m)

    all_messages = [
        SystemMessage(content=[{"type": "text", "text": SUPERVISOR_PROMPT, "cache_control": {"type": "ephemeral"}}])
    ] + sanitized
    llm = get_fast_llm()
    response = await llm.ainvoke(all_messages)
    content = extract_llm_text(response.content)
    next_agent = content.strip()

    valid = set(_AGENTS.keys()) | {"FINISH"}
    if next_agent not in valid:
        logger.warning(f"supervisor: ungültiges Routing '{next_agent}' → fallback chat_agent")
        next_agent = "chat_agent"

    logger.info(f"supervisor → {next_agent}")
    return {"next_agent": next_agent}


def route(state: AgentState) -> AgentName:
    return state.get("next_agent") or "chat_agent"


def _build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    _wrap = wrap_agent_node
    graph.add_node("supervisor", supervisor_node)
    for name, fn in _AGENTS.items():
        graph.add_node(name, _wrap(name)(fn))

    graph.set_entry_point("supervisor")

    edge_map: dict[str, str] = {name: name for name in _AGENTS}
    edge_map["FINISH"] = END
    graph.add_conditional_edges("supervisor", route, edge_map)  # type: ignore[arg-type]

    for name in _AGENTS:
        graph.add_edge(name, "supervisor")

    return graph


def get_graph() -> CompiledStateGraph:
    if agent_graph is None:
        raise RuntimeError(
            "LangGraph nicht initialisiert – init_graph() muss zuerst aufgerufen werden. "
            "Sicherstellen dass _post_init() in bot.py vollständig abgeschlossen ist."
        )
    return agent_graph


async def init_graph() -> None:
    global agent_graph, _db_conn

    async with _init_lock:
        if agent_graph is not None:
            return

        import aiosqlite

        _db_conn = await aiosqlite.connect(str(_DB_PATH))
        checkpointer = AsyncSqliteSaver(_db_conn)
        agent_graph = _build_graph().compile(checkpointer=checkpointer)


async def cleanup_checkpoints(max_per_thread: int = 200) -> None:
    """Löscht alte Checkpoints – behält nur die letzten max_per_thread pro thread_id."""
    if _db_conn is None:
        return
    async with _cleanup_lock:
        deleted = await _db_conn.execute(
            """
            DELETE FROM checkpoints
            WHERE rowid NOT IN (
                SELECT rowid FROM (
                    SELECT rowid,
                           ROW_NUMBER() OVER (
                               PARTITION BY thread_id ORDER BY checkpoint_id DESC
                           ) AS rn
                    FROM checkpoints
                ) WHERE rn <= ?
            )
            """,
            (max_per_thread,),
        )
        await _db_conn.execute("DELETE FROM writes WHERE checkpoint_id NOT IN (SELECT checkpoint_id FROM checkpoints)")
        await _db_conn.commit()
        logger.info(f"Checkpoint-Bereinigung: {deleted.rowcount} Einträge entfernt.")


async def close_graph() -> None:
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        _db_conn = None
