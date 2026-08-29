"""Stage 8 — security-тесты: сеть, секреты, artifact gate, редакция траектории."""
from __future__ import annotations

import os
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

from bossman import errors
from bossman.sandbox import InMemorySecretBroker, NetworkGuard
from bossman.sandbox.artifacts import ArtifactGate
from bossman.sandbox.models import IsolationTier, NetworkMode, PolicyMode, SandboxPolicy
from bossman.sandbox.trajectory import TrajectoryRecorder


def _policy(net: NetworkMode, allowlist=()):
    return SandboxPolicy(mode=PolicyMode.CONNECTED, network_mode=net,
                         isolation_tier=IsolationTier.CONTAINER, allowlist=tuple(allowlist),
                         read_only_root=True, drop_caps=True, no_new_privs=True)


# ---------- сеть ----------

def test_network_offline_blocks_everything():
    g = NetworkGuard()
    d = g.decide("example.com", _policy(NetworkMode.OFFLINE))
    assert not d.allowed and "OFFLINE" in d.reason


@pytest.mark.parametrize("host", [
    "127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.9",
    "169.254.169.254",   # cloud metadata
    "::1", "localhost", "postgres",  # control-plane / loopback name
])
def test_private_and_metadata_denied_even_on_internet(host):
    g = NetworkGuard()
    d = g.decide(host, _policy(NetworkMode.INTERNET))
    assert not d.allowed, f"{host} must be blocked"


def test_internet_allows_public_host():
    g = NetworkGuard()
    assert g.decide("api.github.com", _policy(NetworkMode.INTERNET)).allowed


def test_allowlist_only_permits_listed():
    g = NetworkGuard()
    pol = _policy(NetworkMode.ALLOWLIST, allowlist=["api.github.com"])
    assert g.decide("api.github.com", pol).allowed
    assert g.decide("evil.example", pol).allowed is False


# ---------- секреты ----------

def _broker(scopes=("openrouter",)):
    material = {"openrouter": "sk-REAL-SECRET-VALUE-do-not-leak"}  # ci-secret-scan: allow
    return InMemorySecretBroker(lambda scope: material.get(scope), allowed_scopes=frozenset(scopes))


def test_secret_grant_and_revoke():
    b = _broker()
    g = b.grant("sbx1", "openrouter", ttl_seconds=60)
    assert b.redeem(g.id, "sbx1").startswith("sk-")
    assert b.revoke(g.id) is True
    with pytest.raises(errors.SecretDenied):
        b.redeem(g.id, "sbx1")


def test_secret_binding_enforced():
    b = _broker()
    g = b.grant("sbx1", "openrouter", ttl_seconds=60)
    with pytest.raises(errors.SecretDenied):
        b.redeem(g.id, "OTHER_SANDBOX")  # не привязан к этой песочнице


def test_secret_ttl_expiry():
    b = _broker()
    g = b.grant("sbx1", "openrouter", ttl_seconds=0.01)
    time.sleep(0.02)
    with pytest.raises(errors.SecretDenied):
        b.redeem(g.id, "sbx1")


def test_secret_scope_not_allowed():
    b = _broker(scopes=("openrouter",))
    with pytest.raises(errors.SecretDenied):
        b.grant("sbx1", "github_pat", ttl_seconds=60)


# ---------- artifact gate ----------

def test_path_traversal_rejected(tmp_path):
    gate = ArtifactGate(tmp_path)
    with pytest.raises(errors.ArtifactRejected):
        gate.inspect("../escape.txt")
    with pytest.raises(errors.ArtifactRejected):
        gate.inspect("/etc/passwd")


@pytest.mark.skipif(os.name == "nt",
                    reason="symlink без Developer Mode/админ-прав (WinError 1314)")
def test_symlink_escape_quarantined_or_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret")
    link = root / "link.txt"
    link.symlink_to(outside)  # цель ВНЕ root
    gate = ArtifactGate(root)
    with pytest.raises(errors.ArtifactRejected):
        gate.inspect("link.txt")  # symlink escape наружу → отказ


@pytest.mark.skipif(os.name == "nt",
                    reason="symlink без Developer Mode/админ-прав (WinError 1314)")
def test_symlink_inside_root_is_quarantined(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "real.txt").write_text("data")
    link = root / "link.txt"
    link.symlink_to(root / "real.txt")  # цель ВНУТРИ root
    art = ArtifactGate(root).inspect("link.txt")
    assert art.quarantined and "symlink" in art.reasons


def test_executable_quarantined(tmp_path):
    (tmp_path / "run.sh").write_text("#!/bin/sh\necho hi")
    (tmp_path / "tool.exe").write_bytes(b"MZ\x00\x00")
    a1 = ArtifactGate(tmp_path).inspect("tool.exe")
    assert a1.quarantined and "executable" in a1.reasons


def test_size_limit(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 5000)
    with pytest.raises(errors.ArtifactRejected):
        ArtifactGate(tmp_path, max_bytes=1000).inspect("big.bin")


def test_hash_computed(tmp_path):
    (tmp_path / "f.txt").write_text("bossman")
    a = ArtifactGate(tmp_path).inspect("f.txt")
    assert len(a.sha256) == 64 and a.size == len("bossman")


def test_secret_scan_hook_quarantines(tmp_path):
    (tmp_path / "leak.txt").write_text("token=sk-abcdef0123456789")
    scanner = lambda data: ["apikey"] if b"sk-" in data else []
    a = ArtifactGate(tmp_path, secret_scanner=scanner).inspect("leak.txt")
    assert a.quarantined and any(r.startswith("secret:") for r in a.reasons)


def test_zip_archive_traversal_rejected(tmp_path):
    z = tmp_path / "eb.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../evil.txt", "pwn")
    with pytest.raises(errors.ArtifactRejected):
        ArtifactGate(tmp_path).safe_archive_members(z)


def test_tar_archive_traversal_rejected(tmp_path):
    t = tmp_path / "eb.tar"
    payload = tmp_path / "x.txt"
    payload.write_text("x")
    with tarfile.open(t, "w") as tf:
        info = tarfile.TarInfo(name="../../evil.txt")
        data = b"pwn"
        info.size = len(data)
        import io
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(errors.ArtifactRejected):
        ArtifactGate(tmp_path).safe_archive_members(t)


# ---------- траектория: без секретов ----------

def test_trajectory_redacts_secrets(tmp_path):
    rec = TrajectoryRecorder("sbx1", sink_path=tmp_path / "tr.jsonl")
    ev = rec.record("tool_call", tool="http", header="Authorization: Bearer sk-abcdef0123456789ABCD",  # ci-secret-scan: allow
                    api_key="super-secret-value-1234")
    assert "sk-abcdef0123456789ABCD" not in str(ev)  # ci-secret-scan: allow
    assert ev["api_key"] == "«REDACTED»"
    written = (tmp_path / "tr.jsonl").read_text()
    assert "sk-abcdef0123456789ABCD" not in written  # ci-secret-scan: allow
    assert "super-secret-value-1234" not in written
