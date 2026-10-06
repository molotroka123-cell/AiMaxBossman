"""HTTP face of the one backend (manifest control contract). Binds loopback by default; optional bearer token."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
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


class DeskStartBody(BaseModel):
    source: dict
    adapter: str = "poker_train"
    desk_mode: str = "observe"
    seed: int = 1
    max_hands: int = 40
    auto_deal: bool = True
    verify_timeout_s: float = 6.0


class ModeBody(BaseModel):
    mode: str


class PauseBody(BaseModel):
    paused: bool


class SandboxBody(BaseModel):
    args: dict = {}


class VerifyBody(BaseModel):
    adapter: str = "ton_poker"
    heldout_dir: str
    context: str = ""


class CalibBody(BaseModel):
    adapter: str = "ton_poker"
    labelled_dir: str
    heldout_dir: str
    rois: dict


def create_app(data_dir: str | Path | None = None, profile_dir: str | Path | None = None) -> FastAPI:
    data = Path(data_dir or os.environ.get("POKERVISION_DATA", Path.home() / ".pokervision"))
    svc = VisionService(data, Path(profile_dir) if profile_dir else Path(__file__).parent / "adapters" / "profiles")
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

    # ---------------------------------------------------------------- desk: source panel, modes, stream, overlay
    @app.get("/api/v1/sources", dependencies=[Depends(auth)])
    def sources() -> dict:
        from .catalog import list_sources, platform_note
        return {"sources": list_sources(os.environ.get("POKERTRAIN_URL", "http://127.0.0.1:3000/")), **platform_note()}

    @app.post("/api/v1/desk/start", dependencies=[Depends(auth)])
    def desk_start(b: DeskStartBody) -> dict:
        try:
            return svc.start_desk(**b.model_dump())
        except PermissionError as exc:
            raise HTTPException(403, {"code": "NOT_ALLOWED", "message": str(exc)})
        except NotImplementedError as exc:
            raise HTTPException(501, {"code": "NOT_RUN", "message": str(exc)})
        except NotLoopback as exc:
            raise HTTPException(403, {"code": "REFUSED", "message": str(exc)})
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": str(exc)})
        except RuntimeError as exc:
            raise HTTPException(409, {"code": "BUSY", "message": str(exc)})

    @app.post("/api/v1/desk/mode", dependencies=[Depends(auth)])
    def desk_mode(b: ModeBody) -> dict:
        try:
            return svc.set_desk_mode(b.mode)
        except PermissionError as exc:
            raise HTTPException(403, {"code": "NOT_ALLOWED", "message": str(exc)})
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": str(exc)})

    @app.post("/api/v1/desk/pause", dependencies=[Depends(auth)])
    def desk_pause(b: PauseBody) -> dict:
        try:
            return svc.set_paused(b.paused)
        except RuntimeError as exc:
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": str(exc)})

    @app.post("/api/v1/desk/resume-executor", dependencies=[Depends(auth)])
    def desk_resume() -> dict:
        return svc.resume_executor()

    @app.post("/api/v1/desk/sandbox/{cmd}", dependencies=[Depends(auth)])
    def desk_sandbox(cmd: str, b: SandboxBody) -> dict:
        if cmd not in ("move", "resize", "minimize", "close", "reopen", "cover", "uncover"):
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": "unknown sandbox command"})
        try:
            return svc.sandbox_cmd(cmd, **b.args)
        except PermissionError as exc:
            raise HTTPException(403, {"code": "NOT_ALLOWED", "message": str(exc)})

    @app.get("/api/v1/frame.jpg", dependencies=[Depends(auth)])
    def frame_jpg() -> Response:
        f = svc.frame_jpeg()
        if f is None:
            raise HTTPException(404, "no frame yet")
        return Response(f[1], media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Frame-Seq": str(f[0])})

    @app.get("/api/v1/stream.mjpeg", dependencies=[Depends(auth)])
    async def stream() -> StreamingResponse:
        import asyncio

        async def gen():
            last = -1
            idle = 0
            while True:
                f = svc.frame_jpeg()
                if f is not None and f[0] != last:
                    last = f[0]; idle = 0
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(f[1])).encode() + b"\r\n\r\n" + f[1] + b"\r\n"
                else:
                    idle += 1
                    if idle > 600 and not svc.status()["running"]:      # ~30 s without frames and no session: end the stream
                        return
                await asyncio.sleep(0.05)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/overlay.json", dependencies=[Depends(auth)])
    def overlay_json() -> dict:
        o = svc.overlay_json()
        if o is None:
            raise HTTPException(404, "no frame yet")
        return o

    @app.get("/api/v1/recommendation", dependencies=[Depends(auth)])
    def recommendation() -> dict:
        return svc.recommendation_view()

    @app.get("/api/v1/journal", dependencies=[Depends(auth)])
    def journal() -> dict:
        return svc.journal_view()

    @app.get("/api/v1/journal/frame", dependencies=[Depends(auth)])
    def journal_frame(name: str) -> Response:
        j = svc.journal
        if not j or ".." in name or not name.startswith("frames/"):
            raise HTTPException(404, "no such frame")
        p = j.dir / name
        if not p.exists():
            raise HTTPException(404, "no such frame")
        return Response(p.read_bytes(), media_type="image/png")

    @app.post("/api/v1/calibrate", dependencies=[Depends(auth)])
    def calibrate(b: CalibBody) -> dict:
        try:
            return svc.calibrate_roi(b.adapter, b.labelled_dir, b.heldout_dir, b.rois)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(400, {"code": "BAD_REQUEST", "message": str(exc)})

    @app.post("/api/v1/verify", dependencies=[Depends(auth)])
    def verify(b: VerifyBody) -> dict:
        try:
            return svc.verify_roi(b.adapter, b.heldout_dir, b.context)
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
