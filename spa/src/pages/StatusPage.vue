<template>
  <div class="page status-page">
    <header class="page-head">
      <div>
        <h1><HIcon name="pulse" :size="22" /> 运行状态</h1>
        <p class="sub">哈文的身体检查单 —— 电脑资源、模型、Token 花销、安全、定时提醒一眼看清</p>
      </div>
      <div class="head-actions">
        <label class="auto-refresh"><input type="checkbox" v-model="auto" /> 每 15 秒自动刷新</label>
        <BigButton variant="ghost" :px="14" @click="load(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
      </div>
    </header>

    <div v-if="err" class="err-tip">⚠️ 数据拉取失败：{{ err }}（哈文服务可能在休息，稍后重试）</div>

    <div class="mon-grid">
      <!-- 卡1 电脑资源 -->
      <section class="mbox">
        <div class="mt2"><HIcon name="pulse" :size="18" class="ic" /> 电脑资源</div>
        <div class="gauge">
          <div class="gcol">
            <div class="gbar"><i :style="barStyle(d.resources?.cpu_percent, 'var(--blue)')" /></div>
            <b>CPU {{ d.resources?.cpu_percent ?? '—' }}%</b>
          </div>
          <div class="gcol">
            <div class="gbar"><i :style="barStyle(d.resources?.vram?.percent, 'var(--amber)')" /></div>
            <b>显存 {{ d.resources?.vram?.percent ?? '—' }}%</b>
          </div>
          <div class="gcol">
            <div class="gbar"><i :style="barStyle(d.resources?.ram?.percent, 'var(--green)')" /></div>
            <b>内存 {{ d.resources?.ram?.percent ?? '—' }}%</b>
          </div>
        </div>
        <div class="kv"><span class="k2">显卡</span><span class="v2">{{ d.resources?.vram?.gpu || '未识别' }}</span></div>
        <div class="kv"><span class="k2">显存用量</span><span class="v2">{{ vramText }}</span></div>
        <div class="kv"><span class="k2">内存用量</span><span class="v2">{{ ramText }}</span></div>
      </section>

      <!-- 卡2 模型加载 -->
      <section class="mbox">
        <div class="mt2"><HIcon name="bot" :size="18" class="ic" /> 模型加载</div>
        <div v-if="!d.models?.loaded?.length && !d.models?.installed?.length" class="kv">
          <span class="k2">本地模型</span><span class="v2">未检测到（Ollama 未运行）</span>
        </div>
        <div v-for="m in d.models?.loaded || []" :key="'L' + m.name" class="kv">
          <span class="k2">{{ m.name }}</span><span class="v2 ok">运行中{{ m.size_gb ? ` · ${m.size_gb}GB` : '' }}</span>
        </div>
        <div v-for="m in d.models?.installed || []" :key="'I' + m.name" class="kv">
          <span class="k2">{{ m.name }}</span><span class="v2">已安装{{ m.size_gb ? ` · ${m.size_gb}GB` : '' }}</span>
        </div>
        <div class="kv">
          <span class="k2">外脑{{ d.models?.foreign_brain ? `（${d.models.foreign_brain}）` : '' }}</span>
          <span class="v2" :class="{ ok: d.models?.external_ok }">{{ d.models?.foreign_brain ? 'API 就绪' : '未配置' }}</span>
        </div>
      </section>

      <!-- 卡3 今日对话 + Token 账本 -->
      <section class="mbox">
        <div class="mt2"><HIcon name="chart" :size="18" class="ic" /> 今日对话 · Token 账本</div>
        <div class="kv"><span class="k2">今日新会话</span><span class="v2">{{ d.chat_today?.conversations_today ?? '—' }} 个</span></div>
        <div class="kv"><span class="k2">B 方案规则触发（零 token）</span><span class="v2 ok">{{ led.b_plan_hits }} 次</span></div>
        <div class="kv"><span class="k2">FC 对话轮数</span><span class="v2">{{ led.fc_turns }} 轮</span></div>
        <div class="kv"><span class="k2">FC 工具调用</span><span class="v2">{{ led.fc_rounds }} 次</span></div>
        <div class="kv"><span class="k2">工具结果瘦身</span><span class="v2">{{ led.tool_result_slimmed }} 次 · 省 {{ fmtChars(led.tool_result_chars_saved) }}</span></div>
        <div class="kv"><span class="k2">目录注入</span><span class="v2">{{ fmtChars(led.catalog_chars_last) }}</span></div>
        <div class="ledger-note">💰 A 批改造后 FC 工具部分从 ≈3353 token/轮 降到 ≈1007，这里的数据会随使用实时累积</div>
      </section>

      <!-- 卡4 安全 -->
      <section class="mbox">
        <div class="mt2"><HIcon name="shield" :size="18" class="ic" /> 安全</div>
        <div class="kv"><span class="k2">权限模式</span><span class="v2">{{ d.security?.permission_mode || '—' }}</span></div>
        <div class="kv"><span class="k2">数据出站</span><span class="v2">{{ d.security?.outbound || '—' }}</span></div>
        <div class="kv"><span class="k2">FC 工具体系</span><span class="v2" :class="{ ok: d.security?.fc_active }">{{ d.security?.fc_active ? '已启用' : '关闭' }}</span></div>
        <div class="kv"><span class="k2">危险命令拦截</span><span class="v2 ok">实时生效</span></div>
      </section>

      <!-- 卡5 定时提醒（★ 2026-09-17 #740 增删改；★ 2026-10-23 定版拍板正名：与「任务」页划清界限） -->
      <section class="mbox">
        <div class="mt2"><HIcon name="task" :size="18" class="ic" /> 定时提醒
          <button class="mini-add" @click="openAdd">＋ 新增</button>
        </div>
        <div v-if="!cronTasks.length" class="kv"><span class="k2">暂无任务</span><span class="v2">—</span></div>
        <div v-for="c in cronTasks" :key="c.task_id" class="cron-row">
          <div class="cron-main">
            <b>{{ c.name }}</b>
            <span class="cron-desc">{{ scheduleText(c) }} · {{ c.prompt?.slice(0, 26) }}{{ c.prompt?.length > 26 ? '…' : '' }}</span>
          </div>
          <button class="mini-t" :class="{ off: !c.enabled }" @click="toggleCron(c)">{{ c.enabled ? '启用中' : '已停用' }}</button>
          <button class="mini-t" @click="editCron(c)">改</button>
          <button class="mini-t danger" @click="deleteCron(c)">删</button>
        </div>
        <div class="kv"><span class="k2">正在执行</span><span class="v2">{{ d.tasks?.running ?? 0 }} 个</span></div>

        <!-- 新增/编辑表单 -->
        <div v-if="formOpen" class="cron-form">
          <div class="cf-head"><b>{{ editingId ? '修改定时提醒' : '新增定时提醒' }}</b><button class="mini-t" @click="formOpen = false">收起</button></div>
          <input v-model.trim="cf.name" placeholder="任务名字，如：晚饭提醒" />
          <textarea v-model.trim="cf.prompt" rows="2" placeholder="到点后哈文要干的事，如：提醒家人吃药并说一句关心的话"></textarea>
          <div class="cf-row">
            <select v-model="cf.type">
              <option value="cron">每天定时</option>
              <option value="interval">每隔一段时间</option>
              <option value="once">只执行一次</option>
            </select>
            <template v-if="cf.type === 'cron'">
              <input type="number" min="0" max="23" v-model.number="cf.hour" style="width:64px" /> 点
              <input type="number" min="0" max="59" v-model.number="cf.minute" style="width:64px" /> 分
            </template>
            <template v-else-if="cf.type === 'interval'">
              每 <input type="number" min="1" v-model.number="cf.minutes" style="width:64px" /> 分钟
            </template>
            <template v-else>
              <input type="datetime-local" v-model="cf.run_at" />
            </template>
          </div>
          <div class="cf-btns">
            <BigButton :px="13" :loading="cronBusy" @click="saveCron">{{ editingId ? '保存修改' : '创建任务' }}</BigButton>
          </div>
        </div>
      </section>

      <!-- 卡6 健康报告 -->
      <section class="mbox">
        <div class="mt2"><HIcon name="heart" :size="18" class="ic" /> 健康报告</div>
        <div class="kv"><span class="k2">数据库大小</span><span class="v2">{{ d.health?.db_size_mb ?? '—' }} MB</span></div>
        <div class="kv"><span class="k2">知识库条目</span><span class="v2">{{ d.health?.kb_entries ?? '—' }} 条</span></div>
        <div class="kv"><span class="k2">错误日志</span><span class="v2 ok">{{ d.health?.errors ?? 0 }} 条</span></div>
        <div class="kv"><span class="k2">备份方式</span><span class="v2">{{ d.health?.backup || '—' }}</span></div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON, delJSON } from '../api.js'

const d = ref({})
const err = ref('')
const auto = ref(true)
let timer = null

const led = computed(() => d.value.chat_today?.ledger || {})
const vramText = computed(() => {
  const v = d.value.resources?.vram
  return v?.total_gb ? `${v.used_gb} / ${v.total_gb} GB` : '—'
})
const ramText = computed(() => {
  const r = d.value.resources?.ram
  return r?.total_gb ? `${r.used_gb} / ${r.total_gb} GB` : '—'
})

function barStyle(p, color) {
  const w = Math.max(0, Math.min(100, Number(p) || 0))
  return { width: w + '%', background: color }
}
function fmtChars(n) {
  n = Number(n) || 0
  return n >= 10000 ? (n / 10000).toFixed(1) + ' 万字符' : n + ' 字符'
}

async function load(showErr = false) {
  try {
    const res = await fetch('/api/status/monitor')
    const j = await res.json()
    if (j.success) { d.value = j.data; err.value = '' }
    else if (showErr) err.value = j.error || '未知错误'
  } catch (e) {
    if (showErr) err.value = e.message
  }
  loadCron()
}

/* ── ★ 2026-09-17 #740 定时提醒增删改（2026-10-23 正名，原「定时任务」） ── */
const cronTasks = ref([])
const cronBusy = ref(false)
const formOpen = ref(false)
const editingId = ref('')
const cf = ref({ name: '', prompt: '', type: 'cron', hour: 8, minute: 0, minutes: 30, run_at: '' })

async function loadCron() {
  try {
    const j = await getJSON('/api/cron/list')
    cronTasks.value = j.tasks || []
  } catch (_) { cronTasks.value = [] }
}

function scheduleText(c) {
  if (c.schedule_type === 'cron') return `每天 ${String(c.schedule_config?.hour ?? 8).padStart(2, '0')}:${String(c.schedule_config?.minute ?? 0).padStart(2, '0')}`
  if (c.schedule_type === 'interval') return `每 ${c.schedule_config?.minutes ?? 60} 分钟`
  if (c.schedule_type === 'once') return `单次 ${c.schedule_config?.run_at || ''}`
  return c.schedule_type
}

function openAdd() {
  editingId.value = ''
  cf.value = { name: '', prompt: '', type: 'cron', hour: 8, minute: 0, minutes: 30, run_at: '' }
  formOpen.value = true
}

function editCron(c) {
  editingId.value = c.task_id
  cf.value = {
    name: c.name, prompt: c.prompt, type: c.schedule_type,
    hour: c.schedule_config?.hour ?? 8, minute: c.schedule_config?.minute ?? 0,
    minutes: c.schedule_config?.minutes ?? 30, run_at: c.schedule_config?.run_at ?? '',
  }
  formOpen.value = true
}

async function saveCron() {
  if (!cf.value.name || !cf.value.prompt) { alert('任务名字和「要干的事」都要填'); return }
  cronBusy.value = true
  try {
    const body = { ...cf.value }
    if (body.type === 'once' && body.run_at && body.run_at.length === 16) body.run_at = body.run_at + ':00'
    const j = editingId.value
      ? await postJSON(`/api/cron/${editingId.value}/update`, body)
      : await postJSON('/api/cron/add', body)
    if (j.success) { formOpen.value = false; editingId.value = ''; await loadCron() }
    else alert('保存失败：' + (j.error || ''))
  } catch (e) { alert('保存失败：' + e.message) }
  finally { cronBusy.value = false }
}

async function toggleCron(c) {
  try {
    await postJSON(`/api/cron/${c.task_id}/toggle`, { enabled: !c.enabled })
    await loadCron()
  } catch (e) { alert('操作失败：' + e.message) }
}

async function deleteCron(c) {
  if (!confirm(`确定删除定时提醒「${c.name}」？`)) return
  try {
    await delJSON(`/api/cron/${c.task_id}`)
    await loadCron()
  } catch (e) { alert('删除失败：' + e.message) }
}

onMounted(() => {
  load(true)
  timer = setInterval(() => { if (auto.value) load(false) }, 15000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.status-page { display: flex; flex-direction: column; height: 100%; overflow: auto; }
.page-head { display: flex; justify-content: space-between; align-items: flex-end; padding: 22px 26px 6px; }
.page-head h1 { display: flex; align-items: center; gap: 9px; font-size: 21px; margin: 0; }
.page-head .sub { font-size: 13px; color: var(--sub); margin: 5px 0 0; }
.head-actions { display: flex; align-items: center; gap: 14px; }
.auto-refresh { font-size: 12.5px; color: var(--sub); display: flex; align-items: center; gap: 6px; cursor: pointer; }

.err-tip { margin: 10px 26px; padding: 10px 14px; background: #FDF2F2; border: 1px solid #F5C6C6; border-radius: 10px; color: #B4443C; font-size: 13px; }

.mon-grid { flex: 1; padding: 16px 26px 26px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; align-content: start; }
.mbox { border: 1px solid var(--line); border-radius: 13px; padding: 15px 16px; background: #fff; }
.mt2 { font-size: 13.5px; font-weight: 700; display: flex; align-items: center; gap: 7px; margin-bottom: 10px; color: var(--ink); }
.mt2 .ic { color: var(--blue); }

.gauge { display: flex; gap: 12px; align-items: flex-end; margin-bottom: 8px; }
.gcol { text-align: center; flex: 1; }
.gbar { height: 64px; background: #EEF1F6; border-radius: 9px; display: flex; align-items: flex-end; overflow: hidden; }
.gbar i { display: block; width: 100%; border-radius: 9px 9px 0 0; transition: height .5s ease; }
.gcol b { display: block; font-size: 10.5px; color: var(--sub); margin-top: 6px; font-weight: 400; }

.kv { display: flex; justify-content: space-between; gap: 10px; font-size: 12.5px; padding: 6px 0; border-bottom: 1px dashed var(--line); }
.kv:last-of-type { border-bottom: none; }
.kv .k2 { color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kv .v2 { font-weight: 600; text-align: right; white-space: nowrap; }
.v2.ok { color: var(--green); }

.ledger-note { margin-top: 9px; font-size: 11.5px; color: var(--sub); background: #F6F8FB; border-radius: 8px; padding: 7px 10px; line-height: 1.6; }

/* ── ★ #740 定时提醒管理 ── */
.mini-add { margin-left: auto; border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 3px 10px; cursor: pointer; color: var(--blue); font-family: inherit; }
.mini-add:hover { border-color: var(--blue); }
.cron-row { display: flex; align-items: center; gap: 7px; padding: 6px 0; border-bottom: 1px dashed var(--line); }
.cron-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.cron-main b { font-size: 12.5px; }
.cron-desc { font-size: 11px; color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini-t { border: 1px solid var(--line); background: #fff; border-radius: 7px; font-size: 11px; padding: 3px 9px; cursor: pointer; color: var(--ink); font-family: inherit; flex-shrink: 0; }
.mini-t:hover { border-color: var(--blue); color: var(--blue); }
.mini-t.off { color: var(--sub); background: var(--c-bg-soft); }
.mini-t.danger:hover { border-color: var(--red); color: var(--red); }
.cron-form { margin-top: 10px; background: #F6F8FB; border-radius: 10px; padding: 11px 12px; display: flex; flex-direction: column; gap: 8px; }
.cf-head { display: flex; justify-content: space-between; align-items: center; }
.cf-head b { font-size: 12.5px; }
.cron-form input, .cron-form textarea, .cron-form select { border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px; font-size: 12.5px; font-family: inherit; outline: none; width: 100%; box-sizing: border-box; background: #fff; }
.cron-form input:focus, .cron-form textarea:focus, .cron-form select:focus { border-color: var(--blue); }
.cron-form textarea { resize: vertical; }
.cf-row { display: flex; align-items: center; gap: 7px; font-size: 12.5px; flex-wrap: wrap; }
.cf-row select { width: auto; }
.cf-btns { display: flex; gap: 8px; }

@media (max-width: 1100px) { .mon-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 760px) { .mon-grid { grid-template-columns: 1fr; } }
</style>
