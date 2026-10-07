/* Kai Command Center — Service Worker
   Part of: Kai Mobile Command Node — Sub-project 5: Mobile Agent & UI

   Strategy: Cache-first for the app shell, network-first for API data.
   The app shell (HTML, CSS, JS) is cached on install and served from cache
   for instant cold starts. API responses are served from network with a
   stale-while-revalidate fallback so the UI shows cached data while
   fetching fresh data in the background.
*/

const CACHE_NAME = 'kai-cc-v1';
const SHELL_CACHE = 'kai-shell-v1';
const API_CACHE = 'kai-api-v1';

// App shell files — cached on install, never change without a SW update
const SHELL_FILES = [
  '/command-center',
  '/',
  '/kai/manifest.json',
];

// API patterns — network-first with stale-while-revalidate
const API_PATTERNS = [
  '/health',
  '/incidents',
  '/decisions',
  '/approvals',
  '/actions',
  '/verifications',
  '/learning',
  '/api/modules',
  '/auth/status',
  '/kai/notifications/unread-count',
  '/kai/devices',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => {
      return cache.addAll(SHELL_FILES).catch((err) => {
        console.warn('SW: shell cache warm failed (some may be offline):', err.message);
      });
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((k) => k !== SHELL_CACHE && k !== API_CACHE && k !== CACHE_NAME)
          .map((k) => caches.delete(k))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // API requests: network-first, fall back to cache
  const isApi = API_PATTERNS.some((p) => url.pathname.startsWith(p));
  if (isApi) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(API_CACHE).then((cache) => {
            cache.put(event.request, clone);
          });
          return response;
        })
        .catch(() => {
          return caches.match(event.request).then((cached) => {
            return cached || new Response(JSON.stringify({
              error: 'offline',
              detail: 'No cached data available',
            }), {
              status: 503,
              headers: { 'Content-Type': 'application/json' },
            });
          });
        })
    );
    return;
  }

  // Navigation / app shell: cache-first
  if (event.request.mode === 'navigate' || SHELL_FILES.some((f) => url.pathname === f || url.pathname.endsWith(f))) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        return cached || fetch(event.request).then((response) => {
          const clone = response.clone();
          caches.open(SHELL_CACHE).then((cache) => {
            cache.put(event.request, clone);
          });
          return response;
        });
      })
    );
    return;
  }

  // Other static assets: cache-first
  event.respondWith(
    caches.match(event.request).then((cached) => {
      return cached || fetch(event.request);
    })
  );
});

// Push notification handler (for future FCM/web-push integration)
self.addEventListener('push', (event) => {
  if (!event.data) return;

  try {
    const data = event.data.json();
    const options = {
      body: data.body || '',
      icon: '/kai/icon-192.png',
      badge: '/kai/badge-72.png',
      tag: data.id || 'kai-notification',
      data: {
        url: data.url || '/command-center',
        notificationId: data.id,
      },
      actions: (data.actions || []).map((a) => ({
        action: a.action,
        title: a.label,
      })),
      requireInteraction: data.severity === 'critical',
      vibrate: data.severity === 'critical' ? [200, 100, 200] : [100, 50, 100],
    };

    event.waitUntil(self.registration.showNotification(data.title, options));
  } catch (e) {
    console.warn('SW: push notification parse failed', e);
  }
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const targetUrl = event.notification.data?.url || '/command-center';

  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      // Focus existing window if open
      for (const client of clients) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.focus();
          client.postMessage({ type: 'navigate', url: targetUrl });
          return;
        }
      }
      // Open new window
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});

// Background sync for offline actions
self.addEventListener('sync', (event) => {
  if (event.tag === 'kai-heartbeat') {
    event.waitUntil(
      self.clients.matchAll({ type: 'window' }).then((clients) => {
        clients.forEach((client) => {
          client.postMessage({ type: 'background-sync', tag: 'kai-heartbeat' });
        });
      })
    );
  }
});
