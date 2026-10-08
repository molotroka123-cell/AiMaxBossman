"""Командная строка: цена каталога и опроса задач.

Три дефекта, каждый воспроизведён до правки:

* `catalog_for` ради ключа кэша обходил ВСЕ ~650 конечных маршрутов (с
  раскрытием вложенных роутеров) на каждом /command-bar, /parse и /run —
  даже когда каталог уже был готов. Ключ теперь дешёвый, а инвалидация
  проверяется отдельно: новый маршрут обязан попасть в каталог;

* до прихода каталога (~220 КБ) строка заметки была пустой — владелец не
  видел, что панель вообще что-то делает;

* `setInterval(refreshTasks, 2000)` продолжал дёргать /api/command-bar/tasks
  во вкладке, которую никто не видит.

Поведение панели проверяется исполнением НАСТОЯЩЕГО ui/commandbar.js в node
на минимальном DOM; статические проверки ниже — страховка на случай, когда
node на машине нет.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from starlette.applications import Starlette
from starlette.routing import Route

from bcc.features import command_bar as cb

from .test_command_bar import _flag_on  # noqa: F401 — флаг командной строки включён

UI = Path(__file__).resolve().parents[1] / "ui"
JS = (UI / "commandbar.js").read_text(encoding="utf-8")


# ------------------------------------------------------------------ каталог


def _pairs(catalog: dict) -> set[tuple[str, str]]:
    return {(c.path, c.method) for c in catalog.values()}


async def _noop():
    return {}


def _app() -> tuple[FastAPI, APIRouter, APIRouter]:
    """Приложение, устроенное как настоящее: фичи — вложенные роутеры под /api."""
    app = FastAPI()
    feature = APIRouter()
    feature.add_api_route("/alpha/items", _noop, methods=["GET"], name="list_items")
    inner = APIRouter()
    inner.add_api_route("/gamma/deep", _noop, methods=["GET"], name="read_deep")
    outer = APIRouter()
    outer.include_router(inner)
    app.include_router(feature, prefix="/api")
    app.include_router(outer, prefix="/api")
    return app, feature, inner


async def test_a_cached_catalog_does_not_walk_every_route_on_each_request(env, monkeypatch):
    """Попадание в кэш не обходит маршруты: раньше каждый запрос строки платил
    полным обходом ~650 маршрутов только ради сравнения их числа."""
    cached = cb.catalog_for(env.app)                 # первый вызов строит каталог
    visits: list[object] = []
    real = cb._walk_routes

    def counting(routes):
        for route in real(routes):
            visits.append(route)
            yield route

    monkeypatch.setattr(cb, "_walk_routes", counting)
    for _ in range(3):                               # /command-bar, /parse, /run
        assert cb.catalog_for(env.app) is cached
    assert visits == [], f"попадание в кэш обошло маршруты: {len(visits)} посещений"


def test_adding_a_route_to_the_app_refreshes_the_catalog():
    app, _feature, _inner = _app()
    first = cb.catalog_for(app)
    assert ("/api/alpha/items", "GET") in _pairs(first)
    assert cb.catalog_for(app) is first, "без изменений каталог берётся из кэша"

    app.add_api_route("/api/beta/run", _noop, methods=["POST"], name="start_beta")
    assert ("/api/beta/run", "POST") in _pairs(cb.catalog_for(app))


def test_adding_a_route_to_an_already_included_router_refreshes_the_catalog():
    """Фича-роутер подключён раньше, маршрут в него добавлен позже: FastAPI его
    обслуживает — значит, и каталог обязан его увидеть (верхний список
    app.routes при этом не меняется ни по длине, ни по составу)."""
    app, feature, inner = _app()
    first = cb.catalog_for(app)
    top_level = list(app.routes)

    feature.add_api_route("/alpha/items/{item_id}", _noop, methods=["DELETE"], name="drop_item")
    inner.add_api_route("/gamma/deep/more", _noop, methods=["POST"], name="dig_more")
    assert list(app.routes) == top_level, "сценарий именно про вложенный роутер"
    served = {(r.path, m) for r in cb._walk_routes(app.routes) for m in r.methods}
    assert ("/api/alpha/items/{item_id}", "DELETE") in served   # FastAPI это обслуживает

    second = cb.catalog_for(app)
    assert second is not first
    assert {("/api/alpha/items/{item_id}", "DELETE"),
            ("/api/gamma/deep/more", "POST")} <= _pairs(second)


def test_removing_a_route_refreshes_the_catalog():
    app, _feature, _inner = _app()
    assert ("/api/gamma/deep", "GET") in _pairs(cb.catalog_for(app))
    app.router.routes.pop()                          # подключённый последним outer/inner
    assert ("/api/gamma/deep", "GET") not in _pairs(cb.catalog_for(app))


def test_without_a_routes_version_the_catalog_still_follows_the_routes():
    """Негативный контроль запасного пути: приложение без версии маршрутов
    (чистый Starlette) не получает вечный кэш — каталог по-прежнему следит
    за маршрутами полным обходом."""
    app = Starlette(routes=[Route("/api/plain/items", _noop, methods=["GET"])])
    assert not hasattr(app.router, "_get_routes_version")
    first = cb.catalog_for(app)
    assert ("/api/plain/items", "GET") in _pairs(first)
    assert cb.catalog_for(app) is first

    app.router.routes.append(Route("/api/plain/more", _noop, methods=["POST"]))
    assert ("/api/plain/more", "POST") in _pairs(cb.catalog_for(app))


# ------------------------------------------------------------------ панель: статика


def test_note_has_a_loading_text_before_the_catalog_arrives():
    created = re.search(r"h\('div\.bx-cmd-note', \{ id: 'cmdbar-note' \},\s*'([^']+)'\)", JS)
    assert created, "строка заметки создаётся пустой: до прихода каталога панель молчит"
    assert "Загруж" in created.group(1)


def test_task_polling_listens_to_visibility_and_forgets_it_on_destroy():
    added = re.findall(r"document\.addEventListener\('visibilitychange', (\w+)\)", JS)
    removed = re.findall(r"document\.removeEventListener\('visibilitychange', (\w+)\)", JS)
    assert added, "опрос задач не знает, что вкладку скрыли"
    assert added == removed, "destroy() обязан снять обработчик видимости"
    assert "document.hidden" in JS


# ------------------------------------------------------------------ панель: исполнение в node

DRIVER = r"""
const observed = {};
const intervals = new Map();
let nextTimer = 1;
globalThis.setInterval = (fn) => { const id = nextTimer++; intervals.set(id, fn); return id; };
globalThis.clearInterval = (id) => { intervals.delete(id); };
const flush = () => new Promise((resolve) => setImmediate(resolve));
const tick = async (n) => { for (let i = 0; i < n; i += 1) { [...intervals.values()].forEach((fn) => fn()); await flush(); } };

class Text {
  constructor(data) { this.data = String(data); this.nodeType = 3; this.parentNode = null; }
  get textContent() { return this.data; }
}
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase(); this.childNodes = []; this.parentNode = null;
    this.attrs = {}; this.listeners = {}; this.style = { setProperty() {} }; this.dataset = {};
    this.hidden = false; this.nodeType = 1;
    const cls = new Set(); this.classList = { add: (c) => cls.add(c), contains: (c) => cls.has(c) };
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  removeChild(child) { this.childNodes = this.childNodes.filter((c) => c !== child); child.parentNode = null; return child; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get firstChild() { return this.childNodes[0] || null; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === root; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  set textContent(v) { this.childNodes = []; this.appendChild(new Text(v)); }
  find(id) {
    if (this.attrs.id === id) return this;
    for (const c of this.childNodes) { const hit = c instanceof El && c.find(id); if (hit) return hit; }
    return null;
  }
  querySelector(sel) { return this.find(String(sel).replace(/^#/, '')); }
}
const root = new El('html');
const doc = {
  hidden: false, listeners: {},
  head: root.appendChild(new El('head')), body: root.appendChild(new El('body')),
  createElement: (t) => new El(t), createElementNS: (_ns, t) => new El(t),
  createTextNode: (t) => new Text(t),
  getElementById: (id) => root.find(id),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); },
  setHidden(value) { this.hidden = value; (this.listeners.visibilitychange || []).slice().forEach((fn) => fn()); },
};
globalThis.document = doc;

const calls = [];
let catalogGate = null;
const fakeApi = {
  raw: (path) => {
    calls.push(path);
    if (path === '/api/command-bar') return new Promise((resolve, reject) => { catalogGate = { resolve, reject }; });
    return Promise.resolve({ tasks: [] });
  },
};
const taskCalls = () => calls.filter((p) => p === '/api/command-bar/tasks').length;
const note = (bar) => bar.pane.find('cmdbar-note').textContent;

const { mountCommandBar } = await import(process.env.MODULE_URL);

// 1) каталог ещё в пути — заметка уже говорит, что идёт загрузка
let bar = mountCommandBar({ api: fakeApi, poll: 2000 });
observed.loading = note(bar);
catalogGate.resolve({ enabled: true, capabilities: [{ id: 'tasks.list' }], aliases: {} });
await flush();
observed.loaded = note(bar);
observed.tasks_after_load = taskCalls();

// 2) видимая вкладка опрашивается по таймеру
await tick(3);
observed.visible_polls = taskCalls() - observed.tasks_after_load;

// 3) вкладку скрыли — ни таймера, ни запросов
doc.setHidden(true);
observed.timers_while_hidden = intervals.size;
let before = taskCalls();
await tick(5);
observed.hidden_polls = taskCalls() - before;

// 4) вкладку вернули — сразу свежий список и ровно один таймер
before = taskCalls();
doc.setHidden(false);
await flush();
observed.resume_immediate = taskCalls() - before;
doc.setHidden(false);              // повторное событие не плодит таймеры
observed.timers_after_resume = intervals.size;
before = taskCalls();
await tick(2);
observed.polls_after_resume = taskCalls() - before;

// 5) destroy снимает и таймер, и обработчик видимости
bar.destroy();
observed.timers_after_destroy = intervals.size;
before = taskCalls();
doc.setHidden(true); doc.setHidden(false);
await flush();
observed.calls_after_destroy = taskCalls() - before;
observed.timers_after_destroy_and_show = intervals.size;

// 6) панель, смонтированная в скрытой вкладке, не опрашивает до показа
doc.hidden = true;
bar = mountCommandBar({ api: fakeApi, poll: 2000 });
catalogGate.resolve({ enabled: true, capabilities: [], aliases: {} });
await flush();
observed.timers_mounted_hidden = intervals.size;
doc.setHidden(false);
observed.timers_mounted_hidden_then_shown = intervals.size;
bar.destroy();

// 7) каталог не пришёл — честная ошибка вместо вечной «загрузки»
doc.hidden = false;
bar = mountCommandBar({ api: fakeApi, poll: 2000 });
catalogGate.reject(new Error('Сервер недоступен'));
await flush();
observed.failed = note(bar);
bar.destroy();

process.stdout.write(JSON.stringify(observed));
"""


def _node() -> str:
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: ui/commandbar.js loading/visibility behaviour was not executed")
    return node


@pytest.fixture(scope="module")
def observed(tmp_path_factory) -> dict:
    driver = tmp_path_factory.mktemp("cmdbar") / "drive.mjs"
    driver.write_text(DRIVER, encoding="utf-8")
    run = subprocess.run([_node(), "--experimental-detect-module", "--no-warnings", str(driver)],
                         capture_output=True, text=True, timeout=60, check=False,
                         env=dict(os.environ, MODULE_URL=(UI / "commandbar.js").as_uri()))
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def test_note_says_loading_until_the_catalog_arrives(observed):
    assert "Загруж" in observed["loading"], observed
    assert observed["loaded"].startswith("Возможностей: 1"), observed
    assert "Загруж" not in observed["loaded"]


def test_failed_catalog_replaces_loading_with_an_honest_error(observed):
    assert "недоступна" in observed["failed"] and "Сервер недоступен" in observed["failed"], observed
    assert "Загруж" not in observed["failed"]


def test_hidden_tab_does_not_poll_and_resumes_with_an_immediate_refresh(observed):
    assert observed["visible_polls"] == 3, observed       # видимая вкладка опрашивается
    assert observed["timers_while_hidden"] == 0, observed
    assert observed["hidden_polls"] == 0, observed
    assert observed["resume_immediate"] == 1, observed
    assert observed["timers_after_resume"] == 1, observed
    assert observed["polls_after_resume"] == 2, observed


def test_destroy_stops_polling_for_good(observed):
    assert observed["timers_after_destroy"] == 0, observed
    assert observed["calls_after_destroy"] == 0, observed
    assert observed["timers_after_destroy_and_show"] == 0, observed


def test_bar_mounted_in_a_hidden_tab_waits_for_it_to_be_shown(observed):
    assert observed["timers_mounted_hidden"] == 0, observed
    assert observed["timers_mounted_hidden_then_shown"] == 1, observed
