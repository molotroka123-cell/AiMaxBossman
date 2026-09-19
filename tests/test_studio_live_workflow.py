"""Живая приёмка Studio включается только настройками владельца.

Раннер `tools/studio_live_owner.py` без `--execute` всегда пишет
OWNER_REQUIRED — то есть рабочего пути живой проверки в прогоне не было
вовсе. Здесь проверяется, что путь появился и что он НЕ включается сам:
`--execute` добавляется только внутри условия по секретам владельца, а сам
раннер отвергает любую ненулевую цену (`validate_free_policy`), поэтому
автоматической траты денег нет по построению.
"""
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'windows-bundle.yml'
GUARD = 'if ($env:BOSSMAN_STUDIO_POLICY -and $env:BOSSMAN_STUDIO_MODELS)'


def studio_step() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    steps = [step for job in document['jobs'].values() for step in job.get('steps', [])]
    named = [s for s in steps if str(s.get('name', '')).startswith('Studio installed catalog')]
    assert len(named) == 1, f'ожидался ровно один шаг живой приёмки Studio, найдено {len(named)}'
    return named[0]


def test_the_owner_can_switch_the_live_studio_path_on_with_settings():
    step = studio_step()
    assert 'BOSSMAN_STUDIO_POLICY' in step['env'] and 'BOSSMAN_STUDIO_MODELS' in step['env'], \
        'шаг не читает настройки владельца, включать живой путь нечем'
    lines = step['run'].splitlines()
    guard = [i for i, line in enumerate(lines) if GUARD in line]
    appends = [i for i, line in enumerate(lines) if "'--execute'" in line]
    assert len(guard) == 1 and len(appends) == 1, (guard, appends)
    assert guard[0] < appends[0], 'аргумент --execute добавляется вне условия по настройкам владельца'
    closing = [i for i, line in enumerate(lines) if line.strip().startswith('} else {')]
    assert closing and appends[0] < closing[0], 'добавление --execute вышло за пределы условия'
    assert any('--policy' in line and '--execute' in line for line in lines), \
        'политика владельца обязана идти вместе с --execute'


def test_the_run_never_passes_execute_unconditionally():
    """Отрицательный контроль: деньги не тратятся сами.

    Вызов раннера обязан идти через собранный список аргументов, а не нести
    `--execute` в самой строке запуска, иначе живая генерация пошла бы в
    каждом прогоне, независимо от настроек владельца.
    """
    step = studio_step()
    # Вызов — это строка, запускающая ВСТРОЕННЫЙ интерпретатор архива; текст
    # предупреждения тоже упоминает раннер и --execute, но ничего не исполняет.
    invocations = [line for line in step['run'].splitlines()
                   if 'studio_live_owner.py' in line and 'BCC_ACCEPTANCE_PYTHON' in line]
    assert len(invocations) == 1, f'ожидался ровно один запуск раннера, найдено {len(invocations)}'
    for line in invocations:
        assert '--execute' not in line, f'безусловный --execute в строке запуска: {line.strip()}'
        assert '@live' in line, 'аргументы обязаны собираться условно, а не подставляться в строке'


def test_owner_required_stays_non_fatal_and_named():
    """OWNER_REQUIRED — это не отказ прогона и не тихий пропуск."""
    run = studio_step()['run']
    assert '$code -eq 2' in run and 'exit 0' in run, 'код 2 обязан оставаться нефатальным'
    assert 'STUDIO=OWNER_REQUIRED' in run, 'причина обязана называться в журнале прогона'
    assert 'STUDIO_LIVE_PATH=' in run, 'выбранный путь обязан быть виден в журнале'
