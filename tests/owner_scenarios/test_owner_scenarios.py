"""Корневой набор гоняет ВЛАДЕЛЬЧЕСКИЕ сценарии и стережёт сам каркас.

Три разные обязанности, которые нельзя путать:

1. ПРОГОН. Все 20 сценариев исполняются по-настоящему. Уровень каждого
   сверяется с ЗАМОРОЖЕННОЙ таблицей ожиданий — но только когда требуемые
   способности В СРЕДЕ ЕСТЬ. Если способности нет, ожидается ровно тот честный
   вердикт, который каркас обязан выдать вместо исполнения.
2. ЧЕСТНЫЕ ПРОБЕЛЫ. Три сценария заморожены как НЕ зелёные, потому что цепочка
   владельца на этой ветке действительно не замыкается (см. OWNER_GAPS). Если
   продукт починят, эти строки станут красными — и это правильно: таблица
   обязана заметить, что пробел закрылся.
3. КОНТРОЛЬ КАРКАСА. Сценарий без отрицательного контроля не бывает зелёным,
   упавший сценарий — FAIL, а живой шаг модели без НАСТОЯЩЕГО успешного вызова
   адаптера не получает AI_BACKED_CI ни при каких зелёных проверках внутри.

Зависимости: pytest + httpx + bossman_shared. Ни sqlalchemy, ни pydantic, ни
asyncio-плагина: корневой CI ставит только pytest/pytest-timeout/psutil/httpx/pyyaml,
и сценарии, которым нужен весь продукт, в таком прогоне честно не исполняются.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import capabilities as caps  # noqa: E402
import ci_ai_provider as cap  # noqa: E402
import scenario_runner as sr  # noqa: E402

MESSAGES = [{"role": "user", "content": "Ответь одним словом: ГОТОВ"}]
FAKE_KEY = "fake-adapter-placeholder-value"  # ci-secret-scan: allow (подставное значение)

#: Уровень, который сценарий обязан выдать, КОГДА все его способности есть.
EXPECTED_WHEN_CAPABLE = {
    "OS-01": sr.AI_BACKED_CI, "OS-02": sr.AI_BACKED_CI, "OS-03": sr.AI_BACKED_CI,
    "OS-04": sr.AI_BACKED_CI, "OS-05": sr.AI_BACKED_CI, "OS-06": sr.AI_BACKED_CI,
    "OS-07": sr.AI_BACKED_CI, "OS-08": sr.AI_BACKED_CI, "OS-09": sr.AI_BACKED_CI,
    "OS-10": sr.AI_BACKED_CI, "OS-11": sr.AI_BACKED_CI, "OS-12": sr.AI_BACKED_CI,
    "OS-13": sr.AI_BACKED_CI, "OS-14": sr.AI_BACKED_CI, "OS-15": sr.OWNER_REQUIRED,
    "OS-16": sr.OWNER_REQUIRED, "OS-17": sr.AI_BACKED_CI,
    # OS-18 закрыт BL-097: объявленные эффекты дошли до восстановления, бронь
    # отпускается, и следующий допуск ПРОХОДИТ. Сценарий проверяет возобновление,
    # а не описывает дефект.
    "OS-18": sr.AI_BACKED_CI,
    "OS-19": sr.AI_BACKED_CI, "OS-20": sr.AI_BACKED_CI,
}

#: Пробелы ПРОДУКТА, а не прогона. Каждый обязан быть виден владельцу дословно.
OWNER_GAPS = {
    "OS-15": "не имеет API одобрений",
    "OS-16": "генерация не настоящая",
}


@pytest.fixture(scope="module")
def report() -> dict:
    return sr.run_all()


def _row(report: dict, scenario_id: str) -> dict:
    return next(r for r in report["scenarios"] if r["id"] == scenario_id)


# ------------------------------------------------------------- реестр и прогон
def test_реестр_объявляет_ровно_двадцать_сценариев_владельца(report):
    ids = [row["id"] for row in report["scenarios"]]
    assert ids == [f"OS-{n:02d}" for n in range(1, 21)], ids
    assert report["total"] == 20
    assert report["board"] == "OWNER_SCENARIOS"


def test_реестр_и_реализации_не_разъехались():
    """Строка без кода и код без строки одинаково смертельны."""
    registry = sr.load_registry()
    declared = {row["id"] for row in registry["scenarios"]}
    assert declared == set(sr.IMPLEMENTATIONS), (
        declared ^ set(sr.IMPLEMENTATIONS))


def test_уровни_совпадают_с_замороженной_таблицей(report):
    """Со всеми способностями — ожидаемый уровень; без них — честный вердикт."""
    mismatched = {}
    for row in report["scenarios"]:
        blockers = [b["capability"] for b in row["blockers"]]
        if blockers:
            expected = sorted({caps.MISSING_LEVEL[name] for name in blockers},
                              key=lambda lv: sr.LEVELS.index(lv))
            if row["level"] not in expected:
                mismatched[row["id"]] = (row["level"], f"блокеры {blockers}")
            continue
        if row["level"] != EXPECTED_WHEN_CAPABLE[row["id"]]:
            mismatched[row["id"]] = (row["level"], row["reason"][:160])
    assert not mismatched, mismatched


def test_честные_пробелы_продукта_названы_дословно(report):
    """Пробел обязан быть видимым: молчаливый OWNER_REQUIRED ничего не говорит."""
    for scenario_id, fragment in OWNER_GAPS.items():
        row = _row(report, scenario_id)
        if row["blockers"]:
            continue  # способности нет в среде — пробел продукта не измерялся
        assert row["level"] != sr.AI_BACKED_CI, (
            f"{scenario_id}: пробел закрылся — обнови OWNER_GAPS и таблицу ожиданий")
        assert fragment in row["reason"], (scenario_id, row["reason"][:200])


def test_каждое_исполненное_утверждение_имеет_отрицательный_контроль(report):
    for row in report["scenarios"]:
        if row["blockers"] or row["ai_evidence"] in ("blocked", "no_key", "owner_secret"):
            continue
        assert row["positive"] >= 1, row["id"]
        assert row["negative"] >= 1, row["id"]


def test_глубина_улики_записана_у_каждого_сценария(report):
    for row in report["scenarios"]:
        assert row["evidence_depth"] in sr.DEPTHS, row
        assert row["declared_depth"] in sr.DEPTHS, row


def test_отчёт_сериализуем_и_без_запрещённых_меток(report):
    text = json.dumps(report, ensure_ascii=False)
    assert "OWNER_LOCAL_MODEL" not in text
    assert "LOCAL_MODEL_CERTIFIED" not in text
    assert FAKE_KEY not in text
    for row in report["scenarios"]:
        assert row["level"] in sr.LEVELS, row
    assert report["verdict"].startswith("OWNER SCENARIOS:")


def test_словарь_уровней_закрыт():
    assert sr.LEVELS == ("AI_BACKED_CI", "OWNER_HARDWARE_REQUIRED", "OWNER_REQUIRED",
                         "FAIL", "INSUFFICIENT_EVIDENCE", "NOT_RUN")
    assert "OWNER_LOCAL_MODEL" not in sr.LEVELS
    assert "LOCAL_MODEL_CERTIFIED" not in sr.LEVELS
    assert sr.GREEN_LEVELS == (sr.AI_BACKED_CI,)


def test_табло_владельца_не_складывается_с_регрессией(report):
    assert "REGRESSION" in report["board_note"]
    assert report["green"] == report["totals"][sr.AI_BACKED_CI]
    assert sum(report["totals"].values()) == report["total"]


# ------------------------------------------------- контроль самого каркаса
def _scenario(func, *, model_step="none", id_="X-01", depth=sr.PRODUCT_CONTRACTS):
    return sr.Scenario(id=id_, number=0, title="проверка каркаса", chain="каркас",
                       requires=(), model_step=model_step, depth=depth, func=func)


def _ok_transport(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                              "content": "ГОТОВ"}}],
                                     "usage": {"prompt_tokens": 3, "completion_tokens": 1}})


def test_положительный_случай_каркаса_зеленеет():
    def body(ctx):
        ctx.positive("законное поведение работает", True)
        ctx.negative("плохой случай отвергнут", True)

    assert sr.run_scenario(_scenario(body), cap.CIAIProvider(env={})).level == sr.AI_BACKED_CI


def test_сценарий_без_отрицательного_контроля_не_зеленеет():
    def body(ctx):
        ctx.positive("что-то выполнилось", True)

    result = sr.run_scenario(_scenario(body), cap.CIAIProvider(env={}))
    assert result.level == sr.INSUFFICIENT_EVIDENCE
    assert "отрицательного контроля" in result.reason


def test_упавший_сценарий_это_FAIL_а_не_не_проверено():
    def body(ctx):
        ctx.positive("до падения было зелено", True)
        ctx.negative("и отрицательный контроль был", True)
        raise RuntimeError("продукт сломался")

    result = sr.run_scenario(_scenario(body), cap.CIAIProvider(env={}))
    assert result.level == sr.FAIL and "продукт сломался" in result.reason


def test_проваленный_отрицательный_контроль_это_FAIL():
    def body(ctx):
        ctx.positive("законное поведение работает", True)
        ctx.negative("плохой случай был принят", False, "утечка прошла")

    assert sr.run_scenario(_scenario(body), cap.CIAIProvider(env={})).level == sr.FAIL


def test_недостающая_способность_не_исполняет_сценарий_и_называет_блокер():
    calls = []

    def body(ctx):
        calls.append(1)
        ctx.positive("сюда попадать нельзя", True)
        ctx.negative("и сюда", True)

    blocker = caps.Capability("ai_key", False, "ключа нет")
    result = sr.run_scenario(_scenario(body), cap.CIAIProvider(env={}), [blocker])
    assert result.level == sr.OWNER_REQUIRED and not calls
    assert result.blockers[0]["capability"] == "ai_key"


def test_самый_суровый_блокер_решает_уровень():
    broken = caps.Capability("gateway_router", False, "ImportError")
    key = caps.Capability("ai_key", False, "нет ключа")
    result = sr.run_scenario(_scenario(lambda ctx: None), cap.CIAIProvider(env={}),
                             [key, broken])
    assert result.level == sr.FAIL, "сломанный продукт важнее отсутствующего секрета"


def test_живой_шаг_без_настоящего_вызова_не_получает_AI_BACKED_CI():
    """Анти-чит: зелёные проверки внутри не заменяют успешный вызов модели."""
    def body(ctx):
        ctx.positive("сценарий объявил себя успешным", True)
        ctx.negative("и отрицательный контроль нарисовал", True)

    result = sr.run_scenario(_scenario(body, model_step="live"), cap.CIAIProvider(env={}))
    assert result.level == sr.INSUFFICIENT_EVIDENCE
    assert result.ai_evidence == "not_called"


def test_живой_шаг_без_ключа_даёт_OWNER_REQUIRED_а_не_PASS():
    def body(ctx):
        answer = ctx.require_model(ctx.ai.chat(MESSAGES))
        ctx.positive("модель ответила", bool(answer.text))
        ctx.negative("отрицательный контроль", True)

    result = sr.run_scenario(_scenario(body, model_step="live"), cap.CIAIProvider(env={}))
    assert result.level == sr.OWNER_REQUIRED
    assert result.ai_calls and result.ai_calls[0]["outcome"] == cap.NO_KEY


def test_живой_шаг_с_рабочим_адаптером_зеленеет():
    """Парный положительный случай: зелёный путь существует и достижим."""
    def body(ctx):
        answer = ctx.require_model(ctx.ai.chat(MESSAGES))
        ctx.positive("модель ответила по контракту", answer.text == "ГОТОВ")
        ctx.negative("отказ не превращается в ответ", not cap.AIResult(cap.NO_KEY).text)

    provider = cap.CIAIProvider(env={"BOSSMAN_CI_AI_API_KEY": FAKE_KEY},
                                transport=httpx.MockTransport(_ok_transport))
    result = sr.run_scenario(_scenario(body, model_step="live"), provider)
    assert result.level == sr.AI_BACKED_CI and result.ai_evidence == "live_call_ok"


def test_живой_шаг_с_неверным_ответом_модели_это_FAIL():
    def body(ctx):
        ctx.require_model(ctx.ai.chat(MESSAGES))
        ctx.positive("сюда не дойдём", True)

    provider = cap.CIAIProvider(
        env={"BOSSMAN_CI_AI_API_KEY": FAKE_KEY},
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="не json")))
    assert sr.run_scenario(_scenario(body, model_step="live"), provider).level == sr.FAIL


def test_объявленные_тупики_дают_свои_уровни():
    def secret(ctx):
        ctx.owner_required("нужен ключ владельца")

    def hardware(ctx):
        ctx.owner_hardware_required("нужна камера владельца")

    def unproven(ctx):
        ctx.not_proven("цепочку нельзя завершить")

    provider = cap.CIAIProvider(env={})
    assert sr.run_scenario(_scenario(secret), provider).level == sr.OWNER_REQUIRED
    assert sr.run_scenario(_scenario(hardware), provider).level == sr.OWNER_HARDWARE_REQUIRED
    assert sr.run_scenario(_scenario(unproven), provider).level == sr.INSUFFICIENT_EVIDENCE


def test_неизвестная_глубина_улики_отвергается_при_объявлении():
    with pytest.raises(ValueError):
        sr.scenario(id="X-99", depth="на-глазок")


# ------------------------------------------------- контроль самого адаптера
def test_четыре_беды_дают_четыре_разных_исхода():
    def provider(handler, key=FAKE_KEY):
        env = {"BOSSMAN_CI_AI_API_KEY": key} if key else {}
        return cap.CIAIProvider(env=env, transport=httpx.MockTransport(handler))

    outcomes = (
        provider(_ok_transport, key="").chat(MESSAGES).outcome,
        provider(lambda r: httpx.Response(429, text="slow down")).chat(MESSAGES).outcome,
        provider(lambda r: httpx.Response(404, json={"error": "model not found"})).chat(MESSAGES).outcome,
        provider(lambda r: httpx.Response(200, text="{}")).chat(MESSAGES).outcome,
    )
    assert outcomes == (cap.NO_KEY, cap.RATE_LIMITED, cap.MODEL_UNAVAILABLE, cap.INVALID_RESPONSE)
    assert len(set(outcomes)) == 4
    assert set(outcomes) == set(cap.DISTINCT_FAILURE_OUTCOMES)


def test_адаптер_без_ключа_не_придумывает_ответ():
    provider = cap.CIAIProvider(env={})
    result = provider.chat(MESSAGES)
    assert result.outcome == cap.NO_KEY and result.message is None and result.text == ""
    assert provider.ok_calls == 0 and provider.key_present is False
    with pytest.raises(ValueError):
        result.as_core_message()


def test_потолок_расходов_объявлен_и_соблюдается():
    provider = cap.CIAIProvider(env={"BOSSMAN_CI_AI_API_KEY": FAKE_KEY},
                                transport=httpx.MockTransport(_ok_transport), max_calls=2)
    assert [provider.chat(MESSAGES).outcome for _ in range(3)] == [
        cap.OK, cap.OK, cap.BUDGET_EXCEEDED]
    assert provider.ok_calls == 2
    big = cap.CIAIProvider(env={"BOSSMAN_CI_AI_API_KEY": FAKE_KEY},
                           transport=httpx.MockTransport(_ok_transport), max_request_bytes=16)
    assert big.chat(MESSAGES).outcome == cap.BUDGET_EXCEEDED


def test_ключ_не_утекает_ни_в_отчёт_ни_в_подробности():
    provider = cap.CIAIProvider(
        env={"BOSSMAN_CI_AI_API_KEY": FAKE_KEY},
        transport=httpx.MockTransport(lambda r: httpx.Response(400, text=f"bad {FAKE_KEY}")))
    result = provider.chat(MESSAGES)
    assert FAKE_KEY not in result.detail
    assert FAKE_KEY not in json.dumps(result.to_report(), ensure_ascii=False)
    assert FAKE_KEY not in json.dumps(provider.budget_report(), ensure_ascii=False)
    assert provider.key_source == "BOSSMAN_CI_AI_API_KEY"


def test_структурированный_вывод_и_вызов_инструмента_переживают_адаптер():
    """Требование владельца: свободный текст, структура, выбор инструмента, аргументы."""
    call = {"id": "c1", "type": "function",
            "function": {"name": "fs.write", "arguments": "{\"path\": \"a\"}"}}
    tools_provider = cap.CIAIProvider(
        env={"BOSSMAN_CI_AI_API_KEY": FAKE_KEY},
        transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": None,
                                                "tool_calls": [call]}}],
                       "usage": {"prompt_tokens": 5}})))
    result = tools_provider.chat(MESSAGES, tools=[{"type": "function",
                                                   "function": {"name": "fs.write"}}])
    assert result.outcome == cap.OK and result.tool_calls == [call]
    assert json.loads(result.tool_calls[0]["function"]["arguments"])["path"] == "a"
    assert result.as_core_message()["tool_calls"] == [call]

    structured = cap.CIAIProvider(
        env={"BOSSMAN_CI_AI_API_KEY": FAKE_KEY},
        transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant",
                                                "content": '{"шаги": ["раз"]}'}}]})))
    answer = structured.chat(MESSAGES)
    assert answer.outcome == cap.OK and json.loads(answer.text)["шаги"] == ["раз"]


def test_ответ_без_содержимого_и_без_инструментов_не_считается_ответом():
    provider = cap.CIAIProvider(
        env={"BOSSMAN_CI_AI_API_KEY": FAKE_KEY},
        transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": ""}}]})))
    assert provider.chat(MESSAGES).outcome == cap.INVALID_RESPONSE


def test_проба_адаптера_честна_о_готовности():
    assert cap.probe(env={})["ready"] is False
    assert "OWNER_REQUIRED" in cap.probe(env={})["blocker"]
    assert cap.probe(env={"OPENROUTER_API_KEY": FAKE_KEY})["ready"] is True


# ----------------------------------------------------- контроль проб среды
def test_модуль_из_чужого_рабочего_каталога_не_считается_способностью():
    """Прогон обязан зеленеть на коде ЭТОЙ ветки, а не соседнего checkout."""
    ok, detail = caps._module_on_branch("bossman_shared.objective_store")
    assert ok and str(ROOT) in detail
    missing_ok, missing_detail = caps._module_on_branch("совсем.нет.такого.модуля")
    assert missing_ok is False and "ModuleNotFoundError" in missing_detail
    outside_ok, outside_detail = caps._module_on_branch("json")
    assert outside_ok is False and "НЕ с этой ветки" in outside_detail


def test_каждая_недостающая_способность_имеет_адресата():
    for name in caps._PROBES:
        assert caps.MISSING_LEVEL[name] in sr.LEVELS
        assert caps.probe(name).detail.strip(), name
