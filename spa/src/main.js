import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './theme.css'
import './pwa'
import './panelApiBridge.js'  // ★ 2026-09-18 面板桥：iframe 面板 HarvenPanel API 的 postMessage 转发层（旧版有、SPA 迁移漏掉）
import { startReminderPoll } from './reminderPoll.js'  // ★ 2026-10-15 #1150 提醒主动触达：60s 轮询横幅+铃声

startReminderPoll()

createApp(App).use(createPinia()).use(router).mount('#app')

/* ★ 2026-09-16 助手小窗导航通道：panel-assist.js 是独立 script 拿不到 router，
   通过全局事件 harven:navigate 请求 SPA 跳转（小窗说"打开XX"直接跳页面） */
window.addEventListener('harven:navigate', (e) => {
  const p = e && e.detail && e.detail.path
  if (p && typeof p === 'string' && p.startsWith('/')) {
    try { router.push(p).catch(() => {}) } catch (err) { /* 静默 */ }
  }
})

/* ★ 2026-09-16 旧地址兼容：老版本用 /spa/index.html?panel=xxx 打开面板，
   这类链接/标签页进 SPA 后转成 #/panel/xxx 直达面板详情页 */
try {
  const qp = new URLSearchParams(location.search).get('panel')
  if (qp) {
    history.replaceState(null, '', location.pathname)
    router.replace('/panel/' + encodeURIComponent(qp)).catch(() => {})
  }
} catch (err) { /* 静默 */ }
