// Harven AI - Minimal Service Worker (no caching)
// This replaces the old caching SW to break the cache deadlock
self.addEventListener('install', () => { self.skipWaiting(); });
self.addEventListener('activate', () => { self.clients.claim(); });
// NO fetch handler = no request interception = fresh files every time
