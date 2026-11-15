import { defineStore } from 'pinia'
import { ref } from 'vue'

// 全局 UI 状态 — 总方案 B0 状态规范
// 模式三色：cloud 外脑 / local 本地 / vertical 垂直
export const useUiStore = defineStore('ui', () => {
  const mode = ref('cloud')            // 当前查询模式
  const sidebarCollapsed = ref(false)
  const currentPage = ref('home')
  const railOpen = ref(false)          // ★ F批手机端：窄屏左栏抽屉开关（桌面端恒显示，此状态只在 <900px 生效）

  function setMode(m) {
    if (['cloud', 'local', 'vertical'].includes(m)) mode.value = m
  }
  function closeRail() { railOpen.value = false }
  return { mode, sidebarCollapsed, currentPage, railOpen, setMode, closeRail }
})
