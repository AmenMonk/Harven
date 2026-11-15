/* ═══ 对话框导航指令拦截（A路线）· 2026-09-16 开发者需求 ═══
 * "打开家庭面板 / 进入成长档案 / 去知识库" → 前端直接跳转，不进聊天管线
 * 防误触发三闸：①祈使词开头才尝试 ②长度≤24且无问号 ③目标词精确匹配（含少量后缀），不精确就走聊天（模型B路线兜底）
 * 白名单动态：内置页面 + 出厂分类 + 自定义/隐藏分类(localStorage) + 已装面板(/api/panels) + 常用别名
 */
import { DEFAULT_CATS } from './panelCatalog.js'

/* 内置页面（关键词需与聊天里可能说的说法一致，都是整词精确） */
const BUILTINS = [
  { keys: ['面板中心', '面板管理', '面板页', '我的面板', '面板', '模版库', '模板库'], path: '/panels', name: '面板中心' },
  { keys: ['知识库'], path: '/knowledge', name: '知识库' },
  { keys: ['技能库', '技能'], path: '/skills', name: '技能库' },
  { keys: ['管家', '管家中心', '任务'], path: '/butler', name: '管家中心' },
  { keys: ['邮箱', '邮件'], path: '/mail', name: '邮箱' },
  { keys: ['harvenhub', '哈文hub', 'hub市场', '市场'], path: '/hub', name: 'HarvenHub' },
  { keys: ['模型中心', '模型页', '模型'], path: '/models', name: '模型中心' },
  { keys: ['设置', '设置页'], path: '/settings', name: '设置' },
  { keys: ['运行状态', '状态页', '监控中心'], path: '/status', name: '运行状态' },
  { keys: ['时间轴', '会话记录'], path: '/timeline', name: '时间轴' },
  { keys: ['照片库', '照片中心', '相册', '照片'], path: '/photos', name: '照片库' },
  { keys: ['创作工坊', '工坊'], path: '/studio', name: '创作工坊' },
  { keys: ['更多'], path: '/more', name: '更多' },
]

/* 出厂模板（family/company/create）+ 自定义模板（localStorage 同步 PanelsPage） */
const CAT_SUFFIX = ['生活', '记录', '娱乐', '数据', '模板']
function getCats() {
  let custom = [], hidden = []
  try {
    custom = JSON.parse(localStorage.getItem('harven_spa_panel_cats') || '[]')
    hidden = JSON.parse(localStorage.getItem('harven_spa_panel_cats_hidden') || '[]')
  } catch (e) { /* 老包兼容 */ }
  return [...DEFAULT_CATS, ...custom]
    .filter(c => c && c.id && !hidden.includes(c.id))
    .map(c => ({ id: c.id, name: c.name || '', path: '/space/' + c.id }))
}

/* 常用别名 → 面板ID（覆盖日常口语叫法；动态面板名匹配兜底其余） */
const PANEL_ALIAS = [
  ['孩子成长档案', 'kid_archive'], ['成长档案', 'kid_archive'], ['成长', 'kid_archive'], ['孩子档案', 'kid_archive'],
  ['食物库存', 'fridge_stock_1'], ['冰箱', 'fridge_stock_1'],
  ['记账账本', 'accounting_daily_1'], ['记账', 'accounting_daily_1'], ['账本', 'accounting_daily_1'],
  ['车辆保养', 'car_maintenance_1'], ['车辆', 'car_maintenance_1'], ['汽车', 'car_maintenance_1'],
  ['宠物', 'pet_care_1'],
  ['绿植', 'plants_1'], ['花草', 'plants_1'], ['浇花', 'plants_1'],
  ['健康档案', 'health_file'], ['体检', 'health_file'], ['体检报告', 'health_file'], ['复查', 'health_file'],
  ['健身记录', 'fitness_log'], ['健身', 'fitness_log'], ['运动记录', 'fitness_log'],
  ['孩子成长', 'child_growth'], ['接种', 'child_growth'], ['视力', 'child_growth'],
  ['家庭日历', 'home_calendar'], ['日历', 'home_calendar'], ['日程', 'home_calendar'],
  ['物品存放', 'home_items'], ['物品', 'home_items'], ['东西在哪', 'home_items'],
  ['老人关怀', 'elder_care'], ['老人', 'elder_care'], ['用药', 'elder_care'],
  ['家庭药箱', 'med_cabinet'], ['药箱', 'med_cabinet'], ['吃药', 'med_cabinet'],
  ['证件保单', 'home_docs'], ['证件', 'home_docs'], ['保单', 'home_docs'],
  ['水电气', 'home_utilities'], ['缴费', 'home_utilities'],
  ['全家待办', 'family_todo'],
  ['人情往来', 'home_favors'], ['人情', 'home_favors'], ['随礼', 'home_favors'],
  ['家庭维修', 'home_repair'], ['维修', 'home_repair'],
]

/* 已装面板缓存（5分钟） */
let _panels = null
let _panelsAt = 0
async function getPanels() {
  if (_panels && Date.now() - _panelsAt < 5 * 60 * 1000) return _panels
  try {
    const r = await fetch('/api/panels')
    const j = await r.json()
    _panels = (j.panels || []).filter(p => p && p.id).map(p => ({ id: p.id, name: p.name || '' }))
    _panelsAt = Date.now()
  } catch (e) { _panels = _panels || [] }
  return _panels
}

/* 祈使句判定：必须以打开/进入/切换…开头（普通聊天里"提到"面板不会命中） */
const IMPERATIVE = /^(请|麻烦|帮我|劳驾)?(帮我)?(打开|进入|切换到|切换|带我去|跳转到|跳转|去(看看|一下|下)?|查看|看看)/

/* ★ #1214 最近打开目标（指代消解：「打开啊/再打开」→ 去最近的目标） */
let _lastTarget = null
try { _lastTarget = JSON.parse(sessionStorage.getItem('harven_nav_last') || 'null') } catch (e) { _lastTarget = null }
function _remember(name, path) {
  _lastTarget = { name, path, at: Date.now() }
  try { sessionStorage.setItem('harven_nav_last', JSON.stringify(_lastTarget)) } catch (e) { /* 隐私模式等 */ }
}

/* 目标词候选：原词优先，逐级剥量词后缀（面板/页面/中心…），剥空即止 */
function candidates(kw) {
  const list = [kw]
  let cur = kw
  while (list.length < 4) {
    const next = cur.replace(/(面板|页面|空间|中心|界面|工具|记录页|的页|页|库)$/g, '').trim()
    if (!next || next === cur) break
    list.push(next)
    cur = next
  }
  return list
}

/**
 * 尝试把一句话解析成页面跳转。
 * 返回 {handled:true, path, name, note} 或 null（不是导航指令，走聊天）。
 */
export async function tryNavigate(text) {
  const t = (text || '').trim()
  if (!t || t.length > 24) return null
  if (/[？?]/.test(t)) return null                    // 问句绝不跳
  if (!IMPERATIVE.test(t)) return null                // 非祈使不跳
  // ★ #1214 剥尾扩充：语气词「啊/呀/哇/嘛/呢」也剥（「打开啊」以前剥不干净 → kw0="啊" → 四层全不命中 → 落通用聊天瞎猜）
  let kw0 = t.replace(IMPERATIVE, '').replace(/^(一下|一下下|下)/, '').replace(/(吧|呗|哈|哦|啦|啊|呀|哇|嘛|呢)$/g, '').trim()
  if (!kw0) {
    // ★ #1214 无指代消解：「打开啊/打开一下」——10 分钟内打开过 → 直接再去；否则反问，绝不走通用聊天瞎猜面板列表
    if (_lastTarget && _lastTarget.path && Date.now() - _lastTarget.at < 10 * 60 * 1000) {
      return { handled: true, path: _lastTarget.path, name: _lastTarget.name, note: `🧭 好，再去「${_lastTarget.name}」～` }
    }
    return { handled: true, path: '', name: '', note: '🧭 你想打开哪个呀？直接说，比如「打开管家中心」「打开冰箱面板」～' }
  }
  const kws = candidates(kw0)

  /* 1. 内置页面 */
  for (const cand of kws) {
    for (const b of BUILTINS) {
      for (const k of b.keys) {
        if (cand === k) return ok(b.path, b.name)
      }
    }
  }
  /* 2. 模板（全名或去后缀简称，如"家庭"→家庭模板） */
  for (const cand of kws) {
    for (const c of getCats()) {
      const short = c.name.replace(new RegExp(`(${CAT_SUFFIX.join('|')})$`), '')
      if (cand === c.name || (short && cand === short)) return ok(c.path, c.name)
    }
  }
  /* 3. 面板别名（精确） */
  for (const cand of kws) {
    for (const [alias, pid] of PANEL_ALIAS) {
      if (cand !== alias) continue
      const hit = await findPanel(pid)
      if (hit) return ok('/panel/' + hit.id, hit.name)
      return ok('/panels', alias, `没有找到「${alias}」对应的已装面板，先帮你打开面板页，在里面看看～`)
    }
  }
  /* 4. 动态面板名（关键词与面板名互含且相差≤2字，防止"记账的账单"之类粘连误配） */
  const panels = await getPanels()
  for (const cand of kws) {
    const hits = panels.filter(p => {
      const nm = p.name
      if (!nm) return false
      if (cand === nm) return true
      if (cand.includes(nm) && cand.length - nm.length <= 2) return true
      if (nm.includes(cand) && nm.length - cand.length <= 2) return true
      return false
    })
    if (hits.length === 1) return ok('/panel/' + hits[0].id, hits[0].name)
    if (hits.length > 1) {
      return ok('/panels', '面板管理',
        `🧭 「${kw0}」匹配到多个面板：${hits.map(h => h.name).join('、')}——先帮你打开面板页，你在里面点想看的那个～`)
    }
  }
  /* 未命中 → 交给聊天管线（模型B路线兜底花式说法） */
  return null
}

async function findPanel(pid) {
  const panels = await getPanels()
  return panels.find(p => p.id === pid) || null
}

function ok(path, name, note) {
  if (path && name) _remember(name, path)  // ★ #1214 记最近目标，供「打开啊」指代消解
  return { handled: true, path, name, note: note || `🧭 已打开「${name}」～` }
}
