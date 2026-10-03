#!/usr/bin/env python3
"""Review selected smart frames locally and checkpoint every result immediately."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image


def checkpoint(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def review_frame(path: Path, *, model: str, max_edge: int, timeout: int) -> dict[str, Any]:
    original = Image.open(path).convert("RGB")
    size = original.size
    if max(size) > max_edge:
        original.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    original.save(buf, format="JPEG", quality=88, optimize=True)
    image = base64.b64encode(buf.getvalue()).decode("ascii")
    prompt = (
        "Read this chart screenshot. Return JSON only: asset/ticker, venue, timeframe, "
        "readable prices/levels, indicators, annotations and promotion/chat overlays. "
        "Separate direct visual evidence from inference. Never guess digits. "
        "Say which nearby spoken trading idea the frame supports or contradicts, "
        "or mark relation unknown if transcript context is insufficient."
    )
    body = {"model": model, "stream": False, "think": False, "keep_alive": "10m",
            "format": "json", "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt, "images": [image]}]}
    request = urllib.request.Request("http://127.0.0.1:11434/api/chat",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    raw = (result.get("message") or {}).get("content") or "{}"
    try:
        observation = json.loads(raw)
    except json.JSONDecodeError:
        observation = {"raw": raw}
    return {"frame": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "input_size": size, "inference_size": original.size, "model": model,
            "elapsed_seconds": round(time.monotonic() - start, 2), "observation": observation}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("frames", nargs="+", help="selected frame filenames; only these are reviewed")
    ap.add_argument("--model", default="bossman-fast-qwen36-vision:latest")
    ap.add_argument("--max-edge", type=int, default=1024)
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()
    manifest = json.loads((args.run_dir / "smart_frames.json").read_text(encoding="utf-8"))
    out = args.run_dir / "smart_visual_audit.json"
    payload = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {
        "video_id": args.run_dir.name, "model": args.model, "status": "PARTIAL", "frames": []}
    done = {row.get("frame") for row in payload.get("frames", [])}
    selected = {row["frame"]: row for row in manifest.get("frames", [])}
    for name in args.frames:
        if name in done:
            continue
        if name not in selected:
            ap.error(f"frame not in smart_frames.json: {name}")
        src = args.run_dir / "frames" / name
        meta = selected[name]
        try:
            row = review_frame(src, model=args.model, max_edge=args.max_edge, timeout=args.timeout)
            row.update({"time_s": meta.get("time_s"), "score": meta.get("score"),
                        "selection_reasons": meta.get("reasons"), "transcript_evidence": meta.get("evidence")})
        except Exception as exc:  # record a failed frame without losing prior successful frames
            row = {"frame": name, "time_s": meta.get("time_s"), "status": "ERROR",
                   "error_type": type(exc).__name__, "error": str(exc)[:300]}
        payload.setdefault("frames", []).append(row)
        payload["status"] = "PARTIAL"
        checkpoint(out, payload)
        print(json.dumps({"frame": name, "time_s": meta.get("time_s"),
                          "status": row.get("status", "OK"), "elapsed": row.get("elapsed_seconds")}, ensure_ascii=False), flush=True)
    payload["status"] = "COMPLETE" if len(payload.get("frames", [])) >= len(manifest.get("frames", [])) else "PARTIAL"
    checkpoint(out, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
