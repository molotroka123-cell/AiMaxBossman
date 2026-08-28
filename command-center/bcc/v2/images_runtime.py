"""Runtime helpers for BOSSMAN Images.

The first implementation ships with a deterministic local SVG mock provider so
the entire library/queue/UI can be proven without network calls or new packages.

A real provider should implement the same ImageProvider.render() contract.
"""
from __future__ import annotations

import hashlib
import html
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ImageProvider(Protocol):
    name: str

    async def render(self, spec: dict[str, Any], index: int) -> tuple[bytes, str, dict[str, Any]]:
        """Return bytes, mime_type, provider metadata."""


@dataclass
class ImageStorage:
    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, relative: str) -> Path:
        dest = (self.root / relative).resolve()
        try:
            dest.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("image path escapes media root") from exc
        return dest

    def save(self, relative: str, data: bytes) -> Path:
        dest = self._safe_path(relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def resolve_existing(self, path: str) -> Path:
        p = Path(path).expanduser().resolve()
        try:
            p.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("asset outside image media root") from exc
        if not p.is_file():
            raise FileNotFoundError(p)
        return p

    @staticmethod
    def mime_for(path: Path, fallback: str = "application/octet-stream") -> str:
        return mimetypes.guess_type(str(path))[0] or fallback


class MockImageProvider:
    """Network-free visual provider for exact feature E2E tests."""

    name = "mock-image"

    async def render(self, spec: dict[str, Any], index: int) -> tuple[bytes, str, dict[str, Any]]:
        prompt = str(spec.get("prompt") or "BOSSMAN image")
        width = max(256, min(int(spec.get("width") or 1024), 4096))
        height = max(256, min(int(spec.get("height") or 1024), 4096))
        seed = int(spec.get("seed") or 1) + index
        digest = hashlib.sha256(f"{prompt}|{seed}|{index}".encode()).hexdigest()
        c1 = f"#{digest[0:6]}"
        c2 = f"#{digest[6:12]}"
        c3 = f"#{digest[12:18]}"
        label = html.escape(prompt.strip()[:110])
        model = html.escape(str(spec.get("model_alias") or self.name))
        ratio = html.escape(str(spec.get("aspect_ratio") or "1:1"))

        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs>
  <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{c1}"/>
    <stop offset=".55" stop-color="{c2}"/>
    <stop offset="1" stop-color="{c3}"/>
  </linearGradient>
  <radialGradient id="r">
    <stop offset="0" stop-color="#ffffff" stop-opacity=".30"/>
    <stop offset="1" stop-color="#000000" stop-opacity="0"/>
  </radialGradient>
</defs>
<rect width="100%" height="100%" rx="26" fill="url(#g)"/>
<circle cx="{width*.72:.0f}" cy="{height*.28:.0f}" r="{min(width,height)*.28:.0f}" fill="url(#r)"/>
<path d="M0 {height*.76:.0f} C {width*.2:.0f} {height*.56:.0f}, {width*.42:.0f} {height*.92:.0f}, {width:.0f} {height*.62:.0f} L {width} {height} L 0 {height} Z" fill="#020817" opacity=".48"/>
<rect x="{width*.055:.0f}" y="{height*.68:.0f}" width="{width*.89:.0f}" height="{height*.23:.0f}" rx="22" fill="#020817" opacity=".72"/>
<text x="{width*.085:.0f}" y="{height*.755:.0f}" font-size="{max(20,int(min(width,height)*.033))}" fill="#fff" font-family="Inter,Arial,sans-serif" font-weight="700">{label}</text>
<text x="{width*.085:.0f}" y="{height*.82:.0f}" font-size="{max(15,int(min(width,height)*.022))}" fill="#c8d7ff" font-family="Inter,Arial,sans-serif">BOSSMAN · {model} · {ratio} · seed {seed}</text>
</svg>"""
        return svg.encode("utf-8"), "image/svg+xml", {
            "provider": self.name,
            "mock": True,
            "seed": seed,
            "digest": digest[:16],
        }


def safe_filename(value: str, fallback: str = "image") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")
    return (cleaned[:80] or fallback)
