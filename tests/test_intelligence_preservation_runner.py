"""Измеритель удержания интеллекта: он обязан быть честным ДО живого прогона.

Гейт `tools/intelligence_preservation_gate.py` существовал без измерителя, и
канонический `docs/benchmark/intelligence-preservation-current.json` не
производил никто — поэтому проверка Intelligence Preservation в CI падала не
из-за модели и не из-за кода, а из-за отсутствия файла.

Здесь модель подменяется: проверяется НАША арифметика — парность полос,
подсчёт lost/gained, инверсия метрики выдумок, привязка к коммиту и то, что
недостаточная выборка честно остаётся недостаточной.

И главное (аудит AF-04). Полоса FULL раньше была рендером строки: системный
промпт + текстовый контекст + СПИСОК ИМЁН инструментов. Такой прогон ничего не
говорит об обвязке, потому что обвязки в нём нет. Раздел «полоса FULL обязана
выполнять» ниже — поведенческий: полоса прогоняется через пробную модель,
которая ПРОСИТ вызов инструмента, и проверяется, что инструмент действительно
исполнился, а его вывод вернулся модели. Регрессия к списку имён проваливает
эти тесты, а не проходит их.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from intelligence_preservation_gate import GateConfig, evaluate  # noqa: E402
from intelligence_preservation_run import (BASELINE, CONTRACT_SINGLE_TURN,  # noqa: E402
                                           CONTRACT_TOOL_NOT_EXECUTED,
                                           FULL_LANE_CONTRACT_CODES,
                                           MODES, REQUIRED_METRICS,
                                           SUFFICIENT_SAMPLES_PER_METRIC, LaneRun,
                                           ModelReply, ProductionFullLane,
                                           RunnerError, Task, ToolCall, assert_full_lane,
                                           build_lanes, build_payload, full_lane_violations,
                                           load_tasks, measure, paired_items, score)

TASKS = ROOT / "docs" / "benchmark" / "intelligence_tasks.json"
SHA = "a" * 40

# Ровно то, что делала полоса FULL до исправления AF-04: короткий системный
# промпт + перечисление имён инструментов ТЕКСТОМ. Копия живёт в тестах, а не в
# продукте — чтобы негативный контроль был воспроизводим, а сам продукт больше
# не содержал этой строки.
LEGACY_TOOLS_PROMPT = (
    "Доступные инструменты: fs.read(path), fs.write(path, text), browser.open(url), "
    "terminal.run(command), computer.click(x, y), computer.type(text). "
    "Когда нужен инструмент — назови ровно один и только его имя.")


class LegacyPromptSmokeFull:
    """Полоса FULL «как раньше»: дописать список имён инструментов и спросить
    модель один раз. Существует только затем, чтобы контракт её ОТВЕРГАЛ."""

    mode = "full"

    def identity(self) -> dict:
        return {"lane": "full", "kind": "prompt_smoke"}

    def run(self, task: Task, call) -> LaneRun:
        from intelligence_preservation_run import SYSTEM_PROMPT
        messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n{LEGACY_TOOLS_PROMPT}"}]
        user = task.prompt
        if task.context:
            user = f"Известное о проекте:\n{task.context}\n\nЗадача: {task.prompt}"
        messages.append({"role": "user", "content": user})
        return LaneRun(text=call(messages, None).content, turns=1)


class ArithmeticFullDouble:
    """Двойник полосы FULL ТОЛЬКО для проверки арифметики парности.

    Он предъявляет модели схему инструмента (чтобы тесты могли отличить полосу
    FULL от промптовых), но ничего не исполняет — и поэтому контракт AF-04 его
    ОТВЕРГАЕТ, что отдельно проверяется ниже. Существует затем, чтобы проверки
    lost/gained, инверсии выдумок и привязки к SHA работали и там, где
    bossman-core не установлен (тонкое окружение root CI).
    """

    mode = "full"
    SCHEMA = [{"type": "function", "function": {
        "name": "fs_read", "description": "двойник",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}}]

    def identity(self) -> dict:
        return {"lane": "full", "kind": "arithmetic_double", "executes_tools": False}

    def run(self, task: Task, call) -> LaneRun:
        return LaneRun(text=call([{"role": "user", "content": task.prompt}],
                                 self.SCHEMA).content, turns=1)


@pytest.fixture(scope="module")
def full_lane(tmp_path_factory):
    """Настоящая production-полоса. Без установленного bossman-core (redis/
    asyncpg/playwright не входят в тонкий набор root CI) измерять нечего."""
    try:
        return ProductionFullLane(workdir=tmp_path_factory.mktemp("full-lane"))
    except RunnerError as exc:
        pytest.skip(f"bossman-core (production-петля) недоступен, необязательный "
                    f"пакет не установлен: {exc}")


@pytest.fixture(scope="module")
def lanes():
    """Полосы для арифметики парности: настоящие промптовые + явный двойник."""
    return build_lanes(full=ArithmeticFullDouble())


@pytest.fixture(scope="module")
def production_lanes(full_lane):
    return build_lanes(full=full_lane)


# =====================================================================
# AF-04: полоса FULL обязана ВЫПОЛНЯТЬ, а не описывать инструменты
# =====================================================================
def test_a_prompt_renderer_can_no_longer_produce_the_full_lane():
    """Полосу выполнения нельзя получить дописыванием текста, поэтому рендерер
    её просто не умеет: попытка падает, а не возвращает prompt-smoke молча."""
    task = Task("t", "reasoning_accuracy", "2+2?", {"kind": "equals", "value": "4"})
    with pytest.raises(RunnerError, match="execution lane"):
        task.rendered("full")


def test_the_production_full_lane_satisfies_the_execution_contract(full_lane):
    """Позитивный контроль: настоящая полоса проходит все пять проверок."""
    assert full_lane_violations(full_lane) == []
    assert_full_lane(full_lane)          # не должно бросить


def test_a_full_lane_reduced_to_a_list_of_tool_names_is_rejected():
    """ГЛАВНЫЙ негативный контроль (AF-04).

    Тот самый рендер, что был в проде до исправления, проваливает КАЖДУЮ из
    пяти проверок контракта: он не предлагает модели схем инструментов, ходит к
    модели один раз, ничего не исполняет, ничего не возвращает модели и вносит
    контекст склейкой в текст задачи вместо рамки «это данные».
    """
    violations = full_lane_violations(LegacyPromptSmokeFull())
    assert set(violations) == set(FULL_LANE_CONTRACT_CODES), violations
    with pytest.raises(RunnerError, match="prompt smoke"):
        assert_full_lane(LegacyPromptSmokeFull())


def test_the_arithmetic_double_cannot_pass_itself_off_as_the_full_lane():
    """Двойник из этого файла предъявляет схему инструмента, но не исполняет её.
    Контракт обязан его отвергнуть — иначе завтра он окажется в проде."""
    violations = full_lane_violations(ArithmeticFullDouble())
    assert CONTRACT_TOOL_NOT_EXECUTED in violations
    assert CONTRACT_SINGLE_TURN in violations
    with pytest.raises(RunnerError, match="prompt smoke"):
        assert_full_lane(ArithmeticFullDouble())


def test_the_full_lane_actually_runs_a_tool_and_shows_the_model_its_output(full_lane):
    """Наблюдаемый вызов инструмента: модель просит fs_read — файл читается
    настоящим handler'ом, и модель на следующем шаге ВИДИТ его содержимое.
    Полоса, которая только перечисляет имена, этого показать не может."""
    nonce = "NONCE-8f31c0d5"
    (full_lane.sandbox_root() / "probe_ab.txt").write_text(nonce + "\n", encoding="utf-8")
    seen: list[list[dict]] = []

    def model(messages, tools=None):
        seen.append([dict(m) for m in messages])
        if len(seen) == 1:
            return ModelReply(tool_calls=[ToolCall("fs_read", {"path": "probe_ab.txt"})])
        return ModelReply(content="прочитал")

    run = full_lane.run(Task("t", "tool_selection_accuracy", "прочитай probe_ab.txt",
                             {"kind": "contains", "all_of": [nonce]}), _client(model))
    assert run.turns >= 2, "полоса сходила к модели один раз — инструмент не исполнялся"
    assert "fs.read" in run.executed_tools, run.declined_tools
    later = "\n".join(m["content"] for turn in seen[1:] for m in turn)
    assert nonce in later, "вывод инструмента не вернулся модели"


def test_the_full_lane_offers_real_tool_schemas_not_a_sentence_of_names(full_lane):
    """Инструменты приходят модели СХЕМАМИ из настоящего REGISTRY: имя
    резолвится в ToolDef, у параметров есть типы. Предложение «Доступные
    инструменты: fs.read(path), …» ни одного такого объекта не даёт."""
    from bossman.toolkit import by_api_name
    captured: dict = {}

    def model(messages, tools=None):
        captured["tools"] = tools
        return ModelReply(content="ок")

    full_lane.run(Task("t", "tool_selection_accuracy", "?", {"kind": "equals", "value": "ок"}),
                  _client(model))
    offered = captured["tools"]
    assert offered, "полоса FULL не предложила модели ни одного инструмента"
    for entry in offered:
        fn = entry["function"]
        assert by_api_name(fn["name"]) is not None, f"{fn['name']} нет в production REGISTRY"
        assert isinstance(fn["parameters"]["properties"], dict)
    assert any(fn["function"]["parameters"]["properties"] for fn in offered)
    # ровно то, что production-функция выдаёт этому агенту
    import bossman.runner as production
    assert len(offered) == len(production._tool_schemas(full_lane.agent))


def test_the_full_lane_uses_the_production_context_mechanism(full_lane):
    """Контекст входит блоком `retrieved` настоящего ContextBuilder — с
    provenance и рамкой «это ДАННЫЕ, не инструкции». Старый режим склеивал его
    в текст задачи, и никакой границы там не было."""
    from bossman.context import RETRIEVED_DATA_HEADER
    seen: list[list[dict]] = []

    def model(messages, tools=None):
        seen.append([dict(m) for m in messages])
        return ModelReply(content="ок")

    task = Task("longctx-x", "long_context_accuracy", "какой порт?",
                {"kind": "equals", "value": "8003"}, context="сервис d3 слушает порт 8003")
    full_lane.run(task, _client(model))
    messages = seen[0]
    framed = [m for m in messages if RETRIEVED_DATA_HEADER in m["content"]]
    assert framed, "контекст пришёл без рамки «данные ≠ инструкции»"
    assert "сервис d3" in framed[0]["content"]
    assert "источник: docs/benchmark/intelligence_tasks.json#longctx-x" in framed[0]["content"]
    assert messages[-1]["content"] == task.prompt, "контекст снова склеен в текст задачи"


def test_the_full_lane_keeps_the_production_authorization_boundary(full_lane):
    """Негативный контроль на права: измерение не имеет права трогать мир.
    Инструмент не выданный агенту и инструмент под подтверждением НЕ
    исполняются — тем же отказом, что и в production."""
    def model(messages, tools=None):
        if not getattr(model, "done", False):
            model.done = True
            return ModelReply(tool_calls=[ToolCall("fs_write", {"path": "x", "content": "y"}),
                                          ToolCall("browser_confirmed_click", {"selector": "#pay"}),
                                          ToolCall("no_such_tool", {})])
        return ModelReply(content="ок")

    run = full_lane.run(Task("t", "tool_selection_accuracy", "?",
                             {"kind": "equals", "value": "ок"}), _client(model))
    assert run.executed_tools == [], run.executed_tools
    assert "fs.write:not_granted" in run.declined_tools
    assert "browser.confirmed_click:needs_confirm" in run.declined_tools
    assert "no_such_tool:unknown" in run.declined_tools
    assert not (full_lane.sandbox_root() / "x").exists()


def test_the_module_no_longer_ships_a_static_tool_name_prompt():
    """Вторичная страховка к поведенческим тестам выше: строки-перечисления
    инструментов в продукте больше нет, её место заняли схемы из REGISTRY."""
    import intelligence_preservation_run as runner
    assert not hasattr(runner, "TOOLS_PROMPT")


def test_every_lane_carries_observable_configuration_identifiers(production_lanes):
    """Каждая полоса обязана предъявлять, чем она была. Иначе через месяц не
    отличить прогон настоящей петли от прогона списка имён."""
    ids = {mode: production_lanes[mode].identity() for mode in MODES}
    assert {ids[m]["lane"] for m in MODES} == set(MODES)
    for mode in ("raw", "system", "context"):
        assert ids[mode]["executes_tools"] is False
    full = ids["full"]
    assert full["kind"] == "production_execution_loop"
    assert full["executes_tools"] is True
    assert full["system_prompt_builder"] == "bossman.runner._system_prompt"
    assert full["context_mechanism"] == "bossman.context.ContextBuilder"
    for key in ("agent_sha256", "system_prompt_sha256", "tool_registry_sha256",
                "context_window", "tool_schema_count", "not_covered"):
        assert full.get(key), key


# =====================================================================
# набор задач и оценка
# =====================================================================
def test_the_task_set_covers_every_metric_the_gate_requires():
    tasks = load_tasks(TASKS)
    covered = {t.metric for t in tasks}
    assert covered == set(REQUIRED_METRICS), sorted(set(REQUIRED_METRICS) - covered)
    assert len({t.task_id for t in tasks}) == len(tasks)


def test_every_task_can_be_scored_at_all():
    """Задача, которую нечем проверить, — не задача."""
    for task in load_tasks(TASKS):
        assert isinstance(score(task, "какой-то ответ"), bool)


def test_the_prompt_lanes_differ_only_by_the_scaffolding_never_by_the_task():
    task = Task("t", "reasoning_accuracy", "сколько будет 2+2?", {"kind": "equals", "value": "4"},
                context="важный факт")
    raw = task.rendered("raw")
    assert len(raw) == 1 and raw[0]["role"] == "user"
    assert "важный факт" not in raw[0]["content"]         # RAW не видит контекста
    assert task.rendered("system")[0]["role"] == "system"
    assert "важный факт" not in task.rendered("system")[-1]["content"]
    assert "важный факт" in task.rendered("context")[-1]["content"]
    for mode in ("raw", "system", "context"):             # сама задача одна и та же
        assert "2+2" in task.rendered(mode)[-1]["content"]


@pytest.mark.parametrize("answer,expected", [
    ("4", True), (" 4 ", True), ("четыре", False), ("", False),
])
def test_equals_scoring_is_exact_but_forgiving_of_whitespace(answer, expected):
    task = Task("t", "reasoning_accuracy", "?", {"kind": "equals", "value": "4"})
    assert score(task, answer) is expected


def test_json_scoring_reads_a_fenced_answer_and_rejects_prose():
    task = Task("t", "structured_output_accuracy", "?",
                {"kind": "json_fields", "fields": {"name": "Ада", "age": "36"}})
    assert score(task, '```json\n{"name": "Ада", "age": 36}\n```') is True
    assert score(task, '{"name": "Ада"}') is False          # поля не хватает
    assert score(task, "Ада, 36 лет") is False              # это не JSON


def test_tool_scoring_refuses_an_answer_that_names_the_neighbour_too():
    task = Task("t", "tool_selection_accuracy", "?",
                {"kind": "tool", "name": "fs.read", "not": ["fs.write"]})
    assert score(task, "fs.read") is True
    assert score(task, "можно fs.read или fs.write") is False
    assert score(task, "fs.write") is False


def test_tool_scoring_counts_an_observed_call_and_still_punishes_the_neighbour():
    """Полоса, умеющая вызывать инструменты, выбирает их ДЕЛОМ. Не засчитать
    настоящий вызов значило бы наказать FULL за то, что она сделала работу."""
    task = Task("t", "tool_selection_accuracy", "?",
                {"kind": "tool", "name": "fs.read", "not": ["fs.write"]})
    assert score(task, "", LaneRun(requested_tools=["fs_read"])) is True
    assert score(task, "", LaneRun(requested_tools=["fs_read", "fs_write"])) is False
    assert score(task, "", LaneRun(requested_tools=[])) is False


def test_hallucination_scoring_counts_taking_the_bait_as_failure():
    task = Task("t", "hallucination_rate", "?", {"kind": "absent", "none_of": ["2031"]})
    assert score(task, "не знаю") is True
    assert score(task, "вышла в 2031 году") is False


# =====================================================================
# парность и арифметика полос
# =====================================================================
def _client(fn):
    from intelligence_preservation_run import as_model_client
    return as_model_client(fn)


def _run_lanes(answers, lanes):
    """answers: {mode: {task_id: ответ}} — полосы идут по порядку MODES."""
    tasks = [Task(f"t{i}", "reasoning_accuracy", "?", {"kind": "equals", "value": "4"})
             for i in range(3)]
    script = [answers[mode][t.task_id] for mode in MODES for t in tasks]

    def call(messages, tools=None):
        return script.pop(0)

    return tasks, measure(tasks, call, lanes=lanes)


def test_paired_discordance_counts_what_the_scaffolding_lost_and_gained(lanes):
    """Смысл парности: не «сколько верно», а «что именно полоса потеряла»."""
    _, result = _run_lanes({
        "raw":     {"t0": "4", "t1": "4", "t2": "x"},
        "system":  {"t0": "4", "t1": "x", "t2": "4"},   # одну потеряла, одну приобрела
        "context": {"t0": "4", "t1": "4", "t2": "4"},   # ничего не потеряла, одну приобрела
        "full":    {"t0": "x", "t1": "x", "t2": "x"},   # потеряла обе
    }, lanes)
    m = result["system"]["reasoning_accuracy"]
    assert (m.lost, m.gained) == (1, 1) and m.samples == 3
    assert result["context"]["reasoning_accuracy"].lost == 0
    assert result["context"]["reasoning_accuracy"].gained == 1
    assert result["full"]["reasoning_accuracy"].lost == 2
    assert BASELINE not in {"system", "context", "full"}


def test_every_lane_answers_the_same_items_so_the_gate_can_pair_them(lanes):
    _, result = _run_lanes({m: {f"t{i}": "4" for i in range(3)} for m in MODES}, lanes)
    counts = {m: result[m]["reasoning_accuracy"].samples for m in MODES}
    assert len(set(counts.values())) == 1, counts


def test_a_model_that_does_not_answer_fails_the_item_instead_of_dropping_it(lanes):
    """Выброшенная задача ломает парность — молчание модели это провал, не пропуск."""
    tasks = [Task("t0", "reasoning_accuracy", "?", {"kind": "equals", "value": "4"})]

    def call(messages, tools=None):
        raise ConnectionResetError("model went away")

    result = measure(tasks, call, lanes=lanes)
    for mode in MODES:
        metric = result[mode]["reasoning_accuracy"]
        assert metric.samples == 1 and metric.passed == 0.0


def test_the_hallucination_metric_is_inverted_so_higher_is_worse(lanes):
    """Для выдумок единица — плохо. Ошибка знака здесь превратила бы модель,
    которая врёт на каждом вопросе, в образцовую."""
    tasks = [Task(f"h{i}", m, "?", {"kind": "absent", "none_of": ["2031"]}
                  if m == "hallucination_rate" else {"kind": "equals", "value": "не знаю"})
             for i, m in enumerate(REQUIRED_METRICS)]
    clean = build_payload(measure(tasks, lambda msgs, tools=None: "не знаю", lanes=lanes),
                          model="m", dataset_id="d", evaluated_sha=SHA)
    assert clean["modes"]["raw"]["hallucination_rate"]["score"] == 0.0
    liar = build_payload(measure(tasks, lambda msgs, tools=None: "вышла в 2031", lanes=lanes),
                         model="m", dataset_id="d", evaluated_sha=SHA)
    assert liar["modes"]["raw"]["hallucination_rate"]["score"] == 1.0


def test_a_metric_with_no_tasks_is_a_refusal_not_a_zero(lanes):
    """Метрика без задач — дефект НАБОРА. Ноль отправил бы владельца чинить
    гейт («samples must be positive») вместо собственного набора."""
    result = measure([Task("t0", "reasoning_accuracy", "?", {"kind": "equals", "value": "4"})],
                     lambda msgs, tools=None: "4", lanes=lanes)
    with pytest.raises(RunnerError, match="covers no items for 'coding_correctness'"):
        build_payload(result, model="m", dataset_id="d", evaluated_sha=SHA)


# --------------------------------------------- сквозная проверка через гейт
def test_a_perfect_run_produces_a_payload_the_real_gate_accepts_structurally(production_lanes):
    """Файл обязан быть тем самым, который читает гейт, а не похожим на него."""
    tasks = load_tasks(TASKS)
    result = measure(tasks, _perfect_model(tasks), lanes=production_lanes)
    payload = build_payload(result, model="qwen2.5-coder:14b", dataset_id="bossman-retention-v1",
                            evaluated_sha=SHA,
                            lane_identity={m: production_lanes[m].identity() for m in MODES})
    # Набор рассчитан ровно на порог гейта: 20 задач на каждую требуемую метрику.
    for name in REQUIRED_METRICS:
        assert payload["modes"]["raw"][name]["samples"] == 20, name
    report = evaluate(payload, GateConfig(expect_sha=SHA, min_samples_per_metric=20))
    assert report["status"] in ("PASS", "NO_GO", "INSUFFICIENT_EVIDENCE"), report
    # Идеальный прогон во всех полосах: ни одна полоса ничего не потеряла.
    for lane in ("system", "context", "full"):
        assert payload["modes"][lane]["reasoning_accuracy"]["paired"]["lost"] == 0


def test_the_payload_publishes_per_item_paired_results(lanes):
    """`lost`/`gained` без поэлементных исходов нечем перепроверить."""
    tasks = load_tasks(TASKS)
    result = measure(tasks, _perfect_model(tasks), lanes=lanes)
    payload = build_payload(result, model="m", dataset_id="d", evaluated_sha=SHA)
    items = payload["items"]
    assert len(items) == len(tasks)
    assert all(set(MODES) <= set(row) for row in items)
    # пересчёт lost по поэлементным строкам обязан сойтись со сводкой
    for name in REQUIRED_METRICS:
        lost = sum(1 for r in items if r["metric"] == name and r["raw"] and not r["full"])
        assert lost == payload["modes"]["full"][name]["paired"]["lost"], name
    assert paired_items(result) == items


def test_a_lane_that_loses_core_ability_is_not_a_pass(production_lanes):
    """Отрицательный контроль ко всему измерению: если обвязка ломает модель,
    вердикт обязан это увидеть, иначе гейт бесполезен."""
    tasks = load_tasks(TASKS)
    perfect = _perfect_model(tasks)

    def call(messages, tools=None):
        # Полосу FULL узнаём по предложенным инструментам, а не по тексту:
        # инструменты предлагает только она.
        if tools:
            task_id = _task_id_of(messages, tasks)
            # устойчивый выбор трети задач: hash() зависит от PYTHONHASHSEED
            if int(hashlib.sha1(task_id.encode()).hexdigest(), 16) % 3 == 0:
                return "не знаю"
        return perfect(messages, tools)

    result = measure(tasks, call, lanes=production_lanes)
    payload = build_payload(result, model="m", dataset_id="bossman-retention-v1",
                            evaluated_sha=SHA)
    report = evaluate(payload, GateConfig(expect_sha=SHA, min_samples_per_metric=20))
    assert report["status"] != "PASS", report["status"]
    assert payload["modes"]["full"]["reasoning_accuracy"]["paired"]["lost"] > 0


def test_the_payload_is_bound_to_the_commit_under_test(lanes):
    """Вердикт по старому SHA не является вердиктом по новому."""
    tasks = load_tasks(TASKS)
    result = measure(tasks, _perfect_model(tasks), lanes=lanes)
    payload = build_payload(result, model="m", dataset_id="d", evaluated_sha=SHA)
    with pytest.raises(ValueError, match="not the commit under test"):
        evaluate(payload, GateConfig(expect_sha="b" * 40))


def test_a_task_set_naming_an_unknown_metric_is_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"dataset_id": "x", "tasks": [
        {"task_id": "a", "metric": "vibes", "prompt": "?", "expect": {"kind": "equals", "value": "1"}}
    ]}), encoding="utf-8")
    with pytest.raises(RunnerError, match="metrics the gate does not know"):
        load_tasks(path)


# =====================================================================
# достаточность выборки — измеренная, а не оценённая
# =====================================================================
def _flawless_payload(samples: int) -> dict:
    """Безупречный ПАРНЫЙ прогон: ни одной потерянной задачи, ни одной выдумки."""
    modes = {}
    for mode in MODES:
        block = {}
        for name in REQUIRED_METRICS:
            item = {"score": 0.0 if name in ("hallucination_rate",) else 1.0,
                    "samples": samples}
            if mode != BASELINE:
                item["paired"] = {"lost": 0, "gained": 0}
            block[name] = item
        modes[mode] = block
    # Полосы объявлены так же, как их объявляет настоящий раннер: гейт теперь
    # требует, чтобы полоса FULL ДЕЙСТВИТЕЛЬНО исполняла обвязку, а не
    # описывала её. Здесь измеряется достаточность выборки, поэтому блок должен
    # быть настоящим, иначе этот тест молча проверял бы отказ по другой причине.
    lanes = {mode: {"lane": mode, "kind": "prompt_ablation", "executes_tools": False}
             for mode in MODES if mode != "full"}
    lanes["full"] = {"lane": "full", "kind": "production_execution_loop",
                     "executes_tools": True,
                     "observed": {"model_turns": 2 * samples, "executed": samples,
                                  "declined": 0, "items_with_executed_tool_call": samples}}
    return {"model": "m", "dataset_id": "d", "evaluated_sha": SHA,
            "lanes": lanes, "modes": modes}


@pytest.mark.parametrize("samples,expected", [
    (20, "INSUFFICIENT_EVIDENCE"), (100, "INSUFFICIENT_EVIDENCE"),
    (150, "INSUFFICIENT_EVIDENCE"), (SUFFICIENT_SAMPLES_PER_METRIC, "PASS"),
])
def test_even_a_flawless_paired_run_needs_the_measured_sample_count(samples, expected):
    """Записанный в docs факт, перепроверяемый прогоном самого гейта: 20/100/150
    задач на метрику не дают PASS даже при безупречном результате; 189 дают.
    `--min-samples 20` — нижняя граница допустимости, а не достаточность."""
    report = evaluate(_flawless_payload(samples),
                      GateConfig(expect_sha=SHA, min_samples_per_metric=20))
    assert report["status"] == expected, (samples, report["status"], report["findings"])


def test_the_payload_states_its_own_insufficiency_instead_of_implying_a_pass(lanes):
    tasks = load_tasks(TASKS)
    payload = build_payload(measure(tasks, _perfect_model(tasks), lanes=lanes),
                            model="m", dataset_id="d", evaluated_sha=SHA)
    suff = payload["sufficiency"]
    assert suff["smallest_metric_samples"] == 20
    assert suff["measured_samples_for_a_pass_verdict"] == SUFFICIENT_SAMPLES_PER_METRIC
    assert suff["smallest_metric_samples"] < suff["measured_samples_for_a_pass_verdict"]


# --------------------------------------------------------------- helpers
def _perfect_answer(task):
    expect = task.expect
    kind = expect["kind"]
    if kind == "equals":
        return expect["value"]
    if kind == "contains":
        return " ".join(expect["all_of"])
    if kind == "absent":
        return "не знаю"
    if kind == "tool":
        return expect["name"]
    return json.dumps(expect["fields"], ensure_ascii=False)


def _task_id_of(messages, tasks):
    body = messages[-1]["content"]
    for task in tasks:
        if task.prompt in body:
            return task.task_id
    raise AssertionError("prompt not found")


def _perfect_model(tasks):
    by_id = {t.task_id: t for t in tasks}

    def call(messages, tools=None):
        return _perfect_answer(by_id[_task_id_of(messages, tasks)])

    return call
