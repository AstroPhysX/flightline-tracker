const CACHE='flightline-tracker-v15';
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll([
    '/', '/history', '/logbook',
    '/static/style.css?v=15',
    '/static/i18n.js?v=15',
    '/static/admin.js?v=15',
    '/static/app.js?v=15',
    '/static/history.js?v=15',
    '/static/logbook.js?v=15',
    '/static/theme.js?v=15'
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
