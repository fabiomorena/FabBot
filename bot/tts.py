"""
Text-to-Speech fuer FabBot – Phase 70.
Provider: OpenAI TTS (primär) → edge-tts (Fallback)

Phase 70 Fixes:
- _validate_tts_config() – Validierung ausgelagert, wird in main.py aufgerufen
- Spezifischerer Log bei Retry-Erschöpfung vs. echtem API-Fehler
- _get_tts_voice() + _get_tts_model() lazy getters (konsistent mit _get_openai_api_key)
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# edge-tts Fallback
TTS_VOICE = "de-DE-KatjaNeural"
TTS_RATE  = "+0%"

TTS_MAX_CHARS = 1000

# TTS-Status – None = noch nicht gelesen (lazy), bool = explizit gesetzt via set_tts_enabled()
_tts_enabled: bool | None = None

# Laufender afplay-Prozess
_current_afplay: subprocess.Popen | None = None

_SOURCE_HEADERS = {"quellen:", "quellen", "sources:", "sources", "source:"}

# ---------------------------------------------------------------------------
# Konfiguration – lazy getters + Validierung
# ---------------------------------------------------------------------------

_VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
_VALID_MODELS = {"tts-1", "tts-1-hd"}

# Öffentlich für externe Lesbarkeit/Tests – intern immer _get_tts_voice()/_get_tts_model() verwenden
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "nova")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "tts-1")

_TTS_RETRY_STATUS = {429, 503}
_TTS_RETRY_DELAY  = 0.5


def _get_openai_api_key() -> str:
    """Lazy read – Key-Rotation ohne Neustart moeglich."""
    return os.getenv("OPENAI_API_KEY", "")


def _get_tts_voice() -> str:
    """Lazy read von OPENAI_TTS_VOICE – konsistent mit _get_openai_api_key."""
    return os.getenv("OPENAI_TTS_VOICE", "nova")


def _get_tts_model() -> str:
    """Lazy read von OPENAI_TTS_MODEL – konsistent mit _get_openai_api_key."""
    return os.getenv("OPENAI_TTS_MODEL", "tts-1")


def _validate_tts_config() -> None:
    """
    Prueft TTS-Konfiguration und loggt Warnings bei ungültigen Werten.

    Phase 70 Fix: ausgelagert aus Modul-Level – wird in main.py aufgerufen
    NACHDEM logging.basicConfig() konfiguriert ist, damit Warnings sichtbar sind.
    """
    voice = _get_tts_voice()
    model = _get_tts_model()
    if voice not in _VALID_VOICES:
        logger.warning(
            f"Unbekannte OPENAI_TTS_VOICE: {voice!r} – "
            f"erlaubte Werte: {sorted(_VALID_VOICES)}"
        )
    if model not in _VALID_MODELS:
        logger.warning(
            f"Unbekanntes OPENAI_TTS_MODEL: {model!r} – "
            f"erlaubte Werte: {sorted(_VALID_MODELS)}"
        )


def is_tts_enabled() -> bool:
    if _tts_enabled is None:
        return os.getenv("TTS_ENABLED", "true").lower() != "false"
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
# OpenAI TTS
# ---------------------------------------------------------------------------

async def _synthesize_openai(text: str) -> bytes | None:
    """
    Generiert Audio via OpenAI TTS API.
    1 Retry bei 429/503 mit _TTS_RETRY_DELAY Sekunden Backoff.

    Phase 70 Fix: spezifischerer Log bei Retry-Erschoepfung vs. echtem Fehler.
    """
    api_key = _get_openai_api_key()
    if not api_key:
        return None

    voice = _get_tts_voice()
    model = _get_tts_model()

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(2):
                resp = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": model, "input": text, "voice": voice},
                )

                if resp.status_code == 200:
                    logger.info(
                        f"OpenAI TTS: {len(resp.content)} bytes, "
                        f"voice={voice}, model={model}"
                    )
                    return resp.content

                if resp.status_code in _TTS_RETRY_STATUS and attempt == 0:
                    logger.warning(
                        f"OpenAI TTS {resp.status_code} – "
                        f"Retry in {_TTS_RETRY_DELAY}s..."
                    )
                    await asyncio.sleep(_TTS_RETRY_DELAY)
                    continue

                # Phase 70: unterscheide Retry-Erschoepfung von echtem Fehler
                if resp.status_code in _TTS_RETRY_STATUS:
                    logger.warning(
                        f"OpenAI TTS: Retry erschoepft nach {resp.status_code} – "
                        f"Fallback zu edge-tts"
                    )
                else:
                    logger.warning(
                        f"OpenAI TTS API Fehler: {resp.status_code} – "
                        f"Fallback zu edge-tts"
                    )
                return None

    except Exception as e:
        logger.warning(f"OpenAI TTS Fehler (Fallback zu edge-tts): {e}")
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


_is_tts_available = _is_edge_tts_available


async def _synthesize_edge_tts(text: str) -> bytes | None:
    if not _is_edge_tts_available():
        logger.warning("edge-tts nicht installiert.")
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
    text = _clean_for_tts(text)
    if not text:
        return None
    if len(text) > TTS_MAX_CHARS:
        logger.info(f"TTS Text auf {TTS_MAX_CHARS} Zeichen gekürzt (original: {len(text)})")
        text = text[:TTS_MAX_CHARS] + "..."

    if _get_openai_api_key():
        audio = await _synthesize_openai(text)
        if audio:
            return audio

    logger.info("TTS Fallback: edge-tts wird verwendet.")
    return await _synthesize_edge_tts(text)


async def speak_and_send(text: str, bot, chat_id: int) -> bool:
    stop_speaking()
    if not _tts_enabled:
        return False

    audio_bytes = await synthesize(text)
    if not audio_bytes:
        return False

    suffix = ".mp3"
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = Path(f.name)

        results = await asyncio.gather(
            _play_on_mac(tmp_path),
            _send_voice_telegram(bot, chat_id, audio_bytes),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"speak_and_send Task-Fehler (ignoriert): {r}")
        return True

    except Exception as e:
        logger.error(f"TTS speak_and_send Fehler: {e}")
        return False
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


async def _play_on_mac(path: Path) -> None:
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
    try:
        await bot.send_voice(chat_id=chat_id, voice=audio_bytes)
    except Exception as e:
        logger.warning(f"Telegram send_voice Fehler: {e}")
