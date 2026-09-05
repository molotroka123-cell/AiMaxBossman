"""Separate, explicitly enabled BCC instance; frozen BCC source stays untouched."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import threading
from typing import Any

_DISCOVERY_LOCK = threading.Lock()


def create(*, data_dir: str | Path | None = None, state_root: str | Path | None = None,
           artifact_root: str | Path | None = None, settings: Any = None,
           runtime_factory: Any = None, start_workers: bool = False) -> Any:
    import bcc.features
    from bcc.api import create_app
    from bcc.config import Settings

    if settings is None:
        directory = Path(data_dir or os.environ.get("BOSSMAN_EXECUTIVE_OS_DATA_DIR")
                         or Path.home() / ".bossman" / "executive-os-bcc").resolve()
        # Explicit URL prevents inherited DATABASE_URL from selecting a live DB.
        settings = Settings(data_dir=directory,
                            database_url=f"sqlite+aiosqlite:///{directory / 'bcc.db'}",
                            host="127.0.0.1", port=8812)
    directory = Path(settings.data_dir).resolve()
    extensions = str(Path(__file__).resolve().parent / "extensions")
    with _DISCOVERY_LOCK:
        original_paths = list(bcc.features.__path__)
        try:
            if extensions not in bcc.features.__path__:
                bcc.features.__path__.append(extensions)
            app = create_app(settings, start_workers=start_workers, announce_token=False)
        finally:
            bcc.features.__path__[:] = original_paths
    app.state.svc.executive_os_config = {
        "state_root": Path(state_root) if state_root is not None else directory / "executive-os",
        "artifact_root": Path(artifact_root) if artifact_root is not None else directory / "artifacts",
        "runtime_factory": runtime_factory,
    }
    return app


def main() -> None:
    import uvicorn
    from bcc.app import is_loopback

    parser = argparse.ArgumentParser(description="Run the opt-in Executive OS BCC instance")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8812)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    if not is_loopback(args.host):
        parser.error("Executive OS sidecar must bind to a loopback address")
    uvicorn.run(create(data_dir=args.data_dir), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
