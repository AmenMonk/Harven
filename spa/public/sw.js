/* ═══════════════════════════════════════════════════════════════
   哈文 SPA Service Worker（F 批 PWA）
   设计原则（吸取旧版缓存死锁教训）：
   1. /api/* 一律不拦截 —— 对话必须走真网络，绝不吃缓存旧答案
   2. index.html / 导航请求 → 网络优先：有网永远拿最新壳，断网才用缓存
   3. /static/spa/assets/* → 缓存优先：文件名带 hash，内容变 = 名字变，
      缓存永不指向旧内容，绝对安全
   4. 其余静态（图标等）→ 缓存优先但只缓存同源 GET
   ═══════════════════════════════════════════════════════════════ */
const CACHE = 'harven-spa-v3'   // ★ #1354（2026-10-31）v2→v3：activate 自动删 v2 旧缓存（开发者页面再钉旧版实锤，根治见版本巡检）

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(['/static/spa/', '/static/spa/index.html'])).then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (e) => {
  const req = e.request
  const url = new URL(req.url)

  // 只管同源 GET；/api 一律放行（对话、上传绝不吃缓存）
  if (req.method !== 'GET' || url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) return
  // 面板 iframe / SSE 走的路径不拦
  if (url.pathname.startsWith('/panel/')) return

  // 带-hash 的构建产物：缓存优先（内容寻址，永不脏）
  if (url.pathname.startsWith('/static/spa/assets/')) {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
        if (resp.ok) {
          const clone = resp.clone()
          caches.open(CACHE).then((c) => c.put(req, clone))
        }
        return resp
      }))
    )
    return
  }

  // 页面导航（含断网后打开哈文）：网络优先，失败回缓存壳
  if (req.mode === 'navigate' || url.pathname === '/static/spa/' || url.pathname === '/static/spa/index.html') {
    e.respondWith(
      fetch(req).then((resp) => {
        if (resp.ok) {
          const clone = resp.clone()
          caches.open(CACHE).then((c) => c.put('/static/spa/index.html', clone))
        }
        return resp
      }).catch(() => caches.match('/static/spa/index.html'))
    )
    return
  }

  // 其他同源静态（图标等）：缓存优先 + 后台更新
  e.respondWith(
    caches.match(req).then((hit) => {
      const netFetch = fetch(req).then((resp) => {
        if (resp.ok) {
          const clone = resp.clone()
          caches.open(CACHE).then((c) => c.put(req, clone))
        }
        return resp
      }).catch(() => hit)
      return hit || netFetch
    })
  )
})
