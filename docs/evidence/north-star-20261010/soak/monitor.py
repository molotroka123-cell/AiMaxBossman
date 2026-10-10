"""Independent heartbeat monitor for the 24h Bossman soak (stdlib + psutil only).

Every INTERVAL seconds it appends one JSON line to heartbeat.jsonl. It reads RUN.json for the soak pid,
start time and port; it never touches the soak, the backend or any owner service. It stops by itself at
start + 24h30m and writes monitor-final.json. The soak verdict itself is out/metrics.json + out/findings.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "RUN.json"
OUT = ROOT / "out"
HB = ROOT / "heartbeat.jsonl"
FINAL = ROOT / "monitor-final.json"
INTERVAL = int(os.environ.get("SOAK_MONITOR_INTERVAL", "300"))
END_AFTER_S = 24 * 3600 + 30 * 60
MARKERS = ("Traceback", "FAIL", "[high]")


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tree(pid: int) -> list[psutil.Process]:
    try:
        p = psutil.Process(pid)
        return [p, *p.children(recursive=True)]
    except psutil.Error:
        return []


def dir_size(p: Path) -> int:
    total = 0
    for root, _, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def health(port: int) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=5) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
            return {"http": r.status, "status": body.get("status"), "build": body.get("build_sha_short"),
                    "source": body.get("source")}
    except Exception as e:  # noqa: BLE001  (backend is restarted on purpose every 25 interactions)
        return {"error": type(e).__name__ + ": " + str(e)[:120]}


def scan_log(path: Path, offset: int) -> tuple[int, dict, str]:
    counts = {m: 0 for m in MARKERS}
    last = ""
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
            offset += len(data)
        text = data.decode("utf-8", "replace")
        for line in text.splitlines():
            for m in MARKERS:
                if m in line:
                    counts[m] += 1
        lines = [x for x in text.splitlines() if x.strip()]
        last = lines[-1][:300] if lines else ""
    except OSError:
        pass
    return offset, counts, last


def main() -> int:
    run = json.loads(RUN.read_text(encoding="utf-8"))
    pid, port = int(run["soak_pid"]), int(run["port"])
    start = datetime.strptime(run["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    end = start + END_AFTER_S
    offset, last_line, beats, gaps, max_rss, fail_total = 0, "", 0, [], 0.0, {m: 0 for m in MARKERS}
    prev = None
    first_dead = None
    while True:
        now = time.time()
        procs = tree(pid)
        rss = 0
        names: dict[str, int] = {}
        for p in procs:
            try:
                rss += p.memory_info().rss
                n = (p.name() or "").lower()
                names[n] = names.get(n, 0) + 1
            except psutil.Error:
                pass
        offset, counts, tail = scan_log(OUT / "soak.stdout.log", offset)
        last_line = tail or last_line
        for k, v in counts.items():
            fail_total[k] += v
        alive = bool(procs) and procs[0].is_running()
        if not alive and first_dead is None:
            first_dead = utc()
        events = OUT / "events.jsonl"
        rec = {"utc": utc(), "elapsed_h": round((now - start) / 3600, 3), "soak_pid": pid, "soak_alive": alive,
               "health": health(port), "tree_rss_mb": round(rss / 1048576, 1),
               "python_procs": sum(v for k, v in names.items() if k.startswith("python")),
               "ollama_procs": sum(v for k, v in names.items() if "ollama" in k),
               "chrome_procs": sum(v for k, v in names.items() if "chrom" in k or "headless" in k),
               "free_ram_gb": round(psutil.virtual_memory().available / 2**30, 1),
               "out_mb": round(dir_size(OUT) / 1048576, 1),
               "events_lines": sum(1 for _ in events.open("rb")) if events.is_file() else 0,
               "new_markers": counts, "stdout_last": last_line,
               "exit_file": (OUT / "soak.exit.txt").read_text(encoding="utf-8", errors="replace").strip()
               if (OUT / "soak.exit.txt").is_file() else None}
        with HB.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        beats += 1
        max_rss = max(max_rss, rec["tree_rss_mb"])
        if prev is not None and now - prev > 900:
            gaps.append({"from_epoch": prev, "to_epoch": now, "gap_s": round(now - prev)})
        prev = now
        if now >= end:
            break
        time.sleep(min(INTERVAL, max(1, end - now)))
    FINAL.write_text(json.dumps({"finished_utc": utc(), "heartbeats": beats, "gaps_over_15min": gaps,
                                 "max_tree_rss_mb": max_rss, "markers_total": fail_total,
                                 "soak_first_seen_dead_utc": first_dead,
                                 "note": "monitor evidence only; the soak verdict is out/metrics.json + out/findings.json"},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
