"""Поставляемый раннер не имеет права падать на кириллице в своём же выводе.

Прогон 132 (`cc794b06`) на настоящей Windows:

    File ".../app-support/target_hardware_acceptance.py", line 395, in main
        print(text)
      File "encodings\\cp1252.py", line 19, in encode
    UnicodeEncodeError: 'charmap' codec can't encode characters in position 626-627

Механизм известен и уже чинился однажды для обхода интерфейса (`63c71b41`):
раннеры приёмки запускаются с `-I`, а `-I` подразумевает `-E`, то есть
`PYTHONUTF8` и `PYTHONIOENCODING` ИГНОРИРУЮТСЯ. На Windows поток получает
кодировку локали, и первая же русская строка роняет процесс.

Тогда правка была точечной — только в том файле, где отказ увидели. Здесь
проверяется весь КЛАСС: каждый раннер, который едет в архив, обязан перевести
свои потоки в UTF-8. Требование распространяется и на раннеры без русских
строк: путь, который они печатают, проходит через имя пользователя Windows, а
оно у владельца вполне может быть кириллическим.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import build_windows_bundle as bundle  # noqa: E402

RECONFIGURE = re.compile(r"reconfigure\(\s*encoding\s*=\s*[\"']utf-8[\"']", re.I)


def test_every_shipped_runner_puts_its_console_into_utf8():
    """Список берётся из САМОЙ поставки, а не переписан руками."""
    missing = [name for origin, name in bundle.SUPPORT_SCRIPTS
               if not RECONFIGURE.search(origin.read_text(encoding="utf-8"))]
    assert not missing, (
        "эти раннеры едут в архив и упадут на кириллице под `-I` на Windows: "
        + ", ".join(missing))


def test_every_shipped_runner_replaces_what_it_cannot_encode():
    """UTF-8 может быть недоступен; печать всё равно не имеет права падать."""
    weak = []
    for origin, name in bundle.SUPPORT_SCRIPTS:
        source = origin.read_text(encoding="utf-8")
        for call in re.findall(r"reconfigure\([^)]*\)", source):
            if "utf-8" in call.lower() and "errors=" not in call:
                weak.append(f"{name}: {call.strip()}")
    assert not weak, ("перевод в UTF-8 без errors= оставляет тот же отказ "
                      "там, где UTF-8 недоступен: " + "; ".join(weak))


@pytest.mark.parametrize("name", sorted({n for _, n in bundle.SUPPORT_SCRIPTS}))
def test_the_helper_is_called_before_anything_is_printed(name):
    """Помощник, объявленный и не вызванный, ничего не чинит."""
    origin = next(o for o, n in bundle.SUPPORT_SCRIPTS if n == name)
    source = origin.read_text(encoding="utf-8")
    called = re.search(r"^\s*(utf8_console|_console_utf8)\(\)", source, re.M)
    inline = re.search(r"for\s+\w+\s+in\s*\(\s*sys\.stdout\s*,\s*sys\.stderr\s*\)",
                       source)
    assert called or inline, f"{name}: поток объявлен, но не переведён"


def test_a_cp1252_stream_really_breaks_without_the_helper():
    """Отрицательный контроль: без него отказ НАСТОЯЩИЙ, а не выдуманный.

    Без этой пары проверки выше — просто требование стиля.
    """
    raw = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
    with pytest.raises(UnicodeEncodeError):
        print("CPU не опознан как целевой", file=raw)

    fixed = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
    fixed.reconfigure(encoding="utf-8", errors="replace")
    print("CPU не опознан как целевой", file=fixed)   # не падает


def test_the_hardware_runner_prints_its_whole_report_on_a_locale_console():
    """Сквозная проверка на том файле, который и упал в прогоне 132.

    Запускается отдельным процессом с `-I` — ровно так его зовёт приёмка, — и
    с потоком, насильно переведённым в cp1252, то есть в условиях Windows.
    """
    driver = (
        "import io, sys, runpy\n"
        "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='cp1252',\n"
        "                              newline='')\n"
        "sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='cp1252',\n"
        "                              newline='')\n"
        "sys.argv = ['target_hardware_acceptance.py']\n"
        f"runpy.run_path({str(REPO / 'scripts' / 'target_hardware_acceptance.py')!r},\n"
        "               run_name='__main__')\n")
    result = subprocess.run([sys.executable, "-I", "-c", driver],
                            capture_output=True, timeout=300)
    combined = (result.stdout + result.stderr).decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in combined, combined[-2000:]
    assert "TARGET_HARDWARE_VERDICT=" in combined, combined[-2000:]
