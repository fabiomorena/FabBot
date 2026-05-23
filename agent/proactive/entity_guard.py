"""
agent/proactive/entity_guard.py – Phase 260 (Issue #260)

Gemeinsamer Entity Guard für proaktive Nachrichten.
Extrahiert aus bot/evening_checkin.py (Phase 204, Issue #214/#216).

API:
  build_context_word_set(text) → frozenset[str]
  extract_named_entities(text) → list[str]
  has_hallucination(response, context_words) → bool
"""

import logging
import re

logger = logging.getLogger(__name__)

# Häufige deutsche Substantive und Funktionswörter, die keine Eigennamen sind
_COMMON_GERMAN_WORDS: frozenset[str] = frozenset(
    {
        "Der",
        "Die",
        "Das",
        "Ein",
        "Eine",
        "Einen",
        "Einem",
        "Einer",
        "Eines",
        "Ich",
        "Du",
        "Er",
        "Sie",
        "Es",
        "Wir",
        "Ihr",
        "Mein",
        "Meine",
        "Dein",
        "Deine",
        "Sein",
        "Ihre",
        "Was",
        "Wie",
        "Wo",
        "Wann",
        "Warum",
        "Welche",
        "Welcher",
        "Welches",
        "Wer",
        "Heute",
        "Gestern",
        "Morgen",
        "Tag",
        "Nacht",
        "Woche",
        "Monat",
        "Jahr",
        "Uhr",
        "Zeit",
        "Stunden",
        "Minuten",
        "Arbeit",
        "Musik",
        "Projekt",
        "Projekte",
        "Session",
        "Thema",
        "Themen",
        "Gespräch",
        "Frage",
        "Antwort",
        "Plan",
        "Idee",
        "Fortschritt",
        "Stand",
        "Nachrichten",
        "Gedanken",
        "Gedanke",
        "Gefühl",
        "Gefühle",
        "Dinge",
        "Ding",
        "Sache",
        "Sachen",
        "Bereich",
        "Teil",
        "Weg",
        "Studio",
        "Mix",
        "Track",
        "Tracks",
        "Beat",
        "Beats",
        "FabBot",
        "Fabio",
    }
)


def build_context_word_set(text: str) -> frozenset[str]:
    """Alle Tokens aus text (lowercase) als Whitelist für den Entity Guard."""
    return frozenset(w.lower() for w in re.findall(r"\b[a-zA-ZäöüÄÖÜß]{2,}\b", text))


def _mid_sentence_caps(text: str) -> list[str]:
    """Großgeschriebene Wörter die NICHT Satzanfang sind (potenzielle Eigennamen)."""
    tokens = list(re.finditer(r"\b([A-ZÄÖÜ][a-zäöüß]{2,})\b", text))
    result = []
    for m in tokens:
        prefix = text[: m.start()].rstrip()
        if not prefix or prefix[-1] in ".!?":
            continue
        result.append(m.group(1))
    return result


def has_hallucination(response: str, context_words: frozenset[str]) -> bool:
    """True wenn response kapitalisierte Wörter enthält die nicht im Kontext stehen."""
    for word in _mid_sentence_caps(response):
        if word in _COMMON_GERMAN_WORDS:
            continue
        if word.lower() not in context_words:
            logger.warning("Entity Guard: potenzielle Halluzination – '%s' nicht im Kontext", word)
            return True
    return False


def extract_named_entities(text: str) -> list[str]:
    """Extrahiert potenzielle Eigennamen aus text für Whitelist-Injection im Prompt."""
    seen: set[str] = set()
    result = []
    for w in re.findall(r"\b([A-ZÄÖÜ][a-zäöüß]{2,})\b", text):
        if w not in _COMMON_GERMAN_WORDS and w not in seen:
            seen.add(w)
            result.append(w)
    return result[:20]
