"""
tests/test_sanitize_enforcement.py

Issue #182: sanitize_input_async als strukturelle Pflicht für alle MessageHandler.

Prüft per AST/Regex, dass jede via MessageHandler registrierte Funktion in bot.py
sanitize_input_async, _sanitize_and_validate oder handle_message_text aufruft –
direkt oder über _handle_*-Delegaten (transitiv eine Ebene tief).
Schlägt automatisch an wenn ein neuer Handler ohne Sanitierung hinzugefügt wird.
"""

import ast
import re
from pathlib import Path

BOT_PY = Path(__file__).parent.parent / "bot" / "bot.py"

_SANITIZE_MARKERS = frozenset({"sanitize_input_async", "_sanitize_and_validate", "handle_message_text"})


def _function_body(source: str, func_name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            lines = source.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return ""


def _combined_body(source: str, func_name: str) -> str:
    """Funktionskörper + alle direkt aufgerufenen _handle_*-Delegaten (eine Ebene)."""
    body = _function_body(source, func_name)
    for delegate in re.findall(r"\b(_handle_\w+)\s*\(", body):
        body += "\n" + _function_body(source, delegate)
    return body


def _registered_message_handlers(source: str) -> list[str]:
    return re.findall(r"MessageHandler\s*\([^,]+,\s*(\w+)", source)


class TestSanitizeEnforcement:
    def test_bot_py_exists(self):
        assert BOT_PY.exists(), f"bot.py nicht gefunden: {BOT_PY}"

    def test_message_handlers_registered(self):
        source = BOT_PY.read_text()
        handlers = _registered_message_handlers(source)
        assert len(handlers) > 0, "Keine MessageHandler in bot.py gefunden"

    def test_all_message_handlers_sanitize(self):
        """Jeder MessageHandler muss sanitize_input_async / _sanitize_and_validate
        / handle_message_text im Funktionskörper oder seinen _handle_*-Delegaten aufrufen."""
        source = BOT_PY.read_text()
        handlers = _registered_message_handlers(source)
        missing = []
        for name in handlers:
            body = _combined_body(source, name)
            if not any(marker in body for marker in _SANITIZE_MARKERS):
                missing.append(name)
        assert not missing, (
            "Diese MessageHandler fehlt sanitize_input_async / "
            "_sanitize_and_validate / handle_message_text:\n" + "\n".join(f"  - {n}" for n in missing)
        )

    def test_sanitize_markers_present_in_source(self):
        """Sanity-Check: Sanitize-Funktionen existieren überhaupt in bot.py."""
        source = BOT_PY.read_text()
        for marker in _SANITIZE_MARKERS:
            assert marker in source, f"Marker '{marker}' fehlt komplett in bot.py"
