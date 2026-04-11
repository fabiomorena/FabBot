"""
Tests für Phase 95 – Issue #6: validate_models_on_startup() in llm.py.

Testet:
1. Valide Default-Models → kein Fehler
2. Valider Custom-Model-String → kein Fehler
3. Leerer SONNET-String → RuntimeError
4. Leerer HAIKU-String → RuntimeError
5. Tippfehler im Prefix (claud- statt claude-) → RuntimeError
6. Fehlende Datumszahl → RuntimeError
7. Beide ungültig → RuntimeError mit beiden Fehlern im Text
8. Fehlermeldung enthält den ungültigen String
9. Nach Fehler: _warn_if_unusual() wird NICHT zusätzlich aufgerufen (RuntimeError kommt zuerst)
10. _warn_if_unusual() allein: valide Models → keine Warning
"""

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Hilfsfunktion
# ---------------------------------------------------------------------------

def _run_validate(sonnet=None, haiku=None):
    """Führt validate_models_on_startup() mit gepatchten Env-Vars aus."""
    env = {}
    if sonnet is not None:
        env["ANTHROPIC_MODEL_SONNET"] = sonnet
    if haiku is not None:
        env["ANTHROPIC_MODEL_HAIKU"] = haiku

    # Defaults wenn nicht gesetzt
    defaults = {
        "ANTHROPIC_MODEL_SONNET": "claude-sonnet-4-20250514",
        "ANTHROPIC_MODEL_HAIKU":  "claude-haiku-4-5-20251001",
    }
    for k, v in defaults.items():
        if k not in env:
            env[k] = v

    with patch.dict("os.environ", env, clear=False):
        import importlib
        import agent.llm as llm_mod
        importlib.reload(llm_mod)
        llm_mod.validate_models_on_startup()


# ---------------------------------------------------------------------------
# 1. Valide Default-Models
# ---------------------------------------------------------------------------

def test_valid_defaults():
    """Default-Models sind valide – kein Fehler."""
    _run_validate()  # Kein Exception = OK


# ---------------------------------------------------------------------------
# 2. Valider Custom-Model-String
# ---------------------------------------------------------------------------

def test_valid_custom_models():
    """Valide Custom-Strings passieren ohne Fehler."""
    _run_validate(
        sonnet="claude-opus-4-20260101",
        haiku="claude-haiku-4-5-20251001",
    )


# ---------------------------------------------------------------------------
# 3. Leerer SONNET-String
# ---------------------------------------------------------------------------

def test_empty_sonnet_raises():
    """Leerer SONNET-String → RuntimeError."""
    with pytest.raises(RuntimeError, match="ANTHROPIC_MODEL_SONNET"):
        _run_validate(sonnet="")


# ---------------------------------------------------------------------------
# 4. Leerer HAIKU-String
# ---------------------------------------------------------------------------

def test_empty_haiku_raises():
    """Leerer HAIKU-String → RuntimeError."""
    with pytest.raises(RuntimeError, match="ANTHROPIC_MODEL_HAIKU"):
        _run_validate(haiku="")


# ---------------------------------------------------------------------------
# 5. Tippfehler im Prefix
# ---------------------------------------------------------------------------

def test_typo_prefix_raises():
    """'claud-sonnet-4-20250514' (fehlendes 'e') → RuntimeError."""
    with pytest.raises(RuntimeError, match="ANTHROPIC_MODEL_SONNET"):
        _run_validate(sonnet="claud-sonnet-4-20250514")


# ---------------------------------------------------------------------------
# 6. Fehlende Datumszahl
# ---------------------------------------------------------------------------

def test_missing_date_raises():
    """'claude-sonnet' ohne Datum → RuntimeError."""
    with pytest.raises(RuntimeError, match="ANTHROPIC_MODEL_HAIKU"):
        _run_validate(haiku="claude-haiku")


# ---------------------------------------------------------------------------
# 7. Beide ungültig → beide im Fehlertext
# ---------------------------------------------------------------------------

def test_both_invalid_raises_with_both_errors():
    """Beide ungültig → RuntimeError enthält beide Env-Var-Namen."""
    with pytest.raises(RuntimeError) as exc_info:
        _run_validate(sonnet="wrong", haiku="also-wrong")
    msg = str(exc_info.value)
    assert "ANTHROPIC_MODEL_SONNET" in msg
    assert "ANTHROPIC_MODEL_HAIKU" in msg


# ---------------------------------------------------------------------------
# 8. Fehlermeldung enthält den ungültigen String
# ---------------------------------------------------------------------------

def test_error_message_contains_invalid_string():
    """RuntimeError-Meldung enthält den tatsächlich konfigurierten String."""
    bad_model = "claud-typo-20250514"
    with pytest.raises(RuntimeError) as exc_info:
        _run_validate(sonnet=bad_model)
    assert bad_model in str(exc_info.value)


# ---------------------------------------------------------------------------
# 9. _warn_if_unusual() – valide Models → keine Warning
# ---------------------------------------------------------------------------

def test_warn_if_unusual_valid_no_warning(caplog):
    """Valide Model-Strings erzeugen keine Warning."""
    import logging
    import importlib
    import agent.llm as llm_mod
    importlib.reload(llm_mod)

    with caplog.at_level(logging.WARNING, logger="agent.llm"):
        llm_mod._warn_if_unusual("claude-sonnet-4-20250514")
        llm_mod._warn_if_unusual("claude-haiku-4-5-20251001")

    assert not caplog.records, f"Unerwartete Warnings: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 10. _warn_if_unusual() – ungültiger String → Warning
# ---------------------------------------------------------------------------

def test_warn_if_unusual_invalid_logs_warning(caplog):
    """Ungültiger Model-String → Warning wird geloggt."""
    import logging
    import importlib
    import agent.llm as llm_mod
    importlib.reload(llm_mod)

    with caplog.at_level(logging.WARNING, logger="agent.llm"):
        llm_mod._warn_if_unusual("claud-typo-20250514")

    assert any("Ungewöhnlicher Modell-String" in r.message for r in caplog.records)
