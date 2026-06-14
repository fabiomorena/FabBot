"""
tests/test_ph225_llm_routing_golden.py – Phase 225 (Issue #286)

Misst die End-to-End-Routing-Trefferquote des Supervisors (Pre-Routing + Haiku-LLM)
gegen ein Golden-Set realistischer Nachrichten. Braucht einen echten ANTHROPIC_API_KEY
und wird in der CI (kein Key) automatisch übersprungen – die LLM-Entscheidung ist
nicht-deterministisch, daher Schwellwert statt 100%.

Lokal ausführen:
    ANTHROPIC_API_KEY=… .venv/bin/python -m pytest tests/test_ph225_llm_routing_golden.py -v -s
"""

import os

import pytest
from langchain_core.messages import HumanMessage

from agent.supervisor import supervisor_node

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="LLM-Routing-Golden-Set braucht echten ANTHROPIC_API_KEY (in CI geskippt)",
)

# (Nachricht, erwarteter Agent). Mischung aus Pre-Routing- und LLM-Routing-Fällen –
# misst die reale Routing-Erfahrung end-to-end.
_GOLDEN_SET: list[tuple[str, str]] = [
    # Negierte Erinnerungen → reminder_agent (Kern von #286)
    ("Vergiss morgen nicht das Meeting um 10", "reminder_agent"),
    ("Vergiss nicht die Tabletten heute Abend", "reminder_agent"),
    ("Nicht vergessen: Müll rausbringen", "reminder_agent"),
    ("Denk dran, Marco anzurufen", "reminder_agent"),
    ("Erinnere mich um 18 Uhr ans Training", "reminder_agent"),
    # Echte Memory-Fakten → memory_agent
    ("Merke dir dass ich Veganer bin", "memory_agent"),
    ("Mein Bruder Marco wohnt in Hamburg", "memory_agent"),
    ("Vergiss den Eintrag über meine alte Adresse", "memory_agent"),
    # Meinung / Erklärung / Smalltalk → chat_agent
    ("Was denkst du über Techno?", "chat_agent"),
    ("Erklär mir wie eine CPU funktioniert", "chat_agent"),
    ("Danke, das war hilfreich", "chat_agent"),
    ("Fass das nochmal kurz zusammen", "chat_agent"),
    # System / Web / Kalender
    ("Wie viel RAM ist gerade frei?", "system_agent"),
    ("Wie ist das Wetter morgen in Berlin?", "web_agent"),
    ("Was steht heute in meinem Kalender?", "calendar_agent"),
    # Abgrenzung: Erinnerung vs. Memory-Fakt mit Negation
    ("Vergiss nicht dass ich keine Milch mag", "memory_agent"),
    ("Vergiss nicht mich morgen früh zu wecken", "reminder_agent"),
    # Weitere Reminder-Formulierungen
    ("Erinnere mich später daran die Mail zu schreiben", "reminder_agent"),
    ("Liste meine Erinnerungen auf", "reminder_agent"),
    ("Setz eine Erinnerung für 15 Uhr", "reminder_agent"),
]

_ACCURACY_THRESHOLD = 0.85


@pytest.mark.asyncio
async def test_llm_routing_accuracy():
    misses = []
    for text, expected in _GOLDEN_SET:
        state = {"messages": [HumanMessage(content=text)]}
        result = await supervisor_node(state)
        actual = result.get("next_agent")
        if actual != expected:
            misses.append((text, expected, actual))

    accuracy = (len(_GOLDEN_SET) - len(misses)) / len(_GOLDEN_SET)
    assert accuracy >= _ACCURACY_THRESHOLD, (
        f"Routing-Trefferquote {accuracy:.0%} < {_ACCURACY_THRESHOLD:.0%}. Fehler: {misses}"
    )
