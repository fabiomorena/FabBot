"""
Tests Phase 159 – API-Health-Check im Heartbeat (Issue #102)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── get_alert_messages ───────────────────────────────────────────────────────


def test_no_alert_on_first_check():
    """Erster Check (kein vorheriger Zustand) → kein Alert."""
    from agent.proactive.api_health import get_alert_messages

    current = {"anthropic": True, "tavily": False}
    previous = {}
    assert get_alert_messages(current, previous) == []


def test_alert_on_api_down():
    """API war up, ist jetzt down → Alert."""
    from agent.proactive.api_health import get_alert_messages

    current = {"anthropic": False}
    previous = {"anthropic": True}
    alerts = get_alert_messages(current, previous)
    assert len(alerts) == 1
    assert "nicht erreichbar" in alerts[0]
    assert "Anthropic" in alerts[0]


def test_alert_on_api_recovery():
    """API war down, ist jetzt up → Alert."""
    from agent.proactive.api_health import get_alert_messages

    current = {"anthropic": True}
    previous = {"anthropic": False}
    alerts = get_alert_messages(current, previous)
    assert len(alerts) == 1
    assert "wieder erreichbar" in alerts[0]


def test_no_alert_when_stable_up():
    """API bleibt up → kein Alert."""
    from agent.proactive.api_health import get_alert_messages

    current = {"anthropic": True, "tavily": True}
    previous = {"anthropic": True, "tavily": True}
    assert get_alert_messages(current, previous) == []


def test_no_alert_when_stable_down():
    """API bleibt down → kein wiederholter Alert."""
    from agent.proactive.api_health import get_alert_messages

    current = {"anthropic": False}
    previous = {"anthropic": False}
    assert get_alert_messages(current, previous) == []


def test_multiple_alerts():
    """Mehrere APIs ändern Zustand → mehrere Alerts."""
    from agent.proactive.api_health import get_alert_messages

    current = {"anthropic": False, "tavily": True}
    previous = {"anthropic": True, "tavily": False}
    alerts = get_alert_messages(current, previous)
    assert len(alerts) == 2


# ─── active_apis ─────────────────────────────────────────────────────────────


def test_active_apis_always_includes_anthropic():
    """Anthropic ist immer aktiv."""
    from agent.proactive.api_health import _active_apis

    with patch.dict("os.environ", {}, clear=False):
        apis = _active_apis()
    assert "anthropic" in apis


def test_active_apis_includes_tavily_when_key_set():
    """Tavily nur wenn TAVILY_API_KEY gesetzt."""
    from agent.proactive.api_health import _active_apis

    with patch.dict("os.environ", {"TAVILY_API_KEY": "fake", "BRAVE_API_KEY": ""}):
        apis = _active_apis()
    assert "tavily" in apis
    assert "brave" not in apis


# ─── check_apis ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_apis_returns_bool_per_api():
    """check_apis() gibt {name: bool} für alle aktiven APIs zurück."""
    from agent.proactive.api_health import check_apis

    with (
        patch("agent.proactive.api_health._active_apis", return_value=["anthropic"]),
        patch("agent.proactive.api_health._ping", new_callable=AsyncMock, return_value=True),
    ):
        result = await check_apis()

    assert "anthropic" in result
    assert result["anthropic"] is True


@pytest.mark.asyncio
async def test_check_apis_handles_ping_exception():
    """check_apis() behandelt Exception in _ping als down."""
    from agent.proactive.api_health import check_apis

    with (
        patch("agent.proactive.api_health._active_apis", return_value=["anthropic"]),
        patch("agent.proactive.api_health._ping", new_callable=AsyncMock, side_effect=Exception("timeout")),
    ):
        result = await check_apis()

    assert result["anthropic"] is False


# ─── run_api_health_check ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_sends_alert_on_state_change(tmp_path):
    """run_api_health_check() sendet Nachricht wenn API down geht."""
    from agent.proactive import api_health

    previous = {"anthropic": True}
    current = {"anthropic": False}

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with (
        patch.object(api_health, "_STATE_FILE", tmp_path / "state.json"),
        patch.object(api_health, "_load_state", return_value=previous),
        patch.object(api_health, "check_apis", new_callable=AsyncMock, return_value=current),
        patch.object(api_health, "_save_state"),
    ):
        await api_health.run_api_health_check(bot, chat_id=123)

    bot.send_message.assert_called_once()
    call_kwargs = bot.send_message.call_args
    assert "nicht erreichbar" in str(call_kwargs)


@pytest.mark.asyncio
async def test_run_no_message_when_stable(tmp_path):
    """run_api_health_check() sendet nichts wenn APIs stabil bleiben."""
    from agent.proactive import api_health

    state = {"anthropic": True}
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with (
        patch.object(api_health, "_load_state", return_value=state),
        patch.object(api_health, "check_apis", new_callable=AsyncMock, return_value=state),
        patch.object(api_health, "_save_state"),
    ):
        await api_health.run_api_health_check(bot, chat_id=123)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_run_is_failsafe():
    """run_api_health_check() swallowed Exception – Bot läuft weiter."""
    from agent.proactive import api_health

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch.object(api_health, "check_apis", new_callable=AsyncMock, side_effect=Exception("crash")):
        # Darf keinen Exception werfen
        await api_health.run_api_health_check(bot, chat_id=123)


# ─── Heartbeat-Integration ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_calls_api_health_check():
    """_run_heartbeat() ruft run_api_health_check() immer auf."""
    from bot import heartbeat_scheduler

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with (
        patch("bot.heartbeat_scheduler.run_api_health_check", new_callable=AsyncMock) as mock_health,
        patch("bot.heartbeat_scheduler.is_on_cooldown", return_value=True),
        patch("bot.heartbeat_scheduler.is_muted", return_value=False),
    ):
        await heartbeat_scheduler._run_heartbeat(bot, chat_id=123)

    mock_health.assert_called_once_with(bot, 123)


@pytest.mark.asyncio
async def test_heartbeat_api_check_runs_despite_cooldown():
    """API-Check läuft auch wenn proaktiver Cooldown aktiv ist."""
    from bot import heartbeat_scheduler

    bot = MagicMock()
    bot.send_message = AsyncMock()

    api_check_called = False

    async def fake_api_check(b, cid):
        nonlocal api_check_called
        api_check_called = True

    with (
        patch("bot.heartbeat_scheduler.run_api_health_check", side_effect=fake_api_check),
        patch("bot.heartbeat_scheduler.is_on_cooldown", return_value=True),
        patch("bot.heartbeat_scheduler.is_muted", return_value=False),
    ):
        await heartbeat_scheduler._run_heartbeat(bot, chat_id=123)

    assert api_check_called
