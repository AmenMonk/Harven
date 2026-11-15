<template>
  <span class="mode-capsule" :class="`m-${mode}`">{{ label }}</span>
</template>

<script setup>
// 模式胶囊 — 总方案 B0 状态规范：纯文字，三色（外脑紫/本地绿/垂直琥珀）
import { computed } from 'vue'
const props = defineProps({
  mode: { type: String, default: 'cloud' },  // cloud | local | vertical | collab
  text: { type: String, default: '' },
})
const LABELS = { cloud: '外脑', local: '本地', vertical: '垂直', collab: '🦞 协作' }
const label = computed(() => props.text || LABELS[props.mode] || props.mode)
</script>

<style scoped>
.mode-capsule {
  display: inline-block; padding: 1px 10px; border-radius: 999px;
  font-size: 12px; font-weight: 600; line-height: 20px;
}
.m-cloud { color: var(--mode-cloud); background: var(--mode-cloud-bg); }
.m-local { color: var(--mode-local); background: var(--mode-local-bg); }
.m-vertical { color: var(--mode-vertical); background: var(--mode-vertical-bg); }
.m-collab { color: #8A6632; background: #F7EEDC; }
</style>
