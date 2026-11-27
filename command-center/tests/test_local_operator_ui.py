"""«Локальные модели» pult page (27.11): pure logic and a render of the REAL page module in node with stub UI modules."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "ui"


def _node() -> str:
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


STUB_API = """export const api = { raw: async (path, opts) => globalThis.__api(path, opts) };"""
STUB_COMPONENTS = """export function h(tag, ...kids) {
  const flat = []; const walk = (k) => { if (Array.isArray(k)) k.forEach(walk); else if (k !== null && k !== undefined && k !== false) flat.push(k); };
  let attrs = null; if (kids.length && kids[0] && typeof kids[0] === 'object' && !Array.isArray(kids[0]) && !kids[0].tag) attrs = kids.shift();
  walk(kids); return { tag, attrs, children: flat, set textContent(v) { this.text = v; } }; }"""
STUB_UI = """export const pageHead = (t, sub, o) => ({ tag: 'head', title: t, children: (o && o.actions) || [] });
export const panel = (t, body) => ({ tag: 'panel', title: t, children: [body] });
export const pill = (text, o) => ({ tag: 'pill', text, tone: o && o.tone, children: [] });
export const btn = (label, onClick, o) => ({ tag: 'btn', label, onClick, variant: o && o.variant, children: [] });
export const errorNote = (e) => ({ tag: 'error', text: String(e), children: [] });"""

DRIVER = r"""
import * as M from './pages/local_operator_model.js';
const out = {};
const tools = { name: 'qwen3-coder:30b', tools: true, vision: false, chat: true, cloud: false, capabilities_source: 'ollama' };
const vis = { name: 'gemma3:4b', tools: false, vision: true, chat: true, cloud: false, capabilities_source: 'ollama' };
const cloud = { name: 'gpt-oss:120b-cloud', tools: true, chat: true, cloud: true, capabilities_source: 'ollama' };
const emb = { name: 'nomic-embed-text', tools: false, chat: false, cloud: false, capabilities_source: 'ollama' };
const old = { name: 'old:latest', tools: false, chat: true, cloud: false, capabilities_source: 'unknown' };
const agents = [{ id: 11, name: 'Локально · qwen3-coder:30b' }, { id: 12, name: 'Локально · qwen3-coder:30b · ПК' },
                { id: 13, name: 'Локально · gemma3:4b' }];
out.r_tools = M.readiness(tools, agents).state;
out.r_vis = M.readiness(vis, agents).state;
out.r_vis_note = M.readiness(vis, agents).note;
out.r_cloud = M.readiness(cloud, agents).state;
out.r_emb = M.readiness(emb, agents).state;
out.r_unsynced = M.readiness({ ...tools, name: 'new:1b' }, agents).state;
out.b_old = M.modelBadges(old).map((b) => b.text);
out.b_cloud = M.modelBadges(cloud).map((b) => b.text);
out.route = M.chatRoute({ id: 12 });
out.route_none = M.chatRoute(null);
out.sum_ok = M.syncSummary({ ok: true, models: [{ status: 'create' }, { status: 'exists' }, { status: 'skipped' }],
                             agents: [{ status: 'create' }, { status: 'create' }, { status: 'drift' }] });
out.sum_err = M.syncSummary({ ok: false, error: 'Ollama не отвечает — 127.0.0.1:11434: ConnectError' });
out.size = [M.humanSize(18e9), M.humanSize(270e6), M.humanSize(0)];

globalThis.localStorage = { store: {}, setItem(k, v) { this.store[k] = v; } };
globalThis.location = { href: '' };
globalThis.__api = async (path) => {
  if (path === '/api/local-operator/status') return { ollama: { ok: true, version: '0.12.3', models: [tools, vis, cloud] },
    agents: [...agents, { id: 80, name: 'Bossman Harness' }] };
  throw new Error('unexpected ' + path);
};
const page = (await import('./pages/local_operator.js')).default;
const tree = await page.render({ refresh() {} });
const flat = []; const walk = (n) => { if (!n || typeof n !== 'object') { if (n !== undefined && n !== null) flat.push(n); return; } flat.push(n); (n.children || []).forEach(walk); };
walk(tree);
const btns = flat.filter((n) => n && n.tag === 'btn');
out.buttons = btns.map((b) => b.label);
const pcBtn = btns.find((b) => b.label === 'Чат + ПК');
pcBtn.onClick();
out.after_click = { route: localStorage.store['bcc.chat.route'], href: location.href };
out.text = JSON.stringify(tree).includes('qwen3-coder:30b');
globalThis.__api = async () => { throw new Error('сеть недоступна'); };
const errTree = await page.render({ refresh() {} });
out.err = JSON.stringify(errTree).includes('сеть недоступна');
console.log(JSON.stringify(out));
"""


def test_local_operator_page_logic_and_render(tmp_path):
    node = _node()
    (tmp_path / "pages").mkdir()
    for name in ("local_operator.js", "local_operator_model.js"):
        shutil.copy(UI / "pages" / name, tmp_path / "pages" / name)
    (tmp_path / "api.js").write_text(STUB_API, encoding="utf-8")
    (tmp_path / "components.js").write_text(STUB_COMPONENTS, encoding="utf-8")
    (tmp_path / "pages" / "_ui.js").write_text(STUB_UI, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    run = subprocess.run([node, str(driver)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["r_tools"] == "ready_pc" and out["r_vis"] == "ready_chat" and "только чат" in out["r_vis_note"]
    assert out["r_cloud"] == "skip" and out["r_emb"] == "skip" and out["r_unsynced"] == "not_synced"
    assert "возможности неизвестны" in out["b_old"] and "инструменты" not in out["b_old"]
    assert out["b_cloud"][0] == "облако — не локально"
    assert out["route"] == "agent:12" and out["route_none"] == "auto"
    assert "моделей подключено: 1" in out["sum_ok"] and "агентов создано: 2" in out["sum_ok"]
    assert "пропущено: 1" in out["sum_ok"] and "не тронуто" in out["sum_ok"]
    assert out["sum_err"].startswith("Не подключено: Ollama не отвечает")
    assert out["size"] == ["18.0 ГБ", "270 МБ", ""]
    assert "Взять модели из Ollama" in out["buttons"] and "Чат + ПК" in out["buttons"]
    assert "Открыть Harness в чате" in out["buttons"]
    assert out["buttons"].count("Чат + ПК") == 1          # only the tool model has a PC agent; cloud gets nothing
    assert out["after_click"] == {"route": "agent:12", "href": "/chat.html"}
    assert out["text"] and out["err"]


def test_page_is_registered_in_the_pult():
    src = (UI / "pages" / "index.js").read_text(encoding="utf-8")
    assert "id: 'local-operator'" in src and "import('./local_operator.js')" in src
