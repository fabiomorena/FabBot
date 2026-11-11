"""
tests/test_ph246_done_intent.py – Phase 246 (Issue #375)

Ein Löschauftrag im Chat erledigte nichts und wurde selbst als neues Pending
Item gespeichert. Belegt in der Produktiv-DB (2026-11-11) – der source_context
zitiert jeweils den Löschauftrag:

    name='Trip'                      type=task    ctx="Erinnerung 'Trip' soll gelöscht werden; …"
    name='Trip erledigen'            type=intent  ctx='Erledige Trip'
    name='Trip erledigen'            type=task    ctx="User bittet den Bot, die Erinnerung …"
    name='Erinnerung Führungen Sowjetisches Ehrenmal löschen'  type=task  09.11. 20:01

Eine Kettenreaktion: der Löschversuch erzeugt ein Item, dessen Löschversuch das
nächste. Die Liste wurde durch jeden Versuch länger statt kürzer.

Die Erkennung ist bewusst ENG. Ein verpasster Löschauftrag kostet einen
weiteren Versuch; ein falsch erkannter ruft mark_done() auf, und dafür gibt es
keinen Reopen-Pfad (Trade-off aus Phase 235).
"""

import pytest

from agent.proactive.done_intent import detect_done_target, is_done_request


class TestErkennt:
    @pytest.mark.parametrize(
        "nachricht,ziel",
        [
            ("lösch Hallo aus dem Briefing", "Hallo"),
            ("lösche Backtesting", "Backtesting"),
            ("Lösche die Erinnerung an die Führungen am Ehrenmal", "Führungen am Ehrenmal"),
            ("entferne Musik produzieren", "Musik produzieren"),
            ("nimm Trip aus dem Briefing", "Trip"),
            ("nimm das Training raus", "Training"),
            ("streiche Backtesting", "Backtesting"),
            ("Backtesting ist erledigt", "Backtesting"),
            ("Musik produzieren ist durch", "Musik produzieren"),
            ("Trainingsumgebung bauen kann weg", "Trainingsumgebung bauen"),
            ("Erinnerung Führungen Sowjetisches Ehrenmal löschen", "Führungen Sowjetisches Ehrenmal"),
            ("ich hab Backtesting erledigt", "Backtesting"),
            ("habe die Trainingsumgebung erledigt", "Trainingsumgebung"),
        ],
    )
    def test_loeschauftrag_mit_ziel(self, nachricht, ziel):
        assert detect_done_target(nachricht) == ziel


class TestErkenntNicht:
    """False Positives sind teuer – im Zweifel lieber nichts tun."""

    @pytest.mark.parametrize(
        "nachricht",
        [
            "ich muss die alten Mails löschen",
            "ich sollte den Ordner mal aufräumen und löschen",
            "ich will die Datei löschen",
            "wie lösche ich eine Datei in Python?",
            "kann man einen Branch wieder löschen?",
            "soll ich das löschen?",
            "Musik produzieren",
            "was steht noch an",
            "erinnere mich an die Führungen am Ehrenmal",
            "vergiss nicht dass ich keine Milch mag",
            "lösch",
            "",
            # "gemacht" ist bewusst kein Trigger – zu breit ("ich hab Sport gemacht")
            "ich hab heute viel gemacht",
        ],
    )
    def test_kein_loeschauftrag(self, nachricht):
        assert detect_done_target(nachricht) is None
        assert is_done_request(nachricht) is False


class TestIsDoneRequest:
    def test_true_bei_loeschauftrag(self):
        assert is_done_request("lösch Backtesting aus dem Briefing") is True

    def test_true_auch_ohne_extrahierbares_ziel(self):
        """Die Extraktion kann scheitern – die Absicht ist trotzdem klar.

        Entscheidend, weil daran haengt, ob extrahiert wird: aus einem
        Loeschsatz darf nie ein neues Item entstehen.
        """
        assert is_done_request("lösch das bitte aus dem Briefing") is True
        assert detect_done_target("lösch das bitte aus dem Briefing") is None


# ---------------------------------------------------------------- Bot-Anbindung

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402


def _update() -> MagicMock:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _create_task_mock() -> MagicMock:
    """Ersatz fuer asyncio.create_task, der die Coroutine sauber schliesst.

    Ohne das Schliessen meldet der GC "coroutine was never awaited".
    """

    def _schliesse(coro):
        coro.close()
        return MagicMock()

    return MagicMock(side_effect=_schliesse)


class TestErledigeAusChat:
    async def test_erledigt_den_benannten_punkt(self):
        import bot.bot as bot_mod

        update = _update()
        with patch("agent.proactive.pending.mark_done", return_value=["Backtesting"]) as mark:
            await bot_mod._erledige_aus_chat("lösch Backtesting aus dem Briefing", update)

        mark.assert_called_once_with("Backtesting")
        assert "Backtesting" in update.message.reply_text.await_args[0][0]

    async def test_meldet_wenn_nichts_gematcht_hat(self):
        """Der Bot darf nicht bestaetigen, was gar nicht passiert ist."""
        import bot.bot as bot_mod

        update = _update()
        with patch("agent.proactive.pending.mark_done", return_value=[]):
            await bot_mod._erledige_aus_chat("lösch Zahnarzt aus dem Briefing", update)

        text = update.message.reply_text.await_args[0][0]
        assert "Zahnarzt" in text
        assert "✅" not in text

    async def test_ohne_benannten_punkt_wird_nichts_erledigt(self):
        import bot.bot as bot_mod

        update = _update()
        with patch("agent.proactive.pending.mark_done") as mark:
            await bot_mod._erledige_aus_chat("lösch das bitte aus dem Briefing", update)

        mark.assert_not_called()


class TestKeineExtraktionBeiLoeschauftrag:
    """Kern von #375: aus einem Loeschsatz darf kein neues Item entstehen."""

    async def test_loeschauftrag_startet_keine_extraktoren(self):
        import bot.bot as bot_mod

        update = _update()
        with (
            patch("bot.bot._erledige_aus_chat", AsyncMock()) as erledige,
            patch("asyncio.create_task", _create_task_mock()) as create_task,
        ):
            await bot_mod._nachbereitung("lösch Backtesting aus dem Briefing", "Ok.", update)

        erledige.assert_awaited_once()
        create_task.assert_not_called()

    async def test_normale_nachricht_extrahiert_weiter(self):
        import bot.bot as bot_mod

        update = _update()
        with (
            patch("bot.bot._erledige_aus_chat", AsyncMock()) as erledige,
            patch("asyncio.create_task", _create_task_mock()) as create_task,
        ):
            await bot_mod._nachbereitung("ich war heute im Studio", "Schön!", update)

        erledige.assert_not_awaited()
        assert create_task.call_count == 2
