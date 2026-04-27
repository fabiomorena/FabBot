"""
agent/agents/system_agent.py – Issue #37

CPU/RAM/Disk-Metriken via psutil ohne Shell-Befehle.
Nutzbar direkt vom User ("wie hoch ist die CPU?") und vom Heartbeat für Alerts.
"""
import logging
from dataclasses import dataclass

import psutil
from langchain_core.messages import AIMessage

from agent.state import AgentState
from agent.audit import log_action

logger = logging.getLogger(__name__)

# Schwellwerte für proaktive Alerts
CPU_ALERT_THRESHOLD = 80.0    # %
RAM_ALERT_THRESHOLD = 85.0    # %
DISK_ALERT_THRESHOLD = 90.0   # %


@dataclass(frozen=True)
class SystemStats:
    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float


def collect_stats() -> SystemStats:
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return SystemStats(
        cpu_percent=cpu,
        ram_percent=ram.percent,
        ram_used_gb=round(ram.used / 1024**3, 1),
        ram_total_gb=round(ram.total / 1024**3, 1),
        disk_percent=disk.percent,
        disk_used_gb=round(disk.used / 1024**3, 1),
        disk_total_gb=round(disk.total / 1024**3, 1),
    )


def format_stats(stats: SystemStats) -> str:
    cpu_flag = " ⚠️" if stats.cpu_percent >= CPU_ALERT_THRESHOLD else ""
    ram_flag = " ⚠️" if stats.ram_percent >= RAM_ALERT_THRESHOLD else ""
    disk_flag = " ⚠️" if stats.disk_percent >= DISK_ALERT_THRESHOLD else ""
    return (
        f"🖥 System-Status\n"
        f"CPU: {stats.cpu_percent:.1f}%{cpu_flag}\n"
        f"RAM: {stats.ram_used_gb} / {stats.ram_total_gb} GB "
        f"({stats.ram_percent:.1f}%){ram_flag}\n"
        f"Disk: {stats.disk_used_gb} / {stats.disk_total_gb} GB "
        f"({stats.disk_percent:.1f}%){disk_flag}"
    )


def get_alert_message(stats: SystemStats) -> str | None:
    """Gibt eine Alert-Nachricht zurück wenn ein Schwellwert überschritten ist, sonst None."""
    alerts = []
    if stats.cpu_percent >= CPU_ALERT_THRESHOLD:
        alerts.append(f"CPU bei {stats.cpu_percent:.1f}%")
    if stats.ram_percent >= RAM_ALERT_THRESHOLD:
        alerts.append(f"RAM bei {stats.ram_percent:.1f}% ({stats.ram_used_gb}/{stats.ram_total_gb} GB)")
    if stats.disk_percent >= DISK_ALERT_THRESHOLD:
        alerts.append(f"Disk bei {stats.disk_percent:.1f}% ({stats.disk_used_gb}/{stats.disk_total_gb} GB)")
    if not alerts:
        return None
    return "⚠️ System-Alert: " + " | ".join(alerts)


async def system_agent(state: AgentState) -> AgentState:
    """Gibt CPU/RAM/Disk-Status zurück. Kein LLM-Call nötig."""
    try:
        stats = collect_stats()
        log_action(
            "system_agent", "stats", "collected",
            state.get("telegram_chat_id"), status="executed"
        )
        result = format_stats(stats)
    except Exception as e:
        logger.error(f"system_agent Fehler: {e}")
        result = "❌ System-Metriken konnten nicht abgerufen werden."

    return {
        "messages": [AIMessage(content=result)],
        "last_agent_result": result,
        "last_agent_name": "system_agent",
    }
