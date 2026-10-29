"""Bossman makes a 20-second rap motion video ON ITS OWN: local model invents the concept, lyrics, music brief and scenes.

    python tools/motion_code/autopilot.py --theme "Презентация Bossman" --variants 3 --out D:\\Bossman\\work\\autopilot\\<run>

Pipeline (no human or Claude creative input): local LLM (Ollama, default Gemma 4 31B) -> N distinct concepts -> per
concept a scene spec for the canvas engine (validated; on errors the model repairs its own JSON) -> the LLM is
unloaded -> Bossman's ACE-Step makes the track via :8801 -> Whisper checks the vocal -> the canvas engine renders
1080p -> automatic QA (duration, loudness, non-empty frames). Facts on screen may come only from FACTS (real,
receipted numbers); the model may not invent numbers.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OLLAMA = "http://127.0.0.1:11434"
MODEL = "gemma4-31b-uncensored-ctx32k:latest"

FACTS = [
    "Bossman v2.6, runs on the owner's own PC: AMD Ryzen AI Max+ 395, Radeon 8060S, 128 GB unified memory",
    "Owner STOP button outranks every agent; new tasks are blocked while STOP is active",
    "Pult: iPhone web pult, Telegram pult, Command Center in the browser, CLI",
    "Every feature is proven with a receipt (tests + live run + sha256); unproven is labelled NOT_PROVEN",
    "Harness cycle: STOP check -> recall -> act -> verify postcondition -> learn -> sha256 receipt",
    "1152 tests passed on the latest hardening line",
    "Memory: lessons go to SQLite (facts, decisions, procedures, failures) and an Obsidian vault",
    "Local generation: images (flux, qwen-image), video (Wan, LTX), music (ACE-Step); cloud cost of this video: 0 $",
    "Cold model load from the new SSD: up to 2.47x faster; voice reply latency 11.2 s -> 4.8 s",
    "Cloud models are chat-only and capped by budget; secrets and personal data never leave the PC",
    "Real live receipts (write sha EXACTLY like this): Job Object kill-on-close 81b2552b…f43220, approval bound to payload 3b1018f5…8a8ef3, owner spend reconcile c49757e1…739678, harness execute-step 5c2195d9…c17255",
]
RECEIPT_SHAS = {"81b2552b…f43220", "3b1018f5…8a8ef3", "c49757e1…739678", "5c2195d9…c17255"}

KINDS = {
    "title": {"text": "str<=9 chars, one word, e.g. BOSSMAN", "sub": "str<=40"},
    "stop": {"lines": "list of 3 short str (<=12 chars each)", "sub": "str<=40"},
    "chip": {"chip": "str<=14", "chip_sub": "str<=24", "title": "str<=18", "metrics": "list of 3 {label str<=28, value 0..1 bar fill, text str<=18}"},
    "pult": {"app": "str<=20", "title": "str<=10", "title2": "str<=10", "sub": "str<=40", "msgs": "list of 4 short chat lines <=30 chars, alternating owner/bossman"},
    "receipts": {"title": "str<=22", "rows": "list of 3-4 {name str<=26, sha str like '81b2552b…f43220' from FACTS, verdict PASS}"},
    "loop": {"steps": "list of 4-6 str<=12", "center": "str<=10", "center_sub": "str<=24", "title": "str<=10", "notes": "list of 2 str<=44"},
    "chorus": {"text": "str<=9 chars", "sub": "str<=24", "stats": "list of 3 {value int from FACTS, label str<=24, suffix optional str}"},
    "outro": {"text": "str<=9", "sub": "str<=40", "foot": "str<=70"},
}

CONCEPTS_PROMPT = """You are Bossman, a local AI on the owner's PC, and today you are also its art director.
Invent {n} DIFFERENT concepts for a 20-second rap motion-graphics presentation about yourself. Theme: {theme}.
Each concept needs its own idea, mood, colour palette and angle (for example: a heist, a courtroom, a sports broadcast,
a space launch - but invent your own). Use only these true facts, never invent numbers:
{facts}
Return ONLY JSON: {{"concepts": [{{"name": "...", "idea": "one sentence", "mood": "...", "palette": "words"}}]}}"""

SPEC_PROMPT = """You are Bossman, a local AI, directing a 20-second rap motion video about yourself.
Concept: {concept}
Theme: {theme}. True facts you may use (never invent other numbers or hashes):
{facts}

Write ONE JSON object for the scene engine. The video is 32 beats at {bpm} BPM (20 s). Fields:
- "title": concept name
- "bpm": {bpm}
- "lyrics": an English rap in ACE-Step format: "[verse]" then 4 lines, blank line, "[chorus]" then 2 lines. Short punchy lines,
   rhyming, about Bossman, using the concept's angle. The word "Bossman" must appear at least twice.
- "music_prompt": one line style brief for a music model (genre, drums, bass, vocal, energy), max 200 chars, must say "rap vocal"
- "colors": {{"bg": "#hex dark", "a": "#hex accent", "b": "#hex second accent", "c": "#hex third", "ink": "#hex text", "dim": "#hex muted"}}
- "scenes": 6 to 8 scenes in the order you want; each {{"kind": one of {kinds}, "beats": number, ...fields of that kind}}.
   The beats of all scenes must add up to 32. Start with "title", end with "outro". Use each kind at most once.
Fields per kind: {fields}
Return ONLY the JSON object."""


def ollama_json(prompt: str, model: str = MODEL, temperature: float = 0.9, keep_alive: str = "10m") -> dict:
    body = {"model": model, "stream": False, "format": "json", "keep_alive": keep_alive,
            "options": {"temperature": temperature, "num_ctx": 8192},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(OLLAMA + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        text = json.loads(r.read())["message"]["content"]
    return json.loads(text)


def unload(model: str = MODEL) -> None:
    req = urllib.request.Request(OLLAMA + "/api/generate", json.dumps({"model": model, "keep_alive": 0}).encode(),
                                 {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=120).read()


HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_spec(spec: dict) -> list[str]:
    """Problems the model must fix itself; empty list = the engine can render it."""
    errs = []
    scenes = spec.get("scenes")
    if not isinstance(scenes, list) or not 4 <= len(scenes) <= 9:
        return ["scenes must be a list of 6-8 scenes"]
    kinds = [s.get("kind") for s in scenes if isinstance(s, dict)]
    if kinds[:1] != ["title"] or kinds[-1:] != ["outro"]:
        errs.append("first scene must be title and last must be outro")
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        errs.append(f"unknown kinds {bad}; allowed {list(KINDS)}")
    if len(set(kinds)) != len(kinds):
        errs.append("each kind at most once")
    try:
        total = sum(float(s.get("beats") or 0) for s in scenes)
        if abs(total - 32) > 0.01:
            errs.append(f"beats add up to {total}, must be 32")
    except (TypeError, ValueError):
        errs.append("beats must be numbers")
    for s in scenes:
        for field in KINDS.get(s.get("kind"), {}):
            if field not in s:
                errs.append(f"scene {s.get('kind')} misses field {field}")
    lyr = str(spec.get("lyrics") or "")
    if "[verse]" not in lyr or "[chorus]" not in lyr or lyr.lower().count("bossman") < 2:
        errs.append("lyrics need [verse], [chorus] and the word Bossman at least twice")
    cols = spec.get("colors") or {}
    if not all(HEX.match(str(cols.get(k, ""))) for k in ("bg", "a", "b", "c", "ink", "dim")):
        errs.append("colors need bg,a,b,c,ink,dim as #rrggbb")
    if "rap" not in str(spec.get("music_prompt") or "").lower():
        errs.append("music_prompt must ask for a rap vocal")
    # fact check (29.10: the model reused one hash tail for every receipt): hashes and numbers only from FACTS
    facts_text = " ".join(FACTS)
    for s in scenes:
        if s.get("kind") == "receipts":
            for row in s.get("rows") or []:
                sha = str((row or {}).get("sha") or "").replace("...", "…")
                if sha not in RECEIPT_SHAS:
                    errs.append(f"receipt sha {sha!r} is not a real receipt; use exactly one of {sorted(RECEIPT_SHAS)}")
                elif isinstance(row, dict):
                    row["sha"] = sha
        if s.get("kind") == "chorus":
            for st in s.get("stats") or []:
                v = (st or {}).get("value")
                if not isinstance(v, (int, float)) or (v != 0 and not re.search(rf"(?<![\d.]){re.escape(str(v))}(?![\d])", facts_text)):
                    errs.append(f"chorus stat value {v!r} is not a number from FACTS")
    return errs


def make_spec(concept: dict, theme: str, bpm: int, tries: int = 3, ask=ollama_json) -> tuple[dict, list[dict]]:
    prompt = SPEC_PROMPT.format(concept=json.dumps(concept, ensure_ascii=False), theme=theme, facts="\n".join(f"- {f}" for f in FACTS),
                                bpm=bpm, kinds=list(KINDS), fields=json.dumps(KINDS, ensure_ascii=False))
    log = []
    spec = ask(prompt)
    for attempt in range(tries):
        errs = validate_spec(spec)
        log.append({"attempt": attempt + 1, "errors": errs})
        if not errs:
            return spec, log
        spec = ask(prompt + "\n\nYour previous JSON:\n" + json.dumps(spec, ensure_ascii=False)
                   + "\nFix ONLY these problems and return the full corrected JSON:\n- " + "\n- ".join(errs), temperature=0.4)
    errs = validate_spec(spec)
    log.append({"attempt": tries + 1, "errors": errs})
    if errs:
        raise ValueError(f"model could not produce a valid spec: {errs}")
    return spec, log


def engine_spec(spec: dict) -> dict:
    return {"bpm": int(spec.get("bpm") or 96), "offset": 0, "duration": 20, "version_tag": "v2.6 · made by Bossman itself · local",
            "colors": spec["colors"], "scenes": spec["scenes"]}


def music(api, spec: dict, wait_s: float = 900) -> dict:
    r = api("POST", "/api/music/generate", {"prompt": spec["music_prompt"][:400], "lyrics": spec["lyrics"], "duration": 22,
                                           "bpm": int(spec.get("bpm") or 96), "thinking": False, "time_signature": "4"}, timeout=900)
    tid, t0 = r["task_id"], time.time()
    while time.time() - t0 < wait_s:
        time.sleep(5)
        if api("GET", f"/api/music/tasks/{tid}", timeout=60)["status"] != "running":
            break
    return api("POST", f"/api/music/tasks/{tid}/save", {"master": "hype", "duration": 20}, timeout=300)


def whisper_words(audio: Path) -> list[dict]:
    from faster_whisper import WhisperModel
    m = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(str(audio), language="en", word_timestamps=True)
    return [{"w": w.word.strip(), "t": round(w.start, 2)} for s in segs for w in s.words]


def qa(video: Path, frames_dir: Path) -> dict:
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height",
                                       "-of", "json", str(video)], capture_output=True, text=True).stdout)
    loud = subprocess.run(["ffmpeg", "-i", str(video), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", loud)
    frames_dir.mkdir(parents=True, exist_ok=True)
    lumas = []
    for t in (1, 4, 7, 10, 13, 16, 18.5):
        f = frames_dir / f"qa_{t:04.1f}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=320:-1", str(f)])
        sig = subprocess.run(["ffmpeg", "-i", str(f), "-vf", "signalstats,metadata=print", "-f", "null", "-"],   # info level: metadata
                             capture_output=True, text=True).stderr
        y = re.findall(r"YAVG=([\d.]+)", sig)
        lumas.append(float(y[0]) if y else 0.0)
    dur = float(probe["format"]["duration"])
    res = {"duration_s": round(dur, 2), "size": [probe["streams"][0].get("width"), probe["streams"][0].get("height")],
           "lufs": float(m[-1]) if m else None, "frame_luma": lumas}
    res["pass"] = bool(19.5 <= dur <= 20.5 and res["lufs"] is not None and -14 <= res["lufs"] <= -6
                       and sum(1 for y in lumas if y > 8) >= 5)
    return res


def repair_run(out: Path, model: str, fps: int) -> int:
    """Fact/format check of every spec of a finished run; failing specs go back to the SAME local model with the error
    list (no human edits), then the video is re-rendered on the variant's existing track and QA runs again."""
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    ask = lambda prompt, temperature=0.4: ollama_json(prompt, model, temperature)   # noqa: E731
    for v in report["videos"]:
        i = v["variant"]
        doc = json.loads((out / f"v{i}_spec.json").read_text(encoding="utf-8"))
        spec, log = doc["spec"], doc.get("repair_log", [])
        errs = validate_spec(spec)
        tries = 0
        while errs and tries < 3:
            tries += 1
            spec = ask("You are Bossman. Your scene JSON for a video about yourself failed the fact check.\nTrue facts:\n"
                       + "\n".join(f"- {f}" for f in FACTS) + "\n\nYour JSON:\n" + json.dumps(spec, ensure_ascii=False)
                       + "\n\nFix ONLY these problems and return the full corrected JSON:\n- " + "\n- ".join(errs))
            errs = validate_spec(spec)
            log.append({"attempt": f"repair-{tries}", "errors": errs})
        (out / f"v{i}_spec.json").write_text(json.dumps({"spec": spec, "repair_log": log}, ensure_ascii=False, indent=1), encoding="utf-8")
        v["fact_check"] = "PASS" if not errs else f"FAIL: {errs[:3]}"
        v["repairs"] = tries
        if tries:
            (out / f"v{i}_engine.json").write_text(json.dumps(engine_spec(spec), ensure_ascii=False, indent=1), encoding="utf-8")
            r = subprocess.run([sys.executable, str(HERE / "canvas" / "render.py"), str(out / f"v{i}_engine.json"),
                                str(out / f"v{i}.mp4"), v["music"]["file"], "--fps", str(fps)], capture_output=True, text=True)
            v["render_ok"] = r.returncode == 0
        v["qa"] = qa(out / f"v{i}.mp4", out / f"v{i}_frames")
        print(json.dumps({k: v[k] for k in ("variant", "title", "fact_check", "repairs", "qa")}, ensure_ascii=False), flush=True)
    unload(model)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if all(v["qa"].get("pass") and v.get("fact_check") == "PASS" for v in report["videos"]) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--theme", default="Презентация Bossman")
    ap.add_argument("--variants", type=int, default=3)
    ap.add_argument("--bpm", type=int, default=96)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--repair", action="store_true", help="re-check an existing run: the model repairs its own failing specs, "
                                                             "videos re-render with the music already made")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.repair:
        return repair_run(out, a.model, a.fps)
    sys.path.insert(0, str(ROOT / "tools"))
    import prove_chat_attachments as p
    t0 = time.time()
    ask = lambda prompt, temperature=0.9: ollama_json(prompt, a.model, temperature)   # noqa: E731
    concepts = ask(CONCEPTS_PROMPT.format(n=a.variants, theme=a.theme, facts="\n".join(f"- {f}" for f in FACTS)))["concepts"][:a.variants]
    (out / "concepts.json").write_text(json.dumps(concepts, ensure_ascii=False, indent=1), encoding="utf-8")
    print("concepts:", [c.get("name") for c in concepts], flush=True)
    specs = []
    for i, c in enumerate(concepts, 1):
        spec, log = make_spec(c, a.theme, a.bpm, ask=ask)
        (out / f"v{i}_spec.json").write_text(json.dumps({"spec": spec, "repair_log": log}, ensure_ascii=False, indent=1), encoding="utf-8")
        specs.append(spec)
        print(f"v{i} spec ok after {len(log)} attempt(s): {spec.get('title')}", flush=True)
    unload(a.model)                                   # one heavy model at a time: the LLM leaves before ACE-Step
    report = {"theme": a.theme, "model": a.model, "concepts": concepts, "videos": []}
    for i, spec in enumerate(specs, 1):
        v = {"variant": i, "title": spec.get("title")}
        sv = music(p.api, spec)
        audio = Path((sv.get("master") or {}).get("output_path") or sv["path"])
        v["music"] = {"sha256": sv.get("sha256"), "lufs": (sv.get("master") or {}).get("measured_lufs"),
                      "bpm": (sv.get("master") or {}).get("bpm_measured"), "file": str(audio)}
        words = whisper_words(audio)
        v["vocal_words"] = len(words)
        v["bossman_sung"] = sum(1 for w in words if "boss" in w["w"].lower())
        (out / f"v{i}_engine.json").write_text(json.dumps(engine_spec(spec), ensure_ascii=False, indent=1), encoding="utf-8")
        video = out / f"v{i}.mp4"
        r = subprocess.run([sys.executable, str(HERE / "canvas" / "render.py"), str(out / f"v{i}_engine.json"), str(video), str(audio),
                            "--fps", str(a.fps)], capture_output=True, text=True)
        v["render_ok"] = r.returncode == 0 and video.is_file()
        v["qa"] = qa(video, out / f"v{i}_frames") if v["render_ok"] else {"pass": False, "error": r.stderr[-400:]}
        report["videos"].append(v)
        print(json.dumps(v, ensure_ascii=False), flush=True)
    report["seconds"] = round(time.time() - t0)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if all(v["qa"].get("pass") for v in report["videos"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
