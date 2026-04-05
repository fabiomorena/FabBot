"""
WhatsApp Web Automation für FabBot – Phase 81.

Playwright-basiert mit persistenter Session.

Setup-Flow:
  1. /wa_setup → headed Browser → QR-Code scannen → Session gespeichert
  2. Danach: headless, unsichtbar im Hintergrund

Sicherheit:
  - Nur Kontakte aus personal_profile.yaml (whatsapp_contacts) erlaubt
  - HITL vor jedem Senden
  - Audit-Log für jede gesendete Nachricht

Öffentliche API:
  is_session_ready()                          → bool
  load_whatsapp_contacts()                    → list[dict]
  find_contact(name)                          → dict | None
  await init_whatsapp_session()               → (bool, str)
  await send_whatsapp_message(name, text)     → (bool, str)
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_DIR  = Path.home() / ".fabbot" / "whatsapp_session"
_SESSION_FILE = _SESSION_DIR / "session.json"
_WHATSAPP_URL = "https://web.whatsapp.com"

# Timeouts in ms
_LOAD_TIMEOUT   = 30_000
_QR_TIMEOUT     = 120_000   # 2 Minuten für QR-Scan
_SEARCH_TIMEOUT =  8_000

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Mögliche Selektoren – WhatsApp ändert diese gelegentlich
_SEARCH_SELECTORS = [
    '[data-testid="search"]',
    '[data-testid="chat-list-search"]',
    'div[contenteditable="true"][data-tab="3"]',
]
_INPUT_SELECTORS = [
    '[data-testid="conversation-compose-box-input"]',
    'div[contenteditable="true"][data-tab="10"]',
    'footer div[contenteditable="true"]',
]


# ---------------------------------------------------------------------------
# Session & Kontakt-Utilities
# ---------------------------------------------------------------------------

def is_session_ready() -> bool:
    """Gibt True zurück wenn eine gespeicherte Session existiert."""
    return _SESSION_FILE.exists() and _SESSION_FILE.stat().st_size > 100


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


# ---------------------------------------------------------------------------
# Setup – QR-Code Session (einmalig, headed)
# ---------------------------------------------------------------------------

async def init_whatsapp_session() -> tuple[bool, str]:
    """
    Öffnet einen sichtbaren Browser für den QR-Code Scan.
    Speichert die Session danach als JSON unter ~/.fabbot/whatsapp_session/.
    Gibt (success, message) zurück.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        return False, (
            "playwright nicht installiert.\n"
            "Bitte ausführen:\n"
            "pip install playwright\n"
            "playwright install chromium"
        )

    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("WhatsApp: Starte headed Browser für QR-Code Scan...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--no-sandbox"],
        )
        context = await browser.new_context(user_agent=_USER_AGENT)
        page = await context.new_page()

        try:
            await page.goto(_WHATSAPP_URL, timeout=_LOAD_TIMEOUT)
            logger.info("WhatsApp: Warte auf QR-Code Scan (max. 2 Minuten)...")

            # Chat-Liste erscheint = erfolgreich eingeloggt
            await page.wait_for_selector(
                '[data-testid="chat-list"]',
                timeout=_QR_TIMEOUT,
            )
            await context.storage_state(path=str(_SESSION_FILE))
            logger.info(f"WhatsApp: Session gespeichert: {_SESSION_FILE}")
            await browser.close()
            return True, "✅ WhatsApp Session gespeichert – Bot kann jetzt Nachrichten senden."

        except PWTimeout:
            await browser.close()
            return False, "Timeout beim QR-Code Scan (2 Minuten). Bitte /wa_setup nochmal versuchen."
        except Exception as e:
            logger.error(f"WhatsApp init_session Fehler: {e}")
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Fehler beim Setup: {e}"


# ---------------------------------------------------------------------------
# Nachricht senden (headless)
# ---------------------------------------------------------------------------

async def send_whatsapp_message(whatsapp_name: str, text: str) -> tuple[bool, str]:
    """
    Sendet eine WhatsApp-Nachricht via Playwright (headless).

    whatsapp_name : exakter Name wie in WhatsApp Web angezeigt (inkl. Emojis)
    text          : Nachrichtentext

    Gibt (success, detail_message) zurück.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        return False, "playwright nicht installiert."

    if not is_session_ready():
        return False, "Keine WhatsApp-Session. Bitte zuerst /wa_setup ausführen."

    logger.info(f"WhatsApp: Sende an '{whatsapp_name}': {text[:60]}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )
        context = await browser.new_context(
            storage_state=str(_SESSION_FILE),
            user_agent=_USER_AGENT,
        )
        page = await context.new_page()

        try:
            await page.goto(_WHATSAPP_URL, timeout=_LOAD_TIMEOUT)

            # Session-Check
            try:
                await page.wait_for_selector(
                    '[data-testid="chat-list"]',
                    timeout=_LOAD_TIMEOUT,
                )
            except PWTimeout:
                await browser.close()
                return False, (
                    "WhatsApp Web nicht geladen – Session möglicherweise abgelaufen.\n"
                    "Bitte /wa_setup erneut ausführen."
                )

            # Suchfeld finden
            search_box = await _find_element(page, _SEARCH_SELECTORS, timeout=5_000)
            if search_box is None:
                await browser.close()
                return False, "Suchfeld nicht gefunden – WhatsApp Web Layout möglicherweise geändert."

            await search_box.click()
            await search_box.fill(whatsapp_name)
            await asyncio.sleep(2.0)  # Suchergebnisse laden lassen

            # Kontakt klicken
            clicked = await _click_contact(page, whatsapp_name)
            if not clicked:
                await browser.close()
                return False, f"Kontakt '{whatsapp_name}' nicht in WhatsApp gefunden."

            await asyncio.sleep(0.8)

            # Nachrichtenfeld finden
            msg_box = await _find_element(page, _INPUT_SELECTORS, timeout=5_000)
            if msg_box is None:
                await browser.close()
                return False, "Nachrichtenfeld nicht gefunden."

            await msg_box.click()
            await msg_box.fill(text)
            await asyncio.sleep(0.3)
            await page.keyboard.press("Enter")
            await asyncio.sleep(1.5)  # Senden abwarten

            logger.info(f"WhatsApp: Erfolgreich gesendet an '{whatsapp_name}'")
            await browser.close()
            return True, f"✅ WhatsApp gesendet an {whatsapp_name}."

        except Exception as e:
            logger.error(f"WhatsApp send_message Fehler: {e}")
            try:
                await browser.close()
            except Exception:
                pass
            return False, f"Fehler beim Senden: {e}"


# ---------------------------------------------------------------------------
# Private Hilfsfunktionen
# ---------------------------------------------------------------------------

async def _find_element(page, selectors: list[str], timeout: int = 5_000):
    """Probiert mehrere Selektoren durch, gibt das erste funktionierende Element zurück."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.wait_for(timeout=timeout)
            return el
        except Exception:
            continue
    return None


async def _click_contact(page, whatsapp_name: str) -> bool:
    """
    Klickt den Kontakt in den Suchergebnissen.
    Versucht zuerst exakten title-Match, dann ersten Suchtreffer.
    """
    # Versuch 1: title-Attribut (exakter Match inkl. Emoji)
    try:
        contact = page.locator(f'[title="{whatsapp_name}"]').first
        await contact.click(timeout=_SEARCH_TIMEOUT)
        return True
    except Exception:
        pass

    # Versuch 2: ersten Suchtreffer nehmen
    try:
        first = page.locator('[data-testid="cell-frame-container"]').first
        await first.click(timeout=_SEARCH_TIMEOUT)
        return True
    except Exception:
        pass

    return False
