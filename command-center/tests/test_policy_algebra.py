"""P0-B (аудит 10×10): композиция политики монотонна — нижний слой (tool_rules)
может ужесточить решение верхнего, но не ослабить его.

    DENY ⊗ X = DENY ;  hook-ASK ⊗ AUTO = ASK ;  AUTO ⊗ ASK = ASK
"""
from bcc.tools import ToolSpec, decide_effect


def _spec(hook=None, default="ask", permission="terminal.run"):
    return ToolSpec(name="terminal.run", description="", handler=None,  # type: ignore[arg-type]
                    permission=permission, default_effect=default, effect_hook=hook)


def _push_hook(args):
    cmd = str(args.get("command", ""))
    if "force" in cmd:
        return ("deny", "force push запрещён")
    if cmd.startswith("git push"):
        return ("ask", "push — только с подтверждением")
    return None


GRANTED = {"permissions": {"terminal.run": True}}


def test_rule_cannot_relax_hook_ask_back_to_auto():
    rules = [{"tool": "terminal.run", "resource": "git push*", "effect": "auto"}]
    effect, reason = decide_effect(_spec(_push_hook), {"command": "git push origin main"}, GRANTED, rules)
    assert effect == "ask" and "пол политики" in reason


def test_rule_cannot_relax_deny_from_hook_or_default():
    rules = [{"tool": "*", "resource": "*", "effect": "auto"}]
    assert decide_effect(_spec(_push_hook), {"command": "git push --force"}, GRANTED, rules)[0] == "deny"
    assert decide_effect(_spec(default="deny", permission=None), {"command": "ls"}, GRANTED, rules)[0] == "deny"


def test_rule_can_tighten_and_can_lift_ungranted_default_ask():
    # ужесточение — всегда
    rules = [{"tool": "terminal.run", "resource": "*", "effect": "ask"}]
    assert decide_effect(_spec(), {"command": "ls"}, GRANTED, rules)[0] == "ask"
    rules = [{"tool": "terminal.run", "resource": "*", "effect": "deny"}]
    assert decide_effect(_spec(), {"command": "ls"}, GRANTED, rules)[0] == "deny"
    # owner-одобренное правило снимает ASK, возникший только из невыданного права
    rules = [{"tool": "terminal.run", "resource": "pytest*", "effect": "auto"}]
    assert decide_effect(_spec(), {"command": "pytest -q"}, {"permissions": {}}, rules)[0] == "auto"
    # ...но не ASK хука по опасным аргументам
    assert decide_effect(_spec(_push_hook), {"command": "git push"}, {"permissions": {}},
                         [{"tool": "*", "resource": "*", "effect": "auto"}])[0] == "ask"


def test_unknown_rule_effect_falls_to_ask():
    rules = [{"tool": "*", "resource": "*", "effect": "yolo"}]
    assert decide_effect(_spec(), {"command": "ls"}, GRANTED, rules)[0] == "ask"


def test_constant_gate_hook_opts_out_of_floor_but_deny_stays():
    # OpenCode: хук-константа объявлен hook_is_floor=False — осознанное правило владельца снимает ASK…
    spec = ToolSpec(name="opencode.session.start", description="", handler=None,  # type: ignore[arg-type]
                    permission="terminal.run", default_effect="ask",
                    effect_hook=lambda a: ("ask", "автономный агент"), hook_is_floor=False)
    rules = [{"tool": "opencode.*", "resource": "*", "effect": "auto"}]
    assert decide_effect(spec, {"title": "x"}, GRANTED, rules)[0] == "auto"
    # …но без правила ASK остаётся, и DENY хука не снимается даже с опт-аутом
    assert decide_effect(spec, {"title": "x"}, GRANTED, [])[0] == "ask"
    deny_spec = ToolSpec(name="opencode.session.start", description="", handler=None,  # type: ignore[arg-type]
                         permission="terminal.run", effect_hook=lambda a: ("deny", "нет"), hook_is_floor=False)
    assert decide_effect(deny_spec, {"title": "x"}, GRANTED, rules)[0] == "deny"


# ------------------------------------------------- аудит: два разрыва монотонности

def test_a_granted_permission_lifts_ask_but_never_a_default_deny():
    """Право отвечает «можно ли без подтверждения», а не «можно ли вообще».

    До правки инструмент с default_effect="deny" становился AUTO у любого
    агента с выданным правом — DENY автора спецификации исчезал молча.
    """
    assert decide_effect(_spec(default="deny"), {"command": "ls"}, GRANTED, None)[0] == "deny"
    # Контроль: то же право по-прежнему снимает ASK у default="ask".
    assert decide_effect(_spec(default="ask"), {"command": "ls"}, GRANTED, None)[0] == "auto"
    # И правило владельца default-DENY тоже не открывает.
    rules = [{"tool": "*", "resource": "*", "effect": "auto"}]
    assert decide_effect(_spec(default="deny"), {"command": "ls"}, GRANTED, rules)[0] == "deny"


def test_a_matched_deny_rule_is_not_undone_by_a_later_broad_auto_rule():
    """Порядок правил решает между AUTO и ASK, но отказ он не отменяет.

    До правки `rm *` → deny, а следом `*`/`*` → auto давало AUTO: общее правило
    в конце списка тихо снимало конкретный запрет владельца.
    """
    rules = [{"tool": "terminal.run", "resource": "rm *", "effect": "deny", "reason": "не удалять"},
             {"tool": "*", "resource": "*", "effect": "auto"}]
    effect, reason = decide_effect(_spec(), {"command": "rm -rf /tmp/x"}, GRANTED, rules)
    assert effect == "deny" and "пол политики — deny" in reason
    # То же с ASK после DENY.
    rules[1]["effect"] = "ask"
    assert decide_effect(_spec(), {"command": "rm -rf /tmp/x"}, GRANTED, rules)[0] == "deny"
    # Контроль: команда, НЕ попавшая под запрет, по-прежнему получает общее правило.
    assert decide_effect(_spec(), {"command": "ls"}, GRANTED, rules)[0] == "ask"
    # Контроль: DENY ПОСЛЕ auto тоже побеждает — ужесточение всегда проходит.
    rules = [{"tool": "*", "resource": "*", "effect": "auto"},
             {"tool": "terminal.run", "resource": "rm *", "effect": "deny"}]
    assert decide_effect(_spec(), {"command": "rm -rf /tmp/x"}, GRANTED, rules)[0] == "deny"


def test_rule_order_still_decides_between_auto_and_ask():
    """Что правка НЕ меняет: между не-DENY эффектами последнее правило — последнее слово."""
    rules = [{"tool": "terminal.run", "resource": "*", "effect": "ask"},
             {"tool": "terminal.run", "resource": "pytest*", "effect": "auto"}]
    assert decide_effect(_spec(), {"command": "pytest -q"}, GRANTED, rules)[0] == "auto"
    assert decide_effect(_spec(), {"command": "ls"}, GRANTED, rules)[0] == "ask"


def test_wildcard_and_resource_matching_apply_to_the_deny_floor():
    """Пол по DENY поднимается только для СОВПАВШЕГО ресурса."""
    rules = [{"tool": "terminal.*", "resource": "git push*", "effect": "deny"},
             {"tool": "*", "resource": "*", "effect": "auto"}]
    assert decide_effect(_spec(), {"command": "git push origin main"}, GRANTED, rules)[0] == "deny"
    assert decide_effect(_spec(), {"command": "git status"}, GRANTED, rules)[0] == "auto"
