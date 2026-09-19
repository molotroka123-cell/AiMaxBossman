"""Владельческие сценарии 31–35: губернатор расходов.

Владелец платит настоящими деньгами, поэтому здесь проверяется не «есть ли
счётчик», а пять свойств, каждое из которых стоит денег, если его нет:

* 31 — НЕИЗВЕСТНАЯ стоимость запрещает работу и НЕ считается нулём;
* 32 — ноль, введённый владельцем, не делает платную модель бесплатной (BL-084);
* 33 — превышение бюджета останавливает ДО внешнего вызова, а не после него;
* 34 — резерв и списание атомарны: срыв посередине не оставляет двойного расхода;
* 35 — накопленный расход переживает перезапуск процесса.

У каждого сценария обе половины. Отрицательный контроль стоит ПЕРВЫМ везде, где
положительный расходует одноразовый ресурс: успешный допуск занимает
единственную открытую бронь цели (`objective_has_unreconciled_admission`), а
удачный резерв съедает дневной конверт. Иначе «бюджет остановил» доказывалось бы
уже израсходованным бюджетом.

Зависимости модуля — только стандартная библиотека и `bossman_shared` (через
`spine_fixtures`). Всё, что тянет sqlalchemy (`bcc.studio.*`), импортируется
ВНУТРИ функций и объявлено в реестре как `command_center`: BL-085 — корневой CI
ставит только pytest/pytest-timeout/psutil/httpx/pyyaml, и без способности
раннер отдаёт честный вердикт вместо исполнения.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bossman-core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spine_fixtures as fx  # noqa: E402
from bossman_shared.objective_admission import (ADMISSION_STATE_CHANGED,  # noqa: E402
                                                COST_ESTIMATE_UNKNOWN,
                                                DUPLICATE_RESERVATION, AdmissionKernel,
                                                CostEstimate, build_proposal)
from bossman_shared.objective_store import ObjectiveStoreError  # noqa: E402
from scenario_runner import PRODUCT_CONTRACTS, scenario  # noqa: E402

# ------------------------------------------------------------------ 31
@scenario(id="OS-31", depth=PRODUCT_CONTRACTS)
def os31_unknown_cost_forbids_the_work(ctx) -> None:
    """Нет оценки — нет работы. Ноль — это оценка, отсутствие оценки — не ноль."""
    store, spec, _ = fx.ready_store(ctx.path("state", "objectives.sqlite3"))
    target = ctx.path("работа", "результат.txt")
    treasury, policy = fx.FakeTreasury(), fx.FakePolicy()
    conflicts = fx.FakeConflicts()
    kernel = AdmissionKernel(policy, treasury, conflicts)

    def refusal(estimate, tag: str, at: float):
        """Предложение с такой оценкой → решение допуска. Ничего не резервирует."""
        proposal = build_proposal(
            store, fx.OBJECTIVE_A,
            [fx.observation(spec, observed_at=at - 10.0, observation_id=f"obs-{tag}")],
            at, "scheduled", requested_capabilities=(fx.CAPABILITY,),
            expected_effects=(fx.effect(target),), cost_estimate=estimate)
        if proposal is None:
            raise AssertionError(f"{tag}: цель не породила предложение")
        return kernel.admit(store, proposal, now=at)

    # ОТРИЦАТЕЛЬНЫЕ ПЕРВЫМИ: удачный допуск занял бы единственную открытую бронь
    # цели, и дальнейшие отказы объяснялись бы ею, а не стоимостью.
    missing = refusal(None, "none", fx.NOW)
    ctx.negative("предложение БЕЗ оценки стоимости не допущено",
                 missing.admitted is False and missing.reason == COST_ESTIMATE_UNKNOWN,
                 f"reason={missing.reason}")
    ctx.negative("нечисловая оценка (NaN) не допущена",
                 refusal(CostEstimate(float("nan"), 10, 1.0), "nan", fx.NOW + 1).reason
                 == COST_ESTIMATE_UNKNOWN)
    ctx.negative("отрицательная оценка не допущена",
                 refusal(CostEstimate(-1.0, 10, 1.0), "neg", fx.NOW + 2).reason
                 == COST_ESTIMATE_UNKNOWN)
    ctx.negative("нецелое число токенов не допущено",
                 refusal(CostEstimate(0.1, 1.5, 1.0), "frac", fx.NOW + 3).reason
                 == COST_ESTIMATE_UNKNOWN)
    ctx.negative("неизвестная стоимость НЕ дошла до казны и не легла нулём",
                 treasury.reserved == [] and policy.calls == 0 and conflicts.held == {},
                 f"резервов={len(treasury.reserved)}, обращений к политике={policy.calls}")
    ctx.negative("после отказов у цели нет ни одной открытой брони",
                 len(store.open_reservations(fx.OBJECTIVE_A)) == 0)

    # ПОЛОЖИТЕЛЬНАЯ ПОЛОВИНА: известная оценка проходит, и казна получает ИМЕННО её.
    known = CostEstimate(0.5, 1000, 30.0)
    allowed = refusal(known, "known", fx.NOW + 4)
    reserved = [estimate for _, estimate in treasury.reserved]
    ctx.positive("предложение с известной оценкой допущено",
                 allowed.admitted is True and bool(allowed.reservation_id),
                 f"reason={allowed.reason}")
    ctx.positive("в казну ушла ровно объявленная оценка, а не ноль",
                 reserved == [known] and reserved[0].cost_usd == 0.5,
                 f"зарезервировано={[e.to_dict() for e in reserved]}")

    # Бесплатная работа — это ИЗВЕСТНЫЙ ноль, и он отличим от отсутствия оценки.
    store.settle_reservation(allowed.reservation_id, "RELEASED")
    free = CostEstimate(0.0, 0, 0.0)
    zero = refusal(free, "zero", fx.NOW + 5)
    ctx.positive("честный нулевой тариф допускается — «неизвестно» не равно «ноль»",
                 zero.admitted is True and [e for _, e in treasury.reserved][-1] == free,
                 f"reason={zero.reason}")


# ------------------------------------------------------------------ 32
@scenario(id="OS-32", depth=PRODUCT_CONTRACTS)
def os32_owner_zero_is_not_a_free_tariff(ctx) -> None:
    """BL-084: бесплатность подтверждает каталог поставки, а не поле в форме."""
    import asyncio  # noqa: PLC0415

    from bcc.studio import catalog  # noqa: PLC0415
    from bcc.studio import governance as gov  # noqa: PLC0415
    from bcc.studio.runtime import StudioError  # noqa: PLC0415

    paid = "openrouter:minimax/hailuo-3-max"
    unknown_model = "openrouter:никому-не-известная"

    ctx.positive("каталог поставки знает про БЕСПЛАТНЫЙ тариф — он не отвечает «всё платное»",
                 catalog.declared_free("comfyui:*") is True,
                 "declared_free('comfyui:*') is True")
    ctx.positive("каталог знает про ПЛАТНЫЙ тариф именно этой модели",
                 catalog.declared_free(paid) is False, f"declared_free({paid!r}) is False")
    ctx.negative("модели нет в каталоге — «бесплатна» не утверждается",
                 catalog.declared_free(unknown_model) is None, "declared_free(...) is None")

    async def run():
        svc = await _studio_service(ctx.path("cc", "bcc.sqlite3"))
        out = {}
        # Владелец ввёл ноль напротив ПЛАТНОЙ модели и включил free_only.
        await gov.save_policy(svc, {"enabled": True, "free_only": True,
                                    "cloud_budget_usd": 1, "per_job_usd": 1,
                                    "prices": {paid: 0}})
        out["zero_paid"] = await _refused(gov.reserve(svc, paid, 1, 1, []), StudioError)
        out["after_zero"] = await gov.budget_status(svc)
        # Той же модели каталог бесплатность не подтверждает ни при какой цене.
        await gov.save_policy(svc, {"enabled": True, "free_only": True,
                                    "cloud_budget_usd": 1, "per_job_usd": 1,
                                    "prices": {unknown_model: 0}})
        out["zero_unknown"] = await _refused(gov.reserve(svc, unknown_model, 1, 1, []),
                                             StudioError)
        # Положительная половина: правка закрыла ТОЛЬКО режим free_only.
        await gov.save_policy(svc, {"enabled": True, "free_only": False,
                                    "cloud_budget_usd": 1, "per_job_usd": 1,
                                    "prices": {paid: 0.1}})
        out["honest_price"] = await gov.reserve(svc, paid, 1, 1, [])
        out["after_honest"] = await gov.budget_status(svc)
        return out

    got = asyncio.run(run())
    ctx.negative("нулевая оценка владельца НЕ покупает платную модель",
                 isinstance(got["zero_paid"], StudioError)
                 and "free_only" in str(got["zero_paid"]),
                 f"отказ: {got['zero_paid']}")
    ctx.negative("отказ не списал ни цента: дневной счётчик остался нулевым",
                 got["after_zero"]["committed_upper_bound_usd"] == 0,
                 f"committed={got['after_zero']['committed_upper_bound_usd']}")
    ctx.negative("неизвестная каталогу модель с нулём тоже отвергнута",
                 isinstance(got["zero_unknown"], StudioError)
                 and "free_only" in str(got["zero_unknown"]))
    ctx.positive("та же модель с ЧЕСТНОЙ ценой вне free_only работает — способность не потеряна",
                 abs(got["honest_price"]["upper_bound_usd"] - 0.1) < 1e-9,
                 f"upper_bound_usd={got['honest_price']['upper_bound_usd']}")
    ctx.positive("законный резерв виден в дневном счётчике",
                 abs(got["after_honest"]["committed_upper_bound_usd"] - 0.1) < 1e-9,
                 f"committed={got['after_honest']['committed_upper_bound_usd']}")


# ------------------------------------------------------------------ 33
@scenario(id="OS-33", depth=PRODUCT_CONTRACTS)
def os33_budget_stops_before_the_external_call(ctx) -> None:
    """Превышение бюджета останавливает ДО сети. Считаются запросы к транспорту."""
    import asyncio  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from bcc.studio import governance as gov  # noqa: PLC0415
    from bcc.studio.provider import GenerationPlane  # noqa: PLC0415
    from bcc.studio.providers.openrouter import OpenRouterProvider  # noqa: PLC0415
    from bcc.studio.runtime import StudioError  # noqa: PLC0415

    paid = "openrouter:minimax/hailuo-3-max"
    requests: list[str] = []

    def serve(request):
        """Единственная дверь наружу. Каждый запрос ЗДЕСЬ — потраченные деньги."""
        requests.append(str(request.url))
        return httpx.Response(200, json={"id": "req-1", "status": "queued"})

    async def run():
        svc = await _studio_service(ctx.path("cc", "bcc.sqlite3"))
        out = {}
        # ОТРИЦАТЕЛЬНЫЙ ПЕРВЫМ: удачный резерв съел бы дневной конверт, и отказ
        # ниже объяснялся бы им, а не превышением бюджета задачи.
        await gov.save_policy(svc, {"enabled": True, "free_only": False,
                                    "cloud_budget_usd": 10, "per_job_usd": 0.05,
                                    "prices": {paid: 0.10}})
        out["per_job"] = await _refused(gov.reserve(svc, paid, 1, 1, []), StudioError)
        out["after_per_job"] = await gov.budget_status(svc)
        out["requests_after_refusal"] = len(requests)

        # ПОЛОЖИТЕЛЬНАЯ ПОЛОВИНА: в пределах бюджета путь ДОХОДИТ до сети.
        await gov.save_policy(svc, {"enabled": True, "free_only": False,
                                    "cloud_budget_usd": 0.25, "per_job_usd": 0.20,
                                    "prices": {paid: 0.10}})
        # reserve(svc, model, job_id, count, inputs): одна генерация по 0.10.
        reservation = await gov.reserve(svc, paid, 2, 1, [])
        out["reservation"] = reservation

        async def gate():
            await gov.check_current(svc, reservation)
            price = reservation["policy"]["prices"][paid]
            return {"kind": "cloud", "pricing_known": True, "price_in": price, "price_out": price}

        # Значение ключа фиктивное и в отчёт не попадает: сеть подменена
        # транспортом, настоящих учётных данных владельца здесь нет и не нужно.
        provider = OpenRouterProvider("fake-owner-key-not-real",
                                      transport=httpx.MockTransport(serve), gate=gate)
        out["receipt"] = await provider.submit(GenerationPlane(paid, "кот в окне", {}, ()))
        out["requests_after_submit"] = len(requests)

        # Дневной конверт исчерпан: вторая задача не выпускается наружу.
        out["daily"] = await _refused(gov.reserve(svc, paid, 3, 2, []), StudioError)
        out["after_daily"] = await gov.budget_status(svc)

        # Отзыв политики в полёте: отказ приходит из шлюза ДО обращения к сети.
        await gov.save_policy(svc, {"enabled": False, "free_only": False,
                                    "cloud_budget_usd": 0.25, "per_job_usd": 0.20,
                                    "prices": {paid: 0.10}})
        out["revoked"] = await _refused(
            provider.submit(GenerationPlane(paid, "кот в окне", {}, ())), StudioError)
        out["requests_final"] = len(requests)
        return out

    got = asyncio.run(run())
    ctx.negative("бюджет задачи превышен — резерв отказан",
                 isinstance(got["per_job"], StudioError) and got["per_job"].reason == "budget",
                 f"отказ: {got['per_job']}")
    ctx.negative("отказ случился ДО внешнего вызова: провайдер не видел ни одного запроса",
                 got["requests_after_refusal"] == 0,
                 f"запросов={got['requests_after_refusal']}")
    ctx.negative("несостоявшаяся задача не списала ничего",
                 got["after_per_job"]["committed_upper_bound_usd"] == 0)
    ctx.positive("в пределах бюджета работа ДОХОДИТ до провайдера — путь настоящий",
                 got["requests_after_submit"] == 1 and bool(got["receipt"].request_id),
                 f"запросов={got['requests_after_submit']}")
    ctx.negative("исчерпанный дневной конверт останавливает следующую задачу",
                 isinstance(got["daily"], StudioError) and got["daily"].reason == "budget",
                 f"отказ: {got['daily']}")
    # Первая задача заняла 0.10 из 0.25; вторая просила бы 0.20 — вместе это
    # больше конверта, поэтому резерв отказан и счётчик остался на 0.10.
    ctx.negative("после отказа по дневному конверту наружу так никто и не сходил",
                 got["requests_after_submit"] == 1
                 and abs(got["after_daily"]["committed_upper_bound_usd"] - 0.1) < 1e-9,
                 f"committed={got['after_daily']['committed_upper_bound_usd']}")
    ctx.negative("отозванная в полёте политика закрывает и запасной путь — прямой submit",
                 isinstance(got["revoked"], StudioError)
                 and got["revoked"].reason == "policy_changed" and got["requests_final"] == 1,
                 f"запросов={got['requests_final']}, отказ: {got['revoked']}")

    _objective_budget_stops_before_ports(ctx)


def _objective_budget_stops_before_ports(ctx) -> None:
    """То же свойство на втором денежном рубеже — лимите самой цели владельца.

    Долговечная запись о намерении берётся ДО любого внешнего порта, и лимит
    `max_cost_usd` проверяется в той же транзакции. Значит превышение видно
    раньше, чем кто-либо успевает что-то занять или потратить.
    """
    store, spec, _ = fx.ready_store(ctx.path("state", "objectives-limit.sqlite3"))
    treasury, policy, conflicts = fx.FakeTreasury(), fx.FakePolicy(), fx.FakeConflicts()
    kernel = AdmissionKernel(policy, treasury, conflicts)
    limit = spec.to_dict()["limits"]["max_cost_usd"]
    over = fx.propose(store, spec, target=ctx.path("работа", "результат.txt"),
                      estimate=CostEstimate(limit + 1.0, 10, 1.0), observation_id="obs-over")
    decision = kernel.admit(store, over, now=fx.NOW)
    ctx.negative("оценка выше лимита цели не допущена",
                 decision.admitted is False, f"reason={decision.reason}")
    ctx.negative("ни казна, ни политика, ни реестр конфликтов не были вызваны вовсе",
                 treasury.reserved == [] and policy.calls == 0 and conflicts.held == {},
                 f"резервов={len(treasury.reserved)}, политика={policy.calls}")
    # Честно: код отказа здесь общий (`admission_state_changed`) и не отличает
    # «кончился бюджет цели» от гонки жизненного цикла. Деньги сохранены, но
    # причина владельцу не названа — это записано в отчёте как пробел продукта.
    ctx.positive("превышение лимита цели приходит РЕШЕНИЕМ, а не исключением",
                 decision.admitted is False and bool(decision.reason),
                 f"reason={decision.reason}, detail={decision.detail!r}; код общий — "
                 "«бюджет цели кончился» и гонка жизненного цикла неразличимы")
    within = fx.propose(store, spec, target=ctx.path("работа", "результат.txt"),
                        estimate=CostEstimate(1.0, 10, 1.0), now=fx.NOW + 1,
                        observation_id="obs-within", observed_at=fx.NOW - 9.0)
    allowed = kernel.admit(store, within, now=fx.NOW + 1)
    ctx.positive("оценка в пределах лимита цели допускается — отвергается не всё подряд",
                 allowed.admitted is True and len(treasury.reserved) == 1,
                 f"reason={allowed.reason}")


# ------------------------------------------------------------------ 34
@scenario(id="OS-34", depth=PRODUCT_CONTRACTS)
def os34_reserve_and_settle_are_atomic(ctx) -> None:
    """Срыв между резервом и публикацией допуска не оставляет двойного расхода."""
    from bossman_v3.organization.models import Resources  # noqa: PLC0415
    from bossman_v3.organization.treasury import ResourceTreasury  # noqa: PLC0415

    treasury = ResourceTreasury()
    treasury.set_limit("organization", Resources(usd=10.0, tokens=100_000, wall_seconds=100_000))
    port = _TreasuryPort(treasury)
    store, spec, _ = fx.ready_store(ctx.path("state", "objectives.sqlite3"))
    target = ctx.path("работа", "результат.txt")
    kernel = AdmissionKernel(fx.FakePolicy(), port, fx.FakeConflicts())
    estimate = CostEstimate(0.5, 1000, 30.0)

    # ОТРИЦАТЕЛЬНЫЙ ПЕРВЫМ: сначала срыв, потом удачный проход. Обратный порядок
    # доказывал бы «нет двойного расхода» на цели, которая уже всё потратила.
    crashed = fx.propose(store, spec, target=target, estimate=estimate, observation_id="obs-crash")
    decision = kernel.admit(_StoreThatDiesOnPublish(store), crashed, now=fx.NOW)
    envelope = treasury.snapshot()["organization"]
    ctx.negative("срыв между резервом и публикацией НЕ выдаёт полномочия",
                 decision.admitted is False, f"reason={decision.reason}")
    ctx.negative("резерв, взятый до срыва, возвращён в конверт",
                 envelope["reserved"]["usd"] == 0 and port.release_calls == 1,
                 f"reserved={envelope['reserved']['usd']}, release={port.release_calls}")
    ctx.negative("срыв не превратился в расход: потрачено ноль",
                 envelope["spent"]["usd"] == 0 and store.get(fx.OBJECTIVE_A).cost_usd_used == 0.0,
                 f"spent={envelope['spent']['usd']}")

    replay = kernel.admit(store, crashed, now=fx.NOW + 1)
    ctx.negative("переигрывание сорванного допуска НЕ покупает второй резерв",
                 replay.admitted is False and replay.reason == DUPLICATE_RESERVATION
                 and port.reserve_calls == 1,
                 f"reason={replay.reason}, резервов={port.reserve_calls}")

    # ПОЛОЖИТЕЛЬНАЯ ПОЛОВИНА: обычный путь резервирует и списывает ровно один раз.
    good = fx.propose(store, spec, target=target, estimate=estimate, now=fx.NOW + 2,
                      observation_id="obs-good", observed_at=fx.NOW - 8.0)
    admitted = kernel.admit(store, good, now=fx.NOW + 2)
    ctx.positive("обычный допуск состоялся и держит резерв ровно на оценку",
                 admitted.admitted is True
                 and treasury.snapshot()["organization"]["reserved"]["usd"] == 0.5,
                 f"reserved={treasury.snapshot()['organization']['reserved']['usd']}")
    # Закрытие брони делает продукт (расход цели списывается в ТОЙ ЖЕ транзакции),
    # а `commit` в казну — шаг ВЫЗЫВАЮЩЕГО: ядро допуска его не делает и не
    # притворяется, что между базой и внешней казной есть общая транзакция.
    # Здесь роль вызывающего исполняет сценарий; измеряется семантика казны.
    state = store.settle_reservation(admitted.reservation_id, "COMMITTED")
    port.commit(admitted.treasury_scopes, estimate, estimate)
    after = treasury.snapshot()["organization"]
    ctx.positive("списание сняло резерв и записало факт РОВНО ОДИН раз",
                 after["reserved"]["usd"] == 0 and after["spent"]["usd"] == 0.5
                 and state.cost_usd_used == 0.5,
                 f"spent={after['spent']['usd']}, у цели={state.cost_usd_used}")

    second = _raises(lambda: store.settle_reservation(admitted.reservation_id, "COMMITTED"),
                     ObjectiveStoreError)
    ctx.negative("повторное списание той же брони отвергнуто",
                 isinstance(second, ObjectiveStoreError) and "already settled" in str(second),
                 f"отказ: {second}")
    ctx.negative("накопленный расход цели не удвоился",
                 store.get(fx.OBJECTIVE_A).cost_usd_used == 0.5,
                 f"cost_usd_used={store.get(fx.OBJECTIVE_A).cost_usd_used}")


# ------------------------------------------------------------------ 35
@scenario(id="OS-35", depth=PRODUCT_CONTRACTS)
def os35_spend_survives_restart(ctx) -> None:
    """Расход пережил смерть процесса — и после перезапуска он всё ещё ограничивает."""
    import subprocess  # noqa: PLC0415

    path = ctx.path("state", "objectives.sqlite3")
    target = ctx.path("работа", "результат.txt")
    store, spec, _ = fx.ready_store(path)
    limit = spec.to_dict()["limits"]["max_cost_usd"]
    fresh = store.get(fx.OBJECTIVE_A).cost_usd_used
    ctx.negative("чистая цель показывает НОЛЬ расхода — счётчик не константа",
                 fresh == 0.0, f"cost_usd_used={fresh}")

    spent = CostEstimate(3.0, 1000, 30.0)
    first = fx.kernel().admit(
        store, fx.propose(store, spec, target=target, estimate=spent, observation_id="obs-spend"),
        now=fx.NOW)
    if not first.admitted:
        raise AssertionError(f"первая задача не допущена: {first.reason}")
    before = store.settle_reservation(first.reservation_id, "COMMITTED")
    ctx.positive("потраченное записано в долговечное состояние цели",
                 before.cost_usd_used == 3.0 and before.missions_used == 1,
                 f"cost_usd_used={before.cost_usd_used}, missions={before.missions_used}")

    del store  # СМЕРТЬ ПРОЦЕССА: всё, что жило в памяти, исчезло

    # Перезапуск делается ДРУГИМ процессом: чтение тем же процессом доказывало бы
    # только то, что объект жив, а не то, что расход лежит на диске.
    reader = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
        "from bossman_shared.objective_store import ObjectiveStore\n"
        f"state = ObjectiveStore({str(path)!r}).get({fx.OBJECTIVE_A!r})\n"
        "print(json.dumps({'cost': state.cost_usd_used, 'missions': state.missions_used}))\n"
    )
    run = subprocess.run([sys.executable, "-c", reader], capture_output=True, text=True,
                         timeout=120, check=False)
    restarted = json.loads(run.stdout or "{}") if run.returncode == 0 else {}
    ctx.positive("после перезапуска ДРУГОЙ процесс видит тот же накопленный расход",
                 restarted.get("cost") == 3.0 and restarted.get("missions") == 1,
                 f"код возврата={run.returncode}, прочитано={restarted}, "
                 f"stderr={run.stderr.strip()[-160:]}")

    from bossman_shared.objective_store import ObjectiveStore  # noqa: PLC0415

    reopened = ObjectiveStore(path)
    treasury = fx.FakeTreasury()
    kernel = AdmissionKernel(fx.FakePolicy(), treasury, fx.FakeConflicts())
    over = fx.propose(reopened, spec, target=target, now=fx.NOW + 10.0,
                      estimate=CostEstimate(limit - 2.0, 1000, 30.0),
                      observation_id="obs-over", observed_at=fx.NOW + 1.0)
    denied = kernel.admit(reopened, over, now=fx.NOW + 10.0)
    ctx.negative("перезапуск НЕ амнистия: задача сверх остатка лимита отвергнута",
                 denied.admitted is False and treasury.reserved == [],
                 f"reason={denied.reason}, резервов={len(treasury.reserved)}")
    within = fx.propose(reopened, spec, target=target, now=fx.NOW + 11.0,
                        estimate=CostEstimate(2.0, 1000, 30.0),
                        observation_id="obs-within", observed_at=fx.NOW + 2.0)
    allowed = kernel.admit(reopened, within, now=fx.NOW + 11.0)
    ctx.positive("задача в пределах ОСТАТКА после перезапуска допускается",
                 allowed.admitted is True and len(treasury.reserved) == 1,
                 f"reason={allowed.reason}")


# ------------------------------------------------------------- вспомогательное
class _TreasuryPort:
    """Адаптер порта допуска на КАНОНИЧЕСКУЮ казну продукта (`ResourceTreasury`).

    Семантика reserve → commit/release здесь не переписывается: адаптер только
    переводит `CostEstimate` в многомерный `Resources` и считает обращения,
    чтобы «двойного расхода нет» опиралось на число вызовов, а не на веру.
    """

    def __init__(self, treasury) -> None:
        self.treasury = treasury
        self.reserve_calls = self.release_calls = self.commit_calls = 0

    @staticmethod
    def _resources(estimate: CostEstimate):
        from bossman_v3.organization.models import Resources  # noqa: PLC0415

        return Resources(usd=float(estimate.cost_usd), tokens=int(estimate.tokens),
                         wall_seconds=int(estimate.wall_seconds))

    def reserve(self, scopes, estimate):
        self.reserve_calls += 1
        decision = self.treasury.reserve(list(scopes), self._resources(estimate))
        return (decision.allowed,
                f"treasury:{self.reserve_calls}" if decision.allowed else "", decision.reason)

    def release(self, scopes, estimate) -> None:
        self.release_calls += 1
        self.treasury.release(list(scopes), self._resources(estimate))

    def commit(self, scopes, estimate, actual) -> None:
        self.commit_calls += 1
        self.treasury.commit(list(scopes), self._resources(estimate), self._resources(actual))


class _StoreThatDiesOnPublish:
    """НАСТОЯЩЕЕ хранилище, которое умирает ровно между резервом и публикацией.

    Подменяется один-единственный момент — тот, в котором процесс и падает в
    жизни. Всё остальное делает продукт: заявка о намерении, порты, компенсация.
    """

    def __init__(self, real) -> None:
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def complete_admission(self, *args, **kwargs):
        raise OSError("диск отказал между резервом и публикацией допуска")


async def _studio_service(db_path: Path):
    """Минимальная служба студии: настоящая БД продукта и её схема."""
    import types  # noqa: PLC0415

    from bcc.db import Database  # noqa: PLC0415
    import bcc.studio.tables  # noqa: F401,PLC0415 — регистрирует таблицы студии в metadata

    database = Database(f"sqlite+aiosqlite:///{db_path}")
    await database.create_all()
    return types.SimpleNamespace(db=database,
                                 settings=types.SimpleNamespace(data_dir=db_path.parent))


async def _refused(awaitable, expected):
    """Вернуть пойманный отказ ожидаемого типа. Чужой тип — это провал контроля."""
    try:
        await awaitable
    except expected as exc:
        return exc
    except BaseException as exc:  # noqa: BLE001 — чужой тип отказа тоже находка
        return exc
    return None


def _raises(call, expected):
    try:
        call()
    except expected as exc:
        return exc
    except BaseException as exc:  # noqa: BLE001
        return exc
    return None
