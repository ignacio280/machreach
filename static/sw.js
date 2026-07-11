/* MachReach service worker — conservative, safe for a dynamic authed app.
 *
 * Strategy:
 *   - Only ever touch same-origin GET requests.
 *   - Navigations (HTML pages): NETWORK-FIRST, falling back to the offline page
 *     only when the network is unreachable. We never serve cached HTML so an
 *     authenticated/stale page can't be shown to the wrong user.
 *   - /static assets: stale-while-revalidate (fast, self-healing).
 *   - /api, /webhooks, /logout, cross-origin: passthrough, never cached.
 *
 * Bump VERSION to invalidate the static cache on deploy.
 */
const VERSION = 'mr-v3';
const STATIC_CACHE = 'mr-static-' + VERSION;
const OFFLINE_URL = '/static/offline.html';

const PRECACHE = [
  OFFLINE_URL,
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== STATIC_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // fonts, CDNs, analytics

  // Never cache dynamic / auth-sensitive endpoints.
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/webhooks') ||
      url.pathname === '/logout' ||
      url.pathname === '/sw.js') {
    return;
  }

  // Page navigations: network-first, offline page as the only fallback.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  // Static assets: stale-while-revalidate.
  if (url.pathname.startsWith('/static/') || url.pathname === '/favicon.ico') {
    event.respondWith(
      caches.open(STATIC_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          const network = fetch(req).then((resp) => {
            if (resp && resp.status === 200 && resp.type === 'basic') {
              cache.put(req, resp.clone());
            }
            return resp;
          }).catch(() => cached);
          return cached || network;
        })
      )
    );
  }
});
