<template>
  <div v-if="visible" class="sic-mask" @click.self="close">
    <div class="sic-card">
      <!-- ═══ 常态：6 键 + 长按换键（对齐手机确认卡交互） ═══ -->
      <template v-if="!editing">
        <div class="sic-head">📥 把这张图存进面板</div>
        <img :src="image" class="sic-preview" alt="预览" />
        <label class="sic-label">起个名字（比如：营业执照）</label>
        <input v-model="name" class="sic-input" placeholder="这张图是什么" @keyup.enter="save" />
        <label class="sic-label">存到哪个面板？点一下选中</label>
        <div class="sic-slots">
          <button v-for="(s, i) in slots" :key="i" class="sic-slot"
                  :class="{ on: target === i, off: s && !slotOk(s), empty: !s }"
                  @pointerdown="pd($event, i)" @pointerup="pu($event, i)"
                  @pointerleave="pc" @pointercancel="pc" @contextmenu.prevent>
            <template v-if="s">{{ s.label }}<small v-if="multiPhoto(s)"> · {{ s.photoField }}</small></template>
            <template v-else>➕ 空</template>
          </button>
        </div>
        <p class="sic-hint">长按任意键 0.6 秒可更换这 6 个键</p>
        <div class="sic-foot">
          <button class="sic-btn" @click="close">取消</button>
          <button class="sic-btn primary" :disabled="busy || !name.trim() || target === ''"
                  @click="save">{{ busy ? '存入中…' : '存入' }}</button>
        </div>
      </template>

      <!-- ═══ 换键编辑器：上排选槽 / 下排挑模块 ═══ -->
      <template v-else>
        <div class="sic-head">🔧 挑 6 个常用键</div>
        <div class="sic-slots ed">
          <button v-for="(s, i) in slots" :key="i" class="sic-slot mini"
                  :class="{ on: editIdx === i, empty: !s }" @click="editIdx = i">
            {{ s ? s.label : '空' }}
          </button>
        </div>
        <label class="sic-label">给「{{ editIdx + 1 }} 号键」选个模块：</label>
        <div class="sic-cands">
          <template v-for="p in allPanels" :key="p.id">
            <button class="sic-cand" :class="{ needadd: !fieldFor(p) }" :disabled="pending === p.id"
                    @click="pickPanel(p)">{{ p.icon || '🧩' }} {{ p.name }}<small v-if="!fieldFor(p)"> 补图</small></button>
            <span v-if="fieldFor(p) && fieldFor(p).length > 1" class="sic-flds">
              <button v-for="f in fieldFor(p)" :key="f" class="sic-cand fld"
                      @click="assign(p, f)">{{ f }}</button>
            </span>
          </template>
          <p v-if="!allPanels.length" class="sic-hint">还没有模块，先建一个 ↓</p>
        </div>
        <div class="sic-newrow">
          <button class="sic-btn" @click="showNew = !showNew">➕ 新建模块</button>
          <button class="sic-btn" @click="resetSlots">恢复默认</button>
          <button class="sic-btn primary" @click="editing = false">好了</button>
        </div>
        <div v-if="showNew" class="sic-newform">
          <input v-model="newName" class="sic-input" placeholder="模块名字（比如：房产证）" />
          <button class="sic-btn primary" :disabled="creating || !newName.trim()" @click="createNew">
            {{ creating ? '创建中…' : '建好放进这个键' }}
          </button>
          <p class="sic-hint">会自动带「名称 + 照片」两个字段，建好直接当存图模块用</p>
        </div>
      </template>
      <p v-if="msg" class="sic-msg" :class="{ err: isErr }">{{ msg }}</p>
    </div>
  </div>
</template>

<script setup>
// ★ #890（2026-09-26）：聊天发图「存入面板」入口
// ★ #1458（2026-11-05）：重写为 6 键 + 长按换键（对齐手机确认卡 v27 交互）；
//   槽位存 localStorage（hv_spa_save_slots），候选池动态拉 /api/panels 只列带照片字段的模块，
//   支持多照片字段选字段（如物品存放「照片/柜内照片」）、编辑器内一键建模块（名称+照片双字段）。
import { ref, computed, watch } from 'vue'
import { postJSON, getJSON } from '../api.js'

const props = defineProps({
  visible: Boolean,
  image: String,
})
const emit = defineEmits(['close'])

const DEFAULT_SLOTS = [
  { panelId: 'home_docs', label: '📄 证件保单', photoField: '照片' },
  { panelId: 'home_items', label: '📦 物品存放', photoField: '照片' },
  { panelId: 'fridge_stock_1', label: '🧊 冰箱食物', photoField: '照片' },
  { panelId: 'meal_diary', label: '🍽️ 餐饮记录', photoField: '菜品照片' },
  { panelId: 'mistake_notebook_1', label: '📐 错题本', photoField: '错题照片' },
  null,
]
const LS_KEY = 'hv_spa_save_slots'

const name = ref('')
const target = ref('')          // 选中槽下标
const busy = ref(false)
const msg = ref('')
const isErr = ref(false)
const slots = ref(loadSlots())
const panels = ref([])          // 已装面板（动态拉）
const editing = ref(false)
const editIdx = ref(0)
const showNew = ref(false)
const newName = ref('')
const creating = ref(false)
const pending = ref('')  // 正在补照片字段的模块 id

function loadSlots() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr) && arr.length === 6) return arr
    }
  } catch (e) { /* 损坏就用默认 */ }
  return JSON.parse(JSON.stringify(DEFAULT_SLOTS))
}
function persistSlots() {
  try { localStorage.setItem(LS_KEY, JSON.stringify(slots.value)) } catch (e) { /* 忽略 */ }
}

watch(() => props.visible, async (v) => {
  if (!v) return
  name.value = ''; target.value = ''; msg.value = ''; isErr.value = false
  editing.value = false; showNew.value = false
  try {
    const j = await getJSON('/api/panels')
    panels.value = (j && j.panels) || []
  } catch (e) { panels.value = [] }
})

// 该面板的字段里所有照片字段
function photoFields(p) {
  return (p.fields || []).filter(f => f.type === 'image').map(f => f.name)
}
function fieldFor(p) { return photoFields(p).length ? photoFields(p) : null }
function multiPhoto(s) {
  const p = panels.value.find(x => x.id === s.panelId)
  return p ? photoFields(p).length > 1 : false
}
function slotOk(s) {
  const p = panels.value.find(x => x.id === s.panelId)
  return !!(p && photoFields(p).includes(s.photoField))
}
const photoPanels = computed(() =>
  panels.value.filter(p => photoFields(p).length > 0))
const allPanels = computed(() => panels.value)

// ═══ 长按 0.6s 进编辑器；点按 = 选中/取消；空槽点按直接进编辑器 ═══
let lpTimer = null, lpFired = false
function pd(e, i) {
  lpFired = false
  if (!slots.value[i]) { lpFired = true; openEditor(i); return }
  lpTimer = setTimeout(() => { lpFired = true; openEditor(i) }, 600)
}
function pu(e, i) {
  clearTimeout(lpTimer)
  if (lpFired) { lpFired = false; return }
  const s = slots.value[i]
  if (!s) return
  if (!slotOk(s)) { msg.value = '这个模块被卸载或字段改了，长按它换个别的'; isErr.value = true; return }
  msg.value = ''; isErr.value = false
  target.value = target.value === i ? '' : i
}
function pc() { clearTimeout(lpTimer) }
function openEditor(i) { editIdx.value = i; editing.value = true; showNew.value = false; msg.value = '' }

function assign(p, fld) {
  slots.value[editIdx.value] = { panelId: p.id, label: (p.icon || '🧩') + ' ' + p.name, photoField: fld }
  persistSlots()
  // 自动跳下一个空槽，连续配更快
  const next = slots.value.findIndex(s => !s)
  if (next >= 0) editIdx.value = next
}
async function pickPanel(p) {
  const fs = photoFields(p)
  if (fs.length === 1) { assign(p, fs[0]); return }
  if (fs.length > 1) return  // 多照片字段：下方已展开字段按钮，选一个
  // 没有照片字段 → 现场给模块补一个「照片」（#1459）
  pending.value = p.id
  msg.value = ''; isErr.value = false
  try {
    const r = await postJSON(`/api/panels/${p.id}/ensure_photo_field`, {})
    if (r && r.success) {
      try {
        const j = await getJSON('/api/panels')
        panels.value = (j && j.panels) || []
      } catch (e) { /* 用旧列表也能 assign */ }
      const np = panels.value.find(x => x.id === p.id) || p
      assign(np, r.photo_field || '照片')
      msg.value = r.added === false ? '' : `✅ 已给「${p.name}」加上「照片」字段，选好了`
    } else {
      msg.value = (r && r.error) || '加照片字段没成功'; isErr.value = true
    }
  } catch (e) {
    msg.value = '加照片字段失败：' + e.message; isErr.value = true
  } finally { pending.value = '' }
}
function resetSlots() {
  slots.value = JSON.parse(JSON.stringify(DEFAULT_SLOTS))
  persistSlots()
}

async function createNew() {
  if (!newName.value.trim()) return
  creating.value = true
  try {
    const id = 'photo_' + Date.now().toString(36)
    const j = await postJSON('/api/panels/create', {
      name: newName.value.trim(), id, description: '存图快捷键创建', icon: '🖼️',
      fields: [{ name: '名称', type: 'text' }, { name: '照片', type: 'image' }],
      with_chart: false, with_search: true,
    })
    if (!j.success) throw new Error(j.message || '创建失败')
    const p = j.panel || { id, name: newName.value.trim(), icon: '🖼️', fields: [] }
    assign(p, '照片')
    // 刷新已装列表（新建的立即进候选池）
    try {
      const r = await getJSON('/api/panels')
      panels.value = (r && r.panels) || []
    } catch (e) { /* 下次打开再刷 */ }
    newName.value = ''; showNew.value = false
    msg.value = '✅ 模块建好，已放进 ' + (editIdx.value + 1) + ' 号键'
    isErr.value = false
  } catch (e) {
    msg.value = '创建失败：' + e.message; isErr.value = true
  } finally { creating.value = false }
}

async function save() {
  const i = target.value
  if (busy.value || i === '' || !name.value.trim()) return
  const s = slots.value[i]
  if (!s || !slotOk(s)) { msg.value = '先点选一个能存图的模块'; isErr.value = true; return }
  const p = panels.value.find(x => x.id === s.panelId)
  const nameKey = ((p.fields || []).find(f => f.type === 'text') || {}).name || '名称'
  busy.value = true; msg.value = ''
  try {
    const body = {}
    body[nameKey] = name.value.trim()
    body[s.photoField] = props.image || ''
    const r = await postJSON(`/api/panels/${s.panelId}/data`, body)
    if (r && r.success !== false) {
      msg.value = '✅ 已存入「' + s.label.replace(/^\S+\s/, '') + '」，去面板里就能看到'
      isErr.value = false
      setTimeout(close, 1200)
    } else {
      msg.value = (r && r.message) || '存入失败'; isErr.value = true
    }
  } catch (e) {
    msg.value = '存入失败：' + e.message; isErr.value = true
  } finally { busy.value = false }
}

function close() { emit('close') }
</script>

<style scoped>
.sic-mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 300;
  display: flex; align-items: center; justify-content: center; }
.sic-card { width: 360px; max-width: 92vw; max-height: 88vh; overflow-y: auto; background: var(--card, #fff);
  border-radius: 14px; padding: 16px; box-shadow: 0 12px 40px rgba(0,0,0,.22); }
.sic-head { font-weight: 700; font-size: 15px; margin-bottom: 10px; }
.sic-preview { width: 100%; max-height: 140px; object-fit: contain; border-radius: 8px;
  background: var(--bg-soft, #f5f5f5); margin-bottom: 10px; }
.sic-label { display: block; font-size: 12px; opacity: .75; margin: 8px 0 4px; }
.sic-input { width: 100%; box-sizing: border-box; padding: 8px 10px; border-radius: 8px;
  border: 1px solid var(--line, #ddd); background: transparent; color: inherit; font-size: 13px; }
.sic-slots { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: 4px; }
.sic-slot { padding: 9px 4px; border-radius: 10px; border: 1px solid var(--line, #ddd);
  background: transparent; color: inherit; font-size: 12px; cursor: pointer; user-select: none;
  -webkit-user-select: none; touch-action: manipulation; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sic-slot small { opacity: .65; font-size: 10px; }
.sic-slot.on { border-color: var(--accent, #4f7cff); color: var(--accent, #4f7cff); font-weight: 600;
  background: color-mix(in srgb, var(--accent, #4f7cff) 12%, transparent); }
.sic-slot.off { opacity: .35; }
.sic-slot.empty { border-style: dashed; opacity: .55; }
.sic-slots.ed .sic-slot { padding: 7px 4px; }
.sic-hint { font-size: 11px; opacity: .55; margin: 6px 0 0; }
.sic-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.sic-btn { padding: 7px 14px; border-radius: 8px; border: 1px solid var(--line, #ddd);
  background: transparent; color: inherit; cursor: pointer; font-size: 13px; }
.sic-btn.primary { background: var(--accent, #4f7cff); border-color: var(--accent, #4f7cff); color: #fff; }
.sic-btn:disabled { opacity: .5; cursor: not-allowed; }
.sic-cands { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 10px; align-items: center; }
.sic-cand { padding: 5px 10px; border-radius: 999px; border: 1px solid var(--line, #ddd);
  background: transparent; color: inherit; font-size: 12px; cursor: pointer; }
.sic-cand.fld { border-style: dashed; padding: 3px 8px; font-size: 11px; }
.sic-cand.needadd { border-style: dashed; opacity: .8; }
.sic-cand.needadd small { opacity: .6; font-size: 10px; }
.sic-flds { display: inline-flex; gap: 4px; }
.sic-newrow { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
.sic-newform { margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--line, #ddd); }
.sic-newform .sic-btn { margin-top: 8px; }
.sic-msg { font-size: 12px; margin-top: 8px; }
.sic-msg.err { color: #e05656; }
</style>
