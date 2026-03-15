"""
Kleiner lokaler HTTP-Server der vom Menubar angesprochen werden kann.
Laeuft auf localhost:8766 und nimmt Nachrichten entgegen.
"""
import asyncio
import json
import logging
from aiohttp import web

logger = logging.getLogger(__name__)

_message_queue: asyncio.Queue = None


def get_queue() -> asyncio.Queue:
    global _message_queue
    if _message_queue is None:
        _message_queue = asyncio.Queue()
    return _message_queue


async def _handle_message(request: web.Request) -> web.Response:
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
    return web.json_response({"ok": True, "status": "running"})


async def start_local_api():
    app = web.Application()
    app.router.add_post("/message", _handle_message)
    app.router.add_get("/status", _handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8766)
    await site.start()
    logger.info("Local API running on http://127.0.0.1:8766")