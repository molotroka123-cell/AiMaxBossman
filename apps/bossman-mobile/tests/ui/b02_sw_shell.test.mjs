/* B02: phones keep the previous shell until the service-worker cache name changes; this branch changed index.html,
   app.css, app.js and added vendor scripts, so the cache is bumped once (v15). Every local script and stylesheet that
   index.html loads must be in the offline shell, or the app breaks when the PC is unreachable. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { UI } from './_boot.mjs';

test('cache bumped for the B02 shell', () => {
  const sw = readFileSync(join(UI, 'sw.js'), 'utf8');
  assert.ok(Number(/bossman-shell-v(\d+)/.exec(sw)[1]) >= 15);
});

test('every local <script src> and stylesheet of index.html is in the offline shell', () => {
  const html = readFileSync(join(UI, 'index.html'), 'utf8');
  const sw = readFileSync(join(UI, 'sw.js'), 'utf8');
  const shell = sw.split('const SHELL')[1].split('];')[0];
  const refs = [...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g), ...html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(refs.length >= 4);
  for (const r of refs.filter((x) => !/^https?:/.test(x))) assert.ok(shell.includes(`'/m/${r}'`), `${r} is cached for offline`);
  assert.equal(refs.filter((x) => /^https?:|^\/\//.test(x)).length, 0, 'no CDN at runtime');
});
