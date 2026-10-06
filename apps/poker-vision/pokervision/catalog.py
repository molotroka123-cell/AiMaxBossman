"""Source catalog for the Bossman picker (screen-share style): every capturable surface with identity, preview and what is PROVEN for it.
Any surface can be shown; recognition/advice/control are enabled per interface only where an adapter run exists (otherwise UNVERIFIED)."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import cv2

from .adapters import registry
from .sources import list_windows

SAMPLE = Path(__file__).resolve().parents[1] / "evidence" / "sample_frames"


def _thumb(path: Path | None, w: int = 220) -> str | None:
    if path is None or not path.exists():
        return None
    img = cv2.imread(str(path))
    if img is None:
        return None
    h = int(img.shape[0] * w / img.shape[1])
    ok, buf = cv2.imencode(".jpg", cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 70])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode() if ok else None


def _proof(adapter_id: str) -> dict:
    ad = registry.get(adapter_id, None)
    c = ad.capabilities
    return {"adapter": adapter_id, "observe": c.observe, "coach": c.advise_live, "control": c.act,
            "status": "PASS(sandbox)" if c.act else "UNVERIFIED"}


def list_sources(url: str = "http://127.0.0.1:3000/") -> list[dict]:
    out = [
        {"id": "sandbox:trainer", "kind": "sandbox", "title": "Мой Poker Train в тестовом рабочем столе (loopback)", "process": "poker-train (sandbox)",
         "thumbnail": _thumb(SAMPLE / "0003.png"), "spec": {"kind": "sandbox", "url": url, "bootstrap": "cash_nl10", "dpr": 1.0},
         "proof": _proof("poker_train"), "note": "тестовый двойник окон ОС: перемещение, размер, перекрытие, закрытие"},
        {"id": "replay:sample", "kind": "replay", "title": "Запись: образцы кадров Poker Train", "process": "—",
         "thumbnail": _thumb(SAMPLE / "0003.png"), "spec": {"kind": "replay", "path": str(SAMPLE)},
         "proof": {**_proof("poker_train"), "control": False, "status": "replay: наблюдение/подсказки"}, "note": "только просмотр записи"},
    ]
    for w in list_windows():
        out.append({"id": f"window:{w['hwnd']}", "kind": "window", "title": w["title"], "process": "—", "thumbnail": None, "rect": w["rect"],
                    "spec": {"kind": "window", "hwnd": w["hwnd"]}, "proof": {"adapter": None, "observe": True, "coach": False, "control": False, "status": "UNVERIFIED"},
                    "note": "захват окна: NOT_RUN в контейнере; распознавание покера для него не подтверждено"})
    return out


def platform_note() -> dict:
    return {"platform": sys.platform, "windows_enumeration": sys.platform == "win32"}
