import os, json, requests, re, subprocess

issues = []
ok_count = 0

# 1. Python语法
import py_compile
py_files = []
for root, dirs, files in os.walk('H:/HarvenAI/src'):
    for f in files:
        if f.endswith('.py'):
            py_files.append(os.path.join(root, f))
py_files.append('H:/HarvenAI/web_app.py')
py_ok = 0
for pf in sorted(py_files):
    try:
        py_compile.compile(pf, doraise=True)
        py_ok += 1
    except Exception as e:
        issues.append('语法: ' + os.path.basename(pf))
print('[1. Python] OK ' + str(py_ok) + '/' + str(len(py_files)))
ok_count += py_ok

# 2. JS语法
js_files = []
for root, dirs, files in os.walk('H:/HarvenAI/static/js'):
    for f in files:
        if f.endswith('.js'):
            js_files.append(os.path.join(root, f))
js_ok = 0
for jf in sorted(js_files):
    result = subprocess.run(['node', '-c', jf], capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        js_ok += 1
    else:
        issues.append('JS: ' + os.path.basename(jf))
print('[2. JS] OK ' + str(js_ok) + '/' + str(len(js_files)))
ok_count += js_ok

# 3. Skill系统
skills_dir = 'H:/HarvenAI/data/skills'
skill_ok = 0
skill_total = 0
for item in sorted(os.listdir(skills_dir)):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full) and not item.startswith('_'):
        skill_total += 1
        has_script = os.path.exists(os.path.join(full, 'scripts', 'main.py'))
        skill_md = os.path.join(full, 'SKILL.md')
        has_trigger = False
        if os.path.exists(skill_md):
            with open(skill_md, 'rb') as f:
                if f.read(3) == b'\xef\xbb\xbf':
                    issues.append(item + ': BOM')
            with open(skill_md, 'r', encoding='utf-8-sig') as f:
                content = f.read(500)
            if 'trigger:' in content:
                has_trigger = True
            elif 'triggers:' in content:
                issues.append(item + ': triggers格式')
        if has_script and has_trigger:
            skill_ok += 1
        elif not has_script:
            pass  # 纯提示词不算错误
        elif not has_trigger:
            issues.append(item + ': 无触发词')
print('[3. Skill] OK ' + str(skill_ok) + '脚本 / ' + str(skill_total) + '总计')
ok_count += skill_ok

# 4. helpers
helpers = os.path.join(skills_dir, '_helpers.py')
with open(helpers, 'r', encoding='utf-8') as f:
    h = f.read()
funcs = ['call_vertical', 'call_model', 'ask_user', 'check_gpu', 'get_db_path', 'get_media_dir', 'get_reports_dir']
func_ok = sum(1 for fn in funcs if 'def ' + fn in h)
print('[4. helpers] OK ' + str(func_ok) + '/' + str(len(funcs)))
ok_count += func_ok

# 5. 引擎
with open('H:/HarvenAI/src/core/engine.py', 'r', encoding='utf-8') as f:
    engine = f.read()
checks = {
    'quick_command': '_try_quick_command' in engine,
    'smart_split': '_try_smart_split' in engine,
    'skill_match': '_try_skill_trigger_match' in engine,
    'pipeline_deleted': '_try_pipeline' not in engine,
    'split_threshold_1': 'len(amounts) < 1' in engine,
    'msg_save_quick': 'save_message' in engine and 'quick_result' in engine,
    'msg_save_skill': 'full_response' in engine and 'save_message' in engine,
    'collab_block_vertical': '_vertical_skills' in engine,
    'dynamic_skill_name': '_skill_name' in engine,
    'streaming': 'run_skill_script_streaming' in engine,
    'save_msg_import': 'from src.models.database import save_message' in engine,
    'collab_mode': 'collab_mode' in engine,
}
eng_ok = sum(checks.values())
for name, ok in checks.items():
    if not ok:
        issues.append('engine: ' + name)
print('[5. engine] OK ' + str(eng_ok) + '/' + str(len(checks)))
ok_count += eng_ok

# 6. 触发词安全
danger_count = 0
for item in sorted(os.listdir(skills_dir)):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full) and not item.startswith('_'):
        skill_md = os.path.join(full, 'SKILL.md')
        if os.path.exists(skill_md):
            with open(skill_md, 'r', encoding='utf-8-sig') as f:
                content = f.read(500)
            if 'trigger:' in content:
                idx = content.index('trigger:')
                line = content[idx:].split('\n')[0].split(':', 1)[1].strip()
                words = [w.strip() for w in line.split('|') if w.strip() and '.*' not in w and '\\' not in w]
                danger = [w for w in words if len(w) <= 3 and w not in ['BGM', '月结', 'audit']]
                danger_count += len(danger)
if danger_count > 0:
    issues.append('trigger: ' + str(danger_count) + '短词')
print('[6. trigger] ' + str(danger_count) + '个短词可能误触')
if danger_count == 0:
    ok_count += 1

# 7. 面板
panels_dir = 'H:/HarvenAI/data/panels'
panel_ok = 0
for pid in sorted(os.listdir(panels_dir)):
    pdir = os.path.join(panels_dir, pid)
    if not os.path.isdir(pid):
        continue
    p_issues = []
    js_path = os.path.join(pdir, 'panel.js')
    if os.path.exists(js_path):
        with open(js_path, 'r', encoding='utf-8') as f:
            pjs = f.read()
        if 'toggleView' in pjs and 'renderCards' in pjs:
            panel_ok += 1
        else:
            issues.append('panel: ' + pid)
print('[7. panels] OK ' + str(panel_ok))
ok_count += panel_ok

# 8. 字段类型
with open('H:/HarvenAI/static/js/modules/app-panels.js', 'r', encoding='utf-8') as f:
    pjs = f.read()
types_ok = sum(1 for v in ['text', 'number', 'date', 'select', 'textarea', 'time', 'image'] if 'value="' + v + '"' in pjs)
print('[8. field types] OK ' + str(types_ok) + '/7')
ok_count += types_ok

# 9. API
try:
    r = requests.get('http://localhost:7000/', timeout=3)
    flask_ok = r.status_code == 200
except:
    flask_ok = False
if flask_ok:
    apis = ['/api/accounting/summary', '/api/accounting/records', '/api/flows', '/api/panels',
            '/api/skills/triggers', '/api/scheduler/list', '/api/hub/flows', '/api/airllm/status',
            '/api/hardware/gpu', '/api/multi-model/list', '/api/ask_user/pending']
    api_ok = 0
    for api in apis:
        try:
            r = requests.get('http://localhost:7000' + api, timeout=5)
            if r.status_code == 200:
                api_ok += 1
            else:
                issues.append('API: ' + api + ' ' + str(r.status_code))
        except:
            issues.append('API: ' + api)
    print('[9. API] OK ' + str(api_ok) + '/' + str(len(apis)))
    ok_count += api_ok
else:
    print('[9. API] Flask未运行')
    issues.append('Flask未运行')

# 10. 沙箱
with open('H:/HarvenAI/src/core/scripts_runner.py', 'r', encoding='utf-8') as f:
    sr = f.read()
sb_ok = sum([1 if 'timeout: int = 300' in sr else 0,
             1 if 'run_skill_script_streaming' in sr else 0])
print('[10. sandbox] OK ' + str(sb_ok) + '/2')
ok_count += sb_ok

# 11. 调度器
if os.path.exists('H:/HarvenAI/src/core/task_scheduler.py'):
    print('[11. scheduler] OK')
    ok_count += 1
else:
    issues.append('scheduler: 缺失')

# 12. AirLLM
if os.path.exists('H:/HarvenAI/src/core/airllm_integration.py'):
    print('[12. AirLLM] OK')
    ok_count += 1
else:
    issues.append('AirLLM: 缺失')

# 13. GitHub
gh = ['.gitignore', 'README.md', 'CONTRIBUTING.md', 'config.example.yaml']
gh_ok = sum(1 for f in gh if os.path.exists('H:/HarvenAI/' + f))
print('[13. GitHub] OK ' + str(gh_ok) + '/4')
ok_count += gh_ok

# 14. 协作文案
with open('H:/HarvenAI/static/js/modules/app-conversation.js', 'r', encoding='utf-8') as f:
    cjs = f.read()
if 'Skill脚本编排' in cjs:
    print('[14. collab help] OK')
    ok_count += 1
else:
    issues.append('collab help: 旧版')

# 15. ask_user前端
if 'startAskUserPolling' in cjs:
    print('[15. ask_user UI] OK')
    ok_count += 1
else:
    issues.append('ask_user UI: 缺失')

# 16. 多外脑UI
idx_html = open('H:/HarvenAI/templates/index.html', 'r', encoding='utf-8').read()
if 'quickForeignModelSelect' in idx_html:
    print('[16. multi-foreign UI] OK')
    ok_count += 1
else:
    issues.append('multi-foreign UI: 缺失')

# 17. 流程编辑器
fe_path = 'H:/HarvenAI/templates/includes/tab-流程编辑器.html'
if os.path.exists(fe_path):
    fe = open(fe_path, 'r', encoding='utf-8').read()
    fe_ok = sum([1 if 'tab-panel' in fe else 0, 1 if '_flowModels' in fe else 0, 1 if 'addFlowStep' in fe else 0, 1 if 'previewFlowCode' in fe else 0])
    print('[17. flow editor] OK ' + str(fe_ok) + '/4')
    ok_count += fe_ok
else:
    issues.append('flow editor: 缺失')

# 18. 高级功能面板
af_path = 'H:/HarvenAI/templates/includes/tab-高级功能.html'
if os.path.exists(af_path):
    af = open(af_path, 'r', encoding='utf-8').read()
    af_ok = sum([1 if 'tab-panel' in af else 0, 1 if 'loadTriggers' in af else 0, 1 if 'loadSchedTasks' in af else 0, 1 if 'generateSkill' in af else 0])
    print('[18. advanced features] OK ' + str(af_ok) + '/4')
    ok_count += af_ok
else:
    issues.append('advanced features: 缺失')

# 19. 导航
nav_ok = sum([1 if "switchSettingsTab('advanced'" in idx_html else 0,
              1 if "switchSettingsTab('floweditor'" in idx_html else 0,
              1 if "'tabAdvancedFeatures'" in idx_html else 0,
              1 if "'tabFlowEditor'" in idx_html else 0])
print('[19. navigation] OK ' + str(nav_ok) + '/4')
ok_count += nav_ok

# 20. 废弃代码
waste = 0
if '_try_pipeline' in engine:
    issues.append('waste: _try_pipeline')
    waste += 1
if engine.count('from src.models.database import save_message') > 1:
    issues.append('waste: save_message重复import')
    waste += 1
for item in os.listdir(skills_dir):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full):
        sm = os.path.join(full, 'SKILL.md')
        if os.path.exists(sm):
            with open(sm, 'r', encoding='utf-8-sig') as f:
                if 'triggers:' in f.read(500):
                    issues.append('waste: ' + item + ' triggers')
                    waste += 1
                    break
if waste == 0:
    print('[20. waste] OK 无废弃')
    ok_count += 1
else:
    print('[20. waste] ' + str(waste) + '个废弃')

# 21. 数据库
db_path = 'H:/HarvenAI/data/sqlite/lingzhi.db'
if os.path.exists(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    tables = conn.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()
    print('[21. database] OK ' + str(len(tables)) + '表')
    ok_count += 1
    conn.close()
else:
    issues.append('database: 不存在')

# 总结
print('')
print('=' * 60)
print('总计: ' + str(ok_count) + '项正常, ' + str(len(issues)) + '个问题')
print('=' * 60)
if issues:
    print('问题:')
    for i, iss in enumerate(issues, 1):
        print('  ' + str(i) + '. ' + iss)
else:
    print('ALL OK!')
