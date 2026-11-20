"""
bot/flug/store.py – Phase 247 (Issue #385)

Persistenz der Preis-Suchen als JSON unter ~/.fabbot/. Atomar geschrieben
(mkstemp im selben Verzeichnis + fsync + os.replace), Rechte 0600, weil die
Datei Telegram-IDs enthält.

Alle Schreibzugriffe laufen unter einem Lock und lesen den aktuellen Stand
neu ein: Der Watcher arbeitet minutenlang mit einer Kopie, währenddessen kann
ein Nutzer eine Suche anlegen oder löschen. `ersetze()` schreibt deshalb nur
Suchen zurück, die es noch gibt – eine gelöschte lebt nicht wieder auf.

Lesen ist tolerant (kaputte Datei → leer), Schreiben nicht: Ist die Datei
beschädigt oder unlesbar, wirft jeder Schreibpfad StoreFehler, statt sie mit
einem leeren Stand zu überschreiben und alle Suchen zu verlieren.
"""

import asyncio
import json
import logging
import os
import secrets
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bot.flug.modelle import Watch

logger = logging.getLogger(__name__)

_STORE_FILE = Path.home() / ".fabbot" / "flug_watches.json"
MAX_WATCHES_PRO_USER = 10
_LOCK = asyncio.Lock()


class StoreFehler(RuntimeError):
    """Store-Datei beschädigt oder unlesbar – Schreiben verweigert."""


# ── Lesen ─────────────────────────────────────────────────────────────────────


def _lade_roh(*, streng: bool = False) -> dict[str, Any]:
    try:
        daten = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        if streng:
            raise StoreFehler(f"Flug-Store nicht lesbar ({type(e).__name__})") from None
        logger.error(f"Flug-Store nicht lesbar: {type(e).__name__}")
        return {}
    if not isinstance(daten, dict):
        if streng:
            raise StoreFehler("Flug-Store hat ein unerwartetes Format")
        return {}
    return daten


def _parse_watch(eintrag: Any) -> Watch | None:
    try:
        return Watch.model_validate(eintrag)
    except ValidationError:
        return None


def _watches_aus(roh: dict[str, Any]) -> tuple[Watch, ...]:
    liste = roh.get("watches", [])
    if not isinstance(liste, list):
        return ()
    watches = tuple(w for w in (_parse_watch(e) for e in liste) if w is not None)
    if len(watches) != len(liste):
        logger.warning(f"Flug-Store: {len(liste) - len(watches)} ungültige Einträge übersprungen")
    return watches


def _ungueltige_aus(roh: dict[str, Any]) -> tuple[Any, ...]:
    """Rohe Einträge, die nicht validieren – sie werden beim Schreiben unverändert
    mitgeschrieben, damit z. B. eine spätere Modelländerung keine Suchen löscht."""
    liste = roh.get("watches", [])
    if not isinstance(liste, list):
        return ()
    return tuple(e for e in liste if _parse_watch(e) is None)


def _status_aus(roh: dict[str, Any]) -> dict[str, Any]:
    try:
        fehlschlaege = int(roh.get("fehlschlaege", 0))
    except (TypeError, ValueError):
        fehlschlaege = 0
    letzter_lauf = roh.get("letzter_lauf")
    return {
        "fehlschlaege": fehlschlaege,
        "gemeldet": bool(roh.get("gemeldet", False)),
        "letzter_lauf": letzter_lauf if isinstance(letzter_lauf, str) else None,
    }


def lade_watches() -> tuple[Watch, ...]:
    return _watches_aus(_lade_roh())


def watches_von(user_id: int) -> tuple[Watch, ...]:
    return tuple(w for w in lade_watches() if w.user_id == user_id)


def lade_status() -> dict[str, Any]:
    return _status_aus(_lade_roh())


# ── Schreiben ─────────────────────────────────────────────────────────────────


def _schreibe(watches: Iterable[Watch], status: dict[str, Any], ungueltig: tuple[Any, ...] = ()) -> None:
    daten = {"watches": [*(w.model_dump(mode="json") for w in watches), *ungueltig], **status}
    _STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_STORE_FILE.parent, prefix=f".{_STORE_FILE.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _STORE_FILE)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise


def _eindeutig(watch: Watch, vorhanden: tuple[Watch, ...]) -> Watch:
    ids = {w.id for w in vorhanden}
    neu = watch
    while neu.id in ids:
        neu = neu.model_copy(update={"id": secrets.token_hex(4)})
    return neu


async def fuege_hinzu(watch: Watch) -> Watch | None:
    """Speichert eine neue Suche (ggf. mit neuer id). None = Limit des Nutzers erreicht."""
    async with _LOCK:
        roh = _lade_roh(streng=True)
        watches = _watches_aus(roh)
        if sum(1 for w in watches if w.user_id == watch.user_id) >= MAX_WATCHES_PRO_USER:
            return None
        gespeichert = _eindeutig(watch, watches)
        _schreibe((*watches, gespeichert), _status_aus(roh), _ungueltige_aus(roh))
        return gespeichert


async def entferne(user_id: int, watch_id: str) -> Watch | None:
    """Löscht eine Suche – nur, wenn sie diesem Nutzer gehört."""
    async with _LOCK:
        roh = _lade_roh(streng=True)
        watches = _watches_aus(roh)
        treffer = next((w for w in watches if w.id == watch_id and w.user_id == user_id), None)
        if treffer is None:
            return None
        rest = tuple(w for w in watches if (w.id, w.user_id) != (watch_id, user_id))
        _schreibe(rest, _status_aus(roh), _ungueltige_aus(roh))
        return treffer


async def ersetze(aktualisiert: Iterable[Watch], entfernt_ids: frozenset[str] = frozenset()) -> None:
    """Übernimmt geänderte Suchen per (id, user_id); zwischendurch gelöschte bleiben gelöscht."""
    neu_nach_schluessel = {(w.id, w.user_id): w for w in aktualisiert}
    async with _LOCK:
        roh = _lade_roh(streng=True)
        watches = _watches_aus(roh)
        ergebnis = tuple(neu_nach_schluessel.get((w.id, w.user_id), w) for w in watches if w.id not in entfernt_ids)
        _schreibe(ergebnis, _status_aus(roh), _ungueltige_aus(roh))


async def speichere_status(**aenderungen: Any) -> None:
    """Übernimmt einzelne Statusfelder (fehlschlaege, gemeldet, letzter_lauf)."""
    async with _LOCK:
        roh = _lade_roh(streng=True)
        _schreibe(_watches_aus(roh), {**_status_aus(roh), **aenderungen}, _ungueltige_aus(roh))
