"""V2.1 фаза L — компилятор прав из естественного языка.

Проверяется РЕАЛЬНЫЙ эффект: скомпилированные `tool_rules` меняют решение
`bcc.tools.decide_effect`, расширение доступа не применяется без подтверждения,
сужение применяется сразу, непонятые фразы возвращаются списком.
"""
import sqlalchemy as sa

from bcc.db import agents as agents_t, approvals as approvals_t
from bcc.features.nl_permissions import compile_policy, policy_delta
from bcc.tools import ToolSpec, decide_effect

from .helpers import make_stack

EN = ("Coder can edit D:/Projects, run tests automatically, "
      "ask before installs and git push, never access wallets.")
RU = ("Кодер может редактировать D:/Projects, запускать тесты автоматически, "
      "спрашивать перед установками и git push, никогда не трогать кошельки.")


def _terminal_spec() -> ToolSpec:
    return ToolSpec(name="terminal.run", description="", handler=None,  # type: ignore[arg-type]
                    permission="terminal.run", default_effect="ask")


def _check_example(out: dict) -> None:
    policy = out["policy"]
    assert policy["filesystem"]["allow_read"] == ["D:/Projects/**"]
    assert policy["filesystem"]["allow_write"] == ["D:/Projects/**"]
    term = policy["terminal"]
    assert term["pytest*"] == "auto"
    assert term["npm install*"] == "ask"
    assert term["git push*"] == "ask"
    assert policy["secrets"]["*wallet*"] == "deny"
    assert out["unrecognised"] == []


def test_example_sentence_compiles_en():
    _check_example(compile_policy(EN))


def test_example_sentence_compiles_ru():
    _check_example(compile_policy(RU))


def test_compiled_rules_change_decide_effect():
    """Главный тест фазы L: политика реально влияет на решение рантайма."""
    rules = compile_policy(EN)["tool_rules"]
    spec = _terminal_spec()
    agent = {"permissions": {"terminal.run": True}}   # право выдано → базово auto

    assert decide_effect(spec, {"command": "pytest -q"}, agent, rules)[0] == "auto"
    assert decide_effect(spec, {"command": "npm install left-pad"}, agent, rules)[0] == "ask"
    assert decide_effect(spec, {"command": "git push origin main"}, agent, rules)[0] == "ask"
    assert decide_effect(spec, {"command": "cat my_wallet.dat"}, agent, rules)[0] == "deny"
    # без правил те же команды шли бы auto — значит менялось именно правило
    assert decide_effect(spec, {"command": "npm install left-pad"}, agent, [])[0] == "auto"


def test_deny_wins_over_auto_regardless_of_phrase_order():
    """«Разрешено всё в проекте, но кошельки никогда» — deny не должен теряться."""
    rules = compile_policy("agent can run tests automatically, never touch wallets")["tool_rules"]
    assert [r["effect"] for r in rules] == sorted(
        [r["effect"] for r in rules], key=lambda e: {"auto": 0, "ask": 1, "deny": 2}[e])
    spec = _terminal_spec()
    agent = {"permissions": {"terminal.run": True}}
    assert decide_effect(spec, {"command": "pytest && cat wallet.key"}, agent, rules)[0] == "deny"


def test_unparseable_phrases_are_reported():
    out = compile_policy("Coder can edit D:/Projects, разберись с квартальным отчётом, "
                         "never access wallets")
    texts = [u["text"] for u in out["unrecognised"]]
    assert texts == ["разберись с квартальным отчётом"]
    assert out["unrecognised"][0]["reason"]
    assert out["tool_rules"]                       # остальное всё равно скомпилировалось


def test_effect_words_without_object_are_reported():
    out = compile_policy("никогда")
    assert out["tool_rules"] == []
    assert out["unrecognised"][0]["text"] == "никогда"


def test_policy_delta_marks_broadening_and_narrowing():
    old = [{"tool": "terminal.run", "resource": "git push*", "effect": "deny"}]
    new = [{"tool": "terminal.run", "resource": "git push*", "effect": "auto"}]
    assert policy_delta(old, new)["broadens"] is True
    assert policy_delta(new, old)["broadens"] is False
    assert policy_delta(new, old)["narrowed"]
    # снятие старого запрета — тоже расширение, даже если новых правил там нет
    assert policy_delta(old, [{"tool": "memory.search", "resource": "*",
                               "effect": "deny"}])["broadens"] is True


# ---------- API ----------

async def test_compile_endpoint_shows_preview_without_applying(env):
    stack = await make_stack(env.client)
    aid = stack["agent"]["id"]
    r = (await env.client.post("/api/permissions/compile",
                               json={"text": EN, "agent_id": aid})).json()
    assert r["valid"] and r["broadening"] is True
    assert r["delta"]["broadened"]
    async with env.svc.db.session() as s:
        perms = (await s.execute(sa.select(agents_t.c.permissions)
                                 .where(agents_t.c.id == aid))).first()._mapping["permissions"]
    assert not (perms or {}).get("tool_rules")          # превью ничего не записало


async def test_narrowing_applies_directly(env):
    stack = await make_stack(env.client)
    aid = stack["agent"]["id"]
    r = (await env.client.post("/api/permissions/apply",
                               json={"text": "never access wallets", "agent_id": aid})).json()
    assert r["applied"] is True and r["broadening"] is False
    async with env.svc.db.session() as s:
        perms = (await s.execute(sa.select(agents_t.c.permissions)
                                 .where(agents_t.c.id == aid))).first()._mapping["permissions"]
    assert perms["tool_rules"] and all(x["effect"] == "deny" for x in perms["tool_rules"])
    assert (await env.client.get("/api/approvals")).json() == []   # подтверждение не нужно


async def test_broadening_requires_confirmation(env):
    stack = await make_stack(env.client)
    aid = stack["agent"]["id"]
    first = (await env.client.post("/api/permissions/apply",
                                   json={"text": EN, "agent_id": aid})).json()
    assert first["applied"] is False and first["requires_confirmation"] is True
    assert first["delta"]["broadened"]

    async with env.svc.db.session() as s:
        perms = (await s.execute(sa.select(agents_t.c.permissions)
                                 .where(agents_t.c.id == aid))).first()._mapping["permissions"]
    assert not (perms or {}).get("tool_rules")          # до решения — не применено

    appr = (await env.client.get("/api/approvals")).json()
    assert len(appr) == 1 and appr[0]["kind"] == "permissions"
    assert "D:/Projects" in appr[0]["preview"]

    # повтор без решения человека тоже не применяет
    again = await env.client.post("/api/permissions/apply",
                                  json={"text": EN, "agent_id": aid,
                                        "approval_id": first["approval_id"]})
    assert again.status_code == 409

    await env.client.post(f"/api/approvals/{first['approval_id']}",
                          json={"approve": True, "by": "владелец"})
    done = (await env.client.post("/api/permissions/apply",
                                  json={"text": EN, "agent_id": aid,
                                        "approval_id": first["approval_id"]})).json()
    assert done["applied"] is True
    got = (await env.client.get(f"/api/permissions/{aid}")).json()
    assert got["policy"]["terminal"]["git push*"] == "ask"
    assert got["source"]["text"] == EN


async def test_approval_cannot_be_reused_for_another_policy(env):
    """Одобрили одну политику — подставить другую под тот же approval нельзя."""
    stack = await make_stack(env.client)
    aid = stack["agent"]["id"]
    first = (await env.client.post("/api/permissions/apply",
                                   json={"text": EN, "agent_id": aid})).json()
    await env.client.post(f"/api/approvals/{first['approval_id']}",
                          json={"approve": True, "by": "владелец"})
    other = await env.client.post("/api/permissions/apply",
                                  json={"text": "agent can run docker automatically",
                                        "agent_id": aid,
                                        "approval_id": first["approval_id"]})
    assert other.status_code == 409

    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(approvals_t))).fetchall()
    assert len(rows) == 1       # 409 случился ДО создания нового подтверждения


async def test_apply_reports_unrecognised(env):
    stack = await make_stack(env.client)
    r = (await env.client.post(
        "/api/permissions/apply",
        json={"text": "never access wallets, разберись с квартальным отчётом",
              "agent_id": stack["agent"]["id"]})).json()
    assert r["applied"] is True
    assert [u["text"] for u in r["unrecognised"]] == ["разберись с квартальным отчётом"]


async def test_apply_without_any_rule_is_422(env):
    stack = await make_stack(env.client)
    r = await env.client.post("/api/permissions/apply",
                              json={"text": "погода сегодня хорошая",
                                    "agent_id": stack["agent"]["id"]})
    assert r.status_code == 422
