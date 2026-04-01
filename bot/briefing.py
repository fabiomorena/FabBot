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

_raw_time = os.getenv("BRIEFING_TIME", "07:30")
try:
    _h, _m = _raw_time.split(":")
    assert 0 <= int(_h) <= 23 and 0 <= int(_m) <= 59
    BRIEFING_TIME = _raw_time
except Exception:
    logger.warning(f"Ungültiges BRIEFING_TIME Format '{_raw_time}' – verwende 07:30")
    BRIEFING_TIME = "07:30"


async def _get_weather_berlin() -> str:
    """Holt aktuelles Berliner Wetter via wttr.in – kein API-Key nötig."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://wttr.in/Berlin?format=j1")
            resp.raise_for_status()
            data = resp.json()

        current = data.get("current_condition", [{}])[0]
        temp = current["temp_C"]
        feels = current["FeelsLikeC"]
        desc = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        wind = current["windspeedKmph"]

        # Wettericon
        icons = {
            "Sunny": "☀️", "Clear": "🌙", "Partly cloudy": "⛅",
            "Cloudy": "☁️", "Overcast": "☁️", "Mist": "🌫️",
            "Rain": "🌧️", "Light rain": "🌦️", "Heavy rain": "🌧️",
            "Snow": "❄️", "Thunder": "⛈️", "Fog": "🌫️",
        }
        icon = next((v for k, v in icons.items() if k.lower() in desc.lower()), "🌡️")

        # Forecast fuer heute
        forecast = data.get("weather", [{}])[0]
        max_temp = forecast.get("maxtempC", "?")
        min_temp = forecast.get("mintempC", "?")

        return (
            f"{icon} {desc}\n"
            f"🌡️ Aktuell: {temp}°C (gefühlt {feels}°C)\n"
            f"📊 Heute: {min_temp}°C – {max_temp}°C\n"
            f"💧 Luftfeuchtigkeit: {humidity}% | 💨 Wind: {wind} km/h"
        )
    except Exception as e:
        logger.warning(f"Wetter-Fehler: {e}")
        return "Wetter nicht verfügbar."


def _get_calendar_today() -> str:
    """Holt heutige Kalender-Termine via AppleScript (Temp-Datei)."""
    import tempfile, os, time
    today = date.today().strftime("%d.%m.%Y")
    script_lines = [
        f'set startDate to date "{today}"',
        f'set endDate to date "{today}" + (23 * hours) + (59 * minutes)',
        'set output to ""',
        'tell application "Calendar"',
        '    repeat with cal in calendars',
        '        set evts to (every event of cal whose start date >= startDate and start date <= endDate)',
        '        repeat with evt in evts',
        '            set evtStart to start date of evt',
        '            set h to hours of evtStart as string',
        '            set m to minutes of evtStart',
        '            if m < 10 then',
        '                set mStr to "0" & (m as string)',
        '            else',
        '                set mStr to m as string',
        '            end if',
        '            set output to output & (summary of evt) & "|" & h & ":" & mStr & linefeed',
        '        end repeat',
        '    end repeat',
        'end tell',
        'return output',
    ]
    script = "\n".join(script_lines)
    try:
        # Calendar.app aktivieren damit AppleScript nicht haengt
        subprocess.run(["open", "-a", "Calendar"], check=False, timeout=5)
        time.sleep(2)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".applescript", delete=False, encoding="utf-8"
        ) as f:
            f.write(script)
            tmp_path = f.name
        result = subprocess.run(
            ["osascript", tmp_path],
            capture_output=True, text=True, timeout=30
        )
        os.unlink(tmp_path)
        if result.returncode != 0 or not result.stdout.strip():
            return "Keine Termine heute."
        lines = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                lines.append(f"\u2022 {parts[1]} Uhr \u2013 {parts[0]}")
        return "\n".join(lines) if lines else "Keine Termine heute."
    except Exception as e:
        logger.warning(f"Kalender-Fehler im Briefing: {e}")
        return "Kalender nicht verf\u00fcgbar."

async def _fetch_web(query: str) -> str:
    """Einfache Web-Suche via Tavily (kein Brave-Fallback im Briefing)."""
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
                        snippet = re.sub(r"\s+", " ", snippet).strip()
                        # Am letzten Satzende abschneiden statt hart bei 80 Zeichen
                        if len(snippet) > 120:
                            cut = snippet[:120]
                            last_dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
                            snippet = cut[:last_dot + 1] if last_dot > 30 else cut.strip()
                        lines.append(f"• {title}" + (f"\n  {snippet}" if snippet else ""))
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
    wetter_task = asyncio.create_task(_get_weather_berlin())
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
                await speak_and_send(briefing, bot, chat_id)
            logger.info("Morning Briefing erfolgreich gesendet.")
        except Exception as e:
            logger.error(f"Morning Briefing Fehler: {e}")

        # Kurze Pause damit wir nicht doppelt senden
        await asyncio.sleep(60)
