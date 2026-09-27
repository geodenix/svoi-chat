self.addEventListener('push', event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: 'Свои', body: event.data ? event.data.text() : 'Новое сообщение' };
  }

  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true
    });

    if (windows.some(client => client.visibilityState === 'visible')) {
      return;
    }

    await self.registration.showNotification(data.title || 'Свои', {
      body: data.body || 'Новое сообщение',
      tag: data.tag || 'svoi',
      renotify: true,
      data: {
        url: data.url || '/'
      }
    });
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
