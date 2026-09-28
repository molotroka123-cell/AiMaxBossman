"""Lottie animation catalog: fetch, verify and load pinned assets for the engine.

    python tools/motion_studio/lottie_assets.py fetch [--dir CACHE]     # download + sha256-verify all
    python tools/motion_studio/lottie_assets.py check [--dir CACHE]     # verify what is cached

The catalog (lottie/catalog.json) pins every file by sha256, so a changed or substituted
upstream file is rejected, never silently rendered. Files are cached outside Git.
License: Noto Emoji Animation, CC BY 4.0. Videos using them must carry the attribution
line from the catalog (make_video.py writes it into the video's metadata).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "lottie" / "catalog.json"
DEFAULT_CACHE = Path.home() / ".cache" / "bossman-motion" / "lottie"


def catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def ids() -> set[str]:
    return {item["id"] for item in catalog()["items"]}


def _path(cache: Path, item: dict) -> Path:
    return cache / f"{item['id']}.json"


def fetch(cache: Path = DEFAULT_CACHE, only: set[str] | None = None) -> dict[str, str]:
    """Returns {id: "ok" | "cached" | error}. Never keeps a file whose hash does not match."""
    cache.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    for item in catalog()["items"]:
        if only is not None and item["id"] not in only:
            continue
        target = _path(cache, item)
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]:
            status[item["id"]] = "cached"
            continue
        try:
            with urllib.request.urlopen(item["url"], timeout=60) as resp:
                data = resp.read()
        except Exception as exc:  # network errors are reported per item, not fatal for the rest
            status[item["id"]] = f"download failed: {exc.__class__.__name__}"
            continue
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            status[item["id"]] = "sha256 mismatch (upstream changed) - rejected"
            continue
        target.write_bytes(data)
        status[item["id"]] = "ok"
    return status


def load(used: set[str], cache: Path = DEFAULT_CACHE) -> dict[str, dict]:
    """Animation JSON for the ids a spec uses; fetches missing ones, fails loudly on any gap."""
    unknown = used - ids()
    if unknown:
        raise SystemExit(f"unknown lottie ids: {sorted(unknown)}")
    status = fetch(cache, used)
    bad = {k: v for k, v in status.items() if v not in ("ok", "cached")}
    if bad:
        raise SystemExit(f"lottie assets unavailable: {bad}")
    by_id = {item["id"]: item for item in catalog()["items"]}
    return {i: json.loads(_path(cache, by_id[i]).read_text(encoding="utf-8")) for i in sorted(used)}


def used_ids(spec: dict) -> set[str]:
    out: set[str] = set()
    for sc in spec["scenes"]:
        if sc.get("lottie"):
            out.add(sc["lottie"])
        for it in sc.get("items", []):
            icon = it.get("icon", "")
            if icon.startswith("lottie:"):
                out.add(icon.split(":", 1)[1])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "check"])
    ap.add_argument("--dir", type=Path, default=DEFAULT_CACHE)
    args = ap.parse_args()
    if args.cmd == "fetch":
        status = fetch(args.dir)
    else:
        status = {}
        for item in catalog()["items"]:
            p = _path(args.dir, item)
            status[item["id"]] = ("cached" if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == item["sha256"]
                                  else "missing or changed")
    good = sum(v in ("ok", "cached") for v in status.values())
    for k, v in status.items():
        if v not in ("ok", "cached"):
            print(f"{k}: {v}")
    print(f"LOTTIE_ASSETS {good}/{len(status)} verified")
    raise SystemExit(0 if good == len(status) else 1)


if __name__ == "__main__":
    main()
