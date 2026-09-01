"""RunPod preflight audit fix: tools/local_hardware_ab.py must not silently
carry Windows-only assumptions onto a Linux GPU host.

Three real bugs found by audit and fixed here:
1. MODEL was a bare constant with no override — BOSSMAN_AB_MODEL now works.
2. The "direct" arm hard-coded 127.0.0.1:11435 (a Windows/WSL2 workaround
   port) regardless of OLLAMA_HOST, while the gateway arm DID honor
   OLLAMA_HOST — the two arms could silently talk to two different Ollama
   instances. Both arms now resolve through one function.
3. The RSS sampler matched only "ollama.exe" (Windows), so peak_ollama_rss
   silently stayed 0 on any Linux host, including RunPod.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _reload(monkeypatch=None):
    import local_hardware_ab as ab
    return importlib.reload(ab)


def test_model_defaults_and_respects_env_override(monkeypatch):
    monkeypatch.delenv("BOSSMAN_AB_MODEL", raising=False)
    ab = _reload()
    assert ab.MODEL == "qwen2.5:7b"
    monkeypatch.setenv("BOSSMAN_AB_MODEL", "llama3.1:70b")
    ab = _reload()
    assert ab.MODEL == "llama3.1:70b"


def test_direct_arm_url_defaults_to_real_ollama_port_on_linux(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("BOSSMAN_AB_OLLAMA_URL", raising=False)
    monkeypatch.setattr(sys, "platform", "linux", raising=False)
    ab = _reload()
    assert ab._ollama_direct_base_url() == "http://127.0.0.1:11434", \
        "RunPod/Linux must default to Ollama's real port, not the Windows workaround 11435"


def test_direct_arm_url_keeps_windows_workaround_only_on_windows(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("BOSSMAN_AB_OLLAMA_URL", raising=False)
    monkeypatch.setattr(sys, "platform", "win32", raising=False)
    ab = _reload()
    assert ab._ollama_direct_base_url() == "http://127.0.0.1:11435"


def test_direct_arm_respects_ollama_host_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:11434")
    monkeypatch.delenv("BOSSMAN_AB_OLLAMA_URL", raising=False)
    ab = _reload()
    assert ab._ollama_direct_base_url() == "http://10.0.0.5:11434"


def test_explicit_override_wins_over_everything(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:11434")
    monkeypatch.setenv("BOSSMAN_AB_OLLAMA_URL", "http://gpu-box:9999")
    ab = _reload()
    assert ab._ollama_direct_base_url() == "http://gpu-box:9999"


def test_gateway_config_and_direct_arm_use_the_same_resolved_host(monkeypatch, tmp_path):
    """Regression for the actual bug: before the fix, write_config() (gateway
    arm) and the direct-arm calls resolved OLLAMA_HOST independently and
    disagreed. Both must now go through the one resolver."""
    monkeypatch.setenv("OLLAMA_HOST", "192.168.1.50:11434")
    monkeypatch.delenv("BOSSMAN_AB_OLLAMA_URL", raising=False)
    ab = _reload()
    cfg_path = tmp_path / "gateway.yaml"
    ab.write_config(cfg_path)
    written = cfg_path.read_text(encoding="utf-8")
    resolved = ab._ollama_direct_base_url()
    assert resolved in written, \
        f"gateway config must embed the same resolved host as the direct arm: {resolved!r} not in config"


def test_resource_sampler_matches_linux_ollama_process_name():
    src = (ROOT / "tools" / "local_hardware_ab.py").read_text(encoding="utf-8")
    assert '"ollama.exe", "ollama"' in src or '"ollama", "ollama.exe"' in src, \
        "RSS sampler must match the Linux process name 'ollama', not only 'ollama.exe'"
