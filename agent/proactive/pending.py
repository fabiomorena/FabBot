"""
agent/proactive/pending.py – Phase 141 (Issue #88)

Pending Items Tracker: liest offene Entitäten aus ChromaDB,
berechnet Prioritätsscore und gibt sortierte Liste zurück.

API: get_pending_items(limit=10) → list[dict]
"""

import logging
from datetime import datetime, timezone

from agent.proactive.collector import _get_entities_collection

logger = logging.getLogger(__name__)

_TYPE_SCORE: dict[str, int] = {
    "task": 20,
    "event": 18,
    "intent": 15,
    "person": 10,
    "place": 5,
}


def _is_stale(due_date_str: str | None, max_overdue_days: int = 1) -> bool:
    """True wenn due_date mehr als max_overdue_days Tage in der Vergangenheit liegt."""
    if not due_date_str:
        return False
    try:
        due = datetime.strptime(due_date_str[:10], "%Y-%m-%d").date()
        days_overdue = (datetime.now(timezone.utc).date() - due).days
        return days_overdue > max_overdue_days
    except (ValueError, TypeError):
        return False


def _due_date_score(due_date_str: str | None) -> int:
    if not due_date_str:
        return 0
    try:
        due = datetime.strptime(due_date_str[:10], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        days = (due - today).days
        if days < 0:
            return 50
        if days == 0:
            return 48
        if days <= 3:
            return 40
        if days <= 7:
            return 30
        if days <= 30:
            return 20
        if days <= 90:
            return 10
        return 5
    except (ValueError, TypeError):
        return 0


def _mention_score(mention_count: int) -> int:
    return min(mention_count * 5, 30)


def _priority_score(metadata: dict) -> int:
    return (
        _due_date_score(metadata.get("due_date"))
        + _mention_score(int(metadata.get("mention_count", 1)))
        + _TYPE_SCORE.get(metadata.get("entity_type", ""), 0)
    )


def mark_done(name_query: str) -> list[str]:
    """Markiert alle offenen Entitäten deren Name name_query enthält als 'done'.

    Matching: case-insensitiv, partial. Gibt Liste der gematchten Namen zurück.
    """
    collection = _get_entities_collection()
    if collection is None:
        return []
    try:
        result = collection.get(
            where={"status": "open"},
            include=["metadatas"],
        )
    except Exception as e:
        logger.warning(f"mark_done: ChromaDB-Fehler: {e}")
        return []

    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    query = name_query.lower()
    matched_names = []

    for eid, meta in zip(ids, metadatas):
        if query not in meta.get("name", "").lower():
            continue
        updated = {**meta, "status": "done"}
        try:
            collection.update(ids=[eid], metadatas=[updated])
            matched_names.append(meta["name"])
        except Exception as e:
            logger.warning(f"mark_done: Update-Fehler für '{meta.get('name')}': {e}")

    return matched_names


def get_pending_items(limit: int = 10) -> list[dict]:
    """Gibt offene Entitäten sortiert nach Prioritätsscore zurück."""
    collection = _get_entities_collection()
    if collection is None:
        return []

    try:
        result = collection.get(
            where={"status": "open"},
            include=["metadatas", "documents"],
        )
    except Exception as e:
        logger.warning(f"Pending Items: ChromaDB-Fehler: {e}")
        return []

    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    documents = result.get("documents") or []

    items = []
    for i, meta in enumerate(metadatas):
        if _is_stale(meta.get("due_date")):
            continue
        item = dict(meta)
        item["id"] = ids[i] if i < len(ids) else ""
        item["document"] = documents[i] if i < len(documents) else ""
        item["priority_score"] = _priority_score(meta)
        items.append(item)

    items.sort(key=lambda x: x["priority_score"], reverse=True)
    return items[:limit]
