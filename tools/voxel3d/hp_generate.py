"""Bossman makes a 5k-10k triangle game model from a text prompt, with LOCAL models only.

    python tools/voxel3d/hp_generate.py "a wooden hand cart" --out <dir> [--rounds 2] [--no-vision]
    python tools/voxel3d/hp_generate.py "a red toadstool" --out <dir> --neural --height 1.6   # image->3D route
    ... --game <BossBlocks dir> [--apply]   # install plan (dry run) / copy into assets/props

Loop (same split as the voxel generator and Motion Studio: the model only writes JSON, everything
that becomes a file is built and checked deterministically):
  1. local LLM (Ollama `bossman-fast-qwen36-35b-a3b-q5`, think off, JSON mode) writes a part spec;
     the schema + the five hand-made example specs are the prompt (few-shot).
  2. hpspec.repair + validate + build (budget, no floating parts); errors go back as the next turn.
  3. mesh_check validator (5k-10k triangles, watertight shells, pivot/scale, colour).
  4. preview PNG -> local vision model (`bossman-fast-qwen36-vision`) critiques it against the
     prompt (what it looks like, 1-5 recognisable / proportions / colours, up to 3 fixes).
  5. critique -> revision turn, up to `rounds` times; the best-scored valid version is kept.
No Claude or cloud model is called at runtime. The shared-machine PAUSE file is honoured.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voxel3d import hipoly, hpspec, mesh_check, meshgen  # noqa: E402
from voxel3d.generate import extract_json, wait_if_paused  # noqa: E402

LLM_MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
VISION_MODEL = "bossman-fast-qwen36-vision:latest"
ENDPOINT = "http://127.0.0.1:11434"

Chat = Callable[[list[dict]], str]
Vision = Callable[[Path, str], dict]

SYSTEM = """You design 3D models for a game by writing ONE JSON part spec. Output the JSON object only.

Format: {"name": "snake_case", "label": "Short Name", "height": <total height in metres>, "target": 8000,
         "parts": [<part>, ...]}
Units: metres, 1 m = one game block. Axes: x = width, y = UP, z = depth, the FRONT faces +z.
The object stands on the ground (lowest point y = 0) and is centred on x = 0, z = 0.

Part shapes (every part is a closed solid; parts may overlap and MUST touch at least one other part):
- {"shape":"ellipsoid","center":[x,y,z],"radii":[rx,ry,rz]}              bodies, heads, foliage, rocks, spheres
- {"shape":"box","center":[x,y,z],"size":[w,h,d],"round":0.03}           planks, walls, roofs, tables (round = edge radius)
- {"shape":"lathe","profile":[[r,y],...],"center":[x,0,z],"sections":48}  anything round made by turning: pots,
   towers, wells, barrels, mushroom caps, cones. profile = (radius, height) points from bottom to top.
   sections 4 = square, 6 = hexagonal, 8 = octagonal, 32..72 = round
- {"shape":"tube","from":[x,y,z],"to":[x,y,z],"radius":r,"radius_end":r2} poles, legs, logs, beams, axles, branches
- {"shape":"torus","center":[x,y,z],"radius":R,"thickness":t,"axis":[ax,ay,az]}  rings, wheels (axis = wheel axle), rims
- {"shape":"prism","base":[x,y,z],"radius":r,"height":h,"tip":t,"sides":6,"direction":[dx,dy,dz]}  crystals, spikes,
   pointed roofs (sides 4)
Optional per part:
- "rotate":[rx,ry,rz] degrees about the part's own center/base (tilted roof planes, sails, leaning planks)
- "mirror_x":true adds a mirrored copy at -x (pairs: legs, arms, wheels, eyes)
- "repeat":{"count":n,"start_deg":a,"axis":[0,1,0],"center":[0,0,0]} n evenly rotated copies around an axis
   (roots, fence posts in a circle, wheel spokes, windmill sails)
- "scatter":{"on":"<other part name>","count":n,"min_up":0.25} copies placed on another part's surface (spots)
- "color":"#rrggbb" (required), "color_top":"#rrggbb" (gradient up the part), "moss":"#rrggbb" (tint on top faces),
  "variation":0..0.3 (colour noise), "cavities":0..0.5 (dark patches)
- "surface":{"bumps":metres (0.005 wood, 0.03 stone, 0.1-0.3 foliage), "scale":feature size in metres,
   "ridged":true for bark/rock, "grooves":{"axis":"x|y|z","every":0.15,"depth":0.01} for planks}
- "material":"matte"|"gloss" (metal, gold, glazed)|"glow" (lamps, magic, fire)
- "shading":"flat" for crisp faceted pieces (crystals, pointed roofs); "detail":"low" for small thin hard pieces
Rules: realistic proportions and sizes in metres (a chair ~1 m, a cart ~1.5 m, a house 3-5 m); build the
recognisable silhouette first, then 2-4 characteristic details; 6-30 parts; saturated, readable colours (never
washed-out white/grey everywhere); symmetric objects use mirror_x / repeat instead of copying parts by hand;
every part touches another part (no floating pieces); never put two parts in the same place - `repeat` rotates
copies around its axis, so use it only for things arranged in a circle around that axis.
"""


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def ollama_chat(model: str = LLM_MODEL, endpoint: str = ENDPOINT, num_ctx: int = 24576, max_tokens: int = 6000,
                temperature: float = 0.4, timeout: float = 900.0) -> Chat:
    def chat(messages: list[dict]) -> str:
        payload = {"model": model, "messages": messages, "stream": False, "think": False, "format": "json",
                   "keep_alive": "10m", "options": {"temperature": temperature, "num_predict": max_tokens,
                                                    "num_ctx": num_ctx, "seed": 11}}
        return (_post(endpoint.rstrip("/") + "/api/chat", payload, timeout).get("message") or {}).get("content", "")
    return chat


CRITIQUE = ("You are reviewing a 3D model made for a video game. The picture shows the SAME model from two sides "
            "(left: front-left view, right: back-right view). It is supposed to be: \"{prompt}\".\n"
            "Answer as JSON: {{\"looks_like\": \"<what it looks like, 3-10 words>\", \"recognisable\": 1-5, "
            "\"proportions\": 1-5, \"colours\": 1-5, \"fixes\": [\"<one concrete change naming a part, e.g. "
            "'make the roof wider'>\", ...at most 3]}}. 5 = excellent. Be strict and specific.")


def ollama_vision(model: str = VISION_MODEL, endpoint: str = ENDPOINT, timeout: float = 600.0) -> Vision:
    def vision(png: Path, prompt: str) -> dict:
        img = base64.b64encode(Path(png).read_bytes()).decode()
        payload = {"model": model, "stream": False, "think": False, "format": "json", "keep_alive": "10m",
                   "messages": [{"role": "user", "content": CRITIQUE.format(prompt=prompt), "images": [img]}],
                   "options": {"temperature": 0.1, "num_predict": 400, "seed": 3}}
        raw = (_post(endpoint.rstrip("/") + "/api/chat", payload, timeout).get("message") or {}).get("content", "")
        return parse_critique(raw)
    return vision


def parse_critique(raw: str) -> dict:
    obj = extract_json(raw) or {}

    def score(k: str) -> int:
        try:
            return int(min(max(round(float(obj.get(k, 0))), 1), 5))
        except (TypeError, ValueError):
            return 1
    fixes = [str(f)[:160] for f in (obj.get("fixes") or []) if str(f).strip()][:3]
    return {"looks_like": str(obj.get("looks_like", ""))[:120], "recognisable": score("recognisable"),
            "proportions": score("proportions"), "colours": score("colours"), "fixes": fixes}


def few_shot(examples: dict[str, dict] | None = None) -> list[dict]:
    msgs: list[dict] = []
    for spec in (examples or meshgen.example_specs()).values():
        prompt = spec.get("prompt") or spec.get("label", spec["name"])
        body = {k: v for k, v in spec.items() if k != "prompt"}
        msgs += [{"role": "user", "content": f"OBJECT: {prompt}"},
                 {"role": "assistant", "content": json.dumps(body, separators=(",", ":"))}]
    return msgs


ADVICE = {
    "budget_ok": "the triangle count is outside 5000-10000: mark big organic parts as detail high and keep "
                 "'detail': 'low' only for small thin pieces",
    "manifold_ok": "some parts are paper-thin or overlap another part almost exactly: give every part a thickness "
                   "of at least 0.02 m and never put two parts in the same place",
    "pivot_ok": "the model could not be centred on the ground",
    "scale_ok": "the model is too big: keep it under 6 m wide and deep and set height to the real total height",
    "colour_ok": "the colours look washed out: use saturated, readable colours (not near-white or grey everywhere)",
}


def check_advice(check: dict) -> list[str]:
    """Validator failures as instructions the model can act on (it never sees raw mesh stats)."""
    return [ADVICE[k] for k in ADVICE if not check.get(k)] or ["the built model failed the validator"]


def _slug(prompt: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", prompt.lower()).split()
    words = [w for w in words if w not in {"a", "an", "the", "with", "of", "and", "on", "in", "small", "big"}]
    return "bm_" + "_".join(words[:3]) if words else "bm_prop"


def _score(c: dict | None) -> tuple:
    if not c:
        return (-1, -1)
    return (c["recognisable"], c["proportions"] + c["colours"])


def generate(prompt: str, out: Path, chat: Chat, vision: Vision | None = None, tries: int = 3, rounds: int = 2,
             log: Callable[[str], None] = print, name: str | None = None) -> dict:
    """Returns the report dict (also written to out/<name>/report.json)."""
    name = name or _slug(prompt)
    d = Path(out) / name
    d.mkdir(parents=True, exist_ok=True)
    messages = [{"role": "system", "content": SYSTEM}, *few_shot(), {"role": "user", "content": f"OBJECT: {prompt}"}]
    versions: list[dict] = []
    calls = 0
    t_start = time.perf_counter()
    for rnd in range(rounds + 1):
        built = None
        for attempt in range(1, tries + 1):
            wait_if_paused(log)
            t0 = time.perf_counter()
            try:
                raw = chat(messages)
            except Exception as exc:  # timeout / connection: counts as a failed try
                log(f"[{name}] round {rnd} try {attempt}: model call failed: {exc.__class__.__name__}: {exc}")
                continue
            calls += 1
            messages.append({"role": "assistant", "content": raw})
            spec = extract_json(raw)
            if spec is None:
                errors = ["the reply was not one complete JSON object; reply with the JSON spec only"]
            else:
                spec, fixes = hpspec.repair(spec)
                spec["name"] = name
                glb, stats, errors = hpspec.build(spec)
                if not errors:
                    path = d / f"{name}_v{len(versions) + 1}.glb"
                    path.write_bytes(glb)
                    check = mesh_check.check_prop_glb(path, expect_height=float(spec["height"]))
                    if not check["ok"]:
                        errors = check_advice(check)
                    else:
                        built = {"spec": spec, "repairs": fixes, "stats": stats, "check": check, "glb": str(path),
                                 "seconds": round(time.perf_counter() - t0, 1)}
            log(f"[{name}] round {rnd} try {attempt}: {'BUILT' if built else '; '.join(errors)[:160]} "
                f"({time.perf_counter() - t0:.0f} s)")
            if built:
                break
            messages.append({"role": "user", "content": "Fix these problems and reply with the full corrected JSON only:\n- "
                                                        + "\n- ".join(errors[:12])})
        if not built:
            break
        v = len(versions) + 1
        png = d / f"{name}_v{v}.preview.png"
        hipoly.render_preview(Path(built["glb"]), png)
        (d / f"{name}_v{v}.spec.json").write_text(json.dumps(built["spec"], indent=1), encoding="utf-8")
        built.update(version=v, preview=str(png), critique=None)
        if vision is not None:
            try:
                built["critique"] = vision(png, prompt)
            except Exception as exc:
                log(f"[{name}] vision critique failed: {exc}")
        versions.append(built)
        c = built["critique"]
        log(f"[{name}] v{v}: {built['check']['triangles']} tris, critique {c}")
        if c is None or rnd == rounds or (c["recognisable"] >= 4 and c["proportions"] >= 4):
            break
        messages.append({"role": "user", "content": (
            f"Your spec built fine ({built['check']['triangles']} triangles). A reviewer looked at the rendered preview "
            f"and says it looks like: \"{c['looks_like']}\". Scores 1-5: recognisable {c['recognisable']}, proportions "
            f"{c['proportions']}, colours {c['colours']}. Suggested fixes:\n- " + "\n- ".join(c["fixes"] or ["(none)"])
            + f"\nImprove the model so it clearly reads as: {prompt}. Keep what works. Reply with the full JSON only.")})
    report: dict[str, Any] = {"prompt": prompt, "name": name, "llm": LLM_MODEL, "vision": VISION_MODEL if vision else None,
                              "model_calls": calls, "seconds": round(time.perf_counter() - t_start, 1),
                              "versions": [{k: v for k, v in x.items() if k != "spec"} for x in versions]}
    if versions:
        best = max(versions, key=lambda x: (_score(x["critique"]), x["version"]))
        report["best_version"] = best["version"]
        for src, dst in ((best["glb"], d / f"{name}.glb"), (best["preview"], d / f"{name}.preview.png")):
            Path(dst).write_bytes(Path(src).read_bytes())
        (d / f"{name}.spec.json").write_text(json.dumps(best["spec"], indent=1), encoding="utf-8")
        report.update(ok=True, glb=str(d / f"{name}.glb"), preview=str(d / f"{name}.preview.png"),
                      triangles=best["check"]["triangles"], critique=best["critique"],
                      method="BOSSMAN LOCAL: LLM part spec -> procedural build -> vision critique loop")
    else:
        report.update(ok=False, error="no valid spec after all tries")
    (d / "transcript.json").write_text(json.dumps(messages[len(few_shot()) + 1:], indent=1, ensure_ascii=False),
                                       encoding="utf-8")
    (d / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prompt")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--rounds", type=int, default=2, help="vision-critique revision rounds")
    ap.add_argument("--tries", type=int, default=3, help="attempts per round to get a valid spec")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--neural", action="store_true", help="image->3D route (z-image-turbo -> TripoSR) instead")
    ap.add_argument("--height", type=float, default=1.2, help="--neural only: model height in metres")
    ap.add_argument("--game", type=Path, default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if args.neural:
        from voxel3d import neural3d

        name = args.name or _slug(args.prompt).replace("bm_", "nm_")
        rep = neural3d.make_neural(args.prompt, name, args.out / name, args.height)
        report = dict(rep["prop"], ok=rep["prop"]["check"]["ok"], prompt=args.prompt)
    else:
        report = generate(args.prompt, args.out, ollama_chat(), None if args.no_vision else ollama_vision(),
                          tries=args.tries, rounds=args.rounds, name=args.name)
    print(json.dumps({k: report.get(k) for k in ("name", "ok", "triangles", "critique", "model_calls", "seconds", "glb")},
                     ensure_ascii=False))
    if args.game and report.get("ok"):
        from voxel3d import make_props

        rec = {"glb": report["glb"], "label": args.prompt, "method": report.get("method", ""),
               "check": mesh_check.check_prop_glb(Path(report["glb"]))}
        for p in make_props.install(args.game, [rec], args.apply):
            print(("WROTE " if args.apply else "WOULD WRITE ") + p)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
