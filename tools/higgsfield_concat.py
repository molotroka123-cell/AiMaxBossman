"""Финальная сборка ролика из сегментов Higgsfield (TZ-5).

Читает artifacts/higgsfield/state.json, склеивает сегменты ffmpeg concat,
делает превью-кадр (3s) каждого сегмента и report.md.

Запуск: python tools/higgsfield_concat.py [--out final_coffee.mp4]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts" / "higgsfield"
VIDS = BASE / "videos"
STATE = BASE / "state.json"


def ffmpeg_bin() -> str:
    for cand in (shutil.which("ffmpeg"),
                 r"C:\Users\timur\AppData\Local\Temp\opencode\ffmpeg-7.1-essentials_build\bin\ffmpeg.exe"):
        if cand and Path(cand).exists():
            return cand
    raise SystemExit("ffmpeg не найден — положить в tools/bin или PATH")


def probe(path: Path) -> float | None:
    ff = shutil.which("ffprobe")
    if not ff:
        return None
    try:
        out = subprocess.run([ff, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except Exception:
        return None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    out_name = "final_coffee.mp4"
    if "--out" in sys.argv:
        out_name = sys.argv[sys.argv.index("--out") + 1]
    st = json.loads(STATE.read_text(encoding="utf-8-sig"))
    segs = [s for s in st["segments"] if (VIDS / s["file"]).exists()]
    if not segs:
        raise SystemExit("нет скачанных сегментов в state.json")

    ff = ffmpeg_bin()
    frames = VIDS / "frames"
    frames.mkdir(exist_ok=True)

    report = ["# Higgsfield final report", "",
              f"собран: {datetime.now().isoformat(timespec='seconds')}", ""]
    lines = []
    for s in segs:
        p = VIDS / s["file"]
        dur = probe(p) or s.get("dur")
        frame = frames / f"{p.stem}_3s.jpg"
        subprocess.run([ff, "-y", "-ss", "3", "-i", str(p), "-frames:v", "1",
                        "-update", "1", str(frame)], capture_output=True, timeout=60)
        lines.append(f"file '{p.as_posix()}'")
        report.append(f"- seg{s['idx']:02d}: {s['file']} {dur:.2f}s "
                      f"({s.get('kb')}KB) кадр: frames/{frame.name}")
    total = sum(probe(VIDS / s["file"]) or 0 for s in segs)

    lst = VIDS / "concat.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    final = VIDS / out_name
    r = subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(final)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise SystemExit(f"concat failed: {r.stderr[-400:]}")

    report += ["", f"итог: {out_name} {final.stat().st_size // 1024}KB, "
                   f"сегментов {len(segs)}, суммарно ~{total:.1f}s", ""]
    (BASE / "report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
