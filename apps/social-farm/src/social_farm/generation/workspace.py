"""Карантин и утверждённая рабочая область для порождённых файлов.

Скачанный файл — это файл из интернета. Он приходит под именем, которое выбрал
провайдер, в каталог, который выбрал браузер, и до проверки он не результат
работы, а просто байты. Между «браузер что-то скачал» и «это результат работы»
обязан стоять шаг, и этот модуль — он.

Карантин НЕ передаётся параметром. Он выводится из идентификатора аккаунта тем
же способом, что и остальные каталоги браузерного контекста (`isolation.py`), и
это единственный способ его получить: каталог, переданный вызывающим, рано или
поздно окажется общим на два аккаунта, а общий карантин — это результат чужой
генерации, подобранный как свой.

Утверждённая рабочая область, наоборот, приходит из работы: её выбирает
задание, а не адаптер. Здесь проверяется только одно и жёстко — что файл
кладётся ВНУТРЬ неё. Имя, пришедшее от провайдера, в построении пути не
участвует вовсе.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..browser.isolation import AccountContextRoot

QUARANTINE_DIR = "generation-quarantine"
REJECTED_DIR = "rejected"


class WorkspaceEscape(RuntimeError):
    """Путь ведёт за пределы утверждённой рабочей области."""


@dataclass(frozen=True, slots=True)
class GenerationWorkspace:
    """Каталоги порождения медиа для ОДНОГО аккаунта."""

    context_root: AccountContextRoot
    account_id: str

    @property
    def account_dir(self) -> Path:
        return self.context_root.path_for(self.account_id)

    @property
    def quarantine(self) -> Path:
        return self.account_dir / QUARANTINE_DIR

    @property
    def rejected(self) -> Path:
        return self.quarantine / REJECTED_DIR

    def prepare(self) -> Path:
        """Создать каталоги с правами 0700 и убедиться, что аккаунт тот.

        Проверка владельца делается КАЖДЫЙ раз, а не только при создании: между
        запусками каталог могли переименовать или подставить, и подставленный
        карантин отдал бы работе чужой файл.
        """
        directory = self.context_root.prepare(self.account_id)
        self.context_root.assert_owned(self.account_id, directory)
        self.context_root.assert_private(directory)
        for path in (self.quarantine, self.rejected):
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, self.context_root.mode)
        return self.quarantine

    def promote(self, source: str | Path, *, workspace: str | Path,
                name: str) -> Path:
        """Перенести проверенный файл в рабочую область работы.

        Имя собирается нами из идентификатора работы и ИЗМЕРЕННОГО расширения;
        имя провайдера сюда не попадает. Поэтому `name` проверяется на попытку
        выйти из каталога — не потому что мы ждём такой попытки от своего же
        кода, а потому что проверка стоит одну строку, а ошибка стоит записи в
        произвольное место диска.
        """
        target_dir = Path(workspace).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = (target_dir / name).resolve()
        try:
            destination.relative_to(target_dir)
        except ValueError as exc:
            raise WorkspaceEscape(
                f"имя {name!r} выводит из рабочей области {target_dir}") from exc
        shutil.move(str(Path(source).resolve()), str(destination))
        os.chmod(destination, 0o600)
        return destination

    def reject(self, source: str | Path, *, reason: str) -> Path:
        """Убрать непринятый файл из карантина, но НЕ удалять его.

        Удалить было бы удобнее и хуже: разбираться, почему генерация не
        принимается, придётся по самому файлу, а не по строке в журнале.
        Отдельный подкаталог нужен, чтобы следующая работа не подобрала его как
        свежую загрузку.
        """
        source_path = Path(source).resolve()
        self.rejected.mkdir(parents=True, exist_ok=True)
        destination = self.rejected / source_path.name
        counter = 1
        while destination.exists():
            destination = self.rejected / f"{source_path.stem}.{counter}{source_path.suffix}"
            counter += 1
        shutil.move(str(source_path), str(destination))
        note = destination.with_suffix(destination.suffix + ".reason.txt")
        note.write_text(reason, encoding="utf-8")
        os.chmod(note, 0o600)
        return destination


__all__ = ["QUARANTINE_DIR", "REJECTED_DIR", "GenerationWorkspace",
           "WorkspaceEscape"]
