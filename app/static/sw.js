const CACHE='flightline-tracker-v27';
const CORE=[
  '/', '/history', '/logbook',
  '/static/style.css?v=27',
  '/static/i18n.js?v=27',
  '/static/admin.js?v=27',
  '/static/app.js?v=27',
  '/static/history.js?v=27',
  '/static/logbook.js?v=27',
  '/static/theme.js?v=27',
  '/static/icons/favicon-32.png?v=27',
  '/static/icons/favicon-64.png?v=27',
  '/static/icons/apple-touch-icon.png?v=27',
  '/static/icons/icon-192.png?v=27',
  '/static/icons/icon-512.png?v=27'
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
