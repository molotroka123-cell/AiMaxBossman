"""Voice (V2.6, раздел 20) — слой provider-capability, и только он.

Честный probe доступности голосовых провайдеров по tools/registry.yaml:
STT — записи с can: transcribe/subtitles (whisper_local), TTS — с can:
tts/voiceover (piper_local). «Доступен» значит проверяемо доступен: бинарь из
cmd реально находится в PATH (shutil.which). Запись в реестре есть, а бинаря
нет — честное available=False с пометкой «binary not found», а не оптимизм.

Никакого аудио-конвейера и новой reasoning-архитектуры здесь нет — только
проверка возможностей, по которой остальное ядро решает, есть ли у него голос.
"""
from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass, field

import yaml

from .config import settings

# Какие умения реестра означают STT и TTS (см. tools/registry.yaml).
STT_CAPS = frozenset({"transcribe", "subtitles"})
TTS_CAPS = frozenset({"tts", "voiceover"})


@dataclass
class VoiceCapability:
    stt_available: bool
    tts_available: bool
    stt_provider: str | None
    tts_provider: str | None
    details: dict = field(default_factory=dict)


def _binary_from_cmd(cmd: str) -> str | None:
    """Извлечь фактический бинарь из cmd-записи реестра.

    Обычная команда — первый токен ('whisper-cli …' → whisper-cli).
    Обёртка `sh -c '…'` — смотрим ВНУТРЬ: у пайпа проверяем потребителя
    (последний сегмент; 'echo {text} | piper …' → piper), потому что echo
    есть всегда, а голос делает именно piper.
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    if not tokens:
        return None
    if tokens[0] in ("sh", "bash") and len(tokens) >= 3 and tokens[1] == "-c":
        payload = tokens[2]
        last_segment = payload.split("|")[-1].strip()
        try:
            inner = shlex.split(last_segment)
        except ValueError:
            inner = last_segment.split()
        return inner[0] if inner else None
    return tokens[0]


def probe() -> VoiceCapability:
    """Прочитать реестр и честно ответить, есть ли у ядра STT/TTS.

    Нет файла реестра / не парсится → обе способности False, без исключения:
    отсутствие голоса — валидное состояние, а не авария.
    При нескольких доступных провайдерах выбирается лучший по quality.
    """
    path = settings.tools_registry
    details: dict = {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tools: dict = raw.get("tools") or {}
    except FileNotFoundError:
        details["registry"] = f"реестр не найден: {path}"
        return VoiceCapability(False, False, None, None, details)
    except Exception as exc:  # noqa: BLE001 — битый yaml = голоса нет, а не падение
        details["registry"] = f"реестр не читается: {type(exc).__name__}: {exc}"
        return VoiceCapability(False, False, None, None, details)

    stt: list[tuple[int, str]] = []   # (quality, name) — доступные
    tts: list[tuple[int, str]] = []
    for name, spec in tools.items():
        can = set((spec or {}).get("can") or [])
        is_stt, is_tts = bool(can & STT_CAPS), bool(can & TTS_CAPS)
        if not (is_stt or is_tts):
            continue
        cmd = (spec or {}).get("cmd")
        if not cmd:
            details[name] = {"available": False,
                             "note": "запись без cmd — проверить бинарь нечем"}
            continue
        binary = _binary_from_cmd(str(cmd))
        found = bool(binary and shutil.which(binary))
        details[name] = {
            "binary": binary,
            "available": found,
            "note": "ok" if found else f"binary not found: {binary}",
        }
        if found:
            quality = int((spec or {}).get("quality") or 0)
            if is_stt:
                stt.append((quality, name))
            if is_tts:
                tts.append((quality, name))

    stt.sort(reverse=True)
    tts.sort(reverse=True)
    return VoiceCapability(
        stt_available=bool(stt),
        tts_available=bool(tts),
        stt_provider=stt[0][1] if stt else None,
        tts_provider=tts[0][1] if tts else None,
        details=details)
