"""Регрессии, найденные при прогоне /chat.html в настоящем браузере (RUN_20261001_CHAT).

Каждая проверка здесь красная на коде до правки:
* список исполнителей отдавал `usable/refusal/billing = null` для всех моделей, поэтому облачный
  агент с неизвестной ценой в выборе не отключался (сборка без `provider_governance.model_billing`);
* окно ходило в несуществующий `GET /api/models/picker` (404 в консоли на каждой загрузке) — теперь
  любой путь `/api/...` из `ui/chat/*.js` обязан быть настоящим маршрутом сервера;
* чистая логика (markdown, итог ответа со скрытым рассуждением, место работы по псевдониму,
  отмена записи микрофона) — в node: `ui/tests/chat_ux_run.test.mjs`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "ui"


async def _agent(client, *, base_url: str, name: str, kind: str = "local", price_in=None, price_out=None,
                 provider_name: str | None = None) -> dict:
    provider = (await client.post("/api/providers", json={
        "name": provider_name or f"провайдер {name}", "kind": "openai_compat", "base_url": base_url})).json()
    body = {"provider_id": provider["id"], "name": name, "alias": name, "kind": kind}
    if price_in is not None:
        body["price_in"], body["price_out"] = price_in, price_out
    model = (await client.post("/api/models", json=body)).json()
    agent = (await client.post("/api/agents", json={"name": f"агент {name}", "model_id": model["id"],
                                                    "max_steps": 1})).json()
    return agent


async def _model_of(client, agent_id: int) -> dict:
    body = (await client.get("/api/chat/options")).json()
    [found] = [a for a in body["agents"] if a["id"] == agent_id]
    return found["model"]


async def test_unknown_price_cloud_model_is_unusable_with_a_russian_reason(env):
    agent = await _agent(env.client, base_url="https://api.cloud-provider.example/v1", name="cloud-x", kind="cloud")
    model = await _model_of(env.client, agent["id"])
    assert model["locality"] == "cloud" and model["locality_detail"] == "cloud"
    assert model["billing"] == "unknown_price"
    assert model["usable"] is False, "explicit choice would only queue a certain refusal"
    assert re.search(r"[А-Яа-я]", model["refusal"]) and "цен" in model["refusal"].lower(), model["refusal"]


async def test_local_and_lan_models_are_usable_and_free(env):
    local = await _agent(env.client, base_url="http://127.0.0.1:11434/v1", name="qwen-local")
    lan = await _agent(env.client, base_url="http://192.168.1.50:11434/v1", name="qwen-lan")
    m1, m2 = await _model_of(env.client, local["id"]), await _model_of(env.client, lan["id"])
    assert (m1["billing"], m1["usable"], m1["locality_detail"], m1["free"]) == ("local", True, "local", True)
    assert (m2["billing"], m2["usable"], m2["locality_detail"], m2["free"]) == ("local", True, "lan", True)
    assert m1["refusal"] == "" and m2["refusal"] == ""


async def test_paid_cloud_model_is_refused_by_the_free_only_policy_and_allowed_when_the_operator_opts_out(
        env, monkeypatch):
    monkeypatch.delenv("BOSSMAN_ALLOW_PAID_CLOUD", raising=False)
    agent = await _agent(env.client, base_url="https://api.cloud-provider.example/v1", name="paid-x",
                         kind="cloud", price_in=3.0, price_out=15.0)
    blocked = await _model_of(env.client, agent["id"])
    assert blocked["usable"] is False and blocked["billing"] in ("paid", "blocked")
    assert "бесплатн" in blocked["refusal"].lower(), blocked["refusal"]
    monkeypatch.setenv("BOSSMAN_ALLOW_PAID_CLOUD", "1")          # явный выбор оператора, не следствие ключа или цены
    allowed = await _model_of(env.client, agent["id"])
    assert allowed["usable"] is True and allowed["billing"] in ("paid", "paid_capped") and allowed["refusal"] == ""


async def test_free_openrouter_model_is_usable_and_marked_free_cloud(env):
    # цена 0/0 приходит из синхронизированного каталога OpenRouter: без неё модель «без известной цены»
    agent = await _agent(env.client, base_url="https://openrouter.ai/api/v1", name="vendor/model:free", kind="cloud",
                         price_in=0.0, price_out=0.0)
    model = await _model_of(env.client, agent["id"])
    assert model["billing"] == "free_cloud" and model["usable"] is True and model["free"] is True


# --------------------------------------------------------------- окно не ходит в несуществующие ручки


def _route_patterns(app) -> list[tuple[re.Pattern, set[str]]]:
    """Маршруты сервера из его схемы (app.routes у FastAPI хранит вложенные роутеры, а не пути): (путь, методы)."""
    out = []
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith("/api"):
            continue
        marked = re.sub(r"\{[^}]+\}", "PARAMSLOT", path)
        out.append((re.compile("^" + re.escape(marked).replace("PARAMSLOT", "[^/]+") + "$"),
                    {m.upper() for m in ops}))
    return out


def _chat_api_calls() -> list[tuple[str, str, str]]:
    """(метод, путь, файл) для каждого литерала `/api/...` из ui/chat/*.js и pages/chat.js.

    Метод берётся из `method: 'X'` сразу после пути (до следующего вызова api.raw), иначе GET;
    шаблоны `${...}` -> `{}`, строка запроса отбрасывается."""
    calls = []
    files = sorted((UI / "chat").glob("*.js")) + [UI / "pages" / "chat.js"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"""['"`](/api/(?:\$\{[^}]*\}|[A-Za-z0-9_./\-])*)""", text):
            raw = re.sub(r"\$\{[^}]*\}", "{}", m.group(1)).split("?")[0].rstrip("/")
            if raw in ("/api", ""):
                continue
            window = text[m.end():m.end() + 200]
            window = window.split("api.raw(")[0]
            verb = re.search(r"method:\s*['\"]([A-Za-z]+)['\"]", window)
            calls.append(((verb.group(1) if verb else "GET").upper(), raw, path.name))
    return calls


def _unserved(calls, patterns) -> dict:
    missing = {}
    for verb, path, fname in calls:
        probe = path.replace("{}", "x")
        if not any(rx.match(probe) and verb in methods for rx, methods in patterns):
            missing.setdefault(f"{verb} {path}", set()).add(fname)
    return {k: sorted(v) for k, v in missing.items()}


async def test_every_api_call_of_the_chat_window_is_a_real_server_route(env):
    patterns = _route_patterns(env.app)
    assert patterns, "no /api routes found: the test would pass vacuously"
    calls = _chat_api_calls()
    seen = {(verb, path) for verb, path, _ in calls}
    assert ("GET", "/api/chat/threads") in seen and ("POST", "/api/chat/threads/{}/send") in seen, (
        "the literal scan sees the chat code and reads the method of a call")
    missing = _unserved(calls, patterns)
    assert not missing, f"the chat calls routes the server does not serve (404/405 on every load): {missing}"


def test_route_scan_negative_control_a_get_to_a_patch_only_path_is_reported(tmp_path, monkeypatch):
    """Проверка проверки: `GET /api/models/picker` совпадает по пути с `/api/models/{id}`, но GET там не обслуживается."""
    patterns = [(re.compile(r"^/api/models/[^/]+$"), {"PATCH", "DELETE"}), (re.compile(r"^/api/chat/options$"), {"GET"})]
    (tmp_path / "chat").mkdir()
    (tmp_path / "pages").mkdir()
    (tmp_path / "chat" / "main.js").write_text(
        "S.picker = await api.raw('/api/models/picker');\n"
        "await api.raw(`/api/models/${enc(id)}`, { method: 'PATCH', body });\n"
        "S.options = await api.raw('/api/chat/options');\n", encoding="utf-8")
    (tmp_path / "pages" / "chat.js").write_text("", encoding="utf-8")
    monkeypatch.setitem(globals(), "UI", tmp_path)
    assert _unserved(_chat_api_calls(), patterns) == {"GET /api/models/picker": ["main.js"]}


# --------------------------------------------------------------- чистая логика в node


def _node() -> list[str]:
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("node is not installed: ui/tests/chat_ux_run.test.mjs not run")
    # до 20.19 node не распознаёт ES-модули без "type": "module" — флаг включает распознавание (на новых безвреден)
    return [node, "--experimental-detect-module", "--no-warnings"]


def test_chat_run_regressions_in_node():
    result = subprocess.run([*_node(), "--test", str(UI / "tests" / "chat_ux_run.test.mjs")],
                            capture_output=True, text=True, timeout=180, cwd=str(UI.parent))
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    assert re.search(r"^# fail 0$", result.stdout, re.M), result.stdout[-2000:]


# --------------------------------------------------------------- подписка Claude: старый CLI (просьба lane rave-apps)


def _login(result):
    async def login(*, api_key: bool = False):
        return result
    return login


def _version(result):
    async def version():
        return result
    return version


async def _claude_item(env, monkeypatch, version):
    from bcc.rave import connectors
    monkeypatch.setattr(connectors, "claude_login", _login({"installed": True, "logged_in": True, "subscription": True}))
    monkeypatch.setattr(connectors, "codex_login", _login({"installed": False, "logged_in": False, "reason": "CLI не найден"}))
    monkeypatch.setattr(connectors, "claude_version", _version(version))
    body = (await env.client.get("/api/chat/options")).json()
    [claude] = [s for s in body["subscriptions"] if s["name"] == "claude"]
    return claude


async def test_old_claude_cli_is_reported_in_options_with_the_reason(env, monkeypatch):
    claude = await _claude_item(env, monkeypatch, {"installed": True, "version": "2.0.1", "ok": False, "min": "2.1.259"})
    assert claude["version_ok"] is False
    assert "2.0.1" in claude["version_problem"] and "2.1.259" in claude["version_problem"], claude["version_problem"]
    assert "2.1.259" in claude["note"], "the note the owner reads says why Claude is not usable"


async def test_current_claude_cli_has_no_version_problem(env, monkeypatch):
    claude = await _claude_item(env, monkeypatch, {"installed": True, "version": "2.1.284", "ok": True, "min": "2.1.259"})
    assert claude["version_ok"] is True and claude["version_problem"] == ""
    assert claude["logged_in"] is True
