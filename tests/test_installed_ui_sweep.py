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
    driver_spec.loader.exec_module(module)
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
