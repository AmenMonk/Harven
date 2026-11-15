<template>
  <aside class="left-rail">
    <!-- ★ 品牌区（设计稿 v2：鸭子 40px + 哈文 + 版本小字） -->
    <div class="brand">
      <div class="brand-duck"><DuckLogo :px="40" /></div>
      <div class="brand-text">
        <b>{{ instName || t('rail.brand') }}</b>
        <span>{{ t('rail.localRunning') }}</span>
      </div>
    </div>

    <!-- ★ 保险1：＋新对话第一按钮 -->
    <BigButton block icon="plus" class="new-chat-btn" @click="chat.newConversation()">{{ t('rail.newChat') }}</BigButton>

    <!-- ★ 搜索会话框（设计稿 v2） -->
    <div class="search-box">
      <HIcon name="search" :size="14" />
      <input v-model="kw" :placeholder="t('rail.searchPlaceholder')" />
    </div>

    <!-- 会话列表：带模式/面板标签，随搜索过滤 -->
    <div class="side-label side-label-row">
      <span>{{ t('rail.recent') }}</span>
      <button v-if="(chat.conversations || []).length" class="conv-clear"
              :title="t('rail.clearRecent')" @click="onClearAll">{{ t('rail.clearRecent') }}</button>
    </div>
    <div class="conv-list">
      <div v-for="c in filteredConvs" :key="c.id"
           class="conv-item" :class="{ active: c.id === chat.currentId }"
           @click="chat.openConversation(c.id)">
        <div class="conv-line1">
          <span class="conv-title">{{ c.title }}</span>
          <button class="conv-del" :title="t('rail.delConv')" @click.stop="chat.deleteConversation(c.id)">
            <HIcon name="x" :size="13" />
          </button>
        </div>
        <div class="conv-line2">
          <ModeCapsule v-if="chat.convTags(c.id).mode" :mode="chat.convTags(c.id).mode" />
          <span v-if="c.spec_name" class="conv-panel-tag">{{ c.spec_name }}</span>
          <span class="conv-count">{{ t('rail.msgCount', { n: c.msg_count }) }}</span>
        </div>
      </div>
      <p v-if="!filteredConvs.length" class="conv-empty">{{ kw ? t('rail.searchEmpty') : t('rail.listEmpty') }}</p>
      <button class="more-link" @click="$router.push('/timeline')">{{ t('rail.viewAll') }}</button>
    </div>

    <!-- 左栏 8 工具格（设计稿 v2 定稿 + #923 图片库第 8 格），每行 2 个可拖拽排序 -->
    <div class="tool-grid">
      <router-link v-for="item in grid" :key="item.key"
                   :to="item.to" class="tool-cell"
                   draggable="true"
                   @click="ui.closeRail()"
                   @dragstart="onDragStart(item.key)"
                   @dragover.prevent
                   @drop.prevent="onDrop(item.key)">
        <HIcon :name="item.icon" :size="20" />
        <span>{{ t(item.labelKey) }}</span>
      </router-link>
    </div>
    <p class="grid-hint">{{ t('rail.dragHint') }}</p>

    <!-- ★ 底部脚注（设计稿 v2）：监控 / 设置 -->
    <div class="side-foot">
      <router-link to="/status" class="foot-link"><HIcon name="pulse" :size="14" /> {{ t('rail.monitor') }}</router-link>
      <router-link to="/settings" class="foot-link"><HIcon name="gear" :size="14" /> {{ t('rail.settings') }}</router-link>
    </div>
  </aside>
</template>

<script setup>
// 左栏 = 品牌区 + 新对话 + 搜索 + 会话列表 + 7 工具格 + 监控/设置脚注（设计稿 v2 定稿）
import { ref, computed, onMounted } from 'vue'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import BigButton from '../components/BigButton.vue'
import ModeCapsule from '../components/ModeCapsule.vue'
import HIcon from '../components/HIcon.vue'
import DuckLogo from './DuckLogo.vue'
import { t } from '../i18n'
import { getJSON } from '../api.js'

const chat = useChatStore()

/* ★ 2026-09-22 (#851): 实例名（哈文开发者/哈文1），拉不到就用默认「哈文」 */
const instName = ref('')
onMounted(async () => {
  try { const si = await getJSON('/api/system/info'); instName.value = (si.instance_name || '').trim() } catch (_) {}
})

/* ── 搜索会话 ── */
const kw = ref('')
const filteredConvs = computed(() => {
  const list = chat.conversations || []
  const k = kw.value.trim().toLowerCase()
  if (!k) return list
  return list.filter(c => (c.title || '').toLowerCase().includes(k))
})

/* ★ 2026-10-11 #1095：一键清空全部会话（confirm 惯例与项目其余 6 处一致） */
function onClearAll() {
  const n = (chat.conversations || []).length
  if (!n) return
  if (!confirm(t('rail.clearRecentConfirm', { n }))) return
  chat.clearAllConversations()
}

/* ── 左栏 8 格（★ 2026-10-25 #1184 入口收纳：任务格→管家格；知识库/技能/管家/邮箱/Hub/更多/面板/图片库） ── */
const DEFAULT_ORDER = ['knowledge', 'skills', 'butler', 'mail', 'hub', 'more', 'panels', 'photos']
const ITEM_MAP = {
  knowledge: { labelKey: 'rail.knowledge', icon: 'book', to: '/knowledge' },
  skills:    { labelKey: 'rail.skills',   icon: 'puzzle', to: '/skills' },
  butler:    { labelKey: 'rail.butler',   icon: 'task', to: '/butler' },  // ★ #1184 任务入口→管家中心
  mail:      { labelKey: 'rail.mail',     icon: 'mail', to: '/mail' },
  hub:       { labelKey: 'rail.hub',      icon: 'globe', to: '/hub' },
  more:      { labelKey: 'rail.more',     icon: 'palette', to: '/more' },
  panels:    { labelKey: 'rail.panels',   icon: 'grid', to: '/panels' },
  photos:    { labelKey: 'rail.photos',   icon: 'image', to: '/photos' },  // ★ #923 T2：图片库（总存放地）进侧栏，7→8 格
}
/* 兼容迁移：老版本本地排序是 8 格（status/settings 挪去脚注）或缺格 → 过滤 + 按旧顺序保留 + 缺的补尾 */
const _saved = JSON.parse(localStorage.getItem('harven_spa_left_grid') || 'null')
let _order = DEFAULT_ORDER
if (Array.isArray(_saved)) {
  const merged = [..._saved.filter(k => ITEM_MAP[k]), ...DEFAULT_ORDER]
    .filter((k, i, a) => a.indexOf(k) === i)
    .filter(k => k !== 'status' && k !== 'settings')
  _order = merged.length === DEFAULT_ORDER.length ? merged : DEFAULT_ORDER
}
const order = ref(_order)
const grid = ref(order.value.map(k => ({ key: k, ...ITEM_MAP[k] })))

let dragKey = ''
function onDragStart(key) { dragKey = key }
function onDrop(targetKey) {
  if (!dragKey || dragKey === targetKey) return
  const list = grid.value.map(g => g.key)
  const from = list.indexOf(dragKey)
  const to = list.indexOf(targetKey)
  list.splice(to, 0, list.splice(from, 1)[0])
  grid.value = list.map(k => ({ key: k, ...ITEM_MAP[k] }))
  localStorage.setItem('harven_spa_left_grid', JSON.stringify(list))
  dragKey = ''
}

chat.loadConversations()
</script>

<style scoped>
.left-rail {
  width: 252px; flex-shrink: 0; display: flex; flex-direction: column;
  border-right: 1px solid var(--c-line); background: var(--c-card);
  padding: 14px 12px; gap: 10px; overflow: hidden;
}
/* 品牌区 */
.brand { display: flex; align-items: center; gap: 10px; padding: 2px 6px 4px; flex-shrink: 0; }
.brand-duck { width: 40px; height: 40px; border-radius: 10px; overflow: hidden; flex: none; }
.brand-text b { display: block; font-size: 17px; letter-spacing: 2px; color: var(--c-text); }
.brand-text span { font-size: 11px; color: var(--c-text-2); }
.new-chat-btn { flex-shrink: 0; }
/* 搜索框 */
.search-box {
  flex-shrink: 0; display: flex; align-items: center; gap: 6px;
  background: var(--c-bg); border: 1px solid var(--c-line); border-radius: 10px;
  padding: 8px 10px; color: var(--c-text-2);
}
.search-box input {
  flex: 1; border: none; outline: none; background: none;
  font-size: 13px; color: var(--c-text); min-width: 0;
}
.side-label { font-size: 12px; color: var(--c-text-2); margin: 0 4px -4px; flex-shrink: 0; }
/* ★ 2026-10-11 #1095：标题行 flex（左标题右清空键） */
.side-label-row { display: flex; align-items: center; justify-content: space-between; }
.conv-clear { border: none; background: none; color: var(--c-text-2); font-size: 11px; cursor: pointer; padding: 2px 6px; border-radius: 6px; }
.conv-clear:hover { color: #e05555; background: rgba(224, 85, 85, .1); }
.conv-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.conv-item {
  padding: 8px 10px; border-radius: 10px; cursor: pointer;
  border: 1px solid transparent;
}
.conv-item:hover { background: var(--c-bg-soft); }
.conv-item.active { background: var(--c-primary-weak); border-color: var(--c-primary); }
.conv-line1 { display: flex; align-items: center; gap: 6px; }
.conv-title {
  flex: 1; font-size: 13px; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.conv-del { border: none; background: none; color: var(--c-text-2); cursor: pointer; padding: 2px; opacity: 0; }
.conv-item:hover .conv-del { opacity: 1; }
.conv-line2 { display: flex; align-items: center; gap: 6px; margin-top: 3px; }
.conv-panel-tag {
  font-size: 11px; color: var(--c-text-2);
  border: 1px solid var(--c-line); border-radius: 999px; padding: 0 6px;
}
.conv-count { font-size: 11px; color: var(--c-text-2); margin-left: auto; }
.conv-empty { color: var(--c-text-2); font-size: 12px; text-align: center; margin-top: 24px; }
.more-link {
  margin-top: 4px; padding: 6px 10px; text-align: left;
  border: none; background: none; cursor: pointer;
  font-size: 12.5px; color: var(--c-primary);
}
.more-link:hover { text-decoration: underline; }
/* 工具格 */
.tool-grid {
  flex-shrink: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
  border-top: 1px solid var(--c-line); padding-top: 10px;
}
.tool-cell {
  display: flex; flex-direction: column; align-items: center; gap: 3px;
  padding: 8px 0; border-radius: 10px; cursor: grab;
  color: var(--c-text); border: 1px dashed transparent;
}
.tool-cell:hover { background: var(--c-bg-soft); border-color: var(--c-line); }
.tool-cell:active { cursor: grabbing; }
.tool-cell span { font-size: 11px; color: var(--c-text-2); }
.grid-hint { font-size: 10px; color: var(--c-text-2); text-align: center; margin: 0; }
/* 底部脚注 */
.side-foot {
  flex-shrink: 0; display: flex; justify-content: space-between; align-items: center;
  padding: 10px 6px 0; border-top: 1px dashed var(--c-line);
}
.foot-link {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12.5px; color: var(--c-text-2); text-decoration: none;
}
.foot-link:hover { color: var(--c-primary); }

/* ── ★ #810 手机抽屉里加大触控：会话项/工具格 44px 级 ── */
@media (max-width: 768px) {
  .conv-item { padding: 11px 12px; }
  .conv-title { font-size: 14.5px; }
  .tool-cell { padding: 12px 0; }
  .tool-cell span { font-size: 12px; }
  .search-box { padding: 10px 12px; }
  .search-box input { font-size: 15px; }
}
</style>
