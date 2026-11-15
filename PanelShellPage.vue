<template>
  <div class="pshell3">
    <!-- 形态工具条 -->
    <div class="bar">
      <button class="back" @click="$router.push('/panels')"><HIcon name="x" :size="15" /></button>
      <span class="p-ic">{{ panel?.icon && panel.icon.length <= 3 ? panel.icon : '🧩' }}</span>
      <b class="p-name">{{ panel?.name || panelId }}</b>
      <span class="p-desc">{{ panel?.description || '' }}</span>
      <div class="modes">
        <button :class="{ on: mode === 'dock' }" title="停靠在页面里" @click="mode = 'dock'"><HIcon name="box" :size="15" /> 停靠</button>
        <button :class="{ on: mode === 'float' }" title="浮在小窗，可以拖动、缩放" @click="mode = 'float'"><HIcon name="grid" :size="15" /> 浮窗</button>
        <button :class="{ on: mode === 'full' }" title="占满整屏" @click="mode = 'full'"><HIcon name="cube" :size="15" /> 全屏</button>
      </div>
      <div v-if="mode === 'float'" class="sizes">
        <button v-for="s in SIZES" :key="s.w" :class="{ on: size.w === s.w }" @click="size = { ...s }">{{ s.label }}</button>
      </div>
      <a class="newtab" :href="iframeSrc" target="_blank" title="在新标签页打开"><HIcon name="globe" :size="15" /></a>
      <!-- ★ 2026-10-20 面板任务/目标入口（定版拍板补装）：下任务/设目标都在这 -->
      <button class="sharebtn" title="在此面板下任务" @click="taskFormOpen = !taskFormOpen">📋 任务</button>
      <button class="sharebtn" title="为此面板设目标" @click="setGoal">🎯 目标</button>
      <!-- ★ 2026-09-18 #750 分类包分享：一键上传 HarvenHub（只传字段定义，不带私人数据） -->
      <button class="sharebtn" :disabled="sharing" title="把这个分类分享到 HarvenHub 市场供他人安装" @click="share">
        {{ sharing ? '上传中…' : '📤 分享' }}
      </button>
    </div>

    <!-- ★ 2026-10-20 面板下任务小表单 -->
    <div v-if="taskFormOpen" class="task-form">
      <input v-model.trim="taskTitle" autofocus placeholder="要做什么？如：周末清点冰箱库存" class="tf-input" />
      <input v-model="taskDue" type="date" class="tf-date" />
      <button class="tf-btn" :disabled="taskBusy" @click="createPanelTask">{{ taskBusy ? '…' : '建任务' }}</button>
      <span v-if="taskMsg" class="tf-msg">{{ taskMsg }}</span>
    </div>

    <!-- ★ 2026-10-20 目标中心（所有面板通用）：目标+过程可视化+和哈文聊目标 -->
    <div class="goal-center">
      <div class="gc-head">
        <b @click="gcOpen = !gcOpen" class="gc-toggle">🎯 目标中心 <span class="gc-arrow">{{ gcOpen ? '▼' : '▶' }}</span></b>
        <span class="gc-sum" v-if="panelGoals.length">{{ panelGoals.length }} 个进行中</span>
        <span class="gc-spacer" />
        <button class="gc-btn" @click="chatAboutGoal">💬 和哈文聊目标</button>
        <button class="gc-btn" @click="setGoal">+ 设目标</button>
      </div>
      <div v-show="gcOpen" class="gc-body">
        <!-- ★ 2026-10-21 修「连字都打不上」：目标中心直接内嵌输入框，打字→就地 AI 拆解建档，不出页面 -->
        <div class="gc-input-row">
          <textarea ref="gcInputEl" v-model="gcWish" rows="2" class="gc-input"
            placeholder="直接打字说目标，如：三个月瘦 5 公斤，每周运动 3 次 —— 打完点「AI 拆解建档」"
            @keydown.enter.exact.prevent="gcCreate"></textarea>
          <div class="gc-input-ops">
            <button class="gc-btn gc-btn-primary" :disabled="gcBusy || !gcWish.trim()" @click="gcCreate">{{ gcBusy ? '拆解中…' : '🎯 AI 拆解建档' }}</button>
            <button class="gc-btn" @click="chatAboutGoal">💬 去聊天细聊</button>
            <span class="gc-hint">Enter 建档 · 拆解后可改可删，日常盯催全自动（0 token）</span>
          </div>
        </div>
        <div v-if="gcDraft" class="gc-draft">
          <b>拆解草稿（可改）：</b>
          <div v-for="(n, i) in gcDraft" :key="i" class="gc-draft-node">
            <input v-model.trim="n.name" class="gdn-name" />
            <input v-model="n.due" type="date" class="gdn-due" />
            <button class="mini" @click="gcDraft.splice(i, 1)">✕</button>
          </div>
          <div class="gc-input-ops">
            <button class="gc-btn gc-btn-primary" :disabled="gcSaveBusy" @click="gcSaveNow">{{ gcSaveBusy ? '…' : '✅ 就这样，建档' }}</button>
            <button class="gc-btn" @click="gcDraft = null">放弃</button>
          </div>
        </div>
        <div v-if="!panelGoals.length && !gcDraft" class="gc-empty">还没有目标。上面直接打字就行。</div>
        <div v-for="g in panelGoals" :key="g.id" class="pg-card" @click="$router.push('/goals')">
          <span>{{ g.light }}</span>
          <b>{{ g.title }}</b>
          <div class="pg-bar"><i :style="{ width: g.progress + '%' }" /></div>
          <span class="pg-meta">{{ g.done }}/{{ g.total }}<template v-if="g.next_node"> · {{ g.next_node.name }} {{ g.next_node.due }}</template></span>
        </div>
        <div v-if="panelCtx" class="gc-ctx">
          📊 本模块近 7 天 {{ panelCtx.recent7 }} 条记录<template v-if="panelCtx.today !== undefined">，今天 {{ panelCtx.today }} 条</template>——AI 出建议时会参考这些数据
        </div>
      </div>
    </div>

    <!-- 停靠形态：iframe 内嵌在当前页 -->
    <div v-if="mode === 'dock'" class="dock-wrap">
      <iframe v-if="iframeSrc" :src="iframeSrc" class="frame" :title="panelId" />
      <div v-else class="empty">面板不存在：{{ panelId }}</div>
    </div>

    <!-- 全屏形态：占满可视区 -->
    <div v-if="mode === 'full'" class="full-wrap">
      <iframe v-if="iframeSrc" :src="iframeSrc" class="frame" :title="panelId" />
    </div>

    <!-- 浮窗形态：可拖动 + 可缩放 -->
    <div v-if="mode === 'float'" class="float-wrap">
      <div class="fwin" :style="fwinStyle" ref="fwin">
        <div class="fbar" @pointerdown="dragStart">
          <span class="ftitle">{{ panel?.name || panelId }} · 浮窗</span>
          <button class="fbtn" @click="mode = 'dock'" title="收回停靠">✕</button>
        </div>
        <div class="fresize" @pointerdown="resizeStart" />
        <iframe v-if="iframeSrc" :src="iframeSrc" class="frame fframe" :title="panelId" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const router = useRouter()
import HIcon from '../components/HIcon.vue'
import { getJSON } from '../api.js'

const SIZES = [
  { label: '小', w: 420, h: 540 },
  { label: '中', w: 640, h: 640 },
  { label: '大', w: 880, h: 720 },
]

const route = useRoute()
const panelId = computed(() => route.params.id)
const panel = ref(null)
const mode = ref('dock')
const size = ref({ ...SIZES[1] })
const pos = reactive({ x: 120, y: 80 })

const iframeSrc = computed(() => (panelId.value ? `/panel/${panelId.value}` : ''))

const fwinStyle = computed(() => ({
  left: pos.x + 'px',
  top: pos.y + 'px',
  width: size.value.w + 'px',
  height: size.value.h + 'px',
}))

function dragStart(e) {
  if (e.target.closest('.fbtn')) return
  const sx = e.clientX - pos.x, sy = e.clientY - pos.y
  const move = ev => {
    pos.x = Math.max(0, Math.min(window.innerWidth - 80, ev.clientX - sx))
    pos.y = Math.max(0, Math.min(window.innerHeight - 60, ev.clientY - sy))
  }
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

function resizeStart(e) {
  e.preventDefault()
  const sw = size.value.w - e.clientX, sh = size.value.h - e.clientY
  const move = ev => {
    size.value.w = Math.max(320, Math.min(window.innerWidth - pos.x, ev.clientX + sw))
    size.value.h = Math.max(260, Math.min(window.innerHeight - pos.y, ev.clientY + sh))
  }
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

onMounted(async () => {
  try {
    const j = await getJSON('/api/panels')
    panel.value = (j.panels || []).find(p => p.id === panelId.value) || null
  } catch (_) {}
})

/* ── ★ 2026-09-18 #750 分类包分享到 HarvenHub ── */
import { postJSON } from '../api.js'
const sharing = ref(false)
async function share() {
  if (!panelId.value || sharing.value) return
  const name = panel.value?.name || panelId.value
  if (!confirm(`把「${name}」作为分类包上传 HarvenHub，供其他用户安装？\n\n只上传分类的字段定义和界面模板，不含你记录的任何私人数据。`)) return
  sharing.value = true
  try {
    const j = await postJSON('/api/assist/share-to-hub', { panel_id: panelId.value })
    if (j.success) alert(`✅ 「${name}」已分享到 HarvenHub！审核通过后大家都能搜到安装。`)
    else alert('❌ 分享失败：' + (j.error || '未知错误'))
  } catch (e) { alert('❌ 分享失败：' + e.message) }
  finally { sharing.value = false }
}

/* ── ★ 2026-10-20 目标中心（所有面板通用） ── */
const taskFormOpen = ref(false)
const taskTitle = ref('')
const taskDue = ref('')
const taskBusy = ref(false)
const taskMsg = ref('')
const panelGoals = ref([])
const panelCtx = ref(null)
const gcOpen = ref(true)

async function loadPanelGoals() {
  try {
    const j = await getJSON(`/api/goals?panel_id=${encodeURIComponent(panelId.value)}`)
    panelGoals.value = (j.goals || []).filter(g => g.status === 'active').slice(0, 5)
  } catch { panelGoals.value = [] }
  try {
    const c = await getJSON(`/api/panels/${encodeURIComponent(panelId.value)}/context`)
    if (c.success) panelCtx.value = c
  } catch { panelCtx.value = null }
}

/* ★ 2026-10-21 目标中心内嵌输入：打字→AI拆解→改草稿→就地建档（不出页面） */
const gcWish = ref('')
const gcBusy = ref(false)
const gcDraft = ref(null)
const gcSaveBusy = ref(false)
const gcInputEl = ref(null)

async function gcCreate() {
  const wish = gcWish.value.trim()
  if (!wish || gcBusy.value) return
  gcBusy.value = true
  try {
    const r = await fetch('/api/goals/parse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wish }),
    }).then(x => x.json())
    if (r.nodes && r.nodes.length) {
      gcDraft.value = r.nodes
    } else {
      /* 兜底：单节点草稿，不让用户卡死 */
      const d = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)
      gcDraft.value = [{ name: wish.slice(0, 24), due: d, metric: 'manual', target: 1 }]
    }
  } catch (_) {
    const d = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)
    gcDraft.value = [{ name: wish.slice(0, 24), due: d, metric: 'manual', target: 1 }]
  } finally { gcBusy.value = false }
}

async function gcSaveNow() {
  if (!gcDraft.value || !gcDraft.value.length || gcSaveBusy.value) return
  gcSaveBusy.value = true
  try {
    const r = await fetch('/api/goals', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: gcWish.value.trim(), nodes: gcDraft.value,
                             panel_id: panelId.value, token_spent: 3000 }),
    }).then(x => x.json())
    if (r.ok) {
      gcWish.value = ''; gcDraft.value = null
      await loadPanelGoals()
    }
  } catch (_) {} finally { gcSaveBusy.value = false }
}

/* 和哈文聊目标：带上本模块数据摘要，让 AI 拿事实出建议（饮食/休息/运动/计划） */
async function chatAboutGoal() {
  const name = panel.value?.name || panelId.value
  const c = panelCtx.value || {}
  const goalsTxt = panelGoals.value.map(g => `${g.title}（进度${g.done}/${g.total}）`).join('；') || '还没设目标'
  const briefTxt = (c.brief || []).slice(0, 5).join(' / ') || '暂无记录'
  const q = `我在用「${name}」模块。当前目标：${goalsTxt}。`
    + `近7天记录 ${c.recent7 ?? 0} 条（含今天 ${c.today ?? 0} 条），最近：${briefTxt}。`
    + `请基于这些真实数据和我一起定/调目标，并给出具体可执行的建议（该记哪些数据、节奏怎么安排、怎么判断今天达标）。`
  router.push({ path: '/', query: { prefill: q, autosend: 1 } })
}

async function createPanelTask() {
  if (!taskTitle.value) { taskMsg.value = '先写要做什么'; return }
  taskBusy.value = true; taskMsg.value = ''
  try {
    const j = await postJSON('/api/panel_tasks', {
      panel_id: panelId.value, title: taskTitle.value, due: taskDue.value,
    })
    if (j.ok) {
      taskMsg.value = '✅ 已建任务（可在任务中枢看到，带本面板来源标签）'
      taskTitle.value = ''; taskDue.value = ''
      setTimeout(() => { taskFormOpen.value = false; taskMsg.value = '' }, 2200)
    } else taskMsg.value = j.message || '创建失败'
  } catch (e) { taskMsg.value = '网络错误' }
  taskBusy.value = false
}

function setGoal() {
  router.push({ path: '/goals', query: { panel_id: panelId.value, new: '1' } })
}

onMounted(loadPanelGoals)
</script>

<style scoped>
.pshell3 { height: 100%; display: flex; flex-direction: column; overflow: hidden; position: relative; }
.bar { display: flex; align-items: center; gap: 10px; padding: 12px 18px; border-bottom: 1px solid var(--line); background: #fff; }
.back { width: 30px; height: 30px; border-radius: 9px; border: 1px solid var(--line); background: #fff; color: var(--sub); cursor: pointer; display: flex; align-items: center; justify-content: center; }
.back:hover { color: var(--blue); border-color: var(--blue); }
.p-ic { font-size: 20px; }
.p-name { font-size: 15px; }
.p-desc { font-size: 12px; color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }
.modes { margin-left: auto; display: flex; gap: 6px; background: var(--c-bg-soft); border-radius: 10px; padding: 3px; }
.modes button { display: flex; align-items: center; gap: 5px; border: none; background: transparent; padding: 6px 12px; border-radius: 8px; font-size: 12.5px; cursor: pointer; color: var(--sub); }
.modes button.on { background: #fff; color: var(--ink); font-weight: 600; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
/* ★ D3 2026-09-23 手机适配：顶栏两行化（此前 8 个元素挤一行，标题被压成逐字竖排） */
@media (max-width: 700px) {
  .bar { flex-wrap: wrap; row-gap: 8px; padding: 10px 12px; }
  .p-desc { display: none; }
  .modes { margin-left: 0; flex: 1 1 100%; order: 10; }
  .modes button { flex: 1; justify-content: center; }
  .sharebtn { margin-left: auto; }
}
.sizes { display: flex; gap: 4px; }
.sizes button { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 10px; cursor: pointer; color: var(--sub); }
.sizes button.on { border-color: var(--blue); color: var(--blue); font-weight: 700; }
.newtab { display: flex; align-items: center; color: var(--sub); padding: 6px; border-radius: 8px; }
.newtab:hover { color: var(--blue); background: var(--c-bg-soft); }
/* ★ #750 分享按钮 */
.sharebtn { border: 1px solid var(--line); background: #fff; border-radius: 9px; font-size: 12.5px; padding: 6px 12px; cursor: pointer; color: var(--sub); }
.sharebtn:hover:not(:disabled) { color: #1a9a5c; border-color: #1a9a5c; }
.sharebtn:disabled { opacity: .55; cursor: default; }

.dock-wrap { flex: 1; padding: 14px 18px 18px; min-height: 0; }
.frame { width: 100%; height: 100%; border: 1px solid var(--line); border-radius: 13px; background: #fff; }
.full-wrap { position: fixed; inset: 0; z-index: 60; background: var(--c-bg); }
.full-wrap .frame { border: none; border-radius: 0; }

.float-wrap { position: fixed; inset: 0; z-index: 55; pointer-events: none; }
.fwin { position: absolute; display: flex; flex-direction: column; background: #fff; border-radius: 13px; border: 1px solid var(--line); box-shadow: 0 12px 40px rgba(30, 40, 60, .22); pointer-events: auto; overflow: hidden; }
.fbar { display: flex; align-items: center; padding: 8px 10px 8px 14px; background: var(--c-bg-soft); cursor: move; user-select: none; }
.ftitle { font-size: 12.5px; font-weight: 600; flex: 1; }
.fbtn { border: none; background: transparent; cursor: pointer; color: var(--sub); font-size: 13px; padding: 2px 6px; border-radius: 6px; }
.fbtn:hover { color: var(--red); background: #fff; }
.fframe { border: none; border-radius: 0; flex: 1; }
.fresize { position: absolute; right: 0; bottom: 0; width: 18px; height: 18px; cursor: nwse-resize; z-index: 2;
  background: linear-gradient(135deg, transparent 50%, var(--line) 50%); }
.empty { padding: 40px; text-align: center; color: var(--sub); }

/* ★ 2026-10-20 面板任务/目标入口 */
.task-form { display: flex; gap: 8px; align-items: center; padding: 8px 12px; background: var(--c-bg-soft, #f7f8fa); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.tf-input { flex: 1; min-width: 200px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; }
.tf-date { padding: 5px 8px; border: 1px solid var(--line); border-radius: 8px; font-size: 12px; }
.tf-btn { padding: 6px 14px; border-radius: 8px; border: none; background: var(--blue); color: #fff; font-size: 13px; cursor: pointer; }
.tf-msg { font-size: 12px; color: var(--sub); }
.panel-goals { display: flex; gap: 8px; padding: 8px 12px; overflow-x: auto; border-bottom: 1px solid var(--line); }
.pg-card { flex: none; min-width: 190px; border: 1px solid var(--line); border-radius: 10px; padding: 7px 10px; cursor: pointer; background: #fff; display: grid; grid-template-columns: auto 1fr auto; gap: 2px 6px; align-items: center; font-size: 12px; }
.pg-card b { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pg-bar { grid-column: 1 / -1; height: 4px; border-radius: 2px; background: var(--c-bg-soft); overflow: hidden; }
.pg-bar i { display: block; height: 100%; background: #22a06b; }
.pg-meta { color: var(--sub); }

/* ★ 2026-10-20 目标中心 */
.goal-center { border-bottom: 1px solid var(--line); background: var(--c-bg-soft, #fafbfc); }
.gc-head { display: flex; align-items: center; gap: 10px; padding: 8px 14px; }
.gc-toggle { font-size: 13px; cursor: pointer; }
.gc-arrow { font-size: 10px; color: var(--sub); }
.gc-sum { font-size: 11.5px; color: var(--sub); }
.gc-spacer { flex: 1; }
.gc-btn { font-size: 12px; padding: 4px 11px; border-radius: 12px; border: 1px solid var(--line); background: #fff; cursor: pointer; color: var(--ink); }
.gc-btn:hover { border-color: var(--blue); color: var(--blue); }
.gc-body { padding: 0 14px 10px; display: flex; flex-direction: column; gap: 8px; }
.gc-empty { font-size: 12px; color: var(--sub); padding: 6px 0; }
.gc-ctx { font-size: 11.5px; color: var(--sub); }
.gc-input-row { display: flex; flex-direction: column; gap: 6px; }
.gc-input { width: 100%; box-sizing: border-box; border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; font-size: 13px; font-family: inherit; resize: vertical; background: #fff; color: var(--ink); }
.gc-input:focus { outline: none; border-color: var(--blue); }
.gc-input-ops { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.gc-btn-primary { background: var(--blue); color: #fff; border-color: var(--blue); }
.gc-btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.gc-hint { font-size: 11px; color: var(--sub); }
.gc-draft { border: 1px dashed var(--blue); border-radius: 10px; padding: 8px 10px; font-size: 12.5px; }
.gc-draft-node { display: flex; gap: 6px; margin: 5px 0; align-items: center; }
.gdn-name { flex: 1; padding: 5px 8px; border: 1px solid var(--line); border-radius: 8px; font-size: 12.5px; }
.gdn-due { padding: 4px 6px; border: 1px solid var(--line); border-radius: 8px; font-size: 12px; }
</style>
