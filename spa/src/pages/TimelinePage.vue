<template>
  <PageShell :title="t('page.timeline.t')" icon="clock" :sub="t('page.timeline.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="load(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
    </template>

    <div class="tool-row">
      <div class="gbtns">
        <button :class="{ on: grain === 'day' }" @click="grain = 'day'">按日</button>
        <button :class="{ on: grain === 'month' }" @click="grain = 'month'">按月</button>
      </div>
      <div class="search"><HIcon name="search" :size="16" /><input v-model.trim="q" placeholder="按标题过滤" /></div>
      <span class="total">{{ convs.length }} 个会话</span>
    </div>

    <div v-if="!groups.length" class="empty">{{ q ? '没有匹配的会话' : '还没有会话记录' }}</div>

    <!-- 按日 -->
    <template v-if="grain === 'day'">
      <div v-for="g in groups" :key="g.key" class="tgroup">
        <div class="g-head">
          <b class="g-date">{{ g.label }}</b>
          <span class="g-count">{{ g.items.length }} 个会话</span>
        </div>
        <div class="g-list">
          <button v-for="c in g.items" :key="c.id" class="g-item" @click="$router.push('/')">
            <span class="g-dot" />
            <b class="g-title">{{ c.title || '（无标题）' }}</b>
            <span class="g-meta">{{ (c.updated_at || c.created_at || '').slice(11, 16) }} · {{ c.msg_count || 0 }} 条{{ c.category ? ' · ' + c.category : '' }}</span>
          </button>
        </div>
      </div>
    </template>

    <!-- 按月 -->
    <template v-else>
      <div v-for="g in monthGroups" :key="g.key" class="tgroup">
        <div class="g-head">
          <b class="g-date">{{ g.label }}</b>
          <span class="g-count">{{ g.items.length }} 个会话 · {{ monthTop(g) }}</span>
        </div>
        <div class="g-bar"><i :style="{ width: g.pct + '%' }" /></div>
      </div>
    </template>
  </PageShell>
</template>

<script setup>
import { ref, computed } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON } from '../api.js'

const convs = ref([])
const grain = ref('day')
const q = ref('')

const filtered = computed(() => {
  if (!q.value) return convs.value
  return convs.value.filter(c => (c.title || '').toLowerCase().includes(q.value.toLowerCase()))
})

const groups = computed(() => {
  const map = new Map()
  for (const c of filtered.value) {
    const d = (c.updated_at || c.created_at || '').slice(0, 10)
    if (!d) continue
    if (!map.has(d)) map.set(d, [])
    map.get(d).push(c)
  }
  return [...map.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([key, items]) => ({ key, label: key, items }))
})

const monthGroups = computed(() => {
  const map = new Map()
  for (const c of filtered.value) {
    const m = (c.updated_at || c.created_at || '').slice(0, 7)
    if (!m) continue
    if (!map.has(m)) map.set(m, 0)
    map.set(m, map.get(m) + 1)
  }
  const arr = [...map.entries()].sort((a, b) => b[0].localeCompare(a[0]))
  const max = Math.max(1, ...arr.map(e => e[1]))
  return arr.map(([key, n]) => ({ key, label: key, items: Array(n).fill(0), count: n, pct: Math.round((n / max) * 100) }))
})

function monthTop(g) {
  const n = g.count
  if (n >= 40) return '高产的月份 🔥'
  if (n >= 15) return '挺活跃'
  return '偶尔来聊聊'
}

async function load(showErr = false) {
  try {
    const j = await getJSON('/api/conversations')
    convs.value = Array.isArray(j) ? j : (j.conversations || [])
  } catch (_) {}
}

load(true)
</script>

<style scoped>
.tool-row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.gbtns { display: flex; gap: 4px; background: var(--c-bg-soft); border-radius: 10px; padding: 3px; }
.gbtns button { border: none; background: transparent; padding: 6px 16px; border-radius: 8px; font-size: 12.5px; cursor: pointer; color: var(--sub); }
.gbtns button.on { background: #fff; color: var(--ink); font-weight: 700; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.search { flex: 1; display: flex; align-items: center; gap: 8px; background: #fff; border: 1px solid var(--line); border-radius: 11px; padding: 8px 13px; color: var(--sub); }
.search input { flex: 1; border: none; outline: none; font-size: 13px; background: transparent; color: var(--ink); }
.total { font-size: 12.5px; color: var(--sub); white-space: nowrap; }
.empty { padding: 34px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13.5px; }

.tgroup { margin-bottom: 18px; }
.g-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.g-date { font-size: 14.5px; }
.g-count { font-size: 12px; color: var(--sub); }
.g-list { display: flex; flex-direction: column; border-left: 2px solid var(--line); margin-left: 5px; padding-left: 16px; gap: 2px; }
.g-item { display: flex; align-items: center; gap: 9px; border: none; background: transparent; text-align: left; padding: 7px 10px; border-radius: 9px; cursor: pointer; color: var(--ink); position: relative; }
.g-item:hover { background: #fff; box-shadow: var(--shadow-card); }
.g-dot { position: absolute; left: -21.5px; width: 9px; height: 9px; border-radius: 50%; background: var(--blue); border: 2px solid var(--c-bg); }
.g-title { font-size: 13px; flex: 0 0 auto; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.g-meta { font-size: 11.5px; color: var(--sub); }
.g-bar { height: 10px; border-radius: 8px; background: var(--c-bg-soft); overflow: hidden; max-width: 640px; }
.g-bar i { display: block; height: 100%; background: linear-gradient(90deg, var(--blue), var(--mode-cloud)); border-radius: 8px; transition: width .4s; }
</style>
