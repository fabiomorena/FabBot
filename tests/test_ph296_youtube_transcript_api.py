"""
tests/test_ph296_youtube_transcript_api.py – Phase 296 (#301)

Testet _fetch_transcript gegen die instanzbasierte youtube-transcript-api 1.x:
- Kontrakt gegen die installierte Bibliothek (schlägt bei Breaking Changes an)
- fetch() auf einer Instanz statt statischem get_transcript()
- Snippet-Objekte (.text) statt dicts
- Truncation, Sprachpriorität, Fehlerfall

Ergänzt test_ph190_audio_youtube.py, das _fetch_transcript komplett wegmockt
und den Breaking Change deshalb nicht bemerkt hat.
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_snippet(text: str):
    """FetchedTranscriptSnippet-Ersatz – Attributzugriff, kein dict."""
    snippet = MagicMock()
    snippet.text = text
    return snippet


def _make_api_mock(snippets):
    """Baut ein YouTubeTranscriptApi-Mock nach 1.x-Bauart: Instanz mit fetch()."""
    api_instance = MagicMock()
    api_instance.fetch = MagicMock(return_value=snippets)
    api_class = MagicMock(return_value=api_instance)
    return api_class, api_instance


# ---------------------------------------------------------------------------
# Kontrakt gegen die installierte Bibliothek
# ---------------------------------------------------------------------------


class TestInstalledApiContract:
    def test_api_exposes_fetch(self):
        from youtube_transcript_api import YouTubeTranscriptApi

        assert hasattr(YouTubeTranscriptApi, "fetch"), (
            "youtube-transcript-api hat keine fetch()-Methode mehr – API erneut geändert"
        )

    def test_static_get_transcript_is_gone(self):
        """Guard: verhindert Rückfall auf die entfernte 0.x-API."""
        from youtube_transcript_api import YouTubeTranscriptApi

        assert not hasattr(YouTubeTranscriptApi, "get_transcript"), (
            "get_transcript ist zurück – _fetch_transcript-Implementierung prüfen"
        )

    def test_fetch_accepts_languages_kwarg(self):
        import inspect
        from youtube_transcript_api import YouTubeTranscriptApi

        params = inspect.signature(YouTubeTranscriptApi.fetch).parameters
        assert "video_id" in params
        assert "languages" in params

    def test_snippet_exposes_text_attribute(self):
        from youtube_transcript_api._transcripts import FetchedTranscriptSnippet

        assert "text" in FetchedTranscriptSnippet.__dataclass_fields__


# ---------------------------------------------------------------------------
# _fetch_transcript
# ---------------------------------------------------------------------------


class TestFetchTranscript:
    @pytest.mark.asyncio
    async def test_uses_instance_fetch_not_static_call(self):
        from agent.agents.youtube_agent import _fetch_transcript

        api_class, api_instance = _make_api_mock([_make_snippet("Hallo")])

        with patch("youtube_transcript_api.YouTubeTranscriptApi", api_class):
            result = await _fetch_transcript("vid123")

        api_class.assert_called_once_with()
        api_instance.fetch.assert_called_once()
        assert result == "Hallo"

    @pytest.mark.asyncio
    async def test_passes_video_id_and_language_priority(self):
        from agent.agents.youtube_agent import _fetch_transcript

        api_class, api_instance = _make_api_mock([_make_snippet("x")])

        with patch("youtube_transcript_api.YouTubeTranscriptApi", api_class):
            await _fetch_transcript("vid123")

        args, kwargs = api_instance.fetch.call_args
        assert args[0] == "vid123"
        assert kwargs["languages"] == ["de", "en", "en-US", "de-DE"]

    @pytest.mark.asyncio
    async def test_joins_snippet_texts_with_spaces(self):
        from agent.agents.youtube_agent import _fetch_transcript

        snippets = [_make_snippet("Erster"), _make_snippet("Zweiter"), _make_snippet("Dritter")]
        api_class, _ = _make_api_mock(snippets)

        with patch("youtube_transcript_api.YouTubeTranscriptApi", api_class):
            result = await _fetch_transcript("vid123")

        assert result == "Erster Zweiter Dritter"

    @pytest.mark.asyncio
    async def test_truncates_to_max_chars(self):
        from agent.agents.youtube_agent import _fetch_transcript, _TRANSCRIPT_MAX_CHARS

        snippets = [_make_snippet("a" * 1000) for _ in range(100)]
        api_class, _ = _make_api_mock(snippets)

        with patch("youtube_transcript_api.YouTubeTranscriptApi", api_class):
            result = await _fetch_transcript("vid123")

        assert len(result) == _TRANSCRIPT_MAX_CHARS

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self):
        from agent.agents.youtube_agent import _fetch_transcript

        api_instance = MagicMock()
        api_instance.fetch = MagicMock(side_effect=RuntimeError("Kein Transcript verfügbar"))
        api_class = MagicMock(return_value=api_instance)

        with patch("youtube_transcript_api.YouTubeTranscriptApi", api_class):
            result = await _fetch_transcript("vid123")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_transcript_returns_empty_string(self):
        from agent.agents.youtube_agent import _fetch_transcript

        api_class, _ = _make_api_mock([])

        with patch("youtube_transcript_api.YouTubeTranscriptApi", api_class):
            result = await _fetch_transcript("vid123")

        assert result == ""

    @pytest.mark.asyncio
    async def test_error_is_logged_as_warning(self, caplog):
        from agent.agents.youtube_agent import _fetch_transcript

        api_instance = MagicMock()
        api_instance.fetch = MagicMock(side_effect=RuntimeError("boom"))
        api_class = MagicMock(return_value=api_instance)

        with patch("youtube_transcript_api.YouTubeTranscriptApi", api_class):
            with caplog.at_level("WARNING", logger="agent.agents.youtube_agent"):
                await _fetch_transcript("vid123")

        assert "vid123" in caplog.text
