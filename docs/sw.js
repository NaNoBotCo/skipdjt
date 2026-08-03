// sw.js - offline shell for Skip DJT. Cache-first, refreshed each build.
const SHELL = 'skipdjt-202608031717';
const ASSETS = ['./', './index.html', './manifest.webmanifest', './icon.svg',
                './data.json', './card.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks =>
    Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  // Never intercept the outbound affiliate links - they must hit the network
  // so the booking is attributed. A cached redirect would earn nothing.
  if (new URL(e.request.url).origin !== self.location.origin) return;
  e.respondWith(
    caches.match(e.request).then(hit => hit ||
      fetch(e.request).catch(() => caches.match('./index.html')))
  );
});
