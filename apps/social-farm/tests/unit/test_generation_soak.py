"""Шестичасовой прогон: тупиков ноль, и проверка человека гасит одну полосу.

Шесть настоящих часов в проверке кода — это проверка, которую никто не
запустит. Часы здесь виртуальные и детерминированные: тот же прогон, те же
числа, воспроизводимое падение.
"""
from __future__ import annotations

import pytest

from social_farm.generation.media_router import GenerationRoute
from social_farm.generation.soak import (ALERT_COALESCE_SECONDS,
                                         COOLDOWN_SECONDS,
                                         MAX_CONSECUTIVE_REJECTS, Lane, LaneState,
                                         Outcome, SoakConfig, SoakHarness,
                                         default_lanes, main)


def six_hours(**overrides) -> SoakConfig:
    base = dict(hours=6.0, tick_seconds=60.0)
    base.update(overrides)
    return SoakConfig(**base)


# ------------------------------------------------------------------ приёмка

def test_six_unattended_hours_end_with_no_deadlocks_and_a_ready_buffer():
    report = SoakHarness(config=six_hours()).run()
    assert report.metrics.deadlock_count == 0
    assert report.passed, report.failures
    assert report.metrics.accepted_outputs > 0
    assert report.final_buffer.health.value == "ready"


def test_the_report_refuses_to_invent_a_memory_measurement():
    """На виртуальных часах пик памяти никто не мерил. Правдоподобное число
    здесь было бы записью в приёмку того, чего не измеряли."""
    body = SoakHarness(config=six_hours(hours=1.0)).run().to_dict()
    assert body["metrics"]["peak_local_memory_mb"] is None
    assert "никто не мерил" in body["metrics"]["note"]


def test_the_run_is_deterministic():
    first = SoakHarness(config=six_hours(hours=2.0)).run().to_dict()
    second = SoakHarness(config=six_hours(hours=2.0)).run().to_dict()
    assert first == second


# --------------------------------------------------- проверка человека

def test_a_challenge_pauses_only_the_lane_it_happened_on():
    """Капча на браузерном пути не гасит локальную генерацию."""
    lanes = [
        Lane(GenerationRoute.LOCAL, job_seconds=180.0),
        Lane(GenerationRoute.CHEAP_CLOUD, job_seconds=90.0, cost_usd_per_job=0.01),
        Lane(GenerationRoute.HIGGSFIELD_BROWSER, job_seconds=240.0,
             script=(Outcome.HUMAN_CHALLENGE,)),
    ]
    harness = SoakHarness(config=six_hours(), lanes=lanes)
    report = harness.run()

    assert report.lanes["higgsfield_browser"] == LaneState.PAUSED_FOR_OWNER.value
    assert report.lanes["local"] == LaneState.READY.value
    assert report.lanes["cheap_cloud"] == LaneState.READY.value
    assert report.metrics.owner_interventions >= 1
    assert report.metrics.accepted_outputs > 0, "поток продолжал наполняться"
    assert report.metrics.deadlock_count == 0


def test_a_paused_lane_is_not_dispatched_to_again():
    lanes = [Lane(GenerationRoute.HIGGSFIELD_BROWSER, job_seconds=60.0,
                  script=(Outcome.HUMAN_CHALLENGE,))]
    harness = SoakHarness(config=six_hours(hours=1.0), lanes=lanes)
    harness.run()
    assert harness.metrics.generated_jobs <= 6, \
        "остановленная полоса не получает новых работ"


def test_owner_alerts_about_one_lane_are_coalesced():
    """Шесть часов одного и того же сообщения — это фон, который перестают
    читать."""
    lanes = [Lane(GenerationRoute.LOCAL, job_seconds=60.0,
                  script=(Outcome.NEEDS_OWNER_AUTH,))]
    harness = SoakHarness(config=six_hours(), lanes=lanes)
    harness.run()
    assert harness.metrics.owner_alerts <= 2, harness.alerts
    assert harness.metrics.owner_interventions >= 1


# ------------------------------------------------------------------ отказы

def test_repeated_rejects_pause_the_lane_instead_of_retrying_forever():
    """Повторять один и тот же брак — значит платить за него столько же раз."""
    lanes = [Lane(GenerationRoute.LOCAL, job_seconds=60.0,
                  script=(Outcome.REJECTED_MEDIA,))]
    harness = SoakHarness(config=six_hours(hours=1.0), lanes=lanes)
    harness.run()
    assert harness.metrics.rejected_outputs >= MAX_CONSECUTIVE_REJECTS
    # Полоса уходит в паузу и возвращается только по её истечении, поэтому
    # работ заметно меньше, чем тиков.
    assert harness.metrics.generated_jobs < harness.metrics.ticks


def test_a_rate_limit_pauses_the_lane_and_it_returns_by_itself():
    lane = Lane(GenerationRoute.LOCAL, job_seconds=60.0,
                script=(Outcome.RATE_LIMITED, Outcome.ACCEPTED))
    harness = SoakHarness(config=six_hours(hours=2.0), lanes=[lane])
    harness.run()
    assert harness.metrics.rate_limit_incidents >= 1
    assert harness.metrics.accepted_outputs >= 1, "пауза кончается сама"


def test_ui_drift_takes_the_lane_out_and_counts_the_incident():
    lanes = [
        Lane(GenerationRoute.LOCAL, job_seconds=90.0),
        Lane(GenerationRoute.HIGGSFIELD_BROWSER, job_seconds=120.0,
             script=(Outcome.UI_CHANGED,)),
    ]
    harness = SoakHarness(config=six_hours(hours=2.0), lanes=lanes)
    report = harness.run()
    assert report.metrics.ui_drift_incidents >= 1
    assert report.lanes["higgsfield_browser"] == LaneState.BROKEN_UI.value
    assert report.metrics.accepted_outputs > 0


# ------------------------------------------------------------------ эфир

def test_with_every_lane_down_the_stream_falls_back_to_approved_evergreen():
    """Эфир не оставляют пустым и не импровизируют в нём внешним действием."""
    lanes = [
        Lane(GenerationRoute.LOCAL, job_seconds=60.0, script=(Outcome.UI_CHANGED,)),
        Lane(GenerationRoute.CHEAP_CLOUD, job_seconds=60.0,
             script=(Outcome.UI_CHANGED,)),
        Lane(GenerationRoute.HIGGSFIELD_BROWSER, job_seconds=60.0,
             script=(Outcome.HUMAN_CHALLENGE,)),
    ]
    harness = SoakHarness(config=six_hours(hours=3.0), lanes=lanes)
    report = harness.run()
    assert report.metrics.evergreen_seconds > 0
    assert report.metrics.deadlock_count == 0, \
        "нечем работать — это не тупик, это запасной вариант"


def test_a_deadlock_is_counted_when_there_is_work_and_a_way_and_nothing_happens():
    """Отрицательный контроль самого важного числа приёмки.

    Если счётчик тупиков нельзя заставить вырасти, его нулевое значение
    ничего не доказывает.
    """
    harness = SoakHarness(config=six_hours(hours=1.0),
                          lanes=[Lane(GenerationRoute.LOCAL, job_seconds=60.0)])
    # Полоса работоспособна, буфер пуст — и диспетчер молчит.
    harness._dispatch = lambda snapshot: 0                    # noqa: SLF001
    harness.run()
    assert harness.metrics.deadlock_count > 0


def test_generation_and_broadcast_are_decoupled():
    """Провал провайдера не должен останавливать вещание."""
    lanes = [Lane(GenerationRoute.LOCAL, job_seconds=60.0,
                  script=(Outcome.PROVIDER_FAILED,))]
    harness = SoakHarness(config=six_hours(hours=2.0, starting_buffer_seconds=3600.0),
                          lanes=lanes)
    harness.run()
    assert harness.metrics.ticks == 120, "часы шли независимо от провайдера"
    assert harness.buffer_seconds >= 0.0


# ------------------------------------------------------------------ запуск

def test_the_command_line_run_reports_and_exits_zero(capsys):
    code = main(["--hours", "1", "--tick-seconds", "120", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert '"deadlock_count": 0' in out
    assert '"peak_local_memory_mb": null' in out


def test_a_nonsense_configuration_is_refused():
    with pytest.raises(ValueError):
        SoakConfig(hours=0)
    with pytest.raises(ValueError):
        SoakConfig(tick_seconds=0)
    with pytest.raises(ValueError):
        SoakConfig(max_jobs_in_flight=0)


def test_the_default_lanes_cover_the_documented_ladder():
    routes = {lane.route for lane in default_lanes()}
    assert routes == {GenerationRoute.LOCAL, GenerationRoute.CHEAP_CLOUD,
                      GenerationRoute.HIGGSFIELD_BROWSER}
