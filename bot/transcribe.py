import logging
import tempfile

logger = logging.getLogger(__name__)

_model = None
_NO_SPEECH_THRESHOLD = 0.5
_GARBAGE_SCRIPT_RATIO = 0.08  # >8% Nicht-Latein-Zeichen → wahrscheinlich Musik-Halluzination


class NoSpeechDetectedError(Exception):
    pass


def _is_garbage_text(text: str) -> bool:
    """Erkennt Whisper-Halluzinationen: zu viele Nicht-Latein-Zeichen (Sinhala, Burmesisch, etc.)."""
    non_latin = sum(1 for c in text if ord(c) > 0x024F and not c.isspace())
    return non_latin / len(text) > _GARBAGE_SCRIPT_RATIO


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
    Wirft NoSpeechDetectedError wenn Whisper keinen Sprachinhalt erkennt (z.B. Musik).
    """
    try:
        model = _get_model()

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as f:
            f.write(audio_bytes)
            f.flush()
            result = model.transcribe(f.name)

        segments = result.get("segments", [])
        if segments:
            avg_no_speech = sum(s.get("no_speech_prob", 0) for s in segments) / len(segments)
            if avg_no_speech > _NO_SPEECH_THRESHOLD:
                logger.info(f"Whisper: kein Sprachinhalt erkannt (no_speech_prob={avg_no_speech:.2f})")
                raise NoSpeechDetectedError(f"no_speech_prob={avg_no_speech:.2f}")

        text = result.get("text", "").strip()
        if text and _is_garbage_text(text):
            logger.info("Whisper: Halluzinations-Text erkannt (nicht-lateinische Zeichen)")
            raise NoSpeechDetectedError("garbage_script_detected")
        logger.info(f"Whisper lokal transkribiert: {len(text)} Zeichen")
        return text or None

    except NoSpeechDetectedError:
        raise
    except Exception as e:
        logger.error(f"Lokaler Whisper Fehler: {e}")
        return None
