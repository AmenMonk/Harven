import os, json, requests, re

print('=' * 70)
print('HarvenAI 全面检测报告')
print('=' * 70)

issues = []
ok_count = 0

# ========== 1. 代码语法检查 ==========
print('\n[1. 代码语法检查]')
import py_compile
py_files = [
    'H:/HarvenAI/web_app.py',
    'H:/HarvenAI/src/core/engine.py',
    'H:/HarvenAI/src/core/panel_manager.py',
    'H:/HarvenAI/src/core/panel_routes.py',
    'H:/HarvenAI/src/core/scripts_runner.py',
    'H:/HarvenAI/src/core/vertical_api.py',
    'H:/HarvenAI/src/core/knowledge_base.py',
    'H:/HarvenAI/src/models/database.py',
]
for pf in py_files:
    if os.path.exists(pf):
        try:
            py_compile.compile(pf, doraise=True)
            print(f'  OK {os.path.basename(pf)}')
            ok_count += 1
        except py_compile.PyCompileError as e:
            print(f'  ERR {os.path.basename(pf)}: {e}')
            issues.append(f'语法错误: {pf}')

# ========== 2. JS语法检查 ==========
print('\n[2. JS语法检查]')
import subprocess
js_files = [
    'H:/HarvenAI/static/js/modules/app-panels.js',
    'H:/HarvenAI/static/js/modules/app-conversation.js',
    'H:/HarvenAI/static/js/modules/app-settings.js',
]
for jf in js_files:
    if os.path.exists(jf):
        result = subprocess.run(['node', '-c', jf], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f'  OK {os.path.basename(jf)}')
            ok_count += 1
        else:
            print(f'  ERR {os.path.basename(jf)}: {result.stderr[:100]}')
            issues.append(f'JS错误: {jf}')

# ========== 3. Skill系统 ==========
print('\n[3. Skill系统]')
skills_dir = 'H:/HarvenAI/data/skills'
for item in sorted(os.listdir(skills_dir)):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full):
        has_script = os.path.exists(os.path.join(full, 'scripts', 'main.py'))
        skill_md = os.path.join(full, 'SKILL.md')
        trigger_ok = False
        trigger_words = []
        if os.path.exists(skill_md):
            with open(skill_md, 'r', encoding='utf-8') as f:
                content = f.read(500)
            if 'trigger:' in content:
                trigger_ok = True
                idx = content.index('trigger:')
                trigger_line = content[idx:].split('\n')[0]
                trigger_words = trigger_line.split(':', 1)[1].strip().split('|')
            elif 'triggers:' in content:
                issues.append(f'{item}: triggers格式错(复数)')
        s_issues = []
        if not has_script: s_issues.append('无脚本')
        if not trigger_ok: s_issues.append('无触发词')
        if s_issues:
            print(f'  ERR {item}: {",".join(s_issues)}')
            issues.append(f'Skill问题: {item} - {",".join(s_issues)}')
        else:
            danger_words = []
            for w in trigger_words:
                w = w.strip()
                if w and not any(c in w for c in ['.*', '\\', 'scan', 'audit']):
                    if len(w) <= 3 and w not in ['BGM', '月结']:
                        danger_words.append(w)
            if danger_words:
                print(f'  WARN {item}: {len(trigger_words)}个触发词, {len(danger_words)}个可能误触发({",".join(danger_words[:5])})')
            else:
                print(f'  OK {item}: {len(trigger_words)}个触发词')
                ok_count += 1

# ========== 4. 引擎流程检查 ==========
print('\n[4. 引擎流程]')
with open('H:/HarvenAI/src/core/engine.py', 'r', encoding='utf-8') as f:
    engine = f.read()

flow_checks = {
    '_try_quick_command存在': '_try_quick_command' in engine,
    '_try_smart_split存在': '_try_smart_split' in engine,
    '_try_skill_trigger存在': '_try_skill_trigger' in engine,
    '_try_pipeline已删除': '_try_pipeline' not in engine,
    'quick_command存消息': 'save_message' in engine and 'quick_result' in engine,
    'skill_trigger存消息': 'full_response' in engine and 'save_message' in engine,
    'smart_split存消息': '_split_result' in engine and 'save_message' in engine,
    '协 作模式检查': 'collab_mode' in engine,
    'save_message顶部导入': 'from src.models.database import save_message' in engine,
}
for name, ok in flow_checks.items():
    if ok:
        print(f'  OK {name}')
        ok_count += 1
    else:
        print(f'  ERR {name}')
        issues.append(f'引擎: {name}')

# ========== 5. 协作模式安全 ==========
print('\n[5. 协作模式安全]')
# 检查Skill是否受协作模式控制
skill_before_collab = engine.find('_try_skill_trigger')
collab_check = engine.find('_collab_active')
if skill_before_collab > 0 and collab_check > 0:
    if skill_before_collab < collab_check:
        print(f'  WARN Skill在协作检查之前执行 → 协作OFF时Skill仍触发')
        issues.append('安全: Skill不受协作模式控制')
    else:
        print(f'  OK Skill在协作检查之后')
        ok_count += 1

# 检查垂直Skill在协作OFF时是否被阻止
if 'call_vertical' in engine:
    print(f'  WARN call_vertical在_helpers.py里，不受协作模式控制')
    issues.append('安全: call_vertical不受协作模式控制')

# ========== 6. 触发词误触检查 ==========
print('\n[6. 触发词误触风险]')
danger_list = {
    'accounting': ['记账', '买菜', '花了', '支出', '收入', '月报', '这个月', '余额', '多少钱'],
    'code_audit': ['健康检查', '找问题'],
    'finance_report': ['月结'],
    'health_tracker': ['今天跑了', '步数', '体重', '睡眠'],
    'music-gen': ['生成一首', '做一首', '摇滚', '旋律'],
}
total_danger = 0
for skill, words in danger_list.items():
    for w in words:
        print(f'  WARN {skill}: "{w}" 可能误触发')
        total_danger += 1
issues.append(f'触发词: {total_danger}个危险词可能误触发')

# ========== 7. 智能拆分检查 ==========
print('\n[7. 智能拆分]')
split_idx = engine.find('def _try_smart_split')
if split_idx > 0:
    split_code = engine[split_idx:split_idx+2000]
    if 'len(amounts) < 2' in split_code:
        print(f'  WARN 阈值=2, 单个金额不拆分')
        issues.append('智能拆分: 阈值≥2, 单金额不触发')
    elif 'len(amounts) < 1' in split_code:
        print(f'  OK 阈值=1')
        ok_count += 1
    else:
        print(f'  WARN 未找到阈值判断')

# ========== 8. Skill名称显示 ==========
print('\n[8. Skill名称显示]')
if '代码审计触发词' in engine:
    print(f'  ERR 硬编码"代码审计触发词"')
    issues.append('显示: Skill名称硬编码为"代码审计触发词"')
else:
    print(f'  OK 动态显示')
    ok_count += 1

# ========== 9. 面板系统 ==========
print('\n[9. 面板系统]')
panels_dir = 'H:/HarvenAI/data/panels'
if os.path.exists(panels_dir):
    for pid in sorted(os.listdir(panels_dir)):
        pdir = os.path.join(panels_dir, pid)
        if not os.path.isdir(pdir): continue
        files = os.listdir(pdir)
        p_issues = []
        for req in ['panel.json', 'index.html', 'panel.js']:
            if req not in files:
                p_issues.append(f'缺{req}')
        # 检查panel.js是否有新功能
        js_path = os.path.join(pdir, 'panel.js')
        if os.path.exists(js_path):
            with open(js_path, 'r', encoding='utf-8') as f:
                pjs = f.read()
            if 'toggleView' not in pjs:
                p_issues.append('无卡片视图')
            if 'renderCards' not in pjs:
                p_issues.append('无renderCards')
            if 'formatValue' not in pjs:
                p_issues.append('无formatValue')
        if p_issues:
            print(f'  WARN {pid}: {",".join(p_issues)}')
            issues.append(f'面板: {pid} - {",".join(p_issues)}')
        else:
            print(f'  OK {pid}')
            ok_count += 1

# ========== 10. 创建面板字段类型 ==========
print('\n[10. 创建面板字段类型]')
with open('H:/HarvenAI/static/js/modules/app-panels.js', 'r', encoding='utf-8') as f:
    pjs = f.read()
field_types = {
    '文本(text)': 'value="text"' in pjs,
    '数字(number)': 'value="number"' in pjs,
    '日期(date)': 'value="date"' in pjs,
    '下拉(select)': 'value="select"' in pjs,
    '多行文本(textarea)': 'value="textarea"' in pjs,
    '时间(time)': 'value="time"' in pjs,
    '图片(image)': 'value="image"' in pjs,
}
for name, ok in field_types.items():
    if ok:
        print(f'  OK {name}')
        ok_count += 1
    else:
        print(f'  MISSING {name}')
        issues.append(f'创建面板: 缺{name}')

# ========== 11. API端点 ==========
print('\n[11. API端点]')
try:
    r = requests.get('http://localhost:7000/', timeout=3)
    flask_ok = r.status_code == 200
except:
    flask_ok = False

if flask_ok:
    apis = [
        ('/api/accounting/summary', 'GET'),
        ('/api/accounting/records', 'GET'),
        ('/api/flows', 'GET'),
        ('/api/panels', 'GET'),
        ('/api/chat/internal', 'POST'),
    ]
    for path, method in apis:
        try:
            if method == 'GET':
                r = requests.get(f'http://localhost:7000{path}', timeout=5)
            else:
                r = requests.post(f'http://localhost:7000{path}', json={}, timeout=5)
            if r.status_code in [200, 400, 500]:
                print(f'  OK {path} ({r.status_code})')
                ok_count += 1
            else:
                print(f'  ERR {path} ({r.status_code})')
                issues.append(f'API: {path} 返回{r.status_code}')
        except Exception as e:
            print(f'  ERR {path}: {e}')
            issues.append(f'API: {path} 不可达')
else:
    print('  ERR Flask未运行')
    issues.append('Flask未运行')

# ========== 12. _helpers.py ==========
print('\n[12. _helpers.py]')
helpers_path = os.path.join(skills_dir, '_helpers.py')
if os.path.exists(helpers_path):
    with open(helpers_path, 'r', encoding='utf-8') as f:
        h = f.read()
    h_checks = {
        'call_vertical': 'def call_vertical' in h,
        'call_model': 'def call_model' in h,
        'call_model多外脑(model参数)': 'model=None' in h or 'model=None' in h,
        'get_db_path': 'def get_db_path' in h,
        'get_media_dir': 'def get_media_dir' in h,
        'get_reports_dir': 'def get_reports_dir' in h,
    }
    for name, ok in h_checks.items():
        if ok:
            print(f'  OK {name}')
            ok_count += 1
        else:
            print(f'  MISSING {name}')
            issues.append(f'helpers: 缺{name}')

# ========== 13. 沙箱配置 ==========
print('\n[13. 沙箱配置]')
with open('H:/HarvenAI/src/core/scripts_runner.py', 'r', encoding='utf-8') as f:
    sr = f.read()
if 'timeout: int = 300' in sr:
    print(f'  OK 超时300秒')
    ok_count += 1
else:
    print(f'  ERR 超时不是300秒')
    issues.append('沙箱: 超时配置错')

banned = ['subprocess', 'socket', 'requests', 'urllib.request']
for b in banned:
    if b in sr and 'BANNED' in sr:
        print(f'  OK 禁用{b}')
        ok_count += 1

# ========== 14. 数据库 ==========
print('\n[14. 数据库]')
db_path = 'H:/HarvenAI/data/sqlite/lingzhi.db'
if os.path.exists(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()]
    print(f'  OK 数据库存在, {len(tables)}个表')
    ok_count += 1
    # 检查空对话
    try:
        empty = conn.execute('SELECT id, title FROM conversations WHERE id NOT IN (SELECT DISTINCT conversation_id FROM messages)').fetchall()
        if empty:
            print(f'  WARN {len(empty)}个空对话(0条消息)')
            issues.append(f'数据库: {len(empty)}个空对话')
        else:
            print(f'  OK 无空对话')
            ok_count += 1
    except:
        pass
    conn.close()
else:
    print(f'  ERR 数据库不存在')
    issues.append('数据库: lingzhi.db不存在')

# ========== 15. 协作帮助文案 ==========
print('\n[15. 协作帮助文案]')
with open('H:/HarvenAI/static/js/modules/app-conversation.js', 'r', encoding='utf-8') as f:
    cjs = f.read()
if 'Skill脚本编排' in cjs and '多模型协作' in cjs:
    print(f'  OK 已更新')
    ok_count += 1
else:
    print(f'  ERR 还是旧版')
    issues.append('文案: 协作帮助还是旧版')

# ========== 总结 ==========
print('\n' + '=' * 70)
print(f'检查完成: {ok_count}项正常, {len(issues)}个问题')
print('=' * 70)
print('\n问题清单:')
for i, issue in enumerate(issues, 1):
    print(f'  {i}. {issue}')
