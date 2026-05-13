"""
Zentrale Konfiguration für FabBot – Issue #120.

Alle Env-Vars an einem Ort, typsicher via pydantic-settings.
Zugriff ausschließlich über get_settings() – nie os.getenv() direkt.

get_settings() ist lru_cache'd (eine Instanz pro Prozess).
In Tests: get_settings.cache_clear() vor jedem Test (conftest.py-Fixture).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


def _default_knowledge_dir() -> str:
    return str(Path.home() / "Documents" / "Wissen")


class Settings(BaseSettings):
    # ── LLM ──────────────────────────────────────────────────────────────────
    # ANTHROPIC_API_KEY wird direkt von langchain-anthropic aus os.environ gelesen.
    anthropic_model_sonnet: str = "claude-sonnet-4-6"
    anthropic_model_haiku: str = "claude-haiku-4-5-20251001"

    # ── LangSmith Telemetry ───────────────────────────────────────────────────
    langchain_tracing_v2: str = ""
    langchain_api_key: str = ""
    langchain_project: str = "FabBot"
    langchain_endpoint: str = "https://api.smith.langchain.com"

    # ── Web-Search APIs ───────────────────────────────────────────────────────
    tavily_api_key: SecretStr | None = None
    brave_api_key: SecretStr | None = None

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: SecretStr = SecretStr("")
    openai_tts_voice: str = "nova"
    openai_tts_model: str = "tts-1"

    # ── Storage / Retrieval ───────────────────────────────────────────────────
    knowledge_dir: str = Field(default_factory=_default_knowledge_dir)

    # ── Chat ──────────────────────────────────────────────────────────────────
    chat_context_window: int = 20
    memory_nudge_interval: int = 10
    profile_snapshot_ttl: float = 300.0
    fabbot_extra_paths: str = ""

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_allowed_user_ids: str = ""
    telegram_chat_id: str = ""

    # ── Scheduler-Zeiten ──────────────────────────────────────────────────────
    briefing_time: str = "07:30"
    health_check_time: str = "06:00"
    session_summary_time: str = "23:30"
    session_summary_min_messages: int = 10
    party_report_day: int = 2
    party_report_time: str = "20:00"
    evening_checkin_time: str = "21:00"
    evening_checkin_enabled: bool = True
    proactive_quiet_start: int = 22
    proactive_quiet_end: int = 8

    # ── TTS ───────────────────────────────────────────────────────────────────
    tts_enabled: bool = True

    # ── WhatsApp ──────────────────────────────────────────────────────────────
    wa_service_port: int = 8767

    # ── Watchdog ──────────────────────────────────────────────────────────────
    watchdog_auto_restart: bool = True
    watchdog_restart_delay_min: int = 5
    watchdog_max_restarts: int = 3

    model_config = {"extra": "ignore"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
