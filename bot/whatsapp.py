"""
WhatsApp Web Service Client für FabBot – Phase 83.

Ersetzt Playwright durch einen HTTP-Client der mit dem
Node.js whatsapp-web.js Microservice kommuniziert (localhost:8767).

Session-Status: Datei ~/.fabbot/wa_ready
  – wird vom Node.js Service erstellt wenn verbunden
  – wird vom Node.js Service gelöscht bei Disconnect / Shutdown
  – Python prüft via is_session_ready() synchron (kein HTTP-Call nötig)

Öffentliche API (abwärtskompatibel zu Phase 81):
  is_session_ready()                               → bool (sync)
  load_whatsapp_contacts()                         → list[dict]
  find_contact(name)                               → dict | None
  await get_service_status()                       → dict
  await get_qr_code()                              → str | None
  await send_whatsapp_message(wa_name, text)       → (bool, str)
  await start_service()                            → bool
  stop_service()                                   → None
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
_SERVICE_PORT    = int(os.getenv("WA_SERVICE_PORT", "8767"))
_SERVICE_URL     = f"http://127.0.0.1:{_SERVICE_PORT}"
_TOKEN_PATH      = Path.home() / ".fabbot" / "wa_service_token"
_STATUS_FILE     = Path.home() / ".fabbot" / "wa_ready"
_NODE_SERVICE    = Path(__file__).parent.parent / "whatsapp_service" / "server.js"
_HTTP_TIMEOUT    = 10

_service_process: subprocess.Popen | None = None


# ── Token Management ──────────────────────────────────────────────────────

def _get_or_create_token() -> str:
    """Erstellt oder liest den Service-Token (wird vom Node.js Service via ENV erhalten)."""
    if _TOKEN_PATH.exists():
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
    Gibt True zurück wenn gestartet oder bereits läuft.
    Gibt False zurück wenn Node.js nicht gefunden oder Service-Datei fehlt.
    """
    global _service_process

    node_bin = shutil.which("node")
    if not node_bin:
        logger.info("Node.js nicht in PATH – WhatsApp Service deaktiviert.")
        return False

    if not _NODE_SERVICE.exists():
        logger.info(f"WhatsApp Service nicht gefunden: {_NODE_SERVICE} – bitte npm install ausführen.")
        return False

    # node_modules prüfen
    node_modules = _NODE_SERVICE.parent / "node_modules"
    if not node_modules.exists():
        logger.warning(
            f"whatsapp_service/node_modules fehlt – bitte 'cd whatsapp_service && npm install' ausführen."
        )
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
        # Kurz warten damit Express hochfährt
        await asyncio.sleep(3)

        if _service_process.poll() is not None:
            logger.error(
                "WhatsApp Service sofort beendet – Check whatsapp_service/node_modules."
            )
            return False

        logger.info(f"WhatsApp Service gestartet (PID {_service_process.pid}, Port {_SERVICE_PORT})")
        return True

    except Exception as e:
        logger.error(f"WhatsApp Service Start fehlgeschlagen: {e}")
        return False


def stop_service() -> None:
    """Stoppt den Node.js WhatsApp Service sauber."""
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
    Kein HTTP-Call – sofort und thread-safe.
    """
    return _STATUS_FILE.exists()


# ── Service API ───────────────────────────────────────────────────────────

async def get_service_status() -> dict:
    """
    Holt Status vom Node.js Service via HTTP.
    Gibt {ok, ready, qr_available, error} zurück.
    Bei Verbindungsfehler: {ok: False, ready: False, qr_available: False, error: str}
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(f"{_SERVICE_URL}/status", headers=_auth_headers())
            return resp.json()
    except Exception as e:
        return {"ok": False, "ready": False, "qr_available": False, "error": str(e)}


async def get_qr_code() -> str | None:
    """
    Holt den aktuellen QR-Code-String vom Node.js Service.
    Gibt None zurück wenn kein QR-Code verfügbar oder Service nicht erreichbar.
    """
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
    """
    Sucht einen Kontakt in whatsapp_contacts (case-insensitive Name-Match).
    Gibt dict mit 'name' + 'whatsapp_name' zurück, oder None.
    """
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
    """
    Sendet eine WhatsApp-Nachricht via Node.js Service.

    whatsapp_name : exakter WhatsApp-Anzeigename (inkl. Emojis)
    text          : Nachrichtentext

    Gibt (success, detail_message) zurück.
    """
    # Service-Status prüfen (nutzt Status-Datei für schnellen Check)
    if not is_session_ready():
        # Nochmal via HTTP prüfen (Service könnte gerade starten)
        status = await get_service_status()
        if not status.get("ready"):
            return False, (
                "WhatsApp nicht verbunden.\n"
                "Bitte /wa_setup ausführen um die Session herzustellen."
            )

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


# ── Kontakt-Management CRUD (verschlüsselte YAML) ────────────────────────

async def add_whatsapp_contact(name: str, whatsapp_name: str) -> tuple[bool, str]:
    name          = name.strip()
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
        return True, f"Kontakt hinzugefuegt: {name} -> {whatsapp_name}"
    except Exception as e:
        return False, f"Fehler beim Speichern: {e}"


async def remove_whatsapp_contact(name: str) -> tuple[bool, str]:
    name = name.strip()
    if not name:
        return False, "Kein Name angegeben."
    try:
        from agent.profile import load_profile, write_profile
        profile   = load_profile()
        contacts  = profile.get("whatsapp_contacts", [])
        if not isinstance(contacts, list):
            return False, "Keine Kontakte vorhanden."
        original_len = len(contacts)
        profile["whatsapp_contacts"] = [
            c for c in contacts
            if not (isinstance(c, dict) and c.get("name", "").lower() == name.lower())
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
            lines.append(f"- {c.get('name','?')} -> {c.get('whatsapp_name','?')}")
    return "\n".join(lines)
