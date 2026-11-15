<template>
  <PageShell title="管家中心" icon="task" sub="问管家 · 待确认 · 今日议程 · 任务 · 目标 · 提醒 · 随手记，一页管完">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadAll(true)"><HIcon name="dots" :size="16" /> 全部刷新</BigButton>
    </template>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <!-- ⓪ 问管家：直连哈文问答引擎（和首页同一管线，SSE 流式；#1190） -->
    <section class="sec">
      <div class="sec-h">💬 问管家</div>
      <div ref="bqaBox" class="bqa-box" @click="bubbleClick">
        <div v-if="!bqaList.length" class="empty-s">
          直接问，比如「这周花了多少」「明天有什么事」——答案和首页聊天同一个大脑。
        </div>
        <div v-for="(m, i) in bqaList" :key="i" class="bqa-msg" :class="m.role">
          <!-- ★ #1213 【打开:/xxx】标记渲染成跳转按钮（以前标记原样进气泡点不开）；事件经容器委托 -->
          <div class="bqa-bubble" v-html="renderBubble(m)"></div>
          <div v-if="m.role === 'assistant' && m.replyBy" class="bqa-by">{{ m.replyBy }}</div>
        </div>
      </div>
      <div class="bqa-chips">
        <button v-for="q in BQA_QUICK" :key="q" class="mini" :disabled="bqaSending" @click="bqaSend(q)">{{ q }}</button>
      </div>
      <div class="bqa-input">
        <input v-model="bqaText" :placeholder="bqaSending ? '管家正在想…（可继续打字）' : '问管家一句话…'"
               @keyup.enter="bqaSend()" />
        <button class="mini pri" :disabled="bqaSending || !bqaText.trim()" @click="bqaSend()">发送</button>
      </div>
    </section>

    <!-- ① 待确认收纳盒：哈文识别出来等你把关的记录（原鸭子浮球收纳盒迁入，#1184） -->
    <section class="sec">
      <div class="sec-h">📥 待确认收纳盒<span v-if="pending.length" class="sec-n">{{ pending.length }}</span>
        <button v-if="billPendingCount" class="mini pri pend-all" @click="pendConfirmAll">💰 全部入账（{{ billPendingCount }}）</button>
        <button v-if="pending.length" class="mini" title="没入库也没忽略的卡全部丢掉（已入库数据不受影响）" @click="pendClearAll">🗑 清空</button>
      </div>
      <div v-if="!pending.length" class="empty-s">没有等你把关的记录。在聊天里说「记一笔 XX」，哈文识别后先放这里。</div>
      <div v-for="it in pending" :key="it.id" class="pend">
        <div class="pend-src">🗣 {{ it.source_text }} · {{ (it.created_at || '').slice(0, 16) }}<b v-if="panelName(it.panel_id)" class="pend-p"> → {{ panelName(it.panel_id) }}</b></div>
        <div v-for="(r, ri) in (it.records || [])" :key="ri" class="pend-rec">
          <template v-for="(v, k) in r" :key="k"><span v-if="String(v ?? '').trim()">{{ k }}:{{ v }}</span></template>
        </div>
        <div class="pend-ops">
          <button class="mini pri" @click="pendConfirm(it)">入库</button>
          <button class="mini" @click="pendIgnore(it)">忽略</button>
        </div>
      </div>
    </section>

    <!-- ② 今日议程：管家盯出来Today的事（urge 红、info 灰） -->
    <section class="sec">
      <div class="sec-h">
        🛎️ 今日议程
        <span v-if="btData && btData.total" class="sec-n" :class="{ urge: btData.urge_count > 0 }">{{
          btData.urge_count > 0 ? btData.urge_count + ' 件要紧' : btData.total + ' 件事' }}</span>
        <span v-if="btData && btData.quiet" class="quiet-tag">😴 勿扰时段：不出声，照常显示</span>
      </div>
      <div v-for="g in btGroups" :key="g.panel_id" class="ag-group">
        <div class="ag-name" :class="{ link: g.panel_id !== '_butler' }"
             :title="g.panel_id !== '_butler' ? '打开面板' : ''"
             @click="g.panel_id !== '_butler' && $router.push('/panel/' + g.panel_id)">
          {{ g.name }}<i>{{ g.items.length }}</i>
        </div>
        <div v-for="it in g.items" :key="it.id" class="ag-item" :class="it.level">
          <span class="ag-t">{{ it.title }}</span>
          <span class="ag-ops">
            <!-- ★ #1508 目标验收卡：结项/延期一周/按实际（后端 goal_* op 全本地 0 token） -->
            <template v-if="it.kind === 'goal_audit'">
              <button class="mini" title="认定完成：节点记 done，后续节点照常推进" @click="btAct(it, 'goal_pass')">✓ 结项</button>
              <button class="mini" title="本节点及后续整体顺延 7 天" @click="btAct(it, 'goal_defer')">延期一周</button>
              <button class="mini" title="把目标降到实际完成数并结项——不达白不达，别硬扛" @click="btAct(it, 'goal_keep')">按实际</button>
              <button class="mini" title="不用管了" @click="btAct(it, 'dismiss')">忽略</button>
            </template>
            <template v-else>
              <button v-if="g.panel_id && g.panel_id.startsWith('fridge') && it.kind === 'expiry' && it.ref_row > 0 && it.title.includes('已过期')"
                      class="mini" title="记「处理掉了」：台账留痕可撤销，买菜清单自动跟进" @click="btConsume(it)">🗑 已清掉</button>
              <button class="mini" title="办完" @click="btAct(it, 'done')">✓ 办完</button>
              <button class="mini" title="缓到明天" @click="btAct(it, 'snooze')">缓</button>
              <button class="mini" title="不用管了" @click="btAct(it, 'dismiss')">忽略</button>
            </template>
          </span>
        </div>
      </div>
      <div v-if="!btData || !btData.total" class="empty-s">今天议程空空，都办妥了 ✨</div>
    </section>

    <!-- ③ 任务（原任务页整体并入，/tasks 重定向到本页） -->
    <section class="sec">
      <div class="sec-h">
        ✅ 任务
        <button v-for="f in FILTERS" :key="f.id" class="fbtn" :class="{ on: tFilter === f.id }" @click="tFilter = f.id">
          {{ f.name }} <i>{{ countBy(f.id) }}</i>
        </button>
        <button class="mini pri" style="margin-left:auto" @click="showNew = true">＋ 新任务</button>
      </div>
      <div v-if="!tFiltered.length" class="empty-s">这里还没有任务</div>
      <div v-for="t in tFiltered" :key="t.id" class="tcard" :class="{ done: t.status === 'done' }">
        <button class="chk" :title="t.status === 'done' ? '标记为未完成' : '标记完成'" @click="toggleDone(t)">
          <HIcon v-if="t.status === 'done'" name="check" :size="13" />
        </button>
        <div class="t-main">
          <b class="t-title">{{ t.title }}</b>
          <span v-if="t.goal_title" class="goal-tag">{{ t.goal_title }}</span>
          <span v-if="t.content" class="t-desc">{{ t.content }}</span>
          <div class="t-meta">
            <span class="pri-t" :class="'p-' + t.priority">{{ PRI_TEXT[t.priority] || '普通' }}</span>
            <span>{{ (t.created_at || '').slice(0, 16) }}</span>
            <span v-if="t.progress > 0">进度 {{ t.progress }}%</span>
          </div>
          <div v-if="t.status !== 'done'" class="prog">
            <div class="prog-bar"><i :style="{ width: (t.progress || 0) + '%' }" /></div>
            <input class="prog-input" type="number" min="0" max="100" :value="t.progress || 0" @change="setProgress(t, $event)" />
          </div>
        </div>
        <button class="mini danger" @click="delTask(t)">删除</button>
      </div>
    </section>

    <!-- ④ 目标：进度卡墙（详细管理与新建在目标页） -->
    <section class="sec">
      <div class="sec-h">
        🎯 目标<span v-if="goals.length" class="sec-n">{{ goals.length }}</span>
        <button class="mini" style="margin-left:auto" @click="$router.push('/goals')">目标页管理 →</button>
      </div>
      <div v-if="!goals.length" class="empty-s">还没有进行中的目标。去目标页立一个。</div>
      <div class="goal-cards">
        <div v-for="g in goals" :key="g.id" class="gcard" @click="$router.push('/goals')">
          <span class="g-light">{{ g.light }}</span>
          <b class="g-name">{{ g.title }}</b>
          <div class="g-bar"><i :style="{ width: (g.progress || 0) + '%' }" /></div>
          <span class="g-meta">{{ g.done }}/{{ g.total }}<template v-if="g.next_node"> · {{ g.next_node.name }} {{ g.next_node.due }}</template></span>
        </div>
      </div>
    </section>

    <!-- ⑤ 提醒（★ #1333：对话提醒 + 面板提醒一区看全——面板提醒走 /api/assist/alerts（浮球角标同源），
         修复「浮球喊有 1 件、管家中心啥也没有」的两条数据线不同步问题） -->
    <section class="sec">
      <div class="sec-h">⏰ 提醒<span v-if="alertsTotal" class="sec-n">{{ alertsTotal }}</span></div>
      <div v-for="a in panelAlerts" :key="String(a.id)" class="inbox-row">
        <div class="inbox-main">
          <div class="inbox-ct">{{ a.title }}</div>
          <div class="inbox-meta"><em class="inbox-sug">{{ a.srcName }}</em><em v-if="a.remind_at" class="inbox-sug">⏰ {{ a.remind_at }}</em></div>
        </div>
        <button v-if="a.link" class="mini pri" @click="alertGo(a)">去处理</button>
        <button class="mini" @click="alertAck(a)">知道了</button>
      </div>
      <div v-for="r in reminders" :key="'r' + r.id" class="inbox-row">
        <div class="inbox-main">
          <div class="inbox-ct">{{ r.title }}</div>
          <div class="inbox-meta">⏰ {{ r.remind_at }}<em v-if="r.repeat && r.repeat !== 'once'" class="inbox-sug">重复:{{ r.repeat }}</em><em v-else-if="(r.source || '') === 'inbox'" class="inbox-sug">来自随身记</em></div>
        </div>
        <button class="mini danger" @click="reminderDel(r)">删</button>
      </div>
      <div v-if="!alertsTotal" class="empty-s">没有待响的提醒。对话框说「后天提醒我XX」就能设。</div>
    </section>

    <!-- ⑥ 随手记（原右栏弹层 / 面板中心顶栏按钮统一收进来） -->
    <section class="sec">
      <div class="sec-h">📝 随手记<span v-if="inboxCount" class="sec-n">{{ inboxCount }}</span></div>
      <div class="inbox-addrow">
        <input v-model="inboxDraft" class="inbox-input" placeholder="随手记一条…（回车提交）" @keyup.enter="inboxAdd" />
        <button class="mini pri" @click="inboxAdd">记</button>
      </div>
      <div v-for="n in inboxNotes" :key="n.id" class="inbox-row">
        <div class="inbox-main">
          <div class="inbox-ct">{{ n.content }}</div>
          <div class="inbox-meta">{{ (n.created_at || '').slice(0, 16) }}<em v-if="n.target" class="inbox-sug">像「{{ n.target }}」的事</em><em v-if="n.remind_at" class="inbox-sug">⏰ {{ n.remind_at }}</em></div>
        </div>
        <button class="mini" @click="inboxDone(n)">办完</button>
        <button class="mini danger" @click="inboxDel(n)">删</button>
      </div>
      <div v-if="!inboxNotes.length" class="empty-s">空的。对话框说「记一下XX」也能存进来。</div>
    </section>

    <!-- 新建任务弹层 -->
    <div v-if="showNew" class="modal-mask" @click.self="showNew = false">
      <div class="modal">
        <div class="m-head"><b>新建任务</b><button class="mini" @click="showNew = false">关闭</button></div>
        <div class="m-form">
          <label>要做什么<textarea v-model.trim="nf.title" rows="2" placeholder="例如：周三之前给车做保养" /></label>
          <label>补充说明（可不填）<textarea v-model.trim="nf.content" rows="2" /></label>
          <label>重要程度
            <select v-model="nf.priority">
              <option value="low">不着急</option>
              <option value="medium">普通</option>
              <option value="high">比较急</option>
              <option value="urgent">非常急</option>
            </select>
          </label>
          <BigButton :px="14" :loading="creating" @click="createTask"><HIcon name="check" :size="15" /> 建好了</BigButton>
        </div>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
// ★ 2026-10-25 #1183 管家中心：桌面端管家统一入口（与手机管家抽屉同源 API）
//   五区 = 待确认收纳盒 + 今日议程 + 任务 + 目标 + 随手记
//   入口收纳 #1184：/tasks 重定向本页；随身记按钮收拢；鸭子浮球点击直达本页
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { tryNavigate } from '../navCommands.js'
import PageShell from '../components/PageShell.vue'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON, delJSON } from '../api.js'

const router = useRouter()  // ★ #1213/#1214 卡片跳转 + 「打开XX」导航拦截

const msg = ref('')
const msgOk = ref(false)

/* ══ ⓪ 问管家（#1190）：直连 /api/chat 与首页同一管线，SSE 流式；轻对话不挂会话历史 ══ */
const bqaList = ref([])        // {role:'user'|'assistant', content, replyBy, typing}
const bqaText = ref('')
const bqaSending = ref(false)
const bqaBox = ref(null)
const BQA_QUICK = ['今天花了多少', '今天有什么要办的', '孩子最近的学习记录']

function bqaScroll() {
  requestAnimationFrame(() => {
    if (bqaBox.value) bqaBox.value.scrollTop = bqaBox.value.scrollHeight
  })
}

async function bqaSend(preset) {
  const text = String(preset ?? bqaText.value ?? '').trim()
  if (!text || bqaSending.value) return
  bqaText.value = ''
  bqaSending.value = true
  bqaList.value.push({ role: 'user', content: text })
  // ★ #1214 「打开XX」导航拦截：管家页同首页，前端直接跳转不进聊天管线（以前「打开啊」落 FC 空转 26 秒）
  try {
    const nav = await tryNavigate(text)
    if (nav && nav.handled) {
      bqaList.value.push({ role: 'assistant', content: nav.note || `🧭 已打开「${nav.name}」～`, replyBy: '🧭 页面导航' })
      bqaSending.value = false
      bqaScroll()
      if (nav.path) router.push(nav.path)
      return
    }
  } catch (e) { /* 拦截异常降级为聊天 */ }
  const reply = reactive({ role: 'assistant', content: '', replyBy: '', typing: true })
  bqaList.value.push(reply)
  bqaScroll()
  // ★ 2026-10-25 #1191 根治「打不了字」：输入框永不禁用（SSE 挂流也不锁），防重只锁发送按钮；
  //   90 秒看门狗 + 流动重置——只要还在出字就不掐，彻底卡死最多等 90 秒自动解救
  const ctrl = new AbortController()
  let watchdog = setTimeout(() => ctrl.abort(), 90000)
  const armWatchdog = () => { clearTimeout(watchdog); watchdog = setTimeout(() => ctrl.abort(), 90000) }
  let aborted = false
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // ★ #1212 butler:true：后端关断 SkillDiscovery（问数据不需要搜技能市场）
      body: JSON.stringify({ message: text, query_mode: 'direct', web_search: false, collab_mode: 'off', butler: true }),
      signal: ctrl.signal,
    })
    if (!resp.ok) {
      // ★ #1212 不看状态码实锤：429/500 时 body 是 JSON，零 text 事件 → 假装「没有收到回答」
      reply.content = '⚠️ 服务这会儿没响应（' + resp.status + '），稍后再问一次'
    } else {
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      armWatchdog()
      buf += decoder.decode(value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop()
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        let evt
        try { evt = JSON.parse(line.slice(5)) } catch { continue }
        if (evt.type === 'text') {
          reply.content += evt.content || ''
          bqaScroll()
        } else if (evt.type === 'model_source') {
          const ms = evt.content || {}
          if (ms.label) reply.replyBy = (ms.is_external ? '🧠 ' : '🏠 ') + ms.label
        } else if (evt.type === 'error') {
          reply.content += (reply.content ? '\n' : '') + '⚠️ ' + (evt.content || '出错了，再问一次试试')
        }
      }
    }
    } // ★ #1212 end resp.ok else
  } catch (e) {
    aborted = (e && e.name === 'AbortError')
    reply.content += (reply.content ? '\n' : '')
      + (aborted ? '⚠️ 回答超时（90 秒没出字）。注意：办理类请求（记一下/提醒我）可能已在后台生效，可到下方「提醒/随手记」区确认后再决定要不要重发，避免重复记录；如果反复超时，重启一下哈文' : '⚠️ 连不上了，检查哈文是否在运行')
  }
  clearTimeout(watchdog)
  reply.typing = false
  if (!reply.content) reply.content = '（没有收到回答。注意：办理类请求（记一下/提醒我）可能已在后台生效，可到下方「提醒/随手记」区确认，别重复记）'
  bqaSending.value = false
  bqaScroll()
}
function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 4000)
}

/* ── ★ #1213 聊天气泡渲染：【打开:/xxx】标记 → 可点跳转按钮（同 HomePage #926 通道） ── */
const _JUMP_RE = /【打开:\s*(\/[^\】]+?)\s*\】/g
const _JUMP_LABELS = {
  '/photos': '图片库', '/memory': '记忆相册', '/panels': '面板列表', '/knowledge': '知识库', '/skills': '技能库',
  '/butler': '管家中心', '/tasks': '管家中心', '/mail': '邮箱', '/models': '模型中心', '/settings': '设置',
  '/status': '运行状态', '/timeline': '时间轴', '/hub': '市场', '/profile': '画像',
  '/vertical': '垂直大脑', '/create': '创造区', '/studio': '创作工坊',
}
function _escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}
function renderBubble(m) {
  let html = _escHtml(m.content)
  const re = new RegExp(_JUMP_RE.source, 'g')
  html = html.replace(re, (all, path) => {
    let label = _JUMP_LABELS[path]
    if (!label && path.startsWith('/panel/')) label = '对应面板'
    if (!label) return all  // 白名单外的标记原样保留不跳
    return `<button class="bqa-jump" data-path="${_escHtml(path)}">🚪 打开${_escHtml(label)} →</button>`
  })
  // ★ 2026-11-15 (#1520): 音频文件路径 → 内嵌播放器+下载（同主聊天页 embedChatAudio；路径字符不受 _escHtml 影响）
  html = html.replace(/(\/data\/media\/[A-Za-z0-9_\-\u4e00-\u9fa5.]+\.(?:wav|mp3|m4a|ogg|flac))/g,
    (all, p) => `<span style="display:block;margin:6px 0;"><audio controls preload="metadata" src="${p}" style="width:100%;max-width:420px;height:36px;display:block;margin-bottom:2px;"></audio><a href="${p}" download style="font-size:12px;">⬇️ 下载音频</a></span>`)
  // 气泡 CSS 已有 white-space:pre-wrap，换行原样保留即可，不转 <br> 免得双重换行
  return html + (m.typing ? '<span class="bqa-dots">▍</span>' : '')
}
function bubbleClick(e) {
  const btn = e.target && e.target.closest ? e.target.closest('.bqa-jump') : null
  if (btn && btn.dataset.path) router.push(btn.dataset.path)
}

/* ── ① 待确认收纳盒 ── */
const pending = ref([])
const panelNames = ref({})
function panelName(id) { return panelNames.value[id] || '' }
async function loadPending() {
  try {
    const [pj, panels] = await Promise.all([
      fetch('/api/assist/pending').then(r => r.json()),
      getJSON('/api/panels').catch(() => ({})),
    ])
    pending.value = pj.items || []
    const m = {}
    for (const p of (panels.panels || [])) m[p.id] = p.name
    panelNames.value = m
  } catch { pending.value = [] }
}
async function pendConfirm(it) {
  try {
    const r = await fetch(`/api/assist/pending/${it.id}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    const j = await r.json()
    flash(j.message || (j.success ? '已入库' : '入库失败'), !!j.success)
    await loadPending()
  } catch (e) { flash('入库失败：' + e.message, false) }
}
async function pendIgnore(it) {
  try { await fetch(`/api/assist/pending/${it.id}`, { method: 'DELETE' }); await loadPending() }
  catch (e) { flash('操作失败：' + e.message, false) }
}
/* ★ S1 三小活（2026-11-12）一键清空：双确认防手滑，只删未确认卡 */
async function pendClearAll() {
  if (!pending.value.length) return
  if (!confirm(`清空全部 ${pending.value.length} 张待确认卡？\n入库和忽略都没点的会全部丢掉，已入库的数据不受影响。`)) return
  if (!confirm('再确认一次：真的全部清空？')) return
  try {
    const r = await fetch('/api/assist/pending/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    const j = await r.json()
    flash(j.success ? `已清空 ${j.cleared} 张` : '清空失败', !!j.success)
    await loadPending()
  } catch (e) { flash('清空失败：' + e.message, false) }
}
/* ★ #1377（2026-11-02）账单待确认卡批量入库：心跳/手动分析多张票时逐张点太累 */
const billPendingCount = computed(() => pending.value.filter(i => i.panel_id === 'accounting_daily_1').length)
const pendAllBusy = ref(false)
async function pendConfirmAll() {
  if (pendAllBusy.value) return
  const bills = pending.value.filter(i => i.panel_id === 'accounting_daily_1')
  if (!bills.length) return
  if (!confirm(`把 ${bills.length} 张账单待确认卡全部入库？`)) return
  pendAllBusy.value = true
  let ok = 0
  for (const it of bills) {
    try {
      const r = await fetch(`/api/assist/pending/${it.id}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      const j = await r.json()
      if (j.success) ok++
    } catch (e) { /* 单张失败继续下一张 */ }
  }
  pendAllBusy.value = false
  flash(`已入库 ${ok}/${bills.length} 张账单卡`, ok === bills.length)
  await loadPending()
}

/* ── ② 今日议程 ── */
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

/* ★ #1376（2026-11-02 定版拍板）冰箱过期条「已清掉」：走消耗落点（留痕可撤销），顺手把议程打钩 */
async function btConsume(it) {
  try {
    const r = await fetch('/api/panels/fridge/consume', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: it.ref_row, action: 'trashed' }),
    }).then(x => x.json())
    // 幂等/已处理过也照样把议程打钩，不让提醒条赖着
    await fetch('/api/butler/action', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: it.id, op: 'done' }),
    })
    flash(r.success ? (r.message || '已记「处理掉了」') : (r.error || '没记上，议程已打钩'), r.success)
    btRefresh()
  } catch (e) { flash('操作失败：' + e.message, false) }
}

/* ── ③ 任务 ── */
const FILTERS = [
  { id: 'all', name: '全部' },
  { id: 'pending', name: '待办' },
  { id: 'doing', name: '进行中' },
  { id: 'done', name: '已完成' },
]
const PRI_TEXT = { low: '不急', medium: '普通', high: '较急', urgent: '紧急' }
const tasks = ref([])
const tFilter = ref('all')
const showNew = ref(false)
const creating = ref(false)
const nf = reactive({ title: '', content: '', priority: 'medium' })
const tFiltered = computed(() => tFilter.value === 'all' ? tasks.value : tasks.value.filter(t => t.status === tFilter.value))
function countBy(id) {
  return id === 'all' ? tasks.value.length : tasks.value.filter(t => t.status === id).length
}
async function loadTasks() {
  try {
    tasks.value = await getJSON('/api/tasks')
    if (!Array.isArray(tasks.value)) tasks.value = []
  } catch { /* 静默 */ }
}
async function createTask() {
  if (!nf.title) { flash('先写要做什么哦', false); return }
  creating.value = true
  try {
    await postJSON('/api/tasks', { title: nf.title, content: nf.content, priority: nf.priority })
    showNew.value = false
    nf.title = ''; nf.content = ''; nf.priority = 'medium'
    flash('任务已创建')
    await loadTasks()
  } catch (e) { flash('创建失败：' + e.message, false) }
  finally { creating.value = false }
}
async function toggleDone(t) {
  try {
    if (t.status === 'done') await postJSON(`/api/tasks/${t.id}/progress`, { progress: t.progress || 0, status: 'pending' })
    else await postJSON(`/api/tasks/${t.id}/complete`)
    await loadTasks()
  } catch (e) { flash('操作失败：' + e.message, false) }
}
async function setProgress(t, ev) {
  const v = Math.max(0, Math.min(100, Number(ev.target.value) || 0))
  try { await postJSON(`/api/tasks/${t.id}/progress`, { progress: v }); await loadTasks() }
  catch (e) { flash('进度保存失败：' + e.message, false) }
}
async function delTask(t) {
  if (!confirm(`删除任务「${t.title}」？`)) return
  try { await delJSON(`/api/tasks/${t.id}`); flash('已删除'); await loadTasks() }
  catch (e) { flash('删除失败：' + e.message, false) }
}

/* ── ④ 目标 ── */
const goals = ref([])
async function loadGoals() {
  try {
    const r = await fetch('/api/goals?status=active')
    const j = await r.json()
    goals.value = j.goals || []
  } catch { goals.value = [] }
}

/* ── ⑤ 提醒（★ #1217 待响列表 + ★ #1333 面板提醒并入）──
   两源：/api/reminders/pending（对话设的提醒，可删）+ /api/assist/alerts（面板提醒：
   冰箱过期/物品/错题/习惯/老人/健康待办——浮球角标同源， numeric id 与对话提醒去重）；
   面板提醒无后端删除接口，「知道了」= 当天不再显示（localStorage，与收纳盒健康待办同口径） */
const reminders = ref([])
const panelAlerts = ref([])
const alertsTotal = computed(() => panelAlerts.value.length + reminders.value.length)
const ALERT_TYPE_NAME = { fridge: '冰箱台账', storage: '物品存放', mistake: '错题本', habit: '习惯打卡', elder: '老人关怀', health: '健康待办', reminder: '提醒' }
const _alertAckKey = (id) => 'butler_alert_ack_' + id
async function loadReminders() {
  try {
    const r = await fetch('/api/reminders/pending').then(x => x.json())
    reminders.value = (r.reminders || []).slice(0, 20)
  } catch { reminders.value = [] }
  try {
    const j = await fetch('/api/assist/alerts').then(x => x.json())
    const pendIds = new Set(reminders.value.map(r => String(r.id)))
    const today = new Date().toISOString().slice(0, 10)
    panelAlerts.value = (j.reminders || j.items || [])
      .filter(it => {
        if (pendIds.has(String(it.id))) return false /* 对话提醒已在下方单独展示，去重 */
        try { if (localStorage.getItem(_alertAckKey(it.id)) === today) return false } catch (e) { /* 静默 */ }
        return true
      })
      .map(it => ({ ...it, srcName: ALERT_TYPE_NAME[it.type] || (String(it.module || '').replace(/_\d+$/, '')) || '面板' }))
      .slice(0, 20)
  } catch { panelAlerts.value = [] }
}
function alertGo(a) { try { window.location.href = a.link } catch (e) { /* 静默 */ } }
function alertAck(a) {
  try { localStorage.setItem(_alertAckKey(a.id), new Date().toISOString().slice(0, 10)) } catch (e) { /* 静默 */ }
  panelAlerts.value = panelAlerts.value.filter(x => String(x.id) !== String(a.id))
  flash('今天不再显示这条')
}
async function reminderDel(r) {
  try {
    await fetch(`/api/reminders/${r.id}`, { method: 'DELETE' })
    flash('已删除该提醒'); await loadReminders()
  } catch (e) { flash('删除失败：' + e.message, false) }
}

/* ── ⑥ 随手记 ── */
const inboxNotes = ref([])
const inboxCount = ref(0)
const inboxDraft = ref('')
async function inboxRefresh() {
  try {
    const j = await getJSON('/api/inbox/list')
    inboxNotes.value = j.notes || []
    inboxCount.value = j.open_count || 0
  } catch { /* 静默 */ }
}
async function inboxAdd() {
  const c = inboxDraft.value.trim()
  if (!c) return
  try {
    await postJSON('/api/inbox/add', { content: c, source: 'manual' })
    inboxDraft.value = ''
    inboxRefresh()
  } catch { /* 静默 */ }
}
async function inboxDone(n) {
  try { await postJSON('/api/inbox/resolve', { id: n.id, to: n.target || '' }); inboxRefresh() }
  catch { /* 静默 */ }
}
async function inboxDel(n) {
  try { await postJSON('/api/inbox/delete', { id: n.id }); inboxRefresh() }
  catch { /* 静默 */ }
}

/* ── 汇总加载 + 60 秒轮询（议程/收纳盒保持新鲜） ── */
function loadAll(showErr = false) {
  loadPending(); btRefresh(); loadTasks(); loadGoals(); inboxRefresh(); loadReminders()
  if (showErr) flash('已刷新')
}
let pollTimer = null
onMounted(() => {
  loadAll()
  pollTimer = setInterval(() => { btRefresh(); loadPending() }, 60000)
})
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg, #eef7f0); color: var(--mode-local, #1d7a46); }
.tip.bad { background: #FDF2F2; color: #B4443C; }

.sec { border: 1px solid var(--line, #e5e5e5); border-radius: 14px; background: #fff; padding: 14px 16px; margin-bottom: 14px; }
.sec-h { display: flex; align-items: center; gap: 8px; font-size: 14.5px; font-weight: 700; margin-bottom: 10px; flex-wrap: wrap; }
.sec-n { display: inline-block; min-width: 18px; padding: 0 7px; height: 18px; line-height: 18px; border-radius: 9px; background: var(--c-bg-soft, #f2f4f8); color: var(--sub, #8892a6); font-size: 11.5px; text-align: center; font-weight: 700; }
.sec-n.urge { background: #FDECEC; color: #C0392B; }
.quiet-tag { font-size: 11.5px; font-weight: 400; color: var(--sub, #8892a6); }
.empty-s { padding: 16px; text-align: center; color: var(--sub, #8892a6); background: var(--c-bg-soft, #f6f7fa); border-radius: 10px; font-size: 13px; }

/* 待确认 */
.pend { border: 1px dashed var(--line, #e5e5e5); border-radius: 11px; padding: 10px 12px; margin-bottom: 8px; }
.pend-src { font-size: 12px; color: var(--sub, #8892a6); margin-bottom: 5px; }
.pend-p { color: var(--blue, #3b82f6); margin-left: 4px; }
.pend-rec { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12.5px; padding: 2px 0; }
.pend-ops { display: flex; gap: 8px; margin-top: 7px; }

/* 议程 */
.ag-group { margin-bottom: 10px; }
.ag-name { font-size: 12.5px; font-weight: 700; color: var(--sub, #8892a6); margin-bottom: 5px; }
.ag-name.link { color: var(--blue, #3b82f6); cursor: pointer; }
.ag-name i { font-style: normal; margin-left: 5px; font-size: 11px; background: var(--c-bg-soft, #f2f4f8); border-radius: 8px; padding: 0 6px; }
.ag-item { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px; border-radius: 10px; margin-bottom: 5px; background: var(--c-bg-soft, #f6f7fa); }
.ag-item.urge { background: #FDF3F2; }
.ag-t { flex: 1; font-size: 13px; min-width: 0; }
.ag-item.urge .ag-t { font-weight: 700; color: #B4443C; }
.ag-ops { display: flex; gap: 6px; flex-shrink: 0; }

/* 任务 */
.fbtn { border: 1px solid var(--line, #e5e5e5); background: #fff; border-radius: 20px; padding: 4px 12px; font-size: 12px; cursor: pointer; color: var(--sub, #8892a6); font-weight: 400; }
.fbtn i { font-style: normal; color: var(--blue, #3b82f6); font-weight: 700; margin-left: 3px; }
.fbtn.on { border-color: var(--blue, #3b82f6); color: var(--blue, #3b82f6); font-weight: 700; background: #F3F7FE; }
.tcard { border: 1px solid var(--line, #e5e5e5); border-radius: 12px; padding: 11px 13px; display: flex; gap: 11px; align-items: flex-start; margin-bottom: 8px; }
.tcard.done { opacity: .55; }
.chk { width: 22px; height: 22px; border-radius: 7px; border: 1.6px solid var(--line, #e5e5e5); background: #fff; cursor: pointer; flex-shrink: 0; margin-top: 2px; display: flex; align-items: center; justify-content: center; color: #fff; }
.tcard.done .chk { background: var(--green, #22a06b); border-color: var(--green, #22a06b); }
.t-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.t-title { font-size: 14px; }
.tcard.done .t-title { text-decoration: line-through; }
.goal-tag { display: inline-block; font-size: 11px; color: var(--blue, #3b82f6); background: #F3F7FE; border-radius: 10px; padding: 1px 9px; width: fit-content; }
.t-desc { font-size: 12.5px; color: var(--sub, #8892a6); }
.t-meta { display: flex; gap: 12px; font-size: 11.5px; color: var(--sub, #8892a6); }
.pri-t { font-weight: 700; }
.pri-t.p-low { color: var(--sub, #8892a6); }
.pri-t.p-medium { color: var(--blue, #3b82f6); }
.pri-t.p-high { color: var(--amber, #d29922); }
.pri-t.p-urgent { color: var(--red, #c0392b); }
.prog { display: flex; align-items: center; gap: 9px; margin-top: 5px; }
.prog-bar { flex: 1; max-width: 260px; height: 7px; border-radius: 6px; background: var(--c-bg-soft, #eee); overflow: hidden; }
.prog-bar i { display: block; height: 100%; background: var(--blue, #3b82f6); border-radius: 6px; transition: width .3s; }
.prog-input { width: 58px; border: 1px solid var(--line, #e5e5e5); border-radius: 7px; padding: 2px 6px; font-size: 11.5px; }

/* 目标 */
.goal-cards { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 2px; }
.gcard { flex: none; min-width: 200px; max-width: 260px; border: 1px solid var(--line, #e5e5e5); border-radius: 12px; padding: 9px 12px; cursor: pointer; background: #fff; display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; align-items: center; }
.g-light { font-size: 14px; }
.g-name { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.g-bar { grid-column: 1 / -1; height: 5px; border-radius: 3px; background: var(--line, #eee); overflow: hidden; }
.g-bar i { display: block; height: 100%; background: #22a06b; border-radius: 3px; }
.g-meta { grid-column: 1 / -1; font-size: 11px; color: var(--sub, #999); }

/* 随手记 */
.inbox-addrow { display: flex; gap: 8px; margin-bottom: 8px; }
.inbox-input { flex: 1; border: 1px solid var(--line, #e5e5e5); border-radius: 9px; padding: 8px 11px; font-size: 13px; background: #fff; color: var(--ink, #333); outline: none; }
.inbox-input:focus { border-color: var(--blue, #3b82f6); }
.inbox-row { display: flex; align-items: flex-start; gap: 8px; padding: 8px 2px; border-bottom: 1px dashed var(--line, #e5e5e5); }
.inbox-main { flex: 1; min-width: 0; }
.inbox-ct { font-size: 13px; white-space: pre-wrap; }
.inbox-meta { font-size: 11px; color: var(--sub, #8892a6); margin-top: 2px; }
.inbox-sug { margin-left: 8px; color: var(--blue, #3b82f6); font-style: normal; }

/* 弹层 */
.modal-mask { position: fixed; inset: 0; background: rgba(20, 26, 38, .4); z-index: 80; display: flex; align-items: center; justify-content: center; }
.modal { width: min(460px, 92vw); background: #fff; border-radius: 14px; overflow: hidden; }
.m-head { display: flex; justify-content: space-between; align-items: center; padding: 13px 16px; border-bottom: 1px solid var(--line, #e5e5e5); }
.m-form { padding: 16px; display: flex; flex-direction: column; gap: 11px; }
.m-form label { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--sub, #8892a6); }
.m-form textarea, .m-form select { border: 1px solid var(--line, #e5e5e5); border-radius: 9px; padding: 8px 11px; font-size: 13px; font-family: inherit; outline: none; background: #fff; color: var(--ink, #333); resize: vertical; }
.m-form textarea:focus, .m-form select:focus { border-color: var(--blue, #3b82f6); }

.mini { border: 1px solid var(--line, #e5e5e5); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 11px; cursor: pointer; color: var(--ink, #333); flex-shrink: 0; }
.mini.pri { border-color: var(--blue, #3b82f6); color: var(--blue, #3b82f6); font-weight: 700; }
.mini.danger:hover { border-color: var(--red, #c0392b); color: var(--red, #c0392b); }

/* ══ ⓪ 问管家（#1190）══ */
.bqa-box { max-height: 300px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px;
  padding: 4px 2px; margin-bottom: 8px; }
.bqa-msg { display: flex; flex-direction: column; align-items: flex-end; }
.bqa-msg.assistant { align-items: flex-start; }
.bqa-bubble { max-width: 86%; padding: 7px 11px; border-radius: 12px; font-size: 13.5px;
  line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.bqa-msg.user .bqa-bubble { background: var(--blue, #3b82f6); color: #fff; border-bottom-right-radius: 4px; }
.bqa-msg.assistant .bqa-bubble { background: var(--bg-soft, #f2f4f7); color: var(--text, #222);
  border-bottom-left-radius: 4px; }
.bqa-dots { animation: bqa-blink 1s step-start infinite; }
@keyframes bqa-blink { 50% { opacity: 0 } }
/* ★ #1213 聊天内跳转按钮 */
.bqa-jump { display: inline-block; margin: 2px 4px 2px 0; padding: 3px 10px; border-radius: 9px;
  border: 1px solid var(--blue, #3b82f6); background: transparent; color: var(--blue, #3b82f6);
  font-size: 12.5px; cursor: pointer; }
.bqa-jump:active { background: var(--blue, #3b82f6); color: #fff; }
.bqa-by { font-size: 11px; color: var(--text2, #888); margin-top: 2px; }
.bqa-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.bqa-input { display: flex; gap: 8px; }
.bqa-input input { flex: 1; min-width: 0; border: 1px solid var(--line, #ddd); border-radius: 10px;
  padding: 8px 12px; font-size: 13.5px; background: var(--bg, #fff); color: var(--text, #222); }
.bqa-input input:focus { outline: none; border-color: var(--blue, #3b82f6); }
.bqa-input .mini.pri { padding: 8px 16px; }
</style>
