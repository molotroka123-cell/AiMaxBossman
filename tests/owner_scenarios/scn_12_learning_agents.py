"""Владельческие сценарии 46–50: обучение и много агентов.

* OS-46 — планировщик → исполнитель → проверяющий доходит до конца, и
  проверяющий МОЖЕТ отвергнуть работу исполнителя.
* OS-47 — родитель НЕ завершается, пока ребёнок не подтверждён.
* OS-48 — продвижение выученного требует улики: `TRUST_CRITICAL_KINDS` не
  продвигаются никогда, `PROMOTABLE_KINDS` — только с подтверждением.
* OS-49 — неудача одного агента не роняет всю цепочку молча: она называется.
* OS-50 — одна и та же задача, поданная дважды, не порождает два исполнения.

ПОЧЕМУ ЗДЕСЬ НЕТ ЖЁСТКО ЗАШИТЫХ АГЕНТОВ. Владелец прямо запретил изображать
агентов строками. Поэтому цепочка ведётся НАСТОЯЩИМ продуктом:
`bossman_v3.organization.OrganizationRuntime` (отделы, рынок способностей,
команды по риску, казначейство, ревью), `DeterministicPlanner` (шаги),
`V3ExecutionBridge` + `UniversalComputerAgent` (политика → одобрение →
исполнение → свежее наблюдение → проверка → подписанный журнал) и
`ContractReviewer` (независимость проверяющего). Подменены ровно те же внешние
порты, что и в `spine_fixtures`: политика владельца, одобрение — и инструмент,
который ЗАПУСКАЕТ НАСТОЯЩУЮ КОМАНДУ в песочнице и НАСТОЯЩИЙ наблюдатель
файловой системы. Ни один ответ модели здесь не выдумывается: модель в этой
цепочке не участвует вовсе, поэтому `ai_key` в требованиях не объявлен —
объявлять требование, которого у сценария нет, было бы такой же неправдой.

ПРИВЯЗКА К РЕЕСТРУ — см. `scn_11_context_depth`: пока строки OS-41…OS-50 не
сведены из `registry_41_50.json` в канонический `owner_scenarios.json`,
реализации не регистрируются и прогон двадцати сценариев не ломается.

Зависимости: стандартная библиотека, `bossman_shared` и `bossman`/`bossman_v3`
(способность `bossman_core`, импорт ВНУТРИ сценариев). Ни sqlalchemy, ни
asyncpg — ни прямо, ни транзитивно (BL-085).
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import PRODUCT_CONTRACTS, scenario  # noqa: E402

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------- песочница организации
DEPARTMENT = "engineering"
CAPABILITY = "terminal.run"


class World:
    """Настоящая песочница с настоящими побочными эффектами.

    `runs` — сколько команд ДЕЙСТВИТЕЛЬНО запущено. Это и есть счётчик внешних
    эффектов: дубликат виден как вторая строка, а не как чьё-то мнение.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs: list[str] = []
        self.crash_on: set[str] = set()   # цель, на которой инструмент ПАДАЕТ
        self.silent_on: set[str] = set()  # цель, где «чек есть, эффекта нет»

    def files(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir())

    def target_of(self, action) -> str:
        return Path(str(dict(action.args.get("expect") or {}).get("target", ""))).name


def _ports():
    """Классы портов строятся лениво: `bossman_v3` есть только при способности."""
    from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt,  # noqa: PLC0415
                                      Observation, PolicyDecision, VerificationResult)

    class OwnerPolicy:
        """Политика владельца. Единственный подменённый административный порт."""

        def authorize(self, action, context):
            return PolicyDecision(action.action_type == CAPABILITY)

    class OwnerApproval:
        def request(self, action, policy, context):
            return ApprovalDecision(True, approval_id="ap-владелец")

    class Terminal:
        """Инструмент, который ЗАПУСКАЕТ команду. Ничего не изображается."""

        def __init__(self, world: World, agent_id: str) -> None:
            self.world, self.agent_id = world, agent_id

        def supports(self, action_type):
            return action_type == CAPABILITY

        def execute(self, action):
            started = datetime.now(timezone.utc)
            target = self.world.target_of(action)
            if target in self.world.crash_on:
                raise RuntimeError(f"инструмент упал на цели {target}")
            if target not in self.world.silent_on:
                command = str(action.args["command"])
                env = {"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8"}
                if os.name == "nt":
                    # POSIX shlex would eat the backslashes of the repr()'d Windows
                    # path the planner writes ('C:\\Users' -> 'C:\Users', a \U escape);
                    # CreateProcess keeps them. python.exe also needs SYSTEMROOT.
                    if command.startswith("python "):
                        command = subprocess.list2cmdline([sys.executable]) + command[len("python"):]
                    argv = command
                    env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
                else:
                    argv = shlex.split(command)
                    if argv and argv[0] == "python":
                        argv[0] = sys.executable
                subprocess.run(argv, cwd=str(action.args.get("cwd") or self.world.root),
                               capture_output=True, text=True, timeout=60, env=env)
                self.world.runs.append(target)
            return ExecutionReceipt(CAPABILITY, started, datetime.now(timezone.utc),
                                    effect_id=f"eff-{target}-{len(self.world.runs)}")

    class FileSystem:
        """Свежее наблюдение НАСТОЯЩЕЙ файловой системы, а не памяти процесса."""

        def observe_fresh(self, action, receipt):
            target = Path(str(dict(action.args.get("expect") or {}).get("target", "")))
            return Observation(observed_at=datetime.now(timezone.utc), source="fs",
                               state={"exists": target.exists()})

    class Verifier:
        def verify(self, action, receipt, observation):
            ok = bool(observation.state.get("exists"))
            return VerificationResult(ok, "" if ok else "файла нет после исполнения")

    return OwnerPolicy, OwnerApproval, Terminal, FileSystem, Verifier


def contract_for(world: World, work_id: str, name: str, *, mission: str,
                 deps=(), max_attempts: int = 1, on_failure: str = "fail", risk=None):
    """Контракт БЕЗ шагов: шаги обязан выдать планировщик продукта."""
    from bossman_v3.organization import (DelegationContract, EscalationPolicy,  # noqa: PLC0415
                                         EvidenceRequirement, Resources, RiskTier)
    target = str(world.root / name)
    return DelegationContract(
        work_id=work_id, mission_id=mission, department_id=DEPARTMENT,
        goal=f"создай файл {target} с текстом «готово-{work_id}»",
        required_capability=CAPABILITY, success_criteria=["файл существует"],
        evidence_required=[EvidenceRequirement("file", target)],
        budget=Resources(usd=1.0, tokens=1000, compute_seconds=120),
        risk=risk or RiskTier.MEDIUM, dependencies=list(deps),
        escalation=EscalationPolicy(max_attempts=max_attempts, on_failure=on_failure),
        steps=[])


def boot(tmp: Path, world: World, *, reviewer_model: str = "llama", reviewer: object = None,
         reactions: list | None = None):
    """Поднять организацию на том же диске: это и есть «перезапуск процесса»."""
    from bossman_v3.computer_agent.agent import UniversalComputerAgent  # noqa: PLC0415
    from bossman_v3.organization import (EXECUTOR, REVIEWER, AgentProfile,  # noqa: PLC0415
                                         Department, DeterministicPlanner,
                                         OrganizationRuntime, OrganizationStore,
                                         RecordingHumanReview, RecordingReporter,
                                         Resources, V3ExecutionBridge)
    policy_cls, approval_cls, terminal_cls, fs_cls, verifier_cls = _ports()

    def agent_factory(agent_id, contract):
        return UniversalComputerAgent(policy_cls(), approval_cls(),
                                      terminal_cls(world, agent_id), fs_cls(), verifier_cls())

    human, reporter = RecordingHumanReview(), RecordingReporter()
    runtime = OrganizationRuntime(
        store=OrganizationStore(tmp / "org.sqlite"),
        execution=V3ExecutionBridge(agent_factory=agent_factory,
                                    journal_root=tmp / "journals",
                                    failure_memory_for=lambda d: None),
        human_review=human, reporter=reporter, reviewer=reviewer,
        reactions=list(reactions or []),
        planner=DeterministicPlanner({CAPABILITY}.__contains__, workspace=str(world.root)),
        failure_root=str(tmp / "failures"))
    runtime.set_organization_budget(Resources(usd=100, tokens=1_000_000,
                                              compute_seconds=100_000))
    runtime.register_department(Department(DEPARTMENT, purpose="код",
                                           capabilities={CAPABILITY},
                                           budget=Resources(usd=50, tokens=500_000,
                                                            compute_seconds=50_000)))
    runtime.register_agent(AgentProfile("coder-local", DEPARTMENT, {EXECUTOR}, {CAPABILITY},
                                        tier="local_small", model="glm"))
    runtime.register_agent(AgentProfile("reviewer", DEPARTMENT, {REVIEWER}, {CAPABILITY},
                                        tier="local_small", model=reviewer_model))
    return runtime, human, reporter


def org(ctx, folder: str, *, reviewer_model: str = "llama", reviewer: object = None,
        reactions: list | None = None):
    tmp = ctx.path(folder, "организация")
    tmp.mkdir(parents=True, exist_ok=True)
    world = World(tmp / "мир")
    runtime, human, reporter = boot(tmp, world, reviewer_model=reviewer_model,
                                    reviewer=reviewer, reactions=reactions)
    return tmp, world, runtime, human, reporter


def events(runtime, mission: str) -> list[str]:
    return [row["event"] for row in runtime.store.tail(100, mission_id=mission)]


# ------------------------- OS-46: планировщик → исполнитель → проверяющий
@scenario(id="OS-46", depth=PRODUCT_CONTRACTS)
def os46_planner_executor_reviewer(ctx) -> None:
    """Цепочка доходит до конца, и проверяющий может её остановить."""
    from bossman_v3.organization import (ContractReviewer, MissionState,  # noqa: PLC0415
                                         ReviewVerdict, TaskState, WorkResult)

    tmp, world, runtime, human, reporter = org(ctx, "прямая")
    runtime.receive_mission("m-46", title="релиз", department_id=DEPARTMENT,
                            source="владелец",
                            contracts=[contract_for(world, "w1", "первый.txt", mission="m-46"),
                                       contract_for(world, "w2", "второй.txt", mission="m-46",
                                                    deps=["w1"])])
    status = runtime.run_mission("m-46")
    work = runtime.store.work("w1")
    result = runtime.store.result("w1")

    ctx.positive("ПЛАНИРОВЩИК выдал шаги контракту, который пришёл без них",
                 work["contract"].metadata.get("planned_by") == "DeterministicPlanner"
                 and bool(work["contract"].steps)
                 and "work.planned" in events(runtime, "m-46"),
                 f"шагов={len(work['contract'].steps)}")
    ctx.positive("ИСПОЛНИТЕЛЬ произвёл НАСТОЯЩИЙ внешний эффект",
                 world.files() == ["второй.txt", "первый.txt"] and len(world.runs) == 2,
                 f"запусков={len(world.runs)} файлы={world.files()}")
    ctx.positive("ПРОВЕРЯЮЩИЙ независим и подтвердил улику из журнала",
                 result.reviewed_by == "reviewer" and result.produced_by == "coder-local"
                 and result.metadata["review"]["independent"] is True
                 and result.evidence[0].source.startswith("journal:m-46__w1/"),
                 f"улик={len(result.evidence)}")
    ctx.positive("цепочка ДОШЛА ДО КОНЦА: миссия завершена и подтверждена",
                 status.state == MissionState.COMPLETED.value and status.done
                 and status.verified_results == ("w1", "w2")
                 and reporter.statuses[-1].verified_results == ("w1", "w2"),
                 f"состояние={status.state}")

    # ------- ПРОВЕРЯЮЩИЙ МОЖЕТ ОТВЕРГНУТЬ: ревьюер продукта, живые данные
    reviewer = ContractReviewer(runtime.marketplace)
    contract = work["contract"]
    ctx.positive("независимый проверяющий ПОДТВЕРЖДАЕТ перепроверенную улику",
                 reviewer.review(contract, result, reviewer_id="reviewer",
                                 producer_id="coder-local").approved is True)
    self_review = reviewer.review(contract, result, reviewer_id="coder-local",
                                  producer_id="coder-local")
    ctx.negative("самопроверка отвергнута: исполнитель себе не проверяющий",
                 self_review.approved is False and self_review.independent is False,
                 self_review.reason)
    empty = reviewer.review(contract, WorkResult("w1", executed=True),
                            reviewer_id="reviewer", producer_id="coder-local")
    ctx.negative("работа БЕЗ улики отвергается проверяющим",
                 empty.approved is False and "missing evidence" in empty.reason,
                 empty.reason)

    # ------- вето доходит до состояния работы, а не остаётся мнением
    _, veto_world, veto_rt, veto_human, _ = org(ctx, "вето", reviewer=_LyingReviewer())
    veto_rt.receive_mission("m-46v", title="вето", department_id=DEPARTMENT,
                            contracts=[contract_for(veto_world, "v1", "первый.txt",
                                                    mission="m-46v")])
    veto_status = veto_rt.run_mission("m-46v")
    veto_result = veto_rt.store.result("v1")
    ctx.negative("ВЕТО проверяющего не даёт работе стать выполненной",
                 veto_rt.store.work("v1")["state"] == TaskState.FAILED.value
                 and veto_status.verified_results == ()
                 and veto_status.quality["review_vetoes"] >= 1
                 and veto_result.metadata.get("review_veto") is True,
                 f"ошибки контракта={veto_result.contract_errors}")
    ctx.negative("вето НАЗВАНО причиной, а не спрятано в молчании",
                 any("review veto" in e for e in veto_result.contract_errors)
                 and veto_status.state != MissionState.COMPLETED.value,
                 f"состояние миссии={veto_status.state}")

    # ------- без НЕЗАВИСИМОГО проверяющего цепочка не идёт вовсе
    _, alone_world, alone_rt, alone_human, _ = org(ctx, "клон", reviewer_model="glm")
    alone_rt.receive_mission("m-46a", title="клон", department_id=DEPARTMENT,
                             contracts=[contract_for(alone_world, "a1", "первый.txt",
                                                     mission="m-46a")])
    alone_status = alone_rt.run_mission("m-46a")
    ctx.negative("проверяющий ТОЙ ЖЕ модели не сажается в команду, и работа не идёт",
                 alone_status.state != MissionState.COMPLETED.value
                 and alone_world.runs == [] and bool(alone_human.requests)
                 and "reviewer" in str(alone_status.blockers),
                 f"блокеры={alone_status.blockers}")
    ctx.negative("ReviewVerdict проверяющего не умеет одобрять неподтверждённое",
                 ReviewVerdict("reviewer", False, "нет улики", False).approved is False)


class _LyingReviewer:
    """Проверяющий, «одобряющий» под ЧУЖОЙ личностью.

    Это не заглушка, дающая PASS: она даёт ОТКАЗ. Рантайм обязан не поверить
    вердикту, подписанному не тем, кого он спрашивал, — иначе одобрение можно
    было бы принести со стороны.
    """

    def review(self, contract, result, *, reviewer_id, producer_id):
        from bossman_v3.organization import ReviewVerdict  # noqa: PLC0415
        return ReviewVerdict("кто-то-другой", True, "всё отлично", True)


# ------------------------------- OS-47: родитель ждёт подтверждённого ребёнка
@scenario(id="OS-47", depth=PRODUCT_CONTRACTS)
def os47_parent_waits_for_a_confirmed_child(ctx) -> None:
    """Родитель не завершается, пока ребёнок не подтверждён уликой."""
    from bossman_v3.organization import MissionState, TaskState  # noqa: PLC0415

    # --- ПОЛОЖИТЕЛЬНО: ребёнок подтверждён → родитель идёт и доходит до конца.
    tmp, world, runtime, human, _ = org(ctx, "по-порядку")
    runtime.receive_mission("m-47", title="по порядку", department_id=DEPARTMENT,
                            contracts=[contract_for(world, "child", "ребёнок.txt",
                                                    mission="m-47"),
                                       contract_for(world, "parent", "родитель.txt",
                                                    mission="m-47", deps=["child"])])
    status = runtime.run_mission("m-47")
    ctx.positive("родитель пошёл ТОЛЬКО после подтверждённого ребёнка",
                 world.runs == ["ребёнок.txt", "родитель.txt"]
                 and status.verified_results == ("child", "parent")
                 and status.state == MissionState.COMPLETED.value,
                 f"порядок запусков={world.runs}")
    ctx.positive("подтверждение ребёнка опирается на улику, а не на слово",
                 runtime.store.result("child").verified is True
                 and runtime.store.result("child").evidence[0].verified is True)

    # --- ОТРИЦАТЕЛЬНО: ребёнок ЗАЯВИЛ успех, эффекта нет → родитель не идёт.
    _, liar_world, liar_rt, liar_human, liar_reporter = org(ctx, "лжец")
    liar_world.silent_on = {"ребёнок.txt"}
    liar_rt.receive_mission("m-47l", title="лжец", department_id=DEPARTMENT,
                            contracts=[contract_for(liar_world, "child", "ребёнок.txt",
                                                    mission="m-47l"),
                                       contract_for(liar_world, "parent", "родитель.txt",
                                                    mission="m-47l", deps=["child"])])
    liar_status = liar_rt.run_mission("m-47l")
    parent = liar_rt.store.work("parent")
    ctx.negative("неподтверждённый ребёнок НЕ пропускает родителя",
                 parent["state"] == TaskState.BLOCKED.value
                 and "child" in str((parent["contract"].metadata.get("runtime")
                                       or {}).get("last_reason", "")),
                 f"состояние родителя={parent['state']}")
    ctx.negative("эффект родителя НЕ случился: цепочка остановлена до него",
                 "родитель.txt" not in liar_world.files()
                 and "родитель.txt" not in liar_world.runs,
                 f"файлы={liar_world.files()}")
    ctx.negative("миссия не объявлена завершённой, пока ребёнок не подтверждён",
                 not liar_status.done
                 and liar_status.state != MissionState.COMPLETED.value
                 and liar_status.verified_results == (),
                 f"состояние={liar_status.state} качество={liar_status.quality}")
    ctx.negative("заявленный успех ребёнка посчитан ложным, а не принят",
                 liar_status.quality["false_success_attempts"] >= 1
                 and liar_rt.store.result("child").verified is False,
                 f"ложных успехов={liar_status.quality['false_success_attempts']}")

    # --- ОТРИЦАТЕЛЬНО: ребёнок упал → родитель по-прежнему не идёт.
    _, dead_world, dead_rt, dead_human, _ = org(ctx, "падение-ребёнка")
    dead_world.crash_on = {"ребёнок.txt"}
    dead_rt.receive_mission("m-47d", title="падение", department_id=DEPARTMENT,
                            contracts=[contract_for(dead_world, "child", "ребёнок.txt",
                                                    mission="m-47d"),
                                       contract_for(dead_world, "parent", "родитель.txt",
                                                    mission="m-47d", deps=["child"])])
    dead_status = dead_rt.run_mission("m-47d")
    ctx.negative("упавший ребёнок не открывает родителю дорогу",
                 dead_status.failed == ("child",)
                 and dead_rt.store.work("parent")["state"] == TaskState.BLOCKED.value
                 and dead_world.runs == [],
                 f"провалено={dead_status.failed}")
    ctx.refused("контракт, зависящий от несуществующей работы, не принимается вовсе",
                lambda: dead_rt.receive_mission(
                    "m-47x", title="висяк", department_id=DEPARTMENT,
                    contracts=[contract_for(dead_world, "orphan", "сирота.txt",
                                            mission="m-47x", deps=["no-such-work"])]),
                ValueError)


# --------------------------------- OS-48: продвижение выученного требует улики
APPLICABILITY = "python.refactor"
#: Отметка вида «парного измерения», которую требует гейт продвижения. Число в
#: ней — sha256 НАСТОЯЩЕГО отчёта, снятого в этом прогоне A/B-оценщиком
#: продукта, а не константа из теста.
LANE = "intelligence_preservation/paired/"


def _baseline(n: int) -> int:
    """Базовая версия навыка: промахивается на каждом третьем входе."""
    return n * 2 if n % 3 else n * 2 + 1


def _candidate(n: int) -> int:
    """Кандидат: считает верно на всех входах."""
    return n * 2


def _overfitted(measured_ids: frozenset[str]):
    """Кандидат, «настроенный» на ИЗМЕРЯЕМУЮ часть набора.

    Именно так выглядит подгонка: на задачах, по которым его подбирали, он
    безупречен, а на отложенных хуже базовой версии. Разбиение берётся у
    продукта (`split_tasks`), а не угадывается по номеру задачи.
    """
    def impl(task_id: str, n: int) -> int:
        return n * 2 if task_id in measured_ids else n * 2 + 7
    return impl


@scenario(id="OS-48", depth=PRODUCT_CONTRACTS)
def os48_promotion_needs_evidence(ctx) -> None:
    """Доверенное ядро — никогда; продвигаемое — только с подтверждением."""
    from bossman.learning_guard.ab import evaluate_ab  # noqa: PLC0415
    from bossman.learning_guard.evidence_ledger import DurableEvidenceLedger  # noqa: PLC0415
    from bossman.learning_guard.models import (ABResult, Candidate,  # noqa: PLC0415
                                               PromotionStage, RollbackInfo,
                                               SecuritySnapshot)
    from bossman.learning_guard.promotion import (SecurityRegression,  # noqa: PLC0415
                                                  advance, promote)
    from bossman_shared.objective_improvement import (PROMOTABLE_KINDS,  # noqa: PLC0415
                                                      REQUIRED_STAGES,
                                                      TRUST_CRITICAL_KINDS,
                                                      CandidateImprovement,
                                                      PromotionEvidence, may_promote)
    from bossman_shared.objective_promotion import (Outcome, Task,  # noqa: PLC0415
                                                    authorize, measure, split_tasks)

    tasks = [Task(task_id=f"t-{index:02d}", applicability=APPLICABILITY)
             for index in range(30)]

    def runner(impl):
        """`run` ПРОГОНЯЕТ реализацию и сверяет ответ. Числа не объявляются."""
        def run(variant: str, task: Task) -> Outcome:
            number = int(task.task_id.split("-")[1])
            produced = (impl(task.task_id, number) if variant == "candidate"
                        else _baseline(number))
            ok = produced == number * 2
            return Outcome(task_id=task.task_id, passed=ok, score=1.0 if ok else 0.0)
        return run

    good = measure(tasks, candidate_id="skill:удвоение", candidate_version="v2",
                   baseline_version="v1", applicability_version="ap-3",
                   run=runner(lambda _task, n: _candidate(n)))
    ctx.positive("измерение реально прогнало ОБЕ полосы по одному набору задач",
                 good.measured["candidate"].total >= 15 and good.holdout["candidate"].total >= 5
                 and good.measured["candidate"].pass_rate > good.measured["baseline"].pass_rate,
                 f"измеряемая={good.measured['candidate'].passed}/"
                 f"{good.measured['candidate'].total}, "
                 f"отложенная={good.holdout['candidate'].passed}/"
                 f"{good.holdout['candidate'].total}")

    # Сохранение способности меряется A/B-оценщиком ПРОДУКТА, а не объявляется.
    verdict_ab = evaluate_ab([
        ABResult(task_id=task.task_id, task_class=APPLICABILITY,
                 raw_verified=_baseline(int(task.task_id.split("-")[1]))
                 == int(task.task_id.split("-")[1]) * 2,
                 guarded_verified=True, scope_ref="corpus-1") for task in tasks])
    report = {"lane": LANE.rstrip("/"), "episodes": verdict_ab.episodes,
              "raw": verdict_ab.raw_success, "guarded": verdict_ab.guarded_success,
              "retention": verdict_ab.intelligence_retention}
    ref = LANE + hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    ctx.positive("ссылка на парное измерение собрана из ИЗМЕРЕННЫХ чисел продукта",
                 verdict_ab.passing and verdict_ab.episodes == 30
                 and len(ref) == len(LANE) + 64,
                 "ПРОБЕЛ ПРОДУКТА: ни один модуль не чеканит ссылку "
                 "intelligence_preservation/paired/<64hex>, которую требует "
                 "bossman_shared/objective_improvement.py:63 — здесь она собрана "
                 "из отчёта bossman.learning_guard.ab.evaluate_ab этого прогона")

    ledger = DurableEvidenceLedger(ctx.path("журнал", "ledger.json"))
    promotable = CandidateImprovement(kind="skill", current_version="v1",
                                      candidate_version="v2",
                                      hypothesis="меньше промахов на каждом третьем",
                                      completed_stages=REQUIRED_STAGES)

    def decide(measurement, candidate, *, led=None, retention=None, reference=None,
               security=True, rollback=True, version="ap-3", scope=(APPLICABILITY,)):
        return authorize(measurement, candidate, ledger=led or ledger,
                         applicability_version=version, applicability_scope=scope,
                         retention=(verdict_ab.intelligence_retention
                                    if retention is None else retention),
                         retention_evidence_ref=ref if reference is None else reference,
                         security_pass=security, rollback_available=rollback)

    allowed = decide(good, promotable)
    ctx.positive("ПОДТВЕРЖДЁННЫЙ кандидат продвигаемого вида получает разрешение",
                 allowed.authorized and allowed.reason == "eligible_for_controlled_canary"
                 and allowed.consumer == "skill:удвоение@v2",
                 f"улика={allowed.evidence_key[:16]}")
    ctx.positive("повтор того же решения тем же кандидатом — не переигрывание",
                 decide(good, promotable).authorized is True)

    # --- ДОВЕРЕННОЕ ЯДРО: никогда, каким бы хорошим ни выглядело измерение.
    perfect = PromotionEvidence(baseline_score=0.0, candidate_score=1.0,
                                intelligence_retention=1.0, retention_evidence_ref=ref,
                                security_pass=True, rollback_available=True,
                                sample_count=1000)
    refusals = {kind: may_promote(CandidateImprovement(kind=kind, current_version="v1",
                                                       candidate_version="v2",
                                                       hypothesis="идеально",
                                                       completed_stages=REQUIRED_STAGES),
                                  perfect)
                for kind in sorted(TRUST_CRITICAL_KINDS)}
    ctx.negative("НИ ОДИН вид доверенного ядра не продвигается даже с идеальной уликой",
                 len(refusals) == 7
                 and all(not ok and why == "trust_critical_kind_never_auto_promoted"
                         for ok, why in refusals.values()),
                 f"видов проверено={len(refusals)}")
    ctx.positive("а продвигаемые виды с той же уликой разрешение получают",
                 all(may_promote(CandidateImprovement(kind=kind, current_version="v1",
                                                      candidate_version="v2",
                                                      hypothesis="идеально",
                                                      completed_stages=REQUIRED_STAGES),
                                 perfect)[0] for kind in sorted(PROMOTABLE_KINDS)),
                 f"видов={sorted(PROMOTABLE_KINDS)}")
    ctx.negative("вид вне белого списка не продвигается по умолчанию",
                 may_promote(CandidateImprovement(kind="policy_hook", current_version="v1",
                                                  candidate_version="v2", hypothesis="х",
                                                  completed_stages=REQUIRED_STAGES),
                             perfect) == (False, "unpromotable_kind"))
    ctx.negative("пропуск стадии конвейера назван по имени пропущенной стадии",
                 may_promote(CandidateImprovement(kind="skill", current_version="v1",
                                                  candidate_version="v2", hypothesis="х",
                                                  completed_stages=("observe",)),
                             perfect)[1].startswith("pipeline_stage_skipped:"))

    # --- ПОДТВЕРЖДЕНИЕ ОБЯЗАТЕЛЬНО: каждая нехватка отказывает своим именем.
    overfit_split = split_tasks(tasks, material="skill:подгонка:ap-3")
    overfit = measure(tasks, candidate_id="skill:подгонка", candidate_version="v2",
                      baseline_version="v1", applicability_version="ap-3",
                      split=overfit_split,
                      run=runner(_overfitted(frozenset(overfit_split.measured))))
    overfit_candidate = CandidateImprovement(kind="skill", current_version="v1",
                                             candidate_version="v2",
                                             hypothesis="выиграть на измеряемых",
                                             completed_stages=REQUIRED_STAGES)
    named = {
        "подгонка под измеряемую часть": decide(overfit, overfit_candidate).reason,
        "ссылка не на парное измерение": decide(good, promotable,
                                                reference="измерено, верьте").reason,
        "сохранение способности ниже порога": decide(good, promotable, retention=0.5).reason,
        "красная команда не пройдена": decide(good, promotable, security=False).reason,
        "откат недоступен": decide(good, promotable, rollback=False).reason,
        "применимость сменила версию": decide(good, promotable, version="ap-4").reason,
        "измерено вне объявленной области": decide(good, promotable,
                                                   scope=("go.refactor",)).reason,
    }
    ctx.negative("каждая нехватка подтверждения отказывает СВОИМ именем",
                 named["подгонка под измеряемую часть"] == "holdout_regression"
                 and named["ссылка не на парное измерение"]
                 == "retention_not_bound_to_paired_measurement"
                 and named["сохранение способности ниже порога"] == "intelligence_regression"
                 and named["красная команда не пройдена"] == "red_team_failed"
                 and named["откат недоступен"] == "rollback_unavailable"
                 and named["применимость сменила версию"] == "applicability_version_changed"
                 and named["измерено вне объявленной области"]
                 == "measured_outside_declared_applicability",
                 json.dumps(named, ensure_ascii=False)[:300])

    # --- ОДНОРАЗОВОСТЬ УЛИКИ ПЕРЕЖИВАЕТ ПЕРЕЗАПУСК.
    other_version = CandidateImprovement(kind="skill", current_version="v1",
                                         candidate_version="v9", hypothesis="то же самое",
                                         completed_stages=REQUIRED_STAGES)
    same_numbers = measure(tasks, candidate_id="skill:удвоение", candidate_version="v9",
                           baseline_version="v1", applicability_version="ap-3",
                           run=runner(lambda _task, n: _candidate(n)))
    reopened = DurableEvidenceLedger(ctx.path("журнал", "ledger.json"))
    ctx.negative("та же улика не продвигает ДРУГУЮ версию даже после перезапуска",
                 decide(same_numbers, other_version, led=reopened).reason
                 == "evidence_already_spent",
                 f"ключ улики совпал={same_numbers.evidence_key == good.evidence_key}")

    # --- КОНВЕЙЕР ОБУЧЕНИЯ: владелец, откат, неоптимизируемый security-гейт.
    before = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0, scope_ref="corpus-1")
    after = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0, scope_ref="corpus-1")
    staged = Candidate(kind="skill", ref="skill:удвоение@v2")
    for _ in range(3):
        staged = advance(staged, ab=verdict_ab, security_before=before,
                         security_after=after, shadow_runs=25)
    ctx.positive("кандидат дошёл до VERIFIED по измеренному A/B, а не по самооценке",
                 staged.stage is PromotionStage.VERIFIED and staged.security_proven is True
                 and staged.reasons == ())
    rollback = RollbackInfo(prev_stage="VERIFIED", prev_ref="skill:удвоение@v1")
    ctx.negative("без решения владельца продвижения в продакшн НЕ происходит",
                 promote(staged, owner_approved=False, rollback=rollback).stage
                 is PromotionStage.VERIFIED)
    ctx.positive("с решением владельца и метаданными отката продвижение состоялось",
                 promote(staged, owner_approved=True, rollback=rollback).stage
                 is PromotionStage.OWNER_PROMOTED)
    ctx.refused("просевший security-гейт блокирует продвижение при любом росте",
                lambda: advance(Candidate(kind="skill", ref="skill:удвоение@v2"),
                                ab=verdict_ab, security_before=before,
                                security_after=SecuritySnapshot(leaks=1, bypasses=0,
                                                                containment_rate=1.0,
                                                                scope_ref="corpus-1"),
                                shadow_runs=25),
                SecurityRegression)


# ------------------------------- OS-49: неудача одного агента НАЗЫВАЕТСЯ
@scenario(id="OS-49", depth=PRODUCT_CONTRACTS)
def os49_one_agent_failure_is_named(ctx) -> None:
    """Падение одного исполнителя не уносит цепочку и не остаётся молчаливым."""
    from bossman_v3.organization import MissionState, TaskState  # noqa: PLC0415

    tmp, world, runtime, human, reporter = org(ctx, "падение")
    world.crash_on = {"упадёт.txt"}
    runtime.receive_mission(
        "m-49", title="смешанная миссия", department_id=DEPARTMENT,
        contracts=[contract_for(world, "crashes", "упадёт.txt", mission="m-49"),
                   contract_for(world, "neighbour", "сосед.txt", mission="m-49"),
                   contract_for(world, "descendant", "потомок.txt", mission="m-49",
                                deps=["crashes"])])
    status = runtime.run_mission("m-49")
    failed = runtime.store.result("crashes")

    ctx.positive("НЕЗАВИСИМАЯ соседняя работа дошла до конца, несмотря на падение",
                 "neighbour" in status.completed and "neighbour" in status.verified_results
                 and "сосед.txt" in world.files(),
                 f"выполнено={status.completed}")
    ctx.positive("падение НАЗВАНО причиной с типом ошибки инструмента",
                 status.failed == ("crashes",)
                 and "RuntimeError" in str(status.blockers)
                 and "упадёт.txt" in str(status.blockers),
                 f"блокеры={status.blockers}")
    ctx.positive("падение записано в долговечный журнал организации",
                 "work.failed" in events(runtime, "m-49")
                 and "work.blocked" in events(runtime, "m-49"),
                 f"события={sorted(set(events(runtime, 'm-49')))}")
    ctx.positive("слой миссий получил отчёт, в котором провал виден",
                 reporter.statuses and reporter.statuses[-1].failed == ("crashes",)
                 and reporter.statuses[-1].state != MissionState.COMPLETED.value,
                 f"отчётов={len(reporter.statuses)}")
    # Счётчики обучения затухающие: сравнивать их с целым числом нельзя —
    # «>= 1» проваливалось бы на первом же затухании, ничего не проверяя.
    learned = runtime.learning.stats("coder-local", CAPABILITY)
    ctx.positive("обучение зафиксировало И неудачу, И успех соседней работы",
                 learned.failures > 0 and learned.verified_success > 0,
                 f"неудач={learned.failures:.3f} подтверждённых успехов="
                 f"{learned.verified_success:.3f}")

    ctx.negative("упавшая работа НЕ засчитана выполненной",
                 "crashes" not in status.completed and "crashes" not in status.verified_results
                 and failed.executed is False and failed.success is False,
                 f"executed={failed.executed}")
    ctx.negative("зависимая работа остановлена, а не тихо пропущена",
                 runtime.store.work("descendant")["state"] == TaskState.BLOCKED.value
                 and "потомок.txt" not in world.files()
                 and any(b["work_id"] == "descendant" and b["reason"] for b in status.blockers),
                 f"состояние потомка={runtime.store.work('descendant')['state']}")
    ctx.negative("миссия НЕ объявлена завершённой при одном провале",
                 not status.done and status.state == MissionState.FAILED.value
                 and status.progress < 1.0,
                 f"состояние={status.state} прогресс={status.progress:.2f}")
    ctx.negative("ни один провал не остался без текста причины",
                 all(str(b["reason"]).strip() for b in status.blockers),
                 f"блокеров={len(status.blockers)}")

    # Политика эскалации владельца доводит провал ЛИЧНО до него.
    _, ask_world, ask_rt, ask_human, _ = org(ctx, "спросить-владельца")
    ask_world.crash_on = {"упадёт.txt"}
    ask_rt.receive_mission("m-49a", title="спросить владельца", department_id=DEPARTMENT,
                           contracts=[contract_for(ask_world, "crashes", "упадёт.txt",
                                                   mission="m-49a", max_attempts=2,
                                                   on_failure="ask_owner")])
    ask_status = ask_rt.run_mission("m-49a")
    ctx.positive("при политике «спросить владельца» провал доходит ДО ВЛАДЕЛЬЦА",
                 bool(ask_human.requests) and ask_human.requests[-1][0] == "crashes"
                 and "RuntimeError" in ask_human.requests[-1][1]
                 and ask_status.state != MissionState.COMPLETED.value,
                 f"запросов владельцу={len(ask_human.requests)}")


# ----------------------- OS-50: одна задача дважды — одно исполнение
@scenario(id="OS-50", depth=PRODUCT_CONTRACTS)
def os50_same_task_twice_runs_once(ctx) -> None:
    """Повторная подача той же задачи не порождает второго исполнения."""
    from bossman_v3.organization import MissionState  # noqa: PLC0415

    from bossman_v3.organization import EvidenceRequirement, Reaction, RiskTier  # noqa: PLC0415

    reaction = Reaction("ci.failed", DEPARTMENT, CAPABILITY, "триаж {job}",
                        evidence=(EvidenceRequirement("file", "триаж.txt"),),
                        risk=RiskTier.LOW)
    tmp, world, runtime, human, _ = org(ctx, "дважды", reactions=[reaction])
    runtime.receive_mission("m-50", title="однократно", department_id=DEPARTMENT,
                            contracts=[contract_for(world, "w1", "единственный.txt",
                                                    mission="m-50")])
    first = runtime.run_mission("m-50")
    ctx.positive("первая подача дала ровно один внешний эффект",
                 first.state == MissionState.COMPLETED.value and world.runs == ["единственный.txt"],
                 f"запусков={world.runs}")

    ctx.refused("ТА ЖЕ миссия не принимается второй раз",
                lambda: runtime.receive_mission(
                    "m-50", title="однократно", department_id=DEPARTMENT,
                    contracts=[contract_for(world, "w1", "единственный.txt", mission="m-50")]),
                ValueError)
    ctx.refused("тот же work_id нельзя перенести в другую миссию",
                lambda: runtime.receive_mission(
                    "m-50-other", title="обход", department_id=DEPARTMENT,
                    contracts=[contract_for(world, "w1", "единственный.txt",
                                            mission="m-50-other")]),
                ValueError)

    runtime.run_mission("m-50")
    runtime.run_mission("m-50")
    ctx.negative("повторный прогон завершённой миссии НЕ исполняет её заново",
                 world.runs == ["единственный.txt"], f"запусков={world.runs}")
    # Отдельная проверка, а не то же самое другими словами: отсутствие второго
    # ЭФФЕКТА держит ещё и журнал шагов ниже. Здесь проверяется, что работа
    # вообще не уходит исполнителю второй раз — то есть два независимых
    # исполнения не начинаются, а не «начинаются и гасятся ниже».
    ctx.negative("завершённая работа НЕ делегируется исполнителю второй раз",
                 events(runtime, "m-50").count("work.delegated") == 1,
                 f"делегирований={events(runtime, 'm-50').count('work.delegated')}")

    # Перезапуск процесса: новая организация на том же диске, тот же журнал.
    revived, revived_human, _ = boot(tmp, world, reactions=[reaction])
    statuses = revived.resume()
    revived_again, _, _ = boot(tmp, world, reactions=[reaction])
    revived_again.resume()
    ctx.negative("перезапуск и возобновление НЕ дублируют внешний эффект",
                 world.runs == ["единственный.txt"]
                 and all(s.state == MissionState.COMPLETED.value for s in statuses),
                 f"запусков после двух перезапусков={world.runs}")
    ctx.positive("после перезапуска организация помнит выполненную работу",
                 revived.store.result("w1") is not None
                 and revived.store.result("w1").verified is True
                 and revived.store.mission("m-50")["state"] == MissionState.COMPLETED.value)

    # Повтор того же СОБЫТИЯ с тем же ключом идемпотентности — тоже один раз.
    outcome = revived.accept_event("ci.failed", {"job": "unit", "idempotency_key": "run-42"})
    duplicate = revived.accept_event("ci.failed", {"job": "unit", "idempotency_key": "run-42"})
    ctx.negative("повтор события с тем же ключом помечен дубликатом",
                 duplicate.duplicate is True and outcome.duplicate is False,
                 f"первый accepted={outcome.accepted}")

    # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ САМОЙ ЗАЩИТЫ: ДРУГАЯ задача обязана исполниться,
    # иначе «дубликатов нет» означало бы всего лишь «ничего не работает».
    revived.receive_mission("m-50-new", title="другая задача", department_id=DEPARTMENT,
                            contracts=[contract_for(world, "w2", "второй.txt",
                                                    mission="m-50-new")])
    new_status = revived.run_mission("m-50-new")
    ctx.positive("НОВАЯ задача при этом исполняется — защита не глушит всё подряд",
                 new_status.state == MissionState.COMPLETED.value
                 and world.runs == ["единственный.txt", "второй.txt"],
                 f"запусков={world.runs}")
