"""
Morning Briefing für FabBot.
Täglich um 07:30 Uhr (konfigurierbar via BRIEFING_TIME in .env):
- Wetter Berlin
- Kalender-Termine heute
- Geburtstage
- Top News
"""
import asyncio
import logging
import os
import subprocess
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)

BRIEFING_TIME = os.getenv("BRIEFING_TIME", "07:30")


def _get_calendar_today() -> str:
    """Holt heutige Kalender-Termine via AppleScript."""
    today = date.today().strftime("%d.%m.%Y")
    cmd = [
        "osascript",
        "-e", f'set startDate to date "{today}"',
        "-e", f'set endDate to date "{today}" + (23 * hours) + (59 * minutes)',
        "-e", 'set output to ""',
        "-e", 'tell application "Calendar"',
        "-e", '    repeat with cal in calendars',
        "-e", '        set evts to (every event of cal whose start date >= startDate and start date <= endDate)',
        "-e", '        repeat with evt in evts',
        "-e", '            set evtStart to start date of evt',
        "-e", '            set h to hours of evtStart as string',
        "-e", '            set m to minutes of evtStart',
        "-e", '            if m < 10 then',
        "-e", '                set mStr to "0" & (m as string)',
        "-e", '            else',
        "-e", '                set mStr to m as string',
        "-e", '            end if',
        "-e", '            set output to output & (summary of evt) & "|" & h & ":" & mStr & linefeed',
        "-e", '        end repeat',
        "-e", '    end repeat',
        "-e", 'end tell',
        "-e", 'return output',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not result.stdout.strip():
            return "Keine Termine heute."
        lines = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                lines.append(f"• {parts[1]} Uhr – {parts[0]}")
        return "\n".join(lines) if lines else "Keine Termine heute."
    except Exception as e:
        logger.warning(f"Kalender-Fehler im Briefing: {e}")
        return "Kalender nicht verfügbar."


async def _fetch_web(query: str) -> str:
    """Einfache Web-Suche via Tavily oder Brave."""
    try:
        import httpx
        tavily_key = os.getenv("TAVILY_API_KEY")
        if tavily_key:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": tavily_key, "query": query, "max_results": 3},
                )
                data = resp.json()
                results = data.get("results", [])
                if results:
                    import re
                    lines = []
                    for r in results[:3]:
                        title = r.get("title", "").split(" - ")[0].split(" | ")[0].strip()
                        snippet = r.get("content", r.get("snippet", ""))
                        snippet = re.sub(r"[#*_`]", "", snippet).strip()
                        snippet = re.sub(r"\s+", " ", snippet)[:80].strip()
                        lines.append(f"• {title}" + (f": {snippet}" if snippet else ""))
                    return "\n".join(lines)
        return "Keine Ergebnisse."
    except Exception as e:
        logger.warning(f"Web-Suche Fehler im Briefing: {e}")
        return "Web-Suche nicht verfügbar."


async def generate_briefing() -> str:
    """Erstellt das komplette Morning Briefing."""
    days_de = {
        "Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch",
        "Thursday": "Donnerstag", "Friday": "Freitag", "Saturday": "Samstag", "Sunday": "Sonntag"
    }
    weekday_de = days_de.get(date.today().strftime("%A"), date.today().strftime("%A"))
    today_str = f"{weekday_de}, {date.today().strftime('%d.%m.%Y')}"

    # Parallel abrufen
    wetter_task = asyncio.create_task(_fetch_web("Wetter Berlin heute"))
    news_task = asyncio.create_task(_fetch_web("Top Nachrichten Deutschland heute"))

    kalender = await asyncio.to_thread(_get_calendar_today)
    wetter = await wetter_task
    news = await news_task

    briefing = f"""🌅 *Guten Morgen, Fabio!*
📅 *{today_str}*

🌤 *Wetter Berlin:*
{wetter}

📆 *Deine Termine heute:*
{kalender}

📰 *Top News:*
{news}

Einen guten Tag! 💪"""

    return briefing


async def run_briefing_scheduler(bot, chat_id: int) -> None:
    """Läuft als Background-Task und sendet täglich das Briefing."""
    logger.info(f"Morning Briefing Scheduler gestartet – täglich um {BRIEFING_TIME} Uhr")

    while True:
        now = datetime.now()
        hour, minute = map(int, BRIEFING_TIME.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(f"Nächstes Briefing in {wait_seconds/3600:.1f} Stunden")
        await asyncio.sleep(wait_seconds)

        try:
            logger.info("Erstelle Morning Briefing...")
            briefing = await generate_briefing()
            await bot.send_message(
                chat_id=chat_id,
                text=briefing,
                parse_mode="Markdown",
            )
            # TTS
            from bot.tts import speak_and_send, is_tts_enabled
            if is_tts_enabled():
                from bot.tts import _clean_for_tts, synthesize
                clean = _clean_for_tts(briefing)
                await speak_and_send(clean, bot, chat_id)
            logger.info("Morning Briefing erfolgreich gesendet.")
        except Exception as e:
            logger.error(f"Morning Briefing Fehler: {e}")

        # Kurze Pause damit wir nicht doppelt senden
        await asyncio.sleep(60)
