# -*- coding: utf-8 -*-
"""管家议程引擎 Butler Agenda ★ 2026-10-24 v1.6 一期（定版拍板「开发」）
使命：哈文第一个「自己看 / 自己想 / 自己开口」的主动型 agent——
面板是器官，管家是大脑。所有模块数据录进去后，由管家统一盯、统一开口、统一分发。

═══════════════ v1.6 一期范围（定稿） ═══════════════
① 模块提醒声明规范 + 通用扫描器：每模块一份声明（盯哪张表哪个字段→什么条件→什么级别），
   通用扫描器 30s 扫一遍；现有 coach 13 条规则翻译成首批声明；新模块填声明自动获得主动提醒。
② coach/reminder 主动弹动作收编：面板日期源（REMINDER_SOURCES）由管家统一扫描分发，
   reminder_daemon 的 TTS+通知队列保留为分发出口（不再自己扫教练源）。
③ 统一议程 + 日两次简报 + 沉睡唤醒。

═══════════════ token 经济六铁律 ═══════════════
1. 模板优先模型兜底：话术全部模板生成（0 token），只有用户点进对话才走模型（跟随用户当前模型）
2. 批量合成禁逐条：同类合并，同一面板同轮只播一条汇总
3. 勿扰时段 22:00-7:30 零 token 零打扰（连 TTS 都不出声，条目只静默落表）
4. 盯催封顶：同一条目最多触达 3 次，之后降级为只进议程表不再出声
5. 幂等补跑：简报按日期幂等，错过时点后第一个心跳补上，不重复
6. 议程快照缓存 5 分钟：前端轮询读缓存，不每次打库

═══════════════ 分级（一期实现 L1~L3，L4 推手机二期） ═══════════════
L1 静默：只落议程表（info 级，前端管家卡被动拉）
L2 被动：议程条目 + 前端管家卡红点（前端已有轮询习惯）
L3 建议：urge 级 → TTS 播报 + SPA 横幅（复用 reminder_daemon 通知队列）
红线：管家不新增数据存储（议程表一张除外）、不私写模块数据、只读真家+调既有函数。
"""
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'

# ═══════════════════════════════════════════════════════════
# 声明注册表（首批 = coach_engine 13 条规则 + REMINDER_SOURCES 5 条翻译）
# 每模块一份声明。新模块想获得主动提醒，在这里加一条即可，无需改扫描器。
#
# watches 条目字段：
#   field    : 日期字段名，或 (基准日期字段, 周期天数计算字段) 计算型
#   when     : 'expired' | 'due_within:N'（N 天内到期，含今天）| 'past_within:N'（过期 N 天内仍催）
#   level    : 'info' | 'urge'（urge=L3 出声，info=L1 静默）
#   kind     : 同类合并键（同 panel 同 kind 合并播报）
#   title    : 话术模板，可用 {中文列名} {date} {rel}(今天/N天后) {days}
#   gap      : 沉默型（不盯日期盯空档）——距最后记录 N 天无新数据时触发
# sleep_watch: 沉睡唤醒——模块整体 N 天无新记录时提醒「要不要录一条/真没用可以隐藏」
# ═══════════════════════════════════════════════════════════
DECLARATIONS = {
    # ── 冰箱：到期日 ──
    'fridge_stock_1': {
        'name': '冰箱',
        'only_active': True,   # ★ #1381（2026-11-02）：只扫「状态=在存」——已消耗行（吃完了/过期扔了）还带旧到期日，心跳天天命中催「干红已过期」实锤（台账早记了消耗，提醒永挂）
        'watches': [
            {'field': ('到期日',), 'when': 'expired', 'level': 'urge', 'kind': 'expiry',
             'title': '{名称} 已过期（{date}），建议清掉'},
            {'field': ('到期日',), 'when': 'due_within:3', 'level': 'urge', 'kind': 'expiry',
             'title': '{名称}{rel}到期，尽快吃掉别浪费'},
        ],
    },
    # ── 药箱：有效期（提前 30 天）──
    'med_cabinet': {
        'name': '药箱',
        'watches': [
            {'field': ('有效期',), 'when': 'expired', 'level': 'urge', 'kind': 'expiry',
             'title': '药品 {药品名称} 已过期，建议清掉补新'},
            {'field': ('有效期',), 'when': 'due_within:30', 'level': 'info', 'kind': 'expiry',
             'title': '药品 {药品名称}{rel}到期，记得补货'},
        ],
    },
    # ── 绿植：最近浇水 + 浇水周期（计算型）──
    'plants_1': {
        'name': '绿植',
        'watches': [
            {'field': ('最近浇水', '浇水周期天数'), 'when': 'expired', 'level': 'urge', 'kind': 'water',
             'title': '{植物名} 该浇水了（晚了{days}天）——植物渴了不会说话，我替它们喊'},
            {'field': ('最近浇水', '浇水周期天数'), 'when': 'due_within:1', 'level': 'urge', 'kind': 'water',
             'title': '今天该给 {植物名} 浇水'},
        ],
        'sleep_watch': {'gap_days': 21, 'title': '绿植档案 {n} 天没更新了，最近浇过水吗？'},
    },
    # ── 宠物：下次时间 ──
    'pet_care_1': {
        'name': '宠物',
        'watches': [
            {'field': ('下次时间',), 'when': 'expired', 'level': 'urge', 'kind': 'due',
             'title': '{宠物名}的{事项}已过期{days}天——该安排了'},
            {'field': ('下次时间',), 'when': 'due_within:7', 'level': 'urge', 'kind': 'due',
             'title': '{宠物名}的{事项}{rel}'},
        ],
    },
    # ── 车辆保养：下次保养日 ──
    'car_maintenance_1': {
        'name': '车辆保养',
        'watches': [
            {'field': ('下次保养日',), 'when': 'expired', 'level': 'urge', 'kind': 'due',
             'title': '保养已过期{days}天（计划{date}）——该去保养了'},
            {'field': ('下次保养日',), 'when': 'due_within:14', 'level': 'urge', 'kind': 'due',
             'title': '下次保养{date}（还有{days}天），可以预约了'},
        ],
    },
    # ── 加油：沉默型（2 天没记 = 大概率该加油了）──
    'car_fuel_1': {
        'name': '加油',
        'watches': [
            {'field': ('日期',), 'when': 'silent_gap:2', 'level': 'info', 'kind': 'gap',
             'title': '上次加油是 {date}（{days} 天前），顺路加一把顺便记一笔？'},
        ],
    },
    # ── 健身：沉默型 ──
    'fitness_log': {
        'name': '健身',
        'watches': [
            {'field': ('日期',), 'when': 'silent_gap:2', 'level': 'urge', 'kind': 'gap',
             'title': '已经 {days} 天没动了。休息可以，别断链——今天动 20 分钟就算赢'},
        ],
        'sleep_watch': {'gap_days': 30, 'title': '健身档案 {n} 天没记录了，还在练吗？'},
    },
    # ── 家人健康：复查「下次日期」──
    'health_file': {
        'name': '家人健康',
        'watches': [
            {'field': ('下次日期',), 'when': 'expired', 'level': 'urge', 'kind': 'checkup',
             'title': '{成员}的{项目}复查已过期{days}天，记得安排'},
            {'field': ('下次日期',), 'when': 'due_within:1', 'level': 'urge', 'kind': 'checkup',
             'title': '复查提醒：{成员}的{项目}（{类型}）{rel}'},
        ],
    },
    # ── 记账：不盯日期（预算走 goal_manager，见 _budget_watch），只做沉默唤醒 ──
    'accounting_daily_1': {
        'name': '记账',
        'watches': [],
        'sleep_watch': {'gap_days': 7, 'title': '已经 {n} 天没记账了，说一句「47.6 炸酱面」就能记'},
    },
    # ── 学习：批改沉默型 ──
    'study_center': {
        'name': '学习',
        'watches': [
            {'field': ('日期',), 'when': 'silent_gap:7', 'level': 'info', 'kind': 'gap',
             'title': '作业批改 {days} 天没做了，错题本在攒灰——拍一次就自动续上'},
        ],
    },
    # ── 证件文档：到期日（提前 30 天）──
    'home_docs': {
        'name': '证件',
        'watches': [
            {'field': ('到期日',), 'when': 'expired', 'level': 'urge', 'kind': 'expiry',
             'title': '{名称}（{持有人}）已过期，尽快换补'},
            {'field': ('到期日',), 'when': 'due_within:30', 'level': 'info', 'kind': 'expiry',
             'title': '{名称}（{持有人}）{rel}到期，留意换补'},
        ],
    },
    # ── 保单：到期日（提前 14 天）──
    'insurance_1': {
        'name': '保单',
        'watches': [
            {'field': ('到期日',), 'when': 'expired', 'level': 'urge', 'kind': 'due',
             'title': '保单「{保单名}」已到期{days}天，确认续保'},
            {'field': ('到期日',), 'when': 'due_within:14', 'level': 'info', 'kind': 'due',
             'title': '保单「{保单名}」{rel}到期'},
        ],
    },
    # ── 水电燃气：缴费截止 ──
    'home_utilities': {
        'name': '水电燃气',
        'watches': [
            {'field': ('缴费截止',), 'when': 'expired', 'level': 'urge', 'kind': 'due',
             'title': '{类型}缴费已过期{days}天，赶紧缴上'},
            {'field': ('缴费截止',), 'when': 'due_within:3', 'level': 'urge', 'kind': 'due',
             'title': '{类型}缴费{rel}截止（{金额}元）'},
        ],
    },
    # ── 家庭维修：下次检查 ──
    'home_repair': {
        'name': '维修',
        'watches': [
            {'field': ('下次检查',), 'when': 'expired', 'level': 'info', 'kind': 'due',
             'title': '{维修对象}该检查了（计划{date}，过期{days}天）'},
            {'field': ('下次检查',), 'when': 'due_within:7', 'level': 'info', 'kind': 'due',
             'title': '{维修对象}{rel}该检查'},
        ],
    },
    # ── 家庭任务：截止日期（管家盯催主战场之一）──
    'family_todo': {
        'name': '任务',
        'watches': [
            {'field': ('截止日期',), 'when': 'expired', 'level': 'urge', 'kind': 'todo',
             'title': '任务「{事项}」已过期{days}天，还做吗？'},
            {'field': ('截止日期',), 'when': 'due_within:1', 'level': 'urge', 'kind': 'todo',
             'title': '任务「{事项}」{rel}截止'},
        ],
    },
    # ── 日历日程：日期+时间 ──
    'home_calendar': {
        'name': '日历',
        'watches': [
            {'field': ('日期',), 'when': 'due_within:1', 'level': 'info', 'kind': 'event',
             'title': '日程「{事项}」{rel}'},
        ],
    },
    # ── 孩子成长：沉默型（14 天没记）──
    'child_growth': {
        'name': '成长档案',
        'watches': [
            {'field': ('日期',), 'when': 'silent_gap:14', 'level': 'info', 'kind': 'gap',
             'title': '成长档案 {days} 天没记了——最近有什么小进步？'},
        ],
    },
}

# 沉默唤醒兜底：声明里没写 sleep_watch 的面板通用值（天）；0=不启用
DEFAULT_SLEEP_GAP = 0

# ═══════════════════════════════════════════════════════════
# 议程表（管家唯一新增存储）
# ═══════════════════════════════════════════════════════════


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_butler_table():
    """import 即就绪：议程表 + 简报状态表"""
    conn = _conn()
    conn.execute('''CREATE TABLE IF NOT EXISTS butler_agenda (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedup_key TEXT UNIQUE,
        date TEXT,
        ts TEXT,
        source_panel TEXT,
        kind TEXT,
        level TEXT,
        title TEXT,
        detail TEXT,
        ref_row INTEGER,
        status TEXT DEFAULT 'open',
        notify_count INTEGER DEFAULT 0,
        gen_date TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS butler_state (
        key TEXT PRIMARY KEY, value TEXT)''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_butler_status ON butler_agenda(status, date)')
    # ★ #1209（2026-10-25）触达挂人：议程条目关联到具体家人（panel_members.name），
    #   主动触达从「全家广播」升级为「谁的事找谁」。旧表幂等补列。
    _cols = {r[1] for r in conn.execute('PRAGMA table_info(butler_agenda)').fetchall()}
    if 'member' not in _cols:
        conn.execute("ALTER TABLE butler_agenda ADD COLUMN member TEXT DEFAULT ''")
    conn.commit()
    conn.close()


ensure_butler_table()


def _state_get(key, default=''):
    conn = _conn()
    r = conn.execute('SELECT value FROM butler_state WHERE key=?', (key,)).fetchone()
    conn.close()
    return r[0] if r else default


def _state_set(key, value):
    conn = _conn()
    conn.execute('INSERT INTO butler_state(key,value) VALUES(?,?) '
                 'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
# 配置（config.yaml 顶层 butler 段，缺省全兜底）
# ═══════════════════════════════════════════════════════════


def _cfg():
    try:
        import yaml
        cfg = (yaml.safe_load((BASE_DIR / 'config.yaml').read_text(encoding='utf-8')) or {}).get('butler') or {}
    except Exception:
        cfg = {}
    return {
        'enabled': bool(cfg.get('enabled', True)),
        'quiet': str(cfg.get('quiet', '22:00-7:30')),          # 勿扰时段（支持跨午夜）
        'briefs': list(cfg.get('briefs', ['07:30', '20:00'])),  # 日两次简报时点
        'notify_max': int(cfg.get('notify_max', 2)),            # 盯催封顶次数（10-25 降为 2：早简报1+晚简报1+盯催≤2/类 已够）
    }


def _in_quiet(now=None):
    """勿扰判断，支持 22:00-7:30 跨午夜写法"""
    now = now or datetime.now()
    span = _cfg()['quiet']
    try:
        a, b = span.split('-')
        ah, am = map(int, a.split(':'))
        bh, bm = map(int, b.split(':'))
    except Exception:
        return False
    cur = now.hour * 60 + now.minute
    start, end = ah * 60 + am, bh * 60 + bm
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end        # 跨午夜


# ═══════════════════════════════════════════════════════════
# 通用扫描器：按声明扫表 → 议程条目
# ═══════════════════════════════════════════════════════════


def _rows(panel_id, limit=120):
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            f'SELECT * FROM panel_{panel_id} ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _date_of(row, fields):
    for f in fields:
        v = str(row.get(f) or '')[:10]
        if len(v) == 10 and v[4] == '-':
            return v
    return ''


def _max_date(rows, fields):
    ds = [d for d in (_date_of(r, fields) for r in rows) if d]
    return max(ds) if ds else ''


# ★ #1508：非面板来源的分组显示名（DECLARATIONS 里没有的特设来源）
_EXTRA_GROUP_NAMES = {'_goals': '🎯 目标'}


def _fmt_rel(days):
    if days == 0:
        return '今天'
    if days == 1:
        return '明天'
    if days < 0:
        return f'过期{abs(days)}天'
    return f'{days}天后'


def _add_item(dedup_key, source_panel, kind, level, title, detail='', ref_row=0, today=None, supersede_prefix='', member=''):
    """★ 2026-10-25 升级：
    1) 同 key 已存在 → 只刷新 open 条目标题（金额/天数等动态值），不重置 notify_count、不新增；
    2) supersede_prefix → 插入成功时自动关闭同前缀的其他 open 条目（新替旧，治 gap 每天累积）。
    ★ #1209 member：触达对象（panel_members.name，空=全家）"""
    today = today or datetime.now().strftime('%Y-%m-%d')
    try:
        conn = _conn()
        cur = conn.execute('''INSERT OR IGNORE INTO butler_agenda
            (dedup_key, date, ts, source_panel, kind, level, title, detail, ref_row, status, notify_count, gen_date, member)
            VALUES(?,?,?,?,?,?,?,?,?,'open',0,?,?)''',
            (dedup_key, today, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             source_panel, kind, level, title, detail, ref_row, today, str(member or '')[:20]))
        inserted = cur.rowcount > 0
        if not inserted:
            conn.execute("UPDATE butler_agenda SET title=? WHERE dedup_key=? AND status='open' AND title<>?",
                         (title, dedup_key, title))
        elif supersede_prefix:
            conn.execute("UPDATE butler_agenda SET status='superseded' WHERE status='open' AND dedup_key LIKE ? AND dedup_key<>?",
                         (supersede_prefix + '%', dedup_key))
        conn.commit()
        conn.close()
        return inserted
    except Exception as e:
        logger.debug(f'[Butler] 落议程失败 {dedup_key}: {e}')
        return False


def _scan_watch(decl, watch, panel_id, today):
    """扫单个 watch 条件，返回 [(dedup_key, title, level, ref_row)]"""
    out = []
    rows = _rows(panel_id)
    if not rows:
        return out
    # ★ #1381：声明了 only_active 的面板（冰箱）只扫「状态=在存」的行——
    #   已消耗行带着旧到期日会被日期型 watch 反复命中（干红吃完了还天天催过期实锤）
    if decl.get('only_active'):
        rows = [r for r in rows if str(r.get('状态') or '').strip() in ('', '在存')]
        if not rows:
            return out
    today_d = datetime.strptime(today, '%Y-%m-%d').date()
    when = watch['when']

    # ── 沉默型：距最后记录 N 天 ──
    if when.startswith('silent_gap:'):
        gap_n = int(when.split(':')[1])
        last = _max_date(rows, watch['field'] + ('created_at',))
        if not last:
            return out
        try:
            gap = (today_d - datetime.strptime(last, '%Y-%m-%d').date()).days
        except ValueError:
            return out
        if gap >= gap_n:
            # ★ #1266（2026-10-29 实测实锤「早就选择完成了为什么总重复」）：key 去日期化。
            #   旧 key 带 today → 每天生成新 dedup_key → 用户 done/dismiss 只对当天那条有效，
            #   第二天新条目照发（car_fuel_1:gap:10-25~10-29 连发 5 天实锤）。
            #   改用「最后记录日期」当事件标识：同一段沉默期=同一事件=终身 1 条
            #   （同 #1180 bug A 预算 dedup 思路）。open 状态下标题每天照常刷新天数
            #   （走 _add_item 的 UPDATE 分支，不新增不重置）；done/dismiss 后该沉默期
            #   永不再发，直到真记了新记录（last 变化 → gap<2 不触发；再沉默才起新事件）。
            key = f'{panel_id}:gap:{last}'
            title = watch['title'].format(date=last, days=gap, n=gap)
            out.append((key, title, watch['level'], 0))
        return out  # ★ key 带事件标识，supersede 前缀仅用于收编旧格式残留的 open 条目

    # ── 日期型：单字段或 (基准, 周期) 计算型（元组长度=2 才是计算型，长度=1 是普通单字段）──
    calc = watch['field'] if isinstance(watch['field'], tuple) and len(watch['field']) == 2 else None
    for r in rows:
        if calc:
            base = str(r.get(calc[0]) or '')[:10]
            try:
                cyc = int(float(r.get(calc[1]) or 0))
            except Exception:
                cyc = 0
            if len(base) != 10 or cyc <= 0:
                continue
            try:
                dd = datetime.strptime(base, '%Y-%m-%d').date() + timedelta(days=cyc)
            except ValueError:
                continue
        else:
            d = str(r.get(watch['field'][0]) or '')[:10]
            if len(d) != 10:
                continue
            try:
                dd = datetime.strptime(d, '%Y-%m-%d').date()
            except ValueError:
                continue
        days = (dd - today_d).days
        hit = False
        if when == 'expired':
            hit = -7 <= days < 0          # 过期 7 天内催，再老的视为已处理
        elif when.startswith('due_within:'):
            hit = 0 <= days <= int(when.split(':')[1])
        elif when.startswith('past_within:'):
            hit = -int(when.split(':')[1]) <= days < 0
        if not hit:
            continue
        fmt = {k: str(v or '') for k, v in r.items()}
        fmt.update({'date': dd.strftime('%Y-%m-%d'), 'rel': _fmt_rel(days), 'days': abs(days)})
        try:
            title = watch['title'].format(**fmt)
        except (KeyError, IndexError):
            title = watch['title']
        key = f"{panel_id}:{r.get('id')}:{dd.strftime('%Y-%m-%d')}"
        out.append((key, title, watch['level'], r.get('id') or 0))
    return out


def _sleep_wake(decl, panel_id, today):
    """沉睡唤醒：模块 N 天无新记录 → 提醒录入，或建议真没用的隐藏（接工具使用率审计）"""
    sw = decl.get('sleep_watch')
    if not sw:
        return None
    rows = _rows(panel_id, 5)
    if not rows:                      # 从来没录过的不管（避免新装机轰炸）
        return None
    last = _max_date(rows, ('日期', 'created_at', 'ts'))
    if not last:
        return None
    try:
        gap = (datetime.strptime(today, '%Y-%m-%d').date()
               - datetime.strptime(last[:10], '%Y-%m-%d').date()).days
    except ValueError:
        return None
    if gap < sw['gap_days']:
        return None
    # ★ 2026-10-25 周频化：key 改 ISO 周标记，一周最多 1 条（治「每天一条沉睡提醒」）
    iso = datetime.strptime(today, '%Y-%m-%d').isocalendar()
    key = f'{panel_id}:sleep:{iso[0]}-W{iso[1]:02d}'
    title = sw['title'].format(n=gap)
    return (key, f'{title}（要继续用它就录一条；真用不上可以在面板中心隐藏它）', 'info', 0)


def scan_all():
    """全量扫描一次，返回本轮新增条目数"""
    today = datetime.now().strftime('%Y-%m-%d')
    if not _cfg()['enabled']:
        return 0
    added = 0
    for panel_id, decl in DECLARATIONS.items():
        for watch in decl.get('watches', []):
            for key, title, level, ref_row in _scan_watch(decl, watch, panel_id, today):
                sp = f'{panel_id}:gap:' if key.startswith(f'{panel_id}:gap:') else ''
                if _add_item(key, panel_id, watch.get('kind', 'watch'), level, title,
                             ref_row=ref_row, today=today, supersede_prefix=sp):
                    added += 1
        sw = _sleep_wake(decl, panel_id, today)
        if sw and _add_item(sw[0], panel_id, 'sleep', sw[2], sw[1], today=today,
                            supersede_prefix=f'{panel_id}:sleep:'):
            added += 1
    # ── 预算盯催（记账特殊逻辑：走 goal_manager，0 token 本地计算）──
    added += _budget_watch(today)
    # ── ★ #1356（2026-11-01 定版拍板）首启看图能力检测（A）：三条看图路全空才推，
    #    按显存分流推荐；dedup_key 终身 1 条不骚扰 ──
    added += _vision_setup_watch(today)
    # ── ★ #1508（2026-11-13）目标到期自动验收卡：本地查指标 0 token，达标/掉队推到眼前 ──
    added += _goal_audit_watch(today)
    return added


def _goal_audit_watch(today):
    """★ #1508（2026-11-13）目标到期自动验收卡。
    心跳每日扫：活跃目标的 pending 节点 due<=today（含过期）→ 本地查指标
    （audit_node 同源 query_metric，0 token）→ 验收卡进议程。
    dedup_key = goal_audit:{gid}:{idx}:{due}：同节点同 due 期终身 1 张；
    办完/忽略后不再发；延期后 due 变化自动起新卡（旧卡随动作关闭）。
    前端 kind='goal_audit' 渲染三按钮：✓结项 / 延期一周 / 按实际——走 /action 新 op
    （goal_pass / goal_defer / goal_keep），全部本地函数 0 外脑 token。
    铁律：只摆数据+给选项，拍板永远老谭。"""
    try:
        from src.core.goal_manager import list_goals, query_metric
    except Exception:
        return 0
    added = 0
    try:
        for g in (list_goals(status='active', limit=200) or []):
            plan = g.get('plan') or []
            for i, node in enumerate(plan):
                if node.get('status') != 'pending':
                    continue
                due = node.get('due', '')
                if not due or due > today:
                    continue
                done, target, metric = query_metric(g['id'], i)
                ok = done >= target
                key = f"goal_audit:{g['id']}:{i}:{due}"
                if ok:
                    title = f"\U0001F389 目标「{g['title']}」节点{i + 1}到期——已达标（{done}/{target}）"
                    detail = '点「✓ 结项」把节点记完成，后续节点照常推进。'
                    level = 'info'
                else:
                    m = '（手动指标，以你自己判断为准）' if metric == 'manual' else ''
                    title = f"\u26A0\uFE0F 目标「{g['title']}」节点{i + 1}到期未达标（{done}/{target}）{m}"
                    detail = ('「✓ 结项」=认定完成；「延期一周」=本节点及后续整体顺延 7 天；'
                              '「按实际」=把目标降到已完成数并结项。不处理则明天继续提醒。')
                    level = 'urge'
                if _add_item(key, '_goals', 'goal_audit', level, title,
                             detail=detail, ref_row=i, today=today):
                    added += 1
    except Exception as e:
        logger.debug(f'[Butler] 目标验收卡失败: {e}')
    return added


def _vision_setup_watch(today):
    """★ #1356 首启检测（A）：机器没有任何看图能力时，管家议程推一条安装建议。
    分流规则（已定版）：
      - 显存 ≥4GB → 推本地 HarvenEye 视觉模型档位（按 catalog.fit_list 实配，用户自选）；
      - 显存 <4GB → 绝不推荐本地（装了也跑不动），只推外脑零下载方案；
      - 外脑已配 key 且显存不足 → 用户已有可行路径，不打扰；
    dedup_key 带显存整档：同一台机器终身 1 条（done/dismiss 后不再发，换硬件才重新评估）。
    铁律 0：只推荐不动手，装不装永远用户说了算。"""
    try:
        from src.core.image_processor import vision_readiness
        r = vision_readiness()
    except Exception:
        return 0
    # 已有本地看图路（理解槽通道 / 已装 Ollama 视觉模型）→ 免打扰
    if r.get('understand_channel') or r.get('local_models'):
        return 0
    vram = float(r.get('vram_gb') or 0)
    tier = 'local' if vram >= 4 else 'cloud'
    if tier == 'cloud' and r.get('external_vision'):
        return 0   # 显存不足但外脑已配置：切过去就能用，无需推荐
    key = f'vision_setup:{tier}:{int(vram)}'
    if tier == 'local':
        try:
            _fits = ' / '.join(f"{e['label'].split('（')[0]} 约{e['size_gb']:g}GB" for e in r.get('fit', [])[:2])
        except Exception:
            _fits = ''
        title = f'🎮 你的显卡显存 {vram:g}GB，能装本地看图引擎（{_fits or "到视觉引擎页看档位"}）'
        detail = '装好后拍照、发图哈文就能看懂（图片不出门）。到 设置→视觉引擎 页选档位安装；不装也不影响其他功能。'
        if r.get('external_vision'):
            detail += ' 你已配外脑，切外脑模式也能看图——装本地则更快更私密，选哪种由你。'
    else:
        title = '🖼️ 想让我看图？配一个外脑就行（零下载）'
        detail = ('你的电脑显存不适合装本地视觉模型（装了也跑不动，不推荐）。'
                  '到 设置→模型中心 配置一个外脑（如智谱GLM），发图走云端识别。')
    return 1 if _add_item(key, '_butler', 'watch', 'info', title, detail=detail, today=today) else 0


def _budget_watch(today):
    """★ 2026-10-25 bug A 修复：dedup_key 改用目标 id+指标+档位（over/near），
    不含动态金额 → 同一超支事件终身只一条议程；金额变化时 UPDATE title 刷新，
    near→over 升级时旧档位条目自动 superseded（新替旧）。"""
    try:
        from src.core.coach_engine import check_spend_goals
        alerts = check_spend_goals()
    except Exception:
        return 0
    added = 0
    conn = None
    for a in alerts:
        key = f"budget:{a['goal_id']}:{a['metric']}:{a['kind']}"
        try:
            conn = conn or _conn()
            row = conn.execute('SELECT id, title FROM butler_agenda WHERE dedup_key=?', (key,)).fetchone()
            if row:
                # 已存在：只刷新标题里的动态金额，不新增、不重置 notify_count（不再反复出声）
                if row['title'] != a['title']:
                    conn.execute('UPDATE butler_agenda SET title=? WHERE id=?', (a['title'], row['id']))
                    conn.commit()
                continue
        except Exception as e:
            logger.debug(f'[Butler] budget 查重失败 {key}: {e}')
        if _add_item(key, 'accounting_daily_1', 'budget', 'urge', a['title'], today=today):
            added += 1
            # 新替旧：同目标另一档位（near→over 升级等）的旧 open 条目关掉
            try:
                conn = conn or _conn()
                conn.execute("""UPDATE butler_agenda SET status='superseded'
                                WHERE source_panel='accounting_daily_1' AND kind='budget'
                                  AND status='open' AND dedup_key<>? AND dedup_key LIKE ?""",
                             (key, f"budget:{a['goal_id']}:%"))
                conn.commit()
            except Exception as e:
                logger.debug(f'[Butler] budget 新替旧失败 {key}: {e}')
    if conn:
        try:
            conn.close()
        except Exception:
            pass
    return added


# ═══════════════════════════════════════════════════════════
# 分发：urge 出声（TTS+横幅），同类合并、勿扰静默、封顶降级
# ═══════════════════════════════════════════════════════════


def _distribute():
    """把未触达的 urge 条目分发出去（同类合并：同 panel 同 kind 只播第一条+数量）"""
    cfg = _cfg()
    if not cfg['enabled'] or _in_quiet():
        return
    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM butler_agenda WHERE status='open' AND level='urge' AND notify_count < ?",
        (cfg['notify_max'],)).fetchall()]
    conn.close()
    if not rows:
        return
    try:
        from src.core.reminder_daemon import push_notification
    except Exception:
        return
    # 同类合并：按 (panel,kind) 分组，每组只出声一条
    groups = {}
    for r in rows:
        groups.setdefault((r['source_panel'], r['kind']), []).append(r)
    for (panel, kind), items in groups.items():
        first = items[0]
        name = DECLARATIONS.get(panel, {}).get('name') or panel
        if len(items) > 1:
            text = f'{name}有 {len(items)} 件事要处理：{first["title"]}'
        else:
            text = first['title']
        try:
            push_notification(text, item_id=first['id'])
        except Exception as e:
            logger.debug(f'[Butler] 分发失败: {e}')
    # 触达计数一次性抬升（无论 TTS 是否真出声，算一次盯催）
    try:
        conn = _conn()
        conn.executemany('UPDATE butler_agenda SET notify_count = notify_count + 1 WHERE id=?',
                         [(r['id'],) for r in rows])
        conn.commit()
        conn.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# 日两次简报（幂等补跑）
# ═══════════════════════════════════════════════════════════


def _greeting_prefix():
    """★ #1421（2026-11-04 实测实锤）：称呼+时段动态生成，不再写死「早上好开发者」。
    - 称呼读 user_profile.name（默认「主人」，用户设置里改名即跟随）——每个用户不都姓谭
    - 时段按发出生时刻判断（简报有幂等补跑：下午开机补发早简报，不能再说「早上好」）"""
    now = datetime.now()
    h = now.hour
    if 5 <= h < 11:
        greet = '早上好'
    elif 11 <= h < 14:
        greet = '中午好'
    elif 14 <= h < 18:
        greet = '下午好'
    else:
        greet = '晚上好'
    name = '主人'
    try:
        from src.models.database import get_user_profile
        p = get_user_profile()
        if p and (p.get('name') or '').strip():
            name = p['name'].strip()
    except Exception:
        pass
    return f'{greet}{name}，'


def build_briefing(slot='morning'):
    """生成简报文本（纯模板 0 token）。morning=今日议程，evening=今日小结+明日预告"""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = _conn()
    # ★ 2026-11-12 A 方案②：排除 briefing——旧简报自己不是「事项」，
    #   不排除就嵌套滚雪球（title 越滚越长，顺带一看 15 件实锤）
    open_items = [dict(r) for r in conn.execute(
        "SELECT * FROM butler_agenda WHERE status='open' AND date<=? AND kind<>'briefing' ORDER BY level DESC, id", (today,)).fetchall()]
    conn.close()
    urges = [i for i in open_items if i['level'] == 'urge']
    infos = [i for i in open_items if i['level'] != 'urge']
    lines = []
    if urges:
        lines.append('要紧的 ' + str(len(urges)) + ' 件：')
        lines += ['· ' + i['title'] for i in urges[:5]]
    if infos:
        lines.append('顺带一看 ' + str(len(infos)) + ' 件：')
        lines += ['· ' + i['title'] for i in infos[:3]]
    if not lines:
        lines = ['今天家里一切正常，没有要操心的事。']
    prefix = _greeting_prefix() if slot == 'morning' else '今天收尾，'
    text = prefix + '\n'.join(lines)
    return {'success': True, 'slot': slot, 'date': today,
            'text': text, 'urge_count': len(urges), 'info_count': len(infos),
            'items': [i['title'] for i in (urges[:5] + infos[:3])]}


def _briefing_if_due():
    """心跳里调：到简报时点且当天没发过 → 生成并 TTS 一句摘要（勿扰时段跳过，之后补）"""
    cfg = _cfg()
    if not cfg['enabled'] or _in_quiet():
        return
    now = datetime.now()
    hhmm = now.strftime('%H:%M')
    today = now.strftime('%Y-%m-%d')
    for idx, t in enumerate(cfg['briefs']):
        slot = 'morning' if idx == 0 else 'evening'
        key = f'brief_{slot}_date'
        if hhmm >= t and _state_get(key) != today:
            br = build_briefing(slot)
            _add_item(f'brief:{slot}:{today}', '_butler', 'briefing', 'info',
                       br['text'].replace('\n', ' ｜ ')[:200], today=today)
            _state_set(key, today)
            try:
                from src.core.reminder_daemon import push_notification
                head = br['text'].split('\n')[0]
                total = br['urge_count'] + br['info_count']
                if total > 0:
                    push_notification(f'{head}共 {total} 件事，详情看管家卡', item_id=f'brief_{slot}_{today}')
                else:
                    push_notification(head, item_id=f'brief_{slot}_{today}')
            except Exception as e:
                logger.debug(f'[Butler] 简报播报失败: {e}')
            logger.info(f'[Butler] {slot} 简报已生成')


# ═══════════════════════════════════════════════════════════
# 心跳线程（30s 一拍，全量扫描 <10ms）
# ═══════════════════════════════════════════════════════════
_butler_started = False


def _cleanup_old(days=30):
    """议程表只留 30 天，防无限膨胀
    ★ 2026-11-12 A 方案（老谭拍板，简报霸屏/自嵌套根治）：
    简报条目当日有效——昨日及更早的 briefing 自动归档 done。
    实锤：简报落表后永远 open → 每天堆 2 条「早上好主人」长文霸屏议程，
    且 build_briefing 把旧简报当事项嵌套（「顺带一看 15 件」里 14 件是旧简报自己）。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        conn = _conn()
        conn.execute('DELETE FROM butler_agenda WHERE date < ?', (cutoff,))
        cur = conn.execute(
            "UPDATE butler_agenda SET status='done' WHERE kind='briefing' AND status='open' AND date < ?",
            (today,))
        if cur.rowcount:
            logger.info(f'[Butler] 昨日简报自动归档: {cur.rowcount} 条')
        conn.commit()
        conn.close()
    except Exception:
        pass


def _loop():
    last_cleanup = ''
    while True:
        try:
            scan_all()
            _distribute()
            _briefing_if_due()
            today = datetime.now().strftime('%Y-%m-%d')
            if last_cleanup != today:
                _cleanup_old()
                # ★ #1376（2026-11-02 定版拍板）照片 30 天策略：每日顺手清一次消耗副本图
                try:
                    from src.core.fridge_hub import cleanup_expired_photos
                    cleanup_expired_photos(30)
                except Exception as _fe:
                    logger.debug(f'[Butler] 照片30天清理跳过: {_fe}')
                # ★ #1377（2026-11-02 定版拍板 C）账单相册每日自动分析：
                #   扫未标记账单照→视觉抽取→#913 查重→待确认卡。开关 bills_auto_scan 默认开；
                #   8 点前不跑（半夜重启机器不打扰），8 点前启动的当天不自动扫，可用 A 按钮手动。
                #   防重三板斧在 bills_scan 内部（照片级标记，A/C 共用）。
                try:
                    from src.core.panel_assist import get_assist_settings
                    if (get_assist_settings().get('bills_auto_scan') == 'true'
                            and datetime.now().hour >= 8):
                        from src.core.bills_scan import scan_bills
                        scan_bills(source='auto')
                except Exception as _be:
                    logger.debug(f'[Butler] 账单扫描跳过: {_be}')
                # ★ #1452（2026-11-05）照片归位每日兜底：Eye 离线时入库的图当时归不了位
                #   （词典/CLIP 都要 Eye 底座在线），reauto_place_all 幂等只动「未分类+自动」，
                #   每日重跑一次给它们第二次机会；已归位/手动锁定的图永不被碰。
                try:
                    from src.core.img_store import reauto_place_all
                    _tot, _moved = reauto_place_all()
                    if _moved:
                        logger.info(f'[Butler] #1452 照片归位兜底: {_tot} 张候选，补归位 {_moved} 张')
                except Exception as _re:
                    logger.debug(f'[Butler] 照片归位兜底跳过: {_re}')
                last_cleanup = today
        except Exception as e:
            logger.debug(f'[Butler] 心跳异常: {e}')
        time.sleep(30)


def ensure_butler_loop():
    global _butler_started
    if _butler_started:
        return
    _butler_started = True
    threading.Thread(target=_loop, daemon=True, name='butler-agenda').start()
    logger.info('[Butler] 管家议程心跳已启动（30s 扫描 + 声明分发 + 日两次简报）')


# ═══════════════════════════════════════════════════════════
# 前端 API
# ═══════════════════════════════════════════════════════════
bp = Blueprint('butler_agenda', __name__, url_prefix='/api/butler')

_cache = {'ts': 0.0, 'data': None}
_CACHE_TTL = 300  # 议程快照缓存 5 分钟（token 经济铁律 6）


@bp.route('/state', methods=['GET'])
def api_state():
    """今日议程（open 条目，urge 优先）——管家卡主数据源，5 分钟缓存"""
    now = time.time()
    if _cache['data'] is not None and now - _cache['ts'] < _CACHE_TTL:
        return jsonify(_cache['data'])
    today = datetime.now().strftime('%Y-%m-%d')
    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM butler_agenda WHERE status='open' AND date<=? ORDER BY level DESC, id DESC LIMIT 50",
        (today,)).fetchall()]
    conn.close()
    # 按 panel 分组给前端（四分区页签里「今日议程」的渲染结构）
    groups = {}
    for r in rows:
        g = groups.setdefault(r['source_panel'] or '_butler', {
            'panel_id': r['source_panel'] or '_butler',
            'name': _EXTRA_GROUP_NAMES.get(r['source_panel'])
                    or (DECLARATIONS.get(r['source_panel'], {}).get('name', '管家') if r['source_panel'] else '管家'),
            'items': []})
        g['items'].append({'id': r['id'], 'title': r['title'], 'level': r['level'],
                           'kind': r['kind'], 'date': r['date'], 'ts': r['ts'],
                           'ref_row': r['ref_row'] or 0})
    data = {'success': True, 'quiet': _in_quiet(),
            'urge_count': sum(1 for r in rows if r['level'] == 'urge'),
            'total': len(rows), 'groups': list(groups.values())}
    _cache['ts'] = now
    _cache['data'] = data
    return jsonify(data)


@bp.route('/action', methods=['POST'])
def api_action():
    """条目操作：done 打钩 / dismiss 忽略（同类7天静默由 dedup 天然实现）/ snooze 明天再说。
    ★ #1508 新增目标验收卡三动作：goal_pass 结项 / goal_defer 顺延7天 / goal_keep 按实际达标结项"""
    d = request.get_json(silent=True) or {}
    item_id = d.get('id')
    op = d.get('op', '')
    status = {'done': 'done', 'dismiss': 'dismissed', 'snooze': 'open'}.get(op)
    goal_ops = {'goal_pass', 'goal_defer', 'goal_keep'}
    if not item_id or (not status and op not in goal_ops):
        return jsonify({'success': False, 'error': '参数缺 id 或 op 非法'}), 400
    if op in goal_ops:
        # ★ #1508：从 dedup_key（goal_audit:{gid}:{idx}:{due}）解析目标节点，全本地 0 token
        conn = _conn()
        row = conn.execute('SELECT dedup_key FROM butler_agenda WHERE id=?', (item_id,)).fetchone()
        conn.close()
        key = (row['dedup_key'] if row else '') or ''
        parts = key.split(':')
        if len(parts) < 4 or parts[0] != 'goal_audit':
            return jsonify({'success': False, 'error': '条目不是目标验收卡'}), 400
        gid, idx = parts[1], int(parts[2])
        try:
            from src.core import goal_manager as gm
            if op == 'goal_pass':
                gm.set_node_status(gid, idx, 'done')
            elif op == 'goal_defer':
                g = gm.get_goal(gid)
                node = (g.get('plan') or [])[idx] if g else None
                try:
                    nd = (datetime.strptime(node.get('due', ''), '%Y-%m-%d')
                          + timedelta(days=7)).strftime('%Y-%m-%d')
                except Exception:
                    nd = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
                gm.defer_node(gid, idx, nd)
            else:  # goal_keep：按实际完成数降目标并结项（不达白不达，别硬扛）
                done, _t, _m = gm.query_metric(gid, idx)
                gm.lower_target(gid, idx, max(1, int(done or 0)))
                gm.set_node_status(gid, idx, 'done')
        except Exception as e:
            return jsonify({'success': False, 'error': f'目标更新失败: {e}'}), 500
        conn = _conn()
        conn.execute("UPDATE butler_agenda SET status='done' WHERE id=?", (item_id,))
        conn.commit()
        conn.close()
        _cache['ts'] = 0
        return jsonify({'success': True})
    conn = _conn()
    if op == 'snooze':
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        conn.execute('UPDATE butler_agenda SET date=? WHERE id=?', (tomorrow, item_id))
    else:
        conn.execute("UPDATE butler_agenda SET status=? WHERE id=?", (status, item_id))
    conn.commit()
    conn.close()
    _cache['ts'] = 0          # 立即失效缓存
    return jsonify({'success': True})


@bp.route('/briefing', methods=['GET'])
def api_briefing():
    """即时取简报（不落表不播报，纯查询）"""
    slot = request.args.get('slot', 'morning')
    return jsonify(build_briefing(slot))
