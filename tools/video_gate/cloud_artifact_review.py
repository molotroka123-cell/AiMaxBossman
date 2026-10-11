"""Independent artifact descriptions of face-swap crops from several cloud vision models (owner 10.10).

PRIVACY: only small face-area crops of problem frames are sent (<= 384 px, made by artifact_audit.py --crops), never a
whole video, never names. Each model gets the same image and prompt and never sees another model's answer.
Keys are read from %LOCALAPPDATA%/Bossman/keys/provider-keys.env (the Mistral key from the vault-encrypted Bossman
registry, decrypted in memory) and never printed or written.

Economy routing (owner 10.10): Gemini and NVIDIA NIM are free tier; GLM vision (OpenRouter) and Mistral stop at
`budget` USD each; the shared Haiku cap is not used.

  python cloud_artifact_review.py --images a.jpg,b.jpg --out review.json [--models glm,nvidia,nvidia2,mistral]  (gemini: free quota is exhausted for the day, not retried)
Writes review.json: per image, per model: parsed findings, raw text, tokens, cost.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CATEGORIES = ("seam_edge", "colour_mismatch", "temporal_flicker", "ghosting_double_face", "identity_change",
              "misalignment", "blur_smear", "occlusion_error")

PROMPT = (
    "The image is a 2x2 grid of four face-area crops from ONE short video clip, in time order: top-left, top-right, "
    "bottom-left, bottom-right (about half a second in total). The face in the video was replaced by a face-swap "
    "program (consented edit). Act as a strict visual-effects quality reviewer and list the visible artifacts of the "
    "face replacement. Use only these categories: " + ", ".join(CATEGORIES) + ". Meanings: seam_edge = a visible "
    "edge/outline/box where the new face meets the original skin, hair or background; colour_mismatch = face skin "
    "colour or brightness differs from the neck/ears/body; temporal_flicker = brightness/texture/shape of the face "
    "changes between the four frames without reason; ghosting_double_face = two faces blended (double eyes/brows, "
    "semi-transparent features); identity_change = the face looks like a different person in some frames; "
    "misalignment = eyes/mouth/nose in the wrong place (e.g. eyes on the forehead) or face not following the head; "
    "blur_smear = face clearly blurrier/smoother/plastic compared with the surroundings; occlusion_error = hair, "
    "hands or objects in front of the face are cut or painted over badly. Answer ONLY with JSON: "
    '{"artifacts":[{"category":"...","severity":1-3,"frames":["TL","TR","BL","BR"],"where":"short","note":"short"}],'
    '"overall":0-3}. severity 1 = noticeable on close look, 2 = clearly visible, 3 = ruins the shot. '
    'If nothing is visible return {"artifacts":[],"overall":0}.'
)

MODELS = {
    "gemini": ("gemini", "gemini-2.5-flash"),
    "glm": ("openrouter", "z-ai/glm-4.6v"),
    "nvidia": ("nvidia", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"),
    "nvidia2": ("nvidia", "meta/llama-3.2-90b-vision-instruct"),
    "mistral": ("mistral", "mistral-medium-3.5"),
}
#: tried in order (and with the second key) when a free-tier quota answers 429
GEMINI_FALLBACKS = ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-flash-lite", "gemini-3.1-flash-lite")
PAID = ("glm", "mistral")


def _mistral_key() -> str:
    """The Mistral key lives vault-encrypted in the Bossman registry (bcc.db providers id 10); decrypted in memory."""
    data_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
    try:
        from bcc.secrets import Vault
        con = sqlite3.connect(f"file:{data_dir / 'bcc.db'}?mode=ro", uri=True)
        return Vault(data_dir).decrypt(con.execute("select api_key_enc from providers where id=10").fetchone()[0])
    except Exception:  # noqa: BLE001 - the model is then reported as an error, not a crash
        return ""


def load_keys() -> dict:
    path = Path(os.environ.get("LOCALAPPDATA", ""), "Bossman", "keys", "provider-keys.env")
    keys = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip().strip('"')
    keys.setdefault("MISTRAL_API_KEY", _mistral_key())
    return keys


def parse_findings(text: str) -> dict:
    """The first JSON object in the answer; unknown categories dropped; never raises."""
    text = text or ""
    cut = text.rfind('{"artifacts"')                  # reasoning models think aloud first: the answer is the last object
    m = re.search(r"\{.*\}", text[cut:] if cut >= 0 else text, re.S)
    if not m:
        return {"artifacts": [], "overall": None, "parse_error": True}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"artifacts": [], "overall": None, "parse_error": True}
    arts = []
    for a in d.get("artifacts", []) if isinstance(d, dict) else []:
        if isinstance(a, dict) and a.get("category") in CATEGORIES:
            try:
                sev = int(a.get("severity", 1))
            except (TypeError, ValueError):
                sev = 1
            arts.append({"category": a["category"], "severity": max(1, min(3, sev)), "frames": a.get("frames", []),
                         "where": str(a.get("where", ""))[:120], "note": str(a.get("note", ""))[:200]})
    return {"artifacts": arts, "overall": d.get("overall") if isinstance(d, dict) else None}


def _post(url: str, body: dict, headers: dict, timeout: int = 150) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _ask_gemini(model: str, b64: str, keys: dict) -> tuple[str, dict, str]:
    body = {"contents": [{"parts": [{"text": PROMPT}, {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048}}
    last = None
    for name in dict.fromkeys((model, *GEMINI_FALLBACKS)):
        for kname in ("GEMINI_API_KEY", "GEMINI_API_KEY_2"):
            if kname not in keys:
                continue
            try:
                d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent", body,
                          {"x-goog-api-key": keys[kname]})
            except urllib.error.HTTPError as e:
                last = e
                if e.code not in (429, 404, 503):
                    raise
                continue
            text = "".join(p.get("text", "") for p in d["candidates"][0]["content"].get("parts", []))
            u = d.get("usageMetadata", {})
            return text, {"in": u.get("promptTokenCount"), "out": u.get("candidatesTokenCount"), "cost_usd": 0.0}, name
    raise last if last else RuntimeError("no gemini key")


def ask(model_key: str, image: Path, keys: dict) -> dict:
    kind, model = MODELS[model_key]
    b64 = base64.b64encode(image.read_bytes()).decode()
    t0 = time.time()
    if kind == "gemini":
        text, usage, model = _ask_gemini(model, b64, keys)
    else:
        url = {"openrouter": "https://openrouter.ai/api/v1/chat/completions",
               "nvidia": "https://integrate.api.nvidia.com/v1/chat/completions",
               "mistral": "https://api.mistral.ai/v1/chat/completions"}[kind]
        key = keys[{"openrouter": "OPENROUTER_API_KEY", "nvidia": "NVIDIA_API_KEY", "mistral": "MISTRAL_API_KEY"}[kind]]
        body = {"model": model, "temperature": 0.2, "max_tokens": 4000 if kind == "nvidia" else 1500,
                "messages": [{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                                          {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]}
        if kind == "openrouter":
            body["usage"] = {"include": True}
        d = _post(url, body, {"Authorization": "Bearer " + key})
        content = d["choices"][0]["message"].get("content") or ""
        text = content if isinstance(content, str) else "".join(b.get("text", "") for b in content if isinstance(b, dict))
        u = d.get("usage", {})
        if kind == "openrouter":
            cost = float(u.get("cost") or 0.0)
        elif kind == "mistral":    # no cost field: an upper estimate, $0.5 / $2.5 per million tokens
            cost = ((u.get("prompt_tokens") or 0) * 0.5 + (u.get("completion_tokens") or 0) * 2.5) / 1e6
        else:
            cost = 0.0
        usage = {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens"), "cost_usd": cost}
    return {"model": model, "seconds": round(time.time() - t0, 1), "usage": usage, "text": text[:3000], **parse_findings(text)}


def _one(m: str, img: Path, keys: dict) -> dict:
    res: dict = {}
    for attempt in range(2):
        try:
            return ask(m, img, keys)
        except urllib.error.HTTPError as e:
            res = {"error": f"HTTP {e.code}", "detail": e.read()[:300].decode("utf-8", "replace")}
            if e.code not in (429, 500, 502, 503):
                break
            time.sleep(8 * (attempt + 1))
        except Exception as e:  # noqa: BLE001 - recorded, the other models still run
            res = {"error": type(e).__name__, "detail": str(e)[:300]}
    return res


def review(images: list[Path], models: list[str], out: Path, budget: float = 0.15) -> dict:
    """Models run in parallel, one image at a time. Paid models stop at `budget` USD each (cumulative over reruns that
    share `out`). The result file is rewritten after every image."""
    from concurrent.futures import ThreadPoolExecutor
    keys = load_keys()
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    spent = {m: sum(r["usage"].get("cost_usd") or 0.0 for row in data.values() for k, r in row.items()
                    if k == m and "usage" in r) for m in models}
    with ThreadPoolExecutor(max(1, len(models))) as pool:
        for img in images:
            row = data.setdefault(str(img), {})
            todo = [m for m in models if not (m in row and "error" not in row[m])]
            skip = [m for m in todo if m in PAID and spent[m] >= budget]
            for m in skip:
                row[m] = {"error": "budget"}
            run = [m for m in todo if m not in skip]
            for m, res in zip(run, pool.map(lambda x: _one(x, img, keys), run)):
                row[m] = res
                if "usage" in res:
                    spent[m] += res["usage"].get("cost_usd") or 0.0
            out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return data


def confirmed(row: dict, min_models: int = 2) -> dict:
    """category -> models that reported it (only categories reported by >= min_models)."""
    by: dict[str, list[str]] = {}
    for m, r in row.items():
        for a in r.get("artifacts", []) if isinstance(r, dict) else []:
            if m not in by.setdefault(a["category"], []):
                by[a["category"]].append(m)
    return {k: v for k, v in by.items() if len(v) >= min_models}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default="glm,nvidia,nvidia2,mistral")
    a = ap.parse_args(argv)
    data = review([Path(p) for p in a.images.split(",") if p], a.models.split(","), Path(a.out))
    for img, row in data.items():
        print(Path(img).parent.parent.name + "/" + Path(img).name,
              {m: (r.get("overall"), [x["category"] for x in r.get("artifacts", [])]) if "error" not in r else r["error"]
               for m, r in row.items()}, "CONFIRMED:", confirmed(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
