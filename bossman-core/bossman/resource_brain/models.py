"""Модели данных Resource Brain (Этап 4).

Ключевой инвариант коробки Ryzen AI Max 395 — ЕДИНАЯ (unified) память 128 ГБ:
CPU-RAM и VRAM живут в ОДНОМ физическом пуле. Наивный снимок, который держит и
`ram_total`, и `gpu_memory_total` как независимые числа, провоцирует
двойной учёт (сложили CPU-RAM + VRAM → «увидели» 256 ГБ там, где их 128).

Поэтому здесь VRAM моделируется НЕ как отдельный пул, а как ПРЕТЕНЗИЯ
(`gpu_memory_used`) против единственного пула `ram_total`. `pool_total` всегда
равен `ram_total` и НИКОГДА не суммируется с `gpu_memory_total`
(последнее — чисто справочная величина ёмкости адаптера). Доступную для
admission память считает `unified_available`: она вычитает VRAM-претензию из
одного пула, а не прибавляет её.

Все размеры — в байтах (как отдаёт probe), но модель безразмерна: тесты
подставляют условные единицы (например, ram_total=1000).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


# --- уровни давления по единому пулу RAM ------------------------------------

class PressureLevel(str, Enum):
    """Дискретный уровень давления памяти, производный от ram_pressure [0..1].

    Пороговые значения подобраны так, чтобы NOMINAL/ELEVATED были рабочей зоной,
    HIGH — предупреждением (пора выгружать холодные модели), CRITICAL — отказом
    в приёме тяжёлых задач. Границы включаются в верхний уровень (>=)."""

    NOMINAL = "nominal"      # p < 0.60 — свободно
    ELEVATED = "elevated"    # 0.60 <= p < 0.80 — заметная нагрузка
    HIGH = "high"            # 0.80 <= p < 0.92 — пора освобождать
    CRITICAL = "critical"    # p >= 0.92 — на грани OOM

    @classmethod
    def from_pressure(cls, pressure: float) -> "PressureLevel":
        if pressure >= 0.92:
            return cls.CRITICAL
        if pressure >= 0.80:
            return cls.HIGH
        if pressure >= 0.60:
            return cls.ELEVATED
        return cls.NOMINAL


# --- снимок ресурсов --------------------------------------------------------

@dataclass(slots=True)
class ResourceSnapshot:
    """Мгновенный снимок ресурсов хоста.

    Позиционный контракт (не менять — на нём держатся приёмочные тесты):
    ``ResourceSnapshot(ram_total, ram_available, disk_total, disk_free, ...)``.

    Остальные поля — только по имени. `gpu_memory_used` — VRAM-претензия против
    ЕДИНОГО пула `ram_total`; `gpu_memory_total` — справочная ёмкость адаптера,
    в расчёте admission НЕ участвует и НЕ прибавляется к пулу.
    """

    ram_total: int
    ram_available: int
    disk_total: int
    disk_free: int
    cpu_percent: float = 0.0
    gpu_memory_used: int | None = None       # претензия VRAM к единому пулу
    gpu_memory_total: int | None = None       # справочно: ёмкость адаптера
    unified: bool = True                       # единая память (Ryzen AI Max)
    model_resident: tuple[str, ...] = ()       # какие модели уже подняты
    probe: str = "unknown"                     # имя адаптера-пробы
    ts: float = 0.0

    @property
    def pool_total(self) -> int:
        """Полная ёмкость ЕДИНОГО пула. Всегда `ram_total` — VRAM не прибавляем."""
        return self.ram_total

    @property
    def unified_available(self) -> int:
        """Доступная для приёма память единого пула.

        VRAM-претензия вычитается из одного пула, а не считается отдельным
        ресурсом. Если проба сообщила `gpu_memory_used`, доступное не может
        превышать `pool_total - gpu_memory_used` (VRAM «съедает» тот же пул).
        На CPU-only коробке (gpu_memory_used=None) равно `ram_available`.
        """
        avail = self.ram_available
        if self.gpu_memory_used:
            avail = min(avail, self.ram_total - self.gpu_memory_used)
        return max(0, avail)

    @property
    def ram_pressure(self) -> float:
        """Давление на единый пул в [0..1] с учётом VRAM-претензии."""
        if not self.ram_total:
            return 0.0
        return 1.0 - (self.unified_available / self.ram_total)

    @property
    def pressure_level(self) -> PressureLevel:
        return PressureLevel.from_pressure(self.ram_pressure)

    def to_event(self) -> dict[str, object]:
        """Безопасный (без секретов — только числа) payload для шины событий."""
        return {
            "probe": self.probe,
            "unified": self.unified,
            "ram_total": self.ram_total,
            "ram_available": self.ram_available,
            "unified_available": self.unified_available,
            "disk_total": self.disk_total,
            "disk_free": self.disk_free,
            "cpu_percent": self.cpu_percent,
            "gpu_memory_used": self.gpu_memory_used,
            "gpu_memory_total": self.gpu_memory_total,
            "ram_pressure": round(self.ram_pressure, 4),
            "pressure_level": self.pressure_level.value,
            "model_resident": list(self.model_resident),
            "ts": self.ts,
        }


# --- заявка на нагрузку -----------------------------------------------------

WorkloadKind = Literal["llm", "embedding", "rerank", "video", "training", "sandbox", "other"]


@dataclass(slots=True)
class WorkloadRequest:
    """Оценка стоимости задачи ДО её запуска. `estimated_ram`/`estimated_disk` —
    в тех же единицах, что и снимок (байты в проде)."""

    kind: WorkloadKind = "other"
    estimated_ram: int = 0
    estimated_disk: int = 0
    priority: int = 50
    model: str | None = None


# --- решение о приёме -------------------------------------------------------

@dataclass(slots=True)
class AdmissionDecision:
    """Итог статeless-проверки `admit()`. `pressure` — спроецированное давление
    ПОСЛЕ гипотетического приёма заявки."""

    allowed: bool
    reason: str
    pressure: float
    suggested_model: str | None = None


# --- аренда ресурса (ядро исправления P0-гонки OOM) -------------------------

@dataclass(slots=True)
class ResourceLease:
    """Удержанная бронь части единого пула.

    Существование lease в реестре означает, что RAM/диск ЗАРЕЗЕРВИРОВАНЫ — даже
    если держатель ещё не успел их аллоцировать. Именно это закрывает гонку:
    параллельные `acquire()` против ОДНОГО снимка видят бронь друг друга.

    `ttl` защищает от «зависшей» ёмкости: упавший держатель не должен держать
    бронь вечно — по истечении ttl sweep её освобождает. `created_at` — по часам
    реестра (по умолчанию монотонные), `cid` — бандл correlation держателя.
    """

    id: str
    kind: str
    ram: int
    disk: int
    ttl: float
    created_at: float
    cid: dict[str, str] = field(default_factory=dict)

    def age(self, now: float) -> float:
        return max(0.0, now - self.created_at)

    def remaining(self, now: float) -> float:
        return max(0.0, self.ttl - self.age(now))

    def expired(self, now: float) -> bool:
        return self.ttl > 0 and (now - self.created_at) >= self.ttl

    def to_public(self) -> dict[str, object]:
        """Только безопасные поля. cid несёт request/task/run-id — не секреты."""
        return {
            "id": self.id,
            "kind": self.kind,
            "ram": self.ram,
            "disk": self.disk,
            "ttl": self.ttl,
            "cid": dict(self.cid),
        }


# --- резидентность моделей (переиспользуется роутером gateway) ---------------

@dataclass
class ModelResidency:
    """Учёт поднятых («резидентных») моделей и стоимости холодного старта.

    Нужен скореру `rank_models`: резидентная модель дешевле (не платим за
    загрузку весов), поэтому при прочих равных её ранг выше. `cold_start_cost` —
    оценка секунд на подъём модели, если она НЕ резидентна.
    """

    resident: set[str] = field(default_factory=set)
    cold_start_cost: dict[str, float] = field(default_factory=dict)

    def mark_resident(self, model_id: str) -> None:
        self.resident.add(model_id)

    def evict(self, model_id: str) -> None:
        self.resident.discard(model_id)

    def is_resident(self, model_id: str) -> bool:
        return model_id in self.resident

    def as_tuple(self) -> tuple[str, ...]:
        return tuple(sorted(self.resident))

    def cost(self, model_id: str) -> float:
        """0, если модель уже поднята; иначе оценка холодного старта (по
        умолчанию 0 — неизвестная модель не штрафуется сверх непринадлежности к
        резиденту, чтобы скорер оставался монотонным)."""
        if model_id in self.resident:
            return 0.0
        return float(self.cold_start_cost.get(model_id, 0.0))
