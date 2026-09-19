"""BL-084: оценка владельца в 0 не делает платную модель бесплатной.

Задание владельца: «При free_only неизвестный или ненулевой тариф запрещает
отправку». Ноль БЕЗ авторитетного подтверждения — это неизвестный тариф, а не
бесплатный, и разница здесь стоит денег.

Воспроизведение дефекта до правки: политика
`{'free_only': True, 'prices': {'openrouter:minimax/hailuo-3-max': 0}}`
пропускала резервирование. hailuo-3-max — платная модель каталога.

Авторитетный источник — `tools/studio_models.json`, где у каждой модели есть
булев `free`. Поле цены в интерфейсе остаётся тем, чем было: ОЦЕНКОЙ верхнего
допустимого предела, а не тарифом провайдера.
"""
from __future__ import annotations

import pytest

from bcc.studio import catalog


# --- авторитетный источник --------------------------------------------------

def test_the_catalog_knows_which_models_are_paid():
    assert catalog.declared_free("openrouter:minimax/hailuo-3-max") is False


def test_the_catalog_knows_a_free_one():
    """Положительная половина: иначе «всё платное» проходило бы проверки."""
    assert catalog.declared_free("comfyui:*") is True


def test_an_unknown_model_is_not_declared_free():
    """Отсутствие записи — это «неизвестно», а не «бесплатно»."""
    assert catalog.declared_free("openrouter:никому-не-известная") is None


def test_every_openrouter_model_in_the_catalog_declares_its_tier():
    """Модель без объявленного тарифа — дыра того же класса.

    Каталог уже требует булев `free` при загрузке; здесь это закреплено со
    стороны потребителя, чтобы новая запись без тарифа не проехала молча.
    """
    for model in catalog.load()["models"]:
        if model["provider"] == "openrouter":
            assert catalog.declared_free(model["id"]) in (True, False), model["id"]


# --- правило, ради которого всё это ------------------------------------------

@pytest.mark.parametrize("model_id,owner_price,should_pass", [
    # Платная модель, владелец оценил её в ноль — ЗАПРЕТ. Это и есть BL-084.
    ("openrouter:minimax/hailuo-3-max", 0, False),
    # Та же модель с честной ненулевой оценкой — тоже запрет при free_only.
    ("openrouter:minimax/hailuo-3-max", 0.5, False),
    # Модель, объявленная бесплатной, с нулём — проходит.
    ("comfyui:*", 0, True),
])
def test_zero_only_passes_when_the_catalog_confirms_it(model_id, owner_price, should_pass):
    """Правило одной строкой, чтобы его нельзя было прочесть иначе."""
    allowed = owner_price == 0 and catalog.declared_free(model_id) is True
    assert allowed is should_pass


# --- СКВОЗНОЕ через настоящую reserve() -------------------------------------
# Проверок справочника выше НЕ ДОСТАТОЧНО: ровно на такой ошибке я попался в
# BL-091 — чистая функция мерила верно, а маршрут её ответ игнорировал.
# Ниже проверяется сам отказ резервирования.

from bcc.studio.governance import reserve, save_policy  # noqa: E402
from bcc.studio.runtime import StudioError  # noqa: E402

PAID = "openrouter:minimax/hailuo-3-max"


async def test_a_zero_owner_estimate_does_not_buy_a_paid_model(env):
    """BL-084 целиком: платная модель, владелец оценил её в 0 — ОТКАЗ.

    До правки `reserve` возвращала {'upper_bound_usd': 0, ...}, то есть
    пропускала, и первый настоящий платный вызов уходил провайдеру.
    """
    await save_policy(env.svc, {'enabled': True, 'free_only': True,
                                'cloud_budget_usd': 1, 'per_job_usd': 1,
                                'prices': {PAID: 0}})
    with pytest.raises(StudioError, match='free_only'):
        await reserve(env.svc, PAID, 1, 1, [])


async def test_the_same_model_still_works_when_free_only_is_off(env):
    """Положительная половина: правка закрыла ТОЛЬКО режим free_only.

    Без неё «всё отвергается» тоже проходило бы проверку выше, и это была бы
    молчаливая потеря способности, а не защита.
    """
    await save_policy(env.svc, {'enabled': True, 'free_only': False,
                                'cloud_budget_usd': 1, 'per_job_usd': 1,
                                'prices': {PAID: 0.1}})
    got = await reserve(env.svc, PAID, 1, 1, [])
    assert got['upper_bound_usd'] == pytest.approx(0.1)


async def test_an_unknown_model_under_free_only_is_refused(env):
    """Модели нет в каталоге — «бесплатна» не утверждается."""
    unknown = "openrouter:никому-не-известная"
    await save_policy(env.svc, {'enabled': True, 'free_only': True,
                                'cloud_budget_usd': 1, 'per_job_usd': 1,
                                'prices': {unknown: 0}})
    with pytest.raises(StudioError, match='free_only'):
        await reserve(env.svc, unknown, 1, 1, [])
