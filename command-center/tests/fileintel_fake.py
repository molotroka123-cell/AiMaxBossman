"""Детерминированный фейковый сайдкар для контрактных тестов уровня A (§24).

Зачем он такой. Настоящий AI File Sorter требует сборки Qt и локальной модели —
в CI его нет, и «пропустить тесты, раз бинаря нет» означало бы не проверять
границу вообще. Фейк говорит на ТОМ ЖЕ протоколе, что и upstream на
закреплённом SHA (ключи и версии схем прочитаны из HeadlessStatusJson.cpp), и
позволяет вызвать каждый исход, включая те, которые на живой машине не
воспроизведёшь по заказу: незнакомый статус, не-JSON на stdout, падение
процесса, эффект без отчёта.

Фейк НЕ является доказательством того, что настоящий бинарь работает. Он
доказывает, что Bossman правильно ведёт себя на каждом ответе протокола. Это
разные утверждения, и §32 требует не схлопывать их в один PASS.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PLAN_KIND = "aifs.headlessReviewPlan"
PLAN_SCHEMA = 1
STATUS_SCHEMA = 1


def status_document(status: str, *, job_id: str = "job", operation: str = "categorize",
                    message: str = "", error: str = "",
                    paths: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    """Status JSON в форме upstream (HeadlessStatusJson.cpp:88-112)."""
    return {
        "schemaVersion": STATUS_SCHEMA,
        "status": status,
        "operation": operation,
        "jobId": job_id,
        "message": message,
        "error": error,
        "updatedAtUtc": "2026-09-08T12:00:00.000Z",
        "paths": paths or [],
        **extra,
    }


def plan_entry(file_path: str, *, file_name: str = "", category: str = "Documents",
               subcategory: str = "", suggested_name: str = "",
               rename_only: bool = False) -> dict[str, Any]:
    """Запись плана в форме upstream (categorized_file_to_json)."""
    return {
        "filePath": file_path,
        "fileName": file_name or Path(file_path).name,
        "type": "file",
        "category": category,
        "subcategory": subcategory,
        "taxonomyId": 1,
        "fromCache": False,
        "usedConsistencyHints": False,
        "suggestedName": suggested_name or file_name or Path(file_path).name,
        "renameOnly": rename_only,
        "renameApplied": False,
        "canonicalCategory": category,
        "canonicalSubcategory": subcategory,
        "learningContext": "",
    }


def review_plan(entries: list[dict[str, Any]], *, job_id: str = "job",
                operation: str = "categorize",
                paths: list[str] | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": PLAN_SCHEMA,
        "kind": PLAN_KIND,
        "createdAtUtc": "2026-09-08T12:00:00.000Z",
        "jobId": job_id,
        "operation": operation,
        "paths": paths or [],
        "applyOptions": {"baseDir": "", "undoDir": "", "useSubcategories": True,
                         "includeSubdirectories": False,
                         "applySuggestedNames": True,
                         "moveCategorizedEntries": True, "categoryLanguage": "en"},
        "entries": entries,
    }


@dataclass
class FakeSidecar:
    """Подменяемый запускатель процесса.

    Записывает КАЖДЫЙ argv, с которым его позвали, — на этом стоит доказательство
    §6: запрещённый флаг ловится не по намерению, а по тому, что реально
    построено.
    """
    #: что писать в review-файл при анализе (None — не писать вовсе)
    plan: dict[str, Any] | None = None
    #: статус анализа
    analyze_status: str = "review_required"
    #: статус применения
    apply_status: str = "completed"
    #: если задано — писать это в status-файл дословно (для не-JSON и мусора)
    raw_status: str | None = None
    #: настоящий эффект применения: вызывается перед тем, как отчитаться
    effect: Callable[[dict[str, Any]], None] | None = None
    #: поднять это исключение вместо запуска (падение процесса, таймаут)
    raise_on_run: BaseException | None = None
    returncode: int = 0
    calls: list[list[str]] = field(default_factory=list)

    async def __call__(self, argv: list[str], *, timeout: float) -> dict[str, Any]:
        self.calls.append(list(argv))
        if self.raise_on_run is not None:
            raise self.raise_on_run

        flags = {argv[i]: argv[i + 1] for i in range(len(argv) - 1)
                 if argv[i].startswith("--")}
        status_file = Path(flags.get("--status-file", ""))
        review_file = Path(flags.get("--review-file", ""))
        job_id = flags.get("--job-id", "job")
        applying = "--headless-apply" in argv

        if applying:
            if self.effect is not None:
                self.effect(flags)
            document = status_document(self.apply_status, job_id=job_id)
        else:
            if self.plan is not None and str(review_file):
                review_file.parent.mkdir(parents=True, exist_ok=True)
                review_file.write_text(json.dumps(self.plan), encoding="utf-8")
            document = status_document(self.analyze_status, job_id=job_id)

        text = self.raw_status if self.raw_status is not None else json.dumps(document)
        if str(status_file):
            status_file.parent.mkdir(parents=True, exist_ok=True)
            status_file.write_text(text, encoding="utf-8")
        return {"returncode": self.returncode, "stdout": text, "stderr": ""}


def move_effect(flags: dict[str, Any]) -> None:
    """Настоящий эффект: применить план так, как это сделал бы сайдкар.

    Файлы действительно переносятся, поэтому §14 проверяет НАБЛЮДЕНИЕ, а не
    заглушку, которая согласилась бы с чем угодно.
    """
    review = Path(flags["--review-file"])
    plan = json.loads(review.read_text(encoding="utf-8"))
    for entry in plan.get("entries", []):
        source = Path(entry["filePath"])
        if not source.exists():
            continue
        destination = _destination_for(entry, plan)
        if destination is None:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))


def _destination_for(entry: dict[str, Any], plan: dict[str, Any]) -> Path | None:
    source = Path(entry["filePath"])
    name = entry.get("suggestedName") or entry.get("fileName") or source.name
    if entry.get("renameOnly"):
        return source.parent / name
    roots = plan.get("paths") or []
    root = Path(roots[0]) if roots else source.parent
    folder = root
    for part in (entry.get("category"), entry.get("subcategory")):
        if part:
            folder = folder / str(part)
    return folder / name


def make_executable(tmp_path: Path, name: str = "aifilesorter") -> Path:
    """Файл, который discovery примет за исполняемый сайдкар.

    Он не запускается: процесс подменён FakeSidecar'ом. Нужен, чтобы preflight
    не отказал по BINARY_NOT_INSTALLED раньше, чем тест дойдёт до предмета.
    """
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_discovery(executable: Path):
    from bcc.file_intelligence.discovery import Discovery, pinned_sha
    from bcc.file_intelligence.models import BinaryStatus, VERSION_UNVERIFIED
    return Discovery(
        status=BinaryStatus.AVAILABLE,
        resolved_executable=str(executable),
        binary_version=VERSION_UNVERIFIED,
        pinned_upstream_sha_expected=pinned_sha(),
        protocol_version=1,
        version_verified=False,
    )


def local_config(tmp_path: Path, value: str = "Local_7b_Gemma") -> Path:
    """INI сайдкара с заданным LLMChoice (Settings.cpp:480)."""
    path = tmp_path / "ai-file-sorter.ini"
    path.write_text(f"[Settings]\nLLMChoice={value}\n", encoding="utf-8")
    return path


def make_corpus(folder: Path, count: int = 3) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for index in range(count):
        path = folder / f"file{index}.pdf"
        path.write_text(f"contents of file {index}\n" * 8, encoding="utf-8")
        files.append(path)
    return files
