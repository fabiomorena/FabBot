"""
tests/test_ph245_entity_key.py – Phase 245 (Issue #342)

Testet die kanonische Schlüsselbildung, die verhindert, dass dasselbe Item
unter leicht anderer Formulierung ein zweites Mal als offener Eintrag entsteht.

Die Fälle stammen aus der Produktiv-DB (Stand 2026-11-11), wo ein einziger
Termin achtfach vorlag:

    Führungen am Sowjetischen Ehrenmal   event  open
    Führung am Sowjetischen Ehrenmal     event  open
    Führungen am sowjetischen Ehrenmal   task   open
    Führungen Sowjetisches Ehrenmal      task   open
    Kritische Führungen am Sowj. …       event  done   ← bereits erledigt
    Sowjetisches Ehrenmal                place  done   ←

Zwei Varianten waren per /done erledigt, der Eintrag blieb trotzdem sichtbar,
weil die übrigen offen standen und im nächsten Briefing nachrückten.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.proactive.entity_key import canonical_key, resolve_entity_id

# ---------------------------------------------------------------- canonical_key


class TestCanonicalKey:
    def test_lowercase(self):
        assert canonical_key("Backtesting") == canonical_key("backtesting")

    def test_strips_whitespace_and_collapses_spaces(self):
        assert canonical_key("  Musik   produzieren ") == canonical_key("Musik produzieren")

    def test_ignores_punctuation(self):
        assert canonical_key("Mama anrufen!") == canonical_key("Mama anrufen")
        assert canonical_key("Trip – Paris") == canonical_key("Trip Paris")

    def test_ignores_filler_words(self):
        assert canonical_key("Führungen am Ehrenmal") == canonical_key("Führungen Ehrenmal")

    def test_ignores_singular_plural(self):
        assert canonical_key("Führung Ehrenmal") == canonical_key("Führungen Ehrenmal")

    def test_ignores_adjective_ending(self):
        assert canonical_key("Sowjetisches Ehrenmal") == canonical_key("Sowjetischen Ehrenmal")

    def test_real_world_variants_collapse(self):
        """Die vier offenen Ehrenmal-Varianten aus der Produktiv-DB."""
        keys = {
            canonical_key("Führungen am Sowjetischen Ehrenmal"),
            canonical_key("Führung am Sowjetischen Ehrenmal"),
            canonical_key("Führungen am sowjetischen Ehrenmal"),
            canonical_key("Führungen Sowjetisches Ehrenmal"),
        }
        assert len(keys) == 1

    def test_distinct_names_stay_distinct(self):
        assert canonical_key("Backtesting") != canonical_key("Trainingsumgebung bauen")
        assert canonical_key("Flug nach Paris") != canonical_key("Flug nach Rom")

    def test_added_qualifier_stays_distinct(self):
        """Eine echte Erweiterung ist kein Duplikat – konservativ bleiben."""
        assert canonical_key("Backtesting") != canonical_key("Backtesting der Handelsstrategie")

    def test_empty_string(self):
        assert canonical_key("") == ""

    def test_only_filler_words_keeps_something(self):
        """Ein Name aus lauter Füllwörtern darf nicht zum leeren Schlüssel kollabieren."""
        assert canonical_key("am und der") != ""


# ---------------------------------------------------------------- resolve_entity_id


@pytest.fixture
def collection(tmp_path):
    chromadb = pytest.importorskip("chromadb", reason="chromadb nicht installiert (nicht in requirements-ci.txt)")
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return client.get_or_create_collection("entities")


def _add(collection, eid: str, entity_type: str, name: str, status: str = "open") -> None:
    now = datetime.now(timezone.utc).isoformat()
    collection.upsert(
        ids=[eid],
        documents=[name],
        metadatas=[
            {
                "entity_type": entity_type,
                "name": name,
                "status": status,
                "created_at": now,
                "last_mentioned_at": now,
                "source_context": name,
                "mention_count": 1,
            }
        ],
    )


class TestResolveEntityId:
    def test_returns_none_when_collection_empty(self, collection):
        assert resolve_entity_id(collection, "task", "Backtesting") is None

    def test_returns_none_for_unrelated_name(self, collection):
        _add(collection, "id-1", "task", "Backtesting")
        assert resolve_entity_id(collection, "task", "Mama anrufen") is None

    def test_finds_variant_of_existing_entry(self, collection):
        _add(collection, "id-1", "event", "Führungen am Sowjetischen Ehrenmal")
        assert resolve_entity_id(collection, "event", "Führung am Sowjetischen Ehrenmal") == "id-1"

    def test_finds_done_entry_so_it_is_not_reopened(self, collection):
        """Kern von #342: ein erledigtes Item darf nicht als neues offenes zurückkehren."""
        _add(collection, "id-1", "task", "Trainingsumgebung bauen", status="done")
        assert resolve_entity_id(collection, "task", "Trainingsumgebung bauen!") == "id-1"

    def test_task_and_intent_are_the_same_thing(self, collection):
        """'Trainingsumgebung bauen' lag in der Produktiv-DB als task UND intent vor."""
        _add(collection, "id-1", "task", "Trainingsumgebung bauen")
        assert resolve_entity_id(collection, "intent", "Trainingsumgebung bauen") == "id-1"

    def test_person_and_place_stay_separate(self, collection):
        """Typen mit eigener Bedeutung werden nicht zusammengeführt."""
        _add(collection, "id-1", "place", "Berlin")
        assert resolve_entity_id(collection, "person", "Berlin") is None

    def test_event_and_task_stay_separate(self, collection):
        _add(collection, "id-1", "event", "Geburtstag Steffi")
        assert resolve_entity_id(collection, "task", "Geburtstag Steffi") is None

    def test_done_wins_when_several_variants_match(self, collection):
        """Altlast: dieselbe Sache liegt doppelt vor. Erledigt schlaegt offen,
        sonst oeffnet die naechste Erwaehnung das Item wieder."""
        _add(collection, "id-open", "task", "Backtesting!", status="open")
        _add(collection, "id-done", "task", "Backtesting", status="done")
        assert resolve_entity_id(collection, "task", "Backtesting") == "id-done"

    def test_survives_collection_error(self):
        class Broken:
            def get(self, *a, **kw):
                raise RuntimeError("ChromaDB weg")

        assert resolve_entity_id(Broken(), "task", "Backtesting") is None


# ---------------------------------------------------------------- Integration


@pytest.fixture
def chroma(tmp_path, monkeypatch):
    """Echte ChromaDB; beide Extraktoren und pending schreiben dorthin."""
    chromadb = pytest.importorskip("chromadb", reason="chromadb nicht installiert (nicht in requirements-ci.txt)")
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma-int"))
    entities = client.get_or_create_collection("entities")

    import agent.proactive.collector as collector
    import agent.proactive.intent_extractor as intent_extractor
    import agent.proactive.pending as pending

    for modul, name in (
        (collector, "_get_entities_collection"),
        (pending, "_get_entities_collection"),
        (intent_extractor, "_get_collection"),
    ):
        monkeypatch.setattr(modul, name, lambda: entities)
    return entities


def _llm(payload) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(payload)))
    return llm


async def _sammle(*entities_payload) -> None:
    import agent.proactive.collector as collector

    with patch.object(collector, "_get_llm", return_value=_llm(list(entities_payload))):
        await collector.collect_entities(user_message="egal", bot_response="Ok.")


async def _absicht(name: str) -> None:
    import agent.proactive.intent_extractor as ie

    payload = [{"name": name, "context": name}]
    with patch.object(ie, "_get_llm", return_value=_llm(payload)):
        await ie.extract_intentions(name)


def _namen(collection) -> list[str]:
    metas = collection.get(include=["metadatas"])["metadatas"] or []
    return sorted(m["name"] for m in metas)


def _status(collection, name: str) -> str:
    metas = collection.get(include=["metadatas"])["metadatas"] or []
    return next(m["status"] for m in metas if m["name"] == name)


class TestKeineDublettenMehr:
    async def test_variante_erzeugt_keinen_zweiten_eintrag(self, chroma):
        """Der Fall aus der Produktiv-DB: Singular/Plural + Gross/Kleinschreibung."""
        await _sammle(
            {
                "type": "event",
                "name": "Führungen am Sowjetischen Ehrenmal",
                "context": "Termin bis 15.11.",
            }
        )
        await _sammle(
            {
                "type": "event",
                "name": "Führung am sowjetischen Ehrenmal",
                "context": "nochmal erwähnt",
            }
        )

        assert _namen(chroma) == ["Führungen am Sowjetischen Ehrenmal"]
        metas = chroma.get(include=["metadatas"])["metadatas"]
        assert metas[0]["mention_count"] == 2

    async def test_erledigtes_item_bleibt_erledigt(self, chroma):
        """Kern von #342: /done wirkt, auch wenn der Name danach abweicht."""
        from agent.proactive.pending import mark_done

        await _sammle({"type": "task", "name": "Trainingsumgebung bauen", "context": "muss noch"})
        assert mark_done("Trainingsumgebung") == ["Trainingsumgebung bauen"]

        await _sammle({"type": "task", "name": "Trainingsumgebungen bauen!", "context": "nochmal"})

        assert _namen(chroma) == ["Trainingsumgebung bauen"]
        assert _status(chroma, "Trainingsumgebung bauen") == "done"

    async def test_intent_dockt_an_bestehenden_task_an(self, chroma):
        """'Trainingsumgebung bauen' lag als task UND intent vor."""
        await _sammle({"type": "task", "name": "Trainingsumgebung bauen", "context": "muss noch"})
        await _absicht("Trainingsumgebung bauen")

        assert _namen(chroma) == ["Trainingsumgebung bauen"]

    async def test_verschiedene_dinge_bleiben_getrennt(self, chroma):
        await _sammle({"type": "task", "name": "Backtesting", "context": "a"})
        await _sammle({"type": "task", "name": "Backtesting der Handelsstrategie", "context": "b"})

        assert _namen(chroma) == ["Backtesting", "Backtesting der Handelsstrategie"]

    async def test_person_und_ort_werden_nicht_vermischt(self, chroma):
        await _sammle({"type": "place", "name": "Berlin", "context": "a"})
        await _sammle({"type": "person", "name": "Berlin", "context": "b"})

        metas = chroma.get(include=["metadatas"])["metadatas"]
        assert sorted(m["entity_type"] for m in metas) == ["person", "place"]


# ------------------------------------------------- Review-Findings Phase 245
# Die folgenden Faelle stammen aus dem Code-Review des ersten Entwurfs. Jeder
# war dort empirisch reproduziert; sie stehen hier, damit sie nicht zurueckkehren.


class TestKeineFalschenZusammenfuehrungen:
    """Ein zu aggressiver Schluessel verschmilzt verschiedene Dinge – Datenverlust."""

    @pytest.mark.parametrize(
        "a,b",
        [
            ("Andreas", "Andrea"),
            ("Lucas", "Luca"),
            ("Jonas", "Jona"),
            ("Marcos", "Marco"),
            ("Thomas", "Thoma"),
        ],
    )
    def test_personennamen_bleiben_getrennt(self, a, b):
        assert canonical_key(a, "person") != canonical_key(b, "person")

    @pytest.mark.parametrize("a,b", [("Bremen", "Bremer"), ("Hausen", "Hauser")])
    def test_ortsnamen_bleiben_getrennt(self, a, b):
        assert canonical_key(a, "place") != canonical_key(b, "place")

    @pytest.mark.parametrize(
        "a,b",
        [
            ("Flug nach Rom", "Flug von Rom"),
            ("Geschenk für Mama", "Geschenk von Mama"),
            ("Nachricht an Marco", "Nachricht von Marco"),
            ("Zahlung an Finanzamt", "Zahlung vom Finanzamt"),
            ("Termin vor dem Umzug", "Termin nach dem Umzug"),
        ],
    )
    def test_richtung_kehrt_die_bedeutung_um(self, a, b):
        """'nach' und 'von' sind keine Fuellwoerter – sie unterscheiden die Sache."""
        assert canonical_key(a, "task") != canonical_key(b, "task")

    def test_person_andreas_trifft_nicht_andrea(self, collection):
        _add(collection, "id-andrea", "person", "Andrea")
        assert resolve_entity_id(collection, "person", "Andreas") is None


class TestBatchMitVarianten:
    """Zwei Varianten in EINER Nachricht – vorher verlor ChromaDB den ganzen Batch."""

    async def test_variante_im_selben_batch_verliert_keine_daten(self, chroma):
        await _sammle({"type": "task", "name": "Backtesting", "context": "a"})
        await _sammle(
            {"type": "task", "name": "Backtesting", "context": "a"},
            {"type": "task", "name": "Backtestings!", "context": "b"},
            {"type": "person", "name": "Steffi", "context": "c"},
            {"type": "event", "name": "Geburtstag Steffi", "context": "d"},
        )

        assert _namen(chroma) == ["Backtesting", "Geburtstag Steffi", "Steffi"]

    async def test_zwei_neue_varianten_im_selben_batch_werden_eins(self, chroma):
        """Der Bug aus #342 darf nicht aus dem Stand neu entstehen."""
        await _sammle(
            {"type": "task", "name": "Führungen am Ehrenmal", "context": "a"},
            {"type": "task", "name": "Führung am Ehrenmal", "context": "b"},
        )

        assert _namen(chroma) == ["Führungen am Ehrenmal"]


class TestMetadatenBleibenStabil:
    async def test_entity_type_kippt_nicht_auf_intent(self, chroma):
        """task und intent teilen die Identitaet – der gespeicherte Typ bleibt task."""
        await _sammle({"type": "task", "name": "Trainingsumgebung bauen", "context": "a"})
        await _absicht("Trainingsumgebung bauen")

        metas = chroma.get(include=["metadatas"])["metadatas"]
        assert [m["entity_type"] for m in metas] == ["task"]


class TestMarkDoneTrifftVarianten:
    """HIGH-4: Bestandsdubletten – /done muss alle Varianten erwischen."""

    def test_done_trifft_alle_varianten_des_bestands(self, collection, monkeypatch):
        import agent.proactive.pending as pending

        monkeypatch.setattr(pending, "_get_entities_collection", lambda: collection)
        _add(collection, "id-1", "event", "Führungen am Sowjetischen Ehrenmal")
        _add(collection, "id-2", "event", "Führung am Sowjetischen Ehrenmal")
        _add(collection, "id-3", "task", "Führungen am sowjetischen Ehrenmal")
        _add(collection, "id-4", "task", "Führungen Sowjetisches Ehrenmal")

        erledigt = pending.mark_done("Führungen am Sowjetischen Ehrenmal")

        assert len(erledigt) == 4
        assert pending.get_pending_items(5) == []

    def test_done_bleibt_bei_substring_treffern(self, collection, monkeypatch):
        """Das bisherige Teilstring-Matching darf nicht verloren gehen."""
        import agent.proactive.pending as pending

        monkeypatch.setattr(pending, "_get_entities_collection", lambda: collection)
        _add(collection, "id-1", "task", "Hotel in Rom buchen")

        assert pending.mark_done("hotel") == ["Hotel in Rom buchen"]

    def test_done_trifft_keine_fremden_items(self, collection, monkeypatch):
        import agent.proactive.pending as pending

        monkeypatch.setattr(pending, "_get_entities_collection", lambda: collection)
        _add(collection, "id-1", "task", "Backtesting")
        _add(collection, "id-2", "person", "Andrea")

        assert pending.mark_done("Andreas") == []
