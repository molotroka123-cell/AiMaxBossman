"""Владельческие сценарии 21–25: управление компьютером — ПЛОСКОСТЬ УПРАВЛЕНИЯ.

Владелец назвал разделение прямо: настоящие мышь, клавиатура, экран и
приложения остаются за его машиной, а в CI проверяется то, что решает, ЧТО и
КОГДА этим мышью сделать — планирование, выбор цели по смыслу, контракт
действия, идемпотентность и обрыв зацикливания.

Поэтому рабочий стол здесь — ВНЕШНИЙ МИР, а не продукт: `Desktop` ниже это
макет экрана владельца, ровно как `FakePolicy`/`FakeTreasury` в
`spine_fixtures` — макеты внешних сервисов. Всё, что принимает решения
(`bossman.computer_operator.*`, `bossman.apprentice.*`), берётся настоящее, с
этой ветки. Подменить здесь планировщик, политику, верификатор или леджер
значило бы проверять макет вместо продукта.

Живого шага модели в этих пятёрках нет намеренно: плоскость управления
детерминирована, и её вердикт не имеет права зависеть от того, что ответила
модель. Живой вызов модели уже проверяется сценариями OS-01/02/10/11.

Зависимости: только стандартная библиотека и `bossman-core` этой ветки.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Iterator

_ROOT = Path(__file__).resolve().parents[2]
for _part in (str(_ROOT), str(_ROOT / "bossman-core"), str(_ROOT / "command-center"),
              str(_ROOT / "tools"), str(Path(__file__).resolve().parent)):
    if _part not in sys.path:
        sys.path.insert(0, _part)

from scenario_runner import PRODUCT_CONTRACTS, scenario  # noqa: E402

ПРИЛОЖЕНИЕ = "Блокнот"
КНОПКА = "Записать отчёт"
ЭКРАН_ПОСЛЕ = "отчёт записан"


# --------------------------------------------------------------------- ветка
def branch_module(ctx, name: str):
    """Импортировать модуль продукта и доказать, что он с ЭТОЙ ветки.

    Editable-установка умеет подтянуть `bossman`/`bcc` из ДРУГОГО рабочего
    каталога с другим коммитом — и сценарий «зеленел» бы на коде, которого на
    release/bossman-owner нет. Отдельной пробы среды для computer_operator в
    `capabilities.py` пока нет (это зона владельца), поэтому проверка стоит
    здесь и даёт честный тупик, а не молчаливый PASS на чужом коде.
    """
    import importlib  # noqa: PLC0415

    try:
        module = importlib.import_module(name)
    except BaseException as exc:  # noqa: BLE001 — любая поломка импорта есть отсутствие
        ctx.not_proven(f"{name} не импортируется в этом прогоне: {type(exc).__name__}: {exc}")
    origin = getattr(module, "__file__", "") or ""
    try:
        Path(origin).resolve().relative_to(_ROOT)
    except ValueError:
        ctx.not_proven(f"{name} загружен НЕ с этой ветки ({origin}): проверять нечего")
    return module


@contextlib.contextmanager
def apprentice_enabled() -> Iterator[None]:
    """Мастер-тумблер подмастерья на время сценария, и обратно.

    `BOSSMAN_UNIVERSAL_COMPUTER_APPRENTICE` — собственный выключатель продукта,
    по умолчанию ВЫКЛЮЧЕННЫЙ: без него движок отказывается исполнять вообще
    (это проверяется отрицательным контролем в OS-21). Здесь он включается
    ровно так, как его включает владелец, и снимается на выходе, чтобы
    следующий сценарий не унаследовал чужое состояние.
    """
    from bossman.apprentice import flags  # noqa: PLC0415

    previous = os.environ.get(flags.MASTER)
    os.environ[flags.MASTER] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(flags.MASTER, None)
        else:
            os.environ[flags.MASTER] = previous


# ------------------------------------------------------- внешний мир (макет)
class Desktop:
    """Экран, окна и нажатия владельца. НЕ продукт: это то, чем продукт правит."""

    def __init__(self, target: Path, *, button: str = КНОПКА) -> None:
        self.target = target
        self.button = button
        self.generation = 0
        self.summary = "исходный экран"
        self.applications = 0

    def _elements(self) -> list[dict]:
        return [{"role": "button", "control_type": "button", "name": self.button,
                 "text": self.button},
                {"role": "text", "control_type": "text", "name": "статус", "text": self.summary}]

    def snap(self):
        from bossman.computer_operator.models import Observation, new_id  # noqa: PLC0415

        self.generation += 1
        return Observation(new_id("obs"), time.time(),
                           {"app": ПРИЛОЖЕНИЕ, "title": f"отчёт — {ПРИЛОЖЕНИЕ}"},
                           self.summary, {"elements": self._elements()}, None, False,
                           self.generation)

    def press(self) -> None:
        """Настоящий внешний эффект: байты на диске, а не слово об успехе."""
        self.applications += 1
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(f"{ЭКРАН_ПОСЛЕ} #{self.applications}\n", encoding="utf-8")
        self.summary = ЭКРАН_ПОСЛЕ


class DesktopObserver:
    name = "owner-desktop"

    def __init__(self, desktop: Desktop) -> None:
        self.desktop = desktop

    def observe(self):
        return self.desktop.snap()


class Actuator:
    """Рука, которая жмёт. Возвращает квитанцию ИМЕННО о запрошенном действии."""

    def __init__(self, desktop: Desktop) -> None:
        self.desktop = desktop
        self.calls: list[str] = []

    def receipt(self, step, action_id: str, side_effect_id: str):
        from bossman.apprentice.models import EffectReceipt  # noqa: PLC0415

        return EffectReceipt(side_effect_id=side_effect_id, action_id=action_id,
                             action_type=step.kind.value, observed_at=time.time(),
                             evidence_source="owner-desktop")

    def act(self, step, obs, *, action_id, side_effect_id):
        self.calls.append(step.step_id)
        self.desktop.press()
        return self.receipt(step, action_id, side_effect_id)


class FixedPlanner:
    """План владельца, уже переведённый в шаги. Модель здесь не участвует."""

    def __init__(self, steps) -> None:
        self.steps = list(steps)

    def plan(self, task, view):
        from bossman.apprentice.models import Plan  # noqa: PLC0415

        return Plan(goal=task.goal, steps=list(self.steps))

    def replan(self, task, view, failure, rest):
        from bossman.apprentice.models import Plan  # noqa: PLC0415

        return Plan(goal=task.goal, steps=list(rest))


# --------------------------------------------------------------- сборка шага
def write_step(*, key: str = "отчёт-1", risk=None, step_id: str = "шаг-1",
               target_name: str = КНОПКА, expected_text: str = ЭКРАН_ПОСЛЕ,
               checkpoint: str = "отчёт-на-диске", is_goal: bool = True):
    from bossman.apprentice.models import (AppIdentity, PlanStep, RiskClass,  # noqa: PLC0415
                                           SemanticTarget)
    from bossman.computer_operator.models import ActionKind, ExpectedState  # noqa: PLC0415

    return PlanStep(step_id=step_id, kind=ActionKind.UI_INVOKE,
                    app=AppIdentity(app=ПРИЛОЖЕНИЕ),
                    target=SemanticTarget(role="button", name=target_name),
                    expected=ExpectedState(contains_text=expected_text),
                    risk=risk if risk is not None else RiskClass.REVERSIBLE_WRITE,
                    side_effecting=True, checkpoint=checkpoint, is_goal=is_goal,
                    idempotency_key=key)


def build_engine(desktop: Desktop, steps, *, ledger=None, actuator=None):
    from bossman.apprentice.engine import (DefaultVerifier,  # noqa: PLC0415
                                           UniversalComputerApprentice)
    from bossman.apprentice.guards import SideEffectLedger  # noqa: PLC0415

    hand = actuator if actuator is not None else Actuator(desktop)
    verifier = DefaultVerifier(checkpoints={
        "отчёт-на-диске": lambda obs: (desktop.target.is_file(),
                                       f"независимое чтение диска: {desktop.target}")})
    engine = UniversalComputerApprentice(
        planner=FixedPlanner(steps), observer=DesktopObserver(desktop), actuator=hand,
        verifier=verifier, ledger=ledger if ledger is not None else SideEffectLedger())
    return engine, hand


def new_task(goal: str = "записать отчёт", session: str = "сессия-владельца"):
    from bossman.apprentice.models import ApprenticeTask  # noqa: PLC0415

    return ApprenticeTask.create(goal, session_id=session)


# =========================================================== OS-21 — ПЛАН
@scenario(id="OS-21", depth=PRODUCT_CONTRACTS)
def os21_plan_carries_a_verifiable_postcondition(ctx) -> None:
    """План строится и несёт ПРОВЕРЯЕМОЕ пост-состояние, а не обещание успеха."""
    branch_module(ctx, "bossman.computer_operator.planner")
    branch_module(ctx, "bossman.apprentice.engine")
    from bossman.apprentice.errors import ApprenticeDisabled  # noqa: PLC0415
    from bossman.apprentice.models import ApprenticeState, RiskClass  # noqa: PLC0415
    from bossman.computer_operator.models import (ActionKind, ExpectedState,  # noqa: PLC0415
                                                  Observation)
    from bossman.computer_operator.planner import Planner  # noqa: PLC0415
    from bossman.computer_operator.verifier import Verifier  # noqa: PLC0415

    # 1. План строится РАЗБОРЩИКОМ ПРОДУКТА из ответа планировщика.
    planner = Planner(chat_fn=None)
    action = planner.parse_action(
        '{"kind":"UI_INVOKE","target":"Записать отчёт",'
        '"expected":{"contains_text":"отчёт записан"},"idempotency_key":"отчёт-1"}')
    ctx.positive("план действия построен продуктом и несёт пост-состояние",
                 action.kind is ActionKind.UI_INVOKE and not action.expected.is_empty()
                 and action.idempotency_key == "отчёт-1",
                 f"вид={action.kind.value} ожидание={action.expected.contains_text!r}")

    # 2. Пост-состояние проверяется по ЭКРАНУ, а не по слову планировщика.
    verifier = Verifier()
    экран = Observation("obs-после", time.time(), {"app": ПРИЛОЖЕНИЕ, "title": "отчёт"},
                        ЭКРАН_ПОСЛЕ, {"elements": []}, None, False, 1)
    ctx.positive("пост-состояние подтверждается свежим наблюдением экрана",
                 verifier.verify(action, экран).ok,
                 verifier.verify(action, экран).reason)

    # 3. Тот же план проходит ЦИКЛ продукта целиком и закрывается проверенной целью.
    desktop = Desktop(ctx.path("работа", "отчёт.txt"))
    with apprentice_enabled():
        engine, hand = build_engine(desktop, [write_step()])
        result = engine.run(new_task())
    ctx.positive("цикл продукта довёл план до ПРОВЕРЕННОЙ цели",
                 result.state is ApprenticeState.SUCCEED and hand.calls
                 and desktop.target.is_file(),
                 f"состояние={result.state.value} причина={result.reason}")
    ctx.positive("цель закрыта независимым чтением мира, а не экраном",
                 result.checkpoints_reached == ["отчёт-на-диске"]
                 and desktop.target.read_text(encoding="utf-8").strip().endswith("#1"),
                 f"контрольные точки={result.checkpoints_reached}")

    # ---------------------------------------------------------- отрицательные
    from bossman.computer_operator.models import ComputerAction  # noqa: PLC0415

    голое = ComputerAction.make(ActionKind.UI_INVOKE, target=КНОПКА)
    без_постусловия = verifier.verify(голое, экран)
    ctx.negative("действие без пост-состояния не считается выполненным",
                 без_постусловия.reason == "mutating action missing postcondition",
                 без_постусловия.reason)
    вырожденное = ComputerAction.make(ActionKind.UI_INVOKE, target=КНОПКА,
                                      expected=ExpectedState(contains_text=" "))
    пробел = verifier.verify(вырожденное, экран)
    ctx.negative("пробел вместо пост-состояния не является пост-состоянием",
                 вырожденное.expected.is_empty() and not пробел.ok, пробел.reason)
    ложное = ComputerAction.make(ActionKind.UI_INVOKE, target=КНОПКА,
                                 expected=ExpectedState(contains_text="файл удалён"))
    провал = verifier.verify(ложное, экран)
    ctx.negative("ложное пост-состояние отвергается тем же экраном",
                 провал.reason == "postcondition failed", провал.reason)
    ctx.refused("ответ планировщика без объекта плана не превращается в действие",
                lambda: planner.parse_action("никакого плана здесь нет"), ValueError)

    # План, который ничего проверяемого не обещает, НЕ закрывается успехом.
    безцельный = Desktop(ctx.path("безцельно", "отчёт.txt"))
    with apprentice_enabled():
        engine2, hand2 = build_engine(
            безцельный, [write_step(key="отчёт-безцельно", risk=RiskClass.LOW,
                                    checkpoint="", is_goal=False)])
        result2 = engine2.run(new_task(session="сессия-безцельная"))
    ctx.negative("план, кончившийся без проверенной цели, не объявляется успехом",
                 result2.state is ApprenticeState.FAIL and "false completion" in result2.reason,
                 f"состояние={result2.state.value} причина={result2.reason}")

    пустой = Desktop(ctx.path("пусто", "отчёт.txt"))
    from bossman.apprentice.models import AppIdentity, PlanStep  # noqa: PLC0415
    with apprentice_enabled():
        engine3, _ = build_engine(пустой, [PlanStep(step_id="готово", kind=ActionKind.COMPLETE,
                                                    app=AppIdentity())])
        result3 = engine3.run(new_task(session="сессия-пустая"))
    ctx.negative("COMPLETE без проверенной цели — не результат, а отказ",
                 result3.state is ApprenticeState.FAIL
                 and "COMPLETE without a verified goal" in result3.reason,
                 f"причина={result3.reason}")

    # Выключенный мастер-тумблер: движок отказывается исполнять вовсе.
    from bossman.apprentice import flags  # noqa: PLC0415
    было = os.environ.pop(flags.MASTER, None)
    try:
        engine4, hand4 = build_engine(Desktop(ctx.path("выкл", "отчёт.txt")), [write_step()])
        ctx.refused("с выключенным тумблером подмастерье не исполняет ничего",
                    lambda: engine4.run(new_task(session="сессия-выкл")), ApprenticeDisabled)
        ctx.negative("отказ наступил ДО единого нажатия",
                     hand4.calls == [], f"нажатий={len(hand4.calls)}")
    finally:
        if было is not None:
            os.environ[flags.MASTER] = было


# ================================================== OS-22 — ЦЕЛЬ ПО СМЫСЛУ
@scenario(id="OS-22", depth=PRODUCT_CONTRACTS)
def os22_target_is_chosen_by_meaning(ctx) -> None:
    """Цель выбирается по смыслу. Координаты — НАХОДКА, а не зелёная галочка."""
    branch_module(ctx, "bossman.apprentice.engine")
    branch_module(ctx, "bossman.apprentice.guards")
    branch_module(ctx, "bossman.computer_operator.policy")
    from bossman.apprentice.errors import CoordinateTargetForbidden  # noqa: PLC0415
    from bossman.apprentice.guards import resolve_target  # noqa: PLC0415
    from bossman.apprentice.models import (AppIdentity, ApprenticeState,  # noqa: PLC0415
                                           PlanStep, SemanticTarget)
    from bossman.computer_operator.models import (ActionKind, ComputerAction,  # noqa: PLC0415
                                                  ExpectedState, TaskMode)
    from bossman.computer_operator.policy import ComputerPolicy  # noqa: PLC0415

    desktop = Desktop(ctx.path("работа", "отчёт.txt"))
    наблюдение = desktop.snap()
    цель = SemanticTarget(role="button", name=КНОПКА)
    решение = resolve_target(цель, наблюдение)
    ctx.positive("цель, названная ролью и именем, разрешается в конкретный элемент",
                 решение.element is not None and решение.state == "READY"
                 and решение.element.get("name") == КНОПКА,
                 f"состояние={решение.state} score={решение.score}")

    with apprentice_enabled():
        engine, hand = build_engine(desktop, [write_step()])
        result = engine.run(new_task())
    ctx.positive("шаг с осмысленной целью проходит цикл и закрывает задачу",
                 result.state is ApprenticeState.SUCCEED and len(hand.calls) == 1,
                 f"состояние={result.state.value} нажатий={len(hand.calls)}")
    ctx.positive("в журнале действия записана СМЫСЛОВАЯ цель, а не точка экрана",
                 result.records[0].semantic_target.get("name") == КНОПКА
                 and not set(result.records[0].action["args_redacted"]) & {"x", "y"},
                 f"цель={result.records[0].semantic_target}")

    # ---------------------------------------------------------- отрицательные
    ctx.refused("цель, заданную координатами, продукт не принимает",
                lambda: SemanticTarget.from_dict({"role": "button", "x": 10, "y": 20}),
                CoordinateTargetForbidden)
    ctx.refused("шаг плана не имеет права нести координаты",
                lambda: PlanStep(step_id="ш", kind=ActionKind.CLICK, app=AppIdentity(),
                                 target=цель, args={"x": 10, "y": 20}),
                CoordinateTargetForbidden)
    ctx.refused("клик без смысловой цели не собирается вовсе",
                lambda: PlanStep(step_id="ш", kind=ActionKind.CLICK, app=AppIdentity()),
                ValueError)
    чужая = SemanticTarget(role="button", name="Удалить всё")
    ctx.negative("похожая, но ДРУГАЯ цель не разрешается «примерно»",
                 resolve_target(чужая, наблюдение).element is None,
                 f"состояние={resolve_target(чужая, наблюдение).state}")

    # Переименованная кнопка: продукт обязан признать снос цели, а не тыкнуть наугад.
    сдвинутый = Desktop(ctx.path("сдвиг", "отчёт.txt"), button="Совсем другая кнопка")
    with apprentice_enabled():
        engine2, hand2 = build_engine(сдвинутый, [write_step(key="отчёт-сдвиг")])
        result2 = engine2.run(new_task(session="сессия-сдвиг"))
    ctx.negative("исчезнувшая цель даёт отказ, а не нажатие наугад",
                 result2.state is ApprenticeState.FAIL and "selector_drift" in result2.reason
                 and hand2.calls == [] and сдвинутый.applications == 0,
                 f"причина={result2.reason} нажатий={len(hand2.calls)}")

    policy = ComputerPolicy()
    тычок = ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="ок"),
                                target="OK", args={"x": 100, "y": 200}, confidence=0.5)
    неуверенный = policy.classify(тычок, mode=TaskMode.CONTROL, observation=наблюдение)
    ctx.negative("координатный тычок с низкой уверенностью политика отвергает",
                 неуверенный.allow is False, неуверенный.reason)

    # ---------------------------------------------------------------- НАХОДКА
    уверенный = ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="ок"),
                                    target="OK", args={"x": 100, "y": 200}, confidence=1.0)
    вердикт = policy.classify(уверенный, mode=TaskMode.CONTROL, observation=наблюдение)
    if not вердикт.allow:
        ctx.negative("координатный клик недопустим на любой уверенности", True, вердикт.reason)
        return
    ctx.not_proven(
        "координатный клик НЕ недопустим: bossman-core/bossman/computer_operator/policy.py:139-140 "
        "отвергает его только при confidence<MIN_VISION_CONFIDENCE (=.72), и CLICK с "
        "args={'x':100,'y':200} при confidence=1.0 получает PolicyDecision(allow=True); "
        "исполняет его bossman-core/bossman/computer_operator/adapters/windows.py:251 "
        "(pyautogui.click(*self._xy(a)), координаты из args['x']/args['y']). Смысловой выбор "
        "цели гарантирован ТОЛЬКО на плоскости bossman/apprentice (SemanticTarget/PlanStep "
        "запрещают координаты структурно); шиповая цепочка computer_operator.subsystem."
        "build_manager этой гарантии не даёт")


# ================================================= OS-23 — КОНТРАКТ ДЕЙСТВИЯ
@scenario(id="OS-23", depth=PRODUCT_CONTRACTS)
def os23_action_contract_rejects_what_is_not_in_it(ctx) -> None:
    """Контракт действия отвергает действие, которого в нём нет."""
    branch_module(ctx, "bossman.computer_operator.adapters.router")
    branch_module(ctx, "bossman.apprentice.engine")
    from bossman.apprentice.models import ApprenticeState, EffectReceipt  # noqa: PLC0415
    from bossman.computer_operator.adapters.router import ActionRouter  # noqa: PLC0415
    from bossman.computer_operator.models import ActionKind  # noqa: PLC0415
    from bossman.computer_operator.planner import ALWAYS_KINDS, Planner  # noqa: PLC0415

    # 1. Модели предлагаются ТОЛЬКО те виды, что реально поддержаны бэкендами.
    узкий = Planner(chat_fn=None, supported=["UI_INVOKE", "FOCUS"])
    виды = set(asyncio.run(узкий.allowed_kinds()))
    ctx.positive("контракт действия сужается до реально поддержанных видов",
                 виды == {"FOCUS", "UI_INVOKE"} | set(ALWAYS_KINDS),
                 f"виды={sorted(виды)}")

    # 2. Объявленное в контракте действие доходит до бэкенда и принимается.
    class Backend:
        name = "ui-backend"

        def __init__(self) -> None:
            self.executed: list[str] = []

        async def supports(self, action, observation):
            return action.kind is ActionKind.UI_INVOKE

        async def execute(self, action, observation):
            self.executed.append(action.kind.value)

    backend = Backend()
    router = ActionRouter([backend])
    планировщик = Planner(chat_fn=None)
    объявленное = планировщик.parse_action(
        '{"kind":"UI_INVOKE","target":"Записать отчёт",'
        '"expected":{"contains_text":"отчёт записан"}}')
    ctx.positive("действие из контракта исполняется объявленным бэкендом",
                 asyncio.run(router.execute(объявленное, None)) == "ui-backend"
                 and backend.executed == ["UI_INVOKE"],
                 f"исполнено={backend.executed}")

    # ---------------------------------------------------------- отрицательные
    ctx.refused("вида, которого в контракте нет, продукт не собирает",
                lambda: планировщик.parse_action(
                    '{"kind":"LAUNCH_NUKE","expected":{"contains_text":"ок"}}'),
                ValueError)
    ctx.refused("пост-состояние строкой вместо объекта отвергается",
                lambda: планировщик.parse_action('{"kind":"CLICK","expected":"готово"}'),
                ValueError)
    ctx.refused("аргументы строкой вместо объекта отвергаются",
                lambda: планировщик.parse_action(
                    '{"kind":"CLICK","expected":{"contains_text":"ок"},"args":"что угодно"}'),
                ValueError)
    посторонний = планировщик.parse_action(
        '{"kind":"DRAG","target":"панель","expected":{"contains_text":"ок"}}')
    ctx.refused("вид, которого не поддерживает ни один бэкенд, не исполняется",
                lambda: asyncio.run(router.execute(посторонний, None)), RuntimeError)
    ctx.negative("отказ бэкендов не привёл ни к одному исполнению",
                 backend.executed == ["UI_INVOKE"], f"исполнено={backend.executed}")

    # 3. Квитанция обязана быть об ЭТОМ действии, а не о каком-нибудь.
    class ПодменнаяРука(Actuator):
        """Исполнитель, который отчитывается о ДРУГОМ действии."""

        def act(self, step, obs, *, action_id, side_effect_id):
            self.calls.append(step.step_id)
            self.desktop.press()
            return EffectReceipt(side_effect_id=side_effect_id, action_id=action_id,
                                 action_type=ActionKind.TAKE_SCREENSHOT.value,
                                 observed_at=time.time(), evidence_source="owner-desktop")

    class ЧужаяКвитанция(Actuator):
        def act(self, step, obs, *, action_id, side_effect_id):
            self.calls.append(step.step_id)
            self.desktop.press()
            return EffectReceipt(side_effect_id="совсем-другой-эффект", action_id=action_id,
                                 action_type=step.kind.value, observed_at=time.time(),
                                 evidence_source="owner-desktop")

    class БезУлики(Actuator):
        def act(self, step, obs, *, action_id, side_effect_id):
            self.calls.append(step.step_id)
            self.desktop.press()
            return EffectReceipt(side_effect_id=side_effect_id, action_id=action_id,
                                 action_type=step.kind.value, observed_at=time.time(),
                                 evidence_source="")

    подмены = [("квитанция о ДРУГОМ действии", ПодменнаяРука, "подмена"),
               ("квитанция о чужом эффекте", ЧужаяКвитанция, "чужая"),
               ("квитанция без источника улики", БезУлики, "безулики")]
    for имя, класс, тег in подмены:
        стол = Desktop(ctx.path(тег, "отчёт.txt"))
        with apprentice_enabled():
            движок, _ = build_engine(стол, [write_step(key=f"отчёт-{тег}")],
                                     actuator=класс(стол))
            исход = движок.run(new_task(session=f"сессия-{тег}"))
        коды = [запись.error_code for запись in исход.records]
        ctx.negative(f"{имя} отвергнута контрактом",
                     исход.state is ApprenticeState.FAIL and "receipt_invalid" in коды,
                     f"состояние={исход.state.value} коды={коды} причина={исход.reason[:120]}")

    # Честная рука с правильной квитанцией — та же цепочка доходит до успеха.
    честный = Desktop(ctx.path("честно", "отчёт.txt"))
    with apprentice_enabled():
        движок, _ = build_engine(честный, [write_step(key="отчёт-честно")])
        исход = движок.run(new_task(session="сессия-честная"))
    ctx.positive("верная квитанция об ЭТОМ действии принимается",
                 исход.state is ApprenticeState.SUCCEED
                 and исход.records[-1].receipt is not None
                 and исход.records[-1].receipt["action_type"] == ActionKind.UI_INVOKE.value,
                 f"состояние={исход.state.value}")


# ================================================== OS-24 — ИДЕМПОТЕНТНОСТЬ
@scenario(id="OS-24", depth=PRODUCT_CONTRACTS)
def os24_repeat_gives_no_second_effect(ctx) -> None:
    """Повтор того же действия не даёт второго эффекта — в том числе после перезапуска."""
    branch_module(ctx, "bossman.apprentice.durable")
    branch_module(ctx, "bossman.apprentice.engine")
    from bossman.apprentice.durable import DurableSafetyStore  # noqa: PLC0415
    from bossman.apprentice.guards import DurableRequired, SideEffectLedger  # noqa: PLC0415
    from bossman.apprentice.models import ApprenticeState  # noqa: PLC0415

    путь = ctx.path("состояние", "safety.sqlite")
    store = DurableSafetyStore(путь)
    desktop = Desktop(ctx.path("работа", "отчёт.txt"))

    with apprentice_enabled():
        движок1, рука1 = build_engine(desktop, [write_step()], ledger=SideEffectLedger(store))
        исход1 = движок1.run(new_task())
    ctx.positive("первое применение дало РОВНО ОДИН внешний эффект",
                 исход1.state is ApprenticeState.SUCCEED and len(рука1.calls) == 1
                 and desktop.applications == 1
                 and desktop.target.read_text(encoding="utf-8").strip().endswith("#1"),
                 f"нажатий={len(рука1.calls)} применений={desktop.applications}")

    # Повтор ТОГО ЖЕ намерения: другая задача, другой прогон, тот же ключ.
    with apprentice_enabled():
        движок2, рука2 = build_engine(desktop, [write_step()], ledger=SideEffectLedger(store))
        исход2 = движок2.run(new_task())
    ctx.negative("повтор того же намерения НЕ дал второго эффекта",
                 рука2.calls == [] and desktop.applications == 1
                 and all(з.duplicate_suppressed for з in исход2.records),
                 f"нажатий={len(рука2.calls)} применений={desktop.applications}")

    # ПЕРЕЗАПУСК процесса: новый движок, новый леджер, то же долговечное хранилище.
    del движок2
    store.close()
    store2 = DurableSafetyStore(путь)
    with apprentice_enabled():
        движок3, рука3 = build_engine(desktop, [write_step()], ledger=SideEffectLedger(store2))
        исход3 = движок3.run(new_task())
    ctx.negative("перезапуск не переотправляет уже применённый эффект",
                 рука3.calls == [] and desktop.applications == 1,
                 f"нажатий={len(рука3.calls)} применений={desktop.applications} "
                 f"состояние={исход3.state.value}")

    # Контроль обратной стороны: ДРУГОЕ намерение должно проходить.
    with apprentice_enabled():
        движок4, рука4 = build_engine(desktop, [write_step(key="отчёт-второй")],
                                      ledger=SideEffectLedger(store2))
        исход4 = движок4.run(new_task())
    ctx.positive("другой ключ — другое намерение: эффект законно применяется",
                 исход4.state is ApprenticeState.SUCCEED and len(рука4.calls) == 1
                 and desktop.applications == 2,
                 f"применений={desktop.applications}")

    # Запись без ключа идемпотентности отвергается ДО исполнения.
    без_ключа = Desktop(ctx.path("безключа", "отчёт.txt"))
    with apprentice_enabled():
        движок5, рука5 = build_engine(без_ключа, [write_step(key="")],
                                      ledger=SideEffectLedger(store2))
        исход5 = движок5.run(new_task(session="сессия-безключа"))
    ctx.negative("запись без ключа идемпотентности не исполняется вовсе",
                 исход5.state is ApprenticeState.FAIL
                 and "idempotency_key_required" in исход5.reason
                 and рука5.calls == [] and без_ключа.applications == 0,
                 f"причина={исход5.reason[:120]}")

    ctx.refused("живой эффект без долговечного хранилища не допускается",
                lambda: SideEffectLedger(None, live=True), DurableRequired)
    леджер = SideEffectLedger(store2)
    взят, _ = леджер.claim("эффект-однажды")
    ctx.positive("первая заявка на эффект принимается", взят is True)
    ctx.negative("вторая заявка на тот же эффект отклоняется",
                 леджер.claim("эффект-однажды")[0] is False)
    store2.close()


# ================================================ OS-25 — ОБРЫВ ЗАЦИКЛИВАНИЯ
@scenario(id="OS-25", depth=PRODUCT_CONTRACTS)
def os25_loop_is_detected_and_broken(ctx) -> None:
    """Зацикливание обнаруживается и прерывается, а не крутится вечно."""
    branch_module(ctx, "bossman.computer_operator.loop_guard")
    branch_module(ctx, "bossman.computer_operator.manager")
    from bossman.computer_operator.loop_guard import LoopGuard  # noqa: PLC0415
    from bossman.computer_operator.manager import ComputerOperatorManager  # noqa: PLC0415
    from bossman.computer_operator.models import (ActionKind, ComputerAction,  # noqa: PLC0415
                                                  ExpectedState, Observation,
                                                  TaskState, new_id)
    from bossman.computer_operator.store import JsonTaskStore  # noqa: PLC0415

    def наблюдение(тег: str, поколение: int = 1):
        return Observation(f"obs-{тег}", time.time(), {"app": ПРИЛОЖЕНИЕ, "title": тег}, тег,
                           {"elements": [{"control_type": "button", "name": "Готово"}]},
                           None, False, поколение)

    действие = ComputerAction.make(ActionKind.UI_INVOKE,
                                   expected=ExpectedState(contains_text="готово"),
                                   target="Готово")
    застрял = наблюдение("зависший экран")

    guard = LoopGuard()
    for _ in range(3):
        guard.record(действие, застрял, застрял, False)
    вердикт = guard.check(действие, застрял)
    ctx.positive("детектор называет род застревания, а не молчит",
                 вердикт.tripped and вердикт.kind in {"repeat", "no_progress", "verify_loop"},
                 f"род={вердикт.kind} причина={вердикт.reason}")

    колебание = LoopGuard()
    for i in range(4):
        а, б = наблюдение("A"), наблюдение("B")
        колебание.record(действие, а if i % 2 == 0 else б, б if i % 2 == 0 else а, True)
    ctx.positive("колебание состояния между двумя значениями тоже ловится",
                 колебание.check(действие, застрял).kind == "oscillation",
                 f"род={колебание.check(действие, застрял).kind}")

    # НАСТОЯЩИЙ цикл оператора против экрана, который не отвечает ничем.
    class ЗамершийЭкран:
        def __init__(self) -> None:
            self.снимков = 0

        async def observe(self, *, generation):
            self.снимков += 1
            return наблюдение("зависший экран", generation)

        async def probe(self, *, generation):
            return await self.observe(generation=generation)

    class УпрямыйПланировщик:
        def __init__(self) -> None:
            self.вызовов = 0

        async def next_action(self, **kwargs):
            self.вызовов += 1
            return ComputerAction(id=new_id("act"), kind=ActionKind.UI_INVOKE,
                                  expected=ExpectedState(contains_text="готово"),
                                  target="Готово", args={}, confidence=1.0)

    class СчётныйРоутер:
        def __init__(self) -> None:
            self.исполнено = 0

        async def execute(self, action, observation):
            self.исполнено += 1
            return "owner-desktop"

    async def нельзя(*args, **kwargs):
        raise AssertionError("подтверждение в этом сценарии не запрашивается")

    события: list[dict] = []
    планировщик, роутер, экран = УпрямыйПланировщик(), СчётныйРоутер(), ЗамершийЭкран()
    manager = ComputerOperatorManager(
        store=JsonTaskStore(ctx.path("оператор", "tasks.json")), planner=планировщик,
        observer=экран, action_router=роутер, approval_create=нельзя, approval_wait=нельзя,
        event_emit=lambda topic, **kw: события.append(kw))
    задача = manager.create_task("нажать кнопку Готово")
    начало = time.monotonic()
    состояние = asyncio.run(manager.run(задача.id))
    длительность = time.monotonic() - начало
    строка = manager.store.get(задача.id)
    сработок = sum(1 for e in события if e.get("event") == "loop_guard")

    ctx.positive("слепой повтор остановлен ЦИКЛОМ продукта, а не тайм-аутом",
                 сработок >= 1 and "loop guard" in (строка.last_error or ""),
                 f"сработок детектора={сработок} причина={строка.last_error}")
    ctx.positive("действие исполнено считанные разы, а не до исчерпания бюджета",
                 роутер.исполнено <= 3 and роутер.исполнено >= 1,
                 f"исполнено={роутер.исполнено} при max_steps={задача.max_steps}")
    ctx.positive("задача завершилась, а не крутится вечно",
                 строка.terminal and длительность < 30.0,
                 f"состояние={состояние} за {длительность:.2f} с")

    # ---------------------------------------------------------- отрицательные
    ctx.negative("сработавший детектор НЕ объявляет успех",
                 состояние is TaskState.FAILED and состояние is not TaskState.COMPLETED,
                 f"состояние={состояние}")
    guard.reset()
    ctx.negative("после вмешательства владельца история не держит задачу вечно",
                 guard.check(действие, застрял).tripped is False,
                 "reset обязан снимать прежние подписи")
    движется = LoopGuard()
    for i in range(3):
        движется.record(действие, наблюдение(f"шаг-{i}"), наблюдение(f"шаг-{i + 1}"), True)
    ctx.negative("на движущейся работе детектор молчит и не глушит норму",
                 движется.check(действие, наблюдение("шаг-3")).tripped is False,
                 f"вердикт={движется.check(действие, наблюдение('шаг-3'))}")

    # Тот же цикл на экране, который ОТВЕЧАЕТ, доходит до COMPLETED без срабатываний.
    class ЖивойЭкран:
        def __init__(self) -> None:
            self.нажато = 0

        def _снимок(self, generation):
            текст = "готово" if self.нажато else "исходный экран"
            return Observation(new_id("obs"), time.time(),
                               {"app": ПРИЛОЖЕНИЕ, "title": текст}, текст,
                               {"elements": [{"control_type": "button", "name": "Готово"}]},
                               None, False, generation)

        async def observe(self, *, generation):
            return self._снимок(generation)

        async def probe(self, *, generation):
            return self._снимок(generation)

    class ШаговыйПланировщик:
        def __init__(self, экран) -> None:
            self.экран = экран

        async def next_action(self, **kwargs):
            if self.экран.нажато:
                return ComputerAction(id=new_id("act"), kind=ActionKind.COMPLETE,
                                      expected=ExpectedState(contains_text="готово"),
                                      args={}, confidence=1.0)
            return ComputerAction(id=new_id("act"), kind=ActionKind.UI_INVOKE,
                                  expected=ExpectedState(contains_text="готово"),
                                  target="Готово", args={}, confidence=1.0)

    class РаботающийРоутер:
        def __init__(self, экран) -> None:
            self.экран = экран
            self.исполнено = 0

        async def execute(self, action, observation):
            self.исполнено += 1
            self.экран.нажато += 1
            return "owner-desktop"

    живой = ЖивойЭкран()
    события2: list[dict] = []
    manager2 = ComputerOperatorManager(
        store=JsonTaskStore(ctx.path("оператор2", "tasks.json")),
        planner=ШаговыйПланировщик(живой), observer=живой,
        action_router=РаботающийРоутер(живой), approval_create=нельзя, approval_wait=нельзя,
        event_emit=lambda topic, **kw: события2.append(kw))
    задача2 = manager2.create_task("нажать кнопку Готово")
    состояние2 = asyncio.run(manager2.run(задача2.id))
    ctx.negative("нормальная работа не принимается за петлю",
                 состояние2 is TaskState.COMPLETED
                 and not any(e.get("event") == "loop_guard" for e in события2),
                 f"состояние={состояние2} события={[e.get('event') for e in события2]}")
