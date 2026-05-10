"""
agent/agents/music_analysis_agent.py – Musik-Analyse mit Essentia + librosa (Issue #180)

BPM + Key via Essentia (primär), librosa als Cross-Check + Energie/Spektrum.
Direkt aufrufbar aus bot.py-Handlern (analyze_music_bytes) und als LangGraph-Agent.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage, AIMessage

from agent.state import AgentState

logger = logging.getLogger(__name__)

_ESSENTIA_AVAILABLE = False
_LIBROSA_AVAILABLE = False

try:
    import essentia.standard as es

    _ESSENTIA_AVAILABLE = True
except ImportError:
    logger.warning("music_analysis_agent: essentia nicht verfügbar")

try:
    import librosa
    import numpy as np

    _LIBROSA_AVAILABLE = True
except ImportError:
    logger.warning("music_analysis_agent: librosa nicht verfügbar")


def _analyze_sync(audio_path: str) -> dict:
    """Synchrone Kern-Analyse – wird in einem Executor ausgeführt."""
    if not _ESSENTIA_AVAILABLE and not _LIBROSA_AVAILABLE:
        raise RuntimeError("Weder essentia noch librosa installiert.")

    result: dict = {}

    # --- Essentia: BPM + Key ------------------------------------------------
    if _ESSENTIA_AVAILABLE:
        audio_es = es.MonoLoader(filename=audio_path, sampleRate=44100)()

        rhythm = es.RhythmExtractor2013(method="multifeature")
        bpm, _, beats_confidence, _, _ = rhythm(audio_es)
        result["bpm"] = round(float(bpm), 1)
        result["bpm_confidence"] = round(float(beats_confidence), 2)

        key_extractor = es.KeyExtractor()
        key, scale, strength = key_extractor(audio_es)
        result["key"] = key
        result["scale"] = scale
        result["key_confidence"] = round(float(strength), 2)

    # --- librosa: Energie + Spektrum + Chroma-Cross-Check -------------------
    if _LIBROSA_AVAILABLE:
        y, sr = librosa.load(audio_path, sr=44100, mono=True)
        duration = len(y) / sr
        result["duration_sec"] = round(duration, 1)

        rms = librosa.feature.rms(y=y)[0]
        result["rms_mean"] = round(float(np.mean(rms)), 4)
        result["rms_max"] = round(float(np.max(rms)), 4)

        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        result["spectral_centroid_hz"] = int(round(float(np.mean(centroid))))

        zcr = librosa.feature.zero_crossing_rate(y)[0]
        result["zero_crossing_rate"] = round(float(np.mean(zcr)), 4)

        # Chroma-Cross-Check für Key
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        result["key_librosa"] = note_names[int(np.argmax(np.mean(chroma, axis=1)))]

    return result


def format_analysis(r: dict) -> str:
    lines = ["Musik-Analyse:"]

    if "bpm" in r:
        conf = f" (Konfidenz: {r['bpm_confidence']})" if "bpm_confidence" in r else ""
        lines.append(f"BPM: {r['bpm']}{conf}")

    if "key" in r:
        key_str = f"{r['key']} {r['scale'].capitalize()}"
        conf = f" (Konfidenz: {r['key_confidence']})" if "key_confidence" in r else ""
        cross = f" | librosa-Check: {r['key_librosa']}" if "key_librosa" in r else ""
        lines.append(f"Key: {key_str}{conf}{cross}")

    if "duration_sec" in r:
        lines.append(f"Dauer: {r['duration_sec']} sec")

    if "rms_mean" in r:
        lines.append(f"Energie (RMS): {r['rms_mean']} mean / {r['rms_max']} peak")

    if "spectral_centroid_hz" in r:
        lines.append(f"Spektrum: Centroid {r['spectral_centroid_hz']} Hz | ZCR {r['zero_crossing_rate']}")

    return "\n".join(lines)


async def analyze_music_bytes(audio_bytes: bytes, filename: str = "audio.mp3") -> dict:
    """Analysiert Audio-Bytes. Rückgabe: dict mit BPM, Key, Energie etc."""
    suffix = Path(filename).suffix or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _analyze_sync, tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def music_analysis_agent(state: AgentState) -> AgentState:
    """LangGraph-Agent: Musik-Analyse auf Basis eines Dateipfads im Nachrichten-Text."""
    messages = state["messages"]
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)

    if not last_human:
        return {**state, "last_agent_name": "music_analysis_agent"}

    content = last_human.content if isinstance(last_human.content, str) else str(last_human.content)

    # Dateipfad aus dem Nachrichtentext extrahieren
    import re

    path_match = re.search(r"(/[^\s]+\.(mp3|wav|flac|aiff?|ogg|m4a))", content, re.IGNORECASE)
    if not path_match:
        return {
            **state,
            "messages": messages + [AIMessage(content="Kein Audiodateipfad gefunden. Bitte Pfad mit angeben.")],
            "last_agent_name": "music_analysis_agent",
        }

    audio_path = path_match.group(1)
    if not Path(audio_path).exists():
        return {
            **state,
            "messages": messages + [AIMessage(content=f"Datei nicht gefunden: {audio_path}")],
            "last_agent_name": "music_analysis_agent",
        }

    try:
        loop = asyncio.get_event_loop()
        analysis = await loop.run_in_executor(None, _analyze_sync, audio_path)
        answer = format_analysis(analysis)
    except Exception as e:
        logger.error(f"music_analysis_agent Fehler: {e}", exc_info=True)
        answer = f"Fehler bei der Musik-Analyse: {e}"

    return {
        **state,
        "messages": messages + [AIMessage(content=answer)],
        "last_agent_name": "music_analysis_agent",
    }
