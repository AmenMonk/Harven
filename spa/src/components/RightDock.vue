<template>
  <aside class="right-dock" v-show="chat.rightDockOpen" :style="{ width: dockW + 'px' }">
    <div class="dock-resize" @pointerdown="resizeStart" title="拖动调整宽度" />
    <div class="dock-head">
      <HIcon name="grid" :size="16" class="dock-ic" />
      <span class="dock-title">面板</span>
      <button class="dock-mini" @click="$router.push('/panels')">管理 →</button>
      <button class="dock-close" title="收起右栏" @click="chat.rightDockOpen = false">
        <HIcon name="x" :size="16" />
      </button>
    </div>

    <!-- ★ 2026-10-25 #1184 入口收纳：随身记弹层撤除，统一进管家中心（spa /butler）；「+」菜单随身记入口改为跳管家中心 -->

    <!-- ★ 2026-09-14 开发者令：恢复工作台（改名「哈文工作台」）——随时看清是谁在回话 -->
    <!-- ★ 2026-09-15 用户反馈：补详细数据（具体模型名/回答方式），不再只写"本地模型" -->
    <div class="workshop">
      <div class="ws-head"><span class="ws-duck"><img :src="duckImg" alt="" /></span><b>哈文工作台</b></div>
      <div class="ws-row">
        <span class="ws-k">现在回答你的是</span>
        <ModeCapsule v-if="chat.sourceNote" :mode="wsMode" :text="chat.sourceNote" />
        <span v-else class="ws-none">{{ wsModeText }}（还没开始对话）</span>
      </div>
      <div class="ws-row">
        <span class="ws-k">具体模型</span>
        <b class="ws-v" :title="chat.sourceDetail?.label || ''">{{ wsModelName }}</b>
      </div>
      <div class="ws-row">
        <span class="ws-k">回答方式</span>
        <b class="ws-v">{{ wsKindText }}</b>
      </div>
      <div class="ws-row"><span class="ws-k">当前模式</span><b class="ws-v">{{ wsModeText }}</b></div>
    </div>

    <!-- ★ 2026-10-24 #1178 管家议程：右栏管家卡（今日议程 + 办完/缓办，与手机管家抽屉同源 /api/butler/*） -->
    <div class="butler">
      <div class="bt-head">
        <span class="bt-ic">🛎️</span><b>管家</b>
        <span v-if="btData && btData.total" class="bt-badge" :class="{ urge: btData.urge_count > 0 }">{{
          btData.urge_count > 0 ? btData.urge_count + ' 件要紧' : btData.total + ' 件事'
        }}</span>
        <button class="dock-mini" style="margin-left:auto" @click="router.push('/butler')">管家中心</button>
        <button class="dock-mini" @click="btRefresh()">刷新</button>
      </div>
      <div v-if="btData && btData.quiet" class="bt-quiet">😴 勿扰时段：不出声提醒，议程照常显示</div>
      <div class="bt-body">
        <div v-for="g in btGroups" :key="g.panel_id" class="bt-group">
          <div class="bt-gname" :class="{ link: g.panel_id !== '_butler' }"
               :title="g.panel_id !== '_butler' ? '打开面板' : ''"
               @click="g.panel_id !== '_butler' && router.push('/panel/' + g.panel_id)">
            {{ g.name }}<span class="bt-gn">{{ g.items.length }}</span>
          </div>
          <div v-for="it in g.items" :key="it.id" class="bt-item" :class="it.level">
            <span class="bt-t">{{ it.title }}</span>
            <span class="bt-ops">
              <button class="bt-btn" title="办完" @click="btAct(it, 'done')">✓</button>
              <button class="bt-btn" title="缓到明天" @click="btAct(it, 'snooze')">缓</button>
            </span>
          </div>
        </div>
        <div v-if="!btData || !btData.total" class="bt-empty">今天议程空空，都办妥了 ✨</div>
      </div>
    </div>

    <!-- 已停靠的面板（在「面板」页点「停靠」钉到这里）——常驻结果区 -->
    <div v-for="pin in pinnedPanels" :key="pin.id" class="dock-panel">
      <div class="dp-head">
        <span class="dp-ic">{{ pin.icon && pin.icon.length <= 3 ? pin.icon : '🧩' }}</span>
        <b class="dp-name">{{ pin.name }}</b>
        <button class="dp-btn" title="整页打开" @click="$router.push('/panel/' + pin.id)"><HIcon name="send" :size="13" /></button>
        <button class="dp-btn" title="取消停靠" @click="unpin(pin.id)"><HIcon name="x" :size="13" /></button>
      </div>
      <!-- ★ tabindex=-1：iframe 加载完成不再抢走聊天输入框的焦点（打字没反应元凶之一） -->
      <iframe :src="'/panel/' + pin.id" class="dp-frame" loading="lazy" tabindex="-1" :title="pin.name" />
    </div>

    <!-- ★ 分类页签（设计稿 v2：家庭/公司/记账/宠物）—— 与面板页共用分类与归类数据 -->
    <div class="dtabs">
      <button v-for="c in tabCats" :key="c.id"
              class="dtab" :class="{ on: activeTab === c.id }"
              @click="activeTab = c.id">{{ c.name }}</button>
    </div>
    <div class="dbody">
      <div v-for="p in tabPanels" :key="p.id" class="dcard">
        <div class="dc-head">
          <span class="dc-ic">{{ p.icon && p.icon.length <= 3 ? p.icon : '🧩' }}</span>
          <b class="dc-name">{{ p.name }}</b>
          <span v-if="pinnedSet.has(p.id)" class="dc-pinned">已停靠</span>
        </div>
        <p class="dc-desc">{{ p.description || '小工具面板' }}</p>
        <div class="dc-btns">
          <button class="dc-btn pri" @click="$router.push('/panel/' + p.id)">打开</button>
          <button v-if="!pinnedSet.has(p.id)" class="dc-btn" @click="pinPanel(p)">停靠</button>
        </div>
      </div>
      <p v-if="!tabPanels.length" class="dempty">
        「{{ activeTabName }}」还没有面板<br />
        <button class="dc-btn pri" style="margin-top:8px" @click="$router.push('/panels')">去面板页看看 →</button>
      </p>
    </div>

    <!-- ★ 2026-09-13 开发者令：龙虾工作台卡已移除（迷惑性强）；协作进度 C 批接入后走会话内进度卡 -->
  </aside>
</template>

<script setup>
// 右栏 = 面板停靠区：已停靠迷你窗 + 分类页签面板卡（龙虾工作台卡 2026-09-13 移除）
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { getJSON } from '../api.js'
import { migrateCatMap } from '../panelCatalog.js' // ★ 2026-09-18 旧 work/data 归类迁移
import HIcon from './HIcon.vue'
import ModeCapsule from './ModeCapsule.vue'
import duckImg from '../assets/duck.png' // ★ 死命令：哈文图标一律官方鸭子图（Vite 打包保证路径正确）

const chat = useChatStore()
const router = useRouter()

/* ★ 2026-10-25 #1184 入口收纳：随身记弹层及其 inbox/remAgg 代码已撤，随身记统一在管家中心页 */

/* ── 哈文工作台：当前回答方 / 模式 ── */
const wsMode = computed(() => {
  const s = chat.sourceNote || ''
  if (/协作|🦞/.test(s)) return 'collab'
  if (/☁️|外脑/.test(s)) return 'cloud'
  return 'local'
})
const wsModeText = computed(() => {
  if (chat.collabOn) return '🦞 协作（外脑出主意 → 本地执行）'
  return chat.queryMode === 'kb_only' ? '🏠 本地模型' : '🤖 外脑'
})
/* ★ 2026-09-15 用户反馈：具体模型 + 回答方式（数据来自 model_source 结构化事件） */
const wsModelName = computed(() => {
  const d = chat.sourceDetail
  if (!d) return '—'
  if (d.model && !['local', 'fastlane', 'plugin', 'collab', 'none', 'agent'].includes(d.model)) return d.model
  // model 是占位 id 时从 label 里抠模型名（如 "🏠 本地 · qwen2.5:14b"）
  const m = (d.label || '').match(/·\s*(.+)$/)
  return m ? m[1].trim() : (d.label || '—')
})
const wsKindText = computed(() => {
  const d = chat.sourceDetail
  if (!d) return '—'
  if (d.mode === 'vision') return '👁️ 看图识别'
  if (d.mode === 'fastlane') return '⚡ 快车道（本地直执行）'
  if (d.mode === 'agent' || d.mode === 'plugin') return '🧩 智能体/插件'
  if (d.mode === 'collab') return '🦞 协作'
  return d.isExternal ? '☁️ 云端外脑' : '🏠 本地大脑'
})

/* ── ★ #1178 管家卡：今日议程（urge 组排前，5 分钟服务端缓存 + 60 秒轮询） ── */
const btData = ref(null)
const btGroups = computed(() => {
  if (!btData.value) return []
  const hasUrge = g => g.items.some(i => i.level === 'urge')
  return [...btData.value.groups].sort((a, b) => (hasUrge(a) ? 0 : 1) - (hasUrge(b) ? 0 : 1))
})
async function btRefresh() {
  try {
    const r = await fetch('/api/butler/state')
    btData.value = await r.json()
  } catch { /* 静默 */ }
}
async function btAct(it, op) {
  try {
    await fetch('/api/butler/action', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: it.id, op }),
    })
    btRefresh()
  } catch { /* 静默 */ }
}

/* ── 拖拽调宽（宽度记忆在本地）── */
const W_KEY = 'harven_spa_dock_w'
const dockW = ref(Math.min(520, Math.max(240, Number(localStorage.getItem(W_KEY)) || 340)))
function resizeStart(e) {
  e.preventDefault()
  const startX = e.clientX, startW = dockW.value
  const move = ev => {
    dockW.value = Math.min(520, Math.max(240, startW - (ev.clientX - startX)))
  }
  const up = () => {
    localStorage.setItem(W_KEY, String(dockW.value))
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

/* ── 停靠面板清单：与「面板」页通过 localStorage + 自定义事件联动 ── */
const PINS_KEY = 'harven_spa_dock_pins'
const allPanels = ref([])
const pinIds = ref(JSON.parse(localStorage.getItem(PINS_KEY) || '[]'))
const pinnedSet = computed(() => new Set(pinIds.value))
/* ★ 2026-09-18 迷你窗顺序跟随全局面板顺序（面板页/空间页拖拽排序的同一个顺序源） */
const pinnedPanels = computed(() => {
  const rank = new Map(allPanels.value.map((p, i) => [p.id, i]))
  return pinIds.value
    .map(id => allPanels.value.find(p => p.id === id))
    .filter(Boolean)
    .sort((a, b) => (rank.get(a.id) ?? 999) - (rank.get(b.id) ?? 999))
})
function syncPins() { pinIds.value = JSON.parse(localStorage.getItem(PINS_KEY) || '[]') }
function unpin(id) {
  pinIds.value = pinIds.value.filter(x => x !== id)
  localStorage.setItem(PINS_KEY, JSON.stringify(pinIds.value))
  window.dispatchEvent(new CustomEvent('harven-dock-pins'))
}
function pinPanel(p) {
  if (!pinIds.value.includes(p.id)) {
    pinIds.value = [...pinIds.value, p.id]
    localStorage.setItem(PINS_KEY, JSON.stringify(pinIds.value))
    window.dispatchEvent(new CustomEvent('harven-dock-pins'))
  }
}

/* ── 模板页签：与「面板中心」页共用同一套称号（★ 2026-09-18 三级统一：面板>模板>模块） ── */
const CATS_KEY = 'harven_spa_panel_cats'
const CATMAP_KEY = 'harven_spa_panel_cat_map'
const DEFAULT_CATS = [
  { id: 'family',  name: '家庭模板' },
  { id: 'company', name: '公司模板' },
  { id: 'create',  name: '创作模板' },
]
const CAT_HINTS = {
  family: ['记账', '居家', '家庭', '健康', '冰箱', '汽车', '学习', 'accounting', 'family'],
  company: ['任务', '待办', '笔记', '备忘', '会议', '项目', '客户', '日报', '数据', '报表', 'todo', 'task', 'note', 'meeting', 'project', 'crm', 'report'],
  create: ['音乐', '图片', '视频', '创作', '画板', 'music', 'art', 'video'],
}
const customCats = ref(JSON.parse(localStorage.getItem(CATS_KEY) || '[]'))
const catMap = ref(migrateCatMap(JSON.parse(localStorage.getItem(CATMAP_KEY) || '{}')))
function syncCats() {
  customCats.value = JSON.parse(localStorage.getItem(CATS_KEY) || '[]')
  catMap.value = migrateCatMap(JSON.parse(localStorage.getItem(CATMAP_KEY) || '{}'))
}
const tabCats = computed(() => [...DEFAULT_CATS, ...customCats.value])
const activeTab = ref('family')
const activeTabName = computed(() =>
  tabCats.value.find(c => c.id === activeTab.value)?.name || '')
function inferCat(p) {
  const hay = `${p.id || ''} ${p.name || ''} ${p.description || ''}`.toLowerCase()
  for (const [cid, kws] of Object.entries(CAT_HINTS)) if (kws.some(k => hay.includes(k))) return cid
  return 'family' // ★ 2026-09-18 猜不出就归家庭模板（原 fallback 是已下线的智能数据）
}
const tabPanels = computed(() => {
  const cid = activeTab.value
  return allPanels.value.filter(p => (catMap.value[p.id] || inferCat(p)) === cid)
})

async function loadPanels() {
  try {
    const j = await getJSON('/api/panels')
    allPanels.value = j.panels || []
  } catch (_) {}
}

let timer = null
let btTimer = null
onMounted(() => {
  loadPanels()
  btRefresh() /* ★ #1178 管家卡首拉 */
  btTimer = setInterval(btRefresh, 60000)
  timer = setInterval(loadPanels, 30000)
  window.addEventListener('harven-dock-pins', () => { syncPins(); syncCats() })
  window.addEventListener('storage', () => { syncPins(); syncCats() })
  /* ★ 2026-10-25 #1184：「+」菜单随身记入口 → 直接跳管家中心（弹层已撤） */
  window.addEventListener('harven-open-inbox', () => router.push('/butler'))
})
onUnmounted(() => {
  clearInterval(timer)
  if (btTimer) clearInterval(btTimer)
})
</script>

<style scoped>
.right-dock {
  flex-shrink: 0; display: flex; flex-direction: column; gap: 12px;
  border-left: 1px solid var(--c-line); background: var(--c-bg);
  padding: 12px; overflow-y: auto;
  position: relative;
}
.dock-resize {
  position: absolute; left: -3px; top: 0; bottom: 0; width: 7px;
  cursor: col-resize; z-index: 5;
}
.dock-resize:hover { background: linear-gradient(90deg, transparent, var(--c-primary-weak)); }
.dock-head { display: flex; align-items: center; gap: 7px; }
.dock-ic { color: var(--c-primary); }
.dock-title { flex: 1; font-weight: 700; font-size: 14px; }
.dock-mini {
  border: none; background: none; cursor: pointer;
  font-size: 12px; color: var(--c-text-2);
}
.dock-mini:hover { color: var(--c-primary); }
.dock-close { border: none; background: none; cursor: pointer; color: var(--c-text-2); }

/* 停靠面板卡（迷你 iframe 常驻） */
.dock-panel {
  border: 1px solid var(--c-line); border-radius: 12px; background: #fff;
  overflow: hidden; display: flex; flex-direction: column; flex-shrink: 0;
}
.dp-head { display: flex; align-items: center; gap: 7px; padding: 8px 10px; }
.dp-ic { font-size: 15px; }
.dp-name { flex: 1; font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dp-btn { border: none; background: none; cursor: pointer; color: var(--c-text-2); padding: 2px; border-radius: 6px; display: flex; }
.dp-btn:hover { color: var(--c-primary); background: var(--c-bg-soft); }
.dp-frame { width: 100%; height: 210px; border: none; border-top: 1px dashed var(--c-line); background: #fff; }

/* ★ 分类页签（设计稿 v2 dtabs） */
.dtabs {
  flex-shrink: 0; display: flex; gap: 4px; overflow-x: auto;
  border-bottom: 1px solid var(--c-line); padding-bottom: 0;
}
.dtab {
  padding: 6px 13px; border-radius: 9px 9px 0 0; white-space: nowrap;
  font-size: 12.5px; color: var(--c-text-2); cursor: pointer;
  border: 1px solid transparent; border-bottom: none; background: none;
}
.dtab.on {
  background: #fff; border-color: var(--c-line);
  color: var(--c-text); font-weight: 600;
}
.dbody {
  flex-shrink: 0; background: #fff; border: 1px solid var(--c-line); border-radius: 0 12px 12px 12px;
  padding: 10px; display: flex; flex-direction: column; gap: 8px;
}
.dcard { border: 1px solid var(--c-line); border-radius: 10px; padding: 9px 11px; }
.dc-head { display: flex; align-items: center; gap: 7px; }
.dc-ic { font-size: 14px; }
.dc-name { flex: 1; font-size: 13px; }
.dc-pinned { font-size: 10.5px; color: var(--c-primary); background: var(--c-primary-weak); border-radius: 999px; padding: 1px 8px; }
.dc-desc { margin: 4px 0 0; font-size: 11.5px; color: var(--c-text-2); line-height: 1.5; }
.dc-btns { display: flex; gap: 6px; margin-top: 7px; }
.dc-btn {
  border: 1px solid var(--c-line); background: #fff; border-radius: 8px;
  font-size: 11.5px; padding: 4px 12px; cursor: pointer; color: var(--c-text);
}
.dc-btn.pri { background: var(--c-primary); border-color: var(--c-primary); color: #fff; }
.dempty { margin: 4px 0; text-align: center; font-size: 12px; color: var(--c-text-2); line-height: 1.6; }

/* ★ 哈文工作台卡（2026-09-14 恢复，原名龙虾工作台） */
.workshop {
  flex-shrink: 0; border: 1px solid var(--c-line); border-radius: 12px; background: #fff;
  padding: 10px 12px; display: flex; flex-direction: column; gap: 7px;
}
.ws-head { display: flex; align-items: center; gap: 7px; font-size: 13px; }
.ws-duck { width: 20px; height: 20px; border-radius: 6px; overflow: hidden; flex-shrink: 0; display: flex; }
.ws-duck img { width: 100%; height: 100%; object-fit: cover; }
.ws-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.ws-k { font-size: 11.5px; color: var(--c-text-2); flex-shrink: 0; }
.ws-v { font-size: 12px; }
.ws-none { font-size: 11.5px; color: var(--c-text-2); }
/* ★ #1178 管家卡样式 */
.butler {
  flex-shrink: 0; border: 1px solid var(--c-line); border-radius: 12px; background: #fff;
  padding: 10px 12px; display: flex; flex-direction: column; gap: 7px;
}
.bt-head { display: flex; align-items: center; gap: 7px; font-size: 13px; }
.bt-ic { font-size: 15px; }
.bt-badge {
  font-size: 10.5px; border-radius: 999px; padding: 1px 8px;
  background: var(--c-bg-soft); color: var(--c-text-2);
}
.bt-badge.urge { background: #fde8e8; color: #c0392b; font-weight: 600; }
.bt-quiet { font-size: 11px; color: var(--c-text-2); background: var(--c-bg-soft); border-radius: 8px; padding: 4px 8px; }
.bt-body { display: flex; flex-direction: column; gap: 7px; max-height: 260px; overflow-y: auto; }
.bt-group { display: flex; flex-direction: column; gap: 3px; }
.bt-gname { font-size: 11.5px; color: var(--c-text-2); display: flex; align-items: center; gap: 5px; }
.bt-gname.link { cursor: pointer; }
.bt-gname.link:hover { color: var(--c-primary); }
.bt-gn {
  font-size: 10px; background: var(--c-bg-soft); border-radius: 999px;
  min-width: 16px; text-align: center; padding: 0 4px;
}
.bt-item {
  display: flex; align-items: center; gap: 6px;
  font-size: 12.5px; line-height: 1.45;
  border-radius: 8px; padding: 4px 7px; background: var(--c-bg-soft);
}
.bt-item.urge { background: #fde8e8; }
.bt-t { flex: 1; min-width: 0; white-space: normal; word-break: break-all; }
.bt-ops { display: flex; gap: 4px; flex-shrink: 0; }
.bt-btn {
  border: 1px solid var(--c-line); background: #fff; border-radius: 7px;
  font-size: 11px; padding: 2px 7px; cursor: pointer; color: var(--c-text);
}
.bt-btn:hover { border-color: var(--c-primary); color: var(--c-primary); }
.bt-empty { font-size: 12px; color: var(--c-text-2); padding: 6px 2px; }

/* ═══ F批手机端：<900px 右栏退场（信息已并入顶栏胶囊与回复方标注，手机屏宽放不下） ═══ */
@media (max-width: 900px) {
  .right-dock { display: none !important; }
}
/* ★ 2026-10-25 #1184：收件箱弹层样式已随弹层一并撤除，随身记统一在管家中心页 */
</style>
