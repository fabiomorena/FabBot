"""Tests für agent/chat_agent.py – Re-Export-Modul."""


def test_re_export_importable():
    import agent.chat_agent as reexport

    assert hasattr(reexport, "invalidate_chat_cache")
    assert hasattr(reexport, "_CachedPrompt")
