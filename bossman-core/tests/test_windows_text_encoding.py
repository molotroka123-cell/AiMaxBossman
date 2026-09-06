"""W5: текстовый ввод/вывод обязан быть utf-8, а не «как решит локаль хоста».

На Windows Python 3.11 берёт кодировку по умолчанию из ANSI-локали: на русской
установке это cp1251 (консольная — cp866). Кодовая база полна русского текста, а
вывод subprocess'а — рамок и стрелок pytest'а. `'── 1 passed ── ✓ →'` не
кодируется ни в cp1251, ни в cp866: `write_text()` без `encoding=` там не
«пишет как-нибудь», а бросает UnicodeEncodeError — инструмент `run`/`tests`/
`analysis.run` вместо результата отдаёт исключение, а `bossman project plan`
читает utf-8-бриф как cp1251 и скармливает модели мохнатую кракозябру.

Linux воспроизводит это точно так же, если подменить кодировку по умолчанию:
`Path.open()` передаёт `encoding="locale"` в `io.open`, поэтому достаточно
перехватить `io.open`/`builtins.open` и подставить cp1251/cp866 вместо
незаданной кодировки. Реального Windows здесь нет — проверяется само свойство
«кодировка задана явно», которое от платформы не зависит.
"""
from __future__ import annotations

import builtins
import contextlib
import io
from pathlib import Path

import pytest

from bossman.config import settings
from bossman.toolkit import ToolContext, analysis, shell

# Русский текст + рамки/стрелки pytest'а: ни один символ рамки в cp1251/cp866 не
# существует, русские буквы существуют — вместе они ловят и падение, и mojibake.
SAMPLE = "── 1 passed ── ✓ →\nПроверка пройдена: 3 теста\nитог: всё хорошо\n"
ANSI_CODEPAGES = ["cp1251", "cp866"]


@contextlib.contextmanager
def ansi_default_encoding(monkeypatch, codec: str):
    """Симуляция Windows-локали: незаданная кодировка = codec, а не utf-8.

    Две подмены, потому что путей ровно два: `Path.write_text/read_text`
    спрашивают `io.text_encoding(None)`, голый `open()` разрешает кодировку
    внутри C. Явно заданная кодировка проходит насквозь — иначе тест доказывал
    бы не то. Заодно это обходит UTF-8 mode, который на нашем Linux-хосте (LC_ALL=C,
    PEP 540) включён и потому МАСКИРУЕТ дефект; у владельца на русской Windows он выключен.
    """
    real_open = io.open
    real_text_encoding = io.text_encoding

    def fake_text_encoding(encoding, stacklevel=2):
        return codec if encoding is None else real_text_encoding(encoding, stacklevel)

    def fake_open(file, mode="r", buffering=-1, encoding=None, errors=None,
                  newline=None, *args, **kwargs):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = codec
        return real_open(file, mode, buffering, encoding, errors, newline,
                         *args, **kwargs)

    monkeypatch.setattr(io, "text_encoding", fake_text_encoding)
    monkeypatch.setattr(io, "open", fake_open)
    monkeypatch.setattr(builtins, "open", fake_open)
    yield


def test_sample_is_unencodable_in_ansi_codepages():
    """Сначала убеждаемся, что симуляция бьёт по-настоящему (иначе тест пустой)."""
    for codec in ANSI_CODEPAGES:
        with pytest.raises(UnicodeEncodeError):
            SAMPLE.encode(codec)


@pytest.mark.parametrize("codec", ANSI_CODEPAGES)
def test_ansi_default_encoding_really_breaks_naked_write_text(tmp_path, monkeypatch, codec):
    """Контроль симулятора: без encoding= запись падает ровно так, как на Windows."""
    with ansi_default_encoding(monkeypatch, codec):
        with pytest.raises(UnicodeEncodeError):
            (tmp_path / "naked.txt").write_text(SAMPLE)


def _ctx(tmp_path) -> ToolContext:
    return ToolContext(agent="t", workdir=tmp_path)


@pytest.mark.parametrize("codec", ANSI_CODEPAGES)
async def test_shell_run_writes_log_under_ansi_locale(tmp_path, monkeypatch, codec):
    async def fake_exec(cmd, ctx, timeout=600):
        return 0, SAMPLE

    monkeypatch.setattr(shell, "_exec", fake_exec)
    with ansi_default_encoding(monkeypatch, codec):
        res = await shell.run({"cmd": "pytest -q"}, _ctx(tmp_path))
    assert "код выхода: 0" in res.content
    log = next((tmp_path / "assets" / "logs").glob("*.txt"))
    assert log.read_text(encoding="utf-8") == SAMPLE


@pytest.mark.parametrize("codec", ANSI_CODEPAGES)
async def test_shell_tests_writes_log_under_ansi_locale(tmp_path, monkeypatch, codec):
    async def fake_exec(cmd, ctx, timeout=900):
        return 1, SAMPLE + "FAILED tests/test_a.py::test_b\n"

    monkeypatch.setattr(shell, "_exec", fake_exec)
    with ansi_default_encoding(monkeypatch, codec):
        res = await shell.tests({}, _ctx(tmp_path))
    assert res.error
    log = next((tmp_path / "assets" / "logs").glob("tests-*.txt"))
    assert "Проверка пройдена" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("codec", ANSI_CODEPAGES)
async def test_analysis_run_writes_log_under_ansi_locale(tmp_path, monkeypatch, codec):
    async def fake_exec(cmd, ctx, timeout=60):
        return 0, SAMPLE

    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    monkeypatch.setattr(analysis, "_exec", fake_exec)
    with ansi_default_encoding(monkeypatch, codec):
        res = await analysis.run({"code": "print(1)"}, _ctx(tmp_path))
    assert "код выхода: 0" in res.content
    log = next((tmp_path / "assets" / "logs").glob("analysis-*.txt"))
    assert log.read_text(encoding="utf-8") == SAMPLE


@pytest.mark.parametrize("codec", ANSI_CODEPAGES)
async def test_planner_writes_brief_under_ansi_locale(tmp_path, monkeypatch, codec):
    from bossman.projects import planner

    brief = "Сделай ролик про кота\n── смета ── ✓\n"
    plan_yaml = "title: кот\nstages: []\nestimate: {cost: 0, hours: 0}\n"

    async def fake_chat(agent, messages, **kw):
        return {"content": plan_yaml}

    class _Plan:
        tasks: list = []
        estimate: dict = {}
        budget_limit = None

    async def fake_execute(*a, **kw):
        return None

    monkeypatch.setattr(planner, "project_dir", lambda slug: tmp_path / slug)
    monkeypatch.setattr(planner, "chat", fake_chat)
    monkeypatch.setattr(planner, "save_plan", lambda slug, text: None)
    monkeypatch.setattr(planner, "load_plan", lambda slug: _Plan())
    monkeypatch.setattr(planner, "journal_append", lambda slug, text: None)
    monkeypatch.setattr(planner.db, "execute", fake_execute)

    with ansi_default_encoding(monkeypatch, codec):
        await planner.plan_project("kot", brief)
    assert (tmp_path / "kot" / "brief.md").read_text(encoding="utf-8") == brief


@pytest.mark.parametrize("codec", ANSI_CODEPAGES)
async def test_runner_stage_summary_under_ansi_locale(tmp_path, monkeypatch, codec):
    from bossman.projects import runner

    async def fake_chat(agent, messages, **kw):
        return {"content": SAMPLE}

    (tmp_path / "notes").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "project_dir", lambda slug: tmp_path)
    monkeypatch.setattr(runner, "journal_tail", lambda slug, n: "журнал этапа")
    monkeypatch.setattr(runner, "chat", fake_chat)
    monkeypatch.setattr(runner, "journal_append", lambda slug, text: None)

    with ansi_default_encoding(monkeypatch, codec):
        await runner._stage_summary("kot", "s1")
    assert "Проверка пройдена" in (tmp_path / "notes" / "s1.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("codec", ANSI_CODEPAGES)
async def test_cli_reads_utf8_brief_under_ansi_locale(tmp_path, monkeypatch, codec):
    """cli.py:60 — бриф владельца в utf-8 не должен доехать до модели кракозяброй."""
    from argparse import Namespace

    from bossman import cli, db
    from bossman.projects import planner

    brief = "Сделай ролик про кота — 30 секунд, ✓\n"
    path = tmp_path / "brief.md"
    path.write_text(brief, encoding="utf-8")

    seen: dict[str, str] = {}

    async def fake_execute(*a, **kw):
        seen["sql_brief"] = a[2]

    async def fake_plan_project(slug, text, agent=None):
        seen["brief"] = text
        return {"tasks": 0}

    async def fake_close():
        return None

    monkeypatch.setattr(db, "execute", fake_execute)
    monkeypatch.setattr(db, "close", fake_close)
    monkeypatch.setattr(planner, "plan_project", fake_plan_project)

    args = Namespace(action="plan", slug="kot", brief=str(path))
    with ansi_default_encoding(monkeypatch, codec):
        await cli._project(args)
    assert seen["brief"] == brief
    assert seen["sql_brief"] == brief


def test_no_naked_text_io_in_windows_sensitive_modules():
    """Регрессионный сторож: ни один из шести исправленных вызовов не вернётся.

    Проверяем именно исходники — новый `write_text(x)` без `encoding=` пройдёт
    все функциональные тесты выше на Linux и сломается только у владельца.
    """
    from bossman import cli as cli_mod
    from bossman.projects import planner, runner

    modules = [shell, analysis, planner, runner, cli_mod]
    bad: list[str] = []
    for mod in modules:
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for lineno, line in enumerate(src.splitlines(), 1):
            code = line.split("#", 1)[0]
            for call in ("write_text(", "read_text("):
                if call in code and "encoding=" not in code:
                    # многострочный вызов: кодировка может быть на следующей строке
                    tail = "\n".join(src.splitlines()[lineno - 1:lineno + 2])
                    if "encoding=" not in tail:
                        bad.append(f"{mod.__name__}:{lineno}")
    assert not bad, f"текстовый ввод/вывод без encoding=: {bad}"
