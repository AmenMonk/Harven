// ★ 2026-09-14 面板大改版：分类与模板库抽成共享目录
// PanelsPage（管理页）和 SpacePage（分类空间页）共用同一份，
// 改这里两边同步生效。
//
/* ═══════════════════════════════════════════════════════════
   ★ 2026-09-18 定版拍板：面板称号三级统一
   ① 最外面（整个功能）＝「面板」——入口页叫「面板中心」
   ② 中间层（场景包）＝「模板」——家庭模板 / 公司模板 / 创作模板
   ③ 最下边（装好的每个台账）＝「模块」——待装的在「模块库」
   注意：「模型」专指 AI 模型，永远不用在面板体系里。
   ═══════════════════════════════════════════════════════════ */

/* ═══ 默认模板（出厂三大场景包）═══ */
export const DEFAULT_CATS = [
  { id: 'family',  icon: '🏠', name: '家庭模板', desc: '记账、居家、健康、孩子学习', long: '家里的事都在这一页：钱花哪了、孩子学了啥、车该保养没、花浇水了没……下面装上模块，记的每一笔都会自动汇到时间轴里，随时回看。' },
  { id: 'company', icon: '💼', name: '公司模板', desc: '会议、项目、客户、日报、报表', long: '上班的事都在这一页：开会定了啥、项目走到哪一步、客户跟进到哪了、数据报表啥结论。装上模块开始记，时间轴帮你把日子串起来。' },
  { id: 'create',  icon: '🎨', name: '创作模板', desc: '音乐、图片、视频工坊', long: '玩创作的地方：写歌、做图、剪视频的记录都归这页，作品进度和灵感小记一目了然。' },
]

/* ═══ 模块库（同一个模块可装多份，数据互不干扰） ═══ */
export const TPL = [
  // —— 家庭 ——
  { base: 'accounting_daily', group: 'home', icon: '💰', name: '记账账本 · 日常流水', desc: '每天花了多少钱，一笔一笔记清楚', cat: 'family', fields: [{ name: '日期', type: 'date' }, { name: '项目', type: 'text' }, { name: '金额', type: 'number' }, { name: '分类', type: 'select' }, { name: '备注', type: 'text' }] },
  // ★ 2026-10-27 (#1255) 模块库与 #1254 面板减负对齐：删 6 张与下架面板对应的待装卡——
  //   accounting_budget（从未装过、面板中心无此项）/ pet_care / smart_home / car_fuel / insurance / plants
  //   （这 5 个面板刚 hidden 下架，模块库还能一键装回=减负白做）；面板中心没有的卡不上架
  // ★ 2026-09-19 开发者截图反馈：保养字段太穷连品牌都没有 → 扩成 9 字段（品牌/车型/里程/保养项目下拉/花费/地点/下次保养日/备注）
  {
    base: 'car_maintenance', group: 'home', icon: '🚗', name: '车辆保养记录', desc: '每次保养的时间、里程、项目和花费', cat: 'family',
    features: { remind: { title_field: '保养项目', date_field: '下次保养日', advance_days: 7, label: '保养提醒' }, stats: true, ai: true },
    fields: [
      { name: '日期', type: 'date' },
      { name: '照片', type: 'image' },  /* ★ #910 B方案：保养单据拍照存档 */
      { name: '品牌', type: 'text' },
      { name: '车型', type: 'text' },
      { name: '里程表km', type: 'number' },
      { name: '保养项目', type: 'select', options: ['小保养（机油机滤）', '大保养（机油三滤）', '空气滤芯', '空调滤芯', '汽油滤芯', '火花塞', '刹车片/刹车盘', '轮胎换位', '轮胎更换', '电瓶', '雨刮', '防冻液', '变速箱油', '刹车油', '四轮定位', '年检', '洗车美容', '其他'] },
      { name: '花费', type: 'number' },
      { name: '地点', type: 'text' },
      { name: '下次保养日', type: 'date' },
      { name: '备注', type: 'text', adv: 1 },
    ],
  },
  // ★ #1255 下架：car_fuel / insurance / plants 三张待装卡（对应面板已 hidden，加油并入家庭账本、保单并入证件与保单）
  // ★ 2026-10-14 #1142「孩子学习打卡」「错题本」模板退役：学习域归一进孩子成长档案（kid_archive 六大学习域），模板下架防重复装回（错题本×4 悲剧续集预防）
  // ★ 2026-10-11 #1102 旧款「健康体检记录」模板卡退役：健康域由健康中枢四面板接管（health_panels.py 出厂）
  // ★ 2026-09-20 #801 餐饮记录（拍菜品照片 AI 识别自动填，热量汇总联动健康板块）
  {
    base: 'meal_diary', group: 'home', icon: '🍽️', name: '餐饮记录', desc: '每天吃了啥拍张照，AI 自动识别菜和热量，与健康档案数据联动点评', cat: 'family',
    features: { ai: true },
    fields: [
      { name: '日期', type: 'date' },
      { name: '餐次', type: 'select', options: ['早餐', '午餐', '晚餐', '加餐', '夜宵'] },
      { name: '成员', type: 'text' },
      { name: '菜品照片', type: 'image' },
      { name: '识别出的食物', type: 'text' },
      { name: '估算热量', type: 'number' },
      { name: '营养点评', type: 'textarea', adv: 1 },
      { name: '备注', type: 'textarea', adv: 1 },
    ],
  },
  { base: 'fridge_stock', group: 'home', icon: '🧊', name: '冰箱库存台账', desc: '冰箱里有啥、啥时候买的、啥时候到期', cat: 'family', fields: [{ name: '名称', type: 'text' }, { name: '照片', type: 'image' }, { name: '数量', type: 'text' }, { name: '买入日期', type: 'date' }, { name: '到期日', type: 'date' }, { name: '备注', type: 'text' }] },
  // ★ 2026-09-22 (#843) 实测实锤：物品存放台账模块库漏卡（#753 只装了实例没进 TPL）→ 补卡，字段与已装实例 v2.1.1 对齐
  {
    base: 'home_items', group: 'home', icon: '📦', name: '物品存放台账', desc: '家里东西放哪了一问就知道，直接说「螺丝刀收进书房五斗柜第二层」就行', cat: 'family', universal: true,
    features: { ai: true },
    fields: [
      { name: '物品名称', type: 'text' },
      { name: '照片', type: 'image' },  /* ★ #910 B方案：东西长啥样拍一张 */
      { name: '分类', type: 'select', options: ['衣物鞋帽·当季', '衣物鞋帽·换季收纳', '电子产品·线材', '厨房用品', '清洁用品', '工具五金', '药品保健', '文具书籍', '玩具', '运动户外', '汽车用品', '箱包收纳', '纪念收藏', '其他'] },
      { name: '数量', type: 'number' },
      { name: '位置·房间', type: 'select', options: ['客厅', '主卧', '次卧', '儿童房', '书房', '厨房', '卫生间', '阳台', '玄关', '车库', '储物间', '其他'] },
      { name: '位置·柜子家具', type: 'text' },
      { name: '适用季节', type: 'select', options: ['全年', '春秋', '夏季', '冬季'], adv: 1 },
      { name: '位置·盒层', type: 'text', adv: 1 },
      { name: '存放位置', type: 'text', adv: 1 },
      { name: '低库存提醒少于', type: 'number', adv: 1 },
      { name: '状态', type: 'select', options: ['在用', '借出', '收纳存放', '淘汰'], adv: 1 },
      { name: '借给谁', type: 'text', adv: 1 },
      { name: '借出日期', type: 'date', adv: 1 },
      { name: '最近使用', type: 'date', adv: 1 },
      { name: '照片', type: 'image' },
      { name: '柜内照片', type: 'image' },
      { name: '购买日期', type: 'date', adv: 1 },
      { name: '备注', type: 'textarea', adv: 1 },
    ],
  },
  // —— 老人关怀全家桶（★ 2026-09-19 定版拍板「全部开发」：不在身边的儿女担心什么就记什么）——
  // ★ 2026-09-19 老人半合并：「今天吃药了没」并进每日打卡（服药情况+漏了哪种），
  //   药品台账归现有「家庭药箱」模块，独立的「老人用药提醒」模板下架
  {
    base: 'elder_checkin', group: 'home', icon: '📋', name: '老人每日关怀打卡', desc: '每天一条：吃饭、吃药、精神、血压、心情，远方儿女打开就看', cat: 'family',
    features: { stats: true, ai: true },
    fields: [
      { name: '日期', type: 'date' },
      { name: '老人姓名', type: 'text' },
      { name: '状态', type: 'select', options: ['精神好', '一般', '不舒服'] },
      { name: '吃饭', type: 'select', options: ['正常', '吃得少', '没吃'] },
      { name: '服药情况', type: 'select', options: ['全吃了', '漏了一顿', '没吃'] },
      { name: '漏了哪种', type: 'text', adv: 1 },
      { name: '睡眠小时', type: 'number' },
      { name: '血压高压', type: 'number' },
      { name: '血压低压', type: 'number' },
      { name: '心率', type: 'number' },
      { name: '血糖', type: 'number', adv: 1 },
      { name: '体重kg', type: 'number', adv: 1 },
      { name: '心情', type: 'select', options: ['开心', '平静', '低落'], adv: 1 },
      { name: '不适主诉', type: 'textarea' },
      { name: '备注', type: 'textarea', adv: 1 },
    ],
  },
  // ★ 2026-09-19 老人半合并：独立「老人用药提醒」模板下架（elder_medication）——
  //   打卡粒度「一天一条」vs 用药「一种药一条」没法硬合，定案半合并；已装实例同步卸载（0 记录）
  {
    base: 'elder_contact', group: 'home', icon: '📞', name: '联系与代办', desc: '多久没打电话了、该帮爸妈办什么事，都在这本上', cat: 'family',
    features: { remind: { title_field: '待办事项', date_field: '截止日期', advance_days: 1, label: '代办提醒' }, ai: true },
    fields: [
      { name: '日期', type: 'date' },
      { name: '方式', type: 'select', options: ['电话', '视频', '探望', '代办'] },
      { name: '聊了什么', type: 'text' },
      { name: '待办事项', type: 'text' },
      { name: '截止日期', type: 'date' },
      { name: '备注', type: 'text', adv: 1 },
    ],
  },
  {
    base: 'elder_card', group: 'home', icon: '📇', name: '紧急信息卡', desc: '血型、慢性病、紧急联系人——急救时给医生看的那张卡，可打印贴冰箱', cat: 'family',
    features: { ai: false },
    fields: [
      { name: '老人姓名', type: 'text' },
      { name: '血型', type: 'select', options: ['A型', 'B型', 'AB型', 'O型', '不详'] },
      { name: '慢性病史', type: 'textarea' },
      { name: '常用药物', type: 'textarea' },
      { name: '过敏史', type: 'textarea', adv: 1 },
      { name: '紧急联系人1', type: 'text' },
      { name: '联系人1电话', type: 'text' },
      { name: '紧急联系人2', type: 'text', adv: 1 },
      { name: '联系人2电话', type: 'text', adv: 1 },
      { name: '社区医院', type: 'text' },
      { name: '家庭医生电话', type: 'text', adv: 1 },
      { name: '医保卡号', type: 'text', adv: 1 },
      { name: '其他备注', type: 'textarea', adv: 1 },
    ],
  },
  // —— 公司 ——（★ 2026-09-18 原「效率记录」并入公司模板；原「智能数据」的「数据报表」也归这里，「监控清单」删掉）
  { base: 'meeting_notes', group: 'company', icon: '📝', name: '会议记录', desc: '开会主题、结论和待办，翻起来快', cat: 'company', fields: [{ name: '日期', type: 'date' }, { name: '主题', type: 'text' }, { name: '参会人', type: 'text' }, { name: '结论', type: 'text' }, { name: '待办', type: 'text' }] },
  { base: 'project_track', group: 'company', icon: '📌', name: '项目进度跟踪', desc: '每个项目走到哪一步、谁负责、啥时候到期', cat: 'company', fields: [{ name: '项目名', type: 'text' }, { name: '阶段', type: 'text' }, { name: '负责人', type: 'text' }, { name: '截止日', type: 'date' }, { name: '进度', type: 'select' }] },
  { base: 'customer_crm', group: 'company', icon: '🤝', name: '客户跟进台账', desc: '客户联系人、跟进时间和进展状态', cat: 'company', fields: [{ name: '客户名', type: 'text' }, { name: '联系人', type: 'text' }, { name: '跟进日期', type: 'date' }, { name: '状态', type: 'select' }, { name: '备注', type: 'text' }] },
  { base: 'daily_todo', group: 'company', icon: '✅', name: '每日待办清单', desc: '今天要做的事，做完打勾', cat: 'company', fields: [{ name: '日期', type: 'date' }, { name: '事项', type: 'text' }, { name: '优先级', type: 'select' }, { name: '状态', type: 'select' }, { name: '备注', type: 'text' }] },
  { base: 'work_log', group: 'company', icon: '🗓️', name: '工作日报', desc: '每天干了啥、花多少时间，周报不用愁', cat: 'company', fields: [{ name: '日期', type: 'date' }, { name: '工作内容', type: 'textarea' }, { name: '用时小时', type: 'number' }, { name: '产出', type: 'text' }] },
  { base: 'data_report', group: 'company', icon: '📈', name: '数据报表', desc: '定期整理的数字和结论', cat: 'company', fields: [{ name: '日期', type: 'date' }, { name: '指标', type: 'text' }, { name: '数值', type: 'number' }, { name: '结论', type: 'textarea' }] },
  // —— 创作 ——
  { base: 'song_ideas', group: 'create', icon: '🎵', name: '灵感小本本', desc: '随时记下的点子和灵感，别让它跑了', cat: 'create', fields: [{ name: '日期', type: 'date' }, { name: '灵感', type: 'textarea' }, { name: '分类', type: 'select' }, { name: '备注', type: 'text' }] },
  { base: 'work_progress', group: 'create', icon: '🎬', name: '作品进度', desc: '每件作品做到哪一步了', cat: 'create', fields: [{ name: '作品名', type: 'text' }, { name: '类型', type: 'select' }, { name: '进度', type: 'select' }, { name: '更新日期', type: 'date' }, { name: '备注', type: 'text' }] },
  // （★ 2026-09-18 原「智能数据」模板删除：数据报表并入公司模板，监控清单整个砍掉——定版拍板没用）
]

/* ★ 2026-09-19 收尾补漏（实测实锤：梦境手记等 4 个面板没出 AI 按钮）：
   所有模板默认带 ai 开关，已有 features 的在其基础上合并——
   「每个模块都应该有AI的参与」，新装面板不再漏按钮 */
for (const t of TPL) t.features = { ai: true, ...(t.features || {}) }

/* ═══ ★ 2026-09-19 AI 角色卡（定版拍板 P0 全量铺：十角色矩阵）
   每个模块给 AI 一个专职角色（📝记录员/📊分析师/🧮计算员/🎓顾问/⏰管家/🚨哨兵/🗂️档案员/🔍解读员/🏋️教练/✍️写手），
   「AI 分析」按钮按角色给建议，不再千篇一律。安装时写进 panel.json 顶层 ai_role。 ═══ */
export const AI_ROLES = {
  accounting_daily: '你是这个家的记账分析师，擅长从日常流水里看出花销结构，帮忙算账、找可省的开支。',
  // ★ #1255 随卡移除：accounting_budget / pet_care / smart_home / car_fuel / insurance / plants 角色卡
  car_maintenance: '你是资深汽车养护顾问，根据里程、保养项目间隔和花费，给出下次该保养什么、大概多少钱的建议。',
  kid_study: '你是孩子身边的学习教练，从打卡记录发现学习规律，多鼓励、指出需要加强的科目。',
  // ★ #1102 health_check 模板退役，AI 角色随卡移除（健康域四面板自带 ai_role）
  fridge_stock: '你是冰箱管家，盯临期食材，建议先吃什么、缺什么要补。',
  meeting_notes: '你是会议纪要管理员，帮提炼决议与待办，追踪有没有落地。',
  project_track: '你是项目助理，识别延期风险和卡住的环节，提醒该催谁。',
  customer_crm: '你是销售助理，从跟进记录发现商机和太久没联系的沉默客户。',
  daily_todo: '你是效率管家，帮排优先级、清理拖了太久的事。',
  work_log: '你是工作复盘分析师，从日报算时间分布、提炼每周成果。',
  data_report: '你是数据分析师，解读指标变化、给出结论和预警，图表化思维。',
  song_ideas: '你是创作伙伴，帮整理灵感碎片、顺着点子往下启发。',
  work_progress: '你是作品进度管家，盯卡壳的作品、提醒推进下一步。',
  // —— 以下为已装的老面板/生成面板同款角色（按 panel id 前缀匹配） ——
  dream_journal: '你是温柔的解梦师，从梦境记录里找情绪和压力的线索，只做启发陪伴，不下定论不说教。',
  meal_diary: '你是家庭营养师，根据每天的饮食记录和热量估算，给均衡饮食建议；结合健康板块的体重血压趋势时只做生活方式参考，不做医疗诊断。',
  elder_care: '你是家庭养老照护顾问，关注老人健康数据变化和照料安排，发现异常规律及时提醒。',
  family_todo: '你是家庭事务管家，帮排优先级、盯拖延项、把没做完的事排到明天。',
  home_calendar: '你是家庭日程管家，帮看日程是否太挤、提醒重要日子。',
  home_docs: '你是家庭文件档案员，帮归类证件材料，提醒哪些快到期该换。',
  home_favors: '你是人情往来记账员，帮算礼尚往来的平衡，提醒该回礼的节点。',
  home_items: '你是家庭物品管理员，帮找东西、盯低库存、提醒借出未还。',
  // ★ #1103 home_medicine 模板退役（被健康中枢 med_cabinet 替代），AI 角色随卡移除
  home_repair: '你是房屋维护管家，按维修记录提醒周期性检查项目。',
  home_utilities: '你是水电燃气账单分析师，对比各月用量找异常，给节能建议。',
  kid_archive: '你是孩子成长记录分析师，从身高体重、学习、生活各维度看成长趋势，给家长可操作的建议。',
  // —— 老人关怀全家桶（★ 2026-09-19）——
  mistake_notebook: '你是错题本教练：从错因分布看出真问题（"马虎"背后常是熟练度不足），把错题归类成薄弱知识点，按艾宾浩斯节奏（1天/3天/7天）建议重做安排，帮孩子把错题变成提分点。',
  elder_checkin: '你是远程尽孝顾问，替不在身边的儿女盯着爸妈的每日状态：吃饭、吃药、睡眠、血压、心情。发现异常趋势用大白话提醒，比如"这周头晕提了3次，建议约个号"、"这周漏服了两次降压药"，关心但不吓人。',
  // ★ 2026-09-19 老人半合并：elder_medication 角色卡随模板一起下架
  elder_contact: '你是亲情管家，帮儿女算多久没联系爸妈了、代办事项办到哪一步，提醒"该打电话了"时不制造愧疚感，只给具体建议。',
  elder_card: '你是急救信息卡管理员，检查卡片信息是否完整（血型、慢性病、联系人缺了就提醒补上），信息齐全时建议打印贴冰箱。',
}

/* ═══ 自动归类关键词 ═══ */export const CAT_HINTS = {
  family: ['记账', '居家', '家庭', '健康', '冰箱', '汽车', '学习', '宠物', '绿植', '保险', 'accounting', 'family', 'pet', 'car', 'health', 'fridge', 'plant'],
  company: ['任务', '待办', '笔记', '备忘', '会议', '项目', '客户', '日报', '数据', '报表', '统计', '监控', 'todo', 'task', 'note', 'meeting', 'project', 'crm', 'data', 'stats', 'monitor', 'report'],
  create: ['音乐', '图片', '视频', '创作', '画板', '灵感', '作品', 'music', 'art', 'video', 'song'],
}

export function inferCat(p) {
  const hay = `${p.id || ''} ${p.name || ''} ${p.description || ''}`.toLowerCase()
  for (const [cid, kws] of Object.entries(CAT_HINTS)) if (kws.some(k => hay.includes(k))) return cid
  return '' // ★ 2026-09-18 猜不出分类不硬归（定版拍板）：返回空，由调用方按「通用」处理，所有分类空间页都可见
}

/* ★ 2026-09-18 通用模块：panel.json 里 "universal": true 的面板在所有模板空间页都可见 */
export function isUniversal(p) {
  return p?.universal === true || p?.universal === 'true' || p?.universal === 1
}

/* ★ 2026-09-18 catMap 迁移：旧版 localStorage 里可能存着 work/data 归类，
   work → company（效率记录并入公司模板），data → 删除（智能数据已下线）。
   SpacePage / PanelsPage / RightDock 读 catMap 时都要过一遍。 */
export function migrateCatMap(m) {
  const out = {}
  for (const [k, v] of Object.entries(m || {})) {
    if (v === 'work') out[k] = 'company'
    else if (v === 'data') continue
    else out[k] = v
  }
  return out
}
