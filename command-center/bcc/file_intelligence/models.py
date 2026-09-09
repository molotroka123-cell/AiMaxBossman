"""Типы File Intelligence: статусы, отказы, план, квитанция.

Всё, что пересекает границу с сайдкаром или с владельцем, имеет здесь ИМЯ.
Строки-на-месте — то, из чего вырастает «процесс вернул 0, значит готово»:
неназванный исход невозможно ни проверить тестом, ни показать владельцу.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Operation(str, Enum):
    """Ровно то, что понимает upstream. Ничего сверх контракта."""
    CATEGORIZE = "categorize"
    RENAME = "rename"
    CATEGORIZE_AND_RENAME = "categorize-and-rename"


class UpstreamStatus(str, Enum):
    """Состояния, которые СЕЙЧАС объявляет upstream (HeadlessStatusJson.cpp).

    UNKNOWN — не «прочее», а отказ: незнакомое состояние трактуется как
    неуспех (fail-closed), а не подгоняется под ближайшее знакомое. Когда
    upstream добавит состояние, оно приедет сюда как UNKNOWN и будет видно,
    вместо того чтобы тихо стать «completed».
    """
    RUNNING = "running"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: Any) -> "UpstreamStatus":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN


class JobState(str, Enum):
    """Состояние работы у BOSSMAN. Не то же самое, что состояние процесса.

    Разница и есть предмет: PROCESS_EXIT_0 != VERIFIED_EFFECT. Сайдкар может
    сказать `completed`, а Bossman — VERIFICATION_FAILED, если пост-состояние
    файловой системы с этим не согласилось.
    """
    QUEUED = "QUEUED"
    WAITING = "WAITING"                  # §10: рантайм занят другой работой
    ANALYZING = "ANALYZING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    APPLYING = "APPLYING"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    AMBIGUOUS = "AMBIGUOUS"              # §22: эффект мог произойти, исход неизвестен
    DENIED = "DENIED"
    STALE = "STALE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


#: Исходы, после которых работа не возобновляется сама.
TERMINAL_STATES = frozenset({
    JobState.VERIFIED, JobState.VERIFICATION_FAILED, JobState.DENIED,
    JobState.STALE, JobState.CANCELLED, JobState.FAILED,
})


class Refusal(str, Enum):
    """Причины отказа. Владельцу показывается ИМЕННО причина, а не «ошибка»."""
    FEATURE_DISABLED = "FEATURE_DISABLED"
    BINARY_NOT_INSTALLED = "BINARY_NOT_INSTALLED"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    BUSY = "BUSY"

    # §8/§29 — область действия
    PATH_OUTSIDE_AUTHORIZED_ROOTS = "PATH_OUTSIDE_AUTHORIZED_ROOTS"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    SYMLINK_ESCAPE = "SYMLINK_ESCAPE"
    PROTECTED_REPOSITORY = "PROTECTED_REPOSITORY"
    PROTECTED_BOSSMAN_STATE = "PROTECTED_BOSSMAN_STATE"
    PROTECTED_SYSTEM_PATH = "PROTECTED_SYSTEM_PATH"
    PROTECTED_SECRETS_PATH = "PROTECTED_SECRETS_PATH"
    PATH_DOES_NOT_EXIST = "PATH_DOES_NOT_EXIST"

    # §9 — предел headless-контракта
    MULTIPLE_PARENT_FOLDERS = "MULTIPLE_PARENT_FOLDERS"

    # §12 — план устарел
    STALE_REVIEW_PLAN = "STALE_REVIEW_PLAN"

    # §13 — назначение
    DESTINATION_OUTSIDE_ROOTS = "DESTINATION_OUTSIDE_ROOTS"
    DESTINATION_COLLISION = "DESTINATION_COLLISION"
    DUPLICATE_PLANNED_DESTINATION = "DUPLICATE_PLANNED_DESTINATION"
    RESERVED_FILENAME = "RESERVED_FILENAME"
    INVALID_FILENAME = "INVALID_FILENAME"
    PATH_TOO_LONG = "PATH_TOO_LONG"
    OVERWRITE_NOT_PERMITTED = "OVERWRITE_NOT_PERMITTED"

    # §16 — приватность
    REMOTE_BACKEND_NOT_APPROVED = "REMOTE_BACKEND_NOT_APPROVED"
    BACKEND_UNKNOWN = "BACKEND_UNKNOWN"

    # §6/§21 — попытка расширить полномочия через аргументы
    AUTO_APPLY_FORBIDDEN = "AUTO_APPLY_FORBIDDEN"
    UNSUPPORTED_ARGUMENT = "UNSUPPORTED_ARGUMENT"

    # §11 — целостность плана
    REVIEW_PLAN_MALFORMED = "REVIEW_PLAN_MALFORMED"
    REVIEW_PLAN_DIGEST_MISMATCH = "REVIEW_PLAN_DIGEST_MISMATCH"
    REVIEW_PLAN_SCHEMA_UNSUPPORTED = "REVIEW_PLAN_SCHEMA_UNSUPPORTED"

    # §14 — пост-состояние
    POST_STATE_MISMATCH = "POST_STATE_MISMATCH"

    # §22 — рестарт
    AMBIGUOUS_APPLY_NEEDS_RECONCILIATION = "AMBIGUOUS_APPLY_NEEDS_RECONCILIATION"
    ALREADY_APPLIED = "ALREADY_APPLIED"

    OWNER_STOPPED = "OWNER_STOPPED"
    TIMEOUT = "TIMEOUT"


class Denied(Exception):
    """Отказ с названной причиной. Несёт код, а не только текст."""

    def __init__(self, refusal: Refusal, detail: str = "", **context: Any):
        self.refusal = refusal
        self.detail = detail
        self.context = context
        super().__init__(f"{refusal.value}: {detail}" if detail else refusal.value)

    def as_dict(self) -> dict[str, Any]:
        return {"refused": self.refusal.value, "detail": self.detail, **self.context}


class BackendMode(str, Enum):
    """§16. UNKNOWN — это отказ, а не третий нормальный режим.

    Локальность НЕ выводится из того, что запустился локальный исполняемый
    файл: локальный процесс с удалённым эндпоинтом — ровно тот случай, ради
    которого правило существует.
    """
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"
    UNKNOWN = "UNKNOWN"


class BinaryStatus(str, Enum):
    """§18 — что доктор говорит о сайдкаре."""
    AVAILABLE = "AVAILABLE"
    NOT_INSTALLED = "NOT_INSTALLED"
    WRONG_VERSION = "WRONG_VERSION"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    BUSY = "BUSY"


#: §18. Версия сборки, не доказавшая происхождение, называется так, а не
#: «пиннированной»: манифест пиннирован, а бинарь — отдельный вопрос.
VERSION_UNVERIFIED = "VERSION_UNVERIFIED"

#: §15. У upstream на закреплённом SHA нет headless-контракта отмены: в
#: HeadlessAnalysisCommand.cpp `undo_dir` только передаётся в apply-опции, флага
#: `--undo` не существует. Заявлять undo в API значило бы обещать операцию,
#: которой нет.
UNDO_HEADLESS = "UNAVAILABLE"


@dataclass(frozen=True)
class SourceIdentity:
    """§11 — личность файла-источника ДО эффекта.

    Хеш содержимого здесь не для дедупликации, а для доказательства: без него
    «файл на месте» нельзя отличить от «файл заменён другим файлом того же
    размера».
    """
    source_path: str
    canonical_source_path: str
    size: int
    mtime_ns: int
    content_sha256: str
    file_identity: str = ""          # st_dev:st_ino там, где переносимо
    planned_destination: str = ""
    planned_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class PlanEntry:
    """Одна строка плана: что upstream предложил и что Bossman из этого принял."""
    file_path: str
    file_name: str
    entry_type: str
    category: str = ""
    subcategory: str = ""
    suggested_name: str = ""
    rename_only: bool = False
    destination: str = ""
    selected: bool = False
    refusal: Optional[str] = None
    identity: Optional[SourceIdentity] = None

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "identity"}
        out["identity"] = self.identity.as_dict() if self.identity else None
        return out


@dataclass
class ReviewEnvelope:
    """§11 — план upstream, обёрнутый метаданными Bossman.

    Обёртка существует потому, что JSON, который написал сайдкар, не становится
    доверенным оттого, что его написал сайдкар. Дайджест привязывает решение
    владельца к КОНКРЕТНОМУ содержимому файла плана.
    """
    job_id: str
    bossman_run_id: str
    upstream_sha: str
    source_roots: list[str]
    review_file_digest: str
    created_at: str
    owner_scope: list[str]
    operation: str
    backend_mode: str
    entries: list[PlanEntry] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "entries"}
        out["entries"] = [e.as_dict() for e in self.entries]
        return out


@dataclass
class AppliedEffect:
    """§14 — что Bossman НАБЛЮДАЛ сам, а не что ему сообщил процесс."""
    source_path: str
    destination_path: str
    source_absent: bool
    destination_exists: bool
    content_sha256_matches: bool
    verified: bool
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Receipt:
    """Квитанция эффекта. Выдаётся ТОЛЬКО после независимого наблюдения."""
    job_id: str
    state: str
    applied: list[AppliedEffect] = field(default_factory=list)
    unexpected_mutations: list[str] = field(default_factory=list)
    refusal: Optional[str] = None
    detail: str = ""

    @property
    def effect_count(self) -> int:
        return sum(1 for e in self.applied if e.verified)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state,
            "effect_count": self.effect_count,
            "applied": [e.as_dict() for e in self.applied],
            "unexpected_mutations": list(self.unexpected_mutations),
            "refusal": self.refusal,
            "detail": self.detail,
        }
