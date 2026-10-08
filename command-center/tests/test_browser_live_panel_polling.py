"""Browser live panel: the 3 s poll never stacks requests and sleeps in a hidden tab.

The server screenshot is a Playwright ``page.screenshot`` (default timeout 30 s; a page
in the middle of a 60 s ``goto`` can hold it that long). The modal fired a new
screenshot AND a new state request every 3 s regardless of the previous ones, so a slow
page stacked ~10 + 10 requests behind the stuck ones (the browser allows only 6
connections per host — the rest of the dashboard queued behind them too), and kept
doing it while the tab was in the background.

The node test runs the REAL ``ui/pages/browser.js`` (copied next to stub modules for
its three imports) with a fake ``fetch``/``api.raw`` that hang until released and a
captured ``setInterval`` callback the test fires by hand.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

UI = Path(__file__).resolve().parents[1] / "ui"
JS = (UI / "pages" / "browser.js").read_text(encoding="utf-8")

_API_STUB = """
export const hooks = { raw: null };
export const api = { raw: (...a) => hooks.raw(...a) };
export function listOf(data, key) { return (data && data[key]) || []; }
"""

_COMPONENTS_STUB = """
function el(tag, props, children) {
  return { tag, props: props || {}, children: children || [], style: {}, textContent: '', value: '', src: '',
           appendChild(c) { this.children.push(c); return c; } };
}
function isProps(a) { return a && typeof a === 'object' && !Array.isArray(a) && !('tag' in a); }
export function h(tag, attrs, ...children) {
  if (!isProps(attrs)) { children.unshift(attrs); attrs = {}; }
  return el(tag, attrs, children.flat().filter((c) => c !== null && c !== undefined));
}
export const icon = (name) => el('icon', { name });
export const statusBadge = (s) => el('badge', { s });
export const actionButton = (label, fn) => el('button', { label, onClick: fn });
export const input = (props) => el('input', props);
export const fmtDateShort = () => '';
export const toast = () => {};
export const toastOk = () => {};
export const toastError = () => {};
export const modals = [];
export function openModal({ onClose } = {}) {
  const m = { body: el('div'), footer: el('div'), closed: false,
              close() { if (!m.closed) { m.closed = true; if (onClose) onClose(); } } };
  modals.push(m);
  return m;
}
"""

_UI_STUB = """
export const pageHead = (title, sub, { actions = [] } = {}) => ({ tag: 'head', props: {}, children: actions });
export const errorNote = () => ({ tag: 'error', props: {}, children: [] });
export const blank = () => ({ tag: 'blank', props: {}, children: [] });
"""

_DRIVER = """
import BrowserPage from './ui/pages/browser.js';
import { hooks } from './ui/api.js';

const n = { shots: 0, states: 0, cleared: 0 };
const shots = [];
const states = [];
let tick = null;
globalThis.document = { hidden: false };
globalThis.fetch = () => { n.shots++; return new Promise((resolve, reject) => shots.push({ resolve, reject })); };
URL.createObjectURL = () => 'blob:x';
URL.revokeObjectURL = () => {};
globalThis.setInterval = (fn, ms) => { tick = fn; n.interval = ms; return 1; };
globalThis.clearInterval = () => { n.cleared++; };
hooks.raw = async (url) => {
  if (url === '/api/browser/sessions') return { sessions: [{ id: 7, live: true, status: 'running' }] };
  if (url === '/api/browser/health') return { available: true };
  if (url.endsWith('/state')) { n.states++; return new Promise((resolve, reject) => states.push({ resolve, reject })); }
  throw new Error('unexpected ' + url);
};
const flush = () => new Promise((r) => setTimeout(r, 0));
const snap = () => ({ shots: n.shots, states: n.states });
const okShot = { ok: true, blob: async () => ({}) };
function releaseAll({ shotErr = false, stateErr = null } = {}) {
  for (const p of shots.splice(0)) (shotErr ? p.reject(new Error('net')) : p.resolve(okShot));
  for (const p of states.splice(0)) (stateErr ? p.reject(stateErr) : p.resolve({ url: 'https://e.x', paused: false }));
}
function find(node) {
  if (!node || typeof node !== 'object') return null;
  if (typeof node.tag === 'string' && node.tag.startsWith('div.card.clickable')) return node;
  for (const c of node.children || []) { const f = find(c); if (f) return f; }
  return null;
}

const out = {};
const tree = await BrowserPage.render({ refresh() {} });
find(tree).props.onClick();                       // openLivePanel(7)
await flush();
out.opened = snap();
for (let i = 0; i < 5; i++) { tick(); await flush(); }   // 15 s of a stuck server
out.stuck = snap();
releaseAll(); await flush();
tick(); await flush();
out.after_release = snap();
releaseAll({ shotErr: true, stateErr: Object.assign(new Error('boom'), { status: 500 }) }); await flush();
tick(); await flush();
out.after_failure = snap();
releaseAll(); await flush();
document.hidden = true;
for (let i = 0; i < 3; i++) { tick(); await flush(); }
out.hidden = snap();
document.hidden = false;
tick(); await flush();
out.visible_again = snap();
const clearedBefore = n.cleared;
releaseAll({ stateErr: Object.assign(new Error('gone'), { status: 404 }) }); await flush();
out.cleared_by_404 = n.cleared > clearedBefore;
tick(); tick(); await flush();
out.after_404 = snap();
out.interval = n.interval;
process.stdout.write(JSON.stringify(out));
"""


def _node() -> str:
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: production JS panel-polling contracts were not executed")
    return node


def _run_panel(tmp_path: Path) -> dict:
    pages = tmp_path / "ui" / "pages"
    pages.mkdir(parents=True)
    (tmp_path / "ui" / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    (pages / "browser.js").write_text(JS, encoding="utf-8")       # the product file, byte for byte
    (pages / "_ui.js").write_text(_UI_STUB, encoding="utf-8")
    (tmp_path / "ui" / "api.js").write_text(_API_STUB, encoding="utf-8")
    (tmp_path / "ui" / "components.js").write_text(_COMPONENTS_STUB, encoding="utf-8")
    driver = tmp_path / "driver.mjs"
    driver.write_text(_DRIVER, encoding="utf-8")
    run = subprocess.run([_node(), str(driver)], capture_output=True, text=True, timeout=60,
                         cwd=str(tmp_path), check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    return json.loads(run.stdout)


def test_stuck_server_gets_one_request_of_each_kind_not_one_per_tick(tmp_path):
    out = _run_panel(tmp_path)
    assert out["interval"] == 3000
    assert out["opened"] == {"shots": 1, "states": 1}
    # 5 ticks while both requests hang: nothing new is sent.
    assert out["stuck"] == {"shots": 1, "states": 1}, out
    # Control: the guard is released on success AND on failure, polling resumes.
    assert out["after_release"] == {"shots": 2, "states": 2}, out
    assert out["after_failure"] == {"shots": 3, "states": 3}, out


def test_hidden_tab_is_not_polled_and_resumes_when_visible(tmp_path):
    out = _run_panel(tmp_path)
    assert out["hidden"] == out["after_failure"], out
    assert out["visible_again"] == {"shots": 4, "states": 4}, out


def test_gone_session_still_stops_polling(tmp_path):
    """Unchanged behaviour: a 404 from /state stops the poll for good."""
    out = _run_panel(tmp_path)
    assert out["after_404"] == out["visible_again"], out
    assert out["cleared_by_404"] is True


def test_static_interval_runs_the_guarded_tick_only():
    """No-node invariant: setInterval gets the guarded tick, not raw refresh calls."""
    assert re.findall(r"setInterval\((\w+),\s*3000\)", JS) == ["tick"]
    assert not re.search(r"setInterval\(\(\)\s*=>", JS)
    body = JS.split("const tick = () => {", 1)
    assert len(body) == 2, "guarded tick missing"
    tick = body[1].split("};", 1)[0]
    assert "if (document.hidden) return;" in tick
    for flag, fn in (("shotBusy", "refreshShot"), ("stateBusy", "refreshState")):
        assert f"if (!{flag}) {{ {flag} = true; {fn}().finally(() => {{ {flag} = false; }}); }}" in tick
