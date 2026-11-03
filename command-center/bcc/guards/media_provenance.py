"""G4 — media provenance check before publishing / uploading audio or video.

Incident 03.11.2026: six third-party songs were uploaded under fictional titles («Owner Track • ...»). A renamed
copy has a new name but the same sound.

Rule: before an upload, every audio/video file is compared with a LOCAL registry of known third-party files:
  * sha256 of the bytes (exact copy, any name);
  * duration (ffprobe) + a «fingerprint-lite»: the RMS envelope of the decoded mono 8 kHz signal in 0.5 s windows,
    compared by Pearson correlation (survives renaming, re-encoding, bitrate/container change and volume change);
  * chromaprint (`fpcalc`) when it is installed — not required.
A match flags the file; flagged files are never uploaded (`assert_uploadable` raises).
Registry = JSON {"version": 1, "entries": [{name, path, duration, sha256, envelope, chromaprint?}]}, built from a
folder of known third-party files with `build_registry`. Everything stays local; nothing is sent anywhere."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MEDIA_SUFFIXES = frozenset({".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma",
                            ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})
SAMPLE_RATE = 8000
WINDOW_S = 0.5
DURATION_TOL_S = 1.5
#: envelope correlation at or above this = same recording
MATCH_CORR = 0.90
#: a short clip may be a cut of a registry track: sliding correlation over the longer envelope
MIN_ENVELOPE_POINTS = 8


@dataclass
class Fingerprint:
    path: str
    name: str
    duration: float
    sha256: str
    envelope: list[float] = field(default_factory=list)
    chromaprint: str = ""

    def as_entry(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "duration": round(self.duration, 3), "sha256": self.sha256,
                "envelope": [round(x, 5) for x in self.envelope], **({"chromaprint": self.chromaprint}
                                                                      if self.chromaprint else {})}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tool(name: str) -> str | None:
    return shutil.which(name)


def probe_duration(path: Path, *, run: Callable[..., Any] = subprocess.run) -> float:
    exe = _tool("ffprobe")
    if not exe:
        raise FileNotFoundError("ffprobe not found (install ffmpeg)")
    r = run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60, check=False)
    try:
        return float((r.stdout or "").strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def rms_envelope(path: Path, *, window_s: float = WINDOW_S, run: Callable[..., Any] = subprocess.run) -> list[float]:
    """RMS of the decoded mono 8 kHz signal per window, normalised to max 1 (volume-independent)."""
    exe = _tool("ffmpeg")
    if not exe:
        raise FileNotFoundError("ffmpeg not found")
    r = run([exe, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=300, check=False)
    pcm = r.stdout or b""
    return envelope_from_pcm(pcm, window=int(SAMPLE_RATE * window_s))


def envelope_from_pcm(pcm: bytes, *, window: int) -> list[float]:
    import array
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    out: list[float] = []
    for i in range(0, len(a) - window + 1, window):
        chunk = a[i:i + window]
        out.append(math.sqrt(sum(x * x for x in chunk) / window))
    peak = max(out) if out else 0.0
    return [x / peak for x in out] if peak > 0 else out


def chromaprint(path: Path, *, run: Callable[..., Any] = subprocess.run) -> str:
    exe = _tool("fpcalc")
    if not exe:
        return ""
    r = run([exe, "-plain", str(path)], capture_output=True, text=True, timeout=120, check=False)
    return (r.stdout or "").strip()


def fingerprint(path: str | Path) -> Fingerprint:
    p = Path(path)
    return Fingerprint(path=str(p), name=p.name, duration=probe_duration(p), sha256=sha256_file(p),
                       envelope=rms_envelope(p), chromaprint=chromaprint(p))


def correlation(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < MIN_ENVELOPE_POINTS:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def best_alignment(short: list[float], long: list[float], *, max_shift: int | None = None) -> float:
    """Max correlation of `short` against every offset of `long` (a cut / trimmed copy)."""
    if len(short) > len(long):
        short, long = long, short
    if len(short) < MIN_ENVELOPE_POINTS:
        return 0.0
    shifts = len(long) - len(short)
    if max_shift is not None:
        shifts = min(shifts, max_shift)
    return max(correlation(short, long[s:s + len(short)]) for s in range(shifts + 1))


@dataclass
class Match:
    file: str
    registry_name: str
    registry_path: str
    reason: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


def compare(fp: Fingerprint, entry: dict[str, Any]) -> Match | None:
    name = str(entry.get("name") or "")
    if fp.sha256 and fp.sha256 == entry.get("sha256"):
        return Match(fp.path, name, str(entry.get("path") or ""), "identical bytes (sha256)", 1.0)
    if fp.chromaprint and entry.get("chromaprint") and fp.chromaprint == entry["chromaprint"]:
        return Match(fp.path, name, str(entry.get("path") or ""), "chromaprint identical", 1.0)
    env = list(entry.get("envelope") or [])
    if not fp.envelope or not env:
        return None
    same_len = abs(float(entry.get("duration") or 0) - fp.duration) <= DURATION_TOL_S
    score = correlation(fp.envelope, env) if same_len else best_alignment(fp.envelope, env)
    if score >= MATCH_CORR:
        kind = "same duration + envelope" if same_len else "excerpt of a registry track (envelope)"
        return Match(fp.path, name, str(entry.get("path") or ""), f"{kind}, corr={score:.3f}", round(score, 4))
    return None


def load_registry(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.get("entries") or [])


def build_registry(folder: str | Path, out: str | Path, *, fp_fn: Callable[[Path], Fingerprint] = fingerprint) -> int:
    """Registry of every media file under `folder` (the known third-party files). Returns the entry count."""
    entries = [fp_fn(p).as_entry() for p in sorted(Path(folder).rglob("*"))
               if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES]
    Path(out).write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=0),
                         encoding="utf-8")
    return len(entries)


def media_files(paths: list[str | Path]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out += [q for q in sorted(p.rglob("*")) if q.is_file() and q.suffix.lower() in MEDIA_SUFFIXES
                    and ".git" not in q.parts]
        elif p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES:
            out.append(p)
    return out


def check(paths: list[str | Path], registry: list[dict[str, Any]], *,
          fp_fn: Callable[[Path], Fingerprint] = fingerprint) -> dict[str, Any]:
    """{ok, checked, flagged:[...], errors:[...]} — ok only when no file matches the registry and none failed."""
    flagged: list[dict[str, Any]] = []
    errors: list[str] = []
    files = media_files(paths)
    for p in files:
        try:
            fp = fp_fn(p)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{p.name}: {exc}")
            continue
        for entry in registry:
            m = compare(fp, entry)
            if m:
                d = m.as_dict()
                d["renamed"] = Path(m.file).name != m.registry_name
                flagged.append(d)
                break
    return {"ok": not flagged and not errors, "checked": len(files), "flagged": flagged, "errors": errors}


def summary_ru(res: dict[str, Any]) -> str:
    if res["ok"]:
        return f"G4 PASS: {res['checked']} медиафайлов, совпадений с реестром чужих файлов нет"
    lines = [f"G4 FAIL: проверено {res['checked']}, помечено {len(res['flagged'])}, ошибок {len(res['errors'])}"]
    for f in res["flagged"][:20]:
        lines.append(f"  {Path(f['file']).name} = «{f['registry_name']}» ({f['reason']})"
                     + (" — ПЕРЕИМЕНОВАННАЯ копия" if f.get("renamed") else ""))
    lines += [f"  ошибка: {e}" for e in res["errors"][:10]]
    return "\n".join(lines)


class FlaggedMedia(PermissionError):
    pass


def assert_uploadable(paths: list[str | Path], registry: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    """Raise FlaggedMedia unless every file passed; never upload a flagged (or unverifiable) file."""
    res = check(paths, registry, **kw)
    if not res["ok"]:
        raise FlaggedMedia(summary_ru(res))
    return res
