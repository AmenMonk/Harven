<template>
  <PageShell title="🖼️ 图片库" icon="grid" sub="全家照片统一存放 · AI 自动分类归位 · 手动可改 · 全本地">
    <div class="img-layout">
      <!-- 左侧相册栏 -->
      <aside class="img-side">
        <div class="img-side-title">相册</div>
        <div class="img-album" :class="{ active: !album }" @click="selectAlbum('')">
          全部图片 <span class="img-acount">{{ totalAll }}</span>
        </div>
        <div v-for="a in albums" :key="a.name" class="img-album"
             :class="{ active: album === a.name }" @click="selectAlbum(a.name)">
          {{ a.name }} <span class="img-acount">{{ a.count }}</span>
        </div>
        <div class="img-newalbum">
          <input v-model="newAlbum" placeholder="新相册名" @keyup.enter="addAlbum" />
          <button @click="addAlbum">＋</button>
        </div>
        <div class="img-upload-wrap">
          <button class="img-upload-btn" @click="pickUpload">📤 上传图片</button>
          <input ref="fileInput" type="file" accept="image/*" multiple style="display:none"
                 @change="doUpload" />
          <div v-if="uploadHint" class="img-upload-hint">{{ uploadHint }}</div>
        </div>
      </aside>

      <!-- 右侧图墙 -->
      <div class="img-main">
        <div class="img-toolbar">
          <input class="img-search" v-model="kw" :placeholder="semMode ? '用一句话搜：如「老谭在海边」「冰箱里的醋」' : '搜索描述 / 标签 / 相册，如：美团 / 冰箱 / 作业'"
                 @keyup.enter="load" />
          <button class="img-btn" :class="{ on: semMode }" @click="semMode = !semMode; load()" :title="semMode ? '切回标签搜索' : '用一句白话说画面，AI 帮你找'">🧠 语义</button>
          <button class="img-btn" @click="load">搜索</button>
          <button class="img-btn" @click="loadReport">📊 分类汇报</button>
          <button class="img-btn" :class="{ on: selMode }" @click="selMode ? exitSel() : (selMode = true)">☑ 批量</button>
          <button class="img-btn" @click="openTagMgr">🏷 标签</button>
          <button class="img-btn" @click="openFaceEnroll" title="选 TA 的照片录入，哈文就能认出 TA">👤 录家人</button>
          <button class="img-btn" :class="{ on: trashMode }" @click="toggleTrash">🗑 回收站</button>
          <!-- ★ #1407-B 照片组筛选 -->
          <select v-if="imgGroups.length || groupFilter" v-model="groupFilter" class="img-batch-select"
                  @change="load()" title="一物多照组筛选">
            <option value="">全部照片组</option>
            <option value="__none__">未成组</option>
            <option v-for="g in imgGroups" :key="g.group_id" :value="g.group_id">🗂 {{ g.group_id }}（{{ g.count }}）</option>
          </select>
          <span class="img-count">{{ trashMode ? '回收站 · ' : '' }}{{ images.length }} 张</span>
        </div>

        <!-- ★ #1377（2026-11-02）账单相册专属：发给账单模块分析 + 待分析提示 -->
        <div v-if="album === '账单' && !trashMode" class="img-tagfilter">
          <button class="img-btn pri" :disabled="billsBusy" @click="billsAnalyze">💰 发给账单模块分析</button>
          <span v-if="billsPendingCount" class="ir-line">还有 <b>{{ billsPendingCount }}</b> 张没分析过（每天早上心跳也会自动分析）</span>
          <span v-else class="ir-line">都分析过了。新照片会自动进「账单」相册，随时点上面按钮或等心跳。</span>
          <span v-if="billsHint" class="ir-line">{{ billsHint }}</span>
        </div>

        <!-- ★ #1003 标签筛选条（点图上的标签也可触发） -->
        <div v-if="tagFilter && !trashMode" class="img-tagfilter">
          🏷 标签筛选：<b>{{ tagFilter }}</b>
          <button class="img-btn" @click="clearTagFilter">✕ 取消筛选</button>
        </div>

        <!-- ★ #1003 批量操作条 -->
        <div v-if="selMode" class="img-batchbar">
          已选 <b>{{ sel.size }}</b> 张
          <button class="img-btn" @click="selAll">全选</button>
          <button class="img-btn" @click="sel = new Set()">清空</button>
          <template v-if="!trashMode">
            <input v-model="batchTags" class="img-batch-input" placeholder="标签，逗号分隔" @keyup.enter="doBatch('tag')" />
            <button class="img-btn" :disabled="!batchTags.trim()" @click="doBatch('tag')">打标签</button>
            <select v-model="batchAlbum" class="img-batch-select">
              <option value="">选相册…</option>
              <option v-for="a in albums" :key="a.name" :value="a.name">{{ a.name }}</option>
            </select>
            <button class="img-btn" :disabled="!batchAlbum" @click="doBatch('album')">移过去</button>
            <button class="img-btn" :disabled="sel.size < 2" title="选中多张合成一组（一物多照：柜子/抽屉/物品多视角）" @click="doGroup">📁 成组</button>
            <button class="img-btn img-btn-danger" @click="doBatch('delete')">删除（进回收站）</button>
          </template>
          <template v-else>
            <button class="img-btn" @click="doBatch('restore')">恢复</button>
            <button class="img-btn img-btn-danger" @click="doBatch('purge')">彻底删除</button>
          </template>
          <button class="img-btn" @click="exitSel">退出批量</button>
        </div>

        <!-- ★ #930-B6 批量分类汇报 -->
        <div v-if="report" class="img-report">
          <div class="ir-head"><b>📊 图库分类汇报</b><button class="img-btn" @click="report = null">收起</button></div>
          <div class="ir-line">共 <b>{{ report.total }}</b> 张 ·
            <template v-for="(n, a) in report.by_album" :key="a">{{ a }} {{ n }} 张<span v-if="a !== lastAlbumKey"> · </span></template>
          </div>
          <div class="ir-line" v-if="report.untagged_count">⏳ 待打标 {{ report.untagged_count }} 张（后台自动补标中，每 2 分钟一批）</div>
          <div v-if="report.suggest_panel && report.suggest_panel.length" class="ir-line">
            💡 以下 {{ report.suggest_panel.length }} 张看着适合放进面板（点开图后用「存入面板」按钮即可归位）：
            <div class="ir-sugs">
              <span v-for="s in report.suggest_panel" :key="s.id" class="ir-sug">
                <img :src="s.thumb_url" /> → {{ s.suggest_panel }}
              </span>
            </div>
          </div>
        </div>

        <div v-if="loading" class="img-empty">加载中…</div>
        <div v-else-if="!images.length" class="img-empty">
          这个相册还没有图片。<br />
          <span class="img-empty-sub">三大入口自动归档：手机拍照（随口说一句是什么）/ 首页聊天框发图 / 浮窗助手配图；也可以点左下「上传图片」。</span>
        </div>
        <div v-else class="img-grid">
          <div v-for="im in shownImages" :key="im.id" class="img-card" :class="{ sel: sel.has(im.id) }"
               @click="cardClick(im)">
            <div class="img-phwrap">
              <img :src="im.thumb_url" loading="lazy" class="img-thumb"
                   :class="{ show: imgState[im.id] === 'ok', hide: imgState[im.id] === 'err' }"
                   @load="imgLoaded(im.id)" @error="imgError(im.id)" />
              <div v-if="imgState[im.id] !== 'ok'" class="img-skel">
                <template v-if="imgState[im.id] === 'err'">
                  <span>加载失败</span>
                  <button class="img-btn" @click.stop="imgRetry(im)">↻ 重试</button>
                </template>
                <template v-else><span class="img-skel-spin"></span></template>
              </div>
            </div>
            <label v-if="selMode" class="img-check" @click.stop>
              <input type="checkbox" :checked="sel.has(im.id)" @change="toggleSel(im.id)" />
            </label>
            <div class="img-meta">
              <div class="img-note">{{ im.note || '（未写描述）' }}</div>
              <div class="img-sub">
                <span class="img-albumtag">{{ im.album }}</span>
                <span v-if="im.group_id" class="img-tag" title="一物多照组：筛选条可按组看">🗂 {{ im.group_id }}</span>
                <span v-for="t in im.tags.slice(0, 2)" :key="t" class="img-tag img-tag-click"
                      title="点我筛选这个标签的图" @click.stop="filterByTag(t)">{{ t }}</span>
              </div>
              <div v-if="trashMode" class="img-trash-act">
                <button class="img-btn" @click.stop="restoreOne(im)">恢复</button>
                <button class="img-btn img-btn-danger" @click.stop="purgeOne(im)">彻底删</button>
              </div>
            </div>
          </div>
        </div>
        <!-- ★ #1187 分批渲染：滚到底部自动追加 30 张，避免大图墙一次挤兑 -->
        <div v-if="!loading && images.length > shownImages.length" ref="moreSentinel" class="img-more">
          已显示 {{ shownImages.length }} / {{ images.length }} 张，下滑继续加载…
        </div>
      </div>
    </div>

    <!-- 详情弹层 -->
    <div v-if="detail" class="img-overlay" @click.self="detail = null">
      <div class="img-detail">
        <img class="img-big" :src="detail.url" @click="showBig = !showBig" />
        <div v-if="showBig" class="img-fullwrap" @click="showBig = false">
          <img class="img-full" :src="detail.url" />
        </div>

        <!-- ★ #994 修图区（编辑一期：滤镜/旋转/自由裁剪/相框/调亮/替换原图/换一张） -->
        <div class="img-edit-sec">
          <div class="img-edit-head">
            <b>✏️ 修图</b>
            <label class="img-replace-toggle">
              <input type="checkbox" v-model="replaceMode" />
              替换原图（面板记录/相册引用跟着变）
            </label>
          </div>
          <div class="img-edit-row">
            <button v-for="f in FILTERS" :key="f.a" class="img-btn" :disabled="editBusy"
                    @click="doEdit(f.a)">{{ f.n }}</button>
            <button class="img-btn" :disabled="editBusy" @click="doEdit('rotate', { angle: -90 })">↺ 左转</button>
            <button class="img-btn" :disabled="editBusy" @click="doEdit('rotate', { angle: 90 })">↻ 右转</button>
            <button class="img-btn" :class="{ on: cropMode }" :disabled="editBusy" @click="toggleCrop">✂️ 自由裁剪</button>
            <button class="img-btn" :disabled="editBusy" @click="doEdit('frame')">🖼 相框</button>
          </div>
          <div class="img-edit-row">
            <span class="img-edit-lb">亮度</span>
            <input type="range" min="0" max="100" v-model="brightVal" class="img-range" />
            <button class="img-btn" :disabled="editBusy" @click="doEdit('brighten', { strength: brightVal / 100 })">应用</button>
            <button class="img-btn" :disabled="editBusy" @click="pickSwap">🔁 换一张</button>
            <input ref="swapInput" type="file" accept="image/*" style="display:none" @change="doSwap" />
          </div>
          <div v-if="cropMode" class="img-crop-wrap">
            <img ref="cropImg" class="img-crop-img" :src="detail.url" draggable="false"
                 @mousedown.prevent="cropDown" @mousemove="cropMove" @mouseup="cropUp" @mouseleave="cropUp" />
            <div v-if="cropRect" class="img-crop-sel" :style="cropStyle"></div>
            <div class="img-crop-tip">在上面拖一个框，框哪裁哪，然后点「应用裁剪」</div>
            <div class="img-edit-row">
              <button class="img-btn img-btn-primary" :disabled="editBusy || !cropRect" @click="applyCrop">应用裁剪</button>
              <button class="img-btn" @click="cropMode = false; cropRect = null">取消</button>
            </div>
          </div>
          <div v-if="editHint" class="img-edit-hint">{{ editHint }}</div>
        </div>

        <div class="img-drow"><label>描述</label>
          <input v-model="detail.note" placeholder="这是什么的照片（比如：冰箱里的醋 / 孩子的作业）" />
        </div>
        <div class="img-drow"><label>相册</label>
          <select v-model="detail.album">
            <option v-for="a in albums" :key="a.name" :value="a.name">{{ a.name }}</option>
          </select>
        </div>
        <div class="img-drow"><label>标签</label>
          <input v-model="tagsText" placeholder="逗号分隔，如：冰箱,调味品（留空自动打标）" />
        </div>
        <div class="img-drow"><label>存入面板</label>
          <select v-model="panelPick">
            <option value="">（不关联面板）</option>
            <option v-for="p in panels" :key="p.id" :value="p.id">{{ p.icon }} {{ p.name }}</option>
          </select>
        </div>
        <div class="img-dinfo">
          来源：{{ sourceLabel(detail.source) }} · 入库 {{ detail.created_at }}
          <span v-if="detail.panel_id"> · 已关联面板</span>
        </div>
        <div class="img-dactions">
          <button class="img-btn img-btn-primary" @click="saveDetail">保存</button>
          <button class="img-btn" @click="retag">✨ AI 打标签</button>
          <button class="img-btn" :disabled="capBusy" @click="runCap('ocr')">📄 识字</button>
          <button class="img-btn" :disabled="capBusy" @click="runCap('qr')">🔗 扫码</button>
          <button class="img-btn" :disabled="capBusy" @click="runCap('lpr')">🚗 车牌</button>
          <button class="img-btn" :disabled="capBusy" @click="runCap('alive')">🛡 测活</button>
          <button class="img-btn img-btn-danger" @click="removeImg">删除</button>
          <button class="img-btn" @click="detail = null">关闭</button>
        </div>
        <div v-if="capBusy" class="img-cap-result">识别中…</div>
        <div v-else-if="capResult" class="img-cap-result">
          <b>{{ capResult.title }}</b>
          <pre v-if="capResult.text">{{ capResult.text }}</pre>
          <span v-else class="ir-line">{{ capResult.empty }}</span>
        </div>
      </div>
    </div>

    <!-- ★ #1003 标签管理弹层：改名即合并 -->
    <div v-if="tagMgr" class="img-overlay" @click.self="tagMgr = false">
      <div class="img-detail img-tagmgr">
        <div class="ir-head"><b>🏷 标签管理</b><button class="img-btn" @click="tagMgr = false">关闭</button></div>
        <div class="img-tagmgr-tip">改个名就能合并：比如把「猫」改成「猫咪」，两边的图自动归到一起。点标签名直接筛图。</div>
        <div v-if="!tagList.length" class="img-empty-sub">还没有标签。点开图片用「✨ AI 打标签」即可自动生成。</div>
        <div v-for="t in tagList" :key="t.tag" class="img-tagmgr-row">
          <span class="img-tag img-tag-click" @click="tagMgr = false; filterByTag(t.tag)">{{ t.tag }}</span>
          <span class="img-tagmgr-n">{{ t.count }} 张</span>
          <input v-model="t.newName" class="img-batch-input" placeholder="改成…" @keyup.enter="doRename(t)" />
          <button class="img-btn" @click="doRename(t)">改名/合并</button>
        </div>
      </div>
    </div>
    <!-- ★ 2026-11-12 录家人弹层（当场质检版）：勾选即检，不合格当场标红不返工 -->
    <div v-if="feOpen" class="img-overlay" @click.self="feOpen = false">
      <div class="img-detail fe-box">
        <div class="ir-head"><b>👤 录家人</b><button class="img-btn" @click="feOpen = false">关闭</button></div>
        <div class="fe-tips">
          📸 拍摄顺序建议：<b>正面 2 张 → 左右侧脸各 1 → 微笑 1 → 光线暗 1</b>（共 5~8 张）。<br/>
          白天靠窗自然光最好 · 一张照片只有一个人 · 别戴帽子口罩墨镜。<br/>
          勾选后自动质检：<i class="fe-g fe-ok">绿=合格</i> <i class="fe-g fe-warn">黄=能用但一般</i> <i class="fe-g fe-bad">红=换一张</i>
        </div>
        <div class="fe-mrow">
          <label>录入给：</label>
          <select v-model="feMemberId" class="fe-sel">
            <option v-for="m in feMembers" :key="m.id" :value="m.id">{{ m.name }}（已存 {{ m.emb_count }} 张脸）</option>
          </select>
          <button class="img-btn" @click="feNewMember">＋ 新成员</button>
        </div>
        <div v-if="!images.length" class="img-empty-sub">这个相册没照片。先上传几张 TA 的照片再录入。</div>
        <div class="fe-grid">
          <div v-for="im in images" :key="im.id" class="fe-item"
               :class="{ sel: fePicked.includes(im.id) }" @click="feToggle(im.id)">
            <img :src="`/api/images/file/${im.id}?thumb=1`" loading="lazy" />
            <span v-if="fePicked.includes(im.id)" class="fe-num">{{ fePicked.indexOf(im.id) + 1 }}</span>
            <span v-if="feChecks[im.id]" class="fe-badge" :class="'fe-' + feChecks[im.id].grade"
                  :title="feChecks[im.id].reason">{{ feChecks[im.id].grade === 'ok' ? '✓' : feChecks[im.id].grade === 'warn' ? '⚠' : '✗' }}</span>
            <div v-if="feChecks[im.id] && feChecks[im.id].grade !== 'ok'" class="fe-reason">{{ feChecks[im.id].reason }}</div>
          </div>
        </div>
        <div class="fe-foot">
          <span class="fe-sum">已选 {{ fePicked.length }} 张（建议 5~8 张，至少 3 张不同角度）<template v-if="feBadCount"> · <b class="fe-bad">{{ feBadCount }} 张不合格</b>（照样能提交，但建议换成绿的）</template></span>
          <button class="img-btn img-btn-primary" :disabled="!feMemberId || fePicked.length < 1 || feSaving" @click="feSave">{{ feSaving ? '录入中…' : `录入 ${fePicked.length} 张` }}</button>
        </div>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
// ★ #919 2026-09-27 统一图库中心（已批准「通用开发」）：
// 相册栏（默认集+自定义）/ 缩略图墙 / 搜索 / 上传 / 改相册 / 改描述 / 打标签 / 存入面板 / 删除
// 数据源 = images_index 总账（三大入口自动登记 + 知识库/面板附件迁移）
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import PageShell from '../components/PageShell.vue'
import { getJSON, postJSON, delJSON } from '../api'

const albums = ref([])
const images = ref([])
const totalAll = ref(0)
const album = ref('')
const kw = ref('')
const loading = ref(true)
const newAlbum = ref('')
const uploadHint = ref('')
const fileInput = ref(null)
const detail = ref(null)
const tagsText = ref('')
const showBig = ref(false)
const panelPick = ref('')
const panels = ref([])

const sourceLabel = s => ({ mobile: '📱 手机拍照', chat: '💬 聊天发图', panel: '🧩 面板附件',
  knowledge: '📚 知识库', upload: '📤 手动上传' }[s] || s)

// ── ★ 2026-11-12 录家人（当场质检版）：数据源=当前图墙，勾选即检 ──
const feOpen = ref(false)
const feMembers = ref([])
const feMemberId = ref(null)
const fePicked = ref([])
const feChecks = ref({})
const feSaving = ref(false)
const feBadCount = computed(() => Object.values(feChecks.value).filter(c => c.grade === 'bad').length)
let feCheckTimer = null

async function openFaceEnroll() {
  feOpen.value = true
  fePicked.value = []
  feChecks.value = {}
  try {
    const r = await getJSON('/api/face/members')
    feMembers.value = r.members || []
    feMemberId.value = feMembers.value[0]?.id || null
  } catch { feMembers.value = [] }
}

function feToggle(id) {
  const i = fePicked.value.indexOf(id)
  if (i >= 0) fePicked.value.splice(i, 1)
  else {
    if (fePicked.value.length >= 8) { alert('最多勾 8 张（3~5 张不同角度就够认了）'); return }
    fePicked.value.push(id)
  }
  feCheckPicked()
}

function feCheckPicked() {
  clearTimeout(feCheckTimer)
  if (!fePicked.value.length) { feChecks.value = {}; return }
  feCheckTimer = setTimeout(async () => {
    try {
      const r = await postJSON('/api/face/check_photos', { photo_ids: fePicked.value })
      if (r.success) feChecks.value = r.results || {}
    } catch { /* 质检失败静默：不阻塞录入 */ }
  }, 400)
}

async function feNewMember() {
  const name = (prompt('家人的名字？') || '').trim()
  if (!name) return
  try {
    const r = await postJSON('/api/face/members', { name })
    await openFaceEnroll()
    feMemberId.value = r.person_id
  } catch (e) { alert('建档失败：' + (e.message || e)) }
}

async function feSave() {
  if (!feMemberId.value || !fePicked.value.length) return
  feSaving.value = true
  try {
    const r = await postJSON(`/api/face/members/${feMemberId.value}/enroll`, { photo_ids: fePicked.value })
    alert(`✓ ${r.name} 录入成功：${r.enrolled} 张脸入库${r.outliers_removed ? `（自动剔除 ${r.outliers_removed} 张模糊/离群）` : ''}${r.skipped?.length ? `；${r.skipped.length} 张没找到脸被跳过` : ''}`)
    feOpen.value = false
  } catch (e) { alert('录入失败：' + (e.message || e)) }
  finally { feSaving.value = false }
}

async function load() {
  loading.value = true
  renderLimit.value = 60
  imgState.value = {}
  try {
    if (semMode.value && kw.value.trim()) {
      // ★ 2026-11-12 能力②：一句话搜照片（CLIP 语义检索）
      const r = await getJSON(`/api/photos/semsearch?q=${encodeURIComponent(kw.value.trim())}&limit=80`)
      images.value = (r.items || []).map(it => ({ ...it, sem_score: it.score }))
      loading.value = false
      await nextTick(armMoreObserver)
      return
    }
    const q = `/api/images/list?album=${encodeURIComponent(album.value)}&q=${encodeURIComponent(kw.value.trim())}` +
      `&tag=${encodeURIComponent(tagFilter.value || '')}&trash=${trashMode.value ? 1 : 0}` +
      `&group=${encodeURIComponent(groupFilter.value || '')}&limit=500`
    const r = await getJSON(q)
    images.value = r.images || []
    if (!trashMode.value)
      totalAll.value = (await getJSON('/api/images/list?limit=1')).total || images.value.length
  } catch (e) { images.value = [] }
  loading.value = false
  await nextTick(armMoreObserver)
}

/* ── ★ 2026-11-12 能力五件套：语义搜索开关 + 详情弹层识字/扫码/车牌/测活 ── */
const semMode = ref(false)
const capBusy = ref(false)
const capResult = ref(null)
async function runCap(cap) {
  if (!detail.value) return
  capBusy.value = true
  capResult.value = null
  const names = { ocr: '📄 识字结果', qr: '🔗 扫码结果', lpr: '🚗 车牌结果', alive: '🛡 活体检测' }
  try {
    const r = await postJSON(`/api/vision/capability/${cap}`, { photo_id: detail.value.id })
    if (!r.success) { capResult.value = { title: names[cap], empty: r.error || '失败' } ; return }
    const res = r.result || {}
    if (cap === 'ocr') {
      capResult.value = res.ok && res.text
        ? { title: names.ocr, text: res.text }
        : { title: names.ocr, empty: res.error || '这张图里没认出文字' }
    } else if (cap === 'qr') {
      capResult.value = res.ok && res.count
        ? { title: names.qr, text: res.codes.map(c => `[${c.kind}] ${c.data}`).join('\n') }
        : { title: names.qr, empty: res.error || '没认出码' }
    } else if (cap === 'lpr') {
      capResult.value = res.ok && res.count
        ? { title: names.lpr, text: res.plates.map(p => `${p.plate}（可信 ${Math.round(p.score * 100)}%）`).join('\n') }
        : { title: names.lpr, empty: res.error || '没认出车牌' }
    } else if (cap === 'alive') {
      capResult.value = res.ok
        ? { title: names.alive, text: `${res.label}（活体分 ${res.live_score}）` }
        : { title: names.alive, empty: res.error || '检测失败' }
    }
  } catch (e) { capResult.value = { title: names[cap], empty: '失败：' + (e.message || e) } }
  finally { capBusy.value = false }
}

/* ★ #1407-B 照片组（2026-11-03）：组筛选清单 + 批量成组（一物多照：柜子/抽屉/物品多视角） */
const groupFilter = ref('')
const imgGroups = ref([])
async function loadGroups() {
  try {
    const r = await getJSON('/api/images/list?groups=1')
    imgGroups.value = r.groups || []
  } catch (e) { imgGroups.value = [] }
}
async function doGroup() {
  if (sel.value.size < 2) return
  const name = (prompt(`给这 ${sel.value.size} 张照片的组起个名（如：客厅储物柜）：`) || '').trim()
  if (!name) return
  let ok = 0
  for (const id of sel.value) {
    try {
      const r = await postJSON(`/api/images/${id}`, { group_id: name })
      if (r.success) ok++
    } catch (e) { /* 静默 */ }
  }
  exitSel()
  await Promise.all([load(), loadGroups()])
  alert(`📁 已把 ${ok} 张照片成组「${name}」，筛选条可按组看`)
}

async function loadAlbums() {
  try {
    const r = await getJSON('/api/images/albums')
    albums.value = r.albums || []
  } catch (e) { albums.value = [] }
}

/* ★ #930-B6 分类汇报：相册分布 + 待打标 + 建议入面板 */
const report = ref(null)
const lastAlbumKey = computed(() => {
  const ks = Object.keys(report.value?.by_album || {})
  return ks.length ? ks[ks.length - 1] : ''
})
async function loadReport() {
  try { report.value = await getJSON('/api/images/report') }
  catch (e) { report.value = null }
}

async function addAlbum() {
  const name = newAlbum.value.trim()
  if (!name) return
  await postJSON('/api/images/albums', { name })
  newAlbum.value = ''
  await loadAlbums()
}

function pickUpload() { fileInput.value && fileInput.value.click() }

async function doUpload(ev) {
  const files = ev.target.files || []
  if (!files.length) return
  uploadHint.value = `上传中…（${files.length} 张）`
  let ok = 0
  for (const f of files) {
    try {
      const b64 = await new Promise((resolve, reject) => {
        const rd = new FileReader()
        rd.onload = e => resolve(String(e.target.result).split(',')[1] || '')
        rd.onerror = reject
        rd.readAsDataURL(f)
      })
      const body = { image_b64: b64, note: '' }
      if (album.value) body.album = album.value
      const r = await postJSON('/api/images/upload', body)
      if (r.success) ok++
    } catch (e) { /* 单张失败继续 */ }
  }
  uploadHint.value = `已上传 ${ok}/${files.length} 张`
  ev.target.value = ''
  await Promise.all([load(), loadAlbums()])
  setTimeout(() => { uploadHint.value = '' }, 3000)
}

function openDetail(im) {
  detail.value = { ...im }
  tagsText.value = im.tags.join(',')
  panelPick.value = im.panel_id || ''
  showBig.value = false
  /* ★ #994 打开详情重置修图状态 */
  editHint.value = ''
  replaceMode.value = false
  cropMode.value = false
  cropRect.value = null
}

/* ══ ★ #994 图片编辑一期（2026-10-05）：滤镜/旋转/自由裁剪/相框/调亮/替换原图/换一张 ══
   后端 = /api/images/<id>/edit（replace 参数覆盖原文件）+ /replace（换一张）。
   裁剪跟 MemoryPage 的「裁边」不同：在图上拖自由框，框哪裁哪。 */
const FILTERS = [
  { a: 'warm', n: '☀️ 暖色' }, { a: 'cool', n: '❄️ 冷色' }, { a: 'bw', n: '🕶 黑白' },
  { a: 'vivid', n: '🎨 鲜艳' }, { a: 'retro', n: '📻 复古' }, { a: 'contrast', n: '🌗 对比' },
]
const editBusy = ref(false)
const editHint = ref('')
const replaceMode = ref(false)
const brightVal = ref(35)
const cropMode = ref(false)
const cropRect = ref(null)
const cropStart = ref(null)
const cropImg = ref(null)
const swapInput = ref(null)

const cropStyle = computed(() => {
  const c = cropRect.value
  if (!c) return {}
  const x = Math.min(c.x1, c.x2), y = Math.min(c.y1, c.y2)
  return {
    left: x + 'px', top: y + 'px',
    width: Math.abs(c.x2 - c.x1) + 'px', height: Math.abs(c.y2 - c.y1) + 'px',
  }
})

function toggleCrop() {
  cropMode.value = !cropMode.value
  cropRect.value = null
  if (cropMode.value) editHint.value = '在下面大图上拖一个框'
  else editHint.value = ''
}

async function doEdit(action, params) {
  if (!detail.value || editBusy.value) return
  editBusy.value = true
  editHint.value = replaceMode.value ? '改图中…（会直接替换原图）' : '修图中…'
  try {
    const r = await postJSON(`/api/images/${detail.value.id}/edit`,
      { action, params: params || {}, replace: replaceMode.value })
    if (r.success) {
      const ts = Date.now()
      if (replaceMode.value) {
        editHint.value = '已改好并替换原图（面板记录/相册引用同步生效）'
        detail.value = {
          ...detail.value,
          url: `/api/images/file/${detail.value.id}?t=${ts}`,
          thumb_url: `/api/images/file/${detail.value.id}?thumb=1&t=${ts}`,
        }
      } else {
        editHint.value = '修好了（原图还在，修图版是新存的一张）'
        detail.value = { ...detail.value, id: r.id, url: r.url,
          thumb_url: r.url + '?thumb=1', source: 'edited', tags: [] }
      }
      await load()
    } else editHint.value = `没修成：${r.error || '未知原因'}`
  } catch (e) { editHint.value = `没修成：${e.message || e}` }
  editBusy.value = false
}

function cropDown(e) {
  if (!cropMode.value || !cropImg.value) return
  const r = cropImg.value.getBoundingClientRect()
  cropStart.value = { x: e.clientX - r.left, y: e.clientY - r.top }
  cropRect.value = { x1: cropStart.value.x, y1: cropStart.value.y,
                     x2: cropStart.value.x, y2: cropStart.value.y }
}
function cropMove(e) {
  if (!cropMode.value || !cropStart.value || !cropImg.value) return
  const r = cropImg.value.getBoundingClientRect()
  cropRect.value = { ...cropRect.value,
    x2: Math.max(0, Math.min(r.width, e.clientX - r.left)),
    y2: Math.max(0, Math.min(r.height, e.clientY - r.top)) }
}
function cropUp() { cropStart.value = null }

async function applyCrop() {
  const c = cropRect.value
  const img = cropImg.value
  if (!c || !img || editBusy.value) return
  const r = img.getBoundingClientRect()
  const box = [
    Math.min(c.x1, c.x2) / r.width, Math.min(c.y1, c.y2) / r.height,
    Math.max(c.x1, c.x2) / r.width, Math.max(c.y1, c.y2) / r.height,
  ].map(v => Math.max(0, Math.min(1, v)))
  if ((box[2] - box[0]) * (box[3] - box[1]) < 0.01) {
    editHint.value = '框太小了，重新拖一个大点的框'
    return
  }
  await doEdit('crop', { box })
  cropMode.value = false
  cropRect.value = null
}

function pickSwap() { swapInput.value && swapInput.value.click() }

async function doSwap(ev) {
  const f = (ev.target.files || [])[0]
  if (!f || !detail.value || editBusy.value) return
  ev.target.value = ''
  editBusy.value = true
  editHint.value = '换图中…'
  try {
    const b64 = await new Promise((resolve, reject) => {
      const rd = new FileReader()
      rd.onload = e => resolve(String(e.target.result).split(',')[1] || '')
      rd.onerror = reject
      rd.readAsDataURL(f)
    })
    const r = await postJSON(`/api/images/${detail.value.id}/replace`, { image_b64: b64 })
    if (r.success) {
      const ts = Date.now()
      detail.value = {
        ...detail.value,
        url: `/api/images/file/${detail.value.id}?t=${ts}`,
        thumb_url: `/api/images/file/${detail.value.id}?thumb=1&t=${ts}`,
      }
      editHint.value = '换好了（面板记录/相册引用同步生效）'
      await load()
    } else editHint.value = r.error || '没换成'
  } catch (e) { editHint.value = '没换成：' + (e.message || e) }
  editBusy.value = false
}

async function saveDetail() {
  const d = detail.value
  if (!d) return
  const body = { note: d.note, album: d.album }
  if (tagsText.value.trim()) body.tags = tagsText.value
  await postJSON(`/api/images/${d.id}`, body)
  if (panelPick.value) await postJSON(`/api/images/${d.id}`, { panel_id: panelPick.value })
  detail.value = null
  await Promise.all([load(), loadAlbums()])
}

async function retag() {
  const d = detail.value
  if (!d) return
  try {
    // minicpm-v 本地视觉打标签（零 API 费）
    const r = await postJSON(`/api/images/${d.id}/retag`, {})
    if (r.success && r.tags) tagsText.value = r.tags.join(',')
  } catch (e) { /* 静默 */ }
}

async function removeImg() {
  const d = detail.value
  if (!d) return
  if (!confirm('删除后进回收站，可随时恢复；要彻底删请去「🗑 回收站」。确定删吗？')) return
  await delJSON(`/api/images/${d.id}`)
  detail.value = null
  await Promise.all([load(), loadAlbums(), loadTags()])
}

/* ══ ★ #1003 照片库四件套（2026-10-05）：点标签筛选 / 批量操作 / 标签管理 / 回收站 ══ */
const tagFilter = ref('')
const trashMode = ref(false)
const selMode = ref(false)
const sel = ref(new Set())
const tagMgr = ref(false)
const tagList = ref([])
const batchTags = ref('')
const batchAlbum = ref('')

async function loadTags() {
  try { tagList.value = (await getJSON('/api/images/tags')).tags || [] }
  catch (e) { tagList.value = [] }
  tagList.value.forEach(t => { t.newName = '' })
}

function cardClick(im) {
  if (selMode.value) { toggleSel(im.id); return }
  openDetail(im)
}

function toggleSel(id) {
  const s = new Set(sel.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  sel.value = s
}

function selAll() { sel.value = new Set(images.value.map(i => i.id)) }

function exitSel() {
  selMode.value = false
  sel.value = new Set()
  batchTags.value = ''
  batchAlbum.value = ''
}

function filterByTag(t) {
  if (trashMode.value) trashMode.value = false
  tagFilter.value = t
  load()
}

/* ══ ★ #1187 图片库三修（2026-10-25）：相册点击联动 / 缩略图加载态+重试 / 分批渲染 ══
   实锤：相册点击只改 album 变量没重载 → 图墙永远不动；图墙一次全渲染+lazy 排队 → 大片空白占位。 */
function selectAlbum(name) {
  if (album.value === name) { load(); if (name === '账单') billsStatus(); return }   // 点同一个相册也强制刷新
  album.value = name
  load()
  if (name === '账单') billsStatus()
}

/* ── ★ #1377（2026-11-02）账单相册：发给账单模块分析 ── */
const billsBusy = ref(false)
const billsPendingCount = ref(0)
const billsHint = ref('')
async function billsStatus() {
  try {
    const r = await getJSON('/api/images/bills/status')
    billsPendingCount.value = r.pending || 0
  } catch (e) { billsPendingCount.value = 0 }
}
async function billsAnalyze() {
  if (billsBusy.value) return
  billsBusy.value = true
  billsHint.value = ''
  try {
    const r = await postJSON('/api/images/bills/analyze', {})
    billsHint.value = r.message || (r.success ? '已开始分析' : (r.error || '启动失败'))
  } catch (e) { billsHint.value = '启动失败：' + e.message }
  billsBusy.value = false
  setTimeout(billsStatus, 500)
}

const renderLimit = ref(60)
const shownImages = computed(() => images.value.slice(0, renderLimit.value))
const moreSentinel = ref(null)
let _moreObs = null

const imgState = ref({})   // {id: 'ok'|'err'}，未标记 = 加载中（骨架占位）
function imgLoaded(id) { imgState.value = { ...imgState.value, [id]: 'ok' } }
function imgError(id) { imgState.value = { ...imgState.value, [id]: 'err' } }
function imgRetry(im) {
  // 加时间戳破缓存重拉；清状态回到加载中
  const base = im.thumb_url.split('&r=')[0]
  im.thumb_url = base + '&r=' + Date.now()
  const s = { ...imgState.value }; delete s[im.id]
  imgState.value = s
}

function armMoreObserver() {
  if (_moreObs) { _moreObs.disconnect(); _moreObs = null }
  if (!moreSentinel.value) return
  _moreObs = new IntersectionObserver(entries => {
    if (entries.some(e => e.isIntersecting) && renderLimit.value < images.value.length)
      renderLimit.value += 30
  }, { rootMargin: '300px' })
  _moreObs.observe(moreSentinel.value)
}

onUnmounted(() => { if (_moreObs) _moreObs.disconnect() })

function clearTagFilter() { tagFilter.value = ''; load() }

async function toggleTrash() {
  trashMode.value = !trashMode.value
  if (trashMode.value) { tagFilter.value = ''; exitSel() }
  await load()
}

async function doBatch(action) {
  if (!sel.value.size) return
  if (action === 'purge' && !confirm(`彻底删除选中的 ${sel.value.size} 张？删了就找不回来了！`)) return
  const body = { action, ids: [...sel.value] }
  if (action === 'tag') { if (!batchTags.value.trim()) return; body.tags = batchTags.value }
  if (action === 'album') body.album = batchAlbum.value
  try {
    const r = await postJSON('/api/images/batch', body)
    if (r.success && action !== 'tag' && action !== 'album') exitSel()
  } catch (e) { /* 静默 */ }
  await Promise.all([load(), loadAlbums(), loadTags()])
}

async function restoreOne(im) {
  await postJSON(`/api/images/${im.id}/restore`, {})
  await Promise.all([load(), loadAlbums(), loadTags()])
}

async function purgeOne(im) {
  if (!confirm('彻底删除这张？删了就找不回来了！')) return
  await delJSON(`/api/images/${im.id}?hard=1`)
  await Promise.all([load(), loadTags()])
}

async function openTagMgr() { tagMgr.value = true; await loadTags() }

async function doRename(t) {
  const nn = (t.newName || '').trim()
  if (!nn || nn === t.tag) return
  try {
    const r = await postJSON('/api/images/tags', { old: t.tag, new: nn })
    if (r.success) {
      if (tagFilter.value === t.tag) tagFilter.value = nn
      await Promise.all([loadTags(), load()])
    }
  } catch (e) { /* 静默 */ }
}

onMounted(async () => {
  await Promise.all([load(), loadAlbums(), loadGroups(),
    getJSON('/api/panels').then(r => { panels.value = r.panels || [] }).catch(() => {})])
})
</script>

<style scoped>
.img-layout { display: flex; gap: 16px; align-items: flex-start; }
.img-side { width: 170px; flex-shrink: 0; background: var(--c-card); border: 1px solid var(--c-line);
  border-radius: 12px; padding: 12px; position: sticky; top: 12px; }
.img-side-title { font-size: 12px; color: var(--c-text-2); margin-bottom: 8px; }
.img-album { padding: 7px 10px; border-radius: 8px; font-size: 13px; cursor: pointer; color: var(--c-text);
  display: flex; justify-content: space-between; align-items: center; }
.img-album:hover { background: var(--c-bg-soft); }
.img-album.active { background: var(--c-primary-weak); color: var(--c-primary); font-weight: 600; }
.img-acount { font-size: 11px; color: var(--c-text-2); }
.img-newalbum { display: flex; gap: 6px; margin: 8px 0; }
.img-newalbum input { flex: 1; min-width: 0; border: 1px solid var(--c-line); border-radius: 8px;
  padding: 5px 8px; font-size: 12px; background: var(--c-bg); color: var(--c-text); outline: none; }
.img-newalbum button { border: 1px solid var(--c-line); background: var(--c-bg); border-radius: 8px;
  width: 28px; cursor: pointer; color: var(--c-text); }
.img-upload-wrap { margin-top: 6px; }
.img-upload-btn { width: 100%; padding: 8px; border-radius: 10px; border: 1px dashed var(--c-primary);
  background: transparent; color: var(--c-primary); font-size: 13px; cursor: pointer; }
.img-upload-btn:hover { background: var(--c-primary-weak); }
.img-upload-hint { font-size: 11px; color: var(--c-text-2); margin-top: 6px; text-align: center; }
.img-main { flex: 1; min-width: 0; }
.img-toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; }
/* ★ #930-B6 分类汇报卡 */
.img-report { border: 1px solid var(--line); background: var(--c-bg-soft, #f7f8fa); border-radius: 12px; padding: 10px 14px; margin-bottom: 14px; font-size: 12.5px; }
.img-report .ir-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.img-report .ir-line { margin: 4px 0; line-height: 1.7; color: var(--c-text); }
.img-report .ir-sugs { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.img-report .ir-sug { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--line); background: #fff; border-radius: 999px; padding: 2px 10px 2px 3px; font-size: 11.5px; }
.img-report .ir-sug img { width: 26px; height: 26px; border-radius: 50%; object-fit: cover; }
.img-search { flex: 1; min-width: 200px; border: 1px solid var(--c-line); background: var(--c-card);
  color: var(--c-text); border-radius: 10px; padding: 8px 12px; font-size: 13px; outline: none; }
.img-btn { border: 1px solid var(--c-line); background: var(--c-card); color: var(--c-text);
  border-radius: 10px; padding: 7px 14px; font-size: 13px; cursor: pointer; }
.img-btn:hover { border-color: var(--c-primary); color: var(--c-primary); }
.img-btn-primary { background: var(--c-primary); border-color: var(--c-primary); color: #fff; }
.img-btn-danger { color: #E05B5B; }
.img-count { font-size: 12px; color: var(--c-text-2); }
.img-empty { text-align: center; color: var(--c-text-2); padding: 60px 0; font-size: 13px; line-height: 1.9; }
.img-empty-sub { font-size: 12px; }
.img-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.img-card { background: var(--c-card); border: 1px solid var(--c-line); border-radius: 12px;
  overflow: hidden; cursor: pointer; transition: transform .12s, box-shadow .12s; }
.img-card:hover { transform: translateY(-2px); box-shadow: 0 4px 14px rgba(0,0,0,.08); }
.img-card img { width: 100%; height: 120px; object-fit: cover; display: block; background: var(--c-bg-soft); }
/* ★ #1187 缩略图加载态：骨架占位 → 淡入 / 失败可重试 */
.img-phwrap { position: relative; height: 120px; overflow: hidden; background: var(--c-bg-soft); }
.img-phwrap .img-thumb { width: 100%; height: 120px; object-fit: cover; display: block;
  opacity: 0; transition: opacity .25s ease; }
.img-phwrap .img-thumb.show { opacity: 1; }
.img-phwrap .img-thumb.hide { display: none; }
.img-skel { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 8px;
  align-items: center; justify-content: center; color: var(--c-text2, #888); font-size: 12px; }
.img-skel-spin { width: 22px; height: 22px; border-radius: 50%;
  border: 2px solid var(--c-line); border-top-color: var(--c-primary);
  animation: img-spin .8s linear infinite; }
@keyframes img-spin { to { transform: rotate(360deg) } }
.img-more { text-align: center; padding: 14px 0 4px; color: var(--c-text2, #888); font-size: 12px; }
.img-meta { padding: 7px 9px; }
.img-note { font-size: 12px; color: var(--c-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.img-sub { display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap; }
.img-albumtag { font-size: 10.5px; color: var(--c-primary); background: var(--c-primary-weak);
  border-radius: 999px; padding: 1px 7px; }
.img-tag { font-size: 10.5px; color: var(--c-text-2); background: var(--c-bg-soft); border-radius: 999px; padding: 1px 7px; }
.img-overlay { position: fixed; inset: 0; background: rgba(20,28,40,.45); z-index: 999;
  display: flex; align-items: center; justify-content: center; }
.img-detail { background: var(--c-card); border-radius: 16px; padding: 18px; width: 92%;
  max-width: 560px; max-height: 88vh; overflow: auto; }
.img-big { width: 100%; max-height: 300px; object-fit: contain; border-radius: 10px;
  background: var(--c-bg-soft); cursor: zoom-in; }
.img-fullwrap { position: fixed; inset: 0; background: rgba(0,0,0,.85); z-index: 1000;
  display: flex; align-items: center; justify-content: center; cursor: zoom-out; }
.img-full { max-width: 94vw; max-height: 94vh; object-fit: contain; }
.img-drow { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
.img-drow label { width: 62px; font-size: 12px; color: var(--c-text-2); flex-shrink: 0; }
.img-drow input, .img-drow select { flex: 1; min-width: 0; border: 1px solid var(--c-line);
  background: var(--c-bg); color: var(--c-text); border-radius: 8px; padding: 7px 10px;
  font-size: 13px; outline: none; }
.img-dinfo { margin-top: 10px; font-size: 11.5px; color: var(--c-text-2); }
.img-dactions { display: flex; gap: 8px; margin-top: 14px; justify-content: flex-end; flex-wrap: wrap; }
/* ★ 2026-11-12 能力五件套结果条 */
.img-cap-result { margin-top: 10px; background: var(--c-bg-2, #f7f7f7); border-radius: 8px; padding: 8px 12px; font-size: 12.5px; }
.img-cap-result b { display: block; margin-bottom: 4px; }
.img-cap-result pre { white-space: pre-wrap; word-break: break-all; margin: 0; font: inherit; max-height: 180px; overflow-y: auto; }
/* ★ #994 修图区 */
.img-edit-sec { margin-top: 12px; border-top: 1px dashed var(--c-line); padding-top: 10px; }
.img-edit-head { display: flex; justify-content: space-between; align-items: center; font-size: 12.5px; margin-bottom: 8px; }
.img-replace-toggle { font-size: 11.5px; color: var(--c-text-2); display: flex; gap: 4px; align-items: center; cursor: pointer; }
.img-edit-row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin: 6px 0; }
.img-edit-row .img-btn { padding: 5px 10px; font-size: 12px; }
.img-btn.on { border-color: var(--c-primary); color: var(--c-primary); }
.img-edit-lb { font-size: 12px; color: var(--c-text-2); }
.img-range { flex: 0 1 140px; }
.img-edit-hint { font-size: 11.5px; color: var(--c-text-2); margin-top: 6px; }
.img-crop-wrap { margin: 8px 0; user-select: none; }
.img-crop-img { width: 100%; max-height: 300px; object-fit: contain; display: block;
  cursor: crosshair; background: var(--c-bg-soft); }
.img-crop-sel { position: absolute; border: 2px dashed var(--c-primary);
  background: rgba(59, 130, 246, .12); pointer-events: none; }
.img-crop-wrap { position: relative; }
.img-crop-tip { font-size: 11.5px; color: var(--c-text-2); margin: 4px 0; }
/* ★ #1003 四件套 */
.img-tagfilter { display: flex; gap: 10px; align-items: center; background: var(--c-primary-weak);
  color: var(--c-primary); border-radius: 10px; padding: 7px 12px; font-size: 12.5px; margin-bottom: 12px; }
.img-tagfilter .img-btn { padding: 3px 10px; font-size: 12px; }
.img-batchbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; background: var(--c-bg-soft);
  border: 1px dashed var(--c-primary); border-radius: 10px; padding: 8px 12px; font-size: 12.5px; margin-bottom: 12px; }
.img-batchbar .img-btn { padding: 4px 10px; font-size: 12px; }
.img-batch-input { width: 140px; border: 1px solid var(--c-line); background: var(--c-card); color: var(--c-text);
  border-radius: 8px; padding: 4px 8px; font-size: 12px; outline: none; }
.img-batch-select { border: 1px solid var(--c-line); background: var(--c-card); color: var(--c-text);
  border-radius: 8px; padding: 4px 6px; font-size: 12px; outline: none; }
.img-check { position: absolute; top: 6px; left: 6px; background: rgba(255,255,255,.92); border-radius: 6px;
  padding: 3px; cursor: pointer; }
.img-check input { width: 16px; height: 16px; cursor: pointer; }
.img-card { position: relative; }
.img-card.sel { outline: 2px solid var(--c-primary); outline-offset: -2px; }
.img-tag-click { cursor: pointer; }
.img-tag-click:hover { color: var(--c-primary); background: var(--c-primary-weak); }
.img-trash-act { display: flex; gap: 6px; margin-top: 6px; }
.img-trash-act .img-btn { padding: 2px 8px; font-size: 11px; flex: 1; }
.img-tagmgr { max-width: 640px; }
.img-tagmgr-tip { font-size: 12px; color: var(--c-text-2); margin: 6px 0 10px; }
.img-tagmgr-row { display: flex; gap: 8px; align-items: center; padding: 5px 0; border-bottom: 1px dashed var(--c-line); }
.img-tagmgr-row:last-child { border-bottom: none; }
.img-tagmgr-row .img-tag { min-width: 64px; text-align: center; }
.img-tagmgr-n { font-size: 11.5px; color: var(--c-text-2); width: 44px; flex-shrink: 0; }
.img-tagmgr-row .img-batch-input { flex: 1; min-width: 0; }
/* ── ★ 2026-11-12 录家人弹层（当场质检） ── */
.fe-box { width: min(760px, 94vw); max-height: 86vh; overflow-y: auto; }
.fe-tips { font-size: 12.5px; color: var(--c-text-2); line-height: 1.8; background: var(--c-bg-2, #f7f7f7); border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.fe-g { font-style: normal; padding: 1px 6px; border-radius: 6px; font-size: 11.5px; }
.fe-ok { color: #1a7f37; background: rgba(52,199,89,.14); }
.fe-warn { color: #9a6700; background: rgba(255,193,7,.16); }
.fe-bad { color: #c62828; background: rgba(244,67,54,.12); }
.fe-mrow { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.fe-mrow label { font-size: 13px; color: var(--c-text-2); }
.fe-sel { flex: 1; max-width: 260px; }
.fe-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; margin: 8px 0; }
.fe-item { position: relative; cursor: pointer; border-radius: 8px; overflow: hidden; border: 2px solid transparent; aspect-ratio: 1; }
.fe-item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.fe-item.sel { border-color: var(--c-primary, #4a90d9); }
.fe-num { position: absolute; top: 5px; left: 5px; background: var(--c-primary, #4a90d9); color: #fff; border-radius: 50%; width: 20px; height: 20px; font-size: 12px; display: flex; align-items: center; justify-content: center; }
.fe-badge { position: absolute; top: 5px; right: 5px; width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 13px; color: #fff; }
.fe-badge.fe-ok { background: #34c759; }
.fe-badge.fe-warn { background: #f5a623; }
.fe-badge.fe-bad { background: #f44336; }
.fe-reason { position: absolute; left: 0; right: 0; bottom: 0; font-size: 10.5px; color: #fff; background: rgba(0,0,0,.62); padding: 3px 6px; line-height: 1.35; }
.fe-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 8px; }
.fe-sum { font-size: 12.5px; color: var(--c-text-2); }
@media (max-width: 640px) {
  .img-layout { flex-direction: column; }
  .img-side { width: 100%; position: static; }
}
</style>
