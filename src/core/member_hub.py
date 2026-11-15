# -*- coding: utf-8 -*-
"""member_hub.py — 「以人为家的数据中枢」（#1207/#1208/#1210，2026-10-25 定版拍板一步到位）

三层：
  一、人物画像层：panel_members 为画像唯一真家（32 字段名册，不新建第四套名册），
      新增 profile_note 列存 AI 分析趋势（批改/健康结论自动写回）
  二、数据血缘层：读侧聚合——数据不搬家，collect_brief() 按成员一次查齐
      学习批改/错题本/成长时间轴/健康/用药/健身/账单/管家议程各维度现有表
  三、主动触达：after_grade() 批改完 → 错题多 → butler_agenda 主动开口（条目挂人）
      + 画像趋势写回；agenda_summary_for_chat() 让问管家看得到今日议程

注入：build_context(question) 检测问题里的成员名（含「孩子」泛指）→ 一次注入全维度摘要，
问管家「帮小明分析这周数学」不用再翻面板，中枢一次喂齐。
所有表查询独立容错：单表结构变化不影响其他维度。
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'

logger_cfg = {'name': 'MemberHub'}


def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure():
    """import 即就绪：panel_members 幂等补 profile_note 画像趋势列；
    butler_agenda 幂等补 member 列（不 import 但书防循环，直接 ALTER）"""
    try:
        conn = _conn()
        cols = {r[1] for r in conn.execute('PRAGMA table_info(panel_members)').fetchall()}
        if 'profile_note' not in cols:
            conn.execute("ALTER TABLE panel_members ADD COLUMN profile_note TEXT DEFAULT ''")
        try:
            bcols = {r[1] for r in conn.execute('PRAGMA table_info(butler_agenda)').fetchall()}
            if bcols and 'member' not in bcols:
                conn.execute("ALTER TABLE butler_agenda ADD COLUMN member TEXT DEFAULT ''")
        except Exception:
            pass  # 但书表还没建（但书未启用）——之后但书 ensure 会建全
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'[MemberHub] ensure 跳过: {e}')


ensure()


# ═══════════════════════════════════════════════════════════
# 一、人物画像层
# ═══════════════════════════════════════════════════════════

def list_members():
    """全家名册（画像行 dict 列表）"""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute('SELECT * FROM panel_members ORDER BY id').fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def resolve_member(name_part):
    """按名字（精确→包含）解析成员，返回 dict 或 None"""
    if not name_part:
        return None
    key = str(name_part).strip()
    if not key:
        return None
    ms = list_members()
    for m in ms:
        if m.get('name') == key:
            return m
    for m in ms:
        if key in str(m.get('name') or '') or str(m.get('name') or '') in key and len(str(m.get('name') or '')) >= 2:
            return m
    return None


def guess_members(question=''):
    """泛指解析（返回 list）：问题提「孩子/儿子/闺女」没点名 → 名册里有学籍信息的孩子（可能多个）；
    全家只有一个人 → 那个人；都不满足 → []"""
    ms = list_members()
    if not ms:
        return []
    kids = [m for m in ms if str(m.get('school') or '').strip() or str(m.get('grade') or '').strip()]
    _GENERIC = ('孩子', '儿子', '闺女', '女儿', '娃', '宝宝', '小孩')
    if any(g in question for g in _GENERIC):
        return kids[:2]  # 多个孩子全注入（限 2 人防 token 爆）
    if len(ms) == 1:
        return ms[:1]
    return []


def append_profile_note(name, line, max_chars=2400):
    """AI 分析结论写回画像（追加式，限长留尾）"""
    line = str(line or '').strip()
    if not line or not name:
        return False
    try:
        conn = _conn()
        row = conn.execute('SELECT profile_note FROM panel_members WHERE name=?', (name,)).fetchone()
        old = (row['profile_note'] if row else '') or ''
        stamp = datetime.now().strftime('%m-%d')
        new = (old + f'\n[{stamp}] {line}').strip()
        if len(new) > max_chars:  # 保留最近的
            new = new[-max_chars:]
            nl = new.find('\n')
            if nl > 0:
                new = new[nl + 1:]
        conn.execute('UPDATE panel_members SET profile_note=? WHERE name=?', (new, name))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f'[MemberHub] 画像写回失败 {name}: {e}')
        return False


# ═══════════════════════════════════════════════════════════
# 二、数据血缘层（读侧聚合）
# ═══════════════════════════════════════════════════════════

def _q(sql, args=()):
    """独立容错查询：单表失败返回 []"""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f'[MemberHub] 查询跳过: {e}')
        return []


def _children_id(name):
    rows = _q('SELECT id FROM children WHERE name=?', (name,))
    return rows[0]['id'] if rows else None


def collect_brief(name, days=7):
    """一个成员的全维度聚合（血缘层读侧）——各维度独立容错"""
    brief = {'name': name, 'profile': {}, 'study': [], 'wrong_book': [], 'timeline': [],
             'health': [], 'fitness': [], 'meds': [], 'growth': [], 'bills': [], 'agenda': []}
    m = resolve_member(name)
    if m:
        brief['profile'] = m
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # 学习批改 + 错题本
    brief['study'] = _q('SELECT * FROM study_grades WHERE child=? ORDER BY id DESC LIMIT 7', (name,))
    brief['wrong_book'] = _q(
        "SELECT id, subject, question, knowledge_point, wrong_count FROM study_wrong_book "
        "WHERE child=? AND status='active' ORDER BY wrong_count DESC, id DESC LIMIT 6", (name,))

    # 成长时间轴（kid_* 旧链，child_id 经 children 名匹配）
    cid = _children_id(name)
    if cid:
        brief['timeline'] = _q(
            'SELECT ts, category, event_type, title, detail FROM kid_timeline '
            'WHERE child_id=? ORDER BY id DESC LIMIT 10', (cid,))

    # 健康/用药/健身/生长（panel 表 member 列存名字）
    brief['health'] = _q(
        'SELECT "日期" AS d, "类型" AS typ, "项目" AS item, "结果" AS res, "下次日期" AS nxt '
        'FROM panel_health_file WHERE "成员"=? ORDER BY id DESC LIMIT 5', (name,))
    brief['fitness'] = _q(
        'SELECT "日期" AS d, "运动项目" AS item, "时长分钟" AS mins FROM panel_fitness_log '
        'WHERE "成员"=? ORDER BY id DESC LIMIT 5', (name,))
    brief['meds'] = _q(
        'SELECT "药品名称" AS item, "库存" AS stock, "有效期" AS exp, "服用方法" AS how '
        'FROM panel_med_cabinet WHERE ("服用成员"=? OR "成员"=?) ORDER BY id DESC LIMIT 5', (name, name))

    # 账单（近 N 天合计 + 最近笔数）
    bills = _q(
        'SELECT "日期" AS d, "项目" AS item, "金额" AS amt, "分类" AS cat FROM panel_accounting_daily_1 '
        'WHERE member=? AND "日期">=? ORDER BY id DESC LIMIT 20', (name, since))
    brief['bills'] = bills
    try:
        brief['bill_sum'] = round(sum(float(b.get('amt') or 0) for b in bills), 2)
    except Exception:
        brief['bill_sum'] = 0

    # 管家议程里挂这个人的 open 条目
    brief['agenda'] = _q(
        "SELECT kind, level, title FROM butler_agenda WHERE status='open' AND member=? "
        "ORDER BY CASE level WHEN 'urge' THEN 0 ELSE 1 END, id DESC LIMIT 6", (name,))
    return brief


def _fmt_brief(b, max_chars=1600):
    """聚合 dict → 注入文本（紧凑，给模型看）"""
    p = b.get('profile') or {}
    L = []
    head = f"### 👤 家庭成员「{b['name']}」画像"
    tags = []
    for k, lab in (('relation', '关系'), ('grade', '年级'), ('school', '学校'), ('birthday', '生日'),
                   ('height_cm', '身高'), ('weight_kg', '体重'), ('chronic', '慢病'), ('allergy', '过敏'),
                   ('like_food', '爱吃'), ('hate_food', '忌口'), ('interests', '兴趣')):
        v = str(p.get(k) or '').strip()
        if v:
            tags.append(f'{lab}:{v}')
    L.append(head + ('（' + '，'.join(tags[:10]) + '）' if tags else '（名册暂无详情）'))
    note = str(p.get('profile_note') or '').strip()
    if note:
        L.append('📊 近期趋势（AI 自动记录）:\n' + '\n'.join(note.split('\n')[-6:]))

    if b['study']:
        g = b['study'][:5]
        L.append('📚 最近批改: ' + '；'.join(
            f"{r.get('created_at', '')[:10] or ''}{r.get('subject') or '作业'} {r.get('total')}题错{r.get('wrong')}（正确率{r.get('correct_rate')}%）"
            for r in g))
    if b['wrong_book']:
        L.append('❌ 待攻克错题: ' + '；'.join(
            f"{r.get('knowledge_point') or r.get('question', '')[:20]}×{r.get('wrong_count')}次" for r in b['wrong_book'][:4]))
    if b['timeline']:
        L.append('🧸 成长时间轴最近: ' + '；'.join(
            f"{(r.get('ts') or '')[:10]}{r.get('title') or ''}" for r in b['timeline'][:4]))
    if b['health']:
        L.append('🏥 健康档案: ' + '；'.join(
            f"{r.get('d') or ''}{r.get('item') or ''}{('→' + str(r.get('nxt'))) if r.get('nxt') else ''}"
            for r in b['health'][:4]))
    if b['meds']:
        L.append('💊 在用药品: ' + '；'.join(
            f"{r.get('item') or ''}({r.get('how') or ''})" for r in b['meds'][:4]))
    if b['fitness']:
        L.append('🏃 健身: ' + '；'.join(
            f"{r.get('d') or ''}{r.get('item') or ''}{r.get('mins') or ''}分钟" for r in b['fitness'][:3]))
    if b.get('bills'):
        L.append(f"💰 近7天花销合计 ¥{b.get('bill_sum', 0)}（最近: " + '；'.join(
            f"{r.get('item') or ''}¥{r.get('amt')}" for r in b['bills'][:4]) + '）')
    if b['agenda']:
        L.append('🛎️ 管家盯着他/她的事: ' + '；'.join(r.get('title') or '' for r in b['agenda'][:4]))
    txt = '\n'.join(L)
    return txt[:max_chars]


# ═══════════════════════════════════════════════════════════
# 一次注入（#1210）：问管家问题 → 命中成员 → 全维度摘要
# ═══════════════════════════════════════════════════════════

# 命中域触发词：问题里出现这些且命中了成员才注入（避免纯闲聊也灌画像）
_TRIGGER = ('学习', '作业', '批改', '成绩', '错题', '考试', '健康', '体检', '吃药', '药', '看病',
            '花了', '花销', '账', '消费', '成长', '最近', '情况', '怎么样', '分析', '打卡', '运动', '健身')


def build_context(question, max_chars=1600):
    """问题提到名册成员（或「孩子」泛指）→ 返回注入文本；无命中返回 ''"""
    try:
        q = str(question or '')
        if not q or not getattr(build_context, '_members_ok', True):
            return ''
        ms = list_members()
        if not ms:
            return ''
        hits = []
        for m in ms:
            nm = str(m.get('name') or '').strip()
            if nm and len(nm) >= 2 and nm in q:
                hits.append(m)
        if not hits and any(t in q for t in _TRIGGER):
            # 泛指命中也要问题里带域触发词，纯聊天不注入
            hits = guess_members(q)
        if not hits:
            return ''
        out = []
        for m in hits[:2]:  # 最多注入两人，防 token 爆
            b = collect_brief(str(m.get('name') or ''), days=7)
            if b:
                out.append(_fmt_brief(b))
        if not out:
            return ''
        return ('## 👨‍👩‍👧 数据中枢·成员全景（命中问题提到的人，以下为本地真实数据，直接引用）\n\n'
                + '\n\n'.join(out))
    except Exception as e:
        print(f'[MemberHub] build_context 失败: {e}')
        return ''


# ═══════════════════════════════════════════════════════════
# 三、主动触达：批改后回调（写回画像 + 议程挂人）
# ═══════════════════════════════════════════════════════════

def after_grade(child, subject, total, wrong, weak_points=None, note=''):
    """批改入库后调：①画像趋势写回 ②错题多→管家议程主动开口（条目挂人）
    返回 {'noted':bool, 'alerted':bool}"""
    out = {'noted': False, 'alerted': False}
    try:
        rate = round((total - wrong) / total * 100, 1) if total else 0
        weak = '、'.join([str(w) for w in (weak_points or [])[:3] if w])[:60]
        line = f'{subject or "作业"}批改 {total} 题错 {wrong} 题（正确率 {rate}%）' + (f'，薄弱: {weak}' if weak else '')
        out['noted'] = append_profile_note(child, line)
        # 触发规则：错题 ≥5 或 错误率 ≥30% → 主动开口
        if total and (wrong >= 5 or (100 - rate) >= 30):
            try:
                from src.core.butler_agenda import _add_item
                today = datetime.now().strftime('%Y-%m-%d')
                title = f'📚 {child} 今天{subject or "作业"}错 {wrong}/{total} 题（正确率 {rate}%）'
                detail = ('薄弱点: ' + weak) if weak else '详见学习中心错题本'
                out['alerted'] = _add_item(
                    dedup_key=f'study_grade:{child}:{today}', source_panel='study_center',
                    kind='study', level='urge', title=title, detail=detail, member=child)
            except Exception as _be:
                print(f'[MemberHub] 议程触达失败: {_be}')
    except Exception as e:
        print(f'[MemberHub] after_grade 失败: {e}')
    return out


# ═══════════════════════════════════════════════════════════
# 问管家议程摘要（#1209 补空档：以前议程只在前端卡片，模型看不到）
# ═══════════════════════════════════════════════════════════

def agenda_summary_for_chat(max_items=10, max_chars=700):
    """今日 open 议程 → 一段注入文本；无 open 返回 ''"""
    try:
        rows = _q("SELECT source_panel, kind, level, title, member FROM butler_agenda "
                  "WHERE status='open' ORDER BY CASE level WHEN 'urge' THEN 0 ELSE 1 END, id DESC LIMIT ?",
                  (max_items,))
        if not rows:
            return ''
        L = ['## 🛎️ 管家今日议程（本地真实数据，用户问「有什么要办的」直接引用）']
        for r in rows:
            mark = '🔴' if r.get('level') == 'urge' else '⚪'
            who = f"（{r['member']}）" if r.get('member') else ''
            L.append(f'- {mark} {r.get("title") or ""}{who}')
        return '\n'.join(L)[:max_chars]
    except Exception as e:
        print(f'[MemberHub] agenda_summary 失败: {e}')
        return ''


# ═══════════════════════════════════════════════════════════
# HTTP API（web_app 注册）：/api/members/hub + /api/members/timeline
# ═══════════════════════════════════════════════════════════

def get_bp():
    from flask import Blueprint, jsonify, request
    bp = Blueprint('member_hub', __name__)

    @bp.route('/api/members/hub', methods=['GET'])
    def api_member_hub():
        name = (request.args.get('name') or '').strip()
        days = min(int(request.args.get('days') or 7), 90)
        if not name:
            # 不带名字 → 全家概览（每人一行核心指标）
            out = []
            for m in list_members():
                nm = str(m.get('name') or '')
                b = collect_brief(nm, days=days)
                g = (b.get('study') or [{}])[0]
                out.append({
                    'name': nm, 'relation': m.get('relation'), 'grade': m.get('grade'),
                    'latest_grade': ({k: g.get(k) for k in ('subject', 'total', 'wrong', 'correct_rate', 'created_at')} if g else None),
                    'wrong_book_count': len(b.get('wrong_book') or []),
                    'agenda_count': len(b.get('agenda') or []),
                    'bill_sum': b.get('bill_sum', 0),
                })
            return jsonify({'ok': True, 'members': out})
        if not resolve_member(name):
            return jsonify({'ok': False, 'error': f'名册里没有「{name}」'}), 404
        return jsonify({'ok': True, 'brief': collect_brief(name, days=days)})

    @bp.route('/api/members/timeline', methods=['GET'])
    def api_member_timeline():
        """全家事件流（血缘层时间线）：学习批改+成长时间轴+健康+账单混排，最近的在前"""
        name = (request.args.get('name') or '').strip()
        days = min(int(request.args.get('days') or 30), 365)
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        events = []
        if name:
            targets = [name]
        else:
            targets = [str(m.get('name') or '') for m in list_members()]
        for nm in targets:
            if not nm:
                continue
            for r in _q('SELECT created_at AS ts, subject, total, wrong, correct_rate FROM study_grades '
                        'WHERE child=? AND created_at>=? ORDER BY id DESC LIMIT 30', (nm, since)):
                events.append({'ts': r['ts'], 'member': nm, 'domain': 'study',
                               'title': f"{r.get('subject') or '作业'}批改 {r.get('total')}题错{r.get('wrong')}（{r.get('correct_rate')}%）"})
            cid = _children_id(nm)
            if cid:
                for r in _q('SELECT ts, title, category FROM kid_timeline WHERE child_id=? ORDER BY id DESC LIMIT 30', (cid,)):
                    events.append({'ts': r.get('ts'), 'member': nm, 'domain': 'growth',
                                   'title': r.get('title') or '', 'category': r.get('category')})
            for r in _q('SELECT created_at AS ts, "项目" AS item, "金额" AS amt FROM panel_accounting_daily_1 '
                        'WHERE member=? AND "日期">=? ORDER BY id DESC LIMIT 30', (nm, since)):
                events.append({'ts': r.get('ts'), 'member': nm, 'domain': 'bill',
                               'title': f"{r.get('item') or ''} ¥{r.get('amt')}"})
        events.sort(key=lambda e: str(e.get('ts') or ''), reverse=True)
        return jsonify({'ok': True, 'events': events[:100]})

    @bp.route('/api/members/profile_note', methods=['POST'])
    def api_profile_note():
        """AI 分析结论写回画像（追加）"""
        d = request.get_json(silent=True) or {}
        name = str(d.get('name') or '').strip()
        note = str(d.get('note') or '').strip()
        if not name or not note:
            return jsonify({'ok': False, 'error': 'name 和 note 必填'}), 400
        return jsonify({'ok': append_profile_note(name, note[:300])})

    return bp
