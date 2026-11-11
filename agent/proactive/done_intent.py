"""
agent/proactive/done_intent.py – Phase 246 (Issue #375)

Erkennt, dass eine Nachricht einen offenen Punkt erledigen will.

Vorher passierte bei "lösch X aus dem Briefing" zweierlei, beides falsch: X
wurde nicht erledigt (`mark_done()` war nur über `/done` erreichbar), und der
Löschsatz selbst wurde von `collect_entities()` und `extract_intentions()` als
neue Entität eingesammelt. Der Bot bestätigte trotzdem. Die Liste wurde durch
jeden Löschversuch länger statt kürzer – belegt in der Produktiv-DB, wo drei
Einträge ausschließlich aus Löschversuchen entstanden waren.

Bewusst regelbasiert statt per LLM: der Pfad startet nach jeder Nachricht schon
zwei Hintergrund-Calls, und die Trigger-Schicht aus Phase 223 (`_WORD_TRIGGER_RULES`
in `agent/supervisor.py`) hat sich für genau solche Entscheidungen bewährt.

Die Erkennung ist bewusst ENG gehalten. Ein verpasster Löschauftrag kostet
einen weiteren Versuch; ein falsch erkannter ruft `mark_done()` auf, und dafür
gibt es keinen Reopen-Pfad (Trade-off aus Phase 235, Issue #338).

API:
  is_done_request(text)   → bool   – will die Nachricht etwas erledigen?
  detect_done_target(text) → str | None – und welchen Punkt konkret?
"""

import logging
import re

logger = logging.getLogger(__name__)

# "ich muss X löschen" ist eine Absicht des Users, kein Auftrag an den Bot –
# und eine Frage ist nie einer. Beides schlägt jede Trigger-Regel.
_BLOCKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bich\s+(muss|müsste|muesste|sollte|will|möchte|moechte|wollte)\b"),
    re.compile(r"\b(wie|kann man|soll ich|darf ich|wo)\b.*\?"),
    re.compile(r"^\s*(wie|kann man|soll ich|darf ich|wo)\b"),
    # Der Intent-Extraktor-Prompt listet genau diese Formen als Commitment –
    # sie sind das Gegenteil eines Löschauftrags.
    re.compile(r"\b(erinnere mich|vergiss nicht|denk dran)\b"),
)

# Imperativ am Satzanfang: "lösch X", "entferne X", "streich X".
_IMPERATIV_RE = re.compile(
    r"^\s*(?:bitte\s+)?(lösch|loesch|lösche|loesche|entferne|entfern|streich|streiche)\b(?P<rest>.*)",
    re.IGNORECASE,
)

# "nimm X raus" / "nimm X aus dem Briefing"
_NIMM_RE = re.compile(r"^\s*(?:bitte\s+)?nimm\b(?P<rest>.*)", re.IGNORECASE)

# "X ist erledigt" / "X ist durch" / "X kann weg"
_ERLEDIGT_RE = re.compile(
    r"^(?P<ziel>.+?)\s+(?:ist\s+(?:erledigt|durch|fertig)|kann\s+weg)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# "(ich) hab X erledigt" – Vergangenheitsform, daher kein Konflikt mit dem
# "ich muss"-Blocker. Bewusst nur "erledigt": "gemacht" wäre zu breit
# ("ich hab Sport gemacht" ist keine Ansage an die Liste).
_HAB_ERLEDIGT_RE = re.compile(
    r"^\s*(?:ich\s+)?(?:hab|habe)\s+(?P<ziel>.+?)\s+erledigt\s*[.!]?\s*$",
    re.IGNORECASE,
)

# "Erinnerung X löschen" – die Form, in der der Bot den Auftrag selbst
# gespeichert hatte.
_NACHGESTELLT_RE = re.compile(
    r"^\s*(?:die\s+)?(?:erinnerung|eintrag|punkt)\s+(?:an\s+)?(?P<ziel>.+?)\s+"
    r"(?:löschen|loeschen|entfernen|streichen)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Rahmenwörter rund um den eigentlichen Namen.
_PRAEFIXE: tuple[str, ...] = (
    "mir",
    "bitte",
    "die erinnerung an",
    "die erinnerung",
    "den eintrag",
    "den punkt",
    "das",
    "den",
    "die",
    "der",
)
_SUFFIXE: tuple[str, ...] = (
    "aus dem briefing",
    "aus den offenen punkten",
    "von der liste",
    "aus der liste",
    "raus",
    "weg",
    "bitte",
)

# Ein Ziel, das nur aus einem Rahmenwort besteht, ist keins: "lösch das bitte"
# nennt keinen Punkt. mark_done() matcht per Teilstring und würde bei so etwas
# wahllos treffen.
_KEIN_ZIEL: frozenset[str] = frozenset(
    {"das", "die", "der", "den", "dem", "es", "alles", "alle", "eintrag", "punkt", "erinnerung"}
)
_MIN_ZIEL_LENGTH = 3


def _ohne_rahmenwoerter(text: str) -> str:
    """Schält Füll- und Rahmenphrasen ab, bis nur der Name übrig ist."""
    rest = text.strip().strip(",.!:;").strip()
    geaendert = True
    while geaendert:
        geaendert = False
        for praefix in _PRAEFIXE:
            if rest.lower() == praefix:
                return ""
            if rest.lower().startswith(praefix + " "):
                rest = rest[len(praefix) :].strip()
                geaendert = True
        for suffix in _SUFFIXE:
            if rest.lower() == suffix:
                return ""
            if rest.lower().endswith(" " + suffix):
                rest = rest[: -len(suffix)].strip()
                geaendert = True
        rest = rest.strip(",.!:;").strip()
    return rest


def _ist_blockiert(text: str) -> bool:
    kleingeschrieben = text.lower()
    return any(muster.search(kleingeschrieben) for muster in _BLOCKER_PATTERNS)


def _rohes_ziel(text: str) -> str | None:
    """Der Teil der Nachricht, der den zu erledigenden Punkt benennt."""
    for muster in (_NACHGESTELLT_RE, _ERLEDIGT_RE, _HAB_ERLEDIGT_RE):
        treffer = muster.match(text.strip())
        if treffer:
            return treffer.group("ziel")
    for muster in (_IMPERATIV_RE, _NIMM_RE):
        treffer = muster.match(text.strip())
        # Ein nacktes "lösch" nennt weder Ziel noch Auftrag.
        if treffer and treffer.group("rest").strip():
            return treffer.group("rest")
    return None


def is_done_request(text: str) -> bool:
    """True, wenn die Nachricht einen offenen Punkt erledigen will.

    Auch dann True, wenn sich kein Name extrahieren lässt – entscheidend für
    den Aufrufer, der daran festmacht, ob extrahiert wird. Aus einem Löschsatz
    darf nie ein neues Item entstehen.
    """
    if not text or not text.strip() or _ist_blockiert(text):
        return False
    return _rohes_ziel(text) is not None


def detect_done_target(text: str) -> str | None:
    """Name des zu erledigenden Punkts, oder None wenn keiner erkennbar ist."""
    if not is_done_request(text):
        return None
    roh = _rohes_ziel(text)
    if roh is None:
        return None
    ziel = _ohne_rahmenwoerter(roh)
    if len(ziel) < _MIN_ZIEL_LENGTH or ziel.lower() in _KEIN_ZIEL:
        return None
    return ziel
