"""
tests/test_ph339_hochwassermarke_isolation.py – Issue #339

Die Session-Summary-Tests schrieben ihre Hochwassermarke in die echte
~/.fabbot/session_summary_state.json. Beim zweiten lokalen pytest-Lauf griff
die Marke aus dem ersten Lauf und drei Tests scheiterten mit "keine neuen
Nachrichten" – CI merkte nichts (frische Umgebung).

Die autouse-Fixture in conftest.py biegt _HOCHWASSERMARKE_DATEI auf tmp_path um.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ECHTE_MARKEN_DATEI = Path.home() / ".fabbot" / "session_summary_state.json"
TEST_CHAT_ID = 4242424242


def test_marken_datei_liegt_in_tests_nicht_im_home():
    import bot.session_summary as ss

    assert Path.home() not in ss._HOCHWASSERMARKE_DATEI.parents


@pytest.mark.asyncio
async def test_summarize_session_schreibt_nicht_in_die_echte_marken_datei(tmp_path: Path):
    from bot.session_summary import summarize_session
    from langchain_core.messages import AIMessage, HumanMessage

    messages = [HumanMessage(content=f"Msg {i}") for i in range(10)] + [
        AIMessage(content=f"Ans {i}") for i in range(10)
    ]
    with (
        patch("bot.session_summary.SESSIONS_DIR", tmp_path),
        patch("bot.session_summary._get_messages_from_state", new_callable=AsyncMock, return_value=messages),
        patch("bot.session_summary._generate_summary", new_callable=AsyncMock, return_value="## Test"),
        patch("bot.session_summary.MIN_HUMAN_MESSAGES", 5),
    ):
        assert await summarize_session(TEST_CHAT_ID, target_date=date(2026, 4, 4)) is True

    if ECHTE_MARKEN_DATEI.exists():
        assert str(TEST_CHAT_ID) not in json.loads(ECHTE_MARKEN_DATEI.read_text())
