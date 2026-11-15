import os, json, requests

print('=' * 60)
print('HarvenAI 全面功能检查')
print('=' * 60)

# 1. Skill系统
print('\n[1. Skill系统]')
skills_dir = 'H:/HarvenAI/data/skills'
for item in sorted(os.listdir(skills_dir)):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full):
        has_script = os.path.exists(os.path.join(full, 'scripts', 'main.py'))
        skill_md = os.path.join(full, 'SKILL.md')
        has_trigger = False
        trigger_bad = False
        if os.path.exists(skill_md):
            with open(skill_md, 'r', encoding='utf-8') as f:
                content = f.read(500)
            if 'trigger:' in content:
                has_trigger = True
            elif 'triggers:' in content:
                has_trigger = True
                trigger_bad = True
        issues = []
        if not has_script: issues.append('无脚本')
        if not has_trigger: issues.append('无触发词')
        if trigger_bad: issues.append('触发词格式错(triggers复数)')
        status = 'OK' if not issues else 'ERR:' + ','.join(issues)
        print(f'  {item}: {status}')

# 2. helpers
print('\n[2. _helpers.py]')
helpers = os.path.join(skills_dir, '_helpers.py')
if os.path.exists(helpers):
    with open(helpers, 'r', encoding='utf-8') as f:
        content = f.read()
    funcs = [l.split('def ')[1].split('(')[0] for l in content.split('\n') if l.startswith('def ')]
    print(f'  functions: {funcs}')

# 3. 面板
print('\n[3. 面板系统]')
panels_dir = 'H:/HarvenAI/data/panels'
if os.path.exists(panels_dir):
    for pid in sorted(os.listdir(panels_dir)):
        pdir = os.path.join(panels_dir, pid)
        if not os.path.isdir(pdir): continue
        files = os.listdir(pdir)
        has_json = 'panel.json' in files
        has_html = 'index.html' in files
        has_js = 'panel.js' in files
        issues = []
        if not has_json: issues.append('缺panel.json')
        if not has_html: issues.append('缺index.html')
        if not has_js: issues.append('缺panel.js')
        # 检查HTML内容
        if has_html:
            with open(os.path.join(pdir, 'index.html'), 'r', encoding='utf-8') as f:
                html = f.read()
            has_chart = 'loadPanelChart' in html or 'summaryCards' in html
            has_card = 'cardViewWrap' in html or 'toggleView' in html
            if not has_chart: issues.append('无统计图表')
            if not has_card: issues.append('无卡片视图')
        status = 'OK' if not issues else 'WARN:' + ','.join(issues)
        print(f'  {pid}: {status}')

# 4. API
print('\n[4. API端点]')
apis = ['/api/accounting/summary', '/api/accounting/records', '/api/flows', '/api/panels']
for api in apis:
    try:
        r = requests.get(f'http://localhost:7000{api}', timeout=5)
        print(f'  {api}: {"OK" if r.status_code == 200 else "ERR "+str(r.status_code)}')
    except:
        print(f'  {api}: Flask未运行')

# 5. 引擎
print('\n[5. 引擎功能]')
with open('H:/HarvenAI/src/core/engine.py', 'r', encoding='utf-8') as f:
    engine = f.read()
checks = {
    '_try_quick_command': '_try_quick_command' in engine,
    '_try_smart_split': '_try_smart_split' in engine,
    '_try_skill_trigger': '_try_skill_trigger' in engine,
    '_try_pipeline已删': '_try_pipeline' not in engine,
    'save_message(快速命令)': 'quick_result' in engine and 'save_message' in engine,
    'save_message(Skill)': 'full_response' in engine and 'save_message' in engine,
    'collab_mode': 'collab_mode' in engine,
}
for name, ok in checks.items():
    print(f'  {name}: {"OK" if ok else "MISSING"}')

# 6. JS前端
print('\n[6. 前端JS]')
with open('H:/HarvenAI/static/js/modules/app-panels.js', 'r', encoding='utf-8') as f:
    pjs = f.read()
js_checks = {
    '模板选择(_cpTemplate)': '_cpTemplate' in pjs,
    '图标选择(_cpIconPicker)': '_cpIconPicker' in pjs,
    '统计图表(_cpChart)': '_cpChart' in pjs,
    '搜索选项(_cpSearch)': '_cpSearch' in pjs,
    '预览(_cpPreview)': '_cpPreview' in pjs,
    'applyPanelTemplate': '_applyPanelTemplate' in pjs,
    'selectIcon': '_selectIcon' in pjs,
    '卡片视图(toggleView)': 'toggleView' in pjs,
    '多行文本(textarea)': 'textarea' in pjs,
    '时间(type=time)': 'type="time"' in pjs or "type='time'" in pjs,
    '图片(type=file)': 'type="file"' in pjs,
}
for name, ok in js_checks.items():
    print(f'  {name}: {"OK" if ok else "MISSING"}')

# 7. 后端panel_manager
print('\n[7. panel_manager后端]')
with open('H:/HarvenAI/src/core/panel_manager.py', 'r', encoding='utf-8') as f:
    pm = f.read()
pm_checks = {
    'with_chart参数': 'with_chart' in pm,
    'with_search参数': 'with_search' in pm,
    'textarea字段': 'textarea' in pm,
    'time字段': 'time' in pm,
    'image字段': 'file' in pm,
    'renderCards': 'renderCards' in pm,
    'formatValue': 'formatValue' in pm,
    'toggleView': 'toggleView' in pm,
}
for name, ok in pm_checks.items():
    print(f'  {name}: {"OK" if ok else "MISSING"}')

# 8. 协作文案
print('\n[8. 协作帮助文案]')
with open('H:/HarvenAI/static/js/modules/app-conversation.js', 'r', encoding='utf-8') as f:
    cjs = f.read()
print(f'  已更新: {"OK" if "Skill脚本编排" in cjs else "还是旧版"}')

# 9. 现有面板是否有新功能
print('\n[9. 已有面板新功能状态]')
accounting_html = 'H:/HarvenAI/data/panels/accounting/index.html'
if os.path.exists(accounting_html):
    with open(accounting_html, 'r', encoding='utf-8') as f:
        ah = f.read()
    print(f'  accounting统计图表: {"OK" if "loadSummary" in ah or "summaryCards" in ah else "MISSING"}')
    print(f'  accounting卡片视图: {"OK" if "cardViewWrap" in ah or "toggleView" in ah else "MISSING"}')

print('\n' + '=' * 60)
print('检查完成')
