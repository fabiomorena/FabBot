"""
Tests Phase 158 – system_agent (#37) + restricted None-Check (#106)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage


# ─── #37 system_agent ────────────────────────────────────────────────────────

def test_collect_stats_returns_dataclass():
    """collect_stats() liefert SystemStats mit positiven Werten."""
    from agent.agents.system_agent import collect_stats, SystemStats
    stats = collect_stats()
    assert isinstance(stats, SystemStats)
    assert 0.0 <= stats.cpu_percent <= 100.0
    assert 0.0 <= stats.ram_percent <= 100.0
    assert 0.0 <= stats.disk_percent <= 100.0
    assert stats.ram_total_gb > 0
    assert stats.disk_total_gb > 0


def test_format_stats_no_alert():
    """format_stats() zeigt kein ⚠️ wenn alle Werte unter Schwellwert."""
    from agent.agents.system_agent import format_stats, SystemStats
    stats = SystemStats(
        cpu_percent=20.0, ram_percent=40.0, ram_used_gb=3.0, ram_total_gb=8.0,
        disk_percent=50.0, disk_used_gb=200.0, disk_total_gb=400.0,
    )
    result = format_stats(stats)
    assert "CPU: 20.0%" in result
    assert "RAM:" in result
    assert "Disk:" in result
    assert "⚠️" not in result


def test_format_stats_with_alerts():
    """format_stats() zeigt ⚠️ wenn Schwellwerte überschritten."""
    from agent.agents.system_agent import format_stats, SystemStats
    stats = SystemStats(
        cpu_percent=85.0, ram_percent=90.0, ram_used_gb=7.5, ram_total_gb=8.0,
        disk_percent=95.0, disk_used_gb=950.0, disk_total_gb=1000.0,
    )
    result = format_stats(stats)
    assert result.count("⚠️") == 3


def test_get_alert_message_none_when_ok():
    """get_alert_message() gibt None zurück wenn alles unter Schwellwert."""
    from agent.agents.system_agent import get_alert_message, SystemStats
    stats = SystemStats(
        cpu_percent=10.0, ram_percent=30.0, ram_used_gb=2.0, ram_total_gb=8.0,
        disk_percent=40.0, disk_used_gb=100.0, disk_total_gb=400.0,
    )
    assert get_alert_message(stats) is None


def test_get_alert_message_cpu_threshold():
    """get_alert_message() gibt Alert wenn CPU >= 80%."""
    from agent.agents.system_agent import get_alert_message, SystemStats, CPU_ALERT_THRESHOLD
    stats = SystemStats(
        cpu_percent=CPU_ALERT_THRESHOLD, ram_percent=30.0, ram_used_gb=2.0, ram_total_gb=8.0,
        disk_percent=40.0, disk_used_gb=100.0, disk_total_gb=400.0,
    )
    msg = get_alert_message(stats)
    assert msg is not None
    assert "CPU" in msg


def test_get_alert_message_all_thresholds():
    """get_alert_message() enthält alle drei Metriken wenn alle überschritten."""
    from agent.agents.system_agent import get_alert_message, SystemStats
    stats = SystemStats(
        cpu_percent=90.0, ram_percent=90.0, ram_used_gb=7.5, ram_total_gb=8.0,
        disk_percent=95.0, disk_used_gb=950.0, disk_total_gb=1000.0,
    )
    msg = get_alert_message(stats)
    assert msg is not None
    assert "CPU" in msg
    assert "RAM" in msg
    assert "Disk" in msg


@pytest.mark.asyncio
async def test_system_agent_returns_stats():
    """system_agent() gibt formatierte Stats als AIMessage zurück."""
    from agent.agents.system_agent import system_agent, SystemStats

    fake_stats = SystemStats(
        cpu_percent=25.0, ram_percent=50.0, ram_used_gb=4.0, ram_total_gb=8.0,
        disk_percent=60.0, disk_used_gb=300.0, disk_total_gb=500.0,
    )
    state = {
        "messages": [HumanMessage(content="wie ist die CPU-Auslastung?")],
        "telegram_chat_id": 123,
        "last_agent_result": None,
        "last_agent_name": None,
    }

    with patch("agent.agents.system_agent.collect_stats", return_value=fake_stats), \
         patch("agent.agents.system_agent.log_action"):
        result = await system_agent(state)

    assert result["last_agent_name"] == "system_agent"
    assert "CPU" in result["last_agent_result"]
    assert isinstance(result["messages"][-1], AIMessage)


@pytest.mark.asyncio
async def test_system_agent_handles_psutil_error():
    """system_agent() gibt Fehlermeldung zurück wenn psutil fehlschlägt."""
    from agent.agents.system_agent import system_agent

    state = {
        "messages": [HumanMessage(content="ram?")],
        "telegram_chat_id": 123,
        "last_agent_result": None,
        "last_agent_name": None,
    }

    with patch("agent.agents.system_agent.collect_stats", side_effect=Exception("psutil crash")):
        result = await system_agent(state)

    assert "❌" in result["last_agent_result"]
    assert result["last_agent_name"] == "system_agent"


def test_system_agent_registered_in_supervisor():
    """system_agent ist in _AGENTS des Supervisors eingetragen."""
    from agent.supervisor import _AGENTS
    assert "system_agent" in _AGENTS


# ─── #106 restricted None-Check ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restricted_blocks_none_effective_user():
    """restricted() bricht ohne AttributeError ab wenn effective_user None ist."""
    import os
    os.environ.setdefault("TELEGRAM_ALLOWED_USER_IDS", "12345")

    from bot.auth import restricted

    called = False

    @restricted
    async def handler(update, ctx):
        nonlocal called
        called = True

    update = MagicMock()
    update.effective_user = None
    ctx = MagicMock()

    await handler(update, ctx)
    assert not called


@pytest.mark.asyncio
async def test_restricted_blocks_unknown_user():
    """restricted() blockiert User der nicht in der Whitelist ist."""
    import os
    os.environ.setdefault("TELEGRAM_ALLOWED_USER_IDS", "12345")

    from bot.auth import restricted

    called = False

    @restricted
    async def handler(update, ctx):
        nonlocal called
        called = True

    update = MagicMock()
    update.effective_user.id = 99999
    update.message.reply_text = AsyncMock()
    ctx = MagicMock()

    await handler(update, ctx)
    assert not called
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_restricted_allows_whitelisted_user():
    """restricted() lässt erlaubten User durch."""
    import os
    os.environ.setdefault("TELEGRAM_ALLOWED_USER_IDS", "12345")

    from bot.auth import ALLOWED_IDS, restricted

    allowed_id = next(iter(ALLOWED_IDS))
    called = False

    @restricted
    async def handler(update, ctx):
        nonlocal called
        called = True

    update = MagicMock()
    update.effective_user.id = allowed_id
    ctx = MagicMock()

    await handler(update, ctx)
    assert called
