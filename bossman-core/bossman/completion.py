"""Typed completion obligations for the legacy worker. Model text is never proof."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class FileObligation(BaseModel):
    path: str
    sha256: str | None = None
    contains: str | None = None
    exists: bool = True


class CompletionContract(BaseModel):
    mode: Literal["unspecified", "informational", "action"] = "unspecified"
    files: list[FileObligation] = Field(default_factory=list)


class CompletionGate:
    def __init__(self, contract: CompletionContract, root: Path):
        self.contract, self.root = contract, root.resolve()
        self.writes: dict[str, str] = {}
        self.unverified_effect = False

    def _read(self, path: Path) -> bytes:
        # Bound the read itself: a size check alone races concurrent file growth.
        with path.open("rb") as stream:
            data = stream.read(8_000_001)
        if len(data) > 8_000_000:
            raise ValueError("completion evidence exceeds byte budget")
        return data

    def record(self, name: str, rights: str, args: dict, *, error: bool) -> None:
        try:
            self._record(name, rights, args, error=error)
        except (OSError, ValueError, RuntimeError):
            self.unverified_effect = True

    def _record(self, name: str, rights: str, args: dict, *, error: bool) -> None:
        if rights == "read":
            return
        if error or name not in ("fs.write", "fs.edit"):
            self.unverified_effect = True
            return
        path = (self.root / str(args.get("path", ""))).resolve()
        if self.root not in path.parents or not path.is_file():
            self.unverified_effect = True
            return
        if path.stat().st_size > 8_000_000:
            self.unverified_effect = True
            return
        observed = self._read(path)  # independent reopen after tool completion
        if name == "fs.write" and observed != str(args.get("content", "")).encode("utf-8"):
            self.unverified_effect = True
            return
        if name == "fs.edit" and str(args.get("new", "")).encode("utf-8") not in observed:
            self.unverified_effect = True
            return
        self.writes[str(path)] = hashlib.sha256(observed).hexdigest()

    def finish(self) -> tuple[str, str]:
        try:
            return self._finish()
        except (OSError, ValueError, RuntimeError):
            return "unverified", "Не удалось независимо прочитать результат действия."

    def _finish(self) -> tuple[str, str]:
        if self.unverified_effect:
            return "unverified", "Есть действие без независимого подтверждения результата."
        if self.contract.mode != "action":
            if self.writes:
                return "unverified", "Для выполненных действий не задан контракт результата."
            return "answered", "Ответ модели; выполнение действий не подтверждено."
        if not self.contract.files:
            return "unverified", "Не заданы проверяемые результаты действия."
        for req in self.contract.files:
            path = (self.root / req.path).resolve()
            if (self.root not in path.parents or str(path) not in self.writes or not req.exists
                    or not path.is_file() or path.stat().st_size > 8_000_000):
                return "unverified", "Нет подтверждённой записи требуемого файла в этом прогоне."
            data = self._read(path)
            digest = hashlib.sha256(data).hexdigest()
            if digest != self.writes[str(path)] or (req.sha256 is not None and digest != req.sha256):
                return "unverified", "Содержимое файла не совпадает с подтверждённым результатом."
            if req.contains is not None and req.contains.encode("utf-8") not in data:
                return "unverified", "В файле отсутствует требуемое содержимое."
        return "done", "Все результаты подтверждены свежим чтением."
