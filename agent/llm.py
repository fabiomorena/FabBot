"""
Zentraler LLM-Client fuer FabBot.

- get_llm()       → claude-sonnet-4-20250514 (Qualitaet, fuer alle Agents)
- get_fast_llm()  → claude-haiku-4-5-20251001 (Geschwindigkeit, fuer Supervisor-Routing)
"""
import os
from langchain_anthropic import ChatAnthropic

_llm: ChatAnthropic | None = None
_fast_llm: ChatAnthropic | None = None


def get_llm() -> ChatAnthropic:
    """Gibt den Sonnet-Client zurueck (lazy singleton)."""
    global _llm
    if _llm is None:
        _llm = ChatAnthropic(model="claude-sonnet-4-20250514")
    return _llm


def get_fast_llm() -> ChatAnthropic:
    """Gibt den Haiku-Client zurueck (lazy singleton).
    Fuer den Supervisor – schnelles Routing, ~4x schneller als Sonnet.
    """
    global _fast_llm
    if _fast_llm is None:
        _fast_llm = ChatAnthropic(model="claude-haiku-4-5-20251001")
    return _fast_llm
