"""Bossman Motion Concert: one-command local concert video for a full track.

    python concert.py all                 # plan + run + assemble + qc (resumable)
    python concert.py plan                # shot manifest from analysis.json (no GPU)
    python concert.py run [--limit N] [--dry-run]
    python concert.py assemble [--dry-run]
    python concert.py qc

Stdlib only (runs in the ComfyUI venv or any Python 3.10+). Heavy work:
  * singer shots (kind=s2v): Wan2.2 S2V-14B fp8 through a headless ComfyUI (ROCm) on port 8189,
    graph identical to benchmark/run_s2v_benchmark.py, audio = the matching slice of the original track;
  * inserts (kind=i2v): Wan2.2-TI2V-5B through stable-diffusion.cpp Vulkan `sd-cli -M vid_gen`.
ONE heavy job at a time: all S2V shots first in one ComfyUI session, ComfyUI is stopped, then sd-cli inserts.
STOP: create the file <pipeline>/STOP (checked between shots and while waiting for the GPU) or press Ctrl+C.
Resume: a shot whose output exists and passes ffprobe is skipped. The manifest is written after every shot.
The original mp3 is never modified: the final mux copies its audio stream (-c:a copy) untouched.
A failed shot is assembled as a dark-red GAP slate and listed in qc.json; it is never filled by looping a clip.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

# ----------------------------------------------------------------------------------------------- defaults
PROJECT = Path(os.environ.get("MOTION_CONCERT_PROJECT", r"C:\Users\asd\Bossman\Bossman_Motion_Concert_Faint"))
TRACK_NAME = "TOKYO SAMURAI SYNDICATE - Linkin Park - Faint (Cyberpunk Shamisen Cover).mp3"
TRACK_SHA256 = "23FAF7AD168A460DB9DF24F54054C7B0552DB160B2EC6FDF0D31AD59F122BE36"
TRACK_DURATION = 168.829388
ANALYSIS = Path(os.environ.get("MOTION_CONCERT_ANALYSIS", r"C:\Users\asd\Bossman\motion-concert\work\analysis.json"))
COMFY = Path(r"C:\Users\asd\Bossman\media-runtime\ComfyUI")
COMFY_PY = Path(r"C:\Users\asd\Bossman\media-runtime\comfyui-venv\Scripts\python.exe")
COMFY_PORT = 8189
SDCLI = Path(r"C:\Users\asd\Bossman\media-runtime\sdcpp\vulkan\sd-cli.exe")
TI2V_DIR = Path(r"C:\Users\asd\Bossman\models\media\wan22-ti2v-5b")

OUT_W, OUT_H, OUT_FPS = 854, 480, 24          # final 16:9; 832x480 sources are pillarboxed, never stretched
GEN_W, GEN_H = 832, 480
S2V = {"model": "wan2.2_s2v_14B_fp8_scaled.safetensors", "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
       "vae": "wan_2.1_vae.safetensors", "audio_encoder": "wav2vec2_large_english_fp16.safetensors",
       "steps": 4, "cfg": 1.0, "shift": 8.0, "sampler": "uni_pc", "scheduler": "simple",
       # owner-PC 06.10: 20 steps/CFG6 = ~2.9 h per shot; Lightning 4 steps/CFG1 = ~34 min, checked on fragment A
       "lora": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
       "width": GEN_W, "height": GEN_H, "length": 77, "fps": 16}
S2V_MAX_S = S2V["length"] / S2V["fps"]        # 4.8125 s: one S2V generation must cover its slot
I2V = {"model": "Wan2.2-TI2V-5B-Q8_0.gguf", "vae": "wan2.2_vae.safetensors", "t5xxl": "umt5-xxl-encoder-Q8_0.gguf",
       "steps": 20, "cfg": 5.0, "flow_shift": 5.0, "sampler": "euler", "width": GEN_W, "height": GEN_H, "fps": 24}
I2V_MAX_S = 5.0                               # 121 frames @24
BLOCK_S = 4.8
S2V_TARGET = 0.70
SEED_BASE = 20261006

SINGER = ("a fictional adult Japanese woman, lead singer of a cyberpunk rock band, short black bob haircut with neon "
          "blue streak, black leather jacket over a white top, silver earrings")
S2V_PROMPT = {
    "high": "{who}, sings powerfully into a handheld microphone on a big concert stage, lip movements match the singing, "
            "energetic head and shoulder movement on the beat, hair swings, sweat on her face, strobing magenta and cyan "
            "stage lights, LED screen glow behind her, realistic live concert footage",
    "mid": "{who}, sings passionately into a handheld microphone on a concert stage, lip movements match the singing, "
           "natural head and shoulder movement with the music, magenta and cyan stage lights, LED screen glow behind her, "
           "realistic live concert footage",
    "low": "{who}, sings softly and intensely into a handheld microphone, lip movements match the singing, small head "
           "movements, dim cyan backlight, light haze, realistic live concert footage",
}
S2V_NEG = "static image, frozen face, blurry, distorted face, extra fingers, deformed hands, text, watermark, cartoon"
INSERTS = ["band_wide", "shamisen_hands", "crowd", "stage_led"]
I2V_PROMPT = {
    "band_wide": "wide shot of a large concert stage, the cyberpunk rock band performs, the singer moves at the microphone, "
                 "light beams sweep through the haze, raised hands of the crowd wave in the foreground, slow camera push-in, "
                 "realistic live concert footage",
    "shamisen_hands": "close-up of the hands of a musician playing a shamisen, the plectrum strikes the strings in rhythm, "
                      "strings vibrate, neon cyan and magenta lights flicker, light haze, realistic live concert footage",
    "crowd": "a huge stadium crowd jumps and waves raised hands and phone lights, magenta and cyan light beams sweep "
             "over the crowd, haze, slow camera pan, realistic live concert footage",
    "stage_led": "giant LED screen graphics pulse in magenta and cyan, moving head lights sweep across the stage, haze "
                 "drifts, slow camera pan, realistic concert footage",
}
REFS = {"singer": "singer_soul2_a_0.png", "band_wide": "band_wide_0.png", "shamisen_hands": "shamisen_hands_0.png",
        "crowd": "crowd_0.png", "stage_led": "stage_led_0.png"}
ENERGY_RANK = {"high": 2, "mid": 1, "low": 0}


class Stop(Exception):
    pass


# ----------------------------------------------------------------------------------------------- helpers
def sha256(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def run_q(cmd: list[str], timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def ffprobe(p: Path) -> dict | None:
    if not p.is_file() or not shutil.which("ffprobe"):
        return None
    r = run_q(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(p)], 120)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def video_duration(info: dict | None) -> float:
    if not info:
        return 0.0
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            d = s.get("duration") or info.get("format", {}).get("duration")
            try:
                return float(d)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def shot_output_ok(p: Path, need_s: float) -> tuple[bool, str]:
    info = ffprobe(p)
    if info is None:
        return False, "ffprobe_failed_or_missing"
    d = video_duration(info)
    if d + 1.0 / OUT_FPS < need_s:
        return False, f"too_short:{d:.3f}s<{need_s:.3f}s"
    return True, ""


def i2v_frames(seconds: float) -> int:
    """Wan TI2V frame count: 4k+1, at least the slot length at 24 fps."""
    n = max(9, math.ceil(seconds * I2V["fps"]) + 1)
    return n + ((1 - n) % 4)


def peak_rss_gb(proc: subprocess.Popen | None) -> float:
    """Peak working set of a (possibly exited, still-handled) child on Windows; -1 if unknown."""
    if proc is None or os.name != "nt":
        return -1.0
    try:
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
        if fn(int(proc._handle), ctypes.byref(c), c.cb):
            return round(c.PeakWorkingSetSize / 2**30, 2)
    except Exception:
        pass
    return -1.0


class Paths:
    def __init__(self, project: Path):
        self.project = project
        self.root = project / "pipeline"
        self.work = self.root / "work"
        self.manifest = self.work / "manifest.json"
        self.shots = self.work / "shots"
        self.refs = self.work / "refs"
        self.logs = self.work / "logs"
        self.segs = self.work / "segments"
        self.dry = self.work / "dryrun"
        self.out = self.root / "out"
        self.stop = self.root / "STOP"
        self.final = self.out / "faint_concert_854x480.mp4"
        self.qc = self.out / "qc.json"


def load_manifest(P: Paths) -> dict:
    if not P.manifest.is_file():
        raise SystemExit(f"no manifest at {P.manifest}; run `concert.py plan` first")
    return json.loads(P.manifest.read_text(encoding="utf-8"))


def save_manifest(P: Paths, m: dict) -> None:
    P.work.mkdir(parents=True, exist_ok=True)
    m["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = P.manifest.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, P.manifest)


# ----------------------------------------------------------------------------------------------- plan
def _split(start: float, end: float, beats: list[float], max_len: float) -> list[tuple[float, float]]:
    """Split [start,end) into ~BLOCK_S blocks, each <= max_len, internal cuts snapped to beats when possible."""
    length = end - start
    n = max(1, math.ceil(length / BLOCK_S - 1e-9))
    while length / n > max_len:
        n += 1
    cuts = [start]
    for i in range(1, n):
        ideal = start + length * i / n
        near = [b for b in beats if abs(b - ideal) <= 0.35 and cuts[-1] + 0.5 < b < end - 0.5]
        c = min(near, key=lambda b: abs(b - ideal)) if near else ideal
        if c - cuts[-1] > max_len:
            c = ideal
        cuts.append(c)
    cuts.append(end)
    out = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a > max_len:            # snapping pushed a block over the limit -> split it evenly
            k = math.ceil((b - a) / max_len)
            out += [(a + (b - a) * j / k, a + (b - a) * (j + 1) / k) for j in range(k)]
        else:
            out.append((a, b))
    return out


def beat_times(analysis: dict, duration: float) -> tuple[list[float], str]:
    """Beat times from analysis.json; analyze_track.py stores only the count, so fall back to a tempo grid."""
    b = analysis.get("beats")
    if isinstance(b, list) and b:
        return [float(x) for x in b], "analysis.beats (times)"
    bpm, first = analysis.get("tempo_bpm"), analysis.get("first_beat_s")
    if bpm and first is not None:
        step = 60.0 / float(bpm)
        n = int((duration - float(first)) / step) + 1
        return [float(first) + i * step for i in range(n)], f"grid tempo_bpm={bpm} from first_beat_s={first}"
    return [], "none (no beat info)"


def build_plan(analysis: dict, duration: float, refs_dir: Path, s2v_target: float = S2V_TARGET) -> list[dict]:
    beats, _ = beat_times(analysis, duration)
    secs = sorted(analysis["sections"], key=lambda s: s["start_s"])
    # Sections tile the timeline; the first starts at 0 and the last ends at the real audio duration.
    bounds = [0.0] + [float(s["start_s"]) for s in secs[1:]] + [duration]
    blocks = []
    for i, s in enumerate(secs):
        a, b = bounds[i], bounds[i + 1]
        if b - a <= 1e-6:
            continue
        for x, y in _split(a, b, beats, min(S2V_MAX_S, I2V_MAX_S)):
            blocks.append({"start_s": x, "end_s": y, "section": int(s.get("index", i)),
                           "energy": s.get("energy", "mid"), "rms": float(s.get("mean_rms", 0.0)),
                           "boundary_confidence": s.get("boundary_confidence")})
    # 70% S2V by seconds: most energetic sections first (vocal presence is NOT detected; energy is the proxy).
    order = sorted(range(len(blocks)), key=lambda j: (-ENERGY_RANK.get(blocks[j]["energy"], 1), -blocks[j]["rms"], j))
    target = s2v_target * duration
    s2v_s = 0.0
    for j in order:
        blk = blocks[j]
        dur = blk["end_s"] - blk["start_s"]
        if s2v_s + dur / 2 <= target:
            blk["kind"] = "s2v"
            s2v_s += dur
        else:
            blk["kind"] = "i2v"
    ref_sha = {k: sha256(refs_dir / v) for k, v in REFS.items()}
    shots, ins = [], 0
    for n, blk in enumerate(blocks, 1):
        dur = blk["end_s"] - blk["start_s"]
        if blk["kind"] == "s2v":
            ref = "singer"
            prompt = S2V_PROMPT.get(blk["energy"], S2V_PROMPT["mid"]).format(who=SINGER)
            params = dict(S2V)
        else:
            ref = INSERTS[ins % len(INSERTS)]
            ins += 1
            prompt = I2V_PROMPT[ref]
            # owner-PC 06.10: TI2V-5B via sd.cpp measured ~4.7 h per 5 s insert; inserts use the S2V engine instead
            params = dict(S2V)
        shots.append({
            "id": f"s{n:03d}", "take": 0, "start_s": round(blk["start_s"], 6), "end_s": round(blk["end_s"], 6),
            "dur_s": round(dur, 6), "start_frame": round(blk["start_s"] * OUT_FPS), "end_frame": round(blk["end_s"] * OUT_FPS),
            "kind": blk["kind"], "section": blk["section"], "energy": blk["energy"],
            "reference": {"name": ref, "path": str(refs_dir / REFS[ref]), "sha256": ref_sha[ref]},
            "prompt": prompt, "negative": S2V_NEG,
            "seed": SEED_BASE + n, "params": params, "status": "planned", "output": None, "runs": []})
    return shots


def summarize(shots: list[dict], duration: float) -> dict:
    by = {}
    for s in shots:
        k = by.setdefault(s["kind"], {"shots": 0, "seconds": 0.0})
        k["shots"] += 1
        k["seconds"] = round(k["seconds"] + s["end_s"] - s["start_s"], 3)
    covered = round(sum(s["end_s"] - s["start_s"] for s in shots), 6)
    ins = {}
    for s in shots:
        if s["kind"] == "i2v":
            ins[s["reference"]["name"]] = ins.get(s["reference"]["name"], 0) + 1
    st = {}
    for s in shots:
        st[s["status"]] = st.get(s["status"], 0) + 1
    return {"shots": len(shots), "by_kind": by, "inserts_by_ref": ins, "status": st,
            "covered_s": covered, "duration_s": duration,
            "s2v_fraction": round(by.get("s2v", {}).get("seconds", 0) / duration, 3) if duration else 0,
            "min_shot_s": round(min(s["end_s"] - s["start_s"] for s in shots), 3),
            "max_shot_s": round(max(s["end_s"] - s["start_s"] for s in shots), 3)}


def cmd_plan(P: Paths, a) -> dict:
    analysis_p = Path(a.analysis)
    analysis = json.loads(analysis_p.read_text(encoding="utf-8"))
    audio = Path(a.audio)
    info = ffprobe(audio)
    if a.duration:
        duration = float(a.duration)
    elif info:
        duration = float(info["format"]["duration"])
    else:
        duration = TRACK_DURATION
    refs_dir = Path(a.refs)
    shots = build_plan(analysis, duration, refs_dir)
    old = {}
    if P.manifest.is_file():
        try:
            old = {s["id"]: s for s in json.loads(P.manifest.read_text(encoding="utf-8"))["shots"]}
        except Exception:
            old = {}
    for s in shots:   # keep run history/outputs of shots whose slot and recipe did not change
        o = old.get(s["id"])
        if o and all(o.get(k) == s.get(k) for k in ("start_s", "end_s", "kind", "prompt", "seed")) \
                and o.get("reference", {}).get("sha256") == s["reference"]["sha256"]:
            for k in ("take", "status", "output", "runs", "reject_reason"):
                if k in o:
                    s[k] = o[k]
    astream = next((x for x in (info or {}).get("streams", []) if x.get("codec_type") == "audio"), {})
    m = {"version": 1, "project": str(P.project),
         "audio": {"path": str(audio), "exists": audio.is_file(), "sha256": sha256(audio),
                   "expected_sha256": TRACK_SHA256, "duration_s": duration,
                   "start_time": float(astream.get("start_time", 0) or 0), "codec": astream.get("codec_name")},
         "analysis": {"path": str(analysis_p), "sha256": sha256(analysis_p), "tempo_bpm": analysis.get("tempo_bpm"),
                      "sections": len(analysis.get("sections", [])), "beat_source": beat_times(analysis, duration)[1],
                      "not_claimed": analysis.get("not_claimed")},
         "output": {"width": OUT_W, "height": OUT_H, "fps": OUT_FPS, "total_frames": round(duration * OUT_FPS)},
         "shots": shots}
    if m["audio"]["exists"] and m["audio"]["sha256"] != TRACK_SHA256 and not a.audio_override_ok:
        print(f"WARNING: track sha256 {m['audio']['sha256']} != expected {TRACK_SHA256}", file=sys.stderr)
    m["summary"] = summarize(shots, duration)
    save_manifest(P, m)
    print(json.dumps(m["summary"], ensure_ascii=False))
    print(f"manifest: {P.manifest}")
    return m


# ----------------------------------------------------------------------------------------------- run
def check_stop(P: Paths) -> None:
    if P.stop.exists():
        raise Stop(f"STOP file present: {P.stop}")


def _api(method: str, path: str, body=None, timeout=60):
    req = urllib.request.Request(f"http://127.0.0.1:{COMFY_PORT}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def comfy_up() -> bool:
    try:
        _api("GET", "/system_stats", timeout=3)
        return True
    except Exception:
        return False


def foreign_sdcli() -> bool:
    if os.name != "nt":
        return False
    r = run_q(["tasklist", "/FI", "IMAGENAME eq sd-cli.exe", "/NH"], 30)
    return "sd-cli.exe" in r.stdout


def wait_gpu_free(P: Paths, why) -> None:
    """Another heavy job owns the GPU -> wait (honouring STOP/Ctrl+C) instead of competing for it."""
    t0 = time.time()
    while why():
        check_stop(P)
        if time.time() - t0 < 1 or int(time.time() - t0) % 300 < 15:
            print("GPU busy (foreign sd-cli.exe or ComfyUI on :%d); waiting... (create STOP to abort)" % COMFY_PORT,
                  flush=True)
        time.sleep(15)


def prep_ref(src: Path, dst: Path, dry: bool) -> list[str]:
    """Cover-scale + centre-crop to 832x480 (no stretching)."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
           f"scale={GEN_W}:{GEN_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={GEN_W}:{GEN_H}",
           "-frames:v", "1", str(dst)]
    if not dry and not dst.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = run_q(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"ref prep failed: {r.stderr[-300:]}")
    return cmd


def s2v_graph(shot: dict, audio_name: str, image_name: str, prefix: str) -> dict:
    p = shot["params"]
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": p["model"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": p["text_encoder"], "type": "wan", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": p["vae"]}},
        "4": {"class_type": "AudioEncoderLoader", "inputs": {"audio_encoder_name": p["audio_encoder"]}},
        "5": {"class_type": "LoadAudio", "inputs": {"audio": audio_name}},
        "6": {"class_type": "AudioEncoderEncode", "inputs": {"audio_encoder": ["4", 0], "audio": ["5", 0]}},
        "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": shot["prompt"], "clip": ["2", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": shot["negative"] or "", "clip": ["2", 0]}},
        "10": {"class_type": "ModelSamplingSD3", "inputs": {"shift": p["shift"], "model": ["15", 0] if p.get("lora") else ["1", 0]}},
        "11": {"class_type": "WanSoundImageToVideo", "inputs": {"positive": ["8", 0], "negative": ["9", 0], "vae": ["3", 0],
               "width": p["width"], "height": p["height"], "length": p["length"], "batch_size": 1,
               "audio_encoder_output": ["6", 0], "ref_image": ["7", 0]}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["10", 0], "seed": shot["seed"], "steps": p["steps"],
               "cfg": p["cfg"], "sampler_name": p["sampler"], "scheduler": p["scheduler"], "positive": ["11", 0],
               "negative": ["11", 1], "latent_image": ["11", 2], "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": prefix}},
        **({"15": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": p["lora"], "strength_model": 1.0,
                                                                  "model": ["1", 0]}}} if p.get("lora") else {}),
    }


def audio_slice_cmd(audio: Path, start: float, seconds: float, dst: Path) -> list[str]:
    # Reads the track, writes a NEW wav slice for the audio encoder; the mp3 itself is untouched.
    return ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.6f}", "-i", str(audio), "-t", f"{seconds:.6f}",
            "-c:a", "pcm_s16le", str(dst)]


def i2v_cmd(shot: dict, init_png: Path, prompt_file: Path, out_avi: Path) -> list[str]:
    p = shot["params"]
    return [str(SDCLI), "-M", "vid_gen",
            "--diffusion-model", str(TI2V_DIR / p["model"]), "--vae", str(TI2V_DIR / p["vae"]),
            "--t5xxl", str(TI2V_DIR / p["t5xxl"]),
            "--video-frames", str(p["video_frames"]), "--fps", str(p["fps"]), "--flow-shift", str(p["flow_shift"]),
            "--cfg-scale", str(p["cfg"]), "--sampling-method", p["sampler"], "-i", str(init_png),
            "--prompt-file", str(prompt_file), "-W", str(p["width"]), "-H", str(p["height"]),
            "--steps", str(p["steps"]), "-s", str(shot["seed"]), "-o", str(out_avi)]


def to_mp4_cmd(src_args: list[str], dst: Path) -> list[str]:
    return ["ffmpeg", "-v", "error", "-y", *src_args, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "18", str(dst)]


class Comfy:
    def __init__(self, P: Paths):
        self.P, self.proc, self.log, self.device = P, None, None, None

    def start(self) -> None:
        if self.proc is not None:
            return
        wait_gpu_free(self.P, lambda: comfy_up() or foreign_sdcli())
        self.P.logs.mkdir(parents=True, exist_ok=True)
        self.log = open(self.P.logs / "comfyui.log", "a", encoding="utf-8")
        self.proc = subprocess.Popen([str(COMFY_PY), "main.py", "--listen", "127.0.0.1", "--port", str(COMFY_PORT),
                                      "--disable-auto-launch"], cwd=COMFY, stdout=self.log, stderr=subprocess.STDOUT,
                                     env={**os.environ, "TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL": "1"})
        for _ in range(300):
            if comfy_up():
                break
            if self.proc.poll() is not None:
                raise RuntimeError(f"ComfyUI exited at start, code {self.proc.returncode}; see {self.P.logs/'comfyui.log'}")
            time.sleep(2)
        stats = _api("GET", "/system_stats")
        devs = stats.get("devices") or [{}]
        self.device = f"ComfyUI:{devs[0].get('name', '?')} ({devs[0].get('type', '?')})"

    def stop(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(30)
        except Exception:
            self.proc.kill()
        self.proc = None
        if self.log:
            self.log.close()

    def generate(self, P: Paths, m: dict, shot: dict, rec: dict, timeout: float) -> Path:
        self.start()
        tag = f"mc_{shot['id']}_t{shot['take']}"
        img = f"mc_ref_{shot['reference']['name']}_{GEN_W}x{GEN_H}.png"
        prep_ref(Path(shot["reference"]["path"]), COMFY / "input" / img, False)
        wav = f"{tag}.wav"
        r = run_q(audio_slice_cmd(Path(m["audio"]["path"]), shot["start_s"], S2V_MAX_S + 0.1, COMFY / "input" / wav))
        if r.returncode != 0:
            raise RuntimeError(f"audio slice failed: {r.stderr[-300:]}")
        graph = s2v_graph(shot, wav, img, tag)
        P.logs.mkdir(parents=True, exist_ok=True)
        (P.logs / f"{tag}_workflow_api.json").write_text(json.dumps(graph, indent=1), encoding="utf-8")
        pid = _api("POST", "/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})["prompt_id"]
        t0 = time.time()
        try:
            while True:
                time.sleep(5)
                h = _api("GET", f"/history/{pid}")
                if pid in h:
                    st = h[pid].get("status", {})
                    if st.get("status_str") != "success":
                        raise RuntimeError(f"comfy_status:{st.get('status_str')}:{json.dumps(st.get('messages', [])[-2:])[:300]}")
                    break
                if self.proc.poll() is not None:
                    raise RuntimeError(f"server_exited:{self.proc.returncode}")
                if time.time() - t0 > timeout:
                    _api("POST", "/interrupt", {})
                    raise RuntimeError(f"timeout:{timeout}s")
        except KeyboardInterrupt:
            try:
                _api("POST", "/interrupt", {})
            except Exception:
                pass
            raise
        rec["device"] = self.device
        rec["peak_rss_gb"] = peak_rss_gb(self.proc)
        rec["peak_rss_note"] = "ComfyUI server process peak working set since server start (cumulative)"
        frames = sorted((COMFY / "output").glob(f"{tag}_*.png"))
        rec["frames"] = len(frames)
        if not frames:
            raise RuntimeError("no_output_frames")
        out = P.shots / f"{tag}.mp4"
        r = run_q(to_mp4_cmd(["-framerate", str(shot["params"]["fps"]), "-i",
                              str(COMFY / "output" / f"{tag}_%05d_.png")], out))
        if r.returncode != 0:
            raise RuntimeError(f"frames_to_mp4_failed:{r.stderr[-300:]}")
        return out


def i2v_generate(P: Paths, shot: dict, rec: dict, timeout: float) -> Path:
    wait_gpu_free(P, lambda: foreign_sdcli() or comfy_up())
    tag = f"mc_{shot['id']}_t{shot['take']}"
    init = P.refs / f"{shot['reference']['name']}_{GEN_W}x{GEN_H}.png"
    prep_ref(Path(shot["reference"]["path"]), init, False)
    pf = P.logs / f"{tag}_prompt.txt"
    pf.write_text(shot["prompt"], encoding="utf-8")
    avi = P.shots / f"{tag}.avi"
    cmd = i2v_cmd(shot, init, pf, avi)
    with open(P.logs / f"{tag}_sdcli.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(SDCLI.parent))
        t0 = time.time()
        try:
            while proc.poll() is None:
                if time.time() - t0 > timeout:
                    proc.kill()
                    proc.wait()
                    raise RuntimeError(f"timeout:{timeout}s")
                time.sleep(2)
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            raise
    rec["peak_rss_gb"] = peak_rss_gb(proc)
    rec["device"] = "sd.cpp Vulkan (Radeon 8060S)"
    rec["exit_code"] = proc.returncode
    produced = next((c for c in (avi, Path(str(avi) + ".avi")) if c.is_file()), None)
    if proc.returncode != 0 or produced is None:
        raise RuntimeError(f"sdcli_failed:exit={proc.returncode}:output={'yes' if produced else 'none'}")
    out = P.shots / f"{tag}.mp4"
    r = run_q(to_mp4_cmd(["-i", str(produced)], out))
    if r.returncode != 0:
        raise RuntimeError(f"avi_to_mp4_failed:{r.stderr[-300:]}")
    return out


def model_files(shot: dict) -> dict:
    p = shot["params"]
    if shot["kind"] == "s2v":
        return {"engine": "ComfyUI WanSoundImageToVideo", "model": p["model"], "text_encoder": p["text_encoder"],
                "vae": p["vae"], "audio_encoder": p["audio_encoder"]}
    return {"engine": f"sd.cpp {SDCLI.name} -M vid_gen", "model": p["model"], "vae": p["vae"], "t5xxl": p["t5xxl"]}


def dry_print(P: Paths, m: dict, shot: dict) -> None:
    tag = f"mc_{shot['id']}_t{shot['take'] + 1}"
    print(f"\n# {shot['id']} {shot['kind']} {shot['start_s']:.3f}-{shot['end_s']:.3f}s ref={shot['reference']['name']} seed={shot['seed']}")
    if shot["kind"] == "s2v":
        img = COMFY / "input" / f"mc_ref_singer_{GEN_W}x{GEN_H}.png"
        print(subprocess.list2cmdline(prep_ref(Path(shot["reference"]["path"]), img, True)))
        print(subprocess.list2cmdline(audio_slice_cmd(Path(m["audio"]["path"]), shot["start_s"], S2V_MAX_S + 0.1,
                                                      COMFY / "input" / f"{tag}.wav")))
        P.dry.mkdir(parents=True, exist_ok=True)
        g = P.dry / f"{tag}_workflow_api.json"
        g.write_text(json.dumps(s2v_graph(shot, f"{tag}.wav", img.name, tag), indent=1), encoding="utf-8")
        print(f"POST http://127.0.0.1:{COMFY_PORT}/prompt  <- {g}")
        print(subprocess.list2cmdline(to_mp4_cmd(["-framerate", "16", "-i", str(COMFY / "output" / f"{tag}_%05d_.png")],
                                                 P.shots / f"{tag}.mp4")))
    else:
        init = P.refs / f"{shot['reference']['name']}_{GEN_W}x{GEN_H}.png"
        print(subprocess.list2cmdline(prep_ref(Path(shot["reference"]["path"]), init, True)))
        print(subprocess.list2cmdline(i2v_cmd(shot, init, P.logs / f"{tag}_prompt.txt", P.shots / f"{tag}.avi")))


def cmd_run(P: Paths, a) -> int:
    if not P.manifest.is_file():
        cmd_plan(P, a)
    m = load_manifest(P)
    shots = m["shots"]
    for d in (P.shots, P.refs, P.logs):
        d.mkdir(parents=True, exist_ok=True)
    # resume: anything whose output already passes ffprobe is done
    todo = []
    for s in shots:
        if s.get("output"):
            ok, why = shot_output_ok(Path(s["output"]), s["end_s"] - s["start_s"])
            if ok:
                s["status"] = "done"
                continue
            s["status"], s["reject_reason"] = "failed", f"existing output invalid: {why}"
        todo.append(s)
    # one heavy engine at a time: all S2V (one ComfyUI session), then all I2V (sd-cli)
    todo.sort(key=lambda s: (0 if s["kind"] == "s2v" else 1, s["start_s"]))
    if a.limit:
        todo = todo[: a.limit]
    print(f"queue: {len(todo)} shot(s) ({sum(1 for s in todo if s['kind']=='s2v')} s2v, "
          f"{sum(1 for s in todo if s['kind']=='i2v')} i2v); done already: {sum(1 for s in shots if s['status']=='done')}")
    if a.dry_run:
        for s in todo:
            dry_print(P, m, s)
        print("\n(dry-run: nothing executed, manifest statuses unchanged)")
        return 0
    comfy = Comfy(P)
    code = 0
    try:
        for s in todo:
            check_stop(P)
            if "audio_encoder" not in s["params"] and comfy.proc is not None:
                comfy.stop()                        # free the GPU before sd-cli starts
            s["take"] = int(s.get("take", 0)) + 1
            rec = {"take": s["take"], "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": s["seed"],
                   "steps": s["params"]["steps"], **model_files(s)}
            s["status"] = "running"
            save_manifest(P, m)
            t0 = time.time()
            try:
                out = comfy.generate(P, m, s, rec, a.shot_timeout) if "audio_encoder" in s["params"] \
                    else i2v_generate(P, s, rec, a.shot_timeout)
                ok, why = shot_output_ok(out, s["end_s"] - s["start_s"])
                if not ok:
                    raise RuntimeError(f"reject:{why}")
                s["output"], s["status"], s["reject_reason"] = str(out), "done", None
                rec["status"] = "done"
            except KeyboardInterrupt:
                rec["status"], rec["reject_reason"] = "interrupted", "Ctrl+C"
                s["status"] = "interrupted"
                raise
            except Stop:
                raise
            except Exception as e:  # recorded, visible in qc as a gap; queue continues
                rec["status"], rec["reject_reason"] = "failed", str(e)[:500]
                s["status"], s["reject_reason"] = "failed", str(e)[:500]
                code = 1
            finally:
                rec["wall_s"] = round(time.time() - t0, 1)
                s.setdefault("runs", []).append(rec)
                m["summary"] = summarize(shots, m["audio"]["duration_s"])
                save_manifest(P, m)
            print(json.dumps({"id": s["id"], "kind": s["kind"], "status": s["status"], "wall_s": rec["wall_s"],
                              "reject": s.get("reject_reason")}, ensure_ascii=False), flush=True)
    except Stop as e:
        print(f"STOPPED: {e}. Re-run the same command to resume.")
        return 3
    except KeyboardInterrupt:
        print("INTERRUPTED (Ctrl+C). Manifest saved; re-run the same command to resume.")
        return 130
    finally:
        comfy.stop()
        save_manifest(P, m)
    return code


# ----------------------------------------------------------------------------------------------- assemble
def segment_cmd(src: Path | None, frames: int, dst: Path) -> list[str]:
    enc = ["-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(OUT_FPS), str(dst)]
    if src is None:   # visible GAP slate (dark red), never a looped clip
        return ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x3a0000:s={OUT_W}x{OUT_H}:r={OUT_FPS}", *enc]
    vf = (f"fps={OUT_FPS},scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    return ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, *enc]


def concat_cmd(list_file: Path, dst: Path) -> list[str]:
    return ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(dst)]


def mux_cmd(video: Path, audio: Path, dst: Path) -> list[str]:
    """Original mp3 stream copied bit-exact: no re-encode, no trim, no -shortest."""
    return ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", str(dst)]


def cmd_assemble(P: Paths, a) -> int:
    m = load_manifest(P)
    shots = sorted(m["shots"], key=lambda s: s["start_s"])
    total = m["output"]["total_frames"]
    P.segs.mkdir(parents=True, exist_ok=True)
    P.out.mkdir(parents=True, exist_ok=True)
    lines, gaps, cmds = [], [], []
    for i, s in enumerate(shots):
        f0 = s["start_frame"]
        f1 = total if i == len(shots) - 1 else shots[i + 1]["start_frame"]
        n = f1 - f0
        src = Path(s["output"]) if s.get("status") == "done" and s.get("output") else None
        if src is not None and not a.dry_run:
            ok, why = shot_output_ok(src, s["end_s"] - s["start_s"])
            if not ok:
                src = None
                s["reject_reason"] = f"assemble: {why}"
        if src is None:
            gaps.append({"id": s["id"], "kind": s["kind"], "start_s": s["start_s"], "end_s": s["end_s"],
                         "status": s.get("status"), "reason": s.get("reject_reason")})
        seg = P.segs / f"{s['id']}_{'gap' if src is None else 't%d' % s.get('take', 0)}.mp4"
        cmd = segment_cmd(src, n, seg)
        cmds.append(cmd)
        lines.append(f"file '{seg.as_posix()}'")
        if not a.dry_run:
            r = run_q(cmd, 900)
            if r.returncode != 0:
                raise SystemExit(f"segment {s['id']} failed: {r.stderr[-400:]}")
    lst = P.segs / "concat.txt"
    video = P.work / "video_only.mp4"
    cc = concat_cmd(lst, video)
    mc = mux_cmd(video, Path(m["audio"]["path"]), P.final)
    if a.dry_run:
        for c in cmds + [cc, mc]:
            print(subprocess.list2cmdline(c))
        print(f"(dry-run) segments={len(cmds)} gaps={len(gaps)} total_frames={total}")
        return 0
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for c in (cc, mc):
        r = run_q(c, 900)
        if r.returncode != 0:
            raise SystemExit(f"ffmpeg failed: {subprocess.list2cmdline(c)}\n{r.stderr[-400:]}")
    m["assemble"] = {"final": str(P.final), "total_frames": total, "gaps": gaps,
                     "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mux_cmd": mc}
    save_manifest(P, m)
    print(f"final: {P.final}  segments={len(cmds)} gaps={len(gaps)}")
    return 0


# ----------------------------------------------------------------------------------------------- qc
def _stream_md5(p: Path, sel: str) -> str | None:
    r = run_q(["ffmpeg", "-v", "error", "-i", str(p), "-map", sel, "-c", "copy", "-f", "md5", "-"], 300)
    return r.stdout.strip() if r.returncode == 0 else None


def cmd_qc(P: Paths, a) -> int:
    m = load_manifest(P)
    expected = TRACK_DURATION if abs(m["audio"]["duration_s"] - TRACK_DURATION) < 0.5 else m["audio"]["duration_s"]
    frame = 1.0 / OUT_FPS
    q = {"file": str(P.final), "exists": P.final.is_file(), "expected_duration_s": expected, "tolerance_s": frame,
         "checks": {}, "gaps": [], "verdict": "FAIL"}
    gaps = [{"id": s["id"], "kind": s["kind"], "start_s": s["start_s"], "end_s": s["end_s"], "status": s.get("status"),
             "reason": s.get("reject_reason")} for s in m["shots"] if s.get("status") != "done"]
    q["gaps"] = gaps
    q["gap_seconds"] = round(sum(g["end_s"] - g["start_s"] for g in gaps), 3)
    q["summary"] = summarize(m["shots"], m["audio"]["duration_s"])
    if q["exists"]:
        info = ffprobe(P.final) or {}
        st = info.get("streams", [])
        v = [s for s in st if s.get("codec_type") == "video"]
        au = [s for s in st if s.get("codec_type") == "audio"]
        fmt_d = float(info.get("format", {}).get("duration", 0) or 0)
        a_d = float(au[0].get("duration", 0) or 0) if au else 0.0
        v_d = float(v[0].get("duration", 0) or 0) if v else 0.0
        c = q["checks"]
        c["streams"] = {"video": len(v), "audio": len(au), "ok": len(v) == 1 and len(au) == 1,
                        "video_codec": v[0].get("codec_name") if v else None,
                        "size": f"{v[0].get('width')}x{v[0].get('height')}" if v else None,
                        "audio_codec": au[0].get("codec_name") if au else None}
        nb = int(v[0].get("nb_frames", 0) or 0) if v else 0
        c["duration"] = {"format_s": fmt_d, "video_s": v_d, "audio_s": a_d,
                         "audio_vs_expected_s": round(a_d - expected, 6), "video_vs_audio_s": round(v_d - a_d, 6),
                         "video_frames": nb, "expected_frames": m["output"]["total_frames"],
                         "ok": abs(a_d - expected) <= frame and abs(v_d - a_d) <= frame
                         and nb == m["output"]["total_frames"]}
        a_st = float(au[0].get("start_time", 0) or 0) if au else 0.0
        v_st = float(v[0].get("start_time", 0) or 0) if v else 0.0
        c["av_offset"] = {"video_start_s": v_st, "audio_start_s": a_st, "offset_s": round(a_st - v_st, 6),
                          "ok": abs(a_st - v_st) <= frame}
        src_md5 = _stream_md5(Path(m["audio"]["path"]), "0:a:0")
        out_md5 = _stream_md5(P.final, "0:a:0")
        c["audio_bit_exact"] = {"source_packets_md5": src_md5, "final_packets_md5": out_md5,
                                "ok": bool(src_md5) and src_md5 == out_md5}
        r = run_q(["ffmpeg", "-v", "error", "-i", str(P.final), "-f", "null", "-"], 1800)
        c["full_decode"] = {"returncode": r.returncode, "errors": r.stderr.strip()[-1000:],
                            "ok": r.returncode == 0 and not r.stderr.strip()}
        all_ok = all(x.get("ok") for x in c.values())
        q["verdict"] = "PASS" if all_ok and not gaps else ("FAIL_GAPS" if all_ok else "FAIL")
    q["not_verified"] = ["lip-sync quality", "instrument/hand accuracy", "face identity consistency between shots",
                         "vocal on/off times (not detected; energy used as proxy)", "visual artefacts / colour noise"]
    P.out.mkdir(parents=True, exist_ok=True)
    P.qc.write_text(json.dumps(q, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"verdict": q["verdict"], "gaps": len(gaps), "gap_seconds": q["gap_seconds"],
                      "checks": {k: v.get("ok") for k, v in q["checks"].items()}}, ensure_ascii=False))
    print(f"qc: {P.qc}")
    return 0 if q["verdict"] == "PASS" else 1


# ----------------------------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=str(PROJECT), help="project folder (pipeline/ lives inside)")
    ap.add_argument("--analysis", default=str(ANALYSIS))
    ap.add_argument("--audio", default=None, help="track (default <project>/input/<TRACK_NAME>)")
    ap.add_argument("--refs", default=None, help="stills dir (default <project>/reference/higgsfield_stills)")
    ap.add_argument("--duration", type=float, default=None, help="override audio duration (tests only)")
    ap.add_argument("--audio-override-ok", action="store_true", help="silence the sha256 mismatch warning")
    ap.add_argument("--limit", type=int, default=0, help="run at most N pending shots")
    ap.add_argument("--dry-run", action="store_true", help="plan + print commands, no GPU, no ffmpeg encode")
    ap.add_argument("--shot-timeout", type=float, default=5400, help="seconds per heavy job before it is failed")
    ap.add_argument("cmd", choices=["plan", "run", "assemble", "qc", "all"])
    a = ap.parse_args(argv)
    project = Path(a.project)
    a.audio = a.audio or str(project / "input" / TRACK_NAME)
    a.refs = a.refs or str(project / "reference" / "higgsfield_stills")
    P = Paths(project)
    if a.cmd == "plan":
        cmd_plan(P, a)
        return 0
    if a.cmd == "run":
        return cmd_run(P, a)
    if a.cmd == "assemble":
        return cmd_assemble(P, a)
    if a.cmd == "qc":
        return cmd_qc(P, a)
    cmd_plan(P, a)
    rc = cmd_run(P, a)
    if rc in (3, 130):
        return rc
    if a.dry_run:
        return cmd_assemble(P, a)
    cmd_assemble(P, a)
    return cmd_qc(P, a)


if __name__ == "__main__":
    sys.exit(main())
