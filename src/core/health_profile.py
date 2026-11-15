# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
# -*- coding: utf-8 -*-
"""★ 2026-10-11 #1071~#1073 健康中枢后端（定版拍板 B 方案「全部开发」）

一句话：**档案中枢 + 联动钩子**——人手一份健康画像，全家药/食/体检互相盯着。

★ 架构定版（2026-10-11 实施中拍板）：
- 事件类数据（体检/药箱/健身/成长）全部存**通用面板表**（panel_health_file /
  panel_med_cabinet / panel_fitness_log / panel_child_growth，对话即录入/AI 分析/
  照片/提醒条全白嫖现成能力，数据真家唯一，杜绝双存储死代码）——面板由
  health_panels.ensure_health_panels() 出厂创建
- 本文件只管三件事：
  ① family_diet_profile 健康画像（#1012 旧表 ALTER 安全扩列：birthday/blood_type/
     constitution/avoid_eat/need_eat/emergency_contact）
  ② 本地比对预警：health_alerts()（过敏红条+忌口/易上火黄条）、药食冲突小词典
  ③ health_reminders()：跨面板健康待办（临期药/停药/疫苗/下次体检）+ hub 预查注入

铁律：不做诊断，只做档案与提醒；数据比对全部本地毫秒级，语言组织交模型。
"""
import logging
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('health_hub', __name__, url_prefix='/api/panels/health')

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'sqlite' / 'lingzhi.db'

# 出厂健康面板（health_panels.ensure_health_panels 负责创建）
P_HEALTH_FILE = 'panel_health_file'        # 健康档案（体检/就医/疫苗/随访）
P_MED_CABINET = 'panel_med_cabinet'        # 药箱（药品+效期+服用方法）
P_CHILD_GROWTH = 'panel_child_growth'      # 孩子成长（身高体重/视力/接种）
P_FITNESS = 'panel_fitness_log'            # 健身记录（运动/体重/睡眠备注）


def _conn():
    # ★ #1074 全新安装兜底：data/sqlite 目录可能还不存在（打包不含 sqlite），先建目录
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M')


# ══════════════════════════ #1071 ① 健康画像（family_diet_profile 扩列） ══════════════════════════

# #1012 旧表扩列：健康画像新增维度（duplicate column 静默跳过）
_DIET_NEW_COLS = [
    ('birthday', 'TEXT DEFAULT \'\''),            # 生日（年龄/成长联动）
    ('blood_type', 'TEXT DEFAULT \'\''),          # 血型（紧急卡）
    ('constitution', 'TEXT DEFAULT \'\''),        # 体质：易上火/脾胃虚/过敏体质…
    ('avoid_eat', 'TEXT DEFAULT \'\''),           # 上火/不舒服别吃的：辣椒、羊肉…
    ('need_eat', 'TEXT DEFAULT \'\''),            # 不爱吃但要补的：胡萝卜、牛奶…
    ('emergency_contact', 'TEXT DEFAULT \'\''),   # 紧急联系人+电话+慢性病史（紧急卡）
]

_MIGRATION_FLAG = 'health_diet_cols_v1'  # 扩列标记（幂等防重跑）


def ensure_health():
    with _conn() as c:
        # 全新用户一步到位建全列；旧库走 ALTER
        c.execute("""CREATE TABLE IF NOT EXISTS family_diet_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    allergies TEXT DEFAULT '',
                    dislikes  TEXT DEFAULT '',
                    likes     TEXT DEFAULT '',
                    conditions TEXT DEFAULT '',
                    notes     TEXT DEFAULT '',
                    birthday TEXT DEFAULT '',
                    blood_type TEXT DEFAULT '',
                    constitution TEXT DEFAULT '',
                    avoid_eat TEXT DEFAULT '',
                    need_eat TEXT DEFAULT '',
                    emergency_contact TEXT DEFAULT '',
                    updated_at TEXT)""")
        c.execute("CREATE TABLE IF NOT EXISTS health_meta (key TEXT PRIMARY KEY, value TEXT)")
        existing = {r[1] for r in c.execute("PRAGMA table_info(family_diet_profile)").fetchall()}
        for col, decl in _DIET_NEW_COLS:
            if col not in existing:
                try:
                    c.execute(f"ALTER TABLE family_diet_profile ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError as e:
                    logger.debug(f"[health] 扩列 {col} 跳过: {e}")
        c.execute("INSERT OR REPLACE INTO health_meta (key, value) VALUES (?,?)",
                  (_MIGRATION_FLAG, _now()))
        c.commit()


# ══════════════════════════ #1071 画像 CRUD ══════════════════════════

def _strip(d, keys, limit=400):
    return {k: str(d.get(k) or '').strip()[:limit] for k in keys}


def members():
    ensure_health()
    with _conn() as c:
        rows = c.execute("SELECT * FROM family_diet_profile ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def member_save(d):
    ensure_health()
    name = str(d.get('name') or '').strip()[:20]
    if not name:
        return False, '成员称呼不能为空'
    f = _strip(d, ('allergies', 'dislikes', 'likes', 'conditions', 'notes',
                   'birthday', 'blood_type', 'constitution', 'avoid_eat',
                   'need_eat', 'emergency_contact'))
    now = _now()
    vals12 = [f[k] for k in ('allergies', 'dislikes', 'likes', 'conditions', 'notes',
                             'birthday', 'blood_type', 'constitution', 'avoid_eat',
                             'need_eat', 'emergency_contact')]
    with _conn() as c:
        if d.get('id'):
            c.execute("""UPDATE family_diet_profile SET name=?, allergies=?, dislikes=?,
                        likes=?, conditions=?, notes=?, birthday=?, blood_type=?,
                        constitution=?, avoid_eat=?, need_eat=?, emergency_contact=?,
                        updated_at=? WHERE id=?""",
                      [name] + vals12 + [now, int(d['id'])])
        else:
            c.execute("""INSERT INTO family_diet_profile
                        (name, allergies, dislikes, likes, conditions, notes, birthday,
                         blood_type, constitution, avoid_eat, need_eat, emergency_contact,
                         updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      [name] + vals12 + [now])
        c.commit()
    return True, ''


def member_delete(mid):
    ensure_health()
    with _conn() as c:
        c.execute("DELETE FROM family_diet_profile WHERE id=?", (int(mid),))
        c.commit()


# ══════════════════════════ #1071 ② 健康预警（过敏红条+忌口/易上火黄条） ══════════════════════════

def _split_terms(raw):
    return [t.strip() for t in re.split(r'[、,，;；\s]+', str(raw or '')) if t.strip()]


def health_alerts(foods_text, note=''):
    """菜名/备注 × 全家档案 → 三级预警（本地比对零 token）。
    返回 [{'level': danger|warn, 'kind': 过敏|忌口|易上火, 'member':xx, 'term':xx,
           'hit_in':菜名}]，前端按 level 上红条/黄条。"""
    text = str(foods_text or '') + ' ' + str(note or '')
    if not text.strip():
        return []
    alerts = []
    for m in members():
        # 过敏 → 红条（最高优先，沿用 #1062 diet_alerts 语义）
        for alg in _split_terms(m.get('allergies')):
            if alg in text:
                alerts.append({'level': 'danger', 'kind': '过敏', 'member': m['name'],
                               'term': alg, 'hit_in': str(foods_text or '')[:60]})
        # 忌口 → 黄条
        for dis in _split_terms(m.get('dislikes')):
            if dis in text:
                alerts.append({'level': 'warn', 'kind': '忌口', 'member': m['name'],
                               'term': dis, 'hit_in': str(foods_text or '')[:60]})
        # 易上火别吃 → 黄条
        for av in _split_terms(m.get('avoid_eat')):
            if av in text:
                alerts.append({'level': 'warn', 'kind': '易上火', 'member': m['name'],
                               'term': av, 'hit_in': str(foods_text or '')[:60]})
    return alerts


# ══════════════════════════ #1071 药食冲突小词典 ══════════════════════════

# (药关键词, 食物关键词, 提醒文案)。本地纯文本比对，命中即红条，不做诊断。
MED_FOOD_CONFLICTS = [
    ('头孢', '酒', '服用头孢期间严禁饮酒（含酒心巧克力/醉虾/藿香正气水），可能引发双硫仑样反应'),
    ('头孢', '酒精', '服用头孢期间严禁含酒精饮食，可能引发双硫仑样反应'),
    ('甲硝唑', '酒', '服用甲硝唑期间禁止饮酒，可能引发双硫仑样反应'),
    ('甲硝唑', '酒精', '服用甲硝唑期间禁止含酒精饮食，可能引发双硫仑样反应'),
    ('降压', '甘草', '降压药与甘草制剂同用可能升高血压'),
    ('地高辛', '甘草', '服用地高辛期间避免甘草（可能增加中毒风险）'),
    ('华法林', '菠菜', '服用华法林期间大量吃深绿叶菜（富含维生素K）会影响药效，需稳定摄入量'),
    ('华法林', '韭菜', '服用华法林期间避免大量韭菜等富含维生素K的食物'),
    ('他汀', '西柚', '服用他汀类降脂药避免吃西柚/葡萄柚（会升高血药浓度）'),
    ('降压', '西柚', '部分降压药与西柚同食可能增强药效，建议错开或咨询医生'),
    ('左甲状腺素', '豆浆', '左甲状腺素与豆浆/豆制品需间隔 4 小时以上服用'),
    ('优甲乐', '豆浆', '优甲乐（左甲状腺素）与豆浆同服影响吸收，建议晨起空腹服'),
    ('补铁', '茶', '补铁制剂与浓茶同服影响吸收，建议间隔 1~2 小时'),
    ('补铁', '牛奶', '补铁制剂与牛奶同服影响吸收，建议错开'),
    ('铁剂', '茶', '铁剂与浓茶同服影响吸收，建议间隔 1~2 小时'),
    ('铁剂', '牛奶', '铁剂与牛奶同服影响吸收，建议错开'),
    ('钙', '菠菜', '补钙与高草酸食物（菠菜）同食影响吸收，菠菜建议先焯水'),
    ('抗生素', '牛奶', '部分抗生素与牛奶同服影响吸收，用温水送服'),
    ('布洛芬', '空腹', '布洛芬建议饭后服用，减少胃刺激'),
]


def med_food_check(text):
    """一段话（如「爸爸在吃头孢，晚上喝酒了」）× 词典 → 冲突提醒列表。"""
    text = str(text or '')
    if not text.strip():
        return []
    hits = []
    for drug, food, msg in MED_FOOD_CONFLICTS:
        if drug in text and food in text:
            hits.append({'level': 'danger', 'kind': '药食冲突',
                         'member': '', 'term': f'{drug}×{food}', 'msg': msg})
    return hits


# ══════════════════════════ #1071 ③ health_reminders() 跨面板健康待办 ══════════════════════════

def _panel_rows(table, where='', limit=500):
    """读通用面板表（不存在/为空都安全返回 []——面板可能还没出厂创建）。"""
    try:
        with _conn() as c:
            sql = f'SELECT * FROM "{table}"'
            if where:
                sql += f' WHERE {where}'
            sql += f' LIMIT {int(limit)}'
            return [dict(r) for r in c.execute(sql).fetchall()]
    except sqlite3.OperationalError:
        return []


def _within(text, horizon):
    """'YYYY-MM-DD...' 或 'YYYY/MM/DD' → 是否 ≤ horizon。解析不了返回 False。"""
    try:
        d = date.fromisoformat(str(text or '')[:10].replace('/', '-'))
    except (ValueError, TypeError):
        return False
    return d <= horizon


def health_reminders(days_ahead=14):
    """未来 N 天健康待办（确定性，本地毫秒级），供收纳盒⏰/档案页横幅/首页提醒：
    - 药箱：有效期临期（≤N天）+ 停用日期到期（≤N天且填了服用成员）
    - 成长：下次接种日期（≤N天）
    - 档案：下次日期（体检/就医/疫苗复诊，≤N天）
    返回 [{'icon','text','date'}]，日期升序去重。"""
    ensure_health()
    today = date.today()
    horizon = today + timedelta(days=int(days_ahead))
    out = []
    # 药箱：效期 + 停药
    for r in _panel_rows(P_MED_CABINET):
        exp = r.get('有效期') or ''
        if exp and _within(exp, horizon):
            expired = str(exp)[:10] < today.isoformat()
            out.append({'icon': '⏰', 'date': str(exp)[:10],
                        'text': f"药箱里的{r.get('药品名称') or '某药'} "
                                f"{'已过期，请处理' if expired else '快过期，优先用'}（{str(exp)[:10]}）"})
        stop = r.get('停用日期') or ''
        who = r.get('服用成员') or ''
        if stop and who and _within(stop, horizon):
            out.append({'icon': '💊', 'date': str(stop)[:10],
                        'text': f"{who}的{r.get('药品名称') or '药'} {str(stop)[:10]} 到期停药"
                                f"（{r.get('服用方法') or '按医嘱'}）"})
    # 成长：下次接种
    for r in _panel_rows(P_CHILD_GROWTH):
        nxt = r.get('下次接种日期') or ''
        if nxt and _within(nxt, horizon):
            d = str(nxt)[:10]
            tag = '今天该打了' if d == today.isoformat() else ('已过期' if d < today.isoformat() else '快到期')
            out.append({'icon': '💉', 'date': d,
                        'text': f"{r.get('成员') or '孩子'} {r.get('疫苗记录') or '疫苗'} {tag}（{d}）"})
    # 健康档案：下次日期
    for r in _panel_rows(P_HEALTH_FILE):
        nxt = r.get('下次日期') or ''
        if nxt and _within(nxt, horizon):
            d = str(nxt)[:10]
            kind = r.get('类型') or '复查'
            out.append({'icon': '🩺', 'date': d,
                        'text': f"{r.get('成员') or '家人'} 的{kind}"
                                + (f"「{r.get('项目')}」" if r.get('项目') else '')
                                + f" 约定在 {d}"})
    seen, uniq = set(), []
    for r in sorted(out, key=lambda x: (x['date'], x['text'])):
        if r['text'] not in seen:
            seen.add(r['text'])
            uniq.append(r)
    return uniq


# ══════════════════════════ #1071 ④ hub_context_health() 查询链预查 ══════════════════════════

def hub_context_health() -> str:
    """档案中枢「健康域」预查文本（查询链 hub_quick_context 扩展点）：全家画像+用药一览。"""
    try:
        ms = members()
        if not ms:
            return ''
        lines = ['【全家健康档案】']
        for m in ms:
            parts = [m['name']]
            for key, label in (('allergies', '过敏'), ('dislikes', '忌口'),
                               ('avoid_eat', '易上火别吃'), ('constitution', '体质'),
                               ('conditions', '慢病'), ('blood_type', '血型')):
                if m.get(key):
                    parts.append(f"{label}:{m[key]}")
            lines.append('- ' + '｜'.join(parts))
        meds = [r for r in _panel_rows(P_MED_CABINET, limit=30) if r.get('服用成员')]
        if meds:
            lines.append('【正在服用】' + '；'.join(
                f"{r['服用成员']}×{r.get('药品名称') or '?'}（{r.get('服用方法') or '按医嘱'}）"
                for r in meds[:8]))
        return '\n'.join(lines)
    except Exception as e:
        logger.debug(f"[health] hub_context_health 跳过: {e}")
        return ''


# ══════════════════════════ 路由（画像编辑走这里，事件记录走通用面板路由） ══════════════════════════

@bp.route('/diet', methods=['GET'])
def diet_get():
    try:
        return jsonify({'success': True, 'members': members()})
    except Exception as e:
        logger.error(f"[health] diet 查询失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/diet', methods=['POST'])
def diet_post():
    try:
        d = request.get_json(force=True, silent=True) or {}
        ok, err = member_save(d)
        return (jsonify({'success': True, 'members': members()}) if ok
                else (jsonify({'success': False, 'error': err}), 400))
    except Exception as e:
        logger.error(f"[health] diet 保存失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/diet/delete', methods=['POST'])
def diet_del():
    try:
        d = request.get_json(force=True, silent=True) or {}
        mid = int(d.get('id') or 0)
        if not mid:
            return jsonify({'success': False, 'error': '缺少 id'}), 400
        member_delete(mid)
        return jsonify({'success': True, 'members': members()})
    except Exception as e:
        logger.error(f"[health] diet 删除失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/overview', methods=['GET'])
def overview():
    """健康档案中枢首页数据：画像 + 健康待办，一次拉全。"""
    try:
        # ★ #1370（2026-11-02）家庭食物消耗流水挂进画像：近 7 天吃掉/扔掉汇总
        #   数据源 fridge_consume_log（冰箱「吃掉了/处理掉了」按钮留痕），没数据自动缺席
        consume = {}
        try:
            from src.core.fridge_hub import consume_summary
            consume = consume_summary(days=7) or {}
        except Exception as _ce:
            logger.debug(f"[health] 消耗汇总跳过: {_ce}")
        return jsonify({
            'success': True,
            'members': members(),
            'reminders': health_reminders(),
            'consume': consume,
            'counts': {
                'med': len(_panel_rows(P_MED_CABINET, limit=999)),
                'growth': len(_panel_rows(P_CHILD_GROWTH, limit=999)),
                'file': len(_panel_rows(P_HEALTH_FILE, limit=999)),
                'fitness': len(_panel_rows(P_FITNESS, limit=999)),
            },
        })
    except Exception as e:
        logger.error(f"[health] overview 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/reminders', methods=['GET'])
def reminders():
    try:
        days = int(request.args.get('days') or 14)
        return jsonify({'success': True, 'reminders': health_reminders(days)})
    except Exception as e:
        logger.error(f"[health] reminders 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# 惰性建表：模块被 import 即就绪（web_app 启动注册蓝图时触发）
ensure_health()
