// GhostPort PWA Service Worker — cache static assets, offline fallback
const SW_VERSION = "2.0";
const CACHE_NAME = "gp-v" + SW_VERSION;
const STATIC_ASSETS = [
  "/",
  "/login.html",
  "/login.js",
  "/arsenal.js",
  "/logo.png",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
  "/manifest.json",
  "/offline.html"
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  var url = new URL(e.request.url);

  // API calls: network-only (never cache dynamic data)
  if (url.pathname.startsWith("/api/")) {
    return e.respondWith(fetch(e.request));
  }

  // Static assets: network-first with cache fallback
  e.respondWith(
    fetch(e.request)
      .then(function(response) {
        // Cache successful responses
        if (response.ok && e.request.method === "GET") {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function(cache) { cache.put(e.request, clone); });
        }
        return response;
      })
      .catch(function() {
        return caches.match(e.request).then(function(cached) {
          return cached || caches.match("/offline.html");
        });
      })
  );
});
