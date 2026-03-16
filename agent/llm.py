"""
Zentraler LLM-Client für FabBot.
Alle Agenten verwenden get_llm() statt eigener Instantiierung.
Lazy singleton – wird nur einmal erstellt.
"""
import os
from langchain_anthropic import ChatAnthropic

_llm: ChatAnthropic | None = None

MODEL = "claude-sonnet-4-20250514"


def get_llm() -> ChatAnthropic:
    """Gibt die gemeinsame LLM-Instanz zurück (lazy singleton)."""
    global _llm
    if _llm is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY nicht gesetzt.")
        _llm = ChatAnthropic(
            model=MODEL,
            api_key=api_key,
        )
    return _llm