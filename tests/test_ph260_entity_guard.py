"""
tests/test_ph260_entity_guard.py – Phase 260 (Issue #260)

Testet den Entity Guard aus agent/proactive/entity_guard.py:
- has_hallucination erkennt erfundene Eigennamen
- has_hallucination erlaubt Wörter die im Kontext vorkommen
- extract_named_entities filtert Stoppwörter und Satzanfänge korrekt
- build_context_word_set erzeugt lowercase-Whitelist
"""

from agent.proactive.entity_guard import (
    build_context_word_set,
    extract_named_entities,
    has_hallucination,
)


class TestBuildContextWordSet:
    def test_lowercases_all_words(self):
        result = build_context_word_set("Berlin München Hamburg")
        assert "berlin" in result
        assert "münchen" in result
        assert "hamburg" in result

    def test_includes_short_words(self):
        result = build_context_word_set("ab cd ef")
        assert "ab" in result

    def test_ignores_single_chars(self):
        result = build_context_word_set("a b c x y")
        assert "a" not in result

    def test_empty_string(self):
        assert build_context_word_set("") == frozenset()


class TestExtractNamedEntities:
    def test_extracts_capitalised_mid_sentence_words(self):
        text = "das Projekt Komet läuft gut"
        result = extract_named_entities(text)
        assert "Komet" in result

    def test_filters_common_german_words(self):
        text = "Heute ist ein guter Tag für Musik"
        result = extract_named_entities(text)
        assert "Heute" not in result
        assert "Tag" not in result
        assert "Musik" not in result

    def test_deduplicated(self):
        text = "Berlin ist schön, Berlin ist groß"
        result = extract_named_entities(text)
        assert result.count("Berlin") == 1

    def test_max_20_entities(self):
        words = [f"Name{i:02d}" for i in range(30)]
        text = "bla " + " bla ".join(words) + " bla"
        result = extract_named_entities(text)
        assert len(result) <= 20

    def test_empty_string(self):
        assert extract_named_entities("") == []


class TestHasHallucination:
    def test_no_hallucination_when_word_in_context(self):
        context_words = build_context_word_set("Berlin München Hamburg")
        assert not has_hallucination("Ich war in Berlin.", context_words)

    def test_detects_invented_name(self):
        context_words = build_context_word_set("ich war zuhause")
        assert has_hallucination("Dein Freund Thorsten hat angerufen.", context_words)

    def test_ignores_sentence_start_capitals(self):
        context_words = build_context_word_set("alles gut")
        # "Alles" ist Satzanfang → kein Halluzinations-Treffer
        assert not has_hallucination("Alles klar bei dir?", context_words)

    def test_common_german_words_not_flagged(self):
        context_words = build_context_word_set("x y z")
        # "Heute", "Morgen" etc. sind in _COMMON_GERMAN_WORDS → kein Treffer
        assert not has_hallucination("Heute ist ein guter Tag.", context_words)

    def test_empty_response(self):
        context_words = build_context_word_set("etwas")
        assert not has_hallucination("", context_words)

    def test_mid_sentence_known_entity_passes(self):
        context_words = build_context_word_set("das Projekt Komet läuft gut und macht Fortschritte")
        assert not has_hallucination("Das Projekt Komet macht Fortschritte.", context_words)
