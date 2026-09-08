"""Шестичасовой прогон фабрики контента: что он измеряет и чего не выдумывает.

Приёмка требует шести часов без вмешательства. Шесть часов настоящего времени в
проверке кода — это проверка, которую никто не будет запускать, поэтому здесь
часы виртуальные: тик за тиком, детерминированно, без единого `sleep`. Тот же
прогон умеет идти и в реальном времени — на машине владельца, где смысл именно
в реальном.

Что здесь считается **тупиком** и почему он обязан быть нулём: тик, в котором
буфер ниже цели, есть хотя бы одна работоспособная полоса, ни одна работа не
выполняется — и при этом ничего не запущено. Не «медленно» и не «неудачно»:
система, которой есть что делать и чем делать, не делает ничего. Такое состояние
не лечится ожиданием, и поэтому оно считается отдельно от всех остальных
неудач.

Второе обязательное свойство — **проверка человека останавливает одну полосу, а
не поток**. Капча на браузерном пути не должна гасить локальную генерацию: эфир
живёт, пока жива хоть одна полоса, а когда не живёт ни одна — идёт утверждённый
запас, а не импровизация.

Чего прогон НЕ делает: не выдумывает измерений. Пик памяти на виртуальных часах
никто не мерил, и в отчёте так и написано — `null`, а не правдоподобное число.
Деньги считаются по объявленной стоимости полос, потому что это счёт, а не
измерение, и названо оно соответственно.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .content_buffer import (BufferedAsset, BufferHealth, BufferSnapshot,
                             evaluate_buffer, recommended_generation_slots)
from .higgsfield_browser_contracts import MediaKind
from .media_router import GenerationRoute
from .route_inputs import (BrowserReadiness, GenerationObservations, Health,
                           Reading, RouteRequirements, plan_generation_route)

HOUR = 3600.0


class Outcome(str, Enum):
    """Чем кончилась одна работа. Перечень закрыт: «прочее» лечить нечем."""

    ACCEPTED = "accepted"
    REJECTED_MEDIA = "rejected_media"
    HUMAN_CHALLENGE = "human_challenge"
    NEEDS_OWNER_AUTH = "needs_owner_auth"
    RATE_LIMITED = "rate_limited"
    UI_CHANGED = "ui_changed"
    TIMEOUT = "timeout"
    PROVIDER_FAILED = "provider_failed"


class LaneState(str, Enum):
    READY = "ready"
    COOLDOWN = "cooldown"
    PAUSED_FOR_OWNER = "paused_for_owner"
    BROKEN_UI = "broken_ui"


# Сколько ждать после ограничения частоты. Пауза, а не подстройка под лимит.
COOLDOWN_SECONDS = 15 * 60.0
# Сколько раз подряд полоса может отдать брак, прежде чем уйти в паузу. Без
# границы «переделать» превращается в бесконечный цикл за деньги владельца.
MAX_CONSECUTIVE_REJECTS = 3
# Как часто владельцу можно напоминать об одной и той же полосе.
ALERT_COALESCE_SECONDS = 30 * 60.0


@dataclass(slots=True)
class Lane:
    """Одна полоса генерации: путь, его состояние и сценарий поведения.

    Сценарий — это последовательность исходов, которую полоса отдаёт по кругу.
    Не случайность: прогон, который каждый раз идёт иначе, ничего не
    доказывает, а падение в нём невозможно воспроизвести.
    """

    route: GenerationRoute
    script: tuple[Outcome, ...] = (Outcome.ACCEPTED,)
    job_seconds: float = 120.0
    cost_usd_per_job: float = 0.0
    state: LaneState = LaneState.READY
    ready_at: float = 0.0
    _cursor: int = 0
    consecutive_rejects: int = 0
    last_alert_at: float = float("-inf")

    def next_outcome(self) -> Outcome:
        outcome = self.script[self._cursor % len(self.script)]
        self._cursor += 1
        return outcome

    def usable(self, now: float) -> bool:
        if self.state is LaneState.READY:
            return True
        if self.state is LaneState.COOLDOWN and now >= self.ready_at:
            self.state = LaneState.READY
            return True
        return False


@dataclass(slots=True)
class Job:
    lane: GenerationRoute
    started_at: float
    finishes_at: float
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class SoakConfig:
    hours: float = 6.0
    tick_seconds: float = 60.0
    # Сколько эфира съедает вещание за час. Генерация и вещание развязаны:
    # буфер тратится независимо от того, что происходит с провайдерами.
    broadcast_seconds_per_hour: float = HOUR
    target_buffer_seconds: float = 6 * HOUR
    segment_seconds: float = 45.0
    starting_buffer_seconds: float = 0.0
    # Сколько работ идёт одновременно. Шесть, а не одна: генерация занимает
    # минуты, эфир тратится непрерывно, и фабрика в один поток за вещанием не
    # успевает — это видно по прогону, а не выведено из общих соображений.
    max_jobs_in_flight: int = 6
    evergreen_available_seconds: float = 2 * HOUR

    def __post_init__(self) -> None:
        if self.hours <= 0:
            raise ValueError("прогон нулевой длительности ничего не проверяет")
        if self.tick_seconds <= 0:
            raise ValueError("шаг прогона должен быть положительным")
        if self.segment_seconds <= 0:
            raise ValueError("сегмент нулевой длины не наполняет буфер")
        if self.max_jobs_in_flight < 1:
            raise ValueError("хотя бы одна работа одновременно")


@dataclass(slots=True)
class SoakMetrics:
    """Числа приёмки. Неизмеренное остаётся `None`, а не правдоподобным."""

    ticks: int = 0
    generated_jobs: int = 0
    accepted_outputs: int = 0
    rejected_outputs: int = 0
    retries: int = 0
    owner_interventions: int = 0
    owner_alerts: int = 0
    ui_drift_incidents: int = 0
    rate_limit_incidents: int = 0
    timeouts: int = 0
    fallback_count: int = 0
    evergreen_seconds: float = 0.0
    deadlock_count: int = 0
    buffer_low_ticks: int = 0
    buffer_critical_ticks: int = 0
    buffer_empty_ticks: int = 0
    accounted_cost_usd: float = 0.0
    peak_lanes_paused: int = 0
    # Настоящего измерения памяти на виртуальных часах нет. Ставить сюда
    # число значило бы записать в приёмку то, чего никто не мерил.
    peak_local_memory_mb: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticks": self.ticks, "generated_jobs": self.generated_jobs,
            "accepted_outputs": self.accepted_outputs,
            "rejected_outputs": self.rejected_outputs, "retries": self.retries,
            "retry_rate": (self.retries / self.generated_jobs
                           if self.generated_jobs else 0.0),
            "owner_interventions": self.owner_interventions,
            "owner_alerts": self.owner_alerts,
            "ui_drift_incidents": self.ui_drift_incidents,
            "rate_limit_incidents": self.rate_limit_incidents,
            "timeouts": self.timeouts, "fallback_count": self.fallback_count,
            "evergreen_minutes": round(self.evergreen_seconds / 60.0, 2),
            "deadlock_count": self.deadlock_count,
            "buffer_low_ticks": self.buffer_low_ticks,
            "buffer_critical_ticks": self.buffer_critical_ticks,
            "buffer_empty_ticks": self.buffer_empty_ticks,
            "accounted_cost_usd": round(self.accounted_cost_usd, 6),
            "peak_lanes_paused": self.peak_lanes_paused,
            "peak_local_memory_mb": self.peak_local_memory_mb,
            "note": ("accounted_cost_usd — это счёт по объявленной стоимости "
                     "полос, а не измерение; peak_local_memory_mb на "
                     "виртуальных часах никто не мерил"),
        }


@dataclass(frozen=True, slots=True)
class SoakReport:
    metrics: SoakMetrics
    final_buffer: BufferSnapshot
    lanes: Mapping[str, str]
    passed: bool
    failures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "failures": list(self.failures),
                "metrics": self.metrics.to_dict(),
                "final_buffer": {"health": self.final_buffer.health.value,
                                 "playable_seconds": round(
                                     self.final_buffer.playable_seconds, 1),
                                 "target_seconds": self.final_buffer.target_seconds},
                "lanes": dict(self.lanes)}


def default_lanes() -> list[Lane]:
    """Три полосы со сценариями «всё хорошо». Отправная точка прогона."""
    return [
        Lane(GenerationRoute.LOCAL, job_seconds=180.0, cost_usd_per_job=0.0),
        Lane(GenerationRoute.CHEAP_CLOUD, job_seconds=90.0, cost_usd_per_job=0.01),
        Lane(GenerationRoute.HIGGSFIELD_BROWSER, job_seconds=240.0,
             cost_usd_per_job=0.0),
    ]


class SoakHarness:
    """Прогон фабрики на виртуальных часах."""

    def __init__(self, *, config: SoakConfig | None = None,
                 lanes: Sequence[Lane] | None = None,
                 requirements: RouteRequirements | None = None,
                 started_at: float = 0.0) -> None:
        self.config = config or SoakConfig()
        self.lanes = list(lanes if lanes is not None else default_lanes())
        self.requirements = requirements or RouteRequirements(
            media_kind=MediaKind.VIDEO, local_model_mb=4_096.0)
        self.now = float(started_at)
        self.metrics = SoakMetrics()
        self.buffer_seconds = float(self.config.starting_buffer_seconds)
        self.evergreen_left = float(self.config.evergreen_available_seconds)
        self.in_flight: list[Job] = []
        self._alerts: list[tuple[float, str, str]] = []

    # ------------------------------------------------------------------ мир

    def _lane(self, route: GenerationRoute) -> Lane | None:
        for lane in self.lanes:
            if lane.route is route:
                return lane
        return None

    def observations(self) -> GenerationObservations:
        """Наблюдения для настоящего маршрутизатора, а не для его подобия.

        Прогон обязан проверять тот код, который поедет владельцу. Поэтому
        полосы переводятся в наблюдения, а решение принимает
        `plan_generation_route` — со всеми своими отказами по неизмеренному.
        """
        local = self._lane(GenerationRoute.LOCAL)
        cloud = self._lane(GenerationRoute.CHEAP_CLOUD)
        browser = self._lane(GenerationRoute.HIGGSFIELD_BROWSER)
        return GenerationObservations(
            free_memory_mb=Reading(value=16_384.0, source="soak:virtual",
                                   observed_at_epoch_s=self.now, max_age_s=HOUR),
            local_generator=(Health.HEALTHY if local and local.usable(self.now)
                             else Health.DOWN),
            cloud_provider=(Health.HEALTHY if cloud and cloud.usable(self.now)
                            else Health.DOWN),
            browser=_browser_readiness(browser, self.now),
            buffer=self.snapshot().health)

    def snapshot(self) -> BufferSnapshot:
        assets = ()
        if self.buffer_seconds > 0:
            assets = (BufferedAsset("buffer", self.buffer_seconds),)
        return evaluate_buffer(assets, now_epoch_s=self.now,
                               target_seconds=self.config.target_buffer_seconds)

    # ------------------------------------------------------------------ ход

    def tick(self) -> None:
        step = self.config.tick_seconds
        self._finish_jobs()

        # Вещание тратит эфир независимо от генерации: они развязаны, и
        # неудача провайдера не должна останавливать поток.
        consumed = self.config.broadcast_seconds_per_hour * (step / HOUR)
        self.buffer_seconds = max(0.0, self.buffer_seconds - consumed)

        snapshot = self.snapshot()
        if snapshot.health is BufferHealth.LOW:
            self.metrics.buffer_low_ticks += 1
        elif snapshot.health is BufferHealth.CRITICAL:
            self.metrics.buffer_critical_ticks += 1
        elif snapshot.health is BufferHealth.EMPTY:
            self.metrics.buffer_empty_ticks += 1

        paused = sum(1 for lane in self.lanes if not lane.usable(self.now))
        self.metrics.peak_lanes_paused = max(self.metrics.peak_lanes_paused, paused)

        started = self._dispatch(snapshot)
        if snapshot.deficit_seconds > 0 and not started and not self.in_flight:
            usable = [lane for lane in self.lanes if lane.usable(self.now)]
            if usable:
                # Есть что делать и есть чем — и ничего не делается.
                self.metrics.deadlock_count += 1
            else:
                self._serve_evergreen(step)
        self.now += step
        self.metrics.ticks += 1

    def _dispatch(self, snapshot: BufferSnapshot) -> int:
        if snapshot.deficit_seconds <= 0:
            return 0
        room = self.config.max_jobs_in_flight - len(self.in_flight)
        if room <= 0:
            return 0
        wanted = recommended_generation_slots(
            snapshot, average_asset_seconds=self.config.segment_seconds,
            max_batch=room)
        if wanted <= 0:
            return 0

        plan = plan_generation_route(self.observations(), self.requirements,
                                     now=self.now)
        if not plan.has_route:
            if plan.fallback:
                self.metrics.fallback_count += 1
            return 0

        first_choice = plan.chain[0]
        first_choice_used = False
        started = 0
        for route in plan.chain:
            if started >= wanted:
                break
            lane = self._lane(route)
            if lane is None or not lane.usable(self.now):
                continue
            self.in_flight.append(Job(lane=route, started_at=self.now,
                                      finishes_at=self.now + lane.job_seconds))
            self.metrics.generated_jobs += 1
            self.metrics.accounted_cost_usd += lane.cost_usd_per_job
            first_choice_used = first_choice_used or route is first_choice
            started += 1
        if started and not first_choice_used:
            # Запасной путь — это когда первым выбором воспользоваться не
            # удалось, а не когда работ запущено несколько.
            self.metrics.fallback_count += 1
        return started

    def _finish_jobs(self) -> None:
        still: list[Job] = []
        for job in self.in_flight:
            if job.finishes_at > self.now:
                still.append(job)
                continue
            self._settle(job)
        self.in_flight = still

    def _settle(self, job: Job) -> None:
        lane = self._lane(job.lane)
        if lane is None:
            return
        outcome = lane.next_outcome()
        if outcome is Outcome.ACCEPTED:
            lane.consecutive_rejects = 0
            self.metrics.accepted_outputs += 1
            self.buffer_seconds += self.config.segment_seconds
            return

        if outcome in {Outcome.REJECTED_MEDIA, Outcome.PROVIDER_FAILED,
                       Outcome.TIMEOUT}:
            self.metrics.rejected_outputs += 1
            self.metrics.retries += 1
            if outcome is Outcome.TIMEOUT:
                self.metrics.timeouts += 1
            lane.consecutive_rejects += 1
            if lane.consecutive_rejects >= MAX_CONSECUTIVE_REJECTS:
                # Повторять дальше значит платить за один и тот же брак.
                lane.state = LaneState.COOLDOWN
                lane.ready_at = self.now + COOLDOWN_SECONDS
                lane.consecutive_rejects = 0
                self._alert(lane, f"{outcome.value}: полоса в паузе после "
                                  f"{MAX_CONSECUTIVE_REJECTS} отказов подряд")
            return

        if outcome is Outcome.RATE_LIMITED:
            self.metrics.rate_limit_incidents += 1
            lane.state = LaneState.COOLDOWN
            lane.ready_at = self.now + COOLDOWN_SECONDS
            return

        if outcome in {Outcome.HUMAN_CHALLENGE, Outcome.NEEDS_OWNER_AUTH}:
            # Останавливается ОДНА полоса. Остальные продолжают работать.
            lane.state = LaneState.PAUSED_FOR_OWNER
            self.metrics.owner_interventions += 1
            self._alert(lane, f"{outcome.value}: нужен владелец в браузере")
            return

        if outcome is Outcome.UI_CHANGED:
            lane.state = LaneState.BROKEN_UI
            self.metrics.ui_drift_incidents += 1
            self._alert(lane, "интерфейс провайдера изменился; нужен ремонт "
                              "селекторов")

    def _alert(self, lane: Lane, text: str) -> None:
        """Напоминания об одной полосе сливаются: шесть часов подряд одно и то
        же сообщение — это не уведомление, а фон, который перестают читать."""
        if self.now - lane.last_alert_at < ALERT_COALESCE_SECONDS:
            return
        lane.last_alert_at = self.now
        self.metrics.owner_alerts += 1
        self._alerts.append((self.now, lane.route.value, text))

    def _serve_evergreen(self, step: float) -> None:
        """Эфир не оставляют пустым и не импровизируют в нём."""
        served = min(step, self.evergreen_left)
        if served <= 0:
            return
        self.evergreen_left -= served
        self.metrics.evergreen_seconds += served
        self.buffer_seconds += served

    # ------------------------------------------------------------------ прогон

    def run(self) -> SoakReport:
        ticks = int(self.config.hours * HOUR / self.config.tick_seconds)
        for _ in range(max(1, ticks)):
            self.tick()
        return self.report()

    def report(self) -> SoakReport:
        failures = []
        if self.metrics.deadlock_count:
            failures.append(
                f"тупиков: {self.metrics.deadlock_count} (обязано быть 0)")
        if self.metrics.generated_jobs and not self.metrics.accepted_outputs:
            failures.append("ни один результат не принят за весь прогон")
        return SoakReport(metrics=self.metrics, final_buffer=self.snapshot(),
                          lanes={lane.route.value: lane.state.value
                                 for lane in self.lanes},
                          passed=not failures, failures=tuple(failures))

    @property
    def alerts(self) -> list[tuple[float, str, str]]:
        return list(self._alerts)


def _browser_readiness(lane: Lane | None, now: float) -> BrowserReadiness:
    if lane is None:
        return BrowserReadiness.UNKNOWN
    if lane.usable(now):
        return BrowserReadiness.READY
    if lane.state is LaneState.PAUSED_FOR_OWNER:
        return BrowserReadiness.NEEDS_OWNER
    if lane.state is LaneState.COOLDOWN:
        return BrowserReadiness.RATE_LIMITED
    return BrowserReadiness.BLOCKED


# --------------------------------------------------------------------- запуск

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Прогон фабрики контента на виртуальных или настоящих часах")
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--tick-seconds", type=float, default=60.0)
    parser.add_argument("--real-time", action="store_true",
                        help="идти по настоящим часам (для машины владельца)")
    parser.add_argument("--json", action="store_true", help="только отчёт JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    harness = SoakHarness(config=SoakConfig(hours=args.hours,
                                            tick_seconds=args.tick_seconds))
    if args.real_time:
        ticks = int(args.hours * HOUR / args.tick_seconds)
        for _ in range(max(1, ticks)):
            harness.tick()
            time.sleep(args.tick_seconds)
        report = harness.report()
    else:
        report = harness.run()

    body = report.to_dict()
    if args.json:
        print(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())


__all__ = ["ALERT_COALESCE_SECONDS", "COOLDOWN_SECONDS", "HOUR",
           "MAX_CONSECUTIVE_REJECTS", "Job", "Lane", "LaneState", "Outcome",
           "SoakConfig", "SoakHarness", "SoakMetrics", "SoakReport",
           "default_lanes", "main"]
