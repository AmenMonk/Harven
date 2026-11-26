<template>
  <PageShell :title="t('page.models.t')" icon="bot" :sub="t('page.models.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadAll(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
      <a href="#/models/install"><BigButton :px="14"><HIcon name="download" :size="16" /> 装新模型</BigButton></a>
      <a href="#/vision"><BigButton variant="ghost" :px="14"><HIcon name="image" :size="16" /> 视觉引擎</BigButton></a>
      <BigButton variant="ghost" :px="14" :loading="checkupBusy" @click="runCheckup"><HIcon name="dots" :size="16" /> 🔍 模型体检</BigButton>
    </template>

    <!-- #1557 模型体检报告 -->
    <div v-if="checkupReport" class="checkup">
      <div class="ck-head">
        <b>🔍 模型体检报告</b>
        <span class="ck-sum">✅ 已接入 {{ checkupReport.summary?.integrated || 0 }} · ⚠️ 未登记 {{ checkupReport.summary?.known || 0 }} · ❌ 不认识 {{ checkupReport.summary?.unknown || 0 }}</span>
        <button class="ck-x" @click="checkupReport = null">✕</button>
      </div>
      <div v-for="(it, ii) in checkupReport.items" :key="ii" class="ck-row" :class="it.status">
        <span class="ck-st">{{ it.status === 'integrated' ? '✅' : it.status === 'known' ? '⚠️' : '❌' }}</span>
        <div class="ck-body">
          <b>{{ it.name }}</b>
          <span class="ck-hint">{{ it.hint }}</span>
        </div>
      </div>
    </div>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <!-- ═══ 分区一：外脑 ═══ -->
    <h2 class="sec"><span class="dot cloud" /> 外脑 · 云端大脑 <small>聪明但按量花钱，得配钥匙（API Key）</small></h2>
    <div class="foreign-note">
      当前外脑：<b>{{ currentForeign ? FOREIGN_NAME[currentForeign] || currentForeign : '未选外脑（聊天用本地模型）' }}</b>
      <template v-if="currentForeign && currentModelName"> · 型号：<b>{{ currentModelName }}</b></template>
      <!-- ★ #1326：当前对话走本地时明说，免得误以为外脑选错 -->
      <span v-if="activeLocal" class="fn-note">💬 当前对话走的是本地模型（这里报的是外脑槽位，切回外脑用对话区 🤖按钮）</span>
      <!-- ★ 2026-10-23 设置页瘦身：API Keys 标签撤除，钥匙状态摘要收在这儿 -->
      <span class="fn-note">🔑 钥匙：{{ keysConfigured }}/{{ foreignList.length }} 家已配置（点外脑卡片可填/换/删）</span>
      <span class="fn-note">选了谁就用谁：外脑出问题如实报错，不会偷偷换本地；本地出问题也一样，两边互不干扰。</span>
      <!-- ★ #929-A6 语音利弊提示（2026-09-29） -->
      <span v-if="voiceCap.note" class="fn-note">🔊 {{ voiceCap.note }}</span>
    </div>
    <div class="m-grid">
      <div v-for="p in foreignList" :key="p.id" class="fcard" :class="{ on: currentForeign === p.id, open: openId === p.id }" @click="toggleOpen(p.id)">
        <div class="f-head">
          <b>{{ FOREIGN_NAME[p.id] || p.id }}</b>
          <span class="f-badge" :class="p.configured ? 'ok' : ''">{{ p.configured ? '钥匙已配' : '未配钥匙' }}</span>
        </div>
        <div class="f-masked">{{ p.masked || 'sk-…' }}</div>
        <div v-if="p.model_name" class="f-model">型号：{{ p.model_name }}</div>
        <div v-if="openId === p.id" class="f-detail" @click.stop>
          <input class="key-input" v-model.trim="keyInput" :placeholder="'粘贴 ' + (FOREIGN_NAME[p.id] || p.id) + ' 的 API Key'" spellcheck="false" />
          <div class="f-btns">
            <BigButton :px="13" :loading="busyId === p.id + ':save'" @click="saveKey(p)">保存钥匙</BigButton>
            <BigButton variant="ghost" :px="13" :loading="busyId === p.id + ':use'" @click="useForeign(p)">设为当前外脑</BigButton>
            <BigButton v-if="p.configured" variant="ghost" :px="13" :loading="busyId === p.id + ':del'" @click="deleteKey(p)">删除钥匙</BigButton>
          </div>
          <!-- ★ 2026-09-13 用户反馈#9：外脑型号设置 -->
          <div class="model-name-row">
            <label class="mn-label">型号</label>
            <input class="key-input mn-input" v-model.trim="modelInputs[p.id]" :placeholder="MODEL_HINT[p.id] || '例如 official-model-name'" spellcheck="false" @keyup.enter="saveModelName(p)" />
            <BigButton :px="13" :loading="busyId === p.id + ':mn'" @click="saveModelName(p)">保存型号</BigButton>
          </div>
          <div class="f-hint">型号 = 这家外脑的具体大脑版本（{{ MODEL_HINT[p.id] || '留空则用官方默认' }}）。填错型号聊天会报错，填对才能发挥实力</div>
        </div>
      </div>
    </div>

    <!-- ═══ 分区二：本地通用 ═══ -->
    <h2 class="sec"><span class="dot local" /> 本地 · 通用大脑 <small>免费、离线、隐私最好，点「设为当前」即可切换</small></h2>
    <div v-if="!localLLM.length" class="empty">没找到本地通用模型 —— 去「装新模型」页挑一个合适的</div>
    <div class="m-grid">
      <div v-for="m in localLLM" :key="m.id" class="lcard">
        <span class="l-icon">{{ m.icon }}</span>
        <div class="l-info">
          <b>{{ m.name }}</b>
          <span class="l-meta">{{ m.source }} · {{ m.size_gb != null ? m.size_gb + 'GB' : (m.desc || '本机模型') }}</span>
          <span v-if="isAlsoVertical(m)" class="l-vert-tag">🎯 垂直大脑也可用</span>
        </div>
        <button class="mini" :class="{ cur: currentLocalId === m.id }" :disabled="currentLocalId === m.id" @click="setCurrent(m)">
          {{ currentLocalId === m.id ? '当前使用' : '设为当前' }}
        </button>
      </div>
    </div>

    <!-- ═══ 分区三：本地垂直 ═══ -->
    <h2 class="sec"><span class="dot vert" /> 本地 · 专项大脑（垂直模型）<small>点卡片下方按钮跳去对应专项面板使用（垂直模型在面板里选，不走聊天框）</small></h2>
    <div v-if="!verticals.length" class="empty">还没装专项大脑 —— 音乐工坊、图片生成等模型会出现在这里</div>
    <div class="v-grid">
      <div v-for="v in verticals" :key="v.id || v.name" class="vcard">
        <b class="v-name">{{ v.name || v.model_id || v.id }}</b>
        <span class="v-type">{{ VTYPE_TEXT[v.canvas_type] || v.canvas_type || '专项' }}</span>
        <span class="v-desc">{{ v.capability?.can_do?.[0] || v.description || '' }}</span>
        <span class="v-where">💡 在「{{ VTYPE_PANEL[v.canvas_type] || '更多 → 专项大脑面板' }}」里选用这个模型</span>
      </div>
    </div>
    <!-- ═══ 分区四：听音模型（★ 2026-10-23 自设置页「语音识别」标签迁入：什么模型去哪管） ═══ -->
    <h2 class="sec"><span class="dot voice" /> 🎤 听音模型（Whisper）<small>按住说话、手机喊哈文、音频转文字全靠它在本机听音——装好后不用联网也能用</small></h2>
    <div class="voice-row">
      <div class="row-main"><b>当前使用：{{ voiceModels.active || '—' }}</b><span>跟着「常用」档走；常用档没装时哈文会自动先用别的档顶着，不会哑</span></div>
    </div>
    <div class="v-grid" style="margin-bottom:10px">
      <div v-for="m in voiceModels.models" :key="m.name" class="vcard">
        <b class="v-name">{{ m.label }} · {{ m.size }}</b>
        <span class="v-type">{{ m.installed ? '已安装' : '未安装' }}</span>
        <span class="v-desc">{{ m.note }}</span>
        <div class="voice-btns">
          <span v-if="m.preferred" class="pill ok">常用</span>
          <span v-if="m.active" class="pill ok">使用中</span>
          <template v-else-if="m.installed">
            <button v-if="!m.preferred" class="mini" @click="preferVoice(m.name)">设为常用</button>
            <button class="mini danger" @click="deleteVoice(m.name, m.label)">删除</button>
          </template>
          <template v-else>
            <button class="mini" :disabled="voiceDl.state === 'downloading'" @click="downloadVoice(m.name)">下载</button>
            <button v-if="m.name !== (voiceModels.preferred || 'small')" class="mini" @click="preferVoice(m.name)">设为常用</button>
          </template>
        </div>
      </div>
    </div>
    <div v-if="voiceDl.state === 'downloading'" class="tip ok">⏳ 正在下载「{{ voiceDl.name }}」…{{ voiceDl.msg }}下载完成后自动生效，可点右上角「刷新」看进度</div>
    <div v-else-if="voiceDl.msg" class="tip" :class="voiceDl.state === 'error' ? 'bad' : 'ok'">{{ voiceDl.state === 'error' ? '❌ ' : '✅ ' }}{{ voiceDl.msg }}</div>
    <div class="voice-row">
      <div class="row-main"><b>装哪个好？</b><span>日常说指令、记一句话：装「增强 small」（约 480MB）就够，反应快识别准（默认常用档）；要听长段落/会议录音再上「旗舰 large-v3-turbo」（约 1.5GB）</span></div>
    </div>
    <div class="voice-row">
      <div class="row-main"><b>🔊 外脑听语音</b><span>{{ voiceCap.note || '（读取中…）' }}</span></div>
      <button v-if="voiceCap.has_audio" class="mini" @click="toggleAudioDirect">{{ voiceCap.direct_enabled ? '关闭直通' : '开启直通' }}</button>
    </div>
    <div v-if="voiceCap.has_audio" class="tip">直通 = 语音原声直接发给外脑听（语气语调更活，但按量花钱、语音档案存不了）；关着 = 哈文先在家转文字再发（免费秒转，记录可存可搜）。两种都只影响语音，不打扰别的功能</div>
  </PageShell>
</template>

<script setup>
import { ref, computed } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'

const FOREIGN_NAME = {
  deepseek: 'DeepSeek', doubao: '豆包', qwen: '通义千问', kimi: 'Kimi',
  zhipu: '智谱 GLM', yi: '零一万物', baichuan: '百川', stepfun: '阶跃星辰',
}
const VTYPE_TEXT = { image_generation: '🎨 图片', music_generation: '🎵 音乐', speech: '🗣 语音', code: '💻 代码', translation: '🌐 翻译', medical: '🏥 医疗', data_analysis: '📊 数据' }
// ★ 2026-09-13 用户反馈#10：垂直模型"去哪儿用"指引
const VTYPE_PANEL = { medical: '华佗医疗面板', music_generation: '音乐工坊面板', image_generation: '图片生成面板', speech: '语音面板', code: '代码面板', translation: '翻译面板', data_analysis: '数据分析面板' }
// ★ 2026-09-13 用户反馈#9：外脑型号提示（各家常型号示例）
const MODEL_HINT = {
  deepseek: 'deepseek-chat 或 deepseek-reasoner',
  zhipu: 'glm-4-flash / glm-4-plus',
  qwen: 'qwen-max / qwen-plus',
  kimi: 'moonshot-v1-8k / kimi-k2',
  doubao: 'doubao-种子系列型号',
  yi: 'yi-large 等',
  baichuan: 'baichuan2 系列',
  stepfun: 'step-1 系列',
}

const keys = ref({})
const currentForeign = ref('')
const activeLocal = ref(false)   // ★ #1326 当前对话是否走本地
const localAll = ref([])
const verticals = ref([])
const openId = ref('')
const keyInput = ref('')
const busyId = ref('')
const currentLocalId = ref('')
const modelInputs = ref({})   // ★ #9 外脑型号输入框（按 provider 存）
const msg = ref('')
const msgOk = ref(false)
const voiceCap = ref({ note: '', has_audio: false, direct_enabled: false })   // ★ #929-A6

const keysConfigured = computed(() => Object.values(keys.value).filter(v => v.configured).length)

async function loadVoiceCap() {
  try {
    const j = await getJSON('/api/voice/model_capability')
    voiceCap.value = { has_audio: !!j.has_audio, direct_enabled: !!j.direct_enabled, note: j.note || '' }
  } catch (_) { voiceCap.value = { has_audio: false, direct_enabled: false, note: '（后端版本较旧，暂不支持查询）' } }
}
loadVoiceCap()

/* ── ★ 2026-10-23 听音模型区（自 SettingsPage 迁入）── */
const voiceModels = ref({ models: [], installed: [], active: null, preferred: 'small' })
const voiceDl = ref({ name: null, state: 'idle', msg: '' })
let voicePoll = null

async function loadVoiceModels() {
  try {
    const j = await getJSON('/api/voice/models')
    voiceModels.value = { models: j.models || [], installed: j.installed || [], active: j.active, preferred: j.preferred || 'small' }
    voiceDl.value = j.download || { state: 'idle', msg: '' }
    if (voiceDl.value.state === 'downloading' && !voicePoll) {
      voicePoll = setInterval(async () => {
        try {
          const s = await getJSON('/api/voice/models')
          voiceModels.value = { models: s.models || [], installed: s.installed || [], active: s.active, preferred: s.preferred || 'small' }
          voiceDl.value = s.download || { state: 'idle', msg: '' }
          if (voiceDl.value.state !== 'downloading') { clearInterval(voicePoll); voicePoll = null }
        } catch (_) {}
      }, 3000)
    }
  } catch (_) { /* 语音路由不可用时静默（老版本后端） */ }
}
async function downloadVoice(name) {
  try {
    const j = await postJSON('/api/voice/models/download', { name })
    if (j.success) { voiceDl.value = { name, state: 'downloading', msg: '已开始，正在从国内源下载…' }; loadVoiceModels() }
    else flash(j.error || '下载启动失败', false)
  } catch (e) { flash('下载启动失败：' + e.message, false) }
}
async function deleteVoice(name, label) {
  if (!confirm(`删除「${label}」这个听音模型？删掉后语音识别会换用还装着的模型（也可以随时重新下载）`)) return
  try {
    const j = await postJSON('/api/voice/models/delete', { name })
    if (j.success) flash(j.msg || '已删除')
    else flash(j.error || '删除失败', false)
    await loadVoiceModels()
  } catch (e) { flash('删除失败：' + e.message, false) }
}
async function preferVoice(name) {
  try {
    const j = await postJSON('/api/voice/models/prefer', { name })
    if (j.success) flash(j.msg || '已设为常用')
    else flash(j.error || '设置失败', false)
    await loadVoiceModels()
  } catch (e) { flash('设置失败：' + e.message, false) }
}
async function toggleAudioDirect() {
  try {
    const j = await postJSON('/api/voice/audio_direct', { enabled: !voiceCap.value.direct_enabled })
    if (j.success) { voiceCap.value.direct_enabled = !voiceCap.value.direct_enabled; flash(j.msg || '已切换') }
    else flash(j.error || '切换失败', false)
  } catch (e) { flash('切换失败：' + e.message, false) }
}
loadVoiceModels()

const foreignList = computed(() => Object.entries(keys.value).map(([id, v]) => ({ id, ...v })))
const currentModelName = computed(() => foreignList.value.find(p => p.id === currentForeign.value)?.model_name || '')
const localLLM = computed(() => localAll.value.filter(m => m.type === 'llm'))

// ★ 2026-09-14 用户反馈#3：通用区标注"垂直大脑也可用"（该模型同时出现在垂直清单里，如 minicpm-v 视觉）
function isAlsoVertical(m) {
  const hay = `${m.id || ''} ${m.name || ''}`.toLowerCase()
  return verticals.value.some(v => {
    const vm = `${v.ollama_model || ''} ${v.id || ''} ${v.name || ''}`.toLowerCase()
    return vm.includes(hay) || hay.includes(v.ollama_model || '\u0000')
  })
}

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 5000)
}

function toggleOpen(id) {
  openId.value = openId.value === id ? '' : id
  keyInput.value = ''
  if (openId.value) {
    // ★ #9：展开时把已存型号填进输入框
    const p = foreignList.value.find(x => x.id === id)
    modelInputs.value[id] = p?.model_name || ''
  }
}

async function loadAll(showErr = false) {
  try {
    const [k, cf, la, vm] = await Promise.all([
      getJSON('/api/models/api-keys'),
      getJSON('/api/models/current-foreign'),
      getJSON('/api/models/local-all').catch(() => ({ models: [] })),
      getJSON('/api/vertical/models').catch(() => ({ models: [] })),
    ])
    keys.value = k.keys || {}
    currentForeign.value = (cf.model || '').trim()
    activeLocal.value = !!(cf.active_is_local)
    localAll.value = la.models || []
    verticals.value = vm.models || []
    // ★ #10 修复：当前本地模型从后端初始化（之前只在点击后才记，刷新页面"当前使用"标记就丢了）
    if (la.current) currentLocalId.value = la.current
    if (showErr) msg.value = ''
  } catch (e) { if (showErr) { flash('加载失败：' + e.message, false) } }
}

async function saveKey(p) {
  if (!keyInput.value) { flash('先粘贴钥匙再保存', false); return }
  busyId.value = p.id + ':save'
  try {
    await postJSON('/api/models/api-keys', { keys: { [p.id]: keyInput.value } })
    flash(`${FOREIGN_NAME[p.id] || p.id} 的钥匙已保存`)
    keyInput.value = ''
    await loadAll()
  } catch (e) { flash('保存失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function useForeign(p) {
  if (!p.configured) { flash(`${FOREIGN_NAME[p.id] || p.id} 还没配钥匙，先保存钥匙再切换（否则聊天会报 401 无效）`, false); return }
  busyId.value = p.id + ':use'
  try {
    await postJSON('/api/models/switch-foreign', { model: p.id })
    flash(`已切换外脑到 ${FOREIGN_NAME[p.id] || p.id}`)
    await loadAll()
  } catch (e) { flash('切换失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function deleteKey(p) {
  if (!confirm(`删除 ${FOREIGN_NAME[p.id] || p.id} 已保存的钥匙？删除后需重新粘贴才能用这家外脑`)) return
  busyId.value = p.id + ':del'
  try {
    await postJSON('/api/models/api-keys', { delete: [p.id], keys: {} })
    flash(`${FOREIGN_NAME[p.id] || p.id} 的钥匙已删除`)
    if (currentForeign.value === p.id) flash(`钥匙已删除。注意当前外脑还是 ${FOREIGN_NAME[p.id] || p.id}，建议另选一家设为当前`, false)
    await loadAll()
  } catch (e) { flash('删除失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function saveModelName(p) {
  const name = (modelInputs.value[p.id] || '').trim()
  busyId.value = p.id + ':mn'
  try {
    if (!name) { flash('型号留空 = 用官方默认。要填就填全名，比如 deepseek-chat', false); busyId.value = ''; return }
    await postJSON('/api/models/foreign-model-name', { provider: p.id, model_name: name })
    flash(`${FOREIGN_NAME[p.id] || p.id} 型号已设为 ${name}`)
    await loadAll()
  } catch (e) { flash('保存型号失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function setCurrent(m) {
  try {
    const j = await postJSON('/api/models/set-current', { model_id: m.id })
    // ★ R4 2026-09-23 显存换班提示：大模型加载要几十秒，提前说清避免以为卡死
    if (j.success) { currentLocalId.value = m.id; flash(`当前通用大脑已换成 ${m.name}，正在加载到显存（约需 30 秒），期间第一次回复会稍慢，不是卡死`) }
    else flash('切换失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('切换失败：' + e.message, false) }
}

/* #1557 模型体检 */
const checkupBusy = ref(false)
const checkupReport = ref(null)
async function runCheckup() {
  checkupBusy.value = true
  try {
    checkupReport.value = await getJSON('/api/vertical/checkup')
  } catch (e) {
    flash('体检失败：' + e.message, false)
  } finally {
    checkupBusy.value = false
  }
}

loadAll(true)
</script>

<style scoped>
/* #1557 体检报告 */
.checkup { border: 1px solid var(--line); background: #fff; border-radius: 12px; padding: 14px; margin-bottom: 14px; display: flex; flex-direction: column; gap: 8px; }
.ck-head { display: flex; align-items: center; gap: 10px; }
.ck-head b { font-size: 14px; color: var(--ink); }
.ck-sum { font-size: 12px; color: var(--ink-3, #8b949e); }
.ck-x { margin-left: auto; border: none; background: none; cursor: pointer; color: var(--ink-3, #8b949e); font-size: 14px; }
.ck-row { display: flex; gap: 9px; align-items: baseline; padding: 7px 10px; border-radius: 8px; background: rgba(46,160,67,.05); }
.ck-row.known { background: rgba(255,212,0,.08); }
.ck-row.unknown { background: rgba(229,83,75,.06); }
.ck-st { flex-shrink: 0; }
.ck-body { display: flex; flex-direction: column; gap: 2px; }
.ck-body b { font-size: 12.5px; color: var(--ink); }
.ck-hint { font-size: 11.5px; color: var(--ink-3, #8b949e); line-height: 1.5; }
</style><style scoped>
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }

.sec { display: flex; align-items: baseline; gap: 8px; font-size: 15px; margin: 20px 0 11px; }
.sec small { font-size: 12px; color: var(--sub); font-weight: 400; }
.dot { width: 9px; height: 9px; border-radius: 50%; align-self: center; }
.dot.cloud { background: var(--mode-cloud); }
.dot.local { background: var(--mode-local); }
.dot.vert { background: var(--mode-vertical); }
.dot.voice { background: #8B72E0; }

/* ★ 2026-10-23 听音模型区 */
.voice-row { display: flex; align-items: center; gap: 10px; padding: 7px 2px; }
.voice-row .row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.voice-row .row-main b { font-size: 13px; }
.voice-row .row-main span { font-size: 12px; color: var(--sub); }
.voice-btns { display: flex; align-items: center; gap: 7px; margin-top: 7px; flex-wrap: wrap; }
.voice-btns .pill { font-size: 11px; padding: 2px 9px; border-radius: 20px; }
.mini.danger:hover { border-color: #D9534F; color: #D9534F; }

.foreign-note { font-size: 12.5px; color: var(--sub); background: var(--mode-cloud-bg); border-radius: 9px; padding: 7px 12px; margin-bottom: 11px; }
.foreign-note b { color: var(--mode-cloud); }
.foreign-note .fn-note { display: block; margin-top: 3px; color: var(--sub); }

.m-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; }
.fcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px; cursor: pointer; }
.fcard.on { border-color: var(--mode-cloud); box-shadow: 0 0 0 1px var(--mode-cloud); }
.f-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.f-badge { font-size: 11px; padding: 2px 8px; border-radius: 20px; background: #FDF2F2; color: #B4443C; }
.f-badge.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.f-masked { font-size: 12px; color: var(--sub); margin-top: 5px; font-family: monospace; }
.f-model { font-size: 11.5px; color: var(--mode-cloud); margin-top: 3px; font-weight: 700; }
.model-name-row { display: flex; align-items: center; gap: 8px; }
.mn-label { font-size: 12px; font-weight: 700; color: var(--ink); flex-shrink: 0; }
.mn-input { flex: 1; min-width: 0; }
.f-detail { margin-top: 9px; border-top: 1px dashed var(--line); padding-top: 9px; display: flex; flex-direction: column; gap: 8px; }
.key-input { border: 1px solid var(--line); border-radius: 9px; padding: 8px 11px; font-size: 12.5px; outline: none; background: #fff; color: var(--ink); }
.key-input:focus { border-color: var(--blue); }
.f-btns { display: flex; gap: 8px; }
.f-hint { font-size: 11px; color: var(--sub); }

.lcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px; display: flex; align-items: center; gap: 11px; }
.l-icon { font-size: 22px; }
.l-info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.l-info b { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.l-meta { font-size: 11.5px; color: var(--sub); }
.l-vert-tag { font-size: 10.5px; color: var(--mode-vertical); font-weight: 700; border: 0.5px solid var(--mode-vertical); border-radius: 6px; padding: 1px 6px; width: fit-content; }
.mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 5px 12px; cursor: pointer; color: var(--ink); }
.mini:hover:not(:disabled) { border-color: var(--blue); color: var(--blue); }
.mini.cur, .mini:disabled { border-color: var(--green); color: var(--green); cursor: default; font-weight: 700; }

.v-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; }
.vcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.v-where { font-size: 11.5px; color: var(--sub); background: var(--c-bg-soft); border-radius: 8px; padding: 5px 8px; margin-top: 3px; line-height: 1.5; }
.v-name { font-size: 13.5px; }
.v-type { font-size: 11.5px; color: var(--mode-vertical); font-weight: 700; }
.v-desc { font-size: 11.5px; color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.empty { padding: 24px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13px; margin-bottom: 10px; }

@media (max-width: 1000px) { .m-grid, .v-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 660px) { .m-grid, .v-grid { grid-template-columns: 1fr; } }
</style>
