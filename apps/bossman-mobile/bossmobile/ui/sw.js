/* Service worker: caches ONLY the app shell. API answers, photos and the panel always go to the network. */
'use strict';
const CACHE = 'bossman-shell-v4';
const SHELL = ['/m/', '/m/index.html', '/m/app.css', '/m/app.js', '/m/manifest.webmanifest',
  '/m/icons/apple-touch-icon.png', '/m/icons/icon-192.png', '/m/icons/icon-512.png'];

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
