<template>
  <PageShell title="视觉引擎" icon="image" sub="HarvenEye 融合版 · 看图/批改/相册识别的大本营">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadAll"><HIcon name="pulse" :size="16" /> 刷新</BigButton>
    </template>

    <div v-if="msg" class="tip" :class="{ bad: msgBad }">{{ msg }}</div>

    <!-- ── 区1：底座引擎（yolox + Chinese-CLIP，固定初级版）── -->
    <section class="vcard">
      <div class="vhead">
        <b>👁 底座引擎（看东西的眼睛）</b>
        <span class="badge" :class="engineReady ? 'ok' : 'warn'">{{ engineReady ? '已就绪' : '未安装' }}</span>
      </div>
      <p class="vdesc">检测物体（YOLOX）+ 图文检索（Chinese-CLIP），约 757MB，装一次永久用。拍照片秒归类全靠它。</p>
      <div class="filegrid">
        <div v-for="g in engine?.groups || []" :key="g.name" class="fgroup">
          <div class="fg-head">{{ g.name === 'yolox' ? 'YOLOX 检测' : 'Chinese-CLIP 检索' }}
            <span class="mini" :class="g.ready ? 'ok' : 'warn'">{{ g.ready ? '✓' : '缺文件' }}</span>
          </div>
          <div v-for="f in g.files" :key="f.rel" class="frow">
            <span class="fname">{{ f.rel.split('/').pop() }}</span>
            <span class="mini" :class="f.present ? 'ok' : 'warn'">{{ f.present ? '就位' : '缺失' }}</span>
          </div>
        </div>
      </div>
      <div v-if="inst" class="pull-wrap">
        <div class="pull-bar"><div class="pull-fill" :class="{ done: inst.phase === '完成', bad: inst.phase === '失败' }" :style="{ width: pct + '%' }"></div></div>
        <span class="pull-text" :class="{ bad: inst.phase === '失败' }">
          {{ inst.phase }} {{ pct > 0 ? pct + '%' : '' }} {{ inst.file ? '· ' + inst.file.split('/').pop() : '' }}
          <template v-if="inst.error"> · {{ inst.error }}</template>
        </span>
      </div>
      <div class="vbtns">
        <BigButton v-if="!engineReady" :px="14" :loading="installing" @click="installEngine">一键安装底座（757MB）</BigButton>
        <span v-else class="okline">✓ 底座齐备，拍照归类已可用</span>
      </div>
    </section>

    <!-- ── 区2：理解大脑（用户选谁用谁）── -->
    <section class="vcard">
      <div class="vhead">
        <b>🧠 理解大脑（看懂东西的模型）</b>
        <span class="badge" :class="status?.llama_cpp_installed ? 'ok' : 'warn'">
          {{ status?.llama_cpp_installed ? '本地推理组件 ✓' : '本地推理组件缺（只影响本地模型，外脑不受影响）' }}
        </span>
      </div>
      <p class="vdesc">不用装也能用（自动走你选的外脑看图模型）。装了本地模型就零费用、离线可用、照片不出门。</p>

      <div class="pin-now">当前生效：<b>{{ pinnedNow || '自动（跟当前外脑走）' }}</b></div>

      <div class="sec-title">外脑视觉模型（联网、按量计费）</div>
      <div class="mrow" v-for="c in status?.cloud_list || []" :key="c.id">
        <label class="mradio">
          <input type="radio" name="vpin" :checked="pinned === c.id" @change="pin(c.id)" />
          <b>{{ c.name }}</b><span class="mini">{{ c.id }}</span>
        </label>
      </div>

      <div class="sec-title">本地视觉模型（离线、零费用）</div>
      <div v-if="!(status?.local_list || []).length" class="empty">
        还没有本地模型。把下载好的 <b>主模型 GGUF + mmproj GGUF</b> 用下方「导入」装进来（支持魔搭/HuggingFace/朋友拷贝的任何 GGUF）。
      </div>
      <div class="mrow" v-for="m in status?.local_list || []" :key="m.id">
        <label class="mradio">
          <input type="radio" name="vpin" :checked="pinned === m.id" :disabled="!m.ready" @change="pin(m.id)" />
          <b>{{ m.name }}</b>
          <span class="mini">{{ m.size_gb }}GB</span>
          <span class="mini" :class="m.ready ? 'ok' : 'warn'">{{ m.ready ? '可用' : '文件不全' }}</span>
        </label>
        <button class="mini-btn bad" @click="removeModel(m.name)">删除</button>
      </div>

      <details class="import-box">
        <summary>📥 导入本地模型（已下载好 GGUF 文件）</summary>
        <div class="imp-row"><span>模型名字</span><input v-model.trim="imp.name" placeholder="如 qwen3-vl-8b" /></div>
        <div class="imp-row"><span>主模型 GGUF 路径</span><input v-model.trim="imp.gguf" placeholder="如 H:\下载\Qwen3VL-8B-Q4_K_M.gguf" /></div>
        <div class="imp-row"><span>mmproj GGUF 路径</span><input v-model.trim="imp.mmproj" placeholder="如 H:\下载\mmproj-*.gguf" /></div>
        <BigButton :px="14" :loading="importing" @click="doImport">导入并安装</BigButton>
        <p class="vdesc">导入即把两个文件搬进哈文模型目录（同盘秒完成），立即可选。</p>
      </details>
    </section>

    <!-- ── 区3：人脸识别（★ #1470 批次四）── -->
    <section class="vcard">
      <div class="vhead">
        <b>👤 人脸识别（认人）</b>
        <span class="badge" :class="face?.enabled ? 'ok' : 'warn'">{{ face?.enabled ? '已开启' : '默认关闭' }}</span>
      </div>
      <p class="vdesc">认得家人：照片里的人标注名字（爸爸/妈妈…），陌生人只标「访客」不猜身份。🔒 隐私承诺：人脸特征<b>只存本机</b>，永不上传、可一键彻底删除。</p>

      <div class="face-toggle">
        <label class="mradio">
          <input type="checkbox" :checked="face?.enabled" @change="toggleFace" />
          <b>开启人脸识别</b>
          <span class="mini">（开启时自动下载识别模型，共 37MB，仅下载一次）</span>
        </label>
      </div>

      <div v-if="face && !face.models?.ready" class="pull-wrap">
        <BigButton v-if="!faceDlRunning" :px="14" @click="downloadFaceModels">下载人脸模型（37MB）</BigButton>
        <template v-else>
          <div class="pull-bar"><div class="pull-fill" :class="{ done: faceDl.phase === '完成', bad: faceDl.phase === '失败' }" :style="{ width: facePct + '%' }"></div></div>
          <span class="pull-text">{{ faceDl.phase }} {{ facePct }}%</span>
        </template>
      </div>

      <template v-if="face?.enabled">
        <div class="face-row">
          <span class="mini">判定阈值 {{ face.threshold }}（越高越严格，宁可标访客不标错人）</span>
          <input type="range" min="0.30" max="0.60" step="0.01" :value="face.threshold" @change="setThreshold($event)" style="width: 160px" />
        </div>

        <div class="sec-title">家人成员（{{ members.length }}）</div>
        <div v-if="!members.length" class="empty">还没录家人。点下方「录家人」，选 3~5 张 TA 的照片即可。</div>
        <div class="mrow" v-for="m in members" :key="m.id">
          <span><b>{{ m.name }}</b><span class="mini">{{ m.relation || '' }} · 已录 {{ m.emb_count }} 张脸</span></span>
          <button class="mini-btn bad" @click="delMember(m)">删除</button>
        </div>

        <div class="vbtns" style="display:flex; gap:8px; flex-wrap:wrap">
          <BigButton :px="14" @click="startEnroll">＋ 录家人</BigButton>
          <BigButton variant="ghost" :px="14" @click="wipeFace" v-if="members.length">🗑 彻底清除所有人脸数据</BigButton>
        </div>
        <p class="vdesc">录完可拿新照片试认：对话里发照片问「这是谁」，或看照片标签是否带名字。</p>
      </template>

      <!-- 录人弹层：选照片 3~5 张 -->
      <div v-if="picking" class="pick-mask" @click.self="picking = false">
        <div class="pick-box">
          <div class="pick-head">
            <b>录入：{{ enrollMember?.name }}</b>
            <span class="mini">已选 {{ pickIds.length }} 张（建议 3~5 张不同角度/光线的正脸照）</span>
            <button class="mini-btn" @click="picking = false">关</button>
          </div>
          <div class="pick-grid">
            <div v-for="im in pickImages" :key="im.id" class="pick-item" :class="{ sel: pickIds.includes(im.id) }" @click="togglePick(im.id)">
              <img :src="`/api/images/file/${im.id}?thumb=1`" loading="lazy" />
              <span v-if="pickIds.includes(im.id)" class="pick-num">{{ pickIds.indexOf(im.id) + 1 }}</span>
            </div>
          </div>
          <div class="pick-foot">
            <BigButton :px="14" :loading="enrolling" :disabled="pickIds.length < 1" @click="doEnroll">录入 {{ pickIds.length }} 张照片</BigButton>
          </div>
        </div>
      </div>
    </section>

    <!-- ── 区4：🧰 视觉能力（★ 2026-11-12 能力五件套：识字/搜图/扫码/车牌/活体）── -->
    <section class="card">
      <div class="face-head"><b>🧰 视觉能力（拍照识字 · 以文搜图 · 扫码 · 车牌）</b></div>
      <p class="vdesc">每张卡都是本地跑的小模型，不联网不花钱。识字/扫码/车牌去图片库点开照片就能用；以文搜图在图片库搜索框切「语义」。</p>
      <div class="cap-grid">
        <div class="cap-card">
          <b>📄 拍照识字</b><span class="mini">账单小票 / 快递单 / 说明书，瞬间抽出文字</span>
          <span class="cap-st" :class="caps.ocr ? 'ok' : 'off'">{{ caps.ocr ? '✓ 就位' : '未就位' }}</span>
        </div>
        <div class="cap-card">
          <b>🔍 以文搜图</b><span class="mini">搜「老谭在海边」→ 相关照片全出来</span>
          <span class="cap-st" :class="caps.semsearch?.clip_ready ? 'ok' : 'off'">{{ caps.semsearch?.clip_ready ? `✓ 已索引 ${caps.semsearch?.indexed || 0}/${caps.semsearch?.total || 0}` : '需装底座' }}</span>
          <button v-if="caps.semsearch?.clip_ready" class="mini-btn" :disabled="caps.semsearch?.running" @click="buildIndex">
            {{ caps.semsearch?.running ? `建索引中 ${caps.semsearch?.indexed || 0}/${caps.semsearch?.total || 0}` : (caps.semsearch?.indexed < caps.semsearch?.total ? '继续建索引' : '重建索引') }}
          </button>
        </div>
        <div class="cap-card">
          <b>🔗 扫码识码</b><span class="mini">二维码 / 商品条码，拍下就出内容</span>
          <span class="cap-st ok">✓ 就位</span>
        </div>
        <div class="cap-card">
          <b>🚗 车牌识别</b><span class="mini">拍车自动抽出车牌号（车辆面板联动）</span>
          <span class="cap-st" :class="caps.lpr ? 'ok' : 'off'">{{ caps.lpr ? '✓ 就位' : '未就位' }}</span>
        </div>
        <div class="cap-card">
          <b>🛡 活体检测</b><span class="mini">防拿照片糊屏幕骗过人脸识别</span>
          <span class="cap-st" :class="caps.alive ? 'ok' : 'off'">{{ caps.alive ? '✓ 就位' : '未下载' }}</span>
          <button v-if="!caps.alive" class="mini-btn" :disabled="aliveInstalling" @click="installAlive">{{ aliveInstalling ? '下载中…' : '下载（3.5MB）' }}</button>
        </div>
      </div>
    </section>
  </PageShell>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getJSON, postJSON, delJSON } from '../api.js'

const engine = ref(null)
const status = ref(null)
const inst = ref(null)
const msg = ref('')
const msgBad = ref(false)
const installing = ref(false)
const importing = ref(false)
const imp = ref({ name: '', gguf: '', mmproj: '' })
// ★ #1470 人脸识别
const face = ref(null)
const members = ref([])
const faceDl = ref(null)
const faceDlRunning = ref(false)
const picking = ref(false)
const enrollMember = ref(null)
const pickIds = ref([])
const pickImages = ref([])
const enrolling = ref(false)
const facePct = computed(() => {
  const p = faceDl.value
  if (!p?.total_bytes) return 0
  return Math.min(100, Math.round((p.done_bytes / p.total_bytes) * 100))
})
// ★ 2026-11-12 能力五件套
const caps = ref({})
const aliveInstalling = ref(false)
let capTimer = null
async function loadCaps() {
  try { const r = await getJSON('/api/vision/capabilities'); caps.value = r || {} } catch {}
}
async function buildIndex() {
  try {
    await postJSON('/api/photos/semindex', {})
    flash('开始后台建索引（第一次会慢一点，之后新照片自动补）', true)
  } catch (e) { flash('启动失败：' + (e.message || e), false) }
}
async function installAlive() {
  aliveInstalling.value = true
  try {
    // 走底座安装器通道（幂等：yolox/clip 已就位自动跳过，只补活体模型）
    const r = await postJSON('/api/vision/engine/install', {})
    flash(r?.started ? '开始后台下载活体模型（3.5MB）…' : '已在跑或已就位', true)
  } catch (e) { flash('下载失败：' + (e.message || e), false) }
  finally { aliveInstalling.value = false }
}
let timer = null

const engineReady = computed(() => !!engine.value?.ready)
const pinned = computed(() => status.value?.pinned || '')
const pinnedNow = computed(() => {
  const r = status.value?.resolved
  if (!r) return ''
  if (r.provider === 'api') return `外脑 ${r.api_model}`
  if (r.provider === 'llama_cpp') return `本地 ${String(r.gguf || '').split(/[\\/]/).slice(-2, -1)[0] || '模型'}`
  if (r.provider === 'ollama') return '本机 Ollama'
  return ''
})
const pct = computed(() => {
  const p = inst.value
  if (!p?.total_bytes) return 0
  return Math.min(100, Math.round((p.done_bytes / p.total_bytes) * 100))
})

function flash(text, bad = false) { msg.value = text; msgBad.value = bad }

async function loadAll() {
  try {
    const [e, s, m] = await Promise.all([
      getJSON('/api/vision/engine/status'),
      getJSON('/api/vision/status'),
      getJSON('/api/vision/models'),
    ])
    engine.value = e
    inst.value = e.progress || null
    status.value = { ...s, cloud_list: m.cloud || [], local_list: m.local || [] }
  } catch (e) { flash('加载失败: ' + e.message, true) }
}

async function installEngine() {
  installing.value = true
  try {
    const r = await postJSON('/api/vision/engine/install')
    inst.value = r
    flash(r.started ? '开始下载安装（可离开本页，后台继续）' : '安装已在进行中')
    timer = setInterval(pollEngine, 2000)
  } catch (e) { flash('启动安装失败: ' + e.message, true) }
  finally { installing.value = false }
}

async function pollEngine() {
  try {
    const e = await getJSON('/api/vision/engine/status')
    engine.value = e; inst.value = e.progress || inst.value
    if (!inst.value?.running) { clearInterval(timer); timer = null; if (inst.value?.phase === '完成') flash('✓ 底座引擎安装完成') }
  } catch (_) {}
}

async function pin(id) {
  try {
    const r = await postJSON('/api/vision/models/pin', { model_id: id })
    flash(r.pinned ? `✓ 已选 ${r.pinned}（即时生效）` : '✓ 已清除钉选（跟当前外脑走）')
    await loadAll()
  } catch (e) { flash('选择失败: ' + e.message, true) }
}

async function doImport() {
  if (!imp.value.name || !imp.value.gguf || !imp.value.mmproj) { flash('名字和两个文件路径都要填', true); return }
  importing.value = true
  try {
    const r = await postJSON('/api/vision/import', { ...imp.value })
    flash(r.ready ? `✓ ${r.name} 导入完成（${r.size_gb}GB），已可在上方选择` : `导入完成但文件不全：${r.note || ''}`, !r.ready)
    imp.value = { name: '', gguf: '', mmproj: '' }
    await loadAll()
  } catch (e) { flash('导入失败: ' + e.message, true) }
  finally { importing.value = false }
}

async function removeModel(name) {
  if (!confirm(`确定删除本地视觉模型「${name}」？（删除后需重新导入才能用）`)) return
  try {
    await postJSON('/api/vision/models/remove', { name })
    flash(`✓ 已删除 ${name}`)
    await loadAll()
  } catch (e) { flash('删除失败: ' + e.message, true) }
}

// ══ ★ #1470 人脸识别 ══════════════════════════════════
async function loadFace() {
  try {
    face.value = await getJSON('/api/face/status')
    if (face.value.enabled) members.value = (await getJSON('/api/face/members')).members || []
    if (face.value.download_progress?.running) {
      faceDl.value = face.value.download_progress; faceDlRunning.value = true
    }
  } catch (_) {}
}

async function toggleFace(ev) {
  const on = ev.target.checked
  try {
    const r = await postJSON('/api/face/settings', { enabled: on })
    flash(on ? (r.model_download?.started ? '✓ 已开启，正在后台下载识别模型（37MB）…' : '✓ 已开启人脸识别')
             : '已关闭（本机人脸数据保留，可随时再开）', !on)
    if (r.model_download?.started) { faceDlRunning.value = true; timer = timer || setInterval(pollFaceDl, 2000) }
    await loadFace()
  } catch (e) { flash('操作失败: ' + e.message, true); ev.target.checked = !on }
}

async function downloadFaceModels() {
  try {
    const r = await postJSON('/api/face/settings', { enabled: true })
    if (r.model_download?.started) { faceDlRunning.value = true; timer = timer || setInterval(pollFaceDl, 2000) }
    else flash('模型已就位或已有任务在跑')
  } catch (e) { flash('下载失败: ' + e.message, true) }
}

async function pollFaceDl() {
  try {
    const st = await getJSON('/api/face/status')
    faceDl.value = st.download_progress || faceDl.value
    if (!faceDl.value?.running) {
      faceDlRunning.value = false
      if (timer) { clearInterval(timer); timer = null }
      if (faceDl.value?.phase === '完成') flash('✓ 人脸模型就绪，可以录家人了')
      await loadFace()
    }
  } catch (_) {}
}

async function setThreshold(ev) {
  try {
    await postJSON('/api/face/settings', { threshold: parseFloat(ev.target.value) })
    await loadFace()
  } catch (_) {}
}

async function startEnroll() {
  const name = prompt('家人的名字：')
  if (!name) return
  const relation = prompt('TA 是你的…（如：爸爸/妈妈/儿子/女儿，可留空）', '') || ''
  try {
    const r = await postJSON('/api/face/members', { name, relation })
    enrollMember.value = { id: r.person_id, name }
    pickIds.value = []
    const lib = await getJSON('/api/images/list?limit=120')
    pickImages.value = (lib.images || []).filter(im => (im.path || '').toLowerCase().match(/\.(jpe?g|png|webp|bmp)$/))
    picking.value = true
  } catch (e) { flash('建档失败: ' + e.message, true) }
}

function togglePick(id) {
  const i = pickIds.value.indexOf(id)
  if (i >= 0) pickIds.value.splice(i, 1)
  else if (pickIds.value.length < 8) pickIds.value.push(id)
}

async function doEnroll() {
  if (!enrollMember.value || !pickIds.value.length) return
  enrolling.value = true
  try {
    const r = await postJSON(`/api/face/members/${enrollMember.value.id}/enroll`, { photo_ids: pickIds.value })
    flash(`✓ ${r.name} 录入成功：${r.enrolled} 张脸入库${r.outliers_removed ? `（剔除 ${r.outliers_removed} 张模糊/离群）` : ''}`)
    picking.value = false
    await loadFace()
  } catch (e) { flash('录入失败: ' + e.message, true) }
  finally { enrolling.value = false }
}

async function delMember(m) {
  if (!confirm(`删除成员「${m.name}」？TA 的全部人脸特征将一并删除（不可恢复）。`)) return
  try {
    await delJSON(`/api/face/members/${m.id}`)
    await loadFace()
    flash(`✓ 已删除 ${m.name}`)
  } catch (e) { flash('删除失败: ' + e.message, true) }
}

async function wipeFace() {
  if (!confirm('⚠️ 彻底清除所有人脸数据？所有成员档案+特征全部删除，不可恢复。')) return
  if (!confirm('再次确认：真的要清除全部人脸数据吗？')) return
  try {
    const r = await postJSON('/api/face/wipe', { confirm: 'WIPE' })
    flash(`✓ 已彻底清除（${r.wiped_members} 位成员 / ${r.wiped_embeddings} 条特征）`)
    await loadFace()
  } catch (e) { flash('清除失败: ' + e.message, true) }
}

onMounted(() => { loadAll(); loadFace(); loadCaps(); timer = setInterval(pollEngine, 4000); capTimer = setInterval(loadCaps, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer); if (capTimer) clearInterval(capTimer) })
</script>

<style scoped>
.vcard { background: var(--card-bg, #fff); border: 1px solid var(--line, #e5e7eb); border-radius: 14px; padding: 16px; margin-bottom: 14px; }
.vhead { display: flex; align-items: center; gap: 10px; font-size: 15px; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 999px; }
.badge.ok { background: #e6f7ec; color: #0a7a3d; }
.badge.warn { background: #fff4e5; color: #b26a00; }
.vdesc { color: var(--text-2, #6b7280); font-size: 13px; margin: 8px 0 10px; }
.filegrid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 6px 0 10px; }
.fgroup { border: 1px dashed var(--line, #e5e7eb); border-radius: 10px; padding: 8px 10px; }
.fg-head { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
.frow { display: flex; justify-content: space-between; font-size: 12px; padding: 2px 0; }
.fname { font-family: monospace; }
.mini { font-size: 12px; color: var(--text-2, #6b7280); }
.mini.ok { color: #0a7a3d; }
.mini.warn { color: #b26a00; }
.mini-btn { font-size: 12px; border: 1px solid var(--line, #e5e7eb); background: none; border-radius: 8px; padding: 2px 10px; cursor: pointer; }
.mini-btn.bad { color: #c0392b; }
.vbtns { margin-top: 8px; }
.okline { color: #0a7a3d; font-size: 13px; }
.pull-wrap { margin: 8px 0; }
.pull-bar { height: 8px; background: var(--line, #e5e7eb); border-radius: 999px; overflow: hidden; }
.pull-fill { height: 100%; background: var(--pri, #3d6bff); transition: width .5s; }
.pull-fill.done { background: #0a7a3d; }
.pull-fill.bad { background: #c0392b; }
.pull-text { font-size: 12px; color: var(--text-2, #6b7280); }
.pull-text.bad { color: #c0392b; }
.pin-now { font-size: 13px; background: var(--bg-2, #f6f7f9); border-radius: 8px; padding: 6px 10px; margin-bottom: 10px; }
.sec-title { font-size: 13px; font-weight: 600; margin: 10px 0 6px; }
.mrow { display: flex; align-items: center; justify-content: space-between; padding: 6px 8px; border-radius: 8px; }
.mrow:hover { background: var(--bg-2, #f6f7f9); }
.mradio { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.empty { font-size: 13px; color: var(--text-2, #6b7280); padding: 8px; }
.import-box { margin-top: 10px; border-top: 1px dashed var(--line, #e5e7eb); padding-top: 8px; }
.import-box summary { cursor: pointer; font-size: 13px; }
.imp-row { display: flex; align-items: center; gap: 8px; margin: 8px 0; font-size: 13px; }
.imp-row span { width: 120px; flex: none; }
.imp-row input { flex: 1; border: 1px solid var(--line, #e5e7eb); border-radius: 8px; padding: 6px 10px; }
.tip { background: #e6f7ec; color: #0a7a3d; border-radius: 10px; padding: 8px 12px; font-size: 13px; margin-bottom: 12px; }
.tip.bad { background: #fdecea; color: #c0392b; }
/* ★ #1470 人脸区 */
.face-toggle { margin: 6px 0; }
.face-row { display: flex; align-items: center; gap: 10px; margin: 6px 0; }
.pick-mask { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 99; display: flex; align-items: center; justify-content: center; }
.pick-box { background: var(--card-bg, #fff); border-radius: 14px; padding: 14px; width: min(720px, 94vw); max-height: 86vh; display: flex; flex-direction: column; }
.pick-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.pick-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(90px, 1fr)); gap: 6px; overflow-y: auto; flex: 1; }
.pick-item { position: relative; cursor: pointer; border-radius: 8px; overflow: hidden; border: 2px solid transparent; aspect-ratio: 1; }
.pick-item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.pick-item.sel { border-color: var(--pri, #3d6bff); }
.pick-num { position: absolute; right: 4px; top: 4px; background: var(--pri, #3d6bff); color: #fff; font-size: 11px; border-radius: 999px; padding: 0 6px; }
.pick-foot { margin-top: 10px; display: flex; justify-content: flex-end; }
/* ── 区4 能力卡 ── */
.cap-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
.cap-card { border: 1px solid var(--line, #e5e7eb); border-radius: 12px; padding: 12px; display: flex; flex-direction: column; gap: 6px; }
.cap-card b { font-size: 13.5px; }
.cap-st { font-size: 12px; }
.cap-st.ok { color: #0a7a3d; }
.cap-st.off { color: #b26a00; }
.cap-card .mini-btn { align-self: flex-start; }
</style>
