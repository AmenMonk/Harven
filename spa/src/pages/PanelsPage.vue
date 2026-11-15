<template>
  <PageShell :title="t('page.panels.t')" icon="grid" :sub="t('page.panels.s')">
    <template #actions>
      <!-- ★ 2026-10-25 #1184 入口收纳：随身记按钮撤除，统一进管家中心（spa /butler） -->
      <BigButton variant="ghost" :px="14" @click="$router.push('/butler')"><HIcon name="task" :size="16" /> 管家中心</BigButton>
      <BigButton variant="ghost" :px="14" @click="installZip"><HIcon name="download" :size="16" /> 安装ZIP</BigButton>
      <BigButton variant="ghost" :px="14" @click="showHub = true"><HIcon name="globe" :size="16" /> 从Hub装</BigButton>
      <BigButton variant="ghost" :px="14" @click="showCreate = true"><HIcon name="plus" :size="16" /> 新建模块</BigButton>
      <BigButton variant="ghost" :px="14" class="manage-top" @click="openManage">⚙ 管理模块</BigButton>
      <BigButton variant="ghost" :px="14" @click="load(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
      <input ref="zipInput" type="file" accept=".zip" style="display:none" @change="uploadZip" />
    </template>

    <div v-if="err" class="err-tip">⚠️ {{ err }}</div>
    <div v-if="msg" class="ok-tip">{{ msg }}</div>

    <!-- ═══ ★ 2026-09-26 开发者需求：哈文助手入口卡（最近对话一眼看到，点卡片弹开浮窗） ═══ -->
    <div class="assist-card" @click="openAssist">
      <div class="ac-head">
        <span class="ac-title">🪟 哈文助手</span>
        <span v-if="assistLastTime" class="ac-time">最近活跃 {{ assistLastTime }}</span>
        <span class="ac-open">打开对话 →</span>
      </div>
      <div v-if="assistRecent.length" class="ac-msgs">
        <div v-for="(m, i) in assistRecent" :key="i" class="ac-msg">
          <b class="ac-who" :class="{ me: m.role === 'user' }">{{ m.role === 'user' ? '我' : '哈文' }}</b>
          <span class="ac-text">{{ m.preview }}</span>
        </div>
      </div>
      <div v-else class="ac-msgs"><span class="ac-empty">还没聊过，点这里开始第一句～</span></div>
    </div>

    <!-- ═══ ★ #1038（2026-10-06）模板区挪回最顶 + 默认展开 + 大卡（开发者：模板不该缩在下面也不该小） ═══ -->
    <div class="sec-row lib-head">
      <h2 class="sec"><HIcon name="box" :size="17" /> 模板区 <small>家庭 / 公司等空间模板，点卡片进入空间页；把模块卡拖到模板卡上可归类</small></h2>
      <button class="mini lib-toggle" @click="catOpen = !catOpen">{{ catOpen ? '▴ 收起' : '▾ 展开' }}（{{ topCats.length }} 个）</button>
    </div>
    <div v-show="catOpen" class="cat-grid">
      <button
        v-for="c in topCats" :key="c.id"
        class="cat-card" :class="{ on: cat === c.id }"
        @click="router.push('/space/' + c.id)"
        @dragover.prevent @drop="dropPanelOnCat(c, $event)"
      >
        <span class="cat-ic">{{ c.icon }}</span>
        <span class="cat-name">{{ c.name }}</span>
        <span class="cat-desc">{{ c.desc }}</span>
        <span v-if="childCats(c.id).length" class="cat-subrow">
          <span v-for="sc in childCats(c.id)" :key="sc.id" class="cat-sub" :title="'进入子模板 ' + sc.name" @click.stop="router.push('/space/' + sc.id)">
            {{ sc.icon }} {{ sc.name }}<i class="cat-sub-x" title="删除这个子模板" @click.stop="delCat(sc)">×</i>
          </span>
        </span>
        <span class="cat-count">{{ countByCat(c.id) }} 个模块 · 点击进入 →</span>
        <span v-if="isCustomCat(c.id)" class="cat-del" title="删除这个模板" @click.stop="delCat(c)">×</span>
        <span v-else class="cat-del" title="隐藏这个出厂模板（可随时恢复）" @click.stop="delCat(c)">×</span>
      </button>
      <button class="cat-card cat-add" @click="addCat">＋<span class="cat-name">新建模板</span><span class="cat-desc">家庭 / 公司 / 自己…</span></button>
    </div>
    <div v-if="hiddenCount > 0" class="restore-line">
      已隐藏 {{ hiddenCount }} 个出厂模板
      <button class="mini" @click="restoreCats">全部恢复显示</button>
    </div>
    <!-- ═══ 模块库（E 批：设计稿 v3 面板选择器「模块库待选区」，纯前端预置 + 一键安装）
         ★ 2026-09-20 用户反馈：可折叠 + 默认收起（已有模块时），一打开就能看到「我的模块」 ═══ -->
    <div class="sec-row lib-head">
      <h2 class="sec"><HIcon name="box" :size="17" /> 模块库 <small>现成的小账本和小台账，点「安装」一键建好；同一个模块可装多份数据互不干扰</small></h2>
      <button class="mini lib-toggle" @click="libOpen = !libOpen">{{ libOpen ? '▴ 收起' : '▾ 展开' }}（{{ TPL.length }} 个）</button>
    </div>
    <div v-show="libOpen" class="tpl-grid">
      <div v-for="t in TPL" :key="t.base" class="tcard">
        <div class="t-ic">{{ t.icon }}</div>
        <div class="t-info">
          <div class="t-head">
            <b>{{ t.name }}</b>
            <span class="t-group" :class="t.group">{{ t.group === 'home' ? '家庭' : t.group === 'company' ? '公司' : '创作' }}</span>
            <span v-if="tplCount(t) > 0" class="t-count">已装 {{ tplCount(t) }} 份</span>
          </div>
          <span class="t-desc">{{ t.desc }}</span>
          <div class="t-fields">
            <span v-for="f in t.fields" :key="f.name" class="chip">{{ f.name }}</span>
          </div>
        </div>
        <BigButton :px="13" :loading="installingTpl === t.base" @click="installTemplate(t)"><HIcon name="plus" :size="14" /> 安装</BigButton>
      </div>
    </div>

    <!-- ═══ 已装模块卡墙 ═══ -->
    <div class="sec-row">
      <h2 class="sec"><HIcon name="box" :size="17" /> 我的模块 <small>「打开」记一笔 · 「停靠」钉到右栏 · 拖动卡片可归类排序 · 卸载在右上角「⚙ 管理模块」</small></h2>
    </div>
    <div class="quick-filter">
      <button class="qf-chip" :class="{ on: cat === 'all' }" @click="cat = 'all'">全部</button>
      <button v-for="c in visibleCats" :key="c.id" class="qf-chip" :class="{ on: cat === c.id }" @click="cat = cat === c.id ? 'all' : c.id">{{ c.parent ? '└ ' : '' }}{{ c.icon }} {{ c.name }}</button>
    </div>
    <div v-if="!filteredPanels.length" class="empty">
      还没有 {{ cat === 'all' ? '' : allCats.find(c => c.id === cat)?.name + ' 里的' }}模块。
      点右上角「新建模块」一键创建，或「安装ZIP」导入，也可以去 <a href="#/hub">HarvenHub</a> 逛逛。
    </div>
    <!-- ★ #1120（2026-10-13）拖拽换组：模块卡拖到目标组（标题/空白处）松手即换组，落卡片上仍是排序/助手入驻 -->
    <div v-for="g in groupedPanels" :key="g.name" class="pgroup"
      :class="{ 'group-over': dragOverGroup === g.name }"
      @dragover.prevent="onGroupDragOver(g.name, $event)"
      @dragleave="dragOverGroup === g.name && (dragOverGroup = '')"
      @drop="dropGroupChange(g.name, $event)">
      <div class="pg-head" @click="toggleGroup(g.name)">
        <span class="pg-arrow">{{ foldedGroups[g.name] ? '▶' : '▼' }}</span>
        <b>{{ g.name }}</b>
        <span class="pg-n">{{ g.panels.length }} 个模块</span>
      </div>
      <div v-show="!foldedGroups[g.name]" class="panel-grid">
        <div
          v-for="p in g.panels" :key="p.id"
          class="pcard" draggable="true"
          @dragstart="dragPanel(p, $event)"
          @dragover.prevent @drop="dropAgentOnPanel(p, $event)"
          @dragend="dragOverGroup = ''"
        >
        <div class="p-ic">{{ p.icon && p.icon.length <= 3 ? p.icon : '🧩' }}</div>
        <div class="p-info">
          <b>{{ p.name }}</b>
          <span class="p-desc">{{ p.description || '（没有描述）' }}</span>
          <span class="p-meta">{{ p.type || 'table' }} · {{ (p.fields || []).length }} 个字段 · {{ p.author === 'user' ? '我自己建的' : (p.author || '未知作者') }}</span>
          <div v-if="residentAgents(p.id).length" class="p-agents">
            <span v-for="ag in residentAgents(p.id)" :key="ag.id" class="p-agent-chip" :title="'触发词：' + (ag.triggers || []).join('、')">
              🤖 {{ ag.name }}<i class="pa-x" title="移出模块" @click.stop="unbindAgent(p.id, ag.id)">×</i>
            </span>
            <span class="p-agent-tip">把右侧助手卡拖到这里可添加驻场助手</span>
          </div>
        </div>
        <div class="p-btns">
          <BigButton :px="13" @click="$router.push('/panel/' + p.id)"><HIcon name="send" :size="14" /> 打开</BigButton>
          <button class="mini" :class="{ pinned: isPinned(p.id) }" :title="isPinned(p.id) ? '从右栏取消停靠' : '停靠到右栏，随时可见'" @click="togglePin(p)">
            {{ isPinned(p.id) ? '已停靠' : '停靠' }}
          </button>
        </div>
        </div>
      </div>
    </div>

    <!-- ═══ ★ 2026-09-19 卸载权限修复：被移出的模块恢复区（空间页只移出不卸载，全面卸载只在管理弹层） ═══ -->
    <div v-if="removedEntries.length" class="restore-line removed-line">
      <span class="rl-label">🔄 {{ removedEntries.length }} 个模块被移出了空间页（面板和数据都还在）：</span>
      <span v-for="e in removedEntries" :key="e.pid + e.catId" class="rm-chip">
        {{ e.panel.icon && e.panel.icon.length <= 3 ? e.panel.icon : '🧩' }} {{ e.panel.name }}
        <i class="rm-cat">从「{{ catName(e.catId) }}」</i>
        <b class="rm-restore" title="放回原来的空间页" @click="restoreRemoved(e)">恢复</b>
      </span>
    </div>

    <!-- ═══ Agent 详细区（重点） ═══ -->
    <h2 class="sec"><HIcon name="bot" :size="17" /> 助手团队（Agent）<small>助手卡能拖到上面的模块卡里当驻场帮手；🔒核心 3 位不可动，其余可停用（随时开回来），自定义的可删</small></h2>

    <!-- 待审批 -->
    <div v-if="pending.length" class="approve-bar">
      <div class="ab-title"><HIcon name="shield" :size="16" /> 有 {{ pending.length }} 个操作等你授权</div>
      <div v-for="r in pending" :key="r.id" class="ab-item">
        <div class="ab-main">
          <code class="ab-action">{{ r.action }}</code>
          <span class="ab-params">{{ JSON.stringify(r.params).slice(0, 120) }}</span>
          <span class="ab-time">{{ r.requester || '模型' }} 发起 · {{ (r.created_at || '').slice(0, 16).replace('T', ' ') }}</span>
        </div>
        <div class="ab-btns">
          <BigButton variant="ghost" :px="13" :loading="busyId === r.id" @click="approve(r)">同意授权</BigButton>
          <BigButton :px="13" :loading="busyId === r.id + '!x'" @click="approveAndRun(r)">同意并执行</BigButton>
        </div>
      </div>
    </div>

    <div class="agent-grid">
      <div
        v-for="a in agents" :key="a.id"
        class="acard" :class="{ open: openId === a.id, disabled: a.enabled === false }" draggable="true"
        @dragstart="dragAgent(a, $event)"
        @click="openId = openId === a.id ? '' : a.id"
      >
        <div class="a-head">
          <StatusDot :status="a.status === 'busy' ? 'warn' : (a.enabled === false ? 'off' : 'ok')" />
          <b class="a-name">{{ a.name }}</b>
          <span v-if="a.core" class="a-lock" title="核心助手：不可删不可停，哈文根基">🔒</span>
          <span v-if="a.enabled === false" class="a-badge m-disabled">已停用</span>
          <span v-else class="a-badge" :class="'m-' + (a.model_pref || 'local')">{{ modelPrefText(a.model_pref) }}</span>
          <span class="a-count">已被叫过 {{ a.call_count || 0 }} 次</span>
          <HIcon :name="openId === a.id ? 'x' : 'dots'" :size="15" class="a-fold" />
        </div>
        <div class="a-role">{{ a.role }}</div>
        <div class="a-trigs">
          <span v-for="t in (a.triggers || []).slice(0, openId === a.id ? 99 : 3)" :key="t" class="chip">{{ t }}</span>
          <span v-if="(a.triggers || []).length > 3 && openId !== a.id" class="chip more">+{{ a.triggers.length - 3 }}</span>
        </div>
        <div v-if="openId === a.id" class="a-detail" @click.stop>
          <div class="kv"><span class="k2">身份代码</span><span class="v2">{{ a.id }}</span></div>
          <div class="kv"><span class="k2">职责分类</span><span class="v2">{{ a.role }}</span></div>
          <div class="kv"><span class="k2">当前状态</span><span class="v2" :class="{ ok: a.status !== 'busy' }">{{ a.status === 'busy' ? '正在干活' : '随时待命' }}</span></div>
          <div class="kv"><span class="k2">走哪个脑子</span><span class="v2">{{ modelPrefText(a.model_pref) }}</span></div>
          <div class="kv"><span class="k2">累计调用</span><span class="v2">{{ a.call_count || 0 }} 次</span></div>
          <div class="kv"><span class="k2">驻场模块</span><span class="v2">{{ agentPanelsText(a.id) }}</span></div>
          <div class="kv"><span class="k2">触发词（{{ (a.triggers || []).length }} 个）</span></div>
          <div class="a-alltrigs"><span v-for="t in a.triggers || []" :key="t" class="chip">{{ t }}</span></div>
          <div class="a-hint">💬 在聊天框直接喊他的触发词（或 @{{ a.id }}）就能召唤；按住这张卡拖到上面的模块卡里，他就成了那个模块的驻场助手</div>
          <div v-if="a.core" class="a-tier core">🔒 核心助手 —— 电脑操作/系统管家/程序员是哈文根基，不可删除也不可停用</div>
          <template v-else-if="!a.custom">
            <div class="a-tier soft">出厂自带助手 —— 支持停用（随时可开回来，不会丢数据），不可彻底删除</div>
            <div class="a-btns">
              <button v-if="a.enabled !== false" class="mini a-toggle" :disabled="busyId === a.id + ':tg'" @click="toggleAgent(a, false)">⏸ 停用</button>
              <button v-else class="mini a-toggle on" :disabled="busyId === a.id + ':tg'" @click="toggleAgent(a, true)">▶ 开启</button>
            </div>
          </template>
          <button v-else class="mini danger a-del" @click="delAgent(a)">删除这个助手</button>
        </div>
      </div>
    </div>
    <div v-if="!agents.length && !loadErr" class="empty">正在加载助手团队…</div>
    <div v-if="loadErr && !agents.length" class="empty">助手团队加载失败：{{ loadErr }}</div>

    <!-- ═══ 新建模块弹层 ═══ -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal">
        <div class="m-head"><b>新建模块（表格型）</b><button class="mini" @click="showCreate = false">关闭</button></div>
        <div class="m-form">
          <label>模块名称<input v-model.trim="nf.name" placeholder="例如：车辆保养记录" /></label>
          <label>模块ID（字母数字-_，创建后不可改）<input v-model.trim="nf.id" placeholder="例如：car-maintenance" spellcheck="false" /></label>
          <label>描述（可不填）<input v-model.trim="nf.description" placeholder="例如：记录途锐每次保养的时间、项目、花费" /></label>
          <label>图标（一个 emoji）<input v-model.trim="nf.icon" maxlength="3" placeholder="🚗" /></label>
          <div class="nf-fields">
            <div class="nff-head"><span>字段（要记哪些列）</span><button class="mini" @click="nf.fields.push({ name: '', type: 'text' })">＋ 加字段</button></div>
            <div v-for="(f, i) in nf.fields" :key="i" class="nff-row">
              <input v-model.trim="f.name" placeholder="字段名，如：花费" />
              <select v-model="f.type">
                <option value="text">文字</option>
                <option value="number">数字</option>
                <option value="date">日期</option>
                <option value="select">单选</option>
              </select>
              <button class="mini danger" @click="nf.fields.splice(i, 1)">删</button>
            </div>
          </div>
          <label class="chk-line"><input type="checkbox" v-model="nf.with_chart" /> 自动生成统计图表</label>
          <BigButton :px="14" :loading="creating" @click="createPanel"><HIcon name="check" :size="15" /> 建好了</BigButton>
        </div>
      </div>
    </div>
    <!-- ═══ 管理模块弹层（卸载/保护级别集中在这里，防误删） ═══ -->
    <div v-if="showManage" class="modal-mask" @click.self="showManage = false">
      <div class="modal manage-modal">
        <div class="m-head"><b>⚙ 模块管理 <span class="m-sub">卸载、保护级别都在这里集中操作</span></b><button class="mini" @click="showManage = false">关闭</button></div>
        <div class="manage-body">
          <div class="manage-tip">
            保护级别：<b>普通</b> = 确认后即可卸载 · <b>受保护</b> = 卸载前多一道手输模块名确认 ·
            <b>🔒 锁定</b> = 还需要管理员密码（和登录哈文的密码一致），别人碰不了
          </div>
          <div v-for="p in manageList" :key="p.id" class="mg-row">
            <span class="mg-ic">{{ p.icon && p.icon.length <= 3 ? p.icon : '🧩' }}</span>
            <div class="mg-info">
              <b>{{ p.name }}</b>
              <span class="mg-meta">{{ p.type || 'table' }} · 已记 {{ p.record_count || 0 }} 条数据
                <em v-if="p.record_count > 0" class="mg-warn">卸载后数据全没</em></span>
            </div>
            <select class="mg-level" :value="p.protection || 'normal'" @change="saveProtection(p, $event.target.value)">
              <option value="normal">普通</option>
              <option value="protected">受保护</option>
              <option value="locked">🔒 锁定</option>
            </select>
            <button class="mini" @click="exportPanel(p)">导出</button>
            <button class="mini danger" @click="uninstallManaged(p)">卸载</button>
          </div>
          <div v-if="!manageList.length" class="empty">没有已安装的模块</div>
        </div>
      </div>
    </div>

    <!-- ═══ ★ #1130 从 Hub 一键安装面板模块 ═══ -->
    <div v-if="showHub" class="modal-mask" @click.self="showHub = false">
      <div class="modal hub-modal">
        <div class="m-head"><b>从 HarvenHub 安装模块 <span class="m-sub">填 Hub 条目 id，自动下载安装</span></b><button class="mini" @click="showHub = false">关闭</button></div>
        <div class="manage-body">
          <div class="manage-tip">
            到 <b>harvenhub.com</b> 找到想要的模块（类型=模板），进详情页复制内容 id 粘到下面。
            安装完在「我的模块」里就能看到，不用重启不用更新。
          </div>
          <div class="hub-row">
            <input class="hub-id" v-model="hubId" placeholder="粘贴 Hub 内容 id，如 7f579dbb342043e4" @keyup.enter="installHub" />
            <button class="mini" @click="installHub">安装</button>
          </div>
        </div>
      </div>
    </div>
    <!-- ★ 2026-10-25 #1184：随身记收件箱弹层已撤，统一进管家中心 -->
  </PageShell>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import StatusDot from '../components/StatusDot.vue'
import { getJSON, postJSON, delJSON } from '../api.js'
import { DEFAULT_CATS, TPL, AI_ROLES, inferCat, isUniversal, migrateCatMap } from '../panelCatalog.js'
import { useRouter } from 'vue-router'

/* ═══════════ 模板：默认 3 个出厂模板 + 自定义（localStorage） ═══════════
   ★ 2026-09-14 大改版：DEFAULT_CATS / TPL / inferCat 抽到 panelCatalog.js 共享，
   模板卡点击直接进入该模板的「空间页」（/space/:cat） */
const CATS_KEY = 'harven_spa_panel_cats'
const CATMAP_KEY = 'harven_spa_panel_cat_map'
const PINS_KEY = 'harven_spa_dock_pins'
const BIND_KEY = 'harven_spa_panel_agents'
const HIDDEN_KEY = 'harven_spa_panel_cats_hidden'
/* ★ 2026-09-19 卸载权限修复：与 SpacePage 共用排除名单 { '<catId>': ['<panelId>'...] }，
   面板中心负责「被移出的模块」恢复区 + 全面卸载 */
const REMOVED_KEY = 'harven_spa_panel_removed'

const customCats = ref(JSON.parse(localStorage.getItem(CATS_KEY) || '[]'))
const hiddenCats = ref(JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]'))
const catMap = ref(migrateCatMap(JSON.parse(localStorage.getItem(CATMAP_KEY) || '{}')))
const pins = ref(JSON.parse(localStorage.getItem(PINS_KEY) || '[]'))
const binds = ref(JSON.parse(localStorage.getItem(BIND_KEY) || '{}'))
const removed = ref(JSON.parse(localStorage.getItem(REMOVED_KEY) || '{}'))

const allCats = computed(() => [...DEFAULT_CATS, ...customCats.value])
/* ★ 2026-09-16 用户反馈：出厂模板也要能删——默认模板删除=隐藏（可恢复），自定义模板删除=真删 */
const visibleCats = computed(() => allCats.value.filter(c => !hiddenCats.value.includes(c.id)))
const hiddenCount = computed(() => hiddenCats.value.length)
/* ★ 2026-09-16 层级模板：自定义模板可挂在顶级模板下，父卡内以 chips 展示 */
const topCats = computed(() => visibleCats.value.filter(c => !c.parent))
function childCats(pid) { return visibleCats.value.filter(c => c.parent === pid) }
function isCustomCat(id) { return customCats.value.some(c => c.id === id) }
function persistCats() { localStorage.setItem(CATS_KEY, JSON.stringify(customCats.value)) }
function persistHidden() { localStorage.setItem(HIDDEN_KEY, JSON.stringify(hiddenCats.value)) }
function persistCatMap() { localStorage.setItem(CATMAP_KEY, JSON.stringify(catMap.value)) }
function persistPins() {
  localStorage.setItem(PINS_KEY, JSON.stringify(pins.value))
  window.dispatchEvent(new CustomEvent('harven-dock-pins')) // 通知右栏即时刷新
}
function persistBinds() { localStorage.setItem(BIND_KEY, JSON.stringify(binds.value)) }
function persistRemoved() { localStorage.setItem(REMOVED_KEY, JSON.stringify(removed.value)) }
/* 被移出的模块展平成 [{ pid, catId }]（面板还在的才展示——已全面卸载的记录自动失效） */
const removedEntries = computed(() => {
  const out = []
  for (const [cid, pids] of Object.entries(removed.value)) {
    for (const pid of pids || []) {
      const p = panels.value.find(x => x.id === pid)
      if (p) out.push({ pid, catId: cid, panel: p })
    }
  }
  return out
})
function catName(cid) { return allCats.value.find(c => c.id === cid)?.name || cid }
/* 清某个面板的全部移出记录（恢复/重装/全面卸载后调用） */
function clearRemovedFor(pid) {
  let hit = false
  for (const cid of Object.keys(removed.value)) {
    const list = removed.value[cid] || []
    if (list.includes(pid)) { removed.value[cid] = list.filter(x => x !== pid); hit = true }
    if (!removed.value[cid].length) delete removed.value[cid]
  }
  if (hit) persistRemoved()
}

function addCat() {
  const name = prompt('新模板叫什么？（例如：公司 / 自己 / 车库）')
  if (!name || !name.trim()) return
  // ★ 2026-09-16 层级模板：可以挂在某个顶级模板下当子模板
  const tops = topCats.value
  const parentInput = prompt(
    `放在哪个模板下面？\n${tops.map((c, i) => `${i + 1} = ${c.name}`).join('\n')}\n\n直接点确定 = 顶级模板（输入序号 = 它的子模板）`)
  let parent = ''
  if (parentInput && parentInput.trim()) {
    const idx = parseInt(parentInput.trim(), 10)
    if (idx >= 1 && idx <= tops.length) parent = tops[idx - 1].id
  }
  const id = 'c_' + Date.now().toString(36)
  customCats.value.push({ id, icon: parent ? '📎' : '📁', name: name.trim(), desc: parent ? '子模板' : '自定义模板', parent })
  persistCats()
}
function delCat(c) {
  if (isCustomCat(c.id)) {
    if (!confirm(`删除模板「${c.name}」？模板里的模块不会被删，会回到自动归类`)) return
    customCats.value = customCats.value.filter(x => x.id !== c.id)
    for (const k of Object.keys(catMap.value)) if (catMap.value[k] === c.id) delete catMap.value[k]
    persistCats(); persistCatMap()
  } else {
    if (!confirm(`隐藏出厂模板「${c.name}」？里面的模块不会被删，可随时在下方「全部恢复显示」找回来`)) return
    hiddenCats.value = [...hiddenCats.value, c.id]
    persistHidden()
  }
  if (cat.value === c.id) cat.value = 'all'
}
function restoreCats() {
  hiddenCats.value = []
  persistHidden()
}

/* ═══ ★ 2026-09-19 被移出的模块恢复：从排除名单里清掉，模块自动回到原来的空间页 */
function restoreRemoved(e) {
  clearRemovedFor(e.pid)
  flash(`「${e.panel.name}」已恢复，回到「${catName(e.catId)}」空间页`)
}

/* ═══════════ 面板数据 ═══════════ */
const router = useRouter()
const panels = ref([])
const agents = ref([])
const pending = ref([])
const cat = ref('all')
const openId = ref('')
const err = ref('')
const msg = ref('')
const loadErr = ref('')
const busyId = ref('')
const creating = ref(false)
const showCreate = ref(false)
const zipInput = ref(null)
/* ★ 2026-09-20 布局改版：模块库折叠开关（null=未初始化，加载后按是否有模块决定） */
const libOpen = ref(null)
/* ★ #1038（2026-10-06）默认展开：开发者要一进来就看到大模板卡 */
const catOpen = ref(true)
let timer = null
let msgTimer = null

const nf = reactive({ name: '', id: '', description: '', icon: '', with_chart: false, fields: [{ name: '', type: 'text' }] })
/* ★ 2026-09-18 通用模块：universal 标记或猜不出分类的面板，在所有分类筛选下都可见 */
const enriched = computed(() => panels.value.map(p => {
  const guessed = catMap.value[p.id] || inferCat(p)
  return { ...p, _cat: guessed || 'universal', _universal: isUniversal(p) || !guessed }
}))
const filteredPanels = computed(() => (cat.value === 'all' ? enriched.value : enriched.value.filter(p => p._cat === cat.value || p._universal)))

/* ★ 分组宫格：group 来自 /api/panels（_groups.json 唯一数据源，与手机端同一份账）；未分组归「常用」置顶 */
const GROUP_ORDER = ['日程待办', '家庭账本', '食物存放', '健康守护', '还在成长', '老人关怀', '车辆出行', '家务管家', '生活记录']
const groupedPanels = computed(() => {
  const src = filteredPanels.value
  const groups = []
  const common = src.filter(p => !p.group)
  if (common.length) groups.push({ name: '常用', panels: common })
  for (const g of GROUP_ORDER) {
    const list = src.filter(p => p.group === g)
    if (list.length) groups.push({ name: g, panels: list })
  }
  for (const p of src) {   // 兜底：新组名不在 GROUP_ORDER 里也能显示
    if (p.group && !GROUP_ORDER.includes(p.group) && !groups.some(g => g.name === p.group)) {
      const list = src.filter(x => x.group === p.group)
      groups.push({ name: p.group, panels: list })
    }
  }
  return groups
})
const foldedGroups = ref(JSON.parse(localStorage.getItem('panel_groups_folded') || '{}'))
function toggleGroup(n) {
  foldedGroups.value[n] = !foldedGroups.value[n]
  localStorage.setItem('panel_groups_folded', JSON.stringify(foldedGroups.value))
}
function countByCat(cid) {
  /* ★ 2026-09-16 层级：父分类计数包含其子分类 */
  const ids = [cid, ...childCats(cid).map(c => c.id)]
  return enriched.value.filter(p => ids.includes(p._cat)).length
}

function flash(text, ok = true) {
  msg.value = text
  clearTimeout(msgTimer)
  msgTimer = setTimeout(() => { msg.value = '' }, 4000)
}

function modelPrefText(m) {
  return { local: '🏠 本地', external: '☁️ 外脑', vertical: '🎯 垂直' }[m] || '🏠 本地'
}

async function load(showErr = false) {
  try {
    const [p, a, ap] = await Promise.all([
      getJSON('/api/panels').catch(() => ({ panels: [] })),
      getJSON('/api/agents'),
      getJSON('/api/agent/pending').catch(() => ({ approvals: [] })),
    ])
    panels.value = p.panels || p || []
    agents.value = a.agents || []
    pending.value = ap.approvals || []
    loadErr.value = ''
    if (showErr) err.value = ''
  } catch (e) {
    if (showErr) err.value = '数据拉取失败：' + e.message
    loadErr.value = e.message
  }
}

/* ═══════════ 拖拽：助手→面板（驻场） + 面板卡→面板卡（排序 ★ 2026-09-18） ═══════════ */
function dragPanel(p, e) {
  e.dataTransfer.setData('text/harven-panel', p.id)
}
function dropPanelOnCat(c, e) {
  const pid = e.dataTransfer.getData('text/harven-panel')
  if (!pid) return
  catMap.value[pid] = c.id
  persistCatMap()
  flash(`「${panels.value.find(x => x.id === pid)?.name || pid}」已归入「${c.name}」`)
}

/* ★ 2026-09-18 拖拽排序（全局顺序源：后端 _order.json，与空间页/右栏停靠共用） */
async function reorderPanels(dragId, targetId) {
  const ids = panels.value.map(x => x.id)
  const from = ids.indexOf(dragId), to = ids.indexOf(targetId)
  if (from < 0 || to < 0) return
  ids.splice(to, 0, ids.splice(from, 1)[0])
  const rank = new Map(ids.map((id, i) => [id, i]))
  panels.value = [...panels.value].sort((a, b) => (rank.get(a.id) ?? 999) - (rank.get(b.id) ?? 999))
  try {
    const j = await postJSON('/api/panels/reorder', { order: ids })
    if (j.success) flash('顺序已保存')
  } catch (_) { /* 静默：下次刷新会同步 */ }
}

function dragAgent(a, e) {
  e.dataTransfer.setData('text/harven-agent', a.id)
}
function dropAgentOnPanel(p, e) {
  /* 先看是不是面板卡拖上来（排序优先于助手入驻） */
  const pid = e.dataTransfer.getData('text/harven-panel')
  if (pid && pid !== p.id) { reorderPanels(pid, p.id); return }
  const aid = e.dataTransfer.getData('text/harven-agent')
  if (!aid) return
  const list = binds.value[p.id] || []
  if (!list.includes(aid)) list.push(aid)
  binds.value[p.id] = list
  persistBinds()
  const ag = agents.value.find(x => x.id === aid)
  flash(`助手「${ag?.name || aid}」已入驻「${p.name}」`)
}
function residentAgents(pid) {
  return (binds.value[pid] || []).map(id => agents.value.find(a => a.id === id)).filter(Boolean)
}

/* ═══ ★ #1120（2026-10-13）拖拽换组：模块卡拖到目标组松手即换分组（组册 _groups.json 唯一账本） ═══ */
const dragOverGroup = ref('')
function onGroupDragOver(name, e) {
  /* 只有模块卡拖动时才高亮组（助手卡拖动是驻场语义，不高亮） */
  if (e.dataTransfer.types.includes('text/harven-panel')) dragOverGroup.value = name
}
function dropGroupChange(name, e) {
  if (dragOverGroup.value === name) dragOverGroup.value = ''
  const pid = e.dataTransfer.getData('text/harven-panel')
  if (!pid) return /* 助手卡落到组上 → 忽略（驻场只认卡片） */
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
function unbindAgent(pid, aid) {
  binds.value[pid] = (binds.value[pid] || []).filter(x => x !== aid)
  persistBinds()
}
function agentPanelsText(aid) {
  const names = Object.entries(binds.value).filter(([, ids]) => ids.includes(aid)).map(([pid]) => panels.value.find(p => p.id === pid)?.name || pid)
  return names.length ? names.join('、') : '（还没入驻任何面板）'
}

/* ═══════════ 停靠到右栏 ═══════════ */
function isPinned(pid) { return pins.value.includes(pid) }
function togglePin(p) {
  pins.value = isPinned(p.id) ? pins.value.filter(x => x !== p.id) : [...pins.value, p.id]
  persistPins()
  flash(isPinned(p.id) ? `「${p.name}」已停靠到右栏` : `「${p.name}」已从右栏收起`)
}

/* ═══════════ 新建 / 安装 / 卸载 ═══════════ */
async function createPanel() {
  if (!nf.name) { err.value = '先给模块起个名字'; return }
  if (!nf.id || !/^[A-Za-z0-9_-]+$/.test(nf.id)) { err.value = '模块ID只能包含字母、数字、-、_（创建后不可改）'; return }
  const fields = nf.fields.filter(f => f.name).map(f => ({ name: f.name, type: f.type }))
  if (!fields.length) { err.value = '至少要有一个字段'; return }
  creating.value = true
  try {
    const j = await postJSON('/api/panels/create', {
      name: nf.name, id: nf.id, description: nf.description,
      icon: nf.icon || '🧩', fields, with_chart: nf.with_chart, with_search: true,
    })
    if (!j.success) throw new Error(j.message || '创建失败')
    showCreate.value = false
    Object.assign(nf, { name: '', id: '', description: '', icon: '', with_chart: false, fields: [{ name: '', type: 'text' }] })
    flash(`模块「${j.panel?.name || nf.name}」创建成功`)
    await load()
  } catch (e) { err.value = '创建失败：' + e.message }
  finally { creating.value = false }
}

/* ═══════════ 模板库：TPL 已抽到 panelCatalog.js 共享（SpacePage 空间页同用一份） ═══════════ */
const installingTpl = ref('')

function tplCount(t) {
  return panels.value.filter(p => p.id === t.base || p.id.startsWith(t.base + '_')).length
}
function nextTplId(base) {
  let n = 1
  while (panels.value.some(p => p.id === `${base}_${n}`)) n++
  return `${base}_${n}`
}
async function installTemplate(t) {
  const n = tplCount(t) + 1
  const id = nextTplId(t.base)
  if (!confirm(`安装模块「${t.name}」？\n会新建一个模块（${t.fields.length} 个字段都帮你配好），${n > 1 ? `这是第 ${n} 份，数据独立不冲突` : '装好就能记数据'}`)) return
  installingTpl.value = t.base
  try {
    const j = await postJSON('/api/panels/create', {
      name: n > 1 ? `${t.name} #${n}` : t.name,
      id, description: t.desc, icon: t.icon,
      fields: t.fields, with_chart: true, with_search: true,
      features: t.features || {}, // ★ 2026-09-19 模板自带能力配置（提醒/统计/AI）随安装带过去
      ai_role: AI_ROLES[t.base] || '', // ★ 2026-09-19 AI 角色卡随安装带过去（P0 全量铺）
    })
    if (!j.success) throw new Error(j.message || '创建失败')
    // ★ 2026-09-13 用户反馈#6：装哪个分类听用户的——当前选中了分类就归那里，没选（all）才按模板默认归位
    catMap.value[id] = (cat.value && cat.value !== 'all') ? cat.value : t.cat
    clearRemovedFor(id) // ★ 同名 ID 曾被移出过 → 重装要清掉移出记录，否则被排除名单藏住
    persistCatMap()
    flash(`模块「${t.name}」安装成功，已归入对应模板`)
    await load()
  } catch (e) { err.value = '模板安装失败：' + e.message }
  finally { installingTpl.value = '' }
}

function installZip() { zipInput.value?.click() }
async function uploadZip(ev) {
  const file = ev.target.files?.[0]
  ev.target.value = ''
  if (!file) return
  const fd = new FormData()
  fd.append('file', file)
  try {
    const r = await fetch('/api/panels/install', { method: 'POST', body: fd })
    const j = await r.json()
    if (!j.success) throw new Error(j.message || '安装失败')
    flash(`模块「${j.panel?.name || file.name}」安装成功`)
    await load()
  } catch (e) { err.value = '安装失败：' + e.message }
}

/* ═══════════ ★ 2026-09-16 管理面板：集中卸载 + 保护级别（平时卡上不露卸载，防误删） ═══════════ */
const showManage = ref(false)
const manageList = ref([])

/* ═══════════ ★ #1130（2026-10-14）面板生态：导出分享 + 从 Hub 一键安装 ═══════════ */
async function exportPanel(p) {
  try {
    const resp = await fetch(`/api/panels/${encodeURIComponent(p.id)}/export`)
    if (!resp.ok) {
      const j = await resp.json().catch(() => ({}))
      flash(j.message || '导出失败', false)
      return
    }
    const blob = await resp.blob()
    const cd = resp.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/)
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = m ? decodeURIComponent(m[1]) : `${p.id}.zip`
    a.click()
    URL.revokeObjectURL(a.href)
    flash(`「${p.name}」已导出为压缩包，可到 HarvenHub 上传分享给别人`)
  } catch { flash('导出失败', false) }
}

const showHub = ref(false)
const hubId = ref('')
async function installHub() {
  const iid = hubId.value.trim()
  if (!iid) { flash('先填 Hub 内容 id（Hub 网页条目详情里能查到）', false); return }
  try {
    const j = await postJSON('/api/panels/install_hub', { item_id: iid })
    if (!j.success) { flash(j.message || '安装失败', false); return }
    flash(`模块「${j.panel?.name || iid}」安装成功`)
    showHub.value = false
    hubId.value = ''
    await load()
  } catch (e) { flash('安装失败：' + (e.message || '网络错误'), false) }
}

/* ═══ ★ 2026-10-25 #1184 入口收纳：随身记收件箱代码已撤（showInbox/inboxNotes/refreshInboxCount 等），
   随身记统一在管家中心页；顶栏原按钮位改为管家中心直达 ═══ */

async function openManage() {
  showManage.value = true
  try {
    const j = await getJSON('/api/panels/manage')
    manageList.value = j.panels || []
  } catch (e) { err.value = '管理数据拉取失败：' + e.message }
}

async function saveProtection(p, level) {
  try {
    await postJSON(`/api/panels/${p.id}/protection`, { level })
    p.protection = level
    flash(level === 'locked' ? `「${p.name}」已锁定，卸载需要管理员密码` : `「${p.name}」保护级别已更新`)
  } catch (e) { err.value = '保护级别设置失败：' + e.message }
}

async function uninstallManaged(p) {
  const n = p.record_count || 0
  if (!confirm(`卸载「${p.name}」？${n > 0 ? `\n里面有 ${n} 条记录，` : '\n'}卸载后数据全部删除、不可恢复！`)) return
  let password = ''
  if ((p.protection || 'normal') === 'locked') {
    password = prompt('🔒 该模块已锁定保护，请输入管理员密码：') || ''
    if (!password) return
  }
  const typed = prompt(`最后一步：请输入模块名字「${p.name}」确认卸载`)
  if (typed === null) return
  if (typed.trim() !== p.name) { err.value = '名字没对上，已取消卸载'; return }
  try {
    await delJSON(`/api/panels/${p.id}`, password ? { password } : undefined)
    pins.value = pins.value.filter(x => x !== p.id); persistPins()
    delete binds.value[p.id]; persistBinds()
    delete catMap.value[p.id]; persistCatMap()
    clearRemovedFor(p.id) // ★ 全面卸载后排除名单里的记录也失效
    manageList.value = manageList.value.filter(x => x.id !== p.id)
    flash(`「${p.name}」已卸载`)
    await load()
  } catch (e) { err.value = '卸载失败：' + e.message }
}

/* ═══════════ 审批 / 删除助手 ═══════════ */
async function approve(r) {
  busyId.value = r.id
  try { await postJSON('/api/agent/approve', { id: r.id }); await load() }
  catch (e) { err.value = '授权失败：' + e.message }
  finally { busyId.value = '' }
}
async function approveAndRun(r) {
  busyId.value = r.id + '!x'
  try {
    await postJSON('/api/agent/approve', { id: r.id })
    await postJSON('/api/agent/execute', { action: r.action, params: r.params || {} })
    await load()
  } catch (e) { err.value = '执行失败：' + e.message }
  finally { busyId.value = '' }
}
async function delAgent(a) {
  if (!confirm(`删除助手「${a.name}」？删除后无法恢复（只影响这位自定义助手，出厂自带的不会被删）`)) return
  try { await delJSON(`/api/agents/${a.id}`); await load() }
  catch (e) { err.value = '删除失败：' + e.message }
}

// ★ 2026-09-13 三层管理：停用/启用（核心 3 位后端拒绝，前端也不出按钮）
async function toggleAgent(a, enabled) {
  const tip = enabled ? `开启「${a.name}」？开启后聊天框里的触发词重新生效` : `停用「${a.name}」？停用后聊天框不再触发他（可随时开回来，数据不丢）`
  if (!confirm(tip)) return
  busyId.value = a.id + ':tg'
  try { await postJSON(`/api/agents/${a.id}/toggle`, { enabled }); await load() }
  catch (e) { err.value = (enabled ? '开启' : '停用') + '失败：' + e.message }
  finally { busyId.value = '' }
}

/* ═══ ★ 2026-09-26 开发者需求：哈文助手入口卡（读全局历史 localStorage，与浮窗同一份） ═══ */
const assistRecent = ref([])
const assistLastTime = ref('')
function refreshAssistCard() {
  try {
    const raw = JSON.parse(localStorage.getItem('harven_assist_hist_main') || '[]')
    const log = Array.isArray(raw) ? raw.filter(x => x && x.role && typeof x.text === 'string') : []
    const last = log[log.length - 1]
    if (last && last.ts) {
      const d = new Date(last.ts)
      assistLastTime.value = `${d.getMonth() + 1}-${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
    } else {
      assistLastTime.value = log.length ? '更早' : ''
    }
    assistRecent.value = log.slice(-3).reverse().map(m => {
      const plain = String(m.text).replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim()
      return { role: m.role, preview: plain.length > 42 ? plain.slice(0, 42) + '…' : plain }
    })
  } catch (e) { assistRecent.value = []; assistLastTime.value = '' }
}
function openAssist() {
  /* ★ 2026-10-25 #1184 入口收纳：哈文助手卡 → 直达管家中心（待确认收纳盒已统一收进去） */
  router.push('/butler')
}

/* ═══ ★ 2026-09-16 开发者需求：面板总页也挂哈文助手（解释模板/推荐安装/跨面板记录） ═══ */
let assistInst = null
function mountPanelsAssist() {
  if (!window.PanelAssist) return  // panel-assist.js 未加载时静默（老包兼容）
  // 模板目录作为 pageContext 传给后端，"XX模板是干嘛的/该装哪个"据此回答，不编造
  const tplBrief = (TPL || []).map(t =>
    `- 「${t.name}」：${t.desc || ''}（记录字段：${(t.fields || []).map(f => f.name || f).join('、')}）`
  ).join('\n')
  assistInst = window.PanelAssist.mount({
    host: document.body, panelId: '', mode: 'home',
    title: '哈文助手 · 面板中心',
    hintText: '想记什么直接说（自动进对应模块）；问「XX模块是干嘛的」「该装哪个」，哈文帮你挑',
    pageContext: () => '当前页面：哈文面板中心（模块库+我的模块）。\n【可安装的模块目录】\n' + tplBrief,
  })
}

onMounted(() => {
  load(true).then(() => {
    /* ★ 2026-09-20 布局改版：已有模块时模块库默认收起，一打开就先看到「我的模块」 */
    if (libOpen.value === null) libOpen.value = panels.value.length === 0
  })
  timer = setInterval(() => { load(false); refreshAssistCard() }, 20000) /* ★ 20s 顺带刷新助手入口卡 */
  refreshAssistCard()
  /* ★ #1220-A 数据版本变化 → 立即刷新（不等 20s 轮询点） */
  window.addEventListener('harven:sync', load)
  nextTick(mountPanelsAssist)
})
onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('harven:sync', load)
  if (assistInst && assistInst.destroy) { try { assistInst.destroy() } catch (e) { /* 静默 */ } assistInst = null }
})
</script>

<style scoped>
/* ★ 2026-09-20 开发者二轮反馈：面板中心撤掉 1280 限宽改满屏（跟家庭模板/首页一致）；仅禁横向溢出 */
.pshell :deep(.pshell-body) { width: 100%; overflow-x: hidden; }
.err-tip { margin: 0 0 12px; padding: 10px 14px; background: #FDF2F2; border: 1px solid #F5C6C6; border-radius: 10px; color: #B4443C; font-size: 13px; }
.ok-tip { margin: 0 0 12px; padding: 10px 14px; background: var(--mode-local-bg); border-radius: 10px; color: var(--mode-local); font-size: 13px; }
/* ★ 2026-09-26 哈文助手入口卡：最近对话 + 点击弹开浮窗 */
.assist-card { margin: 0 0 14px; padding: 13px 16px; border: 1px solid var(--line); border-radius: 13px; background: #fff; cursor: pointer; transition: border-color .15s, transform .15s; }
.assist-card:hover { border-color: #58a6ff; transform: translateY(-1px); }
.ac-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.ac-title { font-weight: 700; font-size: 14px; }
.ac-time { font-size: 12px; color: #8a8f99; }
.ac-open { margin-left: auto; font-size: 12px; color: #58a6ff; font-weight: 600; }
.ac-msgs { display: flex; flex-direction: column; gap: 4px; }
.ac-msg { display: flex; align-items: baseline; gap: 8px; font-size: 13px; line-height: 1.5; min-width: 0; }
.ac-who { flex: 0 0 auto; font-size: 12px; color: #58a6ff; }
.ac-who.me { color: #e8853d; }
.ac-text { color: #555; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ac-empty { font-size: 12px; color: #9aa0aa; }

/* 分类区（★ 2026-09-20 自适应列数）
   ★ #1032（2026-10-06）模板卡放大第一轮（280px）
   ★ #1038（2026-10-06）第二轮真大卡：280→420 宽、图标 36、模板名 21px——模板是门面，
   280px 在宽屏下摊开仍显小（实测反馈），按门面地位给足尺寸 */
.cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; }
/* 快速筛选 chips（大改版后分类卡点击=进空间页，这里保留就地筛选能力） */
.quick-filter { display: flex; flex-wrap: wrap; gap: 7px; margin: -4px 0 12px; }
.qf-chip { border: 1px solid var(--line); background: #fff; border-radius: 20px; font-size: 12px; padding: 3px 13px; cursor: pointer; color: var(--c-text); }
.qf-chip.on { background: var(--blue); border-color: var(--blue); color: #fff; font-weight: 700; }
.cat-card { position: relative; text-align: left; border: 1px solid var(--line); border-radius: 14px; background: #fff; padding: 24px 24px 20px 28px; cursor: pointer; display: flex; flex-direction: column; gap: 7px; transition: border-color .15s, transform .15s, box-shadow .15s; overflow: hidden; }
.cat-card::before { content: ''; position: absolute; left: 0; top: 16px; bottom: 16px; width: 5px; border-radius: 0 4px 4px 0; background: transparent; transition: background .15s; }
.cat-card:hover { transform: translateY(-3px); box-shadow: 0 6px 18px rgba(30, 90, 180, .10); }
.cat-card:hover::before { background: var(--blue); }
.cat-card.on { border-color: var(--blue); border-width: 2px; padding: 23px 23px 19px 27px; }
.cat-card.on::before { background: var(--blue); }
.cat-ic { font-size: 36px; line-height: 1.2; }
.cat-name { font-weight: 700; font-size: 21px; }
.cat-desc { font-size: 13.5px; color: var(--sub); }
.cat-count { font-size: 13px; color: var(--blue); font-weight: 600; margin-top: 6px; }
.cat-del { position: absolute; top: 7px; right: 9px; font-size: 15px; color: var(--sub); border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; }
.cat-del:hover { color: var(--red); background: #FDF2F2; }
.cat-add { border-style: dashed; color: var(--sub); align-items: center; justify-content: center; font-size: 26px; min-height: 150px; }
.cat-add .cat-name { color: var(--blue); font-size: 17px; }
.cat-add .cat-desc { font-size: 13px; }
.restore-line { display: flex; align-items: center; gap: 10px; margin: -4px 0 10px; font-size: 12px; color: var(--sub); }
.restore-line .mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 3px 11px; cursor: pointer; color: var(--blue); }
.restore-line .mini:hover { border-color: var(--blue); }
/* ★ 2026-09-19 被移出的模块恢复区 */
.removed-line { flex-wrap: wrap; gap: 7px; padding: 8px 12px; border: 1px dashed #BBD4F7; background: #F5F9FF; border-radius: 11px; }
.removed-line .rl-label { font-weight: 600; color: var(--c-text); }
.rm-chip { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; background: #fff; border: 1px solid var(--line); border-radius: 20px; padding: 2px 10px; }
.rm-chip .rm-cat { font-style: normal; color: var(--sub); font-size: 11px; }
.rm-chip .rm-restore { color: var(--blue); font-weight: 700; cursor: pointer; }
.rm-chip .rm-restore:hover { text-decoration: underline; }
/* ★ 2026-09-16 层级分类：父卡内的子分类 chips */
.cat-subrow { display: flex; flex-wrap: wrap; gap: 4px; }
.cat-sub { display: inline-flex; align-items: center; gap: 3px; font-size: 11px; background: var(--c-bg-soft); border-radius: 14px; padding: 1px 8px; color: var(--c-text); cursor: pointer; }
.cat-sub:hover { background: #F3F7FE; color: var(--blue); }
.cat-sub-x { font-style: normal; font-weight: 700; color: var(--sub); }
.cat-sub-x:hover { color: var(--red); }
/* ★ 2026-09-16 管理入口移到顶部工具栏（用户反馈小字找不到） */
.sec-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.sec-row .sec { margin: 22px 0 12px; }
.manage-top { background: #FFF7ED !important; border: 1px solid #F5B04C !important; color: #B45309 !important; font-weight: 600; }
.manage-top:hover { background: #FFEDD5 !important; border-color: #F59E0B !important; }
/* ★ 管理弹层 */
.manage-modal { width: min(720px, 96vw); }.m-sub { font-weight: 400; font-size: 12px; color: var(--sub); margin-left: 6px; }
.manage-body { padding: 12px 16px 16px; overflow-y: auto; }
.manage-tip { font-size: 12px; color: var(--sub); background: var(--c-bg-soft); border-radius: 9px; padding: 8px 12px; margin-bottom: 10px; line-height: 1.6; }
/* ★ #1130 Hub 安装模态 */
.hub-row { display: flex; gap: 9px; }
.hub-id { flex: 1; border: 1px solid var(--line); border-radius: 9px; padding: 8px 11px; font-size: 13px; background: var(--c-bg); color: var(--text); }
.hub-id:focus { outline: 2px solid var(--blue); border-color: transparent; }
/* ★ #1135 随身记 */
/* ★ 2026-10-25 #1184：inbox-badge 角标样式已随随身记按钮撤除 */
.mg-suggest { margin-left: 8px; color: var(--blue); font-style: normal; }
.mg-row { display: flex; align-items: center; gap: 11px; padding: 8px 2px; border-bottom: 1px dashed var(--line); }
.mg-ic { width: 34px; height: 34px; border-radius: 9px; background: var(--c-bg-soft); display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
.mg-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.mg-info b { font-size: 13.5px; }
.mg-meta { font-size: 11.5px; color: var(--sub); }
.mg-warn { font-style: normal; color: var(--red); }
.mg-level { border: 1px solid var(--line); border-radius: 8px; font-size: 12px; padding: 4px 8px; background: #fff; color: var(--ink); flex-shrink: 0; }

.sec { display: flex; align-items: baseline; gap: 8px; font-size: 15px; margin: 16px 0 10px; }
.sec small { font-size: 12px; color: var(--sub); font-weight: 400; }
.empty { padding: 26px; text-align: center; color: var(--sub); font-size: 13px; background: var(--c-bg-soft); border-radius: 12px; }
.empty a { color: var(--blue); font-weight: 600; }

/* 面板卡墙（★ 2026-09-20 自适应 3 列） */
.panel-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 10px; }

/* ★ 分组宫格：组头可点折叠，折叠状态存 localStorage */
.pgroup { margin-bottom: 14px; }
/* ★ #1120（2026-10-13）拖拽换组高亮：目标组虚线框 + 标题染色 */
.pgroup.group-over { outline: 2px dashed var(--blue); outline-offset: 3px; border-radius: 10px; }
.pgroup.group-over .pg-head { background: #F5F9FF; }
.pg-head { display: flex; align-items: center; gap: 8px; padding: 8px 10px; margin-bottom: 8px; border-radius: 10px; background: var(--panel, #F4F6FA); cursor: pointer; user-select: none; }
.pg-head:hover { background: var(--panel-2, #EBEFF5); }
.pg-head b { font-size: 14px; color: var(--c-text, #2B2F36); }
.pg-arrow { font-size: 11px; color: var(--c-text-2, #8A93A2); width: 14px; }
.pg-n { font-size: 12px; color: var(--c-text-2, #8A93A2); }

/* ★ E 批模板库（★ 2026-09-20 自适应 3 列 + 折叠开关） */
.lib-head .sec { margin: 14px 0 8px; }
.lib-toggle { align-self: center; }
.tpl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 9px; }
.tcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 10px 12px; display: flex; gap: 10px; align-items: center; }
.tcard:hover { border-color: var(--blue); }
.t-ic { width: 40px; height: 40px; border-radius: 10px; background: var(--c-bg-soft); display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; }
.t-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.t-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.t-head b { font-size: 13.5px; }
.t-group { font-size: 10px; font-weight: 800; border-radius: 6px; padding: 1px 7px; }
.t-group.home { background: var(--mode-local-bg); color: var(--mode-local); }
.t-group.work { background: var(--mode-cloud-bg); color: var(--mode-cloud); }
.t-count { font-size: 11px; color: var(--green); font-weight: 700; }
.t-desc { font-size: 11.5px; color: var(--sub); }
.t-fields { display: flex; flex-wrap: wrap; gap: 4px; }
.t-fields .chip { font-size: 10px; padding: 1px 7px; }
.pcard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 14px; display: flex; gap: 12px; align-items: flex-start; cursor: grab; }
.pcard:active { cursor: grabbing; }
.p-ic { width: 44px; height: 44px; border-radius: 11px; background: var(--c-bg-soft); display: flex; align-items: center; justify-content: center; font-size: 22px; flex-shrink: 0; }
.p-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.p-info b { font-size: 14px; }
.p-desc { font-size: 12px; color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.p-meta { font-size: 11px; color: var(--sub); }
.p-btns { display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; align-items: stretch; }
.p-btns .mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 11px; cursor: pointer; color: var(--c-text); }
.p-btns .mini.pinned { border-color: var(--blue); color: var(--blue); font-weight: 700; background: #F3F7FE; }
.p-btns .mini.danger:hover { border-color: var(--red); color: var(--red); }
.p-agents { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; align-items: center; }
.p-agent-chip { font-size: 11px; background: var(--mode-cloud-bg); color: var(--mode-cloud); border-radius: 20px; padding: 2px 9px; display: inline-flex; align-items: center; gap: 4px; }
.pa-x { font-style: normal; cursor: pointer; font-weight: 700; }
.pa-x:hover { color: var(--red); }
.p-agent-tip { font-size: 10.5px; color: var(--sub); }

/* 审批条 */
.approve-bar { margin-top: 14px; border: 1px solid #F0D8A8; background: #FDF8EE; border-radius: 13px; padding: 12px 14px; }
.ab-title { display: flex; align-items: center; gap: 7px; font-weight: 700; font-size: 13.5px; color: #9A6B1F; margin-bottom: 9px; }
.ab-item { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-top: 1px dashed #EAD9B5; }
.ab-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ab-action { font-size: 12.5px; font-weight: 700; color: #7A5518; }
.ab-params { font-size: 11.5px; color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ab-time { font-size: 11px; color: var(--sub); }
.ab-btns { display: flex; gap: 8px; flex-shrink: 0; }

/* Agent 卡墙（★ 2026-09-20 自适应 2~3 列） */
.agent-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 10px; }
.a-builtin-hint { margin-top: 9px; padding: 7px 10px; background: var(--c-bg-soft); border-radius: 9px; font-size: 11.5px; color: var(--c-text-2); }
/* ★ 2026-09-13 三层管理样式 */
.a-lock { font-size: 13px; margin-left: 2px; }
.a-tier { margin-top: 9px; padding: 7px 10px; border-radius: 9px; font-size: 11.5px; }
.a-tier.core { background: #FDF6EC; color: #9A6B1F; border: 1px dashed #E5C07B; }
.a-tier.soft { background: var(--c-bg-soft); color: var(--c-text-2); }
.a-btns { margin-top: 8px; display: flex; gap: 8px; }
.a-toggle { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 5px 13px; cursor: pointer; color: var(--c-text-1); }
.a-toggle:hover { border-color: var(--amber); background: var(--amber-soft); }
.a-toggle.on { color: #1E7F4F; }
.a-toggle:disabled { opacity: .55; cursor: wait; }
.a-badge.m-disabled { background: #EEEFF2; color: #8A9099; }
.acard.disabled { opacity: .62; }
.a-del { margin-top: 9px; border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 5px 13px; cursor: pointer; color: var(--red); }
.a-del:hover { background: #FDF2F2; border-color: var(--red); }
.acard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 13px 14px; cursor: grab; }
.acard:active { cursor: grabbing; }
.acard.open { border-color: var(--blue); box-shadow: var(--shadow-card); }
.a-head { display: flex; align-items: center; gap: 8px; }
.a-name { font-size: 14.5px; }
.a-badge { font-size: 11px; padding: 1px 8px; border-radius: 20px; }
.a-badge.m-local { background: var(--mode-local-bg); color: var(--mode-local); }
.a-badge.m-external { background: var(--mode-cloud-bg); color: var(--mode-cloud); }
.a-badge.m-vertical { background: var(--mode-vertical-bg); color: var(--mode-vertical); }
.a-count { margin-left: auto; font-size: 11px; color: var(--sub); }
.a-fold { color: var(--sub); }
.a-role { font-size: 12px; color: var(--sub); margin: 4px 0 7px; }
.a-trigs { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 11px; background: var(--c-bg-soft); border-radius: 20px; padding: 2px 9px; color: var(--c-text); }
.chip.more { color: var(--blue); font-weight: 700; }
.a-detail { margin-top: 10px; border-top: 1px dashed var(--line); padding-top: 8px; }
.kv { display: flex; justify-content: space-between; gap: 10px; font-size: 12.5px; padding: 5px 0; border-bottom: 1px dashed var(--line); }
.kv:last-of-type { border-bottom: none; }
.kv .k2 { color: var(--sub); }
.kv .v2 { font-weight: 600; text-align: right; }
.v2.ok { color: var(--green); }
.a-alltrigs { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 0 2px; }
.a-hint { margin-top: 8px; font-size: 11.5px; color: var(--sub); background: var(--c-bg-soft); border-radius: 8px; padding: 7px 10px; line-height: 1.6; }

/* 新建面板弹层 */
.modal-mask { position: fixed; inset: 0; background: rgba(20, 26, 38, .4); z-index: 80; display: flex; align-items: center; justify-content: center; }
.modal { width: min(520px, 94vw); max-height: 88vh; overflow-y: auto; background: #fff; border-radius: 14px; }
.m-head { display: flex; justify-content: space-between; align-items: center; padding: 13px 16px; border-bottom: 1px solid var(--line); position: sticky; top: 0; background: #fff; }
.m-form { padding: 16px; display: flex; flex-direction: column; gap: 11px; }
.m-form label { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--sub); }
.m-form input, .m-form select { border: 1px solid var(--line); border-radius: 9px; padding: 8px 11px; font-size: 13px; outline: none; background: #fff; color: var(--ink); }
.m-form input:focus, .m-form select:focus { border-color: var(--blue); }
.chk-line { flex-direction: row !important; align-items: center; gap: 7px !important; }
.nf-fields { border: 1px solid var(--line); border-radius: 11px; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.nff-head { display: flex; justify-content: space-between; align-items: center; font-size: 12.5px; color: var(--sub); }
.nff-row { display: grid; grid-template-columns: 1fr 92px 40px; gap: 7px; }
.mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 11px; cursor: pointer; color: var(--ink); flex-shrink: 0; }
.mini.danger { color: var(--red); }
.mini.danger:hover { border-color: var(--red); }

@media (max-width: 1000px) { .cat-grid { grid-template-columns: repeat(2, 1fr); } .tpl-grid, .panel-grid { grid-template-columns: 1fr 1fr; } .agent-grid { grid-template-columns: 1fr; } }
@media (max-width: 700px) { .tpl-grid, .panel-grid { grid-template-columns: 1fr; } }
/* ★ D3b 2026-09-23 手机适配：区块标题行换行（此前「模块库/我的模块」标题被 flex 压成逐字竖排） */
@media (max-width: 700px) {
  .sec { flex-wrap: wrap; row-gap: 2px; }
  .sec small { flex: 1 1 100%; white-space: normal; line-height: 1.5; }
}
/* ★ D3 2026-09-23 手机适配：窄屏模块卡换行，「打开/停靠」按钮独占一行常驻可见（此前被长描述挤出屏外） */
@media (max-width: 700px) {
  .pcard { flex-wrap: wrap; }
  .p-desc, .p-meta { white-space: normal; word-break: break-all; }
  .p-btns { width: 100%; flex-direction: row; margin-top: 8px; padding-top: 9px; border-top: 1px dashed var(--line); }
  .p-btns > * { flex: 1; }
  .p-btns .mini { padding: 7px 11px; }
}
</style>
