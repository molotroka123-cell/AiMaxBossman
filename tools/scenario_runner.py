"""Каркас ВЛАДЕЛЬЧЕСКИХ сценариев: реестр, прогон, уровень улик, отчёт.

Два табло, которые НИКОГДА не складываются:

* REGRESSION — существующие инженерные наборы (Core, Command Center, Astra,
  Fable, PostgreSQL). Это НЕ определение готовности и сюда не попадает.
* OWNER SCENARIOS — настоящие интегрированные рабочие цепочки владельца.
  Прогресс отчитывается строкой «OWNER SCENARIOS: X / N», где N — размер
  реестра, а не числом коммитов, веток, модульных тестов или документов.
  Числа здесь нет намеренно: строка «/ 50» пережила рост реестра до
  восьмидесяти и стала неверной молча.

Единый канонический реестр — `tests/owner_scenarios/owner_scenarios.json`.
Название, цепочка, требуемые способности и шаг модели берутся ТОЛЬКО оттуда;
код сценария лишь привязывается к id. Расхождение реестра и кода — ошибка
загрузки, а не «ну, один сценарий потерялся».

Каркас построен так, чтобы соврать было структурно трудно:

* Уровни улик строго ограничены :data:`LEVELS`. Слова ``OWNER_LOCAL_MODEL`` и
  ``LOCAL_MODEL_CERTIFIED`` в словаре отсутствуют: прогон идёт через CI-адаптер
  ИИ, локальной модели владельца в нём нет, и приписывать ей заслугу нельзя.
* ГЛУБИНА УЛИКИ пишется ОТДЕЛЬНО у каждого сценария (:data:`DEPTHS`), потому
  что «импорт исходника → вызов функции → PASS» не равно «продукт владельца →
  возможность → PASS». Зелёный уровень без глубины ничего не значит.
* Каждое утверждение — ПАРОЙ: без хотя бы одной положительной и одной
  отрицательной проверки уровень ``INSUFFICIENT_EVIDENCE``.
* Исключение внутри сценария — ``FAIL``, а не «не удалось проверить».
* Недостающая способность среды НЕ заменяется заглушкой: сценарий получает
  честный вердикт с названным блокером и не исполняется вовсе.
* Нет синтетического запасного пути. Живой шаг модели зеленеет ТОЛЬКО от
  настоящего успешного вызова адаптера, посчитанного каркасом по журналу.

Запуск:
    python tools/scenario_runner.py [--json отчёт.json] [--only OS-04 OS-18]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_ai_provider import (BUDGET_EXCEEDED, CIAIProvider, INVALID_RESPONSE,  # noqa: E402
                            NO_KEY, redact)

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "tests" / "owner_scenarios"
REGISTRY_FILE = SCENARIO_DIR / "owner_scenarios.json"

# --------------------------------------------------------------- уровни улик
CI_PROVEN = "CI_PROVEN"
OWNER_HARDWARE_REQUIRED = "OWNER_HARDWARE_REQUIRED"
CREDENTIAL_REQUIRED = "CREDENTIAL_REQUIRED"
OWNER_REQUIRED = "OWNER_REQUIRED"
FAIL = "FAIL"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NOT_RUN = "NOT_RUN"

LEVELS = (CI_PROVEN, OWNER_HARDWARE_REQUIRED, CREDENTIAL_REQUIRED, OWNER_REQUIRED, FAIL,
          INSUFFICIENT_EVIDENCE, NOT_RUN)
#: Единственный зелёный уровень. Всё остальное — не доказано.
GREEN_LEVELS = (CI_PROVEN,)
NOT_PROVEN_LEVELS = (FAIL, INSUFFICIENT_EVIDENCE, NOT_RUN)

# ------------------------------------------------------------ глубина улики
INSTALLED_PRODUCT = "installed_product"
PRODUCT_CONTRACTS = "product_contracts"
ADAPTER_CONTRACT = "adapter_contract"
DEPTHS = (INSTALLED_PRODUCT, PRODUCT_CONTRACTS, ADAPTER_CONTRACT)

MODEL_STEPS = ("live", "contract", "none")


class CredentialRequired(Exception):
    """Нужен реальный credential/provider access. Не hardware и не PASS."""


class OwnerRequired(Exception):
    """Нужно действие владельца, не сводящееся к credential/hardware."""


class OwnerHardwareRequired(Exception):
    """Нужна физическая машина владельца: GPU, камера, браузер, рабочий стол."""


class InsufficientEvidence(Exception):
    """Прогон не доказал ничего: способности нет, провайдер лёг, данных нет."""


@dataclass(frozen=True)
class Check:
    name: str
    kind: str          # "positive" | "negative"
    ok: bool
    detail: str = ""

    def to_report(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class Scenario:
    """Объявление из канонического реестра плюс привязанная реализация."""

    id: str
    number: int
    title: str
    chain: str
    requires: tuple[str, ...]
    model_step: str
    depth: str
    func: Callable[["RunContext"], None]
    #: Объявленный пробел, который ЖДЁТ РЕШЕНИЯ ВЛАДЕЛЬЦА, — фрагмент причины,
    #: который обязан прозвучать в отказе дословно. Пусто — строка обязана
    #: доказываться. Объявление живёт ЗДЕСЬ, в реестре, и читается обоими:
    #: раннером (какой незелёный ожидаем) и замороженной таблицей
    #: `tests/owner_scenarios/test_owner_scenarios.py`. Два списка ожидаемых
    #: пробелов разъехались бы, и владелец узнал бы об этом один раз.
    owner_gap: str = ""

    def to_report(self) -> dict[str, Any]:
        return {"id": self.id, "number": self.number, "title": self.title,
                "chain": self.chain, "requires": list(self.requires),
                "model_step": self.model_step, "declared_depth": self.depth,
                "owner_gap": self.owner_gap}


@dataclass
class ScenarioResult:
    scenario: Scenario
    level: str
    reason: str = ""
    checks: list[Check] = field(default_factory=list)
    depth: str = PRODUCT_CONTRACTS
    ai_evidence: str = "no_model_step"
    model_evidence_class: str = "NO_MODEL_REQUIRED"
    ai_calls: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[dict[str, str]] = field(default_factory=list)
    seconds: float = 0.0

    def to_report(self) -> dict[str, Any]:
        base = self.scenario.to_report()
        base.update({"level": self.level, "reason": self.reason, "evidence_depth": self.depth,
                     "ai_evidence": self.ai_evidence, "model_evidence_class": self.model_evidence_class,
                     "ai_calls": self.ai_calls,
                     "blockers": self.blockers, "seconds": round(self.seconds, 4),
                     "positive": sum(1 for c in self.checks if c.kind == "positive"),
                     "negative": sum(1 for c in self.checks if c.kind == "negative"),
                     "checks": [c.to_report() for c in self.checks]})
        return base


class RunContext:
    """Единственный способ сценария записать улику."""

    def __init__(self, scenario: Scenario, workdir: Path, ai: CIAIProvider) -> None:
        self.scenario = scenario
        self.workdir = workdir
        self.ai = ai
        self.checks: list[Check] = []
        self.depth = scenario.depth

    # --------------------------------------------------------- запись проверок
    def positive(self, name: str, ok: bool, detail: str = "") -> bool:
        """Положительный случай: законное поведение действительно работает."""
        self.checks.append(Check(name, "positive", bool(ok), redact(detail)))
        return bool(ok)

    def negative(self, name: str, ok: bool, detail: str = "") -> bool:
        """Отрицательный контроль: ``ok=True`` значит «плохое отвергнуто»."""
        self.checks.append(Check(name, "negative", bool(ok), redact(detail)))
        return bool(ok)

    def refused(self, name: str, callable_: Callable[[], Any],
                expected: type[BaseException] | tuple[type[BaseException], ...],
                detail: str = "") -> bool:
        """Отрицательный контроль-помощник: вызов ОБЯЗАН упасть ожидаемым типом."""
        try:
            callable_()
        except expected as exc:
            return self.negative(name, True, detail or f"{type(exc).__name__}: {exc}")
        except BaseException as exc:  # noqa: BLE001 — чужой тип тоже провал контроля
            return self.negative(name, False, f"неожиданный тип отказа: {type(exc).__name__}: {exc}")
        return self.negative(name, False, "плохой случай НЕ был отвергнут")

    # -------------------------------------------------------------- модель
    def require_model(self, result: Any) -> Any:
        """Живой ответ модели или ЧЕСТНЫЙ тупик — но никогда не подделка."""
        if getattr(result, "ok", False):
            return result
        outcome = getattr(result, "outcome", "UNKNOWN")
        detail = redact(getattr(result, "detail", ""))
        if outcome == NO_KEY:
            raise CredentialRequired(f"нет ключа ИИ в окружении: {detail}")
        if outcome == INVALID_RESPONSE:
            raise AssertionError(f"модель ответила не по контракту: {detail}")
        raise InsufficientEvidence(f"живой вызов не состоялся ({outcome}): {detail}")

    # ------------------------------------------------------- честные тупики
    def owner_required(self, reason: str) -> None:
        raise OwnerRequired(reason)

    def owner_hardware_required(self, reason: str) -> None:
        raise OwnerHardwareRequired(reason)

    def not_proven(self, reason: str) -> None:
        raise InsufficientEvidence(reason)

    def reached_installed_product(self, detail: str = "") -> None:
        """Отметить, что цепочка прошла через установленный продукт владельца."""
        self.depth = INSTALLED_PRODUCT
        self.positive("цепочка прошла через установленный продукт", True, detail)

    def path(self, *parts: str) -> Path:
        p = self.workdir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


# ------------------------------------------------------------------- реестр
IMPLEMENTATIONS: dict[str, tuple[str, Callable[[RunContext], None]]] = {}


def scenario(*, id: str, depth: str = PRODUCT_CONTRACTS
             ) -> Callable[[Callable[[RunContext], None]], Callable[[RunContext], None]]:
    """Привязать реализацию к строке канонического реестра.

    Название, цепочка, требуемые способности и шаг модели живут ТОЛЬКО в
    `owner_scenarios.json`: два источника правды разъехались бы на первой же правке.
    """
    if depth not in DEPTHS:
        raise ValueError(f"неизвестная глубина улики {depth!r}; допустимо {DEPTHS}")

    def wrap(func: Callable[[RunContext], None]) -> Callable[[RunContext], None]:
        if id in IMPLEMENTATIONS:
            raise ValueError(f"сценарий {id} уже реализован")
        IMPLEMENTATIONS[id] = (depth, func)
        return func

    return wrap


def load_registry(path: Path = REGISTRY_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def discover(directory: Path = SCENARIO_DIR,
             registry: dict[str, Any] | None = None) -> list[Scenario]:
    """Собрать сценарии: строки реестра + привязанные реализации.

    Строка без кода и код без строки одинаково смертельны: и то и другое —
    расхождение табло с действительностью.
    """
    data = registry if registry is not None else load_registry(directory / "owner_scenarios.json")
    for path in sorted(directory.glob("scn_*.py")):
        module_name = f"bossman_owner_scn_{path.stem}"
        if module_name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:  # pragma: no cover — защита от битого пути
            raise ImportError(f"не удалось загрузить сценарии из {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    declared = {row["id"] for row in data["scenarios"]}
    orphans = sorted(set(IMPLEMENTATIONS) - declared)
    if orphans:
        raise ValueError(f"реализации без строки в реестре: {orphans}")
    missing = sorted(declared - set(IMPLEMENTATIONS))
    if missing:
        raise ValueError(f"строки реестра без реализации: {missing}")

    out: list[Scenario] = []
    for row in sorted(data["scenarios"], key=lambda r: r["number"]):
        depth, func = IMPLEMENTATIONS[row["id"]]
        if row["model_step"] not in MODEL_STEPS:
            raise ValueError(f"{row['id']}: неизвестный model_step {row['model_step']!r}")
        out.append(Scenario(id=row["id"], number=int(row["number"]), title=row["title"],
                            chain=row["chain"], requires=tuple(row.get("requires") or ()),
                            model_step=row["model_step"], depth=depth, func=func,
                            owner_gap=str(row.get("owner_gap") or "")))
    return out


# -------------------------------------------------------------------- прогон
def _level_from_checks(ctx: RunContext) -> tuple[str, str]:
    positives = [c for c in ctx.checks if c.kind == "positive"]
    negatives = [c for c in ctx.checks if c.kind == "negative"]
    failed = [c for c in ctx.checks if not c.ok]
    if failed:
        names = ", ".join(f"{c.name} ({c.detail})" for c in failed)
        return FAIL, f"провалены проверки: {names}"
    if not positives:
        return INSUFFICIENT_EVIDENCE, "нет ни одного положительного случая"
    if not negatives:
        return INSUFFICIENT_EVIDENCE, ("нет отрицательного контроля: проверка без него "
                                       "ничего не доказывает")
    return CI_PROVEN, ""


def _model_evidence_class(scenario_: Scenario, ai: CIAIProvider, before: int) -> str:
    """Truthful model provenance, independent from scenario PASS/FAIL.

    Deterministic product checks are NO_MODEL_REQUIRED, not AI-backed. Injected
    transports are MOCK_MODEL even when they return a contract-valid envelope.
    A successful real network call is LOCAL only for loopback endpoints; every
    other successful network provider is CLOUD. Missing credentials never
    become model evidence.
    """
    if scenario_.model_step == "none":
        return "NO_MODEL_REQUIRED"
    if scenario_.model_step == "contract":
        return "MOCK_MODEL"
    calls = ai.calls[before:]
    if not any(getattr(call, "ok", False) for call in calls):
        return "NO_MODEL_REQUIRED"
    if getattr(ai, "_transport", None) is not None:
        return "MOCK_MODEL"
    from urllib.parse import urlsplit
    host = (urlsplit(ai.base_url).hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "REAL_LOCAL_MODEL"
    return "REAL_CLOUD_MODEL"


def _level_from_model_step(slice_: Sequence[Any]) -> tuple[str, str, str]:
    """Для ``model_step="live"`` уровень определяет ЖУРНАЛ вызовов, а не сценарий."""
    outcomes = [c.outcome for c in slice_]
    if any(c.ok for c in slice_):
        return CI_PROVEN, "", "live_call_ok"
    if INVALID_RESPONSE in outcomes:
        return FAIL, "модель ответила не по контракту chat/completions", "live_call_invalid"
    if NO_KEY in outcomes:
        return CREDENTIAL_REQUIRED, "нужен реальный CI AI credential в окружении", "no_key"
    if BUDGET_EXCEEDED in outcomes:
        return INSUFFICIENT_EVIDENCE, "исчерпан объявленный потолок расходов прогона", "budget"
    if outcomes:
        return (INSUFFICIENT_EVIDENCE,
                "живой вызов не состоялся: " + ", ".join(sorted(set(outcomes))), "provider_down")
    return (INSUFFICIENT_EVIDENCE,
            "сценарий объявил живой шаг модели, но модель не вызывал", "not_called")


def _blocked(scenario_: Scenario, blockers: Sequence[Any]) -> ScenarioResult:
    """Недостающая способность: честный вердикт вместо исполнения и заглушки."""
    # Самый суровый из блокеров решает уровень: FAIL (сломанный продукт) важнее,
    # чем «нужен владелец», а тот важнее, чем «среда не несёт продукт».
    order = {FAIL: 0, OWNER_HARDWARE_REQUIRED: 1, CREDENTIAL_REQUIRED: 2, OWNER_REQUIRED: 3, INSUFFICIENT_EVIDENCE: 4}
    level = sorted((b.missing_level for b in blockers), key=lambda lv: order[lv])[0]
    reason = "; ".join(f"{b.name}: {b.detail}" for b in blockers)
    return ScenarioResult(scenario=scenario_, level=level, reason=redact(reason),
                          depth=scenario_.depth, ai_evidence="blocked",
                          blockers=[{"capability": b.name, "detail": redact(b.detail),
                                     "level": b.missing_level} for b in blockers])


def run_scenario(scenario_: Scenario, ai: CIAIProvider,
                 blockers: Sequence[Any] = ()) -> ScenarioResult:
    if blockers:
        return _blocked(scenario_, blockers)
    started = time.monotonic()
    before = ai.calls_made
    with TemporaryDirectory(prefix=f"bossman-owner-{scenario_.id}-") as tmp:
        ctx = RunContext(scenario_, Path(tmp), ai)
        try:
            scenario_.func(ctx)
        except CredentialRequired as exc:
            level, reason, evidence = CREDENTIAL_REQUIRED, redact(str(exc)), "credential"
        except OwnerRequired as exc:
            level, reason, evidence = OWNER_REQUIRED, redact(str(exc)), "owner_action"
        except OwnerHardwareRequired as exc:
            level, reason, evidence = OWNER_HARDWARE_REQUIRED, redact(str(exc)), "owner_hardware"
        except InsufficientEvidence as exc:
            level, reason, evidence = INSUFFICIENT_EVIDENCE, redact(str(exc)), "not_proven"
        except BaseException:  # noqa: BLE001 — упавший сценарий это FAIL
            level = FAIL
            reason = redact(traceback.format_exc(limit=6).strip().splitlines()[-1])
            evidence = "crashed"
        else:
            level, reason = _level_from_checks(ctx)
            evidence = {"live": "live_call_pending", "contract": "adapter_contract_only",
                        "none": "no_model_step"}[scenario_.model_step]
            if scenario_.model_step == "live":
                gate, gate_reason, evidence = _level_from_model_step(ai.calls[before:])
                if gate != CI_PROVEN:
                    level, reason = gate, gate_reason
    result = ScenarioResult(scenario=scenario_, level=level, reason=reason, checks=list(ctx.checks),
                            depth=ctx.depth, ai_evidence=evidence,
                            model_evidence_class=_model_evidence_class(scenario_, ai, before),
                            ai_calls=[c.to_report() for c in ai.calls[before:]],
                            seconds=time.monotonic() - started)
    if result.level not in LEVELS:  # pragma: no cover — защита контракта уровней
        raise AssertionError(f"недопустимый уровень улик: {result.level}")
    return result


def run_all(scenarios: Iterable[Scenario] | None = None, *,
            ai: CIAIProvider | None = None, capabilities=None) -> dict[str, Any]:
    """Прогнать владельческие сценарии одним бюджетом ИИ на весь прогон."""
    if capabilities is None:
        sys.path.insert(0, str(SCENARIO_DIR))
        import capabilities as capabilities  # noqa: PLW0127 — модуль проб среды
    provider = ai or CIAIProvider()
    items = list(scenarios) if scenarios is not None else discover()
    results = [run_scenario(s, provider, capabilities.missing(s.requires)) for s in items]
    totals = {level: sum(1 for r in results if r.level == level) for level in LEVELS}
    depths = {d: sum(1 for r in results if r.level == CI_PROVEN and r.depth == d)
              for d in DEPTHS}
    model_classes = {name: sum(1 for r in results if r.model_evidence_class == name)
                     for name in ("NO_MODEL_REQUIRED", "MOCK_MODEL",
                                  "REAL_CLOUD_MODEL", "REAL_LOCAL_MODEL")}
    return {
        **gap_reconciliation(results),
        "board": "OWNER_SCENARIOS",
        "board_note": ("НЕ складывать с табло REGRESSION: инженерные наборы не являются "
                       "определением готовности"),
        "runner": "tools/scenario_runner.py",
        "schema_version": 1,
        "levels": list(LEVELS),
        "totals": totals,
        "green_by_depth": depths,
        "model_evidence_totals": model_classes,
        "total": len(results),
        "green": totals[CI_PROVEN],
        "environment": capabilities.inventory(),
        "ai": provider.budget_report(),
        "verdict": verdict_line(totals, len(results)),
        "scenarios": [r.to_report() for r in results],
    }


def gap_reconciliation(results: Sequence[ScenarioResult]) -> dict[str, Any]:
    """Сверить ОБЪЯВЛЕННЫЕ в реестре пробелы с тем, что вышло на самом деле.

    Три исхода, и все три обязаны быть видны:

    * `undeclared` — строка не доказана, а пробела в реестре нет. Это и есть
      неожиданность: либо продукт сломался, либо сценарий недописан.
    * `closed` — пробел объявлен, а строка позеленела. Пробел закрылся, и
      реестр отстал от действительности; молчать об этом нельзя, иначе
      объявление превращается в вечную индульгенцию.
    * `mislabelled` — пробел объявлен, строка не доказана, но в отказе НЕТ
      объявленного фрагмента. Значит причина теперь другая, а объявление
      прикрывает не тот пробел.
    """
    undeclared, closed, mislabelled = [], [], []
    for r in results:
        # Сценарий с блокером НЕ ИСПОЛНЯЛСЯ: отсутствующая способность — не
        # пробел продукта, и судить по такому прогону нельзя НИ ОДНИМ из трёх
        # способов. Исключение только из `undeclared` уже стоило красного
        # корневого CI: там нет `command_center`, OS-29 уходил в
        # INSUFFICIENT_EVIDENCE с причиной-блокером, и сверка объявляла
        # подменившуюся причину там, где причины не было вовсе. Деградацию
        # способностей меряет объявленный пол зелёных, а не эта сверка.
        if r.blockers:
            continue
        gap = r.scenario.owner_gap
        if not gap and r.level in NOT_PROVEN_LEVELS:
            undeclared.append(r.scenario.id)
        elif gap and r.level == CI_PROVEN:
            closed.append(r.scenario.id)
        elif gap and r.level in NOT_PROVEN_LEVELS and gap not in r.reason:
            mislabelled.append(r.scenario.id)
    return {"gaps_undeclared": undeclared, "gaps_closed": closed,
            "gaps_mislabelled": mislabelled}


def verdict_line(totals: dict[str, int], total: int) -> str:
    parts = [f"{totals[level]} {level}" for level in LEVELS if totals.get(level)]
    return f"OWNER SCENARIOS: {totals[CI_PROVEN]} / {total} — " + ", ".join(parts)


def print_report(report: dict[str, Any]) -> None:
    for row in report["scenarios"]:
        mark = "OK " if row["level"] == CI_PROVEN else "!! "
        print(f"{mark}{row['id']:<6} {row['level']:<23} {row['evidence_depth']:<18} "
              f"+{row['positive']}/-{row['negative']} {row['title']}")
        if row["reason"]:
            print(f"        причина: {row['reason'][:200]}")
    missing = [f"{name}" for name, info in report["environment"].items() if not info["present"]]
    print(f"\nНет в среде: {', '.join(missing) if missing else '—'}")
    ai = report["ai"]
    print(f"ИИ-адаптер: ключ={'есть' if ai['key_present'] else 'НЕТ'}"
          f" ({ai['key_source'] or 'нет переменной'}), вызовов {ai['calls_made']}/"
          f"{ai['max_calls_per_run']}, успешных {ai['ok_calls']}")
    print(f"Зелёные по глубине улики: {report['green_by_depth']}")
    print(f"Модельные доказательства: {report['model_evidence_totals']}")
    declared = [row["id"] for row in report["scenarios"] if row.get("owner_gap")]
    if declared:
        print(f"Объявленные пробелы (ждут решения владельца): {', '.join(declared)}")
    for key, text in (("gaps_undeclared", "НЕ ОБЪЯВЛЕННЫЕ незелёные"),
                      ("gaps_closed", "пробел ЗАКРЫЛСЯ — обнови реестр"),
                      ("gaps_mislabelled", "причина не совпала с объявленным пробелом")):
        if report.get(key):
            print(f"{text}: {', '.join(report[key])}", file=sys.stderr)
    print(report["verdict"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Прогон владельческих сценариев")
    parser.add_argument("--dir", type=Path, default=SCENARIO_DIR)
    parser.add_argument("--only", nargs="*", default=None, help="прогнать только эти id")
    parser.add_argument("--json", type=Path, default=None, help="куда записать JSON-отчёт")
    parser.add_argument("--strict", action="store_true",
                        help="ненулевой код возврата на всём, что не CI_PROVEN")
    parser.add_argument("--min-green", type=int, default=None,
                        help="объявленный ПОЛ зелёных: меньше — отказ. Без него "
                             "тихая деградация зелёного в OWNER_REQUIRED осталась бы "
                             "незамеченной, а прогон — зелёным")
    args = parser.parse_args(argv)

    found = discover(args.dir)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {s.id for s in found}
        if unknown:
            print(f"неизвестные сценарии: {sorted(unknown)}", file=sys.stderr)
            return 2
        found = [s for s in found if s.id in wanted]
    report = run_all(found)
    print_report(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    totals = report["totals"]
    if totals[FAIL]:
        return 1
    if args.min_green is not None and report["green"] < args.min_green:
        # Способность может пропасть из среды молча (не поставился ffmpeg, не
        # поднялась база), и тогда зелёные превращаются в OWNER_REQUIRED, а не в
        # FAIL. Без объявленного пола прогон остался бы зелёным при любой
        # деградации — ровно тот класс, что BL-089.
        print(f"OWNER_SCENARIOS_FLOOR=FAIL зелёных {report['green']} < объявленного "
              f"пола {args.min_green}", file=sys.stderr)
        return 1
    if args.strict and report["green"] != report["total"]:
        return 1
    # Незелёная строка — не всегда неожиданность. Пробел, ЗАЯВЛЕННЫЙ в реестре
    # полем `owner_gap`, ждёт решения владельца и остаётся видимым в отчёте, но
    # не красит прогон: иначе гейт краснел бы вечно и перестал бы что-либо
    # значить — ровно та беда, от которой этот проект и лечится. А вот
    # НЕОБЪЯВЛЕННАЯ недоказанная строка, закрывшийся пробел и подменившаяся
    # причина — красят, потому что означают расхождение табло с
    # действительностью. Отсутствующая способность сюда не входит: её ловит
    # объявленный пол зелёных выше.
    if report["gaps_undeclared"] or report["gaps_closed"] or report["gaps_mislabelled"]:
        return 2
    if totals[NOT_RUN]:
        return 2
    return 0


if __name__ == "__main__":
    # Запуск скриптом дал бы модулю имя __main__, а сценарии импортируют его как
    # `scenario_runner` — получились бы ДВА модуля с двумя разными реестрами.
    from scenario_runner import main as _main

    raise SystemExit(_main())
