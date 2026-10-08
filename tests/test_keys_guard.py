"""tools/keys_guard.py: provider keys survive loss, values never printed (owner order 07.10).

DPAPI exists only on Windows; here _protect/_unprotect are replaced by a reversible fake that
still refuses foreign bytes, so "corrupt copy" behaves like a real undecryptable blob.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("keys_guard", ROOT / "tools" / "keys_guard.py")
kg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kg)

SECRET_A = "fake-provider-value-AAAA-1234"  # ci-secret-scan: allow (fake fixture value)
SECRET_B = "fake-provider-value-BBBB-5678"  # ci-secret-scan: allow (fake fixture value)
MAGIC = b"FAKEDPAPI:"


def _fake_protect(data: bytes) -> bytes:
    return MAGIC + base64.b64encode(data[::-1])


def _fake_unprotect(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise kg.KeysGuardError("DPAPI unprotect failed")
    return base64.b64decode(blob[len(MAGIC):])[::-1]


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(kg, "_protect", _fake_protect)
    monkeypatch.setattr(kg, "_unprotect", _fake_unprotect)
    keys, backups = tmp_path / "keys", tmp_path / "backups" / "keys"
    keys.mkdir(parents=True)
    return keys, backups


def _env(keys: Path) -> Path:
    return keys / kg.ENV_NAME


def _write_env(keys: Path, text: str) -> None:
    _env(keys).write_text(text, encoding="utf-8")


def test_lost_env_file_is_restored_exactly(dirs):
    keys, backups = dirs
    _write_env(keys, f"# owner keys\nOPENROUTER_API_KEY={SECRET_A}\nNVIDIA_API_KEY={SECRET_B}\n")
    assert kg.backup(keys, backups)["ok"]
    _env(keys).unlink()
    out = kg.restore(keys, backups)
    assert out["ok"] and out["restored"] == ["NVIDIA_API_KEY", "OPENROUTER_API_KEY"]
    assert kg.parse_env(_env(keys).read_text(encoding="utf-8")) == {
        "OPENROUTER_API_KEY": SECRET_A, "NVIDIA_API_KEY": SECRET_B}


def test_ensure_completes_a_partial_env_and_a_newer_env_value_wins(dirs):
    keys, backups = dirs
    _write_env(keys, f"OPENROUTER_API_KEY={SECRET_A}\nNVIDIA_API_KEY={SECRET_B}\n")
    kg.backup(keys, backups)
    _write_env(keys, "OPENROUTER_API_KEY=rotated-value\n")          # NVIDIA lost, OpenRouter rotated
    out = kg.ensure(keys, backups)
    assert out["ok"] and out["restore"]["restored"] == ["NVIDIA_API_KEY"]
    env = kg.parse_env(_env(keys).read_text(encoding="utf-8"))
    assert env == {"OPENROUTER_API_KEY": "rotated-value", "NVIDIA_API_KEY": SECRET_B}


def test_an_empty_env_value_never_erases_a_saved_key(dirs):
    keys, backups = dirs
    _write_env(keys, f"OPENROUTER_API_KEY={SECRET_A}\n")
    kg.backup(keys, backups)
    _write_env(keys, "OPENROUTER_API_KEY=\n")
    kg.ensure(keys, backups)
    assert kg.parse_env(_env(keys).read_text(encoding="utf-8"))["OPENROUTER_API_KEY"] == SECRET_A
    # and the refreshed protected copy still holds the value
    saved, _ = kg.load_saved(keys, backups)
    assert saved["OPENROUTER_API_KEY"] == SECRET_A


def test_a_corrupt_primary_copy_is_never_overwritten_and_restore_uses_a_good_copy(dirs):
    keys, backups = dirs
    _write_env(keys, f"OPENROUTER_API_KEY={SECRET_A}\n")
    kg.backup(keys, backups)
    corrupt = b"not a dpapi blob"
    (keys / kg.BLOB_NAME).write_bytes(corrupt)
    out = kg.backup(keys, backups)
    assert out["ok"] and out["primary_corrupt_kept"] and not out["primary_written"]
    assert (keys / kg.BLOB_NAME).read_bytes() == corrupt
    _env(keys).unlink()
    restored = kg.restore(keys, backups)
    assert restored["ok"] and restored["source"].startswith("provider-keys-")
    assert kg.parse_env(_env(keys).read_text(encoding="utf-8")) == {"OPENROUTER_API_KEY": SECRET_A}


def test_values_never_reach_stdout_or_stderr(dirs, capsys):
    keys, backups = dirs
    _write_env(keys, f"OPENROUTER_API_KEY={SECRET_A}\nNVIDIA_API_KEY={SECRET_B}\n")
    for action in ("backup", "verify", "ensure", "restore"):
        kg.main([action, "--keys-dir", str(keys), "--backup-dir", str(backups)])
    _env(keys).unlink()
    kg.main(["restore", "--keys-dir", str(keys), "--backup-dir", str(backups)])
    out = capsys.readouterr()
    for secret in (SECRET_A, SECRET_B):
        assert secret not in out.out and secret not in out.err
    assert "OPENROUTER_API_KEY" in out.out                       # names are reported


def test_rotation_keeps_five_timestamped_copies(dirs):
    keys, backups = dirs
    _write_env(keys, f"OPENROUTER_API_KEY={SECRET_A}\n")
    for i in range(8):
        kg.backup(keys, backups, now=1_790_000_000 + i * 60)
    copies = kg._copies(backups)
    assert len(copies) == kg.KEEP
    assert copies[0].name == "provider-keys-" + __import__("time").strftime(
        "%Y%m%d-%H%M%S", __import__("time").localtime(1_790_000_000 + 7 * 60)) + ".dpapi"


def test_failed_atomic_replace_leaves_the_env_untouched(dirs, monkeypatch):
    keys, backups = dirs
    _write_env(keys, f"OPENROUTER_API_KEY={SECRET_A}\nNVIDIA_API_KEY={SECRET_B}\n")
    kg.backup(keys, backups)
    _write_env(keys, f"OPENROUTER_API_KEY={SECRET_A}\n")
    before = _env(keys).read_bytes()

    def boom(*_a, **_k):
        raise OSError("replace failed")

    monkeypatch.setattr(kg.os, "replace", boom)
    with pytest.raises(OSError):
        kg.restore(keys, backups)
    assert _env(keys).read_bytes() == before
    assert not [p for p in keys.iterdir() if ".tmp" in p.name]   # no torn temp file left


def test_verify_exit_codes(dirs):
    keys, backups = dirs
    assert kg.verify(keys, backups)["code"] == 2                  # no protected copy yet
    _write_env(keys, f"OPENROUTER_API_KEY={SECRET_A}\n")
    kg.backup(keys, backups)
    assert kg.verify(keys, backups)["code"] == 0
    _write_env(keys, "OPENROUTER_API_KEY=\n")
    v = kg.verify(keys, backups)
    assert v["code"] == 1 and v["missing"] == ["OPENROUTER_API_KEY"]


def test_backup_without_keys_writes_nothing(dirs):
    keys, backups = dirs
    out = kg.backup(keys, backups)
    assert not out["ok"] and not (keys / kg.BLOB_NAME).exists() and not backups.exists()


@pytest.mark.skipif(os.name == "nt", reason="the refusal is the non-Windows contract")
def test_without_windows_dpapi_the_protected_operations_refuse(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / kg.ENV_NAME).write_text(f"OPENROUTER_API_KEY={SECRET_A}\n", encoding="utf-8")
    with pytest.raises(kg.KeysGuardError):
        kg.backup(keys, tmp_path / "b")
    assert kg.main(["backup", "--keys-dir", str(keys), "--backup-dir", str(tmp_path / "b")]) == 2


def test_parse_env_ignores_comments_and_export_prefix():
    assert kg.parse_env("# c\n\nexport A=1\nB = two \nnot a line\nA=3\n") == {"A": "3", "B": "two"}
    assert json.loads(json.dumps(kg.merge({"A": "", "B": "x"}, {"A": "saved", "C": "c"}))) == {
        "A": "saved", "B": "x", "C": "c"}


def test_backend_launch_runs_the_guard_before_starting_and_never_blocks_on_it(monkeypatch, tmp_path):
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location("owner_one_bossman", ROOT / "tools" / "owner_one_bossman.py")
    oob = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oob)
    src = (ROOT / "tools" / "owner_one_bossman.py").read_text(encoding="utf-8")
    body = src[src.index("def launch("):]
    assert body.index("keys_ensure(say)") < body.index("argv = command_for(")   # before the spawn
    # Only the launcher module sees a Windows name; the global os module is never touched
    # (patching os.name globally broke pytest's failure reporting on Linux).
    monkeypatch.setattr(oob, "os", SimpleNamespace(name="nt", environ=dict(os.environ, LOCALAPPDATA=str(tmp_path))))
    lines: list[str] = []
    oob.keys_ensure(lines.append)                 # never raises; reports one names-only line
    assert len(lines) == 1 and lines[0].startswith("keys:")
