// useSyncPolling.js — #1220-A 数据版本轮询（2026-10-25）
// 电脑端写库（面板/提醒/随身记）→ /api/sync/version 版本号变化 → 通知订阅者刷新
import { ref, onMounted, onUnmounted } from 'vue'

const POLL_MS = 15000          // 15 秒一轮（本地 LAN，开销可忽略）
let _timer = null
let _refCount = 0              // 多组件共用一个轮询器（引用计数）
const version = ref(-1)        // -1 = 首轮未拉取
const listeners = new Set()    // 回调集合：版本变化时依次调用

async function _pollOnce () {
  try {
    const r = await fetch('/api/sync/version', { cache: 'no-store' })
    if (!r.ok) return
    const d = await r.json()
    const v = d.version ?? -1
    if (version.value !== -1 && v !== version.value) {
      listeners.forEach(fn => { try { fn(v, d) } catch (e) { console.warn('[sync] 回调失败', e) } })
    }
    version.value = v
  } catch (e) {
    // 本地服务没起/网络断：静默，下轮再试
  }
}

/**
 * 订阅数据版本变化。返回取消函数。
 * @param {Function} onChange (newVersion, detail) => void
 */
export function useSyncPolling (onChange) {
  listeners.add(onChange)
  _refCount++
  if (!_timer) {
    _pollOnce()
    _timer = setInterval(_pollOnce, POLL_MS)
  }
  onMounted(() => { /* 首轮已在启动时拉取 */ })
  onUnmounted(() => {
    listeners.delete(onChange)
    _refCount--
    if (_refCount <= 0 && _timer) {
      clearInterval(_timer)
      _timer = null
    }
  })
  return () => listeners.delete(onChange)
}

/** 页面回到前台时立即拉一轮（不等 15 秒） */
export function syncPollNow () {
  return _pollOnce()
}

export function useSyncPollNow () {
  return syncPollNow
}

export default useSyncPolling
