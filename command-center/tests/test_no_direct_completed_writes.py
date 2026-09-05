"""EH-04 (TRUTH-003 §10): структурный AST-тест — `tasks.status = "completed"` пишет только
bcc/lifecycle.py. Любой новый прямой UPDATE tasks … status="completed" вне allowlist — провал."""
from __future__ import annotations

import ast
from pathlib import Path

import bcc

ALLOWLIST = {"finalize.py"}          # единственная каноническая точка финализации
TASK_TABLE_NAMES = {"tasks", "tasks_t"}


def _receiver_chain(node: ast.AST) -> list[ast.AST]:
    out = []
    while isinstance(node, (ast.Call, ast.Attribute)):
        out.append(node)
        node = node.func if isinstance(node, ast.Call) else node.value
    out.append(node)
    return out


def _updates_tasks(call: ast.Call) -> bool:
    for n in _receiver_chain(call):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "update" and n.args:
            a = n.args[0]
            name = a.id if isinstance(a, ast.Name) else (a.attr if isinstance(a, ast.Attribute) else "")
            if name in TASK_TABLE_NAMES:
                return True
    return False


def find_direct_completed_writes(root: Path) -> list[str]:
    offenders = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "values"):
                continue
            if not any(kw.arg == "status" and isinstance(kw.value, ast.Constant) and kw.value.value == "completed"
                       for kw in node.keywords):
                continue
            if _updates_tasks(node) and path.name not in ALLOWLIST:
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        # прямой вызов движка с литералом "completed" тоже финализация в обход канона
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_finish" \
                    and len(node.args) >= 3 and isinstance(node.args[2], ast.Constant) and node.args[2].value == "completed" \
                    and path.name not in ALLOWLIST:
                offenders.append(f"{path.relative_to(root)}:{node.lineno} (_finish completed)")
    return offenders


def test_only_lifecycle_writes_task_completed():
    root = Path(bcc.__file__).parent
    assert find_direct_completed_writes(root) == []


def test_detector_catches_a_direct_write(tmp_path):
    (tmp_path / "bad.py").write_text(
        "import sqlalchemy as sa\nfrom bcc.db import tasks as tasks_t\n"
        "async def f(s, tid):\n    await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(status='completed'))\n",
        encoding="utf-8")
    (tmp_path / "ok.py").write_text("x = {'status': 'completed'}\nrows = [r for r in [] if r['status'] == 'completed']\n", encoding="utf-8")
    found = find_direct_completed_writes(tmp_path)
    assert found == ["bad.py:4"]
