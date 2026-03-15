"""
Lokaler HTTP-Server fuer Menubar-Kommunikation.
Laeuft auf localhost:8766.
Gesichert mit einem Shared Secret Token.
"""
import asyncio
import logging
import os
import secrets
from pathlib import Path
from aiohttp import web

logger = logging.getLogger(__name__)

_message_queue: asyncio.Queue = None

# Token-Datei unter ~/.fabbot/local_api_token
TOKEN_PATH = Path.home() / ".fabbot" / "local_api_token"


def get_or_create_token() -> str:
    """Erstellt oder liest das Shared Secret Token."""
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text().strip()
    token = secrets.token_urlsafe(32)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token)
    TOKEN_PATH.chmod(0o600)  # Nur owner darf lesen
    logger.info(f"Local API token erstellt: {TOKEN_PATH}")
    return token


LOCAL_API_TOKEN = get_or_create_token()


def get_queue() -> asyncio.Queue:
    global _message_queue
    if _message_queue is None:
        _message_queue = asyncio.Queue()
    return _message_queue


def _check_auth(request: web.Request) -> bool:
    """Prueft den Authorization Header."""
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {LOCAL_API_TOKEN}"


async def _handle_message(request: web.Request) -> web.Response:
    if not _check_auth(request):
        logger.warning(f"Local API: unauthorized request from {request.remote}")
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "Empty message"}, status=400)
        await get_queue().put(text)
        logger.info(f"Local API: queued message: {text[:50]}")
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_status(request: web.Request) -> web.Response:
    if not _check_auth(request):
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
    return web.json_response({"ok": True, "status": "running"})


async def start_local_api():
    app = web.Application()
    app.router.add_post("/message", _handle_message)
    app.router.add_get("/status", _handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8766)
    await site.start()
    logger.info("Local API running on http://127.0.0.1:8766 (token-secured)")
