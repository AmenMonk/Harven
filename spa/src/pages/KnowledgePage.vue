<template>
  <PageShell :title="t('page.knowledge.t')" icon="book" :sub="t('page.knowledge.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="load(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
      <BigButton :px="14" @click="fileInput.click()"><HIcon name="download" :size="16" /> 上传文档</BigButton>
      <BigButton variant="ghost" :px="14" :loading="folderBusy" @click="folderInput.click()">📁 导入笔记文件夹</BigButton>
      <input ref="fileInput" type="file" multiple hidden @change="onUpload" />
      <input ref="folderInput" type="file" webkitdirectory multiple hidden @change="onFolderImport" />
    </template>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>
    <div v-if="folderBusy" class="tip">⏳ 正在导入笔记：{{ folderDone }} / {{ folderTotal }} 个文件…（导入中请勿关页）</div>

    <!-- ★ #1242 知识库总开关：关=聊天不查资料（回答更快），开=本地模式自动检索（隐私不出网） -->
    <div class="tip" :class="kbOn ? 'ok' : 'bad'" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
      <span>📚 知识库检索：{{ kbOn
        ? '已开启 —— 本地模式回答时自动查你的资料（内容不出网）'
        : '已关闭 —— 聊天不再查资料，回答更快；上传的资料保留，随时可再开' }}</span>
      <button class="mini" :class="kbOn ? 'danger' : ''" @click="toggleKb">{{ kbOn ? '关闭检索' : '开启检索' }}</button>
    </div>

    <div class="tool-row">
      <div class="search"><HIcon name="search" :size="16" /><input v-model.trim="q" placeholder="按标题或内容关键字过滤（聊天里直接问，也能自动查知识库）" /></div>
      <span class="total">共 {{ items.length }} 份资料</span>
    </div>

    <div v-if="!filtered.length" class="empty">
      {{ q ? '没有匹配的资料' : '知识库还是空的。点右上角「上传文档」，支持 txt / md / pdf / docx / 音频 / 图片等格式。' }}
    </div>

    <div class="kb-grid">
      <div v-for="it in filtered" :key="it.filename" class="kcard">
        <div class="k-top">
          <span class="fmt" :class="'f-' + (it.format || 'text')">{{ (it.format || 'text').toUpperCase() }}</span>
          <b class="k-name" :title="it.filename">{{ it.filename }}</b>
        </div>
        <p class="k-preview" title="点「查看」看全文；聊天里直接问就能命中这份资料">{{ it.preview || '（无预览）' }}</p>
        <div class="k-foot">
          <span class="chunks"><i class="in-ok">✓ 已入库</i> {{ it.chunks }} 个片段 · 提问即命中</span>
          <div class="k-btns">
            <button class="mini distill" title="可选增强：把资料提炼成摘要/问答，检索更准。不点也能正常用，不影响提问" @click="openDistill(it)">⚗️ 蒸馏</button>
            <button class="mini" @click="view(it)">查看</button>
            <button class="mini danger" @click="del(it)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 蒸馏任务区（#797） ═══ -->
    <div v-if="jobs.length" class="dj-sec">
      <div class="dj-head">⚗️ 蒸馏任务
        <span class="dj-sub">蒸馏产物需审核后才进入知识库（双层存储）</span>
      </div>
      <div v-for="j in jobs" :key="j.id" class="dj-row">
        <span class="dj-name" :title="j.source_name">{{ j.source_name }}</span>
        <span class="dj-status" :class="'s-' + j.status">
          {{ j.status === 'running' ? `蒸馏中 ${j.done_chunks}/${j.total_chunks}`
            : j.status === 'done' ? '蒸馏完成'
            : j.status === 'failed' ? '失败（可续跑）'
            : j.status === 'pending' ? '排队中' : j.status }}
        </span>
        <span v-if="j.pending_items" class="dj-badge">{{ j.pending_items }} 条待审</span>
        <div class="k-btns">
          <button v-if="j.status === 'done' && j.pending_items" class="mini distill" @click="openPreview(j)">预览审核</button>
          <button v-if="j.status === 'failed'" class="mini" @click="resumeJob(j)">续跑</button>
          <button class="mini danger" @click="delJob(j)">删除</button>
        </div>
      </div>
    </div>

    <!-- ═══ 蒸馏启动弹层（门2：用户点头选引擎） ═══ -->
    <div v-if="distilling" class="modal-mask" @click.self="distilling = null">
      <div class="modal dm">
        <div class="m-head"><b>⚗️ 蒸馏「{{ distilling.filename }}」</b><button class="mini" @click="distilling = null">关闭</button></div>
        <div class="m-body distill-form">
          <template v-if="distillGate.ok">
            <p class="df-note">把这份资料提炼成<b>摘要 / 要点 / 问答对 / 术语表</b>，以后提问时直接命中，又快又省。原文不动，产物需你审核后才入库。</p>
            <div class="df-row"><label>分类目录</label>
              <select v-model="distillCat">
                <option :value="0">不分类</option>
                <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
              </select>
              <button class="mini" @click="addCategory">新建分类</button>
            </div>
            <div class="df-row"><label>蒸馏引擎</label>
              <label class="df-radio"><input type="radio" value="local" v-model="distillEngine" /> 本地模型（免费，默认）</label>
              <label class="df-radio"><input type="radio" value="cloud" v-model="distillEngine" /> 外脑（更快，耗 token）</label>
            </div>
            <p v-if="distillEngine === 'cloud' && settings.distill_allow_cloud !== 'true'" class="df-warn">外脑蒸馏未开通，去下面打开「蒸馏允许外脑」开关。</p>
            <div class="df-actions">
              <BigButton :px="16" :loading="distillStarting" @click="startDistill">开始蒸馏</BigButton>
              <label class="df-radio"><input type="checkbox" v-model="allowCloud" @change="saveAllowCloud" /> 蒸馏允许外脑</label>
            </div>
          </template>
          <template v-else>
            <p class="df-warn">不能蒸馏：{{ distillGate.reason }}</p>
          </template>
        </div>
      </div>
    </div>

    <!-- ═══ 产物预览审核弹层（门3） ═══ -->
    <div v-if="previewing" class="modal-mask" @click.self="previewing = null">
      <div class="modal pm">
        <div class="m-head">
          <b>审核蒸馏产物 · {{ previewing.source_name }}</b>
          <div><label class="df-radio"><input type="checkbox" v-model="selAll" @change="toggleAll" /> 全选</label>
          <button class="mini" @click="previewing = null">关闭</button></div>
        </div>
        <div class="m-body">
          <div v-if="!pItems.length" class="df-note">没有待审产物。</div>
          <label v-for="it in pItems" :key="it.id" class="pi-row">
            <input type="checkbox" :value="it.id" v-model="picked" />
            <span class="pi-kind" :class="'k-' + it.kind">{{ kindLabel(it.kind) }}</span>
            <span class="pi-text">{{ it.kind === 'qa' ? `问：${it.question}\n答：${it.answer}` : it.content }}</span>
          </label>
        </div>
        <div class="m-foot">
          <span class="dj-sub">已选 {{ picked.length }} / {{ pItems.length }} 条</span>
          <BigButton :px="16" :loading="approving" @click="approvePick">入库所选</BigButton>
        </div>
      </div>
    </div>

    <!-- 查看弹层 -->
    <div v-if="viewing" class="modal-mask" @click.self="viewing = null">
      <div class="modal">
        <div class="m-head">
          <b>{{ viewing.filename }}</b>
          <button class="mini" @click="viewing = null">关闭</button>
        </div>
        <!-- ★ 2026-09-27 图片文件直接预览（原版只会读文本，jpg 必然乱码/空白） -->
        <div v-if="isImgFile(viewing.filename)" class="m-body kb-img-body">
          <img :src="'/api/media/knowledge/' + encodeURIComponent(viewing.filename)" alt="预览" @click="showBig = showBig ? '' : '/api/media/knowledge/' + encodeURIComponent(viewing.filename)" />
          <p class="kb-img-tip">点击图片放大 · 图片已入库参与搜索，聊天里问也能命中</p>
        </div>
        <pre v-else class="m-body">{{ viewText || '加载中…' }}</pre>
      </div>
    </div>
    <!-- 图片大图查看 -->
    <div v-if="showBig" class="modal-mask" @click="showBig = ''" style="z-index: 10001">
      <img :src="showBig" style="max-width:92vw;max-height:88vh;border-radius:8px" alt="大图" />
    </div>
  </PageShell>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'

const items = ref([])
const q = ref('')
const msg = ref('')
const msgOk = ref(false)
const viewing = ref(null)
const viewText = ref('')
const fileInput = ref(null)

const filtered = computed(() => {
  if (!q.value) return items.value
  const k = q.value.toLowerCase()
  return items.value.filter(it => (it.filename + ' ' + (it.preview || '')).toLowerCase().includes(k))
})

async function load(showErr = false) {
  try {
    const j = await getJSON('/api/kb/list')
    items.value = j.items || []
    if (showErr) msg.value = ''
  } catch (e) { if (showErr) { msg.value = '加载失败：' + e.message; msgOk.value = false } }
}

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 4000)
}

/* ── ★ #1242 知识库总开关（kb_search_enabled，即时生效无需重启）── */
const kbOn = ref(true)
async function loadKbToggle() {
  try { const j = await getJSON('/api/kb/toggle'); kbOn.value = !!j.enabled } catch (_) {}
}
async function toggleKb() {
  try {
    const j = await postJSON('/api/kb/toggle', { enabled: !kbOn.value })
    if (j.success) { kbOn.value = j.enabled; flash(j.msg || '已切换') }
    else flash(j.error || '切换失败', false)
  } catch (e) { flash('切换失败：' + e.message, false) }
}
loadKbToggle()

async function onUpload(ev) {
  const files = [...ev.target.files || []]
  ev.target.value = ''
  if (!files.length) return
  const fd = new FormData()
  files.forEach(f => fd.append('files', f))
  try {
    const res = await fetch('/api/knowledge/upload', { method: 'POST', body: fd })
    const j = await res.json()
    if (j.success !== false) { flash(`✅ 已导入 ${files.length} 个文件并完成入库——现在就能直接问哈文（比如：这份资料里关于 XX 说了什么？），不需要等待`); await load() }
    else flash('导入失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('导入失败：' + e.message, false) }
}

/* ── ★ 2026-09-17 #739 笔记文件夹导入（取代 Obsidian 同步） ── */
const folderInput = ref(null)
const folderBusy = ref(false)
const folderDone = ref(0)
const folderTotal = ref(0)
const NOTE_EXT = ['.md', '.markdown', '.txt']
const BATCH = 8

async function onFolderImport(ev) {
  const all = [...ev.target.files || []]
  ev.target.value = ''
  // 只要笔记格式；相对路径里的 / 换成 _ 防止同名碰撞和后端路径注入
  const files = all
    .filter((f) => NOTE_EXT.some((ext) => f.name.toLowerCase().endsWith(ext)))
    .map((f) => {
      const rel = f.webkitRelativePath || f.name
      const safeName = rel.replace(/\//g, '_')
      return new File([f], safeName, { type: f.type || 'text/plain' })
    })
  if (!files.length) { flash('这个文件夹里没有找到 .md / .txt 笔记文件', false); return }
  folderBusy.value = true
  folderTotal.value = files.length
  folderDone.value = 0
  let ok = 0; const failed = []
  try {
    for (let i = 0; i < files.length; i += BATCH) {
      const batch = files.slice(i, i + BATCH)
      const fd = new FormData()
      batch.forEach((f) => fd.append('files', f))
      const res = await fetch('/api/knowledge/upload', { method: 'POST', body: fd })
      const j = await res.json()
      const arr = Array.isArray(j) ? j : []
      arr.forEach((r) => {
        if (r.success) ok += 1
        else failed.push(`${r.name}：${r.error || r.message || '失败'}`)
      })
      folderDone.value = Math.min(i + BATCH, files.length)
    }
    let text = `✅ 导入完成：成功 ${ok} / 共 ${files.length} 个笔记`
    if (failed.length) text += `；失败 ${failed.length} 个（首个：${failed[0]}）`
    flash(text, failed.length < files.length)
    await load()
  } catch (e) { flash('导入中断：' + e.message, false) }
  finally { folderBusy.value = false; folderTotal.value = 0 }
}

async function view(it) {
  viewing.value = it
  viewText.value = ''
  // ★ 2026-09-27 图片文件走预览，不再读文本（read_text 读 jpg 必乱码）
  if (isImgFile(it.filename)) return
  try {
    const j = await getJSON('/api/kb/content?source=' + encodeURIComponent(it.filename))
    viewText.value = j.texts?.join('\n\n———\n\n') || j.text || '（没有取到内容）'
  } catch (e) { viewText.value = '读取失败：' + e.message }
}

const showBig = ref('')
const _IMG_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp']
function isImgFile(fn) {
  return _IMG_EXTS.includes((fn || '').split('.').pop().toLowerCase())
}

async function del(it) {
  if (!confirm(`确定删除「${it.filename}」的全部 ${it.chunks} 个片段？删除后不可恢复。`)) return
  try {
    const j = await postJSON('/api/kb/delete', { filename: it.filename })
    if (j.success) { flash(`已删除 ${j.removed} 个片段`); await load() }
    else flash('删除失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('删除失败：' + e.message, false) }
}

/* ── ★ 2026-09-19 #797 知识蒸馏：三道门（资格→选引擎→审核入库） ── */
const jobs = ref([])
const categories = ref([])
const settings = ref({})
const distilling = ref(null)          // 门2 弹层：当前资料
const distillGate = ref({ ok: true, reason: '' })
const distillEngine = ref('local')    // 默认本地（省 token）
const distillCat = ref(0)
const distillStarting = ref(false)
const allowCloud = ref(false)
const previewing = ref(null)          // 门3 弹层
const pItems = ref([])
const picked = ref([])
const selAll = ref(false)
const approving = ref(false)
let pollTimer = null

const KIND_LABEL = { summary: '摘要', outline: '要点', qa: '问答', term: '术语' }
const kindLabel = (k) => KIND_LABEL[k] || k

async function loadDistill() {
  try {
    const [j, jobsR] = await Promise.all([
      getJSON('/api/knowledge/distill/settings'),
      getJSON('/api/knowledge/distill/jobs'),
    ])
    settings.value = j || {}
    allowCloud.value = settings.value.distill_allow_cloud === 'true'
    jobs.value = jobsR.jobs || []
    const c = await getJSON('/api/knowledge/distill/categories')
    categories.value = c.categories || []
  } catch (e) { /* 静默：蒸馏区加载失败不影响主列表 */ }
}

function openDistill(it) {
  distilling.value = it
  distillEngine.value = 'local'
  // 门1 资格预检：txt/md 才可蒸馏（与后端 gate_check 一致）
  const ext = (it.filename.split('.').pop() || '').toLowerCase()
  const ok = ['txt', 'md', 'markdown'].includes(ext)
  distillGate.value = ok ? { ok: true, reason: '' }
    : { ok: false, reason: '一期蒸馏支持 txt / md 文本类资料' }
}

async function addCategory() {
  const name = prompt('新分类名称（如：大六壬 / 公司流程）：')
  if (!name) return
  try {
    await postJSON('/api/knowledge/distill/categories', { name })
    await loadDistill()
    flash('分类已创建')
  } catch (e) { flash('创建失败：' + e.message, false) }
}

async function startDistill() {
  if (!distilling.value) return
  distillStarting.value = true
  try {
    const j = await postJSON('/api/knowledge/distill/start', {
      source: distilling.value.filename,
      engine: distillEngine.value,
      category_id: distillCat.value,
    })
    if (j.success) {
      flash('⚗️ 蒸馏已开始，任务在下方展示进度，完成后请审核入库')
      distilling.value = null
      await loadDistill()
      startPoll()
    } else flash('启动失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('启动失败：' + e.message, false) }
  finally { distillStarting.value = false }
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(async () => {
    await loadDistill()
    if (!jobs.value.some(j => j.status === 'running' || j.status === 'pending')) stopPoll()
  }, 3000)
}
function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

async function resumeJob(j) {
  try {
    await postJSON('/api/knowledge/distill/start', { source: j.source_name, engine: 'local', resume: true })
    flash('已从断点续跑'); await loadDistill(); startPoll()
  } catch (e) { flash('续跑失败：' + e.message, false) }
}

async function delJob(j) {
  if (!confirm(`删除「${j.source_name}」的蒸馏任务和全部产物？（原文件不受影响）`)) return
  try { await fetch(`/api/knowledge/distill/jobs/${j.id}`, { method: 'DELETE' }); await loadDistill() }
  catch (e) { flash('删除失败：' + e.message, false) }
}

async function openPreview(j) {
  previewing.value = j
  picked.value = []; selAll.value = false
  try {
    const r = await getJSON(`/api/knowledge/distill/jobs/${j.id}/items`)
    pItems.value = r.items || []
  } catch (e) { pItems.value = []; flash('读取产物失败：' + e.message, false) }
}

function toggleAll() { picked.value = selAll.value ? pItems.value.map(i => i.id) : [] }

async function approvePick() {
  if (!picked.value.length) { flash('先勾选要入库的产物', false); return }
  approving.value = true
  try {
    const jid = previewing.value.id
    const j = await postJSON(`/api/knowledge/distill/jobs/${jid}/approve`,
      { approve: picked.value, reject: [] })
    if (j.success) {
      flash(`已入库 ${j.added} 条蒸馏知识（双层存储，来源自动标注）`)
      previewing.value = null
      await loadDistill()
    } else flash('入库失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('入库失败：' + e.message, false) }
  finally { approving.value = false }
}

async function saveAllowCloud() {
  try {
    await postJSON('/api/knowledge/distill/settings',
      { key: 'distill_allow_cloud', value: allowCloud.value ? 'true' : 'false' })
    flash(allowCloud.value ? '已允许外脑蒸馏' : '已切回纯本地蒸馏')
  } catch (e) { flash('保存失败：' + e.message, false) }
}

load(true)
loadDistill()
onUnmounted(stopPoll)
</script>

<style scoped>
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }
.tool-row { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }
.search { flex: 1; display: flex; align-items: center; gap: 8px; background: #fff; border: 1px solid var(--line); border-radius: 11px; padding: 9px 13px; color: var(--sub); }
.search input { flex: 1; border: none; outline: none; font-size: 13px; background: transparent; color: var(--ink); }
.total { font-size: 12.5px; color: var(--sub); white-space: nowrap; }
.empty { padding: 34px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13.5px; }

.kb-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.kcard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 13px 14px; display: flex; flex-direction: column; gap: 8px; }
.k-top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.fmt { font-size: 10px; font-weight: 800; letter-spacing: .5px; padding: 2px 7px; border-radius: 6px; background: var(--c-bg-soft); color: var(--sub); flex-shrink: 0; }
.fmt.f-pdf { background: #FDEEEE; color: #C24E4E; }
.fmt.f-docx, .fmt.f-doc { background: #EAF1FD; color: #4E76C2; }
.fmt.f-md, .fmt.f-txt { background: var(--mode-local-bg); color: var(--mode-local); }
.fmt.f-mp3, .fmt.f-wav, .fmt.f-m4a { background: var(--mode-vertical-bg); color: var(--mode-vertical); }
.k-name { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.k-preview { font-size: 12px; color: var(--sub); margin: 0; height: 36px; overflow: hidden; line-height: 1.5; }
.k-foot { display: flex; align-items: center; justify-content: space-between; }
.chunks { font-size: 11.5px; color: var(--sub); }
.chunks .in-ok { font-style: normal; color: #2E8B57; font-weight: 700; margin-right: 4px; }
.k-preview { cursor: pointer; }
.k-btns { display: flex; gap: 6px; }
.mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 11px; cursor: pointer; color: var(--ink); }
.mini:hover { border-color: var(--blue); color: var(--blue); }
.mini.danger:hover { border-color: var(--red); color: var(--red); }

.modal-mask { position: fixed; inset: 0; background: rgba(20, 26, 38, .4); z-index: 80; display: flex; align-items: center; justify-content: center; }
.modal { width: min(720px, 92vw); max-height: 80vh; background: #fff; border-radius: 14px; display: flex; flex-direction: column; overflow: hidden; }
.m-head { display: flex; justify-content: space-between; align-items: center; padding: 13px 16px; border-bottom: 1px solid var(--line); }
.m-body { margin: 0; padding: 16px; overflow: auto; font-size: 13px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; font-family: inherit; }
/* ★ 2026-09-27 知识库图片预览 */
.kb-img-body { display: flex; flex-direction: column; align-items: center; gap: 8px; white-space: normal; }
.kb-img-body img { max-width: 100%; max-height: 56vh; border-radius: 8px; cursor: zoom-in; box-shadow: 0 2px 12px rgba(0,0,0,.12); }
.kb-img-tip { margin: 0; font-size: 12px; opacity: .6; }

@media (max-width: 1000px) { .kb-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 700px) { .kb-grid { grid-template-columns: 1fr; } }

/* ── ★ #810 手机端：预览/查看弹窗字号加大 + 蒸馏行不挤 ── */
@media (max-width: 768px) {
  .m-body { font-size: 15px; line-height: 1.8; }
  .m-foot { padding: 12px 16px calc(12px + env(safe-area-inset-bottom)); }
  .df-row { flex-wrap: wrap; }
  .df-row > label:first-child { width: auto; }
  .df-row select { flex: 1; min-height: 40px; }
}

/* ── #797 蒸馏 ── */
.mini.distill:hover { border-color: var(--mode-vertical); color: var(--mode-vertical); }
.dj-sec { margin-top: 18px; border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 13px 15px; }
.dj-head { font-size: 13.5px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
.dj-sub { font-size: 11.5px; font-weight: 400; color: var(--sub); }
.dj-row { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-top: 1px dashed var(--line); }
.dj-name { flex: 1; font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dj-status { font-size: 12px; color: var(--sub); white-space: nowrap; }
.dj-status.s-running { color: var(--mode-vertical); font-weight: 600; }
.dj-status.s-done { color: #2E8B57; }
.dj-status.s-failed { color: #B4443C; }
.dj-badge { font-size: 11px; background: var(--mode-vertical-bg); color: var(--mode-vertical); border-radius: 8px; padding: 2px 8px; white-space: nowrap; }
.distill-form { display: flex; flex-direction: column; gap: 12px; }
.df-note { margin: 0; font-size: 12.5px; color: var(--sub); line-height: 1.6; }
.df-warn { margin: 0; font-size: 12.5px; color: #B4443C; }
.df-row { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.df-row > label:first-child { color: var(--sub); width: 66px; flex-shrink: 0; }
.df-row select { border: 1px solid var(--line); border-radius: 8px; padding: 5px 9px; font-size: 13px; background: #fff; color: var(--ink); }
.df-radio { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--ink); cursor: pointer; }
.df-actions { display: flex; align-items: center; gap: 14px; margin-top: 4px; }
.pm .m-body { display: flex; flex-direction: column; gap: 8px; }
.pi-row { display: flex; align-items: flex-start; gap: 9px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 10px; cursor: pointer; font-size: 12.5px; line-height: 1.55; }
.pi-row:hover { border-color: var(--blue); }
.pi-kind { flex-shrink: 0; font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 6px; background: var(--c-bg-soft); color: var(--sub); }
.pi-kind.k-summary { background: var(--mode-local-bg); color: var(--mode-local); }
.pi-kind.k-qa { background: var(--mode-vertical-bg); color: var(--mode-vertical); }
.pi-kind.k-term { background: #FFF7E6; color: #B07A1E; }
.pi-text { white-space: pre-wrap; word-break: break-word; color: var(--ink); }
.m-foot { display: flex; justify-content: space-between; align-items: center; padding: 11px 16px; border-top: 1px solid var(--line); }
</style>
