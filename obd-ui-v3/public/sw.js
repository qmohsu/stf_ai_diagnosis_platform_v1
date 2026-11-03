/* global self, caches, fetch, URL */
const VERSION = "stf-v3-frontend-v2";
const STATIC_CACHE = `${VERSION}-static`;
const PRECACHE = ["/offline.html", "/icons/icon-192.svg", "/icons/icon-512.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== STATIC_CACHE).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/portal/") || url.pathname.startsWith("/api/auth/")) { event.respondWith(fetch(request)); return; }
  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/")) { event.respondWith(cacheFirst(request)); return; }
  if (request.mode === "navigate") event.respondWith(networkFirstNavigation(request));
});
async function cacheFirst(request) {
  const cached = await caches.match(request); if (cached) return cached;
  const response = await fetch(request); if (response.ok) (await caches.open(STATIC_CACHE)).put(request, response.clone()); return response;
}
async function networkFirstNavigation(request) {
  try { return await fetch(request); } catch { return (await caches.match(request)) || (await caches.match("/offline.html")); }
}
