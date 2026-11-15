<template>
  <PageShell :title="t('page.studio.t')" icon="music" :sub="t('page.studio.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="load(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
    </template>

    <div v-if="msg" class="tip bad">{{ msg }}</div>

    <div class="tool-row">
      <div class="search"><HIcon name="search" :size="16" /><input v-model.trim="q" placeholder="按提示词内容过滤" /></div>
      <span class="total">{{ items.length }} 件作品</span>
    </div>

    <div v-if="!filtered.length" class="empty">
      工坊还空着 —— 聊天里说「给我画一张…」或「来一段音乐」，作品完成后会出现在这里。
    </div>

    <div class="s-grid">
      <div v-for="it in filtered" :key="it.id" class="scard">
        <div class="s-top">
          <span class="s-badge">{{ TYPE_TEXT[it.type] || it.type || '作品' }}</span>
          <span class="s-time">{{ (it.created_at || '').slice(0, 16) }}</span>
        </div>
        <p class="s-prompt" :title="it.prompt || it.file_path">{{ it.prompt || it.file_path || '（未记录提示词）' }}</p>
        <div class="s-foot">
          <span class="s-model">{{ it.model_name || it.model || '' }}</span>
          <a v-if="it.file_path && it.file_path.startsWith('/static')" :href="it.file_path" target="_blank" class="mini">查看</a>
          <button v-else class="mini" @click="del(it)">删除记录</button>
        </div>
      </div>
    </div>

    <div class="howto">
      <b>💡 想创作？三种方式：</b>
      <span>① 聊天里直接描述（最简单）② 旧版界面左侧「画板」面板（可调参数）③ 专项面板选模型直答</span>
    </div>
  </PageShell>
</template>

<script setup>
import { ref, computed } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, delJSON } from '../api.js'

const TYPE_TEXT = { image: '🖼 图片', music: '🎵 音乐', video: '🎬 视频', canvas: '🎨 画板' }

const items = ref([])
const q = ref('')
const msg = ref('')

const filtered = computed(() => {
  if (!q.value) return items.value
  return items.value.filter(it => ((it.prompt || '') + (it.file_path || '')).toLowerCase().includes(q.value.toLowerCase()))
})

async function load(showErr = false) {
  try {
    const j = await getJSON('/api/vertical/history?limit=100')
    items.value = (j.history || []).map((h, i) => ({
      id: h.id ?? i,
      type: h.type || guessType(h),
      prompt: h.prompt || h.input || '',
      file_path: h.file_path || '',
      model_name: h.model_name || h.model || '',
      created_at: h.created_at || '',
    }))
  } catch (e) { if (showErr) msg.value = '加载失败：' + e.message }
}

function guessType(h) {
  const p = (h.file_path || '') + (h.prompt || '')
  if (/\.(png|jpg|jpeg|webp)/i.test(p)) return 'image'
  if (/\.(mp3|wav|ogg|flac)/i.test(p)) return 'music'
  if (/\.(mp4|webm)/i.test(p)) return 'video'
  return 'canvas'
}

async function del(it) {
  if (!confirm('删除这条创作记录？')) return
  try { await delJSON(`/api/vertical/history/${it.id}`); await load() }
  catch (e) { msg.value = '删除失败：' + e.message }
}

load(true)
</script>

<style scoped>
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; background: #FDF2F2; color: #B4443C; }
.tool-row { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }
.search { flex: 1; display: flex; align-items: center; gap: 8px; background: #fff; border: 1px solid var(--line); border-radius: 11px; padding: 9px 13px; color: var(--sub); }
.search input { flex: 1; border: none; outline: none; font-size: 13px; background: transparent; color: var(--ink); }
.total { font-size: 12.5px; color: var(--sub); white-space: nowrap; }
.empty { padding: 34px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13.5px; }

.s-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.scard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 13px 14px; display: flex; flex-direction: column; gap: 7px; }
.s-top { display: flex; align-items: center; justify-content: space-between; }
.s-badge { font-size: 11px; font-weight: 700; background: var(--mode-vertical-bg); color: var(--mode-vertical); border-radius: 20px; padding: 2px 10px; }
.s-time { font-size: 11px; color: var(--sub); }
.s-prompt { font-size: 12.5px; margin: 0; flex: 1; height: 56px; overflow: hidden; line-height: 1.5; word-break: break-all; }
.s-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.s-model { font-size: 11px; color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 12px; cursor: pointer; color: var(--ink); text-decoration: none; }
.mini:hover { border-color: var(--blue); color: var(--blue); }
.howto { margin-top: 18px; background: #F6F8FB; border-radius: 11px; padding: 11px 14px; font-size: 12.5px; color: var(--sub); display: flex; flex-direction: column; gap: 3px; }
.howto b { color: var(--ink); font-size: 12.5px; }

@media (max-width: 1000px) { .s-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 660px) { .s-grid { grid-template-columns: 1fr; } }
</style>
