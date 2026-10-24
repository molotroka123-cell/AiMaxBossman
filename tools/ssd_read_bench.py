"""C: vs D: sequential read of the SAME model file: unbuffered (cold, bypasses the OS cache) and buffered (warm).

    python tools/ssd_read_bench.py --rel z-image-turbo/z_image_turbo-Q8_0.gguf --gb 3 --runs 3 --receipt <file.json>

Both roots must hold byte-identical copies (the first --gb are hashed and compared before timing). Runs alternate C, D, C, D
so thermal/background drift hits both. This measures the device, not Bossman's load path; the end-to-end load is a separate
measurement and the speedup is only claimed from what both give.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import mmap
import os
import statistics
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

CHUNK = 16 << 20
ROOTS = {"C": Path(r"C:\Users\asd\Bossman\models\media"), "D": Path(r"D:\Bossman\models\media")}
FILE_FLAG_NO_BUFFERING = 0x20000000
GENERIC_READ, SHARE_READ, OPEN_EXISTING = 0x80000000, 1, 3


def read_unbuffered(path: Path, nbytes: int) -> float:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(path), GENERIC_READ, SHARE_READ, None, OPEN_EXISTING, FILE_FLAG_NO_BUFFERING, None)
    if h in (None, wintypes.HANDLE(-1).value, 0xFFFFFFFFFFFFFFFF):
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    buf = mmap.mmap(-1, CHUNK)                      # page-aligned, as NO_BUFFERING requires
    addr = ctypes.addressof(ctypes.c_char.from_buffer(buf))
    got = wintypes.DWORD(0)
    done = 0
    t0 = time.perf_counter()
    try:
        while done < nbytes:
            if not k32.ReadFile(wintypes.HANDLE(h), ctypes.c_void_p(addr), CHUNK, ctypes.byref(got), None) or got.value == 0:
                break
            done += got.value
    finally:
        dt = time.perf_counter() - t0
        k32.CloseHandle(wintypes.HANDLE(h))
        buf.close()
    return done / dt / 1e6


def read_buffered(path: Path, nbytes: int) -> float:
    done = 0
    t0 = time.perf_counter()
    with open(path, "rb", buffering=0) as f:
        while done < nbytes:
            b = f.read(CHUNK)
            if not b:
                break
            done += len(b)
    return done / (time.perf_counter() - t0) / 1e6


def head_hash(path: Path, nbytes: int) -> str:
    h, done = hashlib.sha256(), 0
    with open(path, "rb", buffering=0) as f:
        while done < nbytes:
            b = f.read(min(CHUNK, nbytes - done))
            if not b:
                break
            h.update(b)
            done += len(b)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rel", required=True)
    ap.add_argument("--gb", type=float, default=3.0)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--receipt")
    a = ap.parse_args(argv)
    n = int(a.gb * 2**30)
    paths = {k: v / a.rel for k, v in ROOTS.items()}
    sizes = {k: p.stat().st_size for k, p in paths.items()}
    hashes = {k: head_hash(p, n) for k, p in paths.items()}
    identical = len(set(sizes.values())) == 1 and len(set(hashes.values())) == 1
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "file": a.rel, "bytes_read": n, "sizes": sizes,
           "head_sha256": hashes, "identical": identical, "cold_unbuffered_MBps": {"C": [], "D": []}, "warm_buffered_MBps": {"C": [], "D": []}}
    if not identical:
        res["verdict"] = "FAIL: the two copies differ; no comparison made"
    else:
        for _ in range(a.runs):
            for k in ("C", "D"):
                res["cold_unbuffered_MBps"][k].append(round(read_unbuffered(paths[k], n)))
        for _ in range(a.runs):
            for k in ("C", "D"):
                res["warm_buffered_MBps"][k].append(round(read_buffered(paths[k], n)))
        med = {m: {k: statistics.median(v) for k, v in res[m].items()} for m in ("cold_unbuffered_MBps", "warm_buffered_MBps")}
        res["median"] = med
        res["D_over_C_cold"] = round(med["cold_unbuffered_MBps"]["D"] / med["cold_unbuffered_MBps"]["C"], 2)
        res["verdict"] = "MEASURED (device read only; end-to-end load not yet compared)"
    try:
        res["runtime_sha"] = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip()
    except OSError:
        pass
    if a.receipt:
        Path(a.receipt).parent.mkdir(parents=True, exist_ok=True)
        Path(a.receipt).write_text(json.dumps(res, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(res))
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
