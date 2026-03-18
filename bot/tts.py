"""
Text-to-Speech fuer FabBot.
Kombiniert:
1. edge-tts → MP3-Datei generieren (Microsoft Neural Voices, kein API-Key)
2. afplay   → sofortige Ausgabe ueber Mac-Lautsprecher
3. Telegram → Sprachnachricht zurueckschicken

Deutsche Stimme: de-DE-KatjaNeural (weiblich, natuerlich)
Alternativ:      de-DE-ConradNeural (maennlich)
"""
import asyncio
import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Deutsche Neural-Stimme – beste Qualitaet fuer Deutsch
TTS_VOICE = "de-DE-KatjaNeural"
# Sprechgeschwindigkeit: +0% = normal, +10% = etwas schneller
TTS_RATE = "+0%"

# Texte ueber dieser Laenge werden nicht vorgelesen (zu lang fuer TTS)
TTS_MAX_CHARS = 1000


def _clean_for_tts(text: str) -> str:
    """Bereinigt Text fuer TTS-Ausgabe.
    Entfernt URLs, Markdown-Formatierung und Quellenangaben
    damit der Bot keine URLs vorliest.
    """
    # Markdown-Links [Text](URL) → nur Text behalten
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)

    # URLs entfernen (http/https)
    text = re.sub(r"https?://\S+", "", text)

    # Markdown-Formatierung entfernen: **, *, __, _, `
    text = re.sub(r"[*_`]{1,2}", "", text)

    # Quellenabschnitt erkennen und alles danach entfernen
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith(("quelle", "quellen:", "source", "quellen")):
            break  # Ab hier alles weglassen
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # Mehrfache Leerzeilen reduzieren
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _is_tts_available() -> bool:
    """Prueft ob edge-tts installiert ist."""
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


async def synthesize(text: str) -> bytes | None:
    """Konvertiert Text zu MP3-Audio via edge-tts.
    Bereinigt den Text vor der Synthese (keine URLs, kein Markdown).
    Gibt Audio-Bytes zurueck oder None bei Fehler.
    """
    if not _is_tts_available():
        logger.warning("edge-tts nicht installiert – TTS deaktiviert.")
        return None

    # Text bereinigen bevor er vorgelesen wird
    text = _clean_for_tts(text)

    if not text:
        return None

    if len(text) > TTS_MAX_CHARS:
        text = text[:TTS_MAX_CHARS] + "..."

    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)

        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]

        return audio_bytes if audio_bytes else None

    except Exception as e:
        logger.error(f"TTS Synthese-Fehler: {e}")
        return None


async def speak_and_send(text: str, bot, chat_id: int) -> bool:
    """
    Spricht Text gleichzeitig ueber Mac-Lautsprecher und
    schickt Sprachnachricht an Telegram.
    Returns True wenn erfolgreich, False bei Fehler.
    """
    audio_bytes = await synthesize(text)
    if not audio_bytes:
        return False

    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = Path(f.name)

        # Parallel: Mac-Lautsprecher + Telegram
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
    """Spielt Audio ueber Mac-Lautsprecher via afplay (macOS-nativ, kein Paket noetig)."""
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["afplay", str(path)],
            timeout=60,
            check=False,
        )
    except Exception as e:
        logger.warning(f"afplay Fehler (nicht kritisch): {e}")


async def _send_voice_telegram(bot, chat_id: int, audio_bytes: bytes) -> None:
    """Schickt Audio als Telegram Sprachnachricht."""
    try:
        await bot.send_voice(chat_id=chat_id, voice=audio_bytes)
    except Exception as e:
        logger.warning(f"Telegram send_voice Fehler: {e}")