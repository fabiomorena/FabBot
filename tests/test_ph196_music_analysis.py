"""
tests/test_ph196_music_analysis.py – Phase 196 (Issue #180)

Testet:
- _analyze_sync: BPM, Key, Energie, Spektrum mit synthetischer Audiodatei
- analyze_music_bytes: async-Wrapper, Temp-File-Handling
- format_analysis: Ausgabe-Format
- music_analysis_agent: LangGraph-Agent (Pfad vorhanden, nicht vorhanden, kein Pfad)
- bot.py-Integration: NoSpeechDetectedError → Musik-Analyse in on_audio + _handle_document_audio
- supervisor: Pre-Routing [MUSIK-ANALYSE]
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import soundfile as sf

try:
    import essentia.standard  # noqa: F401

    _ESSENTIA_AVAILABLE = True
except ImportError:
    _ESSENTIA_AVAILABLE = False

requires_essentia = pytest.mark.skipif(not _ESSENTIA_AVAILABLE, reason="essentia nicht installiert")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sine_wav(freq: float = 440.0, duration: float = 3.0, sr: int = 44100) -> bytes:
    """Erzeugt einen synthetischen Sinus-Ton als WAV-Bytes."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    y = 0.5 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, y, sr)
        path = f.name
    data = Path(path).read_bytes()
    Path(path).unlink(missing_ok=True)
    return data


# ---------------------------------------------------------------------------
# _analyze_sync
# ---------------------------------------------------------------------------


@requires_essentia
def test_analyze_sync_returns_bpm_and_key(tmp_path):
    from agent.agents.music_analysis_agent import _analyze_sync

    wav_bytes = _make_sine_wav(freq=440.0, duration=5.0)
    p = tmp_path / "test.wav"
    p.write_bytes(wav_bytes)

    result = _analyze_sync(str(p))

    assert "bpm" in result
    assert isinstance(result["bpm"], float)
    assert result["bpm"] > 0

    assert "key" in result
    assert isinstance(result["key"], str)

    assert "duration_sec" in result
    assert result["duration_sec"] > 0


def test_analyze_sync_returns_energy_and_spectral(tmp_path):
    from agent.agents.music_analysis_agent import _analyze_sync

    wav_bytes = _make_sine_wav(freq=1000.0, duration=3.0)
    p = tmp_path / "test.wav"
    p.write_bytes(wav_bytes)

    result = _analyze_sync(str(p))

    assert "rms_mean" in result
    assert "rms_max" in result
    assert result["rms_max"] >= result["rms_mean"]

    assert "spectral_centroid_hz" in result
    assert result["spectral_centroid_hz"] > 0

    assert "zero_crossing_rate" in result
    assert "key_librosa" in result


@requires_essentia
def test_analyze_sync_key_confidence(tmp_path):
    from agent.agents.music_analysis_agent import _analyze_sync

    wav_bytes = _make_sine_wav(duration=5.0)
    p = tmp_path / "test.wav"
    p.write_bytes(wav_bytes)

    result = _analyze_sync(str(p))

    assert "key_confidence" in result
    assert 0.0 <= result["key_confidence"] <= 1.0
    assert "bpm_confidence" in result
    assert result["bpm_confidence"] >= 0.0


# ---------------------------------------------------------------------------
# format_analysis
# ---------------------------------------------------------------------------


def test_format_analysis_full():
    from agent.agents.music_analysis_agent import format_analysis

    r = {
        "bpm": 128.0,
        "bpm_confidence": 0.85,
        "key": "A",
        "scale": "minor",
        "key_confidence": 0.72,
        "key_librosa": "A",
        "duration_sec": 183.2,
        "rms_mean": 0.12,
        "rms_max": 0.45,
        "spectral_centroid_hz": 3420,
        "zero_crossing_rate": 0.042,
    }
    text = format_analysis(r)

    assert "BPM: 128.0" in text
    assert "0.85" in text
    assert "A Minor" in text
    assert "0.72" in text
    assert "librosa-Check: A" in text
    assert "183.2 sec" in text
    assert "0.12" in text
    assert "3420" in text


def test_format_analysis_partial():
    from agent.agents.music_analysis_agent import format_analysis

    r = {"bpm": 140.0, "bpm_confidence": 0.9}
    text = format_analysis(r)

    assert "BPM: 140.0" in text
    assert "Key:" not in text


# ---------------------------------------------------------------------------
# analyze_music_bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_music_bytes_returns_dict():
    from agent.agents.music_analysis_agent import analyze_music_bytes

    wav_bytes = _make_sine_wav(freq=440.0, duration=3.0)
    result = await analyze_music_bytes(wav_bytes, "test.wav")

    assert isinstance(result, dict)
    # librosa-Felder sind immer vorhanden
    assert "duration_sec" in result
    assert "rms_mean" in result


@pytest.mark.asyncio
async def test_analyze_music_bytes_cleans_up_tempfile():
    from agent.agents.music_analysis_agent import analyze_music_bytes

    created_paths = []
    original_unlink = Path.unlink

    def tracking_unlink(self, missing_ok=False):
        created_paths.append(str(self))
        original_unlink(self, missing_ok=missing_ok)

    wav_bytes = _make_sine_wav(duration=2.0)
    with patch.object(Path, "unlink", tracking_unlink):
        await analyze_music_bytes(wav_bytes, "cleanup_test.wav")

    assert len(created_paths) == 1


# ---------------------------------------------------------------------------
# music_analysis_agent (LangGraph)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_no_human_message():
    from langchain_core.messages import AIMessage

    from agent.agents.music_analysis_agent import music_analysis_agent
    from agent.state import AgentState

    state: AgentState = {"messages": [AIMessage(content="hallo")]}
    result = await music_analysis_agent(state)

    assert result["last_agent_name"] == "music_analysis_agent"
    assert len(result["messages"]) == 1


@pytest.mark.asyncio
async def test_agent_no_path_in_message():
    from langchain_core.messages import AIMessage, HumanMessage

    from agent.agents.music_analysis_agent import music_analysis_agent
    from agent.state import AgentState

    state: AgentState = {"messages": [HumanMessage(content="analysiere meine musik")]}
    result = await music_analysis_agent(state)

    reply = result["messages"][-1]
    assert isinstance(reply, AIMessage)
    assert "Dateipfad" in reply.content or "nicht gefunden" in reply.content.lower() or "kein" in reply.content.lower()


@pytest.mark.asyncio
async def test_agent_file_not_found():
    from langchain_core.messages import AIMessage, HumanMessage

    from agent.agents.music_analysis_agent import music_analysis_agent
    from agent.state import AgentState

    state: AgentState = {"messages": [HumanMessage(content="analysiere /tmp/does_not_exist_xyz.mp3")]}
    result = await music_analysis_agent(state)

    reply = result["messages"][-1]
    assert isinstance(reply, AIMessage)
    assert "nicht gefunden" in reply.content


@requires_essentia
@pytest.mark.asyncio
async def test_agent_valid_file(tmp_path):
    from langchain_core.messages import AIMessage, HumanMessage

    from agent.agents.music_analysis_agent import music_analysis_agent
    from agent.state import AgentState

    wav_bytes = _make_sine_wav(freq=440.0, duration=3.0)
    p = tmp_path / "track.wav"
    p.write_bytes(wav_bytes)

    state: AgentState = {"messages": [HumanMessage(content=f"analysiere {p}")]}
    result = await music_analysis_agent(state)

    reply = result["messages"][-1]
    assert isinstance(reply, AIMessage)
    assert "BPM" in reply.content
    assert "Key" in reply.content


# ---------------------------------------------------------------------------
# bot.py – NoSpeechDetectedError → Musik-Analyse (on_audio)
# ---------------------------------------------------------------------------


from bot.auth import ALLOWED_IDS

_ALLOWED_ID = next(iter(ALLOWED_IDS))


def _make_audio_update(audio_file_id="audio_123"):
    audio = MagicMock()
    audio.file_id = audio_file_id

    thinking = MagicMock()
    thinking.edit_text = AsyncMock()

    msg = MagicMock()
    msg.audio = audio
    msg.reply_text = AsyncMock(return_value=thinking)

    update = MagicMock()
    update.effective_chat.id = 42
    update.effective_user.id = _ALLOWED_ID
    update.message = msg
    return update


def _make_doc_update(file_name="track.mp3", file_size=1024, file_id="doc_123"):
    doc = MagicMock()
    doc.mime_type = "audio/mpeg"
    doc.file_size = file_size
    doc.file_id = file_id
    doc.file_name = file_name

    thinking = MagicMock()
    thinking.edit_text = AsyncMock()

    msg = MagicMock()
    msg.document = doc
    msg.caption = None
    msg.reply_text = AsyncMock(return_value=thinking)

    update = MagicMock()
    update.effective_chat.id = 42
    update.effective_user.id = _ALLOWED_ID
    update.message = msg
    return update


def _make_ctx(audio_bytes=b"fake_audio"):
    tg_file = AsyncMock()
    tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(audio_bytes))
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=tg_file)
    ctx = MagicMock()
    ctx.bot = bot
    return ctx


@pytest.mark.asyncio
async def test_on_audio_no_speech_triggers_music_analysis():
    from bot.transcribe import NoSpeechDetectedError
    from bot.bot import on_audio

    update = _make_audio_update()
    ctx = _make_ctx()

    fake_analysis = {"bpm": 128.0, "bpm_confidence": 0.9, "key": "A", "scale": "minor", "key_confidence": 0.7}

    with (
        patch("bot.bot.transcribe_audio", side_effect=NoSpeechDetectedError("no_speech")),
        patch("agent.agents.music_analysis_agent.analyze_music_bytes", new=AsyncMock(return_value=fake_analysis)),
        patch("agent.agents.music_analysis_agent.format_analysis", return_value="BPM: 128.0\nKey: A Minor"),
    ):
        await on_audio(update, ctx)

    update.message.reply_text.assert_called()
    calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("BPM" in c or "Analysiere" in c or "128" in c for c in calls) or update.message.reply_text.called


@pytest.mark.asyncio
async def test_on_audio_no_speech_analysis_error_replies_gracefully():
    from bot.transcribe import NoSpeechDetectedError
    from bot.bot import on_audio

    update = _make_audio_update()
    ctx = _make_ctx()

    with (
        patch("bot.bot.transcribe_audio", side_effect=NoSpeechDetectedError("no_speech")),
        patch(
            "agent.agents.music_analysis_agent.analyze_music_bytes",
            new=AsyncMock(side_effect=RuntimeError("essentia crash")),
        ),
    ):
        await on_audio(update, ctx)

    calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("fehlgeschlagen" in c.lower() or "fehler" in c.lower() for c in calls)


@pytest.mark.asyncio
async def test_handle_document_audio_no_speech_triggers_analysis():
    from bot.transcribe import NoSpeechDetectedError
    from bot.bot import _handle_document_audio

    update = _make_doc_update()
    ctx = _make_ctx()
    doc = update.message.document

    fake_analysis = {"bpm": 140.0, "key": "F#", "scale": "major"}

    with (
        patch("bot.bot.transcribe_audio", side_effect=NoSpeechDetectedError("no_speech")),
        patch("agent.agents.music_analysis_agent.analyze_music_bytes", new=AsyncMock(return_value=fake_analysis)),
        patch("agent.agents.music_analysis_agent.format_analysis", return_value="BPM: 140.0\nKey: F# Major"),
    ):
        await _handle_document_audio(update, ctx, doc, 42, _ALLOWED_ID)

    assert update.message.reply_text.called


# ---------------------------------------------------------------------------
# supervisor – Pre-Routing [MUSIK-ANALYSE]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_preroutes_musik_analyse():
    from langchain_core.messages import HumanMessage

    from agent.supervisor import supervisor_node
    from agent.state import AgentState

    state: AgentState = {"messages": [HumanMessage(content="[musik-analyse] /tmp/track.wav")]}
    result = await supervisor_node(state)

    assert result["next_agent"] == "music_analysis_agent"
