"""§4/§6/§21 — разговор с сайдкаром. argv строится из ТИПОВ, а не из строки.

Три правила, которые здесь держатся:

1. Никакой shell-строки. argv собирается списком из типизированных полей, и имя
   файла с `;` или `$(...)` внутри остаётся именем файла, а не командой. Это не
   «экранирование получше» — экранирования просто нет, потому что нет и
   интерпретатора, которому было бы что экранировать.

2. Модель не может подмешать флаг. Всё, что попадает в argv, приходит из
   перечислений и путей, уже прошедших ScopePolicy. Аргумент, начинающийся с
   `-`, не может приехать из данных: путь с таким началом передаётся после
   `--`-разделителя там, где upstream это позволяет, а операция берётся из
   Operation. Отдельно и явно: набор запрещённых флагов проверяется в
   ПОСТРОЕННОМ argv (§6), потому что проверять намерение бесполезно — проверять
   надо результат.

3. GUI не автоматизируется. У upstream есть headless-контракт, и используется
   он. Управлять Qt-окном через распознавание картинки, когда рядом лежит
   документированный протокол, — это подменить проверяемый интерфейс
   непроверяемым.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .models import Denied, Operation, Refusal, UpstreamStatus

#: §6. Ровно те написания, которыми upstream включает применение без ревью
#: (HeadlessAnalysisCommand.cpp:699-700 на закреплённом SHA). Список ведётся
#: здесь, а не в комментарии, потому что его проверяет тест.
FORBIDDEN_FLAGS = frozenset({
    "--auto-apply", "--headless-auto-apply",
    "--apply-without-review", "--headless-apply-without-review",
})

#: Флаги, которые Bossman вообще умеет произносить. Всё остальное — не «пока не
#: поддержано», а «через этот интерфейс не проходит».
ALLOWED_FLAGS = frozenset({
    "--headless", "--headless-apply", "--operation", "--path", "--review-only",
    "--review-file", "--status-file", "--job-id",
})

REVIEW_PLAN_KIND = "aifs.headlessReviewPlan"
SUPPORTED_PLAN_SCHEMA = 1
SUPPORTED_STATUS_SCHEMA = 1


def _reject_forbidden(argv: Sequence[str]) -> None:
    """§6 — проверка ПОСТРОЕННОГО argv, а не намерения.

    Смотрим и на `--flag`, и на `--flag=value`: второе — это тот же флаг, и
    пропустить его значило бы оставить в заборе калитку.
    """
    for token in argv:
        head = token.split("=", 1)[0]
        if head in FORBIDDEN_FLAGS:
            raise Denied(Refusal.AUTO_APPLY_FORBIDDEN,
                         f"{head} is unreachable from File Intelligence: review and "
                         "apply are separate owner-gated steps", flag=head)
        if token.startswith("-") and head not in ALLOWED_FLAGS:
            raise Denied(Refusal.UNSUPPORTED_ARGUMENT,
                         f"{head} is not part of the governed contract", flag=head)


def analyze_argv(executable: str | Path, *, operation: Operation, path: Path,
                 review_file: Path, status_file: Path, job_id: str) -> list[str]:
    """argv для анализа. ВСЕГДА `--review-only`, без исключений и без опции.

    `--review-only` не параметр этой функции. Параметр можно передать, а
    переданное значение можно вычислить из недоверенного входа; отсутствие
    параметра — это то, что нельзя.
    """
    if not isinstance(operation, Operation):
        raise Denied(Refusal.UNSUPPORTED_ARGUMENT,
                     "operation must come from the Operation enum, not from free text")
    argv = [
        str(executable),
        "--headless",
        "--operation", operation.value,
        "--path", str(path),
        "--review-only",
        "--review-file", str(review_file),
        "--status-file", str(status_file),
        "--job-id", _safe_job_id(job_id),
    ]
    _reject_forbidden(argv)
    return argv


def apply_argv(executable: str | Path, *, review_file: Path, status_file: Path,
               job_id: str) -> list[str]:
    """argv для применения СОХРАНЁННОГО плана.

    Применение не принимает ни путей, ни операции: оно исполняет ровно тот
    файл плана, который владелец утвердил. Возможность передать сюда путь
    означала бы, что «применить утверждённое» и «сделать что-то ещё» — один и
    тот же вызов.
    """
    argv = [
        str(executable),
        "--headless-apply",
        "--review-file", str(review_file),
        "--status-file", str(status_file),
        "--job-id", _safe_job_id(job_id),
    ]
    _reject_forbidden(argv)
    return argv


def _safe_job_id(job_id: str) -> str:
    text = str(job_id)
    if not text or len(text) > 128 or text.startswith("-"):
        raise Denied(Refusal.UNSUPPORTED_ARGUMENT, "job id is not a usable identifier")
    if not all(ch.isalnum() or ch in "-_" for ch in text):
        raise Denied(Refusal.UNSUPPORTED_ARGUMENT,
                     "job id must be alphanumeric with - and _ only")
    return text


# --------------------------------------------------------------------- разбор

def parse_status(raw: str | bytes | None) -> dict[str, Any]:
    """Разобрать status JSON сайдкара.

    Не-JSON на stdout — это не «шум, который можно пролистать»: протокол
    нарушен, и дальше нечего разбирать. Незнакомая версия схемы — то же самое.
    Оба случая называются PROTOCOL_FAILED, а не FAILED, чтобы отличать
    «сайдкар не смог сделать работу» от «сайдкар говорит на другом языке».
    """
    if raw is None or (isinstance(raw, (str, bytes)) and not str(raw).strip()):
        raise Denied(Refusal.PROTOCOL_FAILED, "sidecar produced no status document")
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    try:
        document = json.loads(text)
    except (ValueError, TypeError):
        raise Denied(Refusal.PROTOCOL_FAILED,
                     "sidecar status output is not JSON") from None
    if not isinstance(document, dict):
        raise Denied(Refusal.PROTOCOL_FAILED, "sidecar status is not an object")
    version = document.get("schemaVersion")
    if version != SUPPORTED_STATUS_SCHEMA:
        raise Denied(Refusal.PROTOCOL_FAILED,
                     f"unsupported status schemaVersion {version!r}",
                     schema_version=version)
    document["status_enum"] = UpstreamStatus.parse(document.get("status"))
    return document


def parse_review_plan(raw: str | bytes | None) -> dict[str, Any]:
    """Разобрать план ревью и проверить, что это ИМЕННО он.

    `kind` и `schemaVersion` проверяются до чтения записей: файл, оказавшийся
    не планом, не должен разбираться «насколько получится».
    """
    if raw is None or not str(raw).strip():
        raise Denied(Refusal.REVIEW_PLAN_MALFORMED, "review plan file is empty")
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    try:
        document = json.loads(text)
    except (ValueError, TypeError):
        raise Denied(Refusal.REVIEW_PLAN_MALFORMED,
                     "review plan is not JSON") from None
    if not isinstance(document, dict):
        raise Denied(Refusal.REVIEW_PLAN_MALFORMED, "review plan is not an object")
    if document.get("kind") != REVIEW_PLAN_KIND:
        raise Denied(Refusal.REVIEW_PLAN_MALFORMED,
                     f"review plan kind is {document.get('kind')!r}, expected "
                     f"{REVIEW_PLAN_KIND!r}")
    if document.get("schemaVersion") != SUPPORTED_PLAN_SCHEMA:
        raise Denied(Refusal.REVIEW_PLAN_SCHEMA_UNSUPPORTED,
                     f"unsupported review plan schemaVersion "
                     f"{document.get('schemaVersion')!r}")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise Denied(Refusal.REVIEW_PLAN_MALFORMED, "review plan has no entries array")
    return document


def is_terminal(status: UpstreamStatus) -> bool:
    """RUNNING — единственное состояние, после которого стоит ждать дальше.

    UNKNOWN сюда попадает намеренно: незнакомое состояние заканчивает ожидание
    отказом, а не крутит цикл в надежде, что оно станет знакомым.
    """
    return status is not UpstreamStatus.RUNNING
