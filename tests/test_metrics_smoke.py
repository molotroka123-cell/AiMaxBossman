"""Проверка метрик обязана ловить испорченное ОТОБРАЖЕНИЕ, а не только числа.

Прогон 132 показал цену раздельной проверки: определитель железа считал всё
верно, а вывести не смог — упал на кириллице (BL-087). Дефект жил в показе, а
не в вычислении, и ни один тест его не видел, потому что никто не запускал
раннер так, как его запускает приёмка.

Здесь проверяется сам проверяющий. Каждая его способность подтверждена парой:
испорченный случай отвергается, здоровый проходит. Проверка, которую нельзя
провалить, ничего не проверяет.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _smoke():
    spec = importlib.util.spec_from_file_location(
        "metrics_smoke", REPO / "tools" / "metrics_smoke.py")
    module = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = before
    return module


def _fake(tmp_path: Path, body: str, name: str = "fake_metric.py") -> dict:
    """Метрика-подделка: тот же контракт, управляемое поведение."""
    runner = tmp_path / name
    runner.write_text(body, encoding="utf-8")
    return {
        "name": "подделка",
        "repo_path": runner,
        "archive_name": name,
        "args": ["--json", "{json}"],
        "exit_codes": (0,),
        "verdict_lines": ("FAKE_VERDICT=",),
        "json_keys": ("value",),
        "numbers": (("value",),),
    }


HEALTHY = '''import sys, json, argparse
p = argparse.ArgumentParser(); p.add_argument("--json"); a = p.parse_args()
for s in (sys.stdout, sys.stderr):
    try: s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
open(a.json, "w", encoding="utf-8").write(json.dumps({"value": 12.5}))
print("Процессор опознан")
print("FAKE_VERDICT=OK")
'''


def test_a_healthy_metric_passes(tmp_path):
    smoke = _smoke()
    row = smoke.check(_fake(tmp_path, HEALTHY), sys.executable, None, tmp_path)
    assert row["verdict"] == "PASS", row["problems"]


def test_mojibake_on_screen_is_caught_even_though_the_numbers_are_right(tmp_path):
    """Тот самый случай: значения верные, а на экране владельца каша."""
    broken = HEALTHY.replace(
        'print("Процессор опознан")',
        'sys.stdout.buffer.write("Процессор опознан".encode("utf-8").decode("utf-8")'
        '.encode("utf-8").decode("latin-1").encode("utf-8") + b"\\n")')
    smoke = _smoke()
    row = smoke.check(_fake(tmp_path, broken), sys.executable, None, tmp_path)
    assert row["verdict"] == "FAIL", row
    assert any("кодировк" in p for p in row["problems"]), row["problems"]


def test_a_crash_on_a_locale_console_is_caught(tmp_path):
    """Ровно отказ прогона 132: печать падает, вердикта нет."""
    crashing = '''import sys, json, argparse, io
p = argparse.ArgumentParser(); p.add_argument("--json"); a = p.parse_args()
open(a.json, "w", encoding="utf-8").write(json.dumps({"value": 1}))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="cp1252", newline="")
print("Процессор опознан")
print("FAKE_VERDICT=OK")
'''
    smoke = _smoke()
    row = smoke.check(_fake(tmp_path, crashing), sys.executable, None, tmp_path)
    assert row["verdict"] == "FAIL", row
    assert any("трейсбеком" in p or "код выхода" in p for p in row["problems"]), row["problems"]


def test_a_missing_verdict_line_is_caught(tmp_path):
    """Число без строки вердикта владельцу нечем прочесть."""
    silent = HEALTHY.replace('print("FAKE_VERDICT=OK")', 'pass')
    smoke = _smoke()
    row = smoke.check(_fake(tmp_path, silent), sys.executable, None, tmp_path)
    assert row["verdict"] == "FAIL"
    assert any("строки вердикта" in p for p in row["problems"]), row["problems"]


@pytest.mark.parametrize("bad", ["-1", 'float("nan")', '"двенадцать"'])
def test_a_nonsense_number_is_caught(tmp_path, bad):
    """Отрицательное, NaN и строка вместо числа — не метрика."""
    body = HEALTHY.replace('{"value": 12.5}', '{"value": %s}' % bad) \
        .replace('json.dumps({"value": %s})' % bad,
                 'json.dumps({"value": %s})' % bad)
    if bad == '"двенадцать"':
        body = HEALTHY.replace('{"value": 12.5}', '{"value": "двенадцать"}')
    elif bad == 'float("nan")':
        body = HEALTHY.replace('json.dumps({"value": 12.5})',
                               'json.dumps({"value": float("nan")})')
    else:
        body = HEALTHY.replace('{"value": 12.5}', '{"value": -1}')
    smoke = _smoke()
    row = smoke.check(_fake(tmp_path, body), sys.executable, None, tmp_path)
    assert row["verdict"] == "FAIL", row
    assert any("не годное число" in p for p in row["problems"]), row["problems"]


def test_a_metric_that_writes_no_json_is_caught(tmp_path):
    smoke = _smoke()
    body = HEALTHY.replace(
        'open(a.json, "w", encoding="utf-8").write(json.dumps({"value": 12.5}))', 'pass')
    row = smoke.check(_fake(tmp_path, body), sys.executable, None, tmp_path)
    assert row["verdict"] == "FAIL"
    assert any("JSON не записан" in p for p in row["problems"]), row["problems"]


def test_an_unexpected_exit_code_is_caught(tmp_path):
    """Коды объявлены заранее; «любой ненулевой» — не контракт."""
    smoke = _smoke()
    body = HEALTHY + "sys.exit(7)\n"
    row = smoke.check(_fake(tmp_path, body), sys.executable, None, tmp_path)
    assert row["verdict"] == "FAIL"
    assert any("код выхода 7" in p for p in row["problems"]), row["problems"]


def test_a_missing_runner_is_named_not_silently_skipped(tmp_path):
    smoke = _smoke()
    metric = _fake(tmp_path, HEALTHY)
    metric["repo_path"] = tmp_path / "нет-такого.py"
    row = smoke.check(metric, sys.executable, None, tmp_path)
    assert row["verdict"] == "FAIL"
    assert "раннер не найден" in row["problems"]


def test_every_declared_metric_runner_exists_in_the_checkout():
    smoke = _smoke()
    for metric in smoke.METRICS:
        assert metric["repo_path"].is_file(), metric["name"]
        assert metric["exit_codes"], f"{metric['name']}: коды выхода не объявлены"


def test_the_archive_layout_is_where_the_bundle_puts_the_runners():
    """Если раннер объявлен, он обязан ехать в поставку под тем же именем."""
    smoke = _smoke()
    sys.path.insert(0, str(REPO / "tools"))
    import build_windows_bundle as bundle
    shipped = {name for _, name in bundle.SUPPORT_SCRIPTS}
    for metric in smoke.METRICS:
        assert metric["archive_name"] in shipped, (
            f"{metric['name']}: {metric['archive_name']} не едет в архив — "
            "проверка на установленном продукте будет искать его зря")
