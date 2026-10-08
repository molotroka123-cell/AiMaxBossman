"""Skills page: the assign toast must not promise an effect the backend does not have.

POST /api/skills/{id}/assign only stores {skill_id: [agent_id]} under
``skills.assignments``; nothing in run_skill, the engine or the tool loop reads it.
In this product "назначен" means "reaches the model" (MCP Hub: only assigned tools
get into the model context), so the old toast "Навык назначен агенту" read as if the
agent would now apply the skill. Static check, no browser needed.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from bcc.features import skills as skills_feature

CC = Path(__file__).resolve().parents[1]
JS = (CC / "ui" / "pages" / "skills.js").read_text(encoding="utf-8")


def _assign_handler() -> str:
    start = JS.index("/assign`")
    return JS[start:JS.index("catch (e)", start)]


def test_assignment_is_display_only_in_the_backend():
    """The premise of the honest wording; if this fails, revisit the toast below."""
    readers = [p.relative_to(CC).as_posix() for p in (CC / "bcc").rglob("*.py")
               if skills_feature.ASSIGN_KEY in p.read_text(encoding="utf-8", errors="replace")]
    assert readers == ["bcc/features/skills.py"]
    assert "_assignments" not in inspect.getsource(skills_feature.run_skill)


def test_assign_toast_says_saved_not_applied():
    handler = _assign_handler()
    assert "toastOk(" in handler
    assert "Навык назначен агенту" not in handler
    assert "Назначение сохранено" in handler
    assert "не применяет навык сам" in handler
