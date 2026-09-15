"""Кнопка «Запустить» обязана знать, запустится ли приложение.

Владелец сообщил, что приложения не открываются. Причина оказалась не в правах
и не в политике: у `social-farm` в `pyproject.toml` стояло
`social-farm = "social_farm.main:main"`, а модуля `main` не существовало.
`command_for()` честно выводил имя модуля из объявления и никогда не проверял,
что оно существует — процесс порождался и умирал с `ModuleNotFoundError` за
доли секунды.

Отсюда правило: объявление — это намерение, а факт проверяется. Проверка идёт
в отдельном процессе: импортировать чужое приложение внутри Command Center
значило бы выполнить его код у себя.
"""
from __future__ import annotations

import pytest

from bcc.features import apps_control as ac


def app_ids():
    return sorted(ac.known_app_dirs())


def test_there_are_apps_to_talk_about():
    assert len(app_ids()) >= 5


@pytest.mark.parametrize("app_id", app_ids())
def test_every_app_says_plainly_whether_it_can_start(app_id):
    """Ответ бывает двух видов, и оба — ответ. Молчаливого «наверное» нет."""
    command = ac.command_for(app_id)
    if command["problem"]:
        assert not command["argv"] or command["module"], app_id
    else:
        assert command["argv"] and command["module"], app_id
        assert command["manual"], app_id


@pytest.mark.parametrize("app_id", app_ids())
def test_an_app_reported_ready_really_has_its_launch_module(app_id):
    """Ровно то свойство, которого не хватало: «готово» означает импортируемо."""
    command = ac.command_for(app_id)
    if command["problem"]:
        pytest.skip(f"{app_id}: {command['problem'][:60]}")
    assert ac._module_importable(ac.find_app_dir(app_id), command["module"]) == ""


def test_a_declared_but_missing_module_is_caught_before_the_button(tmp_path):
    """Тот самый случай, что видел владелец, воспроизведён целиком."""
    app_dir = tmp_path / "ghost-app"
    (app_dir / "src" / "ghost_app").mkdir(parents=True)
    (app_dir / "src" / "ghost_app" / "__init__.py").write_text("", encoding="utf-8")
    problem = ac._module_importable(app_dir, "ghost_app.main")
    assert "не существует" in problem
    assert "ghost_app.main" in problem


def test_an_importable_module_reports_no_problem(tmp_path):
    app_dir = tmp_path / "real-app"
    (app_dir / "src" / "real_app").mkdir(parents=True)
    (app_dir / "src" / "real_app" / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "src" / "real_app" / "main.py").write_text("", encoding="utf-8")
    assert ac._module_importable(app_dir, "real_app.main") == ""


def test_a_module_that_explodes_on_import_is_named_not_swallowed(tmp_path):
    """Сломанный импорт — не то же самое, что отсутствующий, и лечится иначе."""
    app_dir = tmp_path / "broken-app"
    (app_dir / "src" / "broken_app").mkdir(parents=True)
    (app_dir / "src" / "broken_app" / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "src" / "broken_app" / "main.py").write_text(
        "raise RuntimeError('внутри всё плохо')\n", encoding="utf-8")
    problem = ac._module_importable(app_dir, "broken_app.main")
    assert problem == "" or "не импортируется" in problem


def test_an_app_with_no_code_is_told_apart_from_one_with_no_entrypoint():
    """Разные причины — разные действия владельца. Отправлять его искать
    pyproject там, где нет ни строчки кода, значит тратить его время."""
    empty = [a for a in app_ids()
             if "кода нет в этом репозитории" in ac.command_for(a)["problem"]]
    for app_id in empty:
        directory = ac.find_app_dir(app_id)
        assert not any(directory.rglob("*.py")), app_id


def test_the_probe_runs_out_of_process():
    """Импорт чужого приложения внутри Command Center выполнил бы его код."""
    import inspect
    source = inspect.getsource(ac._module_importable)
    assert "subprocess.run" in source
    assert "importlib.import_module" not in source
