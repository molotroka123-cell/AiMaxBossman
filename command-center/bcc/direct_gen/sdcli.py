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
    _photo("qwen-image-viggle-turbo", "Qwen-Image 2.1 Viggle Turbo (6 шагов)", "qwen-image",
           {"diffusion": "benchmark-20261003/viggle/Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q4_K_M.gguf", **QWEN_BASE},
           "sdcpp:qwen-image-2.1", 6, max_steps=12, manifest_key="", cfg=1.0,
           notes="6-шаговый вариант из benchmark-20261003: источник, ревизия и лицензия UNKNOWN; "
                 "cfg 1.0 выбран для дистиллята и не проверялся на качество."),
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
    if not Path(binary).is_file():
        reasons.append("нет движка sd-cli (stable-diffusion.cpp)")
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
    from ..studio.providers.sdcpp import _argv  # proven flags per engine (single source of truth)
    files = resolve_files(spec, root)
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

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None

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
                    m = PROGRESS.search(line)
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
