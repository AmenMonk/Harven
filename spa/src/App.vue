<template>
  <div class="app-shell">
    <main class="app-main">
      <router-view />
    </main>
    <!-- ★ #810 手机底部标签栏（≤768px 才渲染，电脑端零影响） -->
    <MobileTabBar />
    <!-- ★ #810 手机"添加到主屏幕"一次性引导 -->
    <div v-if="showPwaTip" class="pwa-tip" @click="closePwaTip">
      📲 想变成 APP？点浏览器「⋯」菜单 → <b>添加到主屏幕</b>，桌面就有哈文图标
      <span class="pt-x">知道了</span>
    </div>
  </div>
</template>

<script setup>
// ★ 方案 A 对齐设计稿 v2：撤掉全局顶导航 —— 导航全部收进首页左栏（品牌区/工具格/脚注），
//   内页靠 PageShell 返回键。「更多」页补模型中心/工坊入口，时间轴从左栏「查看全部会话」进。
import { ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useUiStore } from './stores/ui'
import MobileTabBar from './components/MobileTabBar.vue'
import { useSyncPolling, syncPollNow } from './composables/useSyncPolling'

const ui = useUiStore()
const route = useRoute()
watch(() => route.name, n => { ui.currentPage = String(n || 'home') })

// ★ #1220-A 数据版本轮询：电脑端写库 → 手机端/SPA 15 秒内感知，派发 window 事件
//   harven:sync，需要刷新的页面自行 addEventListener 订阅；页面回前台立即拉一轮
useSyncPolling((v, d) => {
  window.dispatchEvent(new CustomEvent('harven:sync', { detail: { version: v, reasons: d?.reasons || [] } }))
})
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') syncPollNow()
})

// ★ #810 主屏引导：只手机宽度 + 首次访问弹一次（localStorage 记住）
const PWA_TIP_KEY = 'harven_pwa_tip_seen'
const showPwaTip = ref(false)
onMounted(() => {
  const mq = window.matchMedia('(max-width: 768px)')
  if (mq.matches && !localStorage.getItem(PWA_TIP_KEY) && route.name !== 'welcome') {
    showPwaTip.value = true
  }
})
function closePwaTip() {
  showPwaTip.value = false
  localStorage.setItem(PWA_TIP_KEY, '1')
}
</script>

<style scoped>
.app-shell { height: 100vh; display: flex; flex-direction: column; }
.app-main { flex: 1; min-height: 0; overflow-y: auto; }

/* ★ #810 主屏引导条（只在手机宽度出现） */
.pwa-tip { display: none; }
@media (max-width: 768px) {
  .pwa-tip {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    position: fixed; left: 10px; right: 10px; bottom: 66px; z-index: 75;
    background: #1f2430; color: #fff; font-size: 12.5px; line-height: 1.5;
    padding: 10px 12px; border-radius: 12px; cursor: pointer;
    box-shadow: 0 6px 20px rgba(0,0,0,.25);
  }
  .pwa-tip b { color: #7db4ff; }
  .pt-x { margin-left: auto; color: #9fb3d1; font-size: 12px; flex-shrink: 0; }
}
</style>

<style>
/* ═══ ★ #810 全局手机层：弹窗全屏化 + 触控尺寸（全部包在 ≤768px，电脑端零改动） ═══ */
@media (max-width: 768px) {
  /* 给底部标签栏让位，避免内容最后一截被盖住 */
  .app-main { padding-bottom: 64px; }
  /* 弹窗 → 全屏贴底（5 个页面共用 .modal-mask，一处改全生效） */
  .modal-mask { align-items: flex-end !important; padding: 0 !important; }
  .modal-mask > div,
  .modal-box {
    width: 100% !important;
    max-width: 100% !important;
    max-height: 88vh !important;
    border-radius: 16px 16px 0 0 !important;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }
  /* 触控尺寸：手机上按钮/输入框 44px 起，字号加大 */
  button, .btn, .big-btn, select, input[type='text'], input[type='password'], input[type='number'], textarea {
    min-height: 40px;
  }
  button { font-size: 14px; }
}
</style>
