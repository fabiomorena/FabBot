"""
Second Brain – Retrieval Engine für FabBot (Phase 77).

ChromaDB (lokal, persistent) + OpenAI text-embedding-3-small.

Indexierte Quellen:
  - personal_profile.yaml  (virtuell, via get_profile_context_full)
  - ~/Documents/Wissen/*.md        (alle /clip Notizen)

Nicht indexiert:
  - ~/Documents/Wissen/Sessions/   (Session Summaries) → direktes Lesen via
    _load_all_sessions() in chat_agent.py (Hotfix 18.04 / Issue #35)
  - claude.md → wird bereits vollständig in jeden chat_agent System-Prompt
    injiziert (direkte Injektion). ChromaDB-Indexierung wäre redundant
    und würde claude.md doppelt in den Prompt laden.

Öffentliche API:
  await index_all(force=False)   – Delta-Indexierung aller Quellen (mtime/hash-Check)
  await index_file(path)         – Einzelne Datei indexieren/aktualisieren
  await remove_file(path)        – Datei aus Index entfernen
  await search(query, n=3)       – Semantische Suche → list[dict]

Design:
  - Fail-safe: chromadb nicht installiert → alle Funktionen geben None/[] zurück
  - Delta (Dateien): nur geänderte Dateien werden re-embedded (mtime-Tracking via JSON)
  - Delta (virtuell): Profil via SHA256-Hash – kein Re-Embed wenn unverändert
  - httpx.AsyncClient: ein Client pro _embed_texts()-Aufruf, außerhalb der Batch-Schleife
  - Semaphore: verhindert parallele ChromaDB-Writes (nur gültig im gleichen asyncio Event Loop / Prozess)
  - Timeout im search(): 5s (wird von chat_agent gesetzt)
  - Mindest-Ähnlichkeit: cosine distance < 0.7 (sonst nicht relevant genug)
  - Chunk-Größe: max. 1500 Zeichen, min. 50 Zeichen
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

_CHROMA_PATH = Path.home() / ".fabbot" / "chroma"
_META_PATH = Path.home() / ".fabbot" / "chroma_meta.json"
_COLLECTION_NAME = "fabbot_knowledge"
_EMBED_MODEL = "text-embedding-3-small"
_MAX_CHUNK_CHARS = 1500
_MIN_CHUNK_CHARS = 50
_MAX_DISTANCE = 0.7
_EMBED_BATCH_SIZE = 100

_WISSEN_DIR = Path(os.getenv("KNOWLEDGE_DIR", str(Path.home() / "Documents" / "Wissen")))
_SESSIONS_DIR = _WISSEN_DIR / "Sessions"

# Semaphore – verhindert parallele ChromaDB-Writes.
# Nur gültig im gleichen asyncio Event Loop / Prozess (Single-Process-Bot).
_write_semaphore: asyncio.Semaphore | None = None

# ChromaDB Collection – lazy singleton
_collection = None

_PID_LOCK_PATH = Path.home() / ".fabbot" / "chroma.pid"


def _check_multiprocess_warning() -> None:
    """Warnt wenn ein zweiter Bot-Prozess ChromaDB gleichzeitig nutzt."""
    pid = os.getpid()
    if _PID_LOCK_PATH.exists():
        try:
            existing_pid = int(_PID_LOCK_PATH.read_text().strip())
            if existing_pid != pid:
                try:
                    os.kill(existing_pid, 0)
                    logger.warning(
                        f"Zweiter Bot-Prozess (PID {existing_pid}) nutzt ChromaDB – "
                        "_write_semaphore schützt nur innerhalb eines Prozesses, "
                        "Datenkorrruption möglich! Alten Prozess beenden."
                    )
                except ProcessLookupError:
                    pass
        except (ValueError, OSError):
            pass
    try:
        _PID_LOCK_PATH.write_text(str(pid))
    except OSError:
        pass


def _get_semaphore() -> asyncio.Semaphore:
    """
    Gibt den Write-Semaphore zurück (lazy, event-loop-sicher).
    Nur gültig im gleichen asyncio Event Loop / Prozess (Single-Process-Bot).
    """
    global _write_semaphore
    if _write_semaphore is None:
        _write_semaphore = asyncio.Semaphore(1)
    return _write_semaphore


# ---------------------------------------------------------------------------
# ChromaDB Setup – fail-safe
# ---------------------------------------------------------------------------


def _get_collection():
    """
    Gibt die ChromaDB-Collection zurück (lazy singleton).
    Gibt None zurück wenn chromadb nicht installiert ist oder Setup fehlschlägt.
    """
    global _collection
    if _collection is not None:
        return _collection
    _check_multiprocess_warning()
    try:
        import chromadb

        _CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
        _collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"ChromaDB Collection '{_COLLECTION_NAME}' bereit ({_collection.count()} Chunks)")
        return _collection
    except ImportError:
        logger.warning("chromadb nicht installiert – Retrieval deaktiviert. Installieren: pip install chromadb")
        return None
    except Exception as e:
        logger.error(f"ChromaDB Setup fehlgeschlagen: {e}")
        return None


# ---------------------------------------------------------------------------
# mtime / Hash-Tracking für Delta-Indexierung
# ---------------------------------------------------------------------------


def _load_meta() -> dict[str, str | float]:
    try:
        if _META_PATH.exists():
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_meta(meta: dict[str, str | float]) -> None:
    try:
        _META_PATH.parent.mkdir(parents=True, exist_ok=True)
        _META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Meta-Speicherung fehlgeschlagen: {e}")


def _content_hash(text: str) -> str:
    """SHA256-Hash eines Textes – für virtuelle Quellen (Profil)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunk_text(text: str) -> list[str]:
    """
    Teilt Text in Chunks auf.
    Splittet zuerst an Markdown-Headings, dann an Absätzen.
    """
    if not text or not text.strip():
        return []

    sections = re.split(r"\n(?=#{1,3} )", text.strip())
    chunks: list[str] = []

    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= _MAX_CHUNK_CHARS:
            if len(section) >= _MIN_CHUNK_CHARS:
                chunks.append(section)
        else:
            paragraphs = [p.strip() for p in section.split("\n\n") if p.strip()]
            current = ""
            for p in paragraphs:
                candidate = (current + "\n\n" + p).strip() if current else p
                if len(candidate) <= _MAX_CHUNK_CHARS:
                    current = candidate
                else:
                    if current and len(current) >= _MIN_CHUNK_CHARS:
                        chunks.append(current)
                    current = p[:_MAX_CHUNK_CHARS]
            if current and len(current) >= _MIN_CHUNK_CHARS:
                chunks.append(current)

    return chunks


def _make_chunk_id(source_id: str, chunk_index: int) -> str:
    raw = f"{source_id}::{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# OpenAI Embeddings
# Ein httpx.AsyncClient außerhalb der Batch-Schleife (HTTP/2-Connection-Reuse)
# ---------------------------------------------------------------------------


async def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """
    Erstellt Embeddings via OpenAI text-embedding-3-small.
    Ein einziger AsyncClient für alle Batches – HTTP/2-Connection-Reuse.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.error("OPENAI_API_KEY nicht gesetzt – Embedding nicht möglich.")
        return None
    if not texts:
        return []

    try:
        import httpx

        all_embeddings: list[list[float]] = []

        async with httpx.AsyncClient(timeout=60) as client:
            for i in range(0, len(texts), _EMBED_BATCH_SIZE):
                batch = texts[i : i + _EMBED_BATCH_SIZE]
                resp = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": _EMBED_MODEL, "input": batch},
                )
                resp.raise_for_status()
                data = resp.json()
                all_embeddings.extend(d["embedding"] for d in data["data"])

        return all_embeddings
    except Exception as e:
        logger.error(f"OpenAI Embedding Fehler: {e}")
        return None


# ---------------------------------------------------------------------------
# Kern-Indexierung
# ---------------------------------------------------------------------------


async def _upsert_chunks(
    chunks: list[str],
    source_id: str,
    source_type: str,
    source_label: str,
    collection,
) -> int:
    if not chunks:
        return 0

    embeddings = await _embed_texts(chunks)
    if embeddings is None:
        return 0

    ids = [_make_chunk_id(source_id, i) for i in range(len(chunks))]
    metadatas = [
        {
            "source": source_id,
            "type": source_type,
            "label": source_label,
            "chunk_index": i,
        }
        for i in range(len(chunks))
    ]

    sem = _get_semaphore()
    async with sem:
        try:
            existing = await asyncio.to_thread(
                collection.get,
                where={"source": source_id},
                include=[],
            )
            if existing and existing.get("ids"):
                await asyncio.to_thread(collection.delete, ids=existing["ids"])
        except Exception as e:
            logger.debug(f"Retrieval: Alte Chunks löschen fehlgeschlagen für '{source_label}': {e}")

        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    return len(chunks)


async def _remove_sessions_from_index() -> None:
    """
    Issue #35: Entfernt alle Session-Chunks aus ChromaDB.
    Einmalige Bereinigung – Sessions werden nicht mehr indexiert.
    """
    collection = _get_collection()
    if collection is None:
        return
    try:
        existing = await asyncio.to_thread(
            collection.get,
            where={"type": "session"},
            include=[],
        )
        if not existing or not existing.get("ids"):
            return
        sem = _get_semaphore()
        async with sem:
            await asyncio.to_thread(collection.delete, ids=existing["ids"])
        meta = _load_meta()
        sessions_str = str(_SESSIONS_DIR.resolve())
        keys_to_remove = [k for k in meta if k.startswith(sessions_str)]
        for k in keys_to_remove:
            meta.pop(k)
        if keys_to_remove:
            _save_meta(meta)
        logger.info(f"Retrieval: {len(existing['ids'])} Session-Chunks aus ChromaDB entfernt (#35)")
    except Exception as e:
        logger.debug(f"Retrieval: Session-Cleanup fehlgeschlagen (ignoriert): {e}")


async def index_file(path: Path, force: bool = False) -> bool:
    """Indexiert eine einzelne Markdown-Datei (Delta: nur bei geänderter mtime).
    Issue #35: Session-Dateien werden übersprungen.
    """
    collection = _get_collection()
    if collection is None:
        return False

    # Issue #35: Sessions direkt via _load_all_sessions() geladen – nicht indexieren
    if str(path.resolve()).startswith(str(_SESSIONS_DIR.resolve())):
        logger.debug(f"Retrieval: '{path.name}' ist Session-Datei – übersprungen (#35)")
        return False

    try:
        filepath = str(path.resolve())
        mtime = path.stat().st_mtime
        meta = _load_meta()

        if not force and meta.get(filepath) == mtime:
            logger.debug(f"Retrieval: '{path.name}' unverändert – skip")
            return False

        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return False

        wissen_str = str(_WISSEN_DIR.resolve())

        if filepath.startswith(wissen_str):
            source_type = "knowledge"
            label = f"Notiz: {path.stem}"
        else:
            source_type = "file"
            label = path.name

        chunks = _chunk_text(text)
        count = await _upsert_chunks(chunks, filepath, source_type, label, collection)

        meta[filepath] = mtime
        _save_meta(meta)
        logger.info(f"Retrieval: '{path.name}' indexiert ({count} Chunks)")
        return True

    except Exception as e:
        logger.error(f"Retrieval: index_file('{path}') fehlgeschlagen: {e}")
        return False


async def _index_virtual(
    content: str,
    virtual_id: str,
    source_type: str,
    label: str,
    force: bool = False,
) -> bool:
    """
    Indexiert virtuellen Inhalt ohne echte Datei.
    SHA256-Hash-Check: nur re-embedden wenn Inhalt geändert.
    """
    collection = _get_collection()
    if collection is None:
        return False
    if not content or not content.strip():
        return False

    try:
        current_hash = _content_hash(content)
        meta_key = f"__hash__{virtual_id}"
        meta = _load_meta()

        if not force and meta.get(meta_key) == current_hash:
            logger.debug(f"Retrieval: '{label}' unverändert (Hash-Check) – skip")
            return False

        chunks = _chunk_text(content)
        count = await _upsert_chunks(chunks, virtual_id, source_type, label, collection)

        meta[meta_key] = current_hash
        _save_meta(meta)
        logger.info(f"Retrieval: '{label}' indexiert ({count} Chunks)")
        return True
    except Exception as e:
        logger.error(f"Retrieval: _index_virtual('{label}') fehlgeschlagen: {e}")
        return False


async def _remove_claude_md_from_index() -> None:
    """
    Phase 79: Entfernt veraltete claude.md-Chunks aus ChromaDB.
    Einmalige Bereinigung beim nächsten index_all()-Aufruf.
    """
    collection = _get_collection()
    if collection is None:
        return
    try:
        existing = await asyncio.to_thread(
            collection.get,
            where={"source": "__claude_md__"},
            include=[],
        )
        if existing and existing.get("ids"):
            sem = _get_semaphore()
            async with sem:
                await asyncio.to_thread(collection.delete, ids=existing["ids"])
            # Hash-Eintrag aus Meta entfernen
            meta = _load_meta()
            meta.pop("__hash____claude_md__", None)
            _save_meta(meta)
            logger.info(
                f"Retrieval: {len(existing['ids'])} veraltete claude.md-Chunks aus ChromaDB entfernt (Phase 79)"
            )
    except Exception as e:
        logger.debug(f"Retrieval: claude.md-Cleanup fehlgeschlagen (ignoriert): {e}")


async def remove_file(path: Path) -> None:
    """Entfernt eine Datei aus dem Index."""
    collection = _get_collection()
    if collection is None:
        return
    try:
        filepath = str(path.resolve())
        sem = _get_semaphore()
        async with sem:
            existing = await asyncio.to_thread(
                collection.get,
                where={"source": filepath},
                include=[],
            )
            if existing and existing.get("ids"):
                await asyncio.to_thread(collection.delete, ids=existing["ids"])
        meta = _load_meta()
        meta.pop(filepath, None)
        _save_meta(meta)
        logger.info(f"Retrieval: '{path.name}' aus Index entfernt")
    except Exception as e:
        logger.warning(f"Retrieval: remove_file('{path}') fehlgeschlagen: {e}")


async def index_all(force: bool = False) -> None:
    """
    Vollständige Delta-Indexierung aller Quellen.

    Phase 79: claude.md nicht mehr indexiert.
    Issue #35: Sessions nicht mehr indexiert – direktes Lesen via _load_all_sessions().

    Quellen:
      1. personal_profile.yaml (virtuell, Hash-Check)
      2. ~/Documents/Wissen/*.md (mtime-Check)
    """
    logger.info("Retrieval: Starte Index-Durchlauf...")
    total_updated = 0

    # Einmalige Bereinigungen veralteter Chunks
    await _remove_claude_md_from_index()
    await _remove_sessions_from_index()

    # 1. personal_profile.yaml (virtuell – Hash-Check)
    try:
        from agent.profile import get_profile_context_full

        profile_text = await asyncio.to_thread(get_profile_context_full)
        if profile_text:
            ok = await _index_virtual(profile_text, "__profile__", "profile", "Persönliches Profil", force=force)
            if ok:
                total_updated += 1
    except Exception as e:
        logger.warning(f"Retrieval: Profil-Indexierung fehlgeschlagen: {e}")

    # 2. ~/Documents/Wissen/*.md (Knowledge Notes – mtime-Check)
    if _WISSEN_DIR.exists():
        for path in sorted(_WISSEN_DIR.glob("*.md")):
            ok = await index_file(path, force=force)
            if ok:
                total_updated += 1

    collection = _get_collection()
    total_chunks = collection.count() if collection else 0
    logger.info(
        f"Retrieval: Index-Durchlauf abgeschlossen – {total_updated} Quellen aktualisiert, {total_chunks} Chunks gesamt"
    )


# ---------------------------------------------------------------------------
# Semantische Suche
# ---------------------------------------------------------------------------


async def search(query: str, n_results: int = 3) -> list[dict]:
    """
    Semantische Suche in der Wissensbasis.
    Gibt [] zurück bei Fehler, leerer Collection oder unter Threshold.
    """
    collection = _get_collection()
    if collection is None:
        return []

    try:
        count = collection.count()
        if count == 0:
            return []

        embeddings = await _embed_texts([query])
        if not embeddings:
            return []

        actual_n = min(n_results, count)
        results = await asyncio.to_thread(
            collection.query,
            query_embeddings=embeddings,
            n_results=actual_n,
            include=["documents", "metadatas", "distances"],
        )

        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        output = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if dist > _MAX_DISTANCE:
                continue
            output.append(
                {
                    "document": doc,
                    "label": meta.get("label", "Unbekannt"),
                    "type": meta.get("type", "unknown"),
                    "distance": round(dist, 3),
                }
            )

        return output

    except Exception as e:
        logger.error(f"Retrieval: search() fehlgeschlagen: {e}")
        return []
