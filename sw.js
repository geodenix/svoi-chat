const CACHE_NAME = 'svoi-shell-v87';
const SHELL = [
  '/',
  '/manifest.webmanifest',
  '/icon-192.svg',
  '/icon-512.svg'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
    );
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/uploads/') ||
    url.pathname === '/sw.js'
  ) {
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(request);
        const cache = await caches.open(CACHE_NAME);
        cache.put('/', fresh.clone());
        return fresh;
      } catch {
        return (await caches.match('/')) || Response.error();
      }
    })());
    return;
  }

  event.respondWith(
    caches.match(request).then(cached => cached || fetch(request))
  );
});

self.addEventListener('push', event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {
      title: 'Свои',
      body: event.data ? event.data.text() : 'Новое сообщение'
    };
  }

  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true
    });

    const unreadCount = Math.max(0, Number(data.unread_count) || 0);
    try {
      if (unreadCount > 0 && typeof navigator.setAppBadge === 'function') {
        await navigator.setAppBadge(unreadCount);
      } else if (unreadCount === 0 && typeof navigator.clearAppBadge === 'function') {
        await navigator.clearAppBadge();
      }
    } catch {}

    if (!data.force && windows.some(client => client.visibilityState === 'visible')) {
      return;
    }

    const tag = String(data.tag || '');
    const incomingCall = tag.startsWith('incoming-call-') || tag.startsWith('group-call-');
    const silent = !!data.silent && !incomingCall;

    const options = {
      body: data.body || 'Новое сообщение',
      tag: data.tag || 'svoi',
      renotify: !silent,
      requireInteraction: incomingCall,
      silent,
      icon: '/icon-192.svg',
      badge: '/icon-192.svg',
      data: {
        url: data.url || '/'
      }
    };
    if (!silent) {
      options.vibrate = incomingCall ? [500, 180, 500, 180, 900] : [180];
    }

    await self.registration.showNotification(data.title || 'Свои', options);
  })());
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const targetUrl = new URL(
    event.notification.data?.url || '/',
    self.location.origin
  ).href;

  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true
    });

    for (const client of windows) {
      if (client.url.startsWith(self.location.origin)) {
        await client.focus();
        if ('navigate' in client) {
          await client.navigate(targetUrl);
        }
        return;
      }
    }

    await self.clients.openWindow(targetUrl);
  })());
});
