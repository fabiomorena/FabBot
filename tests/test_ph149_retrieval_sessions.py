"""
Tests für Phase 149 – Issues #35 und #33.

#35 – retrieval: Sessions aus ChromaDB-Index ausschließen
  1. index_file() überspringt Dateien unter _SESSIONS_DIR
  2. index_file() indexiert Dateien unter _WISSEN_DIR normal
  3. index_all() ruft _remove_sessions_from_index() auf
  4. index_all() indexiert keine Session-Dateien mehr
  5. _remove_sessions_from_index() löscht Chunks mit type='session' aus ChromaDB
  6. _remove_sessions_from_index() bereinigt Meta-Einträge für Session-Pfade
  7. _remove_sessions_from_index() tut nichts wenn keine Session-Chunks vorhanden

#33 – retrieval: Session-Load Rolling Window
  8.  _load_all_sessions() lädt alle wenn ≤20 Sessions
  9.  _load_all_sessions() wendet 30-Tage-Window an bei >20 Sessions
  10. _load_all_sessions() wendet 14-Tage-Window an bei ≥50 Sessions
  11. _load_all_sessions() respektiert max_days-Override
  12. _load_all_sessions() gibt mindestens letzte Session zurück wenn alle älter als Window
  13. _load_all_sessions() Cache-Hit bei unveränderter mtime
  14. _parse_session_date() parst YYYY-MM-DD korrekt
  15. _parse_session_date() gibt None bei ungültigem Dateiname
"""

import asyncio
import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_files(sessions_dir: Path, stems: list[str], content: str = "x") -> list[Path]:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for stem in stems:
        f = sessions_dir / f"{stem}.md"
        f.write_text(f"# {stem}\n{content}", encoding="utf-8")
        files.append(f)
    return files


# ---------------------------------------------------------------------------
# #35 – index_file() überspringt Sessions
# ---------------------------------------------------------------------------

def test_index_file_skips_session_dir(tmp_path):
    """index_file() gibt False zurück für Dateien unter _SESSIONS_DIR."""
    session_file = tmp_path / "Sessions" / "2026-04-01.md"
    session_file.parent.mkdir(parents=True)
    session_file.write_text("test", encoding="utf-8")

    with patch("agent.retrieval._SESSIONS_DIR", tmp_path / "Sessions"), \
         patch("agent.retrieval._get_collection", return_value=MagicMock()):
        from agent import retrieval
        result = asyncio.get_event_loop().run_until_complete(
            retrieval.index_file(session_file)
        )
    assert result is False


def test_index_file_indexes_wissen_file(tmp_path):
    """index_file() verarbeitet normale Wissen-Dateien korrekt."""
    wissen_dir = tmp_path / "Wissen"
    wissen_dir.mkdir()
    note = wissen_dir / "notiz.md"
    note.write_text("Inhalt einer Notiz", encoding="utf-8")

    mock_col = MagicMock()
    mock_col.get.return_value = {"ids": []}

    with patch("agent.retrieval._SESSIONS_DIR", tmp_path / "Wissen" / "Sessions"), \
         patch("agent.retrieval._WISSEN_DIR", wissen_dir), \
         patch("agent.retrieval._get_collection", return_value=mock_col), \
         patch("agent.retrieval._load_meta", return_value={}), \
         patch("agent.retrieval._save_meta"), \
         patch("agent.retrieval._embed_texts", new=AsyncMock(return_value=[[0.1] * 3])):
        from agent import retrieval
        result = asyncio.get_event_loop().run_until_complete(
            retrieval.index_file(note, force=True)
        )
    assert result is True


# ---------------------------------------------------------------------------
# #35 – index_all() ohne Sessions
# ---------------------------------------------------------------------------

def test_index_all_calls_remove_sessions(tmp_path):
    """index_all() ruft _remove_sessions_from_index() auf."""
    with patch("agent.retrieval._remove_claude_md_from_index", new=AsyncMock()), \
         patch("agent.retrieval._remove_sessions_from_index", new=AsyncMock()) as mock_rm, \
         patch("agent.retrieval._index_virtual", new=AsyncMock(return_value=False)), \
         patch("agent.retrieval._WISSEN_DIR", tmp_path), \
         patch("agent.retrieval._get_collection", return_value=MagicMock(count=lambda: 0)):
        from agent import retrieval
        asyncio.get_event_loop().run_until_complete(retrieval.index_all())
    mock_rm.assert_awaited_once()


def test_index_all_no_session_files_indexed(tmp_path):
    """index_all() übergibt keine Session-Dateien an index_file()."""
    sessions_dir = tmp_path / "Sessions"
    sessions_dir.mkdir()
    (sessions_dir / "2026-04-01.md").write_text("session", encoding="utf-8")
    wissen_dir = tmp_path

    indexed_paths = []

    async def fake_index_file(path, force=False):
        indexed_paths.append(path)
        return False

    with patch("agent.retrieval._remove_claude_md_from_index", new=AsyncMock()), \
         patch("agent.retrieval._remove_sessions_from_index", new=AsyncMock()), \
         patch("agent.retrieval._index_virtual", new=AsyncMock(return_value=False)), \
         patch("agent.retrieval._WISSEN_DIR", wissen_dir), \
         patch("agent.retrieval._SESSIONS_DIR", sessions_dir), \
         patch("agent.retrieval.index_file", side_effect=fake_index_file), \
         patch("agent.retrieval._get_collection", return_value=MagicMock(count=lambda: 0)):
        from agent import retrieval
        asyncio.get_event_loop().run_until_complete(retrieval.index_all())

    for p in indexed_paths:
        assert "Sessions" not in str(p)


# ---------------------------------------------------------------------------
# #35 – _remove_sessions_from_index()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_sessions_deletes_chunks():
    """_remove_sessions_from_index() löscht Chunks mit type='session'."""
    mock_col = MagicMock()
    mock_col.get.return_value = {"ids": ["id1", "id2"]}

    with patch("agent.retrieval._get_collection", return_value=mock_col), \
         patch("agent.retrieval._load_meta", return_value={}), \
         patch("agent.retrieval._save_meta"):
        from agent import retrieval
        await retrieval._remove_sessions_from_index()

    mock_col.delete.assert_called_once_with(ids=["id1", "id2"])


@pytest.mark.asyncio
async def test_remove_sessions_cleans_meta(tmp_path):
    """_remove_sessions_from_index() entfernt Meta-Einträge für Session-Pfade."""
    sessions_dir = tmp_path / "Sessions"
    sessions_dir.mkdir()
    session_key = str((sessions_dir / "2026-04-01.md").resolve())
    other_key = str((tmp_path / "notiz.md").resolve())
    meta = {session_key: 1000.0, other_key: 2000.0}

    mock_col = MagicMock()
    mock_col.get.return_value = {"ids": ["id1"]}

    saved = {}

    def fake_save(m):
        saved.update(m)

    with patch("agent.retrieval._get_collection", return_value=mock_col), \
         patch("agent.retrieval._SESSIONS_DIR", sessions_dir), \
         patch("agent.retrieval._load_meta", return_value=dict(meta)), \
         patch("agent.retrieval._save_meta", side_effect=fake_save):
        from agent import retrieval
        await retrieval._remove_sessions_from_index()

    assert session_key not in saved
    assert other_key in saved


@pytest.mark.asyncio
async def test_remove_sessions_noop_when_empty():
    """_remove_sessions_from_index() tut nichts wenn keine Session-Chunks."""
    mock_col = MagicMock()
    mock_col.get.return_value = {"ids": []}

    with patch("agent.retrieval._get_collection", return_value=mock_col):
        from agent import retrieval
        await retrieval._remove_sessions_from_index()

    mock_col.delete.assert_not_called()


# ---------------------------------------------------------------------------
# #33 – _parse_session_date()
# ---------------------------------------------------------------------------

def test_parse_session_date_valid():
    from agent.agents.chat_agent import _parse_session_date
    result = _parse_session_date("2026-04-15")
    assert result == date(2026, 4, 15)


def test_parse_session_date_invalid():
    from agent.agents.chat_agent import _parse_session_date
    assert _parse_session_date("notiz") is None
    assert _parse_session_date("2026-13-01") is None
    assert _parse_session_date("") is None


# ---------------------------------------------------------------------------
# #33 – _load_all_sessions() Rolling Window
# ---------------------------------------------------------------------------

def _stems(n: int, days_back_start: int = 0) -> list[str]:
    """Erzeugt n Dateinamen (YYYY-MM-DD) rückwärts ab today - days_back_start."""
    today = date.today()
    return [
        (today - timedelta(days=days_back_start + i)).strftime("%Y-%m-%d")
        for i in range(n)
    ]


def _run_load_sessions(sessions_dir: Path, **kwargs) -> str:
    import agent.agents.chat_agent as ca
    ca._sessions_cache = None
    with patch("agent.retrieval._SESSIONS_DIR", sessions_dir):
        return ca._load_all_sessions(**kwargs)


def test_load_sessions_all_below_threshold(tmp_path):
    """Bis 20 Sessions: alle geladen, kein Filtering."""
    stems = _stems(15)
    _make_session_files(tmp_path, stems)

    result = _run_load_sessions(tmp_path)

    assert "alle" in result
    for s in stems:
        assert s in result


def test_load_sessions_rolling_window_20_plus(tmp_path):
    """Bei >20 Sessions: nur die letzten 30 Tage geladen."""
    recent = _stems(5, days_back_start=0)      # innerhalb 30 Tage
    old = _stems(20, days_back_start=60)        # älter als 30 Tage
    _make_session_files(tmp_path, recent + old)

    result = _run_load_sessions(tmp_path)

    assert "letzte 30 Tage" in result
    for s in recent:
        assert s in result
    for s in old:
        assert s not in result


def test_load_sessions_rolling_window_50_plus(tmp_path):
    """Bei ≥50 Sessions: nur die letzten 14 Tage geladen."""
    recent = _stems(5, days_back_start=0)       # innerhalb 14 Tage
    old = _stems(50, days_back_start=20)         # älter als 14 Tage
    _make_session_files(tmp_path, recent + old)

    result = _run_load_sessions(tmp_path)

    assert "letzte 14 Tage" in result
    for s in recent:
        assert s in result
    for s in old:
        assert s not in result


def test_load_sessions_max_days_override(tmp_path):
    """max_days-Parameter überschreibt Standard-Window."""
    recent = _stems(3, days_back_start=0)       # innerhalb 7 Tage
    old = _stems(25, days_back_start=10)         # älter als 7 Tage
    _make_session_files(tmp_path, recent + old)

    result = _run_load_sessions(tmp_path, max_days=7)

    assert "letzte 7 Tage" in result
    for s in recent:
        assert s in result
    for s in old:
        assert s not in result


def test_load_sessions_fallback_to_last_when_all_old(tmp_path):
    """Wenn alle Sessions älter als Window: mindestens die letzte laden."""
    old = _stems(25, days_back_start=60)         # alle älter als 30 Tage
    files = _make_session_files(tmp_path, old)

    result = _run_load_sessions(tmp_path)

    assert result != ""
    # Neueste der alten Dateien (alphabetisch letzte) muss enthalten sein
    assert sorted(old)[-1] in result


def test_load_sessions_cache_hit(tmp_path):
    """Cache-Hit: kein erneutes Lesen wenn mtime unverändert."""
    stems = _stems(5)
    files = _make_session_files(tmp_path, stems)
    max_mtime = max(f.stat().st_mtime for f in files)

    import agent.agents.chat_agent as ca
    ca._sessions_cache = (max_mtime, "cached_result")

    with patch("agent.retrieval._SESSIONS_DIR", tmp_path):
        result = ca._load_all_sessions()

    assert result == "cached_result"
    ca._sessions_cache = None
