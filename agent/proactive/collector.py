"""
agent/proactive/collector.py – Phase 140 (Issue #87)

Context Collector: extrahiert strukturierte Entitäten aus Gesprächen
und persistiert sie in ChromaDB Collection 'entities'.

Wird async fire-and-forget nach jeder Bot-Antwort aufgerufen.
Fehler crashen den Bot nicht – vollständig fail-safe.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ENTITY_TYPES = {"person", "place", "event", "intent", "task"}

_CHROMA_PATH = Path.home() / ".fabbot" / "chroma"
_ENTITIES_COLLECTION = "entities"

_EXTRACTION_PROMPT = """Analysiere die folgende Konversation und extrahiere strukturierte Entitäten.

Heute ist {current_date}.

Konversation:
User: {user_message}
Bot: {bot_response}

Extrahiere alle relevanten Entitäten als JSON-Array. Jede Entität hat:
- "type": einer von ["person", "place", "event", "intent", "task"]
- "name": kanonischer Name (z.B. "Steffi", "Salvador", "70. Geburtstag")
- "context": kurzer Kontext-Satz aus der Konversation (max. 100 Zeichen)
- "due_date": ISO-Datum falls erkennbar (z.B. "2026-05-31"), sonst weglassen

Nur Entitäten mit klarer semantischer Bedeutung extrahieren.
Keine trivialen Entitäten (z.B. "Bot", "Antwort").
Verwandtschaftsbeziehungen wie "Steffis Vater" oder "der Bruder von X" nur extrahieren wenn ein eigenständiger Name bekannt ist – sonst weglassen.
Falls keine relevanten Entitäten vorhanden: leeres Array [].

Antwort NUR als JSON-Array, kein Text darum."""

_entities_collection = None


def _get_llm():
    from agent.llm import get_fast_llm
    return get_fast_llm()


def _get_entities_collection():
    global _entities_collection
    if _entities_collection is not None:
        return _entities_collection
    try:
        import chromadb
        _CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
        _entities_collection = client.get_or_create_collection(
            name=_ENTITIES_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"Entities Collection bereit ({_entities_collection.count()} Einträge)")
        return _entities_collection
    except Exception as e:
        logger.warning(f"Entities Collection nicht verfügbar: {e}")
        return None


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _entity_id(entity_type: str, normalized_name: str) -> str:
    key = f"{entity_type}:{normalized_name}"
    return hashlib.sha256(key.encode()).hexdigest()


def _parse_entities(raw: str) -> list[dict]:
    # Markdown-Codeblock entfernen falls vorhanden
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in ENTITY_TYPES:
                continue
            if not item.get("name") or not item.get("context"):
                continue
            result.append(item)
        return result
    except (json.JSONDecodeError, ValueError):
        logger.debug(f"Entity-Parse fehlgeschlagen: {raw[:100]}")
        return []


async def collect_entities(user_message: str, bot_response: str) -> None:
    """Extrahiert Entitäten aus der Konversation und speichert sie in ChromaDB.

    Fire-and-forget – Fehler werden geloggt, nie weitergereicht.
    """
    if not user_message.strip() and not bot_response.strip():
        return

    try:
        llm = _get_llm()
        from langchain_core.messages import HumanMessage
        prompt = _EXTRACTION_PROMPT.format(
            current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            user_message=user_message[:500],
            bot_response=bot_response[:500],
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content if isinstance(response.content, str) else str(response.content)
        entities = _parse_entities(raw)
        if not entities:
            return

        collection = _get_entities_collection()
        if collection is None:
            return

        now = datetime.now(timezone.utc).isoformat()
        ids, documents, metadatas = [], [], []

        for entity in entities:
            normalized = _normalize_name(entity["name"])
            eid = _entity_id(entity["type"], normalized)

            # Existing entry check for mention_count
            try:
                existing = collection.get(ids=[eid])
                mention_count = int(existing["metadatas"][0].get("mention_count", 0)) + 1 \
                    if existing["ids"] else 1
            except Exception:
                mention_count = 1

            metadata = {
                "entity_type": entity["type"],
                "name": entity["name"],
                "status": "open",
                "created_at": now,
                "last_mentioned_at": now,
                "mention_count": mention_count,
                "source_context": entity["context"][:200],
            }
            if entity.get("due_date"):
                metadata["due_date"] = entity["due_date"]

            ids.append(eid)
            documents.append(entity["context"])
            metadatas.append(metadata)

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        logger.info(f"Context Collector: {len(entities)} Entität(en) gespeichert")

        if len(ids) >= 2:
            from agent.proactive.linker import link_entities
            entity_names = {eid: m["name"] for eid, m in zip(ids, metadatas)}
            link_entities(ids, entity_names)

    except Exception as e:
        logger.warning(f"Context Collector Fehler (non-critical): {e}")
