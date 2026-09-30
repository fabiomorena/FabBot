"""
bot/mfa_watch.py – Phase 236 (Issue #343)

Stündlicher Watcher für die Ausbildungsplatzbörse der Ärztekammer Berlin
(Medizinische Fachangestellte). Neue Anzeigen gehen in einen privaten
Telegram-Kanal, Fehler in Fabios Privatchat.

Analog zu curator_scheduler – sleep zuerst, dann prüfen.
"""

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from telegram import LinkPreviewOptions
from telegram.helpers import escape_markdown

from agent.config import get_settings
from bot.telegram_markdown import mit_markdown_fallback

logger = logging.getLogger(__name__)

_STATE_FILE = Path.home() / ".fabbot" / "mfa_watch_state.json"
_MAX_SEITEN = 10
_FEHLER_SCHWELLE = 3
_TIMEOUT = 20.0
_USER_AGENT = "Mozilla/5.0 (compatible; FabBot/1.0)"
_SEITEN_PARAM = "tx_solr%5BpaginatedResults%5D%5BcurrentPage%5D"

# Tags ohne Endtag – dürfen die Verschachtelungstiefe im Parser nicht erhöhen,
# sonst verschluckt ein Anzeigenblock die nachfolgenden.
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)

_ITEM_KLASSE = "vd-result-list__item"
_TITEL_KLASSE = "vd-result-list__item-title"
# Die Trefferzahl steht als "Ergebnisse: <b>20</b>" im Markup – Tags zwischen
# Label und Zahl muessen toleriert werden.
_TREFFER_MUSTER = re.compile(r"Ergebnisse:\s*(?:<[^>]+>\s*)*(\d+)")
_WHITESPACE = re.compile(r"\s+")


class MfaAbrufFehler(RuntimeError):
    """Abruf oder Auswertung der Börse ist fehlgeschlagen."""


# ── State ─────────────────────────────────────────────────────────────────────


def _lade_state() -> dict:
    try:
        daten = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"seen": {}, "fehlschlaege": 0, "gemeldet": False}
    return {
        "seen": daten.get("seen", {}),
        "fehlschlaege": int(daten.get("fehlschlaege", 0)),
        "gemeldet": bool(daten.get("gemeldet", False)),
    }


def _speichere_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"MFA-Watch: State nicht schreibbar ({_STATE_FILE}): {e}")


# ── Parser ────────────────────────────────────────────────────────────────────


class _AnzeigenParser(HTMLParser):
    """Sammelt die Anzeigenblöcke der Börse.

    Innerhalb eines Blocks gliedert sich der Inhalt über Überschriften
    (`vd-result-list__item-title`) in Abschnitte und über `<strong>`-Label in
    benannte Felder. Beides wird getrennt gesammelt, weil Praxisname und
    Adresse vor dem ersten Label stehen.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anzeigen: list[dict] = []
        self._tiefe = 0
        self._aktiv = False
        self._uid = ""
        self._abschnitt = ""
        self._label = ""
        self._abschnitte: dict[str, list[str]] = {}
        self._label_werte: dict[str, list[str]] = {}
        self._kontakt_frei: list[str] = []
        self._email = ""
        self._in_titel = False
        self._in_strong = False

    def _reset_block(self, uid: str) -> None:
        self._aktiv = True
        self._tiefe = 0
        self._uid = uid
        self._abschnitt = ""
        self._label = ""
        self._abschnitte = {}
        self._label_werte = {}
        self._kontakt_frei = []
        self._email = ""
        self._in_titel = False
        self._in_strong = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            return
        attr = {k: (v or "") for k, v in attrs}
        klassen = attr.get("class", "").split()

        if not self._aktiv and _ITEM_KLASSE in klassen:
            self._reset_block(attr.get("data-uid", ""))

        if not self._aktiv:
            return

        self._tiefe += 1
        if tag == "p" and _TITEL_KLASSE in klassen:
            self._in_titel = True
        elif tag == "strong":
            self._in_strong = True
        elif tag == "a" and attr.get("href", "").startswith("mailto:") and not self._email:
            self._email = attr["href"][len("mailto:") :]

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS or not self._aktiv:
            return
        if tag == "p":
            self._in_titel = False
        elif tag == "strong":
            self._in_strong = False

        self._tiefe -= 1
        if self._tiefe <= 0:
            self.anzeigen.append(self._baue_anzeige())
            self._aktiv = False

    def handle_data(self, data: str) -> None:
        if not self._aktiv:
            return
        text = _WHITESPACE.sub(" ", data).strip()
        if not text:
            return

        if self._in_titel:
            self._abschnitt = text
            self._label = ""
            self._abschnitte.setdefault(text, [])
            return
        if self._in_strong:
            self._label = text.rstrip(":").strip()
            self._label_werte.setdefault(self._label, [])
            return

        if self._abschnitt:
            self._abschnitte.setdefault(self._abschnitt, []).append(text)
        if self._label:
            self._label_werte.setdefault(self._label, []).append(text)
        elif self._abschnitt == "Kontaktdaten":
            self._kontakt_frei.append(text)

    def _baue_anzeige(self) -> dict:
        def label(name: str) -> str:
            return " ".join(self._label_werte.get(name, [])).strip()

        def abschnitt(name: str) -> str:
            return " ".join(self._abschnitte.get(name, [])).strip()

        return {
            "uid": self._uid,
            "praxis": self._kontakt_frei[0] if self._kontakt_frei else "",
            # Strasse und PLZ/Ort stehen als getrennte <br/>-Segmente im Markup.
            "adresse": ", ".join(t for t in self._kontakt_frei[1:] if t),
            "ansprechperson": label("Ansprechperson"),
            "email": self._email,
            "telefon": label("Telefonnummer"),
            "webseite": label("Internetseite"),
            "versand": label("Versand der Bewerbung"),
            "bezirk": abschnitt("Bezirk"),
            "fachrichtungen": ", ".join(self._abschnitte.get("Fachrichtungen", [])),
            "beginn": label("Ausbildungsbeginn"),
            "veroeffentlicht": abschnitt("Veröffentlicht am"),
        }


def _parse_anzeigen(html: str) -> list[dict]:
    parser = _AnzeigenParser()
    parser.feed(html)
    return parser.anzeigen


def _gesamttreffer(html: str) -> int | None:
    treffer = _TREFFER_MUSTER.search(html)
    return int(treffer.group(1)) if treffer else None


def _anzeige_id(anzeige: dict) -> str:
    """Stabile ID – bevorzugt die data-uid des CMS, sonst Inhalts-Hash.

    Die Normalisierung des Fallbacks zieht Leerraum zusammen und vereinheitlicht
    die Schreibweise, damit eine minimal geänderte Formulierung keine Dublette
    erzeugt (Lektion aus Issue #342).
    """
    uid = str(anzeige.get("uid", "")).strip()
    if uid:
        return f"uid:{uid}"
    teile = (anzeige.get("praxis", ""), anzeige.get("adresse", ""), anzeige.get("veroeffentlicht", ""))
    roh = "|".join(_WHITESPACE.sub(" ", t).strip().lower() for t in teile)
    return "sha:" + hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]


# ── Formatierung ──────────────────────────────────────────────────────────────


def _formatiere(anzeige: dict, quelle_url: str) -> str:
    def esc(wert: str) -> str:
        return escape_markdown(wert, version=1)

    kopfzeile = "🩺 *Neuer Ausbildungsplatz MFA*"
    zeilen = [esc(anzeige.get("praxis", "")) or "Praxis ohne Namensangabe"]

    ort = " · ".join(
        teil
        for teil in (
            esc(anzeige.get("adresse", "")),
            f"Bezirk {esc(anzeige['bezirk'])}" if anzeige.get("bezirk") else "",
        )
        if teil
    )
    if ort:
        zeilen.append(ort)
    if anzeige.get("fachrichtungen"):
        zeilen.append(f"Fachrichtung: {esc(anzeige['fachrichtungen'])}")
    if anzeige.get("beginn"):
        zeilen.append(f"Beginn: {esc(anzeige['beginn'])}")
    # Nicht jede Praxis hinterlegt eine E-Mail. Ohne Kontaktweg ist die Anzeige
    # für die Leserin wertlos, deshalb Telefon als Rückfallebene plus der
    # Hinweis, wie beworben werden soll.
    if anzeige.get("email"):
        zeilen.append(f"Kontakt: {esc(anzeige['email'])}")
    else:
        if anzeige.get("telefon"):
            zeilen.append(f"Telefon: {esc(anzeige['telefon'])}")
        if anzeige.get("webseite"):
            zeilen.append(f"Web: {esc(anzeige['webseite'])}")
        if anzeige.get("versand"):
            zeilen.append(f"Bewerbung: {esc(anzeige['versand'])}")

    fuss = []
    if anzeige.get("veroeffentlicht"):
        fuss.append(f"Veröffentlicht {esc(anzeige['veroeffentlicht'])}")
    fuss.append(f"→ {quelle_url}")

    return "\n\n".join([kopfzeile, "\n".join(zeilen), "\n".join(fuss)])


# ── Abruf ─────────────────────────────────────────────────────────────────────


def _seiten_url(basis_url: str, seite: int) -> str:
    if seite <= 1:
        return basis_url
    trenner = "&" if "?" in basis_url else "?"
    return f"{basis_url}{trenner}{_SEITEN_PARAM}={seite}"


async def lade_anzeigen(basis_url: str) -> list[dict]:
    """Holt alle Seiten der Börse und gibt die Anzeigen in Seitenreihenfolge zurück."""
    gesammelt: list[dict] = []
    gesehene_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as client:
        for seite in range(1, _MAX_SEITEN + 1):
            url = _seiten_url(basis_url, seite)
            try:
                resp = await client.get(url)
            except httpx.HTTPError as e:
                raise MfaAbrufFehler(f"Abruf von Seite {seite} fehlgeschlagen: {e}") from e

            if resp.status_code != 200:
                raise MfaAbrufFehler(f"Abruf von Seite {seite} lieferte HTTP {resp.status_code}")

            anzeigen = _parse_anzeigen(resp.text)
            if not anzeigen:
                break

            neue = [a for a in anzeigen if _anzeige_id(a) not in gesehene_ids]
            if not neue:
                # Gleiche Seite erneut ausgeliefert – Paginierung greift nicht.
                break
            gesammelt.extend(neue)
            gesehene_ids.update(_anzeige_id(a) for a in neue)

            gesamt = _gesamttreffer(resp.text)
            if gesamt is not None and len(gesammelt) >= gesamt:
                break
        else:
            logger.warning(f"MFA-Watch: Seitenlimit {_MAX_SEITEN} erreicht – Layout geändert?")

    return gesammelt


# ── Prüflauf ──────────────────────────────────────────────────────────────────


async def pruefe_einmal(bot: Any, channel_id: str | int, fehler_chat_id: str | int) -> None:
    """Holt die Börse, sendet unbekannte Anzeigen in den Kanal und pflegt den State."""
    settings = get_settings()
    quelle_url = settings.mfa_watch_url
    anzeigen = await lade_anzeigen(quelle_url)

    state = _lade_state()
    bekannt = state["seen"]

    if not anzeigen:
        raise MfaAbrufFehler("Keine Anzeigen geparst – Layout geändert oder Abruf geblockt")

    neue = [a for a in anzeigen if _anzeige_id(a) not in bekannt]
    jetzt = datetime.now(timezone.utc).isoformat()

    def eintrag(anzeige: dict) -> dict:
        return {
            "first_seen": jetzt,
            "praxis": anzeige.get("praxis", ""),
            "veroeffentlicht": anzeige.get("veroeffentlicht", ""),
        }

    if not bekannt:
        _speichere_state({**state, "seen": {_anzeige_id(a): eintrag(a) for a in anzeigen}, "fehlschlaege": 0})
        logger.info(f"MFA-Watch: Erstlauf – {len(anzeigen)} Anzeigen still übernommen, nichts gesendet.")
        return

    if not neue:
        logger.debug("MFA-Watch: keine neuen Anzeigen.")
        return

    # Älteste zuerst, damit die Reihenfolge im Kanal der Veröffentlichung folgt.
    for anzeige in sorted(neue, key=lambda a: _sortierschluessel(a.get("veroeffentlicht", ""))):
        text = _formatiere(anzeige, quelle_url)
        # Ohne Abschalten haengt Telegram an jede Anzeige dieselbe generische
        # Vorschaukarte der Boersenseite.
        await mit_markdown_fallback(
            bot.send_message,
            text,
            chat_id=channel_id,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        # State pro Anzeige fortschreiben: ein Sendefehler darf die restlichen
        # Anzeigen nicht als gesehen markieren.
        bekannt = {**bekannt, _anzeige_id(anzeige): eintrag(anzeige)}
        _speichere_state({**state, "seen": bekannt, "fehlschlaege": 0})
        await asyncio.sleep(1)

    logger.info(f"MFA-Watch: {len(neue)} neue Anzeige(n) in den Kanal gesendet.")


def _sortierschluessel(datum: str) -> str:
    """dd.mm.yyyy → yyyy-mm-dd; unparsbare Werte wandern ans Ende."""
    teile = datum.split(".")
    if len(teile) == 3 and all(t.isdigit() for t in teile):
        return f"{teile[2]}-{teile[1]}-{teile[0]}"
    return "9999-99-99"


# ── Scheduler ─────────────────────────────────────────────────────────────────


async def run_mfa_watch_scheduler(bot: Any, channel_id: str | int, fehler_chat_id: str | int) -> None:
    """Background-Task: prüft die Börse im konfigurierten Intervall."""
    intervall = get_settings().mfa_watch_interval
    # Aus den Settings stammende Werte landen bewusst nicht im Log: CodeQL
    # wertet die gesamte Settings-Quelle als Geheimnis (py/clear-text-logging-
    # sensitive-data). Intervall, Kanal-ID und Quell-URL stehen in der .env.
    logger.info("MFA-Watch Scheduler gestartet")

    while True:
        await asyncio.sleep(intervall)
        try:
            await pruefe_einmal(bot, channel_id, fehler_chat_id)
            state = _lade_state()
            if state["fehlschlaege"] or state["gemeldet"]:
                _speichere_state({**state, "fehlschlaege": 0, "gemeldet": False})
        except Exception as e:
            logger.error(f"MFA-Watch Scheduler Fehler: {e}")
            await _melde_fehlschlag(bot, fehler_chat_id, e)


async def _melde_fehlschlag(bot: Any, fehler_chat_id: str | int, fehler: Exception) -> None:
    """Meldet erst ab der Schwelle und danach erst wieder nach einem Erfolg."""
    state = _lade_state()
    fehlschlaege = state["fehlschlaege"] + 1
    gemeldet = state["gemeldet"]

    if fehlschlaege >= _FEHLER_SCHWELLE and not gemeldet:
        try:
            await bot.send_message(
                chat_id=fehler_chat_id,
                text=(
                    f"⚠️ MFA-Watch: {fehlschlaege} Fehlversuche in Folge.\n"
                    f"Letzter Fehler: {fehler}\n"
                    "Vermutlich hat sich das Seitenlayout geändert oder der Abruf wird geblockt."
                ),
            )
            gemeldet = True
        except Exception as e:
            logger.error(f"MFA-Watch: Fehlermeldung nicht zustellbar: {e}")

    _speichere_state({**state, "fehlschlaege": fehlschlaege, "gemeldet": gemeldet})
