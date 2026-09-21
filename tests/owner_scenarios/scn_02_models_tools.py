"""Владельческие сценарии 5–8: выбор модели, отказ провайдера, файловый инструмент.

Здесь впервые появляется установленный продукт: файловый инструмент и гейт
завершения берутся из `bossman.toolkit` / `bossman.completion`, а не пишутся
заново. Глубина улики у таких сценариев объявлена отдельно.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

import spine_fixtures as fx  # noqa: E402
from bossman_shared.objective_observer import EnrolledSource, FileStateObserver  # noqa: E402
from ci_ai_provider import (BUDGET_EXCEEDED, CIAIProvider,  # noqa: E402
                            DISTINCT_FAILURE_OUTCOMES, INVALID_RESPONSE,
                            MAX_CALLS_PER_RUN, MAX_REQUEST_BYTES, MODEL_UNAVAILABLE,
                            NO_KEY, OK, RATE_LIMITED, redact)
from scenario_runner import (ADAPTER_CONTRACT, INSTALLED_PRODUCT,  # noqa: E402
                             PRODUCT_CONTRACTS, scenario)

MESSAGES = [{"role": "user", "content": "Ответь одним словом: ГОТОВ"}]
FAKE_KEY = "fake-adapter-placeholder-value"  # ci-secret-scan: allow (подставное значение)


def _ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "ГОТОВ"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 1}})


def _provider(handler, *, key: str = FAKE_KEY, **kwargs) -> CIAIProvider:
    env = {"BOSSMAN_CI_AI_API_KEY": key} if key else {}
    return CIAIProvider(env=env, transport=httpx.MockTransport(handler), **kwargs)


@scenario(id="OS-05", depth=INSTALLED_PRODUCT)
def os05_bossman_picks_an_available_model(ctx) -> None:
    """Алиас владельца разрешается маршрутизатором продукта в достижимую цель.

    Проверяется именно ВЫБОР: локальная цель с более высоким приоритетом идёт
    первой, облачная вырезается ещё до сети при политике «без облака», а
    неизвестный алиас не подменяется «чем-нибудь похожим».
    """
    from bossman.gateway.config import (AliasConfig, BackendConfig,  # noqa: PLC0415
                                        GatewayConfig, ModelTarget)
    from bossman.gateway.router import (CloudPolicyDenied, ModelRouter,  # noqa: PLC0415
                                        RouteNotFound)

    ctx.reached_installed_product("bossman.gateway.router установленного bossman-core")
    config = GatewayConfig(
        backends={
            "local": BackendConfig(name="local", base_url="http://127.0.0.1:11434/v1"),
            "cloud": BackendConfig(name="cloud", base_url="https://openrouter.ai/api/v1",
                                   cloud=True),
        },
        aliases={"bossman-smart": AliasConfig(name="bossman-smart", targets=[
            ModelTarget(backend="local", model="qwen-local", priority=10),
            ModelTarget(backend="cloud", model="cloud-big", priority=90),
        ])},
    )
    # Бэкенды строит сам продукт; сети при этом нет — соединение открывается
    # только на запросе, а здесь проверяется ВЫБОР цели, а не поход в неё.
    router = ModelRouter(config)

    routes = router.resolve("bossman-smart", cloud_allowed=True)
    ctx.positive("алиас владельца разрешается в список достижимых целей",
                 bool(routes), f"целей={len(routes)}")
    ctx.positive("первой идёт локальная цель с более высоким приоритетом",
                 routes[0].backend_name == "local" and routes[0].model == "qwen-local"
                 and routes[0].is_cloud is False,
                 f"первая цель={routes[0].backend_name}/{routes[0].model}")
    local_only = router.resolve("bossman-smart", cloud_allowed=False)
    ctx.positive("при запрете облака остаётся только локальная цель",
                 [r.backend_name for r in local_only] == ["local"],
                 f"цели={[r.backend_name for r in local_only]}")

    ctx.refused("неизвестный алиас не подменяется «чем-нибудь похожим»",
                lambda: router.resolve("нет-такой-модели", cloud_allowed=False),
                RouteNotFound)
    cloudless = GatewayConfig(
        backends={"cloud": BackendConfig(name="cloud", base_url="https://openrouter.ai/api/v1",
                                         cloud=True)},
        aliases={"только-облако": AliasConfig(name="только-облако", targets=[
            ModelTarget(backend="cloud", model="cloud-big", priority=10)])},
    )
    only_cloud = ModelRouter(cloudless)
    ctx.refused("при политике «без облака» облачная цель вырезается ДО сети",
                lambda: only_cloud.resolve("только-облако", cloud_allowed=False),
                CloudPolicyDenied)


@scenario(id="OS-06", depth=ADAPTER_CONTRACT)
def os06_provider_failure_has_a_correct_fallback(ctx) -> None:
    """Четыре беды — четыре РАЗНЫХ исхода, и ни один не даёт синтетический PASS."""
    ok = _provider(_ok_response).chat(MESSAGES)
    ctx.positive("исправный провайдер отвечает по контракту chat/completions",
                 ok.outcome == OK and ok.text == "ГОТОВ"
                 and ok.as_core_message()["_usage"]["prompt_tokens"] == 11)

    no_key = _provider(_ok_response, key="").chat(MESSAGES)
    rate = _provider(lambda r: httpx.Response(429, text="slow down")).chat(MESSAGES)
    missing = _provider(lambda r: httpx.Response(
        404, json={"error": {"message": "model not found"}})).chat(MESSAGES)
    garbage = _provider(lambda r: httpx.Response(200, text="я подумал и решил")).chat(MESSAGES)
    outcomes = (no_key.outcome, rate.outcome, missing.outcome, garbage.outcome)
    ctx.positive("нет ключа / 429 / нет модели / неверный ответ дают четыре разных исхода",
                 outcomes == (NO_KEY, RATE_LIMITED, MODEL_UNAVAILABLE, INVALID_RESPONSE)
                 and len(set(outcomes)) == 4
                 and set(outcomes) == set(DISTINCT_FAILURE_OUTCOMES),
                 f"исходы={outcomes}")
    ctx.positive("потолки расходов объявлены константами",
                 MAX_CALLS_PER_RUN > 0 and MAX_REQUEST_BYTES > 0,
                 f"вызовов={MAX_CALLS_PER_RUN}, байт={MAX_REQUEST_BYTES}")

    ctx.negative("ни один отказ не выдал текста, похожего на ответ модели",
                 all(r.text == "" and r.message is None
                     for r in (no_key, rate, missing, garbage)))
    ctx.refused("отказ нельзя превратить в сообщение модели",
                lambda: no_key.as_core_message(), ValueError)
    empty = CIAIProvider(env={})
    ctx.negative("без ключа адаптер не ходит в сеть и не выдумывает ответ",
                 empty.chat(MESSAGES).outcome == NO_KEY and empty.ok_calls == 0)
    capped = _provider(_ok_response, max_calls=1)
    capped.chat(MESSAGES)
    ctx.negative("превышение потолка вызовов — отдельный исход, а не ответ",
                 capped.chat(MESSAGES).outcome == BUDGET_EXCEEDED and capped.ok_calls == 1)
    leaky = _provider(lambda r: httpx.Response(400, text=f"bad {FAKE_KEY}")).chat(MESSAGES)
    ctx.negative("значение ключа не попадает в отчёт об ошибке",
                 FAKE_KEY not in repr(leaky.to_report()) and FAKE_KEY not in leaky.detail)
    ctx.negative("редактор маскирует и похожие на ключ строки",
                 "sk-fake-token-0001" not in redact("Authorization: Bearer sk-fake-token-0001"))


@scenario(id="OS-07", depth=INSTALLED_PRODUCT)
def os07_bossman_uses_the_file_tool(ctx) -> None:
    """Файловый инструмент ПРОДУКТА пишет и читает внутри рабочей папки агента."""
    from bossman.toolkit import ToolContext  # noqa: PLC0415
    from bossman.toolkit.files import fs_list, fs_read, fs_write  # noqa: PLC0415

    workdir = ctx.path("agent", "workdir")
    workdir.mkdir(parents=True, exist_ok=True)
    ctx.reached_installed_product("bossman.toolkit.files установленного bossman-core")
    tool_ctx = ToolContext(agent="bossman-coder", workdir=workdir)

    written = asyncio.run(fs_write({"path": "заметка.txt", "content": "зелёная сборка\n"}, tool_ctx))
    ctx.positive("инструмент продукта записал файл без ошибки",
                 not written.error and (workdir / "заметка.txt").exists(),
                 written.one_line)
    read_back = asyncio.run(fs_read({"path": "заметка.txt"}, tool_ctx))
    ctx.positive("инструмент продукта читает записанное обратно",
                 not read_back.error and "зелёная сборка" in read_back.content,
                 read_back.one_line)
    listing = asyncio.run(fs_list({"path": "."}, tool_ctx))
    ctx.positive("файл виден в перечне рабочей папки агента",
                 "заметка.txt" in listing.content, listing.one_line)

    missing = asyncio.run(fs_read({"path": "нет-такого.txt"}, tool_ctx))
    ctx.negative("чтение несуществующего файла — честная ошибка, а не пустой успех",
                 missing.error is True, missing.one_line)
    ctx.refused("выход за рабочую папку агента запрещён",
                lambda: asyncio.run(fs_read({"path": "../../../etc/passwd"}, tool_ctx)),
                PermissionError)


@scenario(id="OS-08", depth=INSTALLED_PRODUCT)
def os08_file_change_is_verified_by_bytes(ctx) -> None:
    """Гейт завершения продукта перечитывает файл и сверяет БАЙТЫ, а не отчёт инструмента."""
    from bossman.completion import CompletionContract, CompletionGate, FileObligation  # noqa: PLC0415
    from bossman.toolkit import ToolContext  # noqa: PLC0415
    from bossman.toolkit.files import fs_write  # noqa: PLC0415

    workdir = ctx.path("agent", "workdir")
    workdir.mkdir(parents=True, exist_ok=True)
    ctx.reached_installed_product("bossman.completion.CompletionGate установленного bossman-core")
    tool_ctx = ToolContext(agent="bossman-coder", workdir=workdir)
    content = "зелёная сборка\n"
    args = {"path": "результат.txt", "content": content}

    gate = CompletionGate(CompletionContract(mode="action",
                                             files=[FileObligation(path="результат.txt",
                                                                   contains="зелёная")]),
                          workdir)
    result = asyncio.run(fs_write(args, tool_ctx))
    gate.record("fs.write", "write", args, error=result.error)
    status, text = gate.finish()
    ctx.positive("после записи гейт независимо подтверждает результат байтами",
                 status != "unverified" and not gate.unverified_effect,
                 f"status={status} текст={text[:80]}")

    # Независимое наблюдение продукта поверх того же файла.
    store, spec, _ = fx.ready_store(ctx.path("state", "objectives.sqlite3"))
    observer = FileStateObserver(
        EnrolledSource(source_ref=fx.SOURCE, source_revision="r1", owner_id=fx.OWNER,
                       scope_id=fx.SCOPE_A, objective_digest=spec.digest),
        workdir / "результат.txt")
    observed = observer.observe(now=fx.NOW)
    ctx.positive("наблюдатель продукта видит ровно записанные байты",
                 observed.values["exists"] is True
                 and observed.values["size_bytes"] == len(content.encode("utf-8")),
                 f"size={observed.values['size_bytes']}")

    # Отрицательный контроль: инструмент «отчитался», а на диске другое.
    lying = CompletionGate(CompletionContract(mode="action"), workdir)
    liar_args = {"path": "подмена.txt", "content": "обещанное содержимое"}
    asyncio.run(fs_write({"path": "подмена.txt", "content": "на диске другое"}, tool_ctx))
    lying.record("fs.write", "write", liar_args, error=False)
    ctx.negative("расхождение обещанного и записанного помечено как непроверенное",
                 lying.unverified_effect is True)

    absent = CompletionGate(CompletionContract(mode="action"), workdir)
    absent.record("fs.write", "write", {"path": "никогда.txt", "content": "нет"}, error=False)
    ctx.negative("объявленная запись без файла на диске не считается сделанной",
                 absent.unverified_effect is True)
    ctx.negative("ошибка инструмента не может дать проверенный эффект",
                 _errored_gate(CompletionGate, CompletionContract, workdir).unverified_effect is True)
    _ = store


def _errored_gate(gate_cls, contract_cls, workdir):
    gate = gate_cls(contract_cls(mode="action"), workdir)
    gate.record("fs.write", "write", {"path": "ошибка.txt", "content": "x"}, error=True)
    return gate
