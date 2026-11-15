<template>
  <PageShell title="🎯 目标" icon="flag" sub="接愿望，不是接命令——AI 拆计划、日常盯催、到点审核">
    <template #actions>
      <BigButton :px="14" @click="openWizard">＋ 新目标</BigButton>
    </template>

    <div class="goal-tabs">
      <button :class="{ on: tab === 'active' }" @click="switchTab('active')">进行中</button>
      <button :class="{ on: tab === 'done' }" @click="switchTab('done')">已完成</button>
    </div>

    <!-- 进度卡墙 -->
    <div v-if="cards.length" class="goal-wall">
      <div v-for="g in cards" :key="g.id" class="goal-card" @click="openDetail(g.id)">
        <div class="gc-head">
          <b class="gc-title">{{ g.title }}</b>
          <span class="gc-light">{{ g.light }}</span>
        </div>
        <div class="gc-bar"><div class="gc-bar-in" :style="{ width: g.progress + '%' }"></div></div>
        <div class="gc-meta">
          <span>{{ g.done }}/{{ g.total }} 节点</span>
          <span v-if="g.next_node">下一节点：{{ g.next_node.name }}（{{ g.next_node.due }}）</span>
          <span class="gc-token">已花 {{ g.token_spent }} token</span>
        </div>
      </div>
    </div>
    <div v-else class="goal-empty">
      <p>还没有目标。说一个愿望，比如「让家人吃得更健康」，哈文帮你拆成节点计划。</p>
      <BigButton :px="14" @click="openWizard">＋ 从一个愿望开始</BigButton>
    </div>

    <!-- 详情弹层 -->
    <div v-if="detail" class="mask" @click.self="detail = null">
      <div class="dialog">
        <div class="d-head">
          <b>{{ detail.title }}</b>
          <button class="d-close" @click="detail = null">✕</button>
        </div>
        <div class="d-meta">状态：{{ statusLabel(detail.status) }} · 已花 {{ detail.token_spent }} token</div>
        <div class="d-timeline">
          <div v-for="(n, i) in detail.nodes" :key="i" class="d-node" :class="n.status">
            <div class="dn-dot">{{ n.status === 'done' ? '✅' : n.status === 'missed' ? '❌' : '⏳' }}</div>
            <div class="dn-body">
              <b>{{ n.name }}</b>
              <span class="dn-due">截止 {{ n.due }} · 硬指标 {{ n.metric_now }}/{{ n.metric_target }}（{{ metricLabel(n.metric_type) }}）</span>
              <div class="dn-ops" v-if="detail.status === 'active'">
                <button v-if="n.status !== 'done'" @click="act('node_done', i)">✅ 完成</button>
                <button @click="quickDefer(i)">⏩ 顺延7天</button>
                <button @click="quickLower(i)">⬇ 降目标</button>
                <button v-if="n.status === 'pending'" @click="act('node_missed', i)">❌ 标记未达</button>
              </div>
            </div>
          </div>
        </div>
        <div class="d-ops">
          <button v-if="detail.status === 'active'" @click="act('pause')">⏸ 暂停</button>
          <button v-if="detail.status === 'paused'" @click="act('resume')">▶ 恢复</button>
          <button v-if="detail.status !== 'done'" @click="act('abandon')">🏳 放弃</button>
          <button class="d-del" @click="act('delete')">🗑 删除</button>
        </div>
      </div>
    </div>

    <!-- 设定向导（三步弹窗） -->
    <div v-if="wizard" class="mask" @click.self="wizard = false">
      <div class="dialog">
        <div class="d-head"><b>🎯 新目标（{{ wizStep }}/3）</b><button class="d-close" @click="wizard = false">✕</button></div>

        <template v-if="wizStep === 1">
          <div class="wiz-body">
            <p class="wiz-tip">用一句话说愿望，比如「让家人吃得更健康」「一个月存 3000 块」</p>
            <textarea v-model.trim="wish" rows="3" placeholder="输入你的愿望…"></textarea>
          </div>
          <div class="d-ops"><BigButton :px="14" :loading="wizBusy" @click="aiSplit">🧠 AI 拆解</BigButton></div>
        </template>

        <template v-if="wizStep === 2">
          <div class="wiz-body">
            <p class="wiz-tip">第 2 步 / 3：AI 拆解草稿——每行都能改、能删、能加，改完点右下角「下一步 →」</p>
            <div v-for="(n, i) in wizNodes" :key="i" class="wiz-node">
              <input v-model.trim="n.name" placeholder="节点名" class="wn-name" />
              <input v-model="n.due" type="date" class="wn-due" />
              <select v-model="n.metric" class="wn-metric">
                <option value="manual">手动确认</option>
                <option value="task_done">关联任务完成数</option>
                    <option value="spend_sum">💰 花销上限（记账金额求和）</option>
                    <option value="daily_spend_sum">📅 日花销上限（当天记账求和）</option>
                <option value="record_count">面板记录数</option>
              </select>
              <input v-model.number="n.target" type="number" min="1" class="wn-target" title="目标值" />
              <button class="wn-del" @click="wizNodes.splice(i, 1)">✕</button>
            </div>
            <button class="wn-add" @click="wizNodes.push({ name: '', due: '', metric: 'manual', target: 1 })">＋ 加节点</button>
          </div>
          <div class="d-ops">
            <BigButton type="ghost" :px="14" @click="wizStep = 1">← 上一步</BigButton>
            <BigButton type="primary" :px="14" @click="wizStep = 3">下一步 →</BigButton>
          </div>
        </template>

        <template v-if="wizStep === 3">
          <div class="wiz-body">
            <p class="wiz-tip">第 3 步 / 3：确认没问题就点右下角「✅ 建档」。日常盯催 0 token（本地查询）。</p>
            <div class="wiz-confirm">
              <b>{{ wish }}</b>
              <div v-for="(n, i) in wizNodes" :key="i">{{ i + 1 }}. {{ n.name || '（未命名）' }} → {{ n.due || '未设日期' }}</div>
              <div class="wc-token">预计 token：拆解已花 {{ wizTokens }}，节点审核每次约 2k</div>
            </div>
          </div>
          <div class="d-ops">
            <BigButton type="ghost" :px="14" @click="wizStep = 2">← 上一步</BigButton>
            <BigButton type="primary" :px="14" :disabled="wizBusy" @click="createNow">{{ wizBusy ? "建档中…" : "✅ 建档" }}</BigButton>
          </div>
        </template>
      </div>
    </div>

    <div v-if="pageMsg" class="tip" style="margin-top:10px">{{ pageMsg }}</div>
  </PageShell>
</template>

<script setup>
/* ★ 2026-10-20 第二批2.2 Goals 目标页（哈文开发总表） */
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import PageShell from '../components/PageShell.vue'
import BigButton from '../components/BigButton.vue'

const route = useRoute()
/* ★ 2026-10-20 面板入口带入：?panel_id=xx&new=1 → 向导自动挂本面板 */
const fromPanelId = ref('')

const tab = ref('active')
const cards = ref([])
const detail = ref(null)
const wizard = ref(false)
const wizStep = ref(1)
const wish = ref('')
const wizNodes = ref([])
const wizBusy = ref(false)
const wizTokens = ref(0)
const pageMsg = ref('')

function statusLabel(s) {
  return { active: '🟢 进行中', paused: '⏸ 已暂停', done: '✅ 已完成', abandoned: '🏳 已放弃' }[s] || s
}
function metricLabel(m) {
  return { manual: '手动', task_done: '任务', record_count: '记录', photo_count: '照片', spend_sum: '花销上限', daily_spend_sum: '日预算' }[m] || m
}

async function loadCards() {
  try {
    const r = await fetch(`/api/goals${tab.value === 'done' ? '?status=done' : ''}`)
    const j = await r.json()
    cards.value = j.goals || []
  } catch { cards.value = [] }
}
function switchTab(t) { tab.value = t; loadCards() }

async function openDetail(id) {
  try {
    const r = await fetch(`/api/goals/${id}`)
    if (r.ok) detail.value = await r.json()
  } catch {}
}

async function act(action, nodeIdx = -1, extra = {}) {
  if (action === 'delete' && !confirm('确定删除该目标？（节点任务会解除关联，不会删除任务本身）')) return
  try {
    const r = await fetch(`/api/goals/${detail.value.id}/action`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, node_idx: nodeIdx, ...extra }),
    })
    const j = await r.json()
    pageMsg.value = j.ok ? '已更新' : (j.message || '操作失败')
    if (j.ok) { await openDetail(detail.value.id); await loadCards() }
  } catch { pageMsg.value = '网络错误' }
  setTimeout(() => { pageMsg.value = '' }, 2500)
}

function quickDefer(i) {
  const d = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)
  act('defer', i, { new_due: d })
}
function quickLower(i) {
  const n = detail.value.nodes[i]
  const nt = Math.max(1, Math.floor((n.metric_target || 1) * 0.7))
  act('lower', i, { new_target: nt })
}

function openWizard() {
  wizard.value = true; wizStep.value = 1; wish.value = ''
  wizNodes.value = []; wizTokens.value = 0
}

async function aiSplit() {
  if (!wish.value) { pageMsg.value = '先输入愿望'; return }
  wizBusy.value = true
  try {
    /* AI 拆解走聊天引擎（外脑一次性 ~3k）；此处给本地兜底模板+提示外脑入口 */
    const r = await fetch('/api/goals/parse', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ wish: wish.value }),
    })
    if (r.ok) {
      const j = await r.json()
      wizNodes.value = j.nodes || []
      wizTokens.value = j.token_spent || 3000
    }
    if (!wizNodes.value.length) {
      /* 兜底：单节点草稿 */
      const d = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)
      wizNodes.value = [{ name: wish.value.slice(0, 20), due: d, metric: 'manual', target: 1 }]
      wizTokens.value = 0
    }
    wizStep.value = 2
  } catch {
    const d = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10)
    wizNodes.value = [{ name: wish.value.slice(0, 20), due: d, metric: 'manual', target: 1 }]
    wizStep.value = 2
  } finally { wizBusy.value = false }
}

async function createNow() {
  wizBusy.value = true
  try {
    const r = await fetch('/api/goals', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: wish.value, nodes: wizNodes.value,
                             token_spent: wizTokens.value, panel_id: fromPanelId.value }),
    })
    const j = await r.json()
    if (j.ok) { wizard.value = false; await loadCards(); pageMsg.value = '目标已建立 🎯' }
    else pageMsg.value = j.message || '建档失败'
  } catch { pageMsg.value = '网络错误' }
  wizBusy.value = false
  setTimeout(() => { pageMsg.value = '' }, 2500)
}

onMounted(() => {
  if (route.query.panel_id) {
    fromPanelId.value = String(route.query.panel_id)
    wish.value = `（挂接面板：${fromPanelId.value}）`
    if (route.query.new === '1') openWizard()
  }
  loadCards()
})
</script>

<style scoped>
.goal-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.goal-tabs button { padding: 6px 16px; border-radius: 18px; border: 1px solid var(--line, #ddd); background: transparent; cursor: pointer; font-size: 13px; }
.goal-tabs button.on { background: var(--pri, #4a7dff); color: #fff; border-color: var(--pri, #4a7dff); }
.goal-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.goal-card { border: 1px solid var(--line, #e5e5e5); border-radius: 14px; padding: 14px; cursor: pointer; background: var(--bg2, #fff); transition: .15s; }
.goal-card:hover { border-color: var(--pri, #4a7dff); }
.gc-head { display: flex; justify-content: space-between; align-items: center; }
.gc-title { font-size: 14px; }
.gc-bar { height: 8px; border-radius: 4px; background: var(--line, #eee); margin: 10px 0 8px; overflow: hidden; }
.gc-bar-in { height: 100%; border-radius: 4px; background: linear-gradient(90deg, #22a06b, #4ad0a0); transition: width .3s; }
.gc-meta { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; color: var(--tx2, #999); }
.gc-token { margin-left: auto; color: var(--pri, #4a7dff); }
.goal-empty { text-align: center; padding: 48px 0; color: var(--tx2, #888); }
.mask { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.dialog { background: var(--bg, #fff); border-radius: 16px; width: min(620px, 92vw); max-height: 82vh; overflow: auto; padding: 18px; }
.d-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.d-close { border: none; background: transparent; font-size: 16px; cursor: pointer; }
.d-meta { font-size: 12px; color: var(--tx2, #888); margin-bottom: 12px; }
.d-node { display: flex; gap: 10px; padding: 10px 0; border-bottom: 1px dashed var(--line, #eee); }
.dn-dot { font-size: 18px; }
.dn-body b { font-size: 14px; display: block; }
.dn-due { font-size: 12px; color: var(--tx2, #999); }
.dn-ops { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.dn-ops button { font-size: 12px; padding: 3px 10px; border-radius: 12px; border: 1px solid var(--line, #ddd); background: transparent; cursor: pointer; }
.d-ops { display: flex; gap: 8px; margin-top: 14px; justify-content: flex-end; }
.d-ops button { font-size: 13px; padding: 6px 14px; border-radius: 10px; border: 1px solid var(--line, #ddd); background: transparent; cursor: pointer; }
.d-del { color: #d0443c; }
.wiz-tip { font-size: 13px; color: var(--tx2, #888); margin-bottom: 8px; }
.wiz-body textarea, .wiz-body input { width: 100%; box-sizing: border-box; }
.wiz-body textarea { padding: 10px; border-radius: 10px; border: 1px solid var(--line, #ddd); font-size: 14px; resize: vertical; }
.wiz-node { display: flex; gap: 6px; margin-bottom: 8px; align-items: center; }
.wn-name { flex: 1; padding: 6px 8px; border-radius: 8px; border: 1px solid var(--line, #ddd); font-size: 13px; }
.wn-due, .wn-metric, .wn-target { padding: 6px; border-radius: 8px; border: 1px solid var(--line, #ddd); font-size: 12px; }
.wn-target { width: 60px; }
.wn-del { border: none; background: transparent; cursor: pointer; color: #d0443c; }
.wn-add { font-size: 13px; padding: 6px 14px; border-radius: 10px; border: 1px dashed var(--line, #ccc); background: transparent; cursor: pointer; }
.wiz-confirm { border: 1px solid var(--line, #eee); border-radius: 12px; padding: 12px; font-size: 13px; line-height: 1.9; }
.wc-token { color: var(--pri, #4a7dff); font-size: 12px; }
</style>
