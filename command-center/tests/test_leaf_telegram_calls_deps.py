"""authored_by_lane jeffa: dependency probe of the optional 'calls' extra (read-only, never imports heavy packages)."""
from __future__ import annotations

import sys

import pytest

from bcc.telegram_calls import deps


@pytest.fixture(autouse=True)
def _temp_data(tmp_path, monkeypatch):
    for k in ("LOCALAPPDATA", "APPDATA", "BCC_DATA_DIR", "BOSSMAN_DATA_DIR"):
        monkeypatch.setenv(k, str(tmp_path))


def test_version_of_known_and_unknown_distribution():
    assert isinstance(deps.version_of("pytest"), str)
    assert deps.version_of("definitely-not-a-real-dist-xyz") is None


def test_probe_report_is_internally_consistent():
    rep = deps.probe()
    assert set(rep["packages"]) == set(deps.PACKAGES)
    expected_missing = [info["dist"] for mod, info in rep["packages"].items()
                        if info["required"] and not info["installed"]]
    assert rep["missing_required"] == expected_missing
    assert rep["ready_for_telegram_call"] is (not expected_missing)
    for info in rep["packages"].values():
        assert (info["version"] is None) or info["installed"]


def test_probe_does_not_import_the_heavy_packages():
    heavy = ("telethon", "pytgcalls", "ntgcalls", "faster_whisper")
    before = {m for m in heavy if m in sys.modules}
    deps.probe()
    assert {m for m in heavy if m in sys.modules} == before


def test_optional_packages_never_block_readiness():
    optional = {m for m, (_, req) in deps.PACKAGES.items() if not req}
    assert {"soxr", "pysilero_vad"} <= optional
    rep = deps.probe()
    assert all(d not in rep["missing_required"] for d in ("soxr", "pysilero-vad"))
