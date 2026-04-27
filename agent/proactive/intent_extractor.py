"""
agent/proactive/intent_extractor.py – Phase 162 (Issue #107)

Intentions-Extraktion: erkennt Commitments/Absichten in User-Nachrichten
und speichert sie als Pending Items in ChromaDB.

Analysiert NUR die User-Nachricht (nicht die Bot-Antwort), da Absichten
oft geäußert werden ohne dass der Bot sie explizit aufgreift.

API: extract_intentions(user_message) – fire-and-forget, fail-safe.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_llm():
    from agent.llm import get_fast_llm
    return get_fast_llm()


def _get_collection():
    from agent.proactive.collector import _get_entities_collection
    return _get_entities_collection()


_INTENT_PROMPT = """Analysiere diese Nachricht auf Absichten, Verpflichtungen oder Pläne des Users.

Heute ist {current_date}.

Nachricht: {user_message}

Suche nach Mustern wie:
- "ich muss/sollte/wollte/will/möchte X [tun]"
- "ich habe X noch nicht gemacht"
- "X muss noch erledigt werden"
- "nächste Woche/morgen/bald/irgendwann X"
- "vergiss nicht X" / "erinnere mich an X"

Gib ein JSON-Array zurück. Jeder Eintrag hat:
- "name": prägnanter Name des Commitments (max. 60 Zeichen)
- "context": Original-Zitat aus der Nachricht (max. 100 Zeichen)
- "due_date": ISO-Datum falls erkennbar ("nächste Woche" → +7 Tage, "morgen" → +1 Tag), sonst weglassen

Nur echte Commitments extrahieren – keine Wünsche oder Hypothesen.
Falls keine Commitments erkennbar: leeres Array [].

Antwort NUR als JSON-Array, kein Text darum."""


def _parse_intentions(raw: str) -> list[dict]:
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if not item.get("name") or not item.get("context"):
                continue
            result.append(item)
        return result
    except (json.JSONDecodeError, ValueError):
        logger.debug(f"Intent-Parse fehlgeschlagen: {raw[:100]}")
        return []


def _intent_id(name: str) -> str:
    key = f"intent:{name.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()


async def extract_intentions(user_message: str) -> None:
    """Extrahiert Commitments aus der User-Nachricht und speichert sie in ChromaDB.

    Fire-and-forget – Fehler werden geloggt, nie weitergereicht.
    """
    if not user_message.strip():
        return

    try:
        from langchain_core.messages import HumanMessage

        llm = _get_llm()
        prompt = _INTENT_PROMPT.format(
            current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            user_message=user_message[:500],
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content if isinstance(response.content, str) else str(response.content)
        intentions = _parse_intentions(raw)
        if not intentions:
            return

        collection = _get_collection()
        if collection is None:
            return

        now = datetime.now(timezone.utc).isoformat()
        ids, documents, metadatas = [], [], []

        for intent in intentions:
            eid = _intent_id(intent["name"])

            try:
                existing = collection.get(ids=[eid])
                mention_count = int(existing["metadatas"][0].get("mention_count", 0)) + 1 \
                    if existing["ids"] else 1
            except Exception:
                mention_count = 1

            metadata = {
                "entity_type": "intent",
                "name": intent["name"],
                "status": "open",
                "created_at": now,
                "last_mentioned_at": now,
                "mention_count": mention_count,
                "source_context": intent["context"][:200],
            }
            if intent.get("due_date"):
                metadata["due_date"] = intent["due_date"]

            ids.append(eid)
            documents.append(intent["context"])
            metadatas.append(metadata)

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        logger.info(f"Intent Extractor: {len(intentions)} Absicht(en) gespeichert")

    except Exception as e:
        logger.warning(f"Intent Extractor Fehler (non-critical): {e}")
