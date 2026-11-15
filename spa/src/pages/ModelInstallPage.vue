<template>
  <PageShell :title="t('page.modelInstall.t')" icon="download" :sub="t('page.modelInstall.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadHW"><HIcon name="pulse" :size="16" /> 重新检测电脑</BigButton>
    </template>

    <div v-if="hw" class="hw-bar">
      🖥️ 你的电脑：<b>{{ hw.gpu_name || '未识别显卡' }}</b> · 显存 <b>{{ hw.vram_gb }}GB</b> · 内存 <b>{{ hw.ram_gb }}GB</b>
      <span class="hw-hint">绿色 = 显存装得下很轻松 · 黄色 = 刚好够用 · 红色 = 超了，装了可能跑不动</span>
    </div>

    <div class="tool-row">
      <div class="search">
        <HIcon name="search" :size="16" />
        <input v-model.trim="q" placeholder="搜索模型名，比如 qwen、llama、musicgen（回车搜索）" @keyup.enter="search" />
      </div>
      <select v-model="category" class="cat" @change="search">
        <option value="all">全部分类</option>
        <option value="llm">对话模型</option>
        <option value="music">音乐</option>
        <option value="image">图片</option>
        <option value="speech">语音</option>
        <option value="embedding">向量/检索</option>
      </select>
      <BigButton :px="14" :loading="searching" @click="search">搜索</BigButton>
    </div>

    <div v-if="msg" class="tip bad">{{ msg }}</div>
    <div v-if="!q && !models.length" class="empty">上方搜索框输入模型名开始 —— 首次进入不列任何模型（零推荐原则，选什么由你决定）</div>
    <div v-else-if="!models.length && !searching" class="empty">没有找到「{{ q }}」相关的模型</div>

    <div class="mi-list">
      <div v-for="m in models" :key="m.id" class="micard">
        <div class="mi-main">
          <div class="mi-head">
            <b>{{ m.name }}</b>
            <span class="mi-cat">{{ m.category?.toUpperCase() }}</span>
            <span v-if="m.installed" class="mi-installed">✓ 已安装</span>
            <span v-else class="fit" :class="fitClass(m)">{{ fitText(m) }}</span>
          </div>
          <p class="mi-desc">{{ m.install?.desc || m.description || '（没有描述）' }}</p>
          <div class="mi-meta">
            <span>体积 ≈ {{ m.model_size || '?' }}GB</span>
            <span v-if="m.license">许可：{{ m.license }}</span>
            <span v-if="m.install?.type">来源：{{ m.install.type }}</span>
          </div>
        </div>
        <div v-if="pull[m.id]" class="pull-wrap">
          <div class="pull-bar"><div class="pull-fill" :class="{ done: pull[m.id].status === 'success', bad: pull[m.id].error }" :style="{ width: (pull[m.id].pct || 0) + '%' }"></div></div>
          <span class="pull-text" :class="{ bad: pull[m.id].error }">
            {{ pull[m.id].error ? ('下载失败：' + pull[m.id].error) : (pull[m.id].status === 'success' ? '下载完成 ✅' : '正在下载 ' + (pull[m.id].pct || 0) + '%（Ollama 直连，走国内加速）') }}
          </span>
        </div>
        <BigButton v-if="!m.installed" :px="14" :loading="installing === m.id" :disabled="!!pull[m.id] && pull[m.id].status !== 'error'" @click="install(m)">
          {{ m.install?.type === 'ollama' ? 'Ollama 安装' : '安装' }}
        </BigButton>
        <span v-else class="ok-mark">已就绪</span>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'

const hw = ref(null)
const q = ref('')
const category = ref('all')
const models = ref([])
const searching = ref(false)
const installing = ref('')
const msg = ref('')
// ★ 2026-09-13 ollama 下载进度条：pull[model_id] = { pct, status, error }
const pull = ref({})
const pullTimers = {}

function fitRatio(m) {
  const size = Number(m.model_size) || 0
  const vram = Number(hw.value?.vram_gb) || 0
  if (!size || !vram) return 1
  return size / vram
}
function fitClass(m) {
  const r = fitRatio(m)
  if (r <= 0.6) return 'good'
  if (r <= 1) return 'tight'
  return 'over'
}
function fitText(m) {
  const r = fitRatio(m)
  if (r <= 0.6) return '✓ 显存充裕'
  if (r <= 1) return '△ 显存刚好'
  return '✕ 超过显存'
}

async function loadHW() {
  try { hw.value = await getJSON('/api/models/installer/hardware') } catch (_) {}
}

async function search() {
  searching.value = true
  msg.value = ''
  try {
    const j = await getJSON(`/api/models/installer/search?q=${encodeURIComponent(q.value)}&category=${category.value}`)
    models.value = j.models || []
    if (j.hardware) hw.value = j.hardware
  } catch (e) { msg.value = '搜索失败：' + e.message; models.value = [] }
  finally { searching.value = false }
}

// ★ 2026-09-13 安装分流修复：ollama 来源模型错走 /api/models/install（HF 镜像下载，repo 不存在必失败）
//   ollama 型 → POST /api/ollama/pull + pull-status 轮询进度条
//   pip / MODEL_CATALOG / HF 型 → 维持原路径
async function install(m) {
  const isOllama = m.install?.type === 'ollama'
  // ollama 真名（如 qwen2.5:14b）由后端推导，前端只用它；没有就退回 id
  const pullName = m.install?.package || m.id
  if (!confirm(`开始下载安装「${m.name}」（约 ${m.model_size || '?'}GB）？\n下载时间取决于网速，请保持网络畅通。`)) return
  installing.value = m.id
  try {
    if (isOllama) {
      try {
        await postJSON('/api/ollama/pull', { model: pullName })
      } catch (e) {
        // 409 = 本地 Ollama 里已经装过
        if (String(e.message || '').includes('409') || String(e.message || '').includes('已安装')) {
          m.installed = true
          alert('这个模型本地已经装过了 ✅ 到「模型中心」把它设为当前模型即可使用')
          return
        }
        throw e
      }
      startPullPoll(m, pullName)
    } else {
      const j = await postJSON('/api/models/install', { model_id: m.id })
      if (j.success) {
        m.installed = true
        alert('安装成功 ✅ 到「模型中心」把它设为当前模型即可使用')
      } else msg.value = '安装失败：' + (j.error || j.message || '未知错误')
    }
  } catch (e) { msg.value = '安装失败：' + e.message }
  finally { installing.value = '' }
}

function startPullPoll(m, name) {
  clearInterval(pullTimers[m.id])
  pull.value[m.id] = { pct: 0, status: 'pulling', error: '' }
  pullTimers[m.id] = setInterval(async () => {
    try {
      const j = await getJSON('/api/ollama/pull-status?model=' + encodeURIComponent(name))
      let pct = Number(j.progress) || 0
      if (pct <= 1) pct = pct * 100 // 兼容 0~1 小数与 0~100 两种进度格式
      pull.value[m.id] = { pct: Math.round(pct), status: j.status || 'pulling', error: j.error || '' }
      // 后端成功态是 'done'（也兼容 'success'）
      if (j.status === 'done' || j.status === 'success') {
        stopPullPoll(m.id)
        m.installed = true
        delete pull.value[m.id]
        alert('下载完成 ✅ 到「模型中心」把它设为当前模型即可使用')
      } else if (j.status === 'error' || j.error) {
        stopPullPoll(m.id) // 保留错误状态展示，按钮恢复可点可重试
      }
    } catch (_) { /* 单次轮询失败不打断，下一轮再试 */ }
  }, 2000)
}
function stopPullPoll(id) { clearInterval(pullTimers[id]); delete pullTimers[id] }
onUnmounted(() => Object.keys(pullTimers).forEach(stopPullPoll))

loadHW()
</script>

<style scoped>
.hw-bar { font-size: 12.5px; color: var(--sub); background: #fff; border: 1px solid var(--line); border-radius: 11px; padding: 10px 14px; margin-bottom: 13px; }
.hw-bar b { color: var(--ink); }
.hw-hint { display: block; margin-top: 3px; font-size: 11px; color: var(--sub); }

.tool-row { display: flex; gap: 10px; margin-bottom: 14px; }
.search { flex: 1; display: flex; align-items: center; gap: 8px; background: #fff; border: 1px solid var(--line); border-radius: 11px; padding: 9px 13px; color: var(--sub); }
.search input { flex: 1; border: none; outline: none; font-size: 13px; background: transparent; color: var(--ink); }
.cat { border: 1px solid var(--line); border-radius: 11px; padding: 8px 11px; font-size: 12.5px; background: #fff; color: var(--ink); outline: none; }

.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; background: #FDF2F2; color: #B4443C; }
.empty { padding: 34px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13.5px; }

.mi-list { display: flex; flex-direction: column; gap: 10px; }
.micard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 14px 16px; display: flex; align-items: center; gap: 14px; }
.mi-main { flex: 1; min-width: 0; }
.mi-head { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.mi-head b { font-size: 14.5px; }
.mi-cat { font-size: 10.5px; font-weight: 800; background: var(--c-bg-soft); color: var(--sub); border-radius: 6px; padding: 2px 7px; }
.mi-installed { font-size: 12px; color: var(--green); font-weight: 700; }
.fit { font-size: 11.5px; font-weight: 700; padding: 2px 9px; border-radius: 20px; }
.fit.good { background: var(--mode-local-bg); color: var(--mode-local); }
.fit.tight { background: var(--mode-vertical-bg); color: var(--mode-vertical); }
.fit.over { background: #FDF2F2; color: #B4443C; }
.mi-desc { font-size: 12.5px; color: var(--sub); margin: 4px 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mi-meta { display: flex; gap: 14px; font-size: 11.5px; color: var(--sub); }
.ok-mark { color: var(--green); font-weight: 700; font-size: 13px; white-space: nowrap; }

/* ★ ollama 下载进度条 */
.pull-wrap { width: 100%; margin-top: 8px; }
.pull-bar { height: 7px; border-radius: 20px; background: var(--c-bg-soft); overflow: hidden; }
.pull-fill { height: 100%; border-radius: 20px; background: var(--blue); transition: width .8s ease; }
.pull-fill.done { background: var(--green); }
.pull-fill.bad { background: var(--red); }
.pull-text { display: block; margin-top: 4px; font-size: 11.5px; color: var(--sub); }
.pull-text.bad { color: var(--red); }
.micard { flex-wrap: wrap; }
</style>
