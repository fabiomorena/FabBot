"""Tests für agent/retrieval.py – reine Hilfsfunktionen ohne ChromaDB/OpenAI."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import agent.retrieval as retrieval_module
from agent.retrieval import (
    _chunk_text,
    _make_chunk_id,
    _content_hash,
    _load_meta,
    _save_meta,
    _get_semaphore,
)


@pytest.fixture(autouse=True)
def reset_retrieval_singletons():
    original_semaphore = retrieval_module._write_semaphore
    original_collection = retrieval_module._collection
    retrieval_module._write_semaphore = None
    retrieval_module._collection = None
    yield
    retrieval_module._write_semaphore = original_semaphore
    retrieval_module._collection = original_collection


# ---------------------------------------------------------------------------
# _chunk_text
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_empty_returns_empty(self):
        assert _chunk_text("") == []

    def test_whitespace_only_returns_empty(self):
        assert _chunk_text("   \n  ") == []

    def test_short_text_single_chunk(self):
        text = "Das ist ein kurzer Text mit mehr als 50 Zeichen damit er nicht gefiltert wird."
        chunks = _chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text.strip()

    def test_text_below_min_chars_filtered(self):
        chunks = _chunk_text("kurz")
        assert chunks == []

    def test_splits_on_markdown_headings(self):
        text = "# Abschnitt A\nInhalt A mit ausreichend Zeichen hier.\n# Abschnitt B\nInhalt B mit ausreichend Zeichen hier."
        chunks = _chunk_text(text)
        assert len(chunks) == 2
        assert any("Abschnitt A" in c for c in chunks)
        assert any("Abschnitt B" in c for c in chunks)

    def test_long_text_split_on_paragraphs(self):
        paragraph = "x" * 800
        text = paragraph + "\n\n" + paragraph
        chunks = _chunk_text(text)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 1500

    def test_very_long_paragraph_truncated(self):
        text = "x" * 2000
        chunks = _chunk_text(text)
        for chunk in chunks:
            assert len(chunk) <= 1500


# ---------------------------------------------------------------------------
# _make_chunk_id
# ---------------------------------------------------------------------------


class TestMakeChunkId:
    def test_returns_32_char_hex(self):
        chunk_id = _make_chunk_id("source", 0)
        assert len(chunk_id) == 32
        assert all(c in "0123456789abcdef" for c in chunk_id)

    def test_different_sources_different_ids(self):
        id1 = _make_chunk_id("source_a", 0)
        id2 = _make_chunk_id("source_b", 0)
        assert id1 != id2

    def test_different_indices_different_ids(self):
        id1 = _make_chunk_id("source", 0)
        id2 = _make_chunk_id("source", 1)
        assert id1 != id2

    def test_deterministic(self):
        assert _make_chunk_id("x", 5) == _make_chunk_id("x", 5)


# ---------------------------------------------------------------------------
# _content_hash
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_returns_64_char_hex(self):
        h = _content_hash("test")
        assert len(h) == 64

    def test_same_input_same_hash(self):
        assert _content_hash("abc") == _content_hash("abc")

    def test_different_input_different_hash(self):
        assert _content_hash("abc") != _content_hash("def")

    def test_empty_string(self):
        h = _content_hash("")
        assert len(h) == 64


# ---------------------------------------------------------------------------
# _load_meta / _save_meta
# ---------------------------------------------------------------------------


class TestLoadMeta:
    def test_returns_empty_when_file_missing(self, tmp_path):
        with patch("agent.retrieval._META_PATH", tmp_path / "nonexistent.json"):
            result = _load_meta()
        assert result == {}

    def test_reads_existing_file(self, tmp_path):
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        with patch("agent.retrieval._META_PATH", meta_file):
            result = _load_meta()
        assert result == {"key": "value"}

    def test_returns_empty_on_corrupt_file(self, tmp_path):
        meta_file = tmp_path / "meta.json"
        meta_file.write_text("{ ungültiges json }", encoding="utf-8")
        with patch("agent.retrieval._META_PATH", meta_file):
            result = _load_meta()
        assert result == {}


class TestSaveMeta:
    def test_writes_file(self, tmp_path):
        meta_file = tmp_path / "meta.json"
        with patch("agent.retrieval._META_PATH", meta_file):
            _save_meta({"k": "v"})
        assert json.loads(meta_file.read_text()) == {"k": "v"}

    def test_creates_parent_dir(self, tmp_path):
        meta_file = tmp_path / "subdir" / "meta.json"
        with patch("agent.retrieval._META_PATH", meta_file):
            _save_meta({"k": "v"})
        assert meta_file.exists()


# ---------------------------------------------------------------------------
# _get_semaphore
# ---------------------------------------------------------------------------


class TestGetSemaphore:
    async def test_returns_semaphore(self):
        sem = _get_semaphore()
        import asyncio

        assert isinstance(sem, asyncio.Semaphore)

    async def test_returns_same_instance(self):
        sem1 = _get_semaphore()
        sem2 = _get_semaphore()
        assert sem1 is sem2


# ---------------------------------------------------------------------------
# _get_collection – fail-safe Branches
# ---------------------------------------------------------------------------


class TestGetCollection:
    def test_returns_none_when_chromadb_missing(self):
        with patch.dict("sys.modules", {"chromadb": None}):
            result = retrieval_module._get_collection()
        assert result is None

    def test_returns_none_on_exception(self):
        mock_chromadb = MagicMock()
        mock_chromadb.PersistentClient.side_effect = RuntimeError("db error")
        with patch.dict("sys.modules", {"chromadb": mock_chromadb}):
            result = retrieval_module._get_collection()
        assert result is None

    def test_returns_cached_collection(self):
        mock_collection = MagicMock()
        retrieval_module._collection = mock_collection
        result = retrieval_module._get_collection()
        assert result is mock_collection


# ---------------------------------------------------------------------------
# _embed_texts – fail-safe Branches
# ---------------------------------------------------------------------------


class TestEmbedTexts:
    async def test_returns_none_when_no_api_key(self):
        settings = MagicMock()
        secret = MagicMock()
        secret.get_secret_value.return_value = ""
        settings.openai_api_key = secret
        with patch("agent.retrieval.get_settings", return_value=settings):
            result = await retrieval_module._embed_texts(["test"])
        assert result is None

    async def test_returns_empty_list_for_empty_input(self):
        settings = MagicMock()
        secret = MagicMock()
        secret.get_secret_value.return_value = "sk-test"
        settings.openai_api_key = secret
        with patch("agent.retrieval.get_settings", return_value=settings):
            result = await retrieval_module._embed_texts([])
        assert result == []


# ---------------------------------------------------------------------------
# search – fail-safe Branches
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_returns_empty_when_no_collection(self):
        retrieval_module._collection = None
        with patch("agent.retrieval._get_collection", return_value=None):
            result = await retrieval_module.search("test")
        assert result == []

    async def test_returns_empty_when_collection_empty(self):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 0
        with patch("agent.retrieval._get_collection", return_value=mock_collection):
            result = await retrieval_module.search("test")
        assert result == []

    async def test_returns_empty_when_no_embeddings(self):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        with (
            patch("agent.retrieval._get_collection", return_value=mock_collection),
            patch("agent.retrieval._embed_texts", new_callable=AsyncMock, return_value=None),
        ):
            result = await retrieval_module.search("test")
        assert result == []

    async def test_filters_by_max_distance(self):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 2
        mock_collection.query = MagicMock(
            return_value={
                "ids": [["id1", "id2"]],
                "documents": [["doc nah", "doc weit"]],
                "metadatas": [[{"label": "A", "type": "t"}, {"label": "B", "type": "t"}]],
                "distances": [[0.3, 0.8]],
            }
        )
        with (
            patch("agent.retrieval._get_collection", return_value=mock_collection),
            patch("agent.retrieval._embed_texts", new_callable=AsyncMock, return_value=[[0.1, 0.2]]),
            patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=lambda fn, *a, **kw: fn(*a, **kw)),
        ):
            result = await retrieval_module.search("test", n_results=2)
        assert len(result) == 1
        assert result[0]["document"] == "doc nah"
        assert result[0]["distance"] == 0.3


# ---------------------------------------------------------------------------
# remove_file – fail-safe Branch
# ---------------------------------------------------------------------------


class TestRemoveFile:
    async def test_noop_when_no_collection(self, tmp_path):
        with patch("agent.retrieval._get_collection", return_value=None):
            await retrieval_module.remove_file(tmp_path / "file.md")
