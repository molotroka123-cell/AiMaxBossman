"""RunPod GPU preflight: honest environment inventory before any Bossman
acceptance/benchmark campaign runs.

Read-only. Does NOT download a model, does NOT start any Bossman process,
does NOT touch the network beyond local probes (Postgres TCP connect,
localhost port checks). Every field is either a real measurement or an
explicit honest "unavailable"/"unknown" — never a guess.

Usage:
    python tools/runpod_preflight.py [--json]

Exit code: 0 if RUNPOD_READY=YES, 1 otherwise (so it can gate a shell script).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr or "").strip()
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:  # noqa: BLE001 — preflight must never crash
        return 1, str(exc)


def _bytes_to_human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def check_os() -> dict:
    return {"system": platform.system(), "release": platform.release(),
            "distro": _run(["cat", "/etc/os-release"])[1].splitlines()[0]
            if Path("/etc/os-release").exists() else "unknown"}


def check_python() -> dict:
    return {"version": platform.python_version(), "executable": sys.executable}


def check_cpu_ram() -> dict:
    out: dict = {}
    try:
        out["cpu_count"] = os.cpu_count()
    except Exception:  # noqa: BLE001
        out["cpu_count"] = None
    try:
        import psutil
        vm = psutil.virtual_memory()
        out["ram_total"] = _bytes_to_human(vm.total)
        out["ram_available"] = _bytes_to_human(vm.available)
    except ImportError:
        out["ram_total"] = "unknown (psutil not installed)"
        out["ram_available"] = "unknown"
    return out


def check_disk() -> dict:
    total, used, free = shutil.disk_usage(ROOT)
    return {"total": _bytes_to_human(total), "used": _bytes_to_human(used),
            "free": _bytes_to_human(free), "path": str(ROOT)}


def check_gpu() -> dict:
    """nvidia-smi is the only source of truth here — never inferred."""
    rc, out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,"
                    "driver_version", "--format=csv,noheader"])
    if rc != 0 or not out:
        return {"present": False, "gpu": None, "vram_total": None,
                "vram_used": None, "driver": None,
                "detail": "nvidia-smi absent or failed — no GPU visible"}
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    gpus = []
    for ln in lines:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) >= 4:
            gpus.append({"name": parts[0], "vram_total": parts[1],
                        "vram_used": parts[2], "driver": parts[3]})
    return {"present": True, "count": len(gpus), "gpus": gpus}


def check_cuda() -> dict:
    rc, out = _run(["nvcc", "--version"])
    cuda_toolkit = out if rc == 0 else None
    torch_cuda = None
    try:
        import torch  # type: ignore
        torch_cuda = {"torch_version": torch.__version__,
                      "cuda_available": torch.cuda.is_available(),
                      "cuda_version": getattr(torch.version, "cuda", None)}
    except ImportError:
        torch_cuda = "torch not installed"
    return {"nvcc": cuda_toolkit, "torch": torch_cuda}


def check_docker() -> dict:
    rc, out = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return {"available": rc == 0, "version": out if rc == 0 else None}


def check_persistent_workspace() -> dict:
    """RunPod convention: /workspace is the persistent volume across pod
    restarts. Absence is not fatal — just means the pod wasn't launched with
    a network volume, worth knowing before a long benchmark campaign."""
    p = Path("/workspace")
    if not p.exists():
        return {"exists": False, "writable": False}
    writable = os.access(p, os.W_OK)
    return {"exists": True, "writable": writable, "path": str(p)}


def check_model_runtime() -> dict:
    out: dict = {}
    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    host_part = ollama_host.replace("http://", "").replace("https://", "")
    host, _, port = host_part.partition(":")
    port = int(port or 11434)
    reachable = False
    try:
        with socket.create_connection((host or "127.0.0.1", port), timeout=1.0):
            reachable = True
    except OSError:
        reachable = False
    out["ollama"] = {"host_env": os.environ.get("OLLAMA_HOST"),
                     "effective_url": ollama_host, "reachable": reachable}
    out["ollama_binary"] = shutil.which("ollama") is not None
    return out


def check_model_cache() -> dict:
    hf_cache = Path(os.environ.get("HF_HOME") or os.environ.get("HUGGINGFACE_HUB_CACHE")
                    or Path.home() / ".cache" / "huggingface")
    ollama_models = Path(os.environ.get("OLLAMA_MODELS")
                         or Path.home() / ".ollama" / "models")
    return {
        "huggingface_cache_dir": str(hf_cache), "huggingface_cache_exists": hf_cache.exists(),
        "ollama_models_dir": str(ollama_models), "ollama_models_exists": ollama_models.exists(),
    }


def check_postgres() -> dict:
    dsn_env = os.environ.get("BOSSMAN_TEST_PG_DSN") or os.environ.get("BOSSMAN_DATABASE_URL")
    host, port = "127.0.0.1", 5432
    if dsn_env:
        try:
            after_at = dsn_env.rsplit("@", 1)[-1]
            hostport = after_at.split("/", 1)[0]
            h, _, p = hostport.partition(":")
            host = h or host
            port = int(p) if p else port
        except Exception:  # noqa: BLE001
            pass
    reachable = False
    try:
        with socket.create_connection((host, port), timeout=1.0):
            reachable = True
    except OSError:
        reachable = False
    return {"dsn_env_set": bool(dsn_env), "host": host, "port": port, "reachable": reachable}


def check_gateway_importable() -> dict:
    core = ROOT / "bossman-core"
    sys.path.insert(0, str(core))
    try:
        import bossman.gateway.config as _gw_config  # noqa: F401
        ok = True
        detail = "bossman.gateway.config imports cleanly"
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    finally:
        sys.path.pop(0)
    example = core / "config" / "gateway.example.yaml"
    return {"module_importable": ok, "detail": detail,
            "example_config_present": example.exists()}


def check_cloud_keys() -> dict:
    """Presence only — never prints values. A present key is not a violation
    by itself (agents can still be cloud_policy=never); it's just visibility
    for the operator before a 'local-only' run."""
    names = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "LITELLM_MASTER_KEY", "BOSSMAN_GATEWAY_CORE_KEY", "HIGGSFIELD_API_KEY")
    present = sorted(n for n in names if os.environ.get(n))
    return {"present": present, "count": len(present)}


def check_git_sha() -> str:
    rc, out = _run(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    return out if rc == 0 else "unknown"


def main() -> int:
    report: dict = {}
    report["git_sha"] = check_git_sha()
    report["os"] = check_os()
    report["python"] = check_python()
    report["cpu_ram"] = check_cpu_ram()
    report["disk"] = check_disk()
    report["gpu"] = check_gpu()
    report["cuda"] = check_cuda()
    report["docker"] = check_docker()
    report["persistent_workspace"] = check_persistent_workspace()
    report["model_runtime"] = check_model_runtime()
    report["model_cache"] = check_model_cache()
    report["postgres"] = check_postgres()
    report["gateway"] = check_gateway_importable()
    report["cloud_keys"] = check_cloud_keys()

    # RUNPOD_READY verdict: honest AND, not optimistic. A real GPU with VRAM
    # is the point of a GPU acceptance run — no GPU means NOT ready for that
    # specific purpose, even though the repo itself would still run.
    blockers = []
    if not report["gpu"]["present"]:
        blockers.append("no GPU visible via nvidia-smi")
    if platform.system() != "Linux":
        blockers.append(f"OS is {platform.system()}, expected Linux")
    if not report["gateway"]["module_importable"]:
        blockers.append(f"bossman.gateway.config does not import: {report['gateway']['detail']}")
    ready = not blockers
    report["runpod_ready"] = ready
    report["blockers"] = blockers

    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        g = report["gpu"]
        gpu_line = (f"{g['count']}x {g['gpus'][0]['name']} "
                   f"({g['gpus'][0]['vram_total']})" if g["present"] else "NONE")
        print(f"LOCAL_SHA={report['git_sha']}")
        print(f"OS={report['os']['system']} {report['os']['distro']}")
        print(f"GPU={gpu_line}")
        print(f"VRAM={g['gpus'][0]['vram_total'] if g['present'] else 'N/A'}")
        print(f"CUDA={'nvcc:' + (report['cuda']['nvcc'] or 'absent')}")
        print(f"PYTHON={report['python']['version']}")
        print(f"RAM={report['cpu_ram']['ram_total']}")
        print(f"DISK={report['disk']['free']} free / {report['disk']['total']} total")
        print(f"PERSISTENT_WORKSPACE={'YES' if report['persistent_workspace']['exists'] else 'NO'}")
        print(f"MODEL_RUNTIME=ollama binary={'YES' if report['model_runtime']['ollama_binary'] else 'NO'}, "
              f"reachable={'YES' if report['model_runtime']['ollama']['reachable'] else 'NO'} "
              f"({report['model_runtime']['ollama']['effective_url']})")
        print(f"MODEL_CACHE=hf={report['model_cache']['huggingface_cache_exists']}, "
              f"ollama={report['model_cache']['ollama_models_exists']}")
        print(f"POSTGRES=reachable={'YES' if report['postgres']['reachable'] else 'NO'} "
              f"({report['postgres']['host']}:{report['postgres']['port']})")
        print(f"GATEWAY={'importable' if report['gateway']['module_importable'] else 'BROKEN: ' + report['gateway']['detail']}")
        print(f"CLOUD_KEYS_PRESENT={','.join(report['cloud_keys']['present']) or 'none'}")
        print(f"RUNPOD_READY={'YES' if ready else 'NO'}")
        if blockers:
            print("BLOCKERS:")
            for b in blockers:
                print(f"  - {b}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
