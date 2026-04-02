"""
Vision Agent für FabBot.

Analysiert Bilder via Claude Sonnet Vision als vollständiger LangGraph-Node.
Wird vom Supervisor geroutet wie alle anderen Agents.

Der on_photo Handler in bot/bot.py legt das Bild als base64 in den State
(state["image_data"]) und setzt eine HumanMessage mit [FOTO]-Prefix.
Der Supervisor erkennt den Prefix und routet zu vision_agent.

Security:
- Keine Identifikation von Privatpersonen
- Audit Log (nur Metadaten, kein Bild)
- Rate Limiting via security.py (bereits aktiv)
- Caption-Sanitization im on_photo Handler
"""

import asyncio
import logging
from langchain_core.messages import HumanMessage, AIMessage

from agent.audit import log_action
from agent.llm import get_llm
from agent.state import AgentState

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
_MEDIA_TYPE = "image/jpeg"

VISION_SYSTEM_PROMPT = """Du bist ein präziser Bild-Analyse-Assistent.

Analysiere das Bild und beantworte die Frage des Users.

Deine Fähigkeiten:
- Objekte, Szenen und Umgebungen beschreiben
- Text im Bild lesen und transkribieren (OCR)
- Farben, Formen, Positionen beschreiben
- Technische Geräte, Schilder, Dokumente analysieren

Wichtige Einschränkungen:
- Identifiziere KEINE Privatpersonen namentlich
- Spekuliere NICHT über Identitäten von Menschen
- Beschreibe Menschen nur allgemein (z.B. "eine Person mit roter Jacke")
- Bei unlesbarem Text: explizit sagen was unklar ist

Antworte auf Deutsch, präzise und strukturiert.
"""

_FOTO_PREFIX = "[FOTO]"


async def vision_agent(state: AgentState) -> AgentState:
    """
    LangGraph-Node für Bildanalyse via Claude Sonnet Vision.
    Liest image_data (base64) und image_caption aus dem State.
    Gibt eine normale AIMessage zurück – kein HITL-Prefix nötig.
    """
    chat_id = state.get("telegram_chat_id")
    img_b64 = state.get("image_data")

    if not img_b64:
        return {"messages": [AIMessage(content="Kein Bild im State gefunden.")]}

    # Caption aus letzter HumanMessage extrahieren
    caption = state.get("image_caption") or ""
    if not caption:
        human_msgs = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        if human_msgs:
            raw = human_msgs[-1].content
            if isinstance(raw, str) and raw.startswith(_FOTO_PREFIX):
                caption = raw[len(_FOTO_PREFIX):].strip()

    question = caption if caption else "Beschreibe dieses Bild detailliert."

    try:
        llm = get_llm()
        response = await asyncio.wait_for(
            llm.ainvoke([
                HumanMessage(content=[
                    {
                        "type": "text",
                        "text": VISION_SYSTEM_PROMPT + f"\n\nFrage: {question}",
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _MEDIA_TYPE,
                            "data": img_b64,
                        },
                    },
                ])
            ]),
            timeout=60,
        )

        content = response.content
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )

        result = content.strip()
        log_action(
            "vision_agent",
            "analyze_image",
            f"caption='{caption[:80]}'",
            chat_id,
            status="executed",
        )
        return {"messages": [AIMessage(content=result)]}

    except asyncio.TimeoutError:
        logger.error("Vision Agent Timeout nach 60s.")
        log_action("vision_agent", "analyze_image", "timeout after 60s", chat_id, status="error")
        return {"messages": [AIMessage(content="Timeout bei der Bildanalyse – bitte nochmal versuchen.")]}
    except Exception as e:
        logger.error(f"Vision Agent Fehler: {e}")
        log_action("vision_agent", "analyze_image", f"error: {e}", chat_id, status="error")
        return {"messages": [AIMessage(content=f"Fehler bei der Bildanalyse: {e}")]}
