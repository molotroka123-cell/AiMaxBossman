"""The readiness rows of the voice tract behind a Telegram call: ASR, TTS, local model, ACL. PASS or WARN only, never BLOCKED.

One source for ``bossman call doctor`` (``CallsManager.doctor``) and for the repository doctor (``scripts/bossman_doctor.py``,
seam S6). Checks the SAME things the call worker resolves when it builds its engines (``speech.jeff_engines``): Jeff's Whisper
directory (``bcc.oss.whisper``), the Piper voice (``jeff_desktop.default_voice_env``), the local model of Jeff's config. Nothing is
loaded, nothing is downloaded, nothing leaves the machine except one optional request to the LOCAL model server.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from .hardening import check_calls_home

PASS, WARN = "PASS", "WARN"


def _row(check: str, status: str, detail: str, remedy: str = "") -> dict:
    return {"check": check, "status": status, "detail": detail, "remedy": remedy}


def asr_row() -> dict:
    """Jeff's Whisper: the faster-whisper package and a COMPLETE local model directory (model.bin, config.json, tokenizer.json)."""
    name = "Распознавание речи (Whisper)"
    try:
        from bcc.oss import whisper
        status = whisper.status()
    except Exception as exc:  # noqa: BLE001 - a doctor row must never raise
        return _row(name, WARN, f"не удалось проверить ({type(exc).__name__})", "Установите пакет и модель распознавания речи Jeff")
    if status.get("status") == "configured":
        return _row(name, PASS, "пакет и локальная модель Whisper на месте (движок не загружался)")
    reason = str(status.get("reason") or "модель не настроена")[:160]
    return _row(name, WARN, reason, "Установите extra speech и модель Whisper Jeff (BOSSMAN_WHISPER_MODEL_PATH или каталог tool-cache), "
                                     "без них разговор голосом не начнётся")


def tts_row(data_dir: Path, environ: Mapping[str, str] | None = None) -> dict:
    """Jeff's Piper voice: executable + ru_RU model (+ its .json), resolved the way Jeff's window and the worker resolve them."""
    name = "Голос (Piper)"
    env = dict(os.environ if environ is None else environ)
    try:
        from bcc import jeff_desktop
        jeff_desktop.default_voice_env(Path(data_dir), env)
    except Exception:  # noqa: BLE001
        pass
    exe, model = env.get("BOSSMAN_PIT_TTS_EXECUTABLE", ""), env.get("BOSSMAN_PIT_TTS_MODEL_PATH", "")
    if exe and model and Path(exe).is_file() and Path(model).is_file() and Path(model + ".json").is_file():
        return _row(name, PASS, "исполняемый файл Piper и русская модель голоса на месте")
    missing = [label for label, value in (("BOSSMAN_PIT_TTS_EXECUTABLE", exe), ("BOSSMAN_PIT_TTS_MODEL_PATH", model)) if not value]
    detail = ("не заданы: " + ", ".join(missing)) if missing else "указанные файлы Piper не найдены"
    return _row(name, WARN, detail, "Поставьте голос Jeff в каталог данных Bossman (voice/piper/piper.exe и ru_RU-denis-medium.onnx) "
                                     "или задайте пути; без голоса звонок не начнётся")


def model_row(data_dir: Path, *, probe: bool = True, timeout: float = 2.0) -> dict:
    """Jeff's LOCAL model route (a call never uses a cloud model). ``probe`` asks the local model server once, nothing else."""
    name = "Локальная модель Jeff"
    try:
        from bcc.pit import config as pit_config
        settings = pit_config.load(pit_config.config_path(Path(data_dir)))
    except Exception:  # noqa: BLE001 - not configured / unreadable: both mean "Jeff has no brain for the call yet"
        return _row(name, WARN, "Jeff не настроен в этом каталоге данных", "Один раз выполните настройку Jeff (web-setup с локальной моделью)")
    if not settings.local_models:
        return _row(name, WARN, "у Jeff нет локальной модели: звонок не использует облако", "Добавьте локальную модель Ollama в настройки Jeff")
    if probe and settings.local_url:
        try:
            import httpx
            base = settings.local_url.rstrip("/")
            response = httpx.get(base + "/models", timeout=timeout, trust_env=False)
            if response.status_code >= 500:
                raise RuntimeError(f"HTTP {response.status_code}")
        except Exception:  # noqa: BLE001
            return _row(name, WARN, f"локальная модель {settings.local_models[0]} настроена, но сервер моделей не отвечает",
                        "Запустите локальный сервер моделей (Ollama) и повторите проверку")
    return _row(name, PASS, f"локальная модель {settings.local_models[0]} настроена")


def acl_row(home: Path, secret_key: Path | None = None) -> dict:
    """Owner-only files of the calls folder. Here (the repository doctor) a wide ACL is a WARN; the calls doctor itself blocks."""
    name = "Права на файлы звонков"
    if not Path(home).exists():
        return _row(name, PASS, "каталога звонков ещё нет: проверять нечего")
    bad = [r for r in check_calls_home(Path(home), secret_key) if not r.ok]
    if not bad:
        return _row(name, PASS, "доступ к данным звонков только у владельца")
    return _row(name, WARN, "; ".join(f"{Path(r.path).name or 'telegram-calls'}: {r.detail}" for r in bad)[:300],
                "Откройте Telegram-звонки -> Диагностика: права исправятся, или выполните bossman call doctor")


def jeff_call_rows(data_dir: Path, *, environ: Mapping[str, str] | None = None, probe: bool = False) -> list[dict]:
    """ASR / TTS / local model / ACL in one list, every status PASS or WARN. ``probe=False`` (the default) stays off the network."""
    data_dir = Path(data_dir)
    return [asr_row(), tts_row(data_dir, environ), model_row(data_dir, probe=probe),
            acl_row(data_dir / "telegram-calls", data_dir / "secret.key")]
