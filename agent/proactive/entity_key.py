"""
agent/proactive/entity_key.py – Phase 245 (Issue #342)

Kanonische Schlüssel für Entitäten, damit dieselbe Sache nicht unter leicht
anderer Formulierung ein zweites Mal als offener Eintrag entsteht.

Hintergrund: die Entity-ID ist `sha256("<type>:<name.strip().lower()>")`. Schon
ein Plural, ein Füllwort oder ein abweichender Typ ergibt eine andere ID – in
der Produktiv-DB lag ein einziger Termin dadurch achtfach vor, zwei Varianten
davon erledigt. `_deduplicate_items()` blendet die Dubletten im Briefing aus,
erledigt wird aber nur die getroffene Variante; im nächsten Briefing rückt die
nächste nach und der Eintrag wirkt unlöschbar.

Die ID-Bildung selbst bleibt unverändert – ein neues Schema würde jeden
Bestandseintrag unerreichbar machen und eine Migration erzwingen. Stattdessen
wird vor dem Upsert geprüft, ob es den Eintrag unter einer anderen Schreibweise
schon gibt; nur wenn nicht, entsteht eine neue ID.

Der Schlüssel ist bewusst **konservativ**: ein verpasster Treffer kostet eine
Dublette, ein falscher Treffer verschmilzt zwei verschiedene Dinge zu einem und
ist nicht reversibel. Deshalb wird im Zweifel getrennt (siehe die
Review-Gegenbeispiele in `tests/test_ph245_entity_key.py`).

Bekannte Grenzen, bewusst so:
  - Wortreihenfolge zählt: "Umzug von Berlin nach Rom" ≠ "… von Rom nach Berlin".
  - Keine Umlaut-Transliteration: "Fuehrung" ≠ "Führung".
  - `_stem()` ist nicht konvergent ("Steuer"→`steu`, "Steuern"→`steuer`);
    weiteres Kürzen würde die Kollisionsgefahr erhöhen.

API:
  canonical_key(name, entity_type)           → str
  index_key(entity_type, name)               → str
  build_key_index(collection)                → dict[str, str]
  resolve_in_index(index, entity_type, name) → str | None
  resolve_entity_id(collection, type, name)  → str | None
"""

import logging
import re

logger = logging.getLogger(__name__)

# task und intent meinen dasselbe – beides ist etwas, das zu erledigen ist.
# Die Extraktoren sind sich uneins: collect_entities() liefert "task",
# extract_intentions() "intent", für denselben Satz. Alle übrigen Typen haben
# eigene Bedeutung und werden nicht zusammengeführt (ein Ort namens "Berlin"
# ist keine Person namens "Berlin").
_EQUIVALENT_TYPES: frozenset[str] = frozenset({"task", "intent"})

# Eigennamen vertragen kein Stemming: "Andreas" würde auf "Andrea" fallen,
# "Lucas" auf "Luca", "Bremen" auf "Bremer". Bei Personen und Orten wird
# deshalb nur normalisiert, nicht gekürzt.
_PROPER_NOUN_TYPES: frozenset[str] = frozenset({"person", "place"})

# Artikel und neutrale Partikel. Bewusst kurz: Richtungswörter wie "von",
# "nach", "an", "für" oder "vor" gehören NICHT hierher – sie kehren die
# Bedeutung um ("Flug nach Rom" vs. "Flug von Rom", "Geschenk für Mama" vs.
# "Geschenk von Mama") und würden zwei verschiedene Dinge verschmelzen.
_FILLER_WORDS: frozenset[str] = frozenset(
    {
        "am",
        "beim",
        "bei",
        "das",
        "dem",
        "den",
        "der",
        "des",
        "die",
        "ein",
        "eine",
        "einen",
        "einer",
        "im",
        "in",
        "mit",
        "um",
        "und",
    }
)

# Flexionsendungen, die Singular/Plural und Adjektivformen angleichen
# ("Führung"/"Führungen", "sowjetisches"/"sowjetischen"). Längste zuerst.
_INFLECTION_SUFFIXES: tuple[str, ...] = ("en", "es", "er", "em", "e", "n", "s")

# Nach dem Kürzen muss ein tragfähiger Rest bleiben, sonst wird nicht gekürzt
# ("bauen" → "bau" wäre zu kurz und würde auf "Bau" kollidieren).
_MIN_STEM_LENGTH = 4

_WORD_RE = re.compile(r"[0-9a-zA-ZäöüÄÖÜßàáâãéèêíìîóòôõúùûçñ]+")


def _stem(token: str) -> str:
    """Schneidet eine Flexionsendung ab, solange genug Wortstamm übrig bleibt."""
    for suffix in _INFLECTION_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM_LENGTH:
            return token[: -len(suffix)]
    return token


def canonical_key(name: str, entity_type: str = "") -> str:
    """Vergleichsschlüssel für einen Entitätsnamen.

    Nur für den Abgleich gedacht, nie als Anzeigename und nie als ID –
    der ursprüngliche Name bleibt unverändert in den Metadaten stehen.
    """
    tokens = _WORD_RE.findall(name.lower())
    if not tokens:
        return ""
    meaningful = [t for t in tokens if t not in _FILLER_WORDS]
    # Besteht der Name ausschließlich aus Füllwörtern, ist der ungefilterte
    # Satz das Beste was bleibt – ein leerer Schlüssel würde auf alles passen.
    relevant = meaningful or tokens
    if entity_type in _PROPER_NOUN_TYPES:
        return " ".join(relevant)
    return " ".join(_stem(t) for t in relevant)


def _group(entity_type: str) -> str:
    """Typen, die zusammen verglichen werden, teilen sich einen Gruppennamen."""
    return "todo" if entity_type in _EQUIVALENT_TYPES else entity_type


def index_key(entity_type: str, name: str) -> str:
    """Nachschlagewert für den Key-Index; leer, wenn der Name nichts hergibt."""
    key = canonical_key(name, entity_type)
    return f"{_group(entity_type)}:{key}" if key else ""


def _merge_rank(meta: dict, eid: str) -> tuple:
    """Sortierschlüssel: welcher Eintrag gewinnt, wenn mehrere gleich heißen.

    Erledigt schlägt offen – sonst öffnet die nächste Erwähnung das Item
    wieder, genau der Bug aus #342. Danach der meistgenannte, dann der
    älteste, dann die ID: ChromaDB garantiert keine Reihenfolge, und ohne
    festen Tie-Break springt das Merge-Ziel zwischen zwei Läufen.
    """
    return (
        meta.get("status") != "done",
        -int(meta.get("mention_count") or 0),
        str(meta.get("created_at") or ""),
        eid,
    )


def build_key_index(collection) -> dict[str, str]:
    """Bildet `<gruppe>:<kanonischer schlüssel>` auf die bestehende Entity-ID ab.

    Ein einziger Durchlauf über die Collection, damit nicht jede Entität einer
    Nachricht ihren eigenen Full-Scan auslöst.
    """
    try:
        existing = collection.get(include=["metadatas"])
    except Exception as e:
        # Ohne Index entsteht für jede Entität eine frische ID – die
        # Dubletten-Vermeidung wäre still abgeschaltet, das gehört ins Log.
        logger.warning(f"Key-Index nicht verfügbar, Dubletten-Abgleich entfällt: {e}")
        return {}

    ids = existing.get("ids") or []
    metadatas = existing.get("metadatas") or []

    kandidaten: list[tuple[str, str, dict]] = []
    for eid, meta in zip(ids, metadatas):
        if not isinstance(meta, dict):
            continue
        lookup = index_key(meta.get("entity_type") or "", meta.get("name") or "")
        if lookup:
            kandidaten.append((lookup, eid, meta))

    # Rückwärts sortiert eingetragen, damit der bestplatzierte Eintrag zuletzt
    # schreibt und damit gewinnt.
    kandidaten.sort(key=lambda k: _merge_rank(k[2], k[1]), reverse=True)
    return {lookup: eid for lookup, eid, _ in kandidaten}


def resolve_in_index(index: dict[str, str], entity_type: str, name: str) -> str | None:
    """ID einer bereits bekannten Entität mit gleichem kanonischen Schlüssel."""
    lookup = index_key(entity_type, name)
    return index.get(lookup) if lookup else None


def resolve_entity_id(collection, entity_type: str, name: str) -> str | None:
    """Wie resolve_in_index, baut den Index aber selbst – für Einzelabfragen."""
    return resolve_in_index(build_key_index(collection), entity_type, name)
