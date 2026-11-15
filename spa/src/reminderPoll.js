/* 提醒主动触达 · 前端轮询 ★ 2026-10-15 #1150
   每 60s 拉 /api/reminders/due（daemon 最近 5 分钟已播报的提醒），
   有新通知 → 顶部横幅 + 铃声。localStorage 按 id+remind_at 去重（已弹过不重弹）。
   铃声=Web Audio 两声短音，零音频文件依赖。失败全程静默（绝不影响正常使用）。 */

const SEEN_KEY = 'harven_reminder_seen_v1'

function loadSeen() {
  try { return JSON.parse(localStorage.getItem(SEEN_KEY) || '[]') } catch (e) { return [] }
}

function saveSeen(arr) {
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(arr.slice(-50))) } catch (e) { /* 静默 */ }
}

function ringBell() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext
    if (!Ctx) return
    const ctx = new Ctx()
    const play = (t0) => {
      const o = ctx.createOscillator()
      const g = ctx.createGain()
      o.type = 'sine'
      o.frequency.value = 880
      g.gain.setValueAtTime(0.0001, t0)
      g.gain.exponentialRampToValueAtTime(0.25, t0 + 0.02)
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.35)
      o.connect(g); g.connect(ctx.destination)
      o.start(t0); o.stop(t0 + 0.4)
    }
    const now = ctx.currentTime
    play(now); play(now + 0.5)
    setTimeout(() => { try { ctx.close() } catch (e) { /* 静默 */ } }, 2000)
  } catch (e) { /* 静默 */ }
}

function showToast(n) {
  try {
    const box = document.createElement('div')
    box.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:99999;' +
      'background:#fff7e6;border:1px solid #f0c36d;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.18);' +
      'padding:12px 18px;font-size:14px;color:#6b4a00;max-width:86vw;display:flex;align-items:center;gap:10px;' +
      'font-family:inherit;animation:harvenReminderIn .3s ease'
    const hm = (n.remind_at || '').slice(11) || ''
    box.innerHTML = '<span style="font-size:20px">&#9200;</span>' +
      '<div><div style="font-weight:600">到点啦：' + (n.title || '提醒') + '</div>' +
      (hm ? '<div style="font-size:12px;opacity:.7;margin-top:2px">原定 ' + hm + '</div>' : '') + '</div>'
    const style = document.createElement('style')
    style.textContent = '@keyframes harvenReminderIn{from{opacity:0;transform:translate(-50%,-12px)}to{opacity:1;transform:translate(-50%,0)}}'
    document.head.appendChild(style)
    document.body.appendChild(box)
    setTimeout(() => {
      box.style.transition = 'opacity .4s'
      box.style.opacity = '0'
      setTimeout(() => box.remove(), 450)
      style.remove()
    }, 12000)
  } catch (e) { /* 静默 */ }
}

async function pollOnce() {
  try {
    const res = await fetch('/api/reminders/due', { cache: 'no-store' })
    if (!res.ok) return
    const data = await res.json()
    const list = (data && data.notifications) || []
    if (!list.length) return
    const seen = loadSeen()
    let changed = false
    for (const n of list) {
      const key = n.id + '@' + (n.remind_at || '')
      if (seen.includes(key)) continue
      seen.push(key)
      changed = true
      showToast(n)
      ringBell()
    }
    if (changed) saveSeen(seen)
  } catch (e) { /* 静默：daemon 没起/离线都不打扰 */ }
}

export function startReminderPoll() {
  // 启动后先查一次（页面刚打开时 daemon 可能刚触达过），此后每 60s 一轮
  setTimeout(pollOnce, 4000)
  setInterval(pollOnce, 60000)
}
