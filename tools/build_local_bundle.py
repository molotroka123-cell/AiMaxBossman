#!/usr/bin/env python3
"""Build wheels, install them into a clean venv and boot the installed product.

Third-party dependencies require the configured package index. Bossman itself is
installed only from the newly built local wheels. No source import/UI override
is allowed in acceptance. Existing output directories are never deleted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = (
    ("bossman-shared", ROOT),
    ("bossman-core", ROOT / "bossman-core"),
    ("bossman-command-center", ROOT / "command-center"),
    *((p.parent.name, p.parent) for p in sorted((ROOT / "apps").glob("*/pyproject.toml"))),
)


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=600, **kwargs)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{detail[-6000:]}")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_sha() -> str:
    sha = _run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise RuntimeError("Cannot bind this build to an exact source commit")
    return sha


def _source_dirty() -> bool:
    return bool(_run(["git", "-C", str(ROOT), "status", "--porcelain",
                      "--untracked-files=normal"]).stdout.strip())


def prepare_output(path: Path) -> Path:
    # A misspelled --out must never remove a checkout or owner's data. Requiring
    # a new/empty destination is simpler and stronger than guessing ownership.
    if path.is_symlink():
        raise ValueError("Output must not be a symlink")
    out = path.resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"Output is not an empty directory; choose a new --out: {out}")
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_wheels(wheels: Path) -> list[Path]:
    wheels.mkdir(parents=True, exist_ok=True)
    for name, project in PROJECTS:
        print(f"  building {name}", flush=True)
        _run([sys.executable, "-m", "pip", "wheel", "--no-deps",
              "--wheel-dir", str(wheels), str(project)], cwd=ROOT)
    built = sorted(wheels.glob("*.whl"))
    if len(built) != len(PROJECTS):
        raise RuntimeError(f"Expected {len(PROJECTS)} wheels, produced {len(built)}")
    return built


def verify(wheels: Path, verifier: Path) -> dict:
    # The cwd and the harness copy are outside the repository. PYTHONPATH and
    # UI overrides must not accidentally make an incomplete wheel look good.
    with tempfile.TemporaryDirectory(prefix="bossman-clean-install-") as temporary:
        work = Path(temporary)
        env_dir = work / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = env_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python"
        env = dict(os.environ)
        for key in ("PYTHONPATH", "BCC_UI_DIR", "BCC_DATA_DIR", "DATABASE_URL"):
            env.pop(key, None)
        _run([str(python), "-m", "pip", "install", *map(str, sorted(wheels.glob("*.whl")))],
             cwd=work, env=env)
        _run([str(python), "-m", "pip", "check"], cwd=work, env=env)
        installed_script = work / "verify_installed_product.py"
        shutil.copyfile(verifier, installed_script)
        result_path = work / "acceptance.json"
        _run([str(python), str(installed_script), "--workdir", str(work / "acceptance"),
              "--out", str(result_path)], cwd=work, env=env)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["dependency_versions"] = json.loads(_run(
            [str(python), "-m", "pip", "list", "--format=json"], cwd=work, env=env).stdout)
        return result


INSTALLER = r'''#!/usr/bin/env python3
"""Install the exact verified local wheels, then check the installed server."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--start", action="store_true", help="launch BCC after installation/acceptance")
args = parser.parse_args()
manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
for item in manifest["files"]:
    path = (root / item["path"]).resolve()
    if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
        raise SystemExit(f"Artifact checksum/path verification failed: {item['path']}")
env_dir = root / ".venv"
venv.EnvBuilder(with_pip=True).create(env_dir)
python = env_dir / ("Scripts" if os.name == "nt" else "bin") / "python"
subprocess.run([str(python), "-m", "pip", "install", *map(str, sorted((root / "wheels").glob("*.whl")))], check=True)
subprocess.run([str(python), "-m", "pip", "check"], check=True)
subprocess.run([str(python), str(root / "verify_installed_product.py"), "--out", str(root / "installed-acceptance.json")], check=True)
print("Installed source SHA:", manifest["source_sha"])
print("Start:", python, "-m bcc")
print("Open: http://127.0.0.1:8800")
if args.start:
    raise SystemExit(subprocess.call([str(python), "-m", "bcc"]))
'''

README = """# Bossman local candidate

Source SHA: `{sha}`. Built: {when}. Installed acceptance: **{status}**.

Requires Python 3.11+ and internet access once to install third-party dependencies.
No repository checkout is needed. Unzip the whole directory, then run:

    python install.py --start

On Windows, `py install.py --start` also selects the installed Python.
Open <http://127.0.0.1:8800>. The server prints the location of the access-token
file; enter that token in the login screen. The token itself is not printed to
logs. Data stays in the user's platform data directory across upgrades:
Linux `$XDG_DATA_HOME/bossman/command-center` (default `~/.local/share/...`),
Windows `%LOCALAPPDATA%/Bossman/CommandCenter`, macOS
`~/Library/Application Support/Bossman/CommandCenter`.
`BCC_DATA_DIR` overrides this location. `BCC_UI_DIR` is optional; the wheel
contains its own UI.

Subsequent starts (no reinstallation):

    .venv/bin/python -m bcc

Windows:

    .venv\\Scripts\\python.exe -m bcc

Stop with Ctrl+C. The standalone `bossman serve` service additionally needs its
configured PostgreSQL/Redis services; the Command Center starts its own SQLite
DB, queue worker and scheduler.

The build checks actual HTTP responses, all static assets, login/session/CSRF,
DB/background loops, process restart and persistence, using only installed
wheels. `MANIFEST.json` contains exact source identity, measured acceptance,
dependency versions and file hashes. This is not evidence of a model-backed
agent run. Configure a local/provider model in the UI for live execution.
Browser actions need a Playwright browser (`.venv/bin/python -m playwright install chromium`;
use `.venv\\Scripts\\python.exe` on Windows). Video import/export requires FFmpeg
and ffprobe on PATH. Optional apps' paid accounts/models are not included.

`verify_installed_product.py` reruns deterministic installed boot acceptance.
Owner hardware, credentials and Windows results are never inferred from Linux.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "bossman-local")
    parser.add_argument("--skip-verify", action="store_true", help="diagnostic artifact; NOT verified")
    parser.add_argument("--allow-dirty", action="store_true", help="diagnostic artifact; NOT exact-SHA evidence")
    args = parser.parse_args()
    sha, dirty = _source_sha(), _source_dirty()
    if dirty and not args.allow_dirty:
        raise SystemExit("Source tree is dirty. Commit changes before producing exact-SHA evidence.")
    out = prepare_output(args.out)
    print(f"source {sha} -> {out}", flush=True)
    wheels = out / "wheels"
    build_wheels(wheels)
    verifier = out / "verify_installed_product.py"
    shutil.copyfile(ROOT / "tools" / "verify_installed_product.py", verifier)
    (out / "install.py").write_text(INSTALLER, encoding="utf-8")
    checks = {"status": "NOT_RUN"}
    if not args.skip_verify:
        print("  installing and booting in a clean environment", flush=True)
        checks = verify(wheels, verifier)
    when = datetime.now(timezone.utc).isoformat()
    (out / "README.md").write_text(README.format(sha=sha, when=when, status=checks["status"]), encoding="utf-8")
    # Detect code changing while the build was running; never stamp a moving
    # workspace with an old commit and call it final acceptance.
    changed = _source_sha() != sha or _source_dirty()
    if changed and not args.allow_dirty:
        raise RuntimeError("Source changed during build; artifact is not exact-SHA evidence")
    files = [{"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size,
              "sha256": _sha256(p)} for p in sorted(out.rglob("*")) if p.is_file()]
    manifest = {"source_sha": sha, "source_dirty": dirty or changed,
                "built_at": when, "checks": checks, "files": files}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(files)} artifacts; installed acceptance: {checks['status']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
