"""Local AI generation through stable-diffusion.cpp (Vulkan) — Studio provider.

Why this engine: on the owner's Radeon 8060S the LLM stack already runs on
Vulkan (llama.cpp). The Wan 2.2 14B ComfyUI/ROCm path takes ~27 min for 81
frames at 42 GB peak (AMD ROCm blog). stable-diffusion.cpp runs the same model
families from GGUF on the same Vulkan driver, as one short-lived child process
per job: nothing stays resident next to MAIN/FAST after the job.

Models (pinned files, see `MANIFEST.json`; schema in docs/media/SDCPP_ENGINE_RU.md):
  * `sdcpp:wan2.2-ti2v-5b`  — Wan2.2 TI2V-5B, text→video and image→video
    (`start` role), Apache-2.0.
  * `sdcpp:z-image-turbo`   — Z-Image-Turbo, text→image, Apache-2.0.
  * `sdcpp:flux1-schnell`   — FLUX.1-schnell, text→image, Apache-2.0.
  * `sdcpp:sdxl-base`       — Stable Diffusion XL 1.0, text→image, OpenRAIL++-M.
  * `sdcpp:flux2-klein-4b`  — FLUX.2-klein-4B, text→image, Apache-2.0.

Honesty boundary: the engine writes .webm/.png; the Studio persists only
bytes that pass ffprobe + full decode (runtime.verify_file) — a zero exit code
alone is never "completed". The MP4 made from the engine's .webm is a
container transcode of the model output; the trace separates the three stages
(generation → transcode → import) with a hash at each boundary, so an FFmpeg
render can never pose as generation. The backend ("vulkan") is only ever
*observed* from the engine's own log; when the log says nothing, the trace
says UNVERIFIED.

Model file verification (measured, not assumed):
  * The manifest declares sha256 + bytes for every file; the provider computes
    the real sha256 (expected and observed are separate fields in provenance).
  * A small JSON cache next to the engine work dir remembers
    (path, size, mtime_ns, ctime_ns, inode, expected sha) → observed sha, so
    health calls do not rehash gigabytes. A cache entry is only trusted after
    THIS installation hashed the file at least once (the cache file carries a
    per-installation token); a hand-written cache never skips the first hash.
  * Policy per call (`verify_engine_files(mode=...)`):
      health  — cache when stat keys + expected sha match, else full hash.
      submit  — files < SMALL_FILE_FULL_HASH_BYTES are always fully rehashed;
                larger files need matching stat keys AND a sampled digest
                (head/middle/tail windows of SAMPLE_WINDOW_BYTES) recorded at
                the last full hash. Sampling detects same-size corruption only
                inside those windows — that is the honest limit of the policy.
      force   — full hash of everything, always.

Configuration (owner-set; loopback/no network). Env wins per variable; otherwise
`<data_dir>/media/config.json` ({"sdcpp_bin", "models_dir", "written_at"}, written by
`tools/media_bootstrap.py configure` / write_configuration) so a launch through
Start-Bossman.cmd, a shortcut or a fresh shell still finds the engine:
  BOSSMAN_SDCPP_BIN      path to sd-cli(.exe)
  BOSSMAN_MEDIA_MODELS   directory with MANIFEST.json and the model files
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from bcc.studio.catalog import validate_settings
from bcc.studio.provider import (Fetched, GenerationPlane, ProviderFailure, ProviderOutput,
                                 ProviderStatus, Submitted)

BIN_ENV = "BOSSMAN_SDCPP_BIN"
MODELS_ENV = "BOSSMAN_MEDIA_MODELS"
MANIFEST_SCHEMA_VERSION = 1
HASH_CACHE_NAME = "model-hash-cache.json"

# model id -> manifest entry name; the files and their sha256 come from the manifest.
ENGINES = {
    "sdcpp:wan2.2-ti2v-5b": "wan2.2-ti2v-5b",
    "sdcpp:z-image-turbo": "z-image-turbo",
    "sdcpp:flux1-schnell": "flux1-schnell",
    "sdcpp:sdxl-base": "sdxl-base",
    "sdcpp:flux2-klein-4b": "flux2-klein-4b",
}
# Roles the argv needs per engine; every file listed in the manifest entry is hashed,
# these must at least be present.
REQUIRED_ROLES = {
    "sdcpp:wan2.2-ti2v-5b": ("diffusion", "vae", "text_encoder"),
    "sdcpp:z-image-turbo": ("diffusion", "vae", "text_encoder"),
    "sdcpp:flux1-schnell": ("diffusion", "vae", "clip_l", "t5xxl"),
    "sdcpp:sdxl-base": ("model", "vae"),
    "sdcpp:flux2-klein-4b": ("diffusion", "vae", "llm"),
}
ALLOWED_INPUT_ROLES = frozenset({"start"})
ALLOWED_INPUT_FORMATS = frozenset({"PNG", "JPEG"})
MAX_INPUT_BYTES = 15 * 1024 * 1024          # consistent with dispatch.inputs_for
MAX_INPUT_PIXELS = 32 * 1024 * 1024
SMALL_FILE_FULL_HASH_BYTES = 64 * 1024 * 1024
SAMPLE_WINDOW_BYTES = 4 * 1024 * 1024
MIN_FREE_BYTES = {"video": 2 * 1024 ** 3, "image": 512 * 1024 ** 2}
MAX_LOG_LINE = 4096
MAX_LOG_LINES = 60
CANCEL_WAIT_S = 15
KILL_WAIT_S = 10
HEX64 = re.compile(r"^[0-9a-f]{64}$")

_HASH_CACHE_LOCK_TOKEN = "installation_token"


# ----------------------------------------------------------------------------- manifest

def _check_relative_path(role: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest {role}: path required")
    if "\x00" in value or "\\" in value:
        raise ValueError(f"manifest {role}: path must use forward slashes")
    posix = PurePosixPath(value)
    if posix.is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ValueError(f"manifest {role}: path must be relative")
    parts = posix.parts
    if not parts or any(p in ("..", ".", "") for p in parts):
        raise ValueError(f"manifest {role}: path escapes the model directory")
    return value


def validate_manifest(data) -> dict:
    """Schema check only; no file access. Raises ValueError with the failing field."""
    if not isinstance(data, dict):
        raise ValueError("manifest: object required")
    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("manifest schema_version: must be 1")
    engine = data.get("engine")
    if not isinstance(engine, dict) or not isinstance(engine.get("release"), str) or not engine["release"]:
        raise ValueError("manifest engine.release: string required")
    binary_sha = engine.get("binary_sha256")
    if binary_sha is not None and not (isinstance(binary_sha, str) and HEX64.match(binary_sha)):
        raise ValueError("manifest engine.binary_sha256: 64 lowercase hex chars required")
    engines = data.get("engines")
    if not isinstance(engines, dict) or not engines:
        raise ValueError("manifest engines: non-empty object required")
    for name, entry in engines.items():
        if not isinstance(name, str) or not isinstance(entry, dict) or not isinstance(entry.get("files"), dict) \
                or not entry["files"]:
            raise ValueError(f"manifest engines.{name}: files object required")
        for role, spec in entry["files"].items():
            if not isinstance(spec, dict):
                raise ValueError(f"manifest {name}.{role}: object required")
            _check_relative_path(f"{name}.{role}", spec.get("path"))
            if type(spec.get("bytes")) is not int or spec["bytes"] < 0:
                raise ValueError(f"manifest {name}.{role}: bytes must be a non-negative integer")
            if not isinstance(spec.get("sha256"), str) or not HEX64.match(spec["sha256"]):
                raise ValueError(f"manifest {name}.{role}: sha256 must be 64 lowercase hex chars")
            for opt in ("revision", "url"):
                if spec.get(opt) is not None and not isinstance(spec[opt], str):
                    raise ValueError(f"manifest {name}.{role}: {opt} must be a string")
    return data


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest: unreadable ({type(exc).__name__})") from exc
    return validate_manifest(data)


def _default_cache_dir() -> Path:
    from bcc.config import _data_dir
    return _data_dir() / "studio" / "engine-work"


def _data_dir_path(data_dir=None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    from bcc.config import _data_dir  # honours BCC_DATA_DIR, else the platform data dir
    return _data_dir()


def media_config_path(data_dir=None) -> Path:
    return _data_dir_path(data_dir) / "media" / "config.json"


def _read_media_config(data_dir=None) -> dict | None:
    """`<data_dir>/media/config.json` written by write_configuration; None when missing/invalid."""
    path = media_config_path(data_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    for key in ("sdcpp_bin", "models_dir"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        out[key] = value.strip()
    return out


def describe_configuration(cache_dir: Path | None = None, data_dir=None) -> dict:
    """Where the engine configuration comes from and why it is (not) usable. Never raises.

    Launch via Start-Bossman.cmd, a shortcut or a fresh shell may carry no env vars, so
    each value falls back to the media config file. Env wins per variable when set.
    """
    env_bin = os.environ.get(BIN_ENV, "").strip()
    env_root = os.environ.get(MODELS_ENV, "").strip()
    file_cfg = None if (env_bin and env_root) else _read_media_config(data_dir)
    binary = env_bin or (file_cfg or {}).get("sdcpp_bin", "")
    root = env_root or (file_cfg or {}).get("models_dir", "")
    source = {"bin": "env" if env_bin else ("file" if binary else None),
              "models": "env" if env_root else ("file" if root else None)}
    report = {"bin": binary or None, "models": root or None, "source": source,
              "config_file": str(media_config_path(data_dir)), "error": None, "cfg": None}
    if not binary or not root:
        report["error"] = "not configured: set BOSSMAN_SDCPP_BIN/BOSSMAN_MEDIA_MODELS or write media/config.json"
        return report
    exe, models = Path(binary), Path(root)
    manifest_path = models / "MANIFEST.json"
    if not exe.is_file():
        report["error"] = "engine binary missing"
        return report
    if not manifest_path.is_file():
        report["error"] = "MANIFEST.json missing"
        return report
    try:
        manifest = load_manifest(manifest_path)
    except ValueError as exc:
        report["error"] = str(exc)
        return report
    report["cfg"] = {"bin": exe, "root": models, "manifest": manifest,
                     "cache_dir": Path(cache_dir) if cache_dir is not None else _default_cache_dir()}
    return report


def configuration(cache_dir: Path | None = None, *, strict: bool = False) -> dict | None:
    """Engine binary + validated manifest, or None (not configured / invalid).

    Never raises unless strict=True (then an invalid manifest raises ValueError with the
    failing field). A missing or broken media config must not break Studio or startup.
    """
    report = describe_configuration(cache_dir)
    if report["cfg"] is None and strict and report["error"] and report["error"].startswith("manifest"):
        raise ValueError(report["error"])
    return report["cfg"]


def write_configuration(sdcpp_bin, models_dir, data_dir=None) -> Path:
    """Persist the engine configuration for launches without env vars. Validates first, writes atomically."""
    exe, models = Path(sdcpp_bin).expanduser(), Path(models_dir).expanduser()
    if not exe.is_file():
        raise FileNotFoundError(f"engine binary missing: {exe}")
    manifest_path = models / "MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"MANIFEST.json missing in {models}")
    load_manifest(manifest_path)  # ValueError names the failing field
    path = media_config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"schema_version": 1, "sdcpp_bin": str(exe.resolve()), "models_dir": str(models.resolve()),
              "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


# ----------------------------------------------------------------------------- hashing

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sample_digest(path: Path, size: int) -> str:
    """sha256 of head/middle/tail windows — cheap re-check for multi-GB files."""
    w = SAMPLE_WINDOW_BYTES
    offsets = sorted({0, max(0, size // 2 - w // 2), max(0, size - w)})
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for off in offsets:
            fh.seek(off)
            h.update(fh.read(w))
    return h.hexdigest()


def _stat_key(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns, "inode": st.st_ino}


_VERIFIED: dict[str, dict] = {}            # in-process verified (path → observation) memo, see HashCache
_VERIFIED_TOKEN = secrets.token_hex(16)     # process-unique: a memo never outlives the process


class HashCache:
    """Persisted (path, stat keys, expected sha) → observed sha. Never authoritative on its own:
    an entry is only reused when its `installation_token` matches the token this cache file was
    created with by this code, which a hand-written file cannot know."""

    VERSION = 2

    def __init__(self, directory: Path | None):
        self.path = None if directory is None else Path(directory) / HASH_CACHE_NAME
        # No cache directory → process-local memo (`_VERIFIED`) shared by every call in this
        # process, so a repeated health view does not re-hash 25 GB; tests clear it explicitly.
        self.entries: dict[str, dict] = _VERIFIED if directory is None else {}
        self.token: str | None = _VERIFIED_TOKEN if directory is None else None
        self.dirty = False
        self._load()

    def _load(self):
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == self.VERSION and isinstance(data.get(_HASH_CACHE_LOCK_TOKEN), str) \
                    and isinstance(data.get("entries"), dict):
                self.token = data[_HASH_CACHE_LOCK_TOKEN]
                self.entries = {k: v for k, v in data["entries"].items()
                                if isinstance(v, dict) and v.get("token") == self.token}
        except (OSError, ValueError, UnicodeDecodeError):
            self.entries = {}

    def lookup(self, path: Path, expected: str) -> dict | None:
        entry = self.entries.get(str(path))
        if not entry or entry.get("sha256_expected") != expected or self.token is None \
                or entry.get("token") != self.token:
            return None
        try:
            if _stat_key(path) != {k: entry.get(k) for k in ("size", "mtime_ns", "ctime_ns", "inode")}:
                return None
        except OSError:
            return None
        return entry

    def record(self, path: Path, expected: str, observed: str, sample: str):
        if self.token is None:
            self.token = secrets.token_hex(16)
        self.entries[str(path)] = {**_stat_key(path), "sha256_expected": expected, "sha256_observed": observed,
                                   "sample": sample, "verified_at": time.time(), "token": self.token}
        self.dirty = True

    def save(self):
        if self.path is None or not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            live = {k: v for k, v in self.entries.items() if Path(k).exists()}
            # pid AND thread: health checks run in worker threads, two listings may save at once
            tmp = self.path.with_name(self.path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"version": self.VERSION, _HASH_CACHE_LOCK_TOKEN: self.token,
                                       "entries": live}, indent=0), encoding="utf-8")
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass  # cache is an optimisation; verification already happened


def _resolve_model_file(cfg: dict, role: str, rel: str) -> Path:
    root = Path(cfg["root"]).resolve()
    lexical = root.joinpath(*PurePosixPath(rel).parts)
    if lexical.is_symlink():
        raise PermissionError(f"{role}: model file is a symlink")
    resolved = lexical.resolve()
    if resolved != lexical or root not in resolved.parents:
        raise PermissionError(f"{role}: model path escapes the media model directory (symlink/junction)")
    if not resolved.is_file():
        raise FileNotFoundError(f"{role}: {rel} missing")
    return resolved


def _verify_one(path: Path, role: str, spec: dict, cache: HashCache, mode: str) -> dict:
    size = path.stat().st_size
    expected = spec["sha256"]
    if size != int(spec["bytes"]):
        raise FileNotFoundError(f"{role}: {spec['path']} size {size} differs from manifest {spec['bytes']}")
    entry = None if mode == "force" else cache.lookup(path, expected)
    method = "full"
    if entry is not None:
        if mode == "health":
            method = "cache"
        elif mode == "submit" and size >= SMALL_FILE_FULL_HASH_BYTES:
            method = "sampled"
            if _sample_digest(path, size) != entry.get("sample"):
                raise ValueError(f"{role}: sha256 sample mismatch — file changed since last full verification")
        # submit + small file → full rehash below
        else:
            method = "full"
    observed = entry["sha256_observed"] if method != "full" else _sha256(path)
    if observed != expected:
        raise ValueError(f"{role}: sha256 mismatch (manifest {expected[:12]}…, observed {observed[:12]}…)")
    if method == "full":  # only a matching observation is worth remembering
        cache.record(path, expected, observed, _sample_digest(path, size))
    return {"path": path, "name": path.name, "bytes": size, "sha256_expected": expected,
            "sha256_observed": observed, "method": method, "revision_declared": spec.get("revision")}


def verify_engine_binary(cfg: dict, *, force: bool = False, cache: HashCache | None = None) -> dict:
    exe = Path(cfg["bin"]).resolve()
    expected = cfg["manifest"].get("engine", {}).get("binary_sha256")
    own = cache is None
    cache = cache or HashCache(cfg.get("cache_dir"))
    key = expected or "unpinned"
    entry = None if force else cache.lookup(exe, key)
    if entry is None:
        observed = _sha256(exe)
        cache.record(exe, key, observed, "")
        method = "full"
    else:
        observed, method = entry["sha256_observed"], "cache"
    if own:
        cache.save()
    match = None if expected is None else observed == expected
    if match is False:
        raise ValueError(f"engine binary sha256 mismatch (manifest {expected[:12]}…, observed {observed[:12]}…)")
    return {"path": str(exe), "name": exe.name, "sha256_expected": expected, "sha256_observed": observed,
            "match": match, "method": method, "release_declared": cfg["manifest"].get("engine", {}).get("release")}


def verify_engine_files(cfg: dict, model_id: str, *, mode: str = "health") -> dict[str, dict]:
    """Verify every manifest file of the engine. mode: health | submit | force (see module doc)."""
    if mode not in ("health", "submit", "force"):
        raise ValueError("mode: health|submit|force")
    entry = cfg["manifest"]["engines"].get(ENGINES[model_id])
    if entry is None:
        raise KeyError(f"manifest: engine {ENGINES[model_id]} missing")
    missing = [r for r in REQUIRED_ROLES[model_id] if r not in entry["files"]]
    if missing:
        raise KeyError(f"manifest: {ENGINES[model_id]} lacks roles {missing}")
    cache = HashCache(cfg.get("cache_dir"))
    out = {}
    try:
        verify_engine_binary(cfg, force=mode == "force", cache=cache)
        for role, spec in entry["files"].items():
            path = _resolve_model_file(cfg, role, spec["path"])
            out[role] = _verify_one(path, role, spec, cache, mode)
    finally:
        cache.save()
    return out


def engine_files(cfg: dict, model_id: str) -> dict[str, Path]:
    """Health view: presence, size, sha256 (cached after the first real hash)."""
    return {role: info["path"] for role, info in verify_engine_files(cfg, model_id, mode="health").items()}


# ----------------------------------------------------------------------------- argv / observation

# Длительность ролика (`length`) — пресеты владельца поверх frames/fps. Wan2.2 TI2V-5B
# даёт за один проход не больше ~5 с (81 кадр при 16 fps); длиннее — ЦЕПОЧКА сегментов:
# каждый следующий сегмент — I2V от последнего кадра предыдущего, на стыке первый кадр
# нового сегмента отбрасывается (он повторяет последний кадр прошлого). Склейка — это
# транскод, не генерация: в trace у каждого сегмента свой хэш, у склейки — свой.
# "test_1s" — быстрый пробный прогон: минимальные разрешение и шаги из каталога.
DURATION_PRESETS = {
    "test_1s": {"segments": 1, "frames": 17, "fps": 16, "width": 640, "height": 352, "steps": 16},
    "5s": {"segments": 1, "frames": 81, "fps": 16},
    "10s": {"segments": 2, "frames": 81, "fps": 16},
    "15s": {"segments": 3, "frames": 81, "fps": 16},
    "30s": {"segments": 6, "frames": 81, "fps": 16},
}


def segments_for(settings: dict) -> int:
    preset = DURATION_PRESETS.get((settings or {}).get("length"))
    return preset["segments"] if preset else 1


def apply_length(settings: dict) -> dict:
    """Resolved settings with the `length` preset applied (custom/absent = unchanged)."""
    preset = DURATION_PRESETS.get(settings.get("length"))
    if preset is None:
        return settings
    return {**settings, **{k: v for k, v in preset.items() if k != "segments"}}


# Deadline scale: the catalog deadline covers the reference clip 832x480, 49 frames, 20 steps.
# Bigger work (720p, 81 frames, 50 steps) gets a proportionally longer budget instead of being
# killed mid-segment; smaller work keeps the catalog deadline (never below it).
_REFERENCE_WORK = 832 * 480 * 49 * 20

# ...but proportional is not unbounded. Catalog limits alone allow 1280x1280x81x50 ~= 17x the
# reference, i.e. ~17 h for ONE segment, and the 30 s preset chains six of them: over four days of
# wall clock from a single request. The proportional budget may never cross these ceilings. They
# sit deliberately above the owner's own heaviest real run (1280x704, 81 frames, 50 steps -> ~9.2 h
# of budget), so the fix bounds runaway work without shortening work that is known to be legitimate.
# Both are configurable; an explicit owner limit (hard_timeout_s) is sovereign and not capped here.
MAX_SEGMENT_DEADLINE_ENV = "BOSSMAN_STUDIO_MAX_SEGMENT_DEADLINE_S"
MAX_JOB_DEADLINE_ENV = "BOSSMAN_STUDIO_MAX_JOB_DEADLINE_S"
DEFAULT_MAX_SEGMENT_DEADLINE_S = 12 * 3600
DEFAULT_MAX_JOB_DEADLINE_S = 24 * 3600


def _positive_float(value, default: float) -> float:
    """A number only counts when it is finite and positive; 0, negatives, NaN, inf, booleans,
    strings and None fall back to the reference value. A bogus setting must never buy wall clock."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")) or v <= 0:   # NaN / +-inf / non-positive
        return float(default)
    return v


def _limit_s(env_name: str, default: float) -> float:
    return _positive_float(_env_number(os.environ.get(env_name)), default)


def _env_number(raw):
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def max_segment_deadline_s() -> float:
    return _limit_s(MAX_SEGMENT_DEADLINE_ENV, DEFAULT_MAX_SEGMENT_DEADLINE_S)


def max_job_deadline_s() -> float:
    return _limit_s(MAX_JOB_DEADLINE_ENV, DEFAULT_MAX_JOB_DEADLINE_S)


def workload_scale(settings: dict) -> float:
    s = settings or {}
    if not s.get("frames"):
        return 1.0
    work = (_positive_float(s.get("width"), 832) * _positive_float(s.get("height"), 480)
            * _positive_float(s.get("frames"), 49) * _positive_float(s.get("steps"), 20))
    scale = work / _REFERENCE_WORK
    if scale != scale:  # pragma: no cover - every factor is already finite and positive
        return 1.0
    return max(1.0, scale)


def segment_deadline_s(model: dict, settings: dict) -> float:
    """Work-proportional budget for ONE engine run, never above the absolute ceiling."""
    catalog_s = _positive_float(model.get("deadline_seconds"), 3600)
    return min(catalog_s * workload_scale(settings) + 60, max_segment_deadline_s())


def job_budget_s(model: dict, settings: dict, segments: int) -> float:
    """Wall clock the whole chain may consume, ceiling included. A chain of segments must not
    multiply its way past the absolute limit any more than one oversized segment can."""
    n = max(1, int(segments or 1))
    return min(segment_deadline_s(model, settings) * n, max_job_deadline_s())


def declared_duration_s(settings: dict) -> float | None:
    if settings.get("frames") and settings.get("fps"):
        n = segments_for(settings)
        return (settings["frames"] + (settings["frames"] - 1) * (n - 1)) / settings["fps"]
    return None


def _argv(cfg: dict, model_id: str, plane: GenerationPlane, settings: dict,
          files: dict[str, Path], out: Path, init: Path | None) -> list[str]:
    argv = [str(cfg["bin"])]
    if model_id == "sdcpp:wan2.2-ti2v-5b":
        argv += ["-M", "vid_gen", "--diffusion-model", str(files["diffusion"]),
                 "--vae", str(files["vae"]), "--t5xxl", str(files["text_encoder"]),
                 "--video-frames", str(settings["frames"]), "--fps", str(settings["fps"]),
                 "--flow-shift", "5.0", "--cfg-scale", str(settings["cfg_scale"]),
                 "--sampling-method", "euler"]
        if init is not None:
            argv += ["-i", str(init)]
    elif model_id == "sdcpp:flux1-schnell":
        argv += ["--diffusion-model", str(files["diffusion"]), "--vae", str(files["vae"]),
                 "--clip_l", str(files["clip_l"]), "--t5xxl", str(files["t5xxl"]),
                 "--cfg-scale", "1.0", "--sampling-method", "euler"]
    elif model_id == "sdcpp:flux2-klein-4b":
        # Live on the owner machine 2026-09-22: 1024x1024, 4 steps, 69.8 s.
        # --offload-to-cpu keeps the 4B weights out of the iGPU buffer limit.
        argv += ["--diffusion-model", str(files["diffusion"]), "--vae", str(files["vae"]),
                 "--llm", str(files["llm"]), "--cfg-scale", "1.0",
                 "--offload-to-cpu", "--diffusion-fa"]
    elif model_id == "sdcpp:sdxl-base":
        argv += ["-m", str(files["model"]), "--vae", str(files["vae"]),
                 "--cfg-scale", "7.0", "--sampling-method", "dpm++2m"]
    else:
        argv += ["--diffusion-model", str(files["diffusion"]), "--vae", str(files["vae"]),
                 "--llm", str(files["text_encoder"]), "--cfg-scale", "1.0"]
    argv += ["-p", plane.prompt, "-W", str(settings["width"]), "-H", str(settings["height"]),
             "--steps", str(settings["steps"]), "-s", str(settings["seed"]),
             "-o", str(out)]
    # Живой прогон 2026-09-21 (Radeon 8060S, Vulkan): с --diffusion-fa/--vae-tiling
    # при 480x288/10 шагов Wan выдавал цветной шум; без них 832x480/20 шагов —
    # правильный ролик. Причина НЕ изолирована (tools/media_ab_preset.py готовит
    # A/B-матрицу); флаги не включаем и ни один пресет «рабочим» не объявляем.
    return argv


_BACKEND_RE = re.compile(r"(ggml_vulkan|vulkan\s*device|vulkan\d*\s*=|using\s+vulkan|vk::|VK_)", re.IGNORECASE)
_BACKEND_NEGATIVE = re.compile(r"(not\s+compiled|unavailable|disabled|no\s+vulkan|fall(ing)?\s*back)", re.IGNORECASE)


def observe_backend(lines) -> dict:
    """The backend is never asserted: it is read from the engine log or reported UNVERIFIED."""
    for raw in lines or ():
        line = str(raw)
        if _BACKEND_RE.search(line) and not _BACKEND_NEGATIVE.search(line):
            return {"declared": "vulkan", "observed": line[:200], "evidence": "engine_log", "status": "OBSERVED"}
    return {"declared": "vulkan", "observed": None, "evidence": "none", "status": "UNVERIFIED"}


def _new_rid() -> str:
    return uuid.uuid4().hex[:16]


async def _create_subprocess(*argv, **kwargs):
    return await asyncio.create_subprocess_exec(*argv, **kwargs)


# ----------------------------------------------------------------------------- process control

# MEDIA-LIFECYCLE. `reconcile_orphans` below cleans up at the NEXT start; that leaves a window
# (backend dead, engine alive) in which nothing bounds the engine — it holds RAM and the GPU for
# as long as it likes. The window is closed by the kernel, not by a resident service: every engine
# child is assigned to a Windows Job Object owned by THIS process with KILL_ON_JOB_CLOSE. When the
# owner disappears — clean exit, crash or TerminateProcess alike — the last handle closes and the
# kernel terminates the job. Nothing outside this job is ever touched, so a foreign engine (another
# Bossman, the owner's own run) is unaffected. On POSIX there is no equivalent primitive we are
# willing to take (PR_SET_PDEATHSIG needs a fork hook in a threaded process), so the guard reports
# itself unavailable and the sidecar/reconcile path stays the only mechanism there.
_JOB_HANDLE = None
_JOB_STATE = "unknown"
_JOB_LOCK = threading.Lock()

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def _create_owner_job():
    """Create (once) the kill-on-close job object that owns every engine child."""
    global _JOB_HANDLE, _JOB_STATE
    if os.name != "nt":
        _JOB_STATE = "unsupported_platform"
        return None
    with _JOB_LOCK:
        if _JOB_HANDLE is not None or _JOB_STATE.startswith("unavailable"):
            return _JOB_HANDLE
        try:
            import ctypes
            from ctypes import wintypes

            class _IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in
                            ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                             "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class _BASIC_LIMIT(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                            ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _EXTENDED_LIMIT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            handle = k32.CreateJobObjectW(None, None)   # unnamed + non-inheritable: only we hold it
            if not handle:
                _JOB_STATE = "unavailable:CreateJobObject"
                return None
            info = _EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                               ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(handle)
                _JOB_STATE = "unavailable:SetInformationJobObject"
                return None
            _JOB_HANDLE, _JOB_STATE = handle, "active"
            return handle
        except (OSError, AttributeError, ValueError) as exc:  # pragma: no cover - hostile host
            _JOB_STATE = f"unavailable:{type(exc).__name__}"
            return None


def bind_to_owner_lifetime(pid: int, *, expected_create_time=None) -> bool:
    """Tie one engine process to this process's lifetime. Returns True when the kernel now
    guarantees it dies with us. Never raises: a host that refuses job objects degrades to the
    sidecar/reconcile path, it does not lose the job."""
    handle = _create_owner_job()
    if handle is None:
        return False
    # Identity check before we take any power over the pid: a pid that is no longer the process we
    # spawned must never be adopted (and therefore never killed) by us.
    if expected_create_time is not None and not _same_process(_proc_identity(pid), pid, expected_create_time):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        target = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
        if not target:
            return False
        try:
            return bool(k32.AssignProcessToJobObject(handle, target))
        finally:
            k32.CloseHandle(target)
    except (OSError, AttributeError, ValueError):  # pragma: no cover - hostile host
        return False


def lifecycle_guard_state() -> str:
    """'active' | 'unknown' | 'unsupported_platform' | 'unavailable:<why>' — reported, never assumed."""
    return _JOB_STATE


def _proc_identity(pid: int) -> dict | None:
    import psutil
    try:
        ps = psutil.Process(pid)
        with ps.oneshot():
            return {"pid": pid, "create_time": ps.create_time(), "argv": list(ps.cmdline()),
                    "exe": ps.exe() if hasattr(ps, "exe") else None, "status": ps.status()}
    except (psutil.Error, OSError):
        return None


def _same_process(identity: dict | None, pid, create_time) -> bool:
    if identity is None or pid is None or create_time is None:
        return False
    try:
        return abs(float(identity["create_time"]) - float(create_time)) < 1.0
    except (TypeError, ValueError):
        return False


def _kill_tree(pid: int, *, expected_create_time=None, wait_s: float = KILL_WAIT_S) -> list[int]:
    """Kill pid and all descendants; the root is only touched when its identity matches."""
    import psutil
    killed = []
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return killed
    if expected_create_time is not None and not _same_process(_proc_identity(pid), pid, expected_create_time):
        return killed
    procs = []
    with contextlib.suppress(psutil.Error):
        procs = root.children(recursive=True)
    procs.append(root)
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()
            killed.append(p.pid)
    with contextlib.suppress(psutil.Error):
        psutil.wait_procs(procs, timeout=wait_s)
    return killed


def _sidecar_path(work: Path, rid: str) -> Path:
    return Path(work) / f"{rid}.job.json"


def _write_sidecar(path: Path, record: dict, *, exclusive: bool = False) -> None:
    data = json.dumps(record, ensure_ascii=False, sort_keys=True)
    if exclusive:
        with path.open("x", encoding="utf-8") as fh:
            fh.write(data)
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def _sidecar_files(record: dict, work: Path) -> list[Path]:
    out = []
    values = [record.get("raw"), record.get("init")]
    extra = record.get("segment_files")
    if isinstance(extra, list):
        values += extra
    for value in values:
        if isinstance(value, str) and value:
            p = Path(value)
            if p.parent == work:  # never delete outside the work dir, whatever the sidecar says
                out.append(p)
    rid = record.get("rid")
    if isinstance(rid, str) and re.fullmatch(r"[0-9a-f]{16}", rid):
        out.extend(work.glob(f"{rid}*.part"))
    return out


def reconcile_orphans(work_dir, *, kill: bool = True) -> dict:
    """After a restart: find engine processes recorded by sidecars whose owner (the Bossman process)
    is gone, kill only those whose PID identity (create_time + argv/exe) still matches, remove stale
    work files. PID reuse is reported, never killed. Returns a report; never raises."""
    import psutil
    work = Path(work_dir)
    report = {"work_dir": str(work), "scanned": 0, "killed": [], "would_kill": [], "pid_reused": [],
              "already_gone": [], "skipped_live_owner": [], "removed_files": [], "errors": [],
              "overdue": [], "guard": lifecycle_guard_state()}
    if not work.is_dir():
        return report
    me = _proc_identity(os.getpid())
    for side in sorted(work.glob("*.job.json")):
        report["scanned"] += 1
        try:
            record = json.loads(side.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("sidecar: object required")
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            report["errors"].append({"file": side.name, "error": type(exc).__name__})
            with contextlib.suppress(OSError):
                side.unlink()
            continue
        rid = record.get("rid") or side.name[:-len(".job.json")]
        owner_pid, owner_ct = record.get("owner_pid"), record.get("owner_create_time")
        owner_alive = _same_process(_proc_identity(owner_pid) if owner_pid else None, owner_pid, owner_ct)
        if owner_alive:
            # Our own live job (two workers in one process) or another live Bossman instance.
            report["skipped_live_owner"].append(rid)
            continue
        # Evidence first: an orphan that blew its own recorded budget is named as such, whether or
        # not its pid is still around. Silence about a blown budget is not a clean restart.
        limit = record.get("budget_at") or record.get("deadline_at")
        if isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit < time.time():
            report["overdue"].append(rid)
        pid, ct = record.get("pid"), record.get("create_time")
        ident = _proc_identity(pid) if isinstance(pid, int) and pid > 0 else None
        if ident is not None and ident.get("status") == psutil.STATUS_ZOMBIE:
            ident = None
        if ident is not None:
            same_time = _same_process(ident, pid, ct)
            argv_ok = bool(record.get("argv")) and ident["argv"] == record.get("argv")
            exe_ok = bool(record.get("exe")) and ident.get("exe") == record.get("exe")
            if same_time and (argv_ok or exe_ok):
                if kill:
                    _kill_tree(pid, expected_create_time=ct)
                    report["killed"].append(rid)
                else:
                    report["would_kill"].append(rid)
                    continue
            else:
                report["pid_reused"].append(rid)
        elif pid:
            report["already_gone"].append(rid)
        for p in _sidecar_files(record, work) + [side]:
            try:
                if p.exists() or p.is_symlink():
                    p.unlink()
                    report["removed_files"].append(p.name)
            except OSError as exc:
                report["errors"].append({"file": p.name, "error": type(exc).__name__})
    return report


# ----------------------------------------------------------------------------- input decoding

def _decode_start_image(item: dict) -> tuple[bytes, str]:
    """Strict: role, data URI shape, size limit, real Pillow decode, format whitelist."""
    if item.get("role") not in ALLOWED_INPUT_ROLES:
        raise ValueError(f"media.role: only {sorted(ALLOWED_INPUT_ROLES)} accepted")
    uri = item.get("data_uri")
    if not isinstance(uri, str) or not uri.startswith("data:") or "," not in uri:
        raise ValueError("media.data_uri: data URI required")
    head, b64 = uri.split(",", 1)
    if ";base64" not in head:
        raise ValueError("media.data_uri: base64 data URI required")
    if len(b64) > (MAX_INPUT_BYTES * 4) // 3 + 4:
        raise ValueError(f"media: reference exceeds {MAX_INPUT_BYTES} bytes")
    try:
        data = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("media.data_uri: invalid base64") from exc
    if not data or len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"media: reference empty or exceeds {MAX_INPUT_BYTES} bytes")
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            if fmt not in ALLOWED_INPUT_FORMATS:
                raise ValueError(f"media: image format {fmt} not accepted")
            if probe.width * probe.height > MAX_INPUT_PIXELS:
                raise ValueError("media: image too large")
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()  # full decode, not just the header
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("media:"):
            raise
        raise ValueError(f"media: not a decodable image ({type(exc).__name__})") from exc
    return data, ".png" if fmt == "PNG" else ".jpg"


class SdCppFailure(ProviderFailure):
    """Named provider failure with a safe, credential-free detail."""

    def __init__(self, reason: str, detail: str):
        super().__init__(ProviderStatus("failed", reason))
        self.detail = detail
        self.args = (f"failed: {reason} ({detail})",)


# ----------------------------------------------------------------------------- provider

class SdCppProvider:
    name = "sdcpp"
    hard_timeout_s: float | None = None   # default: catalog deadline_seconds + 60
    min_free_bytes: int | None = None     # default: MIN_FREE_BYTES[surface]

    def __init__(self, cfg: dict, storage_root: Path, model: dict, *, studio_job_id=None):
        self.cfg = cfg
        self.root = Path(storage_root).resolve()
        self.model = model
        self.studio_job_id = studio_job_id
        self.work = self.root / "engine-work"
        self.work.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict] = {}
        self.traces: dict[str, dict] = {}
        try:
            self.reconcile_report = reconcile_orphans(self.work)
        except Exception as exc:  # pragma: no cover - reconciliation must never block a job
            self.reconcile_report = {"error": type(exc).__name__}
        if self.hard_timeout_s is None:
            self.hard_timeout_s = float(model.get("deadline_seconds") or 3600) + 60
        # The catalog default; any other value (class or instance override) is explicit
        # and wins over the work-proportional budget.
        self._catalog_timeout_s = float(model.get("deadline_seconds") or 3600) + 60
        if self.min_free_bytes is None:
            self.min_free_bytes = MIN_FREE_BYTES.get(model.get("surface"), MIN_FREE_BYTES["image"])

    # -- inspection helpers (tests / diagnostics)
    def job_record(self, rid: str) -> dict:
        return self._jobs[rid]

    def failure_detail(self, rid: str) -> str | None:
        job = self._jobs.get(rid)
        return None if job is None else job.get("failure_detail")

    # -- submit
    async def submit(self, plane: GenerationPlane) -> Submitted:
        if plane.model not in ENGINES:
            raise ValueError("model: unknown sdcpp engine")
        if not isinstance(plane.prompt, str) or not plane.prompt.strip():
            raise ValueError("prompt: required")
        settings = apply_length(validate_settings(self.model, plane.settings))
        video = self.model["surface"] == "video"
        segments = segments_for(settings) if video else 1
        init_data = None
        if plane.media:
            if not video:
                raise ValueError("media: image model is text-to-image only")
            if len(plane.media) != 1:
                raise ValueError("media: exactly one start image")
            init_data, init_ext = await asyncio.to_thread(_decode_start_image, plane.media[0])
        free = shutil.disk_usage(self.work).free
        if free < self.min_free_bytes:
            raise SdCppFailure("provider_down", f"disk space {free} bytes below minimum {self.min_free_bytes}")
        try:
            verified = await asyncio.to_thread(verify_engine_files, self.cfg, plane.model, mode="submit")
            binary = await asyncio.to_thread(verify_engine_binary, self.cfg)
        except (ValueError, OSError, KeyError) as exc:
            raise SdCppFailure("provider_down", f"model files failed verification: {exc}") from exc
        files = {role: info["path"] for role, info in verified.items()}
        rid = _new_rid()
        if rid in self._jobs:
            raise ValueError("rid: duplicate request id")
        if segments == 1:
            raws = [self.work / f"{rid}.{'webm' if video else 'png'}"]
            frame_files: list[Path] = []
        else:
            raws = [self.work / f"{rid}-s{i}.webm" for i in range(segments)]
            frame_files = [self.work / f"{rid}-s{i}-last.png" for i in range(segments - 1)]
        raw = raws[0]
        init = self.work / f"{rid}-start{init_ext}" if init_data is not None else None
        argv = _argv(self.cfg, plane.model, plane, settings, files, raw, init)
        me = _proc_identity(os.getpid()) or {}
        explicit = self.hard_timeout_s != self._catalog_timeout_s
        segment_s = float(self.hard_timeout_s) if explicit else segment_deadline_s(self.model, settings)
        budget_s = segment_s * segments if explicit else job_budget_s(self.model, settings, segments)
        started = time.time()
        record = {"version": 1, "rid": rid, "pid": None, "create_time": None, "argv": argv,
                  "exe": None, "raw": str(raw), "init": None if init is None else str(init),
                  "started": started, "studio_job_id": self.studio_job_id, "model": plane.model,
                  "owner_pid": os.getpid(), "owner_create_time": me.get("create_time"),
                  # The budget travels WITH the job: a later process reading this sidecar can tell
                  # an orphan that is still inside its budget from one that blew straight past it.
                  "segments": segments, "deadline_s": segment_s, "budget_s": budget_s,
                  "deadline_at": started + segment_s, "budget_at": started + budget_s}
        if segments > 1:
            record["segment_files"] = [str(p) for p in raws[1:] + frame_files]
        sidecar = _sidecar_path(self.work, rid)
        try:
            _write_sidecar(sidecar, record, exclusive=True)
        except FileExistsError:
            raise ValueError("rid: a job with this request id already exists in the work dir")
        if init is not None:
            init.write_bytes(init_data)
        job = {"rid": rid, "settings": settings, "raw": raw, "init": init, "canceled": False, "fetched": False,
               "argv": argv, "files": files, "verified": verified, "binary": binary, "started": record["started"],
               "log": [], "proc": None, "pid": None, "create_time": None, "returncode": None, "peak_rss": 0,
               "elapsed_s": None, "failure": None, "failure_detail": None, "raw_meta": None,
               "sidecar": sidecar, "record": record, "state": "pending",
               "plane": plane, "raws": raws, "frame_files": frame_files, "segment_results": [],
               "hard_timeout_s": segment_s, "budget_at": record["budget_at"]}
        self._jobs[rid] = job
        job["task"] = asyncio.create_task(self._run(rid, job))
        return Submitted(rid, cancel_ref=rid)

    # -- run
    async def _spawn(self, job: dict):
        """Create the engine process, or None when the job was cancelled first."""
        if job["canceled"]:
            return None
        from bcc.video_studio.media import child_priority_kwargs, lower_child_priority
        job["state"] = "spawning"
        proc = await _create_subprocess(
            *job["argv"], stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, cwd=str(self.work), limit=1 << 20, **child_priority_kwargs())
        lower_child_priority(proc.pid)
        # MEDIA-LIFECYCLE: the kernel, not a future restart, owns the engine's lifetime.
        # Descendants the engine spawns inherit the job, so the whole tree goes with us.
        job["lifecycle_bound"] = bind_to_owner_lifetime(proc.pid)
        return proc

    async def _run(self, rid: str, job: dict) -> None:
        try:
            await self._run_inner(job)
        except asyncio.CancelledError:
            job["failure"] = job.get("failure") or "canceled"
            self._kill_job(job)
            raise
        except Exception as exc:  # never let the task explode: status() reports the named failure
            job["failure"], job["failure_detail"] = "provider_down", f"{type(exc).__name__}: {exc}"[:500]
            self._kill_job(job)
        finally:
            job["elapsed_s"] = round(time.time() - job["started"], 2)
            job["state"] = "done" if job.get("state") != "canceled" else "canceled"
            if job.get("failure") is not None and not job.get("canceled"):
                # A stop or a blown deadline keeps the segments the engine actually finished
                # ("стоп обрывает на том, что уже есть"); any other failure leaves nothing
                # behind, exactly as before — no stale raw/init/sidecar until a restart.
                self._cleanup_work(job, keep_partial=bool(self._done_segment_files(job)))

    async def _run_inner(self, job: dict) -> None:
        """One engine run per segment; segment k>0 is I2V from the last frame of segment k-1."""
        raws = job.get("raws") or [job["raw"]]
        for i, raw in enumerate(raws):
            if i:
                last = job["frame_files"][i - 1]
                try:
                    await self._last_frame(raws[i - 1], last)
                except (ValueError, OSError, RuntimeError) as exc:
                    job["failure"] = "provider_down"
                    job["failure_detail"] = f"segment {i}: last frame extraction failed: {exc}"[:500]
                    return
                seed = job["settings"].get("seed")
                seg = {**job["settings"], "seed": None if seed is None else (seed + i) % 2**31}
                job["argv"] = _argv(self.cfg, self.model["id"], job["plane"], seg, job["files"], raw, last)
                job["raw"], job["raw_meta"], job["proc"], job["returncode"] = raw, None, None, None
                job["record"].update({"argv": job["argv"], "pid": None, "create_time": None,
                                      "exe": None, "segment_index": i,
                                      "deadline_at": time.time() + job["hard_timeout_s"]})
                with contextlib.suppress(OSError):
                    _write_sidecar(job["sidecar"], job["record"])
            t0 = time.time()
            await self._run_engine(job)
            if job["failure"] is not None or job["canceled"] or job["raw_meta"] is None:
                if job["canceled"] and job["failure"] is None:
                    job["failure"] = "canceled"
                if len(raws) > 1 and job.get("failure_detail"):
                    job["failure_detail"] = f"segment {i + 1}/{len(raws)}: {job['failure_detail']}"[:500]
                return
            if len(raws) > 1:
                job["segment_results"].append({
                    "index": i, "name": raw.name, "sha256": await asyncio.to_thread(_sha256, raw),
                    "bytes": raw.stat().st_size, "duration_ms": job["raw_meta"].get("duration_ms"),
                    "image_to_video": i > 0 or job["init"] is not None,
                    "start_frame_sha256": None if i == 0 else await asyncio.to_thread(_sha256, job["frame_files"][i - 1]),
                    "returncode": job["returncode"], "elapsed_s": round(time.time() - t0, 2),
                    "argv": [Path(job["argv"][0]).name] + [a if not os.path.isabs(a) else Path(a).name
                                                           for a in job["argv"][1:]]})

    async def _last_frame(self, raw: Path, out: Path) -> None:
        from bcc.video_studio.media import binary, process
        # -update 1 keeps overwriting one image: what remains is the last decoded frame.
        await process([binary("ffmpeg"), "-v", "error", "-y", "-sseof", "-1", "-i", str(raw),
                       "-update", "1", str(out)], timeout=300)
        if not out.is_file() or out.stat().st_size == 0:
            raise RuntimeError("no frame written")

    async def _run_engine(self, job: dict) -> None:
        import psutil
        try:
            proc = await self._spawn(job)
        except (OSError, ValueError) as exc:  # missing binary, permission, bad argv
            job["failure"], job["failure_detail"] = "provider_down", f"spawn failed: {type(exc).__name__}: {exc}"[:500]
            return
        if proc is None:
            job["failure"] = "canceled"
            return
        job["proc"], job["pid"] = proc, proc.pid
        ident = _proc_identity(proc.pid) or {}
        job["create_time"] = ident.get("create_time")
        job["record"].update({"pid": proc.pid, "create_time": job["create_time"], "exe": ident.get("exe")})
        with contextlib.suppress(OSError):
            _write_sidecar(job["sidecar"], job["record"])
        if job["canceled"]:  # cancel raced the spawn: the process must not run inference
            self._kill_job(job)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), KILL_WAIT_S)
            job["failure"] = "canceled"
            return
        job["state"] = "running"
        try:
            ps = psutil.Process(proc.pid)
        except psutil.Error:
            ps = None

        async def sample():
            while proc.returncode is None:
                with contextlib.suppress(psutil.Error, AttributeError):
                    job["peak_rss"] = max(job["peak_rss"], ps.memory_info().rss)
                await asyncio.sleep(0.5)

        sampler = asyncio.create_task(sample())
        # Two limits, whichever bites first: this segment's budget and what is left of the whole
        # job's. A chain of segments cannot outlast the job ceiling by adding one more segment.
        limit = float(job.get("hard_timeout_s") or self.hard_timeout_s)
        budget_at, which = job.get("budget_at"), "segment deadline"
        if budget_at is not None and budget_at - time.time() < limit:
            limit, which = max(1.0, budget_at - time.time()), "job budget"
        try:
            async with asyncio.timeout(limit):
                await self._pump_log(proc, job)
                await proc.wait()
        except TimeoutError:
            job["failure"], job["failure_detail"] = "timeout", f"engine exceeded {which} {limit:.0f}s"
            self._kill_job(job)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), KILL_WAIT_S)
            return
        finally:
            sampler.cancel()
            with contextlib.suppress(BaseException):
                await sampler
        job["returncode"] = proc.returncode
        if job["canceled"]:
            job["failure"] = "canceled"
            return
        if proc.returncode != 0:
            job["failure"], job["failure_detail"] = "malformed", f"engine exit code {proc.returncode}"
            return
        # Zero exit is not completion: the raw output must probe and decode.
        try:
            from bcc.studio.runtime import verify_file
            meta = await verify_file(job["raw"], self.model["surface"])
        except (ValueError, OSError, KeyError, RuntimeError) as exc:
            job["failure"] = "malformed"
            job["failure_detail"] = f"engine output failed verification: {type(exc).__name__}: {exc}"[:500]
            return
        job["raw_meta"] = meta

    async def _pump_log(self, proc, job: dict) -> None:
        """Read stdout in chunks; cap line length and count. Never blocks on one giant line."""
        buf = bytearray()
        truncated = False  # inside an over-long line: discard bytes up to its newline

        def push(line: bytes):
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                job["log"] = (job["log"] + [text[:MAX_LOG_LINE]])[-MAX_LOG_LINES:]

        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            while buf:
                nl = buf.find(b"\n")
                if truncated:
                    if nl < 0:
                        buf.clear()
                        break
                    del buf[:nl + 1]
                    truncated = False
                    continue
                if nl >= 0:
                    push(bytes(buf[:nl]))
                    del buf[:nl + 1]
                    continue
                if len(buf) > MAX_LOG_LINE:
                    push(bytes(buf[:MAX_LOG_LINE - 24]) + b" ...[line truncated]")
                    buf.clear()
                    truncated = True
                break
        if buf and not truncated:
            push(bytes(buf))

    def _kill_job(self, job: dict) -> None:
        proc = job.get("proc")
        if proc is not None and proc.returncode is None:
            _kill_tree(proc.pid, expected_create_time=job.get("create_time"))
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()

    # -- status
    async def status(self, request_id: str) -> ProviderStatus:
        job = self._jobs.get(request_id)
        if job is None:
            raise ValueError("request_id: unknown to this adapter")
        if job["canceled"]:
            return ProviderStatus("canceled")
        task = job["task"]
        if not task.done():
            return ProviderStatus("running")
        if task.cancelled() or task.exception() is not None:
            job["failure_detail"] = job.get("failure_detail") or "engine task did not finish"
            return ProviderStatus("failed", "provider_down")
        if job["failure"] == "canceled":
            return ProviderStatus("canceled")
        if job["failure"] in ("provider_down", "timeout", "malformed"):
            return ProviderStatus("failed", job["failure"])
        raw: Path = job["raw"]
        if job["raw_meta"] is None or not raw.is_file() or raw.stat().st_size == 0:
            job["failure_detail"] = job.get("failure_detail") or "engine produced no verified output"
            return ProviderStatus("failed", "malformed")
        return ProviderStatus("completed", outputs=(ProviderOutput(f"{request_id}:0"),))

    # -- cancel
    async def cancel(self, request_id: str) -> None:
        job = self._jobs.get(request_id)
        if job is None:
            return
        job["canceled"] = True
        job["state"] = "canceled"
        self._kill_job(job)
        task = job.get("task")
        if task is not None and not task.done():
            # Без asyncio.shield (BL-098, Python 3.14): ожидание через
            # await_shared не отменяет саму задачу, если отменят ожидающего.
            from bcc.single_flight import await_shared
            try:
                await asyncio.wait_for(await_shared(task), CANCEL_WAIT_S)
            except (asyncio.TimeoutError, TimeoutError):
                task.cancel()
            except BaseException:
                pass
            with contextlib.suppress(BaseException):
                await task
        self._kill_job(job)
        proc = job.get("proc")
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), KILL_WAIT_S)
        self._cleanup_work(job, keep_partial=bool(self._done_segment_files(job)))

    def _cleanup_work(self, job: dict, *, keep_partial: bool = False) -> None:
        # "Стоп обрывает на том, что уже есть": the segments the engine actually finished are
        # work that was really done. They survive a stop/timeout until they are fetched or the
        # job is discarded; everything else (half-written raws, start frames, .part files) goes.
        spared = set()
        if keep_partial:
            spared = {Path(p) for p in self._done_segment_files(job)}
        for p in (job["raw"], job["init"], *job.get("raws", ()), *job.get("frame_files", ()),
                  *self.work.glob(f"{job['rid']}*.part")):
            if p is not None and Path(p) not in spared:
                with contextlib.suppress(OSError):
                    Path(p).unlink(missing_ok=True)
        if spared:
            return                                     # the sidecar still owns the kept segments
        with contextlib.suppress(OSError):
            Path(job["sidecar"]).unlink(missing_ok=True)

    # -- partial results ("стоп обрывает на том, что уже есть")
    @staticmethod
    def _done_segment_files(job: dict) -> list[Path]:
        """Segment files the engine finished AND that passed verification, in chain order.

        sd.cpp writes a segment's container only when that segment ends, so a segment that was
        interrupted has no salvageable bytes at all — there is no half clip to rescue, and we
        never pretend otherwise by padding, repeating a frame or interpolating.
        """
        raws = job.get("raws") or []
        if len(raws) < 2:
            return []                                  # single pass: nothing finished before the end
        done = len(job.get("segment_results") or [])
        return [Path(p) for p in raws[:done] if Path(p).is_file() and Path(p).stat().st_size > 0]

    def partial_result(self, request_id: str) -> dict | None:
        """What can honestly be salvaged from a stopped or timed-out chain, or None.

        None means there is nothing to save — not that saving failed. `detail` says which.
        """
        job = self._jobs.get(request_id)
        if job is None:
            raise ValueError("request_id: unknown to this adapter")
        if job.get("fetched"):
            return None
        stopped = bool(job.get("canceled")) or job.get("failure") in ("canceled", "timeout")
        if not stopped:
            return None
        total = len(job.get("raws") or [job["raw"]])
        files = self._done_segment_files(job)
        results = (job.get("segment_results") or [])[:len(files)]
        fps = job["settings"].get("fps") or 0
        frames = job["settings"].get("frames") or 0
        # The joined clip drops the repeated start frame of every later segment.
        joined = frames + (frames - 1) * (len(files) - 1) if files and frames else 0
        reason = "timeout" if job.get("failure") == "timeout" and not job.get("canceled") else "canceled"
        return {"request_id": request_id, "partial": True, "complete": False, "reason": reason,
                "segments_done": len(files), "segments_total": total,
                "duration_s": round(joined / fps, 3) if fps and joined else None,
                "duration_s_if_complete": declared_duration_s(job["settings"]),
                "segments": [dict(r) for r in results],
                "detail": f"{len(files)} of {total} segments finished" if files else
                          "no segment finished: the engine writes a segment only when it ends, "
                          "so there are no bytes to save"}

    async def fetch_partial(self, request_id: str, dest: Path) -> Fetched:
        """Join the finished segments into one file, marked partial in the trace.

        Refuses when nothing finished: an empty or broken file is never handed over as a result.
        """
        job = self._jobs.get(request_id)
        if job is None:
            raise ValueError("request_id: unknown to this adapter")
        if job.get("fetched"):
            raise ValueError("output: already fetched")
        info = self.partial_result(request_id)
        if info is None:
            raise ValueError("output: the job was not stopped; there is no partial result")
        files = self._done_segment_files(job)
        if not files:
            raise SdCppFailure("malformed", info["detail"])
        if self.model["surface"] != "video":
            raise ValueError("output: partial results exist only for segment chains")
        dest = Path(dest).resolve()
        if self.root not in dest.parents:
            raise PermissionError("output path escapes media root")
        tmp = dest.with_name(f".{dest.name}.{request_id}.part")
        try:
            try:
                argv = await self._transcode(files[0], tmp, job, raws=files)
            except ValueError as exc:
                raise SdCppFailure("provider_down", f"transcode failed: {str(exc)[:300]}") from exc
            from bcc.studio.runtime import verify_file
            try:
                final_meta = await verify_file(tmp, self.model["surface"])
            except (ValueError, OSError, KeyError) as exc:
                raise SdCppFailure("malformed",
                                   f"partial output failed verification: {type(exc).__name__}") from exc
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        job["fetched"] = True
        chain = info["segments"]
        observed_ms = final_meta.get("duration_ms")
        self.traces[request_id] = {
            "engine": "stable-diffusion.cpp",
            "engine_release_declared": self.cfg["manifest"].get("engine", {}).get("release"),
            "backend": observe_backend(job["log"]),
            # The loudest field first: this is NOT the clip that was asked for.
            "partial": True, "complete": False, "stopped_by": info["reason"],
            "segments_done": info["segments_done"], "segments_total": info["segments_total"],
            "generation": {
                "source": "engine_process_interrupted",
                "engine_binary": {k: v for k, v in job["binary"].items() if k != "path"},
                "argv": [Path(job["argv"][0]).name] + [a if not os.path.isabs(a) else Path(a).name
                                                       for a in job["argv"][1:]],
                "frames": job["settings"].get("frames"), "fps": job["settings"].get("fps"),
                "length": job["settings"].get("length"), "segments": chain,
                "duration_s_declared": info["duration_s"],
                "duration_s_requested": info["duration_s_if_complete"],
                "duration_s_observed": None if observed_ms is None else round(observed_ms / 1000, 3),
                "image_to_video": job["init"] is not None,
                "synthesis": "none: only segments the engine itself finished; no frame was "
                             "repeated, padded or interpolated to reach the requested length",
                "log_tail": job["log"][-12:],
            },
            "transcode": {"tool": "ffmpeg", "operation": "concat_finished_segments_only",
                          "argv": [Path(argv[0]).name] + [a if not os.path.isabs(a) else Path(a).name
                                                          for a in argv[1:]],
                          "input_sha256": [s["sha256"] for s in chain],
                          "output_sha256": final_meta["sha256"], "output_bytes": final_meta["bytes"]},
            "import": {"dest_name": dest.name, "sha256": final_meta["sha256"],
                       "bytes": final_meta["bytes"], "mime": "video/mp4", "atomic": True},
        }
        self._cleanup_work(job)
        s = job["settings"]
        return Fetched(dest, final_meta["bytes"], "video/mp4", final_meta["sha256"],
                       s.get("width"), s.get("height"),
                       None if final_meta.get("duration_ms") is None else int(final_meta["duration_ms"]))

    def discard_partial(self, request_id: str) -> None:
        """The owner does not want the stump: drop the kept segments and the sidecar."""
        job = self._jobs.get(request_id)
        if job is not None:
            self._cleanup_work(job)

    # -- fetch
    async def _transcode(self, raw: Path, tmp: Path, job: dict, raws=None) -> list[str]:
        from bcc.video_studio.media import binary, process
        raws = list(raws) if raws else (job.get("raws") or [raw])
        if len(raws) == 1:
            # Container transcode of the model's own frames (VP8 .webm → H.264 .mp4).
            argv = [binary("ffmpeg"), "-v", "error", "-y", "-i", str(raw), "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                    "-movflags", "+faststart", "-f", "mp4", str(tmp)]
        else:
            # Segment chain: drop the first frame of every later segment (it repeats the
            # previous segment's last frame, which was its I2V start), then concatenate.
            inputs = [a for p in raws for a in ("-i", str(p))]
            parts = ["[0:v]setpts=PTS-STARTPTS[v0]"] + [
                f"[{i}:v]trim=start_frame=1,setpts=PTS-STARTPTS[v{i}]" for i in range(1, len(raws))]
            chain = "".join(f"[v{i}]" for i in range(len(raws))) + f"concat=n={len(raws)}:v=1:a=0[out]"
            argv = [binary("ffmpeg"), "-v", "error", "-y", *inputs,
                    "-filter_complex", ";".join(parts + [chain]), "-map", "[out]", "-an",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                    "-movflags", "+faststart", "-f", "mp4", str(tmp)]
        await process(argv, timeout=600)
        return argv

    async def fetch(self, output: ProviderOutput, dest: Path) -> Fetched:
        rid = output.ref.split(":", 1)[0]
        job = self._jobs.get(rid)
        if job is None or job["canceled"]:
            raise ValueError("output: unknown or canceled")
        if job["fetched"]:
            raise ValueError("output: already fetched")
        dest = Path(dest).resolve()
        if self.root not in dest.parents:
            raise PermissionError("output path escapes media root")
        raw: Path = job["raw"]
        if job["raw_meta"] is None or not raw.is_file():
            raise ValueError("output: engine output not verified")
        tmp = dest.with_name(f".{dest.name}.{rid}.part")
        video = self.model["surface"] == "video"
        raw_sha = await asyncio.to_thread(_sha256, raw)
        chain = job.get("segment_results") or []
        if len(job.get("raws") or ()) > 1 and len(chain) != len(job["raws"]):
            raise ValueError("output: segment chain incomplete")
        try:
            if video:
                try:
                    argv = await self._transcode(raw, tmp, job)
                except ValueError as exc:  # ffmpeg missing / encoder failure: named, not a 500
                    raise SdCppFailure("provider_down", f"transcode failed: {str(exc)[:300]}") from exc
                transcode = {"tool": "ffmpeg", "argv": [Path(argv[0]).name] + [
                    a if not os.path.isabs(a) else Path(a).name for a in argv[1:]],
                    "input_sha256": [s["sha256"] for s in chain] if chain else raw_sha}
                if chain:
                    transcode["operation"] = "concat_segments_drop_repeated_start_frame"
                mime = "video/mp4"
            else:
                await asyncio.to_thread(shutil.copyfile, raw, tmp)
                transcode = {"tool": "copy", "argv": None, "input_sha256": raw_sha}
                mime = "image/png"
            if job["canceled"]:
                raise ValueError("output: canceled during fetch")
            from bcc.studio.runtime import verify_file
            try:
                final_meta = await verify_file(tmp, self.model["surface"])
            except (ValueError, OSError, KeyError) as exc:
                raise SdCppFailure("malformed", f"transcoded output failed verification: {type(exc).__name__}") from exc
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        job["fetched"] = True
        data_sha = final_meta["sha256"]
        transcode.update({"output_sha256": data_sha, "output_bytes": final_meta["bytes"]})
        s = job["settings"]
        raw_meta = job["raw_meta"]
        entry = self.cfg["manifest"]["engines"][ENGINES[self.model["id"]]]["files"]
        observed_ms = final_meta.get("duration_ms") if chain else raw_meta.get("duration_ms")
        observed_duration = None if observed_ms is None else round(observed_ms / 1000, 3)
        self.traces[rid] = {
            "engine": "stable-diffusion.cpp",
            "engine_release_declared": self.cfg["manifest"].get("engine", {}).get("release"),
            "backend": observe_backend(job["log"]),
            "generation": {
                "source": "engine_process",
                "engine_binary": {k: v for k, v in job["binary"].items() if k != "path"},
                "model_files": {k: {"name": v["name"], "bytes": v["bytes"],
                                    "sha256_expected": v["sha256_expected"], "sha256_observed": v["sha256_observed"],
                                    "revision_declared": v.get("revision_declared", entry[k].get("revision")),
                                    "verification": {"method": v["method"], "mode": "submit"}}
                                for k, v in job["verified"].items()},
                "argv": [Path(job["argv"][0]).name] + [a if not os.path.isabs(a) else Path(a).name
                                                       for a in job["argv"][1:]],
                "returncode": job["returncode"], "elapsed_s": job["elapsed_s"], "peak_rss_bytes": job["peak_rss"],
                "raw_output": {"name": raw.name, "sha256": raw_sha, "bytes": raw.stat().st_size,
                               "container": "matroska" if video else "png",
                               "width": raw_meta.get("width"), "height": raw_meta.get("height")},
                "frames": s.get("frames"), "fps": s.get("fps"), "length": s.get("length"),
                "segments": chain or None,
                "duration_s_declared": declared_duration_s(s), "duration_s_observed": observed_duration,
                "image_to_video": job["init"] is not None,
                "log_tail": job["log"][-12:],
            },
            "transcode": transcode,
            "import": {"dest_name": dest.name, "sha256": data_sha, "bytes": final_meta["bytes"], "mime": mime,
                       "atomic": True},
        }
        self._cleanup_work(job)
        return Fetched(dest, final_meta["bytes"], mime, data_sha, s.get("width"), s.get("height"),
                       None if final_meta.get("duration_ms") is None else int(final_meta["duration_ms"]))
