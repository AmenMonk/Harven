<template>
  <PageShell title="画像管理" icon="users" sub="哈文默默记住的关于你的事 —— 全部可以看、可以改、可以删，学错了你说了算">
    <template #actions>
      <BigButton variant="ghost" :px="14" :loading="analyzing" @click="analyze"><HIcon name="dots" :size="16" /> 让哈文重新分析</BigButton>
    </template>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <!-- Tab 切换 -->
    <div class="ptabs">
      <button :class="{ on: tab === 'dims' }" @click="tab = 'dims'">🗂️ 分维度看</button>
      <button :class="{ on: tab === 'timeline' }" @click="tab = 'timeline'">🕒 时间线</button>
      <button :class="{ on: tab === 'insights' }" @click="tab = 'insights'">
        💡 洞察与建议<span v-if="sugCount" class="badge">{{ sugCount }}</span>
      </button>
    </div>

    <!-- ═══ Tab1 分维度管理 ═══ -->
    <section v-show="tab === 'dims'" class="body">
      <div class="add-row">
        <select v-model="addDim">
          <option v-for="(lbl, key) in DIMS" :key="key" :value="key">{{ lbl }}</option>
        </select>
        <input v-model.trim="addLabel" placeholder="想告诉哈文的事，如：喜欢自驾游、爱吃海鲜" @keyup.enter="addTrait" />
        <BigButton :px="13" :loading="addBusy" @click="addTrait">添加</BigButton>
      </div>
      <p class="hint">手动添加的会标 👤，哈文自动学到的会标 🤖 —— 自动学到的也可以改和删</p>

      <div v-for="(info, key) in grouped" :key="key" class="dim-box">
        <div class="dim-head"><b>{{ DIMS[key] || info.label || key }}</b><span>{{ info.traits.length }} 条</span></div>
        <div v-for="t in info.traits" :key="t.label" class="trait-row">
          <template v-if="editKey === key + '::' + t.label">
            <input v-model.trim="editText" class="edit-input" @keyup.enter="saveEdit(key, t)" />
            <BigButton :px="12" :loading="busy" @click="saveEdit(key, t)">保存</BigButton>
            <BigButton variant="ghost" :px="12" @click="editKey = ''">取消</BigButton>
          </template>
          <template v-else>
            <span class="src" :title="t.evidence">{{ t.source === 'manual' ? '👤' : '🤖' }}</span>
            <b class="t-label">{{ t.label }}</b>
            <span class="conf" :title="'置信度 ' + Math.round(t.confidence * 100) + '%'">
              <i :style="{ width: Math.round(t.confidence * 100) + '%' }" :class="{ low: t.confidence < 0.6 }"></i>
            </span>
            <span v-if="t.verified_count > 1" class="vc">×{{ t.verified_count }}</span>
            <button class="mini" @click="startEdit(key, t)">改</button>
            <button class="mini danger" @click="removeTrait(key, t)">删</button>
          </template>
        </div>
        <div v-if="!info.traits.length" class="dim-empty">这个维度还没有内容</div>
      </div>
    </section>

    <!-- ═══ Tab2 时间线 ═══ -->
    <section v-show="tab === 'timeline'" class="body">
      <div v-if="!timeline.length" class="empty">还没有画像记录 —— 多跟哈文聊聊，或点右上角「重新分析」</div>
      <div class="tl-list">
        <div v-for="(item, i) in timeline" :key="i" class="tl-item">
          <div class="tl-dot" :class="item.source"></div>
          <div class="tl-main">
            <div class="tl-line">
              <b>{{ item.label }}</b>
              <span class="tl-dim">{{ item.dimension_label }}</span>
              <span class="src" :title="item.evidence">{{ item.source === 'manual' ? '👤手动' : (item.source === 'llm' ? '🧠深度分析' : '🤖自动') }}</span>
            </div>
            <div class="tl-sub">
              <span v-if="item.created_at">{{ fmtTime(item.created_at) }}</span>
              <span v-else>早期记录</span>
              <span v-if="item.evidence" class="tl-eva" :title="item.evidence">依据：{{ item.evidence }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ═══ Tab3 洞察与建议 ═══ -->
    <section v-show="tab === 'insights'" class="body">
      <h3 class="sub-h">🌱 最近 7 天新学到的</h3>
      <div v-if="!insights.length" class="empty small">最近没有新的发现 —— 正常，画像会慢慢变准</div>
      <div v-for="(item, i) in insights" :key="'ins' + i" class="ins-card">
        <b>{{ item.label }}</b><span class="tl-dim">{{ item.dimension_label }}</span>
        <span class="tl-sub-inline">{{ fmtTime(item.created_at) }}</span>
      </div>

      <h3 class="sub-h">🤔 哈文不太确定、等你拍板的（{{ suggestions.length }}）</h3>
      <div v-if="!suggestions.length" class="empty small">没有待确认的建议 —— 哈文拿不准的会放这里，不会自作主张</div>
      <div v-for="(s, i) in suggestions" :key="'sug' + i" class="sug-card">
        <div class="sug-main">
          <b>{{ s.label }}</b><span class="tl-dim">{{ s.dimension_label || s.dimension }}</span>
          <p v-if="s.evidence" class="sug-eva">依据：{{ s.evidence }}</p>
        </div>
        <div class="sug-btns">
          <BigButton :px="12" @click="approve(s)">采纳</BigButton>
          <BigButton variant="ghost" :px="12" @click="dismiss(s)">不用</BigButton>
        </div>
      </div>
    </section>
  </PageShell>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON, putJSON, delJSON } from '../api.js'

const DIMS = {
  interests: '兴趣爱好', expertise: '专业领域', communication_style: '交流风格',
  values: '价值取向', habits: '使用习惯', emotional_tone: '情绪倾向',
  skill_level: '技能水平', tech_stack: '技术栈', goals: '当前目标', language_pref: '语言偏好',
}

const tab = ref('dims')
const profile = ref({})
const timeline = ref([])
const insights = ref([])
const suggestions = ref([])
const msg = ref('')
const msgOk = ref(false)
const busy = ref(false)
const analyzing = ref(false)

/* 手动添加 */
const addDim = ref('interests')
const addLabel = ref('')
const addBusy = ref(false)

/* 行内编辑 */
const editKey = ref('')
const editText = ref('')

const grouped = computed(() => profile.value.dimensions || {})
const sugCount = computed(() => suggestions.value.length)

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 4000)
}

function fmtTime(iso) {
  if (!iso) return ''
  return iso.slice(0, 16).replace('T', ' ')
}

async function load() {
  try {
    const [pf, tl, ins, st] = await Promise.all([
      getJSON('/api/profile/auto/state').catch(() => ({})),
      getJSON('/api/profile/timeline').catch(() => ({ items: [] })),
      getJSON('/api/profile/insights').catch(() => ({ items: [] })),
    ])
    profile.value = pf.profile || {}
    suggestions.value = pf.suggestions || []
    timeline.value = tl.items || []
    insights.value = ins.items || []
  } catch (e) { flash('加载失败：' + e.message, false) }
}

async function addTrait() {
  if (!addLabel.value) { flash('先填内容再点添加', false); return }
  addBusy.value = true
  try {
    const j = await postJSON('/api/profile/trait', { dimension: addDim.value, label: addLabel.value })
    if (j.success) { flash('✅ 已添加，下次对话哈文就知道了'); addLabel.value = ''; await load() }
    else flash('添加失败：' + (j.error || ''), false)
  } catch (e) { flash('添加失败：' + e.message, false) }
  finally { addBusy.value = false }
}

function startEdit(dim, t) {
  editKey.value = dim + '::' + t.label
  editText.value = t.label
}

async function saveEdit(dim, t) {
  if (!editText.value) { flash('新内容不能为空', false); return }
  busy.value = true
  try {
    const j = await putJSON('/api/profile/trait', { dimension: dim, label: t.label, new_label: editText.value })
    if (j.success) { flash('✅ 已修改'); editKey.value = ''; await load() }
    else flash('修改失败：' + (j.error || ''), false)
  } catch (e) { flash('修改失败：' + e.message, false) }
  finally { busy.value = false }
}

async function removeTrait(dim, t) {
  if (!confirm(`确定删除「${t.label}」？哈文以后不会再记住这条。`)) return
  busy.value = true
  try {
    const j = await delJSON('/api/profile/trait', { dimension: dim, label: t.label })
    if (j.success) { flash('已删除'); await load() }
    else flash('删除失败：' + (j.error || ''), false)
  } catch (e) { flash('删除失败：' + e.message, false) }
  finally { busy.value = false }
}

async function approve(s) {
  try {
    const j = await postJSON('/api/profile/auto/suggestions/approve', { dimension: s.dimension, label: s.label })
    if (j.success) { flash('✅ 已采纳'); await load() }
    else flash('操作失败：' + (j.error || ''), false)
  } catch (e) { flash('操作失败：' + e.message, false) }
}

async function dismiss(s) {
  try {
    await postJSON('/api/profile/auto/suggestions/dismiss', { dimension: s.dimension, label: s.label })
    await load()
  } catch (e) { flash('操作失败：' + e.message, false) }
}

async function analyze() {
  analyzing.value = true
  try {
    const j = await postJSON('/api/profile/auto/force', {})
    if (j.success !== false) {
      const s = j.result?.stats || j.stats || {}
      flash(`分析完成：看了 ${s.messages ?? '?'} 条消息${s.conversations ? `（${s.conversations} 个会话）` : ''}`)
      await load()
    } else flash('分析失败：' + (j.error || ''), false)
  } catch (e) { flash('分析失败：' + e.message, false) }
  finally { analyzing.value = false }
}

onMounted(load)
</script>

<style scoped>
.tip { margin-bottom: 12px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }
.ptabs { display: flex; gap: 8px; margin-bottom: 14px; }
.ptabs button { border: 1px solid var(--line); background: #fff; border-radius: 20px; padding: 7px 16px; font-size: 13px; cursor: pointer; color: var(--sub); font-family: inherit; }
.ptabs button.on { border-color: var(--blue); color: var(--blue); font-weight: 700; background: #F3F7FE; }
.badge { margin-left: 5px; font-size: 10.5px; background: #FDF2F2; color: #B4443C; border-radius: 12px; padding: 1px 7px; font-weight: 700; }
.body { display: flex; flex-direction: column; gap: 10px; max-width: 860px; }

.add-row { display: flex; gap: 8px; align-items: center; }
.add-row select { border: 1px solid var(--line); border-radius: 9px; padding: 8px 10px; font-size: 13px; font-family: inherit; outline: none; background: #fff; color: var(--ink); }
.add-row input { flex: 1; border: 1px solid var(--line); border-radius: 9px; padding: 8px 12px; font-size: 13px; outline: none; background: #fff; color: var(--ink); }
.add-row input:focus, .add-row select:focus { border-color: var(--blue); }
.hint { margin: 0; font-size: 11.5px; color: var(--sub); }

.dim-box { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 12px 15px; }
.dim-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.dim-head b { font-size: 13.5px; }
.dim-head span { font-size: 11.5px; color: var(--sub); }
.dim-empty { font-size: 12px; color: var(--sub); padding: 4px 0; }
.trait-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-top: 1px dashed var(--line); flex-wrap: wrap; }
.src { font-size: 13px; cursor: help; flex-shrink: 0; }
.t-label { font-size: 13px; }
.conf { width: 64px; height: 5px; border-radius: 4px; background: var(--c-bg-soft); overflow: hidden; flex-shrink: 0; }
.conf i { display: block; height: 100%; background: var(--blue); border-radius: 4px; }
.conf i.low { background: #E8A33D; }
.vc { font-size: 10.5px; color: var(--sub); }
.mini { border: 1px solid var(--line); background: #fff; border-radius: 7px; font-size: 11px; padding: 3px 9px; cursor: pointer; color: var(--ink); font-family: inherit; flex-shrink: 0; }
.mini:hover { border-color: var(--blue); color: var(--blue); }
.mini.danger:hover { border-color: var(--red, #B4443C); color: var(--red, #B4443C); }
.edit-input { flex: 1; border: 1px solid var(--blue); border-radius: 8px; padding: 6px 10px; font-size: 12.5px; outline: none; background: #fff; color: var(--ink); min-width: 200px; }

.empty { padding: 26px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13px; }
.empty.small { padding: 14px; font-size: 12px; }

.tl-list { display: flex; flex-direction: column; }
.tl-item { display: flex; gap: 11px; padding: 9px 2px; border-bottom: 1px dashed var(--line); }
.tl-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--blue); margin-top: 5px; flex-shrink: 0; }
.tl-dot.manual { background: #2E9E5B; }
.tl-dot.llm { background: #8B5CF6; }
.tl-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.tl-line { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.tl-line b { font-size: 13px; }
.tl-dim { font-size: 11px; background: var(--c-bg-soft); border-radius: 12px; padding: 1px 8px; color: var(--sub); }
.tl-sub { display: flex; gap: 10px; font-size: 11px; color: var(--sub); flex-wrap: wrap; }
.tl-eva { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 420px; }

.sub-h { font-size: 13.5px; margin: 6px 0 0; }
.ins-card { display: flex; align-items: baseline; gap: 8px; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 9px 13px; }
.ins-card b { font-size: 13px; }
.tl-sub-inline { font-size: 11px; color: var(--sub); margin-left: auto; }
.sug-card { display: flex; align-items: center; gap: 12px; background: #FFF9F0; border: 1px solid #F0E2C8; border-radius: 10px; padding: 10px 14px; }
.sug-main { flex: 1; min-width: 0; }
.sug-main b { font-size: 13px; }
.sug-eva { margin: 3px 0 0; font-size: 11.5px; color: var(--sub); }
.sug-btns { display: flex; gap: 7px; flex-shrink: 0; }
</style>
