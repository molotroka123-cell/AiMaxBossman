"""Сборка колеса Command Center.

Метаданные живут в pyproject.toml; здесь только одно, чего декларативно
выразить нельзя: интерфейс лежит в `command-center/ui`, ВНЕ пакета `bcc`,
поэтому в колесо он сам по себе не попадает. Установленный продукт из-за
этого отдавал 404 на `/` — сервер поднимался, а пользоваться им было нечем.

UI кладётся в собранный пакет как `bcc/ui`. Исходное дерево не трогаем:
копия делается в каталоге сборки, поэтому чекаут остаётся чистым.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent


class BuildWithUI(build_py):
    def run(self) -> None:
        super().run()
        source = HERE / "ui"
        if not source.is_dir():
            raise SystemExit("command-center/ui отсутствует — колесо без интерфейса не собираем")
        target = Path(self.build_lib) / "bcc" / "ui"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        if not (target / "index.html").is_file():
            raise SystemExit("в собранный пакет не попал ui/index.html")


setup(cmdclass={"build_py": BuildWithUI})
