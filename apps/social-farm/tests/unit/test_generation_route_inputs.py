"""Неизмеренный факт закрывает путь, а не открывает его.

Проверки здесь — про одно решение: маршрутизатор больше не верит числу просто
потому, что число ему передали. У наблюдения есть источник и возраст, и путь,
чья пригодность держится на просроченном или отсутствующем наблюдении, не
предлагается вовсе.
"""
from __future__ import annotations

import time

import pytest

from social_farm.generation.content_buffer import BufferHealth
from social_farm.generation.higgsfield_browser_contracts import MediaKind
from social_farm.generation.media_router import GenerationRoute
from social_farm.generation.route_inputs import (BrowserReadiness,
                                                 FALLBACK_EVERGREEN,
                                                 FALLBACK_WAIT_FOR_OWNER,
                                                 Freshness, GenerationObservations,
                                                 Health, Reading, RouteRequirements,
                                                 plan_generation_route)

NOW = 1_800_000_000.0


def measured(mb: float, *, age: float = 0.0, max_age: float = 120.0) -> Reading:
    return Reading(value=mb, source="observer:process",
                   observed_at_epoch_s=NOW - age, max_age_s=max_age)


def need(**kwargs) -> RouteRequirements:
    kwargs.setdefault("media_kind", MediaKind.VIDEO)
    return RouteRequirements(**kwargs)


def everything_healthy(**overrides) -> GenerationObservations:
    base = dict(free_memory_mb=measured(32_768.0),
                local_generator=Health.HEALTHY,
                cloud_provider=Health.HEALTHY,
                browser=BrowserReadiness.READY,
                buffer=BufferHealth.READY)
    base.update(overrides)
    return GenerationObservations(**base)


# ------------------------------------------------------------------ свежесть

def test_a_reading_without_a_timestamp_is_not_a_measurement():
    assert Reading(value=64_000.0).measured is False
    assert Reading(value=64_000.0).freshness(NOW) is Freshness.MISSING


def test_a_stale_reading_yields_no_value_at_all():
    """Просрочка — это не «чуть менее точно», это «больше не про этот мир»."""
    reading = measured(32_768.0, age=300.0, max_age=120.0)
    assert reading.freshness(NOW) is Freshness.STALE
    assert reading.fresh_value(NOW) is None


# ------------------------------------------- локальная модель и память

def test_an_unmeasured_memory_never_opens_the_local_route():
    """Отрицательный контроль OOM: без измерения локальный путь закрыт."""
    plan = plan_generation_route(
        everything_healthy(free_memory_mb=Reading.unmeasured("наблюдателя нет")),
        need(local_model_mb=8_192.0), now=NOW)
    assert GenerationRoute.LOCAL not in plan.chain
    assert "free_memory_mb" in plan.unmeasured
    assert any("local:" in reason for reason in plan.rejected)


def test_a_stale_memory_reading_closes_the_local_route_too():
    plan = plan_generation_route(
        everything_healthy(free_memory_mb=measured(65_536.0, age=600.0)),
        need(local_model_mb=8_192.0), now=NOW)
    assert GenerationRoute.LOCAL not in plan.chain
    assert "free_memory_mb" in plan.unmeasured


def test_a_model_that_does_not_fit_the_measured_memory_is_refused():
    """8 ГБ свободно, модель просит 70 — путь не предлагается."""
    plan = plan_generation_route(
        everything_healthy(free_memory_mb=measured(8_192.0)),
        need(local_model_mb=71_680.0), now=NOW)
    assert GenerationRoute.LOCAL not in plan.chain
    assert "local:memory" in plan.rejected


def test_a_model_that_fits_with_headroom_is_offered_first():
    """Локальный путь бесплатен и не зависит от сети — при прочих равных он первый."""
    plan = plan_generation_route(
        everything_healthy(free_memory_mb=measured(32_768.0)),
        need(local_model_mb=4_096.0, max_cost_usd=0.05), now=NOW)
    assert plan.chain[0] is GenerationRoute.LOCAL


def test_headroom_is_left_to_the_machine_not_handed_to_the_model():
    """Модель, влезающая впритык, — это вставшая машина владельца."""
    exactly = 10_000.0
    plan = plan_generation_route(
        everything_healthy(free_memory_mb=measured(exactly)),
        need(local_model_mb=exactly), now=NOW)
    assert GenerationRoute.LOCAL not in plan.chain


# ------------------------------------------------------------------ цепочка

def test_the_chain_is_local_then_cloud_then_browser():
    plan = plan_generation_route(everything_healthy(),
                                 need(local_model_mb=2_048.0), now=NOW)
    assert plan.chain == (GenerationRoute.LOCAL, GenerationRoute.CHEAP_CLOUD,
                          GenerationRoute.HIGGSFIELD_BROWSER)


def test_a_dead_local_generator_hands_the_work_to_the_cloud():
    plan = plan_generation_route(
        everything_healthy(local_generator=Health.DOWN),
        need(local_model_mb=2_048.0), now=NOW)
    assert plan.chain[0] is GenerationRoute.CHEAP_CLOUD
    assert GenerationRoute.LOCAL not in plan.chain


def test_a_dead_cloud_leaves_the_browser(monkeypatch):
    plan = plan_generation_route(
        everything_healthy(local_generator=Health.DOWN, cloud_provider=Health.DOWN),
        need(local_model_mb=2_048.0), now=NOW)
    assert plan.chain == (GenerationRoute.HIGGSFIELD_BROWSER,)


def test_a_browser_waiting_for_the_owner_is_not_a_route():
    plan = plan_generation_route(
        everything_healthy(local_generator=Health.DOWN, cloud_provider=Health.DOWN,
                           browser=BrowserReadiness.NEEDS_OWNER),
        need(local_model_mb=2_048.0), now=NOW)
    assert plan.chain == ()
    assert plan.owner_action_required


def test_an_unknown_browser_state_is_not_treated_as_ready():
    plan = plan_generation_route(
        everything_healthy(local_generator=Health.DOWN, cloud_provider=Health.DOWN,
                           browser=BrowserReadiness.UNKNOWN),
        need(local_model_mb=2_048.0), now=NOW)
    assert plan.chain == ()
    assert "browser" in plan.unmeasured


# ------------------------------------------------------------------ запасное

def test_an_empty_buffer_with_no_route_falls_back_to_approved_evergreen():
    """Эфир не оставляют пустым и не импровизируют в нём внешним действием."""
    plan = plan_generation_route(
        GenerationObservations(free_memory_mb=Reading.unmeasured("нет"),
                               local_generator=Health.DOWN,
                               cloud_provider=Health.DOWN,
                               browser=BrowserReadiness.RATE_LIMITED,
                               buffer=BufferHealth.EMPTY),
        need(), now=NOW)
    assert not plan.has_route
    assert plan.fallback == FALLBACK_EVERGREEN


def test_a_blocked_browser_asks_for_the_owner_while_the_buffer_holds():
    plan = plan_generation_route(
        GenerationObservations(free_memory_mb=measured(1_024.0),
                               local_generator=Health.DOWN,
                               cloud_provider=Health.DOWN,
                               browser=BrowserReadiness.BLOCKED,
                               buffer=BufferHealth.READY),
        need(), now=NOW)
    assert plan.fallback == FALLBACK_WAIT_FOR_OWNER
    assert plan.owner_action_required


# ------------------------------------------------------ разбор чужого словаря

def test_a_foreign_payload_can_only_close_routes_never_open_them():
    """Расхождение контрактов между сервисами не выдаёт разрешений."""
    observations = GenerationObservations.from_payload({
        "free_memory_mb": {"value": "много", "source": "чужой сервис"},
        "local_generator": "ПРЕКРАСНО",
        "cloud_provider": None,
        "browser": "definitely_ready",
        "buffer": 42,
    })
    assert observations.free_memory_mb.measured is False
    assert observations.local_generator is Health.UNKNOWN
    assert observations.browser is BrowserReadiness.UNKNOWN
    assert observations.buffer is None

    plan = plan_generation_route(observations, need(local_model_mb=1.0), now=NOW)
    assert plan.chain == ()


def test_a_reading_without_a_timestamp_in_the_payload_is_refused():
    observations = GenerationObservations.from_payload(
        {"free_memory_mb": {"value": 65_536.0, "source": "кто-то сказал"}})
    assert observations.free_memory_mb.measured is False
    assert "без отметки времени" in observations.free_memory_mb.source


def test_a_well_formed_payload_round_trips():
    payload = {
        "free_memory_mb": {"value": 16_384.0, "source": "observer:process",
                           "observed_at_epoch_s": NOW, "max_age_s": 60.0},
        "local_generator": "healthy", "cloud_provider": "degraded",
        "browser": "ready", "buffer": "low",
    }
    observations = GenerationObservations.from_payload(payload)
    assert observations.free_memory_mb.fresh_value(NOW) == 16_384.0
    assert observations.cloud_provider is Health.DEGRADED
    assert observations.buffer is BufferHealth.LOW

    again = observations.to_dict(NOW)
    assert again["free_memory_mb"]["freshness"] == "FRESH"
    assert again["browser"] == "ready"


def test_requirements_refuse_nonsense():
    with pytest.raises(ValueError):
        RouteRequirements(media_kind=MediaKind.IMAGE, local_model_mb=-1.0)
    with pytest.raises(ValueError):
        RouteRequirements(media_kind=MediaKind.IMAGE, max_cost_usd=-0.01)


# ------------------------------------------------- качество как порог допуска

def test_a_cheap_adequate_path_beats_a_better_expensive_one():
    """Качество выше запрошенного работе не нужно, а платить за него приходится."""
    plan = plan_generation_route(everything_healthy(),
                                 need(local_model_mb=2_048.0, min_quality_score=0.5),
                                 now=NOW)
    assert plan.chain[0] is GenerationRoute.LOCAL


def test_a_scene_that_needs_more_than_local_can_give_goes_to_the_browser():
    """Так браузерный путь и запрашивается: сценой, а не предпочтением."""
    plan = plan_generation_route(everything_healthy(),
                                 need(local_model_mb=2_048.0, min_quality_score=0.8),
                                 now=NOW)
    assert plan.chain == (GenerationRoute.HIGGSFIELD_BROWSER,)
    assert "local:quality" in plan.rejected
    assert "cheap_cloud:quality" in plan.rejected


def test_a_middle_bar_leaves_cloud_before_browser():
    plan = plan_generation_route(everything_healthy(),
                                 need(local_model_mb=2_048.0, min_quality_score=0.6),
                                 now=NOW)
    assert plan.chain == (GenerationRoute.CHEAP_CLOUD,
                          GenerationRoute.HIGGSFIELD_BROWSER)


def test_latency_actually_discriminates_between_paths():
    """Без общего срока формула нормирует задержку саму на себя, и та
    перестаёт различать пути вовсе."""
    fast_only = plan_generation_route(
        everything_healthy(local_generator=Health.DOWN),
        need(local_model_mb=2_048.0, max_latency_s=60.0), now=NOW)
    assert fast_only.chain == (GenerationRoute.CHEAP_CLOUD,)
    assert "higgsfield_browser:latency" in fast_only.rejected


def test_a_budget_of_zero_keeps_only_the_free_paths():
    plan = plan_generation_route(everything_healthy(),
                                 need(local_model_mb=2_048.0, max_cost_usd=0.0),
                                 now=NOW)
    assert GenerationRoute.CHEAP_CLOUD not in plan.chain
    assert "cheap_cloud:cost" in plan.rejected
    assert GenerationRoute.LOCAL in plan.chain
