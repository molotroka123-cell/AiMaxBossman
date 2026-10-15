"""Build both 1080p videos from source and the locked Git snapshot."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
WORK = OUT / "_render_chunks"
FRAMES = 2385
CHUNKS = [(0, 600), (600, 1200), (1200, 1800), (1800, FRAMES)]


def render(lang: str, index: int, start: int, end: int) -> Path:
    part = WORK / f"{lang}_{index:02d}.mp4"
    log = WORK / f"{lang}_{index:02d}.log"
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run([
            sys.executable, str(HERE / "render.py"), "video", "--lang", lang,
            "--out", str(part), "--start", str(start), "--end", str(end),
        ], check=True, stdout=stream, stderr=subprocess.STDOUT)
    return part


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    jobs = [(lang, i, a, b) for lang in ("ru", "en") for i, (a, b) in enumerate(CHUNKS)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(render, *job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            path = future.result()
            print(f"done {job[0]} chunk {job[1]} {path.stat().st_size} bytes", flush=True)
    for lang in ("ru", "en"):
        concat = WORK / f"{lang}.txt"
        concat.write_text("".join(f"file '{(WORK / f'{lang}_{i:02d}.mp4').as_posix()}'\n" for i in range(len(CHUNKS))), encoding="utf-8")
        final = OUT / f"bossman_evolution_{lang}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(final)], check=True)
        print(f"final {final} {final.stat().st_size} bytes", flush=True)
        if final.stat().st_size > 15_000_000:
            raise RuntimeError(f"{final.name} exceeds 15 MB; rerender with higher CRF")


if __name__ == "__main__":
    main()
