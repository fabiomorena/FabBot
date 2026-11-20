"""
bot/flug/watcher.py – Phase 247 (Issue #385)

Prüft alle gespeicherten Suchen im konfigurierten Intervall und schickt
Preisalarme über den Flug-Bot. Abrufprobleme gehen – wie beim MFA-Watch –
erst ab einer Schwelle an Fabios FabBot-Chat und danach erst wieder nach
einem Erfolg.

Anders als mfa_watch schläft der Scheduler nach einem Neustart nur die
Restzeit seit dem letzten Lauf: Bei häufigen Neustarts (USB-Disconnects der
externen SSD) käme ein 6-h-Timer, der jedes Mal von vorn beginnt, nie an.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from telegram import LinkPreviewOptions

from agent.config import get_settings
from bot.flug import store
from bot.flug.auswahl import bewerte, passende_angebote
from bot.flug.modelle import Angebot, Watch
from bot.flug.texte import abgelaufen_text, alert_text
from bot.flug.travelpayouts import lade_angebote
from bot.flug.zeitraum import monate

logger = logging.getLogger(__name__)

_FEHLER_SCHWELLE = 3
_PAUSE_SEKUNDEN = 1.0
_MIN_INTERVALL = 900
_STANDARD_INTERVALL = 21600
_ERSTE_WARTEZEIT = 60
_TIMEOUT = 20.0
# Frisch angelegte Suchen prüft die Sofortprüfung des Dialogs – der Watcher
# lässt sie solange aus, sonst könnten beide denselben Alarm schicken.
_SCHONFRIST = timedelta(minutes=15)

Ergebnis = tuple[Watch, Angebot | None, bool]


# ── Prüfen ────────────────────────────────────────────────────────────────────


async def pruefe_watch(client: httpx.AsyncClient, watch: Watch, token: str, heute: date) -> Ergebnis:
    """Fragt jeden Monat des Fensters ab und bewertet das günstigste passende Angebot."""
    gesammelt: tuple[Angebot, ...] = ()
    for monat in monate(max(watch.von, heute), watch.bis):
        treffer = await lade_angebote(client, watch.origin.code, watch.ziel.code, monat, watch.hin_und_zurueck, token)
        gesammelt = (*gesammelt, *treffer)
        await asyncio.sleep(_PAUSE_SEKUNDEN)
    passend = passende_angebote(gesammelt, watch, heute)
    bestes = passend[0] if passend else None
    neu, alarm = bewerte(watch, bestes)
    return neu, bestes, alarm


async def erste_pruefung(watch: Watch, token: str) -> Ergebnis:
    """Sofortprüfung nach dem Anlegen. Speichert bewusst nicht: erst senden, dann merken."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        return await pruefe_watch(client, watch, token, date.today())


async def _sende(bot: Any, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, link_preview_options=LinkPreviewOptions(is_disabled=True))
        return True
    except Exception as e:
        logger.error(f"Flug-Watch: Nachricht nicht zustellbar ({type(e).__name__})")
        return False


async def _pruefe_aktive(aktiv: tuple[Watch, ...], token: str, heute: date) -> tuple[tuple[Watch, Ergebnis], ...]:
    """Prüft alle aktiven Suchen; liefert (alt, Ergebnis)-Paare. Wirft, wenn alle scheitern."""
    paare: tuple[tuple[Watch, Ergebnis], ...] = ()
    fehler: tuple[Exception, ...] = ()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for w in aktiv:
            try:
                paare = (*paare, (w, await pruefe_watch(client, w, token, heute)))
            except Exception as e:
                fehler = (*fehler, e)
                logger.warning(f"Flug-Watch: Abruf fehlgeschlagen ({type(e).__name__})")
    if fehler and len(fehler) == len(aktiv):
        raise fehler[-1]
    return paare


async def pruefe_alle(
    flug_bot: Any,
    token: str,
    heute: date | None = None,
    *,
    erlaubt: frozenset[int],
    jetzt: datetime | None = None,
) -> None:
    """Ein Durchlauf über alle Suchen. Wirft nur, wenn jede Abfrage scheitert.

    Suchen von Nutzern, die nicht (mehr) in der Allowlist stehen, werden
    übersprungen – nicht gelöscht: ein Tippfehler in der .env soll keine
    Daten vernichten, und nach der Korrektur laufen sie einfach weiter.
    """
    heute = heute or date.today()
    jetzt = jetzt or datetime.now(timezone.utc)
    watches = store.lade_watches()
    if not watches:
        return

    abgelaufen = tuple(w for w in watches if w.bis < heute)
    aktiv = tuple(w for w in watches if w.bis >= heute and w.user_id in erlaubt and jetzt - w.angelegt >= _SCHONFRIST)
    for w in abgelaufen:
        if w.user_id in erlaubt:
            await _sende(flug_bot, w.chat_id, abgelaufen_text(w))

    paare: tuple[tuple[Watch, Ergebnis], ...] = ()
    try:
        if aktiv:
            paare = await _pruefe_aktive(aktiv, token, heute)
    finally:
        # Während der Abfragen gelöschte Suchen bekommen keinen Alarm mehr.
        vorhanden = {(w.id, w.user_id) for w in store.lade_watches()}
        noch_da = tuple((alt, erg) for alt, erg in paare if (alt.id, alt.user_id) in vorhanden)
        aktualisiert = tuple([await _melde_alarm(flug_bot, alt, erg) for alt, erg in noch_da])
        await store.ersetze(aktualisiert, frozenset(w.id for w in abgelaufen))


async def _melde_alarm(flug_bot: Any, alt: Watch, ergebnis: Ergebnis) -> Watch:
    """Schickt einen fälligen Alarm; scheitert der Versand, gilt der Preis als nicht gemeldet."""
    neu, bestes, alarm = ergebnis
    if not alarm or bestes is None:
        return neu
    if await _sende(flug_bot, neu.chat_id, alert_text(neu, bestes)):
        return neu
    return neu.model_copy(update={"gemeldeter_preis": alt.gemeldeter_preis})


# ── Scheduler ─────────────────────────────────────────────────────────────────


def _intervall() -> int:
    """Prüfintervall aus der .env; ein Tippfehler darf nichts lahmlegen."""
    try:
        wert = int(str(get_settings().flug_watch_interval).strip())
    except ValueError:
        # Wert bewusst nicht im Log (CodeQL, siehe mfa_watch).
        logger.warning("FLUG_WATCH_INTERVAL ist keine Zahl – Standardintervall aktiv.")
        return _STANDARD_INTERVALL
    return max(wert, _MIN_INTERVALL)


def _erste_wartezeit(intervall: int, letzter_lauf: str | None, jetzt: datetime) -> float:
    """Restzeit bis zum nächsten fälligen Lauf, mindestens _ERSTE_WARTEZEIT."""
    try:
        zuletzt = datetime.fromisoformat(letzter_lauf) if letzter_lauf else None
    except ValueError:
        zuletzt = None
    if zuletzt is None or zuletzt.tzinfo is None:
        return _ERSTE_WARTEZEIT
    rest = intervall - (jetzt - zuletzt).total_seconds()
    return min(max(rest, _ERSTE_WARTEZEIT), intervall)


async def run_flug_watch_scheduler(
    flug_bot: Any, token: str, erlaubt: frozenset[int], fehler_bot: Any, fehler_chat_id: str | int
) -> None:
    """Background-Task: prüft alle Suchen im konfigurierten Intervall."""
    intervall = _intervall()
    logger.info("Flug-Watch Scheduler gestartet")
    warte = _erste_wartezeit(intervall, store.lade_status()["letzter_lauf"], datetime.now(timezone.utc))

    while True:
        await asyncio.sleep(warte)
        warte = intervall
        await _ein_lauf(flug_bot, token, erlaubt, fehler_bot, fehler_chat_id)


async def _ein_lauf(
    flug_bot: Any, token: str, erlaubt: frozenset[int], fehler_bot: Any, fehler_chat_id: str | int
) -> None:
    """Ein Lauf inklusive Statuspflege. Wirft nie – sonst endet der Scheduler."""
    gestartet = datetime.now(timezone.utc).isoformat()
    try:
        await pruefe_alle(flug_bot, token, erlaubt=erlaubt)
    except Exception as e:
        logger.error(f"Flug-Watch Scheduler Fehler: {type(e).__name__}")
        await _melde_fehlschlag(fehler_bot, fehler_chat_id, e, letzter_lauf=gestartet)
        return
    try:
        await store.speichere_status(fehlschlaege=0, gemeldet=False, letzter_lauf=gestartet)
    except Exception as e:
        logger.error(f"Flug-Watch: Status nicht speicherbar ({type(e).__name__})")


async def _melde_fehlschlag(
    bot: Any, fehler_chat_id: str | int, fehler: Exception, *, letzter_lauf: str | None = None
) -> None:
    """Meldet erst ab der Schwelle und danach erst wieder nach einem Erfolg."""
    status = store.lade_status()
    fehlschlaege = status["fehlschlaege"] + 1
    gemeldet = status["gemeldet"]

    if fehlschlaege >= _FEHLER_SCHWELLE and not gemeldet:
        try:
            await bot.send_message(
                chat_id=fehler_chat_id,
                text=(
                    f"⚠️ Flug-Watch: {fehlschlaege} Fehlversuche in Folge.\n"
                    f"Letzter Fehler: {type(fehler).__name__}: {fehler}\n"
                    "Vermutlich ist der Travelpayouts-Token ungültig oder die API nicht erreichbar."
                ),
            )
            gemeldet = True
        except Exception as e:
            logger.error(f"Flug-Watch: Fehlermeldung nicht zustellbar ({type(e).__name__})")

    aenderungen = {"fehlschlaege": fehlschlaege, "gemeldet": gemeldet}
    if letzter_lauf is not None:
        aenderungen = {**aenderungen, "letzter_lauf": letzter_lauf}
    try:
        await store.speichere_status(**aenderungen)
    except Exception as e:
        # Darf den Scheduler nicht beenden – sonst prüft niemand mehr.
        logger.error(f"Flug-Watch: Fehlerstatus nicht speicherbar ({type(e).__name__})")
