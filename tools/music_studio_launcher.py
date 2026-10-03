#!/usr/bin/env python3
"""Start the ACE-Step 1.5 REST API from the Bossman install directory (AMD ROCm on Windows).

Why a wrapper instead of `python acestep/api_server.py`:
the AMD ROCm-for-Windows PyTorch wheels are built without `torch.distributed`
(`torch.distributed.is_available()` is False). ACE-Step imports `vector_quantize_pytorch`
unconditionally, and that package does `from torch.distributed import nn`, which raises
`ImportError: cannot import name 'group' from 'torch.distributed'`
(upstream ACE-Step-1.5 issue #644, open). Inference is single-process, so a stub
`torch.distributed.nn` is enough. Nothing in site-packages is patched; on a PyTorch with real
distributed support the stub is not installed.

Layout (written by tools/music_studio_install.py):
    <install>/bossman_acestep_launcher.py   <- this file, copied
    <install>/repo/                          <- ACE-Step-1.5 checkout (acestep/api_server.py)
    <install>/venv_rocm/                     <- Python 3.12 + ROCm PyTorch

Arguments are passed through to acestep.api_server (--host, --port, ...). Bossman always binds
127.0.0.1. Standard library only until the stub is in place.
"""
from __future__ import annotations

import os
import runpy
import sys
import types
from pathlib import Path


def install_distributed_stub() -> bool:
    """Make `from torch.distributed import nn` importable when distributed is unavailable."""
    import torch.distributed as dist

    if dist.is_available():
        return False
    stub = types.ModuleType("torch.distributed.nn")
    stub.__path__ = []  # behave like a package so `import torch.distributed.nn.x` fails cleanly
    stub.__doc__ = "Bossman stub: this PyTorch build has no torch.distributed (single-process inference)."
    sys.modules["torch.distributed.nn"] = stub
    dist.nn = stub
    return True


def find_repo() -> Path:
    override = os.environ.get("BOSSMAN_ACESTEP_REPO", "").strip()
    return Path(override) if override else Path(__file__).resolve().parent / "repo"


def main() -> None:
    repo = find_repo()
    script = repo / "acestep" / "api_server.py"
    if not script.is_file():
        sys.exit(f"ACE-Step source not found: {script}")
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    stubbed = install_distributed_stub()
    print(f"[bossman] ACE-Step launcher: repo={repo} distributed_stub={stubbed}", flush=True)
    sys.argv[0] = str(script)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
