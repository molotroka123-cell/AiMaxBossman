"""Отказ на старте обязан дойти до владельца словами, а не закрытым окном.

Я добавил `DatabaseFromNewerBuild`: прежняя сборка отказывается открывать базу,
записанную более новой. Отказ правильный — и ровно поэтому он опасен. Если он
вылетит трейсбеком из запуска, владелец, кликнувший ярлык, увидит мелькнувшее
и закрывшееся окно, то есть «Bossman не запускается» без единого слова о том,
что произошло и что делать.

Здесь проверяется путь целиком: настоящая база, настоящий `Services.start()`,
настоящий поток сервера. Пара к каждому утверждению — в тестах ниже: та же
сборка с совместимой базой обязана подняться, иначе «не поднялся» не отличить
от постоянного красного.
"""
from __future__ import annotations

import io
import socket
from pathlib import Path

import sqlalchemy as sa

from bcc import desktop
from bcc.db import SCHEMA_GENERATION


def _free_port() -> int:
    """Свой порт, а не заимствованный из браузерного модуля.

    Этот набор идёт и в job `windows paths`, где браузерные extras не
    установлены: импорт хелпера оттуда уронил бы СБОР, а не тест.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _data_dir(monkeypatch, path: Path) -> Path:
    from bcc.config import settings

    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", path)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path / 'bcc.db'}")
    return path


def _stamp(db_file: Path, generation: int) -> None:
    """Готовит базу так, как её оставила бы более новая сборка."""
    engine = sa.create_engine(f"sqlite:///{db_file}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE IF NOT EXISTS probe (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text(f"PRAGMA user_version = {int(generation)}"))
    finally:
        engine.dispose()


def test_a_database_from_a_newer_build_stops_the_server_with_a_named_reason(
        tmp_path, monkeypatch):
    data = _data_dir(monkeypatch, tmp_path / "data")
    _stamp(data / "bcc.db", SCHEMA_GENERATION + 1)

    port = _free_port()
    server = desktop._BackgroundServer("127.0.0.1", port)
    try:
        assert server.start(f"http://127.0.0.1:{port}", timeout=30.0) is False, \
            "сервер не имел права подняться на базе более новой сборки"
    finally:
        server.stop()

    reason = server.error or ""
    assert "DatabaseFromNewerBuild" in reason, reason
    assert str(SCHEMA_GENERATION + 1) in reason and str(SCHEMA_GENERATION) in reason, reason
    assert "резерв" in reason.lower(), "отказ обязан сказать, что делать: " + reason


def test_the_same_build_starts_on_its_own_database(tmp_path, monkeypatch):
    """Пара: без неё «не поднялся» не отличить от сломанного окружения."""
    data = _data_dir(monkeypatch, tmp_path / "data")
    _stamp(data / "bcc.db", SCHEMA_GENERATION)

    port = _free_port()
    server = desktop._BackgroundServer("127.0.0.1", port)
    try:
        assert server.start(f"http://127.0.0.1:{port}", timeout=150.0) is True, server.error
    finally:
        server.stop()


def test_the_owner_reads_the_reason_and_the_launcher_exits_three(tmp_path, monkeypatch):
    """Сквозь `run()`: причина печатается, код выхода 3, окно не молчит."""
    data = _data_dir(monkeypatch, tmp_path / "data")
    _stamp(data / "bcc.db", SCHEMA_GENERATION + 1)
    monkeypatch.setattr(desktop, "find_browser", lambda *a, **k: "/bin/true")

    out = io.StringIO()
    code = desktop.run(["--profile", str(tmp_path / "profile"), "--port", str(_free_port())],
                       launcher=lambda *a, **k: 0, out=out)
    printed = out.getvalue()
    assert code == 3, printed
    assert "сервер не поднялся" in printed, printed
    assert "DatabaseFromNewerBuild" in printed, printed
    assert "резерв" in printed.lower(), "владелец обязан прочитать, что делать: " + printed


def test_the_reason_is_also_written_to_the_log_the_owner_forwards(tmp_path, monkeypatch):
    """Окно можно закрыть раньше, чем прочитать. Журнал остаётся."""
    data = _data_dir(monkeypatch, tmp_path / "data")
    _stamp(data / "bcc.db", SCHEMA_GENERATION + 1)
    monkeypatch.setattr(desktop, "find_browser", lambda *a, **k: "/bin/true")

    desktop.run(["--profile", str(tmp_path / "profile"), "--port", str(_free_port())],
                launcher=lambda *a, **k: 0, out=io.StringIO())
    log = desktop._run_log_path(data)
    assert log.is_file(), log
    body = log.read_text(encoding="utf-8", errors="replace")
    assert "server-start-failed" in body, body
    assert "DatabaseFromNewerBuild" in body, body


def test_any_startup_failure_reaches_the_owner_by_name_not_as_exit_three(
        tmp_path, monkeypatch):
    """Правка общая, а не подогнана под мой отказ совместимости.

    Дефект был шире: ЛЮБОЙ сбой запуска приходил владельцу как
    «SystemExit: 3» — одно число для испорченной базы, нехватки прав и отказа
    фичи. Здесь причина другого рода: база — не база, а мусор.
    """
    data = _data_dir(monkeypatch, tmp_path / "data")
    (data / "bcc.db").write_bytes("это не SQLite, а просто байты".encode() * 64)

    port = _free_port()
    server = desktop._BackgroundServer("127.0.0.1", port)
    try:
        assert server.start(f"http://127.0.0.1:{port}", timeout=30.0) is False
    finally:
        server.stop()

    reason = server.error or ""
    assert reason and "SystemExit" not in reason, \
        "владелец снова получил код выхода вместо причины: " + reason
    assert "file is not a database" in reason.lower() or "database" in reason.lower(), reason


def test_the_recorder_never_turns_a_bare_exit_into_a_false_cause():
    """Негативный контроль: без записанной причины подставлять нечего."""
    server = object.__new__(desktop._BackgroundServer)
    server._cause = desktop._StartupCause()
    assert server._cause.cause is None
    assert desktop._BackgroundServer._explain(server, SystemExit(3)) == "SystemExit: 3"

    server._cause.cause = "RuntimeError: порт занят"
    assert desktop._BackgroundServer._explain(server, SystemExit(3)) == "RuntimeError: порт занят"
    # Настоящее исключение не подменяется записанным — оно точнее.
    assert desktop._BackgroundServer._explain(server, OSError("address in use")) == \
        "OSError: address in use"
