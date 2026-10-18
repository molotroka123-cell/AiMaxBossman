"""Photo and video models run through stable-diffusion.cpp (``sd-cli``) for the Direct window.

The registry is built from the files that really exist on disk. Nothing is
downloaded, substituted or routed to a paid/cloud service. Argument lists come
from the Studio provider (``bcc.studio.providers.sdcpp``) so the flags that were
proven on this machine are reused, not re-invented; only ``-p`` carries the
user's text, verbatim.

Source/revision/licence are ``UNKNOWN`` unless the models manifest or the table
below states them. sha256 comes from ``MANIFEST.json`` when it lists the file
(declared, not re-hashed here: the files are tens of gigabytes).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, Callable

from .catalog import UNKNOWN

PROGRESS = re.compile(r"\|\s*(\d+)/(\d+)\s*-\s*[\d.]+\s*(?:s/it|it/s)")
GIB = 1024 ** 3
MEMORY_MARGIN = 1.15


@dataclass(frozen=True)
class SdSpec:
    id: str
    label: str
    kind: str                       # photo | video
    family: str
    modes: tuple[str, ...]          # T2I / I2I / T2V / I2V
    files: dict[str, str]           # role -> path relative to the media models root
    engine: str                     # Studio argv id, e.g. "sdcpp:z-image-turbo"
    default_steps: int
    max_steps: int
    default_resolution: str
    cfg: float | None = None        # overrides the Studio default when set
    license: str = UNKNOWN
    manifest_key: str = ""          # MANIFEST.json engines entry (for sha256/licence)
    requires_image: bool = False
    image_flag: str = "-r"          # -r reference (edit) / -i start frame (video)
    fps: int = 0
    frame_step: int = 1
    max_seconds: int = 0
    min_side: int = 256
    max_side: int = 1536
    side_multiple: int = 16
    unsupported: str = ""
    notes: str = ""
    extra_args: tuple[str, ...] = field(default_factory=tuple)


def _photo(id_, label, family, files, engine, steps, *, resolution="512x512", **kw) -> SdSpec:
    return SdSpec(id_, label, "photo", family, kw.pop("modes", ("T2I",)), files, engine, steps,
                  kw.pop("max_steps", 50), resolution, manifest_key=kw.pop("manifest_key", engine.split(":", 1)[1]), **kw)


QWEN_BASE = {"vae": "qwen-image-2.1/qwen_image_2.1_vae_bf16.safetensors",
             "llm": "qwen-image-2.1/Qwen3VL-8B-Instruct-Q8_0.gguf",
             "llm_vision": "qwen-image-2.1/mmproj-Qwen3VL-8B-Instruct-F16.gguf"}

# Shared by both uncensored A14B entries: Wan2.1 VAE (A14B uses it), umt5 Q8 text encoder, and the uncensored
# LightX2V 4-step LoRA pair (files kept next to the merge they were downloaded with).
WAN_A14B_COMMON = {"vae": "wan22-vace-fun-a14b/wan_2.1_vae.safetensors",
                   "text_encoder": "wan22-ti2v-5b/umt5-xxl-encoder-Q8_0.gguf",
                   "lora_high": "wan22-i2v-uncensored/Wan2.2_LightX2V_high_n54vv.safetensors",
                   "lora_low": "wan22-i2v-uncensored/Wan2.2_LightX2V_low_n54vv.safetensors"}

SPECS: tuple[SdSpec, ...] = (
    _photo("z-image-turbo", "Z-Image Turbo", "z-image",
           {"diffusion": "z-image-turbo/z_image_turbo-Q8_0.gguf", "vae": "z-image-turbo/ae.safetensors",
            "text_encoder": "z-image-turbo/Qwen3-4B-Instruct-2507-Q8_0.gguf"},
           "sdcpp:z-image-turbo", 8, max_steps=20, license="Apache-2.0"),
    _photo("flux1-schnell", "FLUX.1 schnell", "flux",
           {"diffusion": "flux1-schnell/flux1-schnell-Q8_0.gguf", "vae": "z-image-turbo/ae.safetensors",
            "clip_l": "flux1-schnell/clip_l.safetensors", "t5xxl": "flux1-schnell/t5xxl_fp8_e4m3fn.safetensors"},
           "sdcpp:flux1-schnell", 4, max_steps=12, license="Apache-2.0"),
    _photo("flux2-klein-4b", "FLUX.2 klein 4B", "flux",
           {"diffusion": "flux2-klein-4b/flux-2-klein-4b-Q8_0.gguf",
            "vae": "flux2-klein-4b/full_encoder_small_decoder.safetensors",
            "llm": "flux2-klein-4b/Qwen3-4B-Q8_0.gguf"},
           "sdcpp:flux2-klein-4b", 4, max_steps=12, resolution="1024x1024", license="Apache-2.0"),
    _photo("sdxl-base", "Stable Diffusion XL 1.0", "sdxl",
           {"model": "sdxl-base/sd_xl_base_1.0.safetensors", "vae": "sdxl-base/sdxl_vae.safetensors"},
           "sdcpp:sdxl-base", 25, max_steps=60, resolution="1024x1024", license="OpenRAIL++-M"),
    _photo("epicrealism-xl", "epiCRealism XL (Pure fix, SDXL)", "sdxl",
           {"model": "epicrealism-xl/epicrealismXL_pureFix.safetensors", "vae": "sdxl-base/sdxl_vae.safetensors"},
           "sdcpp:sdxl-base", 25, max_steps=60, resolution="1024x1024", manifest_key="",
           notes="Фотореалистичный SDXL-чекпоинт с Civitai (6,46 ГБ): источник, ревизия и лицензия UNKNOWN; "
                 "запуск не проверен в этой сборке (NOT_RUN)."),
    _photo("qwen-image-2.1","Qwen-Image 2.1", "qwen-image",
           {"diffusion": "qwen-image-2.1/qwen_image_2.1-Q8_0.gguf", **QWEN_BASE},
           "sdcpp:qwen-image-2.1", 20, max_steps=50,
           license="Qwen Research License (non-commercial research/evaluation only)"),
    _photo("qwen-image-2.1-uc", "Qwen-Image 2.1 UC (вариант «uncensored»)", "qwen-image",
           {"diffusion": "benchmark-20261003/uncensored/qwen-image-2.1-UC-Q4_K_M.gguf", **QWEN_BASE},
           "sdcpp:qwen-image-2.1", 20, max_steps=50, manifest_key="",
           notes="Вариант весов из benchmark-20261003 как есть: источник, ревизия и лицензия UNKNOWN. "
                 "VAE и текстовые энкодеры берутся от Qwen-Image 2.1."),
    _photo("qwen-image-2.1-uc-heretic", "Qwen-Image 2.1 UC + heretic-энкодер", "qwen-image",
           {**QWEN_BASE, "diffusion": "benchmark-20261003/uncensored/qwen-image-2.1-UC-Q4_K_M.gguf",
            "llm": "benchmark-20261003/heretic/qwen3vl_8b_heretic-Q4_K_M.gguf",
            "llm_vision": "benchmark-20261003/heretic/mmproj-qwen3vl_8b_heretic-f16.gguf"},
           "sdcpp:qwen-image-2.1", 20, max_steps=50, manifest_key="",
           notes="Веса как есть: источник, ревизия и лицензия UNKNOWN. Текстовый энкодер заменён на вариант "
                 "heretic, диффузия и VAE те же, что у «UC». Запуск NOT_RUN в этой сборке (не проверялся здесь)."),
    _photo("qwen-image-viggle-turbo", "Qwen-Image 2.1 Viggle Turbo (6 шагов)", "qwen-image",
           {"diffusion": "benchmark-20261003/viggle/Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q4_K_M.gguf", **QWEN_BASE},
           "sdcpp:qwen-image-2.1", 6, max_steps=12, manifest_key="", cfg=1.0,
           notes="6-шаговый вариант из benchmark-20261003: источник, ревизия и лицензия UNKNOWN; "
                 "cfg 1.0 выбран для дистиллята и не проверялся на качество."),
    _photo("qwen-image-2.1-uc-edit", "Qwen-Image 2.1 UC: правка по референсу (вариант «uncensored»)", "qwen-image",
           {"diffusion": "benchmark-20261003/uncensored/qwen-image-2.1-UC-Q4_K_M.gguf", **QWEN_BASE},
           "sdcpp:qwen-image-2.1", 20, max_steps=50, manifest_key="", modes=("I2I",), requires_image=True,
           notes="Веса UC (abenzerps/Qwen-Image-2.1-Uncensored-GGUF, Q4_K_M, без встроенного фильтра безопасности, "
                 "лицензия research-only) в режиме правки по референсу: тот же путь, что в бенчмарке 03.10 "
                 "(узнаваемость сохраняется, 576x1280 заняло 47 мин). Как маршрут этого окна до 17.10 не гонялся. "
                 "Только для собственного или вымышленного персонажа; чужие люди без их согласия: нельзя."),
    _photo("qwen-image-edit-2509", "Qwen-Image Edit 2509 (правка по референсу)", "qwen-image",
           {"diffusion": "qwen-image-edit-2509/Qwen-Image-Edit-2509-Q4_K_S.gguf",
            "vae": "qwen-image-edit-2509/qwen_image_vae.safetensors",
            "llm": "qwen-image-edit-2509/Qwen2.5-VL-7B-Instruct.Q4_K_S.gguf",
            "llm_vision": "qwen-image-edit-2509/Qwen2.5-VL-7B-Instruct.mmproj-Q8_0.gguf"},
           "sdcpp:qwen-image-edit-2509", 20, max_steps=50, modes=("I2I",), requires_image=True,
           notes="Нужен референс. Только для собственного или вымышленного персонажа."),
    SdSpec("wan2.2-ti2v-5b", "Wan2.2 TI2V 5B", "video", "wan", ("T2V", "I2V"),
           {"diffusion": "wan22-ti2v-5b/Wan2.2-TI2V-5B-Q8_0.gguf", "vae": "wan22-ti2v-5b/wan2.2_vae.safetensors",
            "text_encoder": "wan22-ti2v-5b/umt5-xxl-encoder-Q8_0.gguf"},
           "sdcpp:wan2.2-ti2v-5b", 20, 40, "480x272", license="Apache-2.0", manifest_key="wan2.2-ti2v-5b",
           image_flag="-i", fps=16, frame_step=4, max_seconds=5, min_side=256, max_side=1280,
           notes="Один проход до ~5 с. Дольше на этой машине очень медленно (замер: ~14 мин на шаг при 832x480x121)."),
    # Uncensored Wan2.2 A14B (MoE high/low experts) run by sd-cli itself (engine "wan-a14b", argv below).
    # The label says "Uncensored" only because the model cards say so (AGENTS.md): sources + licences in notes.
    SdSpec("wan2.2-i2v-a14b-uncensored", "Wan2.2 I2V A14B Uncensored (картинка→видео, 4 шага)", "video", "wan", ("I2V",),
           {**WAN_A14B_COMMON,
            "diffusion": "wan22-i2v-uncensored-gguf/wan22-i2v-uncensored-low-q8_0.gguf",
            "high_noise": "wan22-i2v-uncensored-gguf/wan22-i2v-uncensored-high-q8_0.gguf"},
           "wan-a14b", 4, 12, "480x272", license="Apache-2.0", manifest_key="wan2.2-i2v-a14b-uncensored", requires_image=True,
           image_flag="-i", fps=16, frame_step=4, max_seconds=5, min_side=256, max_side=1280,
           notes="Uncensored по карточкам моделей: диффузия = merge rzgar/wan2.2-i2v-A14B-Uncensored-base "
                 "(rev 44bd071c, Apache-2.0, «understands short, simple, and uncensored prompts»), сконвертирован "
                 "локально fp8→Q8_0 GGUF; LoRA скорости = rzgar/Wan2.2_LightX2V_4Step_Uncensored (rev adc4ca92, "
                 "Apache-2.0, тег uncensored) на обоих экспертах. Нужна стартовая картинка. Всё локально. Проверено 18.10 (2 с, 272x480, 4 шага): 360 с, цвета на малом размере уплывают в фиолетовый. Нужен sd-cli 95b3090 (старый 74988b2 не помещает эксперта в буфер Vulkan). "
                 "Только для себя или вымышленного персонажа; чужие люди без их согласия: нельзя."),
    SdSpec("wan2.2-t2v-vace-a14b-uncensored", "Wan2.2 VACE-Fun A14B + Uncensored LoRA (текст→видео, 4 шага)", "video",
           "wan", ("T2V",),
           {**WAN_A14B_COMMON,
            "diffusion": "wan22-vace-fun-a14b/LowNoise/Wan2.2-VACE-Fun-A14B-low-noise-Q8_0.gguf",
            "high_noise": "wan22-vace-fun-a14b/HighNoise/Wan2.2-VACE-Fun-A14B-high-noise-Q8_0.gguf"},
           "wan-a14b", 4, 12, "480x272", license="Apache-2.0", manifest_key="wan2.2-t2v-vace-a14b-uncensored",
           unsupported="прогон 18.10 (sd-cli 95b3090, 272x480, 4 шага) упал: Vulkan ErrorOutOfDeviceMemory при загрузке эксперта VACE-Fun Q8_0 (18,7 ГБ); нужен меньший квант (Q4_K_M) или другой путь, пока отключено",
           fps=16, frame_step=4, max_seconds=5, min_side=256, max_side=1280,
           notes="База: alibaba-pai/Wan2.2-VACE-Fun-A14B (Apache-2.0, GGUF QuantStack Q8_0) — сама по себе обычная "
                 "модель; «uncensored» даёт LoRA rzgar/Wan2.2_LightX2V_4Step_Uncensored (rev adc4ca92, Apache-2.0, "
                 "карточка: «meant for both I2V and T2V, and they are uncensored»), она же даёт 4 шага. Всё локально. Нужен sd-cli 95b3090 (старый 74988b2 не помещает эксперта в буфер Vulkan). Прогон 18.10 не удался (нехватка памяти Vulkan на Q8_0)."),
    SdSpec("wan21-vace-1.3b", "Wan2.1 VACE 1.3B", "video", "wan", ("T2V", "I2V"),
           {"diffusion": "wan21-vace-1.3b/wan2.1-vace-1.3b-q8_0.gguf", "vae": "wan21-vace-1.3b/wan_2.1_vae.safetensors"},
           "", 20, 40, "480x272", fps=16, frame_step=4, max_seconds=5,
           unsupported="нет проверенного запуска (workflow-шаблона/набора флагов) для VACE; веса лежат на диске"),
    SdSpec("wan21-vace-14b", "Wan2.1 VACE 14B", "video", "wan", ("T2V", "I2V"),
           {"diffusion": "wan21-vace-14b/Wan2.1_14B_VACE-Q4_K_M.gguf"},
           "", 20, 40, "480x272", fps=16, frame_step=4, max_seconds=5,
           unsupported="нет проверенного запуска (workflow-шаблона/набора флагов) для VACE; веса лежат на диске"),
)
BY_ID = {s.id: s for s in SPECS}


def default_media_root() -> Path:
    env = os.environ.get("BOSSMAN_MEDIA_MODELS", "").strip()
    return Path(env) if env else Path.home() / "Bossman" / "models" / "media"


def default_binary() -> Path:
    env = os.environ.get("BOSSMAN_SDCPP_BIN", "").strip()
    return Path(env) if env else Path.home() / "Bossman" / "media-runtime" / "sdcpp" / "vulkan" / "sd-cli.exe"


def binary_for(spec: SdSpec, binary: Path) -> Path:
    """Engine binary for this spec. Wan2.2 A14B needs the newer sd-cli (95b3090): 74988b2 fails with
    «alloc compute params backend buffer failed» on the 15 GB experts. It lives NEXT TO the default folder
    (``vulkan-20261018``) or at $BOSSMAN_SDCPP_BIN_A14B; the default ``vulkan\`` build is never replaced."""
    if spec.engine != "wan-a14b":
        return Path(binary)
    env = os.environ.get("BOSSMAN_SDCPP_BIN_A14B", "").strip()
    return Path(env) if env else Path(binary).parent.parent / "vulkan-20261018" / "sd-cli.exe"


def _manifest(root: Path) -> dict:
    try:
        data = json.loads((Path(root) / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("engines", {}) if isinstance(data, dict) else {}


def _declared_hash(entry: dict, rel: str) -> str:
    for item in (entry.get("files") or {}).values():
        if isinstance(item, dict) and item.get("path") == rel and re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
            return item["sha256"]
    return UNKNOWN


def resolve_files(spec: SdSpec, root: Path) -> dict[str, Path]:
    return {role: Path(root).joinpath(*rel.split("/")) for role, rel in spec.files.items()}


def describe(spec: SdSpec, root: Path, binary: Path, overrides: dict | None = None) -> dict:
    entry = _manifest(root).get(spec.manifest_key, {}) if spec.manifest_key else {}
    over = (overrides or {}).get(spec.id, {}) if isinstance(overrides, dict) else {}
    weights, missing = [], []
    for role, path in resolve_files(spec, root).items():
        ok = path.is_file() and path.stat().st_size > 0
        if not ok:
            missing.append(f"{role} ({spec.files[role]})")
        weights.append({"role": role, "found": ok, "file": path.name if ok else None,
                        "size_bytes": path.stat().st_size if ok else None,
                        "sha256": _declared_hash(entry, spec.files[role]) if ok else UNKNOWN})
    reasons = []
    if spec.unsupported:
        reasons.append(spec.unsupported)
    binary = binary_for(spec, binary)
    if not Path(binary).is_file():
        reasons.append("нет движка sd-cli (stable-diffusion.cpp)" + (" 95b3090 для Wan2.2 A14B" if spec.engine == "wan-a14b" else ""))
    if missing:
        reasons.append("weights missing on disk: " + "; ".join(missing))
    main = next((w for w in weights if w["role"] in ("diffusion", "model") and w["found"]), None)
    return {
        "id": spec.id, "label": spec.label, "kind": spec.kind, "family": spec.family, "runtime": "sdcpp",
        "modes": list(spec.modes), "available": not reasons, "reason": "; ".join(reasons), "weights": weights,
        "fps": spec.fps, "max_seconds": spec.max_seconds, "min_side": spec.min_side, "max_side": spec.max_side,
        "side_multiple": spec.side_multiple, "requires_image": spec.requires_image, "requires_audio": False,
        "accepts_image": spec.requires_image or "I2V" in spec.modes,
        "default_steps": spec.default_steps, "max_steps": spec.max_steps, "default_resolution": spec.default_resolution,
        "notes": spec.notes,
        "source": str(over.get("source") or UNKNOWN), "revision": str(over.get("revision") or UNKNOWN),
        "license": str(over.get("license") or entry.get("license") or spec.license),
        "sha256": str(over.get("sha256") or (main["sha256"] if main else UNKNOWN)),
    }


def list_models(root: Path, binary: Path, overrides: dict | None = None) -> list[dict]:
    return [describe(s, root, binary, overrides) for s in SPECS]


def frames_for(spec: SdSpec, seconds: float) -> int:
    raw = max(1, round(seconds * spec.fps))
    return max(1, round((raw - 1) / spec.frame_step)) * spec.frame_step + 1


def required_bytes(spec: SdSpec, root: Path) -> int:
    total = sum(p.stat().st_size for p in resolve_files(spec, root).values() if p.is_file())
    return int(total * MEMORY_MARGIN) + 2 * GIB


def build_argv(spec: SdSpec, binary: Path, root: Path, params: dict, prompt: str, negative: str,
               out: Path, image: Path | None) -> list[str]:
    """The Studio argv for this engine, then the user's text verbatim. No other prompt text is added."""
    files = resolve_files(spec, root)
    binary = binary_for(spec, binary)
    if spec.engine == "wan-a14b":
        return _wan_a14b_argv(spec, binary, files, params, prompt, negative, out, image)
    from ..studio.providers.sdcpp import _argv  # proven flags per engine (single source of truth)
    plane = SimpleNamespace(prompt=prompt, media=({"role": "reference"},) if image and spec.image_flag == "-r" else ())
    settings = {"width": params["width"], "height": params["height"], "steps": params["steps"], "seed": params["seed"],
                "frames": params.get("frames", 1), "fps": params.get("fps", 0), "cfg_scale": 5.0}
    argv = _argv({"bin": binary}, spec.engine, plane, settings, files, out,
                 image if image and spec.image_flag == "-i" else None)
    if spec.cfg is not None:
        argv[argv.index("--cfg-scale") + 1] = str(spec.cfg)
    if image and spec.image_flag == "-r":
        argv += ["-r", str(image)]
    if negative:
        argv += ["-n", negative]
    lora_dir = os.environ.get("BOSSMAN_DIRECT_GEN_LORA_DIR", "")
    if lora_dir and "<lora:" in prompt and Path(lora_dir).is_absolute() and Path(lora_dir).is_dir():
        argv += ["--lora-model-dir", lora_dir]           # the owner's own LoRA; the prompt itself stays verbatim
    return argv


def lora_tags(spec: SdSpec) -> str:
    """sd-cli's own LoRA syntax for the spec's fixed LoRAs: ``|high_noise|`` routes one to the high-noise expert."""
    tags = ""
    if "lora_high" in spec.files:
        tags += f"<lora:|high_noise|{Path(spec.files['lora_high']).stem}:1>"
    if "lora_low" in spec.files:
        tags += f"<lora:{Path(spec.files['lora_low']).stem}:1>"
    return tags


def _wan_a14b_argv(spec: SdSpec, binary: Path, files: dict[str, Path], params: dict, prompt: str, negative: str,
                   out: Path, image: Path | None) -> list[str]:
    """Wan2.2 A14B MoE (low + high noise experts) with a 4-step distill LoRA pair: cfg 1, euler, both experts.

    The user's text goes to ``-p`` unchanged; only the model's fixed LoRA tags are appended after it.
    """
    argv = [str(binary), "-M", "vid_gen", "--diffusion-model", str(files["diffusion"]),
            "--high-noise-diffusion-model", str(files["high_noise"]), "--vae", str(files["vae"]),
            "--t5xxl", str(files["text_encoder"]),
            "--video-frames", str(params.get("frames", 1)), "--fps", str(params.get("fps", 16)),
            "--cfg-scale", "1.0", "--high-noise-cfg-scale", "1.0",
            "--sampling-method", "euler", "--high-noise-sampling-method", "euler",
            "--flow-shift", "5.0", "--offload-to-cpu"]
    tags = lora_tags(spec)
    if tags:
        argv += ["--lora-model-dir", str(files["lora_low" if "lora_low" in files else "lora_high"].parent)]
    if image is not None:
        argv += [spec.image_flag, str(image)]
    argv += ["-p", prompt + tags, "-W", str(params["width"]), "-H", str(params["height"]),
             "--steps", str(params["steps"]), "-s", str(params["seed"]), "-o", str(out)]
    if negative:
        argv += ["-n", negative]
    return argv


# ----------------------------------------------------------------------------- process


def free_memory_bytes() -> int | None:
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001 - unmeasured is reported as such by the caller
        return None


def foreign_engines() -> list[int]:
    """PIDs of sd-cli processes that are not children of this process (another job holds the GPU)."""
    try:
        import psutil
        mine = {os.getpid(), *(c.pid for c in psutil.Process().children(recursive=True))}
        return [p.pid for p in psutil.process_iter(["name"])
                if (p.info["name"] or "").lower().startswith("sd-cli") and p.pid not in mine]
    except Exception:  # noqa: BLE001
        return []


@dataclass
class RunResult:
    returncode: int
    log: list[str]
    peak_rss: int
    elapsed_s: float


class SdCliRunner:
    """Runs one engine process; reports real step progress; STOP kills the whole process tree."""

    def __init__(self, progress: re.Pattern = PROGRESS) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.progress = progress                    # the engine's own "value/max" progress line (sd-cli by default)

    async def run(self, argv: list[str], *, cwd: Path, on_progress: Callable[[int, int], Awaitable[None]] | None = None) -> RunResult:
        from ..studio.providers.sdcpp import bind_to_owner_lifetime
        started = time.monotonic()
        self.proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, cwd=str(cwd), limit=1 << 20)
        proc = self.proc
        with contextlib.suppress(Exception):
            bind_to_owner_lifetime(proc.pid)
        log: list[str] = []
        peak = 0
        ps = None
        with contextlib.suppress(Exception):
            import psutil
            ps = psutil.Process(proc.pid)

        async def sample() -> None:
            nonlocal peak
            while proc.returncode is None and ps is not None:
                with contextlib.suppress(Exception):
                    peak = max(peak, ps.memory_info().rss + sum(c.memory_info().rss for c in ps.children(recursive=True)))
                await asyncio.sleep(0.5)

        sampler = asyncio.create_task(sample())
        try:
            buf = b""
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for raw in parts:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    m = self.progress.search(line)
                    if m and on_progress:
                        await on_progress(int(m.group(1)), int(m.group(2)))
                    elif not m:
                        log = (log + [line[:400]])[-60:]
            await proc.wait()
        finally:
            sampler.cancel()
            with contextlib.suppress(BaseException):
                await sampler
        return RunResult(proc.returncode, log, peak, round(time.monotonic() - started, 2))

    def kill(self) -> None:
        """Terminate the engine and every descendant (psutil tree kill, then the direct handle)."""
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        from ..studio.providers.sdcpp import _kill_tree
        _kill_tree(proc.pid)
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()


def image_extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    return None


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        head = path.read_bytes()[:24]
    except OSError:
        return None
    if len(head) < 24 or not head.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
