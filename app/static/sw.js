const CACHE='flightline-tracker-v17';
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll([
    '/', '/history', '/logbook',
    '/static/style.css?v=17',
    '/static/i18n.js?v=17',
    '/static/admin.js?v=17',
    '/static/app.js?v=17',
    '/static/history.js?v=17',
    '/static/logbook.js?v=17',
    '/static/theme.js?v=17'
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
