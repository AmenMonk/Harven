# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
# -*- coding: utf-8 -*-
"""★ 2026-10-10 #1060 餐饮洞察核心（纯本地、零 token、零网络）

开发者 2026-10-10 拍板「餐饮扩展 9 件一步到位」的地基模块：
- A1 外卖自动记账 ← parse_takeout()（备注抽金额/店名/件数）
- A2 外卖统计     ← meal_stats()['takeout']（次数/金额/常点店 TOP）
- B3 热量诚实化   ← estimate_calories()（词典粗估，AI 不许再瞎填）
- 吃辣词典       ← spicy_hits()（本地词表判，不上模型）
- C1 冰箱联动     ← takeout_digest()（近 7 天外卖概况，配 expiry_digest 用）
- C2 过敏预警     ← diet_alerts()（外卖菜名 × family_diet_profile 过敏档案）
- C3 今天吃什么   ← suggest_materials()（口味历史+冰箱库存+季节 确定性素材）

设计铁律：
- 所有函数确定性规则，不调模型、不发网络请求；模型只负责「组织人话」（调用方按阵营接 _llm_call）
- 数据真家：data/sqlite/lingzhi.db 表 panel_meal_diary（中文带引号列名，#880 铁律）
- 备注格式实锤（开发者真实数据）：「外卖：店名，已完成，总计¥69.1共5件，下单时间12:21」
"""
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

logger = __import__('logging').getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sqlite" / "lingzhi.db"
MEAL_PANEL = 'meal_diary'

# ══════════════════ 外卖备注解析（A1/A2 地基） ══════════════════

_RE_AMOUNT = re.compile(r'总计\s*[¥￥]?\s*(\d+(?:\.\d+)?)')
_RE_COUNT = re.compile(r'共\s*(\d+)\s*件')
_RE_SHOP = re.compile(r'外卖\s*[：:]\s*(.+?)[，,]')


def parse_takeout(note):
    """从备注解析外卖信息。非外卖备注返回 None。
    返回 {'shop': 店名, 'amount': float, 'count': int}（缺的键给 None/0）。"""
    note = str(note or '').strip()
    if not note or ('外卖' not in note and '总计' not in note):
        return None
    m_amt = _RE_AMOUNT.search(note)
    if not m_amt:
        return None
    m_shop = _RE_SHOP.search(note)
    m_cnt = _RE_COUNT.search(note)
    return {
        'shop': (m_shop.group(1).strip() if m_shop else ''),
        'amount': round(float(m_amt.group(1)), 2),
        'count': int(m_cnt.group(1)) if m_cnt else 0,
    }


# ══════════════════ 吃辣词典（本地判定，零 token） ══════════════════

# 词组级匹配，宁保守不误报：「芝麻/麻酱/麻团」不辣所以不收单字「麻」；
# 「湘/川/水煮」在菜名里出现基本就是湘菜川菜系（小炒黄牛肉→湘 实锤命中）
SPICY_WORDS = (
    '麻辣', '香辣', '酸辣', '剁椒', '泡椒', '椒麻', '藤椒', '辣椒', '辣子',
    '麻辣烫', '麻辣香锅', '串串', '水煮鱼', '水煮牛肉', '水煮肉片',
    '湘', '川菜', '川味', '麻婆',
)


def spicy_hits(text):
    """返回菜名/文本命中的辣味词列表（去重、按词典序）。"""
    text = str(text or '')
    if not text:
        return []
    hits = []
    for w in SPICY_WORDS:
        if w in text and w not in hits:
            hits.append(w)
    return hits


def is_spicy(text):
    return bool(spicy_hits(text))


# ══════════════════ 热量词典（B3 诚实化：粗估+仅供参考） ══════════════════

# 常见食物 → 每份大卡粗估（家常份量，不做精确营养学——照片估重量 AI 干不准，
# 给个量级当参考就是诚实，装精确就是假精致）
_CALORIE_DICT = {
    # 主食
    '米饭': 200, '炒饭': 600, '馒头': 250, '花卷': 220, '包子': 250, '饺子': 400,
    '馄饨': 350, '云吞': 350, '面条': 450, '炸酱面': 500, '牛肉面': 550, '拉面': 500,
    '热干面': 550, '凉面': 450, '米线': 450, '米粉': 400, '河粉': 450, '螺蛳粉': 550,
    '酸辣粉': 500, '粥': 120, '稀饭': 100, '油条': 230, '烧饼': 300, '煎饼': 400,
    '手抓饼': 450, '汉堡': 550, '披萨': 300, '三明治': 350, '卷饼': 450, '炒面': 550,
    '拌面': 480, '盖浇饭': 650, '煲仔饭': 700, '蛋炒饭': 600,
    # 蛋白/硬菜
    '鸡蛋': 80, '茶叶蛋': 80, '煎蛋': 120, '荷包蛋': 110, '蒸蛋': 90,
    '鸡腿': 350, '鸡翅': 150, '炸鸡': 500, '鸡排': 550, '烤鸡': 450, '白切鸡': 380,
    '红烧肉': 600, '排骨': 500, '猪蹄': 550, '猪脚': 550, '牛肉': 450, '牛腩': 500,
    '羊肉': 480, '烤串': 100, '烤肉': 500, '小炒黄牛肉': 450, '回锅肉': 550,
    '鱼香肉丝': 400, '宫保鸡丁': 450, '水煮肉片': 500, '火锅': 800, '烤鱼': 700,
    '鱼': 250, '虾': 200, '蟹': 250, '豆腐': 150, '麻婆豆腐': 350,
    # 蔬菜/清淡
    '青菜': 80, '白菜': 70, '菠菜': 70, '生菜': 60, '西兰花': 80, '沙拉': 150,
    '凉拌': 120, '蒸菜': 130, '汤': 120,
    # 汤粉面食
    '胡辣汤': 200, '疙瘩汤': 250, '紫菜蛋花汤': 60, '西红柿鸡蛋汤': 80, '冬瓜汤': 50,
    # 快餐小食
    '薯条': 330, '玉米杯': 100, '玉米': 150, '可乐': 150, '奶茶': 400, '咖啡': 100,
    '豆浆': 80, '酸奶': 120, '蛋糕': 350, '面包': 280, '饼干': 200, '水果': 100,
    '苹果': 80, '香蕉': 90, '西瓜': 60, '葡萄': 90,
    # 早茶点心
    '小笼包': 300, '生煎': 250, '烧麦': 220, '虾饺': 180, '肠粉': 250, '糯米鸡': 350,
}

# 未命中词典时按字面兜底（量级猜，宁可区间保守）
_FALLBACK_RULES = (
    ('面', 450), ('粉', 450), ('饭', 550), ('粥', 120), ('汤', 120),
    ('鸡', 350), ('鸭', 400), ('鱼', 250), ('虾', 200), ('肉', 450),
    ('牛', 450), ('羊', 450), ('排', 500), ('蛋', 100), ('豆腐', 150),
    ('菜', 150), ('包', 250), ('饺', 400), ('饼', 350), ('串', 100),
)


def estimate_calories(foods_text):
    """按词典粗估一餐热量。返回 (total_kcal, detail, unmatched)
    - total_kcal: int 粗估合计（词典命中取词典值；未命中按字面兜底；全没命中该菜记 0）
    - detail: [(菜名, kcal, 是否词典精确)]
    - unmatched: 完全没着落的菜名列表（诚实呈现，不硬编）"""
    foods_text = str(foods_text or '').strip()
    if not foods_text:
        return 0, [], []
    foods = [f.strip() for f in re.split(r'[、,，;；/]', foods_text) if f.strip()]
    # 去掉「×N」倍数标记转倍数（香辣鸡翅×2 → 鸡翅×2 份）
    total = 0
    detail = []
    unmatched = []
    for f in foods:
        mult = 1
        m_mult = re.search(r'[×xX*]\s*(\d+)', f)
        if m_mult:
            mult = int(m_mult.group(1))
        base_name = re.sub(r'[×xX*]\s*\d+', '', f).strip()
        kcal = None
        exact = False
        # 1) 全名精确命中
        if base_name in _CALORIE_DICT:
            kcal, exact = _CALORIE_DICT[base_name], True
        else:
            # 2) 词典词是菜名子串（ longest match 优先）
            cands = [(w, v) for w, v in _CALORIE_DICT.items() if w in base_name]
            if cands:
                w, v = max(cands, key=lambda x: len(x[0]))
                kcal, exact = v, False
            else:
                # 3) 字面兜底
                for suffix, v in _FALLBACK_RULES:
                    if suffix in base_name:
                        kcal = v
                        break
        if kcal is None:
            unmatched.append(f)
            continue
        total += kcal * mult
        detail.append((f, kcal * mult, exact))
    return total, detail, unmatched


# ══════════════════ 数据访问（panel_meal_diary） ══════════════════

def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _rows(days=None):
    """读餐饮记录（日期倒序）。days=None 全量；给数字则只取最近 N 天。"""
    c = _conn()
    try:
        if days is None:
            sql = 'SELECT * FROM "panel_meal_diary" ORDER BY "日期" DESC, id DESC'
            return [dict(r) for r in c.execute(sql).fetchall()]
        since = (date.today() - timedelta(days=days)).isoformat()
        sql = ('SELECT * FROM "panel_meal_diary" WHERE substr("日期",1,10) >= ? '
               'ORDER BY "日期" DESC, id DESC')
        return [dict(r) for r in c.execute(sql, (since,)).fetchall()]
    finally:
        c.close()


# ══════════════════ A2 统计（meal_stats） ══════════════════

def meal_stats(days=30):
    """近 N 天餐饮统计（A2 + 吃辣 + 热量参考）。
    返回 dict：
    {
      'days': 30, 'records': 总条数,
      'takeout': {'count':次数, 'amount':总金额, 'items':总件数,
                  'shops':[{'shop':店名,'count':次,'amount':元}] 按次数降序},
      'spicy': {'count':吃辣记录数, 'words':[{'word':词,'count':次}] 降序},
      'calories': {'diary_sum':表内估算热量合计, 'dict_est':词典粗估合计, 'estimated':粗估条数},
      'recent': 最近 5 条概览
    }"""
    rows = _rows(days)
    tk_count, tk_amount, tk_items = 0, 0.0, 0
    shops = {}
    spicy_cnt = 0
    spicy_words = {}
    cal_diary, cal_dict, cal_est_n = 0.0, 0, 0
    for r in rows:
        foods = r.get('识别出的食物') or ''
        note = r.get('备注') or ''
        t = parse_takeout(note)
        if t:
            tk_count += 1
            tk_amount += t['amount'] or 0
            tk_items += t['count'] or 0
            if t['shop']:
                s = shops.setdefault(t['shop'], {'count': 0, 'amount': 0.0})
                s['count'] += 1
                s['amount'] += t['amount'] or 0
        hits = spicy_hits(foods) or spicy_hits(note)
        if hits:
            spicy_cnt += 1
            for w in hits:
                spicy_words[w] = spicy_words.get(w, 0) + 1
        cal = r.get('估算热量')
        if isinstance(cal, (int, float)) and cal > 0:
            cal_diary += cal
        elif foods:
            est, _d, _u = estimate_calories(foods)
            if est > 0:
                cal_dict += est
                cal_est_n += 1
    return {
        'days': days,
        'records': len(rows),
        'takeout': {
            'count': tk_count,
            'amount': round(tk_amount, 2),
            'items': tk_items,
            'shops': sorted(
                [{'shop': k, 'count': v['count'], 'amount': round(v['amount'], 2)}
                 for k, v in shops.items()],
                key=lambda x: -x['count']),
        },
        'spicy': {
            'count': spicy_cnt,
            'words': sorted([{'word': k, 'count': v} for k, v in spicy_words.items()],
                            key=lambda x: -x['count'])[:8],
        },
        'calories': {
            'diary_sum': round(cal_diary, 1),
            'dict_est': cal_dict,
            'estimated': cal_est_n,
        },
        'recent': [
            {'日期': r.get('日期'), '餐次': r.get('餐次') or '',
             '食物': (r.get('识别出的食物') or '')[:60], '外卖': bool(parse_takeout(r.get('备注')))}
            for r in rows[:5]
        ],
    }


# ══════════════════ C1 外卖×冰箱联动素材 ══════════════════

def takeout_digest(days=7):
    """近 N 天外卖概况（C1 用：外卖多 + 冰箱临期 → 提醒「先吃冰箱的」）。
    返回 {'count':次数, 'amount':金额, 'busy':bool(近7天≥5次)}"""
    rows = _rows(days)
    n, amt = 0, 0.0
    for r in rows:
        t = parse_takeout(r.get('备注'))
        if t:
            n += 1
            amt += t['amount'] or 0
    return {'days': days, 'count': n, 'amount': round(amt, 2), 'busy': n >= 5}


# ══════════════════ C2 过敏预警 ══════════════════

def diet_alerts(foods_text, note=''):
    """菜名/备注 × family_diet_profile 过敏档案 → 命中预警。
    返回 [{'member':成员, 'allergy':过敏原, 'hit_in':菜名}]"""
    text = str(foods_text or '') + ' ' + str(note or '')
    if not text.strip():
        return []
    alerts = []
    c = _conn()
    try:
        profiles = c.execute('SELECT name, allergies FROM family_diet_profile').fetchall()
    finally:
        c.close()
    for p in profiles:
        raw = (p['allergies'] or '').strip()
        if not raw:
            continue
        for alg in re.split(r'[、,，;；\s]+', raw):
            alg = alg.strip()
            if alg and alg in text:
                alerts.append({'member': p['name'] or '家人', 'allergy': alg,
                               'hit_in': str(foods_text or '')[:60]})
    return alerts


# ══════════════════ C3 今天吃什么（确定性素材） ══════════════════

def suggest_materials():
    """C3「今天吃什么」素材包（确定性，模型只做组织）。
    - fridge: 冰箱在存食材清单（复用 fridge_hub._stock_rows 同口径，自己查避免私有依赖）
    - tastes: 近 30 天吃得最多的菜（口味历史）
    - sparse: 近 30 天没吃过/吃得少的类别
    - season: 当前季节"""
    season = _season()
    # 口味历史：近 30 天食物频次
    rows = _rows(30)
    freq = {}
    for r in rows:
        for f in re.split(r'[、,，;；/]', str(r.get('识别出的食物') or '')):
            f = re.sub(r'[×xX*]\s*\d+', '', f.strip())
            if f:
                freq[f] = freq.get(f, 0) + 1
    top_tastes = sorted(freq.items(), key=lambda x: -x[1])[:10]
    # 冰箱在存食材
    fridge = []
    try:
        c = _conn()
        try:
            for r in c.execute('SELECT "名称","数量","数量单位","分区" FROM "panel_fridge_stock_1" '
                               'WHERE ifnull("状态","在存") NOT IN (\'已吃完\',\'丢弃\',\'过期\')'):
                qty = (str(r['数量'] or '') + str(r['数量单位'] or '')).strip()
                fridge.append({'name': r['名称'], 'qty': qty, 'zone': r['分区'] or ''})
        finally:
            c.close()
    except Exception as e:
        logger.debug(f"[meal_insights] 冰箱食材读取跳过: {e}")
    return {
        'season': season,
        'top_tastes': [{'food': k, 'count': v} for k, v in top_tastes],
        'fridge': fridge,
    }


def _season(now=None):
    m = (now or datetime.now()).month
    if m in (3, 4, 5):
        return '春季'
    if m in (6, 7, 8):
        return '夏季'
    if m in (9, 10, 11):
        return '秋季'
    return '冬季'


# ══════════════════ A1 外卖自动记账联动（确认卡两段门，不直接入账） ══════════════════

def takeout_accounting_row(data):
    """从餐饮记录字段抽「待确认记账记录」。非外卖返回 None。
    data 为餐饮面板一条记录的字段 dict（日期/识别出的食物/备注）。
    生成记账行走 accounting_daily_1 通用口径（#913 查重 + 分类兜底都在入库链上）。"""
    t = parse_takeout((data or {}).get('备注'))
    if not t:
        return None
    d = str((data or {}).get('日期') or '').strip()[:10] or date.today().isoformat()
    foods = str((data or {}).get('识别出的食物') or '').strip()
    return {
        '日期': d,
        '项目': (t['shop'] or '外卖') + ' 外卖',
        '金额': t['amount'],
        '分类': '餐饮',
        '方向': '支出',
        'kind': 'flow',
        '来源': '哈文助手·餐饮联动',
        '备注': (f"共{t['count']}件 · {foods[:40]}" if t['count'] or foods else '来自餐饮记录'),
    }


def maybe_link_accounting(panel_id, data):
    """餐饮记录入库后的外卖→记账联动（A1）。
    挂点：①panel_routes 手填入库 ②panel_assist 默录入库。
    流程：抽外卖金额 → 记账面板同日同金额查重 → 生成「待确认卡」（add_pending），
    用户在助手对话框点确认才真正入账（两段门，绝不偷偷记账）。
    返回提示文案（'' = 没有联动）。"""
    if panel_id != MEAL_PANEL:
        return ''
    try:
        row = takeout_accounting_row(data)
        if not row or not row.get('金额'):
            return ''
        from src.core.panel_manager import get_panel_manager
        ACC = 'accounting_daily_1'
        mgr = get_panel_manager()
        if mgr.find_duplicate(ACC, row):
            logger.info(f"[meal_insights] 外卖记账联动：同日同金额已存在，跳过（{row.get('项目')} {row.get('金额')}）")
            return ''
        from src.core.panel_assist import add_pending, list_pending
        # ★ 2026-10-10 重复卡防护：待确认队列里已有同日同金额同项目的外卖卡 → 不再生成
        # （餐饮表无金额列，#913 查重拦不住重复录入；记账表查重只挡已入账的，pending 中的管不到）
        for p in list_pending(ACC):
            for pr in (p.get('records') or []):
                if (str(pr.get('项目') or '') == row['项目']
                        and str(pr.get('日期') or '') == row['日期']
                        and abs(float(pr.get('金额') or 0) - row['金额']) < 0.01):
                    logger.info(f"[meal_insights] 外卖记账联动：待确认队列已有同单，跳过（{row['项目']}）")
                    return ''
        add_pending(ACC, 'accounting', [row], source_text='餐饮外卖自动记账')
        logger.info(f"[meal_insights] 外卖记账联动：已生成待确认卡 {row.get('项目')} ¥{row.get('金额')}")
        return (f"💰 检测到外卖 {row['项目']} ¥{row['金额']}，"
                f"已生成一张「记账待确认卡」（在下方助手对话里，点确认才入账，不想要点忽略）")
    except Exception as e:
        logger.warning(f"[meal_insights] 外卖记账联动跳过: {e}")
        return ''
