"""Проверки на цепочку установленного продукта.

Каждой функции вердикта подсовывается ПАРА: законный случай, который обязан
пройти, и по-настоящему плохой случай, который обязан быть отвергнут. Проверка,
у которой нет второго, ничего не проверяет — она подтверждает, что код
выполняется, и красит это в зелёный.

Отдельно проверяется устройство самого задания: своя группа concurrency,
фильтр веток и то, что ни один шаг не ставит продукт в editable-режиме.
"""
from __future__ import annotations

import importlib.util
import re
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "installed-product.yml"


def _module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


chain = _module("installed_product_chain", "tools/installed_product_chain.py")


SHA = "a" * 40
GOOD_PREFIX = "/opt/bossman/venv"
GOOD_MODULES = {
    "bcc": f"{GOOD_PREFIX}/lib/python3.12/site-packages/bcc/__init__.py",
    "bossman": f"{GOOD_PREFIX}/lib/python3.12/site-packages/bossman/__init__.py",
    "bossman_shared": f"{GOOD_PREFIX}/lib/python3.12/site-packages/bossman_shared/__init__.py",
}
GOOD_DIRECT = {name: {"url": "file:///build/wheel", "archive_info": {}}
               for name in chain.PRODUCT_DISTRIBUTIONS}


def _editable(**over):
    kwargs = dict(module_paths=dict(GOOD_MODULES), prefix=GOOD_PREFIX,
                  direct_urls=dict(GOOD_DIRECT), site_entries=["bcc", "fastapi", "pip"],
                  sys_path=["", "/opt/bossman/venv/lib/python3.12/site-packages"])
    kwargs.update(over)
    return chain.editable_problems(**kwargs)


# --- граница поставка / рабочая копия ---------------------------------------

def test_a_real_installation_is_accepted():
    assert _editable() == []


def test_a_package_imported_from_outside_the_installation_is_refused():
    """Законный случай выше и этот — пара. Без него достаточно было бы, чтобы
    функция всегда возвращала пустой список."""
    problems = _editable(module_paths={**GOOD_MODULES,
                                       "bcc": "/home/owner/AiMaxBossman/command-center/bcc/__init__.py"})
    assert any("импортирован мимо установки" in p for p in problems), problems


def test_an_editable_distribution_is_refused_even_when_paths_look_installed():
    """Ровно тот случай, ради которого звено и существует: файлы лежат в
    site-packages (их туда положил путевой хук), а установка — editable."""
    direct = dict(GOOD_DIRECT)
    direct["bossman-command-center"] = {"url": "file:///home/owner/AiMaxBossman/command-center",
                                        "dir_info": {"editable": True}}
    problems = _editable(direct_urls=direct)
    assert any("editable" in p for p in problems), problems


def test_the_setuptools_editable_tail_in_site_packages_is_refused():
    problems = _editable(site_entries=["bcc", "__editable__.bossman_command_center-0.1.0.pth"])
    assert any("__editable__" in p for p in problems), problems


def test_a_checkout_on_the_import_path_is_refused(tmp_path):
    (tmp_path / "command-center" / "bcc").mkdir(parents=True)
    problems = _editable(sys_path=["", str(tmp_path)])
    assert any("рабочая копия" in p for p in problems), problems


def test_a_missing_package_is_named_rather_than_passed_over():
    problems = _editable(module_paths={**GOOD_MODULES, "bossman": None})
    assert any("не импортируется" in p for p in problems), problems


# --- привязка установки к коммиту -------------------------------------------

def test_a_build_json_that_names_the_expected_commit_is_accepted():
    assert chain.build_json_problems({"source_sha": SHA, "source_dirty": False}, SHA) == []


@pytest.mark.parametrize("build,expected,fragment", [
    ({"source_sha": None, "source_dirty": False}, None, "не называет свой коммит"),
    ({"source_sha": "zz", "source_dirty": False}, None, "не называет свой коммит"),
    ({"source_sha": SHA, "source_dirty": True}, None, "грязного дерева"),
    ({"source_sha": SHA, "source_dirty": None}, None, "грязного дерева"),
    ({"source_sha": SHA, "source_dirty": False}, "b" * 40, "улика требуется"),
])
def test_a_build_json_that_cannot_bind_the_evidence_is_refused(build, expected, fragment):
    problems = chain.build_json_problems(build, expected)
    assert any(fragment in p for p in problems), problems


# --- готовность интерфейса ---------------------------------------------------

def _ui(**over):
    kwargs = dict(login_visible=True, shell_visible=True,
                  build_sha_text=f"сборка {SHA[:12]}",
                  build_sha_title=f"{SHA}\nустановленная сборка",
                  expected_sha=SHA, controls=42,
                  min_controls=chain.SHIPPED_SHELL_CONTROLS,
                  view_text="Обзор\nМодели 0\nАгенты 1",
                  console_errors=[], failed_responses=[])
    kwargs.update(over)
    return chain.ui_problems(**kwargs)


def test_a_rendered_page_is_accepted():
    assert _ui() == []


def test_a_blank_page_that_still_answered_two_hundred_is_refused():
    """Двести на корень: сервер ответил, index.html отдан, app.js не отработал,
    и владелец смотрит в пустое окно. Именно это звено и заведено ловить."""
    problems = _ui(login_visible=False, shell_visible=False)
    assert any("#login" in p for p in problems) and any("#shell" in p for p in problems), problems


def test_a_page_served_from_a_working_checkout_is_refused():
    """Граница поставка/рабочая копия, видимая ГЛАЗАМИ владельца: подсказка
    #build-sha заполняется из /api/identity и прямо их различает."""
    problems = _ui(build_sha_title=f"{SHA}\nрабочий чекаут")
    assert any("рабочий чекаут" in p for p in problems), problems


def test_a_page_naming_another_commit_is_refused():
    problems = _ui(build_sha_title="b" * 40 + "\nустановленная сборка")
    assert any("чужой коммит" in p for p in problems), problems


def test_an_unknown_build_identity_is_refused():
    problems = _ui(build_sha_text="SOURCE_IDENTITY_UNKNOWN")
    assert any("не назвал работающую сборку" in p for p in problems), problems


def test_a_half_rendered_shell_is_refused():
    problems = _ui(controls=3)
    assert any("органов управления" in p for p in problems), problems


def test_an_empty_main_region_is_refused():
    """Оболочку показали, страницу не отрисовали. В поставляемой разметке
    `#view` пуст, поэтому непустой текст в нём — единственное доказательство,
    что приложение действительно что-то построило."""
    problems = _ui(view_text="   \n  ")
    assert any("#view пуста" in p for p in problems), problems


def test_the_control_floor_is_the_number_in_the_shipped_markup():
    """Порог не выдуман: он пересчитывается из того же index.html, который
    уедет владельцу. Правка разметки, убравшая органы управления, обязана
    менять это число ОСОЗНАННО, а не тихо."""
    source = (ROOT / "command-center" / "ui" / "index.html").read_text(encoding="utf-8")
    shell = source[source.index('id="shell"'):]
    counted = sum(len(re.findall(pattern, shell)) for pattern in (
        r"<button(?![^>]*\bdisabled\b)", r"<a\s[^>]*href=",
        r"<input(?![^>]*\bdisabled\b)", r"<select(?![^>]*\bdisabled\b)"))
    assert counted == chain.SHIPPED_SHELL_CONTROLS, (counted, chain.SHIPPED_SHELL_CONTROLS)


def test_the_shipped_main_region_really_is_empty():
    """Контроль предыдущей проверки: если бы `#view` приезжал с текстом,
    требование «в #view что-то есть» ничего бы не доказывало."""
    source = (ROOT / "command-center" / "ui" / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<main[^>]*id="view"[^>]*>\s*</main>', source), (
        "в поставляемой разметке #view перестал быть пустым — проверка отрисовки ослабла")


def test_console_errors_and_missing_resources_are_refused():
    problems = _ui(console_errors=["TypeError: x is not a function"],
                   failed_responses=["404 /pages/video_studio.css"])
    assert any("ошибка в консоли" in p for p in problems), problems
    assert any("не получила свой ресурс" in p for p in problems), problems


# --- возобновление -----------------------------------------------------------

def _resume(**over):
    kwargs = dict(version_before=4, version_after_restart=4,
                  body_before="<h1>3</h1>", body_after_restart="<h1>3</h1>",
                  version_continued=5, stale_write_status=409)
    kwargs.update(over)
    return chain.resume_problems(**kwargs)


def test_work_that_continued_after_a_hard_kill_is_accepted():
    assert _resume() == []


def test_work_that_started_over_is_refused():
    """Самый важный отрицательный случай: продукт поднялся, отвечает, и начал
    документ сначала. Счётчик с единицы выглядит как работающий продукт."""
    problems = _resume(version_after_restart=1, version_continued=2)
    assert any("не продолжилась" in p for p in problems), problems


def test_a_counter_that_moves_but_does_not_continue_is_refused():
    problems = _resume(version_continued=1)
    assert any("начал документ сначала" in p for p in problems), problems


def test_a_lost_last_edit_is_refused():
    problems = _resume(body_after_restart="<h1>2</h1>")
    assert any("тело документа отличается" in p for p in problems), problems


def test_a_version_counter_that_guards_nothing_is_refused():
    """Пара к «версия выросла»: если запись по УСТАРЕВШЕЙ версии принимается,
    рост счётчика не доказывает ничего."""
    problems = _resume(stale_write_status=200)
    assert any("ничего не сторожит" in p for p in problems), problems


def test_a_negative_control_that_was_never_run_is_refused():
    problems = _resume(stale_write_status=None)
    assert any("не выполнялся" in p for p in problems), problems


# --- вердикт цепочки ---------------------------------------------------------

def _link(name, status, owner_hardware=False):
    return {"звено": name, "status": status, "улика": "", "owner_hardware": owner_hardware}


def test_all_links_green_is_the_only_way_to_pass():
    assert chain.chain_verdict([_link("a", chain.PASS), _link("b", chain.PASS)]) == chain.PASS


def test_one_failed_link_fails_the_chain():
    assert chain.chain_verdict([_link("a", chain.PASS), _link("b", chain.FAIL)]) == chain.FAIL


def test_a_link_that_never_ran_is_a_failure_not_a_silence():
    """Незапущенная проверка строже не становится. Молчание неотличимо от
    «замечаний нет» в любом отчёте, который смотрит на красное и зелёное."""
    assert chain.chain_verdict([_link("a", chain.PASS), _link("b", chain.NOT_RUN)]) == chain.FAIL


def test_owner_hardware_does_not_block_what_the_runner_can_check():
    links = [_link("a", chain.PASS), _link("окно", chain.OWNER_HW, owner_hardware=True)]
    assert chain.chain_verdict(links) == chain.OWNER_HW


def test_owner_hardware_never_hides_a_real_failure():
    links = [_link("a", chain.FAIL), _link("окно", chain.OWNER_HW, owner_hardware=True)]
    assert chain.chain_verdict(links) == chain.FAIL


def test_exit_codes_separate_pass_failure_and_owner_hardware():
    assert chain.EXIT_CODES == {chain.PASS: 0, chain.FAIL: 1, chain.OWNER_HW: 2}


# --- устройство задания ------------------------------------------------------

def _workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # `on:` в YAML 1.1 разбирается как булево True — отсюда оба ключа.
    data["__on"] = data.get("on", data.get(True))
    return data


def test_the_job_has_its_own_concurrency_group():
    """Иначе новое задание выбивало бы из очереди сборку продукта: GitHub
    держит ровно один ОЖИДАЮЩИЙ прогон на группу."""
    group = _workflow()["concurrency"]["group"]
    assert group.startswith("installed-product-"), group
    for other in (ROOT / ".github" / "workflows").glob("*.yml"):
        if other == WORKFLOW:
            continue
        data = yaml.safe_load(other.read_text(encoding="utf-8")) or {}
        rival = (data.get("concurrency") or {}).get("group")
        assert rival != group, f"{other.name} занимает ту же группу"


def test_a_started_run_is_not_cancelled_by_the_next_commit():
    assert _workflow()["concurrency"]["cancel-in-progress"] is False


def _run_steps() -> list:
    """Только то, что задание ИСПОЛНЯЕТ. Комментарии разбираются YAML'ом и в
    список не попадают: сторож, ловящий собственное объяснение, бесполезен."""
    steps = []
    for job in _workflow()["jobs"].values():
        for step in job["steps"]:
            if isinstance(step.get("run"), str):
                steps.append(step["run"])
    return steps


def test_the_scanner_sees_the_steps_it_is_supposed_to_guard():
    """Контроль самого сторожа: без него пустой список проходил бы всё."""
    steps = _run_steps()
    assert len(steps) >= 4, steps
    assert any("installed_product_install.py" in s for s in steps), steps
    assert any("installed_product_chain.py" in s for s in steps), steps


def test_no_step_installs_the_product_editable():
    """Весь смысл задания. `pip install -e` в любом исполняемом шаге означал
    бы, что проверяется рабочая копия, как бы ни назывались шаги."""
    for step in _run_steps():
        for forbidden in ("pip install -e", "pip install --editable", "install -e ."):
            assert forbidden not in step, (forbidden, step)


def test_the_chain_runs_with_the_installed_interpreter_and_isolation():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "INSTALLED_PRODUCT_PYTHON" in text
    assert "-I tools/installed_product_chain.py" in text, (
        "цепочку обязан исполнять интерпретатор установки с -I, иначе PYTHONPATH "
        "раннера может подсунуть ей чекаут")


def test_the_browser_is_required_on_the_runner():
    """Chromium на раннере есть. Значит его отсутствие — отказ, а не
    «оборудование владельца»: иначе звено про интерфейс молча отключается."""
    assert "--require-browser" in WORKFLOW.read_text(encoding="utf-8")


def test_the_job_is_declared_owner_facing_like_the_windows_pair():
    contract = json.loads((ROOT / "tools" / "owner_facing_branches.json").read_text(encoding="utf-8"))
    assert ".github/workflows/installed-product.yml" in contract["workflows"]
    # Требование покрытия веток сверяется отдельным тестом, тем же, что у пары
    # Windows. Здесь — только то, что задание вообще объявлено.
    assert ".github/workflows/installed-product.yml" not in contract["windows_pair"]


def test_every_executed_script_is_in_the_path_filter():
    paths = _workflow()["__on"]["push"]["paths"]
    for script in ("scripts/installed_product_install.py", "tools/installed_product_chain.py",
                   "tools/build_local_bundle.py", "tools/bundle_evening_test.py",
                   "scripts/bossman_doctor.py"):
        assert script in paths, f"{script} исполняется заданием, но его правка прогон не поднимет"


def test_the_product_itself_triggers_the_chain():
    """До этого задания единственный прогон с чистой установкой и жёстким
    убийством (windows-owner-tasks.yml) поднимался ТОЛЬКО правкой своего
    драйвера: правка самого продукта его не запускала вообще."""
    paths = _workflow()["__on"]["push"]["paths"]
    assert "command-center/**" in paths and "bossman-core/**" in paths
