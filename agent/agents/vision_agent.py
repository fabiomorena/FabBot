"""
Vision Agent für FabBot.

Analysiert Bilder via Claude Sonnet Vision.
Wird direkt aufgerufen (nicht über LangGraph Graph) um zu vermeiden
dass große base64-Daten durch den SQLite-Checkpointer laufen.

Das Ergebnis wird als __MEMORY__ in den State geschrieben damit
chat_agent darauf antworten kann.
"""

import asyncio
import logging
from langchain_core.messages import HumanMessage, AIMessage

from agent.audit import log_action
from agent.llm import get_llm
from agent.state import AgentState

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
_DEFAULT_MEDIA_TYPE = "image/jpeg"

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


async def analyze_image_direct(
    img_b64: str,
    caption: str,
    media_type: str,
    chat_id: int,
) -> str:
    """
    Analysiert ein Bild direkt via Claude Vision.
    Gibt den Analyse-Text zurück.
    Wird direkt aus on_photo/on_document aufgerufen – nicht über Graph.
    """
    question = caption.strip() if caption.strip() else "Beschreibe dieses Bild detailliert."

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
                            "media_type": media_type,
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
            f"caption='{caption[:80]}' media_type={media_type}",
            chat_id,
            status="executed",
        )
        return result

    except asyncio.TimeoutError:
        logger.error("Vision Agent Timeout nach 60s.")
        log_action("vision_agent", "analyze_image", "timeout", chat_id, status="error")
        return "Timeout bei der Bildanalyse – bitte nochmal versuchen."
    except Exception as e:
        logger.error(f"Vision Agent Fehler: {e}")
        log_action("vision_agent", "analyze_image", f"error: {e}", chat_id, status="error")
        return f"Fehler bei der Bildanalyse: {e}"


async def vision_agent(state: AgentState) -> AgentState:
    """
    LangGraph-Node – wird nicht mehr aktiv genutzt aber bleibt im Graph
    damit der Supervisor weiterhin korrekt routet.
    """
    return {"messages": [AIMessage(content="Bildanalyse nicht verfügbar – bitte Foto direkt senden.")]}
