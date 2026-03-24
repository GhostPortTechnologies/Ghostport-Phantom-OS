// GhostPort PWA Service Worker — network-only (no caching)
// This exists solely to make the app installable as a PWA.
// All requests go straight to the network — nothing is cached.

const SW_VERSION = "1.3";

self.addEventListener("install", (e) => {
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  // Network-only — no caching, no offline fallback
  // This handler is required for PWA installability
  e.respondWith(fetch(e.request));
});
