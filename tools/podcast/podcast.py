"""Bossman Podcast — foundation: script by the best free LLM, local voices, rendered segments, a live spool.

    python tools/podcast/podcast.py promo   [--out DIR]       # 15 s English promo, hosts switch 7/5/3 s

Design (owner, 16.10): characters (Девушка/girl, Kisliy, p4ela) host on the owner's studio backgrounds; the text comes
from the best FREE model reachable now (cascade below, every hop logged); voices are local (Kokoro ONNX, CPU); faces
are stills composited on the studio now and Wan S2V talking heads later (GPU, ~30 min per 5 s on this PC, so a live
mode renders the cheap path and upgrades segments only when the GPU is idle). Likeness of real people is used only
with the owner-confirmed consent; output stays on this PC and goes only to the owner's pult.
Nothing here spends money: paid models are never in the cascade.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDIO = Path(os.environ.get("BOSSMAN_PODCAST_STUDIO", r"C:\Users\asd\Bossman\podcast-studio"))
TTS_PY = Path(os.environ.get("BOSSMAN_TTS_PY", r"C:\Users\asd\AppData\Local\Bossman\motion-venv\Scripts\python.exe"))
KOKORO = Path(os.environ.get("BOSSMAN_KOKORO_DIR", r"C:\Users\asd\AppData\Local\Bossman\motion-tts"))
FONT = "C\\:/Windows/Fonts/bahnschrift.ttf"
W, H, FPS = 1080, 1920, 30

CAST = {   # id -> display name, Kokoro voice, still
    "girl": {"name": "MIA", "voice": "af_heart", "image": "cast/girl.png"},
    "kisliy": {"name": "KISLIY", "voice": "am_michael", "image": "cast/kisliy.png"},
    "p4ela": {"name": "P4ELA", "voice": "am_fenrir", "image": "cast/p4ela.png"},
}
# free text models, best first (measured 16.10 on the podcast-banter prompt; :free / owner-declared free tier only)
CASCADE = [(1, "nvidia/nemotron-3.5-super-vl:free"), (12, "deepseek-v4.1-flash"), (1, "poolside/laguna-s-2.1:free"),
           (1, "nvidia/nemotron-3-super-120b-a12b:free"), (1, "google/gemma-4-31b-it:free")]
LOCAL_FALLBACK = "bossman-fast-qwen36-35b-a3b-q5:latest"
SYS = ("You write spoken lines for the Bossman Podcast, an AI-hosted show. Natural spoken English, contractions, short "
       "sentences, confident and warm, no stage directions, no emojis, no markdown. Return ONLY JSON.")
PROMO_PLAN = [("girl", 7.0), ("kisliy", 5.0), ("p4ela", 3.0)]


def run(cmd: list, timeout: float = 900) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed: {r.stderr[-400:]}")
    return r


def duration(p: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).stdout.strip())


# ------------------------------------------------------------------ text
async def chat(messages: list[dict], max_tokens: int = 3000) -> tuple[str, str, list[dict]]:
    """Ask the free cascade; returns (text, model, attempts). Keys stay in the Bossman vault (registry adapters)."""
    sys.path.insert(0, str(ROOT / "command-center"))
    attempts: list[dict] = []
    try:
        import sqlalchemy as sa
        from bcc.config import settings
        from bcc.db import Database, providers as providers_t
        from bcc.events import EventBus
        from bcc.providers import build_adapter
        from bcc.registry import Registry
        from bcc.secrets import Vault
        db = Database(settings.database_url)
        reg = Registry(db, Vault(settings.data_dir), EventBus(db))
        adapters = {}
        for pid, model in CASCADE:
            t = time.monotonic()
            try:
                if pid not in adapters:
                    async with db.session() as s:
                        row = (await s.execute(sa.select(providers_t).where(providers_t.c.id == pid))).mappings().first()
                    adapters[pid] = build_adapter("openai_compat", row["base_url"], reg.vault.decrypt(row["api_key_enc"]))
                r = await asyncio.wait_for(adapters[pid].chat(model, messages, max_tokens=max_tokens, temperature=0.8), 150)
                text = (r.text or "").strip()
                attempts.append({"model": model, "s": round(time.monotonic() - t, 1), "chars": len(text)})
                if text:
                    return text, model, attempts
            except Exception as exc:  # noqa: BLE001 - next hop
                attempts.append({"model": model, "s": round(time.monotonic() - t, 1),
                                 "error": f"{type(exc).__name__}: {str(exc)[:90]}"})
    except Exception as exc:  # noqa: BLE001 - registry unavailable: local only
        attempts.append({"model": "registry", "error": str(exc)[:120]})
    import urllib.request
    body = {"model": LOCAL_FALLBACK, "messages": messages, "stream": False, "think": False, "options": {"temperature": 0.8}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    text = json.load(urllib.request.urlopen(req, timeout=600))["message"]["content"].strip()
    attempts.append({"model": LOCAL_FALLBACK, "local": True})
    return text, LOCAL_FALLBACK, attempts


def parse_lines(text: str, allowed: set[str]) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    data = json.loads(m.group(0) if m else text)
    out = []
    for item in data:
        who = str(item.get("speaker", "")).strip().lower()
        line = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        if who in allowed and line:
            out.append({"speaker": who, "text": line})
    if not out:
        raise ValueError("model returned no usable lines")
    return out


def promo_prompt() -> str:
    parts = ", ".join(f'"{c}" speaks for {s:g} seconds (about {int(s * 2.4)} words)' for c, s in PROMO_PLAN)
    return ("Write a 15-second promo announcing a new feature: Bossman, the AI assistant, now runs its own podcast - "
            "AI hosts that talk live, switch every few seconds and can keep a show going for hours. Three hosts, in this "
            f"order: {parts}. The last line ends with a short call to action (no websites, links or handles). Output a JSON array like "
            '[{"speaker":"girl","text":"..."},{"speaker":"kisliy","text":"..."},{"speaker":"p4ela","text":"..."}].')


# ------------------------------------------------------------------ voice
TTS_SNIPPET = r"""
import json, sys, soundfile as sf
from kokoro_onnx import Kokoro
k = Kokoro(sys.argv[1] + r"\kokoro-v1.0.onnx", sys.argv[1] + r"\voices-v1.0.bin")
for job in json.load(open(sys.argv[2], encoding="utf-8")):
    samples, sr = k.create(job["text"], voice=job["voice"], speed=job.get("speed", 1.0), lang="en-us")
    sf.write(job["out"], samples, sr)
"""


def synthesize(jobs: list[dict], work: Path) -> None:
    spec = work / "tts_jobs.json"
    spec.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    script = work / "tts_run.py"
    script.write_text(TTS_SNIPPET, encoding="utf-8")
    run([TTS_PY, script, KOKORO, spec], timeout=1800)


def fit_voice(lines: list[dict], plan: list[tuple[str, float]], work: Path) -> list[dict]:
    """TTS each line; a line longer than its slot is re-spoken faster (max 1.25x) - words are never cut."""
    jobs = [{"text": l["text"], "voice": CAST[l["speaker"]]["voice"], "out": str(work / f"v{i}.wav")}
            for i, l in enumerate(lines)]
    synthesize(jobs, work)
    redo = []
    for i, (job, (_, slot)) in enumerate(zip(jobs, plan)):
        d = duration(Path(job["out"]))
        lines[i]["voice_s"] = round(d, 2)
        if d > slot - 0.25:
            job["speed"] = min(1.25, round(d / (slot - 0.35), 3))
            redo.append(job)
    if redo:
        synthesize(redo, work)
        for i, job in enumerate(jobs):
            lines[i]["voice_s"] = round(duration(Path(job["out"])), 2)
            lines[i]["speed"] = job.get("speed", 1.0)
    return lines


# ------------------------------------------------------------------ picture
def esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def wrap(text: str, width: int = 26) -> str:
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return "\n".join(out + [cur])


def ff_path(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:")


def render_segment(i: int, who: str, text: str, seconds: float, bg: Path, voice: Path, work: Path) -> Path:
    c = CAST[who]
    sub = work / f"sub{i}.txt"
    sub.write_text(wrap(text), encoding="utf-8", newline="\n")   # CRLF would draw as blank lines
    frames = int(round(seconds * FPS))
    flt = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},eq=brightness=-0.05[bg];"
        f"[1:v]scale=760:-1,crop=760:900,zoompan=z='min(1.0+0.0009*on,1.12)':d={frames}:s=760x900:fps={FPS},"
        f"pad=780:920:10:10:color=0x29d3ff[card];"
        f"[bg][card]overlay=(W-w)/2:250[v1];"
        f"[v1]drawbox=x=0:y=60:w={W}:h=96:color=black@0.55:t=fill,"
        f"drawtext=fontfile='{FONT}':text='BOSSMAN PODCAST':fontcolor=0x29d3ff:fontsize=58:x=(w-tw)/2:y=78,"
        f"drawtext=fontfile='{FONT}':text='LIVE':fontcolor=red:fontsize=40:x=40:y=88,"
        f"drawbox=x=150:y=1150:w=780:h=86:color=0x0b1a2a@0.85:t=fill,"
        f"drawtext=fontfile='{FONT}':text='{esc(c['name'])}':fontcolor=white:fontsize=56:x=(w-tw)/2:y=1164,"
        f"drawtext=fontfile='{FONT}':textfile='{ff_path(sub)}':fontcolor=white:fontsize=50:line_spacing=10:"
        f"borderw=4:bordercolor=black:x=(w-tw)/2:y=1330,fade=t=in:st=0:d=0.18,format=yuv420p[v]"
    )
    out = work / f"seg{i}.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", bg, "-loop", "1", "-i", STUDIO / c["image"], "-i", voice,
         "-filter_complex", flt, "-map", "[v]", "-map", "2:a", "-af", f"apad=whole_dur={seconds}",
         "-t", f"{seconds:.3f}", "-r", FPS, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out])
    return out


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out])


# ------------------------------------------------------------------ commands
def cmd_promo(a) -> int:
    out = Path(a.out)
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    text, model, attempts = asyncio.run(chat([{"role": "system", "content": SYS}, {"role": "user", "content": promo_prompt()}]))
    order = [c for c, _ in PROMO_PLAN]
    lines = sorted(parse_lines(text, set(CAST)), key=lambda l: order.index(l["speaker"]))[:3]
    if [l["speaker"] for l in lines] != order:
        raise RuntimeError(f"model did not give one line per host in order: {[l['speaker'] for l in lines]}")
    t_text = time.time() - t0
    lines = fit_voice(lines, PROMO_PLAN, work)
    t_voice = time.time() - t0 - t_text
    bgs = sorted((STUDIO / "backgrounds").glob("*.*"))
    segs = [render_segment(i, l["speaker"], l["text"], s, bgs[i % len(bgs)], work / f"v{i}.wav", work)
            for i, (l, (_, s)) in enumerate(zip(lines, PROMO_PLAN))]
    final = out / "bossman_podcast_promo_15s.mp4"
    concat(segs, final)
    report = {"model": model, "attempts": attempts, "lines": lines, "plan_s": [s for _, s in PROMO_PLAN],
              "final": str(final), "final_s": round(duration(final), 3),
              "timing_s": {"text": round(t_text, 1), "voice": round(t_voice, 1),
                           "render": round(time.time() - t0 - t_text - t_voice, 1)},
              "faces": "still + slow zoom; S2V talking heads are a second pass when the GPU is idle"}
    (out / "promo_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("promo")
    p.add_argument("--out", default=str(STUDIO / "out" / time.strftime("promo_%Y%m%d_%H%M")))
    a = ap.parse_args(argv)
    return cmd_promo(a) if a.cmd == "promo" else 2


if __name__ == "__main__":
    sys.exit(main())
