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

import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from bcc import config as cfg

COMPONENT = Path(__file__).resolve().parents[1]
REPOSITORY = COMPONENT.parent

#: Build inputs that live OUTSIDE the component but inside the repository, in
#: the layout `command-center/setup.py` resolves them from. They are listed
#: here rather than guessed, so a new external input added to the build shows
#: up as a failure here instead of as a wheel that is missing something.
SIBLING_BUILD_INPUTS = ("apps", "integrations", "tools")

_BUILD_JUNK = shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__",
                                     "data", ".pytest_cache", ".mypy_cache", "node_modules")


def _pristine_component(tmp_path: Path) -> Path:
    """Чистый контекст сборки: компонент ПЛЮС то, что он тянет из репозитория.

    Первая версия этого теста была ЛОЖНО-ЗЕЛЁНОЙ: setuptools переиспользует
    каталог `build/`, и колесо получало интерфейс из прошлой сборки даже с
    удалённой упаковкой. Поэтому собираем из чистой копии.

    Копируется не один каталог компонента, а его настоящий контекст сборки.
    `setup.py` укладывает в колесо не только `ui/`: ещё манифесты приложений
    из `../apps` и закреплённую личность внешнего сайдкара File Intelligence
    из `../integrations`. Копия одного `command-center/` ломала сборку с
    «integration.json is missing» — и это не ложная тревога инструмента, а
    ровно тот вопрос, который тест и задаёт: «а всё ли, что нужно установленному
    продукту, действительно лежит в исходниках?». Сохраняем относительную
    раскладку, в которой владелец и CI собирают колесо.
    """
    repository = tmp_path / "repository"
    shutil.copytree(COMPONENT, repository / COMPONENT.name, ignore=_BUILD_JUNK)
    for name in SIBLING_BUILD_INPUTS:
        source = REPOSITORY / name
        assert source.is_dir(), f"build input {name}/ is missing from the checkout"
        shutil.copytree(source, repository / name, ignore=_BUILD_JUNK)
    return repository / COMPONENT.name


def _build_flags() -> list[str]:
    """Изоляция сборки отключается ТОЛЬКО если бэкенд уже есть рядом.

    `--no-build-isolation` требует, чтобы `setuptools.build_meta` импортировался
    в том же интерпретаторе, который вызывает pip. На Python 3.12 setuptools
    больше не ставится автоматически, и джоб падал с `BackendUnavailable` — не
    потому, что колесо неправильное, а потому, что тест нарушал контракт
    build-system. Когда бэкенда нет, pip поднимает изолированное окружение по
    объявленному в pyproject `requires`, то есть ровно так, как соберёт
    владелец.
    """
    try:
        found = importlib.util.find_spec("setuptools.build_meta") is not None
    except ModuleNotFoundError:
        # Отсутствует сам пакет setuptools: find_spec на подмодуле не вернёт
        # None, а бросит исключение. Именно этот случай и есть py3.12.
        found = False
    return ["--no-build-isolation"] if found else []


def test_the_wheel_carries_the_interface(tmp_path: Path) -> None:
    source = _pristine_component(tmp_path)
    out = tmp_path / "wheels"
    done = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", *_build_flags(),
         "--no-cache-dir", "--wheel-dir", str(out), str(source)],
        capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-4000:]
    wheels = list(out.glob("bossman_command_center-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
    assert "bcc/_ui/index.html" in names, (
        "колесо без интерфейса: установленный продукт ответит 404 на /"
    )
    assert any(n.startswith("bcc/_ui/") and n.endswith(".js") for n in names), sorted(
        n for n in names if n.startswith("bcc/_ui/"))[:20]
    # The same build also carries what File Intelligence and the app registry
    # need; a wheel that serves a page but cannot name its pinned integration
    # is not the installed product either.
    assert "bcc/_integrations/ai-file-sorter/integration.json" in names, sorted(
        n for n in names if n.startswith("bcc/_integrations/"))[:20]


def test_the_interface_is_taken_from_the_package_when_it_is_packaged(tmp_path: Path,
                                                                     monkeypatch) -> None:
    """Упакованный интерфейс имеет приоритет; в чекауте берётся его каталог."""
    monkeypatch.delenv("BCC_UI_DIR", raising=False)
    pkg, root = tmp_path / "site" / "bcc", tmp_path / "site"
    (pkg / "_ui").mkdir(parents=True)
    (pkg / "_ui" / "index.html").write_text("packaged", encoding="utf-8")
    monkeypatch.setattr(cfg, "PKG_DIR", pkg)
    monkeypatch.setattr(cfg, "ROOT", root)
    assert cfg._ui_dir() == pkg / "_ui"

    checkout = tmp_path / "checkout"
    (checkout / "ui").mkdir(parents=True)
    (checkout / "bcc").mkdir()
    monkeypatch.setattr(cfg, "PKG_DIR", checkout / "bcc")
    monkeypatch.setattr(cfg, "ROOT", checkout)
    assert cfg._ui_dir() == checkout / "ui"

    # Negative control: an empty packaged directory is not an interface. The
    # wheel check above passes only because index.html is really in the zip,
    # so the runtime test must not accept a directory that merely exists.
    hollow = tmp_path / "hollow"
    (hollow / "bcc" / "_ui").mkdir(parents=True)
    (hollow / "ui").mkdir()
    monkeypatch.setattr(cfg, "PKG_DIR", hollow / "bcc")
    monkeypatch.setattr(cfg, "ROOT", hollow)
    assert cfg._ui_dir() == hollow / "ui"


def test_owner_data_never_lands_inside_the_installed_package(tmp_path: Path,
                                                             monkeypatch) -> None:
    installed = tmp_path / "site-packages"
    installed.mkdir()
    monkeypatch.setattr(cfg, "ROOT", installed)
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    data = cfg._data_dir()
    assert not str(data).startswith(str(installed)), data

    checkout = tmp_path / "component"
    (checkout / "ui").mkdir(parents=True)
    (checkout / "ui" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "ROOT", checkout)
    assert cfg._data_dir() == checkout / "data"

    # Negative control: a stray pyproject.toml dropped next to an installed
    # package must not be read as a checkout and re-open the original defect.
    marker_only = tmp_path / "site-packages-with-marker"
    marker_only.mkdir()
    (marker_only / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "ROOT", marker_only)
    assert not str(cfg._data_dir()).startswith(str(marker_only)), cfg._data_dir()


def test_the_running_app_serves_the_interface_it_ships() -> None:
    """Смонтированная статика и объявленный каталог — одно и то же место."""
    from bcc.config import Settings
    settings = Settings()
    assert settings.ui_dir.is_dir(), settings.ui_dir
    assert (settings.ui_dir / "index.html").is_file(), settings.ui_dir
