<template>
  <PageShell :title="t('page.skills.t')" icon="puzzle" :sub="t('page.skills.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="load(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
      <router-link to="/create"><BigButton :px="14"><HIcon name="plus" :size="16" /> 造新技能</BigButton></router-link>
      <a href="#/hub"><BigButton :px="14"><HIcon name="download" :size="16" /> 去 Hub 装新技能</BigButton></a>
    </template>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <!-- ★ 2026-09-17 #741 触发词管理 -->
    <section class="trig-box">
      <div class="trig-head">
        <b>🗣️ 触发词管理</b>
        <span>改完立刻生效，聊天里喊出触发词就会自动运行对应技能</span>
      </div>
      <div v-if="!trigList.length" class="trig-empty">还没有可管理触发词的技能</div>
      <div v-for="s in trigList" :key="s.id" class="trig-row">
        <template v-if="editTrigId === s.id">
          <div class="trig-edit">
            <b>{{ s.name }}</b>
            <textarea v-model.trim="editTrigText" rows="2" placeholder="用逗号分隔，例如：记一笔, 查账, 花了多少"></textarea>
            <div class="trig-btns">
              <BigButton :px="12" :loading="trigBusy" @click="saveTrig(s)">保存</BigButton>
              <BigButton variant="ghost" :px="12" @click="editTrigId = ''">取消</BigButton>
            </div>
          </div>
        </template>
        <template v-else>
          <b class="trig-name">{{ s.name }}</b>
          <div class="trig-chips">
            <span v-for="tg in (s.triggers || []).slice(0, 6)" :key="tg" class="chip">{{ tg }}</span>
            <span v-if="(s.triggers || []).length > 6" class="chip more">+{{ s.triggers.length - 6 }}</span>
            <span v-if="!(s.triggers || []).length" class="chip none">无触发词</span>
          </div>
          <button class="mini-e" @click="startEditTrig(s)">改触发词</button>
        </template>
      </div>
    </section>

    <div class="tool-row">
      <div class="search"><HIcon name="search" :size="16" /><input v-model.trim="q" placeholder="按技能名过滤" /></div>
      <span class="total">{{ scripts.length }} 个脚本技能</span>
    </div>

    <div v-if="!filtered.length" class="empty">
      {{ q ? '没有匹配的技能' : '还没有脚本技能。可以到「HarvenHub」安装，或在设置 → 插件 → 技能里创建。' }}
    </div>

    <div class="sk-grid">
      <div v-for="s in filtered" :key="s.skill_name" class="scard" :class="{ open: openName === s.skill_name }" @click="toggle(s)">
        <div class="s-head">
          <span class="s-ic">🧩</span>
          <b class="s-name">{{ s.skill_name }}</b>
          <span class="s-badge">{{ (s.actions || []).length }} 个动作</span>
        </div>
        <div class="s-path" :title="s.entry_point">{{ s.entry_point }}</div>

        <div v-if="openName === s.skill_name" class="s-detail" @click.stop>
          <div v-for="a in s.actions || []" :key="a.name" class="s-action">
            <code>{{ a.name }}</code>
            <span class="s-file">{{ a.file }}</span>
          </div>
          <div class="run-form">
            <label>动作
              <select v-model="runForm.action">
                <option v-for="a in s.actions || []" :key="a.name" :value="a.name">{{ a.name }}</option>
              </select>
            </label>
            <label>参数（JSON）<textarea v-model="runForm.params" rows="3" placeholder='{"query": "示例"}' spellcheck="false" /></label>
            <div class="run-btns">
              <BigButton :px="14" :loading="running" @click="run(s)"><HIcon name="send" :size="15" /> 运行</BigButton>
            </div>
            <pre v-if="runResult" class="run-out">{{ runResult }}</pre>
          </div>
          <div class="s-hint">💬 聊天里喊出这个技能的触发词，哈文也会自动运行它</div>
        </div>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
import { ref, computed, reactive } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'

const scripts = ref([])
const q = ref('')
const openName = ref('')
const runForm = reactive({ action: 'main', params: '{}' })
const runResult = ref('')
const running = ref(false)
const msg = ref('')
const msgOk = ref(false)

const filtered = computed(() => {
  if (!q.value) return scripts.value
  return scripts.value.filter(s => s.skill_name.toLowerCase().includes(q.value.toLowerCase()))
})

function toggle(s) {
  openName.value = openName.value === s.skill_name ? '' : s.skill_name
  runResult.value = ''
  runForm.action = (s.actions || [])[0]?.name || 'main'
  runForm.params = '{}'
}

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 4000)
}

async function load(showErr = false) {
  try {
    const j = await getJSON('/api/skills/scripts')
    scripts.value = j.scripts || []
    if (showErr) msg.value = ''
  } catch (e) { if (showErr) { msg.value = '加载失败：' + e.message; msgOk.value = false } }
}

async function run(s) {
  running.value = true
  runResult.value = ''
  let params = {}
  try { params = runForm.params.trim() ? JSON.parse(runForm.params) : {} }
  catch (e) { runResult.value = '❌ 参数不是合法 JSON：' + e.message; running.value = false; return }
  try {
    const j = await postJSON(`/api/skills/scripts/${s.skill_name}/run`, { action: runForm.action, params })
    if (j.success) {
      const out = j.result ?? j.output ?? j
      runResult.value = typeof out === 'string' ? out : JSON.stringify(out, null, 2)
      flash('运行完成')
    } else {
      runResult.value = '❌ ' + (j.error || '运行失败')
    }
  } catch (e) { runResult.value = '❌ ' + e.message }
  finally { running.value = false }
}

/* ── ★ 2026-09-17 #741 触发词管理 ── */
const trigList = ref([])
const editTrigId = ref('')
const editTrigText = ref('')
const trigBusy = ref(false)

async function loadTriggers() {
  try {
    const j = await getJSON('/api/skills')
    trigList.value = (j.skills || []).filter(s => !s.has_scripts)
  } catch (_) { trigList.value = [] }
}

function startEditTrig(s) {
  editTrigId.value = s.skill_id
  editTrigText.value = (s.triggers || []).join(', ')
}

async function saveTrig(s) {
  const triggers = editTrigText.value.split(/[,，|]/).map(x => x.trim()).filter(Boolean)
  if (!triggers.length) { flash('至少要有一个触发词', false); return }
  trigBusy.value = true
  try {
    const j = await postJSON(`/api/skills/${s.id}`, { triggers })
    if (j.success) {
      flash(`✅ 「${s.name}」触发词已更新，立刻生效`)
      editTrigId.value = ''
      await loadTriggers()
    } else {
      flash('保存失败：' + (j.error || ''), false)
    }
  } catch (e) { flash('保存失败：' + e.message, false) }
  finally { trigBusy.value = false }
}

load(true)
loadTriggers()</script>

<style scoped>
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }
.tool-row { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }
.search { flex: 1; display: flex; align-items: center; gap: 8px; background: #fff; border: 1px solid var(--line); border-radius: 11px; padding: 9px 13px; color: var(--sub); }
.search input { flex: 1; border: none; outline: none; font-size: 13px; background: transparent; color: var(--ink); }
.total { font-size: 12.5px; color: var(--sub); }
.empty { padding: 34px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13.5px; }

/* ── ★ #741 触发词管理 ── */
.trig-box { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 13px 16px; margin-bottom: 14px; }
.trig-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.trig-head b { font-size: 14px; }
.trig-head span { font-size: 11.5px; color: var(--sub); }
.trig-empty { font-size: 12.5px; color: var(--sub); padding: 8px 0; }
.trig-row { display: flex; align-items: center; gap: 9px; padding: 7px 0; border-top: 1px dashed var(--line); flex-wrap: wrap; }
.trig-name { font-size: 13px; flex-shrink: 0; min-width: 96px; }
.trig-chips { flex: 1; display: flex; flex-wrap: wrap; gap: 5px; min-width: 0; }
.chip { font-size: 11.5px; background: var(--c-bg-soft); border-radius: 14px; padding: 3px 10px; color: var(--ink); }
.chip.more { color: var(--sub); }
.chip.none { color: var(--sub); background: #FDF2F2; }
.mini-e { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 11.5px; padding: 4px 11px; cursor: pointer; color: var(--blue); font-family: inherit; flex-shrink: 0; }
.mini-e:hover { border-color: var(--blue); }
.trig-edit { width: 100%; display: flex; flex-direction: column; gap: 7px; padding: 4px 0; }
.trig-edit b { font-size: 13px; }
.trig-edit textarea { border: 1px solid var(--line); border-radius: 9px; padding: 8px 11px; font-size: 12.5px; font-family: inherit; outline: none; resize: vertical; background: #fff; color: var(--ink); }
.trig-edit textarea:focus { border-color: var(--blue); }
.trig-btns { display: flex; gap: 8px; }

.sk-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; align-items: start; }
.scard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 13px 14px; cursor: pointer; }
.scard.open { border-color: var(--blue); }
.s-head { display: flex; align-items: center; gap: 9px; }
.s-ic { font-size: 18px; }
.s-name { font-size: 14.5px; }
.s-badge { margin-left: auto; font-size: 11px; background: var(--c-bg-soft); border-radius: 20px; padding: 2px 9px; color: var(--sub); }
.s-path { font-size: 11px; color: var(--sub); margin-top: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.s-detail { margin-top: 10px; border-top: 1px dashed var(--line); padding-top: 9px; }
.s-action { display: flex; align-items: center; gap: 10px; padding: 4px 0; font-size: 12px; }
.s-action code { background: var(--c-bg-soft); border-radius: 6px; padding: 2px 8px; font-weight: 700; }
.s-file { color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-form { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.run-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--sub); }
.run-form select, .run-form textarea { border: 1px solid var(--line); border-radius: 9px; padding: 7px 10px; font-size: 12.5px; font-family: inherit; outline: none; background: #fff; color: var(--ink); }
.run-form select:focus, .run-form textarea:focus { border-color: var(--blue); }
.run-btns { display: flex; }
.run-out { margin: 0; background: #F7F9FC; border: 1px solid var(--line); border-radius: 9px; padding: 10px 12px; font-size: 12px; max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
.s-hint { margin-top: 8px; font-size: 11.5px; color: var(--sub); background: var(--c-bg-soft); border-radius: 8px; padding: 7px 10px; }

@media (max-width: 1000px) { .sk-grid { grid-template-columns: 1fr; } }
</style>
