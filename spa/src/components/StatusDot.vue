<template>
  <span class="status-dot" :class="`s-${status}`" :title="title" />
</template>

<script setup>
// 状态点 — green 在线 / gray 离线 / amber 忙碌 / red 异常
defineProps({
  status: { type: String, default: 'gray' },  // green | gray | amber | red
  title: { type: String, default: '' },
})
</script>

<style scoped>
.status-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  vertical-align: middle;
}
.s-green { background: var(--mode-local); }
.s-gray { background: #C3CAD6; }
.s-amber { background: var(--mode-vertical); }
.s-red { background: var(--c-danger); }
</style>
