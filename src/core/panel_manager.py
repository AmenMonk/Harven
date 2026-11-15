# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - UI Panel System (面板系统)
用户可安装自定义UI管理界面，每个面板是独立的iframe视图

面板格式（data/panels/<id>/）:
  panel.json    - 元数据（name, id, version, type, fields...）
  index.html    - 面板UI（加载到iframe）
  panel.js      - 面板逻辑（通过postMessage调哈文API）
  panel.css     - 面板样式

panel.json 标准格式:
{
  "name": "家庭记账",
  "id": "accounting",
  "version": "1.0.0",
  "type": "table",
  "icon": "wallet",
  "description": "管理家庭收支",
  "author": "user",
  "fields": [
    {"name": "amount", "label": "金额", "type": "number", "required": true},
    {"name": "category", "label": "分类", "type": "select", "options": ["餐饮","交通"]},
    {"name": "date", "label": "日期", "type": "date"},
    {"name": "note", "label": "备注", "type": "text"}
  ]
}
"""

import os
import json
import shutil
import zipfile
import tempfile
import sqlite3
import threading
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# 项目根目录
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent
PANELS_DIR = BASE_DIR / 'data' / 'panels'
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'

# 支持的字段类型及SQLite映射
FIELD_TYPE_MAP = {
    'text': 'TEXT',
    'number': 'REAL',
    'date': 'TEXT',
    'select': 'TEXT',
    'textarea': 'TEXT',
    'boolean': 'INTEGER',
    'image': 'TEXT',  # ★ #908 B方案：图片字段列只存附件库文件名（本体进 panel_images，不进表）
}

# panel.json 必填字段
REQUIRED_PANEL_FIELDS = ['name', 'id', 'version', 'type']


class Panel:
    """面板对象"""

    def __init__(self, panel_id, name, version, panel_type, icon='file',
                 description='', author='user', fields=None, installed_at=None,
                 features=None, universal=False, hidden=False):
        self.id = panel_id
        self.name = name
        self.version = version
        self.type = panel_type
        self.icon = icon
        self.description = description
        self.author = author
        self.fields = fields or []
        self.features = features or {}  # ★ 2026-09-16 面板能力配置（remind/stats/ai）
        self.installed_at = installed_at or datetime.now().isoformat()
        # ★ 2026-09-18 通用模块：true = 在所有分类空间页都可见，不硬归某一个分类
        self.universal = bool(universal)
        # ★ 2026-09-19 隐藏面板：true = 中枢页的子模块（数据在、卡墙不重复露出），/api/panels/manage 仍可见
        self.hidden = bool(hidden)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'version': self.version,
            'type': self.type,
            'icon': self.icon,
            'description': self.description,
            'author': self.author,
            'fields': self.fields,
            'features': self.features,
            'installed_at': self.installed_at,
            'universal': self.universal,
            'hidden': self.hidden,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            panel_id=d.get('id', ''),
            name=d.get('name', ''),
            version=d.get('version', '1.0.0'),
            panel_type=d.get('type', 'table'),
            icon=d.get('icon', 'file'),
            description=d.get('description', ''),
            author=d.get('author', 'user'),
            fields=d.get('fields', []),
            installed_at=d.get('installed_at'),
            features=d.get('features', {}),
            universal=d.get('universal', False),
            hidden=d.get('hidden', False),
        )


# ★ 2026-09-18 助手提示词按面板定制（生成器传给 panel-assist.js 的 hintText）
#   面板也可在 panel.json 里写 "assist_hint": "..." 覆盖这里
ASSIST_HINTS = {
    'home_items': '直接说就行：「螺丝刀收进书房五斗柜第二层」「充电宝在哪」「卫生纸只剩2卷了」「电钻借给小李了」——记物品、找东西、低库存和借出提醒都会。',
    'fridge_stock_1': '直接说就行：「买了2盒牛奶放冷藏」「鸡蛋还有几个」「冻饺子快过期了」——记食材、查库存、临期提醒都会。',
    # ★ 2026-09-20 #801 餐饮记录（拍菜品照片自动识别填表，热量汇总联动健康板块）
    'meal_diary': '直接说就行：「午饭吃了一碗牛肉面」「晚上全家吃了火锅」——自动记餐饮；拍菜品照片点「AI 识图填表」还能自动识别菜和估算热量。',
    # ★ 2026-10-11 #1072 健康中枢四面板
    'health_file': '直接说就行：「爸爸 10 月 11 号体检，血压偏高，明年 10 月复查」「儿子今天打了流感疫苗」——体检/就医/疫苗/随访都能记，下次日期到点会提醒。',
    'med_cabinet': '直接说就行：「买了 2 盒头孢放药箱上层，有效期到 2027 年 1 月」「布洛芬还有半盒」——记药、盯效期、谁在吃药都管。',
    'fitness_log': '直接说就行：「今天跑了 30 分钟，中等强度，体重 78 公斤」「昨晚睡了 7 个小时」——运动、体重、睡眠都能记。',
    'child_growth': '直接说就行：「儿子今天量了身高 125 公分 24 公斤」「视力左 5.0 右 4.9」「下个月 20 号打疫苗」——成长数据和接种提醒都能记。',
}


class PanelManager:
    """面板管理器（单例）"""

    def __init__(self):
        self._lock = threading.Lock()
        PANELS_DIR.mkdir(parents=True, exist_ok=True)
        self._panels: dict[str, Panel] = {}
        self._scan_panels()
        logger.info(f'[PanelManager] 已初始化，扫描到 {len(self._panels)} 个面板')
        # ★ P0-B FTS5：懒重建（后台线程，从未建过索引才全量跑）
        try:
            self.fts_rebuild_if_needed()
        except Exception:
            pass

    def _scan_panels(self):
        """扫描 data/panels/ 目录，加载所有面板"""
        self._panels.clear()
        if not PANELS_DIR.is_dir():
            return

        for panel_dir in sorted(PANELS_DIR.iterdir()):
            if not panel_dir.is_dir():
                continue
            panel_json = panel_dir / 'panel.json'
            if not panel_json.is_file():
                logger.warning(f'[PanelManager] 跳过 {panel_dir.name}: 缺少 panel.json')
                continue
            try:
                data = json.loads(panel_json.read_text(encoding='utf-8'))
                if not self._validate_panel_json(data):
                    logger.warning(f'[PanelManager] 跳过 {panel_dir.name}: panel.json 格式不合法')
                    continue
                panel = Panel.from_dict(data)
                self._panels[panel.id] = panel
                self._ensure_table(panel.id, panel.fields)
                # ★ 2026-09-16 字段变更懒升级（panel.json 新增字段 → ALTER TABLE 补列）
                self._ensure_panel_columns(panel.id, panel.fields)
                # ★ 2026-09-16 表面板文件随 panel.json 再生成（fields/features 变更即时生效）
                self._sync_generated_panel(panel)
                # ★ 2026-09-16 对话即录入：老面板 index.html 是安装时旧生成器落盘的，启动时补注入对话框引用
                self._upgrade_panel_index(panel_dir)
            except json.JSONDecodeError as e:
                logger.error(f'[PanelManager] 解析 panel.json 失败 {panel_dir.name}: {e}')
            except Exception as e:
                logger.error(f'[PanelManager] 加载面板失败 {panel_dir.name}: {e}')

    def _upgrade_panel_index(self, panel_dir):
        """旧面板 index.html 缺少对话即录入引用时补注入（幂等，升级后老面板自动获得对话框）
        ★ 2026-09-18 开发者截图反馈：聊天窗塌成横条 —— 根因是生成器模板只引 js 没引 css，
        而本方法原来看到 js 就跳过 → css 永远补不上。改为 js/css 各自独立检查补齐。"""
        idx = panel_dir / 'index.html'
        try:
            if not idx.is_file():
                return
            html = idx.read_text(encoding='utf-8')
            if 'panel.js' not in html:
                return  # 非表面板结构（自定义面板自己管理）
            if 'panel-assist.js' in html and 'panel-assist.css' in html:
                return  # 已齐全
            tag_css = '<link rel="stylesheet" href="/static/panel-assist.css">\n'
            tag_js = '<script src="/static/panel-assist.js"></script>\n'
            changed = False
            if 'panel-assist.css' not in html:
                if '/static/panel-api.css' in html:
                    html = html.replace('<link rel="stylesheet" href="/static/panel-api.css">',
                                        tag_css + '<link rel="stylesheet" href="/panel/%s/panel.css">' % panel_dir.name, 1)
                    changed = True
                elif '<link rel="stylesheet" href="/panel/%s/panel.css">' % panel_dir.name in html:
                    html = html.replace('<link rel="stylesheet" href="/panel/%s/panel.css">' % panel_dir.name,
                                        tag_css + '<link rel="stylesheet" href="/panel/%s/panel.css">' % panel_dir.name, 1)
                    changed = True
                elif '</head>' in html:
                    html = html.replace('</head>', tag_css + '</head>', 1)
                    changed = True
            if 'panel-assist.js' not in html:
                before = html
                html = html.replace('<script src="/panel/%s/panel.js"></script>' % panel_dir.name,
                                    tag_js + '<script src="/panel/%s/panel.js"></script>' % panel_dir.name, 1)
                changed = changed or (html != before)
            if changed:
                idx.write_text(html, encoding='utf-8')
                logger.info(f'[PanelManager] 已为面板 {panel_dir.name} 补齐对话即录入组件引用')
        except Exception as e:
            logger.warning(f'[PanelManager] 面板 {panel_dir.name} index.html 升级失败（不影响使用）: {e}')

    def _validate_panel_json(self, data: dict) -> bool:
        """校验panel.json的基本格式"""
        for field in REQUIRED_PANEL_FIELDS:
            if field not in data:
                logger.error(f'[PanelManager] panel.json 缺少必填字段: {field}')
                return False
        panel_id = data.get('id', '')
        if not panel_id or not panel_id.replace('-', '').replace('_', '').isalnum():
            logger.error(f'[PanelManager] panel id 不合法: {panel_id}')
            return False
        return True

    def _ensure_table(self, panel_id: str, fields: list):
        """根据fields定义自动创建SQLite表"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            table_name = f'panel_{panel_id}'

            check_sql = f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"
            cursor = conn.execute(check_sql)
            if cursor.fetchone():
                conn.close()
                return  # 表已存在

            column_defs = ['id INTEGER PRIMARY KEY AUTOINCREMENT']
            # ★ #1427（2026-11-04）「duplicate column name: member」根修（笔记本全新安装实锤）：
            #   记账等面板 panel.json 自带 member 字段，下面无条件再补一列同名 → 建表必炸 →
            #   表不存在 → 后续全挂「no such table」。台式机老表侥幸不触发，新装机必现。
            #   顺带防 panel.json 内部字段重名（同样会炸建表）。
            _seen_cols = set()
            for f in fields:
                fname = f.get('name', '')
                ftype = f.get('type', 'text')
                if not fname or fname.lower() in _seen_cols:
                    continue
                _seen_cols.add(fname.lower())
                sql_type = FIELD_TYPE_MAP.get(ftype, 'TEXT')
                column_defs.append(f'"{fname}" {sql_type}')
            if 'member' not in _seen_cols:
                column_defs.append('"member" TEXT')  # ★ D批 家庭共享：记录人（字段已含则不重复补）
            column_defs.append('created_at TEXT')
            column_defs.append('updated_at TEXT')

            create_sql = f'CREATE TABLE IF NOT EXISTS {table_name} ({", ".join(column_defs)})'
            conn.execute(create_sql)
            conn.commit()
            conn.close()
            logger.info(f'[PanelManager] 创建面板数据表: {table_name}')
        except Exception as e:
            logger.error(f'[PanelManager] 创建数据表失败 {panel_id}: {e}')

    def _ensure_panel_columns(self, panel_id: str, fields: list):
        """★ 2026-09-16 字段变更懒升级：panel.json 新增字段时 ALTER TABLE 补列（旧数据保留）"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            table_name = f'panel_{panel_id}'
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            added = []
            for f in fields:
                fname = f.get('name', '')
                if not fname or fname in cols:
                    continue
                sql_type = FIELD_TYPE_MAP.get(f.get('type', 'text'), 'TEXT')
                conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{fname}" {sql_type}')
                added.append(fname)
            if added:
                conn.commit()
                logger.info(f'[PanelManager] 表 {table_name} 已补列: {added}')
            conn.close()
        except Exception as e:
            logger.error(f'[PanelManager] 字段升级失败 {panel_id}: {e}')

    def _sync_generated_panel(self, panel):
        """★ 2026-09-16 表面板文件随 panel.json 自动再生成（fields/features 变更即时生效）。
        只处理生成器产出的面板（panel.js 首行有生成标记），自定义面板一律不动。"""
        try:
            if panel.type != 'table':
                return
            panel_dir = PANELS_DIR / panel.id
            js_path = panel_dir / 'panel.js'
            if not js_path.is_file() or js_path.stat().st_size == 0:
                return
            first = js_path.read_text(encoding='utf-8').splitlines()[0].strip()
            if first != f"// {panel.id} panel logic":
                return  # 自定义/手工改过的面板，不覆盖
            pj = json.loads((panel_dir / 'panel.json').read_text(encoding='utf-8'))
            features = pj.get('features') or {}
            want_stats = bool(pj.get('with_chart')) or bool(features.get('stats'))
            index_html = self._generate_table_html(panel.id, panel.name, panel.fields,
                                                   panel.icon, want_stats,
                                                   pj.get('with_search', True), features,
                                                   assist_hint=pj.get('assist_hint') or ASSIST_HINTS.get(panel.id, ''))
            # ★ 2026-09-16 护栏（实测实锤事故）：index.html 只许覆盖"生成品"——
            #   生成品必引用 /panel/<id>/panel.js；定制面板（记账2.0/成长档案）引用自己的
            #   js，绝不能被通用模板覆盖回去（22:30 曾把记账定制页覆盖掉）
            idx_path = panel_dir / 'index.html'
            if idx_path.is_file():
                cur_html = idx_path.read_text(encoding='utf-8')
                if ('/panel/%s/panel.js' % panel.id) not in cur_html:
                    # ★ #1447 降噪：每个定制面板每次启动都打一行 INFO 刷屏，降为 DEBUG
                    logger.debug(f'[PanelManager] 面板 {panel.id} index.html 为定制页，跳过再生成')
                    return
            idx_path.write_text(index_html, encoding='utf-8')
            (panel_dir / 'panel.js').write_text(self._generate_panel_js(
                panel.id, panel.fields,
                assist_hint=pj.get('assist_hint') or ASSIST_HINTS.get(panel.id, '')),
                encoding='utf-8')
            # ★ 2026-09-18 顺带重建 panel.css（浅色主题升级；生成品面板的 css 一并刷新）
            (panel_dir / 'panel.css').write_text(self._generate_panel_css(), encoding='utf-8')
        except Exception as e:
            logger.warning(f'[PanelManager] 面板 {panel.id} 再生成跳过: {e}')

    # ── ★ #1037（2026-10-06）对话改模块字段（已批准「开发 全部都做」批④）──
    # 痛点：建完模块想加个字段/改个名，只能删了重建（历史记录全没）。
    # 原则：加字段/改字段名直接做（旧数据保留）；删字段连数据一起没 → 确认门。
    # 护栏：只许改「生成器产出的台账模块」（panel.js 首行生成标记）；定制模块
    #（记账2.0/成长档案/冰箱四Tab）字段被定制 JS/后端写死，改了会炸功能，一律拒绝。

    _CORE_COLS = ('id', 'member', 'created_at', 'updated_at')

    def _is_generated_table(self, panel_id: str) -> bool:
        """与 _sync_generated_panel 同款判断：panel.js 首行 == 生成标记才算生成品"""
        js_path = PANELS_DIR / panel_id / 'panel.js'
        try:
            if not js_path.is_file() or js_path.stat().st_size == 0:
                return False
            return js_path.read_text(encoding='utf-8').splitlines()[0].strip() == f"// {panel_id} panel logic"
        except Exception:
            return False

    def edit_panel_fields(self, panel_id: str, action: str, field_name: str = '',
                          new_name: str = '', ftype: str = 'text', options=None,
                          confirm: bool = False) -> dict:
        """对话改模块字段：list 列出 | add 加 | rename 改名 | delete 删
        加/改名旧数据保留；删除会连历史记录一起删（需 confirm=true）"""
        try:
            from src.models.database import get_connection
            if not self._valid_panel_id(panel_id):
                return {'success': False, 'message': f'非法 panel_id: {panel_id!r}'}
            panel = self._panels.get(panel_id)
            if not panel:
                return {'success': False, 'message': f'模块 {panel_id} 不存在'}
            action = (action or '').strip().lower()
            fields = [dict(f) for f in (panel.fields or [])]

            if action == 'list':
                return {'success': True,
                        'fields': [f.get('name', '') for f in fields],
                        'detail': [{'name': f.get('name', ''), 'type': f.get('type', 'text')} for f in fields]}

            if not self._is_generated_table(panel_id):
                return {'success': False,
                        'message': f'「{panel.name}」是定制模块，字段和功能是配套写死的，不能对话改；'
                                   f'有需要请到电脑端面板管理里手动调整'}
            pj_path = PANELS_DIR / panel_id / 'panel.json'
            pj = json.loads(pj_path.read_text(encoding='utf-8'))

            fname = str(field_name or '').strip()[:12]
            if action == 'add':
                if not fname:
                    return {'success': False, 'message': '要加的字段名叫什么？（如「备注」「单价」）'}
                if any(f.get('name') == fname for f in fields) or fname in self._CORE_COLS:
                    return {'success': False, 'message': f'「{fname}」这个字段已经有了，不用重复加'}
                if len(fields) >= 8:
                    return {'success': False, 'message': '字段已经够多了（上限 8 个），再加表单会太挤'}
                ft = str(ftype or 'text').lower()
                if ft not in ('text', 'number', 'date', 'select', 'image'):  # ★ #1459 image 入白名单（存图快捷键给模块补照片字段用）
                    ft = 'text'
                newf = {'name': fname, 'label': fname, 'type': ft}
                if ft == 'select':
                    opts = [str(o).strip()[:20] for o in (options or []) if str(o).strip()][:8]
                    if not opts:
                        return {'success': False, 'message': '下拉字段要给选项，如 ["已完成","进行中"]'}
                    newf['options'] = opts
                fields.append(newf)
                msg = f'已给「{panel.name}」加上「{fname}」字段（{ft}），旧记录都还在'
            elif action == 'rename':
                new = str(new_name or '').strip()[:12]
                if not fname or not new:
                    return {'success': False, 'message': '改名要说清楚：原字段名 + 新字段名'}
                if fname in self._CORE_COLS:
                    return {'success': False, 'message': f'「{fname}」是系统列，不能改'}
                tgt = next((f for f in fields if f.get('name') == fname), None)
                if not tgt:
                    return {'success': False,
                            'message': f'没有「{fname}」这个字段。现有：' + '、'.join(f.get('name', '') for f in fields)}
                if new == fname:
                    return {'success': False, 'message': '新名字和旧名字一样'}
                if any(f.get('name') == new for f in fields) or new in self._CORE_COLS:
                    return {'success': False, 'message': f'「{new}」已经被别的字段占了，换个名字'}
                tgt['name'] = new
                tgt['label'] = new
                msg = f'已把「{fname}」改名为「{new}」，这一列的历史记录原样保留'
            elif action == 'delete':
                if not fname:
                    return {'success': False, 'message': '要删哪个字段？'}
                if fname in self._CORE_COLS:
                    return {'success': False, 'message': f'「{fname}」是系统列，不能删'}
                if not any(f.get('name') == fname for f in fields):
                    return {'success': False,
                            'message': f'没有「{fname}」这个字段。现有：' + '、'.join(f.get('name', '') for f in fields)}
                if len(fields) <= 1:
                    return {'success': False, 'message': '至少要留 1 个字段，全删了模块就没法记了'}
                if not confirm:
                    return {'success': False, 'confirm_needed': True,
                            'message': f'删「{fname}」这一列，已经记的历史数据也会一起删掉，找不回来。'
                                       f'确定要删吗？确定的话我再动手'}
                fields = [f for f in fields if f.get('name') != fname]
                msg = f'已删掉「{fname}」字段（这列的历史记录一并清了）'
            else:
                return {'success': False, 'message': 'action 只支持 list / add / rename / delete'}

            # ① 落库：sqlite 列操作（加列/改名都保数据；删列数据随行没）
            conn = get_connection(str(DB_PATH))
            table = f'panel_{panel_id}'
            if action == 'add':
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{fname}" '
                             f'{FIELD_TYPE_MAP.get(ft, "TEXT")}')
            elif action == 'rename':
                conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{fname}" TO "{new}"')
            elif action == 'delete':
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
                if fname in cols:
                    conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{fname}"')
            conn.commit()
            conn.close()

            # ② 落盘：panel.json 更新 + 生成品页面再生成（同 create_panel_from_template 尾部）
            pj['fields'] = fields
            pj_path.write_text(json.dumps(pj, ensure_ascii=False, indent=2), encoding='utf-8')
            features = pj.get('features') or {}
            (PANELS_DIR / panel_id / 'index.html').write_text(
                self._generate_table_html(panel_id, pj.get('name', panel.name), fields,
                                          pj.get('icon', panel.icon),
                                          bool(pj.get('with_chart')), bool(pj.get('with_search', True)),
                                          features, assist_hint=pj.get('assist_hint') or ''),
                encoding='utf-8')
            (PANELS_DIR / panel_id / 'panel.js').write_text(
                self._generate_panel_js(panel_id, fields,
                                        assist_hint=pj.get('assist_hint') or ''),
                encoding='utf-8')
            (PANELS_DIR / panel_id / 'panel.css').write_text(self._generate_panel_css(), encoding='utf-8')
            self._scan_panels()
            return {'success': True, 'message': msg,
                    'fields': [f.get('name', '') for f in fields]}
        except Exception as e:
            logger.error(f'[PanelManager] 改字段失败 {panel_id}/{action}: {e}')
            return {'success': False, 'message': f'改字段没成功：{e}'}

    def get_all_panels(self) -> list[dict]:
        """返回所有已安装面板的列表
        ★ 2026-09-18 拖拽排序：按 _order.json 用户自定义顺序排（列表页/空间页/右栏停靠共用一个顺序源），
        没排过序的面板按目录名兜底，行为与旧版一致。"""
        order = self._load_order()
        panels = [p.to_dict() for p in self._panels.values()]
        panels.sort(key=lambda d: (order.get(d['id'], 10 ** 9), d['id']))
        return panels

    def _load_order(self) -> dict:
        """★ 2026-09-18 读面板顺序 {面板id: 序号}（文件损坏/缺失一律静默回退默认序）"""
        try:
            f = PANELS_DIR / '_order.json'
            if f.is_file():
                data = json.loads(f.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    return {str(k): int(v) for k, v in data.items()}
        except Exception as e:
            logger.warning(f'[PanelManager] 读取面板顺序失败（用默认序）: {e}')
        return {}

    def set_panel_order(self, order_list) -> dict:
        """★ 2026-09-18 拖拽排序持久化：order_list = 面板id数组（按新顺序）。
        未出现在数组里的面板排到末尾（按目录名），已卸载的id自动忽略。"""
        if not isinstance(order_list, list):
            return {'success': False, 'message': 'order 必须是面板id数组'}
        ids = [str(x) for x in order_list if x in self._panels]
        unknown = [str(x) for x in order_list if x not in self._panels]
        order = {pid: i for i, pid in enumerate(ids)}
        for p in sorted(self._panels):
            if p not in order:
                order[p] = len(order)
        try:
            (PANELS_DIR / '_order.json').write_text(
                json.dumps(order, ensure_ascii=False, indent=1), encoding='utf-8')
        except Exception as e:
            return {'success': False, 'message': f'保存顺序失败: {e}'}
        return {'success': True, 'message': '顺序已保存', 'skipped': unknown}

    def get_panel(self, panel_id: str) -> Panel | None:
        """获取单个面板"""
        return self._panels.get(panel_id)

    def panel_exists(self, panel_id: str) -> bool:
        return panel_id in self._panels

    # ── ★ #939 C2 Zip Slip 修复（2026-09-30）────────────────────────
    # 实锤：zf.extractall(tmp) 无条目路径校验，ZIP 内含 ../../ 条目可写 tmp 外任意路径。
    @staticmethod
    def _safe_extractall(zf, tmp: Path) -> None:
        """逐条目校验后解压，防 Zip Slip（../ 逃逸 / 绝对路径 / 盘符）。
        任一条目越界即整体拒绝，一个文件都不落盘。"""
        tmp_real = os.path.realpath(str(tmp))
        for name in zf.namelist():
            # 反斜杠统一成正斜杠再判断（Windows 下 ZIP 条目可能带 \）
            norm = name.replace('\\', '/')
            if norm.startswith('/') or (len(norm) >= 2 and norm[1] == ':'):
                raise ValueError(f'ZIP 条目为绝对路径，拒绝解压: {name!r}')
            dest = os.path.realpath(os.path.join(tmp_real, norm))
            if not (dest == tmp_real or dest.startswith(tmp_real + os.sep)):
                raise ValueError(f'ZIP 条目越界（Zip Slip），拒绝解压: {name!r}')
        zf.extractall(tmp)

    def install_panel(self, zip_path: str) -> dict:
        """从ZIP文件安装面板
        返回: {'success': True/False, 'message': str, 'panel': dict|None}
        """
        zip_file = Path(zip_path)
        if not zip_file.is_file():
            return {'success': False, 'message': 'ZIP文件不存在'}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            try:
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    self._safe_extractall(zf, tmp)  # ★ #939 逐条目校验
            except zipfile.BadZipFile:
                return {'success': False, 'message': 'ZIP文件格式错误'}
            except ValueError as e:
                logger.warning(f'[PanelManager] #939 恶意ZIP已拦截: {e}')
                return {'success': False, 'message': f'ZIP 包含危险路径，已拒绝安装: {e}'}

            # 查找 panel.json（支持嵌套一层目录）
            panel_json_path = None
            for p in tmp.rglob('panel.json'):
                panel_json_path = p
                break

            if panel_json_path is None:
                return {'success': False, 'message': 'ZIP中未找到 panel.json'}

            try:
                data = json.loads(panel_json_path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                return {'success': False, 'message': 'panel.json 格式错误'}

            if not self._validate_panel_json(data):
                return {'success': False, 'message': 'panel.json 缺少必填字段（name/id/version/type）'}

            # ★ #1130 引擎兼容校验：模块要求的最低引擎版本高于当前版本 → 拒装并提示升级
            req_engine = str(data.get('required_engine') or '').strip()
            if req_engine:
                from src.version import APP_VERSION
                if self._ver_tuple(req_engine) > self._ver_tuple(APP_VERSION):
                    return {'success': False,
                            'message': f'该模块需要哈文引擎 ≥ {req_engine}，当前版本 {APP_VERSION}，请先升级哈文再安装'}

            panel_id = data['id']
            # ★ #939 panel_id 白名单（与 create_panel_from_template 一致）：
            # 安装路径的 panel_id 来自 ZIP 内 panel.json，原样拼进表名/目录名，必须把关卡补上
            if not self._valid_panel_id(panel_id):
                logger.warning(f'[PanelManager] #939 非法 panel_id 已拦截: {panel_id!r}')
                return {'success': False, 'message': f'panel_id 含非法字符（仅允许字母/数字/中划线/下划线）: {panel_id!r}'}
            target_dir = PANELS_DIR / panel_id

            if target_dir.exists():
                return {'success': False, 'message': f'面板 "{panel_id}" 已安装，请先卸载'}

            # 复制整个面板目录
            source_dir = panel_json_path.parent
            shutil.copytree(str(source_dir), str(target_dir))

            # 重新扫描以注册新面板
            self._scan_panels()

            panel = self._panels.get(panel_id)
            return {
                'success': True,
                'message': f'面板 "{data.get("name", panel_id)}" 安装成功',
                'panel': panel.to_dict() if panel else None,
            }

    # ── ★ #1130（2026-10-14）面板生态四件套：兼容校验 + 导出打包 ──
    # 目标：用户自创模块可导出成标准包 → 传 HarvenHub 分享/出售 → 别人一键安装即用，
    # 免整包更新。定制模块（记账2.0/成长档案/冰箱四Tab）依赖核心代码专属接口，导出无意义 → 拒绝。

    @staticmethod
    def _ver_tuple(v: str) -> tuple:
        """'0.3.1' → (0, 3, 1)；脏值兜底 (0,) 不拦安装"""
        import re as _re
        try:
            nums = tuple(int(x) for x in _re.findall(r'\d+', str(v))[:3])
            return nums if nums else (0,)
        except Exception:
            return (0,)

    def export_panel(self, panel_id: str) -> dict:
        """导出生成器台账面板为标准 zip（panel.json 注入 required_engine 兼容标记，源目录不动）"""
        panel = self._panels.get(panel_id)
        if not panel:
            return {'success': False, 'message': f'面板 "{panel_id}" 不存在'}
        if not self._is_generated_table(panel_id):
            return {'success': False,
                    'message': f'「{panel.name}」是定制模块，页面和接口与核心代码配套写死，不支持导出分享'}
        src_dir = PANELS_DIR / panel_id
        try:
            meta = json.loads((src_dir / 'panel.json').read_text(encoding='utf-8'))
        except Exception as e:
            return {'success': False, 'message': f'panel.json 读取失败: {e}'}
        from src.version import APP_VERSION
        meta['required_engine'] = str(meta.get('required_engine') or APP_VERSION)
        meta['exported_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        exports_dir = PANELS_DIR.parent / 'exports'
        exports_dir.mkdir(parents=True, exist_ok=True)
        zip_name = f"{panel_id}_v{meta.get('version', '1.0.0')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = exports_dir / zip_name
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(src_dir.rglob('*')):
                    if f.is_file() and f.name != 'panel.json':  # panel.json 用注入元数据的副本
                        zf.write(f, f.relative_to(src_dir))
                zf.writestr('panel.json', json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception as e:
            return {'success': False, 'message': f'打包失败: {e}'}
        logger.info(f'[PanelManager] #1130 导出面板 {panel_id} → {zip_path.name}')
        return {'success': True, 'zip_path': str(zip_path), 'name': zip_name,
                'required_engine': meta['required_engine']}

    def uninstall_panel(self, panel_id: str, password: str = '') -> dict:
        """卸载面板。★ 2026-09-16 权限分级：锁定(locked)级面板必须验证管理员密码，防误删防乱删"""
        if panel_id not in self._panels:
            return {'success': False, 'message': f'面板 "{panel_id}" 不存在'}

        if self.get_protection(panel_id) == 'locked':
            if not password or password != self._get_admin_password():
                return {'success': False, 'message': '🔒 该面板已锁定保护，需要管理员密码才能卸载'}

        panel_dir = PANELS_DIR / panel_id
        if panel_dir.exists():
            shutil.rmtree(str(panel_dir))

        # 删除数据表
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            conn.execute(f'DROP TABLE IF EXISTS panel_{panel_id}')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f'[PanelManager] 删除数据表失败 {panel_id}: {e}')

        # ★ #909 面板卸载 → 附件图一并清（登记行+落盘文件），不留孤儿图占盘
        try:
            from src.core import panel_images as _pimg
            for im in _pimg.list_images(panel_id, limit=10000):
                _pimg.delete_image(im['filename'])
        except Exception as e:
            logger.debug(f'[PanelManager] 面板附件图清理跳过: {e}')

        del self._panels[panel_id]
        self._protection.pop(panel_id, None)
        self._save_protection()
        return {'success': True, 'message': f'面板已卸载'}

    # ════════ ★ 2026-09-16 面板管理：保护级别 + 记录统计 ════════

    _PROTECTION_FILE = PANELS_DIR / '_protection.json'
    _PROTECTION_LEVELS = ('normal', 'protected', 'locked')

    def _get_admin_password(self) -> str:
        """管理员密码（★ 2026-09-17 收口：统一走 security_guard，与保险箱/全权模式同源）"""
        try:
            from src.core.security_guard import get_admin_password
            return get_admin_password()
        except Exception:
            return 'harven2026'

    def _load_protection(self) -> dict:
        try:
            if self._PROTECTION_FILE.exists():
                return json.loads(self._PROTECTION_FILE.read_text(encoding='utf-8'))
        except Exception as e:
            logger.warning(f'[PanelManager] 读取保护级别失败: {e}')
        return {}

    def _save_protection(self):
        try:
            self._PROTECTION_FILE.write_text(
                json.dumps(self._protection, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            logger.error(f'[PanelManager] 保存保护级别失败: {e}')

    def get_protection(self, panel_id: str) -> str:
        """面板保护级别：normal 普通 / protected 受保护 / locked 锁定（卸载需管理员密码）"""
        if not hasattr(self, '_protection'):
            self._protection = self._load_protection()
        return self._protection.get(panel_id, 'normal')

    def set_protection(self, panel_id: str, level: str) -> dict:
        if panel_id not in self._panels:
            return {'success': False, 'message': f'面板 "{panel_id}" 不存在'}
        if level not in self._PROTECTION_LEVELS:
            return {'success': False, 'message': f'无效的保护级别: {level}'}
        if not hasattr(self, '_protection'):
            self._protection = self._load_protection()
        if level == 'normal':
            self._protection.pop(panel_id, None)  # 默认档不落盘
        else:
            self._protection[panel_id] = level
        self._save_protection()
        return {'success': True, 'level': level}

    def get_record_counts(self) -> dict:
        """每个面板的数据条数（管理页展示 + 卸载前提醒）"""
        counts = {}
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            for pid in self._panels:
                try:
                    row = conn.execute(f'SELECT COUNT(*) FROM panel_{pid}').fetchone()
                    counts[pid] = row[0] if row else 0
                except Exception:
                    counts[pid] = 0
            conn.close()
        except Exception as e:
            logger.error(f'[PanelManager] 统计记录数失败: {e}')
        return counts

    def create_panel_from_template(self, name: str, panel_id: str, fields: list,
                                    description: str = '', icon: str = 'file',
                                    with_chart: bool = False, with_search: bool = True,
                                    features: dict = None, ai_role: str = '',
                                    universal: bool = False) -> dict:
        """根据模板创建面板（用户填表定义字段后生成完整面板文件）"""
        if self.panel_exists(panel_id):
            return {'success': False, 'message': f'面板ID "{panel_id}" 已存在'}

        if not panel_id or not panel_id.replace('-', '').replace('_', '').isalnum():
            return {'success': False, 'message': '面板ID只能包含字母、数字、-、_'}

        if not fields:
            return {'success': False, 'message': '至少需要定义一个字段'}

        panel_dir = PANELS_DIR / panel_id
        panel_dir.mkdir(parents=True, exist_ok=True)

        # 1. 生成 panel.json
        panel_data = {
            'name': name,
            'id': panel_id,
            'version': '1.0.0',
            'type': 'table',
            'icon': icon,
            'description': description,
            'author': 'user',
            'fields': fields,
            'with_chart': with_chart,
            'with_search': with_search,
            'features': features or {},  # ★ 2026-09-16 能力配置（remind/stats/ai）
            'ai_role': ai_role or '',  # ★ 2026-09-19 AI 角色卡（panel_ai 按角色分析）
            'universal': bool(universal),  # ★ 2026-09-22 (#843) 通用模块：所有分类空间页可见
        }
        (panel_dir / 'panel.json').write_text(
            json.dumps(panel_data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

        # 2. 生成 index.html
        index_html = self._generate_table_html(panel_id, name, fields, icon, with_chart,
                                               with_search, features or {},
                                               assist_hint=ASSIST_HINTS.get(panel_id, ''))
        (panel_dir / 'index.html').write_text(index_html, encoding='utf-8')

        # 3. 生成 panel.css
        panel_css = self._generate_panel_css()
        (panel_dir / 'panel.css').write_text(panel_css, encoding='utf-8')

        # 4. 生成 panel.js
        panel_js = self._generate_panel_js(panel_id, fields,
                                           assist_hint=ASSIST_HINTS.get(panel_id, ''))
        (panel_dir / 'panel.js').write_text(panel_js, encoding='utf-8')

        # 重新扫描
        self._scan_panels()

        panel = self._panels.get(panel_id)
        return {
            'success': True,
            'message': f'面板 "{name}" 创建成功',
            'panel': panel.to_dict() if panel else None,
        }

    def _meal_extras_html(self, panel_id: str) -> dict:
        """★ 2026-10-10 #1062 餐饮面板专属增强（仅 meal_diary，随生成器重启自愈）。
        - top:  C1 冰箱×外卖提醒横幅（打开页面拉 /api/assist/meal-alerts）+ C3「今天吃什么」按钮
        - chart: 专属统计（外卖次数/金额/常点店 TOP/吃辣/热量参考，/api/assist/meal-stats）
        - foot: 热量仅供参考注脚"""
        top = '''
  <!-- ★ 2026-10-10 #1062 C1 横幅：冰箱临期 × 外卖提醒（确定性，打开页面现算） -->
  <div id="mealAlertBar" style="display:none;margin-bottom:12px"></div>
<script>
(async function loadMealAlerts(){
  try{
    var r=await fetch('/api/assist/meal-alerts').then(r=>r.json());
    var a=(r.success&&r.alerts)||[];
    if(!a.length) return;
    var bar=document.getElementById('mealAlertBar');
    bar.style.display='block';
    bar.innerHTML=a.map(function(t){
      return '<div style="border-radius:8px;padding:10px 12px;margin-bottom:6px;font-size:12px;line-height:1.6;' +
        'background:rgba(210,153,34,.08);border:1px solid #d29922;color:#8a6d1a">' + t + '</div>';
    }).join('');
  }catch(e){}
})();
</script>
  <!-- ★ 2026-10-10 #1062 C3 今天吃什么（口味历史+冰箱库存+季节，跟随模型阵营） -->
  <div style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <button class="panel-btn panel-btn-primary" onclick="runMealSuggest(this)">🍜 今天吃什么</button>
    <span style="font-size:11px;color:#9AA3B0">看冰箱库存、口味历史和当季，给今晚 2~3 个方案</span>
  </div>
  <div id="mealSuggestBox" style="display:none;background:rgba(34,160,107,.06);border:1px solid #E5EAF2;border-radius:10px;padding:12px;margin-bottom:16px;white-space:pre-wrap;font-size:12px;line-height:1.8;color:#3A4453"></div>
<script>
async function runMealSuggest(btn){
  var box=document.getElementById('mealSuggestBox');
  box.style.display='block'; box.textContent='⏳ 想菜单中，几秒钟就好…';
  var old=btn.textContent; btn.disabled=true;
  try{
    var r=await fetch('/api/assist/meal-suggest',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json());
    if(!r.success){ box.textContent='⚠️ '+(r.error||'没想出来，再点一次试试'); }
    else{
      var m=r.model?('（'+r.model+'）'):'';
      box.textContent=r.text+'\\n\\n'+m;
    }
  }catch(e){ box.textContent='⚠️ 网络出了点问题：'+e.message; }
  btn.textContent=old; btn.disabled=false;
}
</script>'''

        chart = '''
  <button class="panel-btn" onclick="loadMealStats()" style="margin-bottom:12px">📊 显示统计</button>
  <div id="mealStatsBox" style="display:none;margin-bottom:16px"></div>
<script>
async function loadMealStats(){
  var box=document.getElementById('mealStatsBox');
  box.style.display='block'; box.innerHTML='<div style="font-size:12px;color:#9AA3B0">⏳ 统计中…</div>';
  try{
    var r=await fetch('/api/assist/meal-stats?days=30').then(r=>r.json());
    if(!r.success){ box.innerHTML='<div style="font-size:12px;color:#c0392b">统计没算出来：'+(r.error||'')+'</div>'; return; }
    var s=r.stats||{}; var tk=s.takeout||{}; var sp=s.spicy||{}; var cal=s.calories||{};
    function card(label,val,color){
      return '<div style="background:#fff;border:1px solid #E5EAF2;border-radius:10px;padding:12px;text-align:center">' +
        '<div style="font-size:11px;color:#9AA3B0;margin-bottom:4px">'+label+'</div>' +
        '<div style="font-size:20px;font-weight:bold;color:'+color+'">'+val+'</div></div>';
    }
    var html='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px">' +
      card('近30天记录', (s.records||0), '#5B87D8') +
      card('外卖次数', (tk.count||0)+' 次', '#E05B5B') +
      card('外卖花销', '¥'+(tk.amount||0).toFixed(1), '#E05B5B') +
      card('吃辣次数', (sp.count||0)+' 次', '#d29922') +
      '</div>';
    var shops=tk.shops||[];
    if(shops.length){
      var maxC=shops[0].count||1;
      var colors=['#f85149','#d29922','#3fb950','#58a6ff','#bc8cff'];
      html+='<div style="background:#fff;border:1px solid #E5EAF2;border-radius:10px;padding:12px;margin-bottom:12px">' +
        '<h3 style="font-size:13px;color:#5B87D8;margin-bottom:8px">🛵 常点店 TOP（近30天）</h3>';
      shops.slice(0,5).forEach(function(x,i){
        var pct=Math.max((x.count/maxC*100).toFixed(0),6);
        html+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:11px">' +
          '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+x.shop+'</span>' +
          '<div style="width:40%;height:14px;background:#EEF2F8;border-radius:3px;overflow:hidden">' +
          '<div style="height:100%;width:'+pct+'%;background:'+colors[i%5]+';border-radius:3px"></div></div>' +
          '<span style="width:110px;text-align:right;color:#9AA3B0">'+x.count+'次 ¥'+x.amount.toFixed(1)+'</span></div>';
      });
      html+='</div>';
    }
    var words=(sp.words||[]).map(function(w){return w.word+'×'+w.count;}).join('、');
    html+='<div style="background:#fff;border:1px solid #E5EAF2;border-radius:10px;padding:12px;font-size:12px;line-height:1.8;color:#3A4453">' +
      '🌶️ 爱吃的辣味：'+(words||'最近没吃辣') +
      '<br>🔥 热量参考：表内估算合计 '+(cal.diary_sum||0)+' 千卡' +
      ((cal.estimated||0)>0 ? ('，另有 '+cal.estimated+' 条没填的按家常份量词典粗估 '+cal.dict_est+' 千卡') : '') +
      '<br><span style="color:#9AA3B0;font-size:11px">※ 热量为词典粗估，仅供参考，不是精确营养数据</span></div>';
    box.innerHTML=html;
  }catch(e){ box.innerHTML='<div style="font-size:12px;color:#c0392b">统计加载失败：'+e.message+'</div>'; }
}
</script>'''

        foot = ('<div style="font-size:11px;color:#9AA3B0;margin-top:8px">'
                '* 估算热量为本地词典按家常份量粗估，仅供参考，不是精确营养数据</div>')
        return {'top': top, 'chart': chart, 'foot': foot}

    def _cross_banner_html(self, panel_id: str) -> str:
        """★ 2026-10-11 #1105 关联动态横幅（随生成器重启自愈）。
        打开面板时拉 /api/panels/<id>/cross_context（#1104 link_rules 纯 SQL 取数，零 token），
        有配角近况才显示 chip 条；chip 点击跳对方面板（iframe 内走 SPA 导航事件）。
        无联动规则的面板接口返回空 banners，横幅保持隐藏——通用模板一处生成、全面板安全。"""
        return f'''
  <!-- ★ 2026-10-11 #1105 关联动态横幅（联动规则表驱动，纯 SQL 零 token） -->
  <div id="crossBanner" class="cross-banner" style="display:none"></div>
<script>
(async function loadCrossBanner(){{
  try{{
    var r=await fetch('/api/panels/{panel_id}/cross_context').then(function(x){{return x.json()}});
    var bs=(r&&r.banners)||[];
    if(!bs.length) return;
    var bar=document.getElementById('crossBanner');
    var esc=function(s){{return String(s||'').replace(/</g,'&lt;');}};
    bar.innerHTML='<div class="cross-banner-title">🔗 关联动态</div>'
      +'<div class="cross-banner-chips">'+bs.map(function(b){{
        return '<span class="cross-chip" data-target="'+esc(b.target)+'" title="点击打开'+esc(b.name)+'">'+b.icon+' '+esc(b.text)+'</span>';
      }}).join('')+'</div>';
    bar.style.display='block';
    bar.querySelectorAll('.cross-chip').forEach(function(ch){{
      ch.addEventListener('click',function(){{
        var t=ch.getAttribute('data-target'); if(!t) return;
        try{{ window.parent.dispatchEvent(new CustomEvent('harven:navigate',{{detail:{{path:'/panel/'+t}}}})); }}
        catch(e){{ try{{ window.parent.location.href='/spa/index.html#/panel/'+t; }}catch(e2){{}} }}
      }});
    }});
  }}catch(e){{ /* 横幅属锦上添花，任何异常静默 */ }}
}})();
</script>'''

    def _home_extras_html(self, panel_id: str) -> dict:
        """★ 2026-10-11 #1106 四健康面板主场差异化（开发者定稿模板 v1 实装，随生成器重启自愈）。
        打开谁谁是主角——每家一块专属「主场」开场区块：
        - health_file  成员画像卡（人手一张：最近事件/异常数/下次安排，异常标红）
        - med_cabinet  效期倒计时进度条（红<7天/黄<30天/绿）+ 服用安排时间轴
        - fitness_log  KPI 三卡（本周运动/近30天消耗/最近体重）+ 体重趋势折线
        - child_growth 生长曲线大图（身高+体重双线）+ 定家引用卡（疫苗/体检去健康档案）
        数据全部现拉 /api/panels/<id>/data（确定性本地渲染，零 token）；空数据整块隐藏。
        ★ 诚实适配（与 v1 模板差异）：步数字段不存在→KPI 换近30天消耗；WHO 标准带需要
        出生日期算年龄而库里没有→画身高体重双趋势线，不伪造参考带。"""
        if panel_id not in ('health_file', 'med_cabinet', 'fitness_log', 'child_growth'):
            return {}
        return {'top': self._home_block_html(panel_id)}

    def _home_block_html(self, panel_id: str) -> str:
        if panel_id == 'health_file':
            return '''
  <!-- ★ #1106 健康档案主场：成员画像卡 -->
  <div id="hfHomeCards" style="display:none;margin-bottom:14px">
    <div style="font-size:13px;font-weight:600;color:#3A4453;margin-bottom:8px">👪 全家健康画像</div>
    <div id="hfHomeRow" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px"></div>
  </div>
<script>
(async function loadHfHome(){
  try{
    var r=await fetch('/api/panels/health_file/data?limit=300').then(function(x){return x.json()});
    var rows=(r&&r.records)||[]; if(!rows.length) return;
    var byM={};
    rows.forEach(function(x){ var m=(x['成员']||'未标注').trim()||'未标注'; (byM[m]=byM[m]||[]).push(x); });
    var today=new Date().toISOString().slice(0,10);
    var esc=function(s){return String(s||'').replace(/</g,'&lt;');};
    var html='';
    Object.keys(byM).sort().forEach(function(m){
      var list=byM[m];
      var abn=list.filter(function(x){return String(x['异常提示']||'').trim();});
      var last=list.slice().sort(function(a,b){return String(b['日期']||'').localeCompare(String(a['日期']||''));})[0];
      var next=null;
      list.forEach(function(x){ var d=x['下次日期']; if(d&&String(d)>=today&&(!next||String(d)<next)) next=String(d); });
      var ev=last?(''+(last['类型']||'')+(last['项目']?(' · '+last['项目']):'')):'';
      html+='<div style="background:#fff;border:1px solid '+(abn.length?'#f0b4b4':'#E5EAF2')+';border-radius:12px;padding:10px 12px">'
        +'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
        +'<span style="font-weight:600;font-size:13px">'+esc(m)+'</span>'
        +(abn.length?'<span style="font-size:11px;color:#E05B5B">⚠ '+abn.length+' 项异常</span>':'<span style="font-size:11px;color:#4a9d5b">✓ 无异常</span>')+'</div>'
        +(ev?'<div style="font-size:12px;color:#3A4453">最近：'+esc(ev)+'</div>':'')
        +(next?'<div style="font-size:12px;color:#8a6d1a">下次：'+esc(next)+'</div>':'')
        +'</div>';
    });
    if(html){ document.getElementById('hfHomeRow').innerHTML=html; document.getElementById('hfHomeCards').style.display='block'; }
  }catch(e){}
})();
</script>'''
        if panel_id == 'med_cabinet':
            return '''
  <!-- ★ #1106 家庭药箱主场：效期倒计时条 + 服用安排 -->
  <div id="mcHome" style="display:none;margin-bottom:14px">
    <div style="font-size:13px;font-weight:600;color:#3A4453;margin-bottom:8px">💊 效期倒计时</div>
    <div id="mcExpRow" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;margin-bottom:10px"></div>
    <div id="mcUseWrap" style="display:none">
      <div style="font-size:13px;font-weight:600;color:#3A4453;margin-bottom:8px">⏰ 服用安排</div>
      <div id="mcUseList" style="background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:4px 14px"></div>
    </div>
  </div>
<script>
(async function loadMcHome(){
  try{
    var r=await fetch('/api/panels/med_cabinet/data?limit=300').then(function(x){return x.json()});
    var rows=(r&&r.records)||[]; if(!rows.length) return;
    var now=Date.now(), DAY=86400000, esc=function(s){return String(s||'').replace(/</g,'&lt;');};
    var exps=[], uses=[];
    rows.forEach(function(x){
      var d=String(x['有效期']||'').trim();
      if(d){ var dl=Math.round((new Date(d.replace(/\\//g,'-'))-now)/DAY);
        if(!isNaN(dl)) exps.push({n:x['药品名称'],dl:dl,d:d,st:x['库存']}); }
      var how=String(x['服用方法']||'').trim();
      if(how) uses.push({n:x['药品名称'],who:x['服用成员'],how:how});
    });
    exps.sort(function(a,b){return a.dl-b.dl;});
    var html='';
    exps.slice(0,8).forEach(function(e){
      var c,tag;
      if(e.dl<0){c='#9AA3B0';tag='已过期'+(-e.dl)+'天';}
      else if(e.dl<=7){c='#E05B5B';tag='仅剩'+e.dl+'天';}
      else if(e.dl<=30){c='#d29922';tag='剩'+e.dl+'天';}
      else {c='#4a9d5b';tag='剩'+e.dl+'天';}
      var pct=Math.max(4,Math.min(100,Math.round(e.dl/90*100)));
      html+='<div style="background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:9px 12px">'
        +'<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px">'
        +'<span style="font-weight:600">'+esc(e.n)+'</span><span style="color:'+c+'">'+tag+'</span></div>'
        +'<div style="height:6px;background:#F1F3F7;border-radius:3px;overflow:hidden">'
        +'<div style="height:100%;width:'+pct+'%;background:'+c+';border-radius:3px"></div></div>'
        +(e.st?'<div style="font-size:11px;color:#9AA3B0;margin-top:4px">库存 '+esc(e.st)+'</div>':'')
        +'</div>';
    });
    if(html){ document.getElementById('mcExpRow').innerHTML=html; document.getElementById('mcHome').style.display='block'; }
    if(uses.length){
      var uh='';
      uses.slice(0,8).forEach(function(u){
        uh+='<div style="display:flex;gap:8px;padding:7px 0;border-bottom:1px dashed #F1F3F7;font-size:12px;align-items:baseline">'
          +'<span style="color:#5B87D8">●</span><b style="min-width:70px">'+esc(u.n)+'</b>'
          +'<span style="color:#9AA3B0;min-width:56px">'+esc(u.who||'全家')+'</span>'
          +'<span style="flex:1;color:#3A4453">'+esc(u.how)+'</span></div>';
      });
      document.getElementById('mcUseList').innerHTML=uh;
      document.getElementById('mcUseWrap').style.display='block';
      document.getElementById('mcHome').style.display='block';
    }
  }catch(e){}
})();
</script>'''
        if panel_id == 'fitness_log':
            return '''
  <!-- ★ #1106 健身主场：KPI 三卡 + 体重趋势 -->
  <div id="ftHome" style="display:none;margin-bottom:14px">
    <div id="ftKpis" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px"></div>
    <div id="ftTrendWrap" style="background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:12px 14px;display:none">
      <div style="font-size:13px;font-weight:600;color:#3A4453;margin-bottom:6px">⚖️ 体重趋势</div>
      <div id="ftTrend"></div>
    </div>
  </div>
<script>
(async function loadFtHome(){
  try{
    var r=await fetch('/api/panels/fitness_log/data?limit=300').then(function(x){return x.json()});
    var rows=(r&&r.records)||[]; if(!rows.length) return;
    var esc=function(s){return String(s||'').replace(/</g,'&lt;');};
    var now=new Date();
    var monday=new Date(now); monday.setDate(now.getDate()-((now.getDay()+6)%7)); monday.setHours(0,0,0,0);
    var d30=new Date(now.getTime()-30*86400000);
    var pd=function(s){ return new Date(String(s||'').replace(/\\//g,'-')+'T00:00:00'); };
    var week=0, cal30=0;
    var weights=[];
    rows.forEach(function(x){
      var d=pd(x['日期']); if(isNaN(d)) return;
      if(d>=monday) week++;
      if(d>=d30){ var c=parseFloat(x['消耗千卡']); if(!isNaN(c)) cal30+=c; }
      var w=parseFloat(x['体重kg']); if(!isNaN(w)&&w>0) weights.push({d:d,w:w});
    });
    var lastW=weights.length?weights[weights.length-1].w:null;
    function kpi(tip,val,unit,color){
      return '<div style="background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:10px 12px;text-align:center">'
        +'<div style="font-size:11px;color:#9AA3B0;margin-bottom:3px">'+tip+'</div>'
        +'<div style="font-size:20px;font-weight:700;color:'+color+'">'+val+'<span style="font-size:11px;font-weight:400;color:#9AA3B0"> '+unit+'</span></div></div>';
    }
    document.getElementById('ftKpis').innerHTML=
      kpi('本周运动',week,'次','#5B87D8')+kpi('近30天消耗',Math.round(cal30),'千卡','#E08B3A')
      +kpi('最近体重',lastW!==null?lastW:'--','kg','#4a9d5b');
    document.getElementById('ftHome').style.display='block';
    if(weights.length>=2){
      weights.sort(function(a,b){return a.d-b.d;});
      var W=560,H=120,P=28;
      var dmin=weights[0].d, dmax=weights[weights.length-1].d;
      var span=Math.max(1,dmax-dmin);
      var ws=weights.map(function(p){return p.w;});
      var wmin=Math.min.apply(null,ws), wmax=Math.max.apply(null,ws);
      var wrng=Math.max(0.5,wmax-wmin);
      var pts=weights.map(function(p,i){
        var x=P+(W-2*P)*(span?(p.d-dmin)/span:(i/(weights.length-1)));
        var y=H-P-(H-2*P)*(p.w-wmin)/wrng;
        return [Math.round(x*10)/10,Math.round(y*10)/10];
      });
      var poly=pts.map(function(p){return p.join(',');}).join(' ');
      var svg='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto">'
        +'<polyline points="'+poly+'" fill="none" stroke="#5B87D8" stroke-width="2"/>'
        +pts.map(function(p){return '<circle cx="'+p[0]+'" cy="'+p[1]+'" r="3" fill="#5B87D8"/>';}).join('')
        +'<text x="'+pts[0][0]+'" y="'+(H-6)+'" font-size="10" fill="#9AA3B0">'+esc(weights[0].d.toISOString().slice(0,10))+'</text>'
        +'<text x="'+(pts[pts.length-1][0]-60)+'" y="'+(H-6)+'" font-size="10" fill="#9AA3B0">'+esc(weights[weights.length-1].d.toISOString().slice(0,10))+'</text>'
        +'<text x="4" y="14" font-size="10" fill="#9AA3B0">'+wmax+'kg</text>'
        +'<text x="4" y="'+(H-P+4)+'" font-size="10" fill="#9AA3B0">'+wmin+'kg</text></svg>';
      document.getElementById('ftTrend').innerHTML=svg;
      document.getElementById('ftTrendWrap').style.display='block';
    }
  }catch(e){}
})();
</script>'''
        # child_growth
        return '''
  <!-- ★ #1106 孩子成长主场：生长曲线 + 定家引用卡 -->
  <div id="cgHome" style="display:none;margin-bottom:14px">
    <div id="cgChartWrap" style="background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:12px 14px;margin-bottom:10px;display:none">
      <div style="font-size:13px;font-weight:600;color:#3A4453;margin-bottom:6px">📏 生长曲线
        <span style="font-weight:400;font-size:11px;color:#9AA3B0">（身高 cm · 体重 kg 双线）</span></div>
      <div id="cgChart"></div>
      <div style="font-size:11px;color:#9AA3B0;margin-top:4px">💙 身高（左轴） · 🧡 体重（右轴） · 按记录日期连线</div>
    </div>
    <div id="cgRef" style="display:none;background:rgba(91,135,216,.06);border:1px dashed #C6D6F5;border-radius:12px;padding:10px 14px;font-size:12px;color:#3A4453">
      📌 <span id="cgRefText">疫苗和体检记录统一记在「健康档案」里</span> —— 那边是全家医疗事件的总台账
      <button class="panel-btn" style="font-size:11px;padding:2px 10px;margin-left:8px" id="cgRefBtn">去健康档案看看 →</button>
    </div>
  </div>
<script>
(function bindCgRef(){
  var b=document.getElementById('cgRefBtn'); if(!b) return;
  b.addEventListener('click',function(){
    try{ window.parent.dispatchEvent(new CustomEvent('harven:navigate',{detail:{path:'/panel/health_file'}})); }
    catch(e){ try{ window.parent.location.href='/spa/index.html#/panel/health_file'; }catch(e2){} }
  });
})();
(async function loadCgHome(){
  try{
    var r=await fetch('/api/panels/child_growth/data?limit=300').then(function(x){return x.json()});
    var rows=(r&&r.records)||[]; if(!rows.length) return;
    var esc=function(s){return String(s||'').replace(/</g,'&lt;');};
    var pts=[];
    rows.forEach(function(x){
      var d=String(x['日期']||'').trim(); if(!d) return;
      var h=parseFloat(x['身高cm']), w=parseFloat(x['体重kg']);
      pts.push({d:d,h:isNaN(h)?null:h,w:isNaN(w)?null:w});
    });
    pts.sort(function(a,b){return a.d<b.d?-1:1;});
    var hs=pts.filter(function(p){return p.h!==null;}), ws=pts.filter(function(p){return p.w!==null;});
    if(hs.length+ws.length>=2){
      var W=560,H=150,P=30;
      function line(arr,key,color,rightMax){
        if(arr.length<2) return '';
        var t0=pts[0].d, t1=pts[pts.length-1].d, span=Math.max(1,Date.parse(t1)-Date.parse(t0));
        var vs=arr.map(function(p){return p[key];});
        var vmin=Math.min.apply(null,vs), vmax=Math.max.apply(null,vs), vrng=Math.max(rightMax||0.5,vmax-vmin);
        var poly=arr.map(function(p){
          var x=P+(W-2*P)*(Date.parse(p.d)-Date.parse(t0))/span;
          var y=H-P-(H-2*P)*(p[key]-vmin)/vrng;
          return Math.round(x*10)/10+','+Math.round(y*10)/10;
        }).join(' ');
        return '<polyline points="'+poly+'" fill="none" stroke="'+color+'" stroke-width="2"/>'
          +arr.map(function(p){
            var x=P+(W-2*P)*(Date.parse(p.d)-Date.parse(t0))/span;
            var y=H-P-(H-2*P)*(p[key]-vmin)/vrng;
            return '<circle cx="'+Math.round(x*10)/10+'" cy="'+Math.round(y*10)/10+'" r="3" fill="'+color+'"/>';
          }).join('');
      }
      var svg='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto">'
        +line(hs,'h','#5B87D8',10)+line(ws,'w','#E08B3A',1.5)
        +'<text x="'+P+'" y="'+(H-8)+'" font-size="10" fill="#9AA3B0">'+esc(pts[0].d)+'</text>'
        +'<text x="'+(W-P-70)+'" y="'+(H-8)+'" font-size="10" fill="#9AA3B0">'+esc(pts[pts.length-1].d)+'</text></svg>';
      document.getElementById('cgChart').innerHTML=svg;
      document.getElementById('cgChartWrap').style.display='block';
      document.getElementById('cgHome').style.display='block';
    }
    var hr=await fetch('/api/panels/health_file/data?limit=300').then(function(x){return x.json()});
    var hrows=((hr&&hr.records)||[]).filter(function(x){
      var m=String(x['成员']||''); return /孩|娃|子|女/.test(m);
    });
    if(hrows.length){
      var t=document.getElementById('cgRefText');
      if(t) t.innerHTML='健康档案里有 <b>'+hrows.length+'</b> 条孩子的医疗记录（疫苗/体检/就医）';
    }
    document.getElementById('cgRef').style.display='block';
    document.getElementById('cgHome').style.display='block';
  }catch(e){}
})();
</script>'''

    def _health_extras_html(self, panel_id: str) -> dict:
        """★ 2026-10-11 #1072 健康中枢面板专属增强（随生成器重启自愈）。
        - health_file: 顶部「⏰ 健康待办」+「🆘 全家紧急卡」（血型/过敏/紧急联系人，现场拉取）
        - med_cabinet: 顶部「💊 用药与效期提醒」
        其它健康面板不注入。数据源 /api/panels/health/*（确定性本地比对，零 token）。"""
        if panel_id not in ('health_file', 'med_cabinet'):
            return {}
        title = '💊 用药与效期提醒' if panel_id == 'med_cabinet' else '⏰ 健康待办'
        top = f'''
  <!-- ★ 2026-10-11 #1072 健康横幅：待办 + 紧急卡（打开页面现算，本地比对） -->
  <div id="healthAlertBar" style="display:none;margin-bottom:12px"></div>
  <details id="healthEmergencyCard" style="display:none;margin-bottom:12px;border:1px solid #E5EAF2;border-radius:10px;padding:10px 12px;background:rgba(248,81,73,.04)">
    <summary style="font-size:13px;font-weight:600;cursor:pointer">🆘 全家紧急卡（血型 / 过敏 / 紧急联系人）</summary>
    <div id="healthEmergencyBody" style="font-size:12px;line-height:1.9;color:#3A4453;margin-top:8px"></div>
  </details>
<script>
(async function loadHealthAlerts(){{
  try{{
    var r=await fetch('/api/panels/health/overview').then(function(x){{return x.json()}});
    if(!r.success) return;
    var bar=document.getElementById('healthAlertBar');
    var esc=function(s){{return String(s||'').replace(/</g,'&lt;');}};
    /* 待办/提醒（med_cabinet 页只看药相关的：💊⏰ 图标） */
    var icons=( '{panel_id}'==='med_cabinet' ? ['💊','⏰'] : null );
    var rem=(r.reminders||[]).filter(function(t){{return !icons || icons.indexOf(t.icon)>=0;}});
    var html='';
    if(rem.length){{
      html+='<div style="border-radius:10px;padding:10px 12px;margin-bottom:8px;background:rgba(210,153,34,.08);border:1px solid #d29922">'
          +'<div style="font-size:12px;font-weight:600;color:#8a6d1a;margin-bottom:4px">{title}</div>'
          + rem.map(function(t){{return '<div style="font-size:12px;color:#8a6d1a;line-height:1.8">'+t.icon+' '+esc(t.text)+'</div>';}}).join('')
          +'</div>';
    }}
    if(html){{
      bar.style.display='block'; bar.innerHTML=html;
    }}
    /* 紧急卡（仅 health_file 页展示） */
    if('{panel_id}'==='health_file' && (r.members||[]).length){{
      var lines=r.members.map(function(m){{
        var seg='<b>'+esc(m.name)+'</b>';
        if(m.blood_type) seg+='｜血型 '+esc(m.blood_type);
        if(m.allergies) seg+='｜⚠️过敏 '+esc(m.allergies);
        if(m.conditions) seg+='｜慢病 '+esc(m.conditions);
        if(m.emergency_contact) seg+='｜📞 '+esc(m.emergency_contact);
        return '<div>'+seg+'</div>';
      }}).join('');
      document.getElementById('healthEmergencyCard').style.display='block';
      document.getElementById('healthEmergencyBody').innerHTML=lines
        + '<div style="color:#9AA3B0;font-size:11px;margin-top:4px">在「健康档案」里点助手，说「给爸爸补上血型和紧急联系人」就能完善</div>';
    }}
  }}catch(e){{}}
}})();
</script>'''
        return {'top': top}


    def _generate_table_html(self, panel_id: str, name: str, fields: list,
                             icon: str = 'file', with_chart: bool = False, with_search: bool = True,
                             features: dict = None, assist_hint: str = '') -> str:
        """根据字段定义生成表格面板HTML（支持统计图表+搜索+提醒条+AI分析按钮）
        ★ 2026-09-18 生成器四项升级：表单分组折叠 / 卡片视图默认 / 浅色主题跟随SPA / 助手提示词按面板定制"""
        features = features or {}

        # ★ 2026-10-05 #1010 冰箱深化骨架并入生成器（根治 _sync_generated_panel 重启覆盖）：
        # 仅 fridge_stock_1 注入四 Tab（保质期看板/库存/食谱/买菜+拍照识菜），
        # 其它面板 fridge 为空 dict，三处插值为空串，生成结果与旧版完全一致
        fridge = self._fridge_tabs_html() if panel_id == 'fridge_stock_1' else {}

        # ★ 2026-10-10 #1058 物品存放位置档案骨架（home_items/item_storage 前缀面板）：
        # 四 Tab（台账+首次引导/位置档案/换季挪仓/照片归档），同样随生成器重启自愈
        storage = self._storage_tabs_html(panel_id) if panel_id.startswith(('home_items', 'item_storage')) else {}

        # ★ 2026-10-10 #1062 餐饮面板专属增强（C1 横幅 / C3 今天吃什么 / 专属统计 / 热量仅供参考）：
        # 随生成器重启自愈（_sync_generated_panel 再生成自带）
        meal = self._meal_extras_html(panel_id) if panel_id == 'meal_diary' else {}

        # ★ 2026-10-11 #1072 健康中枢面板增强（健康待办横幅 + 全家紧急卡）：
        # 同样随生成器重启自愈
        health = self._health_extras_html(panel_id)

        # ★ 2026-10-11 #1105 关联动态横幅（#1104 联动规则表驱动）：
        # 主角面板顶部一条「🔗 关联动态」，配角近况 chip 点击直达对方页面；
        # 无联动规则/无数据的面板自动隐藏（fetch 空列表 return）
        cross_html = self._cross_banner_html(panel_id)

        # ★ 2026-10-11 #1106 四健康面板主场差异化（定稿模板 v1 实装）：
        # 画像卡/倒计时条/KPI趋势/生长曲线，随生成器重启自愈
        home = self._home_extras_html(panel_id)

        # ★ 2026-09-18 标题去重：name 自带 emoji 时不再重复拼 icon
        title = name if name.startswith(icon) else f'{icon} {name}'

        # ★ 2026-09-18 表单分组：字段标 adv:1 的进「更多字段」折叠；
        #   没标且字段超过 8 个的，前 5 个算核心、其余折叠（开发者：核心字段直接露出来）
        if any(f.get('adv') for f in fields):
            core_fields = [f for f in fields if not f.get('adv')]
            adv_list = [f for f in fields if f.get('adv')]
        elif len(fields) > 8:
            core_fields = fields[:5]
            adv_list = fields[5:]
        else:
            core_fields, adv_list = list(fields), []
        form_html = ''.join(self._field_input_html(f) for f in core_fields)
        if adv_list:
            form_html += (
                f'<details class="adv-fields" id="advFields">'
                f'<summary>更多字段（选填 {len(adv_list)} 项，想记细再展开）</summary>'
                + ''.join(self._field_input_html(f) for f in adv_list)
                + '</details>')

        # ★ 2026-09-16 提醒条（features.remind：到期红/未到黄，可一键完成）
        remind_html = ''
        if features.get('remind'):
            remind_html = '''
  <!-- ★ 2026-09-16 提醒条（features.remind） -->
  <div id="remindBar" style="display:none;margin-bottom:12px;border-radius:8px;padding:12px;border:0.5px solid #5a3a3a;background:rgba(248,81,73,0.08)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <h3 style="font-size:13px;color:#f85149;margin:0">⏰ 提醒事项</h3>
      <button class="panel-btn" style="font-size:11px;padding:2px 10px" onclick="loadRemindBar()">刷新</button>
    </div>
    <div id="remindList" style="font-size:12px"></div>
  </div>
<script>
async function loadRemindBar(){
  try{
    var rs=await fetch('/api/home/reminders?module=PANEL_ID&pending=1').then(r=>r.json());
    var list=(rs.reminders||[]);
    var now=new Date(); var due=[],up=[];
    list.forEach(function(r){ var t=new Date(String(r.remind_at).replace(' ','T'));
      (t<=now?due:up).push(r); });
    if(!due.length&&!up.length){document.getElementById('remindBar').style.display='none';return;}
    document.getElementById('remindBar').style.display='block';
    var esc=function(s){return String(s||'').replace(/</g,'&lt;');};
    var html='';
    due.forEach(function(r){html+='<div style="display:flex;align-items:center;gap:8px;padding:4px 0;color:#f85149"><span style="flex:1">🔴 '+esc(r.remind_at)+' '+esc(r.title)+'</span><button class="panel-btn" style="font-size:11px;padding:2px 8px" onclick="doneRemind('+r.id+')">完成</button></div>';});
    up.slice(0,5).forEach(function(r){html+='<div style="display:flex;align-items:center;gap:8px;padding:4px 0;color:#d29922"><span style="flex:1">🟡 '+esc(r.remind_at)+' '+esc(r.title)+'</span><button class="panel-btn" style="font-size:11px;padding:2px 8px" onclick="doneRemind('+r.id+')">完成</button></div>';});
    document.getElementById('remindList').innerHTML=html;
  }catch(e){console.error('提醒条加载失败:',e);}
}
async function doneRemind(id){
  try{ await fetch('/api/home/reminders/'+id+'/complete',{method:'POST'}); loadRemindBar(); }catch(e){}
}
loadRemindBar();
</script>'''.replace('PANEL_ID', panel_id)

        # ★ 2026-09-16 AI 分析按钮（features.ai：本地模型读面板记录做分析）
        # ★ 2026-09-19 #768 做实：打开面板自动回显上次分析（panel_ai_history 落库）+ 可带问题定向分析
        ai_html = ''
        if features.get('ai'):
            ai_html = '''
  <div style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <button class="panel-btn" onclick="runPanelAI()">✨ AI 分析</button>
    <input id="panelAIQ" placeholder="想让AI重点看什么？选填，如：这个月花超没" style="flex:1;min-width:180px;border:1px solid #E5EAF2;border-radius:8px;padding:7px 10px;font-size:12px;outline:none" onkeydown="if(event.key==='Enter')runPanelAI()">
  </div>
  <div id="panelAIBox" style="display:none;background:rgba(91,135,216,0.07);border:1px solid #E5EAF2;border-radius:10px;padding:12px;margin-bottom:16px;white-space:pre-wrap;font-size:12px;line-height:1.7;color:#3A4453"></div>
<script>
function _showPanelAI(t){
  var box=document.getElementById('panelAIBox');
  box.style.display='block'; box.textContent=t;
}
function _aiErr(msg){
  /* ★ 2026-09-19 修C：技术报错翻译成人话+给下一步，不让用户对着术语发呆 */
  /* ★ D2（2026-10-11）修正：开关类错误后端本来就是人话，如实透传不再误报「本地模型没准备好」 */
  var m = String(msg||'');
  if (/panel_id|缺少/.test(m)) return '⚠️ 分析功能出了个小故障（模块标识没传到）。\\n重启一下哈文就会自动修好：关掉哈文窗口，再双击桌面「Harven AI」打开。';
  if (/超时|timeout/i.test(m)) return '⚠️ 这次分析等太久没回话（模型可能在忙别的）。\\n稍等几秒再点一次「✨ AI 分析」试试。';
  if (/开关|外脑模式|外脑/.test(m)) return '⚠️ ' + m;   /* 开关/阵营/外脑配置类：后端文案已给指引，原样显示 */
  if (/模型|model|加载|ollama/i.test(m)) return '⚠️ 本地分析模型还没准备好。\\n去「设置 → 模型」确认已选好本地模型；或者到聊天框直接问我，我帮你看。';
  return '⚠️ 这次分析没成功：' + m + '\\n再试一次还不行的话，把上面这句话截图，到「设置 → 关于」里反馈。';
}
async function runPanelAI(){
  var box=document.getElementById('panelAIBox');
  box.style.display='block'; box.textContent='⏳ 分析中，几秒钟就好…';
  try{
    var q=(document.getElementById('panelAIQ')||{}).value||'';
    var r=await fetch('/api/assist/panel-ai',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({panel_id:'PANEL_ID',question:q})}).then(r=>r.json());  /* ★ 2026-09-19 修A：原来写成驼峰panelId，后端永远收不到→400，按钮从上线起就没成功过 */
    box.textContent = r.success ? r.text : _aiErr(r.error);
  }catch(e){ box.textContent=_aiErr(e.message); }
}
(async function loadLastPanelAI(){
  try{
    var r=await fetch('/api/assist/panel-ai/last?panel_id=' + encodeURIComponent('PANEL_ID')).then(r=>r.json());
    if(r && r.success && r.result && r.result.text){
      _showPanelAI('🕘 上次分析（' + (r.result.created_at||'') + '）\\n\\n' + r.result.text);
    }
  }catch(e){}
})();
</script>'''.replace('PANEL_ID', panel_id)

        # ★ 2026-09-20 #801 AI 识图填表（features.ai + 含图片字段：菜品/单据照片自动填字段，本地识别不出门）
        vision_html = ''
        if features.get('ai') and any(f.get('type') == 'image' for f in fields):
            vision_html = '''
  <div id="visionBar" style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <button class="panel-btn" onclick="runVisionFill(this)">📷 AI 识图填表</button>
    <span id="visionTip" style="font-size:12px;color:#9AA3B0">先在表单里选好照片，再点一下，识别结果自动填进表单（本地识别，照片不出门）</span>
  </div>
<script>
async function runVisionFill(btn){
  var tip=document.getElementById('visionTip');
  var imgInput=document.querySelector('input[type=file][accept="image/*"]');
  var b64=(imgInput&&imgInput.dataset&&imgInput.dataset.b64)||'';
  if(!b64){ tip.textContent='⚠️ 先在表单里选好照片，再点「AI 识图填表」'; return; }
  var old=btn.textContent; btn.textContent='⏳ 识别中…'; btn.disabled=true;
  try{
    var r=await fetch('/api/panels/PANEL_ID/vision-fill',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image:b64})}).then(r=>r.json());
    if(!r.success){ tip.textContent='⚠️ '+((r.error&&String(r.error).slice(0,80))||'没识别出来，可以手填'); }
    else{
      var vals=r.values||{}; var n=0;
      for(var k in vals){
        var el=document.getElementById('field_'+k);
        if(!el||!vals[k]) continue;
        el.value=String(vals[k]); n++;
      }
      tip.textContent = n ? ('✅ 已自动填 '+n+' 个字段，检查一下再保存') : '识别完了，但没匹配到能填的字段，可以手填';
    }
  }catch(e){ tip.textContent='⚠️ 识别失败：'+e.message; }
  btn.textContent=old; btn.disabled=false;
}
</script>'''.replace('PANEL_ID', panel_id)

        # ★ B1 批量照片录入（2026-10-11 照片链，全面板通用，已批准）：
        #   选多图（≤4）→ batch-extract 自动分类建议 → 确认卡（可改/换面板/组级配图）→ 一起入库。
        #   电脑恒原图；只有超 8MB（base64 顶上传上限）才自动压缩保平安。不靠浮窗，确认卡由 PanelAssist api 提供。
        batch_html = '''
  <div id="batchBar" style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <button class="panel-btn" id="batchPhotoBtn" onclick="runBatchPhotos()">📷 批量照片录入</button>
    <span id="batchTip" style="font-size:12px;color:#9AA3B0">一次最多 4 张，哈文自动认内容、建议记到哪个面板，你确认后一起入库</span>
  </div>
  <input type="file" id="batchPhotoInput" accept="image/*" multiple style="display:none">
<script>
function runBatchPhotos(){
  var inp=document.getElementById('batchPhotoInput');
  if(inp) inp.click();
}
(function(){
  var inp=document.getElementById('batchPhotoInput');
  if(!inp) return;
  /* 电脑恒原图；只有超 8MB（base64 顶上传上限）才自动压缩保平安 */
  function _batchShrink(dataUrl){
    var mb=dataUrl.length*0.75/1048576;
    if(mb<=8){ return Promise.resolve(dataUrl.split(',')[1]||''); }
    return new Promise(function(res){
      var img=new Image();
      img.onload=function(){
        var c=document.createElement('canvas');
        var sc=Math.min(1,1600/Math.max(img.width,img.height));
        c.width=Math.round(img.width*sc);c.height=Math.round(img.height*sc);
        c.getContext('2d').drawImage(img,0,0,c.width,c.height);
        res(c.toDataURL('image/jpeg',0.82).split(',')[1]||'');
      };
      img.onerror=function(){ res(dataUrl.split(',')[1]||''); };
      img.src=dataUrl;
    });
  }
  inp.addEventListener('change', async function(){
    var files=Array.prototype.slice.call(inp.files||[]).slice(0,4);
    inp.value='';
    if(!files.length) return;
    var btn=document.getElementById('batchPhotoBtn'), tip=document.getElementById('batchTip');
    var old=btn.textContent; btn.textContent='⏳ 识别中…'; btn.disabled=true; tip.textContent='正在看 '+files.length+' 张照片…';
    try{
      var imgs=[];
      for(var i=0;i<files.length;i++){
        var dataUrl=await new Promise(function(res,rej){var rd=new FileReader();rd.onload=function(){res(String(rd.result))};rd.onerror=rej;rd.readAsDataURL(files[i])});
        imgs.push(await _batchShrink(dataUrl));
      }
      var r=await fetch('/api/panels/photos/batch-extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({images:imgs})}).then(function(x){return x.json()});
      if(!r.success) throw new Error(r.error||'识别失败');
      var groups=[],empty=0;
      (r.items||[]).forEach(function(it,ii){
        var gs=it.groups||[];
        if(!gs.length){empty++;return;}
        gs.forEach(function(g){var cp=Object.assign({},g);cp._img=imgs[ii];groups.push(cp);});
      });
      if(!groups.length){ tip.textContent='⚠️ 这几张照片都没认出能记的内容'+((r.error&&String(r.error).slice(0,60))?'：'+r.error:''); btn.textContent=old; btn.disabled=false; return; }
      tip.textContent = empty ? ('✅ 识别完，有 '+empty+' 张没认出来，其余已进确认卡') : '✅ 识别完，请在确认卡里过目后入库';
      if(window._paApi && window._paApi.showConfirmHome) window._paApi.showConfirmHome(groups);
      else alert('确认卡组件没就绪，刷新页面再试');
    }catch(e){ tip.textContent='⚠️ '+e.message; btn.textContent=old; btn.disabled=false; return; }
    btn.textContent=old; btn.disabled=false;
  });
})();
</script>'''

        # ★ 2026-08-08: 统计卡片+图表（features.stats 也可开启）
        # ★ 2026-10-10 #1062：餐饮面板用专属统计（外卖/吃辣/热量参考），不再走通用「总记录/分类数/数值合计」
        chart_html = ''
        if panel_id == 'meal_diary' and with_chart:
            chart_html = meal.get('chart', '')
        elif with_chart:
            chart_html = '''
  <!-- ★ 统计卡片 -->
  <div class="summary-cards" id="summaryCards" style="display:none;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">
    <div style="background:#fff;border:1px solid #E5EAF2;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:11px;color:#9AA3B0;margin-bottom:4px">总记录</div><div style="font-size:20px;font-weight:bold;color:#5B87D8" id="chartTotal">0</div>
    </div>
    <div style="background:#fff;border:1px solid #E5EAF2;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:11px;color:#9AA3B0;margin-bottom:4px">分类数</div><div style="font-size:20px;font-weight:bold;color:#22A06B" id="chartCats">0</div>
    </div>
    <div style="background:#fff;border:1px solid #E5EAF2;border-radius:10px;padding:12px;text-align:center">
      <div style="font-size:11px;color:#9AA3B0;margin-bottom:4px">数值合计</div><div style="font-size:20px;font-weight:bold;color:#E05B5B" id="chartSum">0</div>
    </div>
  </div>
  <div id="catSection" style="display:none;background:#fff;border:1px solid #E5EAF2;border-radius:10px;padding:12px;margin-bottom:16px">
    <h3 style="font-size:13px;color:#5B87D8;margin-bottom:8px">📊 分类统计</h3>
    <div id="catChart"></div>
  </div>
  <button class="panel-btn" onclick="loadPanelChart()" style="margin-bottom:12px">📊 显示统计</button>
<script>
async function loadPanelChart(){
  try{
    var r=await fetch('/api/panels/''' + panel_id + '''/data').then(r=>r.json());
    var rows=r.rows||r.records||r.data||[];
    document.getElementById('summaryCards').style.display='grid';
    document.getElementById('catSection').style.display='block';
    document.getElementById('chartTotal').textContent=rows.length;
    var numField=''' + json.dumps(next((f['name'] for f in fields if f.get('type')=='number'), '')) + ''';
    var catField=''' + json.dumps(next((f['name'] for f in fields if f.get('type')=='select'), '')) + ''';
    if(numField){
      var sum=rows.reduce((s,r)=>s+(parseFloat(r[numField])||0),0);
      document.getElementById('chartSum').textContent=sum.toFixed(1);
    }
    if(catField&&rows.length){
      var cats={};
      rows.forEach(r=>{var c=r[catField]||'其他';cats[c]=(cats[c]||0)+1;});
      document.getElementById('chartCats').textContent=Object.keys(cats).length;
      var maxV=Math.max(...Object.values(cats),1);
      var colors=['#f85149','#d29922','#3fb950','#58a6ff','#bc8cff','#ff7b72','#f0883e','#a371f7'];
      var html='';var i=0;
      for(var c in cats){var pct=(cats[c]/maxV*100).toFixed(0);
        html+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px"><span style="width:70px;font-size:11px">'+c+'</span><div style="flex:1;height:16px;background:#EEF2F8;border-radius:3px;overflow:hidden"><div style="height:100%;width:'+pct+'%;background:'+colors[i%8]+';border-radius:3px"></div></div><span style="width:40px;text-align:right;font-size:11px;color:#9AA3B0">'+cats[c]+'</span></div>';
        i++;}
      document.getElementById('catChart').innerHTML=html;
    }
  }catch(e){console.error('图表加载失败:',e);}
}
</script>'''

        search_html = ''
        if with_search:
            search_html = '''
  <div class="panel-search">
    <input type="text" id="searchInput" placeholder="搜索..." oninput="PanelApp.loadData()">
    <select id="memberFilter" class="panel-select" style="margin-top:8px;"><option value="">全部成员</option></select>
  </div>'''

        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}</title>
<link rel="stylesheet" href="/static/panel-assist.css">
<link rel="stylesheet" href="/panel/{panel_id}/panel.css">
</head>
<body class="panel-body">
<div class="panel-container">
  <div class="panel-header">
    <h2>{title}</h2>
    <button class="panel-btn panel-btn-primary" onclick="PanelApp.showForm()">+ 添加</button>
  </div>
  <!-- ★ 2026-10-22 教练横幅：哈文观察+数据指标+行动按钮（标配） -->
  <div id="hvCoach"></div>
{fridge.get('tabs', '')}{storage.get('tabs', '')}
{fridge.get('open', '')}
{meal.get('top', '')}{health.get('top', '')}
{cross_html}
{home.get('top', '')}
{remind_html}
{chart_html}
{ai_html}
{batch_html}
{vision_html}
{search_html}
  <div id="formArea" class="panel-form" style="display:none">
    {form_html}
    <div class="panel-field">
      <label>记录人（家庭成员）</label>
      <select id="field__member"><option value="">不标注</option></select>
    </div>
    <div class="panel-form-actions">
      <button class="panel-btn panel-btn-primary" onclick="PanelApp.saveRecord()">保存</button>
      <button class="panel-btn" onclick="PanelApp.hideForm()">取消</button>
    </div>
    <input type="hidden" id="editId" value="">
  </div>

  <!-- ★ 2026-08-08: 视图切换 -->
  <div style="margin-bottom:8px;display:flex;gap:8px;">
    <button class="panel-btn" onclick="PanelApp.toggleView()" style="font-size:11px;padding:4px 12px;">🔄 切换视图</button>
  </div>

  <!-- ★ 2026-08-08: 卡片视图容器 -->
  <div id="cardViewWrap" style="display:none;"></div>

  <div class="panel-table-wrap">
    <table id="dataTable" class="panel-table">
      <thead>
        <tr>
          {"".join(f'<th>{"估算热量*" if (panel_id == "meal_diary" and f.get("name") == "估算热量") else f.get("label", f.get("name",""))}</th>' for f in fields)}
          <th style="width:100px">操作</th>
        </tr>
      </thead>
      <tbody id="tableBody">
        <tr><td colspan="{len(fields)+1}" class="panel-empty">加载中...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="panel-pagination" id="pagination"></div>
{meal.get('foot', '')}
{fridge.get('mid', '')}{storage.get('mid', '')}
</div>

<script src="/static/panel-api.js"></script>
<script src="/static/panel-coach.js"></script>
<script src="/panel/{panel_id}/panel.js"></script>
<script src="/static/panel-assist.js"></script>
<script>
  PanelApp.init('{panel_id}', {json.dumps(fields, ensure_ascii=False)});
  PanelApp.loadData();
  /* ★ 2026-10-22 教练横幅：哈文观察+数据指标+行动按钮（所有生成品面板标配） */
  if (window.HvCoach) {{ HvCoach.mount({{ panelId: '{panel_id}' }}); }}
  /* ★ 2026-09-16 对话即录入：底部对话框（文字/语音/拍照 → AI预填确认卡） */
  /* ★ B1（2026-10-11）：mount 返回值接住——批量照片录入按钮通过 _paApi.showConfirmHome 弹确认卡 */
  if (window.PanelAssist) {{
    window._paApi = PanelAssist.mount({{
      host: document.querySelector('.panel-container'),
      panelId: '{panel_id}',
      title: '哈文助手 · {name}',
      hintText: {json.dumps(assist_hint, ensure_ascii=False)},
      fields: {json.dumps(fields, ensure_ascii=False)},
      onSaved: function() {{ PanelApp.loadData(); }}
    }});
  }}
</script>
{fridge.get('scripts', '')}{storage.get('scripts', '').replace('PANEL_ID', panel_id)}
</body>
</html>'''

    # ═══════════════ ★ 2026-10-05 #1010~#1015 冰箱深化骨架（仅 fridge_stock_1） ═══════════════
    # 放进生成器 = _sync_generated_panel 重启再生成时自带全部骨架，彻底根治「重启冲掉定制页」。
    # 样式内联在 tabs 段（panel.css 是通用模板会被再生成，不能依赖它放专属样式）。

    def _fridge_tabs_html(self) -> dict:
        return {
            'tabs': '''<style>
.f-tabs{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.f-tab{border:1px solid #E5EAF2;background:#fff;color:#3A4453;border-radius:20px;padding:7px 18px;font-size:13px;cursor:pointer}
.f-tab.on{background:#5B87D8;border-color:#5B87D8;color:#fff;font-weight:600}
.f-card{background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:14px;margin-bottom:14px}
.f-card h3{font-size:13px;color:#3A4453;margin:0 0 10px}
.f-chip{display:inline-flex;align-items:center;gap:6px;background:#F1F3F7;border-radius:16px;padding:4px 12px;font-size:12px;margin:0 8px 8px 0;cursor:pointer;color:#3A4453}
.f-chip:hover{background:#E3EAF6}
.f-chip-x{color:#9AA3B0;font-style:normal;padding:0 2px}
.f-chip-x:hover{color:#E05B5B}
.f-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.f-grid label{font-size:11px;color:#9AA3B0;display:block;margin-bottom:3px}
.f-grid input,.f-grid select,.f-grid textarea{width:100%;box-sizing:border-box;border:1px solid #E5EAF2;border-radius:8px;padding:7px 9px;font-size:12px}
.f-tag{display:inline-block;font-size:11px;padding:2px 10px;border-radius:10px;margin-left:8px}
.f-tip{font-size:12px;color:#9AA3B0}
@media (max-width:600px){.f-grid{grid-template-columns:1fr}}
</style>
<div class="f-tabs">
  <button class="f-tab on" data-tab="fresh" onclick="fSwitchTab('fresh')">⏳ 保质期看板</button>
  <button class="f-tab" data-tab="stock" onclick="fSwitchTab('stock')">📦 库存管理</button>
  <button class="f-tab" data-tab="recipe" onclick="fSwitchTab('recipe')">🍳 食谱建议</button>
  <button class="f-tab" data-tab="buy" onclick="fSwitchTab('buy')">🛒 买菜清单</button>
</div>
<div id="tabFresh">
  <div id="freshBoard" style="display:none;background:#fff;border:1px solid #E5EAF2;border-radius:10px;padding:12px">
    <h3 style="font-size:13px;color:#3A4453;margin:0 0 8px">⏳ 保质期看板 <span id="freshSummary" style="font-weight:normal;color:#9AA3B0;font-size:11px"></span></h3>
    <div id="freshList" style="font-size:12px"></div>
  </div>
  <div id="freshEmpty" style="display:none;text-align:center;color:#9AA3B0;font-size:12.5px;padding:26px 0">
    🔋 看板还很清净：没有临期（7 天内）要操心的食材。<br>记食材时填上「到期日」，快过期这里会自动亮灯提醒
  </div>
</div>
<div id="tabStock" style="display:none">
  <div class="f-card" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button class="panel-btn" onclick="document.getElementById('fPhotoInput').click()">📷 拍照识菜入库</button>
    <input type="file" id="fPhotoInput" accept="image/*" style="display:none" onchange="fPickPhoto(this)">
    <span id="fScanTip" class="f-tip">对冰箱/食材拍一张，认出的食材逐条确认后一键入库（识别用你当前选的模型阵营）</span>
  </div>
  <div id="scanResults"></div>
  <div class="f-card">
    <h3>📦 分区库存 <span class="f-tip">吃掉了/处理掉了点一下就记，台账留痕可撤销，买菜清单自动跟上</span></h3>
    <div id="fZoneStock"><div class="f-tip">正在读库存…</div></div>
  </div>
  <div class="f-card">
    <h3>🧾 消耗流水 <span class="f-tip">家里吃了多少、扔了多少，都在这攒着（汇总自动进健康画像）</span></h3>
    <div id="fConsumeLog"><div class="f-tip">还没有消耗记录。吃了/扔了点什么，在上面的分区库存里点一下就有。</div></div>
  </div>
''',
            'mid': '''</div><!-- /tabStock -->
<div id="tabRecipe" style="display:none">
  <div class="f-card">
    <h3>👨‍👩‍👧 家人饮食档案 <span class="f-tip">过敏原会被食谱硬排除；并入档案中枢，问哈文「家人对什么过敏」也能答</span></h3>
    <div id="fDietList"></div>
    <button class="panel-btn" style="font-size:12px" onclick="fDietEdit('')">＋ 添加成员</button>
  </div>
  <div class="f-card" id="fDietForm" style="display:none">
    <h3 id="fDietFormTitle">添加成员</h3>
    <input type="hidden" id="fid_id">
    <div class="f-grid">
      <div><label>称呼（必填）</label><input id="fid_name" placeholder="如：爸爸 / 儿子"></div>
      <div><label>过敏（逗号分隔，硬排除）</label><input id="fid_allergies" placeholder="如：芒果, 花生"></div>
      <div><label>忌口</label><input id="fid_dislikes" placeholder="如：香菜, 苦瓜"></div>
      <div><label>喜好</label><input id="fid_likes" placeholder="如：爱吃面食"></div>
    </div>
    <div style="margin-top:8px"><label style="font-size:11px;color:#9AA3B0;display:block;margin-bottom:3px">慢病 / 饮食注意（少油少盐少糖会照办）</label>
      <input id="fid_conditions" style="width:100%;box-sizing:border-box;border:1px solid #E5EAF2;border-radius:8px;padding:7px 9px;font-size:12px" placeholder="如：血糖偏高"></div>
    <div style="margin-top:10px;display:flex;gap:8px">
      <button class="panel-btn panel-btn-primary" style="font-size:12px" onclick="fDietSave()">保存</button>
      <button class="panel-btn" style="font-size:12px" onclick="document.getElementById('fDietForm').style.display='none'">取消</button>
    </div>
  </div>
  <div class="f-card">
    <h3>🍳 今天吃什么 <span class="f-tip">库存临期优先 · 过敏硬排除 · 当季搭配 · 跟随你选的模型出单</span></h3>
    <div id="fNewStockBar" style="display:none;margin-bottom:10px;background:rgba(34,160,107,0.08);border:1px solid rgba(34,160,107,0.25);border-radius:10px;padding:9px 12px;font-size:12.5px;color:#3A4453">🥜 库存进了新食材<span id="fNewStockNames"></span>，要不要照着配一桌？ <button class="panel-btn panel-btn-primary" style="font-size:11.5px;padding:4px 10px" onclick="fGenRecipe(document.getElementById('fRecipeBtn'));this.parentElement.style.display='none'">一键生成</button> <a href="javascript:void(0)" style="font-size:11.5px;color:#9AA3B0;margin-left:6px" onclick="this.parentElement.style.display='none'">先不用</a></div>
    <button class="panel-btn panel-btn-primary" id="fRecipeBtn" onclick="fGenRecipe(this)">✨ 生成今日食谱</button>
    <div id="fRecipeBox" style="display:none;margin-top:12px;background:rgba(91,135,216,0.07);border:1px solid #E5EAF2;border-radius:10px;padding:12px;white-space:pre-wrap;font-size:12.5px;line-height:1.8;color:#3A4453"></div>
  </div>
  <div class="f-card">
    <h3>📸 拍冰箱直接问 <span class="f-tip">对冰箱拍一张，按你当前选的模型看图直答「能做什么菜」——快问快答不记台账；想留记录、要临期提醒就用上面的「生成今日食谱」+「拍照识菜入库」</span></h3>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button class="panel-btn" onclick="document.getElementById('fEyeInput').click()">📸 选一张冰箱照片</button>
      <input type="file" id="fEyeInput" accept="image/*" style="display:none" onchange="fEyePick(this)">
      <span id="fEyeName" class="f-tip"></span>
    </div>
    <div id="fEyePreview" style="display:none;margin-top:8px"><img id="fEyeImg" style="max-width:180px;max-height:140px;border-radius:10px;border:1px solid #E5EAF2" alt="冰箱照片"></div>
    <div style="margin-top:8px"><button class="panel-btn panel-btn-primary" id="fEyeBtn" style="display:none" onclick="fEyeAsk(this)">🔍 问一下：今天能做什么菜</button></div>
    <div id="fEyeBox" style="display:none;margin-top:12px;background:rgba(34,160,107,0.06);border:1px solid rgba(34,160,107,0.2);border-radius:10px;padding:12px;white-space:pre-wrap;font-size:12.5px;line-height:1.8;color:#3A4453"></div>
  </div>
</div>
<div id="tabBuy" style="display:none">
  <div class="f-card">
    <h3>🛒 该买啥了 <span class="f-tip">要补货 / 吃完了 / 库存低于警戒线的都在这；对照记账近 3 天，刚买过的会标出来防重复</span></h3>
    <button class="panel-btn" id="fBuyBtn" onclick="fGenBuy(this)">🔄 刷新买菜清单</button>
    <div id="fBuyBox" style="margin-top:12px"></div>
  </div>
</div>''',
            'scripts': '''<script>
/* ══ #1010 冰箱四 Tab 逻辑（随生成器再生成，重启不丢） ══ */
var F_ZONES=['冷藏','冷冻','门格','零食饮料','粮食干货','调味油盐'];
function fEsc(s){return String(s||'').replace(/</g,'&lt;');}
function fSwitchTab(t){
  document.querySelectorAll('.f-tab').forEach(function(b){ b.classList.toggle('on', b.dataset.tab===t); });
  document.getElementById('tabFresh').style.display  = t==='fresh'  ? '' : 'none';
  document.getElementById('tabStock').style.display  = t==='stock'  ? '' : 'none';
  document.getElementById('tabRecipe').style.display = t==='recipe' ? '' : 'none';
  document.getElementById('tabBuy').style.display    = t==='buy'    ? '' : 'none';
  if(t==='fresh')  loadFreshBoard();
  if(t==='stock'){ fLoadZoneStock(); fLoadConsumeLog(); }
  if(t==='recipe'){ loadFDiet(); fLoadLastRecipe(); fCheckNewStock(); }
  if(t==='buy')    fGenBuy();
}
/* ── #1370 分区库存（6 分区分组 + 吃掉了/处理掉了 一键留痕可撤销） ── */
var fZoneItems=[], fZoneZones=[];
async function fLoadZoneStock(){
  var box=document.getElementById('fZoneStock');
  if(!box) return;
  try{
    var r=await fetch('/api/panels/fridge/stock').then(function(x){return x.json()});
    if(!r.success){ box.innerHTML='<div class="f-tip">读库存失败：'+fEsc(r.error||'')+'</div>'; return; }
    fZoneItems=r.items||[]; fZoneZones=r.zones||[];
    fRenderZoneStock();
  }catch(e){ box.innerHTML='<div class="f-tip">读库存失败：'+fEsc(e.message)+'</div>'; }
}
function fRenderZoneStock(){
  var box=document.getElementById('fZoneStock');
  if(!box) return;
  var groups={}, order=[];
  fZoneZones.forEach(function(z){ groups[z]=[]; order.push(z); });
  groups['未分区']=[]; order.push('未分区');
  fZoneItems.forEach(function(it){
    var z=(it.state && it.state!=='在存') ? null : (it.zone||'未分区');
    if(!z) return;
    if(!groups[z]) { groups[z]=[]; order.push(z); }
    groups[z].push(it);
  });
  var html='';
  order.forEach(function(z){
    var arr=groups[z]||[];
    var badge='<span style="background:rgba(91,135,216,0.12);color:#5B87D8;border-radius:10px;padding:1px 8px;font-size:11px;margin-left:6px">'+arr.length+' 项</span>';
    html+='<div style="margin-bottom:10px"><div style="font-size:12px;font-weight:600;color:#3A4453;margin-bottom:4px">'+fEsc(z)+badge+'</div>';
    if(!arr.length){ html+='<div class="f-tip" style="padding-left:2px">空</div>'; }
    arr.forEach(function(it){
      var d=it.days;
      var dot = (d===null||d===undefined) ? '' : (d<0?'<span style="color:#555;font-size:11px;margin-left:4px">已过期'+(-d)+'天</span>' : (d<=3?'<span style="color:#E05B5B;font-size:11px;margin-left:4px">还剩'+d+'天</span>' : (d<=7?'<span style="color:#B07A10;font-size:11px;margin-left:4px">还剩'+d+'天</span>':'')));
      var qty=(it.qty?it.qty:'')+(it.unit||'');
      html+='<div style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:12.5px;color:#3A4453;flex-wrap:wrap">'
        +'<span style="min-width:0">'+fEsc(it.name)+(qty?'（'+fEsc(qty)+'）':'')+dot+'</span>'
        +'<button class="panel-btn" style="font-size:11px;padding:2px 8px" title="吃掉了：台账留痕，买菜清单自动补" onclick="fConsume('+it.id+',\\'eaten\\')">🍽 吃掉了</button>'
        +'<button class="panel-btn" style="font-size:11px;padding:2px 8px" title="处理掉了（过期/坏掉）：留痕可撤销" onclick="fConsume('+it.id+',\\'trashed\\')">🗑 处理掉了</button>'
        +'</div>';
    });
    html+='</div>';
  });
  if(!fZoneItems.length) html='<div class="f-tip">库存还是空的。拍照识菜或手动记一条，这里就按冷藏/冷冻/粮食干货这些分区摆好。</div>';
  box.innerHTML=html;
}
async function fConsume(id, action){
  if(action==='trashed' && !confirm('确认记「处理掉了」？台账会留痕（可撤销），买菜清单自动跟进。')) return;
  try{
    var r=await fetch('/api/panels/fridge/consume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,action:action})}).then(function(x){return x.json()});
    if(!r.success){ alert(r.error||'没记上'); return; }
    fLoadZoneStock(); fLoadConsumeLog(); loadFreshBoard();
  }catch(e){ alert('操作失败：'+e.message); }
}
async function fConsumeUndo(logId){
  try{
    var r=await fetch('/api/panels/fridge/consume/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({log_id:logId})}).then(function(x){return x.json()});
    if(!r.success){ alert(r.error||'撤销失败'); return; }
    fLoadZoneStock(); fLoadConsumeLog(); loadFreshBoard();
  }catch(e){ alert('撤销失败：'+e.message); }
}
/* ── #1370 消耗流水卡 ── */
async function fLoadConsumeLog(){
  var box=document.getElementById('fConsumeLog');
  if(!box) return;
  try{
    var r=await fetch('/api/panels/fridge/consume/log?days=30').then(function(x){return x.json()});
    if(!r.success){ box.innerHTML='<div class="f-tip">读流水失败：'+fEsc(r.error||'')+'</div>'; return; }
    var items=r.items||[], s=r.summary||{};
    var html='';
    if(s && (s.eaten||s.trashed)){
      var top=(s.top||[]).map(function(t){return t[0]}).slice(0,3).join('、');
      html+='<div style="font-size:12px;color:#3A4453;background:rgba(91,135,216,0.06);border-radius:8px;padding:6px 10px;margin-bottom:6px">📊 '+fEsc(s.range||'近期')+'：吃掉 <b>'+s.eaten+'</b> 份 · 处理掉 <b>'+s.trashed+'</b> 份'+(top?(' · 最常吃：'+fEsc(top)):'')+'</div>';
    }
    if(!items.length){
      if(!html) html='<div class="f-tip">还没有消耗记录。吃了/扔了点什么，在分区库存里点一下就有。</div>';
    } else {
      html+=items.slice(0,15).map(function(it){
        var icon=it.action==='eaten'?'🍽':'🗑';
        var undo=it.undone?'<span class="f-tip">已撤销</span>':'<a href="javascript:void(0)" style="font-size:11px;color:#5B87D8" onclick="fConsumeUndo('+it.id+')">撤销</a>';
        var qty=(it.qty?it.qty:'')+(it.unit||'');
        return '<div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12px;color:#3A4453">'
          +'<span>'+icon+' <b>'+fEsc(it.name)+'</b>'+(qty?'（'+fEsc(qty)+'）':'')+(it.zone?' · '+fEsc(it.zone):'')+'</span>'
          +'<span class="f-tip">'+fEsc(it.at||'')+'</span>'+undo+'</div>';
      }).join('');
      if(items.length>15) html+='<div class="f-tip">…近 30 天共 '+items.length+' 条，只显示最近 15 条</div>';
    }
    box.innerHTML=html;
  }catch(e){ box.innerHTML='<div class="f-tip">读流水失败：'+fEsc(e.message)+'</div>'; }
}
/* ── #1372 拍冰箱直接问（#1375 改：跟随你选的模型看图直答，不落账） ── */
var fEyeB64='';
function fEyePick(inp){
  var f=inp.files && inp.files[0];
  inp.value='';
  if(!f) return;
  fEyeB64='';
  var rd=new FileReader();
  rd.onload=function(){
    var img=new Image();
    img.onload=function(){
      var cv=document.createElement('canvas');
      var max=1024, sc=Math.min(1, max/Math.max(img.width,img.height));
      cv.width=Math.round(img.width*sc); cv.height=Math.round(img.height*sc);
      cv.getContext('2d').drawImage(img,0,0,cv.width,cv.height);
      fEyeB64=cv.toDataURL('image/jpeg',0.85);
      document.getElementById('fEyeImg').src=fEyeB64;
      document.getElementById('fEyePreview').style.display='block';
      document.getElementById('fEyeBtn').style.display='inline-block';
      document.getElementById('fEyeName').textContent=f.name;
    };
    img.src=rd.result;
  };
  rd.readAsDataURL(f);
}
async function fEyeAsk(btn){
  if(!fEyeB64){ alert('先选一张冰箱照片'); return; }
  var box=document.getElementById('fEyeBox');
  box.style.display='block'; box.textContent='⏳ 正在看图…（用你当前选的模型，十秒到一分钟）';
  btn.disabled=true;
  try{
    var r=await fetch('/api/panels/fridge/eye-ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:fEyeB64})}).then(function(x){return x.json()});
    if(r.success){ box.textContent=r.text+'\\n\\n── '+r.model+' 直答（快问快答，不记台账） ──'; }
    else box.textContent='⚠️ '+r.error;
  }catch(e){ box.textContent='⚠️ 直答失败：'+e.message; }
  btn.disabled=false;
}
/* ── #1332（2026-10-30）新食材联动：上次生成食谱之后有新入库 → 提示一键配菜 ── */
async function fCheckNewStock(){
  var bar=document.getElementById('fNewStockBar');
  if(!bar) return;
  bar.style.display='none';
  try{
    var rr=await fetch('/api/panels/fridge/recipe').then(function(x){return x.json()});
    var lastT=(rr.success&&rr.recipe&&rr.recipe.created_at)||'';
    var r=await fetch('/api/panels/fridge_stock_1/data').then(function(x){return x.json()});
    var rows=r.rows||r.records||r.data||[];
    var newest='', names=[];
    rows.forEach(function(row){
      var c=String(row.created_at||'');
      if(c>newest) newest=c;
      if(!lastT || c>lastT){ var nm=row['名称']||''; if(nm) names.push(nm); }
    });
    if(!newest || (lastT && newest<=lastT)) return;
    document.getElementById('fNewStockNames').textContent = names.length?('（'+names.slice(0,5).join('、')+'）'):'';
    bar.style.display='block';
  }catch(e){}
}
/* ── 保质期看板：读真实库存按到期日分级（过期黑 / ≤3红 / ≤7黄） ── */
async function loadFreshBoard(){
  try{
    var r = await fetch('/api/panels/fridge_stock_1/data').then(function(x){return x.json()});
    var rows = r.rows || r.records || r.data || [];
    var today = new Date(); today.setHours(0,0,0,0);
    var exp=[], red=[], yellow=[], ok=0;
    rows.forEach(function(row){
      var st = String(row['状态']||'在存');
      if(st && st!=='在存') return;
      var d = row['到期日'] ? new Date(String(row['到期日']).replace(' ','T')) : null;
      if(!d || isNaN(d)) return;
      d.setHours(0,0,0,0);
      var days = Math.round((d-today)/86400000);
      var nm = row['名称']||'（未命名）';
      var qty = (row['数量']?row['数量']:'') + (row['数量单位']||'');
      var zone = row['分区']?' · '+row['分区']:'';
      if(days<0) exp.push('⚫ '+fEsc(nm)+(qty?'（'+qty+'）':'')+zone+' · 已过期 '+(-days)+' 天');
      else if(days<=3) red.push('🔴 '+fEsc(nm)+(qty?'（'+qty+'）':'')+zone+' · 还剩 '+days+' 天');
      else if(days<=7) yellow.push('🟡 '+fEsc(nm)+(qty?'（'+qty+'）':'')+zone+' · 还剩 '+days+' 天');
      else ok++;
    });
    var all = exp.concat(red, yellow);
    var board = document.getElementById('freshBoard');
    var empty = document.getElementById('freshEmpty');
    if(!all.length){ board.style.display='none'; empty.style.display='block'; return; }
    empty.style.display='none'; board.style.display='block';
    document.getElementById('freshSummary').textContent = '要操心 '+all.length+' 项 · 新鲜 '+ok+' 项';
    document.getElementById('freshList').innerHTML = all.map(function(s){return '<div style="padding:3px 0;color:#3A4453">'+s+'</div>'}).join('');
  }catch(e){ console.error('保质期看板失败:', e); }
}
loadFreshBoard();
/* ── #1012 家人饮食档案 ── */
var fDiet=[];
async function loadFDiet(){
  try{
    var r=await fetch('/api/panels/fridge/diet').then(function(x){return x.json()});
    fDiet=r.members||[];
    var box=document.getElementById('fDietList');
    if(!fDiet.length){ box.innerHTML='<div class="f-tip" style="margin-bottom:8px">还没建档案。把家里人的过敏、忌口记进来，食谱会照着避开。</div>'; return; }
    box.innerHTML=fDiet.map(function(m){
      var tags='';
      if(m.allergies) tags+='<span class="f-tag" style="background:rgba(224,91,91,0.1);color:#E05B5B">过敏: '+fEsc(m.allergies)+'</span>';
      if(m.dislikes)  tags+='<span class="f-tag" style="background:rgba(210,153,34,0.12);color:#B07A10">忌口: '+fEsc(m.dislikes)+'</span>';
      if(m.likes)     tags+='<span class="f-tag" style="background:rgba(34,160,107,0.1);color:#22A06B">喜好: '+fEsc(m.likes)+'</span>';
      if(m.conditions)tags+='<span class="f-tag" style="background:rgba(91,135,216,0.1);color:#5B87D8">注意: '+fEsc(m.conditions)+'</span>';
      return '<div class="f-chip" title="点一下编辑" onclick="fDietEdit('+m.id+')">👤 '+fEsc(m.name)+tags+'<i class="f-chip-x" title="删除" onclick="event.stopPropagation();fDietDel('+m.id+')">✕</i></div>';
    }).join('');
  }catch(e){ console.error('饮食档案加载失败:', e); }
}
function fDietEdit(id){
  var m = fDiet.find(function(x){return x.id===id}) || {};
  document.getElementById('fid_id').value = m.id||'';
  document.getElementById('fid_name').value = m.name||'';
  document.getElementById('fid_allergies').value = m.allergies||'';
  document.getElementById('fid_dislikes').value = m.dislikes||'';
  document.getElementById('fid_likes').value = m.likes||'';
  document.getElementById('fid_conditions').value = m.conditions||'';
  document.getElementById('fDietFormTitle').textContent = id ? ('编辑：'+m.name) : '添加成员';
  document.getElementById('fDietForm').style.display='block';
}
async function fDietSave(){
  var name=document.getElementById('fid_name').value.trim();
  if(!name){ alert('称呼得填一个'); return; }
  try{
    await fetch('/api/panels/fridge/diet',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:document.getElementById('fid_id').value||null,name:name,
        allergies:document.getElementById('fid_allergies').value.trim(),
        dislikes:document.getElementById('fid_dislikes').value.trim(),
        likes:document.getElementById('fid_likes').value.trim(),
        conditions:document.getElementById('fid_conditions').value.trim()})});
    document.getElementById('fDietForm').style.display='none';
    loadFDiet();
  }catch(e){ alert('保存失败: '+e.message); }
}
async function fDietDel(id){
  if(!confirm('删除这位成员的饮食档案？')) return;
  try{ await fetch('/api/panels/fridge/diet/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id})}); loadFDiet(); }catch(e){}
}
/* ── #1013 食谱生成（跟随阵营） ── */
async function fLoadLastRecipe(){
  try{
    var r=await fetch('/api/panels/fridge/recipe').then(function(x){return x.json()});
    if(r.success && r.recipe && r.recipe.text){
      var box=document.getElementById('fRecipeBox');
      box.style.display='block';
      box.textContent='🕘 上次生成的食谱（'+(r.recipe.created_at||'')+'）\\n\\n'+r.recipe.text;
    }
  }catch(e){}
}
async function fGenRecipe(btn){
  var box=document.getElementById('fRecipeBox');
  box.style.display='block'; box.textContent='⏳ 正在照着库存和家人档案配菜单…（十几秒到一分钟，模型忙时会久一点）';
  btn.disabled=true;
  try{
    var r=await fetch('/api/panels/fridge/recipe',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(function(x){return x.json()});
    if(r.success){ box.textContent=r.text+'\\n\\n── 本单由 '+r.model+' 生成（跟随你当前选的模型） ──'; fLoadLastRecipe(); }
    else box.textContent='⚠️ '+r.error;
  }catch(e){ box.textContent='⚠️ 生成失败：'+e.message; }
  btn.disabled=false;
}
/* ── #1014 买菜清单 ── */
async function fGenBuy(btn){
  var box=document.getElementById('fBuyBox');
  box.innerHTML='<span class="f-tip">对照库存和记账里…</span>';
  try{
    var r=await fetch('/api/panels/fridge/buylist').then(function(x){return x.json()});
    if(!r.success){ box.innerHTML='<span class="f-tip">⚠️ '+fEsc(r.error)+'</span>'; return; }
    var html='';
    if(!(r.need||[]).length){
      html+='<div style="text-align:center;color:#9AA3B0;font-size:12.5px;padding:18px 0">✅ 暂时啥都不缺。库存见底或标「要补货」的东西会自动出现在这</div>';
    } else {
      html+=r.need.map(function(it){
        var dup = it.bought_recently ? '<div style="font-size:11px;color:#B07A10;margin-top:3px">⚠️ '+fEsc(it.bought_recently)+'</div>' : '';
        return '<div style="display:flex;align-items:baseline;gap:8px;padding:5px 0;border-bottom:1px dashed #EEF2F8;color:#3A4453;font-size:12.5px"><b>'+fEsc(it.name)+'</b>'+(it.qty?'<span class="f-tip">'+fEsc(it.qty)+'</span>':'')+'<span class="f-tip">（'+fEsc(it.reason)+'）</span><span style="flex:1"></span>'+dup+'</div>';
      }).join('');
    }
    if((r.regular||[]).length){
      html+='<div style="margin-top:12px;font-size:12px;color:#9AA3B0">📊 对照近 30 天常买（记账统计，按买过次数排）：<br>'
        + r.regular.slice(0,8).map(function(x){return fEsc(x.item)+'（'+x.n+'次）'}).join('、') + '</div>';
    }
    box.innerHTML=html;
  }catch(e){ box.innerHTML='<span class="f-tip">⚠️ '+fEsc(e.message)+'</span>'; }
}
/* 表单在库存 Tab 里：点「+ 添加」自动切过去再展开 */
(function(){ var orig=PanelApp.showForm; PanelApp.showForm=function(){ fSwitchTab('stock'); orig.apply(PanelApp,arguments); }; })();
/* ── #1015 拍照识菜入库 ── */
async function fPickPhoto(input){
  var file=input.files && input.files[0];
  input.value='';
  if(!file) return;
  var tip=document.getElementById('fScanTip');
  tip.textContent='⏳ 识别中…';
  try{
    var b64=await new Promise(function(res,rej){
      var rd=new FileReader();
      rd.onload=function(){ res(rd.result) }; rd.onerror=rej; rd.readAsDataURL(file);
    });
    var small=await new Promise(function(res){
      var img=new Image();
      img.onload=function(){
        var sc=Math.min(1, 1024/Math.max(img.width,img.height));
        var cv=document.createElement('canvas');
        cv.width=Math.round(img.width*sc); cv.height=Math.round(img.height*sc);
        cv.getContext('2d').drawImage(img,0,0,cv.width,cv.height);
        res(cv.toDataURL('image/jpeg',0.75));
      };
      img.onerror=function(){ res(b64) };
      img.src=b64;
    });
    var r=await fetch('/api/panels/fridge/scan',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image:small})}).then(function(x){return x.json()});
    if(!r.success){ tip.textContent='⚠️ '+r.error; return; }
    tip.textContent='认出 '+r.records.length+' 样，检查一下再入库（用 '+r.model+' 识别）';
    fRenderScan(r.records, r.alerts);
  }catch(e){ tip.textContent='⚠️ 识别失败：'+e.message; }
}
function fRenderScan(records,alerts){
  window.fScanRecords=records;
  var box=document.getElementById('scanResults');
  /* ★ 2026-10-11 #1073 入库健康预警：过敏红条 / 忌口·易上火黄条（本地比对） */
  var al=(alerts||[]).map(function(a){
    var d=(a.level==='danger');
    return '<div style="border-radius:8px;padding:8px 10px;margin-bottom:6px;font-size:12px;line-height:1.6;'
      +(d?'background:rgba(248,81,73,.12);border:1px solid #f85149;color:#c0392b'
         :'background:rgba(210,153,34,.1);border:1px solid #d29922;color:#8a6d1a')+'">'
      +(d?'⚠️':'💡')+' <b>'+fEsc(a.kind||'过敏')+'提醒：</b>'+fEsc(a.member||'家人')+'「<b>'+fEsc(a.term||a.allergy||'')+'</b>」，这批食材里有：'+fEsc(a.hit_in||'')+'</div>';
  }).join('');
  box.innerHTML='<div class="f-card"><h3>📸 识别结果（勾选要入库的）'
    +'<button class="panel-btn panel-btn-primary" style="float:right;font-size:12px" onclick="fScanSaveAll(this)">✅ 勾选的入库</button></h3>'
    +al
    + records.map(function(r,i){
      return '<div style="border:1px solid #EEF2F8;border-radius:10px;padding:10px;margin-bottom:8px" data-i="'+i+'">'
        +'<label style="display:flex;align-items:center;gap:7px;margin-bottom:8px;font-size:13px"><input type="checkbox" class="f-scan-ck" checked><b>'+fEsc(r.名称)+'</b></label>'
        +'<div class="f-grid">'
        +'<div><label>名称</label><input class="f-scan-name" value="'+fEsc(r.名称)+'"></div>'
        +'<div><label>分区</label><select class="f-scan-zone">'+F_ZONES.map(function(z){return '<option'+(z===r.分区?' selected':'')+'>'+z+'</option>'}).join('')+'</select></div>'
        +'<div><label>数量</label><input class="f-scan-qty" value="'+fEsc(r.数量||'')+'"></div>'
        +'<div><label>单位</label><input class="f-scan-unit" value="'+fEsc(r.数量单位||'')+'"></div>'
        +'<div><label>到期日</label><input type="date" class="f-scan-exp" value="'+fEsc(r.到期日||'')+'"></div>'
        +'</div>'
        /* ★ #1035 到期日来源标注：只展示不入库（fScanSaveAll 不读这个字段） */
        +(r['到期日说明']?'<div style="font-size:11px;color:#8A93A6;margin-top:4px">💡 '+fEsc(r['到期日说明'])+'</div>':'')
        +'</div>';
    }).join('')+'</div>';
}
async function fScanSaveAll(btn){
  btn.disabled=true;
  var n=0;
  var cards=document.querySelectorAll('#scanResults [data-i]');
  for(var ci=0;ci<cards.length;ci++){
    var card=cards[ci];
    if(!card.querySelector('.f-scan-ck').checked) continue;
    var i=parseInt(card.dataset.i);
    var rec={名称:card.querySelector('.f-scan-name').value.trim()||('食材'+(i+1)),
             分区:card.querySelector('.f-scan-zone').value, 状态:'在存'};
    var q=card.querySelector('.f-scan-qty').value.trim(); if(q) rec['数量']=q;
    var u=card.querySelector('.f-scan-unit').value.trim(); if(u) rec['数量单位']=u;
    var e=card.querySelector('.f-scan-exp').value; if(e) rec['到期日']=e;
    try{ await HarvenPanel.save(PanelApp.panelId,{data:rec}); n++; }catch(err){ console.error(err); }
  }
  document.getElementById('scanResults').innerHTML='';
  document.getElementById('fScanTip').textContent='✅ 已入库 '+n+' 条，库存列表已刷新';
  PanelApp.loadData();
  loadFreshBoard();
  btn.disabled=false;
}
</script>''',
        }

    # ═══════════════ ★ #1058 物品存放位置档案骨架（home_items/item_storage 前缀） ═══════════════
    # 复刻冰箱模式：骨架在生成器里 = _sync_generated_panel 重启再生成自带，重启不丢。
    # 四 Tab：台账（主表格+首次引导）/ 位置档案（树+格位模板+别名）/ 换季挪仓（多选批量）/ 照片归档（电脑标注台）。

    def _storage_tabs_html(self, panel_id: str) -> dict:
        return {
            'tabs': '''<style>
.s-tabs{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.s-tab{border:1px solid #E5EAF2;background:#fff;color:#3A4453;border-radius:20px;padding:7px 18px;font-size:13px;cursor:pointer}
.s-tab.on{background:#5B87D8;border-color:#5B87D8;color:#fff;font-weight:600}
.s-card{background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:14px;margin-bottom:14px}
.s-card h3{font-size:13px;color:#3A4453;margin:0 0 10px}
.s-tip{font-size:12px;color:#9AA3B0}
.s-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.s-grid label{font-size:11px;color:#9AA3B0;display:block;margin-bottom:3px}
.s-grid input,.s-grid select{width:100%;box-sizing:border-box;border:1px solid #E5EAF2;border-radius:8px;padding:7px 9px;font-size:12px}
.s-ckline{display:flex;align-items:center;gap:8px;padding:6px 4px;border-bottom:1px dashed #EEF2F8;font-size:12.5px}
.s-tree{font-size:12.5px;line-height:2}
.s-room{font-weight:600;color:#3A4453}
.s-cont{color:#5B87D8;margin-left:16px}
.s-slot{color:#9AA3B0;margin-left:36px}
.s-nodebtn{border:none;background:none;color:#C0C7D2;cursor:pointer;font-size:11px;padding:0 4px}
.s-nodebtn:hover{color:#E05B5B}
.s-photogrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:8px}
.s-photogrid img{width:100%;height:80px;object-fit:cover;border-radius:8px;cursor:pointer;border:2px solid transparent}
.s-photogrid img.on{border-color:#5B87D8}
@media (max-width:600px){.s-grid{grid-template-columns:1fr}}
</style>
<div class="s-tabs">
  <button class="s-tab on" data-tab="Ledger" onclick="sSwitchTab('Ledger')">📚 台账</button>
  <button class="s-tab" data-tab="Places" onclick="sSwitchTab('Places')">🏠 位置档案</button>
  <button class="s-tab" data-tab="Season" onclick="sSwitchTab('Season')">🔄 换季挪仓</button>
  <button class="s-tab" data-tab="Photos" onclick="sSwitchTab('Photos')">🖼 照片归档</button>
</div>
<div id="tabLedger">
  <div id="sGuide" style="display:none;background:rgba(91,135,216,0.06);border:1px solid #C6D6F5;border-radius:12px;padding:14px;margin-bottom:14px">
    <h3 style="font-size:13px;color:#5B87D8;margin:0 0 8px">👋 第一次用？先花一分钟把家里安排明白 <span class="s-tip">（跳过也能直接记，但建好档案后「在哪/收哪/换季」一问就答）</span></h3>
    <div id="sGuideStep1">
      <div style="font-size:12.5px;margin-bottom:6px">第 1 步：你有几套房子要管？</div>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="sGuideProp" value="家" style="border:1px solid #E5EAF2;border-radius:8px;padding:7px 9px;font-size:12px;width:160px" placeholder="这套房子叫什么，如：家 / 老家">
        <button class="panel-btn panel-btn-primary" style="font-size:12px" onclick="sGuideToStep2()">下一步</button>
      </div>
      <div class="s-tip" style="margin-top:6px">有多套房就都建上（回这里再走一遍、换个名字即可），找东西时说「老家的梯子」就能分开找</div>
    </div>
    <div id="sGuideStep2" style="display:none">
      <div style="font-size:12.5px;margin-bottom:6px">第 2 步：这套房子（<b id="sGuidePropName">家</b>）有哪些房间？勾一下，没有的自己填</div>
      <div id="sGuideRooms" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px"></div>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="sGuideCustomRoom" style="border:1px solid #E5EAF2;border-radius:8px;padding:7px 9px;font-size:12px;width:160px" placeholder="其他房间名，如：储物间">
        <button class="panel-btn" style="font-size:12px" onclick="sGuideAddCustom()">＋ 加上</button>
        <button class="panel-btn panel-btn-primary" style="font-size:12px" onclick="sGuideFinish()">建好了，开始用</button>
      </div>
    </div>
    <div id="sGuideStep3" style="display:none">
      <div style="font-size:12.5px;line-height:1.9">✅ 房间建好了！<b>先记 20%</b>：证件、药品、工具、换季衣物这些「一找不到就着急」的东西优先记，低频杂物不用急着全录。<br>
      记的时候位置选到柜子和格位（比如「左门第2层」），以后问「羽绒服在哪」直接给答案。<br>
      柜子格子不用一个个建：去「🏠 位置档案」选柜子类型一键套模板（衣柜=左门1~4层+右门1~4层）。</div>
      <button class="panel-btn panel-btn-primary" style="font-size:12px;margin-top:8px" onclick="document.getElementById('sGuide').style.display='none'">知道了</button>
    </div>
  </div>
''',
            'mid': '''</div><!-- /tabLedger -->
<div id="tabPlaces" style="display:none">
  <div class="s-card">
    <h3>🏠 加房间 / 加柜子 <span class="s-tip">柜子选类型一键套格位模板，层数随时可改</span></h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
      <div><label class="s-tip" style="display:block">房子</label>
        <select id="sTreeProp" class="panel-select" style="width:120px;font-size:12px" onchange="sRenderTree()"></select></div>
      <div><label class="s-tip" style="display:block">加房间</label>
        <input id="sNewRoom" style="border:1px solid #E5EAF2;border-radius:8px;padding:7px 9px;font-size:12px;width:110px" placeholder="如：书房">
      </div>
      <button class="panel-btn" style="font-size:12px" onclick="sAddRoom()">＋ 房间</button>
      <div><label class="s-tip" style="display:block">柜子类型</label>
        <select id="sContType" class="panel-select" style="width:150px;font-size:12px"></select></div>
      <div><label class="s-tip" style="display:block">柜子叫什么</label>
        <input id="sContName" style="border:1px solid #E5EAF2;border-radius:8px;padding:7px 9px;font-size:12px;width:110px" placeholder="如：大衣柜"></div>
      <div><label class="s-tip" style="display:block">放哪个房间</label>
        <select id="sContRoom" class="panel-select" style="width:110px;font-size:12px"></select></div>
      <div><label class="s-tip" style="display:block">层数</label>
        <input id="sContLevels" type="number" min="1" max="10" value="4" style="border:1px solid #E5EAF2;border-radius:8px;padding:7px 9px;font-size:12px;width:60px"></div>
      <button class="panel-btn panel-btn-primary" style="font-size:12px" onclick="sAddContainer()">套模板建柜子</button>
    </div>
  </div>
  <div class="s-card">
    <h3>🗺 位置总览 <span class="s-tip">点「别名」给柜子/格位加俗称（衣橱=衣柜），怎么说都能找到；悬停节点可删</span></h3>
    <div id="sTreeView" class="s-tree">加载中…</div>
  </div>
</div>
<div id="tabSeason" style="display:none">
  <div class="s-card">
    <h3>🔄 换季挪仓 <span class="s-tip">换季把衣服被子整体搬家：筛出来 → 勾选 → 选新位置 → 确认一键挪</span></h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:10px">
      <div><label class="s-tip" style="display:block">季节</label>
        <select id="sSeasonPick" class="panel-select" style="width:100px;font-size:12px">
          <option value="">全部</option><option>春秋</option><option>夏季</option><option>冬季</option>
        </select></div>
      <div><label class="s-tip" style="display:block">分类</label>
        <select id="sCatPick" class="panel-select" style="width:150px;font-size:12px"><option value="">全部</option></select></div>
      <button class="panel-btn" style="font-size:12px" onclick="sLoadSeason()">🔄 刷出物品</button>
    </div>
    <div id="sSeasonList" style="max-height:260px;overflow:auto"></div>
  </div>
  <div class="s-card" id="sMoveCard" style="display:none">
    <h3>📦 挪到哪</h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
      <div><label class="s-tip" style="display:block">房间</label>
        <select id="sMvRoom" class="panel-select" style="width:120px;font-size:12px" onchange="sMvFillCont()"></select></div>
      <div><label class="s-tip" style="display:block">柜子/家具</label>
        <select id="sMvCont" class="panel-select" style="width:140px;font-size:12px" onchange="sMvFillSlot()"></select></div>
      <div><label class="s-tip" style="display:block">格位</label>
        <select id="sMvSlot" class="panel-select" style="width:140px;font-size:12px"></select></div>
      <button class="panel-btn panel-btn-primary" style="font-size:12px" onclick="sDoMove(this)">挪仓（<span id="sMoveN">0</span> 件）</button>
    </div>
    <div id="sMoveConfirm" style="display:none;margin-top:10px;background:rgba(210,153,34,0.08);border:1px solid #E8D5A3;border-radius:10px;padding:10px;font-size:12px"></div>
  </div>
</div>
<div id="tabPhotos" style="display:none">
  <div class="s-card">
    <h3>🖼 待归档照片 <span class="s-tip">在电脑上把拍好的物品照片一次性标注入库：点照片 → 填名字选位置 → 回车保存自动跳下一张，顺手就归完</span></h3>
    <div id="sPhotoGrid" class="s-photogrid"></div>
    <div id="sPhotoEmpty" style="display:none;color:#9AA3B0;font-size:12.5px;padding:14px 0">🎉 没有待归档的照片了。手机上拍照存进「物品存放」相册的照片会出现在这里。</div>
  </div>
  <div class="s-card" id="sPhotoForm" style="display:none">
    <h3 id="sPhotoTitle">标注照片</h3>
    <div style="display:flex;gap:14px;flex-wrap:wrap">
      <img id="sPhotoPrev" style="width:150px;height:150px;object-fit:cover;border-radius:10px;border:1px solid #E5EAF2">
      <div style="flex:1;min-width:240px">
        <div class="s-grid">
          <div><label>物品名称（必填）</label><input id="sPName" onkeydown="if(event.key==='Enter')sSavePhoto()"></div>
          <div><label>分类</label><select id="sPCat"></select></div>
          <div><label>房间</label><select id="sPRoom" onchange="sPFillCont()"></select></div>
          <div><label>柜子/家具</label><select id="sPCont" onchange="sPFillSlot()"></select></div>
          <div><label>格位</label><select id="sPSlot"></select></div>
          <div><label>适用季节</label><select id="sPSeason"><option value="">不填</option><option>全年</option><option>春秋</option><option>夏季</option><option>冬季</option></select></div>
        </div>
        <div style="margin-top:8px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <label style="font-size:12px;display:flex;gap:4px;align-items:center"><input type="checkbox" id="sPKeep" checked> 沿用这条位置到下一张</label>
          <button class="panel-btn" style="font-size:12px" onclick="sVisionPre(this)">✨ AI 认一下</button>
          <button class="panel-btn panel-btn-primary" style="font-size:12px" onclick="sSavePhoto()">保存并下一张（回车）</button>
        </div>
        <div id="sPhotoTip" class="s-tip" style="margin-top:6px"></div>
      </div>
    </div>
  </div>
</div>''',
            'scripts': '''<script>
/* ══ #1058 物品存放四 Tab（随生成器再生成，重启不丢） ══ */
var S_PID = 'PANEL_ID';
var S_TREE = [];            /* 位置树（storage-places/tree） */
var S_CATS = ['衣物鞋帽·当季','衣物鞋帽·换季收纳','电子产品·线材','厨房用品','清洁用品','工具五金','药品保健','文具书籍','玩具','运动户外','汽车用品','箱包收纳','纪念收藏','其他'];
var S_LAST_POS = null;      /* 照片归档「沿用上一条位置」 */
function sEsc(s){return String(s||'').replace(/</g,'&lt;');}
function sSwitchTab(t){
  document.querySelectorAll('.s-tab').forEach(function(b){ b.classList.toggle('on', b.dataset.tab===t); });
  ['Ledger','Places','Season','Photos'].forEach(function(n){
    var el=document.getElementById('tab'+n); if(el) el.style.display = (n===t)?'':'none';
  });
  if(t==='Places') sLoadTree();
  if(t==='Season'){ sLoadSeason(); }
  if(t==='Photos') sLoadPhotos();
}
/* ── 位置树 ── */
async function sLoadTree(){
  try{
    var r=await fetch('/api/panels/storage-places/tree').then(function(x){return x.json()});
    S_TREE=r.tree||[];
    /* 房产下拉 */
    var props=(r.properties||['家']);
    var sel=document.getElementById('sTreeProp');
    sel.innerHTML=props.map(function(p){return '<option>'+sEsc(p)+'</option>'}).join('');
    /* 首次引导：没建过任何档案才显示 */
    document.getElementById('sGuide').style.display = S_TREE.length ? 'none' : '';
    sRenderTree();
    sFillTreeSelects();
  }catch(e){ console.error('位置树加载失败:', e); }
}
function sTreeOfProp(prop){
  for(var i=0;i<S_TREE.length;i++) if(S_TREE[i].name===prop) return S_TREE[i];
  return {name:prop, rooms:[]};
}
function sRenderTree(){
  var prop=document.getElementById('sTreeProp').value||'家';
  var t=sTreeOfProp(prop);
  var box=document.getElementById('sTreeView');
  /* 容器下拉/挪仓下拉同步刷 */
  if(!t.rooms.length){ box.innerHTML='<span class="s-tip">这个房子还没有房间。上面「加房间」先建一个，或者回「台账」页走一遍首次引导。</span>'; sFillTreeSelects(); return; }
  var html='';
  t.rooms.forEach(function(room){
    html+='<div class="s-room">📍 '+sEsc(room.name)
      +'<button class="s-nodebtn" title="删除房间" onclick="sDelPlace(event,'+sFirstId(room)+')">✕</button></div>';
    (room.containers||[]).forEach(function(c){
      html+='<div class="s-cont">🗄 '+sEsc(c.name)
        +' <button class="s-nodebtn" onclick="sAlias('+c.id+',\\''+sEsc(c.name).replace(/'/g,'')+'\\')">别名</button>'
        +' <button class="s-nodebtn" onclick="sDelPlace(event,'+c.id+')">✕</button></div>';
      (c.slots||[]).forEach(function(s){
        html+='<div class="s-slot">└ '+sEsc(s.name)
          +' <button class="s-nodebtn" onclick="sAlias('+s.id+',\\''+sEsc(s.name).replace(/'/g,'')+'\\')">别名</button>'
          +' <button class="s-nodebtn" onclick="sDelPlace(event,'+s.id+')">✕</button></div>';
      });
    });
    (room.loose_slots||[]).forEach(function(s){
      html+='<div class="s-slot">└ '+sEsc(s.name)+' <button class="s-nodebtn" onclick="sDelPlace(event,'+s.id+')">✕</button></div>';
    });
  });
  box.innerHTML=html;
  sFillTreeSelects();
}
function sFirstId(room){
  if(room.containers&&room.containers.length) return room.containers[0].id;
  if(room.loose_slots&&room.loose_slots.length) return room.loose_slots[0].id;
  return 'null';
}
async function sAddRoom(){
  var room=document.getElementById('sNewRoom').value.trim();
  if(!room){ return; }
  var prop=document.getElementById('sTreeProp').value||'家';
  await fetch('/api/panels/storage-places',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({property_name:prop, room:room})});
  document.getElementById('sNewRoom').value='';
  sLoadTree();
}
async function sAddContainer(){
  var name=document.getElementById('sContName').value.trim();
  var type=document.getElementById('sContType').value;
  var room=document.getElementById('sContRoom').value;
  var lv=parseInt(document.getElementById('sContLevels').value)||0;
  if(!name||!room){ alert('先填柜子名字、选好放哪个房间'); return; }
  var r=await fetch('/api/panels/storage-places/template',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({property_name:document.getElementById('sTreeProp').value||'家', room:room, container_name:name, container_type:type, levels:lv})}).then(function(x){return x.json()});
  if(r.success){ document.getElementById('sContName').value=''; sLoadTree(); }
  else alert(r.message||'没建成，再试一次');
}
async function sDelPlace(ev,id){
  ev.stopPropagation();
  if(!confirm('删掉这个位置？物品记录不会删，只是位置档案里不再显示。')) return;
  await fetch('/api/panels/storage-places/'+id,{method:'DELETE'});
  sLoadTree();
}
async function sAlias(id,name){
  var a=prompt('给「'+name+'」加俗称（逗号分隔，如：衣橱,大衣柜）。留空=清除别名：', '');
  if(a===null) return;
  await fetch('/api/panels/storage-places/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({aliases:a.split(/[,,]/).map(function(s){return s.trim()}).filter(Boolean)})});
  sLoadTree();
}
/* ── 首次引导 ── */
var S_GUIDE_ROOMS=['客厅','主卧','次卧','儿童房','书房','厨房','卫生间','阳台','玄关','车库','储物间'];
function sGuideToStep2(){
  var prop=document.getElementById('sGuideProp').value.trim()||'家';
  document.getElementById('sGuidePropName').textContent=prop;
  document.getElementById('sGuideRooms').innerHTML=S_GUIDE_ROOMS.map(function(r,i){
    var on=(i<4)?' checked':'';  /* 前四个默认勾上 */
    return '<label class="f-chip" style="margin:0"><input type="checkbox" class="s-gr" value="'+r+'"'+on+' style="margin-right:4px">'+r+'</label>';
  }).join('');
  document.getElementById('sGuideStep1').style.display='none';
  document.getElementById('sGuideStep2').style.display='';
}
function sGuideAddCustom(){
  var v=document.getElementById('sGuideCustomRoom').value.trim();
  if(!v) return;
  var d=document.createElement('label');
  d.className='f-chip'; d.style.margin='0';
  d.innerHTML='<input type="checkbox" class="s-gr" value="'+sEsc(v)+'" checked style="margin-right:4px">'+sEsc(v);
  document.getElementById('sGuideRooms').appendChild(d);
  document.getElementById('sGuideCustomRoom').value='';
}
async function sGuideFinish(){
  var prop=document.getElementById('sGuideProp').value.trim()||'家';
  var rooms=[].map.call(document.querySelectorAll('.s-gr:checked'),function(x){return x.value});
  if(!rooms.length){ alert('至少勾一个房间'); return; }
  for(var i=0;i<rooms.length;i++){
    await fetch('/api/panels/storage-places',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({property_name:prop, room:rooms[i]})});
  }
  document.getElementById('sGuideStep2').style.display='none';
  document.getElementById('sGuideStep3').style.display='';
  sLoadTree();
}
/* ── 三级联动填充（挪仓 + 照片归档 + 录入表单共用树数据） ── */
function sFillOpts(sel, arr, keepBlank){
  if(!sel) return;
  var cur=sel.value;
  sel.innerHTML=(keepBlank?'<option value="">（不填）</option>':'')+arr.map(function(x){return '<option>'+sEsc(x)+'</option>'}).join('');
  if(arr.indexOf(cur)>=0) sel.value=cur;
}
function sRoomsOf(prop){ return sTreeOfProp(prop||'家').rooms||[]; }
function sContsOf(roomName){
  var rooms=sRoomsOf(document.getElementById('sTreeProp')?document.getElementById('sTreeProp').value:'家');
  for(var i=0;i<rooms.length;i++) if(rooms[i].name===roomName) return rooms[i].containers||[];
  return [];
}
function sSlotsOf(roomName,contName){
  var cs=sContsOf(roomName);
  for(var i=0;i<cs.length;i++) if(cs[i].name===contName) return (cs[i].slots||[]).map(function(s){return s.name});
  return [];
}
function sFillTreeSelects(){
  var prop=(document.getElementById('sTreeProp')||{}).value||'家';
  var rooms=sRoomsOf(prop).map(function(r){return r.name});
  ['sContRoom','sMvRoom','sPRoom'].forEach(function(id){ sFillOpts(document.getElementById(id), rooms); });
  /* 柜子类型下拉（一次） */
  var ct=document.getElementById('sContType');
  if(ct && !ct.options.length){
    fetch('/api/panels/storage-places/templates').then(function(x){return x.json()}).then(function(r){
      ct.innerHTML=(r.templates||[]).map(function(t){return '<option value="'+sEsc(t.type)+'">'+sEsc(t.label)+'</option>'}).join('');
    });
  }
  /* 分类下拉（换季筛选 + 照片归档） */
  ['sCatPick','sPCat'].forEach(function(id){ var el=document.getElementById(id); if(el&&!el.options.length) sFillOpts(el, S_CATS, id==='sCatPick'); });
  sMvFillCont(); sPFillCont();
}
function sMvFillCont(){
  var room=(document.getElementById('sMvRoom')||{}).value||'';
  sFillOpts(document.getElementById('sMvCont'), sContsOf(room).map(function(c){return c.name}), true);
  sMvFillSlot();
}
function sMvFillSlot(){
  var room=(document.getElementById('sMvRoom')||{}).value||'';
  var cont=(document.getElementById('sMvCont')||{}).value||'';
  sFillOpts(document.getElementById('sMvSlot'), cont?sSlotsOf(room,cont):[], true);
}
function sPFillCont(){
  var room=(document.getElementById('sPRoom')||{}).value||'';
  sFillOpts(document.getElementById('sPCont'), sContsOf(room).map(function(c){return c.name}), true);
  sPFillSlot();
}
function sPFillSlot(){
  var room=(document.getElementById('sPRoom')||{}).value||'';
  var cont=(document.getElementById('sPCont')||{}).value||'';
  sFillOpts(document.getElementById('sPSlot'), cont?sSlotsOf(room,cont):[], true);
}
/* ── 换季挪仓 ── */
var S_SEASON_ROWS=[];
async function sLoadSeason(){
  try{
    var r=await fetch('/api/panels/'+S_PID+'/data').then(function(x){return x.json()});
    var rows=r.rows||r.records||r.data||[];
    var season=(document.getElementById('sSeasonPick')||{}).value||'';
    var cat=(document.getElementById('sCatPick')||{}).value||'';
    S_SEASON_ROWS=rows.filter(function(x){
      if(season && String(x['适用季节']||'').indexOf(season)<0) return false;
      if(cat && String(x['分类']||'')!==cat) return false;
      return true;
    });
    var box=document.getElementById('sSeasonList');
    if(!S_SEASON_ROWS.length){
      box.innerHTML='<div class="s-tip" style="padding:10px 0">没有匹配的物品。录入时选上「适用季节」，换季一筛就出来。</div>';
      document.getElementById('sMoveCard').style.display='none'; return;
    }
    box.innerHTML=S_SEASON_ROWS.map(function(x,i){
      var pos=[x['位置·房间'],x['位置·柜子家具'],x['位置·盒层']].filter(Boolean).join(' → ')||x['存放位置']||'（没填位置）';
      return '<div class="s-ckline"><input type="checkbox" class="s-mvck" data-i="'+i+'">'
        +'<span style="flex:1">'+sEsc(x['物品名称']||'（未命名）')+' <span class="s-tip">现放：'+sEsc(pos)+'</span></span></div>';
    }).join('');
    box.querySelectorAll('.s-mvck').forEach(function(ck){ ck.addEventListener('change', sMoveCount); });
    document.getElementById('sMoveCard').style.display='';
    sMoveCount();
  }catch(e){ console.error('换季筛失失败:', e); }
}
function sMoveCount(){
  var n=document.querySelectorAll('.s-mvck:checked').length;
  document.getElementById('sMoveN').textContent=n;
}
async function sDoMove(btn){
  var idx=[].map.call(document.querySelectorAll('.s-mvck:checked'),function(x){return parseInt(x.dataset.i)});
  if(!idx.length){ alert('先勾上要挪的物品'); return; }
  var room=(document.getElementById('sMvRoom')||{}).value||'';
  var cont=(document.getElementById('sMvCont')||{}).value||'';
  var slot=(document.getElementById('sMvSlot')||{}).value||'';
  if(!room){ alert('至少选到房间'); return; }
  /* 确认卡 */
  var names=idx.map(function(i){return S_SEASON_ROWS[i]['物品名称']||'（未命名）'});
  var newPos=[room,cont,slot].filter(Boolean).join(' → ');
  var cf=document.getElementById('sMoveConfirm');
  cf.style.display='block';
  cf.innerHTML='将把 <b>'+idx.length+'</b> 件挪到 <b>'+sEsc(newPos)+'</b>：<br>'+names.map(function(n){return '· '+sEsc(n)}).join('<br>')
    +'<br><button class="panel-btn panel-btn-primary" style="font-size:12px;margin-top:8px" onclick="sMoveApply(this)">确认挪仓</button>'
    +'<button class="panel-btn" style="font-size:12px;margin-top:8px" onclick="this.parentNode.style.display=\\'none\\'">取消</button>';
}
async function sMoveApply(btn){
  btn.disabled=true;
  var idx=[].map.call(document.querySelectorAll('.s-mvck:checked'),function(x){return parseInt(x.dataset.i)});
  var room=(document.getElementById('sMvRoom')||{}).value||'';
  var cont=(document.getElementById('sMvCont')||{}).value||'';
  var slot=(document.getElementById('sMvSlot')||{}).value||'';
  var ok=0;
  for(var i=0;i<idx.length;i++){
    var row=S_SEASON_ROWS[idx[i]];
    var data={'位置·房间':room};
    if(cont) data['位置·柜子家具']=cont; else data['位置·柜子家具']='';
    if(slot) data['位置·盒层']=slot; else data['位置·盒层']='';
    try{ await fetch('/api/panels/'+S_PID+'/data/'+row.id,{method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({data:data})}); ok++; }catch(e){ console.error(e); }
  }
  document.getElementById('sMoveConfirm').style.display='none';
  document.getElementById('sMoveCard').style.display='none';
  sLoadSeason(); PanelApp.loadData();
  btn.disabled=false;
}
/* ── 照片归档（电脑标注台） ── */
var S_PHOTOS=[], S_PHOTO_IDX=-1;
async function sLoadPhotos(){
  try{
    var r=await fetch('/api/panels/'+S_PID+'/images?unlinked=1').then(function(x){return x.json()});
    S_PHOTOS=r.images||[];
    S_PHOTO_IDX=-1;
    var grid=document.getElementById('sPhotoGrid');
    grid.innerHTML=S_PHOTOS.map(function(p,i){
      return '<img src="/api/panels/image/'+encodeURIComponent(p.filename)+'" data-i="'+i+'" title="'+sEsc(p.filename)+'" onclick="sPickPhoto('+i+')">';
    }).join('');
    document.getElementById('sPhotoEmpty').style.display=S_PHOTOS.length?'none':'';
    document.getElementById('sPhotoForm').style.display='none';
  }catch(e){ console.error('照片加载失败:', e); }
}
async function sPickPhoto(i){
  S_PHOTO_IDX=i;
  var p=S_PHOTOS[i];
  var grid=document.getElementById('sPhotoGrid');
  grid.querySelectorAll('img').forEach(function(im){ im.classList.toggle('on', parseInt(im.dataset.i)===i); });
  document.getElementById('sPhotoForm').style.display='';
  document.getElementById('sPhotoPrev').src='/api/panels/image/'+encodeURIComponent(p.filename);
  document.getElementById('sPhotoTitle').textContent='标注照片（'+(i+1)+'/'+S_PHOTOS.length+'）· '+p.filename.slice(0,24);
  document.getElementById('sPName').value='';
  document.getElementById('sPhotoTip').textContent='';
  /* 沿用上一条位置 */
  if(S_LAST_POS && (document.getElementById('sPKeep')||{}).checked){
    document.getElementById('sPRoom').value=S_LAST_POS.room||'';
    sPFillCont();
    document.getElementById('sPCont').value=S_LAST_POS.cont||'';
    sPFillSlot();
    document.getElementById('sPSlot').value=S_LAST_POS.slot||'';
  }
  document.getElementById('sPName').focus();
}
async function sVisionPre(btn){
  if(S_PHOTO_IDX<0) return;
  btn.disabled=true; btn.textContent='⏳ 识别中…';
  try{
    /* 图池照片 → 转 b64 → 走现有 vision-fill（本地阵营识别，照片不出门） */
    var blob=await fetch('/api/panels/image/'+encodeURIComponent(S_PHOTOS[S_PHOTO_IDX].filename)).then(function(x){return x.blob()});
    var b64=await new Promise(function(res){ var fr=new FileReader();
      fr.onload=function(){ res(String(fr.result).split(',')[1]); }; fr.readAsDataURL(blob); });
    var r=await fetch('/api/panels/'+S_PID+'/vision-fill',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image:b64})}).then(function(x){return x.json()});
    if(r.success && r.values){
      var v=r.values;
      if(v['分类'] && S_CATS.indexOf(v['分类'])>=0) document.getElementById('sPCat').value=v['分类'];
      if(v['适用季节']) document.getElementById('sPSeason').value=v['适用季节'];
      document.getElementById('sPhotoTip').textContent='✅ AI 帮忙填了能认出来的（分类/季节），名字和位置自己确认下';
    } else {
      document.getElementById('sPhotoTip').textContent='没认出能预填的，手填就好';
    }
  }catch(e){ document.getElementById('sPhotoTip').textContent='识别失败：'+e.message; }
  btn.disabled=false; btn.textContent='✨ AI 认一下';
}
async function sSavePhoto(){
  if(S_PHOTO_IDX<0) return;
  var name=document.getElementById('sPName').value.trim();
  if(!name){ document.getElementById('sPhotoTip').textContent='先填物品名称（必填）'; return; }
  var room=(document.getElementById('sPRoom')||{}).value||'';
  var cont=(document.getElementById('sPCont')||{}).value||'';
  var slot=(document.getElementById('sPSlot')||{}).value||'';
  var rec={'物品名称':name};
  var cat=(document.getElementById('sPCat')||{}).value||''; if(cat) rec['分类']=cat;
  var season=(document.getElementById('sPSeason')||{}).value||''; if(season) rec['适用季节']=season;
  if(room) rec['位置·房间']=room;
  if(cont) rec['位置·柜子家具']=cont;
  if(slot) rec['位置·盒层']=slot;
  try{
    var r=await fetch('/api/panels/'+S_PID+'/data',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({data:rec})}).then(function(x){return x.json()});
    var rid=r.record_id||r.id||(r.record&&r.record.id);
    if(rid){ /* 图池照片挂到新记录 */
      await fetch('/api/panels/'+S_PID+'/images/attach',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({filename:S_PHOTOS[S_PHOTO_IDX].filename, record_id:rid})});
    }
    S_LAST_POS={room:room, cont:cont, slot:slot};
    sLoadPhotos(); PanelApp.loadData();
  }catch(e){ document.getElementById('sPhotoTip').textContent='保存失败：'+e.message; }
}
/* ── 录入表单三级联动：包装 PanelApp.showForm，位置三字段换档案驱动下拉 ── */
(function(){
  var _orig=PanelApp.showForm;
  PanelApp.showForm=function(){
    _orig.apply(this, arguments);
    setTimeout(function(){
      try{
        var prop=(document.getElementById('sTreeProp')||{}).value||'家';
        var rooms=sRoomsOf(prop).map(function(r){return r.name});
        var rs=document.getElementById('位置·房间')||document.getElementById('field_位置·房间');
        if(rs && rooms.length){ sFillOpts(rs, rooms.concat(['其他']), true); }
        var cs=document.getElementById('位置·柜子家具')||document.getElementById('field_位置·柜子家具');
        var ss=document.getElementById('位置·盒层')||document.getElementById('field_位置·盒层');
        if(cs){ sFillOpts(cs, sContsOf(rs?rs.value:'').map(function(c){return c.name}), true); }
        if(ss && cs){ sFillOpts(ss, cs.value?sSlotsOf(rs?rs.value:'',cs.value):[], true);
          cs.onchange=function(){ sFillOpts(ss, cs.value?sSlotsOf(rs?rs.value:'',cs.value):[], true); }; }
      }catch(e){}
    }, 60);
  };
})();
sLoadTree();
</script>''',
        }

    def _field_input_html(self, field: dict) -> str:
        """生成单个字段的表单HTML"""
        fname = field.get('name', '')
        flabel = field.get('label', fname)
        ftype = field.get('type', 'text')
        required = 'required' if field.get('required') else ''

        if ftype == 'select':
            options = field.get('options', [])
            opts_html = ''.join(f'<option value="{o}">{o}</option>' for o in options)
            return f'''<div class="panel-field">
      <label>{flabel}</label>
      <select id="field_{fname}" {required}><option value="">请选择</option>{opts_html}</select>
    </div>
'''
        elif ftype == 'textarea':
            return f'''<div class="panel-field">
      <label>{flabel}</label>
      <textarea id="field_{fname}" {required} rows="4"></textarea>
    </div>
'''
        elif ftype == 'date':
            return f'''<div class="panel-field">
      <label>{flabel}</label>
      <input type="date" id="field_{fname}" {required}>
    </div>
'''
        elif ftype == 'time':
            return f'''<div class="panel-field">
      <label>{flabel}</label>
      <input type="time" id="field_{fname}" {required}>
    </div>
'''
        elif ftype == 'number':
            return f'''<div class="panel-field">
      <label>{flabel}</label>
      <input type="number" id="field_{fname}" {required} step="0.01">
    </div>
'''
        elif ftype == 'image':
            # ★ #908 B方案：图片字段 = 选图（前端压缩）+ 预览；保存时走 POST data
            #   自动转存附件库、列里只落文件名（见 panel_routes 照片剥离链）
            # ★ #917 多图：multiple 多选，预览区逐张带 ✕ 删除
            return f'''<div class="panel-field">
      <label>{flabel}（可多选）</label>
      <input type="file" accept="image/*" multiple id="field_{fname}" {required} onchange="PanelApp.pickImage('{fname}')">
      <div class="panel-imgprev" id="prev_{fname}"></div>
    </div>
'''
        else:
            return f'''<div class="panel-field">
      <label>{flabel}</label>
      <input type="text" id="field_{fname}" {required}>
    </div>
'''

    def _generate_panel_css(self) -> str:
        """生成面板通用样式
        ★ 2026-09-18 浅色主题重做：跟随 SPA 主题令牌（底 #FBFCFE / 卡白 / 主 #5B87D8），
        替换旧暗色硬编码（实测实锤：黑底面板与 SPA 浅色页格格不入）"""
        return ''':root {
  --pa-bg: #FBFCFE; --pa-card: #FFFFFF; --pa-soft: #F1F3F7;
  --pa-text: #3A4453; --pa-sub: #9AA3B0; --pa-line: #E5EAF2;
  --pa-primary: #5B87D8; --pa-primary-weak: #C6D6F5; --pa-danger: #E05B5B;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
.panel-body { font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 13px; color: var(--pa-text); background: var(--pa-bg); padding: 20px; }
.panel-container { max-width: 1000px; margin: 0 auto; }
.panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.panel-header h2 { font-size: 18px; font-weight: 600; color: var(--pa-text); }
.panel-btn { padding: 6px 16px; border-radius: 10px; border: 1px solid var(--pa-line); background: #fff; color: var(--pa-text); cursor: pointer; font-size: 13px; }
.panel-btn:hover { border-color: var(--pa-primary); color: var(--pa-primary); }
.panel-btn-primary { background: var(--pa-primary); border-color: var(--pa-primary); color: #fff; }
.panel-btn-primary:hover { opacity: 0.9; color: #fff; }
.panel-btn-danger { color: var(--pa-danger); }
.panel-btn-danger:hover { border-color: var(--pa-danger); color: var(--pa-danger); }
.panel-search { margin-bottom: 12px; }
.panel-search input { width: 100%; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--pa-line); background: #fff; color: var(--pa-text); font-size: 13px; }
.panel-search input:focus { outline: none; border-color: var(--pa-primary); }
.panel-select { width: 100%; padding: 8px 10px; border-radius: 10px; border: 1px solid var(--pa-line); background: #fff; color: var(--pa-text); font-size: 13px; }

.panel-form { background: var(--pa-card); border-radius: 14px; padding: 16px; margin-bottom: 16px; border: 1px solid var(--pa-line); box-shadow: 0 1px 4px rgba(58,68,83,0.06); }
.panel-field { margin-bottom: 10px; }
.panel-field label { display: block; color: var(--pa-sub); font-size: 12px; margin-bottom: 4px; }
.panel-field input, .panel-field select, .panel-field textarea { width: 100%; padding: 8px 10px; border-radius: 10px; border: 1px solid var(--pa-line); background: #fff; color: var(--pa-text); font-size: 13px; font-family: inherit; }
.panel-field input:focus, .panel-field select:focus, .panel-field textarea:focus { outline: none; border-color: var(--pa-primary); }
.panel-field textarea { min-height: 60px; resize: vertical; }
.panel-form-actions { display: flex; gap: 8px; margin-top: 12px; }
/* ★ 2026-09-18 更多字段折叠区（对话/表单分离：核心字段直出，其余收进来） */
.adv-fields { border: 1px dashed var(--pa-line); border-radius: 10px; padding: 10px 14px; margin: 12px 0; background: var(--pa-soft); }
.adv-fields summary { cursor: pointer; color: var(--pa-sub); font-size: 12px; user-select: none; }
.adv-fields[open] summary { margin-bottom: 10px; }

.panel-table-wrap { overflow-x: auto; background: var(--pa-card); border-radius: 14px; border: 1px solid var(--pa-line); }
.panel-table { width: 100%; border-collapse: collapse; }
.panel-table th { text-align: left; padding: 10px 12px; background: var(--pa-soft); color: var(--pa-sub); font-weight: 500; font-size: 12px; border-bottom: 1px solid var(--pa-line); }
.panel-table td { padding: 10px 12px; border-bottom: 1px solid var(--pa-soft); color: var(--pa-text); }
.panel-table tr:hover td { background: #F8FAFD; }
.panel-empty { text-align: center; color: var(--pa-sub); padding: 30px !important; }
.panel-pagination { display: flex; justify-content: center; gap: 8px; margin-top: 16px; }
.panel-pagination button { padding: 4px 12px; border-radius: 8px; border: 1px solid var(--pa-line); background: #fff; color: var(--pa-text); cursor: pointer; font-size: 12px; }
.panel-pagination button:disabled { opacity: 0.4; cursor: default; }
.panel-pagination span { color: var(--pa-sub); font-size: 12px; line-height: 28px; }
/* ★ #908/#909/#914 B方案图片能力（2026-09-27） */
.panel-imgprev { margin-top: 6px; }
.panel-imgprev img { max-width: 120px; max-height: 120px; border-radius: 10px; border: 1px solid var(--pa-line); cursor: pointer; }
.panel-picbadge { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 10px; background: rgba(91,135,216,.12); color: var(--pa-primary); cursor: pointer; margin-right: 6px; vertical-align: middle; }
.panel-picbadge:hover { background: rgba(91,135,216,.22); }
.panel-pic-overlay { position: fixed; inset: 0; background: rgba(20,28,40,.45); z-index: 9999; display: flex; align-items: center; justify-content: center; }
.panel-pic-box { background: #fff; border-radius: 16px; padding: 18px; max-width: 640px; width: 92%; max-height: 84vh; overflow: auto; box-shadow: 0 12px 40px rgba(30,40,60,.25); }
.panel-pic-box h3 { font-size: 15px; margin-bottom: 10px; color: var(--pa-text); }
.panel-pic-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 10px; margin-bottom: 10px; }
.panel-pic-item { position: relative; text-align: center; }
.panel-pic-item img { width: 100%; height: 84px; object-fit: cover; border-radius: 10px; border: 1px solid var(--pa-line); cursor: pointer; background: var(--pa-soft); }
.panel-pic-del { position: absolute; top: 2px; right: 2px; font-size: 10px; padding: 1px 6px; }
.panel-pic-att { margin-top: 4px; font-size: 11px; padding: 2px 8px; width: 100%; }
.panel-pic-empty { grid-column: 1 / -1; text-align: center; color: var(--pa-sub); padding: 20px; font-size: 12px; }
.panel-pic-foot { display: flex; gap: 8px; justify-content: flex-end; }
.panel-pic-pool { border-top: 1px dashed var(--pa-line); margin-top: 8px; padding-top: 10px; font-size: 12px; color: var(--pa-sub); }
/* ★ #1105 关联动态横幅（2026-10-11） */
.cross-banner { margin-bottom: 12px; border: 1px solid var(--pa-line); border-radius: 12px; padding: 10px 14px; background: rgba(91,135,216,.05); }
.cross-banner-title { font-size: 12px; font-weight: 600; color: var(--pa-primary); margin-bottom: 6px; }
.cross-banner-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.cross-chip { display: inline-block; font-size: 12px; color: var(--pa-text); background: var(--pa-card); border: 1px solid var(--pa-line); border-radius: 14px; padding: 3px 12px; cursor: pointer; line-height: 1.6; }
.cross-chip:hover { border-color: var(--pa-primary); color: var(--pa-primary); }
'''

    def _generate_panel_js(self, panel_id: str, fields: list, assist_hint: str = '') -> str:
        """生成面板JS逻辑（支持多行文本/时间/图片+卡片视图）"""
        # ★ 2026-09-19 易用性②：空面板示例话术——优先用定制助手提示（本身就是口语例子），
        # 否则按字段拼一句通用示例，空态时教用户「不用填表，直接说也行」
        _ex = assist_hint.strip()
        if not _ex:
            _text_f = next((f.get('name') for f in fields if isinstance(f, dict) and f.get('type') == 'text' and f.get('name')), '')
            _num_f = next((f.get('name') for f in fields if isinstance(f, dict) and f.get('type') == 'number' and f.get('name')), '')
            _seg = []
            if _text_f: _seg.append(f'{_text_f}=买菜')
            if _num_f: _seg.append(f'{_num_f}=35')
            _ex = '，'.join(_seg) if _seg else '今天记一条'
        return f'''// {panel_id} panel logic
const PanelApp = {{
  panelId: '{panel_id}',
  fields: {json.dumps(fields, ensure_ascii=False)},
  members: [],
  editingId: null,
  emptyExample: {json.dumps(_ex, ensure_ascii=False)},  // ★ 2026-09-19 空面板示例话术
  viewMode: 'card',  // ★ 2026-08-08: table | card（★ 2026-09-18 卡片改为默认，多列表格不好读）
  imgMap: {{}},      // ★ #909 附件库图索引：record_id -> [图片]
  unlinked: [],      // ★ #914 未关联图池（发过图没记上账的）
  imgPicks: {{}},    // ★ #908 表单里刚选的图（field名 -> 压缩后dataURL）

  init(id, flds) {{
    this.panelId = id;
    this.fields = flds;
    this.loadMembers();  // ★ D批 家庭共享：加载家庭成员
    this.loadImages();  // ★ #909 附件库图索引（列表📷角标/看图弹层用）
    this.applyView();  // ★ 2026-09-18 初始就按默认视图显示
  }},

  // ★ 2026-09-18 按当前视图模式切换表格/卡片的显隐
  applyView() {{
    const tableWrap = document.querySelector('.panel-table-wrap');
    const cardWrap = document.getElementById('cardViewWrap');
    if (this.viewMode === 'card') {{
      if (tableWrap) tableWrap.style.display = 'none';
      if (cardWrap) cardWrap.style.display = 'block';
    }} else {{
      if (tableWrap) tableWrap.style.display = 'block';
      if (cardWrap) cardWrap.style.display = 'none';
    }}
  }},

  // ★ D批：拉取家庭成员，填充记录人下拉/筛选器/表头
  async loadMembers() {{
    try {{
      const r = await fetch('/api/panels/members').then(r => r.json());
      this.members = r.members || [];
      const sel = document.getElementById('field__member');
      if (sel) sel.innerHTML = '<option value="">不标注</option>' + this.members.map(m => '<option value="' + m.name + '">' + m.name + '</option>').join('');
      const mf = document.getElementById('memberFilter');
      if (mf) mf.innerHTML = '<option value="">全部成员</option>' + this.members.map(m => '<option value="' + m.name + '">' + m.name + '</option>').join('');
      if (this.members.length) {{
        const ths = document.querySelectorAll('#dataTable thead th');
        if (ths.length === this.fields.length + 1 && !document.getElementById('thMember')) {{
          const th = document.createElement('th');
          th.id = 'thMember'; th.textContent = '记录人'; th.style.width = '80px';
          ths[ths.length - 1].parentNode.insertBefore(th, ths[ths.length - 1]);
        }}
      }}
    }} catch (e) {{ console.error('成员加载失败:', e); }}
  }},

  // ★ D批：成员彩色徽章
  memberBadge(name) {{
    if (!name) return '';
    const m = this.members.find(x => x.name === name);
    const color = (m && m.color) || '#5B8DEF';
    return '<span style="display:inline-block;font-size:11px;padding:1px 8px;border-radius:10px;color:#fff;background:' + color + '">' + name + '</span>';
  }},

  /* ══ ★ #908/#909/#914 B方案图片能力（2026-09-27）══ */

  // 客户端压缩：最长边 1024 + JPEG 0.75（手机照片 5MB → ~150KB，存得快看得清）
  _compress(dataUrl) {{
    return new Promise(resolve => {{
      const img = new Image();
      img.onload = () => {{
        const max = 1024; let w = img.width, h = img.height;
        if (w > max || h > max) {{ const k = max / Math.max(w, h); w = Math.round(w * k); h = Math.round(h * k); }}
        const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
        cv.getContext('2d').drawImage(img, 0, 0, w, h);
        resolve(cv.toDataURL('image/jpeg', 0.75));
      }};
      img.onerror = () => resolve(dataUrl);
      img.src = dataUrl;
    }});
  }},

  // 表单图片字段：选图（★ #917 支持多选）→ 压缩 → 预览（保存时 imgPicks 打包成 JSON 数组）
  pickImage(name) {{
    const el = document.getElementById('field_' + name);
    if (!el) return;
    const files = el.files || [];
    const self = this;
    if (!files.length) {{ delete this.imgPicks[name]; el.dataset.b64 = ''; this._renderPicks(name); return; }}
    this.imgPicks[name] = this.imgPicks[name] || [];
    let done = 0;
    for (const f of files) {{
      const rd = new FileReader();
      rd.onload = async ev => {{
        const b64 = await self._compress(String(ev.target.result));
        self.imgPicks[name].push(b64);
        done++;
        if (done === files.length) {{ el.dataset.b64 = 'multi'; self._renderPicks(name); el.value = ''; }}
      }};
      rd.readAsDataURL(f);
    }}
  }},

  // 多图预览渲染（每张带 ✕ 删除）
  _renderPicks(name) {{
    const prev = document.getElementById('prev_' + name);
    if (!prev) return;
    const picks = this.imgPicks[name] || [];
    prev.innerHTML = picks.map((b, i) =>
      '<span style="position:relative;display:inline-block;margin:3px;">'
      + '<img src="' + b + '" style="max-width:90px;max-height:90px;border-radius:8px;cursor:pointer" onclick="window.open(this.src)">'
      + '<button onclick="PanelApp._rmPick(\\'' + name + '\\',' + i + ')" style="position:absolute;top:-4px;right:-4px;border:none;background:#E05B5B;color:#fff;border-radius:50%;width:18px;height:18px;font-size:10px;cursor:pointer;">✕</button></span>'
    ).join('');
  }},

  _rmPick(name, idx) {{
    (this.imgPicks[name] || []).splice(idx, 1);
    if (!this.imgPicks[name].length) delete this.imgPicks[name];
    this._renderPicks(name);
  }},

  // 拉附件库图索引：record_id 分组 + 未关联图池
  async loadImages() {{
    try {{
      const r = await fetch('/api/panels/' + this.panelId + '/images?limit=300').then(x => x.json());
      const map = {{}};
      (r.images || []).forEach(im => {{
        if (im.record_id != null) {{ (map[im.record_id] = map[im.record_id] || []).push(im); }}
      }});
      this.imgMap = map;
    }} catch (e) {{ this.imgMap = {{}}; }}
    try {{
      const r2 = await fetch('/api/panels/' + this.panelId + '/images?unlinked=1').then(x => x.json());
      this.unlinked = (r2.success && r2.images) || [];
    }} catch (e) {{ this.unlinked = []; }}
  }},

  // 列表📷角标（附件库有图的记录才显示，点开看图弹层）
  picBadge(r) {{
    // ★ #1376（2026-11-02 定版拍板）照片 30 天策略：冰箱台账已消耗的行不再挂缩略图（省显示位），
    //   换状态图标；磁盘图 30 天后由管家心跳自动清，文字档案永久保留
    if ((this.panelId||'').indexOf('fridge')===0) {{
      const st = String(r['状态']||'').trim();
      if (st && st!=='在存') return '<span style="font-size:20px;vertical-align:middle" title="已'+(st==='吃完了'?'吃掉':'处理掉')+'，照片按 30 天策略清理、文字档案保留">'+(st==='吃完了'?'🍽':'🗑')+'</span>';
    }}
    const imgs = this.imgMap[r.id] || [];
    if (!imgs.length) return '';
    const first = '<img src="' + (imgs[0].thumb_url || imgs[0].url) + '" loading="lazy" style="width:40px;height:40px;object-fit:cover;border-radius:6px;cursor:pointer;vertical-align:middle;border:1px solid #E5EAF2" onclick="PanelApp.showPics(' + r.id + ')" title="' + imgs.length + ' 张图片，点击查看">';
    const more = imgs.length > 1 ? ' <span class="panel-picbadge" onclick="PanelApp.showPics(' + r.id + ')">×' + imgs.length + '</span>' : '';
    return first + more;
  }},

  // ★ #922 T1 卡片视图缩略图条：最多展示 4 张，超出出 +N，点击任意一张进看图弹层
  imgStrip(r) {{
    // ★ #1376 同 picBadge：冰箱已消耗行不渲染图片条
    if ((this.panelId||'').indexOf('fridge')===0) {{
      const st = String(r['状态']||'').trim();
      if (st && st!=='在存') return '<div style="font-size:13px;color:#888;margin:2px 0 8px">'+(st==='吃完了'?'🍽 已吃掉':'🗑 已处理掉')+' · 照片按 30 天策略清理、文字档案保留</div>';
    }}
    const imgs = this.imgMap[r.id] || [];
    if (!imgs.length) return '';
    const tiles = imgs.slice(0, 4).map(im =>
      '<img src="' + (im.thumb_url || im.url) + '" loading="lazy" style="width:64px;height:64px;object-fit:cover;border-radius:8px;cursor:pointer;border:1px solid #E5EAF2" onclick="PanelApp.showPics(' + r.id + ')">').join('');
    const more = imgs.length > 4 ? '<span class="panel-picbadge" style="align-self:center" onclick="PanelApp.showPics(' + r.id + ')">+' + (imgs.length - 4) + '</span>' : '';
    return '<div style="display:flex;gap:5px;flex-wrap:wrap;margin:2px 0 8px;">' + tiles + more + '</div>';
  }},

  // 看图弹层：看大图 / 补传 / 从未关联图池一键挂到这条
  async showPics(recordId) {{
    let imgs = this.imgMap[recordId] || [];
    try {{
      const r = await fetch('/api/panels/' + this.panelId + '/images?record_id=' + recordId).then(x => x.json());
      if (r.success) imgs = r.images || [];
    }} catch (e) {{}}
    const overlay = document.createElement('div');
    overlay.className = 'panel-pic-overlay';
    const box = document.createElement('div');
    box.className = 'panel-pic-box';
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    const self = this;
    const redraw = () => {{ overlay.remove(); self.showPics(recordId); }};
    let html = '<h3>📷 记录图片' + (imgs.length ? '（' + imgs.length + ' 张）' : '') + '</h3>';
    html += '<div class="panel-pic-grid">' + (imgs.map(im =>
      '<div class="panel-pic-item"><img src="' + im.thumb_url + '" onclick="window.open(\\'' + im.url + '\\')">' +
      '<button class="panel-btn panel-btn-danger panel-pic-del" data-f="' + im.filename + '">✕</button></div>').join('')
      || '<div class="panel-pic-empty">这条记录还没有图片，点下面「补传一张」</div>') + '</div>';
    html += '<div class="panel-pic-foot">' +
      '<button class="panel-btn" id="panelPicAdd">＋ 补传一张</button>' +
      '<button class="panel-btn panel-pic-close">关闭</button></div>';
    if (this.unlinked.length) {{
      html += '<div class="panel-pic-pool"><b>待关联的图（' + this.unlinked.length + ' 张）</b>' +
        '<div style="font-size:11px;color:#9AA3B0;margin:4px 0 8px">之前发过但没记上账的图都在这，点「挂到这条」就归到本记录</div>' +
        '<div class="panel-pic-grid">' + this.unlinked.map(im =>
          '<div class="panel-pic-item"><img src="' + im.thumb_url + '">' +
          '<button class="panel-btn panel-pic-att" data-f="' + im.filename + '">挂到这条</button></div>').join('') + '</div></div>';
    }}
    // ★ #920 从统一图库选图（2026-09-27）：手机拍过/聊天发过的图直接取用，不用重新拍照
    html += '<div class="panel-pic-pool"><a href="javascript:;" id="panelPicGal" style="color:#5B87D8;font-size:12px">🖼️ 从图片库选一张</a>' +
      '<div id="panelPicGalBox" style="display:none"></div></div>';
    box.innerHTML = html;
    box.querySelector('#panelPicGal').onclick = async () => {{
      const gb = box.querySelector('#panelPicGalBox');
      if (gb.style.display !== 'none') {{ gb.style.display = 'none'; return; }}
      gb.style.display = 'block';
      gb.innerHTML = '<div style="color:#9AA3B0;font-size:11px;padding:8px 0">图库加载中…</div>';
      try {{
        const gr = await fetch('/api/images/list?limit=24').then(x => x.json());
        const gimgs = (gr.images || []);
        gb.innerHTML = gimgs.length
          ? '<div class="panel-pic-grid">' + gimgs.map(im =>
              '<div class="panel-pic-item"><img src="' + im.thumb_url + '">' +
              '<button class="panel-btn panel-pic-use" data-u="' + im.url + '">用这张</button></div>').join('') + '</div>'
          : '<div style="color:#9AA3B0;font-size:11px;padding:8px 0">图片库还是空的</div>';
        gb.querySelectorAll('.panel-pic-use').forEach(b => b.onclick = async () => {{
          const self2 = self;
          try {{
            const blob = await fetch(b.dataset.u).then(x => x.blob());
            const b64 = await new Promise(res => {{
              const rd = new FileReader();
              rd.onload = e => res(String(e.target.result).split(',')[1] || '');
              rd.readAsDataURL(blob);
            }});
            await fetch('/api/panels/image', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ panel_id: self2.panelId, image_b64: b64, record_id: recordId, note: '从图片库选用' }}) }});
            redraw();
          }} catch (e) {{ alert('取图失败，请重试'); }}
        }});
      }} catch (e) {{ gb.innerHTML = '<div style="color:#E05B5B;font-size:11px;padding:8px 0">图库加载失败</div>'; }}
    }};
    box.querySelector('.panel-pic-close').onclick = () => {{ overlay.remove(); self.loadImages().then(() => self.loadData()); }};
    box.querySelector('#panelPicAdd').onclick = () => {{
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = 'image/*';
      inp.onchange = () => {{
        const f = inp.files && inp.files[0]; if (!f) return;
        const rd = new FileReader();
        rd.onload = async ev => {{
          const b64 = await self._compress(String(ev.target.result));
          await fetch('/api/panels/image', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ panel_id: self.panelId, image_b64: b64.split(',')[1] || b64, record_id: recordId, note: '看图弹层补传' }}) }});
          redraw();
        }};
        rd.readAsDataURL(f);
      }};
      inp.click();
    }};
    box.querySelectorAll('.panel-pic-del').forEach(b => b.onclick = async () => {{
      await fetch('/api/panels/image/' + b.dataset.f, {{ method: 'DELETE' }});
      redraw();
    }});
    box.querySelectorAll('.panel-pic-att').forEach(b => b.onclick = async () => {{
      await fetch('/api/panels/' + self.panelId + '/images/attach', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ filename: b.dataset.f, record_id: recordId }}) }});
      redraw();
    }});
  }},

  // ★ 2026-08-08: 切换视图模式
  toggleView() {{
    this.viewMode = this.viewMode === 'table' ? 'card' : 'table';
    this.applyView();
    this.loadData();
  }},

  // ★ 2026-08-08: 格式化字段值显示
  formatValue(f, val) {{
    if (!val) return '';
    if (f.type === 'image') {{
      // ★ #908 列里存的是附件库文件名（uuid.jpg）→ 直接出缩略图；老数据 dataURL 也兼容
      // ★ #917 多图：列里逗号分隔多个文件名 → 逐张出缩略图
      const s = String(val);
      if (s.startsWith('data:image')) {{
        return '<img src="' + s + '" style="max-width:60px;max-height:60px;border-radius:6px;cursor:pointer" onclick="window.open(this.src)">';
      }}
      const names = s.split(',').map(x => x.trim()).filter(x => /^[0-9a-f]{{32}}\\.jpg$/.test(x));
      if (names.length) {{
        return names.map(n => '<img src="/api/panels/image/' + n + '?thumb=1" style="max-width:60px;max-height:60px;border-radius:6px;cursor:pointer;margin:1px" onclick="window.open(\\'/api/panels/image/' + n + '\\')">').join(' ');
      }}
      return '';
    }}
    if (f.type === 'textarea') {{
      const short = val.length > 30 ? val.substring(0, 30) + '...' : val;
      return '<span title="' + val.replace(/"/g, '&quot;') + '">' + short.replace(/\\n/g, '<br>') + '</span>';
    }}
    return String(val);
  }},

  async loadData() {{
    const search = document.getElementById('searchInput')?.value || '';
    let filter = {{}};
    if (search) filter._search = search;
    const mf = document.getElementById('memberFilter');
    if (mf && mf.value) filter.member = mf.value;  // ★ D批：按成员筛选
    try {{
      await this.loadImages();  // ★ #909 列表📷角标数据源（轻量两条 GET）
      const records = await HarvenPanel.query(this.panelId, filter);
      if (!records || records.length === 0) {{
        /* ★ 2026-09-19 易用性②：空态带示例话术——教用户「不用填表，直接说也行」 */
        var _ex = (typeof PanelApp !== 'undefined' && PanelApp.emptyExample) || '今天记一条';
        var _emptyHtml = '<div style="text-align:center;color:#9AA3B0;padding:34px 20px;line-height:1.9;">还没有记录<br>'
          + '<span style="font-size:12px;">点上方「＋ 添加」像填表一样记第一条；也可以回聊天框直接说，比如：'
          + '<b style="color:#5B87D8;">「' + _ex + '」</b>，哈文自动帮你入档</span></div>';
        if (this.viewMode === 'card') {{
          const cardWrap = document.getElementById('cardViewWrap');
          if (cardWrap) cardWrap.innerHTML = _emptyHtml;
        }} else {{
          const tbody = document.getElementById('tableBody');
          if (tbody) tbody.innerHTML = '<tr><td colspan="' + (this.fields.length + 1) + '" class="panel-empty">' + _emptyHtml + '</td></tr>';
        }}
        return;
      }}
      if (this.viewMode === 'card') {{
        this.renderCards(records);
      }} else {{
        this.renderTable(records);
      }}
    }} catch(e) {{
      // ★ 2026-09-18 错误双写：卡片模式下表格是隐藏的，错误只写表格用户会看到一片空白
      const tbody = document.getElementById('tableBody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="' + (this.fields.length + 1) + '" class="panel-empty">加载失败: ' + e.message + '</td></tr>';
      const errCard = document.getElementById('cardViewWrap');
      if (errCard && this.viewMode === 'card') errCard.innerHTML = '<div style="text-align:center;color:#E05B5B;padding:40px;">⚠️ 加载失败：' + e.message + '<br><span style="font-size:11px;color:#9AA3B0;">（若是超时，请刷新页面重试）</span></div>';
    }}
  }},

  // ★ 2026-08-08: 表格视图
  renderTable(records) {{
    const tbody = document.getElementById('tableBody');
    if (!tbody) return;
    tbody.innerHTML = records.map(r => {{
      const cells = this.fields.map(f => '<td>' + this.formatValue(f, r[f.name]) + '</td>').join('');
      const memberCell = this.members.length ? '<td>' + this.memberBadge(r.member) + '</td>' : '';
      return '<tr>' + cells + memberCell + '<td>' + this.picBadge(r) + '<button class="panel-btn" onclick="HarvenMoneyBridge.goById(' + r.id + ')" style="font-size:11px;padding:2px 8px" title="把这条记到记账账本">💰</button> <button class="panel-btn" onclick="PanelApp.editRecord(' + r.id + ')" style="font-size:11px;padding:2px 8px">编辑</button> <button class="panel-btn panel-btn-danger" onclick="PanelApp.deleteRecord(' + r.id + ')" style="font-size:11px;padding:2px 8px">删除</button></td></tr>';
    }}).join('');
  }},

  // ★ 2026-08-08: 卡片视图
  renderCards(records) {{
    const cardWrap = document.getElementById('cardViewWrap');
    if (!cardWrap) return;
    cardWrap.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;">' + records.map(r => {{
      var fields = this.fields.map(f => {{
        var val = this.formatValue(f, r[f.name]);
        if (!val) return '';
        return '<div style="margin-bottom:4px;"><span style="font-size:10px;color:#9AA3B0;">' + f.name + ':</span> <span style="font-size:12px;">' + val + '</span></div>';
      }}).join('');
      var memberLine = r.member ? '<div style="margin-bottom:6px;">' + this.memberBadge(r.member) + '</div>' : '';
      return '<div style="background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:12px;box-shadow:0 1px 4px rgba(58,68,83,0.06);">' + memberLine + this.imgStrip(r) + fields + '<div style="margin-top:8px;text-align:right;"><button class="panel-btn" onclick="HarvenMoneyBridge.goById(' + r.id + ')" style="font-size:11px;padding:2px 8px;" title="把这条记到记账账本">💰</button> <button class="panel-btn" onclick="PanelApp.editRecord(' + r.id + ')" style="font-size:11px;padding:2px 8px;">编辑</button> <button class="panel-btn panel-btn-danger" onclick="PanelApp.deleteRecord(' + r.id + ')" style="font-size:11px;padding:2px 8px;">删除</button></div></div>';
    }}).join('') + '</div>';
  }},

  showForm() {{
    this.editingId = null;
    document.getElementById('editId').value = '';
    this.imgPicks = {{}};  // ★ #908 清掉上一条选的图
    // ★ 2026-09-18 新记录时「更多字段」默认收起
    const advBoxNew = document.getElementById('advFields');
    if (advBoxNew) advBoxNew.open = false;
    this.fields.forEach(f => {{
      const el = document.getElementById('field_' + f.name);
      if (el) {{
        el.value = '';
        if (f.type === 'image') {{
          el.dataset.b64 = '';
          const pv = document.getElementById('prev_' + f.name);
          if (pv) pv.innerHTML = '';
        }}
      }}
    }});
    document.getElementById('formArea').style.display = 'block';
  }},

  hideForm() {{
    document.getElementById('formArea').style.display = 'none';
  }},

  editRecord(id) {{
    HarvenPanel.query(this.panelId, {{}}).then(records => {{
      const record = records.find(r => r.id == id);
      if (!record) return;
      this.editingId = id;
      document.getElementById('editId').value = id;
      this.fields.forEach(f => {{
        const el = document.getElementById('field_' + f.name);
        if (el) {{
          if (f.type === 'image') {{
            // ★ #908 编辑时回显旧图（固定写进 prev_ 预览位，不再叠 div）；没换图就不动该字段
            // ★ #917 旧图多张（逗号分隔）也逐张回显
            const pv = document.getElementById('prev_' + f.name);
            const v = String(record[f.name] || '');
            const parts = v.startsWith('data:image') ? [v]
              : v.split(',').map(x => x.trim()).filter(x => /^[0-9a-f]{{32}}\\.jpg$/.test(x));
            const tag = parts.map(p => p.startsWith('data:image')
              ? '<img src="' + p + '" style="max-width:90px;max-height:90px;border-radius:8px;margin:2px;">'
              : '<img src="/api/panels/image/' + p + '?thumb=1" style="max-width:90px;max-height:90px;border-radius:8px;margin:2px;">').join('');
            if (pv) pv.innerHTML = tag;
            el.value = '';
            el.dataset.b64 = '';
          }} else {{
            el.value = record[f.name] || '';
          }}
        }}
      }});
      const msel = document.getElementById('field__member');
      if (msel) msel.value = record.member || '';  // ★ D批：回显记录人
      // ★ 2026-09-18 编辑时有值的折叠字段自动展开（和成长档案同款体验）
      const advBox = document.getElementById('advFields');
      if (advBox) {{
        let hasAdv = false;
        this.fields.forEach(f => {{
          const el = document.getElementById('field_' + f.name);
          if (el && advBox.contains(el) && el.value) hasAdv = true;
        }});
        advBox.open = hasAdv;
      }}
      document.getElementById('formArea').style.display = 'block';
    }});
  }},

  async saveRecord() {{
    const data = {{}};
    for (const f of this.fields) {{
      const el = document.getElementById('field_' + f.name);
      if (!el) continue;
      if (f.type === 'image') {{
        // ★ #908 用压缩后的图（pickImage 存的）；★ #917 多图打包成 JSON 数组；没换图就不带该字段
        if (this.imgPicks[f.name] && this.imgPicks[f.name].length) {{
          data[f.name] = JSON.stringify(this.imgPicks[f.name]);
        }}
        continue;
      }}
      data[f.name] = el.value;
    }}
    const msel = document.getElementById('field__member');
    if (msel) data['member'] = msel.value || '';  // ★ D批：记录人
    try {{
      const editId = document.getElementById('editId').value;
      if (editId) {{
        await HarvenPanel.update(this.panelId, parseInt(editId), data);
      }} else {{
        await HarvenPanel.save(this.panelId, data);
      }}
      this.hideForm();
      this.loadData();
    }} catch(e) {{
      alert('保存失败: ' + e.message);
    }}
  }},

  async deleteRecord(id) {{
    if (!confirm('确定删除这条记录？')) return;
    try {{
      await HarvenPanel.remove(this.panelId, id);
      this.loadData();
    }} catch(e) {{
      alert('删除失败: ' + e.message);
    }}
  }}
}};

// ★ 2026-09-16 轻联动：其他面板一键带出记账（确认制，带来源标记）
const HarvenMoneyBridge = {{
  async goById(id) {{
    try {{
      var recs = await HarvenPanel.query(PanelApp.panelId, {{}});
      var row = recs.find(r => r.id == id);
      if (row) this.go(row);
    }} catch (e) {{ alert('读取记录失败: ' + e.message); }}
  }},
  go(row) {{
    try {{
      var fields = PanelApp.fields || [];
      var amtNames = ['金额', '价格', '费用', '花费', '总价', 'amount'];
      var projNames = ['项目', '名称', '事项', '内容', 'item', 'name'];
      var amtField = fields.find(f => amtNames.indexOf(f.name) !== -1 && row[f.name] != null && row[f.name] !== '');
      var projField = fields.find(f => projNames.indexOf(f.name) !== -1 && row[f.name]) || fields.find(f => f.type === 'text' && row[f.name]);
      localStorage.setItem('harven_money_prefill', JSON.stringify({{
        project: projField ? String(row[projField.name]) : '',
        amount: amtField ? (Number(row[amtField.name]) || '') : '',
        category: '', note: '来自面板记录',
        source: PanelApp.panelId, ts: Date.now()
      }}));
      if (window.parent && window.parent !== window) {{
        try {{
          var pp = String(window.parent.location.pathname || '');
          var ph = String(window.parent.location.hash || '');
          if (pp.indexOf('/static/spa') !== -1 || ph.indexOf('#/') !== -1) {{
            window.parent.location.hash = '#/panel/accounting_daily_1';
            return;
          }}
        }} catch (e) {{}}
      }}
      alert('已暂存到记账带出：请在哈文新版页面的「记账账本」里确认入账');
    }} catch (e) {{ alert('记账带出失败: ' + e.message); }}
  }}
}};
'''

    # ── 面板数据操作方法 ──

    def _ensure_member_column(self, table_name: str):
        """★ D批 家庭共享：确保表里有 member 列（旧表懒升级，新表建表时已带）"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            if 'member' not in cols:
                conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "member" TEXT')
                conn.commit()
                logger.info(f'[PanelManager] 表 {table_name} 已补 member 列（家庭共享）')
            conn.close()
        except Exception as e:
            logger.error(f'[PanelManager] member 列检查失败 {table_name}: {e}')

    def query_panel_data(self, panel_id: str, filters: dict = None,
                         limit: int = 100, offset: int = 0) -> list[dict]:
        """查询面板数据"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            table_name = f'panel_{panel_id}'

            where_clauses = []
            params = []
            if filters:
                search = filters.pop('_search', None)
                if 'member' in filters:
                    self._ensure_member_column(table_name)  # 旧表还没这列时先补上
                for k, v in filters.items():
                    where_clauses.append(f'"{k}" = ?')
                    params.append(v)
                if search:
                    panel = self._panels.get(panel_id)
                    if panel:
                        search_clauses = []
                        for f in panel.fields:
                            if f.get('type') == 'text':
                                search_clauses.append(f'"{f["name"]}" LIKE ?')
                                params.append(f'%{search}%')
                        if search_clauses:
                            where_clauses.append('(' + ' OR '.join(search_clauses) + ')')

            sql = f'SELECT * FROM {table_name}'
            if where_clauses:
                sql += ' WHERE ' + ' AND '.join(where_clauses)
            sql += f' ORDER BY id DESC LIMIT {limit} OFFSET {offset}'

            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f'[PanelManager] 查询数据失败 {panel_id}: {e}')
            return []

    # ── ★ #938 C1 列名注入修复（2026-09-30）────────────────────────
    # 实锤：insert/update 把 request JSON 的键 f'"{k}"' 直接拼 SQL，
    # 键含双引号可逃逸（POC: 'x" -- injected' → 500 incomplete input）。
    # 修法：PRAGMA table_info 取表实际列做白名单，白名单外的键一律丢弃。

    @staticmethod
    def _valid_panel_id(panel_id: str) -> bool:
        """panel_id 格式守卫（与 create_panel_from_template 白名单一致）。
        isalnum 对中文返回 True，中文面板名不受影响。"""
        return bool(panel_id) and panel_id.replace('-', '').replace('_', '').isalnum()

    def _filter_columns(self, conn, table_name: str, data: dict) -> tuple:
        """按表实际列过滤 data，返回 (干净data, 被丢弃的键列表)。
        白名单 = PRAGMA table_info 实际列；多余键丢弃不留痕进 SQL。
        ★ #1399（2026-11-02）：字段名归一化模糊匹配——FC 模型常把「时长分钟」写成
        「时长(分钟)」（健身时长实锤丢数据），去括号/空格后比对，对得上就自动改名落库。"""
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()}
        clean, dropped = {}, []
        for k, v in data.items():
            if k in cols:
                clean[k] = v
                continue
            norm = self._norm_field(k)
            hit = next((c for c in cols if c not in clean and self._norm_field(c) == norm), None)
            if hit:
                clean[hit] = v
                logger.info(f'[PanelManager] #1399 字段归一化: {k!r} → {hit!r} ({table_name})')
            else:
                dropped.append(k)
        return clean, dropped

    @staticmethod
    def _norm_field(name: str) -> str:
        """字段名归一化：去括号（全半角）/空格/连字符/下划线，小写化。"""
        for ch in '()（）[]【】 -_':
            name = name.replace(ch, '')
        return name.lower()

    def insert_panel_data(self, panel_id: str, data: dict) -> dict:
        """插入面板数据"""
        try:
            from src.models.database import get_connection
            if not self._valid_panel_id(panel_id):
                raise ValueError(f'非法 panel_id: {panel_id!r}')
            table_name = f'panel_{panel_id}'
            if 'member' in data:
                self._ensure_member_column(table_name)  # 旧表懒升级
            conn = get_connection(str(DB_PATH))
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['created_at'] = now
            data['updated_at'] = now
            data, dropped = self._filter_columns(conn, table_name, data)  # ★ #938 白名单
            if dropped:
                logger.warning(f'[PanelManager] #938 插入 {panel_id} 丢弃白名单外字段: {dropped}')
            # ★ #1513：系统列不算业务字段——否则全非法时 clean 永远非空，
            #   悄悄落一条只有 created_at/updated_at 的空壳行（冒烟实锤）
            _SYS_COLS = ('created_at', 'updated_at', 'member')
            if not any(k not in _SYS_COLS for k in data):
                # ★ #1513（2026-11-14）：报错必须带可用字段清单——否则 FC 模型只能瞎猜第二轮
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
                conn.close()
                usable = [c for c in cols if c not in ('id', 'created_at', 'updated_at')]
                raise ValueError(f'{panel_id} 无合法字段可插入：传了 {sorted(dropped)}，都不在该面板的列里。'
                                 f'可用字段：{usable}。请把内容对号入座到可用字段（语义近的直接映射，如「项目」→「保养项目」）')
            keys = list(data.keys())
            placeholders = ', '.join(['?' for _ in keys])
            columns = ', '.join([f'"{k}"' for k in keys])
            sql = f'INSERT INTO {table_name} ({columns}) VALUES ({placeholders})'
            cursor = conn.execute(sql, list(data.values()))
            conn.commit()
            new_id = cursor.lastrowid
            conn.close()
            self.fts_sync(panel_id, new_id, data)  # ★ P0-B FTS5 同步
            result = {'id': new_id, **data}
            if dropped:
                result['dropped_keys'] = dropped  # 响应里提示被丢字段
            return result
        except Exception as e:
            logger.error(f'[PanelManager] 插入数据失败 {panel_id}: {e}')
            raise

    # ── ★ #913 入库查重（2026-09-27）────────────────────────────
    # 痛点实锤：开发者在纯文本快捷通道连发「记录保存」「识别存储」救同一张京东截图，
    # 同笔消费被抽 3 组 6 条全部入库。根因：入库零查重。
    _AMT_NAMES = ('金额', '实付', '价格', '费用', '花费', '总价', 'amount')
    _NAME_NAMES = ('项目', '名称', '事项', '内容', '品名', 'item', 'name')

    def find_duplicate(self, panel_id: str, data: dict):
        """查今天是否已有同金额+同名的记录。命中返回 {'id': 行id}，否则 None。
        规则：只比「今天」（历史同名同价是正常复购）；必须金额能对上才判重；
        名字按互相包含算近似（"京东购物" vs "京东"）。没有金额字段/金额非数字的面板不查。"""
        try:
            amt_name = next((n for n in self._AMT_NAMES if n in data), '')
            if not amt_name:
                return None
            try:
                amt = round(float(str(data.get(amt_name, '')).replace('¥', '').replace('￥', '').strip()), 2)
            except Exception:
                return None
            new_name = str(next((data[n] for n in self._NAME_NAMES if data.get(n)), '')).strip()
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("panel_{panel_id}")').fetchall()]
            if amt_name not in cols:
                conn.close()
                return None
            name_col = next((n for n in self._NAME_NAMES if n in cols and n in data), '')
            today = datetime.now().strftime('%Y-%m-%d')
            sql = f'SELECT id, "{amt_name}"' + (f', "{name_col}"' if name_col else '') + \
                f' FROM "panel_{panel_id}" WHERE created_at >= ?'
            params = [today + ' 00:00:00']
            if name_col:
                sql += f' AND "{name_col}" IS NOT NULL'
            dup_id = None
            for row in conn.execute(sql, params).fetchall():
                rid, r_amt = row[0], row[1]
                try:
                    r_amt = round(float(r_amt or 0), 2)
                except Exception:
                    continue
                if abs(r_amt - amt) > 0.001:
                    continue
                if name_col:
                    r_name = str(row[2] if len(row) > 2 and row[2] else '').strip()
                    if new_name:
                        if new_name == r_name or new_name in r_name or r_name in new_name:
                            dup_id = rid
                            break
                    # 新记录没名字时不误杀：要求库里的也没名字才算重
                    elif not r_name:
                        dup_id = rid
                        break
                else:
                    dup_id = rid
                    break
            conn.close()
            return {'id': dup_id} if dup_id else None
        except Exception as e:
            logger.debug(f'[PanelManager] 查重跳过 {panel_id}: {e}')
            return None

    def update_panel_data(self, panel_id: str, row_id: int, data: dict) -> dict:
        """更新面板数据"""
        try:
            from src.models.database import get_connection
            if not self._valid_panel_id(panel_id):
                raise ValueError(f'非法 panel_id: {panel_id!r}')
            conn = get_connection(str(DB_PATH))
            table_name = f'panel_{panel_id}'
            data, dropped = self._filter_columns(conn, table_name, data)  # ★ #938 白名单
            if dropped:
                logger.warning(f'[PanelManager] #938 更新 {panel_id}#{row_id} 丢弃白名单外字段: {dropped}')
            # ★ #1513：系统列不算业务字段（与插入侧对称）
            _SYS_COLS = ('created_at', 'updated_at', 'member')
            if not any(k not in _SYS_COLS for k in data):
                # ★ #1513：同插入——报错附可用字段清单
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
                conn.close()
                usable = [c for c in cols if c not in ('id', 'created_at', 'updated_at')]
                raise ValueError(f'{panel_id}#{row_id} 无合法字段可更新：传了 {sorted(dropped)}。'
                                 f'可用字段：{usable}。请把内容对号入座到可用字段')
            set_clauses = ', '.join([f'"{k}" = ?' for k in data.keys()])
            params = list(data.values()) + [row_id]
            # 自动更新 updated_at
            sql = f"UPDATE {table_name} SET {set_clauses}, updated_at = datetime('now','localtime') WHERE id = ?"
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            self.fts_sync(panel_id, row_id, data)  # ★ P0-B FTS5 同步
            result = {'id': row_id, **data}
            if dropped:
                result['dropped_keys'] = dropped
            return result
        except Exception as e:
            logger.error(f'[PanelManager] 更新数据失败 {panel_id}: {e}')
            raise

    def delete_panel_data(self, panel_id: str, row_id: int) -> bool:
        """删除面板数据"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            table_name = f'panel_{panel_id}'
            conn.execute(f'DELETE FROM {table_name} WHERE id = ?', [row_id])
            conn.commit()
            conn.close()
            self.fts_delete(panel_id, row_id)  # ★ P0-B FTS5 同步
            return True
        except Exception as e:
            logger.error(f'[PanelManager] 删除数据失败 {panel_id}: {e}')
            return False

    # ── ★ P0-B 2026-09-27 FTS5 全文索引（已批准）──────────────────────────
    # 痛点：查询靠 LLM 全表逐条读，慢且费 token。建 trigram 全文虚表，
    # 查询先命中 top N 再给模型。中文支持：trigram 对 >=3 字走 MATCH，
    # 1~2 字短词走 LIKE（trigram 表 LIKE 有专门优化），全兼容。

    def fts_ensure(self, conn=None):
        """确保 FTS 虚表存在（幂等，可复用外部连接）"""
        from src.models.database import get_connection
        own = conn is None
        if own:
            conn = get_connection(str(DB_PATH))
        conn.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS fts_panel_records
            USING fts5(text, panel_id UNINDEXED, record_id UNINDEXED, tokenize='trigram')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS fts_meta (key TEXT PRIMARY KEY, value TEXT)''')
        if own:
            conn.commit()
            conn.close()

    @staticmethod
    def _fts_text(data: dict) -> str:
        """记录 → 索引文本（全部字段值拼接，跳过长 base64 图片列）"""
        parts = []
        for k, v in (data or {}).items():
            if not v or k in ('id', 'created_at', 'updated_at'):
                continue
            s = str(v)
            if len(s) > 400 or s.startswith('data:image') or (k in ('照片', '柜内照片', '图片') and len(s) > 100):
                continue  # base64 图 / 超长字段不进索引
            parts.append(f'{k}:{s}')
        return ' '.join(parts)[:2000]

    def fts_sync(self, panel_id: str, row_id, data: dict):
        """插入/更新后同步索引（旧条目先删再插）"""
        try:
            text = self._fts_text(data)
            if not text:
                return
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            self.fts_ensure(conn)
            conn.execute('DELETE FROM fts_panel_records WHERE panel_id=? AND record_id=?',
                         (panel_id, row_id))
            conn.execute('INSERT INTO fts_panel_records(text, panel_id, record_id) VALUES (?,?,?)',
                         (text, panel_id, row_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f'[PanelManager] FTS 同步跳过 {panel_id}#{row_id}: {e}')

    def fts_delete(self, panel_id: str, row_id):
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            self.fts_ensure(conn)
            conn.execute('DELETE FROM fts_panel_records WHERE panel_id=? AND record_id=?',
                         (panel_id, row_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f'[PanelManager] FTS 删除跳过 {panel_id}#{row_id}: {e}')

    def fts_rebuild_all(self):
        """全量重建索引（首次启用/表损坏时）。后台线程调用，不阻塞启动。"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            self.fts_ensure(conn)
            conn.execute('DELETE FROM fts_panel_records')
            conn.execute("INSERT OR REPLACE INTO fts_meta(key, value) VALUES ('rebuilt_at', datetime('now','localtime'))")
            n = 0
            for pid in list(self._panels.keys()):
                table_name = f'panel_{pid}'
                try:
                    rows = conn.execute(f'SELECT * FROM "{table_name}"').fetchall()
                except Exception:
                    continue
                cols = [d[0] for d in conn.execute(f'SELECT * FROM "{table_name}" LIMIT 0').description]
                for row in rows:
                    data = dict(zip(cols, row))
                    rid = data.get('id')
                    text = self._fts_text(data)
                    if text and rid is not None:
                        conn.execute('INSERT INTO fts_panel_records(text,panel_id,record_id) VALUES (?,?,?)',
                                     (text, pid, rid))
                        n += 1
            conn.commit()
            conn.close()
            logger.info(f'[PanelManager] FTS5 全量重建完成: {n} 条记录入索引')
            return n
        except Exception as e:
            logger.error(f'[PanelManager] FTS 重建失败: {e}')
            return 0

    def fts_rebuild_if_needed(self):
        """懒重建：从未重建过才跑（后台线程，不阻塞）"""
        import threading
        def _bg():
            try:
                from src.models.database import get_connection
                conn = get_connection(str(DB_PATH))
                self.fts_ensure(conn)
                row = conn.execute("SELECT value FROM fts_meta WHERE key='rebuilt_at'").fetchone()
                conn.close()
                if not row:
                    self.fts_rebuild_all()
            except Exception as e:
                logger.debug(f'[PanelManager] FTS 懒重建跳过: {e}')
        threading.Thread(target=_bg, daemon=True).start()

    def fts_search(self, query: str, panel_id: str = '', limit: int = 10) -> list[dict]:
        """全文检索面板记录。<3 字短词走 LIKE，>=3 字走 MATCH。
        返回 [{'panel_id','record_id','text'}]，失败返回 []（调用方退回原路径）。"""
        q = str(query or '').strip()
        if not q:
            return []
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            self.fts_ensure(conn)
            if len(q) >= 3:
                # MATCH：多词用 OR 连接提升召回（"京东 洗衣机"）
                match_q = ' OR '.join(f'"{w.replace(chr(34), "")}"' for w in q.split() if w)
                sql = ('SELECT panel_id, record_id, text FROM fts_panel_records '
                       'WHERE fts_panel_records MATCH ?')
                params = [match_q]
                if panel_id:
                    sql += ' AND panel_id = ?'
                    params.append(panel_id)
                sql += ' ORDER BY rank LIMIT ?'
                params.append(limit)
                try:
                    rows = conn.execute(sql, params).fetchall()
                except Exception:
                    rows = []  # MATCH 语法不符（如全符号）→ 退 LIKE
            else:
                rows = []
            if not rows:
                sql = ('SELECT panel_id, record_id, text FROM fts_panel_records '
                       'WHERE text LIKE ?')
                params = [f'%{q}%']
                if panel_id:
                    sql += ' AND panel_id = ?'
                    params.append(panel_id)
                sql += ' LIMIT ?'
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
            # ★ 整串不中（如「京东买了什么」整句非连续出现）→ jieba 切关键词逐个检索
            if not rows:
                try:
                    import jieba
                    kws = [w for w in jieba.cut_for_search(q) if len(w) >= 2][:6]
                except Exception:
                    kws = []
                if kws:
                    seen, merged = set(), []
                    for kw in kws:
                        sql = ('SELECT panel_id, record_id, text FROM fts_panel_records '
                               'WHERE text LIKE ?')
                        params = [f'%{kw}%']
                        if panel_id:
                            sql += ' AND panel_id = ?'
                            params.append(panel_id)
                        sql += ' LIMIT ?'
                        params.append(limit)
                        for r in conn.execute(sql, params).fetchall():
                            key = (r[0], r[1])
                            if key not in seen:
                                seen.add(key)
                                merged.append((r[0], r[1], r[2]))  # ★ 保持 tuple 与其他分支同构（dict 会让末尾 r[0] 取值抛 KeyError）
                        if len(merged) >= limit:
                            break
                    rows = merged[:limit]
            conn.close()
            return [{'panel_id': r[0], 'record_id': r[1], 'text': r[2]} for r in rows]
        except Exception as e:
            logger.debug(f'[PanelManager] FTS 检索失败: {e}')
            return []

    # ── ★ D批 家庭共享：家庭成员管理 ──

    @staticmethod
    def _ensure_members_table(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS panel_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            color TEXT DEFAULT '',
            role TEXT DEFAULT '员工',
            created_at TEXT)''')
        # ★ 2026-09-20 (#807): 旧库补 role 列（企业场景三级角色标签：管理员/负责人/员工）
        cols = [r[1] for r in conn.execute('PRAGMA table_info(panel_members)').fetchall()]
        if 'role' not in cols:
            conn.execute("ALTER TABLE panel_members ADD COLUMN role TEXT DEFAULT '员工'")
        # ★ 2026-10-20 家人档案扩列（定版拍板方案A）：panel_members 从工作分工表升级为
        #   全家档案正主——生日/手机/口味/忌口过敏/健康备注，全面板共享+生日挂提醒
        # ★ 2026-10-21 家人健康总纲（定版拍板一步到位）：扩至 32 字段六层
        #   基础6/健康9/饮食4/成长4/社会5/生活7 + extra(自定义10)
        for col, default in (
            ('relation', "''"), ('birthday', "''"), ('phone', "''"),
            ('preference', "''"), ('allergy', "''"), ('health_note', "''"),
            ('extra', "'{}'"),   # 自定义录入项（JSON，上限10项，用户可和AI讨论添加）
            # ── 基础层补 ──
            ('birthday_lunar', "''"),   # 农历生日，如 08-10（月-日），daemon 双轨扫
            ('birth_time', "''"),       # 出生时间（时辰），如 午时
            ('wechat', "''"),
            # ── 健康层 ──
            ('blood_type', "''"),       # A/B/O/AB/未知
            ('height_cm', "''"), ('weight_kg', "''"),
            ('chronic', "''"),          # 慢性病
            ('medication', "''"),       # 长期用药
            ('drug_allergy', "''"),     # 药物过敏（与食物忌口分开！）
            ('vaccine_note', "''"),     # 疫苗情况
            ('exercise', "''"),         # 运动习惯
            ('sleep_note', "''"),       # 睡眠情况
            # ── 饮食层补 ──
            ('like_food', "''"), ('hate_food', "''"),
            # ── 成长层（孩子）──
            ('school', "''"), ('grade', "''"), ('interests', "''"),
            # ── 社会层（大人）──
            ('job', "''"), ('company', "''"),
            ('license_type', "''"), ('license_expiry', "''"),  # 驾照
            ('id_expiry', "''"),                                # 身份证有效期
            # ── 生活层 ──
            ('emergency_contact', "''"), ('emergency_phone', "''"),
            ('top_size', "''"), ('shoe_size', "''"),
            ('budget_monthly', "''"),   # 月预算/零花钱额度
            ('comm_style', "''"),       # 沟通偏好（对孩子简单话/对老人慢语速）
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE panel_members ADD COLUMN {col} TEXT DEFAULT {default}")

    # 家人档案字段白名单（update_member 用）——六层 32 字段
    MEMBER_FIELDS = {
        'name', 'color', 'role', 'extra',
        # 基础
        'relation', 'birthday', 'birthday_lunar', 'birth_time', 'phone', 'wechat',
        # 健康
        'blood_type', 'height_cm', 'weight_kg', 'chronic', 'medication', 'drug_allergy',
        'vaccine_note', 'exercise', 'sleep_note', 'health_note',
        # 饮食
        'preference', 'allergy', 'like_food', 'hate_food',
        # 成长
        'school', 'grade', 'interests',
        # 社会
        'job', 'company', 'license_type', 'license_expiry', 'id_expiry',
        # 生活
        'emergency_contact', 'emergency_phone', 'top_size', 'shoe_size',
        'budget_monthly', 'comm_style',
    }
    # 星座按生日自动算，不落库
    ZODIAC = [('摩羯', (1, 19)), ('水瓶', (2, 18)), ('双鱼', (3, 20)), ('白羊', (4, 19)),
              ('金牛', (5, 20)), ('双子', (6, 21)), ('巨蟹', (7, 22)), ('狮子', (8, 22)),
              ('处女', (9, 22)), ('天秤', (10, 23)), ('天蝎', (11, 22)), ('射手', (12, 21)),
              ('摩羯', (12, 31))]

    @staticmethod
    def zodiac_of(birthday):
        """★ 星座自动算：从公历生日推星座（MM-DD），不合法返回空"""
        b = str(birthday or '')
        if len(b) < 10:
            return ''
        try:
            mm, dd = int(b[5:7]), int(b[8:10])
        except ValueError:
            return ''
        for name, (m2, d2) in PanelManager.ZODIAC:
            if (mm, dd) <= (m2, d2):
                return name
        return ''

    def get_members(self) -> list[dict]:
        """获取家庭成员列表（★ 10-21 含 32 字段 + 星座自动 + extra 解析）"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            self._ensure_members_table(conn)
            rows = conn.execute('SELECT * FROM panel_members ORDER BY id').fetchall()
            conn.commit()
            conn.close()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d['extra'] = json.loads(d.get('extra') or '{}')
                except Exception:
                    d['extra'] = {}
                d['zodiac'] = self.zodiac_of(d.get('birthday'))
                out.append(d)
            return out
        except Exception as e:
            logger.error(f'[PanelManager] 读取成员失败: {e}')
            return []

    def update_member(self, member_id: str, **fields) -> bool:
        """★ 家人档案编辑（白名单=32字段；extra 支持自定义录入项，上限 10）"""
        allowed = self.MEMBER_FIELDS
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == 'extra':
                # 自定义录入项：dict，key 限 20 字、最多 10 项
                if isinstance(v, str):
                    try:
                        v = json.loads(v or '{}')
                    except Exception:
                        v = {}
                if not isinstance(v, dict):
                    continue
                clean = {}
                for i, (ek, ev) in enumerate(v.items()):
                    if i >= 10:
                        break
                    ek = str(ek).strip()[:20]
                    if ek:
                        clean[ek] = str(ev or '').strip()[:200]
                vals.append(json.dumps(clean, ensure_ascii=False))
                sets.append('extra=?')
                continue
            sets.append(f'{k}=?')
            vals.append(str(v or '').strip()[:200])
        if not sets:
            return False
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            conn.execute(f"UPDATE panel_members SET {', '.join(sets)} WHERE id=?", (*vals, member_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f'[PanelManager] 编辑成员失败: {e}')
            return False

    def upcoming_birthdays(self, days_ahead=7) -> list[dict]:
        """★ 生日扫描（reminder_daemon 挂钩）：未来 N 天过生日的家人。
        ★ 2026-10-21 双轨：公历生日 + 农历生日（birthday_lunar MM-DD，zhdate 转公历）"""
        out = []
        seen = set()
        try:
            from datetime import datetime
            today = datetime.now().date()

            def _push(m, days):
                if m['id'] in seen or not (0 <= days <= days_ahead):
                    return
                seen.add(m['id'])
                bd = str(m.get('birthday') or '')
                try:
                    age = today.year - int(bd[:4]) if len(bd) >= 10 and bd[:4].isdigit() and 0 < today.year - int(bd[:4]) < 120 else None
                except Exception:
                    age = None
                out.append({'id': m['id'], 'name': m['name'], 'relation': m.get('relation', ''),
                            'birthday': bd or ('农历' + str(m.get('birthday_lunar', ''))),
                            'days_until': days, 'age': age})

            for m in self.get_members():
                # ── 公历轨 ──
                b = str(m.get('birthday') or '').strip()
                if len(b) >= 10:
                    try:
                        bd = datetime.strptime(b[:10], '%Y-%m-%d').date()
                        this_year = bd.replace(year=today.year)
                        if this_year >= today:
                            _push(m, (this_year - today).days)
                    except ValueError:
                        pass
                # ── 农历轨（birthday_lunar: MM-DD）──
                ln = str(m.get('birthday_lunar') or '').strip()
                if ln and m['id'] not in seen:
                    try:
                        clean = ln.replace('月', '-').replace('日', '').replace('初', '')
                        parts = [p for p in clean.split('-') if p.isdigit()]
                        if len(parts) >= 2:
                            lm, ld = int(parts[0]), int(parts[1])
                            from zhdate import ZhDate
                            zh = ZhDate(today.year, lm, ld).to_datetime().date()
                            if zh >= today:
                                _push(m, (zh - today).days)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f'[PanelManager] 生日扫描失败: {e}')
        return out

    def add_member(self, name: str, color: str = '', role: str = '员工') -> dict:
        """新增成员（★ #807 role：场景角色标签，公司页可选 管理员/负责人/员工）"""
        name = (name or '').strip()
        role = (role or '员工').strip()
        if role not in ('管理员', '负责人', '员工'):
            role = '员工'
        if not name:
            raise ValueError('成员名字不能为空')
        if len(name) > 20:
            raise ValueError('名字太长了，20 个字以内吧')
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            self._ensure_members_table(conn)
            dup = conn.execute('SELECT id FROM panel_members WHERE name = ?', [name]).fetchone()
            if dup:
                conn.close()
                raise ValueError(f'已经有叫「{name}」的成员了')
            conn.execute('INSERT INTO panel_members (name, color, role, created_at) VALUES (?, ?, ?, ?)',
                         [name, color, role, datetime.now().isoformat()])
            conn.commit()
            conn.close()
        except ValueError:
            raise
        except Exception as e:
            logger.error(f'[PanelManager] 新增成员失败: {e}')
            raise ValueError('保存失败，请重试')
        return {'name': name, 'color': color, 'role': role}

    def delete_member(self, member_id: int) -> bool:
        """删除家庭成员（已有记录里的名字保留不动，只是以后选不到这个人了）"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            cur = conn.execute('DELETE FROM panel_members WHERE id = ?', [member_id])
            conn.commit()
            conn.close()
            return cur.rowcount > 0
        except Exception as e:
            logger.error(f'[PanelManager] 删除成员失败: {e}')
            return False

    def get_panel_data_count(self, panel_id: str) -> int:
        """获取面板数据总数"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            table_name = f'panel_{panel_id}'
            row = conn.execute(f'SELECT COUNT(*) as cnt FROM {table_name}').fetchone()
            conn.close()
            return row['cnt'] if row else 0
        except Exception:
            return 0


# ═══════════════════════ 全局单例 ═══════════════════════

_panel_manager: PanelManager | None = None
_pm_lock = threading.Lock()


def get_panel_manager() -> PanelManager:
    """获取面板管理器单例"""
    global _panel_manager
    if _panel_manager is None:
        with _pm_lock:
            if _panel_manager is None:
                _panel_manager = PanelManager()
    return _panel_manager
