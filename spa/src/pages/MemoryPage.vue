<template>
  <PageShell title="🌅 记忆相册" icon="image" sub="生活照回忆时光轴（和图片库同一套照片）· 一句话找照片 · AI 修图 · 看图编故事">
    <!-- 工具条：一句话搜索 + 快速分类 + 批量导入 -->
    <div class="mem-toolbar">
      <input class="mem-search" v-model="kw" placeholder="一句话找照片，比如：上次拍的晚饭 / 孩子的笑脸 / 海边"
             @keyup.enter="load" />
      <button class="mem-btn mem-btn-primary" @click="load">找照片</button>
      <button class="mem-btn" @click="pickImport">📥 从相册导入</button>
      <input ref="fileInput" type="file" accept="image/*" multiple style="display:none" @change="doImport" />
    </div>
    <div class="mem-chips">
      <button v-for="p in people" :key="p.name" class="mem-chip mem-chip-person"
              :class="{ on: activePerson === p.name }" @click="pickPerson(p.name)">
        <img v-if="p.thumb_url" :src="p.thumb_url" class="mem-person-face" />{{ p.name }}<small>{{ p.count }}</small>
      </button>
      <button class="mem-chip mem-chip-scan" :disabled="scanning" @click="doFaceScan">{{ scanning ? '🔍 扫描中…' : '🔍 扫描认人' }}</button>
      <span v-if="scanHint" class="mem-hint">{{ scanHint }}</span>
    </div>
    <div class="mem-chips">
      <button v-for="c in CATS" :key="c" class="mem-chip" :class="{ on: activeCat === c }"
              @click="pickCat(c)">{{ c }}</button>
      <span v-if="importHint" class="mem-hint">{{ importHint }}</span>
      <span class="mem-count">{{ images.length }} 张</span>
    </div>

    <!-- 时间线图墙（按月分组） -->
    <div v-if="loading" class="mem-empty">加载中…</div>
    <div v-else-if="!images.length" class="mem-empty">
      这里还没有生活照片。<br />
      <span class="mem-empty-sub">点上方「从相册导入」把手机/电脑里的照片批量搬进来；手机 APP 拍照会自动归档到这里。之后一句话就能找到，还能让哈文修图、编故事。</span>
    </div>
    <div v-else>
      <div v-for="g in grouped" :key="g.month" class="mem-month">
        <div class="mem-month-label">{{ g.month }} <small>{{ g.items.length }} 张</small></div>
        <div class="mem-grid">
          <div v-for="im in g.items" :key="im.id" class="mem-card" @click="openDetail(im)">
            <img :src="im.thumb_url" loading="lazy" />
            <div class="mem-card-note">{{ im.note || '（还没写描述）' }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 详情 + AI 弹层 -->
    <div v-if="detail" class="mem-overlay" @click.self="closeDetail">
      <div class="mem-detail">
        <img class="mem-big" :src="detail.url" @click="showBig = !showBig" />
        <div v-if="showBig" class="mem-fullwrap" @click="showBig = false">
          <img class="mem-full" :src="detail.url" />
        </div>

        <!-- AI 说话 -->
        <div class="mem-ai-row">
          <span class="mem-ai-label">让哈文看图说话：</span>
          <button class="mem-btn mem-sm" :disabled="aiBusy" @click="speak('story')">📖 编故事</button>
          <button class="mem-btn mem-sm" :disabled="aiBusy" @click="speak('poem')">✒️ 写首诗</button>
          <button class="mem-btn mem-sm" :disabled="aiBusy" @click="speak('kid')">🧒 讲给孩子听</button>
        </div>
        <div v-if="aiText" class="mem-ai-text">{{ aiText }}<span v-if="aiModel" class="mem-ai-model">（{{ aiModel }} 本地生成）</span></div>

        <!-- 修图 -->
        <div class="mem-edit-sec">
          <div class="mem-ai-label">修图（基础款本地免费秒出）：</div>
          <div class="mem-edit-row">
            <button v-for="f in FILTERS" :key="f.a" class="mem-btn mem-sm" :disabled="editBusy"
                    @click="doEdit(f.a)">{{ f.n }}</button>
            <button class="mem-btn mem-sm" :disabled="editBusy" @click="doEdit('rotate')">↻ 旋转</button>
            <button class="mem-btn mem-sm" :disabled="editBusy" @click="doEdit('crop')">✂️ 裁边</button>
            <button class="mem-btn mem-sm" :disabled="editBusy" @click="doEdit('frame')">🖼 加相框</button>
          </div>
          <div class="mem-edit-row">
            <span class="mem-ai-label">亮度</span>
            <input type="range" min="0" max="100" v-model="brightVal" class="mem-range" />
            <button class="mem-btn mem-sm" :disabled="editBusy" @click="doEdit('brighten', { strength: brightVal / 100 })">应用调亮</button>
          </div>
          <!-- 生成式修图（二期：需要 Stable Diffusion 在跑） -->
          <div class="mem-edit-row">
            <input class="mem-gen-input" v-model="genPrompt"
                   :placeholder="genReady ? 'AI 大改：想改成什么样？比如 水彩画风格 / 变成冬天' : '生成式修图需要先启动 Stable Diffusion（画画引擎）'"
                   :disabled="!genReady || editBusy" @keyup.enter="doGenEdit" />
            <button class="mem-btn mem-sm" :disabled="!genReady || editBusy" @click="doGenEdit">✨ AI 修图</button>
          </div>
          <div v-if="editHint" class="mem-hint">{{ editHint }}</div>
        </div>

        <!-- 描述 / 删除 / 修图历史 -->
        <div class="mem-drow"><label>描述</label>
          <input v-model="detail.note" placeholder="这是哪的照片（也可以让 AI 打标签）" />
        </div>
        <div class="mem-dactions">
          <button class="mem-btn mem-btn-primary" @click="saveNote">保存描述</button>
          <button class="mem-btn" @click="retag">✨ AI 打标签</button>
          <button class="mem-btn mem-btn-danger" @click="removeImg">删除</button>
          <button class="mem-btn" @click="closeDetail">关闭</button>
        </div>
        <div v-if="detail.tags && detail.tags.length" class="mem-tags">
          <span v-for="t in detail.tags" :key="t" class="mem-tag">{{ t }}</span>
        </div>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
// ★ #925 T4 2026-09-29 记忆相册（定版拍板：图片库=总存放地，记忆相册=手机生活照 AI 相册）
// 一句话找照片（分词搜索）/ 时间线 / AI 修图（一期本地 PIL 免费 + 二期 SD 生成式）/ 看图编故事
import { ref, computed, onMounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import { getJSON, postJSON, delJSON } from '../api'

const CATS = ['自拍', '风景', '美食', '家人', '宠物', '截图', '生活照']
const FILTERS = [
  { a: 'warm', n: '☀️ 暖色' }, { a: 'cool', n: '❄️ 冷色' }, { a: 'bw', n: '🕶 黑白' },
  { a: 'vivid', n: '🎨 鲜艳' }, { a: 'retro', n: '📻 复古' }, { a: 'contrast', n: '🌗 对比' },
]

const kw = ref('')
const activeCat = ref('')
const images = ref([])
const loading = ref(true)
const fileInput = ref(null)
const importHint = ref('')
/* ★ #1491 按人翻回忆：人物 chips + 按人筛选 + 全量扫描 */
const people = ref([])
const activePerson = ref('')
const scanning = ref(false)
const scanHint = ref('')
const detail = ref(null)
const showBig = ref(false)
const aiText = ref('')
const aiModel = ref('')
const aiBusy = ref(false)
const editBusy = ref(false)
const editHint = ref('')
const brightVal = ref(35)
const genPrompt = ref('')
const genReady = ref(false)

// 时间线分组：created_at 'YYYY-MM-DD HH:MM:SS' → 'YYYY年M月'
const grouped = computed(() => {
  const map = new Map()
  for (const im of images.value) {
    const m = (im.created_at || '').slice(0, 7)
    const label = m ? `${m.slice(0, 4)}年${parseInt(m.slice(5, 7), 10)}月` : '更早'
    if (!map.has(label)) map.set(label, [])
    map.get(label).push(im)
  }
  return [...map.entries()].map(([month, items]) => ({ month, items }))
})

async function load() {
  loading.value = true
  try {
    // 生活照域：优先按分类词搜；无分类时拉全部（本页就是生活照主场）
    const q = activeCat.value || kw.value.trim()
    const url = `/api/images/list?q=${encodeURIComponent(q)}&limit=500`
    const r = await getJSON(url)
    let imgs = r.images || []
    // 拉全部时把面板存档/知识库文件这些"工作图"滤掉，只留生活照来源
    if (!q) imgs = imgs.filter(i => ['mobile', 'upload', 'edited', 'chat'].includes(i.source))
    images.value = imgs
  } catch (e) { images.value = [] }
  loading.value = false
}

function pickCat(c) {
  activeCat.value = activeCat.value === c ? '' : c
  activePerson.value = ''
  kw.value = ''
  load()
}

/* ═══ ★ #1491 按人翻回忆 ═══ */
async function loadPeople() {
  try {
    const r = await getJSON('/api/images/face/people')
    people.value = r.people || []
  } catch (e) { people.value = [] }
}

async function pickPerson(name) {
  if (activePerson.value === name) {   // 再点一次取消，回全部
    activePerson.value = ''
    activeCat.value = ''
    await load()
    return
  }
  activePerson.value = name
  activeCat.value = ''
  kw.value = ''
  loading.value = true
  try {
    const r = await getJSON(`/api/images/face/people/${encodeURIComponent(name)}`)
    images.value = r.images || []
  } catch (e) { images.value = [] }
  loading.value = false
}

async function doFaceScan() {
  if (scanning.value) return
  scanning.value = true
  scanHint.value = ''
  try {
    const r = await postJSON('/api/images/face/scan', {})
    scanHint.value = r.message || (r.success ? '扫描已启动' : (r.error || '启动不了'))
    if (r.success) {
      // 轮询进度（115 张实测 ~20s，上限 3 分钟）
      const t0 = Date.now()
      const timer = setInterval(async () => {
        try {
          const s = await getJSON('/api/images/face/scan')
          scanHint.value = s.running ? `🔍 扫描中 ${s.done}/${s.total}…` : ''
          if (!s.running) {
            clearInterval(timer)
            scanning.value = false
            await loadPeople()
            if (s.hits) scanHint.value = `认出 ${s.hits} 张带家人的照片，点上面人名看回忆`
            setTimeout(() => { scanHint.value = '' }, 5000)
          }
        } catch (e) { clearInterval(timer); scanning.value = false }
      }, 2000)
    } else scanning.value = false
  } catch (e) { scanHint.value = String(e.message || e); scanning.value = false }
}

function pickImport() { fileInput.value && fileInput.value.click() }

async function doImport(ev) {
  const files = ev.target.files || []
  if (!files.length) return
  importHint.value = `导入中…（${files.length} 张）`
  let ok = 0
  for (const f of files) {
    try {
      const b64 = await new Promise((resolve, reject) => {
        const rd = new FileReader()
        rd.onload = e => resolve(String(e.target.result).split(',')[1] || '')
        rd.onerror = reject
        rd.readAsDataURL(f)
      })
      const r = await postJSON('/api/images/upload', { image_b64: b64, note: '', album: '生活照' })
      if (r.success) ok++
    } catch (e) { /* 单张失败继续 */ }
  }
  importHint.value = `已导入 ${ok}/${files.length} 张，AI 正在后台打标签`
  ev.target.value = ''
  await load()
  setTimeout(() => { importHint.value = '' }, 4000)
}

function openDetail(im) {
  detail.value = { ...im }
  showBig.value = false
  aiText.value = ''
  aiModel.value = ''
  editHint.value = ''
}

function closeDetail() { detail.value = null }

async function speak(style) {
  if (!detail.value || aiBusy.value) return
  aiBusy.value = true
  aiText.value = ''
  try {
    const r = await postJSON(`/api/images/${detail.value.id}/describe`, { style })
    if (r.success) { aiText.value = r.text; aiModel.value = (r.model || '').slice(0, 24) }
    else aiText.value = `没说成：${r.error || '视觉模型没有返回'}`
  } catch (e) { aiText.value = `没说成：${e.message || e}` }
  aiBusy.value = false
}

async function doEdit(action, params) {
  if (!detail.value || editBusy.value) return
  editBusy.value = true
  editHint.value = '修图中…'
  try {
    const r = await postJSON(`/api/images/${detail.value.id}/edit`, { action, params: params || {} })
    if (r.success) {
      editHint.value = '修好了（原图还在，修图版是新存的一张）'
      const fresh = await getJSON(`/api/images/list?q=${encodeURIComponent(detail.value.id)}&limit=1`).catch(() => null)
      const nr = fresh && fresh.images && fresh.images[0]
      detail.value = nr || { ...detail.value, id: r.id, url: r.url, thumb_url: r.url + '?thumb=1', source: 'edited', tags: [] }
      await load()
    } else editHint.value = `没修成：${r.error || '未知原因'}`
  } catch (e) { editHint.value = `没修成：${e.message || e}` }
  editBusy.value = false
}

async function doGenEdit() {
  if (!detail.value || editBusy.value || !genPrompt.value.trim()) return
  editBusy.value = true
  editHint.value = 'AI 修图中…（生成式要等十几秒到一分钟）'
  try {
    const r = await postJSON(`/api/images/${detail.value.id}/gen_edit`, { prompt: genPrompt.value.trim() })
    if (r.success) {
      editHint.value = 'AI 修好了'
      genPrompt.value = ''
      detail.value = { ...detail.value, id: r.id, url: r.url, thumb_url: r.url + '?thumb=1', source: 'edited', tags: [] }
      await load()
    } else editHint.value = r.error || '没修成'
  } catch (e) { editHint.value = `没修成：${e.message || e}` }
  editBusy.value = false
}

async function saveNote() {
  const d = detail.value
  if (!d) return
  await postJSON(`/api/images/${d.id}`, { note: d.note })
  editHint.value = '描述已保存'
  await load()
}

async function retag() {
  const d = detail.value
  if (!d) return
  try {
    const r = await postJSON(`/api/images/${d.id}/retag`, {})
    if (r.success && r.tags) { d.tags = r.tags; editHint.value = `标签：${r.tags.join('、')}` }
    else editHint.value = r.error || '打标失败'
  } catch (e) { editHint.value = String(e.message || e) }
}

async function removeImg() {
  const d = detail.value
  if (!d) return
  if (!confirm('删除这张？（修图版和原图各自独立，只删当前这张）')) return
  await delJSON(`/api/images/${d.id}`)
  detail.value = null
  await load()
}

onMounted(async () => {
  await Promise.all([load(), loadPeople(),
    getJSON('/api/images/genstatus').then(r => { genReady.value = !!r.available }).catch(() => {})])
})
</script>

<style scoped>
.mem-toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.mem-search { flex: 1; min-width: 220px; border: 1px solid var(--c-line); background: var(--c-card);
  color: var(--c-text); border-radius: 10px; padding: 9px 12px; font-size: 13px; outline: none; }
.mem-btn { border: 1px solid var(--c-line); background: var(--c-card); color: var(--c-text);
  border-radius: 10px; padding: 8px 14px; font-size: 13px; cursor: pointer; }
.mem-btn:hover { border-color: var(--c-primary); color: var(--c-primary); }
.mem-btn:disabled { opacity: .5; cursor: not-allowed; }
.mem-btn-primary { background: var(--c-primary); border-color: var(--c-primary); color: #fff; }
.mem-sm { padding: 5px 10px; font-size: 12px; }
.mem-chips { display: flex; gap: 8px; margin: 12px 0 16px; align-items: center; flex-wrap: wrap; }
.mem-chip { border: 1px solid var(--c-line); background: var(--c-card); color: var(--c-text-2);
  border-radius: 999px; padding: 4px 14px; font-size: 12.5px; cursor: pointer; }
.mem-chip.on { background: var(--c-primary-weak); color: var(--c-primary); border-color: var(--c-primary); font-weight: 600; }
/* ★ #1491 人物 chips（按人翻回忆） */
.mem-chip-person { display: inline-flex; align-items: center; gap: 6px; }
.mem-chip-person small { opacity: .65; font-size: 11px; }
.mem-person-face { width: 22px; height: 22px; border-radius: 50%; object-fit: cover; flex: none; }
.mem-chip-scan { border-style: dashed; }
.mem-chip:disabled { opacity: .5; cursor: wait; }
.mem-hint { font-size: 12px; color: var(--c-primary); }
.mem-count { margin-left: auto; font-size: 12px; color: var(--c-text-2); }
.mem-empty { text-align: center; color: var(--c-text-2); padding: 60px 0; font-size: 13px; line-height: 1.9; }
.mem-empty-sub { font-size: 12px; }
.mem-month { margin-bottom: 22px; }
.mem-month-label { font-size: 14px; font-weight: 700; color: var(--c-text); margin-bottom: 10px; }
.mem-month-label small { font-weight: 400; color: var(--c-text-2); margin-left: 6px; }
.mem-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.mem-card { background: var(--c-card); border: 1px solid var(--c-line); border-radius: 12px;
  overflow: hidden; cursor: pointer; transition: transform .12s, box-shadow .12s; }
.mem-card:hover { transform: translateY(-2px); box-shadow: 0 4px 14px rgba(0,0,0,.08); }
.mem-card img { width: 100%; height: 120px; object-fit: cover; display: block; background: var(--c-bg-soft); }
.mem-card-note { padding: 6px 9px; font-size: 12px; color: var(--c-text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mem-overlay { position: fixed; inset: 0; background: rgba(20,28,40,.45); z-index: 999;
  display: flex; align-items: center; justify-content: center; }
.mem-detail { background: var(--c-card); border-radius: 16px; padding: 18px; width: 94%;
  max-width: 620px; max-height: 90vh; overflow: auto; }
.mem-big { width: 100%; max-height: 300px; object-fit: contain; border-radius: 10px;
  background: var(--c-bg-soft); cursor: zoom-in; }
.mem-fullwrap { position: fixed; inset: 0; background: rgba(0,0,0,.85); z-index: 1000;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out; }
.mem-full { max-width: 94vw; max-height: 94vh; object-fit: contain; }
.mem-ai-row { display: flex; gap: 8px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
.mem-ai-label { font-size: 12.5px; color: var(--c-text-2); }
.mem-ai-text { margin-top: 10px; padding: 10px 12px; background: var(--c-bg-soft); border-radius: 10px;
  font-size: 13px; line-height: 1.8; color: var(--c-text); white-space: pre-wrap; }
.mem-ai-model { font-size: 11px; color: var(--c-text-2); margin-left: 6px; }
.mem-edit-sec { margin-top: 12px; border-top: 1px dashed var(--c-line); padding-top: 10px; }
.mem-edit-row { display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
.mem-range { flex: 0 0 140px; }
.mem-gen-input { flex: 1; min-width: 180px; border: 1px solid var(--c-line); background: var(--c-bg);
  color: var(--c-text); border-radius: 8px; padding: 7px 10px; font-size: 12.5px; outline: none; }
.mem-drow { display: flex; gap: 10px; align-items: center; margin-top: 12px; }
.mem-drow label { width: 40px; font-size: 12px; color: var(--c-text-2); flex-shrink: 0; }
.mem-drow input { flex: 1; min-width: 0; border: 1px solid var(--c-line); background: var(--c-bg);
  color: var(--c-text); border-radius: 8px; padding: 7px 10px; font-size: 13px; outline: none; }
.mem-dactions { display: flex; gap: 8px; margin-top: 12px; justify-content: flex-end; flex-wrap: wrap; }
.mem-btn-danger { color: #E05B5B; }
.mem-tags { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
.mem-tag { font-size: 11px; color: var(--c-text-2); background: var(--c-bg-soft);
  border-radius: 999px; padding: 2px 9px; }
@media (max-width: 640px) {
  .mem-grid { grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 8px; }
  .mem-card img { height: 92px; }
}
</style>
