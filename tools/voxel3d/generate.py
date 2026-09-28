"""Prompt -> voxel spec with a local model (native Ollama /api/chat, think:false, JSON mode).

Same loop as Motion Studio generate_spec.py (fix 274543f8): the model only writes JSON; every
draft goes through repair_spec() + validate(); the error list is sent back as the next user turn
until the draft is valid or `tries` is exhausted. Before every model call the shared-machine
PAUSE file is honoured (wait while it exists).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

from . import MAX_DIM
from .spec import MAX_VOXELS_PER_OP, build, lint, repair_spec, validate

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
PAUSE_FILE = Path(os.environ.get("BOSSMAN_PAUSE_FILE", r"C:\Users\asd\Bossman\rc19-owner-test.PAUSE"))

SYSTEM = f"""You design small voxel (Minecraft-style) 3D objects for a block game. Output ONE JSON object and nothing else.

Format: {{"name": "snake_case_name", "size": [X, Y, Z], "palette": {{"material": "#rrggbb", ...}}, "ops": [...]}}
Axes: x = width, y = UP (height), z = depth. Integer cells, 0-based, inclusive. Each of X, Y, Z is 1..{MAX_DIM}.
The object stands on the ground: its lowest voxels are at y = 0. Use 2..8 palette materials with realistic colors.
Ops, applied in order (later ops overwrite earlier ones; "mat": "air" carves voxels away):
- {{"op":"box","from":[x,y,z],"to":[x,y,z],"mat":"m"}}  add "hollow": true for walls only (rooms, chests)
- {{"op":"sphere","center":[x,y,z],"radius":r,"mat":"m"}}  radius may be [rx,ry,rz] for an ellipsoid
- {{"op":"cylinder","base":[x,y,z],"radius":r,"height":h,"axis":"y","mat":"m"}}  base = centre of the first disc
- {{"op":"cone","base":[x,y,z],"radius":r,"height":h,"direction":"up","mat":"m"}}  tapers to a point
- {{"op":"line","from":[x,y,z],"to":[x,y,z],"mat":"m"}}
- {{"op":"voxels","at":[[x,y,z],...],"mat":"m"}}  single details (eyes, handles, flames), at most {MAX_VOXELS_PER_OP} cells
- {{"op":"roof","from":[x,y,z],"to":[x,y,z],"style":"gable","axis":"x","mat":"m"}}  stepped roof over the from..to
  footprint starting at from.y (gable: ridge along axis x or z; pyramid: shrinks on all sides)
- {{"op":"layer","y":n,"rows":["..ab..", ...],"key":{{"a":"m1","b":"m2"}}}}  row i = z i, character j = x j, "." = nothing
- {{"op":"mirror","axis":"x"}}  copies everything built so far to the other side (x -> X-1-x)
Rules: every part must touch the main body face-to-face (no floating pieces); keep every cell inside size;
leave empty space where the real object has it (hollow rooms, gaps under a table, open sides of a bridge walkway
between railings): build only the parts or carve with "air"; prefer box/line/layer/mirror over long voxel lists;
use 3..30 ops; pick a size that fits the object (a torch is about 3x8x3, a house about 11x10x11, a tree about 9x14x9);
recognisable silhouette first, a few colour details second. Material names: lowercase letters, digits, _.
"""

EXAMPLE_PROMPT = "a small wooden table"
EXAMPLE_SPEC = {
    "name": "wooden_table", "size": [6, 4, 4],
    "palette": {"wood": "#9c6b3c", "dark_wood": "#6e4a28"},
    "ops": [
        {"op": "box", "from": [0, 3, 0], "to": [5, 3, 3], "mat": "wood"},
        {"op": "box", "from": [0, 0, 0], "to": [0, 2, 0], "mat": "dark_wood"},
        {"op": "box", "from": [0, 0, 3], "to": [0, 2, 3], "mat": "dark_wood"},
        {"op": "mirror", "axis": "x"},
    ],
}


EXAMPLE2_PROMPT = "a stone well with a small wooden roof"
EXAMPLE2_SPEC = {
    "name": "stone_well", "size": [7, 9, 7],
    "palette": {"stone": "#8a8a8a", "water": "#3a6fd0", "wood": "#8b5a2b", "roof": "#6e3b22"},
    "ops": [
        {"op": "cylinder", "base": [3, 0, 3], "radius": 3, "height": 3, "mat": "stone"},
        {"op": "cylinder", "base": [3, 1, 3], "radius": 2, "height": 2, "mat": "air"},
        {"op": "cylinder", "base": [3, 1, 3], "radius": 2, "height": 1, "mat": "water"},
        {"op": "box", "from": [0, 3, 3], "to": [0, 5, 3], "mat": "wood"},
        {"op": "mirror", "axis": "x"},
        {"op": "roof", "from": [0, 6, 1], "to": [6, 6, 5], "style": "gable", "axis": "x", "mat": "roof"},
    ],
}
FEW_SHOT_REPLIES = 2
CUT_OFF = ("the reply was not a single complete JSON object (it was probably cut off because it was too long): "
           "use fewer, larger ops - box, line, layer, roof, mirror - and keep voxels lists short")


def wait_if_paused(log: Callable[[str], None] | None = None, poll: float = 30.0, max_wait: float = 3600.0) -> float:
    waited = 0.0
    while PAUSE_FILE.exists() and waited < max_wait:
        if log:
            log(f"PAUSE file present ({PAUSE_FILE}); waiting")
        time.sleep(poll)
        waited += poll
    return waited


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def chat_ollama(messages: list[dict], model: str = DEFAULT_MODEL, endpoint: str = DEFAULT_ENDPOINT,
                timeout: float = 300.0, max_tokens: int = 4096, fmt: str | None = "json",
                temperature: float = 0.3) -> str:
    payload = {"model": model, "messages": messages, "stream": False, "think": False, "keep_alive": "10m",
               "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 8192, "seed": 7}}
    if fmt:
        payload["format"] = fmt
    data = _post(endpoint.rstrip("/") + "/api/chat", payload, timeout)
    return (data.get("message") or {}).get("content", "")


def extract_json(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _log(msg: str) -> None:
    print(f"[voxel3d.generate] {msg}", file=sys.stderr, flush=True)


def generate(prompt: str, chat: Callable[[list[dict]], str], tries: int = 3,
             log: Callable[[str], None] = _log) -> tuple[dict | None, list[str], list[str], list[dict]]:
    """Returns (spec or None, remaining errors, repairs applied, transcript).

    Validator errors are sent back until valid. A valid draft that trips a lint (e.g. a solid block
    for an object that should have open space) gets ONE improvement turn; if that answer is not
    valid, the first valid draft is kept."""
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"OBJECT: {EXAMPLE_PROMPT}"},
                {"role": "assistant", "content": json.dumps(EXAMPLE_SPEC)},
                {"role": "user", "content": f"OBJECT: {EXAMPLE2_PROMPT}"},
                {"role": "assistant", "content": json.dumps(EXAMPLE2_SPEC)},
                {"role": "user", "content": f"OBJECT: {prompt}"}]
    errors: list[str] = ["no attempt"]
    fixes: list[str] = []
    fallback: tuple[dict, list[str]] | None = None
    draft: dict | None = None
    for attempt in range(1, max(1, tries) + 1):
        wait_if_paused(log)
        t0 = time.monotonic()
        try:
            raw = chat(messages)
        except Exception as exc:  # timeout / connection: report per try
            errors = [f"model call failed: {exc.__class__.__name__}: {str(exc)[:160]}"]
            log(f"try {attempt}: {errors[0]}")
            continue
        log(f"try {attempt}: {len(raw)} chars in {time.monotonic() - t0:.1f} s")
        messages.append({"role": "assistant", "content": raw})
        draft = extract_json(raw)
        if draft is None:
            errors = [CUT_OFF if len(raw) > 2000 else "the reply was not a single JSON object"]
            log(f"try {attempt}: reply is not a JSON object")
        else:
            draft, fixes = repair_spec(draft)
            errors = validate(draft)
            log(f"try {attempt}: {'VALID' if not errors else f'{len(errors)} validator errors'}"
                + (f", {len(fixes)} repairs" if fixes else ""))
            if not errors:
                hints = lint(build(draft)[0])
                if hints and fallback is None and attempt < tries:
                    fallback = (draft, fixes)
                    log(f"try {attempt}: lint -> one improvement turn: {hints[0][:60]}")
                    messages.append({"role": "user", "content": "Valid, but check this and reply with the full JSON only:\n- "
                                                                + "\n- ".join(hints)})
                    continue
                return draft, [], fixes, messages
        if fallback is not None:
            log(f"try {attempt}: improvement not valid; keeping the first valid draft")
            return fallback[0], [], fallback[1], messages
        messages.append({"role": "user", "content": "Fix these problems and reply with the full corrected JSON only:\n- "
                                                    + "\n- ".join(errors[:20])})
    if fallback is not None:
        return fallback[0], [], fallback[1], messages
    return (draft if isinstance(draft, dict) else None), errors, fixes, messages
