"""
tests/test_ph234_session_hochwassermarke.py – Phase 234 (Issue #333)

Der Session-Summarizer las jeden Abend den kompletten Thread-State und fasste
daraus `messages[-_MESSAGE_WINDOW:]` zusammen. Schrieb Fabio tagelang nichts,
waren das jeden Abend dieselben alten Nachrichten:

- Alle vier September-Sessions mit Inhalt beschrieben Themen vom 15. August
- 65 von 153 Dateien waren reine Platzhalter ("Keine neuen Informationen")
- Der Anteil leerer Dateien stieg von 0 % im April auf 77 % im August

LangChain-Messages tragen keinen Zeitstempel, eine Filterung nach Datum ist
also unmöglich. Stattdessen merkt sich der Summarizer, wie viele Nachrichten er
zuletzt verarbeitet hat, und nimmt nur die neuen.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import bot.session_summary as ss


@pytest.fixture
def marke(tmp_path, monkeypatch):
    """Isolierte Zustandsdatei pro Test."""
    pfad = tmp_path / "session_summary_state.json"
    monkeypatch.setattr(ss, "_HOCHWASSERMARKE_DATEI", pfad)
    return pfad


def _dialog(n: int) -> list:
    """n Wortwechsel (je eine User- und eine Bot-Nachricht)."""
    nachrichten = []
    for i in range(n):
        nachrichten.append(HumanMessage(content=f"Frage {i}"))
        nachrichten.append(AIMessage(content=f"Antwort {i}"))
    return nachrichten


# ---------------------------------------------------------------------------
# Hochwassermarke: lesen und schreiben
# ---------------------------------------------------------------------------


def test_marke_ohne_datei_ist_null(marke):
    assert ss._lade_hochwassermarke(chat_id=42) == 0


def test_marke_wird_pro_chat_gespeichert(marke):
    ss._speichere_hochwassermarke(chat_id=42, anzahl=100)
    ss._speichere_hochwassermarke(chat_id=7, anzahl=5)

    assert ss._lade_hochwassermarke(chat_id=42) == 100
    assert ss._lade_hochwassermarke(chat_id=7) == 5


def test_marke_uebersteht_kaputte_datei(marke):
    marke.write_text("{kein json")
    assert ss._lade_hochwassermarke(chat_id=42) == 0


def test_marke_schrumpft_mit_dem_state(marke):
    """Wird der Checkpoint bereinigt, ist die alte Marke ungültig – sonst
    verschwindet die Zusammenfassung für immer."""
    ss._speichere_hochwassermarke(chat_id=42, anzahl=5000)

    neue = ss._neue_nachrichten(_dialog(10), marke=5000)

    assert len(neue) == 20, "bei geschrumpftem State muss alles verarbeitet werden"


# ---------------------------------------------------------------------------
# Nur neue Nachrichten
# ---------------------------------------------------------------------------


def test_nur_nachrichten_nach_der_marke():
    alle = _dialog(10)  # 20 Nachrichten

    neue = ss._neue_nachrichten(alle, marke=16)

    assert len(neue) == 4
    assert neue[0].content == "Frage 8"


def test_keine_neuen_nachrichten_liefert_leer():
    alle = _dialog(10)
    assert ss._neue_nachrichten(alle, marke=20) == []


# ---------------------------------------------------------------------------
# Das eigentliche Symptom: keine Datei ohne neue Gespräche
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ohne_neue_nachrichten_wird_keine_datei_geschrieben(marke, tmp_path):
    """Der Kern von #333: 65 Platzhalter-Dateien entstanden genau so."""
    ss._speichere_hochwassermarke(chat_id=42, anzahl=20)
    schreiber = AsyncMock()

    with (
        patch.object(ss, "_get_messages_from_state", new=AsyncMock(return_value=_dialog(10))),
        patch.object(ss, "_session_path", return_value=tmp_path / "2026-09-13.md"),
        patch.object(ss, "_generate_summary", new=AsyncMock()) as llm,
        patch.object(ss, "_write_summary_file", schreiber),
    ):
        ergebnis = await ss.summarize_session(chat_id=42)

    assert ergebnis is False
    llm.assert_not_awaited(), "kein LLM-Call ohne neue Nachrichten"
    schreiber.assert_not_called()


@pytest.mark.asyncio
async def test_alte_gespraeche_landen_nicht_in_der_zusammenfassung(marke, tmp_path):
    """Die September-Sessions beschrieben den 15. August, weil die alten
    Nachrichten mit im Dialog standen."""
    alt = [HumanMessage(content="Lösche alle offenen Punkte"), AIMessage(content="Erledigt.")]
    neu = [HumanMessage(content="Wie ist das Wetter?"), AIMessage(content="20 Grad.")]
    ss._speichere_hochwassermarke(chat_id=42, anzahl=len(alt))

    gesehener_dialog = {}

    async def fake_llm(dialog_text):
        gesehener_dialog["text"] = dialog_text
        return "## Zusammenfassung\nWetter besprochen."

    with (
        patch.object(ss, "_get_messages_from_state", new=AsyncMock(return_value=alt + neu)),
        patch.object(ss, "_session_path", return_value=tmp_path / "2026-09-13.md"),
        patch.object(ss, "MIN_HUMAN_MESSAGES", 1),
        patch.object(ss, "_generate_summary", fake_llm),
    ):
        await ss.summarize_session(chat_id=42)

    assert "Wetter" in gesehener_dialog["text"]
    assert "offenen Punkte" not in gesehener_dialog["text"], "alte Nachricht ist durchgerutscht"


@pytest.mark.asyncio
async def test_marke_wird_nach_erfolg_fortgeschrieben(marke, tmp_path):
    """SESSIONS_DIR mitpatchen – sonst greift der Path-Traversal-Schutz."""
    alle = _dialog(10)
    sessions = tmp_path / "Sessions"
    sessions.mkdir()

    with (
        patch.object(ss, "_get_messages_from_state", new=AsyncMock(return_value=alle)),
        patch.object(ss, "SESSIONS_DIR", sessions),
        patch.object(ss, "_session_path", return_value=sessions / "2026-09-13.md"),
        patch.object(ss, "MIN_HUMAN_MESSAGES", 1),
        patch.object(ss, "_generate_summary", new=AsyncMock(return_value="## Zusammenfassung\nX")),
    ):
        erfolg = await ss.summarize_session(chat_id=42)

    assert erfolg is True, "Datei wurde nicht geschrieben – Marke kann nicht wandern"
    assert ss._lade_hochwassermarke(chat_id=42) == 20


@pytest.mark.asyncio
async def test_marke_bleibt_bei_fehlgeschlagenem_llm(marke, tmp_path):
    """Sonst geht der Tag verloren, an dem der LLM-Call scheiterte."""
    ss._speichere_hochwassermarke(chat_id=42, anzahl=4)

    with (
        patch.object(ss, "_get_messages_from_state", new=AsyncMock(return_value=_dialog(10))),
        patch.object(ss, "_session_path", return_value=tmp_path / "2026-09-13.md"),
        patch.object(ss, "MIN_HUMAN_MESSAGES", 1),
        patch.object(ss, "_generate_summary", new=AsyncMock(return_value=None)),
    ):
        await ss.summarize_session(chat_id=42)

    assert ss._lade_hochwassermarke(chat_id=42) == 4, "Marke darf nur nach erfolgreichem Schreiben wandern"


def test_marke_datei_ist_json(marke):
    ss._speichere_hochwassermarke(chat_id=42, anzahl=7)
    assert json.loads(marke.read_text()) == {"42": 7}
