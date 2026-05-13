"""
agent/agents/youtube_agent.py – YouTube-Video-Verständnis (Issue #144)

Zweistufig:
1. youtube-transcript-api: Untertitel holen (kein API-Key, kein Download)
2. Whisper-Fallback: yt-dlp Audio → transcribe_audio() wenn kein Transcript
"""

import asyncio
import logging
import re
import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from agent.state import AgentState
from agent.llm import get_llm
from agent.utils import extract_llm_text

logger = logging.getLogger(__name__)

_YT_RE = re.compile(r"(?:youtube\.com/watch[?&].*?v=|youtu\.be/)([\w-]+)")
_TRANSCRIPT_MAX_CHARS = 50_000
_AUDIO_MAX_BYTES = 100 * 1024 * 1024  # 100 MB

_SYSTEM_PROMPT = (
    "Du bist ein Video-Analyse-Assistent. Du bekommst das Transkript eines YouTube-Videos "
    "und die Frage des Users. Beantworte die Frage präzise auf Basis des Transkripts. "
    "Falls keine spezifische Frage gestellt wurde, erstelle eine kompakte Zusammenfassung."
)


def _extract_video_id(text: str) -> str | None:
    m = _YT_RE.search(text)
    return m.group(1) if m else None


async def _fetch_transcript(video_id: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        loop = asyncio.get_event_loop()
        entries = await loop.run_in_executor(
            None,
            lambda: YouTubeTranscriptApi.get_transcript(video_id, languages=["de", "en", "en-US", "de-DE"]),  # type: ignore[attr-defined]
        )
        text = " ".join(e["text"] for e in entries)
        logger.info(f"youtube_agent: Transcript geladen ({len(text)} Zeichen)")
        return text[:_TRANSCRIPT_MAX_CHARS]
    except Exception as e:
        logger.warning(f"youtube_agent: Transcript-API Fehler für {video_id}: {e}")
        return None


async def _whisper_fallback(video_id: str) -> str | None:
    try:
        import yt_dlp

        from bot.transcribe import transcribe_audio

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_tpl = str(Path(tmp_dir) / "audio.%(ext)s")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": output_tpl,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
                "quiet": True,
                "no_warnings": True,
                "max_filesize": _AUDIO_MAX_BYTES,
            }
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: yt_dlp.YoutubeDL(ydl_opts).download([f"https://www.youtube.com/watch?v={video_id}"]),
            )
            audio_files = list(Path(tmp_dir).glob("*.mp3"))
            if not audio_files:
                logger.warning(f"youtube_agent: yt-dlp lieferte keine MP3-Datei für {video_id}")
                return None
            audio_bytes = audio_files[0].read_bytes()
            text = await transcribe_audio(audio_bytes)
            if text:
                logger.info(f"youtube_agent: Whisper-Fallback ({len(text)} Zeichen)")
            return text
    except Exception as e:
        logger.error(f"youtube_agent: Whisper-Fallback Fehler: {e}", exc_info=True)
        return None


async def youtube_agent(state: AgentState) -> AgentState:
    messages = state["messages"]
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    if not last_human:
        return {**state, "last_agent_name": "youtube_agent"}

    user_text = last_human.content if isinstance(last_human.content, str) else str(last_human.content)
    video_id = _extract_video_id(user_text)

    if not video_id:
        return {
            **state,
            "messages": messages + [AIMessage(content="Keine gültige YouTube-URL gefunden.")],
            "last_agent_name": "youtube_agent",
        }

    transcript = await _fetch_transcript(video_id)
    if not transcript:
        chat_id = state.get("telegram_chat_id")
        if chat_id:
            from agent._bot_bridge import send_status

            await send_status(
                chat_id, "Kein Transcript verfügbar – lade Audio herunter und transkribiere mit Whisper..."
            )
        transcript = await _whisper_fallback(video_id)

    if not transcript:
        return {
            **state,
            "messages": messages + [AIMessage(content="Konnte kein Transkript für dieses Video abrufen.")],
            "last_agent_name": "youtube_agent",
        }

    llm = get_llm()
    response = await llm.ainvoke(
        [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=f"Transkript:\n{transcript}\n\nFrage: {user_text}"),
        ]
    )
    answer = extract_llm_text(response.content)

    return {
        **state,
        "messages": messages + [AIMessage(content=answer)],
        "last_agent_name": "youtube_agent",
    }
