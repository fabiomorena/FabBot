"""
Text-to-Speech fuer FabBot – Phase 61.
Provider: ElevenLabs (primär) → edge-tts (Fallback)

ElevenLabs:
- Stimme: Ami (Voice ID via ELEVENLABS_VOICE_ID in .env)
- Modell: eleven_multilingual_v2
- API Key: ELEVENLABS_API_KEY in .env
- Stability: ELEVENLABS_STABILITY (default: 0.5)
- Similarity Boost: ELEVENLABS_SIMILARITY_BOOST (default: 0.75)

edge-tts (Fallback):
- Wird verwendet wenn ELEVENLABS_API_KEY nicht gesetzt oder API-Fehler
- Deutsche Stimme: de-DE-KatjaNeural

TTS kann zur Laufzeit via /tts on|off togglen werden.
Laufende Sprachausgabe kann via /stop gestoppt werden.
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ElevenLabs Konfiguration
ELEVENLABS_API_KEY          = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID         = os.getenv("ELEVENLABS_VOICE_ID", "xTXZTtb6MuzG0jJR05Y9")
ELEVENLABS_MODEL            = "eleven_multilingual_v2"
ELEVENLABS_STABILITY        = float(os.getenv("ELEVENLABS_STABILITY", "0.5"))
ELEVENLABS_SIMILARITY_BOOST = float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.75"))

# edge-tts Fallback
TTS_VOICE = "de-DE-KatjaNeural"
TTS_RATE  = "+0%"

TTS_MAX_CHARS = 1000

# TTS-Status
_tts_enabled: bool = os.getenv("TTS_ENABLED", "true").lower() != "false"

# Laufender afplay-Prozess
_current_afplay: subprocess.Popen | None = None

_SOURCE_HEADERS = {"quellen:", "quellen", "sources:", "sources", "source:"}


def is_tts_enabled() -> bool:
    return _tts_enabled


def set_tts_enabled(enabled: bool) -> None:
    global _tts_enabled
    _tts_enabled = enabled
    logger.info(f"TTS {'aktiviert' if enabled else 'deaktiviert'}.")


def stop_speaking() -> bool:
    global _current_afplay
    if _current_afplay and _current_afplay.poll() is None:
        _current_afplay.terminate()
        _current_afplay = None
        logger.info("Sprachausgabe gestoppt.")
        return True
    return False


def _clean_for_tts(text: str) -> str:
    """Bereinigt Text fuer TTS-Ausgabe."""
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_`]{1,2}", "", text)

    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip().lower()
        if stripped in _SOURCE_HEADERS or stripped.startswith("## quell"):
            break
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    text = re.sub(r'[\U00010000-\U0010ffff\U00002600-\U000027BF\U0001F300-\U0001F9FF]', '', text, flags=re.UNICODE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# ElevenLabs
# ---------------------------------------------------------------------------

async def _synthesize_elevenlabs(text: str) -> bytes | None:
    """Generiert Audio via ElevenLabs API."""
    if not ELEVENLABS_API_KEY:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": ELEVENLABS_MODEL,
                    "voice_settings": {
                        "stability": ELEVENLABS_STABILITY,
                        "similarity_boost": ELEVENLABS_SIMILARITY_BOOST,
                        "style": 0.0,
                        "use_speaker_boost": True,
                    },
                },
            )
            if resp.status_code == 200:
                logger.info(f"ElevenLabs TTS: {len(resp.content)} bytes")
                return resp.content
            else:
                logger.warning(f"ElevenLabs API Fehler: {resp.status_code} – Fallback zu edge-tts")
                return None
    except Exception as e:
        logger.warning(f"ElevenLabs Fehler (Fallback zu edge-tts): {e}")
        return None


# ---------------------------------------------------------------------------
# edge-tts Fallback
# ---------------------------------------------------------------------------

def _is_edge_tts_available() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


# Alias für Test-Kompatibilität (Tests mocken _is_tts_available)
_is_tts_available = _is_edge_tts_available


async def _synthesize_edge_tts(text: str) -> bytes | None:
    """Generiert Audio via edge-tts (Fallback)."""
    if not _is_edge_tts_available():
        logger.warning("edge-tts nicht installiert – TTS nicht verfügbar.")
        return None
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]
        return audio_bytes if audio_bytes else None
    except Exception as e:
        logger.error(f"edge-tts Fehler: {e}")
        return None


# ---------------------------------------------------------------------------
# Haupt-Synthesize
# ---------------------------------------------------------------------------

async def synthesize(text: str) -> bytes | None:
    """Konvertiert Text zu Audio. ElevenLabs primär, edge-tts als Fallback."""
    text = _clean_for_tts(text)
    if not text:
        return None
    if len(text) > TTS_MAX_CHARS:
        logger.info(f"TTS Text auf {TTS_MAX_CHARS} Zeichen gekürzt (original: {len(text)})")
        text = text[:TTS_MAX_CHARS] + "..."

    # ElevenLabs primär
    if ELEVENLABS_API_KEY:
        audio = await _synthesize_elevenlabs(text)
        if audio:
            return audio

    # edge-tts Fallback
    logger.info("TTS Fallback: edge-tts wird verwendet.")
    return await _synthesize_edge_tts(text)


async def speak_and_send(text: str, bot, chat_id: int) -> bool:
    """Spricht Text über Mac-Lautsprecher und schickt Sprachnachricht an Telegram."""
    stop_speaking()
    if not _tts_enabled:
        return False

    audio_bytes = await synthesize(text)
    if not audio_bytes:
        return False

    # ElevenLabs liefert MP3, edge-tts auch
    suffix = ".mp3"

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = Path(f.name)

        await asyncio.gather(
            _play_on_mac(tmp_path),
            _send_voice_telegram(bot, chat_id, audio_bytes),
        )
        return True

    except Exception as e:
        logger.error(f"TTS speak_and_send Fehler: {e}")
        return False
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


async def _play_on_mac(path: Path) -> None:
    """Spielt Audio über Mac-Lautsprecher via afplay."""
    global _current_afplay
    try:
        def _run() -> None:
            global _current_afplay
            _current_afplay = subprocess.Popen(["afplay", str(path)])
            try:
                _current_afplay.wait(timeout=300)
            except subprocess.TimeoutExpired:
                _current_afplay.terminate()
                logger.warning("afplay Timeout nach 300s.")
            finally:
                _current_afplay = None

        await asyncio.to_thread(_run)
    except Exception as e:
        logger.warning(f"afplay Fehler (nicht kritisch): {e}")


async def _send_voice_telegram(bot, chat_id: int, audio_bytes: bytes) -> None:
    """Schickt Audio als Telegram Sprachnachricht."""
    try:
        await bot.send_voice(chat_id=chat_id, voice=audio_bytes)
    except Exception as e:
        logger.warning(f"Telegram send_voice Fehler: {e}")
