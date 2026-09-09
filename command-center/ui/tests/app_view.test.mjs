import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

import { retainAppFrame } from '../app_view.js';
import { replace } from '../components.js';

// Execute the actual shell refresh path. This is a deterministic DOM contract
// test, not a replacement for the real Playwright owner workflow in CI.
const shell = await readFile(new URL('../app.js', import.meta.url), 'utf8');
const renderPage = shell.slice(shell.indexOf('async function renderPage()'),
  shell.indexOf('\nfunction refresh()'));

class Element {
  constructor(kind, attrs = {}) {
    this.kind = kind; this.attrs = attrs; this.nodeType = 1;
    this.dataset = {}; this.children = []; this.parentNode = null;
    this.removals = 0;
  }
  get firstChild() { return this.children[0] || null; }
  get firstElementChild() { return this.firstChild; }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this; this.children.push(child); return child;
  }
  removeChild(child) {
    this.removals += 1;
    this.children.splice(this.children.indexOf(child), 1); child.parentNode = null;
    return child;
  }
  replaceWith(next) {
    const parent = this.parentNode, index = parent.children.indexOf(this);
    if (next.parentNode) next.parentNode.removeChild(next);
    parent.children[index] = next; next.parentNode = parent; this.parentNode = null;
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  querySelector(selector) {
    const kind = selector === '.bx-appview-head' ? 'head' : 'iframe';
    for (const child of this.children) {
      if (child.kind === kind) return child;
      const found = child.querySelector(selector);
      if (found) return found;
    }
    return null;
  }
}

function app({ id = 'file-commander-mini', src = '/api/apps/file-commander-mini/view/',
  sandbox = 'allow-scripts allow-forms allow-same-origin allow-popups', running = true } = {}) {
  const node = new Element('app'); node.dataset.appId = id;
  node.appendChild(new Element('head'));
  if (running) node.appendChild(new Element('iframe', { src, sandbox, title: id }));
  return node;
}

async function refresh(view, next) {
  const context = vm.createContext({
    PAGE_BY_ID: new Map([['apps', { render: async () => next }]]),
    currentPage: 'apps', currentParams: { open: 'file-commander-mini' },
    renderToken: 0, lastRendered: 'apps', ctx: {}, el: { view },
    retainAppFrame, replace, mark() {}, schedulePreload() {}, syncTopStats() {},
    window: { scrollTo() {} }, console,
  });
  vm.runInContext(renderPage, context);
  await context.renderPage();
}

test('shell refresh preserves the running iframe and refreshes launcher controls', async () => {
  const view = new Element('view'), previous = app(); view.appendChild(previous);
  const frame = previous.querySelector('.bx-appview-frame iframe');
  const next = app(), nextHead = next.querySelector('.bx-appview-head');
  await refresh(view, next);
  assert.equal(view.firstElementChild, previous);
  assert.equal(view.removals, 0, 'refresh must not detach the active iframe ancestor');
  assert.equal(previous.querySelector('.bx-appview-frame iframe'), frame);
  assert.equal(previous.querySelector('.bx-appview-head'), nextHead);
});

for (const change of [
  { id: 'another-app' }, { src: '/different-process/' },
  { sandbox: 'allow-scripts' }, { running: false },
]) {
  test(`changed app identity, policy or stopped process cannot retain stale frame: ${JSON.stringify(change)}`, async () => {
    const view = new Element('view'); view.appendChild(app());
    const next = app(change);
    await refresh(view, next);
    assert.equal(view.firstElementChild, next);
    assert.equal(view.removals, 1);
  });
}
