<template>
  <PageShell :title="t('page.more.t')" icon="palette" :sub="t('page.more.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadAll(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
    </template>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <!-- ★ 快捷入口（撤顶导航后：模型中心/创作工坊从这里进） -->
    <div class="quick-links">
      <router-link to="/models" class="q-card"><b>🧠 模型中心</b><span>外脑钥匙 · 本地模型 · 专项大脑管理</span></router-link>
      <router-link to="/studio" class="q-card"><b>🎨 创作工坊</b><span>音乐/图片等创作历史</span></router-link>
      <router-link to="/timeline" class="q-card"><b>🕒 对话时间轴</b><span>按日/按月翻全部聊天</span></router-link>
      <router-link to="/photos" class="q-card"><b>📷 图片库</b><span>全部图片的总存放地：面板存档/聊天发图/手机拍照统一归档</span></router-link>
      <router-link to="/memory" class="q-card"><b>🌅 记忆相册</b><span>手机生活照 AI 相册：一句话找照片 · AI 修图 · 编故事</span></router-link>
    </div>

    <!-- ★ 2026-09-17 #737 创造区入口（造Agent / 造Skill / 改哈文提案审批） -->
    <!-- ★ 2026-09-21 (#827) 入口收敛：创作归创造页，面板新建归面板页，本卡只做指路 -->
    <router-link to="/create" class="create-card">
      <span class="cc-icon">🛠️</span>
      <span class="cc-main"><b>创造</b><span>造 Agent · 造 Skill · 改哈文（提案审批）—— 要做记账表/台账这类表格面板，去「面板」页右上角新建</span></span>
      <span v-if="evoPending" class="cc-badge">{{ evoPending }} 条提案待审批</span>
      <span class="cc-arrow">→</span>
    </router-link>

    <h2 class="sec"><HIcon name="palette" :size="17" /> 专项大脑（垂直模型）<small>点卡片进「垂直大脑」页，表单直答</small></h2>
    <div v-if="!verticals.length" class="empty">还没装专项模型</div>
    <div class="v-grid">
      <button v-for="v in verticals" :key="v.id || v.name" class="vcard" @click="$router.push('/vertical')">
        <span class="v-type">{{ VTYPE_TEXT[v.canvas_type] || '🎯 专项' }}</span>
        <b>{{ v.name || v.model_id || v.id }}</b>
        <span class="v-desc">{{ v.capability?.can_do?.slice(0, 2)?.join('、') || v.description || '专项任务处理' }}</span>
      </button>
    </div>

    <h2 class="sec"><HIcon name="users" :size="17" /> 你的画像<small>哈文在日常聊天里默默记住的你的喜好</small></h2>
    <div class="profile-box">
      <pre v-if="profile.has_content">{{ profile.summary }}</pre>
      <div v-else class="empty" style="margin:0">还没有画像内容 —— 多跟哈文聊聊，它会慢慢懂你</div>
      <router-link to="/profile" class="profile-manage">⚙️ 管理画像（看时间线 / 改 / 删 / 批准建议）→</router-link>
    </div>

    <h2 class="sec"><HIcon name="puzzle" :size="17" /> 插件与推荐工具<small>已装 {{ installed.length }} 个插件 · {{ recommended.length }} 个推荐工具</small></h2>
    <div v-if="!recommended.length && !installed.length" class="empty">暂无插件与推荐工具</div>
    <div class="plug-grid">
      <div v-for="r in recommended" :key="r.id || r.name" class="pcard">
        <b>{{ r.name }}</b>
        <span class="p-desc">{{ r.description || r.desc || '' }}</span>
        <BigButton v-if="!r.installed" :px="13" :loading="instId === (r.id || r.name)" @click="installTool(r)">检测并安装</BigButton>
        <span v-else class="ok-mark">✓ 已就绪</span>
      </div>
      <div v-for="p in installed" :key="p.id" class="pcard">
        <b>{{ p.name || p.id }}</b>
        <span class="p-desc">{{ p.description || '已安装的插件' }}</span>
        <span class="ok-mark">✓ 已安装</span>
      </div>
    </div>

    <h2 class="sec"><HIcon name="book" :size="17" /> 帮助<small>大白话使用手册</small></h2>
    <div class="help-links">
      <a class="help-card" href="/docs/guide" target="_blank" rel="noreferrer">
        <b>📖 完整使用指南</b><span>从安装到进阶，一步一步教你用</span>
      </a>
      <a class="help-card" href="/docs/guide#ch16" target="_blank" rel="noreferrer">
        <b>❓ 常见问题</b><span>遇到问题先来这里看看</span>
      </a>
      <a class="help-card" href="#/hub">
        <b>🌐 HarvenHub 社区</b><span>装技能、装模型、提需求</span>
      </a>
    </div>
  </PageShell>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'
import { canInstall, onInstallAvailability, promptInstall } from '../pwa'

const VTYPE_TEXT = { image_generation: '🎨 图片', music_generation: '🎵 音乐', speech: '🗣 语音', code: '💻 代码', translation: '🌐 翻译', medical: '🏥 医疗', data_analysis: '📊 数据' }

const verticals = ref([])
const profile = ref({})
const recommended = ref([])
const installed = ref([])
const instId = ref('')
const msg = ref('')
const msgOk = ref(false)
const evoPending = ref(0)

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 5000)
}

async function loadAll(showErr = false) {
  try {
    const [vm, pf, rec, ins] = await Promise.all([
      getJSON('/api/vertical/models').catch(() => ({ models: [] })),
      getJSON('/api/profile/auto/summary').catch(() => ({})),
      getJSON('/api/plugins/recommended').catch(() => ({})),
      getJSON('/api/plugins/list-installed').catch(() => ({ plugins: [] })),
    ])
    verticals.value = vm.models || []
    profile.value = pf
    recommended.value = rec.tools || rec.plugins || rec.items || (Array.isArray(rec) ? rec : [])
    installed.value = ins.plugins || []
    if (showErr) msg.value = ''
  } catch (e) { if (showErr) flash('加载失败：' + e.message, false) }
  // ★ 创造区徽标：待审批提案数
  try {
    const j = await getJSON('/api/evolution/proposals?status=pending')
    evoPending.value = (j.proposals || []).length
  } catch (_) { evoPending.value = 0 }
}

async function installTool(r) {
  const id = r.id || r.name
  instId.value = id
  try {
    const j = await postJSON('/api/plugins/recommended/install', { tool_id: id })
    if (j.success) flash(`「${r.name}」安装成功 ✅`)
    else flash('安装失败：' + (j.error || '请先自行安装对应软件'), false)
    await loadAll()
  } catch (e) { flash('安装失败：' + e.message, false) }
  finally { instId.value = '' }
}

loadAll(true)

/* ── F批 PWA 安装状态 ── */
const installable = ref(canInstall())
const showManualHint = ref(false)
const installHint = ref('')
const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true
let _off = null
onMounted(() => { _off = onInstallAvailability((ok) => { installable.value = ok }) })
onUnmounted(() => { if (_off) _off() })

async function doInstall() {
  const outcome = await promptInstall()
  if (outcome === 'accepted') installHint.value = '安装成功，桌面/主屏幕见 🐤'
  else if (outcome === 'dismissed') installHint.value = '先不装也没关系，随时回这里再装'
  else showManualHint.value = true
}
</script>

<style scoped>
.quick-links { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 6px; }
.q-card {
  display: flex; flex-direction: column; gap: 4px;
  border: 1px solid var(--c-line); border-radius: 12px; padding: 11px 13px;
  text-decoration: none; color: var(--c-text); background: #fff;
}
.q-card:hover { border-color: var(--c-primary); }
.q-card b { font-size: 13.5px; }
.q-card span { font-size: 11.5px; color: var(--c-text-2); }
/* ★ 创造区入口卡 */
.create-card {
  display: flex; align-items: center; gap: 13px;
  border: 1.5px dashed var(--c-primary); border-radius: 12px;
  background: var(--c-primary-weak); padding: 13px 16px;
  margin: 10px 0 6px; text-decoration: none; color: var(--c-text);
}
.create-card:hover { box-shadow: 0 3px 12px rgba(0,0,0,.07); }
.cc-icon { font-size: 22px; }
.cc-main { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cc-main b { font-size: 14px; }
.cc-main span:last-child { font-size: 11.5px; color: var(--c-text-2); }
.cc-badge { font-size: 11.5px; font-weight: 700; background: #FDF2F2; color: #B4443C; border-radius: 14px; padding: 3px 10px; white-space: nowrap; }
.cc-arrow { font-size: 17px; color: var(--c-primary); }
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }
.sec { display: flex; align-items: baseline; gap: 8px; font-size: 15px; margin: 20px 0 11px; }
.sec small { font-size: 12px; color: var(--sub); font-weight: 400; }
.empty { padding: 22px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13px; }

.v-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; }
.vcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; cursor: pointer; text-align: left; font-family: inherit; transition: box-shadow .15s, transform .15s; }
.vcard:hover { box-shadow: 0 4px 14px rgba(0,0,0,.08); transform: translateY(-2px); }
.v-type { font-size: 11.5px; color: var(--mode-vertical); font-weight: 700; }
.v-desc { font-size: 11.5px; color: var(--sub); }

.profile-box { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; max-width: 860px; }
.profile-box pre { margin: 0; font-family: inherit; font-size: 12.5px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
/* ★ 2026-09-18 #743 画像管理入口 */
.profile-manage { display: block; margin-top: 10px; padding-top: 9px; border-top: 1px dashed var(--line); font-size: 12.5px; color: var(--blue); text-decoration: none; font-weight: 600; }
.profile-manage:hover { text-decoration: underline; }

.plug-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; }
.pcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.pcard b { font-size: 13.5px; }
.p-desc { font-size: 11.5px; color: var(--sub); flex: 1; }
.ok-mark { color: var(--green); font-weight: 700; font-size: 12.5px; }

.help-links { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; }
.help-card { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 14px 16px; display: flex; flex-direction: column; gap: 3px; color: var(--ink); }
.help-card:hover { border-color: var(--blue); }
.help-card b { font-size: 13.5px; }
.help-card span { font-size: 11.5px; color: var(--sub); }

@media (max-width: 1000px) { .v-grid, .plug-grid, .help-links { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 660px) { .v-grid, .plug-grid, .help-links { grid-template-columns: 1fr; } }

/* ═══ F批 PWA 安装卡 ═══ */
.pwa-card {
  display: flex; align-items: center; gap: 14px; justify-content: space-between;
  border: 1.5px dashed var(--c-primary); border-radius: 12px;
  background: var(--c-primary-weak); padding: 13px 16px; margin-bottom: 12px;
}
.pwa-l { display: flex; flex-direction: column; gap: 3px; }
.pwa-l b { font-size: 14px; }
.pwa-desc { font-size: 12px; color: var(--c-text-2); }
.pwa-hint { font-size: 12px; color: var(--mode-local); font-weight: 700; }
.pwa-manual {
  border: 1px solid var(--c-line); border-radius: 12px; background: #fff;
  padding: 10px 16px; margin-bottom: 12px;
}
.pwa-manual p { margin: 6px 0; font-size: 12.5px; color: var(--c-text); }
</style>
