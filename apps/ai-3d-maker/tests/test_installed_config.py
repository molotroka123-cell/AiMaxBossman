from pathlib import Path

from ai_3d_maker import config


def test_bcc_app_data_directory_is_honored(tmp_path, monkeypatch):
    monkeypatch.delenv("AI3D_DATA_DIR", raising=False)
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path / "data"))
    settings = config.Settings()
    assert settings.data_dir == tmp_path / "data" / "ai-3d-maker"
    settings.ensure_dirs()
    assert settings.jobs_dir.is_dir()


def test_installed_default_does_not_write_python_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("AI3D_DATA_DIR", raising=False)
    monkeypatch.delenv("BOSSMAN_APPS_DATA", raising=False)
    monkeypatch.setattr(config, "APP_ROOT", tmp_path / "python")
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    assert config.Settings().data_dir == tmp_path / "user-data" / "bossman" / "apps" / "ai-3d-maker"
    assert not (tmp_path / "python").exists()
