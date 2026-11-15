# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
# -*- coding: utf-8 -*-
"""★ 2026-10-11 #1072 健康中枢四面板出厂创建（定版拍板 B 方案）

四个面板全部走 panel_manager.create_panel_from_template 生成器（与界面「新建模块」同源）：
- health_file   健康档案：体检/就医/疫苗/随访统一台账 + 紧急卡 + 健康待办横幅
- med_cabinet   家庭药箱：药品/效期/服用方法 + 效期到期提醒
- fitness_log   健身记录：运动/体重/睡眠 + 趋势图
- child_growth  孩子成长：身高体重/视力/接种 + 成长曲线 + 接种提醒

启动时 ensure_health_panels()：缺哪个补哪个（幂等），并把旧「健康体检记录」
面板（health_check_1/2）的数据一次性迁入 health_file（标记键防重跑）。
事件数据存通用面板表（panel_<id>，中文字段列），对话即录入/AI 分析/照片/提醒条
全部复用现成能力——数据真家唯一，不搞双存储。
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'sqlite' / 'lingzhi.db'

_MIGRATE_FLAG = 'health_file_migrated_v1'

# ── #1098 面板可见性注册：界面显示靠 _order.json + _groups.json ──
_PANELS_DIR = Path(__file__).parent.parent.parent / 'data' / 'panels'
_ORDER_FILE = _PANELS_DIR / '_order.json'
_GROUPS_FILE = _PANELS_DIR / '_groups.json'
_RETIRED = ('health_check_1', 'health_check_2', 'home_medicine')  # 旧「健康体检记录」+ 老「家庭药箱」（字段少且0数据，被 med_cabinet 替代）退场
_HEALTH_GROUP = '健康守护'

# ── 四面板出厂定义（字段名=数据库列名，改字段会 ALTER 补列，老数据不丢） ──

HEALTH_PANELS = [
    {
        'id': 'health_file',
        'name': '健康档案',
        'icon': '🩺',
        'description': '全家体检/就医/疫苗/随访统一台账，人手一份健康画像，紧急卡+健康待办一目了然',
        'with_chart': False,
        'with_search': True,
        'features': {
            'remind': {'title_fields': ['成员', '项目'], 'date_field': '下次日期',
                       'advance_days': 14, 'label': '健康提醒'},
            'ai': True,
        },
        'ai_role': ('你是家庭健康档案分析师：追踪全家体检、就医、疫苗接种记录，'
                    '指标异常波动提醒关注或复查；只做档案整理与提醒，不做诊断，'
                    '紧急情况直接建议就医。'),
        'fields': [
            {'name': '成员', 'type': 'text', 'required': True},
            {'name': '类型', 'type': 'select',
             'options': ['体检', '就医', '疫苗', '复查随访']},
            {'name': '日期', 'type': 'date'},
            {'name': '项目', 'type': 'text'},
            {'name': '结果', 'type': 'text'},
            {'name': '下次日期', 'type': 'date'},
            {'name': '异常提示', 'type': 'text', 'adv': 1},
            {'name': '医院科室', 'type': 'text', 'adv': 1},
            {'name': '花费', 'type': 'number', 'adv': 1},
            {'name': '备注', 'type': 'textarea', 'adv': 1},
        ],
    },
    {
        'id': 'med_cabinet',
        'name': '家庭药箱',
        'icon': '💊',
        'description': '药品/保健品台账+效期盯梢+服用方法，谁在吃什么药全家看得见',
        'with_chart': False,
        'with_search': True,
        'features': {
            'remind': {'title_fields': ['药品名称'], 'date_field': '有效期',
                       'advance_days': 30, 'label': '效期提醒'},
            'ai': True,
        },
        'ai_role': ('你是家庭药箱管家：帮着清点库存、盯药品效期、核对服用方法；'
                    '只做整理和提醒，调整用药请遵医嘱。'),
        'fields': [
            {'name': '药品名称', 'type': 'text', 'required': True},
            {'name': '类别', 'type': 'select',
             'options': ['处方药', '非处方药', '中成药', '保健品', '医疗器械', '外用药']},
            {'name': '规格', 'type': 'text'},
            {'name': '库存', 'type': 'number'},
            {'name': '有效期', 'type': 'date'},
            {'name': '存放位置', 'type': 'text'},
            {'name': '服用成员', 'type': 'text'},
            {'name': '服用方法', 'type': 'text', 'adv': 1},
            {'name': '停用日期', 'type': 'date', 'adv': 1},
            {'name': '备注', 'type': 'textarea', 'adv': 1},
        ],
    },
    {
        'id': 'fitness_log',
        'name': '健身记录',
        'icon': '🏃',
        'description': '全家运动/体重/睡眠一起记，趋势图看变化',
        'with_chart': True,
        'with_search': True,
        'features': {'ai': True},
        'ai_role': ('你是家庭健身教练：看全家运动量、体重和睡眠趋势，'
                    '多鼓励、给可执行的小建议，不提苛刻目标。'),
        'fields': [
            {'name': '成员', 'type': 'text', 'required': True},
            {'name': '日期', 'type': 'date'},
            {'name': '运动项目', 'type': 'text'},
            {'name': '时长分钟', 'type': 'number'},
            {'name': '强度', 'type': 'select', 'options': ['轻度', '中等', '高强度']},
            {'name': '消耗千卡', 'type': 'number'},
            {'name': '体重kg', 'type': 'number'},
            {'name': '昨晚睡眠小时', 'type': 'number', 'adv': 1},
            {'name': '睡眠质量', 'type': 'select', 'adv': 1,
             'options': ['好', '一般', '差']},
            {'name': '备注', 'type': 'textarea', 'adv': 1},
        ],
    },
    {
        'id': 'child_growth',
        'name': '孩子成长',
        'icon': '🌱',
        'description': '身高体重/视力/疫苗接种一路记，成长曲线+接种提醒',
        'with_chart': True,
        'with_search': True,
        'features': {
            'remind': {'title_fields': ['成员', '疫苗记录'], 'date_field': '下次接种日期',
                       'advance_days': 14, 'label': '接种提醒'},
            'ai': True,
        },
        'ai_role': ('你是孩子成长记录员：追踪身高体重、视力和疫苗接种，'
                    '按年龄给参考区间提示；不做诊断，明显偏离建议儿保科看看。'),
        'fields': [
            {'name': '成员', 'type': 'text', 'required': True},
            {'name': '日期', 'type': 'date'},
            {'name': '身高cm', 'type': 'number'},
            {'name': '体重kg', 'type': 'number'},
            {'name': '左视力', 'type': 'text'},
            {'name': '右视力', 'type': 'text'},
            {'name': '疫苗记录', 'type': 'text', 'adv': 1},
            {'name': '下次接种日期', 'type': 'date', 'adv': 1},
            {'name': '备注', 'type': 'textarea', 'adv': 1},
        ],
    },
]

_PANEL_IDS = {p['id'] for p in HEALTH_PANELS}
_GROUP_MEMBERS = [p['id'] for p in HEALTH_PANELS]  # ★ #1103 健康守护组=纯四员（老 home_medicine 已退役）


def ensure_health_panels():
    """出厂四面板：缺哪个补哪个（幂等），随后旧体检数据一次性迁入。"""
    created = []
    try:
        from src.core.panel_manager import get_panel_manager
        mgr = get_panel_manager()
        for p in HEALTH_PANELS:
            if mgr.panel_exists(p['id']):
                continue
            r = mgr.create_panel_from_template(
                p['name'], p['id'], p['fields'],
                description=p['description'], icon=p['icon'],
                with_chart=p['with_chart'], with_search=p['with_search'],
                features=p['features'], ai_role=p['ai_role'], universal=False)
            if r.get('success'):
                created.append(p['id'])
        if created:
            logger.info(f"[health] 健康中枢面板出厂: {created}")
    except Exception as e:
        logger.warning(f"[health] 健康面板出厂跳过（不影响启动）: {e}")
        return
    _migrate_old_checkups()
    _register_visibility()


def _register_visibility():
    """★ #1098 四新面板登记进 _order/_groups（幂等），旧体检面板退场。

    create_panel_from_template 只生成面板本体四件套+数据库表，
    界面目录显示靠 _order.json（顺序）+ _groups.json（分组）注册，
    不登记=面板躺在盘上但界面看不见——这里补上根治。
    """
    try:
        _PANELS_DIR.mkdir(parents=True, exist_ok=True)
        changed = False

        # ── 1. _order.json：新面板追加序号，旧面板移除 ──
        order = {}
        if _ORDER_FILE.exists():
            try:
                order = json.loads(_ORDER_FILE.read_text('utf-8'))
            except Exception:
                order = {}
        next_pos = (max(order.values()) + 1) if order else 0
        for pid in _GROUP_MEMBERS:  # 四新面板补注册
            if pid not in order:
                order[pid] = next_pos
                next_pos += 1
                changed = True
        for old in _RETIRED:
            if old in order:
                order.pop(old)
                changed = True
        if changed:
            _ORDER_FILE.write_text(
                json.dumps(order, ensure_ascii=False, indent=1), 'utf-8')

        # ── 2. _groups.json：健康守护组换成四新面板，旧面板全组摘除 ──
        groups = {}
        if _GROUPS_FILE.exists():
            try:
                groups = json.loads(_GROUPS_FILE.read_text('utf-8'))
            except Exception:
                groups = {}
        for g in list(groups):
            cleaned = [m for m in groups[g] if m not in _RETIRED]
            if cleaned != groups[g]:
                groups[g] = cleaned
                changed = True
        if groups.get(_HEALTH_GROUP) != _GROUP_MEMBERS:
            groups[_HEALTH_GROUP] = list(_GROUP_MEMBERS)
            changed = True
        if changed:
            _GROUPS_FILE.write_text(
                json.dumps(groups, ensure_ascii=False, indent=2), 'utf-8')
        if changed:
            logger.info("[health] 面板可见性注册完成：四新面板上岗，旧体检面板退场")
    except Exception as e:
        logger.warning(f"[health] 面板可见性注册跳过（不影响启动）: {e}")


def _migrate_old_checkups():
    """旧「健康体检记录」面板（health_check_1/2）→ health_file，一次性幂等。"""
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)  # ★ #1074 全新安装兜底
        with sqlite3.connect(str(DB_PATH), timeout=15) as c:
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("CREATE TABLE IF NOT EXISTS health_meta (key TEXT PRIMARY KEY, value TEXT)")
            if c.execute("SELECT 1 FROM health_meta WHERE key=?", (_MIGRATE_FLAG,)).fetchone():
                return
            # 目标表可能还没建（面板刚出厂），没有就先按字段结构补一张
            cols = [('成员', 'TEXT'), ('类型', 'TEXT'), ('日期', 'TEXT'), ('项目', 'TEXT'),
                    ('结果', 'TEXT'), ('下次日期', 'TEXT'), ('异常提示', 'TEXT'),
                    ('医院科室', 'TEXT'), ('花费', 'REAL'), ('备注', 'TEXT'),
                    ('member', 'TEXT'), ('created_at', 'TEXT'), ('updated_at', 'TEXT')]
            col_names = {r[1] for r in c.execute(
                "PRAGMA table_info('panel_health_file')").fetchall()}
            if not col_names:
                ddl = ', '.join([f'"{n}" {t}' if n not in ('id',) else n
                                 for n, t in cols])
                c.execute(f"CREATE TABLE IF NOT EXISTS panel_health_file "
                          f"(id INTEGER PRIMARY KEY AUTOINCREMENT, {ddl})")
                col_names = {n for n, _ in cols}
            moved = 0
            now = datetime.now().strftime('%Y-%m-%d %H:%M')
            for old in ('panel_health_check_1', 'panel_health_check_2'):
                try:
                    rows = c.execute(f'SELECT * FROM "{old}"').fetchall()
                except sqlite3.OperationalError:
                    continue  # 旧表不存在
                names = [d[0] for d in c.execute(f'SELECT * FROM "{old}" LIMIT 0').description]
                for row in rows:
                    d = dict(zip(names, row))
                    item = str(d.get('项目') or '')
                    num = str(d.get('数值') or '')
                    proj = f'{item}：{num}'.strip('：')
                    note = str(d.get('备注') or '')
                    nxt = str(d.get('下次体检日') or '')
                    insert = {}
                    if '成员' in col_names:
                        insert['成员'] = str(d.get('成员') or '家人')
                    if '类型' in col_names:
                        insert['类型'] = '体检'
                    if '日期' in col_names:
                        insert['日期'] = str(d.get('日期') or '')
                    if '项目' in col_names:
                        insert['项目'] = proj
                    if '下次日期' in col_names:
                        insert['下次日期'] = nxt
                    if '备注' in col_names:
                        insert['备注'] = note
                    if 'member' in col_names:
                        insert['member'] = str(d.get('member') or '')
                    if 'created_at' in col_names:
                        insert['created_at'] = now
                    if 'updated_at' in col_names:
                        insert['updated_at'] = now
                    keys = ', '.join(f'"{k}"' for k in insert)
                    ph = ', '.join('?' * len(insert))
                    c.execute(f"INSERT INTO panel_health_file ({keys}) VALUES ({ph})",
                              list(insert.values()))
                    moved += 1
            c.execute("INSERT OR REPLACE INTO health_meta (key, value) VALUES (?,?)",
                      (_MIGRATE_FLAG, f'ok moved={moved} at {now}'))
            c.commit()
            if moved:
                logger.info(f"[health] 旧体检面板迁移完成：{moved} 条 → panel_health_file")
    except Exception as e:
        logger.warning(f"[health] 旧体检数据迁移跳过（不影响启动）: {e}")


if __name__ == '__main__':  # 手动补跑入口
    ensure_health_panels()
