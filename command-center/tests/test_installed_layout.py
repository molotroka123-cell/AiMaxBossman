"""Source/installed layout contracts; full wheels are exercised by local-bundle CI."""
from pathlib import Path

from bcc import config


def test_installed_ui_is_default_but_explicit_override_survives(tmp_path, monkeypatch):
    pkg = tmp_path / "site-packages" / "bcc"
    (pkg / "_ui").mkdir(parents=True)
    (pkg / "_ui" / "index.html").write_text("installed UI")
    monkeypatch.setattr(config, "PKG_DIR", pkg)
    monkeypatch.setattr(config, "ROOT", pkg.parent)
    monkeypatch.delenv("BCC_UI_DIR", raising=False)
    assert config.Settings(data_dir=tmp_path / "data").ui_dir == pkg / "_ui"
    monkeypatch.setenv("BCC_UI_DIR", str(tmp_path / "explicit UI"))
    assert config.Settings(data_dir=tmp_path / "data").ui_dir == tmp_path / "explicit UI"


def test_installed_data_and_skill_writes_do_not_target_python_environment(tmp_path, monkeypatch):
    root = tmp_path / "read-only-python" / "site-packages"
    monkeypatch.setattr(config, "ROOT", root)
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    monkeypatch.setenv("BCC_UI_DIR", str(root / "external" / "ui"))
    settings = config.Settings()
    assert settings.data_dir == tmp_path / "user-data" / "bossman" / "command-center"
    assert settings.skills_workspace == settings.data_dir
    settings.ensure_dirs()
    assert settings.data_dir.is_dir()
    assert not root.exists()


def test_windows_data_uses_localappdata_and_explicit_data_dir_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path / "site-packages")
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local App Data"))
    assert config.Settings().data_dir == tmp_path / "Local App Data" / "Bossman" / "CommandCenter"
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "owner-selected"))
    assert config.Settings().data_dir == tmp_path / "owner-selected"


def test_existing_source_install_keeps_its_data_and_skill_workspace(tmp_path, monkeypatch):
    root = tmp_path / "checkout" / "command-center"
    (root / "ui").mkdir(parents=True)
    (root / "ui" / "index.html").write_text("source UI")
    (root / "pyproject.toml").write_text("[project]")
    monkeypatch.setattr(config, "ROOT", root)
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    settings = config.Settings()
    assert settings.data_dir == root / "data"
    assert settings.skills_workspace == root.parent
