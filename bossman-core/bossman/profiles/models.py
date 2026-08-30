"""Профили доступа к чату (мульти-пользователь поверх Stage 6 device-identity).

Профиль = «аккаунт», которому владелец даёт доступ к чату на своём сервере.
Каждый профиль несёт набор ПЕРЕКЛЮЧАТЕЛЕЙ (toggles), которые реально влияют на
работу ИИ: выключенный переключатель ЗАПРЕЩАЕТ соответствующую capability на
уровне исполнителя (не косметически). По умолчанию всё чувствительное ВЫКЛЮЧЕНО
(deny-by-default): гостю нельзя ни управлять компом, ни трогать личные данные,
пока владелец явно не включит.

Никакого второго authority-движка: профиль привязывается к существующему
device-id (Stage 6), а enforcement идёт через существующие точки-чекпоинты
(computer_operator, tool-grants). Здесь — только типизированная модель и словарь.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# --- Переключатели доступа (человеко-понятные тумблеры) ---
# Значение по умолчанию — БЕЗОПАСНОЕ (False = запрещено), кроме базового чата.
TOGGLES: dict[str, bool] = {
    "computer_control": False,   # управление компьютером (десктоп/терминал) — любым способом
    "internet": False,           # интернет: браузер / HTTP
    "messaging": False,          # отправка сообщений во внешние каналы
    "filesystem_write": False,   # запись в файловую систему
    "personal_data": False,      # доступ к личным данным (персональная память)
    "cloud_llm": False,          # облачные модели (иначе только локальные)
    "code_execution": False,     # выполнение кода / dev-задач
}

# --- Capability (что просит исполнитель) → какой тумблер её разрешает ---
# Неизвестная capability отсутствует в карте → gate вернёт DENY (deny-by-default).
CAPABILITY_TOGGLE: dict[str, str] = {
    "computer.control": "computer_control",
    "computer.observe": "computer_control",
    "terminal.run": "computer_control",
    "terminal.read": "computer_control",
    "browser.control": "internet",
    "browser.read": "internet",
    "http.get": "internet",
    "channel.send": "messaging",
    "filesystem.write": "filesystem_write",
    "personal.read": "personal_data",
    "cloud.llm": "cloud_llm",
    "code.execute": "code_execution",
}


def default_toggles() -> dict[str, bool]:
    return dict(TOGGLES)


def normalize_toggles(raw: dict | None) -> dict[str, bool]:
    """Оставляем только известные тумблеры; неизвестные ключи игнорируем; отсутствующие
    берём из безопасного дефолта. Значение приводим к bool явно."""
    out = default_toggles()
    for k, v in (raw or {}).items():
        if k in TOGGLES:
            out[k] = bool(v)
    return out


@dataclass
class Profile:
    id: str
    name: str
    device_id: str | None = None        # привязка к Stage 6 device-identity
    telegram_user_id: str | None = None  # привязка к Telegram-пользователю
    enabled: bool = True
    toggles: dict[str, bool] = field(default_factory=default_toggles)
    memory_namespace: str = ""           # namespace знаний профиля (project=…)
    created_at: float = field(default_factory=lambda: 0.0)
    updated_at: float = field(default_factory=lambda: 0.0)

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_row(self) -> dict:
        return {
            "id": self.id, "name": self.name, "device_id": self.device_id,
            "telegram_user_id": self.telegram_user_id, "enabled": self.enabled,
            "toggles": dict(self.toggles), "memory_namespace": self.memory_namespace,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Profile":
        return cls(
            id=str(row["id"]), name=str(row.get("name") or row["id"]),
            device_id=row.get("device_id"), telegram_user_id=row.get("telegram_user_id"),
            enabled=bool(row.get("enabled", True)),
            toggles=normalize_toggles(row.get("toggles")),
            memory_namespace=str(row.get("memory_namespace") or ""),
            created_at=float(row.get("created_at") or 0.0),
            updated_at=float(row.get("updated_at") or 0.0),
        )


@dataclass(frozen=True)
class CapabilityDecision:
    allow: bool
    reason: str
    capability: str
    toggle: str | None = None
