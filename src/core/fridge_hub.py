# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
# -*- coding: utf-8 -*-
"""★ 2026-10-05 #1012~#1015 冰箱深化后端（定版拍板「全部开发」）

四组能力（全部围绕 panel_fridge_stock_1 表）：
- #1012 家人饮食档案：family_diet_profile 表（成员/过敏/忌口/喜好/慢病），
  并入档案中枢域（hub_context_diet() 供查询链预查注入）
- #1013 食谱生成：库存临期优先 + 家人档案硬排除过敏 + 当季 + 地区 →
  ★ 跟随阵营（用户当前选谁谁出：外脑/本地，不切换不回落，gate=answer_allow_cloud）
- #1014 买菜建议：要补货/吃完了/低库存 → 清单；记账近 3 天联动标「刚买过」防重复；
  附近 30 天常买参考（记账消费统计）
- #1015 拍照识菜入库：照片 → _camp_vision（#893 分阵营）→ 多条记录 JSON →
  前端确认卡 → 走现有 POST /api/panels/<id>/data 逐条入库

#1011 临期主动提醒的确定性清单也在这里（expiry_digest），由 panel_summary 每日调度调用。
"""
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('fridge_hub', __name__, url_prefix='/api/panels/fridge')

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'sqlite' / 'lingzhi.db'
FRIDGE_TABLE = 'panel_fridge_stock_1'
ACC_TABLE = 'panel_accounting_daily_1'

ZONE_OPTIONS = ['冷藏', '冷冻', '门格', '零食饮料', '粮食干货', '调味油盐']

_lock = None  # 惰性建（轻量表，直接短连接 + busy_timeout 即可）


def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


# ══════════════════════════ #1012 家人饮食档案 ══════════════════════════

def _ensure_diet():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS family_diet_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    allergies TEXT DEFAULT '',
                    dislikes  TEXT DEFAULT '',
                    likes     TEXT DEFAULT '',
                    conditions TEXT DEFAULT '',
                    notes     TEXT DEFAULT '',
                    updated_at TEXT)""")


def _diet_members():
    _ensure_diet()
    with _conn() as c:
        rows = c.execute("SELECT * FROM family_diet_profile ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@bp.route('/diet', methods=['GET'])
def diet_list():
    try:
        return jsonify({'success': True, 'members': _diet_members()})
    except Exception as e:
        logger.error(f"[fridge] diet 查询失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/diet', methods=['POST'])
def diet_save():
    """新增/更新一位成员。body: {id?, name, allergies, dislikes, likes, conditions, notes}"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        name = str(d.get('name') or '').strip()[:20]
        if not name:
            return jsonify({'success': False, 'error': '成员称呼不能为空'}), 400
        fields = {k: str(d.get(k) or '').strip()[:400]
                  for k in ('allergies', 'dislikes', 'likes', 'conditions', 'notes')}
        _ensure_diet()
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        with _conn() as c:
            if d.get('id'):
                c.execute("""UPDATE family_diet_profile SET name=?, allergies=?, dislikes=?,
                            likes=?, conditions=?, notes=?, updated_at=? WHERE id=?""",
                          (name, fields['allergies'], fields['dislikes'], fields['likes'],
                           fields['conditions'], fields['notes'], now, int(d['id'])))
            else:
                c.execute("""INSERT INTO family_diet_profile
                            (name, allergies, dislikes, likes, conditions, notes, updated_at)
                            VALUES (?,?,?,?,?,?,?)""",
                          (name, fields['allergies'], fields['dislikes'], fields['likes'],
                           fields['conditions'], fields['notes'], now))
            c.commit()
        return jsonify({'success': True, 'members': _diet_members()})
    except Exception as e:
        logger.error(f"[fridge] diet 保存失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/diet/delete', methods=['POST'])
def diet_delete():
    try:
        d = request.get_json(force=True, silent=True) or {}
        mid = int(d.get('id') or 0)
        if not mid:
            return jsonify({'success': False, 'error': '缺少 id'}), 400
        _ensure_diet()
        with _conn() as c:
            c.execute("DELETE FROM family_diet_profile WHERE id=?", (mid,))
            c.commit()
        return jsonify({'success': True, 'members': _diet_members()})
    except Exception as e:
        logger.error(f"[fridge] diet 删除失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def hub_context_diet() -> str:
    """档案中枢「饮食域」预查文本（查询链 hub_quick_context 扩展点）：全家过敏/忌口一览，只读本地。"""
    try:
        ms = _diet_members()
        if not ms:
            return ''
        lines = ['【全家饮食档案】']
        for m in ms:
            parts = [m['name']]
            if m['allergies']:
                parts.append(f"过敏:{m['allergies']}")
            if m['dislikes']:
                parts.append(f"忌口:{m['dislikes']}")
            if m['likes']:
                parts.append(f"喜好:{m['likes']}")
            if m['conditions']:
                parts.append(f"慢病:{m['conditions']}")
            lines.append('；'.join(parts))
        return '\n'.join(lines)
    except Exception:
        return ''


# ══════════════════════════ 库存读取（共用） ══════════════════════════

def _stock_rows(active_only=True):
    """读冰箱表 → [{...中文列..., _days:剩余天数或None}]；表不存在返回 []"""
    try:
        with _conn() as c:
            cols = [r[1] for r in c.execute(
                f'PRAGMA table_info("{FRIDGE_TABLE}")').fetchall()]
            if not cols:
                return []
            rows = [dict(r) for r in c.execute(
                f'SELECT * FROM "{FRIDGE_TABLE}" ORDER BY id DESC').fetchall()]
    except Exception as e:
        logger.error(f"[fridge] 读库存失败: {e}")
        return []
    today = date.today()
    out = []
    for r in rows:
        st = str(r.get('状态') or '').strip()
        if active_only and st and st != '在存':
            continue
        days = None
        raw = str(r.get('到期日') or '').strip()
        if raw:
            m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', raw)
            if m:
                try:
                    days = (date(int(m.group(1)), int(m.group(2)), int(m.group(3))) - today).days
                except ValueError:
                    days = None
        r['_days'] = days
        r['_st'] = st or '在存'
        out.append(r)
    return out


def expiry_digest():
    """#1011 确定性临期清单（不花钱、不算模型）。返回 (lines:list[str], n_expired, n_soon)。"""
    lines, n_exp, n_soon = [], 0, 0
    for r in _stock_rows(active_only=True):
        d = r['_days']
        if d is None:
            continue
        nm = r.get('名称') or '（未命名）'
        qty = (str(r.get('数量') or '') + str(r.get('数量单位') or '')).strip()
        zone = r.get('分区') or ''
        tail = (f'（{qty}）' if qty else '') + (f'·{zone}' if zone else '')
        if d < 0:
            lines.append(f'⚫ {nm}{tail}：已过期 {-d} 天，建议处理掉')
            n_exp += 1
        elif d <= 3:
            lines.append(f'🔴 {nm}{tail}：还剩 {d} 天，这两天吃掉')
            n_soon += 1
        elif d <= 7:
            lines.append(f'🟡 {nm}{tail}：还剩 {d} 天')
            n_soon += 1
    return lines, n_exp, n_soon


# ══════════════════════════ #1013 食谱生成（跟随阵营） ══════════════════════════

def _season_of(now=None):
    m = (now or datetime.now()).month
    if m in (3, 4, 5):
        return '春季'
    if m in (6, 7, 8):
        return '夏季'
    if m in (9, 10, 11):
        return '秋季'
    return '冬季'


def _recent_eat_hint():
    """★ #1370 近期消耗偏好注入食谱（有流水才注入，没有返回空串）。"""
    try:
        s = consume_summary(days=7)
        if not s or not s.get('top'):
            return ''
        names = '、'.join(f'{n}（{c}次）' for n, c in s['top'])
        return (f"【家里近7天吃动向】吃掉 {s['eaten']} 份、处理掉 {s['trashed']} 份，"
                f"吃得最多：{names}。搭配时别总重复这几样，营养换着来。\n")
    except Exception:
        return ''


@bp.route('/recipe', methods=['GET'])
def recipe_last():
    """回看上次生成的食谱（panel_ai_history 里 question=【食谱】开头）"""
    try:
        d = recipe_last_data()
        return jsonify(d)
    except Exception as e:
        logger.error(f"[fridge] recipe 回看失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def recipe_last_data():
    """★ #1034（2026-10-06）抽数：食谱回看核心逻辑（不依赖 Flask 请求上下文，
    供 route 与 cloud_link relay 的 fridge.view 共用）"""
    from src.core.panel_assist import _conn as _pa_conn
    with _pa_conn() as c:
        row = c.execute("""SELECT question, text, model, created_at FROM panel_ai_history
                        WHERE panel_id='fridge_stock_1' AND question LIKE '【食谱】%'
                        ORDER BY id DESC LIMIT 1""").fetchone()
    if not row:
        return {'success': True, 'recipe': None}
    return {'success': True, 'recipe': {
        'text': row['text'], 'model': row['model'], 'created_at': row['created_at']}}


@bp.route('/recipe', methods=['POST'])
def recipe_make():
    """生成今日食谱。★ 跟随阵营：用户当前选外脑走外脑、选本地走本地，不切换不回落。"""
    try:
        stock = _stock_rows(active_only=True)
        if not stock:
            return jsonify({'success': False,
                            'error': '冰箱台账还是空的，先在「库存」里记几样食材，我才能照着出菜谱'}), 200

        # 库存清单（临期排前）
        def _sort_key(r):
            return (0 if (r['_days'] is not None and r['_days'] <= 3) else 1, -(r['_days'] or 999))
        stock.sort(key=_sort_key)
        stock_lines = []
        for r in stock[:40]:
            nm = r.get('名称') or '？'
            qty = (str(r.get('数量') or '') + str(r.get('数量单位') or '')).strip()
            zone = r.get('分区') or ''
            head = f"- {nm}"
            if qty or zone:
                head += f"（{qty}{('，' + zone) if zone else ''}）"
            d = r['_days']
            if d is not None:
                head += f"，到期日剩 {d} 天" + ('【临期，优先用掉】' if d <= 3 else '')
            stock_lines.append(head)

        # 家人档案（★ #1073 扩健康画像：体质宜忌/不爱吃但要补也进来）
        diet_lines = []
        for m in _diet_members():
            seg = f"- {m['name']}"
            if m.get('allergies'):
                seg += f"｜过敏：{m['allergies']}（任何菜里绝对不能出现）"
            if m.get('dislikes'):
                seg += f"｜忌口：{m['dislikes']}"
            if m.get('constitution'):
                seg += f"｜体质：{m['constitution']}"
            if m.get('avoid_eat'):
                seg += f"｜易上火别吃：{m['avoid_eat']}"
            if m.get('need_eat'):
                seg += f"｜不爱吃但要补：{m['need_eat']}（变着花样安排，做得好吃点）"
            if m.get('likes'):
                seg += f"｜喜好：{m['likes']}"
            if m.get('conditions'):
                seg += f"｜慢病/注意：{m['conditions']}"
            diet_lines.append(seg)

        from src.utils.config import get_config
        region = str(get_config().get('family.region') or '').strip()

        system = (
            "你是贴心的家庭冰箱管家，根据家里现有食材安排一日三餐。"
            "铁律：1) 任何家庭成员过敏的食材，绝对不允许出现在任何菜里；"
            "2) 忌口尽量避开，慢病注意少油少盐少糖；"
            "3) 体质标了易上火别吃的，别安排那些食材（家人吃着舒服最重要）；"
            "4) 「不爱吃但要补」的食材每周尽量安排到，换个做法让孩子/家人吃得下去；"
            "5) 优先消耗临期食材（标了【临期，优先用掉】的必须安排）；"
            "6) 只用库存清单里的食材为主料，缺的辅料在最后列「需要买的」；"
            "7) 结合当季吃时令；8) 不确定的信息不编造。")
        prompt = (
            f"【今天是】{datetime.now().strftime('%Y年%m月%d日')}（{_season_of()}）"
            + (f"，所在地区：{region}" if region else "") + "\n"
            f"【冰箱现有库存】\n" + "\n".join(stock_lines) + "\n"
            + ("【家人饮食档案】\n" + "\n".join(diet_lines) + "\n" if diet_lines else "")
            + (_recent_eat_hint() or "")
            + "\n请生成今天的家的三餐建议：早/午/晚各 1~2 个菜，"
              "每个菜写清用到哪些库存食材、简单做法一句话；"
              "最后给「需要买的」小清单（没有就写无需采购）。"
              "语言家常一点，别啰嗦。")
        from src.core.panel_assist import _llm_call, _save_ai_result
        text, model, err = _llm_call(system, prompt, max_tokens=1300,
                                     temperature=0.5, gate='answer_allow_cloud')
        if err or not text:
            msg = err or '模型没有返回内容'
            return jsonify({'success': False,
                            'error': f'食谱没生成出来：{msg}（用的就是你当前选的模型，可以在设置里换一个再试）'}), 200
        try:
            _save_ai_result('fridge_stock_1', f'【食谱】{datetime.now().strftime("%m-%d %H:%M")}', text, model)
        except Exception as e:
            logger.warning(f"[fridge] 食谱落库失败: {e}")
        return jsonify({'success': True, 'text': text, 'model': model})
    except Exception as e:
        logger.error(f"[fridge] recipe 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════ #1014 买菜建议（联动记账防重复） ══════════════════════════

@bp.route('/buylist', methods=['GET'])
def buylist():
    try:
        d = buylist_data()
        return jsonify(d)
    except Exception as e:
        logger.error(f"[fridge] buylist 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def buylist_data():
    """★ #1034（2026-10-06）抽数：买菜清单核心逻辑（不依赖 Flask 请求上下文，
    供 route 与 cloud_link relay 的 fridge.view 共用）。返回 dict。"""
    with _conn() as c:
        cols = [r[1] for r in c.execute(
            f'PRAGMA table_info("{FRIDGE_TABLE}")').fetchall()]
        if not cols:
            return {'success': True, 'need': [], 'regular': [], 'note': '冰箱台账还没建'}
        rows = [dict(r) for r in c.execute(
            f'SELECT * FROM "{FRIDGE_TABLE}"').fetchall()]

        # 记账联动：近 3 天支出项目（防重复）+ 近 30 天常买（参考）
        recent, regular = [], []
        acc_cols = [r[1] for r in c.execute(
            f'PRAGMA table_info("{ACC_TABLE}")').fetchall()]
        if acc_cols and '方向' in acc_cols:
            d3 = (date.today() - timedelta(days=3)).isoformat()
            d30 = (date.today() - timedelta(days=30)).isoformat()
            recent = [dict(r) for r in c.execute(
                f'''SELECT "日期", "项目", "金额" FROM "{ACC_TABLE}"
                    WHERE "方向"='支出' AND "日期" >= ? ORDER BY "日期" DESC''',
                (d3,)).fetchall()]
            regular = [dict(r) for r in c.execute(
                f'''SELECT "项目", COUNT(*) AS n, SUM("金额") AS amt FROM "{ACC_TABLE}"
                    WHERE "方向"='支出' AND "日期" >= ? AND "项目" != ''
                    GROUP BY "项目" ORDER BY n DESC LIMIT 10''',
                (d30,)).fetchall()]

    need, seen = [], set()
    for r in rows:
        nm = str(r.get('名称') or '').strip()
        if not nm or nm in seen:
            continue
        st = str(r.get('状态') or '').strip() or '在存'
        qty_raw, thr_raw = r.get('数量'), r.get('低库存提醒少于')
        try:
            qty = float(qty_raw) if qty_raw not in (None, '') else None
        except (TypeError, ValueError):
            qty = None
        try:
            thr = float(thr_raw) if thr_raw not in (None, '') else 0.0
        except (TypeError, ValueError):
            thr = 0.0
        unit = str(r.get('数量单位') or '').strip()
        zone = str(r.get('分区') or '').strip()
        reason = ''
        if st == '要补货':
            reason = '你标记了要补货'
        elif st == '吃完了':
            reason = '已经吃完'
        elif st == '在存' and thr > 0 and qty is not None and qty <= thr:
            reason = f'库存只剩 {qty:g}{unit}（低于 {thr:g}）'
        if not reason:
            continue
        seen.add(nm)
        it = {'name': nm, 'qty': ('' if qty is None else f'{qty:g}{unit}'),
              'zone': zone, 'reason': reason, 'bought_recently': ''}
        # 防重复：近 3 天记账里买过同名/名字互相包含的 → 提示先确认
        for rc in recent:
            rit = str(rc.get('项目') or '')
            if rit and (rit in nm or nm in rit):
                it['bought_recently'] = f"{str(rc.get('日期') or '')} 记账里刚买过（{rit}），先确认是否已补"
                break
        need.append(it)
    # 临期/低库存排前（要补货标记优先）
    need.sort(key=lambda x: (0 if '标记' in x['reason'] else 1, x['name']))
    regular = [{'item': r.get('项目') or '', 'n': r.get('n') or 0,
                'amt': round(float(r.get('amt') or 0), 1)} for r in regular
               if r.get('item')]
    return {'success': True, 'need': need, 'regular': regular}


# ══════════════════════════ #1015 拍照识菜入库（分阵营视觉） ══════════════════════════

# ── ★ #1035（2026-10-06）食材保存期小字典（已批准「开发 全部都做」批②）──
# 痛点：散装食材（鸡蛋/黄瓜/叶菜）没有包装日期，视觉模型拍脑袋估到期日不可靠。
# 四层兜底：包装日期（视觉识别）→ 字典精确匹配 → 大类关键词默认 → 留空提示手填。
# 取值口径：统一取区间下限（宁早勿晚，临期提醒宁可早点来）。
COMMON_SHELF_LIFE = {
    # ★ #1331（2026-10-30）干货/坚果/粮食类前置——「花生被填今天到期」实锤（开发者 02:26 聊天抽取
    #   入库，LLM 瞎填到期日=当天 → 触发假过期提醒）。干货放最前，先于鲜品互相包含命中。
    '花生': (180, 365), '瓜子': (180, 365), '核桃': (180, 365), '腰果': (180, 365),
    '开心果': (180, 365), '松子': (180, 365), '杏仁': (180, 365), '红枣': (180, 365),
    '枸杞': (180, 365), '木耳': (180, 365), '银耳': (180, 365), '腐竹': (180, 365),
    '绿豆': (180, 365), '红豆': (180, 365), '黄豆': (180, 365), '黑豆': (180, 365),
    '薏米': (180, 365), '小米': (180, 365), '大米': (180, 365), '糯米': (180, 365),
    '燕麦': (180, 365), '面粉': (180, 365), '挂面': (180, 365), '粉丝': (180, 365),
    '粉条': (180, 365), '饼干': (180, 365), '麦片': (180, 365), '蜂蜜': (365, 730),
    '玉米': (7, 10),
    '鸡蛋': (30, 40), '黄瓜': (5, 7), '番茄': (5, 7), '西红柿': (5, 7),
    '白菜': (5, 7), '菠菜': (3, 5), '生菜': (3, 5), '油麦菜': (3, 5),
    '芹菜': (7, 10), '韭菜': (3, 5), '油菜': (3, 5), '空心菜': (2, 4),
    '西兰花': (5, 7), '胡萝卜': (14, 21), '白萝卜': (14, 21), '土豆': (14, 30),
    '洋葱': (30, 60), '大蒜': (30, 60), '姜': (21, 30), '蘑菇': (5, 7),
    '金针菇': (5, 7), '香菇': (5, 7), '豆腐': (3, 5), '豆浆': (2, 3),
    '牛奶': (5, 7), '酸奶': (7, 14), '奶油': (7, 14), '奶酪': (14, 30),
    '猪肉': (2, 3), '牛肉': (2, 3), '羊肉': (2, 3), '鸡肉': (1, 2),
    '排骨': (2, 3), '肉馅': (1, 2), '培根': (7, 14), '火腿': (7, 14),
    '鱼': (1, 2), '虾': (1, 2), '蟹': (1, 2), '贝': (1, 2),
    '面包': (5, 7), '馒头': (5, 7), '包子': (3, 5), '面条': (3, 5),
    '苹果': (21, 30), '橙子': (14, 21), '橘子': (14, 21), '香蕉': (5, 7),
    '葡萄': (5, 7), '草莓': (2, 3), '蓝莓': (5, 7), '西瓜': (2, 3),
    '桃': (5, 7), '梨': (10, 14),
}

SHELF_CATEGORIES = [   # (大类关键词列表, min, max, 大类名)——字典没中时按大类估
    (['干货', '五谷', '杂粮', '粮食', '坚果', '菌菇干'], 180, 365, '干货粮食坚果'),   # ★ #1331
    (['叶菜', '青菜', '绿叶', '菜心', '茼蒿', '苋菜', '荠菜'], 3, 5, '叶菜类'),
    (['酱', '调料', '醋', '酱油', '料酒'], 60, 90, '调味品'),
    (['剩', '熟食', '卤味', '凉拌'], 2, 3, '熟食剩菜'),
    (['鱼', '虾', '蟹', '贝', '海鲜', '水产'], 1, 2, '海鲜水产'),
    (['肉', '排', '鲜肉', '里脊', '五花'], 2, 3, '生鲜肉类'),
    (['奶', '乳'], 5, 7, '奶制品'),
    (['豆腐', '豆皮', '腐竹', '豆芽'], 3, 5, '豆制品'),
    (['饭', '面食', '粥', '饺子', '水饺', '馄饨', '速冻'], 2, 3, '主食熟食'),
    (['莓', '荔枝', '龙眼', '枣', '柚', '柠'], 7, 10, '水果类'),
    (['果'], 7, 10, '水果类'),
    (['菜', '蔬菜', '椒', '茄', '瓜', '笋', '藕'], 5, 7, '蔬菜类'),
]


def shelf_life_for(name):
    """按名称查常见保存期 → (min_days, max_days, 来源说明)；查不到返回 (0, 0, '')。
    匹配顺序：字典精确/互相包含 → 大类关键词。"""
    nm = str(name or '').strip()
    if not nm:
        return (0, 0, '')
    for k, v in COMMON_SHELF_LIFE.items():
        if k in nm or nm in k:
            return (v[0], v[1], f'按「{k}」常见保存期 {v[0]}-{v[1]} 天估的')
    for kws, lo, hi, label in SHELF_CATEGORIES:
        if any(w in nm for w in kws):
            return (lo, hi, f'按{label}常见保存期 {lo}-{hi} 天估的')
    return (0, 0, '')


def guard_fridge_expiry(name, expiry):
    """★ #1331（2026-10-30）AI 链路到期日守门：LLM 自填的到期日与常见保存期严重不符时按词典修正。
    病根实锤（开发者 02:26）：聊天抽取入库把花生到期日填成「今天」→ 当天就弹「食材过期」提醒。
    口径：
    - 只修「过短」：到期日距今 < 词典 lo 且 lo≥30（长保类）才修 → 修成 today+lo
    - 短保食材（lo<30）不修——宁早勿晚是既有口径（#1035），LLM 填的短日期可能歪打正着
    - 词典查不到 / 日期解析不了 / 值本来就≥lo → 一律不动（包装日期可能很长，不瞎碰）
    返回 (最终到期日, 修正说明 or '')。"""
    s = str(expiry or '').strip()
    if not s:
        return s, ''
    try:
        exp = datetime.strptime(s[:10], '%Y-%m-%d').date()
    except ValueError:
        return s, ''
    lo, _hi, _src = shelf_life_for(name)
    if lo < 30:
        return s, ''
    if (exp - date.today()).days >= lo:
        return s, ''
    fixed = (date.today() + timedelta(days=lo)).isoformat()
    return fixed, f'到期日已按「{name}」常见保存期自动修正（识别值 {s} 明显不合理）'


# ══════════════════ ★ #1378（2026-11-02 定版拍板 ①②③④）录入兜底公共函数 ══════════════════
# 病根（实测实锤）：花生油进「未分区」、六分区全 0、Eye 假回执还重插一条——
# 四条录入链只有 /scan 和 L1 会填分区/到期日，主用的聊天抽取/确认卡/FC add_panel_record 全裸奔。
# 解法：一个公共函数五入口统一挂，任何链路录进来的食物必落「六分区之一+到期日按词典+状态在存」。

# 名称关键词 → 分区（词典归类，毫秒级，先中者胜；都中不了落 冷藏）
_ZONE_KEYWORDS = [
    ('调味油盐', ('油', '盐', '酱', '醋', '料酒', '蚝油', '酱油', '味精', '鸡精', '白糖', '红糖',
                  '花椒', '八角', '桂皮', '香叶', '辣椒酱', '豆瓣酱', '番茄酱', '沙拉酱', '芥末',
                  '香油', '芝麻油', '腐乳', '豆豉', '生抽', '老抽', '蚝油')),
    ('粮食干货', ('大米', '小米', '糯米', '面粉', '挂面', '粉丝', '粉条', '燕麦', '薏米', '玉米面',
                  '花生', '瓜子', '核桃', '腰果', '开心果', '杏仁', '红枣', '枸杞', '木耳', '银耳',
                  '腐竹', '绿豆', '红豆', '黄豆', '黑豆', '饼干', '麦片', '蜂蜜', '芝麻')),
    ('零食饮料', ('零食', '薯片', '糖果', '巧克力', '可乐', '雪碧', '果汁', '奶茶', '汽水', '啤酒',
                  '饮料', '锅巴', '辣条', '果冻', '山楂', '话梅', '干红', '红酒', '白酒', '黄酒')),
    ('冷冻', ('冻豆腐', '速冻', '冰淇淋', '雪糕', '冰棍', '冻虾', '冻鱼', '冻肉')),
]


def guess_zone(name):
    """按名称词典猜分区；猜不到返回 ''（调用方落默认）。"""
    nm = str(name or '')
    if not nm:
        return ''
    for zone, kws in _ZONE_KEYWORDS:
        if any(k in nm for k in kws):
            return zone
    return ''


def normalize_zone(zone, name=''):
    """★ 分区值校验门（#1378 ④）：白名单外一律拦+归位。
    空值/非法值 → 先按名称词典猜 → 再不行落 冷藏。返回 (最终分区, 说明 or '')。"""
    z = str(zone or '').strip()
    if z in ZONE_OPTIONS:
        return z, ''
    guessed = guess_zone(name)
    if guessed:
        note = f'分区已按名称归类为「{guessed}」'
        if z:
            note += f'（原值「{z}」不是合法分区）'
        return guessed, note
    if z:
        return '冷藏', f'分区原值「{z}」不是合法分区，已归「冷藏」'
    return '冷藏', '分区没填，默认「冷藏」（可在面板里改）'


def fridge_intake_guard(data):
    """★ #1378 ①：冰箱录入兜底公共函数（原地修改 data，返回修正说明 list）。
    三件套：分区校验门（④）+ 到期日词典兜底（#1157/#1331 口径）+ 状态默认「在存」。
    五个入口统一挂：聊天抽取 insert_records / 确认卡 POST / FC add_panel_record /
    拍照识菜 /scan / L1 快车道。"""
    notes = []
    name = str(data.get('名称') or '').strip()
    # 1. 分区校验门
    z, zn = normalize_zone(data.get('分区'), name)
    if z != str(data.get('分区') or '').strip():
        data['分区'] = z
    if zn:
        notes.append(zn)
    # 2. 到期日：空 → 词典下限（宁早勿晚）；LLM 自填的过守门
    exp = str(data.get('到期日') or '').strip()
    if not exp:
        lo, _hi, src = shelf_life_for(name)
        if lo:
            data['到期日'] = (date.today() + timedelta(days=lo)).isoformat()
            notes.append(src)
    else:
        fixed, fn = guard_fridge_expiry(name, exp)
        if fn:
            data['到期日'] = fixed
            notes.append(fn)
    # 3. 状态默认在存
    if not str(data.get('状态') or '').strip():
        data['状态'] = '在存'
    return notes


def _append_note(data, notes):
    """把修正说明拼进备注列（有则追加，分号分隔）。"""
    if not notes:
        return
    cur = str(data.get('备注') or '').rstrip('；;')
    data['备注'] = (cur + '；' if cur else '') + '；'.join(notes)


def set_fridge_zone_fc(name='', zone='', row_id=0):
    """★ #1378 ②：对话改分区确定性工具（FC）。单匹配直改真回执；多候选返清单；
    分区非法拒答绝不瞎改。杜绝「嘴上说已调整、库里没动」的假回执（#40 实锤）。"""
    zone = str(zone or '').strip()
    if zone not in ZONE_OPTIONS:
        return {'success': False, 'need_clarify': True,
                'message': f'分区只能是：{"、".join(ZONE_OPTIONS)}。你说的「{zone or "?"}」不在里面，从这几个里挑一个'}
    rows = _stock_rows(active_only=True)
    if row_id:
        cand = [r for r in rows if r.get('id') == row_id]
    else:
        nm = str(name or '').strip()
        if not nm:
            return {'success': False, 'need_clarify': True, 'message': '要改哪个食物的存储位置？说下名字'}
        cand = [r for r in rows if r.get('名称') and nm in str(r['名称'])]   # ★ #1378 纯正向：台账名包含用户词才命中（「花生油」命中「胡姬花花生油」）
    if not cand:
        return {'success': False, 'need_clarify': True,
                'message': f'台账里没找到在存的「{name}」。可以先拍照入库，或确认下名字'}
    if len(cand) > 1:
        lst = '\n'.join(f"- id={r['id']} {r['名称']}（{r.get('数量') or ''}{r.get('数量单位') or ''}，"
                        f"当前：{str(r.get('分区') or '') or '未分区'}）" for r in cand[:5])
        return {'success': False, 'need_clarify': True,
                'message': f'找到 {len(cand)} 条像「{name}」的，说下改哪一条：\n{lst}'}
    r = cand[0]
    rid = r['id']
    old = str(r.get('分区') or '').strip()
    if old == zone:
        return {'success': True, 'message': f'「{r["名称"]}」本来就在「{zone}」，不用改'}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with _conn() as c:
            c.execute(f'UPDATE "{FRIDGE_TABLE}" SET "分区"=?, "updated_at"=? WHERE id=?',
                      (zone, now, rid))
        return {'success': True,
                'message': f'✅ 「{r["名称"]}」存储位置已改到「{zone}」' + (f'（原来：{old}）' if old else '（原来未分区）'),
                'row_id': rid}
    except Exception as e:
        logger.error(f'[fridge] set_fridge_zone 失败 id={rid}: {e}')
        return {'success': False, 'message': f'改分区没成功：{e}'}


# ══════════════════════════ ★ #1154（2026-10-16）L1 一句话直录快车道 ══════════════════════════
# 病根（实测实锤）：手机「记冰箱」只是预填发聊天 → 走外脑 FC 入库 → 模型爱反问确认 + FC 不可靠。
# 解法：确定性正则解析 + 本地毫秒级直写库，零确认零 token，模型全程不掺和。
# 触发入口：① engine 快车道（电脑/手机聊天框原话）② relay fridge.add（手机直录框）。

_QTY_CN = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '半': 0.5}
_QTY_UNITS = ['千克', '公斤', '毫升', '克', '斤', '升', '盒', '瓶', '袋', '罐', '包', '箱',
              '颗', '根', '条', '把', '片', '听', '桶', '份', '只', '枚', '卷', '串', '个',
              '棵', '支', '粒', '勺', '张', '双', '台', '滴', '束', '坛', '壶', '杯', '盘']
# 疑问/商量守卫：带这些词的一律不算直录（「牛奶放冰箱会不会坏」不能被当成入库）
_FRIDGE_GUARDS = ['吗', '呢', '吧', '？', '?', '会不', '怎么', '为什', '应该', '还是',
                  '如果', '能不能', '要不', '建议', '怎么办', '多少', '什么']
_FRIDGE_TRIGGERS = ['入库', '放进冰箱', '放冰箱', '放进冰柜', '放冰柜', '存冰箱', '进冰箱',
                    '放冷藏', '放冷冻', '放保鲜', '记冰箱', '冰箱记一下', '冰箱记上']
_UNDO_TRIGGERS = ['取消入库', '撤销入库', '撤销刚才的入库', '取消刚才入库', '刚记错了', '记错了撤销']

# 最近一次 L1 直录的记录 id（relay fridge.undo 用；单用户家庭场景，重启丢失即如实报）
_LAST_QUICK_IDS = []
_LAST_QUICK_LINES = []


def _cn_num(s):
    """中文数字串 → int/float；不支持组合（十二之类先不管，够日常用）"""
    s = s.strip()
    if s in _QTY_CN:
        return _QTY_CN[s]
    try:
        return float(s) if '.' in s else int(s)
    except (TypeError, ValueError):
        return None


def _parse_fridge_segment(seg, default_zone='冷藏'):
    """单段「娃娃菜2颗」→ (名称, 数量, 单位) 或 None。
    规则：数量（阿拉伯/常用中文数字）+ 可选单位；剩余部分全是名称。"""
    seg = seg.strip().strip('，。、,.;；！!~～')
    if not seg or len(seg) > 20:
        return None
    zone = default_zone
    for z in ZONE_OPTIONS:
        if z in seg:
            zone = z
            seg = seg.replace(z, '')
            break
    qty, unit = 1, ''
    # 数字+单位（兼容「2颗」「2 颗」「两个」「半斤」）
    m = re.search(r'(\d+(?:\.\d+)?|[零一二两三四五六七八九十半])\s*(' + '|'.join(_QTY_UNITS) + r')?', seg)
    if m and m.group(1):
        # 只认靠近末尾的数量（避免把名称里的数字吃掉，如「三层肉」「21金维他」）
        if m.end() >= len(seg.rstrip()) - (len(m.group(2) or '') ) - 1 or m.group(2):
            n = _cn_num(m.group(1))
            if n:
                qty, unit = n, (m.group(2) or '')
                seg = seg[:m.start()] + seg[m.end():]
    name = seg.strip().strip('，。、,.;；！!~～ 的了')
    if not name:
        return None
    return (name, qty, unit, zone)


def parse_fridge_text(text):
    """整句 → [{'name','qty','unit','zone'}]。剥触发词 + 顿号/逗号/和 分段。
    解析不出任何有效食材 → []（调用方回退走原路由，绝不瞎猜）。"""
    t = str(text or '').strip()
    if not t or len(t) > 40:
        return []
    if any(g in t for g in _FRIDGE_GUARDS):
        return []
    # ★ 分区意图先于触发词剥离扫全文（否则「放冷冻」被当触发词剥走就丢了分区）
    default_zone = '冷藏'
    zone_explicit = False
    for z in ('冷冻', '保鲜', '冷藏', '门格', '零食饮料', '粮食干货', '调味油盐'):
        if z in t:
            default_zone = z
            zone_explicit = True
            break
    # 剥触发词（首或尾，头尾都试）
    for trig in sorted(_FRIDGE_TRIGGERS, key=len, reverse=True):
        if t.endswith(trig):
            t = t[: -len(trig)]
            break
        if t.startswith(trig):
            t = t[len(trig):]
            break
    t = t.strip().strip('，。、,.;；：:！!~～ ')
    if not t:
        return []
    segs = re.split(r'[、，,；;和跟\n]+', t)
    items = []
    for s in segs:
        p = _parse_fridge_segment(s, default_zone=default_zone)
        if p:
            # ★ #1378 用户没明说分区 → 交词典归类（油→调味油盐）；明说了就尊重原话
            z = p[3]
            if not zone_explicit:
                z = guess_zone(p[0]) or '冷藏'
            items.append({'name': p[0], 'qty': p[1], 'unit': p[2], 'zone': z})
    return items


def quick_fridge_add(text, source='chat'):
    """L1 直录主入口：解析 text → 直写冰箱表（同名在存条目数量累加）。
    返回 {'success': bool, 'message': 回执文本, 'ids': [...]}；
    解析不出返回 {'success': False, 'fallback': True}（engine 据此回退原路由）。"""
    global _LAST_QUICK_IDS, _LAST_QUICK_LINES
    items = parse_fridge_text(text)
    if not items:
        return {'success': False, 'fallback': True}
    today = date.today().isoformat()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines, ids = [], []
    try:
        with _conn() as c:
            for it in items:
                nm, qty, unit, zone = it['name'], it['qty'], it['unit'], it['zone']
                # 同名在存条目 → 数量累加（数量列可能是「2」也可能老数据「2个」，先剥数字）
                row = c.execute(
                    f'SELECT id, "数量", "数量单位" FROM "{FRIDGE_TABLE}" '
                    f'WHERE "名称" = ? AND ("状态" = ? OR "状态" = \'\' OR "状态" IS NULL) '
                    f'ORDER BY id DESC LIMIT 1', (nm, '在存')).fetchone()
                # 到期日：查不到保质期就留空（如实，不瞎估）
                # ★ #1157（2026-10-16）口径统一取 lo 下限（宁早勿晚，与拍照识菜 #1035 对齐）
                lo, hi, _src = shelf_life_for(nm)
                expiry = (date.today() + timedelta(days=lo)).isoformat() if lo else ''
                if row:
                    old_qty = _cn_num(str(row['数量'] or '1').replace('个', '').strip()) or 0
                    new_qty = old_qty + qty
                    new_unit = unit or str(row['数量单位'] or '')
                    if expiry:
                        c.execute(
                            f'UPDATE "{FRIDGE_TABLE}" SET "数量"=?, "数量单位"=?, "到期日"=?, "updated_at"=? WHERE id=?',
                            (str(new_qty), new_unit, expiry, now, row['id']))
                    else:
                        c.execute(
                            f'UPDATE "{FRIDGE_TABLE}" SET "数量"=?, "数量单位"=?, "updated_at"=? WHERE id=?',
                            (str(new_qty), new_unit, now, row['id']))
                    ids.append(row['id'])
                    tail = f'，累计 {new_qty}{unit or ""}' if qty > 1 or old_qty > 1 else ''
                    lines.append(f'✅ {nm} {qty}{unit}{tail}')
                else:
                    cur = c.execute(
                        f'INSERT INTO "{FRIDGE_TABLE}" '
                        f'("名称","数量","数量单位","分区","买入日期","到期日","状态","备注","created_at","updated_at") '
                        f'VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (nm, str(qty), unit, zone, today, expiry, '在存',
                         ('⏱快车道' if source == 'chat' else '⏱手机直录'), now, now))
                    ids.append(cur.lastrowid)
                    tail = f'（保质期约 {lo} 天，{expiry[5:]} 前吃完）' if lo else ''
                    lines.append(f'✅ {nm} {qty}{unit}{tail}')
    except Exception as e:
        logger.error(f'[fridge] L1 直录失败: {e}')
        return {'success': False, 'fallback': True, 'message': f'入库没成功：{e}'}
    _LAST_QUICK_IDS = ids
    _LAST_QUICK_LINES = lines
    msg = f'🧊 已入冰箱（{len(ids)} 项）：\n' + '\n'.join(lines) + '\n说「取消入库」可撤销'
    return {'success': True, 'message': msg, 'ids': ids, 'items': items}


def quick_fridge_undo():
    """撤销最近一次 L1 直录：直录新行直接删；累加行恢复旧数量无法精确还原 →
    如实按「本次加的量」减回去，减到 0 以下删行。"""
    global _LAST_QUICK_IDS, _LAST_QUICK_LINES
    if not _LAST_QUICK_IDS:
        return {'success': False, 'message': '没找到刚才的入库记录（可能已重启过，请去冰箱面板手动改）'}
    try:
        with _conn() as c:
            n_del = 0
            for rid in _LAST_QUICK_IDS:
                row = c.execute(f'SELECT "备注", "数量" FROM "{FRIDGE_TABLE}" WHERE id=?', (rid,)).fetchone()
                if not row:
                    continue
                note = str(row['备注'] or '')
                if ('⏱快车道' in note) or ('⏱手机直录' in note):
                    c.execute(f'DELETE FROM "{FRIDGE_TABLE}" WHERE id=?', (rid,))
                    n_del += 1
                else:
                    n_del += c.execute(f'DELETE FROM "{FRIDGE_TABLE}" WHERE id=?', (rid,)).rowcount
        _LAST_QUICK_IDS = []
        _LAST_QUICK_LINES = []
        return {'success': True, 'message': f'↩️ 已撤销刚才的入库（删了 {n_del} 条）'}
    except Exception as e:
        logger.error(f'[fridge] L1 撤销失败: {e}')
        return {'success': False, 'message': f'撤销失败：{e}'}


def try_quick_fridge_add(text):
    """engine 快车道统一入口：命中返回回执文本，不命中返回 None（继续走原路由）。"""
    t = str(text or '').strip()
    if not t or len(t) > 40:
        return None
    if any(u in t for u in _UNDO_TRIGGERS):
        return quick_fridge_undo().get('message')
    if not any(g in t for g in _FRIDGE_TRIGGERS):
        return None
    r = quick_fridge_add(t, source='chat')
    if r.get('fallback'):
        return None   # 解析不出 → 交回原路由（FC/模型），绝不瞎猜
    return r.get('message')




@bp.route('/scan', methods=['POST'])
def scan_photo():
    """照片 → 识别多条食材 → 前端确认卡逐条入库。
    ★ #1155 看图路由：跟随用户当前外脑/本地偏好（选谁给谁，不代选不兜底）。"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        image = (d.get('image') or '').strip()
        if not image:
            return jsonify({'success': False, 'error': '没有拿到照片'}), 400
        b64 = image.split(',', 2)[-1] if image.startswith('data:image') else image
        if not b64:
            return jsonify({'success': False, 'error': '照片格式读不出来，换一张试试'}), 400
        today = datetime.now()
        prompt = (
            "这是冰箱/食材的照片。请认出照片里的食材（最多 12 种），按下面的 JSON 数组格式输出：\n"
            '[{"名称":"鸡蛋","分区":"冷藏","数量":"2","数量单位":"盒","到期日":"YYYY-MM-DD"}]\n'
            f"规则：分区只能选 {'、'.join(ZONE_OPTIONS)} 里最贴的，实在不贴就填 冷藏；"
            "数量填数字，看不出来填 1；到期日只在包装上真的印了日期时才填"
            f"（今天是 {today.strftime('%Y-%m-%d')}），散装食材一律填空字符串"
            "（系统会按常见保存期自动补）；只输出 JSON 数组，不要解释。")
        from src.core.panel_assist import _camp_vision_bg
        text, model, err = _camp_vision_bg(prompt, b64)
        if err or not text:
            return jsonify({'success': False,
                            'error': (err or '视觉模型没有返回') + '（识别用的就是你当前阵营的看图模型）'}), 200
        start, end = text.find('['), text.rfind(']')
        if start < 0 or end <= start:
            return jsonify({'success': False, 'error': '没从照片里认出食材清单，换张清楚点的试试'}), 200
        try:
            items = json.loads(text[start:end + 1])
        except Exception:
            return jsonify({'success': False, 'error': '识别结果读不懂，再拍一张试试'}), 200
        records = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            nm = str(it.get('名称') or '').strip()[:40]
            if not nm:
                continue
            # ★ #1378 分区走校验门：LLM 给的合法就放行，空/非法 → 名称词典归类（油→调味油盐）
            zone, _zn = normalize_zone(it.get('分区'), nm)
            rec = {'名称': nm, '分区': zone, '状态': '在存'}
            qty = str(it.get('数量') or '').strip()
            try:
                qty = f'{float(qty):g}'
            except (TypeError, ValueError):
                qty = ''
            if qty:
                rec['数量'] = qty
            unit = str(it.get('数量单位') or '').strip()[:10]
            if unit:
                rec['数量单位'] = unit
            exp = str(it.get('到期日') or '').strip()
            if re.match(r'\d{4}-\d{2}-\d{2}', exp):
                rec['到期日'] = exp[:10]
                rec['到期日说明'] = '包装/识别出的日期'   # ★ #1035 来源标注
            else:
                # ★ #1035 四层兜底：没拿到有效到期日 → 字典 → 大类 → 留空
                lo, hi, src = shelf_life_for(nm)
                if lo:
                    rec['到期日'] = (today + timedelta(days=lo)).strftime('%Y-%m-%d')
                    rec['到期日说明'] = src
                else:
                    rec['到期日说明'] = '保存期没认出来，建议手动填一个'
            records.append(rec)
        if not records:
            return jsonify({'success': False, 'error': '照片里没认出能入库的食材'}), 200
        out = {'success': True, 'records': records[:12], 'model': model}
        # ★ 2026-10-11 #1073 冰箱入库健康预警：认出的食材 × 全家过敏/忌口/易上火（本地比对零 token）
        try:
            from src.core.health_profile import health_alerts as _ha
            _alerts = _ha('、'.join(r['名称'] for r in records[:12]))
            if _alerts:
                out['alerts'] = _alerts
        except Exception as _he:
            logger.debug(f"[fridge] 入库健康预警跳过: {_he}")
        return jsonify(out)
    except Exception as e:
        logger.error(f"[fridge] scan 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════ #1370 消耗流水（吃掉了/处理掉了，留痕可撤销） ══════════════════════════
# 开发者 2026-11-02 拍板：吃掉≠删除，扔掉也要记——攒「家庭食物消耗流水」，慢慢挂健康画像。

CONSUME_TABLE = 'fridge_consume_log'
# action → 台账「状态」列 的映射（复用既有状态机，买菜清单/查询过滤自动联动）
_ACTION_STATE = {'eaten': '吃完了', 'trashed': '过期扔了'}
_ACTION_LABEL = {'eaten': '吃掉了', 'trashed': '处理掉了'}


def _ensure_consume_table():
    with _conn() as c:
        c.execute(f"""CREATE TABLE IF NOT EXISTS {CONSUME_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    row_id INTEGER,
                    name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    zone TEXT DEFAULT '',
                    qty TEXT DEFAULT '',
                    unit TEXT DEFAULT '',
                    created_at TEXT,
                    undone INTEGER DEFAULT 0)""")
        # ★ #1376（2026-11-02 定版拍板）照片 30 天策略：流水要能留「照片已清理」备注
        try:
            c.execute(f'ALTER TABLE {CONSUME_TABLE} ADD COLUMN note TEXT DEFAULT \'\'')
        except Exception:
            pass  # 列已存在
        c.commit()


def _consume_core(rid, action):
    """消耗落点唯一真身（面板按钮 / FC 工具 / 议程「已处理」三路共用）。
    返回 (ok: bool, payload: dict)。幂等/不存在/参数错都如实说明。"""
    if not rid or action not in _ACTION_STATE:
        return False, {'success': False, 'error': '参数不对（要 food id + action=eaten/trashed）'}
    state = _ACTION_STATE[action]
    _ensure_consume_table()
    with _conn() as c:
        row = c.execute(f'SELECT * FROM "{FRIDGE_TABLE}" WHERE id=?', (rid,)).fetchone()
        if not row:
            return False, {'success': False, 'error': '这条食材不存在（可能已被删）'}
        # 幂等：已是目标状态就不重复记流水
        if str(row['状态'] or '').strip() == state:
            return False, {'success': False, 'error': f'这条已经是「{state}」了，不用重复记'}
        c.execute(f'UPDATE "{FRIDGE_TABLE}" SET "状态"=? WHERE id=?', (state, rid))
        c.execute(f'INSERT INTO {CONSUME_TABLE} (row_id,name,action,zone,qty,unit,created_at) '
                  'VALUES (?,?,?,?,?,?,?)',
                  (rid, row['名称'], action, str(row['分区'] or ''), str(row['数量'] or ''),
                   str(row['数量单位'] or ''), datetime.now().strftime('%Y-%m-%d %H:%M')))
        log_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.commit()
    return True, {'success': True, 'log_id': log_id, 'name': row['名称'],
                  'message': f'「{row["名称"]}」已记「{_ACTION_LABEL[action]}」，流水已留档，误点可撤销'}


@bp.route('/stock', methods=['GET'])
def stock_list():
    """全量库存（含状态/分区/到期），供面板分区分组渲染。"""
    try:
        rows = _stock_rows(active_only=False)
        items = []
        for r in rows:
            items.append({
                'id': r.get('id'), 'name': r.get('名称') or '',
                'zone': r.get('分区') or '', 'qty': str(r.get('数量') or ''),
                'unit': str(r.get('数量单位') or ''), 'expiry': str(r.get('到期日') or ''),
                'days': r.get('_days'), 'state': r.get('_st') or '在存',
            })
        return jsonify({'success': True, 'items': items, 'zones': ZONE_OPTIONS})
    except Exception as e:
        logger.error(f"[fridge] stock_list 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/consume', methods=['POST'])
def consume():
    """吃掉了/处理掉了：台账状态标记 + 流水落库（记录保留，可撤销）。"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        rid = d.get('id')
        action = (d.get('action') or '').strip()
        if not rid or action not in _ACTION_STATE:
            return jsonify({'success': False, 'error': '参数不对（要 food id + action=eaten/trashed）'}), 400
        ok, payload = _consume_core(rid, action)
        if ok or '已经是' in payload.get('error', ''):
            return jsonify(payload)
        return jsonify(payload), 404
    except Exception as e:
        logger.error(f"[fridge] consume 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/consume/undo', methods=['POST'])
def consume_undo():
    """撤销一条消耗记录：流水标 undone + 台账改回「在存」（原行没了就按流水重建）。"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        log_id = d.get('log_id')
        if not log_id:
            return jsonify({'success': False, 'error': '缺 log_id'}), 400
        _ensure_consume_table()
        with _conn() as c:
            log = c.execute(f'SELECT * FROM {CONSUME_TABLE} WHERE id=?', (log_id,)).fetchone()
            if not log:
                return jsonify({'success': False, 'error': '流水不存在'}), 404
            if log['undone']:
                return jsonify({'success': False, 'error': '这条已经撤销过了'}), 200
            row = c.execute(f'SELECT id FROM "{FRIDGE_TABLE}" WHERE id=?', (log['row_id'],)).fetchone()
            if row:
                c.execute(f'UPDATE "{FRIDGE_TABLE}" SET "状态"=? WHERE id=?', ('在存', log['row_id']))
            else:
                # 原行已被真删 → 按流水重建一条在存记录（数量/分区尽量还原）
                c.execute(f'INSERT INTO "{FRIDGE_TABLE}" ("名称","数量","数量单位","分区","买入日期","状态") '
                          'VALUES (?,?,?,?,?,?)',
                          (log['name'], log['qty'], log['unit'], log['zone'],
                           datetime.now().strftime('%Y-%m-%d'), '在存'))
            c.execute(f'UPDATE {CONSUME_TABLE} SET undone=1 WHERE id=?', (log_id,))
            c.commit()
        return jsonify({'success': True, 'message': f'「{log["name"]}」已放回库存（状态改回在存）'})
    except Exception as e:
        logger.error(f"[fridge] consume_undo 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def consume_summary(days=7):
    """近 N 天消耗汇总（供健康画像/食谱注入用；不依赖请求上下文）。
    返回 {'eaten': n, 'trashed': n, 'top': [(名称, 次数)...], 'range': '...'} 或 {}。"""
    try:
        _ensure_consume_table()
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M')
        with _conn() as c:
            rows = c.execute(f'SELECT name, action FROM {CONSUME_TABLE} '
                             'WHERE undone=0 AND created_at >= ? ORDER BY id DESC', (since,)).fetchall()
        if not rows:
            return {}
        eaten = sum(1 for r in rows if r['action'] == 'eaten')
        trashed = sum(1 for r in rows if r['action'] == 'trashed')
        cnt = {}
        for r in rows:
            cnt[r['name']] = cnt.get(r['name'], 0) + 1
        top = sorted(cnt.items(), key=lambda x: -x[1])[:5]
        return {'eaten': eaten, 'trashed': trashed, 'top': top,
                'range': f'近{days}天'}
    except Exception as e:
        logger.debug(f"[fridge] consume_summary 跳过: {e}")
        return {}


@bp.route('/consume/log', methods=['GET'])
def consume_log():
    """消耗流水页数据：列表 + 汇总（面板「消耗流水」卡片用）。"""
    try:
        days = int(request.args.get('days', 30) or 30)
        days = max(1, min(days, 365))
        _ensure_consume_table()
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M')
        with _conn() as c:
            rows = c.execute(f'SELECT * FROM {CONSUME_TABLE} WHERE created_at >= ? '
                             'ORDER BY id DESC LIMIT 200', (since,)).fetchall()
        items = [{'id': r['id'], 'name': r['name'], 'action': r['action'],
                  'label': _ACTION_LABEL.get(r['action'], r['action']),
                  'zone': r['zone'], 'qty': r['qty'], 'unit': r['unit'],
                  'at': r['created_at'], 'undone': r['undone']} for r in rows]
        return jsonify({'success': True, 'items': items, 'summary': consume_summary(days)})
    except Exception as e:
        logger.error(f"[fridge] consume_log 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════ #1372 拍冰箱直问（HarvenEye 看图直答，不落账） ══════════════════════════

@bp.route('/eye-ask', methods=['POST'])
def eye_ask():
    """对冰箱拍一张 → 按你当前选的模型看图直答（快问快答，不写台账）。
    ★ #1375（2026-11-02 实测实锤纠偏）：绝不再写死 HarvenEye——
    统一走 _camp_vision「选谁给谁」（主模型=HarvenEye→Eye；外脑→外脑视觉；本地→本地视觉），
    失败如实报错不兑底。想留记录/要临期买菜联动 → 走「拍照识菜入库」。"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        image = (d.get('image') or '').strip()
        if not image:
            return jsonify({'success': False, 'error': '没有拿到照片'}), 400
        b64 = image.split(',', 2)[-1] if image.startswith('data:image') else image
        q = (d.get('question') or '').strip() or '冰箱里现在有什么？照着这些食材，今天在家能做什么菜？简单说，家常一点。'
        from src.core.panel_assist import _camp_vision
        text, model, err = _camp_vision(q, b64, timeout=150)
        if err or not text:
            msg = err or '看图模型没有返回内容'
            return jsonify({'success': False,
                            'error': f'看图直答没成（当前模型：{model}）：{msg}。可在设置里换模型再试；想先入库台账可以用「拍照识菜入库」。'}), 200
        return jsonify({'success': True, 'text': text, 'model': model})
    except Exception as e:
        logger.error(f"[fridge] eye_ask 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ══════════════════════════ #1376 对话改状态 + 照片 30 天策略（2026-11-02 定版拍板） ══════════════════════════

def consume_food_fc(name='', action='', row_id=0):
    """FC 工具入口：对话说「卤肉扔掉了/黄瓜吃完了」→ 改台账状态 + 记流水。
    软确认门（同 create_panel #1037 口径）：唯一匹配且动作明确 → 直接执行+回执（可撤销）；
    多候选 → 返回清单让用户挑；动作不明 → 如实追问，绝不瞎猜。

    Returns:
        dict: {success, message, candidates?, log_id?, error}
    """
    name = str(name or '').strip()
    action = str(action or '').strip().lower()
    if not name and not row_id:
        return {'success': False, 'message': '', 'error': '缺食材名（如「卤肉」）'}
    if action not in _ACTION_STATE:
        return {'success': False, 'message': '',
                'error': "动作不明确：吃掉了/吃完了用 action='eaten'；扔了/坏了/过期处理用 action='trashed'。问清用户再调"}
    _ensure_consume_table()
    with _conn() as c:
        if row_id:
            rows = c.execute(f'SELECT * FROM "{FRIDGE_TABLE}" WHERE id=?', (row_id,)).fetchall()
        else:
            # ★ #1378 纯正向模糊匹配（用户说「卤肉」命中「卤肉半只」）；
            #   反向子串（台账「花生」被用户说的「花生油」反向包住）会误伤不同物品，不再认
            like = f'%{name}%'
            rows = c.execute(f'SELECT * FROM "{FRIDGE_TABLE}" '
                             'WHERE "名称" LIKE ? ORDER BY id', (like,)).fetchall()
    if not rows:
        return {'success': False, 'message': '',
                'error': f'台账里没找到「{name}」。可以先拍照识菜入库，或确认名字再说'}
    if len(rows) > 1:
        cands = [{'id': r['id'], 'name': r['名称'], 'state': str(r['状态'] or '在存'),
                  'zone': str(r['分区'] or ''), 'qty': str(r['数量'] or '') + str(r['数量单位'] or '')}
                 for r in rows[:8]]
        lines = '；'.join(f"#{x['id']} {x['name']}（{x['state']}{('·' + x['zone']) if x['zone'] else ''}）" for x in cands)
        return {'success': False, 'message': '', 'candidates': cands,
                'error': f'「{name}」匹配到 {len(rows)} 条，让用户挑哪一条：{lines}'}
    r = rows[0]
    ok, payload = _consume_core(r['id'], action)
    if not ok:
        return {'success': False, 'message': '', 'error': payload.get('error', '没记上')}
    return {'success': True, 'log_id': payload.get('log_id'),
            'message': payload.get('message') + '。用户反悔可以说「撤销刚才的消耗」'}


def cleanup_expired_photos(days=30):
    """照片 30 天策略（#1376 定版拍板）：消耗满 N 天的行 → 删附件图（登记行+落盘文件），
    流水补「照片已按30天策略清理」备注。文字档案（台账行+流水）永久保留。
    由 butler 心跳每日调一次；手机相册原图不受影响（这里删的是入库副本）。

    Returns:
        dict: {success, cleaned: n, note}
    """
    _ensure_consume_table()
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M')
    cleaned = 0
    try:
        from src.core import panel_images as _pimg
    except Exception as e:
        return {'success': False, 'cleaned': 0, 'note': f'panel_images 不可用: {e}'}
    with _conn() as c:
        logs = c.execute(f'SELECT id, row_id, name FROM {CONSUME_TABLE} '
                         'WHERE undone=0 AND action IN ("eaten","trashed") '
                         'AND created_at < ? AND (note IS NULL OR note="")',
                         (cutoff,)).fetchall()
        for lg in logs:
            rid = lg['row_id']
            # 台账行还在且被放回在存（撤销过又重新消耗的看当前状态）→ 不清图
            row = c.execute(f'SELECT "状态" FROM "{FRIDGE_TABLE}" WHERE id=?', (rid,)).fetchone()
            if row and str(row['状态'] or '在存') == '在存':
                continue
            try:
                imgs = _pimg.list_images(FRIDGE_TABLE, record_id=rid, limit=100)
                for im in imgs:
                    _pimg.delete_image(im['filename'])
                cleaned += len(imgs)
            except Exception as e:
                logger.debug(f'[fridge] 清理 {lg["name"]} 图片跳过: {e}')
            try:
                c.execute(f'UPDATE {CONSUME_TABLE} SET note=? WHERE id=?',
                          (f'照片已按{days}天策略清理（{datetime.now().strftime("%Y-%m-%d")}）', lg['id']))
            except Exception:
                pass
        c.commit()
    if cleaned:
        logger.info(f'[fridge] #1376 照片30天策略：清理 {cleaned} 张消耗副本图')
    return {'success': True, 'cleaned': cleaned,
            'note': f'本次清理 {cleaned} 张（文字档案全部保留）'}
