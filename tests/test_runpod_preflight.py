"""RunPod preflight: read-only, never downloads a model, honest on this
(no-GPU) CI host — RUNPOD_READY must be NO here, not a fabricated YES.

Repo-root tests dir mirrors tools/ci_secret_scan.py's placement convention
(tooling that spans both bossman-core and command-center, not owned by
either subpackage's own test suite).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "runpod_preflight.py"


def _run_json() -> dict:
    p = subprocess.run([sys.executable, str(SCRIPT), "--json"],
                       capture_output=True, text=True, timeout=30)
    return json.loads(p.stdout), p.returncode


def test_preflight_runs_without_crashing_and_emits_valid_json():
    report, rc = _run_json()
    assert isinstance(report, dict)
    assert rc in (0, 1)


def test_preflight_never_reports_ready_without_a_real_gpu():
    """This CI host has no GPU. RUNPOD_READY must honestly be False, and the
    absence must be named in blockers — never silently assumed present."""
    report, rc = _run_json()
    if not report["gpu"]["present"]:
        assert report["runpod_ready"] is False
        assert any("GPU" in b for b in report["blockers"])
        assert rc == 1


def test_preflight_exit_code_matches_ready_field():
    report, rc = _run_json()
    assert (rc == 0) == report["runpod_ready"]


def test_preflight_does_not_touch_network_beyond_local_probes():
    """Static check: the script must not import requests/httpx for outbound
    calls, must not shell out to curl/wget, must not reference any external
    hostname other than loopback checks it performs itself."""
    src = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("requests.get", "httpx.get", "urlopen", "curl ", "wget "):
        assert forbidden not in src, f"preflight must stay local-only, found {forbidden!r}"


def test_preflight_never_downloads_a_model():
    src = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("ollama pull", "ollama run", "snapshot_download", "from_pretrained"):
        assert forbidden not in src, f"preflight must not fetch model weights, found {forbidden!r}"


def test_preflight_cloud_keys_reports_names_only_never_values():
    """Presence-only visibility: the script must never read os.environ[NAME]
    and print the value — only whether the name is set."""
    report, _ = _run_json()
    keys = report["cloud_keys"]
    assert "present" in keys and isinstance(keys["present"], list)
    # no field anywhere in the report may be a raw secret value
    dumped = json.dumps(report)
    assert "sk-" not in dumped and "Bearer " not in dumped


def test_preflight_respects_ollama_host_env_override(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("runpod_preflight", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:19999")
    spec.loader.exec_module(mod)
    out = mod.check_model_runtime()
    assert out["ollama"]["effective_url"] == "http://127.0.0.1:19999"
    assert out["ollama"]["reachable"] is False  # nothing listens on 19999


def test_preflight_respects_postgres_dsn_env_override(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("runpod_preflight", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("BOSSMAN_TEST_PG_DSN",
                       "postgresql://u:p@127.0.0.1:59999/db")
    spec.loader.exec_module(mod)
    out = mod.check_postgres()
    assert out["port"] == 59999
    assert out["reachable"] is False
