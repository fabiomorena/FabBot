"""
agent/proactive/relationship_alert.py – Phase 183 (Issue #108)

Beziehungs-Alert: findet Entitäten die zu lange nicht mehr erwähnt wurden
und gibt sie als Trigger-Items für den Heartbeat zurück.

Schwellwerte:
  person  → 14 Tage ohne Erwähnung
  andere  → 30 Tage ohne Erwähnung

Anti-Spam: pro Entität nicht häufiger als alle 7 Tage alarmieren.
Backfill: bestehende Einträge ohne last_mentioned_at_ts werden beim ersten
          Aufruf einmalig migriert (Marker-Datei verhindert Wiederholung).

API:
  find_unmentioned_entities(now_ts) → list[dict]
  mark_alerted(entity_id) → None
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PERSON_THRESHOLD_DAYS = 14
OTHER_THRESHOLD_DAYS = 30
ALERT_COOLDOWN_DAYS = 7
MAX_ALERTS_PER_RUN = 3
MIN_MENTION_COUNT = 2

_BACKFILL_MARKER = Path.home() / ".fabbot" / ".relationship_alert_backfill_done"
_OTHER_TYPES = ("place", "event", "task")


def _get_entities_collection():
    from agent.proactive.collector import _get_entities_collection as _get_col

    return _get_col()


def _backfill_missing_timestamps(collection) -> int:
    """Ergänzt last_mentioned_at_ts für Einträge ohne dieses Feld.

    Parst den vorhandenen ISO-String last_mentioned_at und schreibt den
    entsprechenden Unix-Float zurück. Nötig für Einträge die vor Phase 183
    angelegt wurden.
    """
    try:
        result = collection.get(include=["metadatas"])
        if not result["ids"]:
            return 0
        ids_to_update, metas_to_update = [], []
        for eid, meta in zip(result["ids"], result["metadatas"]):
            if "last_mentioned_at_ts" in meta:
                continue
            iso = meta.get("last_mentioned_at", "")
            if not iso:
                continue
            try:
                dt = datetime.fromisoformat(iso)
                ts = dt.timestamp()
            except (ValueError, OSError):
                continue
            ids_to_update.append(eid)
            metas_to_update.append({**meta, "last_mentioned_at_ts": ts})
        if not ids_to_update:
            return 0
        collection.update(ids=ids_to_update, metadatas=metas_to_update)
        logger.info(f"Backfill: {len(ids_to_update)} Entität(en) mit last_mentioned_at_ts ergänzt")
        return len(ids_to_update)
    except Exception as e:
        logger.warning(f"Backfill fehlgeschlagen (non-critical): {e}")
        return 0


def _run_backfill_once(collection) -> None:
    if _BACKFILL_MARKER.exists():
        return
    count = _backfill_missing_timestamps(collection)
    try:
        _BACKFILL_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _BACKFILL_MARKER.touch()
    except Exception:
        pass
    if count:
        logger.info(f"Einmaliger Backfill abgeschlossen: {count} Einträge migriert")


def _should_alert(meta: dict, now_ts: float) -> bool:
    """True wenn kein oder veralteter Alert-Marker (>= ALERT_COOLDOWN_DAYS)."""
    last_alerted = meta.get("last_alerted_at_ts")
    if not last_alerted:
        return True
    try:
        return (now_ts - float(last_alerted)) >= ALERT_COOLDOWN_DAYS * 86400
    except (TypeError, ValueError):
        return True


def _query_unmentioned(collection, entity_type: str, cutoff_ts: float, now_ts: float) -> list[dict]:
    """Fragt ChromaDB nach Entitäten eines Typs die vor cutoff_ts zuletzt erwähnt wurden."""
    try:
        result = collection.get(
            where={
                "$and": [
                    {"entity_type": {"$eq": entity_type}},
                    {"last_mentioned_at_ts": {"$lt": cutoff_ts}},
                    {"mention_count": {"$gte": MIN_MENTION_COUNT}},
                ]
            },
            include=["metadatas"],
        )
    except Exception as e:
        logger.warning(f"ChromaDB-Query für {entity_type} fehlgeschlagen: {e}")
        return []
    items = []
    for eid, meta in zip(result.get("ids", []), result.get("metadatas", [])):
        if not _should_alert(meta, now_ts):
            continue
        days = int((now_ts - float(meta["last_mentioned_at_ts"])) / 86400)
        items.append(
            {
                "id": eid,
                "name": meta.get("name", ""),
                "entity_type": entity_type,
                "source_context": meta.get("source_context", ""),
                "last_mentioned_at": meta.get("last_mentioned_at", ""),
                "days_since_mention": days,
                "trigger_type": "relationship_alert",
            }
        )
    return items


def find_unmentioned_entities(now_ts: float | None = None) -> list[dict]:
    """Findet Entitäten die zu lange nicht erwähnt wurden.

    Gibt max. MAX_ALERTS_PER_RUN Items zurück, sortiert nach Tagen (längste zuerst).
    Fail-safe: bei Fehler wird [] zurückgegeben.
    """
    try:
        collection = _get_entities_collection()
        if collection is None:
            return []
        if now_ts is None:
            now_ts = datetime.now(timezone.utc).timestamp()
        _run_backfill_once(collection)
        person_cutoff = now_ts - PERSON_THRESHOLD_DAYS * 86400
        other_cutoff = now_ts - OTHER_THRESHOLD_DAYS * 86400
        alerts: list[dict] = []
        alerts.extend(_query_unmentioned(collection, "person", person_cutoff, now_ts))
        for etype in _OTHER_TYPES:
            alerts.extend(_query_unmentioned(collection, etype, other_cutoff, now_ts))
        alerts.sort(key=lambda x: x["days_since_mention"], reverse=True)
        return alerts[:MAX_ALERTS_PER_RUN]
    except Exception as e:
        logger.warning(f"find_unmentioned_entities Fehler (non-critical): {e}")
        return []


def mark_alerted(entity_id: str) -> None:
    """Setzt last_alerted_at_ts auf jetzt (Anti-Spam für 7 Tage)."""
    try:
        collection = _get_entities_collection()
        if collection is None:
            return
        existing = collection.get(ids=[entity_id], include=["metadatas"])
        if not existing["ids"]:
            return
        now_ts = datetime.now(timezone.utc).timestamp()
        meta = {**existing["metadatas"][0], "last_alerted_at_ts": now_ts}
        collection.update(ids=[entity_id], metadatas=[meta])
    except Exception as e:
        logger.warning(f"mark_alerted Fehler (non-critical): {e}")
