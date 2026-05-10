#!/usr/bin/env python3
"""
FabBot Watchdog – läuft via cron, unabhängig vom Bot.

Prüft:
1. Läuft der Launch Agent (com.fabbot.agent)?
2. Ist der Python-Prozess aktiv?

Bei Problem: Telegram-Nachricht direkt via HTTP (kein Bot-Framework nötig).
Bei Recovery: Entwarnung senden.
Auto-Restart: Nach konfigurierbarer Wartezeit wird launchctl kickstart versucht.

State wird in ~/.fabbot/watchdog_state.json gespeichert –
verhindert Spam-Nachrichten bei dauerhaftem Ausfall.

Cron-Eintrag (alle 5 Minuten):
*/5 * * * * /path/to/.venv/bin/python3 /path/to/watchdog.py >> ~/.fabbot/watchdog.log 2>&1
"""

import json
import os
import subprocess
import sys
import time
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
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value


_load_env()

# Settings werden NACH _load_env() geladen – env vars aus .env sind dann gesetzt.
from agent.config import get_settings as _get_settings  # noqa: E402

_cfg = _get_settings()

BOT_TOKEN = _cfg.telegram_bot_token.get_secret_value()

# Phase 86 Fix #1: TELEGRAM_CHAT_ID bevorzugen (semantisch korrekt).
# User-ID ≠ Chat-ID – in Direktchats zufällig identisch, in Gruppen nicht.
# Fallback auf erste ALLOWED_ID für Abwärtskompatibilität.
CHAT_ID = _cfg.telegram_chat_id.strip()
if not CHAT_ID:
    CHAT_ID = _cfg.telegram_allowed_user_ids.split(",")[0].strip()

STATE_PATH = Path.home() / ".fabbot" / "watchdog_state.json"
LOG_PREFIX = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Watchdog:"

# Phase 86 Fix #6: Benannte Konstante statt Magic Number.
_ALERT_DELAY_MINUTES = 10

WATCHDOG_AUTO_RESTART = _cfg.watchdog_auto_restart
WATCHDOG_RESTART_DELAY_MIN = _cfg.watchdog_restart_delay_min
WATCHDOG_MAX_RESTARTS = _cfg.watchdog_max_restarts

# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    defaults = {
        "last_status": "unknown",
        "down_since": None,
        "notified": False,
        "notified_at": None,
        "restart_count": 0,
        "last_restart_at": None,
    }
    try:
        if STATE_PATH.exists():
            stored = json.loads(STATE_PATH.read_text())
            return {**defaults, **stored}
    except Exception as e:
        print(f"{LOG_PREFIX} State laden fehlgeschlagen: {e}")
    return defaults


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
        result = subprocess.run(["launchctl", "list", "com.fabbot.agent"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False
        return '"PID"' in result.stdout
    except Exception:
        return False


def _is_python_process_running() -> bool:
    """Prüft ob ein Python-Prozess mit main.py läuft."""
    try:
        result = subprocess.run(["pgrep", "-f", "FabBot/main.py"], capture_output=True, text=True, timeout=5)
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
# Auto-Restart
# ---------------------------------------------------------------------------


def _attempt_restart(state: dict) -> dict:
    """Versucht Bot-Neustart via launchctl kickstart. Gibt aktualisierten State zurück."""
    restart_num = state.get("restart_count", 0) + 1
    uid = os.getuid()
    service = f"gui/{uid}/com.fabbot.agent"

    print(f"{LOG_PREFIX} Auto-Restart #{restart_num} via launchctl kickstart...")
    _send_telegram(f"🔄 *FabBot Auto-Restart #{restart_num}* wird versucht...")

    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", service],
            capture_output=True,
            text=True,
            timeout=15,
        )
        kickstart_ok = result.returncode == 0
    except Exception as e:
        print(f"{LOG_PREFIX} launchctl kickstart Fehler: {e}")
        kickstart_ok = False

    state["restart_count"] = restart_num
    state["last_restart_at"] = datetime.now().isoformat()

    if kickstart_ok:
        print(f"{LOG_PREFIX} Warte 60s auf Bot-Start...")
        time.sleep(60)
        bot_up_now = _is_bot_up()
    else:
        bot_up_now = False

    if bot_up_now:
        print(f"{LOG_PREFIX} Auto-Restart erfolgreich ✅")
        _send_telegram(f"✅ *FabBot automatisch neu gestartet* (Versuch #{restart_num})")
        state["last_status"] = "up"
        state["down_since"] = None
        state["notified"] = False
        state["notified_at"] = None
    else:
        reason = "launchctl Fehler" if not kickstart_ok else "Bot startet nicht"
        final = restart_num >= WATCHDOG_MAX_RESTARTS
        suffix = "Bitte manuell eingreifen!" if final else "Nächster Versuch beim nächsten Check."
        print(f"{LOG_PREFIX} Auto-Restart #{restart_num} fehlgeschlagen ❌")
        _send_telegram(f"🚨 *Auto-Restart #{restart_num} fehlgeschlagen* – {reason}\n{suffix}")

    return state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    state = _load_state()
    agent_ok = _is_launch_agent_running()
    proc_ok = _is_python_process_running()
    bot_up = agent_ok and proc_ok
    now = datetime.now().isoformat()

    if bot_up:
        print(f"{LOG_PREFIX} Bot läuft ✅")
        if state.get("last_status") == "down" and state.get("notified"):
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
        state["notified_at"] = None
        state["restart_count"] = 0
        state["last_restart_at"] = None
    else:
        print(f"{LOG_PREFIX} Bot DOWN ❌")
        if state.get("last_status") != "down":
            state["down_since"] = now
        state["last_status"] = "down"

        if not state.get("notified"):
            down_since = state.get("down_since", now)
            try:
                down_dt = datetime.fromisoformat(down_since)
                mins_down = (datetime.now() - down_dt).total_seconds() / 60
            except Exception:
                mins_down = 0

            if mins_down >= _ALERT_DELAY_MINUTES:
                sent = _send_telegram(
                    f"🚨 *FabBot ist DOWN!*\n"
                    f"Launch Agent: {'✅' if agent_ok else '❌'}\n"
                    f"Python-Prozess: {'✅' if proc_ok else '❌'}\n"
                    f"Seit: {down_since[:16].replace('T', ' ')} Uhr\n\n"
                    f"_Starte neu: `launchctl start com.fabbot.agent`_"
                )
                if sent:
                    state["notified"] = True
                    state["notified_at"] = now
                    print(f"{LOG_PREFIX} Alert gesendet.")

        if state.get("notified") and WATCHDOG_AUTO_RESTART:
            restart_count = state.get("restart_count", 0)
            if restart_count < WATCHDOG_MAX_RESTARTS:
                reference = state.get("last_restart_at") or state.get("notified_at")
                if reference:
                    try:
                        ref_dt = datetime.fromisoformat(reference)
                        mins_elapsed = (datetime.now() - ref_dt).total_seconds() / 60
                    except Exception:
                        mins_elapsed = WATCHDOG_RESTART_DELAY_MIN
                    if mins_elapsed >= WATCHDOG_RESTART_DELAY_MIN:
                        state = _attempt_restart(state)

    _save_state(state)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print(f"{LOG_PREFIX} TELEGRAM_BOT_TOKEN nicht gesetzt – abbruch.")
        sys.exit(1)
    main()
