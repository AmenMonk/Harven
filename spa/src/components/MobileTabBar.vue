<template>
  <!-- ★ #810 手机底部标签栏：仅 ≤768px 显示，电脑端不渲染任何内容
       5 个大标签替代"抽屉+返回键"迷路问题；面板壳(全屏)和欢迎页不显示 -->
  <nav v-if="show" class="mtab-bar">
    <button v-for="t in tabs" :key="t.to" class="mtab" :class="{ on: active === t.name }" @click="go(t)">
      <HIcon :name="t.icon" :size="21" />
      <span>{{ t.label }}</span>
    </button>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HIcon from './HIcon.vue'

const route = useRoute()
const router = useRouter()

const tabs = [
  { name: 'home', to: '/', icon: 'chat', label: '对话' },
  { name: 'panels', to: '/panels', icon: 'grid', label: '面板' },
  { name: 'memory', to: '/memory', icon: 'image', label: '相册' },  // ★ #923 T2：记忆相册（手机生活照 AI 相册）
  { name: 'knowledge', to: '/knowledge', icon: 'book', label: '知识' },
  { name: 'butler', to: '/butler', icon: 'task', label: '管家' },  // ★ #1184 任务标签→管家中心
  { name: 'more', to: '/more', icon: 'dots', label: '更多' },
]

// 面板壳是全屏工作区、欢迎页是引导流程——都不该被标签栏挡
const hidden = new Set(['panel-shell', 'welcome', 'demo-collab', 'demo-ask'])
const show = computed(() => !hidden.has(String(route.name)))
const active = computed(() => String(route.name))

function go(t) {
  if (route.name !== t.name) router.push(t.to)
}
</script>

<style scoped>
.mtab-bar {
  display: none;
}
/* ★ 只在手机宽度渲染：电脑端 0 改动 */
@media (max-width: 768px) {
  .mtab-bar {
    display: flex;
    position: fixed;
    left: 0; right: 0; bottom: 0;
    z-index: 70;
    background: #fff;
    border-top: 1px solid var(--line, #e3e6eb);
    padding: 4px 4px calc(4px + env(safe-area-inset-bottom));
  }
  .mtab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    border: none;
    background: none;
    padding: 5px 0 3px;
    min-height: 48px;
    color: var(--sub, #7a8190);
    font-size: 11px;
    cursor: pointer;
    border-radius: 10px;
  }
  .mtab.on { color: var(--blue, #2f6fed); font-weight: 600; }
  .mtab:active { background: var(--c-bg-soft, #f4f6f9); }
}
</style>
