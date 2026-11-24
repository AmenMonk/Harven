// 哈文 SPA 共享 fetch 助手 — B 批页面统一走这里
// 后端约定：多数接口返回 {success, data, error} 或裸数组/对象

export async function getJSON(url) {
  const res = await fetch(url, { headers: { 'Cache-Control': 'no-cache' } })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function postJSON(url, body = {}) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { const j = await res.json(); if (j.error || j.message) msg = j.error || j.message } catch (_) {}
    throw new Error(msg)
  }
  return res.json()
}

export async function putJSON(url, body = {}) {
  const res = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function delJSON(url, body) {
  const opts = { method: 'DELETE' }
  // ★ 2026-09-16 支持 DELETE 带 body（如锁定面板卸载需传管理员密码）
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(url, opts)
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { const j = await res.json(); if (j.error) msg = j.error; else if (j.message) msg = j.message } catch (_) {}
    throw new Error(msg)
  }
  return res.json()
}

// 兼容两种返回：{success,data} 包装 或 裸数据
export function unwrap(j, fallback = {}) {
  if (j && typeof j === 'object' && 'success' in j && 'data' in j) {
    return j.success ? j.data : fallback
  }
  return j ?? fallback
}
