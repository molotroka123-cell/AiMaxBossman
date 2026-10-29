/* Service worker: caches ONLY the app shell. API answers, photos and the panel always go to the network. */
'use strict';
const CACHE = 'bossman-shell-v32';   // v32: AI Gateway privacy card on Status tab + fast health probes (29.10); v31: «Не спрашивать в этом чате» per operation (29.10); v30: model locked per chat (29.10); v29: AUTO never auto-approves for cloud agents (29.10); v28: «Разрешить все» in the approvals card (29.10); v27: glass UI, bundled Inter variable font, sliding tab pill (28.10); v26: push-to-talk mic button (28.10); v25: modern typography, tactile button transitions, green stop-resume, approval meta/rollback & honest N/A cost badge (26.10); v24: mode chips reach the agent dispatcher, terminal cwd/approval/claude refusal, live $ badge (audit 25.10)
const SHELL = ['/m/', '/m/index.html', '/m/app.css', '/m/app.js', '/m/manifest.webmanifest',
  '/m/icons/apple-touch-icon.png', '/m/icons/icon-192.png', '/m/icons/icon-512.png',
  '/m/vendor/tabbable.umd.min.js', '/m/vendor/focus-trap.umd.min.js', '/m/fonts/InterVariable.woff2'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin || !SHELL.includes(url.pathname)) return;
  // network first (fresh shell after an update), cache when the PC is unreachable
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
    return res;
  }).catch(() => caches.match(e.request)));
});
