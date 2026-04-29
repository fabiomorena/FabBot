"""
Tests fuer Phase 97 – Issue #10: _processed_message_ids deque(maxlen=200).

Prueft:
- _is_duplicate erkennt Duplikate korrekt
- Erste Verarbeitung: False (nicht Duplikat)
- Zweite Verarbeitung: True (Duplikat)
- FIFO-Semantik: älteste ID wird verdrängt wenn maxlen erreicht
- update.message is None → False (kein Crash)
- deque hat maxlen=200
"""

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_update(message_id: int) -> MagicMock:
    update = MagicMock()
    update.message.message_id = message_id
    return update


def _make_update_no_message() -> MagicMock:
    update = MagicMock()
    update.message = None
    return update


# ---------------------------------------------------------------------------
# Isolation: _processed_message_ids vor jedem Test leeren
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_deque():
    from bot.bot import _processed_message_ids

    _processed_message_ids.clear()
    yield
    _processed_message_ids.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIsDuplicate:
    async def test_erste_verarbeitung_nicht_duplikat(self):
        from bot.bot import _is_duplicate

        update = _make_update(1001)
        assert await _is_duplicate(update) is False

    async def test_zweite_verarbeitung_ist_duplikat(self):
        from bot.bot import _is_duplicate

        update = _make_update(1002)
        assert await _is_duplicate(update) is False
        assert await _is_duplicate(update) is True

    async def test_verschiedene_ids_kein_duplikat(self):
        from bot.bot import _is_duplicate

        assert await _is_duplicate(_make_update(2001)) is False
        assert await _is_duplicate(_make_update(2002)) is False
        assert await _is_duplicate(_make_update(2003)) is False

    async def test_keine_message_kein_crash(self):
        from bot.bot import _is_duplicate

        update = _make_update_no_message()
        assert await _is_duplicate(update) is False

    async def test_id_wird_in_deque_gespeichert(self):
        from bot.bot import _is_duplicate, _processed_message_ids

        await _is_duplicate(_make_update(3001))
        assert 3001 in _processed_message_ids

    async def test_duplikat_nicht_erneut_in_deque(self):
        from bot.bot import _is_duplicate, _processed_message_ids

        await _is_duplicate(_make_update(4001))
        await _is_duplicate(_make_update(4001))
        assert list(_processed_message_ids).count(4001) == 1


class TestDequeSemantics:
    async def test_maxlen_ist_200(self):
        from bot.bot import _processed_message_ids

        assert _processed_message_ids.maxlen == 200

    async def test_fifo_verdraengung(self):
        from bot.bot import _is_duplicate, _processed_message_ids

        # 200 IDs füllen
        for i in range(200):
            await _is_duplicate(_make_update(i))
        assert len(_processed_message_ids) == 200
        assert 0 in _processed_message_ids

        # 201. ID verdrängt ID 0
        await _is_duplicate(_make_update(200))
        assert len(_processed_message_ids) == 200
        assert 0 not in _processed_message_ids
        assert 200 in _processed_message_ids

    async def test_verdraengte_id_nicht_mehr_duplikat(self):
        from bot.bot import _is_duplicate

        # 200 IDs füllen – ID 0 wird verdrängt
        for i in range(200):
            await _is_duplicate(_make_update(i))
        await _is_duplicate(_make_update(200))  # verdrängt ID 0

        # ID 0 kann jetzt wieder verarbeitet werden (nicht mehr Duplikat)
        assert await _is_duplicate(_make_update(0)) is False

    async def test_kein_unbegrenztes_wachstum(self):
        from bot.bot import _is_duplicate, _processed_message_ids

        for i in range(500):
            await _is_duplicate(_make_update(i))
        assert len(_processed_message_ids) == 200
