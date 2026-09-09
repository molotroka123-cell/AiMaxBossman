"""Установленный продукт обязан быть пригодным к использованию.

Оба дефекта воспроизведены на ЧИСТОЙ установке из колеса, а не в теории:

* `GET /` отвечал 404: `ui/` лежит вне пакета `bcc`, в колесо не попадал, и
  `ui_dir` указывал на несуществующий `site-packages/ui`. Сервер поднимался,
  а пользоваться им было нечем;
* `data_dir` по умолчанию указывал внутрь `site-packages`: БД, ключ и токен
  владельца писались в каталог установки — он бывает только для чтения, а
  переустановка стирала бы данные.

Тесты держат оба: колесо обязано нести интерфейс, а данные обязаны лежать
вне установленного пакета.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from bcc import config as cfg

COMPONENT = Path(__file__).resolve().parents[1]


def _pristine_component(tmp_path: Path) -> Path:
    """Копия компонента без локального мусора сборки.

    Первая версия этого теста была ЛОЖНО-ЗЕЛЁНОЙ: setuptools переиспользует
    каталог `build/`, и колесо получало интерфейс из прошлой сборки даже с
    удалённой упаковкой. Собираем из чистой копии, как на машине владельца.
    """
    target = tmp_path / "component"
    shutil.copytree(COMPONENT, target,
                    ignore=shutil.ignore_patterns("build", "dist", "*.egg-info",
                                                  "__pycache__", "data", ".pytest_cache"))
    return target


def test_the_wheel_carries_the_interface(tmp_path: Path) -> None:
    source = _pristine_component(tmp_path)
    out = tmp_path / "wheels"
    done = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--no-cache-dir", "--wheel-dir", str(out), str(source)],
        capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-4000:]
    wheels = list(out.glob("bossman_command_center-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
    assert "bcc/ui/index.html" in names, (
        "колесо без интерфейса: установленный продукт ответит 404 на /"
    )
    assert any(n.startswith("bcc/ui/") and n.endswith(".js") for n in names), sorted(
        n for n in names if n.startswith("bcc/ui/"))[:20]


def test_the_interface_is_taken_from_the_package_when_it_is_packaged(tmp_path: Path,
                                                                     monkeypatch) -> None:
    """Упакованный интерфейс имеет приоритет; в чекауте берётся его каталог."""
    pkg, root = tmp_path / "site" / "bcc", tmp_path / "site"
    (pkg / "ui").mkdir(parents=True)
    monkeypatch.setattr(cfg, "PKG_DIR", pkg)
    monkeypatch.setattr(cfg, "ROOT", root)
    assert cfg._default_ui_dir() == pkg / "ui"

    checkout = tmp_path / "checkout"
    (checkout / "ui").mkdir(parents=True)
    (checkout / "bcc").mkdir()
    monkeypatch.setattr(cfg, "PKG_DIR", checkout / "bcc")
    monkeypatch.setattr(cfg, "ROOT", checkout)
    assert cfg._default_ui_dir() == checkout / "ui"


def test_owner_data_never_lands_inside_the_installed_package(tmp_path: Path,
                                                             monkeypatch) -> None:
    installed = tmp_path / "site-packages"
    installed.mkdir()
    monkeypatch.setattr(cfg, "ROOT", installed)
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    data = cfg._default_data_dir()
    assert not str(data).startswith(str(installed)), data

    checkout = tmp_path / "component"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "ROOT", checkout)
    assert cfg._default_data_dir() == checkout / "data"


def test_the_running_app_serves_the_interface_it_ships() -> None:
    """Смонтированная статика и объявленный каталог — одно и то же место."""
    from bcc.config import Settings
    settings = Settings()
    assert settings.ui_dir.is_dir(), settings.ui_dir
    assert (settings.ui_dir / "index.html").is_file(), settings.ui_dir
