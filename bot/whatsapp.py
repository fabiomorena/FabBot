"""
WhatsApp Web Service Client für FabBot – Phase 83/86/95c.

Phase 95c Fix (Issue #7): stop_service() → async def stop_service().
Vorher: stop_service() war sync und wurde in _post_shutdown() (async) direkt
aufgerufen → blockierte den Event Loop beim Shutdown (subprocess.terminate()
ist zwar non-blocking, aber der sync Aufruf im async Context ist schlechtes
Pattern und verhindert künftige await-Erweiterungen wie graceful drain).
Jetzt: async def, await stop_service() in _post_shutdown().
Abwärtskompatibel – subprocess.terminate() selbst braucht kein await.

Phase 86 Fix #2: start_service() nutzt aktives HTTP-Polling statt
blindem asyncio.sleep(3). Erkennt einen erfolgreichen Start sobald
der Express-Server antwortet (typisch 0.5–2s, nicht immer 3s).

Öffentliche API (abwärtskompatibel):
  is_session_ready()                               → bool (sync)
  load_whatsapp_contacts()                         → list[dict]
  find_contact(name)                               → dict | None
  await get_service_status()                       → dict
  await get_qr_code()                              → str | None
  await send_whatsapp_message(wa_name, text)       → (bool, str)
  await start_service()                            → bool
  await stop_service()                             → None  ← Phase 95c
  await add_whatsapp_contact(name, wa_name)        → (bool, str)
  await remove_whatsapp_contact(name)              → (bool, str)
  list_whatsapp_contacts_formatted()               → str
"""

import asyncio
import logging
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ── Konfiguration ─────────────────────────────────────────────────────────
_SERVICE_PORT = int(os.getenv("WA_SERVICE_PORT", "8767"))
_SERVICE_URL = f"http://127.0.0.1:{_SERVICE_PORT}"
_TOKEN_PATH = Path.home() / ".fabbot" / "wa_service_token"
_STATUS_FILE = Path.home() / ".fabbot" / "wa_ready"
_NODE_SERVICE = Path(__file__).parent.parent / "whatsapp_service" / "server.js"
_HTTP_TIMEOUT = 10

# Phase 86: Polling-Konfiguration für start_service()
_STARTUP_POLL_INTERVAL = 0.5  # Sekunden zwischen Versuchen
_STARTUP_POLL_ATTEMPTS = 20  # max 10 Sekunden warten (20 × 0.5s)

_service_process: subprocess.Popen | None = None


# ── Token Management ──────────────────────────────────────────────────────


def _get_or_create_token() -> str:
    if _TOKEN_PATH.exists():
        _TOKEN_PATH.chmod(0o600)
        return _TOKEN_PATH.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(token)
    _TOKEN_PATH.chmod(0o600)
    logger.info(f"WhatsApp Service Token erstellt: {_TOKEN_PATH}")
    return token


_SERVICE_TOKEN: str = _get_or_create_token()


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_SERVICE_TOKEN}"}


# ── Service Lifecycle ─────────────────────────────────────────────────────


async def start_service() -> bool:
    """
    Startet den Node.js WhatsApp Service als Subprocess.

    Phase 86 Fix #2: Aktives HTTP-Polling statt blindem asyncio.sleep(3).
    Prüft alle 0.5s ob der Express-Server antwortet (max 10s).
    """
    global _service_process

    node_bin = shutil.which("node")
    if not node_bin:
        logger.info("Node.js nicht in PATH – WhatsApp Service deaktiviert.")
        return False

    if not _NODE_SERVICE.exists():
        logger.info(f"WhatsApp Service nicht gefunden: {_NODE_SERVICE} – bitte npm install ausführen.")
        return False

    node_modules = _NODE_SERVICE.parent / "node_modules"
    if not node_modules.exists():
        logger.warning("whatsapp_service/node_modules fehlt – bitte 'cd whatsapp_service && npm install' ausführen.")
        return False

    if _service_process and _service_process.poll() is None:
        logger.debug("WhatsApp Service läuft bereits.")
        return True

    try:
        env = {**os.environ, "FABBOT_WA_TOKEN": _SERVICE_TOKEN}
        _service_process = subprocess.Popen(
            [node_bin, str(_NODE_SERVICE)],
            env=env,
            cwd=str(_NODE_SERVICE.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.error(f"WhatsApp Service Start fehlgeschlagen: {e}")
        return False

    # Phase 86: Aktives Polling statt blindem sleep(3)
    for attempt in range(_STARTUP_POLL_ATTEMPTS):
        await asyncio.sleep(_STARTUP_POLL_INTERVAL)

        if _service_process.poll() is not None:
            logger.error("WhatsApp Service sofort beendet – Check whatsapp_service/node_modules.")
            return False

        try:
            status = await get_service_status()
            if status.get("ok"):
                elapsed = (attempt + 1) * _STARTUP_POLL_INTERVAL
                logger.info(
                    f"WhatsApp Service gestartet "
                    f"(PID {_service_process.pid}, Port {_SERVICE_PORT}, "
                    f"nach {elapsed:.1f}s)"
                )
                return True
        except Exception:
            pass

    if _service_process.poll() is None:
        elapsed = _STARTUP_POLL_ATTEMPTS * _STARTUP_POLL_INTERVAL
        logger.info(
            f"WhatsApp Service gestartet (PID {_service_process.pid}) – "
            f"/status nach {elapsed:.0f}s noch nicht bereit (QR wird generiert)"
        )
        return True

    logger.error("WhatsApp Service hat sich nach dem Start beendet.")
    return False


async def stop_service() -> None:
    """
    Stoppt den Node.js WhatsApp Service sauber.

    Phase 95c (Issue #7): sync → async.
    Vorher: _post_shutdown() rief stop_service() sync auf – schlechtes Pattern
    im async Shutdown-Hook, blockiert den Event Loop und verhindert künftige
    await-Erweiterungen (z.B. graceful drain, HTTP-Goodbye-Call).
    Jetzt: await stop_service() in _post_shutdown() – konsistent async.
    subprocess.terminate() ist non-blocking, kein run_in_executor nötig.
    """
    global _service_process
    if _service_process and _service_process.poll() is None:
        _service_process.terminate()
        logger.info("WhatsApp Service gestoppt.")
    _service_process = None


# ── Session Status (sync) ─────────────────────────────────────────────────


def is_session_ready() -> bool:
    """
    Synchrone Prüfung ob WhatsApp verbunden ist.
    Liest die Status-Datei die der Node.js Service schreibt/löscht.
    """
    return _STATUS_FILE.exists()


# ── Service API ───────────────────────────────────────────────────────────


async def get_service_status() -> dict:
    """Holt Status vom Node.js Service via HTTP."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{_SERVICE_URL}/status", headers=_auth_headers())
            return resp.json()
    except Exception as e:
        return {"ok": False, "ready": False, "qr_available": False, "error": str(e)}


async def get_qr_code() -> str | None:
    """Holt den aktuellen QR-Code-String vom Node.js Service."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{_SERVICE_URL}/qr", headers=_auth_headers())
            if resp.status_code == 200:
                return resp.json().get("qr")
    except Exception as e:
        logger.debug(f"QR-Code abrufen fehlgeschlagen: {e}")
    return None


# ── Kontakt-Management (Profile YAML) ────────────────────────────────────


def load_whatsapp_contacts() -> list[dict]:
    """Lädt whatsapp_contacts aus personal_profile.yaml. Fail-safe."""
    try:
        from agent.profile import load_profile

        profile = load_profile()
        contacts = profile.get("whatsapp_contacts", [])
        return contacts if isinstance(contacts, list) else []
    except Exception as e:
        logger.error(f"WhatsApp: Kontakte konnten nicht geladen werden: {e}")
        return []


def find_contact(name: str) -> dict | None:
    """Sucht einen Kontakt (case-insensitive). Gibt dict oder None zurück."""
    contacts = load_whatsapp_contacts()
    name_lower = name.strip().lower()
    for c in contacts:
        if not isinstance(c, dict):
            continue
        if c.get("name", "").lower() == name_lower:
            return c
    return None


# ── Nachricht senden ──────────────────────────────────────────────────────


async def send_whatsapp_message(whatsapp_name: str, text: str) -> tuple[bool, str]:
    """Sendet eine WhatsApp-Nachricht via Node.js Service."""
    if not is_session_ready():
        status = await get_service_status()
        if not status.get("ready"):
            return False, ("WhatsApp nicht verbunden.\nBitte /wa_setup ausführen um die Session herzustellen.")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_SERVICE_URL}/send",
                headers=_auth_headers(),
                json={"to": whatsapp_name, "message": text},
            )
            data = resp.json()
            if data.get("ok"):
                return True, data.get("detail", f"✅ Gesendet an {whatsapp_name}")
            else:
                return False, data.get("error", "Unbekannter Fehler beim Senden.")

    except httpx.TimeoutException:
        return False, "Timeout beim Senden – bitte nochmal versuchen."
    except Exception as e:
        logger.error(f"WhatsApp send_message Fehler: {e}")
        return False, f"Fehler beim Senden: {e}"


# ── Kontakt-Management CRUD ───────────────────────────────────────────────


async def add_whatsapp_contact(name: str, whatsapp_name: str) -> tuple[bool, str]:
    name = name.strip()
    whatsapp_name = whatsapp_name.strip()
    if not name or not whatsapp_name:
        return False, "Name und WhatsApp-Name dürfen nicht leer sein."
    try:
        from agent.profile import load_profile, write_profile

        profile = load_profile()
        if "whatsapp_contacts" not in profile or not isinstance(profile["whatsapp_contacts"], list):
            profile["whatsapp_contacts"] = []
        for c in profile["whatsapp_contacts"]:
            if isinstance(c, dict) and c.get("name", "").lower() == name.lower():
                c["whatsapp_name"] = whatsapp_name
                await write_profile(profile)
                return True, f"Kontakt aktualisiert: {name}"
        profile["whatsapp_contacts"].append({"name": name, "whatsapp_name": whatsapp_name})
        await write_profile(profile)
        return True, f"Kontakt hinzugefügt: {name} → {whatsapp_name}"
    except Exception as e:
        return False, f"Fehler beim Speichern: {e}"


async def remove_whatsapp_contact(name: str) -> tuple[bool, str]:
    name = name.strip()
    if not name:
        return False, "Kein Name angegeben."
    try:
        from agent.profile import load_profile, write_profile

        profile = load_profile()
        contacts = profile.get("whatsapp_contacts", [])
        if not isinstance(contacts, list):
            return False, "Keine Kontakte vorhanden."
        original_len = len(contacts)
        profile["whatsapp_contacts"] = [
            c for c in contacts if not (isinstance(c, dict) and c.get("name", "").lower() == name.lower())
        ]
        if len(profile["whatsapp_contacts"]) == original_len:
            return False, f"Kontakt nicht gefunden: {name}"
        await write_profile(profile)
        return True, f"Kontakt entfernt: {name}"
    except Exception as e:
        return False, f"Fehler beim Entfernen: {e}"


def list_whatsapp_contacts_formatted() -> str:
    contacts = load_whatsapp_contacts()
    if not contacts:
        return "Keine WhatsApp-Kontakte konfiguriert."
    lines = [f"WhatsApp-Kontakte ({len(contacts)}):"]
    for c in contacts:
        if isinstance(c, dict):
            lines.append(f"- {c.get('name', '?')} → {c.get('whatsapp_name', '?')}")
    return "\n".join(lines)
