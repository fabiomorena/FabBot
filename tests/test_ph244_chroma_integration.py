"""
tests/test_ph244_chroma_integration.py – Phase 244 (Issue #371)

Integrationstests gegen eine ECHTE temporäre ChromaDB statt gegen Mocks.

Warum es die braucht: 78 Patch-Stellen in 9 Testdateien mocken die
Collections. Ein Mock kodiert aber genau die Annahme, die geprüft werden soll –
der Phase-235-Bug (jeder Upsert setzte `status` auf "open" zurück) war ein
Irrtum über ChromaDBs Upsert-Semantik und ließ alle gemockten Tests grün. Die
sieben Stellen mit Query-Operatoren ($and/$or/$eq/$lt/$gte) in linker.py und
relationship_alert.py waren bis hierher komplett unverifiziert: gäbe einer
dieser Filter nichts mehr zurück, wäre das Feature still tot und die Suite
trotzdem grün.

Geprüft wird deshalb nicht chromadbs API im Abstrakten, sondern die echten
Produktionsfunktionen gegen eine echte DB.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

chromadb = pytest.importorskip("chromadb", reason="chromadb nicht installiert (nicht in requirements-ci.txt)")


@pytest.fixture
def chroma(tmp_path, monkeypatch):
    """Echte ChromaDB in tmp_path; alle Collection-Getter zeigen darauf.

    Die Getter sind in ihren Modulen modul-global gecacht, deshalb werden die
    Funktionen gepatcht und nicht die Globals.
    """
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    entities = client.get_or_create_collection("entities")
    links = client.get_or_create_collection("links")

    import agent.proactive.collector as collector
    import agent.proactive.intent_extractor as intent_extractor
    import agent.proactive.linker as linker
    import agent.proactive.pending as pending
    import agent.proactive.relationship_alert as relationship_alert

    for modul, name in (
        (collector, "_get_entities_collection"),
        (pending, "_get_entities_collection"),
        (intent_extractor, "_get_collection"),
        (linker, "_get_entities_collection"),
        (relationship_alert, "_get_entities_collection"),
    ):
        monkeypatch.setattr(modul, name, lambda: entities)
    monkeypatch.setattr(linker, "_get_links_collection", lambda: links)

    return entities, links


def _llm(payload) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(payload)))
    return llm


async def _sammle(entity: dict, user_message: str = "irgendeine Nachricht") -> None:
    """Ruft die echte collect_entities – nur der LLM ist gemockt, Chroma ist echt."""
    import agent.proactive.collector as collector

    with patch.object(collector, "_get_llm", return_value=_llm([entity])):
        await collector.collect_entities(user_message=user_message, bot_response="Ok.")


HALLO = {
    "type": "task",
    "name": "Nachricht 'Hallo' an Fabio schicken",
    "context": 'schicke die Nachricht "Hallo" an Fabio',
}


# ---------------------------------------------------------------------------
# Upsert-Semantik – die Fehlerklasse aus Phase 235
# ---------------------------------------------------------------------------


class TestUpsertSemantik:
    async def test_done_ueberlebt_erneute_erwaehnung(self, chroma):
        """Der Phase-235-Bug, gegen echtes Chroma: /done muss halten."""
        from agent.proactive.pending import get_pending_items, mark_done

        entities, _ = chroma
        await _sammle(HALLO)
        assert mark_done("Hallo") == [HALLO["name"]]

        await _sammle(HALLO, user_message="lösch 'Hallo' aus dem Briefing")

        meta = entities.get(include=["metadatas"])["metadatas"][0]
        assert meta["status"] == "done"
        assert get_pending_items() == []

    async def test_upsert_merged_metadaten_statt_sie_zu_ersetzen(self, chroma):
        """upsert MERGED – nicht genannte Keys überleben.

        Der Code-Review zu Phase 235 behauptete das Gegenteil ("upsert replaces
        metadata wholesale, so an existing due_date vanishes"). Gegen echtes
        chromadb 1.5.9 ist das falsch, und genau deshalb steht dieser Test hier:
        ein Mock hätte beide Annahmen gleich bereitwillig bestätigt.
        """
        entities, _ = chroma
        morgen = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        await _sammle({**HALLO, "due_date": morgen})
        assert entities.get(include=["metadatas"])["metadatas"][0]["due_date"] == morgen

        await _sammle(HALLO)  # zweite Erwähnung ohne due_date
        assert entities.get(include=["metadatas"])["metadatas"][0]["due_date"] == morgen

    def test_key_laesst_sich_nur_mit_explizitem_none_entfernen(self, chroma):
        """Folge aus dem Merge-Verhalten: Weglassen löscht nicht, None löscht."""
        entities, _ = chroma
        entities.upsert(ids=["a"], documents=["x"], metadatas=[{"status": "open", "due_date": "2026-10-24"}])

        entities.upsert(ids=["a"], documents=["x"], metadatas=[{"status": "open"}])
        assert "due_date" in entities.get(ids=["a"], include=["metadatas"])["metadatas"][0]

        entities.upsert(ids=["a"], documents=["x"], metadatas=[{"status": "open", "due_date": None}])
        assert "due_date" not in entities.get(ids=["a"], include=["metadatas"])["metadatas"][0]

    async def test_abgelaufenes_due_date_haelt_item_dauerhaft_versteckt(self, chroma):
        """Konsequenz des Merge-Verhaltens – gemeldet als #370.

        `_is_stale()` blendet Items aus, deren due_date mehr als einen Tag
        zurückliegt. Da ein due_date durch erneute Erwähnung nicht verschwindet,
        bleibt so ein Item unsichtbar, auch wenn gerade wieder darüber
        gesprochen wird. Der Test pinnt das IST-Verhalten, damit eine
        Verhaltensänderung bewusst passiert.
        """
        from agent.proactive.pending import get_pending_items

        entities, _ = chroma
        gestern_vorbei = (datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat()
        await _sammle({**HALLO, "due_date": gestern_vorbei})
        assert get_pending_items() == []

        await _sammle(HALLO, user_message="was ist eigentlich mit Hallo an Fabio?")
        meta = entities.get(include=["metadatas"])["metadatas"][0]
        assert meta["mention_count"] == 2, "die Erwähnung wurde gezählt"
        assert get_pending_items() == [], "bleibt trotzdem unsichtbar – #370"

    async def test_created_at_bleibt_mention_count_waechst(self, chroma):
        entities, _ = chroma
        await _sammle(HALLO)
        erst = entities.get(include=["metadatas"])["metadatas"][0]

        await _sammle(HALLO)
        zweit = entities.get(include=["metadatas"])["metadatas"][0]

        assert zweit["created_at"] == erst["created_at"]
        assert zweit["mention_count"] == erst["mention_count"] + 1

    async def test_intent_extractor_teilt_die_semantik(self, chroma):
        """intent_extractor nutzt dieselben Helfer – auch gegen echtes Chroma."""
        import agent.proactive.intent_extractor as ie
        from agent.proactive.pending import mark_done

        entities, _ = chroma
        absicht = [{"name": "Steuererklärung machen", "context": "ich muss die Steuer machen"}]
        with patch.object(ie, "_get_llm", return_value=_llm(absicht)):
            await ie.extract_intentions("ich muss die Steuer machen")
            assert mark_done("Steuer") == ["Steuererklärung machen"]
            await ie.extract_intentions("Steuer ist doch längst durch")

        assert entities.get(include=["metadatas"])["metadatas"][0]["status"] == "done"


# ---------------------------------------------------------------------------
# get()-Semantik – die Annahmen in _existing_metadata
# ---------------------------------------------------------------------------


class TestGetSemantik:
    def test_unbekannte_id_liefert_leere_listen(self, chroma):
        """_existing_metadata verlässt sich darauf, dass ids leer ist."""
        from agent.proactive.collector import _existing_metadata

        entities, _ = chroma
        result = entities.get(ids=["gibtsnicht"])
        assert result["ids"] == []
        assert not result.get("metadatas")
        assert _existing_metadata(entities, "gibtsnicht") is None

    def test_datensatz_ohne_metadaten_gilt_als_neu(self, chroma):
        """Chroma liefert metadatas=[None] – der isinstance-Guard muss greifen."""
        from agent.proactive.collector import _existing_metadata

        entities, _ = chroma
        entities.upsert(ids=["ohne"], documents=["kein Metadatensatz"])
        assert entities.get(ids=["ohne"], include=["metadatas"])["metadatas"] == [None]
        assert _existing_metadata(entities, "ohne") is None


# ---------------------------------------------------------------------------
# status-Filter und Batch-Update
# ---------------------------------------------------------------------------


class TestStatusFilterUndBatch:
    def _eintrag(self, entities, eid: str, status: str, name: str) -> None:
        entities.upsert(
            ids=[eid],
            documents=[name],
            metadatas=[
                {
                    "entity_type": "task",
                    "name": name,
                    "status": status,
                    "mention_count": 1,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "last_mentioned_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        )

    def test_get_pending_items_zeigt_nur_offene(self, chroma):
        from agent.proactive.pending import get_pending_items

        entities, _ = chroma
        self._eintrag(entities, "a", "open", "Flug buchen")
        self._eintrag(entities, "b", "done", "Hotel buchen")
        self._eintrag(entities, "c", "open", "Steffi anrufen")

        namen = {i["name"] for i in get_pending_items()}
        assert namen == {"Flug buchen", "Steffi anrufen"}

    def test_mark_all_done_batch_ueber_viele_ids(self, chroma):
        """Der /done-all-Pfad: ein einziges update über eine große Liste."""
        from agent.proactive.pending import get_pending_items, mark_all_done

        entities, _ = chroma
        for i in range(60):
            self._eintrag(entities, f"id{i}", "open", f"Aufgabe {i}")

        erledigt = mark_all_done()
        assert erledigt is not None
        assert len(erledigt) == 60
        assert get_pending_items() == []
        stati = {m["status"] for m in entities.get(include=["metadatas"])["metadatas"]}
        assert stati == {"done"}

    def test_mark_all_done_ohne_offene(self, chroma):
        from agent.proactive.pending import mark_all_done

        entities, _ = chroma
        self._eintrag(entities, "a", "done", "schon erledigt")
        assert mark_all_done() == []


# ---------------------------------------------------------------------------
# Operator-Filter – bis Phase 244 völlig unverifiziert
# ---------------------------------------------------------------------------


class TestOperatorFilter:
    def test_or_filter_findet_links_in_beide_richtungen(self, chroma):
        """linker.py: $or über entity_id_a / entity_id_b."""
        from agent.proactive.linker import get_related_entities, link_entities

        entities, _ = chroma
        for eid, name in (("ea", "Steffi"), ("eb", "Salvador"), ("ec", "Geburtstag")):
            entities.upsert(
                ids=[eid],
                documents=[name],
                metadatas=[{"entity_type": "person", "name": name, "status": "open", "mention_count": 1}],
            )
        link_entities(["ea", "eb", "ec"], {"ea": "Steffi", "eb": "Salvador", "ec": "Geburtstag"})

        namen = {r["name"] for r in get_related_entities("ea")}
        assert namen == {"Salvador", "Geburtstag"}, "Der $or-Filter findet nur eine Richtung"

        # eb steht im Link ea↔eb an zweiter Stelle – muss ea trotzdem finden
        assert "Steffi" in {r["name"] for r in get_related_entities("eb")}

    def test_and_filter_mit_eq_lt_gte(self, chroma):
        """relationship_alert.py: $and aus $eq (Typ), $lt (Zeit), $gte (Erwähnungen)."""
        from agent.proactive.relationship_alert import MIN_MENTION_COUNT, _query_unmentioned

        entities, _ = chroma
        jetzt = datetime.now(timezone.utc).timestamp()
        cutoff = jetzt - 14 * 86400
        alt = cutoff - 86400
        neu = jetzt - 3600

        faelle = [
            ("treffer", "person", alt, MIN_MENTION_COUNT, True),
            ("falscher_typ", "place", alt, MIN_MENTION_COUNT, False),
            ("zu_frisch", "person", neu, MIN_MENTION_COUNT, False),
            ("zu_selten", "person", alt, MIN_MENTION_COUNT - 1, False),
        ]
        for eid, typ, ts, count, _erwartet in faelle:
            entities.upsert(
                ids=[eid],
                documents=[eid],
                metadatas=[
                    {
                        "entity_type": typ,
                        "name": eid,
                        "status": "open",
                        "mention_count": count,
                        "last_mentioned_at_ts": ts,
                        "last_mentioned_at": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                    }
                ],
            )

        gefunden = {i["name"] for i in _query_unmentioned(entities, "person", cutoff, jetzt)}
        assert gefunden == {eid for eid, _t, _ts, _c, erwartet in faelle if erwartet}

    def test_and_filter_findet_andere_typen_mit_eigenem_cutoff(self, chroma):
        """Der zweite Aufrufpfad: place/event/task mit 30-Tage-Schwelle."""
        from agent.proactive.relationship_alert import _query_unmentioned

        entities, _ = chroma
        jetzt = datetime.now(timezone.utc).timestamp()
        cutoff = jetzt - 30 * 86400
        entities.upsert(
            ids=["ort"],
            documents=["Salvador"],
            metadatas=[
                {
                    "entity_type": "place",
                    "name": "Salvador",
                    "status": "open",
                    "mention_count": 3,
                    "last_mentioned_at_ts": cutoff - 86400,
                    "last_mentioned_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        )
        assert [i["name"] for i in _query_unmentioned(entities, "place", cutoff, jetzt)] == ["Salvador"]
        assert _query_unmentioned(entities, "person", cutoff, jetzt) == []
