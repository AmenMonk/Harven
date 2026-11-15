<template>
  <PageShell :title="catInfo.name" icon="grid" :sub="catInfo.long">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="openMembers"><HIcon name="grid" :size="16" /> 成员</BigButton>
      <BigButton variant="ghost" :px="14" @click="$router.push('/panels')"><HIcon name="box" :size="16" /> 面板中心</BigButton>
      <BigButton variant="ghost" :px="14" @click="loadAll(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
    </template>

    <div v-if="err" class="err-tip">⚠️ {{ err }}</div>
    <div v-if="msg" class="ok-tip">{{ msg }}</div>

    <!-- ═══ 新手指导（分步，可关闭） ═══ -->
    <div v-if="!guideOff" class="guide">
      <div class="g-head">
        <b>👋 第一次用？三步就上手</b>
        <button class="mini" @click="closeGuide">知道了，不再显示</button>
      </div>
      <div class="g-steps">
        <div class="g-step"><span class="g-no">1</span><p>从下面<b>「模块库」</b>挑一个用得上的，点<b>安装</b>（或者直接把它拖进「我的模块」）。比如家里记账，就装「记账账本 · 日常流水」。</p></div>
        <div class="g-step"><span class="g-no">2</span><p>点模块卡上的<b>「打开」</b>，像填表格一样记第一笔数据。</p></div>
        <div class="g-step"><span class="g-no">3</span><p>回到这页看<b>「时间轴」</b>——你记的每一笔都会按日期自动排好，哪天记了啥一翻就知道。</p></div>
      </div>
    </div>

    <!-- ═══ 统计行（★ 2026-09-20 时间筛选移到时间轴标题行，跟时间轴走） ═══ -->
    <div class="stat-row">
      <span class="stat"><b>{{ myPanels.length }}</b> 个模块</span>
      <span class="stat"><b>{{ entries.length }}</b> 条记录</span>
    </div>

    <!-- ═══ ★ 2026-10-20 家人健康（顶级区块，家庭/创作场景；定版拍板：单独提出到上边+指导说明） ═══ -->
    <!-- ★ 2026-10-22 合并归一：「家人健康」唯一界面=家人健康面板。这里只留一行入口，不再重复展示/编辑 -->
    <div v-if="catId !== 'company'" class="fam-entry" @click="$router.push('/panel/health_file')" title="批资料/生日提醒/忌口/健康档案都在这里">
      <b>👨‍👩‍👧 家人健康</b>
      <span class="fe-sum">{{ members.length ? members.map(m => m.name).join(' · ') : '还没添加家人' }}</span>
      <span class="fe-go">管理家人资料与健康档案 →</span>
    </div>

    <!-- ═══ 我的模块（拖放目标区）★ 2026-09-20 用户反馈：板块提前，不再被时间轴顶到下面 ═══ -->
    <h2 class="sec"><HIcon name="box" :size="17" /> 我的模块 <small>点「打开」记录 · 「停靠」钉到右栏 · 装错了点「移出」（只是从这页收起，数据还在，面板中心可恢复） · 按住卡片拖到另一张卡上可换位置 · 拖到组标题上可换分组</small></h2>
    <div
      v-if="!myPanels.length"
      class="empty drop-hint"
      :class="{ over: dropOver }"
      @dragover.prevent="dropOver = true"
      @dragleave="dropOver = false"
      @drop="onDropTpl($event)"
    >
      还没有模块。去下面「模块库」装一个，或者把模块卡<b>直接拖到这里</b>。
    </div>
    <div
      v-else
      class="drop-zone"
      :class="{ over: dropOver }"
      @dragover.prevent="dropOver = true"
      @dragleave="dropOver = false"
      @drop="onDropTpl($event)"
    >
      <!-- ★ #1038（2026-10-06）开发者：空间页也要折叠分组（和面板中心同一套体验）；拖放安装收在外层不变形 -->
      <!-- ★ #1120（2026-10-13）拖拽换组：模块卡拖到目标组（标题/空白处）松手即换组，落卡片上仍是排序 -->
      <div v-for="g in groupedMyPanels" :key="g.name" class="pgroup"
        :class="{ 'group-over': dragOverGroup === g.name }"
        @dragover.prevent="onGroupDragOver(g.name, $event)"
        @dragleave="dragOverGroup === g.name && (dragOverGroup = '')"
        @drop="dropGroupChange(g.name, $event)">
        <!-- ★ 2026-10-20 组标题可拖：拖到另一组标题上换小组顺序 -->
        <div class="pg-head" draggable="true"
          @dragstart="onGroupHeadDragStart(g.name, $event)"
          @dragover.prevent
          @drop.stop="onGroupHeadDrop(g.name)"
          @click="toggleSpaceGroup(g.name)">
          <span class="pg-grip" title="按住拖动可调整小组顺序">⋮⋮</span>
          <span class="pg-arrow">{{ foldedSpaceGroups[g.name] ? '▶' : '▼' }}</span>
          <b>{{ g.name }}</b>
          <span class="pg-n">{{ g.panels.length }} 个模块</span>
        </div>
        <div v-show="!foldedSpaceGroups[g.name]" class="panel-grid">
          <div v-for="p in g.panels" :key="p.id" class="pcard" draggable="true"
            @dragstart="dragMyPanel(p, $event)"
            @dragover.prevent @drop="dropPanelReorder(p, $event)"
            @dragend="dragOverGroup = ''">
            <div class="p-ic">{{ p.icon && p.icon.length <= 3 ? p.icon : '🧩' }}</div>
            <div class="p-info">
              <b>{{ p.name }}</b>
              <span class="p-desc">{{ p.description || '（没有描述）' }}</span>
              <span class="p-meta">{{ (p.fields || []).length }} 个字段 · 已记 {{ countOf(p.id) }} 条</span>
            </div>
            <div class="p-btns">
              <BigButton :px="13" @click="$router.push('/panel/' + p.id)"><HIcon name="send" :size="14" /> 打开</BigButton>
              <button class="mini" :class="{ pinned: isPinned(p.id) }" :title="isPinned(p.id) ? '从右栏取消停靠' : '停靠到右栏，随时可见'" @click="togglePin(p)">
                {{ isPinned(p.id) ? '已停靠' : '停靠' }}
              </button>
              <button class="mini" title="从本页移出（面板和数据都还在，面板中心可恢复）" @click="removeFromSpace(p)">移出</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    <!-- ★ 2026-09-19 卸载权限修复：空间页只有「移出本页」（排除名单，不动数据），全面卸载收归面板中心「⚙ 管理模块」 -->

    <!-- ═══ 时间轴（★ 2026-09-20 后置 + 可折叠 + 限高内滚；时间/成员筛选跟标题行走） ═══ -->
    <div class="sec-row">
      <h2 class="sec"><HIcon name="dots" :size="17" /> 时间轴 <small>这个模板里所有模块记的内容，按日期倒着排</small></h2>
      <div class="tl-controls">
        <div class="tfilter">
          <span class="tf-label">显示：</span>
          <button v-for="f in TFILTERS" :key="f.id" class="tf-chip" :class="{ on: tfilter === f.id }" @click="tfilter = f.id">{{ f.name }}</button>
        </div>
        <div v-if="members.length" class="tfilter">
          <span class="tf-label">只看谁记的：</span>
          <button class="tf-chip" :class="{ on: !mfilter }" @click="mfilter = ''">{{ isCompany ? '全部' : '全家' }}</button>
          <button v-for="m in members" :key="m.id" class="tf-chip mchip" :class="{ on: mfilter === m.name }" @click="mfilter = mfilter === m.name ? '' : m.name">
            <span class="m-dot" :style="{ background: m.color }" />{{ m.name }}
          </button>
        </div>
        <button class="mini tl-toggle" @click="tlOpen = !tlOpen">{{ tlOpen ? '▴ 收起' : '▾ 展开' }}</button>
      </div>
    </div>
    <template v-if="tlOpen">
      <div v-if="!filteredEntries.length" class="empty">
        {{ tfilter === 'all' ? '模块都装好了，但还没有记录。点上面的「打开」记第一笔吧。' : '这个时间段没有记录，换「全部」看看。' }}
      </div>
      <div v-else class="tl tl-scroll">
        <div v-for="day in timelineDays" :key="day.key" class="tl-day">
          <div class="tl-date"><span class="tl-dot" /><b>{{ day.label }}</b><span class="tl-cnt">{{ day.items.length }} 条</span></div>
          <div class="tl-items">
            <div v-for="en in day.items" :key="en.key" class="tl-item" @click="$router.push('/panel/' + en.panel.id)" :title="'点开「' + en.panel.name + '』查看'">
              <span class="tl-ic">{{ en.panel.icon && en.panel.icon.length <= 3 ? en.panel.icon : '🧩' }}</span>
              <div class="tl-main">
                <div class="tl-line">
                  <b class="tl-mod">{{ en.panel.name }}</b>
                  <span v-if="en.record.member" class="tl-member" :style="{ background: memberColor(en.record.member) }">{{ en.record.member }}</span>
                  <span class="tl-sum">{{ en.summary }}</span>
                </div>
                <span class="tl-time">{{ en.timeText }}</span>
              </div>
              <HIcon name="send" :size="14" class="tl-go" />
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- ═══ 模块库（可拖）★ 2026-09-20 (#807) 场景隔离：默认只显示本场景模块，其它场景一键展开 ═══ -->
    <div class="sec-row">
      <h2 class="sec"><HIcon name="grid" :size="17" /> 模块库 <small>点「安装」或<b>按住卡片拖到上面「我的模块」</b>；同一个模块可以装多份</small></h2>
      <div class="tl-controls">
        <button v-if="otherTplCount" class="mini" @click="showAllTpl = !showAllTpl">{{ showAllTpl ? '只看本场景' : '显示其他场景模块 (' + otherTplCount + ')' }}</button>
      </div>
    </div>
    <div class="tpl-grid">
      <div
        v-for="t in scopedTpl" :key="t.base"
        class="tcard" :class="{ fit: t._fit }" draggable="true"
        @dragstart="dragTpl(t, $event)"
      >
        <div class="t-ic">{{ t.icon }}</div>
        <div class="t-info">
          <div class="t-head">
            <b>{{ t.name }}</b>
            <span v-if="t._fit" class="t-fit">推荐</span>
            <span v-if="tplCount(t) > 0" class="t-count">已装 {{ tplCount(t) }} 份</span>
          </div>
          <span class="t-desc">{{ t.desc }}</span>
        </div>
        <BigButton :px="13" :loading="installingTpl === t.base" @click="installTemplate(t)"><HIcon name="plus" :size="14" /> 安装</BigButton>
      </div>
    </div>

    <!-- ═══ ★ D批 成员管理弹窗 ═══ -->
    <div v-if="showMembers" class="modal-mask" @click.self="showMembers = false">
      <div class="modal-box">
        <div class="m-head">
          <b>{{ memberModalTitle }}</b>
          <button class="mini" @click="showMembers = false">关闭</button>
        </div>
        <p class="m-tip">{{ memberModalTip }}</p>
        <div class="m-add">
          <input v-model="newMember" :placeholder="memberPlaceholder" maxlength="20" @keyup.enter="addMember" />
          <select v-if="isCompany" v-model="newMemberRole" class="role-sel" title="角色只是备注标签，一期不做账号权限">
            <option value="员工">员工</option>
            <option value="负责人">负责人</option>
            <option value="管理员">管理员</option>
          </select>
          <BigButton :px="13" @click="addMember"><HIcon name="plus" :size="14" /> 添加</BigButton>
        </div>
        <div class="m-list">
          <!-- ★ 2026-10-20：档案编辑已提升到页顶「家人健康」区块，弹层回归「选记录人」本职 -->
          <div v-for="m in members" :key="m.id" class="m-item">
            <span class="m-dot big" :style="{ background: m.color }" />
            <b>{{ m.name }}</b>
            <span v-if="isCompany" class="m-role" :class="'r-' + m.role">{{ m.role || '员工' }}</span>
            <span v-if="!isCompany && m.relation" class="m-role">{{ m.relation }}</span>
            <span class="m-spacer" />
            <button class="mini danger" @click="delMember(m)">删除</button>
          </div>
          <div v-if="!members.length" class="m-empty">还没有成员，在上面输入名字加一个吧</div>
        </div>
      </div>
    </div>
    <!-- ═══ ★ 2026-09-16 家庭空间·对话即录入：说啥记啥，自动分类进模块 ═══ -->
    <div class="assist-dock">
      <div id="spaceAssistHost"></div>
    </div>
  </PageShell>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageShell from '../components/PageShell.vue'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON, delJSON } from '../api.js'
import { DEFAULT_CATS, TPL, AI_ROLES, inferCat, isUniversal, migrateCatMap } from '../panelCatalog.js'

/* ═══ 分类信息（支持自定义分类，和 PanelsPage 共用 localStorage） ═══ */
const route = useRoute()
const catId = computed(() => route.params.cat || 'family')
const CATS_KEY = 'harven_spa_panel_cats'
const CATMAP_KEY = 'harven_spa_panel_cat_map'
const PINS_KEY = 'harven_spa_dock_pins'
/* ★ 2026-09-19 卸载权限修复（实测实锤：空间页不该有全局卸载权限）
   排除名单：{ '<catId>': ['<panelId>', ...] } —— 只把模块从某个模板空间页收起，
   面板和数据都在，恢复入口在面板中心「被移出的模块」；全面卸载只留在面板中心「⚙ 管理模块」 */
const REMOVED_KEY = 'harven_spa_panel_removed'

const customCats = ref(JSON.parse(localStorage.getItem(CATS_KEY) || '[]'))
const catMap = ref(migrateCatMap(JSON.parse(localStorage.getItem(CATMAP_KEY) || '{}')))
const pins = ref(JSON.parse(localStorage.getItem(PINS_KEY) || '[]'))
const removed = ref(JSON.parse(localStorage.getItem(REMOVED_KEY) || '{}'))

function persistCatMap() { localStorage.setItem(CATMAP_KEY, JSON.stringify(catMap.value)) }
function persistRemoved() { localStorage.setItem(REMOVED_KEY, JSON.stringify(removed.value)) }
/* 清掉某个面板在所有页的移出记录（恢复/重装/全面卸载时用） */
function clearRemovedFor(pid) {
  let hit = false
  for (const cid of Object.keys(removed.value)) {
    const list = removed.value[cid] || []
    if (list.includes(pid)) { removed.value[cid] = list.filter(x => x !== pid); hit = true }
    if (!removed.value[cid].length) delete removed.value[cid]
  }
  if (hit) persistRemoved()
}
function persistPins() {
  localStorage.setItem(PINS_KEY, JSON.stringify(pins.value))
  window.dispatchEvent(new CustomEvent('harven-dock-pins'))
}

const catInfo = computed(() => {
  const def = DEFAULT_CATS.find(c => c.id === catId.value)
  if (def) return def
  const cus = customCats.value.find(c => c.id === catId.value)
  return cus || { id: catId.value, icon: '📁', name: catId.value, desc: '模板', long: '这个模板的空间页：装上模块开始记录，时间轴帮你回看每一天。' }
})

/* ═══ 数据 ═══ */
const panels = ref([])
const counts = ref({}) // panel_id -> 记录条数
const entries = ref([]) // 时间轴条目
const tfilter = ref('all')
const members = ref([]) // ★ D批 家庭成员
const mfilter = ref('') // ★ D批 只看某个成员
const showMembers = ref(false)
const newMember = ref('')
const dropOver = ref(false)
const err = ref('')
const msg = ref('')
const installingTpl = ref('')
/* ★ 2026-09-20 布局改版：时间轴折叠开关（默认展开但限高） */
const tlOpen = ref(true)
let msgTimer = null
let timer = null

const TFILTERS = [
  { id: 'all', name: '全部' },
  { id: 'today', name: '今天' },
  { id: '7d', name: '近 7 天' },
  { id: '30d', name: '近 30 天' },
]

function flash(text) {
  msg.value = text
  clearTimeout(msgTimer)
  msgTimer = setTimeout(() => { msg.value = '' }, 4000)
}

/* 本分类的模块：①universal 通用模块所有分类都显示；②catMap 手动归类优先；
   ③自动归类命中才归入——猜不出分类的按「通用」处理（★ 2026-09-18 定版拍板：不硬归）
   ★ 2026-09-19 加排除名单：被「移出本页」的模块在这里不再显示（数据还在） */
const myPanels = computed(() => {
  const rm = removed.value[catId.value] || []
  return panels.value.filter(p => {
    if (rm.includes(p.id)) return false
    if (isUniversal(p)) return true
    const c = catMap.value[p.id] || inferCat(p)
    return !c || c === catId.value
  })
})

/* ★ #1038（2026-10-06）空间页折叠分组：group 来自 /api/panels（_groups.json），
   与面板中心同一份账同一套体验；未分组归「常用」置顶；折叠状态存 localStorage（按空间页分 key） */
const GROUP_ORDER = ['日程待办', '家庭账本', '食物存放', '健康守护', '还在成长', '老人关怀', '车辆出行', '家务管家', '生活记录']
/* ★ 2026-10-20 模块小组顺序可拖拽（开发者要求）：用户顺序存 _group_order.json，GROUP_ORDER 降级为默认 */
const groupOrder = ref([])
async function loadGroupOrder() {
  try {
    const j = await getJSON('/api/panels/groups/order')
    groupOrder.value = j.order || []
  } catch { groupOrder.value = [] }
}
function sortedGroupNames() {
  const base = groupOrder.value.length ? groupOrder.value.slice() : GROUP_ORDER.slice()
  for (const g of GROUP_ORDER) if (!base.includes(g)) base.push(g)
  return base
}
const dragGroupFrom = ref('')
function onGroupHeadDragStart(name, e) {
  dragGroupFrom.value = name
  try { e.dataTransfer.setData('text/harven-group', name) } catch (_) {}
  e.dataTransfer.effectAllowed = 'move'
}
async function onGroupHeadDrop(toName) {
  const from = dragGroupFrom.value
  dragGroupFrom.value = ''
  if (!from || from === toName) return
  const arr = sortedGroupNames().filter(x => x !== from)
  const idx = arr.indexOf(toName)
  arr.splice(idx < 0 ? arr.length : idx, 0, from)
  groupOrder.value = arr
  try { await postJSON('/api/panels/groups/reorder', { order: arr }) } catch (_) {}
}

const groupedMyPanels = computed(() => {
  const src = myPanels.value
  const groups = []
  const common = src.filter(p => !p.group)
  if (common.length) groups.push({ name: '常用', panels: common })
  for (const g of sortedGroupNames()) {
    const list = src.filter(p => p.group === g)
    if (list.length) groups.push({ name: g, panels: list })
  }
  for (const p of src) {   // 兜底：新组名不在 ORDER 里也能显示
    if (p.group && !sortedGroupNames().includes(p.group) && !groups.some(g => g.name === p.group)) {
      groups.push({ name: p.group, panels: src.filter(x => x.group === p.group) })
    }
  }
  return groups
})
const foldedSpaceGroups = ref(JSON.parse(localStorage.getItem('space_groups_folded_' + (catId.value || '')) || '{}'))
function toggleSpaceGroup(n) {
  foldedSpaceGroups.value[n] = !foldedSpaceGroups.value[n]
  localStorage.setItem('space_groups_folded_' + (catId.value || ''), JSON.stringify(foldedSpaceGroups.value))
}

/* ═══ 加载：面板列表 + 每个面板的记录（汇时间轴 + 数条数） ═══ */
async function loadAll(showErr = false) {
  try {
    const j = await getJSON('/api/panels')
    panels.value = j.panels || []
    getJSON('/api/panels/members').then(mj => { members.value = mj.members || [] }).catch(() => {})
    // 只拉本分类（含通用）面板的记录（最多 300 条/面板，足够时间轴回看）
    const ids = myPanels.value.map(p => p.id)
    const results = await Promise.all(ids.map(id =>
      getJSON(`/api/panels/${id}/data?limit=300`).catch(() => ({ records: [] }))
    ))
    const newCounts = {}
    const newEntries = []
    ids.forEach((id, i) => {
      const panel = panels.value.find(p => p.id === id)
      const recs = results[i].records || []
      newCounts[id] = results[i].total ?? recs.length
      const dateField = (panel?.fields || []).find(f => f.type === 'date')?.name
      for (const r of recs) {
        const raw = (dateField && r[dateField]) || r.created_at || ''
        const ts = parseTs(raw)
        newEntries.push({ key: `${id}:${r.id}`, panel, record: r, ts, raw })
      }
    })
    counts.value = newCounts
    entries.value = newEntries.sort((a, b) => (b.ts?.getTime() || 0) - (a.ts?.getTime() || 0))
    if (showErr) err.value = ''
  } catch (e) {
    if (showErr) err.value = '数据拉取失败：' + e.message
  }
}

function parseTs(raw) {
  if (!raw) return null
  const d = new Date(String(raw).replace(' ', 'T'))
  return isNaN(d.getTime()) ? null : d
}

function countOf(pid) { return counts.value[pid] ?? 0 }

/* ═══ 时间筛选 + 成员筛选 + 按天分组 ═══ */
const filteredEntries = computed(() => {
  let list = entries.value
  if (mfilter.value) list = list.filter(e => e.record.member === mfilter.value)
  if (tfilter.value === 'all') return list
  const now = new Date()
  let cutoff = null
  if (tfilter.value === 'today') { cutoff = new Date(now.getFullYear(), now.getMonth(), now.getDate()) }
  else if (tfilter.value === '7d') cutoff = new Date(now.getTime() - 7 * 864e5)
  else if (tfilter.value === '30d') cutoff = new Date(now.getTime() - 30 * 864e5)
  return list.filter(e => e.ts && e.ts >= cutoff)
})

const timelineDays = computed(() => {
  const days = []
  const idx = {}
  for (const en of filteredEntries.value) {
    if (!en.ts) continue
    const key = fmtDay(en.ts)
    if (!(key in idx)) {
      idx[key] = days.length
      days.push({ key, label: dayLabel(en.ts), items: [] })
    }
    en.timeText = en.ts.toTimeString().slice(0, 5)
    en.summary = summarize(en)
    days[idx[key]].items.push(en)
  }
  // 没解析出日期的记录排最后，归到「更早」
  const noDate = filteredEntries.value.filter(e => !e.ts)
  if (noDate.length) days.push({ key: '_nodate', label: '更早（没写日期）', items: noDate.map(e => { e.summary = summarize(e); e.timeText = ''; return e }) })
  return days
})

function fmtDay(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
function dayLabel(d) {
  const today = new Date(); const yest = new Date(Date.now() - 864e5)
  if (fmtDay(d) === fmtDay(today)) return '今天'
  if (fmtDay(d) === fmtDay(yest)) return '昨天'
  const week = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()]
  return `${d.getMonth() + 1}月${d.getDate()}日 · 周${week}`
}

/* 一条记录的人话摘要：优先取文字字段，金额等数字字段带上 */
function summarize(en) {
  const fs = (en.panel?.fields || []).filter(f => f.type !== 'date')
  const parts = []
  for (const f of fs) {
    const v = en.record[f.name]
    if (v === undefined || v === null || v === '') continue
    if (f.type === 'textarea') { parts.push(String(v).slice(0, 40)); continue }
    if (f.type === 'number') { parts.push(`${f.name} ${v}`); continue }
    if (f.type === 'select') { parts.push(String(v)); continue }
    parts.push(String(v).slice(0, 24))
  }
  return parts.slice(0, 4).join(' · ') || '（空记录）'
}

/* ═══ 模块库：拖拽安装 + 点击安装 ═══ */
const sortedTpl = computed(() => {
  const myGroup = catId.value === 'family' ? 'home' : catId.value // company/create 直对应（★ 2026-09-18 work→company、data 下线）
  return TPL.map(t => ({ ...t, _fit: t.group === myGroup || t.cat === catId.value }))
    .sort((a, b) => (b._fit ? 1 : 0) - (a._fit ? 1 : 0))
})
/* ★ 2026-09-20 (#807) 场景隔离：公司/创作页默认只显示本场景模块，其它场景一键展开（用户反馈：公司页不该看到家庭模块） */
const showAllTpl = ref(false)
const otherTplCount = computed(() => sortedTpl.value.filter(t => !t._fit).length)
const scopedTpl = computed(() => showAllTpl.value ? sortedTpl.value : sortedTpl.value.filter(t => t._fit))
watch(showAllTpl, () => { localStorage.setItem('harven_spa_tpl_scope', showAllTpl.value ? 'all' : 'scene') })

/* ★ 2026-09-22 (#844) 计数口径修正（定版拍板）：
   「已装 N 份」只数本页可见的实例——被移出本页的（removed 名单）、归属别的页的都不算；
   面板中心「⚙ 管理模块」真正卸载（删数据）后这里自然减一，跟用户直觉一致 */
function tplCount(t) {
  const rm = removed.value[catId.value] || []
  return panels.value.filter(p => {
    if (!(p.id === t.base || p.id.startsWith(t.base + '_'))) return false
    if (rm.includes(p.id)) return false // 被移出本页的不算
    if (isUniversal(p)) return true
    const c = catMap.value[p.id] || inferCat(p)
    return !c || c === catId.value
  }).length
}
function nextTplId(base) {
  let n = 1
  while (panels.value.some(p => p.id === `${base}_${n}`)) n++
  return `${base}_${n}`
}

function dragTpl(t, e) { e.dataTransfer.setData('text/harven-tpl', t.base) }

/* ═══ ★ 2026-09-18 我的模块拖拽排序（全局顺序源：后端 _order.json，与面板页/右栏停靠共用） ═══ */
function dragMyPanel(p, e) { e.dataTransfer.setData('text/harven-panel', p.id) }

function dropPanelReorder(p, e) {
  /* 模板拖进来是安装，交给外层 onDropTpl；面板卡拖进来才是排序 */
  if (e.dataTransfer.getData('text/harven-tpl')) return
  const pid = e.dataTransfer.getData('text/harven-panel')
  if (!pid || pid === p.id) return
  e.stopPropagation()
  reorderPanels(pid, p.id)
}

/* ═══ ★ #1120（2026-10-13）拖拽换组：模块卡拖到目标组松手即换分组（组册 _groups.json 唯一账本） ═══ */
const dragOverGroup = ref('')
function onGroupDragOver(name, e) {
  /* 只有模块卡拖动时才高亮组（模板卡拖动是安装语义，不高亮） */
  if (e.dataTransfer.types.includes('text/harven-panel')) dragOverGroup.value = name
}
function dropGroupChange(name, e) {
  if (dragOverGroup.value === name) dragOverGroup.value = ''
  const pid = e.dataTransfer.getData('text/harven-panel')
  if (!pid) return /* 模板卡落到组上 → 交给外层 onDropTpl 走安装 */
  e.stopPropagation()
  changePanelGroup(pid, name === '常用' ? '' : name)
}
async function changePanelGroup(pid, group) {
  const p = panels.value.find(x => x.id === pid)
  if (!p || (p.group || '') === group) return
  const old = p.group || ''
  p.group = group /* 本地立即生效，失败回滚 */
  try {
    const j = await postJSON(`/api/panels/${encodeURIComponent(pid)}/group`, { group })
    if (j.success) flash(group ? `「${p.name}」已移入「${group}」` : `「${p.name}」已移回常用`)
    else { p.group = old; flash(j.message || '换组失败') }
  } catch (_) { p.group = old; flash('换组失败，已还原') }
}

async function reorderPanels(dragId, targetId) {
  const ids = panels.value.map(x => x.id)
  const from = ids.indexOf(dragId), to = ids.indexOf(targetId)
  if (from < 0 || to < 0) return
  ids.splice(to, 0, ids.splice(from, 1)[0])
  /* 本地立即重排（不等待网络），失败静默（下次刷新会回到服务器顺序） */
  const rank = new Map(ids.map((id, i) => [id, i]))
  panels.value = [...panels.value].sort((a, b) => (rank.get(a.id) ?? 999) - (rank.get(b.id) ?? 999))
  try {
    const j = await postJSON('/api/panels/reorder', { order: ids })
    if (j.success) flash('顺序已保存')
  } catch (_) { /* 静默：顺序下次刷新会同步 */ }
}

function onDropTpl(e) {
  dropOver.value = false
  const base = e.dataTransfer.getData('text/harven-tpl')
  if (!base) return
  const t = TPL.find(x => x.base === base)
  if (t) installTemplate(t)
}

async function installTemplate(t) {
  const n = tplCount(t) + 1
  const id = nextTplId(t.base)
  if (!confirm(`安装模块「${t.name}」到「${catInfo.value.name}」？\n字段都帮你配好，${n > 1 ? `这是第 ${n} 份，数据独立不冲突` : '装好就能记数据'}`)) return
  installingTpl.value = t.base
  try {
    const j = await postJSON('/api/panels/create', {
      name: n > 1 ? `${t.name} #${n}` : t.name,
      id, description: t.desc, icon: t.icon,
      fields: t.fields, with_chart: true, with_search: true,
      features: t.features || {}, // ★ 2026-09-19 模板自带能力配置（提醒/统计/AI）随安装带过去
      ai_role: AI_ROLES[t.base] || '', // ★ 2026-09-19 AI 角色卡随安装带过去（P0 全量铺）
      universal: t.universal === true, // ★ 2026-09-22 (#843) 通用模块标记随安装带过去（如物品存放台账）
    })
    if (!j.success) throw new Error(j.message || '创建失败')
    catMap.value[id] = catId.value // ★ 装进当前空间页对应的分类
    clearRemovedFor(id) // ★ 同名 ID 曾被移出过 → 重新装回来要清掉移出记录，否则会被排除名单藏住
    persistCatMap()
    flash(`「${t.name}」装好了，去点「打开」记第一笔吧`)
    await loadAll()
  } catch (e) { err.value = '安装失败：' + e.message }
  finally { installingTpl.value = '' }
}

/* ═══ ★ 2026-09-19 卸载权限修复（实测实锤：空间页不该有全局卸载权限）
   原来「卸载」直接调 DELETE /api/panels/<id> 全局删除——梦境手记在公司页点卸载，
   家庭模板/面板首页也一起没了，权限错误。
   现在：空间页只有「移出本页」= 写进排除名单，面板和数据都在，
   面板中心「被移出的模块」可恢复；全面卸载只留在面板中心「⚙ 管理模块」。 ═══ */
function removeFromSpace(p) {
  const uniTip = isUniversal(p) ? '\n（这是通用模块，在所有页面都会显示；移出只是从这一页收起，其他页不受影响）' : ''
  if (!confirm(`把「${p.name}」从本页移出？\n只是不再显示在这个页面，模块和数据都还在，\n以后在面板中心「被移出的模块」里可以恢复。${uniTip}`)) return
  const rm = removed.value[catId.value] || []
  if (!rm.includes(p.id)) rm.push(p.id)
  removed.value[catId.value] = rm
  persistRemoved()
  pins.value = pins.value.filter(x => x !== p.id); persistPins() // 移出后右栏停靠也收起
  flash(`「${p.name}」已移出本页，面板和数据都还在，面板中心可以恢复`)
}

/* ═══ 停靠 ═══ */
function isPinned(pid) { return pins.value.includes(pid) }
function togglePin(p) {
  pins.value = isPinned(p.id) ? pins.value.filter(x => x !== p.id) : [...pins.value, p.id]
  persistPins()
  flash(isPinned(p.id) ? `「${p.name}」已停靠到右栏` : `「${p.name}」已从右栏收起`)
}

/* ═══ ★ D批 家庭共享：成员管理（★ #807 场景化：公司页=公司成员+角色标签） ═══ */
const MEMBER_COLORS = ['#5B8DEF', '#E5764F', '#3CA96C', '#9A6BE0', '#E0A23C', '#3BB3C3', '#D45B8C', '#6E7B8B']
const isCompany = computed(() => catId.value === 'company')
const memberModalTitle = computed(() => isCompany.value ? '💼 公司成员' : catId.value === 'create' ? '👥 创作成员' : '👨‍👩‍👧 家庭成员')
const memberModalTip = computed(() => isCompany.value
  ? '给每个同事建一个名字，记日报/会议纪要时就能标注记录人，时间轴也能只看某个人记的。角色是备注标签（账号权限体系二期上线），不选默认是员工。'
  : catId.value === 'create'
    ? '给每个创作者建一个名字，记录时标注是谁记的，时间轴也能只看某个人。'
    : '给每个家人建一个名字，打开模块记账/打卡时就能选「记录人」，时间轴也能只看某个人记的。不添加成员也能正常用，只是记录不标注是谁记的。')
const memberPlaceholder = computed(() => isCompany.value ? '同事名字，比如：张三 / 李四 / 王五' : '成员名字，比如：爸爸 / 妈妈 / 大宝')
const newMemberRole = ref('员工')
function memberColor(name) {
  const m = members.value.find(x => x.name === name)
  if (m?.color) return m.color
  const i = Math.max(0, MEMBER_COLORS.indexOf(name)) || 0
  return MEMBER_COLORS[i % MEMBER_COLORS.length]
}
function openMembers() {
  newMember.value = ''
  showMembers.value = true
}
async function addMember() {
  const name = newMember.value.trim()
  if (!name) { flash('先在框里输入成员名字再点「+ 添加」'); return }   // ★ 2026-10-22 修「添加不好用」：空名静默改为明确提示
  const color = MEMBER_COLORS[members.value.length % MEMBER_COLORS.length]
  try {
    const j = await postJSON('/api/panels/members', { name, color, role: newMemberRole.value })
    if (!j.success) throw new Error(j.message || '添加失败')
    members.value = j.members || []
    newMember.value = ''
    flash(isCompany.value
      ? `成员「${name}」（${newMemberRole.value}）加好了，记日报/纪要时就能标注是谁记的`
      : `成员「${name}」加好了，记账/打卡时就能标注是谁记的`)
  } catch (e) { err.value = '添加成员失败：' + e.message }
}
async function delMember(m) {
  if (!confirm(`删除成员「${m.name}」？\n他之前记的记录都还在（名字保留），只是以后不能再选他。`)) return
  try {
    const j = await delJSON(`/api/panels/members/${m.id}`)
    if (!j.success) throw new Error(j.message || '删除失败')
    members.value = j.members || []
    if (mfilter.value === m.name) mfilter.value = ''
    flash(`成员「${m.name}」已删除`)
  } catch (e) { err.value = '删除成员失败：' + e.message }
}

/* ★ 2026-10-22 合并归一：家人资料编辑已统一到「家人健康」面板（HvFamilyBar 六层弹窗），空间页只留入口 */
const $r = useRouter()

/* ═══ 新手指导 ═══ */
const GUIDE_KEY = 'harven_spa_space_guide_off'
const guideOff = ref(localStorage.getItem(GUIDE_KEY) === '1')
function closeGuide() { guideOff.value = true; localStorage.setItem(GUIDE_KEY, '1') }

/* ═══ 生命周期：切分类自动重载 ═══ */
watch(catId, () => { loadAll(); showAllTpl.value = false; nextTick(mountSpaceAssist) })
onMounted(loadGroupOrder)   // ★ 2026-10-20 组顺序

/* ═══ ★ 2026-09-16 家庭空间·对话即录入（跨面板自动分类） ═══ */
let assistInst = null
function mountSpaceAssist() {
  const host = document.getElementById('spaceAssistHost')
  if (!host || !window.PanelAssist) return  // panel-assist.js 未加载时静默（老包兼容）
  host.innerHTML = ''
  assistInst = window.PanelAssist.mount({
    host, panelId: '', mode: 'home',
    /* ★ #807 场景化文案：公司页不再说「家里的事」 */
    title: isCompany.value ? '哈文助手 · 公司空间' : '哈文助手 · 家庭空间',
    hintText: isCompany.value
      ? '公司的事直接说：周一开会定了三件事 / A 项目进入测试 / 张三客户回访完成……哈文自动分类进对应模块，确认一下就入档'
      : '家里的事直接说：买菜花了50 / 数学考了85分 / 打了流感疫苗……哈文自动分类进对应模块，确认一下就入档',
    onSaved: () => loadAll(false),
  })
}

onMounted(() => {
  loadAll(true)
  timer = setInterval(() => loadAll(false), 20000)
  nextTick(mountSpaceAssist)
})
onUnmounted(() => {
  clearInterval(timer)
  // ★ 圆钮挂在 body 上，SPA 路由离开时主动销毁，否则残留在其他页面
  if (assistInst && assistInst.destroy) assistInst.destroy()
  assistInst = null
})
</script>

<style scoped>
/* ★ 2026-09-20 开发者二轮反馈：撤掉 1280 限宽，改满屏（跟首页一致）；仅禁横向溢出 */
.pshell :deep(.pshell-body) { width: 100%; overflow-x: hidden; }
.err-tip { margin: 0 0 12px; padding: 10px 14px; background: #FDF2F2; border: 1px solid #F5C6C6; border-radius: 10px; color: #B4443C; font-size: 13px; }
.ok-tip { margin: 0 0 12px; padding: 10px 14px; background: var(--mode-local-bg); border-radius: 10px; color: var(--mode-local); font-size: 13px; }

/* 新手指导 */
.guide { border: 1px dashed var(--blue); background: #F5F9FF; border-radius: 13px; padding: 13px 16px; margin-bottom: 14px; }
.g-head { display: flex; justify-content: space-between; align-items: center; }
.g-head b { font-size: 13.5px; color: var(--blue); }
.g-steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 10px; }
.g-step { display: flex; gap: 8px; align-items: flex-start; }
.g-step p { margin: 0; font-size: 12px; line-height: 1.65; color: var(--c-text); }
.g-no { width: 20px; height: 20px; border-radius: 50%; background: var(--blue); color: #fff; font-size: 11.5px; font-weight: 800; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }

/* 统计 + 时间筛选 */
.stat-row { display: flex; align-items: center; gap: 14px; margin: 4px 0 2px; }
.stat { font-size: 12.5px; color: var(--sub); }
.stat b { color: var(--c-text); font-size: 15px; margin-right: 2px; }
.stat-spacer { flex: 1; }
.tfilter { display: flex; align-items: center; gap: 6px; }
.tf-label { font-size: 12px; color: var(--sub); }
.tf-chip { border: 1px solid var(--line); background: #fff; border-radius: 20px; font-size: 12px; padding: 3px 12px; cursor: pointer; color: var(--c-text); }
.tf-chip.on { background: var(--blue); border-color: var(--blue); color: #fff; font-weight: 700; }

.sec { display: flex; align-items: baseline; gap: 8px; font-size: 15px; margin: 16px 0 10px; }
.sec small { font-size: 12px; color: var(--sub); font-weight: 400; }
.sec-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.sec-row .sec { margin: 16px 0 10px; }
.tl-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.tl-toggle { align-self: center; }
.empty { padding: 26px; text-align: center; color: var(--sub); font-size: 13px; background: var(--c-bg-soft); border-radius: 12px; }
.empty b { color: var(--blue); }

/* 时间轴（★ 2026-09-20 限高内滚：再多的记录也只占一小块版面） */
.tl { display: flex; flex-direction: column; gap: 0; }
.tl-scroll { max-height: 430px; overflow-y: auto; padding-right: 4px; }
.tl-day { display: grid; grid-template-columns: 150px 1fr; gap: 14px; position: relative; }
.tl-date { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 10px 0; position: sticky; top: 0; }
.tl-date b { color: var(--c-text); }
.tl-cnt { font-size: 11px; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; padding: 1px 8px; }
.tl-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--blue); flex-shrink: 0; }
.tl-day::before { content: ''; position: absolute; left: 4px; top: 34px; bottom: -6px; width: 2px; background: var(--line); }
.tl-day:last-child::before { bottom: 10px; }
.tl-items { display: flex; flex-direction: column; gap: 7px; padding: 6px 0 14px; min-width: 0; }
.tl-item { display: flex; align-items: center; gap: 10px; border: 1px solid var(--line); background: #fff; border-radius: 11px; padding: 9px 13px; cursor: pointer; transition: border-color .15s, transform .15s; }
.tl-item:hover { border-color: var(--blue); transform: translateX(3px); }
.tl-ic { font-size: 18px; flex-shrink: 0; }
.tl-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.tl-line { display: flex; gap: 8px; align-items: baseline; min-width: 0; }
.tl-mod { font-size: 12.5px; color: var(--blue); flex-shrink: 0; }
.tl-sum { font-size: 12.5px; color: var(--c-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-time { font-size: 11px; color: var(--sub); }
.tl-go { color: var(--sub); flex-shrink: 0; }

/* 我的模块（★ 2026-09-20 自适应列数） */
.panel-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 10px; }
/* ★ #1038（2026-10-06）折叠分组（与面板中心同款） */
.pgroup { margin-bottom: 14px; }
.pg-head { display: flex; align-items: center; gap: 8px; padding: 8px 10px; margin-bottom: 8px; border-radius: 10px; background: var(--panel, #F4F6FA); cursor: pointer; user-select: none; }
.pg-head:hover { background: var(--panel-2, #EBEFF5); }
.pg-head b { font-size: 14px; color: var(--c-text, #2B2F36); }
.pg-arrow { font-size: 11px; color: var(--c-text-2, #8A93A2); width: 14px; }
.pg-n { font-size: 12px; color: var(--c-text-2, #8A93A2); }
.drop-zone { border-radius: 13px; transition: outline .15s; }
.drop-zone.over, .empty.drop-hint.over { outline: 2px dashed var(--blue); outline-offset: 4px; background: #F5F9FF; }
/* ★ #1120（2026-10-13）拖拽换组高亮：目标组虚线框 + 标题染色 */
.pgroup.group-over { outline: 2px dashed var(--blue); outline-offset: 3px; border-radius: 10px; }
.pgroup.group-over .pg-head { background: #F5F9FF; }
.pcard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 14px; display: flex; gap: 12px; align-items: flex-start; }
.p-ic { width: 44px; height: 44px; border-radius: 11px; background: var(--c-bg-soft); display: flex; align-items: center; justify-content: center; font-size: 22px; flex-shrink: 0; }
.p-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.p-info b { font-size: 14px; }
.p-desc { font-size: 12px; color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.p-meta { font-size: 11px; color: var(--sub); }
.p-btns { display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; align-items: stretch; }
.p-btns .mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 11px; cursor: pointer; color: var(--c-text); }
.p-btns .mini.pinned { border-color: var(--blue); color: var(--blue); font-weight: 700; background: #F3F7FE; }
.p-btns .mini.danger:hover { border-color: var(--red); color: var(--red); }

/* 模块库（★ 2026-09-20 自适应列数） */
.tpl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 9px; }
.tcard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 12px 14px; display: flex; gap: 11px; align-items: center; cursor: grab; transition: border-color .15s; }
.tcard:hover { border-color: var(--blue); }
.tcard:active { cursor: grabbing; }
.tcard.fit { border-color: #BBD4F7; background: #FAFCFF; }
.t-ic { width: 40px; height: 40px; border-radius: 10px; background: var(--c-bg-soft); display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; }
.t-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.t-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.t-head b { font-size: 13.5px; }
.t-fit { font-size: 10px; font-weight: 800; border-radius: 6px; padding: 1px 7px; background: var(--mode-local-bg); color: var(--mode-local); }
.t-count { font-size: 11px; color: var(--green); font-weight: 700; }
.t-desc { font-size: 11.5px; color: var(--sub); }

.mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 11px; cursor: pointer; color: var(--ink); flex-shrink: 0; }
.mini.danger { color: var(--red); }

/* ★ D批 家庭共享 */
.mchip { display: inline-flex; align-items: center; gap: 5px; }
.m-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.m-dot.big { width: 12px; height: 12px; }
.tl-member { font-size: 10.5px; color: #fff; border-radius: 9px; padding: 1px 8px; flex-shrink: 0; font-weight: 600; }
.modal-mask { position: fixed; inset: 0; background: rgba(20, 24, 34, .42); z-index: 90; display: flex; align-items: center; justify-content: center; }
.modal-box { width: 420px; max-width: calc(100vw - 40px); background: #fff; border-radius: 16px; padding: 18px 20px; box-shadow: 0 18px 50px rgba(0,0,0,.18); }
.m-head { display: flex; justify-content: space-between; align-items: center; }
.m-head b { font-size: 15px; }
.m-tip { font-size: 12px; color: var(--sub); line-height: 1.7; margin: 8px 0 12px; }
.m-add { display: flex; gap: 8px; }
/* ★ 2026-09-20 (#807) 公司成员角色 */
.m-add input { flex: 1; }
.role-sel { border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px; font-size: 13px; background: #fff; color: var(--ink); flex-shrink: 0; }
.m-role { font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 6px; background: var(--c-bg-soft); color: var(--sub); flex-shrink: 0; }
.m-role.r-管理员 { background: #FDF0E6; color: #C26A1B; }
.m-role.r-负责人 { background: var(--mode-vertical-bg); color: var(--mode-vertical); }
.m-role.r-员工 { background: #EDF3EC; color: #3C7A4E; }
.m-add input { flex: 1; border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px; font-size: 13px; }
.m-add input:focus { outline: none; border-color: var(--blue); }
.m-list { margin-top: 12px; display: flex; flex-direction: column; gap: 7px; max-height: 240px; overflow-y: auto; }
.m-item { display: flex; align-items: center; gap: 9px; border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px; }
.m-item b { font-size: 13px; }
.m-spacer { flex: 1; }
.m-empty { font-size: 12px; color: var(--sub); text-align: center; padding: 14px 0; }

@media (max-width: 1000px) { .panel-grid, .tpl-grid { grid-template-columns: 1fr 1fr; } .g-steps { grid-template-columns: 1fr; } .tl-day { grid-template-columns: 100px 1fr; } }
@media (max-width: 700px) { .panel-grid, .tpl-grid { grid-template-columns: 1fr; } }

/* ── ★ #810 手机端：成员弹窗好点、时间轴更松 ── */
@media (max-width: 768px) {
  .m-add { flex-wrap: wrap; }
  .m-add input { min-width: 100%; min-height: 42px; font-size: 15px; }
  .role-sel { min-height: 42px; font-size: 15px; }
  .m-item { padding: 11px 12px; }
  .m-item b { font-size: 14.5px; }
  .m-list { max-height: 50vh; }
  /* ★ D3 补 2026-09-23 用户反馈：家庭模板页模块卡窄屏两段式——「打开/停靠/移出」按钮独占一行常驻可见（此前被挤出屏外） */
  .pcard { flex-wrap: wrap; }
  .p-desc, .p-meta { white-space: normal; word-break: break-all; }
  .p-btns { width: 100%; flex-direction: row; margin-top: 8px; padding-top: 9px; border-top: 1px dashed var(--line); }
  .p-btns > * { flex: 1; }
  .p-btns .mini { padding: 7px 11px; }
  /* ★ #847 区块标题两行化：标题横排一行，说明文字换行铺满，不再竖排 */
  .sec { flex-wrap: wrap; row-gap: 2px; }
  .sec small { flex: 1 1 100%; white-space: normal; line-height: 1.5; }
  .stat-row { flex-wrap: wrap; gap: 8px; }
}

/* ★ 2026-09-16 家庭空间·对话即录入 dock */
.assist-dock {
  position: sticky; bottom: 12px; z-index: 20;
  margin-top: 18px;
}
.assist-dock #spaceAssistHost :deep(.pa-wrap) {
  border-radius: 14px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, .12);
}
/* ★ 2026-10-20 家人健康编辑 */
.fam-hub { border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; margin-bottom: 16px; background: var(--c-bg-soft); }
.fh-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.fh-head b { font-size: 15px; }
.fh-tip { font-size: 11.5px; color: var(--sub); }
.fh-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; }
.fh-card { position: relative; border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; background: #fff; cursor: pointer; }
.fh-card:hover { border-color: var(--blue); }
.fh-top { display: flex; align-items: center; gap: 7px; }
.fh-rel { font-size: 11px; color: var(--blue); background: #F3F7FE; border-radius: 9px; padding: 1px 8px; }
.fh-meta { display: flex; flex-direction: column; gap: 2px; font-size: 11.5px; color: var(--sub); margin-top: 6px; }
.fh-warn { color: #B4443C; }
.fh-note { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fh-ops { margin-top: 6px; text-align: right; }
.fh-edit { font-size: 11.5px; color: var(--blue); }
.fh-add { border: 1px solid var(--blue); border-radius: 12px; background: var(--blue); color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; min-height: 80px; }
.fh-add:hover { filter: brightness(1.08); }
.fam-modal { width: min(560px, 94vw); max-height: 86vh; overflow: auto; }
.fam-modal .fam-profile { margin-top: 12px; }
.fh-ops { display: flex; gap: 10px; justify-content: flex-end; flex-wrap: wrap; }
.fam-profile { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; padding: 10px; margin-top: 8px; background: var(--c-bg-soft); border-radius: 10px; cursor: default; }
.fam-profile label { display: flex; flex-direction: column; gap: 3px; font-size: 11.5px; color: var(--sub); position: relative; }
.fam-profile input { border: 1px solid var(--line); border-radius: 7px; padding: 5px 8px; font-size: 12.5px; }
.fp-extra { grid-column: 1 / -1; display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; }
.fp-ex-del { position: absolute; right: 4px; top: 0; color: #B4443C; cursor: pointer; font-size: 10px; }
.fp-extra-add { grid-column: 1 / -1; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.fp-extra-add input { flex: 1; min-width: 90px; }
.fp-ex-n { font-size: 11px; color: var(--sub); }
.fp-ops { grid-column: 1 / -1; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.fp-msg { font-size: 12px; color: var(--green); }
.pg-grip { font-size: 11px; color: var(--sub); cursor: grab; letter-spacing: -1px; }
</style>
