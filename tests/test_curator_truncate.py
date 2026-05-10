"""
tests/test_curator_truncate.py

Issue #181: _truncate_profile_yaml – Fallback wenn erste Sektion > 8000 Zeichen.

Testet:
- Erste Sektion allein > _YAML_MAX_CHARS → Ergebnis nicht leer, hard truncate auf max
- Normale Truncation: Sektion 1 passt, Sektion 2 zu groß → break, Hinweis am Ende
- Alles passt rein → unveränderter Output
- skipped-Hinweis zeigt korrekte Anzahl übersprungener Sektionen
"""


from agent.proactive.curator import _truncate_profile_yaml, _YAML_MAX_CHARS


def _make_big_value(size: int) -> str:
    return "x" * size


class TestTruncateProfileYaml:
    def test_small_profile_unchanged(self):
        profile = {"name": "Fabio", "city": "Berlin"}
        result = _truncate_profile_yaml(profile)
        assert "Fabio" in result
        assert "Berlin" in result
        assert len(result) <= _YAML_MAX_CHARS

    def test_first_section_oversized_not_empty(self):
        """Bug #181: erste Sektion > _YAML_MAX_CHARS → kein leeres Ergebnis."""
        profile = {"notes": _make_big_value(_YAML_MAX_CHARS + 1000)}
        result = _truncate_profile_yaml(profile)
        assert len(result) > 0, "Ergebnis darf nicht leer sein"
        assert len(result) <= _YAML_MAX_CHARS

    def test_first_section_oversized_hard_truncated(self):
        """Hard-truncated Sektion hat exakt _YAML_MAX_CHARS Zeichen."""
        profile = {"notes": _make_big_value(_YAML_MAX_CHARS * 2)}
        result = _truncate_profile_yaml(profile)
        assert len(result) == _YAML_MAX_CHARS

    def test_normal_truncation_first_section_kept(self):
        """Sektion 1 passt, Sektion 2 überschreitet Limit → Sektion 1 enthalten."""
        profile = {
            "name": "Fabio",
            "notes": _make_big_value(_YAML_MAX_CHARS),
        }
        result = _truncate_profile_yaml(profile)
        assert "Fabio" in result
        assert "name" in result

    def test_normal_truncation_shows_skipped_hint(self):
        """Wenn Sektionen übersprungen wurden → Hinweis am Ende."""
        profile = {
            "name": "Fabio",
            "notes": _make_big_value(_YAML_MAX_CHARS),
            "extra": "more",
        }
        result = _truncate_profile_yaml(profile)
        assert "gekürzt" in result

    def test_skipped_count_correct(self):
        """skipped-Hinweis zeigt korrekte Anzahl."""
        profile = {
            "name": "Fabio",
            "notes": _make_big_value(_YAML_MAX_CHARS),
            "extra": "more",
        }
        result = _truncate_profile_yaml(profile)
        assert "[2 Sektionen gekürzt]" in result

    def test_multiple_sections_fit(self):
        """Mehrere kleine Sektionen passen alle rein."""
        profile = {f"key{i}": f"value{i}" for i in range(10)}
        result = _truncate_profile_yaml(profile)
        for i in range(10):
            assert f"value{i}" in result
        assert "gekürzt" not in result
