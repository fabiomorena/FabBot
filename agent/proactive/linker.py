"""
agent/proactive/linker.py – Phase 143 (Issue #90)

Context Linking: verknüpft gemeinsam erwähnte Entitäten in ChromaDB.
Gewicht (Häufigkeit gemeinsamer Erwähnung) wird pro Paar inkrementiert.

API:
  link_entities(entity_ids, entity_names) → None
  get_related_entities(entity_id, limit=5) → list[dict]
"""

import hashlib
import logging
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from agent.proactive.collector import _get_entities_collection

logger = logging.getLogger(__name__)

_CHROMA_PATH = Path.home() / ".fabbot" / "chroma"
_LINKS_COLLECTION = "entity_links"

_links_collection = None


def _link_id(id_a: str, id_b: str) -> str:
    pair = ":".join(sorted([id_a, id_b]))
    return hashlib.sha256(pair.encode()).hexdigest()


def _get_links_collection():
    global _links_collection
    if _links_collection is not None:
        return _links_collection
    try:
        import chromadb
        _CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
        _links_collection = client.get_or_create_collection(
            name=_LINKS_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        return _links_collection
    except Exception as e:
        logger.warning(f"Links Collection nicht verfügbar: {e}")
        return None


def link_entities(entity_ids: list[str], entity_names: dict[str, str]) -> None:
    """Verknüpft alle Paare aus entity_ids, inkrementiert Gewicht bei Wiederholung."""
    if len(entity_ids) < 2:
        return
    collection = _get_links_collection()
    if collection is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        for id_a, id_b in combinations(entity_ids, 2):
            lid = _link_id(id_a, id_b)
            try:
                existing = collection.get(ids=[lid])
                weight = int(existing["metadatas"][0].get("weight", 0)) + 1 \
                    if existing["ids"] else 1
            except Exception:
                weight = 1
            name_a = entity_names.get(id_a, id_a)
            name_b = entity_names.get(id_b, id_b)
            collection.upsert(
                ids=[lid],
                documents=[f"{name_a} ↔ {name_b}"],
                metadatas=[{
                    "entity_id_a": id_a,
                    "entity_id_b": id_b,
                    "weight": weight,
                    "last_seen_at": now,
                }],
            )
    except Exception as e:
        logger.warning(f"Context Linking Fehler: {e}")


def get_related_entities(entity_id: str, limit: int = 5) -> list[dict]:
    """Gibt mit entity_id verknüpfte Entitäten sortiert nach Linkgewicht zurück."""
    links_col = _get_links_collection()
    entities_col = _get_entities_collection()
    if links_col is None or entities_col is None:
        return []
    try:
        result = links_col.get(
            where={"$or": [
                {"entity_id_a": {"$eq": entity_id}},
                {"entity_id_b": {"$eq": entity_id}},
            ]},
            include=["metadatas"],
        )
    except Exception as e:
        logger.warning(f"get_related_entities Fehler: {e}")
        return []

    link_metas = result.get("metadatas") or []
    if not link_metas:
        return []

    sorted_links = sorted(link_metas, key=lambda x: int(x.get("weight", 1)), reverse=True)

    other_ids = []
    weights: dict[str, int] = {}
    for link in sorted_links[:limit]:
        other_id = link["entity_id_b"] if link["entity_id_a"] == entity_id else link["entity_id_a"]
        other_ids.append(other_id)
        weights[other_id] = int(link.get("weight", 1))

    if not other_ids:
        return []

    try:
        entities_result = entities_col.get(ids=other_ids, include=["metadatas"])
        ids_out = entities_result.get("ids") or []
        metas_out = entities_result.get("metadatas") or []
        items = []
        for i, meta in enumerate(metas_out):
            item = dict(meta)
            item["id"] = ids_out[i] if i < len(ids_out) else ""
            item["link_weight"] = weights.get(item["id"], 1)
            items.append(item)
        return sorted(items, key=lambda x: x["link_weight"], reverse=True)
    except Exception as e:
        logger.warning(f"get_related_entities Entity-Fetch Fehler: {e}")
        return []
