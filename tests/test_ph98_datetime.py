"""
tests/test_ph98_datetime.py  –  Ph.99-kompatible Version
"""
import re
import pytest
from unittest.mock import patch

DATETIME_PATTERN = re.compile(r'\d{2}\.\d{2}\.\d{4}')
FIXED_DATETIME   = "Sonntag, 12.04.2026 – 10:00 Uhr"
PATCH_TARGET     = "agent.utils.get_current_datetime"


class TestChatAgentPrompt:

    def test_dynamic_suffix_contains_datetime(self):
        from agent.agents.chat_agent import _build_dynamic_prompt_suffix
        with patch(PATCH_TARGET, return_value=FIXED_DATETIME):
            suffix = _build_dynamic_prompt_suffix(None, None)
        assert DATETIME_PATTERN.search(suffix), f"Kein Datum im Suffix: {suffix!r}"

    def test_contains_datetime(self):
        from agent.agents.chat_agent import _build_dynamic_prompt_suffix
        with patch(PATCH_TARGET, return_value=FIXED_DATETIME):
            suffix = _build_dynamic_prompt_suffix(None, None)
        assert FIXED_DATETIME in suffix, f"Erwartet: {FIXED_DATETIME!r}\nSuffix: {suffix!r}"

    def test_datetime_not_cached_across_calls(self):
        from agent.agents.chat_agent import _build_dynamic_prompt_suffix
        call_count = 0
        def counting_datetime():
            nonlocal call_count
            call_count += 1
            return f"Sonntag, 12.04.2026 – 10:0{call_count} Uhr"
        with patch(PATCH_TARGET, side_effect=counting_datetime):
            s1 = _build_dynamic_prompt_suffix(None, None)
            s2 = _build_dynamic_prompt_suffix(None, None)
        assert call_count == 2, f"get_current_datetime() {call_count}x aufgerufen, erwartet 2x"
        assert s1 != s2

    def test_last_agent_result_in_suffix(self):
        from agent.agents.chat_agent import _build_dynamic_prompt_suffix
        fake = "Berlin: 18°C, sonnig"
        with patch(PATCH_TARGET, return_value=FIXED_DATETIME):
            suffix = _build_dynamic_prompt_suffix(fake, "web_agent")
        assert fake in suffix, f"last_agent_result fehlt im Suffix: {suffix!r}"

    def test_no_last_agent_result_when_none(self):
        from agent.agents.chat_agent import _build_dynamic_prompt_suffix
        with patch(PATCH_TARGET, return_value=FIXED_DATETIME):
            suffix = _build_dynamic_prompt_suffix(None, None)
        assert "None" not in suffix
        assert "last_agent_result" not in suffix


class TestOtherAgentsDatetime:

    @pytest.mark.parametrize("module,func", [
        ("agent.agents.web",            "_build_web_prompt"),
        ("agent.agents.terminal",       "_build_terminal_prompt"),
        ("agent.agents.reminder_agent", "_build_reminder_prompt"),
    ])
    def test_agent_prompt_contains_datetime(self, module, func):
        import importlib
        try:
            mod = importlib.import_module(module)
            build_fn = getattr(mod, func)
        except (ImportError, AttributeError):
            pytest.skip(f"{module}.{func} nicht gefunden")
        with patch(PATCH_TARGET, return_value="Sonntag, 12.04.2026 – 09:00 Uhr"):
            prompt = build_fn()
        assert DATETIME_PATTERN.search(prompt), f"{func}() enthält kein Datum: ...{prompt[-200:]!r}"
