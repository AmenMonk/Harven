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
}

# panel.json 必填字段
REQUIRED_PANEL_FIELDS = ['name', 'id', 'version', 'type']


class Panel:
    """面板对象"""

    def __init__(self, panel_id, name, version, panel_type, icon='file',
                 description='', author='user', fields=None, installed_at=None):
        self.id = panel_id
        self.name = name
        self.version = version
        self.type = panel_type
        self.icon = icon
        self.description = description
        self.author = author
        self.fields = fields or []
        self.installed_at = installed_at or datetime.now().isoformat()

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
            'installed_at': self.installed_at,
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
        )


class PanelManager:
    """面板管理器（单例）"""

    def __init__(self):
        self._lock = threading.Lock()
        PANELS_DIR.mkdir(parents=True, exist_ok=True)
        self._panels: dict[str, Panel] = {}
        self._scan_panels()
        logger.info(f'[PanelManager] 已初始化，扫描到 {len(self._panels)} 个面板')

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
            except json.JSONDecodeError as e:
                logger.error(f'[PanelManager] 解析 panel.json 失败 {panel_dir.name}: {e}')
            except Exception as e:
                logger.error(f'[PanelManager] 加载面板失败 {panel_dir.name}: {e}')

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
            for f in fields:
                fname = f.get('name', '')
                ftype = f.get('type', 'text')
                if not fname:
                    continue
                sql_type = FIELD_TYPE_MAP.get(ftype, 'TEXT')
                column_defs.append(f'"{fname}" {sql_type}')
            column_defs.append('created_at TEXT')
            column_defs.append('updated_at TEXT')

            create_sql = f'CREATE TABLE IF NOT EXISTS {table_name} ({", ".join(column_defs)})'
            conn.execute(create_sql)
            conn.commit()
            conn.close()
            logger.info(f'[PanelManager] 创建面板数据表: {table_name}')
        except Exception as e:
            logger.error(f'[PanelManager] 创建数据表失败 {panel_id}: {e}')

    def get_all_panels(self) -> list[dict]:
        """返回所有已安装面板的列表"""
        return [p.to_dict() for p in self._panels.values()]

    def get_panel(self, panel_id: str) -> Panel | None:
        """获取单个面板"""
        return self._panels.get(panel_id)

    def panel_exists(self, panel_id: str) -> bool:
        return panel_id in self._panels

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
                    zf.extractall(tmp)
            except zipfile.BadZipFile:
                return {'success': False, 'message': 'ZIP文件格式错误'}

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

            panel_id = data['id']
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

    def uninstall_panel(self, panel_id: str) -> dict:
        """卸载面板"""
        if panel_id not in self._panels:
            return {'success': False, 'message': f'面板 "{panel_id}" 不存在'}

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

        del self._panels[panel_id]
        return {'success': True, 'message': f'面板已卸载'}

    def create_panel_from_template(self, name: str, panel_id: str, fields: list,
                                    description: str = '', icon: str = 'file',
                                    with_chart: bool = False, with_search: bool = True) -> dict:
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
        }
        (panel_dir / 'panel.json').write_text(
            json.dumps(panel_data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

        # 2. 生成 index.html
        index_html = self._generate_table_html(panel_id, name, fields, icon, with_chart, with_search)
        (panel_dir / 'index.html').write_text(index_html, encoding='utf-8')

        # 3. 生成 panel.css
        panel_css = self._generate_panel_css()
        (panel_dir / 'panel.css').write_text(panel_css, encoding='utf-8')

        # 4. 生成 panel.js
        panel_js = self._generate_panel_js(panel_id, fields)
        (panel_dir / 'panel.js').write_text(panel_js, encoding='utf-8')

        # 重新扫描
        self._scan_panels()

        panel = self._panels.get(panel_id)
        return {
            'success': True,
            'message': f'面板 "{name}" 创建成功',
            'panel': panel.to_dict() if panel else None,
        }

    def _generate_table_html(self, panel_id: str, name: str, fields: list,
                             icon: str = 'file', with_chart: bool = False, with_search: bool = True) -> str:
        """根据字段定义生成表格面板HTML（支持统计图表+搜索选项）"""
        # ★ 2026-08-08: 统计卡片+图表
        chart_html = ''
        if with_chart:
            chart_html = '''
  <!-- ★ 统计卡片 -->
  <div class="summary-cards" id="summaryCards" style="display:none;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">
    <div style="background:rgba(255,255,255,0.03);border:0.5px solid var(--border,#333);border-radius:8px;padding:12px;text-align:center">
      <div style="font-size:11px;color:#8b949e;margin-bottom:4px">总记录</div><div style="font-size:20px;font-weight:bold;color:#58a6ff" id="chartTotal">0</div>
    </div>
    <div style="background:rgba(255,255,255,0.03);border:0.5px solid var(--border,#333);border-radius:8px;padding:12px;text-align:center">
      <div style="font-size:11px;color:#8b949e;margin-bottom:4px">分类数</div><div style="font-size:20px;font-weight:bold;color:#3fb950" id="chartCats">0</div>
    </div>
    <div style="background:rgba(255,255,255,0.03);border:0.5px solid var(--border,#333);border-radius:8px;padding:12px;text-align:center">
      <div style="font-size:11px;color:#8b949e;margin-bottom:4px">数值合计</div><div style="font-size:20px;font-weight:bold;color:#f85149" id="chartSum">0</div>
    </div>
  </div>
  <div id="catSection" style="display:none;background:rgba(255,255,255,0.02);border:0.5px solid var(--border,#333);border-radius:8px;padding:12px;margin-bottom:16px">
    <h3 style="font-size:13px;color:#58a6ff;margin-bottom:8px">📊 分类统计</h3>
    <div id="catChart"></div>
  </div>
  <button class="panel-btn" onclick="loadPanelChart()" style="margin-bottom:12px">📊 显示统计</button>
<script>
async function loadPanelChart(){
  try{
    var r=await fetch('/api/panels/''' + panel_id + '''/data').then(r=>r.json());
    var rows=r.rows||r.data||[];
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
        html+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px"><span style="width:70px;font-size:11px">'+c+'</span><div style="flex:1;height:16px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden"><div style="height:100%;width:'+pct+'%;background:'+colors[i%8]+';border-radius:3px"></div></div><span style="width:40px;text-align:right;font-size:11px;color:#8b949e">'+cats[c]+'</span></div>';
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
  </div>'''

        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}</title>
<link rel="stylesheet" href="/static/panel-api.css">
<link rel="stylesheet" href="/panel/{panel_id}/panel.css">
</head>
<body class="panel-body">
<div class="panel-container">
  <div class="panel-header">
    <h2>{icon} {name}</h2>
    <button class="panel-btn panel-btn-primary" onclick="PanelApp.showForm()">+ 添加</button>
  </div>
{chart_html}
{search_html}
  <div id="formArea" class="panel-form" style="display:none">
    {"".join(self._field_input_html(f) for f in fields)}
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
          {"".join(f'<th>{f.get("label", f.get("name",""))}</th>' for f in fields)}
          <th style="width:100px">操作</th>
        </tr>
      </thead>
      <tbody id="tableBody">
        <tr><td colspan="{len(fields)+1}" class="panel-empty">加载中...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="panel-pagination" id="pagination"></div>
</div>

<script src="/static/panel-api.js"></script>
<script src="/panel/{panel_id}/panel.js"></script>
<script>
  PanelApp.init('{panel_id}', {json.dumps(fields, ensure_ascii=False)});
  PanelApp.loadData();
</script>
</body>
</html>'''

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
      <textarea id="field_{fname}" {required} rows="4" style="width:100%;padding:8px;border-radius:6px;border:0.5px solid var(--border,#333);background:var(--bg-secondary,#222);color:var(--text-primary,#ddd);font-size:13px;resize:vertical;"></textarea>
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
            return f'''<div class="panel-field">
      <label>{flabel}</label>
      <input type="file" accept="image/*" id="field_{fname}" {required} onchange="if(this.files[0]){{var r=new FileReader();r.onload=function(e){{this.dataset.b64=e.target.result;}}.bind(this);r.readAsDataURL(this.files[0]);}}">
    </div>
'''
        else:
            return f'''<div class="panel-field">
      <label>{flabel}</label>
      <input type="text" id="field_{fname}" {required}>
    </div>
'''

    def _generate_panel_css(self) -> str:
        """生成面板通用样式"""
        return '''* { margin: 0; padding: 0; box-sizing: border-box; }
.panel-body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 13px; color: #ccc; background: #1a1a1f; padding: 20px; }
.panel-container { max-width: 1000px; margin: 0 auto; }
.panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.panel-header h2 { font-size: 18px; font-weight: 500; color: #e0e0e0; }
.panel-btn { padding: 6px 16px; border-radius: 6px; border: 0.5px solid #444; background: transparent; color: #bbb; cursor: pointer; font-size: 13px; }
.panel-btn-primary { background: #2d2d6b; border-color: #5a5ab5; color: #c0c0e0; }
.panel-btn-danger { color: #e05050; border-color: #804040; }
.panel-btn:hover { opacity: 0.85; }
.panel-search { margin-bottom: 12px; }
.panel-search input { width: 100%; padding: 8px 12px; border-radius: 6px; border: 0.5px solid #444; background: #222; color: #ddd; font-size: 13px; }
.panel-search input:focus { outline: none; border-color: #5a5ab5; }

.panel-form { background: #222; border-radius: 8px; padding: 16px; margin-bottom: 16px; border: 0.5px solid #333; }
.panel-field { margin-bottom: 10px; }
.panel-field label { display: block; color: #999; font-size: 12px; margin-bottom: 4px; }
.panel-field input, .panel-field select, .panel-field textarea { width: 100%; padding: 8px 10px; border-radius: 6px; border: 0.5px solid #444; background: #1a1a1f; color: #ddd; font-size: 13px; }
.panel-field input:focus, .panel-field select:focus, .panel-field textarea:focus { outline: none; border-color: #5a5ab5; }
.panel-field textarea { min-height: 60px; resize: vertical; }
.panel-form-actions { display: flex; gap: 8px; margin-top: 12px; }

.panel-table-wrap { overflow-x: auto; }
.panel-table { width: 100%; border-collapse: collapse; }
.panel-table th { text-align: left; padding: 10px 12px; background: #222; color: #999; font-weight: 500; font-size: 12px; border-bottom: 0.5px solid #333; }
.panel-table td { padding: 10px 12px; border-bottom: 0.5px solid #2a2a2a; color: #ccc; }
.panel-table tr:hover td { background: #222; }
.panel-empty { text-align: center; color: #666; padding: 30px !important; }
.panel-pagination { display: flex; justify-content: center; gap: 8px; margin-top: 16px; }
.panel-pagination button { padding: 4px 12px; border-radius: 4px; border: 0.5px solid #444; background: #222; color: #bbb; cursor: pointer; font-size: 12px; }
.panel-pagination button:disabled { opacity: 0.4; cursor: default; }
.panel-pagination span { color: #999; font-size: 12px; line-height: 28px; }
'''

    def _generate_panel_js(self, panel_id: str, fields: list) -> str:
        """生成面板JS逻辑（支持多行文本/时间/图片+卡片视图）"""
        return f'''// {panel_id} panel logic
const PanelApp = {{
  panelId: '{panel_id}',
  fields: {json.dumps(fields, ensure_ascii=False)},
  editingId: null,
  viewMode: 'table',  // ★ 2026-08-08: table | card

  init(id, flds) {{
    this.panelId = id;
    this.fields = flds;
  }},

  // ★ 2026-08-08: 切换视图模式
  toggleView() {{
    this.viewMode = this.viewMode === 'table' ? 'card' : 'table';
    const tableWrap = document.querySelector('.panel-table-wrap');
    const cardWrap = document.getElementById('cardViewWrap');
    if (this.viewMode === 'card') {{
      if (tableWrap) tableWrap.style.display = 'none';
      if (cardWrap) cardWrap.style.display = 'block';
    }} else {{
      if (tableWrap) tableWrap.style.display = 'block';
      if (cardWrap) cardWrap.style.display = 'none';
    }}
    this.loadData();
  }},

  // ★ 2026-08-08: 格式化字段值显示
  formatValue(f, val) {{
    if (!val) return '';
    if (f.type === 'image' && val.startsWith('data:image')) {{
      return '<img src="' + val + '" style="max-width:60px;max-height:60px;border-radius:4px;cursor:pointer" onclick="window.open(this.src)">';
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
    try {{
      const records = await HarvenPanel.query(this.panelId, filter);
      if (!records || records.length === 0) {{
        if (this.viewMode === 'card') {{
          const cardWrap = document.getElementById('cardViewWrap');
          if (cardWrap) cardWrap.innerHTML = '<div style="text-align:center;color:#8b949e;padding:40px;">暂无数据，点击"+添加"创建第一条</div>';
        }} else {{
          const tbody = document.getElementById('tableBody');
          if (tbody) tbody.innerHTML = '<tr><td colspan="' + (this.fields.length + 1) + '" class="panel-empty">暂无数据</td></tr>';
        }}
        return;
      }}
      if (this.viewMode === 'card') {{
        this.renderCards(records);
      }} else {{
        this.renderTable(records);
      }}
    }} catch(e) {{
      const tbody = document.getElementById('tableBody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="' + (this.fields.length + 1) + '" class="panel-empty">加载失败: ' + e.message + '</td></tr>';
    }}
  }},

  // ★ 2026-08-08: 表格视图
  renderTable(records) {{
    const tbody = document.getElementById('tableBody');
    if (!tbody) return;
    tbody.innerHTML = records.map(r => {{
      const cells = this.fields.map(f => '<td>' + this.formatValue(f, r[f.name]) + '</td>').join('');
      return '<tr>' + cells + '<td><button class="panel-btn" onclick="PanelApp.editRecord(' + r.id + ')" style="font-size:11px;padding:2px 8px">编辑</button> <button class="panel-btn panel-btn-danger" onclick="PanelApp.deleteRecord(' + r.id + ')" style="font-size:11px;padding:2px 8px">删除</button></td></tr>';
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
        return '<div style="margin-bottom:4px;"><span style="font-size:10px;color:#8b949e;">' + f.label + ':</span> <span style="font-size:12px;">' + val + '</span></div>';
      }}).join('');
      return '<div style="background:rgba(255,255,255,0.03);border:0.5px solid #30363d;border-radius:8px;padding:12px;">' + fields + '<div style="margin-top:8px;text-align:right;"><button class="panel-btn" onclick="PanelApp.editRecord(' + r.id + ')" style="font-size:11px;padding:2px 8px;">编辑</button> <button class="panel-btn panel-btn-danger" onclick="PanelApp.deleteRecord(' + r.id + ')" style="font-size:11px;padding:2px 8px;">删除</button></div></div>';
    }}).join('') + '</div>';
  }},

  showForm() {{
    this.editingId = null;
    document.getElementById('editId').value = '';
    this.fields.forEach(f => {{
      const el = document.getElementById('field_' + f.name);
      if (el) {{
        if (el.type === 'file') {{
          // 图片字段重置
          el.value = '';
        }} else {{
          el.value = '';
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
          if (el.type === 'file') {{
            // 图片字段编辑时显示旧图
            if (record[f.name]) {{
              var preview = document.createElement('div');
              preview.innerHTML = '<img src="' + record[f.name] + '" style="max-width:80px;max-height:80px;border-radius:4px;">';
              el.parentNode.appendChild(preview);
            }}
          }} else {{
            el.value = record[f.name] || '';
          }}
        }}
      }});
      document.getElementById('formArea').style.display = 'block';
    }});
  }},

  async saveRecord() {{
    const data = {{}};
    for (const f of this.fields) {{
      const el = document.getElementById('field_' + f.name);
      if (el) {{
        if (el.type === 'file' && el.files[0]) {{
          // 图片转base64
          const file = el.files[0];
          const b64 = await new Promise(resolve => {{
            const reader = new FileReader();
            reader.onload = e => resolve(e.target.result);
            reader.readAsDataURL(file);
          }});
          data[f.name] = b64;
        }} else {{
          data[f.name] = el.value;
        }}
      }}
    }}
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
'''

    # ── 面板数据操作方法 ──

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

    def insert_panel_data(self, panel_id: str, data: dict) -> dict:
        """插入面板数据"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            table_name = f'panel_{panel_id}'
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data['created_at'] = now
            data['updated_at'] = now
            keys = list(data.keys())
            placeholders = ', '.join(['?' for _ in keys])
            columns = ', '.join([f'"{k}"' for k in keys])
            sql = f'INSERT INTO {table_name} ({columns}) VALUES ({placeholders})'
            cursor = conn.execute(sql, list(data.values()))
            conn.commit()
            new_id = cursor.lastrowid
            conn.close()
            return {'id': new_id, **data}
        except Exception as e:
            logger.error(f'[PanelManager] 插入数据失败 {panel_id}: {e}')
            raise

    def update_panel_data(self, panel_id: str, row_id: int, data: dict) -> dict:
        """更新面板数据"""
        try:
            from src.models.database import get_connection
            conn = get_connection(str(DB_PATH))
            table_name = f'panel_{panel_id}'
            set_clauses = ', '.join([f'"{k}" = ?' for k in data.keys()])
            params = list(data.values()) + [row_id]
            # 自动更新 updated_at
            sql = f"UPDATE {table_name} SET {set_clauses}, updated_at = datetime('now','localtime') WHERE id = ?"
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            return {'id': row_id, **data}
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
            return True
        except Exception as e:
            logger.error(f'[PanelManager] 删除数据失败 {panel_id}: {e}')
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
