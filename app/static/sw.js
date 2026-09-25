const CACHE='flightline-tracker-v23';
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll([
    '/', '/history', '/logbook',
    '/static/style.css?v=23',
    '/static/i18n.js?v=23',
    '/static/admin.js?v=23',
    '/static/app.js?v=23',
    '/static/history.js?v=23',
    '/static/logbook.js?v=23',
    '/static/theme.js?v=23',
    '/static/icons/favicon-32.png',
    '/static/icons/favicon-64.png',
    '/static/icons/apple-touch-icon.png',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png'
  ])));
});
self.addEventListener('activate', event => {
  event.waitUntil(Promise.all([
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))),
    self.clients.claim()
  ]));
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
