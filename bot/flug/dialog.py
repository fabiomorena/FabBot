"""
bot/flug/dialog.py – Phase 247 (Issue #385)

Telegram-Dialog des Flug-Bots:
/neu → Ziel → Abflug → Zeitraum → Hin & zurück? → Nächte → Preisgrenze.
Dazu /liste, /loeschen, /abbrechen.

Der Entwurf liegt in `nutzerdaten(ctx)["flug"]` und wird bei jedem Schritt als
neues dict gesetzt. Buttons tragen nur Codes, die vorher angeboten wurden –
ein manipulierter Callback kann keinen fremden Ort einschleusen.
"""

import logging
import re
import secrets
import warnings
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError
from telegram import LinkPreviewOptions, Update
from telegram.ext import (
    BaseHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from agent.config import get_settings
from bot.flug import store
from bot.flug.drossel import Drossel
from bot.flug.loeschen import LOESCHEN, cmd_loeschen, on_loeschen_antwort, on_loeschen_klick
from bot.flug.modelle import MAX_NAECHTE, MAX_PREIS, MIN_PREIS, Ort, Watch
from bot.flug.orte import OrtsFehler, suche_orte
from bot.flug.texte import alert_text, liste_text, stand_text, watch_text, zeitraum_text
from bot.flug.tg_hilfen import antworte as _antworte
from bot.flug.tg_hilfen import eingabe_text as _text
from bot.flug.tg_hilfen import tastatur as _tastatur
from bot.flug.tg_hilfen import chat_id, klick, nutzer_id, nutzerdaten
from bot.flug.watcher import erste_pruefung
from bot.flug.zeitraum import ZeitraumFehler, parse_zeitraum

logger = logging.getLogger(__name__)

ZIEL, ABFLUG, ZEITRAUM, ART, DAUER, PREIS = range(6)
_KEY = "flug"

_HILFE = (
    "✈️ Flug-Bot – ich beobachte Flugpreise und melde mich, wenn es günstig wird.\n\n"
    "/neu – neue Suche anlegen\n"
    "/liste – deine Suchen\n"
    "/loeschen – eine Suche löschen (auch /löschen Lissabon)\n"
    "/abbrechen – laufende Eingabe abbrechen"
)
_HILFE_ZEITRAUM = "Beispiele: „März 2027“, „März bis April“, „15.03.–30.03.2027“"
_PREIS_FRAGE = "Bis zu welchem Preis soll ich Alarm schlagen? (Euro pro Person, z. B. 150)"
_PREIS_MUSTER = re.compile(r"^(\d{1,5})(?:[.,]\d{1,2})?\s*(?:€|eur|euro)?$", re.IGNORECASE)
_DAUER_MUSTER = re.compile(r"^(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?$")
_DAUER_OPTIONEN = (("egal", "egal"), ("2–4 Nächte", "2-4"), ("5–9 Nächte", "5-9"), ("10–16 Nächte", "10-16"))
_KEIN_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)
# Konstante statt Setting: CodeQL stuft jeden Settings-Wert als Geheimnis ein,
# und der Abflugort landet in der JSON-Datei (clear-text storage).
_STANDARD_ABFLUG = Ort(code="BER", name="Berlin")

# Schützt den kostenlosen Travelpayouts-Account vor Request-Schleifen.
_ORTSSUCHE = Drossel(max_anzahl=30, fenster_sekunden=3600)
_SOFORTPRUEFUNG = Drossel(max_anzahl=5, fenster_sekunden=3600)


def _heute() -> date:
    return date.today()


# ── Helfer ────────────────────────────────────────────────────────────────────


def _entwurf(ctx: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return nutzerdaten(ctx).get(_KEY, {})


def _setze(ctx: ContextTypes.DEFAULT_TYPE, **werte: Any) -> None:
    nutzerdaten(ctx)[_KEY] = {**_entwurf(ctx), **werte}


def _ort_label(ort: Ort) -> str:
    return f"{ort.anzeige()}, {ort.land}" if ort.land else ort.anzeige()


async def _verloren(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nutzerdaten(ctx)[_KEY] = {}
    await _antworte(update, "Da ist etwas verloren gegangen – starte bitte neu mit /neu.")
    return ConversationHandler.END


async def _suche(update: Update, term: str) -> tuple[Ort, ...] | None:
    """Ortssuche mit nutzertauglicher Fehlermeldung; None = Fehler bereits gemeldet."""
    if not 2 <= len(term) <= 60:
        await _antworte(update, "Bitte einen Ortsnamen mit 2 bis 60 Zeichen.")
        return None
    if not _ORTSSUCHE.erlaube(nutzer_id(update)):
        await _antworte(update, "Zu viele Suchen in kurzer Zeit. Bitte versuch es später nochmal.")
        return None
    try:
        orte = await suche_orte(term)
    except OrtsFehler as e:
        logger.warning(f"Flug-Bot: Ortssuche fehlgeschlagen ({e})")
        await _antworte(update, "Die Ortssuche ist gerade nicht erreichbar. Versuch es gleich nochmal.")
        return None
    if not orte:
        await _antworte(update, "Nichts gefunden. Versuch es mit einem anderen Namen oder dem Flughafencode.")
        return None
    return orte


# ── Allgemeine Befehle ────────────────────────────────────────────────────────


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _antworte(update, _HILFE)


async def cmd_neu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nutzerdaten(ctx)[_KEY] = {}
    await _antworte(update, "Wohin soll's gehen? Schreib eine Stadt oder einen Flughafen.")
    return ZIEL


async def cmd_abbrechen(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nutzerdaten(ctx)[_KEY] = {}
    await _antworte(update, "Abgebrochen.")
    return ConversationHandler.END


# ── Ziel + Abflug ─────────────────────────────────────────────────────────────


async def on_ziel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    orte = await _suche(update, _text(update))
    if orte is None:
        return ZIEL
    if len(orte) == 1:
        return await _ziel_gesetzt(update, ctx, orte[0])
    _setze(ctx, optionen={o.code: o for o in orte})
    knoepfe = [(_ort_label(o), f"ziel:{o.code}") for o in orte]
    await _antworte(update, "Welches meinst du?", reply_markup=_tastatur(knoepfe))
    return ZIEL


async def on_abflug(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    orte = await _suche(update, _text(update))
    if orte is None:
        return ABFLUG
    if len(orte) == 1:
        return await _abflug_gesetzt(update, ctx, orte[0])
    _setze(ctx, optionen={o.code: o for o in orte})
    knoepfe = [(_ort_label(o), f"abflug:{o.code}") for o in orte]
    await _antworte(update, "Welchen Abflugort meinst du?", reply_markup=_tastatur(knoepfe))
    return ABFLUG


async def on_ort_klick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = klick(update)
    await query.answer()
    rolle, _, code = (query.data or "").partition(":")
    optionen: dict[str, Ort] = dict(_entwurf(ctx).get("optionen", {}))
    if rolle == "abflug":
        optionen = {_STANDARD_ABFLUG.code: _STANDARD_ABFLUG, **optionen}

    ort = optionen.get(code)
    if ort is None:
        await _antworte(update, "Diese Auswahl ist nicht mehr gültig. Bitte schreib den Ort nochmal.")
        return ZIEL if rolle == "ziel" else ABFLUG
    await query.edit_message_text(f"✓ {ort.anzeige()}")
    if rolle == "ziel":
        return await _ziel_gesetzt(update, ctx, ort)
    return await _abflug_gesetzt(update, ctx, ort)


async def _ziel_gesetzt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, ort: Ort) -> int:
    _setze(ctx, ziel=ort, optionen={})
    standard = _STANDARD_ABFLUG
    await _antworte(
        update,
        f"Ziel: {ort.anzeige()}.\nAbflug von {standard.anzeige()}? "
        "Tipp auf den Button oder schreib einen anderen Abflugort.",
        reply_markup=_tastatur([(standard.anzeige(), f"abflug:{standard.code}")]),
    )
    return ABFLUG


async def _abflug_gesetzt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, ort: Ort) -> int:
    ziel = _entwurf(ctx).get("ziel")
    if ziel is None:
        return await _verloren(update, ctx)
    if ort.code == ziel.code:
        await _antworte(update, "Abflug und Ziel sind gleich. Von wo fliegst du ab?")
        return ABFLUG
    _setze(ctx, origin=ort, optionen={})
    await _antworte(update, f"Abflug: {ort.anzeige()}.\nWann willst du los?\n{_HILFE_ZEITRAUM}")
    return ZEITRAUM


# ── Zeitraum, Art, Dauer ──────────────────────────────────────────────────────


async def on_zeitraum(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        zeitraum = parse_zeitraum(_text(update), _heute())
    except ZeitraumFehler as e:
        await _antworte(update, f"{e}\n{_HILFE_ZEITRAUM}")
        return ZEITRAUM
    _setze(ctx, von=zeitraum.von, bis=zeitraum.bis)
    await _antworte(
        update,
        f"Abflug im Zeitraum {zeitraum_text(zeitraum.von, zeitraum.bis)}.\nHin und zurück oder nur hin?",
        reply_markup=_tastatur([("Hin & zurück", "art:rt"), ("Nur hin", "art:ow")], pro_zeile=2),
    )
    return ART


async def on_art(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = klick(update)
    await query.answer()
    hin_und_zurueck = query.data == "art:rt"
    await query.edit_message_text("✓ Hin & zurück" if hin_und_zurueck else "✓ Nur hin")
    if not hin_und_zurueck:
        _setze(ctx, hin_und_zurueck=False, naechte=(None, None))
        await _antworte(update, _PREIS_FRAGE)
        return PREIS
    _setze(ctx, hin_und_zurueck=True)
    await _antworte(
        update,
        "Wie viele Nächte willst du bleiben? Tipp auf einen Button oder schreib z. B. „3-7“.",
        reply_markup=_tastatur([(t, f"dauer:{d}") for t, d in _DAUER_OPTIONEN], pro_zeile=2),
    )
    return DAUER


def _parse_dauer(text: str) -> tuple[int | None, int | None] | None:
    norm = text.strip().lower()
    if norm == "egal":
        return None, None
    m = _DAUER_MUSTER.match(norm)
    if not m:
        return None
    lo = int(m[1])
    hi = int(m[2]) if m[2] else lo
    if lo > hi or hi > MAX_NAECHTE:
        return None
    return lo, hi


async def _dauer_gesetzt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, naechte: tuple[int | None, int | None]) -> int:
    _setze(ctx, naechte=naechte)
    await _antworte(update, _PREIS_FRAGE)
    return PREIS


async def on_dauer_klick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = klick(update)
    await query.answer()
    naechte = _parse_dauer((query.data or "").partition(":")[2])
    if naechte is None:
        return DAUER
    await query.edit_message_text(
        f"✓ {(query.data or '').partition(':')[2].replace('-', '–')} Nächte"
        if naechte[0] is not None
        else "✓ Dauer egal"
    )
    return await _dauer_gesetzt(update, ctx, naechte)


async def on_dauer_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    naechte = _parse_dauer(_text(update))
    if naechte is None:
        await _antworte(update, f"Bitte „egal“, eine Zahl oder einen Bereich wie „3-7“ (höchstens {MAX_NAECHTE}).")
        return DAUER
    return await _dauer_gesetzt(update, ctx, naechte)


async def on_bitte_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _antworte(update, "Bitte tipp auf einen der Buttons oben.")


# ── Preis + Speichern ─────────────────────────────────────────────────────────


def _parse_preis(text: str) -> int | None:
    m = _PREIS_MUSTER.match(text)
    if not m:
        return None
    preis = int(m[1])
    return preis if MIN_PREIS <= preis <= MAX_PREIS else None


def _baue_watch(update: Update, entwurf: dict[str, Any], preis: int) -> Watch | None:
    try:
        lo, hi = entwurf.get("naechte", (None, None))
        return Watch(
            id=secrets.token_hex(4),
            user_id=nutzer_id(update),
            chat_id=chat_id(update),
            origin=entwurf["origin"],
            ziel=entwurf["ziel"],
            von=entwurf["von"],
            bis=entwurf["bis"],
            hin_und_zurueck=entwurf.get("hin_und_zurueck", True),
            naechte_min=lo,
            naechte_max=hi,
            max_preis=preis,
            angelegt=datetime.now(timezone.utc),
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        return None


async def on_preis(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    preis = _parse_preis(_text(update))
    if preis is None:
        await _antworte(update, f"Bitte eine Zahl zwischen {MIN_PREIS} und {MAX_PREIS} (Euro pro Person).")
        return PREIS

    watch = _baue_watch(update, _entwurf(ctx), preis)
    if watch is None:
        return await _verloren(update, ctx)
    nutzerdaten(ctx)[_KEY] = {}

    try:
        gespeichert = await store.fuege_hinzu(watch)
    except (OSError, store.StoreFehler) as e:
        logger.error(f"Flug-Bot: Suche nicht speicherbar ({type(e).__name__})")
        await _antworte(update, "Speichern hat nicht geklappt. Bitte versuch es später nochmal.")
        return ConversationHandler.END
    if gespeichert is None:
        await _antworte(
            update, f"Du hast schon maximal viele Suchen ({store.MAX_WATCHES_PRO_USER}). Lösch erst eine mit /loeschen."
        )
        return ConversationHandler.END

    await _antworte(update, f"✅ Suche angelegt:\n{watch_text(gespeichert)}")
    await _sofortpruefung(update, gespeichert)
    return ConversationHandler.END


async def _sofortpruefung(update: Update, watch: Watch) -> None:
    token = get_settings().travelpayouts_token.get_secret_value().strip()
    if not token:
        await _antworte(
            update,
            "Die Preisabfrage ist noch nicht eingerichtet. Die Suche ist gespeichert "
            "und läuft los, sobald die Abfrage aktiv ist.",
        )
        return
    if not _SOFORTPRUEFUNG.erlaube(watch.user_id):
        await _antworte(update, "Den aktuellen Preis prüfe ich beim nächsten regulären Durchlauf.")
        return
    try:
        neu, bestes, alarm = await erste_pruefung(watch, token)
    except Exception as e:
        logger.warning(f"Flug-Bot: Sofortprüfung fehlgeschlagen ({type(e).__name__})")
        await _antworte(
            update, "Den aktuellen Preis konnte ich gerade nicht abrufen – ich versuche es später automatisch."
        )
        return
    try:
        if alarm and bestes is not None:
            await _antworte(update, alert_text(neu, bestes), link_preview_options=_KEIN_LINK_PREVIEW)
        else:
            await _antworte(update, stand_text(neu, bestes))
    except Exception as e:
        # Versand gescheitert: Preis gilt nicht als gemeldet, der Watcher alarmiert später.
        logger.error(f"Flug-Bot: Ergebnis der Sofortprüfung nicht zustellbar ({type(e).__name__})")
        neu = neu.model_copy(update={"gemeldeter_preis": watch.gemeldeter_preis})
    await _speichere_still(neu)


async def _speichere_still(watch: Watch) -> None:
    try:
        await store.ersetze((watch,))
    except Exception as e:
        logger.error(f"Flug-Bot: Prüfergebnis nicht speicherbar ({type(e).__name__})")


# ── Liste + Löschen ───────────────────────────────────────────────────────────


async def cmd_liste(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _antworte(update, liste_text(store.watches_von(nutzer_id(update))))


async def on_unbekannt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _antworte(
        update,
        "Dazu läuft gerade kein Dialog. Befehle: /neu (neue Suche), /liste, /loeschen, /hilfe.",
    )


async def on_veralteter_klick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await klick(update).answer("Diese Auswahl ist abgelaufen.")


# ── Handler ───────────────────────────────────────────────────────────────────


Handler = BaseHandler[Update, Any, Any]


def build_handlers() -> list[Handler]:
    # Nur neue Nachrichten: eine nachträglich bearbeitete Nachricht darf nicht
    # als nächste Dialog-Eingabe zählen.
    neu = filters.UpdateType.MESSAGE
    # Alles mit "/" vorne ist ein (Versuch eines) Befehls, nie Dialog-Eingabe –
    # auch "/löschen", das Telegram nicht als Befehl erkennt.
    text = neu & filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^/")
    # Telegram-Befehle kennen nur a–z, 0–9 und "_": "/löschen" kommt als "/l" an.
    loeschen_mit_umlaut = neu & filters.Regex(re.compile(r"^/l(?:ö|oe)sche?n?(?:@\w+)?(?:\s|$)", re.IGNORECASE))
    loeschen_knopf = r"^del:([0-9a-f]{8}|nein)$"
    with warnings.catch_warnings():
        # per_message=False ist gewollt: Text und Buttons mischen sich im Dialog.
        warnings.filterwarnings("ignore", category=PTBUserWarning, message=".*per_message.*")
        eintritt: list[Handler] = [
            CommandHandler("neu", cmd_neu, filters=neu),
            CommandHandler("loeschen", cmd_loeschen, filters=neu),
            MessageHandler(loeschen_mit_umlaut, cmd_loeschen),
        ]
        zustaende: dict[object, list[Handler]] = {
            ZIEL: [MessageHandler(text, on_ziel), CallbackQueryHandler(on_ort_klick, pattern=r"^ziel:[A-Z]{3}$")],
            ABFLUG: [
                MessageHandler(text, on_abflug),
                CallbackQueryHandler(on_ort_klick, pattern=r"^abflug:[A-Z]{3}$"),
            ],
            ZEITRAUM: [MessageHandler(text, on_zeitraum)],
            ART: [CallbackQueryHandler(on_art, pattern=r"^art:(rt|ow)$"), MessageHandler(text, on_bitte_button)],
            DAUER: [
                CallbackQueryHandler(on_dauer_klick, pattern=r"^dauer:(egal|\d{1,2}-\d{1,2})$"),
                MessageHandler(text, on_dauer_text),
            ],
            PREIS: [MessageHandler(text, on_preis)],
            LOESCHEN: [
                CallbackQueryHandler(on_loeschen_klick, pattern=loeschen_knopf),
                MessageHandler(text, on_loeschen_antwort),
            ],
        }
        rueckfall: list[Handler] = [
            CommandHandler("abbrechen", cmd_abbrechen, filters=neu),
            CommandHandler("neu", cmd_neu, filters=neu),
            CommandHandler("loeschen", cmd_loeschen, filters=neu),
            MessageHandler(loeschen_mit_umlaut, cmd_loeschen),
        ]
        dialog: ConversationHandler[Any] = ConversationHandler(
            entry_points=eintritt, states=zustaende, fallbacks=rueckfall, allow_reentry=True
        )
    # Reihenfolge zählt: innerhalb einer Gruppe greift nur der erste passende
    # Handler, die Catch-alls am Ende fangen also nur, was sonst niemand nimmt.
    return [
        dialog,
        CommandHandler(["start", "hilfe"], cmd_start, filters=neu),
        CommandHandler("liste", cmd_liste, filters=neu),
        # Lösch-Buttons auf älteren Nachrichten, außerhalb des Löschdialogs
        CallbackQueryHandler(on_loeschen_klick, pattern=loeschen_knopf),
        MessageHandler(neu, on_unbekannt),
        CallbackQueryHandler(on_veralteter_klick),
    ]
