"""HTTP face of the one backend (manifest control contract). Binds loopback by default; optional bearer token."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from . import __version__
from .actuator import ActionRefused
from .service import VisionService
from .sources import NotLoopback


class StartBody(BaseModel):
    mode: str
    adapter: str = "poker_train"
    path: str | None = None
    url: str | None = None
    act: bool = False
    window: dict | None = None
    max_frames: int = 100000
    interval_s: float = 0.0
    seed: int = 1
    bootstrap: str | None = None
    max_hands: int = 40


class CalibBody(BaseModel):
    adapter: str = "ton_poker"
    labelled_dir: str
    heldout_dir: str
    rois: dict


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    data = Path(data_dir or os.environ.get("POKERVISION_DATA", Path.home() / ".pokervision"))
    svc = VisionService(data, Path(__file__).parent / "adapters" / "profiles")
    app = FastAPI(title="Poker Vision", version=__version__)
    app.state.svc = svc
    token = os.environ.get("POKERVISION_TOKEN")

    def auth(authorization: str | None = Header(default=None)) -> None:
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "bearer token required")

    @app.get("/health")
    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "OK", "version": __version__, "running": svc.status()["running"]}

    @app.get("/api/v1/capabilities", dependencies=[Depends(auth)])
    def caps() -> dict:
        return svc.capabilities()

    @app.post("/api/v1/session", dependencies=[Depends(auth)])
    def start(b: StartBody) -> dict:
        try:
            return svc.start(**b.model_dump())
        except (NotLoopback, ActionRefused) as exc:
            raise HTTPException(403, {"code": "REFUSED", "message": str(exc)})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": str(exc)})
        except RuntimeError as exc:
            raise HTTPException(409, {"code": "BUSY", "message": str(exc)})

    @app.post("/api/v1/calibrate", dependencies=[Depends(auth)])
    def calibrate(b: CalibBody) -> dict:
        try:
            return svc.calibrate_roi(b.adapter, b.labelled_dir, b.heldout_dir, b.rois)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": str(exc)})

    @app.post("/api/v1/stop", dependencies=[Depends(auth)])
    def stop() -> dict:
        return svc.stop()

    @app.get("/api/v1/status", dependencies=[Depends(auth)])
    def status() -> dict:
        return svc.status()

    @app.get("/api/v1/metrics")
    def metrics() -> dict:
        s = svc.status()
        return {"frames": s["frames"], "latency_p50_ms": s["latency_ms"]["p50"], "latency_p95_ms": s["latency_ms"]["p95"], "hands": s["hands"]}

    @app.get("/api/v1/state", dependencies=[Depends(auth)])
    def state() -> dict:
        return svc.state()

    @app.get("/api/v1/overlay.png", dependencies=[Depends(auth)])
    def overlay() -> Response:
        png = svc.overlay_png()
        if png is None:
            raise HTTPException(404, "no frame yet")
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/history", dependencies=[Depends(auth)])
    def history() -> dict:
        return svc.history()

    @app.get("/api/v1/labelqueue", dependencies=[Depends(auth)])
    def labelqueue() -> dict:
        import json
        p = svc.data_dir / "label_queue" / "index.jsonl"
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return {"count": len(rows), "items": rows[-50:]}

    return app


app = None


def main() -> None:
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("POKERVISION_PORT") or os.environ.get("APP_PORT") or "8931"))
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("poker-vision binds loopback only")
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
