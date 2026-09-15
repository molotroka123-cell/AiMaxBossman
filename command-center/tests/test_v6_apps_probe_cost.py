"""V6 §C: опрос карточек приложений не имеет права замораживать цикл событий.

Измерено на HEAD до правки: `httpx.AsyncClient()` на КАЖДУЮ карточку строит
SSL-контекст (~22 мс синхронного CPU), девять карточек — ~200 мс, на которые
замирали все запросы дашборда. Правила после правки, проверяемые здесь
детерминированно (без таймеров): один HTTP-клиент на весь опрос; SSL-контекст
строится один раз на процесс; манифест перечитывается только когда файл
изменился.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from bcc.features import apps as apps_mod

MANIFEST = """
id: {id}
name: App {id}
default_port: {port}
ui:
  order: 1
"""


@pytest.fixture
def apps_root(tmp_path, monkeypatch):
    root = tmp_path / "apps"
    for i in range(4):
        d = root / f"app{i}"
        d.mkdir(parents=True)
        (d / "app.manifest.yaml").write_text(MANIFEST.format(id=f"app{i}", port=1 + i),
                                             encoding="utf-8")
    monkeypatch.setattr(apps_mod, "APPS_DIR", root)
    monkeypatch.setattr(apps_mod, "ROOT", tmp_path / "command-center")   # manifest_path относителен корню репо
    monkeypatch.setattr(apps_mod, "_cache", {"at": 0.0, "apps": []})
    monkeypatch.setattr(apps_mod, "_described", {})
    monkeypatch.setattr(apps_mod, "_ssl_context", None)
    return root


@pytest.mark.anyio
async def test_one_client_and_one_ssl_context_for_the_whole_probe(apps_root, monkeypatch):
    clients = 0
    contexts = 0
    real_client = httpx.AsyncClient
    real_ctx = httpx.create_ssl_context

    def counting_client(*a, **kw):
        nonlocal clients
        clients += 1
        return real_client(*a, **kw)

    def counting_ctx(*a, **kw):
        nonlocal contexts
        contexts += 1
        return real_ctx(*a, **kw)

    monkeypatch.setattr(apps_mod.httpx, "AsyncClient", counting_client)
    monkeypatch.setattr(apps_mod.httpx, "create_ssl_context", counting_ctx)

    apps = await apps_mod.collect(force=True)
    assert [a["id"] for a in apps] == ["app0", "app1", "app2", "app3"]
    assert all(a["status"] == "STOPPED" for a in apps)      # порты 1..4 никто не слушает
    assert clients == 1, f"клиентов на опрос: {clients} (ожидался один)"
    assert contexts == 1

    await apps_mod.collect(force=True)
    assert clients == 2 and contexts == 1, "SSL-контекст переиспользуется между опросами"


@pytest.mark.anyio
async def test_manifest_is_reparsed_only_when_the_file_changes(apps_root, monkeypatch):
    parsed = 0
    real_load = apps_mod._load

    def counting_load(path):
        nonlocal parsed
        parsed += 1
        return real_load(path)

    monkeypatch.setattr(apps_mod, "_load", counting_load)
    await apps_mod.collect(force=True)
    assert parsed == 4
    await apps_mod.collect(force=True)
    assert parsed == 4, "файлы не менялись — разбирать заново нечего"

    target = apps_root / "app2" / "app.manifest.yaml"
    text = target.read_text(encoding="utf-8").replace("App app2", "Renamed")
    target.write_text(text, encoding="utf-8")
    import os
    os.utime(target, ns=(os.stat(target).st_mtime_ns + 10**9, os.stat(target).st_mtime_ns + 10**9))
    apps = await apps_mod.collect(force=True)
    assert parsed == 5
    assert next(a for a in apps if a["id"] == "app2")["name"] == "Renamed"


@pytest.mark.anyio
async def test_the_manifest_rule_holds_for_every_caller_not_just_the_dashboard(apps_root, monkeypatch):
    """Инвариант «перечитывать только изменившееся» был реализован для ОДНОГО
    вызывающего и проверялся ТОЛЬКО через него.

    Тест выше (`test_manifest_is_reparsed_only_when_the_file_changes`) ходит
    через `collect()`, а тот идёт через `_describe_cached`/`_described`. Правило
    там и правда соблюдалось. Но `task_exchange.known_apps()` зовёт `_load()`
    НАПРЯМУЮ, мимо этого кэша, — и нарушал объявленный инвариант 9 раз в
    секунду на простаивающем сервере (измерено: 9 манифестов, обход раз в
    секунду, ни одного изменения файла). Профиль показывал 876 815 вызовов
    `yaml.reader.peek` за 33 секунды в продукте, которым никто не пользовался.

    Поэтому правило теперь живёт в `_load`, а проверяется ЧЕРЕЗ НАРУШИТЕЛЯ и по
    настоящим РАЗБОРАМ (`yaml.safe_load`), а не по вызовам `_load`: после правки
    `_load` зовётся столько же раз, сколько и раньше, — он просто перестал
    разбирать. Считать вызовы значило бы измерять не то, что починено.
    """
    parses = 0
    real_safe_load = apps_mod.yaml.safe_load

    def counting(*a, **kw):
        nonlocal parses
        parses += 1
        return real_safe_load(*a, **kw)

    monkeypatch.setattr(apps_mod.yaml, "safe_load", counting)
    from bcc.features import task_exchange as tx
    monkeypatch.setattr(tx, "APPS_DIR", apps_root)

    assert sorted(tx.known_apps()) == ["app0", "app1", "app2", "app3"]
    assert parses == 4, "первый обход обязан прочитать все манифесты"

    for _ in range(10):
        tx.known_apps()
    assert parses == 4, (
        f"файлы не менялись, а разборов уже {parses}: холостой разбор вернулся")

    # Обратный контроль: строгость не имеет права проглотить НАСТОЯЩЕЕ изменение.
    target = apps_root / "app1" / "app.manifest.yaml"
    target.write_text(target.read_text(encoding="utf-8").replace("App app1", "Другое"),
                      encoding="utf-8")
    bump = os.stat(target).st_mtime_ns + 10**9
    os.utime(target, ns=(bump, bump))
    tx.known_apps()
    assert parses == 5, "изменённый манифест обязан быть перечитан СЛЕДУЮЩИМ же обходом"


@pytest.mark.anyio
async def test_the_cache_never_hands_out_an_object_someone_else_can_edit(apps_root):
    """Кэш отдаёт КОПИЮ. Иначе один вызывающий, правящий свой словарь, менял бы
    манифест для всех остальных — и это было бы хуже лишнего разбора."""
    first = apps_mod._load(apps_root / "app0" / "app.manifest.yaml")
    first["name"] = "испорчено"
    second = apps_mod._load(apps_root / "app0" / "app.manifest.yaml")
    assert second["name"] == "App app0", second


@pytest.mark.anyio
async def test_a_removed_app_leaves_nothing_behind_in_the_cache(apps_root):
    """Словарь живёт текущим набором приложений, а не всем, что когда-либо
    видел: иначе смена каталога (в тестах — временного APPS_DIR) растила бы его
    без предела."""
    apps_mod._manifest_files()
    for path in list(apps_mod._MANIFEST_CACHE):
        assert path.parent.parent == apps_root, path
    import shutil
    shutil.rmtree(apps_root / "app3")
    apps_mod._manifest_files()
    assert not any(p.parent.name == "app3" for p in apps_mod._MANIFEST_CACHE)


@pytest.mark.anyio
async def test_probe_without_a_client_still_works(apps_root):
    described = [apps_mod._describe_cached(p) for p in apps_mod._manifest_files()]
    live = await apps_mod._probe(described[0])
    assert live["status"] == "STOPPED" and "не отвечает" in live["detail"]


@pytest.mark.anyio
async def test_concurrent_callers_share_one_probe_and_errors_reach_everyone(apps_root, monkeypatch):
    """§5 single-flight: N одновременных collect() — один опрос; ошибка — всем;
    следующий вызов после ошибки идёт заново."""
    probes = 0
    real = apps_mod._probe_client

    def counting():
        nonlocal probes
        probes += 1
        return real()

    monkeypatch.setattr(apps_mod, "_probe_client", counting)
    results = await asyncio.gather(*(apps_mod.collect(force=True) for _ in range(8)))
    assert probes == 1
    assert all(r is results[0] for r in results)

    def broken():
        nonlocal probes
        probes += 1
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(apps_mod, "_probe_client", broken)
    outcomes = await asyncio.gather(*(apps_mod.collect(force=True) for _ in range(4)),
                                    return_exceptions=True)
    assert probes == 2
    assert all(isinstance(o, RuntimeError) for o in outcomes), outcomes

    monkeypatch.setattr(apps_mod, "_probe_client", counting)
    fresh = await apps_mod.collect(force=True)
    assert probes == 3 and [a["id"] for a in fresh] == ["app0", "app1", "app2", "app3"]


@pytest.mark.anyio
async def test_a_cancelled_waiter_does_not_kill_the_shared_probe(apps_root):
    first = asyncio.ensure_future(apps_mod.collect(force=True))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(apps_mod.collect(force=True))
    await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    apps = await first
    assert [a["id"] for a in apps] == ["app0", "app1", "app2", "app3"]
