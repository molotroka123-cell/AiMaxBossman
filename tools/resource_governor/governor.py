#!/usr/bin/env python3
"""Bossman resource governor: an OUT-OF-PROCESS helper that keeps the PC quiet and fast while the owner's phone
generations (photo/video, direct-gen) run. It never restarts or touches the running backend.

* WARM-UP  (idle only): reads the weight files of the models the owner really uses (ranked by the direct-gen job journal)
  into the OS file cache (RAM) with low thread priority and a Very-Low file I/O priority hint. Nothing is loaded into
  VRAM; sd-cli / ollama are never started. Stops within one chunk when a job appears.
* GENERATION GUARD (while any job is queued/loading/generating/postprocessing): unloads every Ollama model and keeps them
  unloaded, handles Jeff according to ``jeff_during_generation`` in governor.json, lowers the priority of identifiable
  non-essential Bossman python processes, and restores everything when the last job ends.

Polls ``GET /api/direct-gen/jobs`` (cheap) every ``poll_s``; never ``/health/live``. Stdlib + psutil + httpx, Windows.
Decision logic is pure and injected with an ``IO`` object so it is unit-tested with fakes (test_governor.py).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import logging.handlers
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LIVE = ("queued", "loading", "generating", "postprocessing")
STATE_NAME = "governor-state.json"
STATUS_NAME = "governor-status.json"
CONFIG_NAME = "governor.json"
LOG_NAME = "governor.log"
PIT_FLAG = "generation-cloud.json"
PIT_FLAG_SCHEMA = "bossman.pit.generation-cloud/1"
PIT_VIOLATION = "generation-cloud-violation.json"
JEFF_MODES = ("pause", "cloud_free", "cloud", "cpu_small")

DEFAULT_CONFIG: dict[str, Any] = {
    "dry_run": False,
    "poll_s": 10,
    "unknown_leave_s": 180,
    # pause | cloud_free (default, owner 18.10: free cloud models, $0, chats leave the PC) | cloud (paid, STUB) | cpu_small (design only)
    "jeff_during_generation": "cloud_free",
    # Tried in order; the first route that verifies is used for the whole generation.
    #  bcc_provider: a Bossman provider row (bcc.db) whose listed model ALIASES are priced 0/0 and online there (Experiential
    #    Labs, provider 13: free on the owner's plan; the gateway lists list prices, so the real guard is usage.cost > 0
    #    on every reply, enforced inside Jeff). The paid gemini-4-argon (id 99) is not in the alias list and is priced > 0.
    #  openrouter: ids that the LIVE OpenRouter catalog lists at price exactly 0/0 and that end with :free.
    "cloud_free_routes": [
        {"kind": "bcc_provider", "provider_id": 13,
         "aliases": ["explabs:deepseek-v4.1-flash-1017", "explabs:deepseek-v4.1-flash-alt"]},
        {"kind": "openrouter", "models": [
            "venice/uncensored:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "google/gemma-4-31b-it:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "google/gemma-4-26b-a4b-it:free",
        ]},
    ],
    "cloud_ban_s": 3600,
    # local Jeff comes back only after this many idle seconds (never during/right after a generation)
    "resume_delay_s": 120,
    # owner 18.10 wants Jeff back: a hold that existed before the first generation is NOT re-applied afterwards
    # (false); set true to put the old hold back (Jeff stays stopped after the generation).
    "restore_owner_hold": False,
    "catalog_url": "https://openrouter.ai/api/v1/models",
    "ollama_unload": True,
    "deprioritize": True,
    "warm": {
        "enabled": True, "idle_s": 60, "max_models": 2, "max_bytes": 40_000_000_000, "min_free_gb": 30,
        "rewarm_after_s": 1800, "chunk_mb": 8, "lookback_days": 14, "io_priority_hint": 0, "index_refresh_s": 1800,
        # Always-hot models (owner 18.10): their weights are kept in the file cache. Warmth is MEASURED, not assumed:
        # a small timed read (3 samples) every hot_probe_s while idle and right after a generation; below hot_min_mb_s
        # the file is re-read (low priority). Nothing is ever loaded into VRAM.
        "hot_models": ["qwen-image-2.1-uc-edit"], "hot_probe_s": 120, "hot_probe_mb": 16, "hot_min_mb_s": 1000,
    },
    "paths": {
        "backend_url": "http://127.0.0.1:8801",
        "ollama_url": "http://127.0.0.1:11434",
        "media_root": "~/Bossman/models/media",
        "launcher_script": "~/Bossman/one-bossman/owner_one_bossman.py",
        "launcher_python": "~/AppData/Local/Programs/Python/Python312/python.exe",
        "launcher_home": "~/Bossman/app/BOSSMAN-Windows-x64-94e40cd17822",
        "launcher_src": "~/Bossman/wt-checkpoint-20261015/command-center",
        "backend_port": 8801,
    },
}

# processes the deprioritizer must never touch (substring, case-insensitive, matched on the command line)
PROTECTED_CMD = ("-m bcc --host", "-m bcc ", "bcc.pit", "bcc.telegram_companion", "bossmobile", "warterminal",
                 "owner_one_bossman", "resource_governor", "search_proxy", "keep_awake", "sd-cli", "ollama",
                 "llama-server", "codex", "chatgpt", "claude", "ffmpeg")
CANDIDATE_NAMES = ("python.exe", "pythonw.exe")

log = logging.getLogger("governor")


# ---------------------------------------------------------------------------------------------------------------
# configuration / paths
# ---------------------------------------------------------------------------------------------------------------
def data_dir() -> Path:
    return Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")


def state_dir() -> Path:
    return data_dir() / "one-bossman"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load_config(path: Path | None = None) -> dict:
    path = path or state_dir() / CONFIG_NAME
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(user, dict):
            user = {}
    except (OSError, ValueError):
        user = {}
    cfg = _merge(DEFAULT_CONFIG, user)
    if cfg.get("jeff_during_generation") not in JEFF_MODES:
        log.warning("unknown jeff_during_generation %r -> pause", cfg.get("jeff_during_generation"))
        cfg["jeff_during_generation"] = "pause"
    return cfg


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


# ---------------------------------------------------------------------------------------------------------------
# pure decision helpers
# ---------------------------------------------------------------------------------------------------------------
def parse_ts(value: Any) -> float | None:
    try:
        d = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def live_jobs(jobs: list[dict]) -> list[dict]:
    return [j for j in jobs if isinstance(j, dict) and j.get("status") in LIVE]


def newest_activity(jobs: list[dict]) -> float:
    """Latest moment any job was created/started/finished (0 when none)."""
    best = 0.0
    for j in jobs:
        for key in ("finished_at", "started_at", "created_at"):
            t = parse_ts(j.get(key))
            if t:
                best = max(best, t)
    return best


def warm_allowed(jobs: list[dict] | None, now: float, last_active: float, idle_s: float) -> tuple[bool, str]:
    """Warm-up only with a KNOWN job list, no live job, and >= idle_s since the last job activity."""
    if jobs is None:
        return False, "jobs unknown"
    if live_jobs(jobs):
        return False, "job live"
    quiet_since = max(last_active, newest_activity(jobs))
    if now - quiet_since < idle_s:
        return False, f"idle {now - quiet_since:.0f}s < {idle_s:.0f}s"
    return True, "idle"


def rank_models(jobs: list[dict], now: float, lookback_days: float = 14.0) -> list[tuple[str, float]]:
    """Models by recency-weighted usage (half-life 3 days). Cancelled jobs and jobs without weights do not count."""
    score: dict[str, float] = {}
    for j in jobs:
        prov = j.get("provenance") or {}
        model = j.get("model") or prov.get("model_id")
        if not model or j.get("status") == "cancelled" or not prov.get("weights"):
            continue
        t = parse_ts(j.get("created_at")) or 0.0
        age_days = max(0.0, (now - t) / 86400.0)
        if age_days > lookback_days:
            continue
        score[model] = score.get(model, 0.0) + 0.5 ** (age_days / 3.0)
    return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))


def plan_files(jobs: list[dict], ranked: list[tuple[str, float]], index: dict[str, list[tuple[str, int]]],
               max_models: int) -> list[dict]:
    """[{model, files:[{path,size}]}] for the top models; a file shared by two models is warmed once."""
    seen: set[str] = set()
    plan = []
    for model, _score in ranked[:max(0, max_models)]:
        latest = max((j for j in jobs if j.get("model") == model and (j.get("provenance") or {}).get("weights")),
                     key=lambda j: parse_ts(j.get("created_at")) or 0.0, default=None)
        files = []
        for w in (latest or {}).get("provenance", {}).get("weights", []):
            if not w.get("found") or not w.get("file"):
                continue
            cands = [c for c in index.get(str(w["file"]).lower(), []) if not w.get("size_bytes") or c[1] == w["size_bytes"]]
            if not cands:
                continue
            path, size = cands[0]
            if path in seen:
                continue
            seen.add(path)
            files.append({"path": path, "size": size})
        if files:
            plan.append({"model": model, "files": files})
    return plan


def budget_ok(available: int, size: int, min_free: int, warmed: int, max_bytes: int) -> tuple[bool, str]:
    """Warm a file only if RAM stays >= min_free after it AND the total warmed bytes stay under the cap."""
    if warmed + size > max_bytes:
        return False, f"cap {max_bytes / 1e9:.0f} GB reached"
    if available - size < min_free:
        return False, f"free RAM {available / 1e9:.1f} GB - {size / 1e9:.1f} GB < {min_free / 1e9:.0f} GB"
    return True, "ok"


def is_depri_candidate(name: str, cmdline: str, cwd: str, nice_is_normal: bool) -> bool:
    """Python processes of this owner's Bossman checkouts that are not on the protected list."""
    if not nice_is_normal or name.lower() not in CANDIDATE_NAMES:
        return False
    low = cmdline.lower()
    if any(p in low for p in PROTECTED_CMD):
        return False
    probe = (low + " " + (cwd or "").lower()).replace("/", "\\")
    return "\\bossman\\" in probe


def cloud_free_pick(configured: list[str], catalog: dict[str, tuple[Any, Any]] | None, limit: int = 5) -> list[str]:
    """Configured ids that the LIVE catalog lists with price exactly 0/0 and an id ending in :free, not Liquid/LFM."""
    if not catalog:
        return []
    out = []
    for m in configured:
        pr = catalog.get(m)
        if not pr or not m.endswith(":free") or re.search(r"lfm|liquid/", m, re.I) or m in out:
            continue
        try:
            if float(pr[0]) == 0.0 and float(pr[1]) == 0.0:
                out.append(m)
        except (TypeError, ValueError):
            continue
    return out[:limit]


def pick_bcc_provider_models(rows: list[dict] | None, aliases: list[str]) -> list[str]:
    """Upstream model names for the configured aliases, only rows that bcc.db prices exactly 0/0 (known) and online."""
    out = []
    for r in rows or []:
        try:
            free = (float(r["price_in"]) == 0.0 and float(r["price_out"]) == 0.0 and int(r["pricing_known"]) == 1
                    and r.get("status") == "online")
        except (KeyError, TypeError, ValueError):
            continue
        name = str(r.get("name") or "")
        if (free and r.get("alias") in aliases and name and name not in out
                and not re.search(r"lfm|liquid/", name, re.I)):
            out.append(name)
    return out[:5]


# ---------------------------------------------------------------------------------------------------------------
# Windows system access
# ---------------------------------------------------------------------------------------------------------------
class SystemIO:
    """All side effects. Tests replace this class with a fake."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.paths = cfg["paths"]
        self.token = ""
        try:
            self.token = (data_dir() / "token").read_text(encoding="utf-8").strip()
        except OSError:
            log.warning("backend token file not readable; job polling will fail")
        import httpx
        self._http = httpx.Client(timeout=5.0)
        self._lock = threading.Lock()

    # -- time / memory --------------------------------------------------------------------------------------
    def now(self) -> float:
        return time.time()

    def mem(self) -> dict:
        import psutil
        vm = psutil.virtual_memory()
        out = {"total": vm.total, "available": vm.available, "cache": _system_cache_bytes()}
        return out

    # -- backend / ollama ------------------------------------------------------------------------------------
    def fetch_jobs(self) -> list[dict] | None:
        try:
            r = self._http.get(self.paths["backend_url"] + "/api/direct-gen/jobs", headers={"X-BCC-Token": self.token})
            r.raise_for_status()
            jobs = r.json().get("jobs")
            return jobs if isinstance(jobs, list) else None
        except Exception as exc:  # noqa: BLE001 - unknown state is a first-class result
            log.debug("jobs poll failed: %s", type(exc).__name__)
            return None

    def ollama_ps(self) -> list[str]:
        try:
            r = self._http.get(self.paths["ollama_url"] + "/api/ps")
            return [m["name"] for m in r.json().get("models", []) if m.get("name")]
        except Exception:  # noqa: BLE001
            return []

    def ollama_unload(self, name: str) -> bool:
        try:
            r = self._http.post(self.paths["ollama_url"] + "/api/generate", json={"model": name, "keep_alive": 0})
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def free_catalog(self) -> dict[str, tuple[Any, Any]] | None:
        """Live provider catalog prices (public GET, no chat text). None when unreadable."""
        try:
            r = self._http.get(self.cfg["catalog_url"], timeout=15.0)
            r.raise_for_status()
            return {m["id"]: ((m.get("pricing") or {}).get("prompt"), (m.get("pricing") or {}).get("completion"))
                    for m in r.json().get("data", []) if m.get("id")}
        except Exception:  # noqa: BLE001
            return None

    def provider_rows(self, provider_id: int) -> list[dict] | None:
        """Model rows of a Bossman provider (name/alias/price/status only; the key column is never selected)."""
        import sqlite3
        try:
            con = sqlite3.connect((data_dir() / "bcc.db").as_uri() + "?mode=ro", uri=True, timeout=3)
            try:
                con.row_factory = sqlite3.Row
                has_key = con.execute("select length(api_key_enc) > 0 from providers where id=?", (provider_id,)).fetchone()
                if not has_key or not has_key[0]:
                    return None
                return [dict(r) for r in con.execute(
                    "select name, alias, price_in, price_out, pricing_known, status from models where provider_id=?",
                    (provider_id,))]
            finally:
                con.close()
        except Exception:  # noqa: BLE001
            return None

    def violation_read(self) -> dict | None:
        try:
            return json.loads((data_dir() / "pit-v1.7" / PIT_VIOLATION).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def violation_clear(self) -> None:
        try:
            (data_dir() / "pit-v1.7" / PIT_VIOLATION).unlink()
        except FileNotFoundError:
            pass

    # -- Jeff (through the owner launcher; hold-jeff semantics) ------------------------------------------------
    @property
    def hold_path(self) -> Path:
        return state_dir() / "hold-jeff"

    def hold_read(self) -> str | None:
        try:
            return self.hold_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def jeff_running(self) -> bool:
        return _lock_held(data_dir() / "pit-v1.7" / "poller.lock")

    def _launcher(self, args: list[str]) -> tuple[int, str]:
        p = self.paths
        argv = [str(expand(p["launcher_python"])), str(expand(p["launcher_script"]))] + args
        flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
        r = subprocess.run(argv, capture_output=True, text=True, timeout=180, creationflags=flags,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "")[-400:] + (r.stderr or "")[-200:]

    def jeff_stop(self, hold: bool) -> bool:
        args = ["stop", "--data-dir", str(data_dir()), "--kinds", "jeff"] + (["--hold"] if hold else [])
        rc, out = self._launcher(args)
        log.info("launcher stop jeff hold=%s rc=%s", hold, rc)
        return rc == 0

    def jeff_launch(self) -> bool:
        p = self.paths
        rc, out = self._launcher(["launch", "jeff", "--home", str(expand(p["launcher_home"])), "--data-dir",
                                  str(data_dir()), "--port", str(p["backend_port"]), "--src",
                                  str(expand(p["launcher_src"])), "--python", str(expand(p["launcher_python"]))])
        log.info("launcher launch jeff rc=%s", rc)
        return rc == 0

    def cloud_flag_write(self, route: dict) -> None:
        d = data_dir() / "pit-v1.7"
        d.mkdir(parents=True, exist_ok=True)
        body = {"schema": PIT_FLAG_SCHEMA, "models": list(route["models"]), "heartbeat_at": time.time(),
                "written_by": "resource_governor"}
        if route.get("provider_id") is not None:
            body["provider"] = {"bcc_provider_id": int(route["provider_id"])}
        _atomic_json(d / PIT_FLAG, body)

    def cloud_flag_remove(self) -> None:
        try:
            (data_dir() / "pit-v1.7" / PIT_FLAG).unlink()
        except FileNotFoundError:
            pass

    # -- processes -------------------------------------------------------------------------------------------
    def list_depri_candidates(self) -> list[dict]:
        import psutil
        out = []
        me = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "nice"]):
            try:
                if p.info["pid"] == me:
                    continue
                cmd = " ".join(p.info.get("cmdline") or [])
                normal = p.info.get("nice") == psutil.NORMAL_PRIORITY_CLASS
                if not (p.info.get("name") or "").lower() in CANDIDATE_NAMES:
                    continue
                cwd = ""
                try:
                    cwd = p.cwd()
                except (psutil.Error, OSError):
                    pass
                if is_depri_candidate(p.info["name"], cmd, cwd, normal):
                    out.append({"pid": p.info["pid"], "ct": p.info["create_time"], "name": p.info["name"],
                                "cmd": cmd[:100], "nice": p.info["nice"]})
            except (psutil.Error, OSError):
                continue
        return out

    def boost_sdcli(self) -> list[int]:
        """Generation engine (sd-cli) to HIGH priority; returns pids changed. It dies with the job: nothing to restore."""
        import psutil
        done = []
        for p in psutil.process_iter(["pid", "name", "nice"]):
            try:
                if (p.info["name"] or "").lower() == "sd-cli.exe" and p.info["nice"] != psutil.HIGH_PRIORITY_CLASS:
                    p.nice(psutil.HIGH_PRIORITY_CLASS)
                    done.append(p.info["pid"])
            except (psutil.Error, OSError):
                continue
        return done

    def set_priority(self, pid: int, ct: float, nice: int | None, ionice: int | None) -> bool:
        import psutil
        try:
            p = psutil.Process(pid)
            if abs(p.create_time() - ct) > 1.0:
                return False                                      # pid was reused
            if nice is not None:
                p.nice(nice)
            if ionice is not None:
                p.ionice(ionice)
            return True
        except (psutil.Error, OSError, ValueError):
            return False

    def get_ionice(self, pid: int) -> int | None:
        import psutil
        try:
            return int(psutil.Process(pid).ionice())
        except (psutil.Error, OSError, ValueError):
            return None

    # -- warm-up ---------------------------------------------------------------------------------------------
    def build_index(self) -> dict[str, list[tuple[str, int]]]:
        idx: dict[str, list[tuple[str, int]]] = {}
        root = expand(self.paths["media_root"])
        for dp, _dirs, files in os.walk(root):
            for f in files:
                fp = os.path.join(dp, f)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                if size >= 1_000_000:
                    idx.setdefault(f.lower(), []).append((fp, size))
        return idx

    def probe_file(self, path: str, mb: int) -> float:
        """Timed read of 3 samples (start / middle / end) with NORMAL priority; returns MB/s. Cold NVMe reads that
        crawl show up as a low number; a cached file reads at memory speed."""
        size = os.path.getsize(path)
        n = max(1, mb) * 1024 * 1024
        total, t0 = 0, time.perf_counter()
        with open(path, "rb", buffering=0) as f:
            for off in (0, max(0, size // 2 - n // 2), max(0, size - n)):
                f.seek(off)
                total += len(f.read(n))
        return total / max(time.perf_counter() - t0, 1e-9) / 1e6

    def warm_file(self, path: str, allow: Callable[[], bool], chunk_mb: int, hint: int) -> dict:
        return read_file_low_priority(path, allow, chunk_mb, hint)


def _atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _lock_held(path: Path) -> bool:
    """True when another process holds the kernel byte lock at offset 0 (same test the owner launcher uses)."""
    if not path.exists():
        return False
    try:
        f = open(path, "a+b")
    except OSError:
        return True
    try:
        import msvcrt
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return True
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    finally:
        f.close()


class _PERF(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t),
                ("CommitPeak", ctypes.c_size_t), ("PhysicalTotal", ctypes.c_size_t),
                ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t),
                ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t),
                ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t),
                ("HandleCount", ctypes.c_ulong), ("ProcessCount", ctypes.c_ulong), ("ThreadCount", ctypes.c_ulong)]


def _system_cache_bytes() -> int | None:
    """Windows 'system cache' (file cache working set + standby list) in bytes."""
    if os.name != "nt":
        return None
    try:
        pi = _PERF()
        pi.cb = ctypes.sizeof(pi)
        if not ctypes.WinDLL("psapi").GetPerformanceInfo(ctypes.byref(pi), pi.cb):
            return None
        return int(pi.SystemCache * pi.PageSize)
    except Exception:  # noqa: BLE001
        return None


def read_file_low_priority(path: str, allow: Callable[[], bool], chunk_mb: int = 8, hint: int = 0) -> dict:
    """Sequentially read ``path`` to populate the OS file cache. Thread priority lowest, file I/O priority hint
    (0 VeryLow / 1 Low), FILE_FLAG_SEQUENTIAL_SCAN. Memory priority is left at the default on purpose: the Windows
    'background mode' would also drop it to 1 and those cache pages would be the first to be repurposed.
    ``allow()`` is asked between chunks; False aborts at once. Nothing is parsed or sent anywhere."""
    t0 = time.perf_counter()
    done = 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_SEQUENTIAL", 0))
    aborted = False
    old_prio = None
    try:
        if os.name == "nt":
            import msvcrt
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentThread.restype = ctypes.c_void_p
            k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            k32.GetThreadPriority.argtypes = [ctypes.c_void_p]
            k32.SetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
            th = k32.GetCurrentThread()
            old_prio = k32.GetThreadPriority(th)
            k32.SetThreadPriority(th, -15)                                  # THREAD_PRIORITY_IDLE
            val = ctypes.c_ulong(int(hint))
            k32.SetFileInformationByHandle(msvcrt.get_osfhandle(fd), 12, ctypes.byref(val), 4)  # FileIoPriorityHintInfo
        buf = bytearray(max(1, chunk_mb) * 1024 * 1024)
        view = memoryview(buf)
        with os.fdopen(fd, "rb", buffering=0) as f:
            fd = -1
            while True:
                if not allow():
                    aborted = True
                    break
                n = f.readinto(view)
                if not n:
                    break
                done += n
    finally:
        if fd != -1:
            os.close(fd)
        if old_prio is not None:
            k32.SetThreadPriority(k32.GetCurrentThread(), old_prio)
    secs = max(time.perf_counter() - t0, 1e-9)
    return {"bytes": done, "secs": secs, "mb_s": done / secs / 1e6, "aborted": aborted}


# ---------------------------------------------------------------------------------------------------------------
# Jeff controller
# ---------------------------------------------------------------------------------------------------------------
class JeffController:
    """Handles Jeff for the duration of a generation. State is persisted so a crash never leaves Jeff in the wrong
    mode (recover() at the next start). It only ever undoes what it did itself."""

    def __init__(self, io: Any, cfg: dict, state_path: Path | None, dry: bool):
        self.io, self.cfg, self.state_path, self.dry = io, cfg, state_path, dry
        self.state: dict | None = None
        self._catalog_at = 0.0
        self._route: dict | None = None
        self.banned_until = 0.0
        self.pending: dict | None = None          # {"due": ts, "hold_written": str|None} local Jeff relaunch after idle
        if state_path and not dry:
            try:
                data = json.loads(Path(state_path).read_text(encoding="utf-8"))
                self.state, self.pending = data.get("jeff"), data.get("pending")
            except (OSError, ValueError):
                self.state = None

    @property
    def active(self) -> bool:
        return bool(self.state)

    @property
    def mode(self) -> str | None:
        return (self.state or {}).get("mode")

    def _save(self) -> None:
        if self.state_path and not self.dry:
            _atomic_json(Path(self.state_path), {"jeff": self.state, "pending": self.pending})

    def _do(self, what: str, fn: Callable[[], Any]) -> Any:
        if self.dry:
            log.info("DRYRUN would: %s", what)
            return True
        return fn()

    # -- enter -----------------------------------------------------------------------------------------------
    def enter(self) -> str:
        want = self.cfg["jeff_during_generation"]
        if self.active:
            if self.mode == "cloud_free":
                self._maybe_switch_route()
            return self.mode or want
        if want == "cloud_free":
            route = self._verified_free_route()
            if route:
                self._enter_cloud_free(route)
                return "cloud_free"
            log.warning("cloud_free requested but no verified free route (or banned after a cost violation): "
                        "falling back to pause")
        elif want in ("cloud", "cpu_small"):
            log.warning("jeff mode %s is a STUB (not enabled): falling back to pause", want)
        self._enter_pause()
        return "pause"

    def _verified_free_route(self) -> dict | None:
        now = self.io.now()
        if now < self.banned_until:
            return None
        if self._route and now - self._catalog_at < 600:
            return self._route
        route = None
        for r in self.cfg["cloud_free_routes"]:
            if r.get("kind") == "bcc_provider":
                names = pick_bcc_provider_models(self.io.provider_rows(int(r["provider_id"])), list(r.get("aliases") or []))
                if names:
                    route = {"kind": "bcc_provider", "provider_id": int(r["provider_id"]), "models": names}
            elif r.get("kind") == "openrouter":
                names = cloud_free_pick(list(r.get("models") or []), self.io.free_catalog())
                if names:
                    route = {"kind": "openrouter", "provider_id": None, "models": names}
            if route:
                break
        self._route, self._catalog_at = route, now
        return route

    def _take_pending(self) -> bool:
        """A local Jeff that was waiting for its idle-delay relaunch counts as 'was running' for the new generation."""
        pend, self.pending = self.pending, None
        return bool(pend)

    def _maybe_switch_route(self) -> None:
        """Adopted cloud_free state on another route than the best one now: restart Jeff on the better route."""
        st = self.state
        best = self._verified_free_route()
        if not best or self.dry:
            return
        if (best.get("provider_id"), list(best["models"])) == (st.get("provider_id"), list(st.get("models") or [])):
            return
        log.info("JEFF route switch %s -> %s", st.get("route") or "openrouter", best["kind"])
        self.io.cloud_flag_write(best)
        self.io.jeff_stop(False)
        self.io.jeff_launch()
        st.update(models=best["models"], provider_id=best.get("provider_id"), route=best["kind"])
        self._save()

    def tick_pending(self, now: float) -> None:
        pend = self.pending
        if not pend or now < pend["due"] or self.dry:
            return
        self.pending = None
        if pend.get("hold_written") is not None and self.io.hold_read() != pend["hold_written"]:
            log.warning("Jeff NOT resumed: the hold changed after we wrote it (owner decision wins)")
        elif not self.io.jeff_running():
            log.info("JEFF local resume after idle delay")
            self.io.jeff_launch()
        self._save()

    def _enter_pause(self) -> None:
        held = self.io.hold_read()
        running = self.io.jeff_running() or self._take_pending()
        st = {"mode": "pause", "since": _iso(self.io.now()), "owner_hold_before": held is not None,
              "was_running_before": running, "stopped_by_us": False, "hold_written": None}
        if running:
            if self._do("stop Jeff with hold (launcher stop --kinds jeff --hold)", lambda: self.io.jeff_stop(True)):
                st["stopped_by_us"] = True
                st["hold_written"] = None if self.dry else self.io.hold_read()
        else:
            log.info("Jeff pause: Jeff is not running (%s)", "owner hold present" if held is not None else "no hold")
        self.state = st
        self._save()
        log.info("JEFF pause entered: %s", {k: v for k, v in st.items() if k != "hold_written"})

    def _enter_cloud_free(self, route: dict) -> None:
        models = route["models"]
        held = self.io.hold_read()
        running = self.io.jeff_running() or self._take_pending()
        st = {"mode": "cloud_free", "since": _iso(self.io.now()), "owner_hold_before": held is not None,
              "was_running_before": running, "models": models,
              "provider_id": route.get("provider_id"), "route": route.get("kind")}
        self.state = st
        self._save()                                       # persisted BEFORE any side effect: crash-safe
        self._do("write generation-cloud flag %s %s" % (route.get("kind"), ",".join(models)),
                 lambda: self.io.cloud_flag_write(route))
        if running:
            self._do("stop local Jeff (restart to load the cloud route)", lambda: self.io.jeff_stop(False))
        self._do("launch Jeff (explicit launch clears hold-jeff)", lambda: self.io.jeff_launch())
        log.info("JEFF cloud_free entered: route=%s models=%s owner_hold_before=%s was_running_before=%s",
                 route.get("kind"), models, held is not None, running)

    def heartbeat(self) -> None:
        self.check_violation()
        if self.mode == "cloud_free" and not self.dry:
            self.io.cloud_flag_write({"models": self.state.get("models") or [], "provider_id": self.state.get("provider_id")})

    def check_violation(self) -> None:
        """A Jeff reply reported usage.cost > 0: cloud_free is closed for cloud_ban_s and Jeff falls back to pause."""
        if self.mode != "cloud_free":
            return
        v = self.io.violation_read()
        if v is None:
            return
        log.warning("COST VIOLATION: a cloud reply reported cost > 0 (model %s): leaving cloud_free for pause",
                    str(v.get("model"))[:60])
        st = self.state
        self.banned_until = self.io.now() + float(self.cfg["cloud_ban_s"])
        self._do("remove generation-cloud flag", lambda: self.io.cloud_flag_remove())
        self._do("stop cloud Jeff, hold", lambda: self.io.jeff_stop(True))
        if not self.dry:
            self.io.violation_clear()
        self.state = {"mode": "pause", "since": _iso(self.io.now()), "owner_hold_before": st.get("owner_hold_before"),
                      "was_running_before": st.get("was_running_before"),
                      "stopped_by_us": bool(st.get("was_running_before")),
                      "hold_written": None if self.dry else self.io.hold_read(), "after_violation": True}
        self._save()

    # -- leave -----------------------------------------------------------------------------------------------
    def leave(self, reason: str) -> None:
        st = self.state
        if not st:
            return
        mode = st.get("mode")
        if mode == "cloud_free":
            self._do("remove generation-cloud flag", lambda: self.io.cloud_flag_remove())   # FIRST: any restart is local
            restore_hold = bool(st.get("owner_hold_before")) and bool(self.cfg["restore_owner_hold"])
            self._do("stop cloud Jeff", lambda: self.io.jeff_stop(restore_hold))
            if restore_hold:
                log.info("Jeff restored to the owner's hold (stopped, held)")
            elif st.get("was_running_before") or st.get("owner_hold_before"):
                self._defer_resume(None)             # owner 18.10: Jeff must work; the old hold is not re-applied
        elif mode == "pause":
            if st.get("stopped_by_us"):
                current = self.io.hold_read()
                if self.dry or (current is not None and current == st.get("hold_written")):
                    self._defer_resume(st.get("hold_written"))
                else:
                    log.warning("Jeff NOT resumed: the hold changed after we wrote it (owner decision wins)")
        log.info("JEFF %s left (%s)", mode, reason)
        self.state = None
        self._save()

    def _defer_resume(self, hold_written: str | None) -> None:
        delay = float(self.cfg["resume_delay_s"])
        if self.dry:
            log.info("DRYRUN would: relaunch local Jeff after %ss idle", delay)
        elif delay <= 0:
            self.io.jeff_launch()
        else:
            self.pending = {"due": self.io.now() + delay, "hold_written": hold_written}
            log.info("local Jeff relaunch deferred %ss (idle delay)", delay)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------------------------
# governor
# ---------------------------------------------------------------------------------------------------------------
class Governor:
    def __init__(self, io: Any, cfg: dict, state_path: Path | None = None, status_path: Path | None = None):
        self.io, self.cfg = io, cfg
        self.dry = bool(cfg["dry_run"])
        self.phase = "IDLE"
        self.last_active = 0.0
        self.unknown_since: float | None = None
        self.unloads = 0
        self.jeff = JeffController(io, cfg, state_path, self.dry)
        self.depri: dict[tuple[int, float], tuple[int, int | None]] = {}
        self.depri_tick = 0
        self.status_path = status_path
        self.warm_thread: threading.Thread | None = None
        self.warm_stop = threading.Event()
        self.warm_log: list[dict] = []
        self.warmed: dict[str, float] = {}                 # path -> time last warmed
        self.warmed_bytes = 0
        self.hot_probed: dict[str, float] = {}
        self.hot_state: dict[str, float] = {}      # file -> last measured MB/s
        self.index: dict[str, list[tuple[str, int]]] = {}
        self.index_at = 0.0
        self.last_decision = ""
        self._fresh_jobs_at = 0.0
        self._fresh_live = False
        self._lock = threading.Lock()

    # -- one polling step ------------------------------------------------------------------------------------
    def tick(self) -> None:
        now = self.io.now()
        jobs = self.io.fetch_jobs()
        if jobs is None:
            self._unknown(now)
            return
        self.unknown_since = None
        live = live_jobs(jobs)
        if live:
            self.last_active = now
            self._stop_warm("job live")
            if self.phase != "ACTIVE":
                self._enter_active(live)
            else:
                self._guard(live)
        else:
            if self.phase == "ACTIVE":
                self._leave_active("last job finished")
            elif self.jeff.active:                         # crash recovery: state file says Jeff was switched
                self.jeff.leave("recovery at start: no live job")
            self.jeff.tick_pending(now)
            self._maybe_warm(jobs, now)
        self._write_status(jobs)

    def _unknown(self, now: float) -> None:
        self._stop_warm("jobs unknown")
        if self.unknown_since is None:
            self.unknown_since = now
            log.warning("job list unavailable: no unload, no warm-up (state unknown)")
        if self.phase == "ACTIVE":
            self.jeff.heartbeat()
            if now - self.unknown_since >= self.cfg["unknown_leave_s"]:
                self._leave_active("backend unreachable for %ds" % self.cfg["unknown_leave_s"])

    def _enter_active(self, live: list[dict]) -> None:
        self.phase = "ACTIVE"
        kinds = sorted({f"{j.get('kind')}:{j.get('status')}" for j in live})
        self.last_decision = f"ACTIVE {len(live)} job(s) {kinds}"
        log.info("ENTER ACTIVE: %d live job(s) %s dry_run=%s", len(live), kinds, self.dry)
        self._unload_ollama()
        mode = self.jeff.enter()
        log.info("jeff handling: %s", mode)
        self._apply_depri()
        self._boost()

    def _boost(self) -> None:
        if self.dry or not self.cfg["deprioritize"]:
            return
        for pid in self.io.boost_sdcli():
            log.info("sd-cli pid %s -> HIGH priority", pid)

    def _guard(self, live: list[dict]) -> None:
        self._unload_ollama()
        self._boost()
        self.jeff.heartbeat()
        self.depri_tick += 1
        if self.depri_tick % 6 == 0:
            self._apply_depri()

    def _leave_active(self, reason: str) -> None:
        log.info("LEAVE ACTIVE: %s", reason)
        self.jeff.leave(reason)
        self._restore_depri()
        self.phase = "IDLE"
        self.last_decision = "IDLE " + reason
        self.hot_probed.clear()                 # re-measure the hot weights right after a generation

    # -- ollama ----------------------------------------------------------------------------------------------
    def _unload_ollama(self) -> None:
        if not self.cfg["ollama_unload"]:
            return
        for name in self.io.ollama_ps():
            if self.dry:
                log.info("DRYRUN would unload ollama model %s (keep_alive=0)", name)
                continue
            ok = self.io.ollama_unload(name)
            self.unloads += 1 if ok else 0
            log.info("ollama unload %s -> %s", name, "ok" if ok else "FAILED")

    # -- priorities ------------------------------------------------------------------------------------------
    def _apply_depri(self) -> None:
        if not self.cfg["deprioritize"]:
            return
        import psutil
        for c in self.io.list_depri_candidates():
            key = (c["pid"], c["ct"])
            if key in self.depri:
                continue
            if self.dry:
                log.info("DRYRUN would lower priority of pid %s %s", c["pid"], c["cmd"])
                self.depri[key] = (c["nice"], None)
                continue
            orig_io = self.io.get_ionice(c["pid"])
            if self.io.set_priority(c["pid"], c["ct"], psutil.BELOW_NORMAL_PRIORITY_CLASS, 0):
                self.depri[key] = (c["nice"], orig_io)
                log.info("priority lowered: pid %s %s", c["pid"], c["cmd"])

    def _restore_depri(self) -> None:
        for (pid, ct), (nice, ionice) in list(self.depri.items()):
            if not self.dry:
                self.io.set_priority(pid, ct, nice, ionice)
        if self.depri:
            log.info("priorities restored for %d process(es)", len(self.depri))
        self.depri.clear()

    # -- warm-up ---------------------------------------------------------------------------------------------
    def _jobs_allow(self) -> bool:
        """Called by the reader between chunks: fresh (<=1.5 s old) answer to 'may we keep reading?'."""
        if self.warm_stop.is_set():
            return False
        now = self.io.now()
        with self._lock:
            if now - self._fresh_jobs_at < 1.5:
                return not self._fresh_live
        jobs = self.io.fetch_jobs()
        with self._lock:
            self._fresh_jobs_at = now
            self._fresh_live = jobs is None or bool(live_jobs(jobs))
            if self._fresh_live:
                self.warm_stop.set()
            return not self._fresh_live

    def _stop_warm(self, why: str) -> None:
        t = self.warm_thread
        if t and t.is_alive():
            self.warm_stop.set()
            log.info("warm-up STOP requested: %s", why)

    def _maybe_warm(self, jobs: list[dict], now: float) -> None:
        w = self.cfg["warm"]
        if not w["enabled"] or (self.warm_thread and self.warm_thread.is_alive()):
            return
        ok, why = warm_allowed(jobs, now, self.last_active, w["idle_s"])
        self.last_decision = ("warm: " if ok else "no warm: ") + why
        if not ok:
            return
        if now - self.index_at > w["index_refresh_s"]:
            self.index, self.index_at = self.io.build_index(), now
        plan = plan_files(jobs, rank_models(jobs, now, w["lookback_days"]), self.index, w["max_models"])
        todo = [(p["model"], f) for p in plan for f in p["files"]
                if now - self.warmed.get(f["path"], 0.0) >= w["rewarm_after_s"]]
        hot_todo = self._hot_todo(jobs, now)
        hot_paths = {f["path"] for _m, f in hot_todo}
        todo = hot_todo + [(m, f) for m, f in todo if f["path"] not in hot_paths]
        if not todo:
            return
        if self.dry:
            if now - self.warmed.get("__dry__", 0.0) >= w["rewarm_after_s"]:
                self.warmed["__dry__"] = now
                log.info("DRYRUN would warm: %s", [(m, Path(f["path"]).name, round(f["size"] / 1e9, 2)) for m, f in todo])
            return
        self.warm_stop.clear()
        self._fresh_jobs_at = 0.0
        self.warm_thread = threading.Thread(target=self._warm_worker, args=(todo,), name="warm", daemon=True)
        self.warm_thread.start()

    def _hot_todo(self, jobs: list[dict], now: float) -> list[tuple[str, dict]]:
        """Hot-model files whose MEASURED read speed is below the threshold (cold) -> must be re-read."""
        w = self.cfg["warm"]
        hot = list(w.get("hot_models") or [])
        if not hot:
            return []
        plan = plan_files(jobs, [(m, 1.0) for m in hot], self.index, len(hot))
        out = []
        for p in plan:
            for f in p["files"]:
                if now - self.hot_probed.get(f["path"], 0.0) < w["hot_probe_s"]:
                    continue
                self.hot_probed[f["path"]] = now
                try:
                    speed = self.io.probe_file(f["path"], w["hot_probe_mb"])
                except OSError:
                    continue
                self.hot_state[f["path"]] = speed
                if speed < w["hot_min_mb_s"]:
                    log.info("hot file COLD (%.0f MB/s < %s): %s -> re-read", speed, w["hot_min_mb_s"], Path(f["path"]).name)
                    out.append((p["model"], f))
        return out

    def _warm_worker(self, todo: list[tuple[str, dict]]) -> None:
        w = self.cfg["warm"]
        min_free = int(w["min_free_gb"] * 1e9)
        before = self.io.mem()
        run_bytes = 0
        for model, f in todo:
            if not self._jobs_allow():
                log.info("warm-up aborted before %s (job activity)", Path(f["path"]).name)
                break
            avail = self.io.mem()["available"]
            ok, why = budget_ok(avail, f["size"], min_free, self.warmed_bytes + run_bytes, w["max_bytes"])
            if not ok:
                log.info("warm-up stopped, budget: %s", why)
                break
            try:
                r = self.io.warm_file(f["path"], self._jobs_allow, w["chunk_mb"], w["io_priority_hint"])
            except OSError as exc:
                log.warning("warm-up read failed %s: %s", Path(f["path"]).name, type(exc).__name__)
                continue
            run_bytes += r["bytes"]
            if not r["aborted"]:
                self.warmed[f["path"]] = self.io.now()
                self.hot_probed.pop(f["path"], None)       # verify warmth again on the next idle tick
            entry = {"model": model, "file": Path(f["path"]).name, "gb": round(r["bytes"] / 1e9, 2),
                     "mb_s": round(r["mb_s"]), "aborted": r["aborted"]}
            self.warm_log.append(entry)
            log.info("warmed %s", entry)
            if r["aborted"]:
                break
        self.warmed_bytes += run_bytes
        after = self.io.mem()
        log.info("warm-up run done: read %.1f GB; available %.1f -> %.1f GB; cache %s -> %s GB", run_bytes / 1e9,
                 before["available"] / 1e9, after["available"] / 1e9,
                 _gb(before.get("cache")), _gb(after.get("cache")))

    # -- status / lifecycle ----------------------------------------------------------------------------------
    def _write_status(self, jobs: list[dict]) -> None:
        if not self.status_path:
            return
        m = self.io.mem()
        _atomic_json(Path(self.status_path), {
            "at": _iso(self.io.now()), "phase": self.phase, "dry_run": self.dry, "live_jobs": len(live_jobs(jobs)),
            "decision": self.last_decision, "jeff_mode": self.jeff.mode, "jeff_setting": self.cfg["jeff_during_generation"],
            "ollama_unloads": self.unloads, "depri_processes": len(self.depri), "warm_log": self.warm_log[-6:],
            "hot_mb_s": {Path(k).name: round(v) for k, v in self.hot_state.items()},
            "ram_available_gb": round(m["available"] / 1e9, 1), "cache_gb": _gb(m.get("cache"))})

    def shutdown(self) -> None:
        self.warm_stop.set()
        if self.warm_thread:
            self.warm_thread.join(timeout=5)
        if self.phase == "ACTIVE" or self.jeff.active:
            self._leave_active("governor exit")


def _gb(v: Any) -> Any:
    return None if v is None else round(v / 1e9, 1)


# ---------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------
def setup_logging(console: bool) -> None:
    root = logging.getLogger("governor")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    state_dir().mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(state_dir() / LOG_NAME, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def acquire_single_instance() -> Any:
    """One governor per user: a held byte lock on governor.lock (released by the OS when the process dies)."""
    import msvcrt
    f = open(state_dir() / "governor.lock", "a+b")
    try:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def cmd_run(args: argparse.Namespace) -> int:
    setup_logging(args.console)
    lock = acquire_single_instance()
    if lock is None:
        log.error("another governor instance holds governor.lock; exiting")
        return 3
    cfg = load_config()
    if args.dry_run:
        cfg["dry_run"] = True
    import psutil
    try:
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except psutil.Error:
        pass
    io = SystemIO(cfg)
    gov = Governor(io, cfg, state_dir() / STATE_NAME, state_dir() / STATUS_NAME)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGTERM)):
        signal.signal(sig, lambda *_: stop.set())
    log.info("governor started dry_run=%s jeff=%s poll=%ss warm=%s", gov.dry, cfg["jeff_during_generation"],
             cfg["poll_s"], cfg["warm"]["enabled"])
    try:
        while not stop.is_set():
            try:
                gov.tick()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                log.exception("tick failed")
            stop.wait(max(1.0, float(cfg["poll_s"])))
    finally:
        gov.shutdown()
        log.info("governor stopped")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    p = state_dir() / STATUS_NAME
    print(p.read_text(encoding="utf-8") if p.exists() else "no status yet")
    return 0


def cmd_init(_: argparse.Namespace) -> int:
    p = state_dir() / CONFIG_NAME
    state_dir().mkdir(parents=True, exist_ok=True)
    if p.exists():
        print(f"exists: {p}")
    else:
        p.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written: {p}")
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    """Read one real weight file twice with the warm-up reader: cold MB/s, then warm MB/s, plus RAM before/after."""
    io = SystemIO(load_config())
    size = os.path.getsize(args.file)
    out = {"file": os.path.basename(args.file), "size_gb": round(size / 1e9, 2), "mem_before": io.mem()}
    first = read_file_low_priority(args.file, lambda: True, 8, 0)
    out["mem_after_first_read"] = io.mem()
    second = read_file_low_priority(args.file, lambda: True, 8, 0)
    out["mem_after_second_read"] = io.mem()
    out["first_read_mb_s"], out["second_read_mb_s"] = round(first["mb_s"]), round(second["mb_s"])
    out["first_secs"], out["second_secs"] = round(first["secs"], 1), round(second["secs"], 1)
    print(json.dumps(out, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--console", action="store_true")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    m = sub.add_parser("measure")
    m.add_argument("file")
    m.set_defaults(fn=cmd_measure)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
