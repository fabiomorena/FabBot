"""
Health Check für FabBot.

Täglich um 06:00 Uhr (konfigurierbar via HEALTH_CHECK_TIME in .env)
werden alle kritischen Komponenten geprüft und ein Statusbericht
per Telegram gesendet.

Geprüfte Komponenten:
 1. Terminal       – df -h via subprocess
 2. Anthropic      – minimaler Haiku-Call (günstigster Check)
 3. Web-Suche      – Tavily/Brave API Keys vorhanden + HTTP-Ping
 4. Kalender       – AppleScript osascript-Call
 5. Profil         – personal_profile.yaml ladbar
 6. Memory DB      – SQLite memory.db öffnbar
 7. Disk Space     – Hauptpartition < 85% belegt
 8. ChromaDB       – Second Brain Client initialisierbar
 9. WhatsApp       – Bridge HTTP-Ping auf localhost:8767
10. Audit Log      – ~/.fabbot/audit.log schreibbar
11. TTS            – OpenAI API Key vorhanden + Endpoint erreichbar

Design-Prinzipien:
- Kein LangGraph, kein Agent-Graph – direkter, minimaler Code
- Jeder Check ist vollständig isoliert (eigener try/except)
- Ein fehlgeschlagener Check blockiert nie die anderen
- Fail-safe: Wenn health_check selbst crasht, läuft der Bot weiter
"""

import asyncio
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Konfiguration
_raw_time = os.getenv("HEALTH_CHECK_TIME", "06:00")
try:
    _h, _m = _raw_time.split(":")
    assert 0 <= int(_h) <= 23 and 0 <= int(_m) <= 59
    HEALTH_CHECK_TIME = _raw_time
except Exception:
    logger.warning(f"Ungültiges HEALTH_CHECK_TIME Format '{_raw_time}' – verwende 06:00")
    HEALTH_CHECK_TIME = "06:00"

# Timeout für einzelne Checks
_CHECK_TIMEOUT = 10  # Sekunden


# ---------------------------------------------------------------------------
# Einzelne Check-Funktionen
# ---------------------------------------------------------------------------


async def _check_terminal() -> tuple[bool, str]:
    """Prüft ob Shell-Befehle ausführbar sind."""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.run,
                ["df", "-h"],
                capture_output=True,
                text=True,
                timeout=_CHECK_TIMEOUT,
            ),
            timeout=_CHECK_TIMEOUT + 2,
        )
        if result.returncode == 0 and result.stdout:
            return True, "df -h erfolgreich"
        return False, f"df -h returncode={result.returncode}"
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


async def _check_anthropic() -> tuple[bool, str]:
    """Prüft ob die Anthropic API erreichbar ist (minimaler Haiku-Call)."""
    try:
        from agent.llm import get_fast_llm
        from langchain_core.messages import HumanMessage

        llm = get_fast_llm()
        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content="ping")]),
            timeout=_CHECK_TIMEOUT,
        )
        if response and response.content:
            return True, "Haiku antwortet"
        return False, "Leere Antwort"
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


async def _check_web() -> tuple[bool, str]:
    """Prüft ob Web-Suche konfiguriert und erreichbar ist."""
    try:
        import httpx

        tavily_key = os.getenv("TAVILY_API_KEY")
        brave_key = os.getenv("BRAVE_API_KEY")

        if not tavily_key and not brave_key:
            return False, "Kein API-Key konfiguriert (TAVILY_API_KEY / BRAVE_API_KEY)"

        # Minimaler HTTP-Check – nur Verbindung testen, kein echter API-Call
        if tavily_key:
            async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
                resp = await client.get("https://api.tavily.com/")
                # 4xx ist ok – wir wollen nur wissen ob der Host erreichbar ist
                return True, f"Tavily erreichbar (HTTP {resp.status_code})"

        if brave_key:
            async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
                resp = await client.get("https://api.search.brave.com/")
                return True, f"Brave erreichbar (HTTP {resp.status_code})"

        return False, "Kein Key konfiguriert"

    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


async def _check_calendar() -> tuple[bool, str]:
    """Prüft ob AppleScript / Calendar erreichbar ist."""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.run,
                ["osascript", "-e", 'tell application "Calendar" to get name'],
                capture_output=True,
                text=True,
                timeout=_CHECK_TIMEOUT,
            ),
            timeout=_CHECK_TIMEOUT + 2,
        )
        if result.returncode == 0:
            return True, f"Calendar antwortet: {result.stdout.strip()[:40]}"
        return False, f"osascript returncode={result.returncode}: {result.stderr.strip()[:60]}"
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


async def _check_profile() -> tuple[bool, str]:
    """Prüft ob personal_profile.yaml ladbar ist."""
    try:
        from agent.profile import load_profile

        profile = load_profile()
        if profile:
            name = profile.get("identity", {}).get("name", "?")
            return True, f"Profil geladen (User: {name})"
        return False, "Profil leer oder nicht gefunden"
    except Exception as e:
        return False, str(e)[:80]


async def _check_memory_db() -> tuple[bool, str]:
    """Prüft ob die SQLite Memory-DB öffnbar ist."""
    try:
        import aiosqlite

        db_path = Path.home() / ".fabbot" / "memory.db"
        if not db_path.exists():
            return False, f"memory.db nicht gefunden: {db_path}"

        async with aiosqlite.connect(str(db_path)) as conn:
            cursor = await conn.execute("SELECT count(*) FROM sqlite_master")
            row = await cursor.fetchone()
            table_count = row[0] if row else 0
            return True, f"memory.db öffnbar ({table_count} Tabellen)"
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


async def _check_disk_space() -> tuple[bool, str]:
    """Warnt wenn die Hauptpartition >85% belegt ist."""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.run,
                ["df", "-h", "/"],
                capture_output=True,
                text=True,
            ),
            timeout=_CHECK_TIMEOUT,
        )
        if result.returncode != 0:
            return False, "df / fehlgeschlagen"
        # Zeile 2: "Filesystem  Size  Used  Avail  Use%  Mounted"
        line = result.stdout.strip().splitlines()[-1]
        parts = line.split()
        pct_str = next((p for p in parts if p.endswith("%")), None)
        if not pct_str:
            return False, "Auslastung nicht parsebar"
        pct = int(pct_str.rstrip("%"))
        if pct >= 85:
            return False, f"Disk {pct}% belegt – kritisch!"
        return True, f"Disk {pct}% belegt ({parts[3] if len(parts) > 3 else '?'} frei)"
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


async def _check_chromadb() -> tuple[bool, str]:
    """Prüft ob die ChromaDB (Second Brain) initialisierbar ist."""
    try:
        import chromadb

        chroma_path = Path.home() / ".fabbot" / "chroma"
        if not chroma_path.exists():
            return False, f"Chroma-Verzeichnis nicht gefunden: {chroma_path}"

        client = await asyncio.wait_for(
            asyncio.to_thread(chromadb.PersistentClient, path=str(chroma_path)),
            timeout=_CHECK_TIMEOUT,
        )
        collections = await asyncio.to_thread(client.list_collections)
        return True, f"ChromaDB OK ({len(collections)} Collections)"
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


async def _check_whatsapp() -> tuple[bool, str]:
    """Prüft ob die WhatsApp-Bridge auf localhost erreichbar ist."""
    try:
        import httpx

        port = int(os.getenv("WA_SERVICE_PORT", "8767"))
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"http://127.0.0.1:{port}/health")
            if resp.status_code < 500:
                return True, f"Bridge antwortet (HTTP {resp.status_code})"
            return False, f"Bridge HTTP {resp.status_code}"
    except Exception:
        return False, "Bridge nicht erreichbar (nicht gestartet?)"


async def _check_audit_log() -> tuple[bool, str]:
    """Prüft ob das Audit-Log schreibbar ist."""
    try:
        audit_path = Path.home() / ".fabbot" / "audit.log"
        await asyncio.to_thread(_audit_write_check, audit_path)
        size_kb = audit_path.stat().st_size // 1024 if audit_path.exists() else 0
        return True, f"audit.log schreibbar ({size_kb} KB)"
    except Exception as e:
        return False, str(e)[:80]


def _audit_write_check(path: Path) -> None:
    with open(path, "a"):
        pass


async def _check_heartbeat() -> tuple[bool, str]:
    """Prüft Heartbeat-Status: ob Cooldown aktiv oder stummgeschaltet."""
    try:
        from agent.proactive.heartbeat import is_on_cooldown, is_muted

        muted = is_muted()
        on_cooldown = is_on_cooldown()
        if muted:
            return True, "Heartbeat aktiv (stummgeschaltet)"
        if on_cooldown:
            return True, "Heartbeat aktiv (Cooldown läuft)"
        return True, "Heartbeat aktiv (bereit)"
    except Exception as e:
        return False, str(e)[:80]


async def _check_schedulers() -> tuple[bool, str]:
    """Prüft ob alle Background-Scheduler-Tasks noch aktiv sind."""
    try:
        from bot.bot import _scheduler_tasks

        if not _scheduler_tasks:
            return False, "Keine Scheduler registriert"
        dead = [t.get_name() for t in _scheduler_tasks if t.done()]
        if dead:
            return False, f"Gestorben: {', '.join(dead)}"
        return True, f"{len(_scheduler_tasks)} Scheduler aktiv"
    except Exception as e:
        return False, str(e)[:80]


async def _check_tts() -> tuple[bool, str]:
    """Prüft ob OpenAI TTS konfiguriert und der Endpoint erreichbar ist."""
    try:
        import httpx

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return False, "OPENAI_API_KEY nicht gesetzt"

        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                return True, "OpenAI TTS erreichbar"
            if resp.status_code == 401:
                return False, "API-Key ungültig (401)"
            return True, f"OpenAI erreichbar (HTTP {resp.status_code})"
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)[:80]


# ---------------------------------------------------------------------------
# Haupt-Check-Funktion
# ---------------------------------------------------------------------------


async def run_health_check(bot, chat_id: int) -> None:
    """
    Führt alle Checks parallel aus und sendet einen Statusbericht.
    Komplett fail-safe – kein Fehler beeinflusst den Bot-Betrieb.
    """
    try:
        logger.info("Health Check gestartet...")

        # Alle Checks parallel ausführen
        checks = await asyncio.gather(
            _check_terminal(),
            _check_anthropic(),
            _check_web(),
            _check_calendar(),
            _check_profile(),
            _check_memory_db(),
            _check_disk_space(),
            _check_chromadb(),
            _check_whatsapp(),
            _check_audit_log(),
            _check_tts(),
            _check_heartbeat(),
            _check_schedulers(),
            return_exceptions=True,  # Exceptions als Ergebnis, kein crash
        )

        labels = [
            "Terminal",
            "Anthropic API",
            "Web-Suche",
            "Kalender",
            "Profil",
            "Memory DB",
            "Disk Space",
            "ChromaDB",
            "WhatsApp Bridge",
            "Audit Log",
            "TTS",
            "Heartbeat",
            "Schedulers",
        ]

        lines = ["🤖 *FabBot Health Check*\n"]
        all_ok = True

        for label, result in zip(labels, checks):
            if isinstance(result, Exception):
                # asyncio.gather hat eine Exception gefangen
                ok, detail = False, str(result)[:80]
            else:
                ok, detail = result

            icon = "✅" if ok else "❌"
            lines.append(f"{icon} {label}: {detail}")
            if not ok:
                all_ok = False

        now = datetime.now().strftime("%d.%m.%Y, %H:%M Uhr")
        lines.append(f"\n{'✅ Alle Systeme normal' if all_ok else '⚠️ Probleme erkannt'}")
        lines.append(f"_{now}_")

        message = "\n".join(lines)
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        logger.info(f"Health Check abgeschlossen – {'OK' if all_ok else 'PROBLEME'}")

    except Exception as e:
        # Letzter Fallback – auch wenn das Senden scheitert
        logger.error(f"Health Check Fehler (nicht kritisch): {e}")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


async def run_health_check_scheduler(bot, chat_id: int) -> None:
    """
    Läuft als Background-Task und führt täglich den Health Check durch.
    Startet pünktlich zur konfigurierten Zeit.
    """
    logger.info(f"Health Check Scheduler gestartet – täglich um {HEALTH_CHECK_TIME} Uhr")

    while True:
        now = datetime.now()
        hour, minute = map(int, HEALTH_CHECK_TIME.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(f"Nächster Health Check in {wait_seconds / 3600:.1f} Stunden")
        await asyncio.sleep(wait_seconds)

        await run_health_check(bot, chat_id)

        # Kurze Pause damit wir nicht doppelt feuern
        await asyncio.sleep(60)
