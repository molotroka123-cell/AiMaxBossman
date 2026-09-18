"""Откат не имеет права молча открыть базу, которую писала более новая сборка.

Почему это важно именно сейчас. `KNOWN_GOOD_SHAS.json` прямо называет
обновление и откат непроверенными, а база до сих пор не несла НИКАКОЙ отметки
версии: ни `PRAGMA user_version`, ни строки схемы. Миграции только вперёд и
идемпотентны, поэтому откат выглядел безопасным ровно до первой неаддитивной
миграции — и узнал бы об этом владелец по своим пропавшим данным.

Отметка поколения не чинит прошлое: сборки, выпущенные ДО неё, проверять
нечем, и их защищает только резервная копия. Она защищает будущее: начиная с
этой сборки откат на неё же обязан упереться в названный отказ, а не в
догадку.
"""
import pytest
import sqlalchemy as sa
from bcc.db import Database, SCHEMA_GENERATION, DatabaseFromNewerBuild


def _user_version(path):
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            return conn.execute(sa.text("PRAGMA user_version")).scalar()
    finally:
        engine.dispose()


def _stamp(path, value):
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(f"PRAGMA user_version = {int(value)}"))
            conn.commit()
    finally:
        engine.dispose()


async def _open(path):
    db = Database(f"sqlite+aiosqlite:///{path}")
    try:
        await db.create_all()
    finally:
        await db.engine.dispose()


async def test_a_fresh_database_is_stamped_with_this_generation(tmp_path):
    """Своя база получает отметку — иначе завтра её нечем будет узнать."""
    path = tmp_path / "fresh.sqlite"
    await _open(path)
    assert _user_version(path) == SCHEMA_GENERATION


async def test_the_same_generation_opens_without_complaint(tmp_path):
    """Обычный повторный запуск не должен превращаться в событие."""
    path = tmp_path / "same.sqlite"
    await _open(path)
    await _open(path)
    assert _user_version(path) == SCHEMA_GENERATION


async def test_a_database_from_a_newer_build_is_refused_by_name(tmp_path):
    """Главная проверка: новее — значит отказ, а не попытка угадать.

    Без этой защиты старая сборка открывала такую базу молча и работала с
    таблицами, которых не понимает.
    """
    path = tmp_path / "newer.sqlite"
    await _open(path)
    _stamp(path, SCHEMA_GENERATION + 1)
    with pytest.raises(DatabaseFromNewerBuild) as refused:
        await _open(path)
    text = str(refused.value)
    assert str(SCHEMA_GENERATION + 1) in text and str(SCHEMA_GENERATION) in text
    assert "резерв" in text.lower(), "отказ обязан сказать владельцу, что делать"
    assert _user_version(path) == SCHEMA_GENERATION + 1, "чужую отметку не перетирать"


async def test_a_database_without_a_stamp_opens_and_gets_one(tmp_path):
    """База прежних сборок — законный вход вперёд, а не отказ."""
    path = tmp_path / "old.sqlite"
    await _open(path)
    _stamp(path, 0)
    await _open(path)
    assert _user_version(path) == SCHEMA_GENERATION
