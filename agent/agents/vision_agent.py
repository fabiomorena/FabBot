"""
Vision Agent für FabBot.

Analysiert Bilder via Claude Sonnet Vision:
- Objekterkennung
- Texterkennung (OCR)
- Szenenbeschreibung
- Freie Fragen via Caption

Security:
- Keine Identifikation von Privatpersonen
- Audit Log (nur Metadaten, kein Bild)
- Rate Limiting via security.py (bereits aktiv)
"""

import base64
import logging
from langchain_core.messages import HumanMessage

from agent.audit import log_action
from agent.llm import get_llm

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB

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


async def analyze_image(
    image_bytes: bytes,
    caption: str,
    chat_id: int,
    media_type: str = "image/jpeg",
) -> str:
    """
    Analysiert ein Bild via Claude Sonnet Vision.
    Gibt einen Beschreibungstext zurück.
    """
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return f"Bild zu groß (max. 5 MB, erhalten: {len(image_bytes) // 1024} KB)."

    try:
        img_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        llm = get_llm()

        question = caption.strip() if caption.strip() else "Beschreibe dieses Bild detailliert."

        response = await llm.ainvoke([
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
        ])

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
            f"size={len(image_bytes)}b caption='{caption[:80]}'",
            chat_id,
            status="executed",
        )
        return result

    except Exception as e:
        logger.error(f"Vision Agent Fehler: {e}")
        log_action("vision_agent", "analyze_image", f"error: {e}", chat_id, status="error")
        return f"Fehler bei der Bildanalyse: {e}"
