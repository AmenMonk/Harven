<template>
  <PageShell :title="t('page.create.t')" icon="bulb" :sub="t('page.create.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadAll(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
    </template>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <!-- Tab 切换 -->
    <div class="anchors">
      <button :class="{ on: tab === 'agent' }" @click="tab = 'agent'">🤖 造 Agent</button>
      <button :class="{ on: tab === 'skill' }" @click="tab = 'skill'">🧩 造 Skill</button>
      <button :class="{ on: tab === 'evo' }" @click="tab = 'evo'">🔧 改哈文<span v-if="pendingCount" class="badge">{{ pendingCount }}</span></button>
    </div>

    <!-- ═══ 造 Agent ═══ -->
    <section v-show="tab === 'agent'" class="sec-body">
      <p class="hint">给哈文添一个专项小助手：起个名字、说说它擅长什么，哈文自动生成它的"性格说明书"。以后在聊天里提到触发词，就会交给它处理。</p>
      <div class="form-card">
        <label>名字 <i>必填</i></label>
        <input v-model.trim="ag.name" placeholder="例如：菜谱大厨" />
        <label>擅长什么 <i>必填</i></label>
        <textarea v-model.trim="ag.role" rows="3" placeholder="例如：家里冰箱有什么就做什么菜，会考虑孩子不吃辣"></textarea>
        <label>触发词 <i>聊天里说到这些词就交给它（逗号分隔）</i></label>
        <input v-model.trim="ag.triggers" placeholder="做菜, 菜谱, 吃什么" />
        <label>性格说明书（system_prompt） <i>可自动生成后手动修改</i></label>
        <textarea v-model="ag.prompt" rows="6" placeholder="点下面按钮自动生成，也可以自己写"></textarea>
        <div class="btn-line">
          <BigButton variant="ghost" :px="13" @click="genAgentPrompt">✨ 帮我写说明书</BigButton>
          <BigButton :px="13" :loading="agBusy" @click="registerAgent">注册这个 Agent</BigButton>
        </div>
      </div>

      <h3 class="sub">已有的自定义 Agent（{{ customAgents.length }}）</h3>
      <div v-if="!customAgents.length" class="empty">还没有自定义 Agent —— 用上面的表单造一个吧</div>
      <div class="row" v-for="a in customAgents" :key="a.id">
        <div class="row-main">
          <b>{{ a.name }} <span class="mono">{{ a.id }}</span></b>
          <span>{{ a.role || a.system_prompt?.slice(0, 60) }}</span>
        </div>
        <BigButton variant="ghost" :px="12" @click="toggleAgent(a)">{{ a.disabled ? '启用' : '停用' }}</BigButton>
        <BigButton variant="ghost" :px="12" @click="deleteAgent(a)">删除</BigButton>
      </div>
    </section>

    <!-- ═══ 造 Skill ═══ -->
    <section v-show="tab === 'skill'" class="sec-body">
      <p class="hint">描述你想要的小技能，AI 帮你写出来装进哈文。做好的技能会出现在「技能」页，聊天里说触发词就能用。</p>
      <div class="form-card">
        <label>技能名字 <i>必填</i></label>
        <input v-model.trim="sk.name" placeholder="例如：喝水提醒助手" />
        <label>它能干什么 <i>必填，说得越具体 AI 写得越准</i></label>
        <textarea v-model.trim="sk.description" rows="3" placeholder="例如：记录我每天喝水量，能查询今天/本周喝了多少，目标每天 8 杯"></textarea>
        <label>触发词 <i>可选（逗号分隔）</i></label>
        <input v-model.trim="sk.triggers" placeholder="喝水, 喝水量" />
        <div class="btn-line">
          <BigButton :px="13" :loading="skBusy" @click="genSkill">🧩 让 AI 生成这个技能</BigButton>
          <a href="#/skills"><BigButton variant="ghost" :px="13">去技能页看看</BigButton></a>
        </div>
        <pre v-if="skResult" class="gen-result">{{ skResult }}</pre>
      </div>
    </section>

    <!-- ═══ 改哈文（受控进化提案审批） ═══ -->
    <section v-show="tab === 'evo'" class="sec-body">
      <p class="hint">哈文想改自己的功能时，会先写"修改申请书"放在这里，你批准了它才动手；不放心还可以事后一键回滚。</p>
      <div v-if="!proposals.length" class="empty">暂无待审批的提案 —— 哈文安分守己中 ✅</div>
      <div class="proposal-card" v-for="p in proposals" :key="p.id">
        <div class="p-head">
          <b>#{{ p.id }} {{ p.title }}</b>
          <span class="pill">{{ statusText(p.status) }}</span>
        </div>
        <p class="p-desc">{{ p.description }}</p>
        <p v-if="p.risk_note" class="p-risk">⚠️ {{ p.risk_note }}</p>
        <div class="p-meta">提案人：{{ p.proposed_by || '哈文' }} · 涉及文件 {{ (p.changes || []).length }} 处</div>
        <div class="btn-line">
          <BigButton :px="13" :loading="evoBusyId === p.id" @click="approve(p)">✅ 批准并让哈文改</BigButton>
          <BigButton variant="ghost" :px="13" :loading="evoBusyId === p.id" @click="reject(p)">拒绝</BigButton>
        </div>
      </div>

      <h3 class="sub">已批准记录</h3>
      <div class="row" v-for="h in history" :key="h.id">
        <div class="row-main"><b>{{ h.title || h.action || ('提案 #' + h.proposal_id) }}</b><span>{{ h.created_at || h.time || '' }}</span></div>
        <span class="pill ok">{{ statusText(h.status) }}</span>
      </div>
    </section>
  </PageShell>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'
import { t } from '../i18n'

const tab = ref('agent')
const msg = ref('')
const msgOk = ref(false)

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 6000)
}

/* ── 造 Agent ── */
const ag = ref({ name: '', role: '', triggers: '', prompt: '' })
const agBusy = ref(false)
const customAgents = ref([])

function genAgentPrompt() {
  if (!ag.value.name || !ag.value.role) { flash('先填好名字和擅长什么', false); return }
  // 与老版 Agent 工坊同源模板（本地铁速生成，不依赖网络）
  ag.value.prompt =
    `你是哈文AI的专项子Agent——${ag.value.name}。\n\n` +
    `## 你的角色\n${ag.value.role}\n\n` +
    `## 规则\n1. 只做你擅长的事，不越界\n2. 输出：先结论，后详情\n` +
    `3. 遇到不属于你领域的请求，回复："[SKIP] 请交给其他Agent处理"\n\n` +
    `## 能力边界\n${ag.value.role}，仅此而已。`
}

async function registerAgent() {
  if (!ag.value.name || !ag.value.role || !ag.value.prompt) { flash('名字、擅长什么、说明书三项都要填', false); return }
  agBusy.value = true
  try {
    const slug = ag.value.name.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '_').replace(/^_+|_+$/g, '') || 'agent'
    const id = `custom_${slug}_${Date.now().toString(36).slice(-4)}`
    const j = await postJSON('/api/agents/register', {
      id,
      name: ag.value.name,
      role: ag.value.role,
      system_prompt: ag.value.prompt,
      triggers: ag.value.triggers ? ag.value.triggers.split(/[,，、]/).map((s) => s.trim()).filter(Boolean) : [],
    })
    if (j.success) {
      flash(`✅ Agent「${ag.value.name}」已注册！在聊天里说触发词就能叫它出场`)
      ag.value = { name: '', role: '', triggers: '', prompt: '' }
      await loadAgents()
    } else flash('注册失败：' + (j.error || ''), false)
  } catch (e) { flash('注册失败：' + e.message, false) }
  finally { agBusy.value = false }
}

async function loadAgents() {
  try {
    const j = await getJSON('/api/agents')
    const list = j.agents || j || []
    customAgents.value = list.filter((a) => (a.id || '').startsWith('custom_'))
  } catch (_) { customAgents.value = [] }
}

async function toggleAgent(a) {
  try {
    await postJSON(`/api/agents/${a.id}/toggle`, {})
    await loadAgents()
  } catch (e) { flash('操作失败：' + e.message, false) }
}

async function deleteAgent(a) {
  if (!confirm(`确定删除 Agent「${a.name}」吗？`)) return
  try {
    await fetch(`/api/agents/${a.id}`, { method: 'DELETE' })
    flash('已删除')
    await loadAgents()
  } catch (e) { flash('删除失败：' + e.message, false) }
}

/* ── 造 Skill ── */
const sk = ref({ name: '', description: '', triggers: '' })
const skBusy = ref(false)
const skResult = ref('')

async function genSkill() {
  if (!sk.value.name || !sk.value.description) { flash('技能名字和功能描述都要填', false); return }
  skBusy.value = true; skResult.value = ''
  try {
    const j = await postJSON('/api/skills/builder/generate', {
      name: sk.value.name,
      description: sk.value.description,
      triggers: sk.value.triggers,
    })
    if (j.success) {
      skResult.value = j.output || j.preview || JSON.stringify(j, null, 2)
      flash('✅ 技能已生成并安装，去「技能」页就能看到')
      sk.value = { name: '', description: '', triggers: '' }
    } else flash('生成失败：' + (j.error || ''), false)
  } catch (e) { flash('生成失败：' + e.message, false) }
  finally { skBusy.value = false }
}

/* ── 改哈文（提案审批） ── */
const proposals = ref([])
const history = ref([])
const pendingCount = ref(0)
const evoBusyId = ref(0)

function statusText(s) {
  return { pending: '待审批', approved: '已批准', rejected: '已拒绝', applied: '已生效', rolled_back: '已回滚' }[s] || s || '—'
}

async function loadEvolution() {
  try {
    const j = await getJSON('/api/evolution/proposals?status=pending')
    proposals.value = j.proposals || []
    pendingCount.value = proposals.value.length
  } catch (_) { proposals.value = [] }
  try {
    const j = await getJSON('/api/evolution/history')
    history.value = (j.history || []).slice(0, 10)
  } catch (_) { history.value = [] }
}

async function approve(p) {
  if (!confirm(`批准「${p.title}」？批准后哈文会自动修改代码并生效。`)) return
  evoBusyId.value = p.id
  try {
    // ★ 2026-11-15: 受保护动作密码门——401 时收集管理员密码重试一次
    let j = await postJSON(`/api/evolution/proposals/${p.id}/approve`, {})
    if (j.code === 'password_required') {
      const pwd = prompt('🔒 批准提案需要管理员密码（受保护的进化操作）：', '')
      if (!pwd) { flash('已取消：未输入管理员密码', false); return }
      j = await postJSON(`/api/evolution/proposals/${p.id}/approve`, { password: pwd })
    }
    if (j.success !== false) flash(`✅ 提案 #${p.id} 已批准，哈文这就去改`)
    else if (j.code === 'password_wrong') flash('批准失败：管理员密码错误', false)
    else flash('批准失败：' + (j.error || ''), false)
    await loadEvolution()
  } catch (e) { flash('批准失败：' + e.message, false) }
  finally { evoBusyId.value = 0 }
}

async function reject(p) {
  evoBusyId.value = p.id
  try {
    await postJSON(`/api/evolution/proposals/${p.id}/reject`, {})
    flash(`已拒绝提案 #${p.id}`)
    await loadEvolution()
  } catch (e) { flash('拒绝失败：' + e.message, false) }
  finally { evoBusyId.value = 0 }
}

function loadAll(showErr = false) {
  loadAgents()
  loadEvolution()
  if (showErr) msg.value = ''
}

onMounted(() => loadAll(true))
</script>

<style scoped>
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }
.anchors { display: flex; gap: 7px; margin-bottom: 16px; flex-wrap: wrap; }
.anchors button { display: flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: #fff; border-radius: 20px; padding: 7px 16px; font-size: 13px; cursor: pointer; color: var(--sub); font-family: inherit; }
.anchors button.on { border-color: var(--blue); color: var(--blue); font-weight: 700; background: #F3F7FE; }
.badge { background: #FDF2F2; color: #B4443C; border-radius: 10px; padding: 0 7px; font-size: 11px; font-weight: 700; }
.sec-body { display: flex; flex-direction: column; gap: 10px; max-width: 860px; }
.hint { font-size: 12.5px; color: var(--sub); margin: 0; line-height: 1.7; }
.form-card { background: #fff; border: 1px solid var(--line); border-radius: 13px; padding: 15px 17px; display: flex; flex-direction: column; gap: 6px; }
.form-card label { font-size: 12.5px; font-weight: 700; display: flex; align-items: baseline; gap: 8px; }
.form-card label i { font-size: 11px; font-weight: 400; color: var(--sub); }
.form-card input, .form-card textarea { border: 1px solid var(--line); border-radius: 9px; padding: 9px 12px; font-size: 13px; font-family: inherit; outline: none; resize: vertical; }
.form-card input:focus, .form-card textarea:focus { border-color: var(--blue); }
.btn-line { display: flex; gap: 9px; margin-top: 5px; flex-wrap: wrap; }
.gen-result { margin: 8px 0 0; font-size: 12px; background: var(--c-bg-soft); border-radius: 9px; padding: 10px 12px; white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow: auto; }
.sub { font-size: 13.5px; margin: 12px 0 2px; }
.row { display: flex; align-items: center; gap: 14px; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px 16px; }
.row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.row-main b { font-size: 13.5px; }
.row-main span { font-size: 12px; color: var(--sub); word-break: break-all; }
.mono { font-family: monospace; font-size: 11px; color: var(--sub); font-weight: 400; }
.empty { padding: 20px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13px; }
.pill { font-size: 11.5px; padding: 3px 10px; border-radius: 20px; background: #FFF8E8; color: #8A6D1C; white-space: nowrap; }
.pill.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.proposal-card { background: #fff; border: 1px solid var(--line); border-radius: 13px; padding: 14px 17px; display: flex; flex-direction: column; gap: 7px; }
.p-head { display: flex; align-items: center; gap: 9px; }
.p-head b { font-size: 14px; flex: 1; }
.p-desc { margin: 0; font-size: 12.5px; color: var(--ink); line-height: 1.7; }
.p-risk { margin: 0; font-size: 12px; color: #8A6D1C; }
.p-meta { font-size: 11.5px; color: var(--sub); }
</style>
