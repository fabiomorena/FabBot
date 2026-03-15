import logging
import tempfile

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lädt das Whisper-Modell einmalig beim ersten Aufruf (lazy loading)."""
    global _model
    if _model is None:
        import whisper
        logger.info("Lade Whisper-Modell 'small'...")
        _model = whisper.load_model("small")
        logger.info("Whisper-Modell geladen.")
    return _model


async def transcribe_audio(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Transkribiert Audio-Bytes lokal via Whisper.
    Gibt transkribierten Text zurück, oder None bei Fehler.
    """
    try:
        model = _get_model()

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as f:
            f.write(audio_bytes)
            f.flush()
            result = model.transcribe(f.name)

        text = result.get("text", "").strip()
        logger.info(f"Whisper lokal transkribiert: {len(text)} Zeichen")
        return text or None

    except Exception as e:
        logger.error(f"Lokaler Whisper Fehler: {e}")
        return None