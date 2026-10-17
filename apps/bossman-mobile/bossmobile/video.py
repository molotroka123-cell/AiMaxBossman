"""Video section: the owner's Bossman video models + FaceFusion face swap, exactly as Bossman's catalog describes them.

Nothing here decides what Bossman can do: the catalog (GET /api/direct-gen/models, /faceswap) and Bossman's own
validation of POST /api/direct-gen/jobs and /swap-jobs stay the authority. This module only
* shapes the catalog for the phone (every video model, unavailable ones with the reason in Russian),
* checks a request against the SAME limits before it is sent, so the phone never offers a value Bossman rejects,
* translates Bossman's error codes into Russian.
Limits mirrored from command-center/bcc/direct_gen (service.py, catalog.py, sdcli.py, features/direct_gen.py).
"""
from __future__ import annotations

import re
from typing import Any

from . import video_timing
from .policy import uncensored_label
from .timing import step_range

MAX_INPUT = 32 * 1024 * 1024          # service.MAX_INPUT_BYTES: image, audio, face photo (decoded)
MAX_SWAP_VIDEO = 300 * 1024 * 1024    # service.MAX_SWAP_VIDEO
MAX_FACES = 5                         # faceswap.MAX_SOURCES
RES = re.compile(r"^(\d{3,4})x(\d{3,4})$")

_PHRASES = [
    (re.compile(r"weights missing on disk: ([^;]+)"), lambda m: "нет весов на диске: " + m.group(1)),
    (re.compile(r"no verified workflow template installed \(([^)]+)\)"),
     lambda m: f"не установлен проверенный шаблон запуска ({m.group(1)})"),
    (re.compile(r"ANIMATE needs a driving video and pose inputs; the Direct window has no such input yet"),
     lambda m: "нужны ведущее видео и позы; в окне генератора Bossman такого входа пока нет"),
    (re.compile(r"sd-cli binary not found[^;]*"), lambda m: "не найден sd-cli"),
    (re.compile(r"FaceFusion is not installed[^;]*", re.I), lambda m: "FaceFusion не установлен"),
]

ERRORS_RU = {
    "runtime_unreachable": "ComfyUI сейчас не запущен на ПК — задача не принята. Запустите ComfyUI и повторите.",
    "model_unavailable": "модель сейчас недоступна",
    "unknown_model": "такой модели нет в Bossman",
    "invalid_duration": "длительность вне допустимого для этой модели",
    "invalid_resolution": "размер кадра вне допустимого для этой модели",
    "invalid_steps": "число шагов вне допустимого для этой модели",
    "invalid_seed": "сид должен быть целым неотрицательным числом",
    "invalid_prompt": "промпт: от 1 до 8000 символов",
    "image_required": "этой модели нужна картинка",
    "image_unsupported": "эта модель не принимает картинку",
    "image_invalid": "картинка должна быть PNG или JPEG до 32 МБ",
    "audio_required": "этой модели нужен звук (речь или песня)",
    "audio_unsupported": "эта модель не принимает звук",
    "audio_invalid": "звук: файл до 32 МБ",
    "consent_required": "нужно подтвердить согласие человека на фото",
    "invalid_preset": "нет такого пресета замены лица",
    "invalid_frame": "кадр: «как в исходнике» или 16:9",
    "video_required": "нужно исходное видео",
    "video_invalid": "видео: файл до 300 МБ",
    "faces_required": f"нужно от 1 до {MAX_FACES} фото лица",
    "face_invalid": "фото лица должны быть PNG или JPEG до 32 МБ",
    "gpu_busy": "видеокарта занята другой задачей — ничего не запущено",
    "insufficient_memory": "не хватает свободной памяти ПК — ничего не запущено",
    "already_finished": "задача уже завершена",
}


def reason_ru(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    if re.search(r"[А-Яа-яЁё]", text):          # Bossman already wrote it in Russian
        return text
    out = text
    for rx, fn in _PHRASES:
        out = rx.sub(fn, out)
    return out if out != text else f"недоступна ({text})"


def _aspect_of(res: str) -> str:
    m = RES.fullmatch(str(res or ""))
    if not m:
        return "16:9"
    w, h = int(m.group(1)), int(m.group(2))
    opts = {"16:9": 16 / 9, "9:16": 9 / 16, "3:2": 3 / 2, "2:3": 2 / 3, "1:1": 1.0}
    return min(opts, key=lambda k: abs(opts[k] - w / h))


def model_view(m: dict, status: dict, history: dict[str, dict]) -> dict[str, Any]:
    """One catalog video model for the phone. `ready` = Bossman would accept a job right now (as far as we can see)."""
    runtime = m.get("runtime") or ("sdcpp" if m.get("default_steps") else "comfyui")
    steps = None
    if m.get("default_steps") and m.get("max_steps"):
        steps = step_range(m)
    fixed = (video_timing.TABLE.get(str(m.get("id"))) or {}).get("fixed_steps")
    ready, why = bool(m.get("available")), reason_ru(m.get("reason"))
    rt = status.get("runtime") or {}
    if ready and runtime == "comfyui" and rt.get("reachable") is False:
        ready, why = False, "ComfyUI сейчас не запущен на ПК — Bossman не примет задачу, пока его не запустить"
    if ready and runtime == "sdcpp" and (status.get("sd_cli") or {}).get("present") is False:
        ready, why = False, "на ПК не найден sd-cli"
    accepts_image = bool(m.get("accepts_image")) and (runtime == "sdcpp" or bool(m.get("requires_image")))
    return {
        "id": m.get("id"), "label": m.get("label"), "kind": "video", "family": m.get("family"), "runtime": runtime,
        "modes": m.get("modes") or [], "available": bool(m.get("available")), "ready": ready,
        "reason": why, "notes": m.get("notes") or "", "license": m.get("license"),
        "requires_image": bool(m.get("requires_image")), "accepts_image": accepts_image,
        "requires_audio": bool(m.get("requires_audio")), "accepts_audio": bool(m.get("requires_audio")),
        "fps": int(m.get("fps") or 16), "frame_step": video_timing.FRAME_STEP.get(str(m.get("family")), 4),
        "max_seconds": float(m.get("max_seconds") or 5), "min_side": int(m.get("min_side") or 256),
        "max_side": int(m.get("max_side") or 1280), "side_multiple": int(m.get("side_multiple") or 16),
        "default_resolution": m.get("default_resolution") or "480x320",
        "default_aspect": _aspect_of(m.get("default_resolution")),
        "steps": steps, "fixed_steps": fixed if not steps else None,
        "uncensored_label": uncensored_label(m.get("label")),
        "timing": video_timing.timing_for(str(m.get("id")), history),
    }


def faceswap_view(info: dict, history: dict[str, dict]) -> dict[str, Any]:
    return {"id": video_timing.FACESWAP_ID, "label": "Замена лица в видео (FaceFusion)", "kind": "faceswap",
            "available": bool(info.get("available")), "ready": bool(info.get("available")),
            "reason": reason_ru(info.get("reason")), "license": info.get("license"),
            "presets": [{"id": p.get("id"), "label": p.get("label")} for p in info.get("presets") or []
                        if isinstance(p, dict) and p.get("id")],
            "frames": [{"id": "original", "label": "как в исходнике"}, {"id": "16:9", "label": "16:9"}],
            "max_faces": MAX_FACES, "max_video_mb": MAX_SWAP_VIDEO // 1048576,
            "timing": video_timing.timing_for(video_timing.FACESWAP_ID, history)}


def check_job(spec: dict, *, resolution: str, duration: float, steps: int | None, has_image: bool,
              has_audio: bool) -> str | None:
    """Russian refusal text, or None when Bossman's own checks would pass for these values."""
    if not spec.get("ready"):
        return spec.get("reason") or "модель сейчас недоступна"
    m = RES.fullmatch(resolution or "")
    if not m:
        return "размер кадра должен выглядеть как 480x272"
    lo, hi, k = spec["min_side"], spec["max_side"], spec["side_multiple"]
    for side in (int(m.group(1)), int(m.group(2))):
        if not lo <= side <= hi or side % k:
            return f"каждая сторона кадра — от {lo} до {hi} и кратна {k}"
    if not 0 < duration <= spec["max_seconds"]:
        return f"длительность — больше 0 и не больше {spec['max_seconds']:g} с"
    if spec.get("steps"):
        top = spec["steps"]["max_steps"]
        if steps is not None and not 1 <= steps <= top:
            return f"для этой модели шагов может быть от 1 до {top}"
    elif steps is not None:
        return "у этой модели число шагов задано её графом и не меняется"
    if spec["requires_image"] and not has_image:
        return "этой модели нужна стартовая картинка"
    if has_image and not spec["accepts_image"]:
        return "эта модель не принимает картинку"
    if spec["requires_audio"] and not has_audio:
        return "этой модели нужен звук (речь или песня)"
    if has_audio and not spec["accepts_audio"]:
        return "эта модель не принимает звук"
    return None


def image_kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    return None
