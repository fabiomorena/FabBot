"""
Second Brain – Retrieval Engine für FabBot (Phase 77).

ChromaDB (lokal, persistent) + OpenAI text-embedding-3-small.

Indexierte Quellen:
  - personal_profile.yaml  (virtuell, via get_profile_context_full)
  - claude.md              (virtuell, via load_claude_md)
  - ~/Documents/Wissen/*.md        (alle /clip Notizen)
  - ~/Documents/Wissen/Sessions/   (Session Summaries)

Öffentliche API:
  await index_all(force=False)   – Delta-Indexierung aller Quellen (mtime-Check)
  await index_file(path)         – Einzelne Datei indexieren/aktualisieren
  await remove_file(path)        – Datei aus Index entfernen
  await search(query, n=3)       – Semantische Suche → list[dict]

Design:
  - Fail-safe: chromadb nicht installiert → alle Funktionen geben None/[] zurück
  - Delta: nur geänderte Dateien werden re-embedded (mtime-Tracking via JSON)
  - Semaphore: verhindert parallele ChromaDB-Writes
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
_MAX_DISTANCE = 0.7          # cosine – darunter = relevant
_EMBED_BATCH_SIZE = 100      # OpenAI: max 2048 Inputs, wir bleiben konservativ

_WISSEN_DIR = Path.home() / "Documents" / "Wissen"
_SESSIONS_DIR = _WISSEN_DIR / "Sessions"

# Semaphore – verhindert parallele ChromaDB-Writes
_write_semaphore: asyncio.Semaphore | None = None

# ChromaDB Collection – lazy singleton
_collection = None


def _get_semaphore() -> asyncio.Semaphore:
    """Gibt den Write-Semaphore zurück (lazy, event-loop-sicher)."""
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
    Alle Aufrufer prüfen auf None.
    """
    global _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
        _CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
        _collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"ChromaDB Collection '{_COLLECTION_NAME}' bereit "
            f"({_collection.count()} Chunks)"
        )
        return _collection
    except ImportError:
        logger.warning(
            "chromadb nicht installiert – Retrieval deaktiviert. "
            "Installieren: pip install chromadb"
        )
        return None
    except Exception as e:
        logger.error(f"ChromaDB Setup fehlgeschlagen: {e}")
        return None


# ---------------------------------------------------------------------------
# mtime-Tracking für Delta-Indexierung
# ---------------------------------------------------------------------------

def _load_meta() -> dict[str, float]:
    """Lädt gespeicherte mtime-Werte."""
    try:
        if _META_PATH.exists():
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_meta(meta: dict[str, float]) -> None:
    """Speichert mtime-Werte."""
    try:
        _META_PATH.parent.mkdir(parents=True, exist_ok=True)
        _META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Meta-Speicherung fehlgeschlagen: {e}")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str) -> list[str]:
    """
    Teilt Text in Chunks auf.
    Splittet zuerst an Markdown-Headings (##, ###), dann an Absätzen.
    Chunks unter _MIN_CHUNK_CHARS werden verworfen.
    """
    if not text or not text.strip():
        return []

    # An H1-H3 Headings splitten (Zeilen die mit # beginnen)
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
            # Zu groß → an Absätzen weiter splitten
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
    """Stabile, eindeutige ID aus Quell-ID + Chunk-Index."""
    raw = f"{source_id}::{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# OpenAI Embeddings
# ---------------------------------------------------------------------------

async def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """
    Erstellt Embeddings via OpenAI text-embedding-3-small.
    Verarbeitet in Batches von _EMBED_BATCH_SIZE.
    Gibt None bei Fehler zurück (Aufrufer prüft auf None).
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.error("OPENAI_API_KEY nicht gesetzt – Embedding nicht möglich.")
        return None
    if not texts:
        return []

    try:
        import httpx
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i: i + _EMBED_BATCH_SIZE]
            async with httpx.AsyncClient(timeout=60) as client:
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
    """
    Embeddet Chunks und speichert sie in ChromaDB.
    Löscht zuerst alle alten Chunks dieser Quelle (via source_id).
    Gibt Anzahl indexierter Chunks zurück, 0 bei Fehler.
    """
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
        # Alte Chunks dieser Quelle löschen
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

        # Neue Chunks upserten
        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    return len(chunks)


async def index_file(path: Path, force: bool = False) -> bool:
    """
    Indexiert eine einzelne Markdown-Datei (Delta: nur bei geänderter mtime).
    force=True überspringt den mtime-Check.
    Gibt True zurück wenn indexiert wurde, False wenn übersprungen oder Fehler.
    """
    collection = _get_collection()
    if collection is None:
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

        # Source-Type aus Pfad ableiten
        sessions_str = str(_SESSIONS_DIR.resolve())
        wissen_str = str(_WISSEN_DIR.resolve())

        if filepath.startswith(sessions_str):
            source_type = "session"
            label = f"Session: {path.stem}"
        elif filepath.startswith(wissen_str):
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
) -> bool:
    """
    Indexiert virtuellen Inhalt ohne echte Datei (Profil, claude.md).
    Wird bei jedem index_all() neu indexiert (kein mtime-Check nötig).
    """
    collection = _get_collection()
    if collection is None:
        return False
    if not content or not content.strip():
        return False

    try:
        chunks = _chunk_text(content)
        count = await _upsert_chunks(chunks, virtual_id, source_type, label, collection)
        logger.info(f"Retrieval: '{label}' indexiert ({count} Chunks)")
        return True
    except Exception as e:
        logger.error(f"Retrieval: _index_virtual('{label}') fehlgeschlagen: {e}")
        return False


async def remove_file(path: Path) -> None:
    """Entfernt eine Datei aus dem Index (z.B. nach Löschen)."""
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
    Läuft als Background-Task beim Bot-Start.
    force=True re-indexiert alle Dateien unabhängig von mtime.
    """
    logger.info("Retrieval: Starte Index-Durchlauf...")
    total_updated = 0

    # 1. personal_profile.yaml (virtuell – immer neu generieren)
    try:
        from agent.profile import get_profile_context_full
        profile_text = await asyncio.to_thread(get_profile_context_full)
        if profile_text:
            ok = await _index_virtual(profile_text, "__profile__", "profile", "Persönliches Profil")
            if ok:
                total_updated += 1
    except Exception as e:
        logger.warning(f"Retrieval: Profil-Indexierung fehlgeschlagen: {e}")

    # 2. claude.md (virtuell – immer neu laden)
    try:
        from agent.claude_md import load_claude_md
        claude_text = await asyncio.to_thread(load_claude_md)
        if claude_text:
            ok = await _index_virtual(claude_text, "__claude_md__", "claude_md", "Bot-Instruktionen")
            if ok:
                total_updated += 1
    except Exception as e:
        logger.warning(f"Retrieval: claude.md-Indexierung fehlgeschlagen: {e}")

    # 3. ~/Documents/Wissen/*.md (Knowledge Notes)
    if _WISSEN_DIR.exists():
        for path in sorted(_WISSEN_DIR.glob("*.md")):
            ok = await index_file(path, force=force)
            if ok:
                total_updated += 1

    # 4. ~/Documents/Wissen/Sessions/*.md (Session Summaries)
    if _SESSIONS_DIR.exists():
        for path in sorted(_SESSIONS_DIR.glob("????-??-??.md")):
            ok = await index_file(path, force=force)
            if ok:
                total_updated += 1

    collection = _get_collection()
    total_chunks = collection.count() if collection else 0
    logger.info(
        f"Retrieval: Index-Durchlauf abgeschlossen – "
        f"{total_updated} Quellen aktualisiert, {total_chunks} Chunks gesamt"
    )


# ---------------------------------------------------------------------------
# Semantische Suche
# ---------------------------------------------------------------------------

async def search(query: str, n_results: int = 3) -> list[dict]:
    """
    Semantische Suche in der Wissensbasis.

    Gibt list[dict] zurück:
      [{"document": str, "label": str, "type": str, "distance": float}]

    Gibt [] zurück wenn:
      - chromadb nicht installiert
      - Collection leer
      - Keine Ergebnisse unter _MAX_DISTANCE
      - Embedding-Fehler
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
                continue  # Nicht relevant genug
            output.append({
                "document": doc,
                "label": meta.get("label", "Unbekannt"),
                "type": meta.get("type", "unknown"),
                "distance": round(dist, 3),
            })

        return output

    except Exception as e:
        logger.error(f"Retrieval: search() fehlgeschlagen: {e}")
        return []
