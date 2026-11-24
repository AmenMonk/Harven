<template>
  <PageShell title="模型组合工坊" icon="grid" sub="组合即模型：几个模型串成一条链，做好后像 HarvenEye 一样被任何面板选用">
    <template #actions>
      <BigButton v-if="mode !== 'list'" type="ghost" :disabled="false" @click="backToList"><HIcon name="x" :size="16" /> 返回列表</BigButton>
      <BigButton v-else type="ghost" @click="newChain"><HIcon name="plus" :size="16" /> 新建组合</BigButton>
      <BigButton type="ghost" @click="loadAll"><HIcon name="dots" :size="16" /> 刷新</BigButton>
    </template>

    <div v-if="err" class="err-tip">⚠️ {{ err }}</div>
    <div v-if="msg" class="ok-tip">{{ msg }}</div>

    <!-- ═══ 列表区 ═══ -->
    <div v-if="mode === 'list'">
      <div class="quick-filter">
        <button :class="{ on: tab === 'all' }" @click="tab = 'all'">全部</button>
        <button :class="{ on: tab === 'mine' }" @click="tab = 'mine'">我的组合</button>
        <button :class="{ on: tab === 'builtin' }" @click="tab = 'builtin'">内置组合</button>
      </div>
      <div class="chain-grid">
        <div v-for="c in filtered" :key="c.id" class="chain-card">
          <div class="cc-head">
            <b class="cc-name">{{ c.name }}</b>
            <span class="pill" :class="c.origin === 'builtin' ? 'ok' : 'soft'">{{ originLabel(c.origin) }}</span>
            <span class="pill" :class="c.identity === 'pure_local' ? 'ok' : 'warn'">{{ c.identity === 'pure_local' ? '纯本地' : '混合云' }}</span>
            <span v-if="!c.enabled" class="pill warn">已停用</span>
          </div>
          <p class="cc-target">{{ c.target || '（没写目标描述——它是组合在能力表和 Hub 上的自我介绍）' }}</p>
          <div class="cc-tags">
            <span v-for="cap in c.capabilities" :key="cap" class="tag">{{ capLabel(cap) }}</span>
            <span class="tag dim">v{{ c.version }}</span>
          </div>
          <div class="cc-steps">
            <span v-for="(s, i) in c.steps" :key="s.id" class="step-chip">
              {{ i + 1 }}. {{ s.name }}<i v-if="s.model" class="step-model">{{ shortModel(s.model) }}</i>
            </span>
          </div>
          <div class="cc-foot">
            <span class="cc-last" :class="lastClass(c)">
              {{ lastText(c) }}
            </span>
            <div class="cc-actions">
              <button class="mini" @click="openEdit(c)">{{ c.builtin_protected ? '🔍 查看' : '✏️ 编辑' }}</button>
              <button class="mini" @click="openRun(c)">▶ 试跑</button>
              <button class="mini" @click="openLogs(c)">📋 物流单</button>
              <button v-if="c.builtin_protected" class="mini" @click="forkChain(c)">🍴 Fork 另存</button>
              <button v-else class="mini danger" @click="removeChain(c)">🗑 删除</button>
            </div>
          </div>
        </div>
        <div v-if="!filtered.length" class="empty-tip">这个分区还没有组合。点右上「新建组合」或 Fork 一个内置示范田开练。</div>
      </div>
    </div>

    <!-- ═══ 编辑区 ═══ -->
    <div v-if="mode === 'edit'" class="edit-wrap">
      <div v-if="editing.builtin_protected" class="lock-tip">
        🔒 <b>{{ editing.name }}</b> 是官方内置组合（原件保护，只能看不能改）。想改造它请用列表里的「🍴 Fork 另存」。
      </div>
      <div class="form-grid">
        <label>组合名称 <input v-model="form.name" :disabled="editing.builtin_protected" placeholder="给组合起个名（唯一，如：冰箱管家眼）" /></label>
        <label class="full">目标描述 <textarea v-model="form.target" :disabled="editing.builtin_protected" rows="2" placeholder="这个组合是干什么的？一句话说清 —— AI 参谋和 Hub 上的用户都靠它判断"></textarea></label>
        <label>版本 <input v-model="form.version" :disabled="editing.builtin_protected" placeholder="1.0.0" /></label>
        <label>失败回退
          <select v-model="form.on_step_fail" :disabled="editing.builtin_protected">
            <option value="abort">中止整链（最稳）</option>
            <option value="use_backup">换备选模型</option>
            <option value="skip_optional">跳过支线环</option>
          </select>
        </label>
      </div>

      <h3 class="sec-title">🧩 环节流水线（从上到下依次执行）</h3>
      <div v-for="(s, i) in form.steps" :key="i" class="step-card" :class="{ locked: editing.builtin_protected }">
        <div class="sc-head">
          <b>{{ i + 1 }}. {{ s.name || '未命名环节' }}</b>
          <span class="pill soft">{{ engineLabel(s.engine) }}</span>
          <span class="pill" :class="s.damage === 'readonly' ? 'ok' : (s.damage === 'db' ? 'warn' : 'danger')">{{ damageLabel(s.damage) }}</span>
          <span v-if="s.optional" class="pill soft">支线可跳</span>
          <div class="sc-tools">
            <button class="mini" :disabled="editing.builtin_protected || i === 0" @click="moveStep(i, -1)">↑</button>
            <button class="mini" :disabled="editing.builtin_protected || i === form.steps.length - 1" @click="moveStep(i, 1)">↓</button>
            <button class="mini danger" :disabled="editing.builtin_protected" @click="form.steps.splice(i, 1)">删</button>
          </div>
        </div>
        <div class="form-grid">
          <label>环节名 <input v-model="s.name" :disabled="editing.builtin_protected" placeholder="如：目标检测" /></label>
          <label>能力
            <select v-model="s.capability" :disabled="editing.builtin_protected" @change="onCapChange(s)">
              <option v-for="cap in CAPS" :key="cap" :value="cap">{{ capLabel(cap) }}</option>
            </select>
          </label>
          <label>模型绑定
            <select v-model="s.binding" :disabled="editing.builtin_protected">
              <option value="fixed">钉死模型（我指定）</option>
              <option value="dynamic_follow">跟用户所选（能力过滤）</option>
            </select>
          </label>
          <label v-if="s.binding === 'fixed'">用哪个模型
            <select v-model="s.model" :disabled="editing.builtin_protected" @change="onModelChange(s)">
              <option value="">— 选模型（只列本机真实可用的）—</option>
              <option v-for="m in modelsFor(s)" :key="m.id" :value="m.id">{{ m.label }}（{{ engineLabel(m.channel) }}）</option>
            </select>
          </label>
          <label v-else>能力过滤（逗号分隔）
            <input :value="(s.dynamic_cap_filter || []).join(',')" :disabled="editing.builtin_protected"
                   placeholder="如：vision_understand,text_gen" @input="s.dynamic_cap_filter = $event.target.value.split(',').map(x => x.trim()).filter(Boolean)" />
          </label>
          <label>破坏力等级
            <select v-model="s.damage" :disabled="editing.builtin_protected">
              <option value="readonly">只读（安全）</option>
              <option value="db">落库（会记数据）</option>
              <option value="action">动作（安全模式自动跳过）</option>
            </select>
          </label>
          <label class="chk"><input v-model="s.optional" type="checkbox" :disabled="editing.builtin_protected" /> 支线环（缺失不瘫主链）</label>
        </div>
        <p v-if="capWarn(s)" class="warn-line">⚠️ {{ capWarn(s) }}</p>
      </div>
      <button class="mini add-step" :disabled="editing.builtin_protected" @click="addStep">＋ 加一个环节</button>

      <div class="edit-foot">
        <BigButton type="ghost" @click="checkChain">🩺 硬件体检+三道门</BigButton>
        <BigButton :disabled="editing.builtin_protected" @click="saveChain">💾 保存</BigButton>
      </div>
      <div v-if="checkResult" class="check-card" :class="{ bad: !checkResult.ok }">
        <b>{{ checkResult.ok ? '✅ 三道门通过，可以试跑' : '❌ 没过门' }}</b>
        <p v-if="checkResult.vram_peak_gb !== undefined">显存峰值需求：{{ checkResult.vram_peak_gb }} GB（聚段取最大）</p>
        <p v-for="(p, i) in checkResult.problems || []" :key="i" class="warn-line">⚠️ {{ p }}</p>
      </div>
    </div>

    <!-- ═══ 试跑区 ═══ -->
    <div v-if="mode === 'run'" class="run-wrap">
      <div class="run-head">
        <BigButton type="ghost" @click="backToList">← 返回</BigButton>
        <b>▶ 试跑：{{ running.name }}</b>
        <span class="pill soft">{{ running.identity === 'pure_local' ? '纯本地' : '混合云' }}</span>
        <BigButton :disabled="testing" @click="runTest">{{ testing ? '跑着…' : '开始试跑' }}</BigButton>
      </div>
      <p class="dim-line">样例：不指定就用图片库最近一张照片。试跑走真实模型，外脑环会花一点 API 额度。</p>
      <div v-if="testResult">
        <div class="run-total" :class="testResult.ok ? '' : 'bad'">
          {{ testResult.ok ? `✅ 全链跑通，总耗时 ${testResult.total_ms} ms` : `❌ ${testResult.error || '试跑失败'}` }}
          <span v-if="testResult.label" class="run-label">{{ testResult.label }}</span>
        </div>
        <table v-if="testResult.steps && testResult.steps.length" class="log-table">
          <thead><tr><th>环节</th><th>模型</th><th>耗时</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="(s, i) in testResult.steps" :key="i">
              <td>{{ s.step_name || s.step_id }}</td>
              <td>{{ s.model_id || '—' }}</td>
              <td>{{ s.elapsed_ms }} ms</td>
              <td>{{ s.ok ? '✅' : '❌ ' + (s.error || '') }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="testResult.result" class="result-box">
          <b>结果：</b>
          <pre>{{ resultText(testResult.result) }}</pre>
        </div>
      </div>
    </div>

    <!-- ═══ 物流单区 ═══ -->
    <div v-if="mode === 'logs'" class="logs-wrap">
      <div class="run-head"><BigButton type="ghost" @click="backToList">← 返回</BigButton><b>📋 物流单：{{ running.name }}</b><span class="dim-line">每环耗时与成败的历史事实（AI 参谋靠它出优化建议）</span></div>
      <table v-if="logs.length" class="log-table">
        <thead><tr><th>时间</th><th>环节</th><th>模型</th><th>耗时</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="(s, i) in logs" :key="i">
            <td class="dim">{{ fmtTime(s.ts) }}</td>
            <td>{{ s.step_name || s.step_id }}</td>
            <td>{{ s.model_id || '—' }}</td>
            <td>{{ s.elapsed_ms }} ms</td>
            <td>{{ s.ok ? '✅' : '❌ ' + (s.error || '') }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-tip">还没有调用记录。</div>
    </div>
  </PageShell>
</template>

<script setup>
import { computed, ref } from 'vue'
import PageShell from '../components/PageShell.vue'
import BigButton from '../components/BigButton.vue'
import HIcon from '../components/HIcon.vue'
import { getJSON, postJSON, putJSON, delJSON } from '../api.js'

const chains = ref([])
const models = ref([])
const mode = ref('list')
const tab = ref('all')
const err = ref('')
const msg = ref('')
const editing = ref({})
const form = ref(emptyForm())
const running = ref({})
const testing = ref(false)
const testResult = ref(null)
const logs = ref([])
const checkResult = ref(null)
let isNew = false

const CAPS = ['dedup', 'detect', 'face', 'embed', 'tag', 'locate', 'vision_understand', 'text_gen']
const CAP_LABEL = { dedup: '查重', detect: '目标检测', face: '人脸', embed: '向量/语义', tag: '打标', locate: '定位', vision_understand: '看图理解', text_gen: '文字生成' }

function emptyForm() {
  return { id: '', name: '', version: '1.0.0', target: '', on_step_fail: 'abort',
           input_type: 'image_path', output_type: 'text', license: 'Apache-2.0', steps: [] }
}

const filtered = computed(() => {
  if (tab.value === 'mine') return chains.value.filter(c => c.origin !== 'builtin')
  if (tab.value === 'builtin') return chains.value.filter(c => c.origin === 'builtin')
  return chains.value
})

async function loadAll() {
  err.value = ''; msg.value = ''
  try {
    const [a, b] = await Promise.all([getJSON('/api/chains/'), getJSON('/api/chains/models')])
    chains.value = a.chains || []
    models.value = b.models || []
  } catch (e) { err.value = '加载失败：' + e.message }
}

// ── 列表辅助 ──
function originLabel(o) { return o === 'builtin' ? '内置' : (o === 'forked' ? '副本' : '我的') }
function capLabel(c) { return CAP_LABEL[c] || c }
function engineLabel(e) { return { internal: '内置底座', llama_cpp: '本地引擎', ollama: 'Ollama', cloud: '外脑', program: '程序环', chain: '组合' }[e] || e }
function damageLabel(d) { return { readonly: '只读', db: '落库', action: '动作' }[d] || d }
function shortModel(m) { return (m || '').replace('harven.base.', '').replace('ollama:', '').replace('cloud:', '') }
function lastText(c) {
  if (!c.last_run) return '还没跑过'
  return `${c.last_run.ok ? '🟢 上次跑通' : '🔴 上次失败'} · ${Math.round(c.last_run.total_ms)} ms`
}
function lastClass(c) { return c.last_run ? (c.last_run.ok ? 'ok-line' : 'bad-line') : 'dim' }

// ── 编辑 ──
function newChain() {
  editing.value = {}
  form.value = emptyForm()
  form.value.steps = [blankStep()]
  isNew = true
  checkResult.value = null
  mode.value = 'edit'
}
function blankStep() {
  return { id: 's' + Math.random().toString(36).slice(2, 7), name: '', capability: 'vision_understand',
           binding: 'fixed', model: '', backup_models: [], dynamic_cap_filter: [],
           engine: 'internal', input_type: 'any', output_type: 'any', damage: 'readonly',
           packed: '', keep_alive_s: 0, optional: false, note: '' }
}
function openEdit(c) {
  getJSON(`/api/chains/${c.id}`).then(res => {
    editing.value = c
    form.value = res.recipe
    isNew = false
    checkResult.value = null
    mode.value = 'edit'
  }).catch(e => { err.value = e.message })
}
function addStep() { form.value.steps.push(blankStep()) }
function moveStep(i, d) {
  const arr = form.value.steps
  const j = i + d
  if (j < 0 || j >= arr.length) return
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}
function onCapChange(s) { s.model = '' }
function onModelChange(s) {
  const m = models.value.find(x => x.id === s.model)
  if (m) s.engine = m.channel
}
function modelsFor(s) {
  return models.value.filter(m => (m.capabilities || []).includes(s.capability))
}
function capWarn(s) {
  if (s.binding !== 'fixed' || !s.model) return ''
  const m = models.value.find(x => x.id === s.model)
  if (!m) return `模型 ${s.model} 本机没盘点到——保存也过不了能力门，请换本机真实存在的`
  if (!(m.capabilities || []).includes(s.capability)) return `${m.label} 不具备「${capLabel(s.capability)}」能力，结构门/能力门会拦`
  return ''
}
function backToList() { mode.value = 'list'; loadAll() }

async function saveChain() {
  err.value = ''; msg.value = ''
  try {
    const payload = { recipe: form.value }
    if (isNew) {
      const r = await postJSON('/api/chains', payload)
      msg.value = `✅ 组合「${r.recipe_id}」已保存并选用（组合即模型，已进能力登记表）`
    } else {
      await putJSON(`/api/chains/${form.value.id}`, payload)
      msg.value = '✅ 已保存'
    }
    mode.value = 'list'
    loadAll()
  } catch (e) { err.value = e.message }
}
async function forkChain(c) {
  const name = prompt(`Fork「${c.name}」另存。新组合名：`, c.name + '·副本')
  if (!name) return
  try {
    const r = await postJSON(`/api/chains/${c.id}/fork`, { name })
    msg.value = `✅ 已 Fork 为「${r.recipe_id}」，可以编辑了`
    await loadAll()
    const nc = chains.value.find(x => x.id === r.recipe_id)
    if (nc) openEdit(nc)
  } catch (e) { err.value = e.message }
}
async function removeChain(c) {
  if (!confirm(`删除组合「${c.name}」？底座模型会保留，只卸组合本身。`)) return
  try {
    await delJSON(`/api/chains/${c.id}`)
    msg.value = '✅ 已卸载（底座模型保留）'
    loadAll()
  } catch (e) { err.value = e.message }
}
async function checkChain() {
  checkResult.value = null
  err.value = ''
  try {
    // 干跑用当前编辑中的表单（未保存也能体检）：临时登记只读检查走后端保存前校验
    if (isNew) {
      const r = await postJSON('/api/chains', { recipe: form.value })
      checkResult.value = await postJSON(`/api/chains/${r.recipe_id}/check`, {})
      form.value.id = r.recipe_id
      isNew = false
      msg.value = '已先保存草稿再体检（配方单要落盘才能跑三道门）'
    } else {
      await putJSON(`/api/chains/${form.value.id}`, { recipe: form.value })
      checkResult.value = await postJSON(`/api/chains/${form.value.id}/check`, {})
    }
  } catch (e) { err.value = e.message }
}

// ── 试跑 ──
function openRun(c) { running.value = c; testResult.value = null; mode.value = 'run' }
async function runTest() {
  testing.value = true
  err.value = ''
  try {
    testResult.value = await postJSON(`/api/chains/${running.value.id}/test`, {})
  } catch (e) { err.value = '试跑失败：' + e.message }
  testing.value = false
}
function resultText(r) {
  try {
    if (typeof r === 'string') return r
    return JSON.stringify(r, null, 2).slice(0, 2000)
  } catch (_) { return String(r) }
}

// ── 物流单 ──
function openLogs(c) {
  running.value = c
  mode.value = 'logs'
  getJSON(`/api/chains/${c.id}/logs?limit=200`).then(r => { logs.value = r.rows || [] })
    .catch(e => { err.value = e.message })
}
function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts)
  return isNaN(d) ? String(ts) : d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

loadAll()
</script>

<style scoped>
.quick-filter { display: flex; gap: 7px; margin: -4px 0 14px; flex-wrap: wrap; }
.quick-filter button { border: 1px solid var(--line, #ddd); background: transparent; color: inherit; border-radius: 16px; padding: 5px 14px; cursor: pointer; font-size: 13px; }
.quick-filter button.on { background: var(--accent, #4a7dff); color: #fff; border-color: var(--accent, #4a7dff); }
.chain-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.chain-card { border: 1px solid var(--line, #e3e3e3); border-radius: 12px; padding: 13px 14px; display: flex; flex-direction: column; gap: 8px; background: var(--card, #fff); }
.cc-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.cc-name { font-size: 15px; }
.cc-target { font-size: 12px; opacity: .75; margin: 0; min-height: 16px; }
.cc-tags { display: flex; gap: 5px; flex-wrap: wrap; }
.tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: rgba(90, 130, 255, .12); color: var(--accent, #4a7dff); }
.tag.dim { background: rgba(128, 128, 128, .12); color: inherit; opacity: .7; }
.cc-steps { display: flex; flex-wrap: wrap; gap: 5px; }
.step-chip { font-size: 11px; padding: 2px 8px; border-radius: 8px; background: rgba(128, 128, 128, .1); }
.step-model { font-style: normal; opacity: .6; margin-left: 4px; }
.cc-foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: auto; flex-wrap: wrap; }
.cc-last { font-size: 12px; }
.ok-line { color: #2e9e5b; } .bad-line { color: #d54941; } .dim { opacity: .6; }
.cc-actions { display: flex; gap: 5px; flex-wrap: wrap; }
.mini { border: 1px solid var(--line, #ddd); background: transparent; color: inherit; border-radius: 8px; padding: 4px 9px; cursor: pointer; font-size: 12px; }
.mini:disabled { opacity: .4; cursor: not-allowed; }
.mini.danger { color: #d54941; border-color: rgba(213, 73, 65, .4); }
.empty-tip { grid-column: 1 / -1; text-align: center; padding: 40px 0; opacity: .6; font-size: 13px; }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: rgba(128, 128, 128, .14); }
.pill.ok { background: rgba(46, 158, 91, .15); color: #2e9e5b; }
.pill.warn { background: rgba(230, 162, 60, .16); color: #c07c1d; }
.pill.danger { background: rgba(213, 73, 65, .14); color: #d54941; }
.pill.soft { opacity: .8; }

.edit-wrap, .run-wrap, .logs-wrap { display: flex; flex-direction: column; gap: 12px; }
.lock-tip { border: 1px dashed rgba(230, 162, 60, .6); border-radius: 10px; padding: 10px 14px; font-size: 13px; background: rgba(230, 162, 60, .07); }
.sec-title { margin: 6px 0 0; font-size: 14px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.form-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; opacity: .85; }
.form-grid label.full { grid-column: 1 / -1; }
.form-grid input, .form-grid select, .form-grid textarea { border: 1px solid var(--line, #ddd); border-radius: 8px; padding: 7px 10px; font-size: 13px; background: var(--card, #fff); color: inherit; }
.form-grid input:disabled, .form-grid select:disabled, .form-grid textarea:disabled { opacity: .5; }
.form-grid .chk { flex-direction: row; align-items: center; gap: 6px; }
.step-card { border: 1px solid var(--line, #e3e3e3); border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
.step-card.locked { opacity: .75; }
.sc-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.sc-tools { margin-left: auto; display: flex; gap: 5px; }
.add-step { align-self: flex-start; }
.edit-foot { display: flex; gap: 10px; }
.check-card { border: 1px solid var(--line, #e3e3e3); border-radius: 10px; padding: 10px 14px; font-size: 13px; }
.check-card.bad { border-color: rgba(213, 73, 65, .5); }
.warn-line { color: #c07c1d; font-size: 12px; margin: 2px 0; }
.run-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.dim-line { font-size: 12px; opacity: .6; margin: 0; }
.run-total { font-size: 14px; padding: 8px 12px; border-radius: 10px; background: rgba(46, 158, 91, .1); }
.run-total.bad { background: rgba(213, 73, 65, .1); color: #d54941; }
.run-label { font-size: 12px; opacity: .7; margin-left: 8px; }
.log-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.log-table th, .log-table td { text-align: left; padding: 6px 9px; border-bottom: 1px solid var(--line, #eee); }
.log-table th { opacity: .6; font-weight: 500; }
.result-box { border: 1px solid var(--line, #e3e3e3); border-radius: 10px; padding: 10px 14px; font-size: 13px; }
.result-box pre { white-space: pre-wrap; word-break: break-all; margin: 6px 0 0; font-size: 12px; max-height: 320px; overflow: auto; }
</style>
