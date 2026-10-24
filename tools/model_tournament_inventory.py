"""Step 0 of the local model tournament: what is installed, exactly, with NO download (owner 24.10).

    python tools/model_tournament_inventory.py [--out docs/audits/2026-10-24-local-model-tournament/00-inventory.json]

Read-only against the local Ollama (127.0.0.1:11434) and the local Bossman backend. For every tag: weights blob sha256
(parsed from the Modelfile ``FROM``), tag digest, size, architecture, DECLARED context, context baked into the
Modelfile (``num_ctx``), capabilities. Tags that share a weights blob are ONE model in several configurations, never
separate contestants. Also: Ollama version, loaded models, backend runtime SHA, free space on D:, free RAM.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import time
import urllib.request
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434"
BACKEND = "http://127.0.0.1:8801"


def _get(url: str, body: dict | None = None, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback only
        return json.load(resp)


def weights_blob(modelfile: str) -> str | None:
    """sha256 of the weights file named by the Modelfile's FROM line (a local blob path), else None."""
    for line in re.findall(r"^FROM\s+(.+)$", modelfile or "", re.M):
        m = re.search(r"sha256-([0-9a-f]{64})", line)
        if m:
            return m.group(1)
    return None


def baked_num_ctx(parameters: str) -> int | None:
    for line in (parameters or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit():
            return int(parts[1])
    return None


def group_by_weights(rows: list[dict]) -> list[dict]:
    """Groups of tags that run the SAME weights; a group of one is a unique model."""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        groups[row.get("weights_sha256") or f"unknown:{row['name']}"].append(row)
    return [{"weights_sha256": k, "tags": [r["name"] for r in v], "size_gb": v[0]["size_gb"],
             "configurations": [{"tag": r["name"], "baked_num_ctx": r["baked_num_ctx"], "digest": r["digest"][:12]} for r in v],
             "duplicate": len(v) > 1} for k, v in sorted(groups.items(), key=lambda kv: kv[1][0]["name"])]


def inventory() -> dict:
    rows = []
    for tag in _get(f"{OLLAMA}/api/tags")["models"]:
        show = _get(f"{OLLAMA}/api/show", {"model": tag["name"]})
        info = show.get("model_info", {})
        rows.append({"name": tag["name"], "digest": tag["digest"], "size_gb": round(tag["size"] / 1e9, 1),
                     "weights_sha256": weights_blob(show.get("modelfile", "")),
                     "architecture": info.get("general.architecture"),
                     "declared_context": next((v for k, v in info.items() if k.endswith(".context_length")), None),
                     "baked_num_ctx": baked_num_ctx(show.get("parameters", "")),
                     "capabilities": show.get("capabilities") or []})
    try:
        runtime = _get(f"{BACKEND}/health/live")
    except OSError as exc:
        runtime = {"error": type(exc).__name__}
    try:
        loaded = [{"name": m["name"], "context_length": m.get("context_length"), "size_gb": round(m["size"] / 1e9, 1)}
                  for m in _get(f"{OLLAMA}/api/ps")["models"]]
    except OSError:
        loaded = None
    try:
        import psutil
        ram = {"total_gb": round(psutil.virtual_memory().total / 1e9, 1), "free_gb": round(psutil.virtual_memory().available / 1e9, 1)}
    except ImportError:
        ram = None
    d = shutil.disk_usage("D:/") if Path("D:/").exists() else None
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ollama_version": _get(f"{OLLAMA}/api/version")["version"],
            "runtime": {k: runtime.get(k) for k in ("build_sha", "source_identity", "version", "error")},
            "loaded_now": loaded, "ram": ram,
            "d_drive_free_gb": round(d.free / 1e9) if d else None, "tags": len(rows),
            "unique_weights": len({r["weights_sha256"] for r in rows}), "models": rows, "groups": group_by_weights(rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="docs/audits/2026-10-24-local-model-tournament/00-inventory.json")
    a = ap.parse_args(argv)
    doc = inventory()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    dup = [g for g in doc["groups"] if g["duplicate"]]
    print(f"{doc['tags']} tags, {doc['unique_weights']} unique weights, {len(dup)} duplicate groups; ollama {doc['ollama_version']}; "
          f"runtime {str(doc['runtime'].get('build_sha'))[:12]}; D: free {doc['d_drive_free_gb']} GB -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
