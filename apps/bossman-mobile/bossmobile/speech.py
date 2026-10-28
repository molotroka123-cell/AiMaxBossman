"""Push-to-talk input for the mobile pult (audit 28.10, part III 3.1: «голосовой дуплекс Push-to-Talk»).

The phone records with MediaRecorder: Chrome gives WebM/Opus, iOS Safari gives MP4/AAC. Bossman's local Whisper
(`/api/speech/transcribe`, faster-whisper, CPU, no cloud) accepts only 16-bit PCM WAV, so the pult converts with the
host's FFmpeg first (stdin -> stdout, no temp files) and then forwards the WAV. Nothing is stored; the audio is never sent
anywhere but the local Bossman.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024        # about five minutes of Opus; the engine itself stops at 10 minutes / 32 MiB
MAX_SECONDS = 120                  # a spoken message, not a recording session
MIN_BYTES = 200


class SpeechInputError(ValueError):
    def __init__(self, message: str, code: str, status: int = 422) -> None:
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    candidate = Path.home() / "Bossman" / "media-runtime" / "ffmpeg" / "bin" / "ffmpeg.exe"
    return str(candidate) if candidate.is_file() else None


async def to_wav16k(audio: bytes, ffmpeg: str | None = None, timeout: float = 60.0) -> bytes:
    """Any browser audio container -> 16 kHz mono 16-bit WAV, at most MAX_SECONDS long."""
    if len(audio) < MIN_BYTES:
        raise SpeechInputError("запись слишком короткая", "BM_SPEECH_EMPTY")
    if len(audio) > MAX_BYTES:
        raise SpeechInputError(f"запись больше {MAX_BYTES // 1048576} МБ", "BM_SPEECH_TOO_LARGE", 413)
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        raise SpeechInputError("на ПК не найден ffmpeg: голос не обработать", "BM_SPEECH_NO_FFMPEG", 503)
    proc = await asyncio.create_subprocess_exec(
        exe, "-v", "error", "-i", "pipe:0", "-t", str(MAX_SECONDS), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", "-f", "wav", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _err = await asyncio.wait_for(proc.communicate(audio), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise SpeechInputError("обработка записи заняла слишком много времени", "BM_SPEECH_TIMEOUT", 504) from None
    if proc.returncode != 0 or len(out) < 1000 or out[:4] != b"RIFF":
        raise SpeechInputError("не удалось прочитать запись: формат не распознан", "BM_SPEECH_FORMAT")
    return fix_wav_sizes(out)


def fix_wav_sizes(wav: bytes) -> bytes:
    """ffmpeg writing a WAV to a pipe cannot seek back, so it leaves the RIFF and `data` sizes at 0xFFFFFFFF (or 0) and may
    put a LIST chunk first. Whisper's validator needs real sizes: walk the chunks and write them."""
    buf = bytearray(wav)
    pos = 12
    while pos + 8 <= len(buf):
        cid = bytes(buf[pos:pos + 4])
        if cid == b"data":
            buf[pos + 4:pos + 8] = (len(buf) - pos - 8).to_bytes(4, "little")
            buf[4:8] = (len(buf) - 8).to_bytes(4, "little")
            return bytes(buf)
        size = int.from_bytes(buf[pos + 4:pos + 8], "little")
        if size > len(buf):                       # an unfinished chunk before `data`: cannot walk on
            break
        pos += 8 + size + (size & 1)
    raise SpeechInputError("не удалось прочитать запись: нет аудиоданных", "BM_SPEECH_FORMAT")
