/* ═══════════════════════════════════════════════════════
   F批 PWA：SW 注册 + 安装提示捕获（beforeinstallprompt）
   仅 https 或 localhost 注册（file:// / http+局域网 IP 不支持 SW，静默跳过）
   ═══════════════════════════════════════════════════════ */

// ── 安装提示：浏览器弹的"添加到主屏幕"存起来，等用户在「更多」页主动点安装 ──
let _deferredPrompt = null
const _listeners = new Set()

export function canInstall() { return !!_deferredPrompt }
export function onInstallAvailability(fn) {
  _listeners.add(fn)
  return () => _listeners.delete(fn)
}
export async function promptInstall() {
  if (!_deferredPrompt) return 'unavailable'
  _deferredPrompt.prompt()
  const { outcome } = await _deferredPrompt.userChoice
  if (outcome === 'accepted') _deferredPrompt = null
  _listeners.forEach((fn) => fn(canInstall()))
  return outcome
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault()          // 不让浏览器自己乱弹
  _deferredPrompt = e         // 存起来，「更多」页要用
  _listeners.forEach((fn) => fn(true))
})

window.addEventListener('appinstalled', () => {
  _deferredPrompt = null
  _listeners.forEach((fn) => fn(false))
})

// ── SW 注册 ──
if ('serviceWorker' in navigator && (location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname))) {
  window.addEventListener('load', () => {
    // ★ #1354：updateViaCache:'none' —— SW 脚本自身的更新检查也绝不走 HTTP 缓存，
    // 防"老 SW 缓存了新 SW"的死锁复发（10-31 开发者页面钉死 10-05 版实锤）
    navigator.serviceWorker.register('/static/spa/sw.js', { updateViaCache: 'none' }).catch(() => {
      /* SW 失败不影响使用——离线缓存是增强能力，不是依赖 */
    })
  })
}

// ═════════ ★ #1354（2026-10-31）版本巡检：服务器出新构建时顶部横幅提示一键刷新 ═════════
// 原理：构建产物 JS 文件名带内容 hash（index-<hash>.js）。从当前页面的 <script src>
// 提取本次构建的文件名，定期以 no-store 拉最新壳比对——文件名不一致 = 有新版。
// 配合 SW v3 双保险：SW 管"断网能开"，巡检管"开着页面也能感知发版"。
function _currentBuildJs() {
  try {
    const s = document.querySelector('script[src*="/static/spa/assets/index-"]')
    return (s && s.getAttribute('src')) || ''
  } catch (e) { return '' }
}

function _showUpdateBanner() {
  if (document.getElementById('harven-update-bar')) return
  const bar = document.createElement('div')
  bar.id = 'harven-update-bar'
  bar.textContent = '哈文已更新，点这里立即升级到新版'
  bar.style.cssText =
    'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
    'background:#2563eb;color:#fff;text-align:center;padding:11px 14px;' +
    'font-size:14px;font-weight:600;cursor:pointer;' +
    'box-shadow:0 2px 8px rgba(0,0,0,.25);font-family:system-ui,-apple-system,sans-serif'
  bar.onclick = () => location.reload()
  document.body.appendChild(bar)
}

async function _checkSpaUpdate() {
  if (document.hidden) return           // 页面在后台不折腾
  try {
    const r = await fetch('/static/spa/index.html?_chk=' + Date.now(), { cache: 'no-store' })
    if (!r.ok) return
    const html = await r.text()
    const m = html.match(/assets\/index-[A-Za-z0-9_-]+\.js/)
    if (!m) return
    const cur = _currentBuildJs()
    if (cur && cur.indexOf(m[0]) === -1) _showUpdateBanner()
  } catch (e) { /* 断网静默，巡检绝不能打扰正常使用 */ }
}

if (_currentBuildJs()) {
  setInterval(_checkSpaUpdate, 90000)   // 90 秒一查（页面可见时才真正发请求）
  window.addEventListener('focus', _checkSpaUpdate)  // 切回标签页立刻查一次
  _checkSpaUpdate()
}
