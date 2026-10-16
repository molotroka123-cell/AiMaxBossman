"""Bossman Podcast — foundation: script by the best free LLM, local voices, rendered segments, a live spool.

    python tools/podcast/podcast.py promo   [--out DIR]       # 15 s English promo, hosts switch 7/5/3 s
    python tools/podcast/podcast.py episode --topic "..." [--minutes 3]   # multi-block episode with memory

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
    "girl": {"name": "SANDRA", "voice": "af_heart", "image": "cast/girl.png"},
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
AAC_PRIMING_S = 1024 / 48000 + 0.002   # one AAC frame at 48 kHz (+2 ms margin) between live segments


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
        try:
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
        finally:   # an undisposed engine/HTTP client kept the process alive ~19 min after the episode finished
            for ad in adapters.values():
                close = getattr(ad, "aclose", None) or getattr(ad, "close", None)
                if close:
                    res = close()
                    if asyncio.iscoroutine(res):
                        await res
            await db.close()
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
    try:
        data = json.loads(m.group(0) if m else text)
    except json.JSONDecodeError:
        # free models sometimes stop mid-string: keep every complete {"speaker": ..., "text": ...} object
        data = [json.loads(o) for o in re.findall(r'\{\s*"speaker"\s*:\s*"[^"]*"\s*,\s*"text"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}', text)]
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


def episode_prompt(topic: str, n_lines: int, covered: list[str], last: list[dict]) -> str:
    """One 2-3 minute block; the model sees what was already said so the show moves on instead of looping."""
    hosts = ('"girl" (Mia: the host, steers the talk), "kisliy" (sceptic, dry humour), '
             '"p4ela" (hype, practical examples)')
    memory = ""
    if covered:
        memory += "Already covered (do NOT repeat): " + "; ".join(covered[-24:]) + ". "
    if last:
        memory += "Last lines so far: " + " ".join(f'{l["speaker"]}: {l["text"]}' for l in last[-4:]) + " "
    opener = "Continue the conversation naturally from the last line." if last else "Mia opens the show and introduces the topic."
    return (f"Bossman Podcast episode. Topic: {topic}. Hosts: {hosts}. {memory}{opener} Write the next {n_lines} lines, "
            "hosts take turns (never the same host twice in a row), each line 15-40 words, one new idea or reaction per "
            "line. No websites, links or handles; no invented quotes or facts about real people. "
            'Output a JSON array like [{"speaker":"girl","text":"..."},{"speaker":"kisliy","text":"..."}].')


URL_RE = re.compile(r"\b(?:https?://|www\.)\S+|\b\S+\.(?:com|net|org|io|ai)\b", re.I)


def clean_lines(lines: list[dict]) -> list[dict]:
    """Text check before voice: drop links, collapse a host speaking twice in a row."""
    out: list[dict] = []
    for l in lines:
        text = URL_RE.sub("", l["text"]).strip(" ,")
        if len(text.split()) < 3:
            continue
        if out and out[-1]["speaker"] == l["speaker"]:
            out[-1]["text"] += " " + text
        else:
            out.append({"speaker": l["speaker"], "text": text})
    return out


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
        f"[1:v]scale=760:900:force_original_aspect_ratio=increase,crop=760:900,zoompan=z='min(1.0+0.0009*on,1.12)':d={frames}:s=760x900:fps={FPS},"
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


def cmd_episode(a) -> int:
    """Blocks of lines until the voiced length reaches --minutes; every block is voiced and rendered as it arrives,
    so the same loop later feeds the live spool."""
    out = Path(a.out)
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    bgs = sorted((STUDIO / "backgrounds").glob("*.*"))
    target = a.minutes * 60
    segs: list[Path] = []
    lines_all: list[dict] = []
    blocks: list[dict] = []
    covered: list[str] = []
    total = 0.0
    while total < target and len(blocks) < a.max_blocks:
        b = len(blocks)
        tb = time.time()
        text, model, attempts = asyncio.run(chat([{"role": "system", "content": SYS},
                                                  {"role": "user", "content": episode_prompt(a.topic, a.lines, covered, lines_all)}]))
        lines = clean_lines(parse_lines(text, set(CAST)))
        t_text = time.time() - tb
        jobs = [{"text": l["text"], "voice": CAST[l["speaker"]]["voice"], "out": str(work / f"b{b}_v{i}.wav")}
                for i, l in enumerate(lines)]
        synthesize(jobs, work)
        t_voice = time.time() - tb - t_text
        for i, (l, job) in enumerate(zip(lines, jobs)):
            n = len(segs)
            l["seconds"] = round(duration(Path(job["out"])) + 0.35, 2)   # short breath between hosts
            segs.append(render_segment(n, l["speaker"], l["text"], l["seconds"], bgs[n % len(bgs)], Path(job["out"]), work))
            total += l["seconds"]
            covered.append(" ".join(l["text"].split()[:8]))
        lines_all.extend(lines)
        blocks.append({"block": b, "model": model, "attempts": attempts, "lines": len(lines),
                       "audio_s": round(sum(l["seconds"] for l in lines), 1), "text_s": round(t_text, 1),
                       "voice_s": round(t_voice, 1), "render_s": round(time.time() - tb - t_text - t_voice, 1)})
        print(f"block {b}: {len(lines)} lines, {blocks[-1]['audio_s']} s audio by {model}, total {total:.0f}/{target:.0f} s",
              flush=True)
    final = out / "bossman_podcast_episode.mp4"
    concat(segs, final)
    wall = time.time() - t0
    final_s = duration(final)
    report = {"topic": a.topic, "target_s": target, "final": str(final), "final_s": round(final_s, 1),
              "wall_s": round(wall, 1), "realtime_factor": round(final_s / wall, 2), "blocks": blocks,
              "speakers": {c: sum(1 for l in lines_all if l["speaker"] == c) for c in CAST}, "lines": lines_all}
    (out / "episode_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "lines"}, ensure_ascii=False, indent=1))
    return 0


def produce_block(topic: str, n_lines: int, covered: list[str], said: list[dict], work: Path, start: int,
                  bgs: list[Path]) -> tuple[list[tuple[Path, float]], dict]:
    """One block of the show: text -> voices -> rendered segments (path, seconds)."""
    tb = time.time()
    text, model, attempts = asyncio.run(chat([{"role": "system", "content": SYS},
                                              {"role": "user", "content": episode_prompt(topic, n_lines, covered, said)}]))
    lines = clean_lines(parse_lines(text, set(CAST)))
    jobs = [{"text": l["text"], "voice": CAST[l["speaker"]]["voice"], "out": str(work / f"v{start + i}.wav")}
            for i, l in enumerate(lines)]
    synthesize(jobs, work)
    out = []
    for i, (l, job) in enumerate(zip(lines, jobs)):
        n = start + i
        sec = round(duration(Path(job["out"])) + 0.35, 2)
        out.append((render_segment(n, l["speaker"], l["text"], sec, bgs[n % len(bgs)], Path(job["out"]), work), sec))
        covered.append(" ".join(l["text"].split()[:8]))
    said.extend(lines)
    return out, {"model": model, "attempts": len(attempts), "lines": len(lines), "audio_s": round(sum(s for _, s in out), 1),
                 "wall_s": round(time.time() - tb, 1)}


def ts_span(p: Path) -> float:
    """Earliest start to latest end over ALL streams: the container duration missed the video tail (~7 ms)."""
    out = run(["ffprobe", "-v", "error", "-show_entries", "stream=start_time,duration", "-of", "csv=p=0", p]).stdout
    spans = [tuple(map(float, l.split(",")[:2])) for l in out.split() if l.count(",") >= 1 and "N/A" not in l]
    return max(s + d for s, d in spans) - min(s for s, _ in spans)


def write_playlist(live: Path, window: list[tuple[int, str, float, bool]], ended: bool = False) -> None:
    """Sliding HLS playlist; written to a temp file and renamed so a player never reads half a file."""
    target = max(int(s) + 1 for _, _, s, _ in window)
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}", f"#EXT-X-MEDIA-SEQUENCE:{window[0][0]}"]
    for _, name, sec, disc in window:
        if disc:
            lines.append("#EXT-X-DISCONTINUITY")
        lines += [f"#EXTINF:{sec:.3f},", name]
    if ended:
        lines.append("#EXT-X-ENDLIST")
    tmp = live / "live.m3u8.tmp"
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, live / "live.m3u8")


def cmd_live(a) -> int:
    """Endless show: a producer thread keeps >= --ahead seconds rendered; the broadcaster publishes each segment
    into a sliding HLS playlist at real-time pace. Empty buffer -> a filler card goes on air, the show never stops.
    STOP file (or --minutes) ends it after the current segment. Open live/live.m3u8 in a player/OBS (or serve the dir)."""
    import queue
    import threading
    out = Path(a.out)
    work, live = out / "work", out / "live"
    for d in (work, live):
        d.mkdir(parents=True, exist_ok=True)
    stop_file = live / "STOP"
    stop_file.unlink(missing_ok=True)
    bgs = sorted((STUDIO / "backgrounds").glob("*.*"))
    filler = work / "filler.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", bgs[0], "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"drawtext=fontfile='{FONT}':text='BOSSMAN PODCAST - BACK IN A MOMENT':fontcolor=0x29d3ff:fontsize=54:"
                f"x=(w-tw)/2:y=(h-th)/2,format=yuv420p",
         "-t", "4", "-r", FPS, "-c:v", "libx264", "-c:a", "aac", "-ar", "48000", filler])
    buf: "queue.Queue[tuple[Path, float]]" = queue.Queue()
    stats = {"blocks": [], "errors": [], "buffered_s": 0.0}
    done = threading.Event()

    def producer() -> None:
        covered: list[str] = []
        said: list[dict] = []
        n = 0
        while not done.is_set():
            if stats["buffered_s"] >= a.ahead:
                time.sleep(1)
                continue
            try:
                segs, info = produce_block(a.topic, a.lines, covered, said, work, n, bgs)
            except Exception as exc:  # noqa: BLE001 - the show goes on with the filler; logged
                stats["errors"].append(f"{time.strftime('%H:%M:%S')} {type(exc).__name__}: {str(exc)[:160]}")
                time.sleep(10)
                continue
            n += len(segs)
            stats["blocks"].append(info)
            for s in segs:
                stats["buffered_s"] += s[1]
                buf.put(s)

    threading.Thread(target=producer, daemon=True).start()
    seq, window, prev_filler = 0, [], None
    t_start = time.time()
    on_air = {"show_s": 0.0, "filler_s": 0.0}
    air_t = 0.0
    try:
        while not stop_file.exists() and (a.minutes <= 0 or time.time() - t_start < a.minutes * 60):
            try:
                seg, sec = buf.get(timeout=0.5 if seq else a.first_wait)
                stats["buffered_s"] -= sec
                is_filler = False
            except queue.Empty:
                seg, sec, is_filler = filler, 4.0, True
            name = f"seg_{seq:06d}.ts"
            # every rendered segment starts at t=0; shift it to the running air time so the stream is continuous.
            # The offset is the REAL container length (AAC frames overshoot the nominal length by a few ms; the
            # nominal one made audio DTS go backwards at every joint)
            # and +1 AAC frame: the encoder's priming packet sits at -21.3 ms and overlapped the previous segment
            air_t += AAC_PRIMING_S
            run(["ffmpeg", "-v", "error", "-y", "-i", seg, "-c", "copy", "-bsf:v", "h264_mp4toannexb",
                 "-output_ts_offset", f"{air_t:.6f}", "-f", "mpegts", live / name])
            air_t += ts_span(live / name)
            window.append((seq, name, sec, prev_filler is not None and prev_filler != is_filler))
            prev_filler = is_filler
            window = window[-max(3, int(a.window / 4)):]
            write_playlist(live, window)
            for old in live.glob("seg_*.ts"):           # keep a little history behind the window for slow players
                if int(old.stem[4:]) < window[0][0] - 3:
                    old.unlink(missing_ok=True)
            on_air["filler_s" if is_filler else "show_s"] += sec
            seq += 1
            time.sleep(sec)                              # real-time pace: the next segment goes up when this one ends
    finally:
        done.set()
        if window:   # players stop waiting for new segments instead of hanging on a dead live playlist
            write_playlist(live, window, ended=True)
    report = {"topic": a.topic, "wall_s": round(time.time() - t_start, 1), "segments": seq, **{k: round(v, 1) for k, v in on_air.items()},
              "buffer_left_s": round(stats["buffered_s"], 1), "blocks": stats["blocks"], "errors": stats["errors"],
              "stopped_by": "STOP file" if stop_file.exists() else "minutes"}
    (out / "live_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("promo")
    p.add_argument("--out", default=str(STUDIO / "out" / time.strftime("promo_%Y%m%d_%H%M")))
    e = sub.add_parser("episode")
    e.add_argument("--topic", required=True)
    e.add_argument("--minutes", type=float, default=3.0)
    e.add_argument("--lines", type=int, default=12, help="lines per block (~2-3 min)")
    e.add_argument("--max-blocks", type=int, default=40)
    e.add_argument("--out", default=str(STUDIO / "out" / time.strftime("episode_%Y%m%d_%H%M")))
    lv = sub.add_parser("live")
    lv.add_argument("--topic", required=True)
    lv.add_argument("--minutes", type=float, default=0, help="0 = until the STOP file appears")
    lv.add_argument("--ahead", type=float, default=600, help="seconds rendered ahead of the air")
    lv.add_argument("--lines", type=int, default=12)
    lv.add_argument("--window", type=float, default=300, help="playlist window, seconds")
    lv.add_argument("--first-wait", type=float, default=300, help="seconds to wait for the first block before filler")
    lv.add_argument("--out", default=str(STUDIO / "out" / time.strftime("live_%Y%m%d_%H%M")))
    a = ap.parse_args(argv)
    return {"promo": cmd_promo, "episode": cmd_episode, "live": cmd_live}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
