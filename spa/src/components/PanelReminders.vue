<template>
  <!-- ★ #1219（2026-10-25）通用「相关提醒」横条：任何面板一行接入（SPA 壳层，零改面板模板）
       双源 = 用户口头设的提醒（home_reminders，按域标签）+ 面板数据自动算的（管家议程） -->
  <div v-if="items.length" class="prm">
    <div class="prm-head">⏰ 相关提醒<span class="prm-n">{{ items.length }}</span></div>
    <div v-for="it in items" :key="it.id" class="prm-row">
      <div class="prm-main">
        <span class="prm-title">{{ it.title }}</span>
        <span class="prm-time">{{ it.time }}</span>
        <span v-if="it.src === 'agenda'" class="prm-tag">面板自动</span>
        <span v-else class="prm-tag user">口头设置</span>
      </div>
      <button v-if="it.src === 'user'" class="prm-del" title="删除该提醒" @click="del(it)">删</button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({ panelId: { type: String, required: true } })
const items = ref([])

async function load() {
  if (!props.panelId) { items.value = []; return }
  try {
    const r = await fetch(`/api/reminders/for-panel/${encodeURIComponent(props.panelId)}`).then(x => x.json())
    items.value = r.reminders || []
  } catch { items.value = [] }
}
async function del(it) {
  try {
    await fetch(`/api/reminders/${it.rid}`, { method: 'DELETE' })
    await load()
  } catch { /* 静默 */ }
}
watch(() => props.panelId, load, { immediate: true })
// 每 60 秒轻刷新（与管家中心节奏一致）
let t = null
watch(() => props.panelId, (nv, ov) => {
  if (t) clearInterval(t)
  if (nv) t = setInterval(load, 60000)
}, { immediate: true })
</script>

<style scoped>
.prm { border: 1px solid #e8eefc; background: #f6f9ff; border-radius: 12px; padding: 10px 14px; margin-bottom: 0; flex-shrink: 0; max-height: 132px; overflow-y: auto; }
.prm-head { font-size: 13px; font-weight: 600; color: #3b6fd4; margin-bottom: 6px; }
.prm-n { margin-left: 6px; font-size: 12px; color: #7d9bd8; font-weight: 400; }
.prm-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 13px; }
.prm-main { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.prm-title { color: #333; }
.prm-time { color: #8a94a6; font-size: 12px; }
.prm-tag { font-size: 11px; padding: 1px 7px; border-radius: 8px; background: #eef2f8; color: #8a94a6; }
.prm-tag.user { background: #e8f5ee; color: #2c8a58; }
.prm-del { border: 1px solid #eee; background: #fff; border-radius: 8px; padding: 2px 9px; font-size: 12px; color: #b4443c; cursor: pointer; }
.prm-del:hover { background: #fdf2f2; }
</style>
