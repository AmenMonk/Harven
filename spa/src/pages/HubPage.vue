<template>
  <PageShell :title="t('page.hub.t')" icon="globe" :sub="t('page.hub.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadAll(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
      <a href="https://harvenhub.com" target="_blank" rel="noreferrer" class="site-link"><BigButton variant="ghost" :px="14"><HIcon name="globe" :size="15" /> 访问官网</BigButton></a>
    </template>

    <!-- 统计条 -->
    <div class="stat-row" v-if="stats">
      <div class="stat"><b>{{ stats.total || '—' }}</b><span>全部内容</span></div>
      <div v-for="t in typeList.slice(0, 6)" :key="t.id" class="stat" :class="{ clickable: true }" @click="pickType(t.id)">
        <b>{{ t.count }}</b><span>{{ t.name }}</span>
      </div>
    </div>

    <!-- 分类 chips -->
    <div class="chip-row" v-if="stats?.categories?.length">
      <button class="chip" :class="{ on: category === '' }" @click="category = ''; loadList()">全部分类</button>
      <button v-for="c in stats.categories" :key="c.id" class="chip" :class="{ on: category === c.id }" @click="category = c.id; loadList()">
        {{ c.name }} <i>{{ c.item_count }}</i>
      </button>
    </div>

    <!-- 搜索 -->
    <div class="tool-row">
      <div class="search">
        <HIcon name="search" :size="16" />
        <input v-model.trim="q" placeholder="搜索市场内容，回车确认" @keyup.enter="doSearch" />
      </div>
      <select v-model="sort" class="sort" @change="loadList">
        <option value="newest">最新</option>
        <option value="popular">人气</option>
        <option value="rating">评分</option>
        <option value="downloads">下载最多</option>
      </select>
    </div>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>
    <div v-if="sourceTip" class="src-tip">{{ sourceTip }}</div>

    <div v-if="!list.length" class="empty">市场暂时没有匹配的内容（云端不可用时会自动看本地缓存）</div>

    <div class="hub-grid">
      <div v-for="it in list" :key="it.id" class="hcard">
        <div class="h-top">
          <span class="h-type" :class="'t-' + (it.type || 'other')">{{ TYPE_TEXT[it.type] || it.type || '内容' }}</span>
          <b class="h-name" :title="it.name">{{ it.name }}</b>
        </div>
        <p class="h-desc">{{ it.description || '（没有描述）' }}</p>
        <div class="h-foot">
          <span class="h-meta">⬇ {{ it.download_count || 0 }} · ★ {{ it.avg_rating || it.rating || 0 }} · {{ (it.author_id || it.author || '匿名') }}</span>
          <BigButton :px="13" :loading="dlId === it.id" @click="download(it)"><HIcon name="download" :size="14" /> 下载</BigButton>
        </div>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'

const TYPE_TEXT = { skill: '技能', agent: 'Agent', knowledge: '知识包', bundle: 'Bundle', template: '模板', model: '模型', tool: '工具' }

const stats = ref(null)
const list = ref([])
const q = ref('')
const category = ref('')
const sort = ref('newest')
const dlId = ref('')
const msg = ref('')
const msgOk = ref(false)

const typeList = computed(() => {
  const by = stats.value?.by_type || {}
  return Object.entries(by).map(([id, count]) => ({ id, name: TYPE_TEXT[id] || id, count })).sort((a, b) => b.count - a.count)
})
const sourceTip = computed(() => '')

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 5000)
}

function pickType(t) { q.value = ''; category.value = ''; loadList(t) }

async function loadStats() {
  try {
    stats.value = await getJSON('/api/harvenhub-market/stats')
  } catch (_) {}
}

async function loadList(typeOverride) {
  try {
    const type = typeof typeOverride === 'string' ? typeOverride : ''
    const qs = new URLSearchParams({ sort: sort.value, limit: 40 })
    if (category.value) qs.set('category', category.value)
    if (type) qs.set('type', type)
    const j = await getJSON('/api/harvenhub-market/browse?' + qs.toString())
    list.value = j.items || []
  } catch (e) { list.value = [] }
}

async function doSearch() {
  if (!q.value) { loadList(); return }
  try {
    const j = await getJSON('/api/harvenhub-market/search?q=' + encodeURIComponent(q.value) + '&limit=40')
    list.value = j.results || []
  } catch (e) { flash('搜索失败：' + e.message, false) }
}

async function download(it) {
  if (!confirm(`下载并安装「${it.name}」？`)) return
  dlId.value = it.id
  try {
    const j = await postJSON(`/api/harvenhub-market/download/${it.id}`)
    if (j.success) flash(`「${it.name}」已下载并安装成功 ✅`)
    else flash('下载失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('下载失败：' + e.message, false) }
  finally { dlId.value = '' }
}

onMounted(() => { loadStats(); loadList() })
</script>

<style scoped>
.site-link { text-decoration: none; }
.stat-row { display: grid; grid-template-columns: repeat(7, 1fr); gap: 10px; margin-bottom: 13px; }
.stat { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 10px 6px; text-align: center; display: flex; flex-direction: column; gap: 1px; }
.stat.clickable { cursor: pointer; }
.stat.clickable:hover { border-color: var(--blue); }
.stat b { font-size: 17px; }
.stat span { font-size: 11px; color: var(--sub); }

.chip-row { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 12px; }
.chip { border: 1px solid var(--line); background: #fff; border-radius: 20px; padding: 4px 13px; font-size: 12px; cursor: pointer; color: var(--ink); }
.chip i { font-style: normal; color: var(--blue); font-weight: 700; }
.chip.on { border-color: var(--blue); background: #F3F7FE; color: var(--blue); font-weight: 700; }

.tool-row { display: flex; gap: 10px; margin-bottom: 14px; }
.search { flex: 1; display: flex; align-items: center; gap: 8px; background: #fff; border: 1px solid var(--line); border-radius: 11px; padding: 9px 13px; color: var(--sub); }
.search input { flex: 1; border: none; outline: none; font-size: 13px; background: transparent; color: var(--ink); }
.sort { border: 1px solid var(--line); border-radius: 11px; padding: 8px 11px; font-size: 12.5px; background: #fff; color: var(--ink); outline: none; }

.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }
.src-tip { font-size: 11.5px; color: var(--sub); margin-bottom: 10px; }
.empty { padding: 34px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13.5px; }

.hub-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.hcard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 13px 14px; display: flex; flex-direction: column; gap: 7px; }
.h-top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.h-type { font-size: 10.5px; font-weight: 800; padding: 2px 8px; border-radius: 6px; background: var(--c-bg-soft); color: var(--sub); flex-shrink: 0; }
.h-type.t-skill { background: var(--mode-local-bg); color: var(--mode-local); }
.h-type.t-agent { background: var(--mode-cloud-bg); color: var(--mode-cloud); }
.h-type.t-knowledge { background: var(--mode-vertical-bg); color: var(--mode-vertical); }
.h-name { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.h-desc { font-size: 12px; color: var(--sub); margin: 0; flex: 1; height: 54px; overflow: hidden; line-height: 1.5; }
.h-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.h-meta { font-size: 11.5px; color: var(--sub); }

@media (max-width: 1100px) { .stat-row { grid-template-columns: repeat(4, 1fr); } .hub-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 700px) { .hub-grid { grid-template-columns: 1fr; } }
</style>
