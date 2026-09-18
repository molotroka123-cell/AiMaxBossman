"""The disposable UI sweep must not leak managed application processes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

spec = importlib.util.spec_from_file_location('installed_ui_sweep',
    Path(__file__).resolve().parents[1] / 'tools' / 'installed_ui_sweep.py')
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


ROOT = Path(__file__).resolve().parents[1]



def test_a_page_with_several_screens_is_swept_on_every_declared_route():
    """Экран, который страница не объявила, обход не увидит — и это измерено.

    18.09: `#/images` давал 11 нажатий, `#/images` вместе с `#/images?studio=1`
    — 22 на тех же страницах. Одиннадцать органов управления Studio не
    проверялись гейтом вовсе, потому что обход ходил только по идентификаторам.
    """
    registry = """
      lazyPage({ id: 'images', title: 'Студия', icon: 'models', nav: 'primary', section: 'studio',
                 sweep: ['images', 'images?studio=1'] },
        () => import('./images.js'), (m) => m.default),
    """
    assert _driver().page_routes(registry) == ['images', 'images?studio=1']


def test_a_page_without_declared_routes_is_still_swept_by_its_id():
    """Отрицательный контроль: разбор не имеет права потерять обычную страницу.

    Без этой пары «улучшение» обхода могло бы тихо выкинуть из гейта каждую
    страницу, которая ничего про себя не объявила, — то есть почти все.
    """
    registry = """
      lazyPage({ id: 'oss', title: 'Локальные инструменты', icon: 'tools', nav: 'more', section: 'system' },
        () => import('./oss.js'), (m) => m.default),
      lazyPage({ id: 'video-studio', title: 'Video Studio', icon: 'video', nav: 'primary', section: 'studio' },
        () => import('./video_studio.js'), (m) => m.default),
    """
    assert _driver().page_routes(registry) == ['oss', 'video-studio']


def test_the_real_registry_still_sweeps_both_studio_screens():
    """Сторож против тихой потери режима Studio в самом реестре."""
    routes = _driver().page_routes((ROOT / 'command-center' / 'ui' / 'pages' / 'index.js').read_text(encoding='utf-8'))
    assert 'images' in routes and 'images?studio=1' in routes
    assert len(routes) == len(set(routes)), 'повторяющийся маршрут обхода'


def test_stop_owned_tree_releases_child_working_directory(tmp_path):
    working = tmp_path / 'managed app with spaces'
    working.mkdir()
    parent = subprocess.Popen([sys.executable, '-c',
        'import subprocess,sys,time; sys.stdin.readline(); '
        'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
        'print(p.pid,flush=True); time.sleep(60)'],
        cwd=working, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    if psutil.Process(parent.pid).cmdline()[1:] != parent.args[1:]:
        parent.terminate()
        parent.wait(timeout=5)
        if os.name == 'nt':
            pytest.fail('Native Windows runner must expose actual child process identity')
        pytest.skip('Managed execution remaps Popen PIDs; native process-tree test required')
    parent.stdin.write('start\n')
    parent.stdin.flush()
    child = psutil.Process(int(parent.stdout.readline()))
    # This unrelated process must remain untouched by the scoped cleanup.
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    def stop_parent(process):
        process.terminate()
        process.wait(timeout=5)
    try:
        sweep.stop_owned_process_tree(parent, stop_parent)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert unrelated.poll() is None
        working.rmdir()  # Windows rejects this while the child owns its cwd.
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
        unrelated.kill()
        unrelated.wait(timeout=5)


def _click(page, label, verdict, detail=''):
    """Настоящий Click драйвера, а не его имитация.

    Имитация здесь была бы худшим видом зелёного: write_report зовёт
    `asdict`, то есть требует именно dataclass, и подделка с теми же полями
    прошла бы ровно до того дня, когда у Click появится новое поле. Тогда CI
    остался бы зелёным, а установленный продукт упал бы на выгрузке отчёта.
    """
    drv = _driver()
    return drv.Click(page=page, label=label, selector='button', verdict=verdict, detail=detail)


def _driver():
    import sys
    name = '_sweep_driver_under_test'
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'ui_acceptance_sweep.py'
    driver_spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(driver_spec)
    sys.modules[name] = module
    # Драйвер — самостоятельный скрипт: при импорте он кладёт command-center в
    # sys.path ради своих отложенных импортов bcc. Здесь это чужой побочный
    # эффект, и он не безобиден: в command-center/tests есть __init__.py,
    # поэтому обычный пакет перекрывает корневой `tests` (пространство имён), а
    # соседнее `from tests.test_learning_trace import _case` перестаёт
    # разрешаться. Путь возвращается как был; запущенный как скрипт драйвер
    # ставит его себе сам.
    before = list(sys.path)
    try:
        driver_spec.loader.exec_module(module)
    finally:
        sys.path[:] = before
    return module


def _report(tmp_path, capsys, clicks):
    out = tmp_path / 'ui-sweep.json'
    sweep.write_report(out, 'a' * 40, {'source_sha': 'a' * 40}, ['images'], clicks)
    return json.loads(out.read_text(encoding='utf-8')), capsys.readouterr().out


def test_a_control_needing_review_is_named_in_the_log_not_only_in_the_artifact(tmp_path, capsys):
    """`"error": 4` без имён нечем починить.

    Подробности лежат в ui-sweep.json, а он выгружается артефактом. Из
    окружения агента хост артефактов запрещён политикой исходящего трафика
    (CONNECT ... 403), то есть единственный носитель имён недостижим ровно
    тогда, когда он нужен. Журнал задания доступен всегда — значит, имена
    обязаны быть и в нём.
    """
    report, printed = _report(tmp_path, capsys, [
        _click('images', 'Создать', 'error', 'TypeError: cannot read length of null'),
        _click('video', 'Экспорт', 'dead'),
        _click('images', 'Готово', 'works'),
    ])
    assert report['status'] == 'REVIEW_REQUIRED'
    assert 'Создать' in printed and 'TypeError' in printed
    assert 'Экспорт' in printed
    # Исправная кнопка в список разбора не попадает: иначе тридцать страниц
    # рабочих кнопок утопят четыре настоящие находки.
    assert 'Готово' not in printed


def test_a_clean_sweep_prints_no_review_block(tmp_path, capsys):
    """Негативный контроль. Без него проверка выше зеленела бы и от кода,
    который печатает КАЖДОЕ нажатие: «Создать» нашлось бы в любом случае."""
    report, printed = _report(tmp_path, capsys, [
        _click('images', 'Создать', 'works'),
        _click('video', 'Экспорт', 'opens_feature'),
    ])
    assert report['status'] == 'PASS'
    assert 'REVIEW' not in printed
    assert 'Создать' not in printed


def test_the_verdict_itself_is_untouched_by_the_new_output(tmp_path, capsys):
    """Печать — не гейт. Статус обязан по-прежнему считаться по четырём
    категориям разбора, а не по тому, что удалось напечатать."""
    for verdict in ('dead', 'error', 'disabled_silent', 'vanished'):
        report, _ = _report(tmp_path, capsys, [_click('images', 'Кнопка', verdict)])
        assert report['status'] == 'REVIEW_REQUIRED', verdict
    for verdict in ('works', 'opens_feature', 'disabled_reason', 'request_accepted'):
        report, _ = _report(tmp_path, capsys, [_click('images', 'Кнопка', verdict)])
        assert report['status'] == 'PASS', verdict


def test_the_sweep_driver_is_found_beside_the_shipped_copy_first(tmp_path):
    """OA-04: in the archive the driver ships next to installed_ui_sweep.py."""
    import importlib.util
    import shutil
    repo = Path(__file__).resolve().parents[1]
    assert sweep.sweep_driver() == repo / 'scripts' / 'ui_acceptance_sweep.py'
    support = tmp_path / 'app-support'
    support.mkdir()
    shutil.copyfile(repo / 'tools' / 'installed_ui_sweep.py', support / 'installed_ui_sweep.py')
    spec = importlib.util.spec_from_file_location('shipped_sweep', support / 'installed_ui_sweep.py')
    shipped = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shipped)
    assert shipped.sweep_driver() == tmp_path / 'scripts' / 'ui_acceptance_sweep.py', 'no driver beside: falls back'
    (support / 'ui_acceptance_sweep.py').write_text('# shipped driver\n', encoding='utf-8')
    assert shipped.sweep_driver() == support / 'ui_acceptance_sweep.py'


def test_loading_the_driver_does_not_repoint_the_top_level_tests_package():
    """Отрицательный контроль к загрузке драйвера.

    Загрузка скрипта обхода — не «просто импорт»: он кладёт command-center в
    sys.path, а там лежит command-center/tests с __init__.py. Обычный пакет
    перекрывает корневой `tests` (пространство имён), и соседнее
    `from tests.test_learning_trace import _case` перестаёт разрешаться. Когда
    это случалось на СБОРЕ, прерывался весь корневой набор — не один тест.
    Проверяется ровно тот импорт, который ломался, и ровно тот путь, который
    добавляет драйвер: состояние sys.path до и после его загрузки.
    """
    sys.modules.pop('_sweep_driver_under_test', None)
    before = list(sys.path)
    assert _driver().page_routes('') == []
    assert sys.path == before, 'загрузка драйвера изменила sys.path'
    assert importlib.util.find_spec('tests.test_learning_trace') is not None
