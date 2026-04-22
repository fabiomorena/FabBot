#!/usr/bin/env python3
"""
FabBot Watchdog – läuft via cron, unabhängig vom Bot.

Prüft:
1. Läuft der Launch Agent (com.fabbot.agent)?
2. Ist der Python-Prozess aktiv?

Bei Problem: Telegram-Nachricht direkt via HTTP (kein Bot-Framework nötig).
Bei Recovery: Entwarnung senden.

State wird in ~/.fabbot/watchdog_state.json gespeichert –
verhindert Spam-Nachrichten bei dauerhaftem Ausfall.

Cron-Eintrag (alle 5 Minuten):
*/5 * * * * /path/to/.venv/bin/python3 /path/to/watchdog.py >> ~/.fabbot/watchdog.log 2>&1
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Konfiguration – via Umgebungsvariablen oder .env
# ---------------------------------------------------------------------------

# Phase 86 Fix #5: python-dotenv statt eigenem Parser.
# Vorher: key, _, value = line.partition("=") ohne Quote-Stripping →
# TOKEN="abc" wurde als '"abc"' eingelesen (mit Anführungszeichen).
# python-dotenv behandelt Quotes, Kommentare und = in Werten korrekt.
def _load_env() -> None:
    """Lädt .env Datei – bevorzugt python-dotenv, Fallback auf einfachen Parser."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import dotenv_values
        for key, value in dotenv_values(env_path).items():
            if key not in os.environ and value is not None:
                os.environ[key] = value
    except ImportError:
        # Fallback: einfacher Parser – partition trennt nur beim ersten =
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value

_load_env()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Phase 86 Fix #1: TELEGRAM_CHAT_ID bevorzugen (semantisch korrekt).
# User-ID ≠ Chat-ID – in Direktchats zufällig identisch, in Gruppen nicht.
# Fallback auf erste ALLOWED_ID für Abwärtskompatibilität.
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
if not CHAT_ID:
    CHAT_ID = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",")[0].strip()

STATE_PATH = Path.home() / ".fabbot" / "watchdog_state.json"
LOG_PREFIX = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Watchdog:"

# Phase 86 Fix #6: Benannte Konstante statt Magic Number.
# Vorher: `if mins_down >= 9:  # nach ~10 Minuten` – Kommentar und Code widersprüchlich.
_ALERT_DELAY_MINUTES = 10

# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception:
        pass
    return {"last_status": "unknown", "down_since": None, "notified": False}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"{LOG_PREFIX} State-Fehler: {e}")

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _is_launch_agent_running() -> bool:
    """Prüft ob com.fabbot.agent via launchctl läuft."""
    try:
        result = subprocess.run(
            ["launchctl", "list", "com.fabbot.agent"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return False
        return '"PID"' in result.stdout
    except Exception:
        return False


def _is_python_process_running() -> bool:
    """Prüft ob ein Python-Prozess mit main.py läuft."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "FabBot/main.py"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_bot_up() -> bool:
    return _is_launch_agent_running() and _is_python_process_running()

# ---------------------------------------------------------------------------
# Telegram Notification
# ---------------------------------------------------------------------------

def _send_telegram(message: str) -> bool:
    """Sendet Nachricht direkt via Telegram API – kein Bot-Framework."""
    if not BOT_TOKEN or not CHAT_ID:
        print(f"{LOG_PREFIX} Kein Token/Chat-ID – Benachrichtigung übersprungen.")
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"{LOG_PREFIX} Telegram-Fehler: {e}")
        return False

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    state = _load_state()
    bot_up = _is_bot_up()
    now = datetime.now().isoformat()

    if bot_up:
        print(f"{LOG_PREFIX} Bot läuft ✅")
        if state.get("last_status") == "down" and state.get("notified"):
            # Recovery – Entwarnung senden
            downtime = ""
            if state.get("down_since"):
                try:
                    down_dt = datetime.fromisoformat(state["down_since"])
                    delta = datetime.now() - down_dt
                    mins = int(delta.total_seconds() / 60)
                    downtime = f" (war {mins} Minuten down)"
                except Exception:
                    pass
            _send_telegram(f"✅ *FabBot ist wieder online*{downtime}")
        state["last_status"] = "up"
        state["down_since"] = None
        state["notified"] = False
    else:
        print(f"{LOG_PREFIX} Bot DOWN ❌")
        if state.get("last_status") != "down":
            state["down_since"] = now
        state["last_status"] = "down"

        if not state.get("notified"):
            down_since = state.get("down_since", now)
            try:
                down_dt   = datetime.fromisoformat(down_since)
                mins_down = (datetime.now() - down_dt).total_seconds() / 60
            except Exception:
                mins_down = 0

            # Phase 86 Fix #6: _ALERT_DELAY_MINUTES statt Magic Number 9
            if mins_down >= _ALERT_DELAY_MINUTES - 1:
                sent = _send_telegram(
                    f"🚨 *FabBot ist DOWN!*\n"
                    f"Launch Agent: {'✅' if _is_launch_agent_running() else '❌'}\n"
                    f"Python-Prozess: {'✅' if _is_python_process_running() else '❌'}\n"
                    f"Seit: {down_since[:16].replace('T', ' ')} Uhr\n\n"
                    f"_Starte neu: `launchctl start com.fabbot.agent`_"
                )
                if sent:
                    state["notified"] = True
                    print(f"{LOG_PREFIX} Alert gesendet.")

    _save_state(state)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print(f"{LOG_PREFIX} TELEGRAM_BOT_TOKEN nicht gesetzt – abbruch.")
        sys.exit(1)
    main()
