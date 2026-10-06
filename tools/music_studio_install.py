#!/usr/bin/env python3
"""Install ACE-Step 1.5 for Bossman Music Studio (AMD ROCm on Windows). Standard library only.

    python tools\\music_studio_install.py check [--dir DIR]
    python tools\\music_studio_install.py install [--dir DIR] [--python PY312] [--dry-run] [--skip-models]

Default DIR: %BOSSMAN_ACESTEP_DIR%, else <parent of this checkout>\\media-runtime\\acestep (the place
Bossman looks first). Layout produced (what bcc/features/music_studio.py expects):

    DIR\\repo\\                          ACE-Step-1.5 source at the pinned commit (+ repo\\checkpoints models)
    DIR\\venv_rocm\\                     Python 3.12 + AMD ROCm PyTorch (torch 2.9.1+rocm7.2.1)
    DIR\\bossman_acestep_launcher.py     tools\\music_studio_launcher.py (torch.distributed stub, see its docstring)

Steps (the same ones done by hand on the owner's Ryzen AI MAX+ 395 / gfx1151 on 2026-09-30):
  1. git clone ACE-Step-1.5 (shallow) and check out the pinned commit;
  2. python3.12 -m venv venv_rocm;
  3. install the torch/torchvision/torchaudio 2.9.x+rocm7.2.1 wheels from repo.radeon.com (a pinned
     index, deliberately not a Bossman dependency: ~2 GB, torch 0.8 + rocm-sdk-core 0.6 +
     rocm-sdk-libraries 0.5);
  4. pip install -c constraints -r requirements-rocm.txt + diskcache python-multipart toml typer-slim
     (the constraint keeps pip from replacing the ROCm torch);
  5. python -m acestep.model_downloader  (main model from Hugging Face, ~9.4 GB: turbo DiT, LM 1.7B,
     Qwen3-Embedding, VAE).
Nothing is installed without the `install` subcommand; `--dry-run` only prints the plan.

Smart App Control stays ON. Known facts (2026-09-30): torch/ROCm, scipy, numba, soundfile import fine;
PyWavelets (pywt) is blocked by SAC ("Политика управления приложениями заблокировала этот файл"), which
only disables the optional DCW sampler correction. Do not turn SAC off to work around anything here.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/ACE-Step/ACE-Step-1.5.git"
REPO_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"     # the commit verified on 2026-09-30
ROCM_LINKS = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/"
TORCH_PINS = ["torch==2.9.1+rocm7.2.1", "torchvision==0.24.1+rocm7.2.1", "torchaudio==2.9.1+rocm7.2.1"]
EXTRA_PIP = ["diskcache", "python-multipart", "toml", "typer-slim"]
HERE = Path(__file__).resolve().parent


def default_dir() -> Path:
    explicit = os.environ.get("BOSSMAN_ACESTEP_DIR", "").strip()
    return Path(explicit) if explicit else HERE.parents[1] / "media-runtime" / "acestep"


def venv_python(root: Path) -> Path:
    return root / "venv_rocm" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def report(root: Path) -> dict:
    repo, py = root / "repo", venv_python(root)
    checkpoints = repo / "checkpoints"
    models = {name: (checkpoints / name).is_dir() for name in
              ("acestep-v15-turbo", "acestep-5Hz-lm-1.7B", "vae", "Qwen3-Embedding-0.6B")}
    return {"dir": str(root), "repo_source": (repo / "acestep" / "api_server.py").is_file(),
            "venv_python": py.is_file(), "launcher": (root / "bossman_acestep_launcher.py").is_file(),
            "models": models, "complete": all([(repo / "acestep" / "api_server.py").is_file(), py.is_file(),
                                               (root / "bossman_acestep_launcher.py").is_file(), *models.values()])}


def run(cmd: list, *, dry: bool, cwd: Path | None = None, env: dict | None = None) -> None:
    print("+", " ".join(str(part) for part in cmd), flush=True)
    if not dry:
        subprocess.run([str(part) for part in cmd], cwd=cwd, env=env, check=True)


def find_python312(explicit: str | None) -> str:
    candidates = [explicit] if explicit else []
    candidates += [str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe"),
                   shutil.which("python3.12") or "", shutil.which("python") or ""]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        out = subprocess.run([candidate, "-c", "import sys;print(sys.version_info[:2])"], capture_output=True, text=True)
        if out.stdout.strip() == "(3, 12)":
            return candidate
    sys.exit("Python 3.12 not found (AMD ROCm wheels for Windows exist for 3.12 only). Pass --python PATH.")


def install(root: Path, python: str | None, dry: bool, skip_models: bool) -> None:
    repo = root / "repo"
    if not dry:
        root.mkdir(parents=True, exist_ok=True)
    if not (repo / "acestep").is_dir():
        run(["git", "clone", "--depth", "1", REPO_URL, repo], dry=dry)
    # A shallow clone has only HEAD; fetch the pinned commit when HEAD has moved on.
    head = "" if dry else subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if dry or head != REPO_COMMIT:
        run(["git", "-C", repo, "fetch", "--depth", "1", "origin", REPO_COMMIT], dry=dry)
        run(["git", "-C", repo, "-c", "advice.detachedHead=false", "checkout", REPO_COMMIT], dry=dry)
    py312 = find_python312(python) if not dry else (python or "<python3.12>")
    venv = venv_python(root)
    if dry or not venv.is_file():
        run([py312, "-m", "venv", root / "venv_rocm"], dry=dry)
    constraints = root / "constraints.txt"
    if not dry:
        constraints.write_text("\n".join(TORCH_PINS) + "\n", encoding="ascii")
    run([venv, "-m", "pip", "install", "--no-input", "--find-links", ROCM_LINKS, *TORCH_PINS], dry=dry)
    run([venv, "-m", "pip", "install", "--no-input", "-c", constraints, "-r", repo / "requirements-rocm.txt", *EXTRA_PIP], dry=dry)
    if not dry:
        shutil.copyfile(HERE / "music_studio_launcher.py", root / "bossman_acestep_launcher.py")
    else:
        print("+ copy", HERE / "music_studio_launcher.py", "->", root / "bossman_acestep_launcher.py")
    if not skip_models:
        env = {**os.environ, "PYTHONPATH": str(repo), "PYTHONUTF8": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
        run([venv, "-m", "acestep.model_downloader"], dry=dry, cwd=repo, env=env)
    print("done." if not dry else "dry-run: nothing was changed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["check", "install"])
    parser.add_argument("--dir", type=Path, default=None)
    parser.add_argument("--python", default=None, help="path to a Python 3.12 interpreter")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-models", action="store_true", help="do not download the ~9.4 GB main model")
    args = parser.parse_args()
    root = (args.dir or default_dir()).resolve()
    if args.command == "check":
        info = report(root)
        for key, value in info.items():
            print(f"{key}: {value}")
        return 0 if info["complete"] else 1
    install(root, args.python, args.dry_run, args.skip_models)
    return 0


if __name__ == "__main__":
    sys.exit(main())
