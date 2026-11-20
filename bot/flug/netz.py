"""
bot/flug/netz.py – Phase 247 (Issue #385)

GET mit JSON-Antwort und harter Größengrenze. Die Antwort wird gestreamt und
bei Überschreiten abgebrochen, statt sie komplett in den FabBot-Prozess zu
laden.
"""

import json
from typing import Any

import httpx

MAX_BYTES = 2_000_000


async def hole_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Any,
    headers: dict[str, str] | None = None,
    fehler: type[Exception],
) -> Any:
    """Lädt JSON; jede Störung wird als `fehler` mit untainted Text geworfen."""
    teile: tuple[bytes, ...] = ()
    groesse = 0
    try:
        async with client.stream("GET", url, params=params, headers=headers) as resp:
            if resp.status_code != 200:
                raise fehler(f"HTTP {resp.status_code}")
            async for stueck in resp.aiter_bytes():
                groesse += len(stueck)
                if groesse > MAX_BYTES:
                    raise fehler("Antwort zu groß")
                teile = (*teile, stueck)
    except httpx.HTTPError as e:
        raise fehler(f"Netzwerkfehler: {type(e).__name__}") from None
    try:
        return json.loads(b"".join(teile))
    except ValueError:
        raise fehler("Antwort ist kein JSON") from None
