"""
tests/test_ph218_self_knowledge.py – Phase 218: Self-Knowledge (Issue #225).

Bot kennt seine eigene Architektur via SELF.md im Projektroot.
SELF.md wird in den gecachten statischen System-Prompt des chat_agent injiziert.
"""

from unittest.mock import MagicMock, patch


class TestLoadSelfMd:
    def setup_method(self):
        import agent.agents.chat_agent as ca

        ca._self_md_cache = None

    def test_load_self_md_returns_content(self):
        import agent.agents.chat_agent as ca

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "# FabBot Architektur\nSonnet fuer Agenten."

        with patch.object(ca, "_SELF_MD_PATH", mock_path):
            ca._self_md_cache = None
            result = ca.load_self_md()

        assert "FabBot Architektur" in result
        assert "Sonnet fuer Agenten" in result

    def test_load_self_md_missing_returns_empty(self):
        import agent.agents.chat_agent as ca

        mock_path = MagicMock()
        mock_path.exists.return_value = False

        with patch.object(ca, "_SELF_MD_PATH", mock_path):
            ca._self_md_cache = None
            result = ca.load_self_md()

        assert result == ""

    def test_load_self_md_cached_after_first_call(self):
        import agent.agents.chat_agent as ca

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "Inhalt"

        with patch.object(ca, "_SELF_MD_PATH", mock_path):
            ca._self_md_cache = None
            ca.load_self_md()
            ca.load_self_md()

        assert mock_path.read_text.call_count == 1

    def test_load_self_md_read_error_returns_empty(self):
        import agent.agents.chat_agent as ca

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = OSError("Permission denied")

        with patch.object(ca, "_SELF_MD_PATH", mock_path):
            ca._self_md_cache = None
            result = ca.load_self_md()

        assert result == ""


class TestBuildChatPromptWithSelfMd:
    def setup_method(self):
        import agent.agents.chat_agent as ca

        ca._prompt_cache = None
        ca._self_md_cache = None

    def test_self_md_in_static_prompt(self):
        import agent.agents.chat_agent as ca

        with (
            patch.object(ca, "load_self_md", return_value="SELF_MD_INHALT"),
            patch("agent.agents.chat_agent.load_claude_md", return_value="", create=True),
            patch(
                "agent.agents.chat_agent.get_profile_context_full",
                return_value="",
                create=True,
            ),
        ):
            ca._prompt_cache = None
            result = ca._build_chat_prompt()

        assert "SELF_MD_INHALT" in result

    def test_self_md_before_claude_md_in_prompt(self):
        import agent.agents.chat_agent as ca

        with (
            patch.object(ca, "load_self_md", return_value="SELF_MD_MARKER"),
            patch("agent.claude_md.load_claude_md", return_value="CLAUDE_MD_MARKER"),
            patch(
                "agent.agents.chat_agent.get_profile_context_full",
                return_value="",
                create=True,
            ),
        ):
            ca._prompt_cache = None
            result = ca._build_chat_prompt()

        assert "SELF_MD_MARKER" in result
        assert "CLAUDE_MD_MARKER" in result
        assert result.index("SELF_MD_MARKER") < result.index("CLAUDE_MD_MARKER")


class TestInvalidateChatCacheResetsSelMd:
    def test_invalidate_resets_self_md_cache(self):
        import agent.agents.chat_agent as ca

        ca._prompt_cache = MagicMock()
        ca._self_md_cache = "gecachter Inhalt"

        ca.invalidate_chat_cache()

        assert ca._prompt_cache is None
        assert ca._self_md_cache is None
