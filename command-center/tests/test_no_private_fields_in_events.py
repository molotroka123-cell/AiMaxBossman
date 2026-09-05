"""TZ-08 §2.7 (приватность телеметрии): ни один `bus.emit(...)` и ни один
`_log(...)` в bcc не передаёт приватные поля именованными аргументами —
`messages`, `prompt`, `system_prompt`, `api_key`, `cookie(s)`, `token`, `password`.
Статический AST-обход всего пакета: событие — телеметрия, не носитель секретов."""
from __future__ import annotations

import ast
from pathlib import Path

import bcc

FORBIDDEN = {"messages", "prompt", "system_prompt", "api_key", "api_key_enc", "cookie", "cookies",
             "token", "password", "secret", "authorization"}
EMITTERS = {"emit", "_log"}


def _emitter_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in EMITTERS:
            yield node


def test_no_private_fields_are_emitted_as_event_payload():
    root = Path(bcc.__file__).parent
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _emitter_calls(tree):
            bad = sorted(k.arg for k in call.keywords if k.arg in FORBIDDEN)
            if bad:
                offenders.append(f"{path.relative_to(root)}:{call.lineno} {call.func.attr}({', '.join(bad)}=…)")
    assert offenders == [], "приватные поля в телеметрии:\n" + "\n".join(offenders)


def test_scanner_sees_real_emits():
    """Сканер не пустой: в пакете есть emit-вызовы (иначе тест ничего не проверяет)."""
    root = Path(bcc.__file__).parent
    count = sum(1 for p in root.rglob("*.py") for _ in _emitter_calls(ast.parse(p.read_text(encoding="utf-8"))))
    assert count > 100
