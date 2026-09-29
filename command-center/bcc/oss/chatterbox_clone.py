"""Optional offline Russian voice clone using a separate, bounded local worker.

The reference, model weights and Python environment are host assets, never
packaged with Bossman. Callers must guard outgoing text before synthesis.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
from typing import Callable
import wave

from .piper import PiperError, _local_file, _run, _validate_wav
from .voice_profile import VoiceProfile


MAX_TEXT_CHARS = 280
MAX_TEXT_BYTES = 1200
MAX_REFERENCE_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MODEL_FILES = (
    "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt",
    "grapheme_mtl_merged_expanded_v1.json", "conds.pt",
)
_synthesis_lock = threading.Lock()


def _reference(path: str | Path) -> Path:
    reference = _local_file(path, ".wav")
    if not 44 < reference.stat().st_size <= MAX_REFERENCE_BYTES:
        raise PiperError("VOICE_REFERENCE_INVALID")
    try:
        with wave.open(str(reference), "rb") as audio:
            channels, width, rate, frames, compression, _ = audio.getparams()
            duration = frames / rate if rate else 0
            if (channels != 1 or width != 2 or compression != "NONE" or
                    not 16000 <= rate <= 48000 or not 5 <= duration <= 30):
                raise PiperError("VOICE_REFERENCE_INVALID")
            if len(audio.readframes(frames)) != frames * channels * width:
                raise PiperError("VOICE_REFERENCE_INVALID")
    except (OSError, EOFError, wave.Error, ZeroDivisionError) as exc:
        raise PiperError("VOICE_REFERENCE_INVALID") from exc
    return reference


def synthesize_ogg(
    text: str, *, python_executable: str | Path, model_dir: str | Path,
    reference_path: str | Path, ffmpeg_executable: str | Path,
    profile: VoiceProfile | None = None,
    stopped: Callable[[], bool] = lambda: False,
) -> bytes:
    """Synthesize one short Russian utterance into OGG/Opus without a server.

    The worker uses a local Chatterbox Multilingual V3 checkpoint on CPU. The
    process is killed on STOP/timeout, and neither text nor audio enters logs.
    """
    if (not isinstance(text, str) or not 0 < len(text.strip()) <= MAX_TEXT_CHARS or
            "\x00" in text or len(text.encode("utf-8")) > MAX_TEXT_BYTES):
        raise PiperError("VOICE_TEXT_INVALID")
    python = _local_file(python_executable, ".exe")
    ffmpeg = _local_file(ffmpeg_executable)
    directory = Path(model_dir)
    if not directory.is_absolute() or not directory.is_dir() or any(
        not (directory / name).is_file() for name in MODEL_FILES
    ):
        raise PiperError("VOICE_MODEL_INVALID")
    reference = _reference(reference_path)
    voice_profile = profile or VoiceProfile("Acid")
    exaggeration, cfg_weight = voice_profile.chatterbox_parameters()
    worker = _local_file(Path(__file__).with_name("chatterbox_worker.py"), ".py")
    if stopped():
        raise PiperError("VOICE_STOPPED")
    if not _synthesis_lock.acquire(blocking=False):
        raise PiperError("VOICE_BUSY")
    try:
        with tempfile.TemporaryDirectory(prefix="bossman-clone-") as temp:
            wav = Path(temp) / "reply.wav"
            ogg = Path(temp) / "reply.ogg"
            _run([str(python), str(worker), str(directory.resolve()),
                  str(reference), str(wav), str(exaggeration), str(cfg_weight)],
                 stdin=text.encode("utf-8"),
                 stopped=stopped, timeout=180)
            _validate_wav(wav)
            _run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                  "-i", str(wav), "-ac", "1", "-c:a", "libopus", "-b:a", "32k",
                  "-f", "ogg", str(ogg)], stdin=None, stopped=stopped, timeout=30)
            if stopped():
                raise PiperError("VOICE_STOPPED")
            if not ogg.is_file() or not 32 < ogg.stat().st_size <= MAX_OUTPUT_BYTES:
                raise PiperError("VOICE_AUDIO_INVALID")
            output = ogg.read_bytes()
            if not output.startswith(b"OggS"):
                raise PiperError("VOICE_AUDIO_INVALID")
            return output
    finally:
        _synthesis_lock.release()
