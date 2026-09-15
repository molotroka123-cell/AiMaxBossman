"""Одна точка правды об OpenRouter: КАКОЙ провайдер и ОТКУДА его ключ.

Аудит-11 (OR-003) нашёл три имени одной переменной в трёх модулях: владелец
вставлял ключ «как подсказывает документация» и получал либо провайдера без
плагина, либо плагин без провайдера. Здесь имена сведены в одно каноническое с
чтением старых (ломать чужие установки нельзя), а расхождение значений не
замалчивается: о нём говорят и API, и `bossman doctor`.

Провайдер опознаётся указателем в settings, а НЕ подстрокой в base_url:
подстрока делает «провайдером OpenRouter» любой прокси, в адресе которого
случайно встретилось это слово. Указатель ставится один раз при создании; для
установок, где провайдер уже создан старым bootstrap'ом, есть разовая привязка
по ТОЧНОМУ нормализованному адресу — это сравнение целиком, а не поиск подстроки.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import sqlalchemy as sa

from ..db import providers as providers_t, settings_kv
from .openrouter_ext import DEFAULT_BASE, normalize_base_url

# Каноническое имя — то, которое уже читают Gateway, плагин и video_factory;
# исторический BOSSMAN_-вариант из README продолжает работать, но нигде не
# записывается. Совместимость важнее красоты: у владельца переменная уже задана.
ENV_API_KEY = "OPENROUTER_API_KEY"
LEGACY_ENV_API_KEYS = ("BOSSMAN_OPENROUTER_API_KEY",)
PROVIDER_POINTER = "openrouter.provider_id"
PROVIDER_NAME = "OpenRouter"
PROVIDER_KIND = "openai_compat"


@dataclass(slots=True)
class Credential:
    """Ключ + откуда он взят + с чем он расходится.

    Пустой ключ и «ключей несколько, и они разные» — разные состояния: второе
    владелец обязан увидеть, иначе он правит не ту переменную.
    """
    key: str | None = None
    source: str = "none"                    # vault | env:<ИМЯ> | none
    conflicts: list[str] = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.key)

    @property
    def conflict_message(self) -> str | None:
        if not self.conflicts:
            return None
        return ("ключ OpenRouter задан в нескольких местах с разными значениями: "
                + ", ".join(self.conflicts)
                + f"; используется {self.source}")


def env_credential() -> Credential:
    """Ключ из окружения: каноническое имя сильнее устаревших."""
    values: list[tuple[str, str]] = []
    for name in (ENV_API_KEY, *LEGACY_ENV_API_KEYS):
        value = (os.environ.get(name) or "").strip()
        if value:
            values.append((name, value))
    if not values:
        return Credential()
    name, value = values[0]
    distinct = {v for _, v in values}
    conflicts = [n for n, _ in values] if len(distinct) > 1 else []
    return Credential(value, f"env:{name}", conflicts)


async def _pointer(db, vault) -> int | None:
    async with db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == PROVIDER_POINTER))).first()
    if row is None:
        return None
    raw = vault.decrypt(row._mapping["value_enc"])
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _set_pointer(db, vault, provider_id: int) -> None:
    async with db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == PROVIDER_POINTER))
        await s.execute(sa.insert(settings_kv).values(
            key=PROVIDER_POINTER, value_enc=vault.encrypt(str(provider_id))))
        await s.commit()


async def provider_row(db, vault) -> dict | None:
    """Строка провайдера OpenRouter или None. Указатель, затем точный адрес."""
    pid = await _pointer(db, vault)
    async with db.session() as s:
        if pid is not None:
            row = (await s.execute(sa.select(providers_t)
                                   .where(providers_t.c.id == pid))).first()
            if row is not None:
                return dict(row._mapping)
        rows = (await s.execute(sa.select(providers_t)
                                .order_by(providers_t.c.id))).fetchall()
    for row in rows:                     # разовая привязка старых установок
        data = dict(row._mapping)
        if normalize_base_url(data.get("base_url") or "") == DEFAULT_BASE:
            await _set_pointer(db, vault, int(data["id"]))
            return data
    return None


async def remember_provider(db, vault, provider_id: int) -> None:
    """Запомнить, какой провайдер считается OpenRouter'ом этой установки."""
    await _set_pointer(db, vault, provider_id)


async def resolve(db, vault) -> Credential:
    """Ключ для обращения к OpenRouter: сначала хранилище, потом окружение.

    Порядок не произволен: в vault лежит то, что владелец ввёл В ЭТОЙ установке
    руками, а переменная окружения — общесистемная и может остаться от другой.
    Ключ никуда не копируется: это чтение, а не синхронизация хранилищ.
    """
    env = env_credential()
    row = await provider_row(db, vault)
    stored = vault.decrypt(row.get("api_key_enc")) if row else None
    if stored:
        conflicts = list(env.conflicts)
        if env.key and env.key != stored:
            conflicts = ["vault", *(c for c in (env.source,) if c)]
        return Credential(stored, "vault", conflicts)
    return env
