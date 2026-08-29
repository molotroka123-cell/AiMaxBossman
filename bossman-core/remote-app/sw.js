const CACHE = "bossman-stage12-v1";
const SHELL = ["/remote/app", "/remote/app/app.js", "/remote/app/remote-core.mjs", "/remote/app/styles.css", "/remote/app/manifest.webmanifest"];
self.addEventListener("install", e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  // Never cache authenticated API traffic or SSE.
  if (!u.pathname.startsWith("/remote/app")) return;
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
});
