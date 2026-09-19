"""Обязательное для СЕРТИФИКАТА задание обязано быть достижимо на владельческой ветке.

Класс BL-089, но на уровень выше. Тогда из фильтра выпала ветка; здесь
выпадает само ТРЕБОВАНИЕ. Два списка, которые обязаны сходиться, велись
порознь:

* `tools/exact_sha_certify.DEFAULT_REQUIRED` — что обязано быть зелёным на
  точном SHA, иначе сертификат не выдаётся;
* `tools/owner_facing_branches.json` → `workflows` — что обязано ПОДНИМАТЬСЯ
  на ветках, из которых выпускается продукт.

Первый список рос (оба Windows — BL-089, табло владельца — BL-101), второй
переписывался руками, и сойтись им было не на чем. Сертификат, которому
нужно задание, никогда на этой ветке не поднимающееся, не может быть выдан
НИКОГДА — и причину этого пришлось бы искать в двух разных файлах.

Существующий сторож `test_default_required_names_match_workflow_files`
доказывает, что у каждого требуемого имени ЕСТЬ файл. Что задание может
запуститься — он не доказывает, и именно эта щель и сработала.

Проверка ведётся тем же сопоставителем, которым пользуется сторож веток
(`tools/workflow_filters`): второй экземпляр семантики фильтров разъехался бы
с первым молча.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from workflow_filters import (path_is_in_filter, push_trigger,  # noqa: E402
                              reachable_on_push)

import exact_sha_certify as esc  # noqa: E402

CONTRACT = json.loads((ROOT / "tools" / "owner_facing_branches.json").read_text(encoding="utf-8"))
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def _by_name() -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for path in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("name"):
            found.setdefault(str(data["name"]), []).append(path)
    return found


BY_NAME = _by_name()


def workflow_file(name: str) -> Path:
    files = BY_NAME.get(name, [])
    assert len(files) == 1, (
        f"требуемое задание {name!r} отображается в {len(files)} файлов: {files}. "
        "Совпадение в сертификаторе идёт по ОТОБРАЖАЕМОМУ имени, поэтому ни ноль, "
        "ни два файла не дают однозначного ответа «этот прогон был»."
    )
    return files[0]


def spec(name: str) -> dict:
    return yaml.safe_load(workflow_file(name).read_text(encoding="utf-8"))


# --- собственно контракт ------------------------------------------------------

@pytest.mark.parametrize("name", esc.DEFAULT_REQUIRED)
@pytest.mark.parametrize("branch", CONTRACT["must_be_covered"])
def test_every_required_workflow_can_run_on_every_owner_branch(name, branch):
    assert reachable_on_push(spec(name), branch), (
        f"задание {name!r} ({workflow_file(name).name}) не поднимается пушем в "
        f"{branch}, но без его успешного прогона на точном SHA сертификат не "
        f"выдаётся. Значит, сертификат на этой ветке не может быть выдан НИКОГДА, "
        f"и выглядит это не как отказ, а как отсутствие прогона."
    )


def _branch_restricted(name: str) -> bool:
    """Ограничено ли задание списком веток, а не поднимается на любой.

    Различие существенное. `root-ci` и обе CI пакетов слушают `**` — им
    объявление владельческих веток не нужно и не подходит: список в
    `owner_facing_branches.json` требует ещё и ОТКАЗЫВАТЬ посторонним веткам,
    а задание на `**` не отказывает никому и не должно.
    """
    from workflow_filters import push_trigger  # noqa: PLC0415

    push = push_trigger(spec(name))
    if push is None:
        return False
    return any(
        not reachable_on_push(spec(name), branch)
        for branch in CONTRACT["must_not_be_covered"]
    )


def test_every_branch_restricted_required_workflow_is_declared():
    """Ограниченное ветками требуемое задание обязано стоять в объявлении.

    Иначе покрытие держалось бы на одном этом файле: `owner_facing_branches.json`
    остался бы неполным, и следующая правка фильтров прошла бы мимо задания,
    которого в нём нет. Именно так и случилось с `bossman-v2-repair.yml` —
    обязательное для сертификата, ограниченное одной чужой веткой и не
    объявленное нигде.
    """
    declared = {str(p) for p in CONTRACT["workflows"]}
    missing = sorted(
        workflow_file(name).relative_to(ROOT).as_posix()
        for name in esc.DEFAULT_REQUIRED
        if _branch_restricted(name)
        and workflow_file(name).relative_to(ROOT).as_posix() not in declared
    )
    assert not missing, (
        "обязательные для сертификата задания ограничены ветками, но не "
        f"объявлены владельческими: {missing}"
    )


def test_the_declaration_rule_does_not_demand_the_impossible():
    """Пара к проверке выше: задание на `**` ограниченным НЕ считается.

    Без неё правило чинилось бы внесением `root-ci` в список, а тот обязан
    отказывать посторонним веткам — и отказать не может, потому что слушает
    все. Проверка выше стала бы невыполнимой, а невыполнимую проверку снимают.
    """
    unrestricted = [name for name in esc.DEFAULT_REQUIRED if not _branch_restricted(name)]
    assert "root-ci (shared contracts, learning layer, tools)" in unrestricted
    restricted = [name for name in esc.DEFAULT_REQUIRED if _branch_restricted(name)]
    assert "One-download Windows application" in restricted


# --- пара: проверка обязана УМЕТЬ отказывать ---------------------------------
# Без неё «достижимо» чинилось бы возвратом True, и все проверки выше стали бы
# пустыми. Случай взят настоящий: ровно такой фильтр стоял у
# bossman-v2-repair.yml, и прогонов на release/bossman-owner было ноль.

@pytest.mark.parametrize("push,branch,expected", [
    ({"branches": ["claude/bossman-control-v03-43igbk"]}, "release/bossman-owner", False),
    ({"branches": ["claude/**", "night/**", "release/**"]}, "release/bossman-owner", True),
    ({"branches": ["release/*"]}, "release/bossman-owner", True),
    ({"branches": ["release/*"]}, "release/owner/x", False),
    ({}, "любая/ветка", True),                                   # фильтра нет — поднимается везде
    ({"branches-ignore": ["release/**"]}, "release/bossman-owner", False),
    ({"branches": ["**"], "paths": ["tools/x.py"]}, "release/bossman-owner", True),  # пути — другой вопрос
])
def test_the_reachability_check_can_say_no(push, branch, expected):
    assert reachable_on_push({"on": {"push": push}}, branch) is expected


def test_a_workflow_that_ignores_push_is_not_reachable():
    """Достижимость меряется ПУШЕМ, и оба исключения — по своей причине.

    `workflow_dispatch`: он требует человека. Сертификат выдаётся по прогонам
    на точном SHA; ручной запуск такой прогон даёт, но только если кто-то его
    нажал, а порядок выпуска, который держится на «не забыть нажать», ничем не
    отличается от отсутствующей проверки.

    `pull_request`: прогон по этому событию сертификат ЗАСЧИТАЛ БЫ — в
    `head_sha` он несёт вершину ветки, а не коммит слияния (проверено на
    настоящем ответе API, прогон 35436272757). Исключён он не поэтому, а
    потому, что существует лишь пока открыт PR с подходящей базой. Свойство
    «обязательное задание поднимается» не должно зависеть от того, завёл ли
    кто-то PR.
    """
    assert reachable_on_push({"on": {"workflow_dispatch": None}}, "release/bossman-owner") is False
    assert reachable_on_push({"on": ["pull_request"]}, "release/bossman-owner") is False
    assert reachable_on_push({"on": ["push"]}, "release/bossman-owner") is True


# --- BL-107: достижимость по ПУТЯМ ------------------------------------------
# Вопрос «эта ли ветка» и вопрос «этот ли коммит» — разные, и второй так же
# решает судьбу сертификата. Три обязательных задания подняты фильтром путей и
# намеренно не поднимаются на каждый коммит: владельческий прогон Windows идёт
# сорок минут. Значит кандидат, диff которого не задел ровно тех файлов,
# прогона не получает вовсе — MISSING, не окрашенный никак.

MARKER = CONTRACT["release_candidate_marker"]


def push_paths(name: str) -> list[str]:
    """Фильтр путей задания. Разбор один и тот же на все проверки.

    Короткая форма (`on: [push]`) и `push:` без тела дают пустой блок: событие
    слушается, фильтров нет. Своего разбора здесь заводить нельзя — он
    разъехался бы с тем, которым считается достижимость по ветке.
    """
    push = push_trigger(spec(name))
    return list((push or {}).get("paths") or [])


@pytest.mark.parametrize("name", esc.DEFAULT_REQUIRED)
def test_a_path_filtered_required_workflow_is_raised_by_declaring_a_candidate(name):
    patterns = push_paths(name)
    if not patterns:
        # Фильтра путей нет — задание поднимается на ЛЮБОЙ коммит, и поднимать
        # его объявлением незачем. Это законный проход, а не пропуск: ровно
        # такие задания и не нуждаются в объявлении.
        return
    assert path_is_in_filter(patterns, MARKER), (
        f"{name!r} обязательно для сертификата и поднимается фильтром путей, но "
        f"объявление кандидата ({MARKER}) его НЕ поднимает. Значит кандидат, чей "
        f"диff не задел путей этого задания, прогона не получит вовсе, а "
        f"отсутствующий прогон читается как «замечаний нет». Ручной "
        f"workflow_dispatch порядком выпуска не считается: он держится на «не "
        f"забыть нажать»."
    )


def test_the_candidate_marker_exists_and_says_what_it_is():
    """Иначе правило чинилось бы строкой в фильтре и отсутствующим файлом.

    Пути, которого нет, не тронет ни один коммит: фильтр выглядел бы покрытым,
    а объявить кандидата было бы нечем.
    """
    marker = ROOT / MARKER
    assert marker.is_file(), f"объявление кандидата {MARKER} не существует"
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data.get("candidate_label"), "у объявления нет метки кандидата"
    assert "sha" not in data, (
        "SHA в объявлении быть не может: коммит с этим файлом ещё не существует "
        "в момент его правки. Кандидатом становится SHA, который родится из пуша."
    )


def test_the_marker_check_can_say_no():
    """Пара: без неё `path_is_in_filter` могла бы вернуть True на всё.

    Третий случай — настоящая ловушка порядка: отрицание, поставленное ПОСЛЕ
    маркера, гасит его, и фильтр, в котором маркер видно глазами, его не
    поднимает.
    """
    assert path_is_in_filter(["tools/**"], MARKER) is True
    assert path_is_in_filter(["scripts/windows_owner_tasks.py"], MARKER) is False
    assert path_is_in_filter([MARKER, "!tools/**"], MARKER) is False
