<template>
  <PageShell :title="t('page.vertical.t')" icon="palette" :sub="t('page.vertical.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="$router.push('/models')"><HIcon name="cpu" :size="16" /> 管理模型</BigButton>
    </template>

    <!-- ═══ 模型卡墙 ═══ -->
    <div v-if="!current">
      <div v-if="loading" class="empty">正在加载专项模型清单…</div>
      <div v-else-if="!models.length" class="empty">
        这里只会显示<b>实际装好、能用的</b>专项模型（装好后自动出现）——
        去 <router-link to="/models">模型中心</router-link>「本地垂直」下载，
        或把电脑里已有的模型通过 Ollama 导入后刷新本页
      </div>
      <div v-else class="m-grid">
        <button v-for="m in models" :key="m.id" class="m-card" @click="pick(m)">
          <span class="m-ic">{{ m.icon && m.icon.length <= 3 ? m.icon : '🧩' }}</span>
          <b class="m-name">{{ m.name }}</b>
          <span class="m-desc">{{ m.description || VTYPE[m.type] || '专项任务' }}</span>
          <span class="m-type">{{ VTYPE[m.type] || m.canvas_type || '专项' }}</span>
        </button>
      </div>
    </div>

    <!-- ═══ 模型直答页 ═══ -->
    <div v-else class="detail">
      <div class="d-head">
        <button class="back" @click="current = null">← 返回模型列表</button>
        <span class="d-ic">{{ current.icon && current.icon.length <= 3 ? current.icon : '🧩' }}</span>
        <div class="d-title">
          <b>{{ current.name }}</b>
          <span class="d-desc">{{ current.description }}</span>
        </div>
      </div>

      <!-- 能力边界（参谋提示） -->
      <div v-if="capText" class="cap-box">
        <b>🎯 它能干什么 / 不能干什么</b>
        <div class="cap-row" v-if="current.capability?.can_do?.length"><span class="cap-k ok">能</span>{{ current.capability.can_do.join('；') }}</div>
        <div class="cap-row" v-if="current.capability?.cannot_do?.length"><span class="cap-k no">不能</span>{{ current.capability.cannot_do.join('；') }}</div>
        <div class="cap-row" v-if="current.capability?.advisor_tips"><span class="cap-k tip">建议</span>{{ current.capability.advisor_tips }}</div>
      </div>
      <div v-if="current.license_warning" class="lic">{{ current.license_warning }}</div>

      <!-- 表单 -->
      <div class="form">
        <!-- ★ 2026-09-15 开发者令#1：附件通道 —— 图片/音频/视频/文章随表单发送 -->
        <div class="f-row">
          <label>📎 附上文件（可选）<span class="att-hint">图片/视频给看图模型看，音频帮你转成文字，文章直接读</span></label>
          <div class="att-bar">
            <button class="att-btn" type="button" @click="vattEl?.click()">＋ 选择文件</button>
            <span class="att-limits">图片≤10MB · 音频≤25MB · 视频≤60MB · 文章≤5MB，最多 4 个</span>
          </div>
          <input ref="vattEl" type="file" multiple hidden
                 accept="image/*,audio/*,video/*,.txt,.md,.docx,.pdf" @change="onAttPicked" />
          <div v-if="atts.length" class="att-chips">
            <div v-for="(a, ai) in atts" :key="ai" class="att-chip">
              <span class="att-ic">{{ attIcon(a) }}</span>
              <span class="att-name" :title="a.name">{{ a.name }}</span>
              <button class="att-x" title="移除" @click="atts.splice(ai, 1)">✕</button>
            </div>
          </div>
        </div>
        <div v-for="(p, key) in formFields" :key="key" class="f-row">
          <label>{{ p.label || key }}<i v-if="p.required" class="req">*</i></label>

          <textarea v-if="p.type === 'textarea'" v-model="form[key]" :placeholder="p.placeholder || ''" rows="3" />

          <select v-else-if="p.type === 'select' && p.options" v-model="form[key]">
            <option v-for="o in p.options" :key="o" :value="o">{{ o }}</option>
          </select>

          <div v-else-if="p.type === 'range'" class="range-row">
            <input type="range" :min="p.min" :max="p.max" :step="p.step" v-model.number="form[key]" />
            <span class="range-val">{{ form[key] }}</span>
          </div>

          <input v-else v-model="form[key]" :placeholder="p.placeholder || ''" />
        </div>
        <div v-if="!Object.keys(formFields).length" class="empty" style="margin:0">这个模型不需要填参数，直接点下面的按钮</div>
      </div>

      <div class="run-row">
        <div class="run-col">
          <BigButton :px="20" :loading="running" @click="run">{{ runLabel }}</BigButton>
          <div v-if="running" class="run-tip">{{ runTip }}</div>
        </div>
      </div>

      <!-- 结果 -->
      <div v-if="errMsg" class="result err">⚠️ {{ errMsg }}</div>
      <div v-else-if="result" class="result">
        <img v-if="isImage" :src="result.file" class="r-img" />
        <audio v-else-if="isAudio" controls :src="result.file" class="r-audio" />
        <pre v-else class="r-text">{{ result.output || '（模型没有返回内容）' }}</pre>
        <div v-if="result.file && !isImage && !isAudio" class="r-file">生成文件：<a :href="result.file" target="_blank">{{ result.file }}</a></div>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import BigButton from '../components/BigButton.vue'
import HIcon from '../components/HIcon.vue'
import { getJSON, postJSON } from '../api.js'

/* ═══ 类型中文名 ═══ */
const VTYPE = {
  image_generation: '🎨 图片生成',
  music_generation: '🎵 音乐生成',
  speech_synthesis: '🗣️ 语音合成',
  medical_qa: '🩺 医疗问答',
  code_generation: '💻 代码生成',
  data_analysis: '📊 数据分析',
  translation: '🌐 翻译',
}

/* ═══ 数据 ═══ */
const models = ref([])
const loading = ref(true)
const current = ref(null)
const form = reactive({})
const running = ref(false)
const result = ref(null)
const errMsg = ref('')

/* ★ 2026-09-15 开发者令#1：附件通道 */
const vattEl = ref(null)
const atts = ref([]) // {name, type, dataUrl}
const ATT_LIMIT = { image: 10, audio: 25, video: 60, other: 5 } // MB
function attIcon(a) {
  if ((a.type || '').startsWith('image/')) return '🖼️'
  if ((a.type || '').startsWith('audio/')) return '🎵'
  if ((a.type || '').startsWith('video/')) return '🎬'
  return '📄'
}
function pickAttachments() { vattEl.value?.click() }
function onAttPicked(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  for (const f of files) {
    const kind = (f.type || '').startsWith('image/') ? 'image'
      : (f.type || '').startsWith('audio/') ? 'audio'
      : (f.type || '').startsWith('video/') ? 'video' : 'other'
    const cap = ATT_LIMIT[kind]
    if (f.size > cap * 1024 * 1024) {
      errMsg.value = `「${f.name}」超过 ${cap}MB 上限`
      continue
    }
    if (atts.value.length >= 4) { errMsg.value = '一次最多带 4 个文件'; break }
    const reader = new FileReader()
    reader.onload = () => atts.value.push({ name: f.name, type: f.type, dataUrl: reader.result })
    reader.readAsDataURL(f)
  }
}

const formFields = computed(() => (current.value?.params) || {})
const isImage = computed(() => /image|stable.?diffusion/i.test(current.value?.type || current.value?.canvas_type || ''))
const isAudio = computed(() => /music|speech|audio|tts/i.test(current.value?.type || current.value?.canvas_type || ''))
const runLabel = computed(() => {
  if (isImage.value) return '🎨 开始生成'
  if (/music/i.test(current.value?.type || '')) return '🎵 开始作曲'
  if (/speech|tts/i.test(current.value?.type || '')) return '🗣️ 开始合成'
  return '🚀 开始执行'
})
const capText = computed(() => {
  const c = current.value?.capability
  return c && (c.can_do?.length || c.cannot_do?.length || c.advisor_tips)
})
/* 执行中的等待提示（本地大模型推理可能要几分钟） */
const runTip = computed(() => {
  if (/music/i.test(current.value?.type || '')) return '本地作曲比较慢，可能要 2~8 分钟，别关页面…'
  if (/image/i.test(current.value?.type || '')) return '正在画图，一般 20 秒~2 分钟…'
  if (/speech|tts/i.test(current.value?.type || '')) return '正在合成语音…'
  return '本地大模型正在思考，可能要 1~5 分钟，别关页面…'
})

/* ═══ 加载模型清单 ═══ */
onMounted(async () => {
  try {
    const j = await getJSON('/api/vertical/models')
    models.value = j.models || []
  } catch (e) {
    console.warn('垂直模型加载失败', e)
  } finally {
    loading.value = false
  }
})

/* ═══ 选模型 → 初始化表单默认值 ═══ */
function pick(m) {
  current.value = m
  result.value = null
  errMsg.value = ''
  atts.value = []
  Object.keys(form).forEach(k => delete form[k])
  for (const [k, p] of Object.entries(m.params || {})) form[k] = p.default ?? ''
}

/* ═══ 执行直答 ═══ */
async function run() {
  running.value = true
  result.value = null
  errMsg.value = ''
  try {
    const params = {}
    for (const [k, p] of Object.entries(formFields.value)) {
      const v = form[k]
      if (p.type === 'range') params[k] = Number(v)
      else params[k] = v ?? ''
    }
    const j = await postJSON('/api/vertical/execute', {
      model_id: current.value.id,
      params,
      action: 'generate',
      attachments: atts.value, // ★ 2026-09-15 附件通道（图片/音频/视频/文章）
    })
    // ★ 修复 2026-09-14：后端包裹返回 {success, data:{output/file/error}}（旧UI读的就是 result.data）
    // 错误可能在 data.error（引擎返回不抛异常）或顶层 error（HTTP 500 由 postJSON 抛出）
    const inner = (j && typeof j === 'object' && 'data' in j && j.data) ? j.data : j
    if (inner && inner.error) {
      errMsg.value = inner.error
    } else if (j && j.success === false) {
      errMsg.value = j.error || '执行失败'
    } else {
      result.value = inner || {}
      atts.value = [] // 发送成功清空附件
    }
  } catch (e) {
    errMsg.value = e.message || '执行失败'
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.empty { padding: 26px; text-align: center; color: var(--ink-3, #8b949e); font-size: 13px; border: 1px dashed var(--line); border-radius: 10px; }
.m-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.m-card { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; text-align: left; border: 1px solid var(--line); background: #fff; border-radius: 12px; padding: 14px; cursor: pointer; transition: box-shadow .15s, transform .15s; }
.m-card:hover { box-shadow: 0 4px 14px rgba(0,0,0,.08); transform: translateY(-2px); }
.m-ic { font-size: 26px; line-height: 1; }
.m-name { font-size: 14.5px; color: var(--ink); }
.m-desc { font-size: 12px; color: var(--ink-3, #8b949e); line-height: 1.5; }
.m-type { font-size: 11px; color: var(--accent, #5B8DEF); background: rgba(91,141,239,.1); padding: 2px 8px; border-radius: 999px; }

.detail { display: flex; flex-direction: column; gap: 14px; }
.d-head { display: flex; align-items: center; gap: 12px; }
.back { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12.5px; padding: 5px 12px; cursor: pointer; color: var(--ink); flex-shrink: 0; }
.d-ic { font-size: 30px; }
.d-title { display: flex; flex-direction: column; gap: 2px; }
.d-title b { font-size: 16px; color: var(--ink); }
.d-desc { font-size: 12.5px; color: var(--ink-3, #8b949e); }

.cap-box { border: 1px solid var(--line); background: rgba(91,141,239,.04); border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 7px; }
.cap-box > b { font-size: 13px; color: var(--ink); }
.cap-row { font-size: 12.5px; color: var(--ink-2, #444); line-height: 1.6; display: flex; gap: 8px; align-items: baseline; }
.cap-k { flex-shrink: 0; font-size: 11px; padding: 1px 7px; border-radius: 999px; }
.cap-k.ok { color: #1a7f37; background: rgba(46,160,67,.12); }
.cap-k.no { color: #9a3412; background: rgba(249,115,22,.12); }
.cap-k.tip { color: #5B8DEF; background: rgba(91,141,239,.12); }
.lic { font-size: 12px; color: #9a6700; background: rgba(255,212,0,.1); border-radius: 8px; padding: 8px 12px; }

.form { display: flex; flex-direction: column; gap: 12px; border: 1px solid var(--line); background: #fff; border-radius: 12px; padding: 16px; }

/* ★ 2026-09-15 附件通道 */
.att-hint { font-weight: 400; font-size: 11.5px; color: var(--ink-3, #8b949e); margin-left: 6px; }
.att-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.att-btn { border: 1px dashed var(--accent, #5B8DEF); background: rgba(91,141,239,.06); color: var(--accent, #5B8DEF); border-radius: 8px; padding: 6px 14px; font-size: 12.5px; cursor: pointer; }
.att-btn:hover { background: rgba(91,141,239,.14); }
.att-limits { font-size: 11px; color: var(--ink-3, #8b949e); }
.att-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.att-chip { display: flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 4px 10px; max-width: 260px; }
.att-ic { font-size: 14px; }
.att-name { font-size: 12px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 170px; }
.att-x { border: none; background: none; color: var(--ink-3, #8b949e); cursor: pointer; font-size: 11px; padding: 0 2px; }
.att-x:hover { color: var(--red, #e5534b); }
.f-row { display: flex; flex-direction: column; gap: 6px; }
.f-row label { font-size: 13px; font-weight: 600; color: var(--ink); }
.req { color: var(--red, #e5534b); font-style: normal; margin-left: 3px; }
.f-row textarea, .f-row input, .f-row select { border: 1px solid var(--line); border-radius: 8px; padding: 8px 11px; font-size: 13px; font-family: inherit; background: #fff; color: var(--ink); }
.f-row textarea:focus, .f-row input:focus, .f-row select:focus { outline: none; border-color: var(--accent, #5B8DEF); }
.range-row { display: flex; align-items: center; gap: 10px; }
.range-row input { flex: 1; }
.range-val { font-size: 12.5px; color: var(--accent, #5B8DEF); min-width: 34px; text-align: right; }

.run-row { display: flex; justify-content: center; }
.run-col { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.run-tip { font-size: 12.5px; color: var(--ink-3, #8b949e); }
.result { border: 1px solid var(--line); background: #fff; border-radius: 12px; padding: 14px; }
.result.err { color: var(--red, #e5534b); font-size: 13px; }
.r-img { width: 100%; border-radius: 8px; }
.r-audio { width: 100%; }
.r-text { white-space: pre-wrap; font-size: 13.5px; line-height: 1.7; color: var(--ink); font-family: inherit; margin: 0; }
.r-file { font-size: 12px; color: var(--ink-3, #8b949e); margin-top: 8px; }
</style>
