"""
tests/test_ph235_done_bleibt_done.py – Phase 235 (Issue #338)

/done wirkte nicht dauerhaft: collector.py und intent_extractor.py schrieben
beim Upsert hart status="open" und created_at=now. Jede weitere Erwähnung –
auch der Löschauftrag selbst – öffnete ein erledigtes Item wieder.

Getestet:
- Extraktoren übernehmen status und created_at vom Bestand
- mention_count und last_mentioned_at werden weiter aktualisiert
- Neue Entitäten starten unverändert mit status="open", mention_count=1
- mark_all_done() markiert alle offenen Items in einem Batch
- /done all fragt per HITL nach und führt nur bei Bestätigung aus
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ALTES_DATUM = "2026-05-06T19:03:23+00:00"


def _llm_mit(payload: list[dict]) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps(payload))
    return llm


def _collection_mit_bestand(status: str, mention_count: int = 3) -> MagicMock:
    col = MagicMock()
    col.get.return_value = {
        "ids": ["eid"],
        "metadatas": [
            {
                "entity_type": "task",
                "name": "Nachricht 'Hallo' an Fabio schicken",
                "status": status,
                "created_at": ALTES_DATUM,
                "mention_count": mention_count,
            }
        ],
    }
    return col


def _collection_ohne_bestand() -> MagicMock:
    col = MagicMock()
    col.get.return_value = {"ids": [], "metadatas": []}
    return col


HALLO_TASK = [{"type": "task", "name": "Nachricht 'Hallo' an Fabio schicken", "context": "schicke Hallo an Fabio"}]
HALLO_INTENT = [{"name": "Nachricht 'Hallo' an Fabio schicken", "context": "schicke Hallo an Fabio"}]


# ---------------------------------------------------------------------------
# collector.collect_entities
# ---------------------------------------------------------------------------


class TestCollectorStatusErhalt:
    async def _upsert_metadata(self, col: MagicMock) -> dict:
        from agent.proactive.collector import collect_entities

        with (
            patch("agent.proactive.collector._get_llm", return_value=_llm_mit(HALLO_TASK)),
            patch("agent.proactive.collector._get_entities_collection", return_value=col),
        ):
            await collect_entities(user_message="lösch Hallo aus dem Briefing", bot_response="Ok.")
        return col.upsert.call_args[1]["metadatas"][0]

    async def test_done_bleibt_done(self):
        meta = await self._upsert_metadata(_collection_mit_bestand("done"))
        assert meta["status"] == "done"

    async def test_open_bleibt_open(self):
        meta = await self._upsert_metadata(_collection_mit_bestand("open"))
        assert meta["status"] == "open"

    async def test_created_at_wird_nicht_ueberschrieben(self):
        meta = await self._upsert_metadata(_collection_mit_bestand("done"))
        assert meta["created_at"] == ALTES_DATUM

    async def test_mention_count_und_last_mentioned_at_aktualisiert(self):
        meta = await self._upsert_metadata(_collection_mit_bestand("done", mention_count=3))
        assert meta["mention_count"] == 4
        assert meta["last_mentioned_at"] != ALTES_DATUM

    async def test_neue_entitaet_startet_open(self):
        meta = await self._upsert_metadata(_collection_ohne_bestand())
        assert meta["status"] == "open"
        assert meta["mention_count"] == 1
        assert meta["created_at"] == meta["last_mentioned_at"]


# ---------------------------------------------------------------------------
# intent_extractor.extract_intentions
# ---------------------------------------------------------------------------


class TestIntentExtractorStatusErhalt:
    async def _upsert_metadata(self, col: MagicMock) -> dict:
        from agent.proactive.intent_extractor import extract_intentions

        with (
            patch("agent.proactive.intent_extractor._get_llm", return_value=_llm_mit(HALLO_INTENT)),
            patch("agent.proactive.intent_extractor._get_collection", return_value=col),
        ):
            await extract_intentions(user_message="Hallo ist doch längst raus")
        return col.upsert.call_args[1]["metadatas"][0]

    async def test_done_bleibt_done(self):
        meta = await self._upsert_metadata(_collection_mit_bestand("done"))
        assert meta["status"] == "done"

    async def test_created_at_wird_nicht_ueberschrieben(self):
        meta = await self._upsert_metadata(_collection_mit_bestand("done"))
        assert meta["created_at"] == ALTES_DATUM

    async def test_mention_count_aktualisiert(self):
        meta = await self._upsert_metadata(_collection_mit_bestand("done", mention_count=3))
        assert meta["mention_count"] == 4

    async def test_neue_absicht_startet_open(self):
        meta = await self._upsert_metadata(_collection_ohne_bestand())
        assert meta["status"] == "open"
        assert meta["mention_count"] == 1


class TestExistingMetadata:
    def test_datensatz_ohne_metadaten_gilt_als_neu(self):
        from agent.proactive.collector import _existing_metadata

        col = MagicMock()
        col.get.return_value = {"ids": ["eid"], "metadatas": [None]}
        assert _existing_metadata(col, "eid") is None

    def test_chromadb_fehler_gilt_als_neu(self):
        from agent.proactive.collector import _existing_metadata

        col = MagicMock()
        col.get.side_effect = RuntimeError("Chroma down")
        assert _existing_metadata(col, "eid") is None


# ---------------------------------------------------------------------------
# pending.mark_all_done
# ---------------------------------------------------------------------------


class TestMarkAllDone:
    def test_markiert_alle_offenen_in_einem_batch(self):
        from agent.proactive.pending import mark_all_done

        col = MagicMock()
        col.get.return_value = {
            "ids": ["a", "b", "c"],
            "metadatas": [
                {"name": "Flug buchen", "status": "open"},
                {"name": "Hotel buchen", "status": "open"},
                {"name": "Steffi anrufen", "status": "open"},
            ],
        }
        with patch("agent.proactive.pending._get_entities_collection", return_value=col):
            result = mark_all_done()

        assert sorted(result) == ["Flug buchen", "Hotel buchen", "Steffi anrufen"]
        assert col.update.call_count == 1
        kwargs = col.update.call_args[1]
        assert kwargs["ids"] == ["a", "b", "c"]
        assert all(m["status"] == "done" for m in kwargs["metadatas"])

    def test_fragt_nur_offene_ab(self):
        from agent.proactive.pending import mark_all_done

        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        with patch("agent.proactive.pending._get_entities_collection", return_value=col):
            mark_all_done()
        assert col.get.call_args[1]["where"] == {"status": "open"}

    def test_nichts_offen_gibt_leere_liste_ohne_update(self):
        from agent.proactive.pending import mark_all_done

        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        with patch("agent.proactive.pending._get_entities_collection", return_value=col):
            assert mark_all_done() == []
        assert not col.update.called

    def test_chromadb_lesefehler_gibt_none(self):
        from agent.proactive.pending import mark_all_done

        col = MagicMock()
        col.get.side_effect = RuntimeError("Chroma down")
        with patch("agent.proactive.pending._get_entities_collection", return_value=col):
            assert mark_all_done() is None

    def test_chromadb_updatefehler_gibt_none(self):
        from agent.proactive.pending import mark_all_done

        col = MagicMock()
        col.get.return_value = {"ids": ["a"], "metadatas": [{"name": "Flug buchen", "status": "open"}]}
        col.update.side_effect = ValueError("Unequal lengths")
        with patch("agent.proactive.pending._get_entities_collection", return_value=col):
            assert mark_all_done() is None

    def test_ohne_collection_gibt_none(self):
        from agent.proactive.pending import mark_all_done

        with patch("agent.proactive.pending._get_entities_collection", return_value=None):
            assert mark_all_done() is None


# ---------------------------------------------------------------------------
# bot.cmd_done all – HITL
# ---------------------------------------------------------------------------


def _update_von_erlaubtem_user() -> MagicMock:
    from bot.auth import ALLOWED_IDS

    update = MagicMock()
    update.effective_user.id = next(iter(ALLOWED_IDS))
    update.effective_chat.id = 4711
    update.message.reply_text = AsyncMock()
    return update


def _ctx_mit_args(*args: str) -> MagicMock:
    ctx = MagicMock()
    ctx.args = list(args)
    return ctx


@pytest.mark.asyncio
async def test_done_all_fragt_nach_und_fuehrt_bei_bestaetigung_aus():
    import bot.bot as bot_mod

    update = _update_von_erlaubtem_user()
    confirm = AsyncMock(return_value=True)
    with (
        patch("bot.bot.request_confirmation", confirm),
        patch("agent.proactive.pending.mark_all_done", return_value=["A", "B", "C"]) as mark_all,
        patch("agent.proactive.pending.mark_done") as mark_one,
    ):
        await bot_mod.cmd_done(update, _ctx_mit_args("all"))

    confirm.assert_awaited_once()
    assert confirm.call_args[0][1] == 4711
    mark_all.assert_called_once()
    mark_one.assert_not_called()
    antwort = update.message.reply_text.call_args[0][0]
    assert "3" in antwort


@pytest.mark.asyncio
async def test_done_all_bei_ablehnung_passiert_nichts():
    import bot.bot as bot_mod

    update = _update_von_erlaubtem_user()
    with (
        patch("bot.bot.request_confirmation", AsyncMock(return_value=False)),
        patch("agent.proactive.pending.mark_all_done") as mark_all,
    ):
        await bot_mod.cmd_done(update, _ctx_mit_args("all"))

    mark_all.assert_not_called()
    # Der Inline-Button meldet bereits "Abgelehnt" – keine zweite Nachricht (wie bei /clip)
    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_done_all_singular_bei_einem_punkt():
    import bot.bot as bot_mod

    update = _update_von_erlaubtem_user()
    with (
        patch("bot.bot.request_confirmation", AsyncMock(return_value=True)),
        patch("agent.proactive.pending.mark_all_done", return_value=["Hotel buchen"]),
    ):
        await bot_mod.cmd_done(update, _ctx_mit_args("all"))
    assert "1 offener Punkt erledigt" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_done_all_meldet_fehler_statt_haken():
    import bot.bot as bot_mod

    update = _update_von_erlaubtem_user()
    with (
        patch("bot.bot.request_confirmation", AsyncMock(return_value=True)),
        patch("agent.proactive.pending.mark_all_done", return_value=None),
    ):
        await bot_mod.cmd_done(update, _ctx_mit_args("all"))
    antwort = update.message.reply_text.call_args[0][0]
    assert "✅" not in antwort
    assert "fehl" in antwort.lower()


@pytest.mark.asyncio
async def test_done_all_ohne_offene_punkte():
    import bot.bot as bot_mod

    update = _update_von_erlaubtem_user()
    with (
        patch("bot.bot.request_confirmation", AsyncMock(return_value=True)),
        patch("agent.proactive.pending.mark_all_done", return_value=[]),
    ):
        await bot_mod.cmd_done(update, _ctx_mit_args("all"))
    assert "nichts offen" in update.message.reply_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_done_all_ist_case_insensitiv():
    import bot.bot as bot_mod

    update = _update_von_erlaubtem_user()
    with (
        patch("bot.bot.request_confirmation", AsyncMock(return_value=True)),
        patch("agent.proactive.pending.mark_all_done", return_value=["A"]) as mark_all,
    ):
        await bot_mod.cmd_done(update, _ctx_mit_args("ALL"))
    mark_all.assert_called_once()


@pytest.mark.asyncio
async def test_done_mit_namen_fragt_nicht_nach():
    import bot.bot as bot_mod

    update = _update_von_erlaubtem_user()
    confirm = AsyncMock(return_value=True)
    with (
        patch("bot.bot.request_confirmation", confirm),
        patch("agent.proactive.pending.mark_done", return_value=["Hotel buchen"]),
    ):
        await bot_mod.cmd_done(update, _ctx_mit_args("Hotel"))
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_done_ohne_argument_nennt_all_in_der_hilfe():
    import bot.bot as bot_mod

    update = _update_von_erlaubtem_user()
    await bot_mod.cmd_done(update, _ctx_mit_args())
    assert "all" in update.message.reply_text.call_args[0][0]
