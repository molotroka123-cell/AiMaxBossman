"""Market collector data root: `bossman market watch` (no --root) must write where `/api/market/status` reads.

Found by the night-2026-09-30 audit: `ledger.default_root()` used BCC_DATA_DIR or %LOCALAPPDATA%, while the
backend's data dir (`bcc.config._data_dir()`) is `command-center/data` for a source checkout (the owner's
:8800 setup) and XDG on Linux, so the two never met unless the v15 owner-run passed --root explicitly.
"""
from __future__ import annotations

from pathlib import Path

from bcc import config
from bcc.market import collector
from bcc.market.ledger import Ledger, default_root

from .conftest import client_for, make_settings, start_app

TAIL = Path("market-data") / "twitch" / "k1m6a"


def test_default_root_follows_the_backends_data_dir_not_its_own_guess(tmp_path, monkeypatch):
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    monkeypatch.setattr(config, "_data_dir", lambda: tmp_path / "backend-data")
    assert default_root() == tmp_path / "backend-data" / TAIL
    assert default_root("other") == tmp_path / "backend-data" / "market-data" / "twitch" / "other"


def test_default_root_uses_the_environment_override_like_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "from-env"))
    assert default_root() == tmp_path / "from-env" / TAIL
    assert default_root() == config._data_dir() / TAIL == config.Settings().data_dir / TAIL


def test_default_root_equals_the_backend_default_in_this_checkout(monkeypatch):
    """Whatever this machine's layout is (source checkout, installed wheel, XDG), the collector and the backend
    resolve the same directory: negative control of the stubbed test above, with nothing stubbed."""
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    assert default_root() == config.Settings().data_dir / TAIL


async def test_what_the_collector_writes_by_default_is_what_the_status_endpoint_reads(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("BCC_DATA_DIR", str(settings.data_dir))        # the backend and the CLI share the environment
    app, svc = await start_app(settings)
    try:
        async with client_for(app, svc) as c:
            before = (await c.get("/api/market/status")).json()
            assert before["ledger"] is None and before["root"] == str(default_root())
            Ledger(default_root()).close()                            # what `bossman market watch` does first
            after = (await c.get("/api/market/status")).json()
            assert after["ledger"] is not None and after["ledger"]["attempted"] == 0
    finally:
        await svc.stop()


def test_cli_stop_without_root_writes_the_stop_file_where_the_backend_looks(tmp_path, monkeypatch, capsys):
    backend = tmp_path / "backend-data"
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    monkeypatch.setattr(config, "_data_dir", lambda: backend)
    assert collector.main(["stop"]) == 0
    assert (backend / TAIL / "STOP").is_file()
    assert str(backend / TAIL / "STOP") in capsys.readouterr().out


def test_an_explicit_root_still_overrides_the_default(tmp_path, monkeypatch):
    backend = tmp_path / "backend-data"
    explicit = tmp_path / "elsewhere"
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    monkeypatch.setattr(config, "_data_dir", lambda: backend)
    assert collector.main(["stop", "--root", str(explicit)]) == 0
    assert (explicit / "STOP").is_file()
    assert not backend.exists()                                       # the default location was not touched
