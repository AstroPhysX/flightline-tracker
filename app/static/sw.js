const CACHE='flightline-tracker-v29';
const CORE=[
  '/', '/history', '/logbook',
  '/static/style.css?v=29',
  '/static/i18n.js?v=29',
  '/static/admin.js?v=29',
  '/static/app.js?v=29',
  '/static/history.js?v=29',
  '/static/logbook.js?v=29',
  '/static/theme.js?v=29',
  '/static/icons/favicon-32.png?v=29',
  '/static/icons/favicon-64.png?v=29',
  '/static/icons/apple-touch-icon.png?v=29',
  '/static/icons/icon-192.png?v=29',
  '/static/icons/icon-512.png?v=29',
  '/static/icons/icon-maskable-192.png?v=29',
  '/static/icons/icon-maskable-512.png?v=29'
];
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(CORE)));
});
self.addEventListener('activate', event => {
  event.waitUntil(Promise.all([
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))),
    self.clients.claim()
  ]));
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request).then(hit => hit || (event.request.mode === 'navigate' ? caches.match('/') : Response.error()))));
});
