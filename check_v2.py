import os, json, requests, subprocess, py_compile, sqlite3

issues = []
ok = 0

# Python
py = []
for r, d, fs in os.walk('src'):
    for f in fs:
        if f.endswith('.py'): py.append(os.path.join(r,f))
py.append('web_app.py')
pyok = 0
for p in py:
    try: py_compile.compile(p, doraise=True); pyok += 1
    except: issues.append('py:'+os.path.basename(p))
print('Python: %d/%d' % (pyok, len(py)))
ok += pyok

# JS
js = []
for r, d, fs in os.walk('static/js'):
    for f in fs:
        if f.endswith('.js'): js.append(os.path.join(r,f))
jsok = 0
for j in js:
    r2 = subprocess.run(['node','-c',j],capture_output=True,text=True,timeout=10)
    if r2.returncode==0: jsok += 1
    else: issues.append('js:'+os.path.basename(j))
print('JS: %d/%d' % (jsok, len(js)))
ok += jsok

# Engine
e = open('src/core/engine.py','r',encoding='utf-8').read()
ec = [
    ('_try_quick_command' in e),
    ('_try_smart_split' in e),
    ('_try_skill_trigger_match' in e),
    ('_try_pipeline' not in e),
    ('len(amounts) < 1' in e),
    ('save_message' in e),
    ('_vertical_skills' in e),
    ('_skill_name' in e),
    ('run_skill_script_streaming' in e),
    ('collab_mode' in e),
]
print('Engine: %d/%d' % (sum(ec), len(ec)))
ok += sum(ec)

# Helpers
h = open('data/skills/_helpers.py','r',encoding='utf-8').read()
fns = ['call_vertical','call_model','ask_user','check_gpu','get_db_path','get_media_dir','get_reports_dir']
fk = sum(1 for f in fns if 'def '+f in h)
print('Helpers: %d/%d' % (fk, len(fns)))
ok += fk

# Skills
sd = 'data/skills'
sk = 0
st = 0
for item in os.listdir(sd):
    f = os.path.join(sd, item)
    if os.path.isdir(f) and not item.startswith('_'):
        st += 1
        hs = os.path.exists(os.path.join(f, 'scripts', 'main.py'))
        sm = os.path.join(f, 'SKILL.md')
        ht = False
        if os.path.exists(sm):
            ht = 'trigger:' in open(sm, 'r', encoding='utf-8-sig').read(500)
        if hs and ht: sk += 1
print('Skills: %d script / %d total' % (sk, st))
ok += sk

# API
try:
    r3 = requests.get('http://localhost:7000/', timeout=3)
    fo = r3.status_code == 200
except:
    fo = False
if fo:
    apis = ['/api/accounting/summary','/api/flows','/api/panels','/api/skills/triggers','/api/scheduler/list','/api/hub/flows','/api/airllm/status','/api/hardware/gpu','/api/multi-model/list','/api/ask_user/pending']
    aok = 0
    for a in apis:
        try:
            r4 = requests.get('http://localhost:7000'+a, timeout=5)
            if r4.status_code == 200: aok += 1
            else: issues.append('api:'+a)
        except: issues.append('api:'+a)
    print('API: %d/%d' % (aok, len(apis)))
    ok += aok
else:
    print('API: Flask not running')
    issues.append('Flask not running')

# Other features
sr = open('src/core/scripts_runner.py','r',encoding='utf-8').read()
print('Sandbox 300s: %s' % ('timeout: int = 300' in sr))
print('Streaming: %s' % ('run_skill_script_streaming' in sr))
print('Scheduler: %s' % os.path.exists('src/core/task_scheduler.py'))
print('AirLLM: %s' % os.path.exists('src/core/airllm_integration.py'))
gh = sum(1 for f in ['.gitignore','README.md','CONTRIBUTING.md','config.example.yaml'] if os.path.exists(f))
print('GitHub files: %d/4' % gh)
ok += gh

cjs = open('static/js/modules/app-conversation.js','r',encoding='utf-8').read()
print('Collab help updated: %s' % ('Skill脚本编排' in cjs))
print('ask_user UI: %s' % ('startAskUserPolling' in cjs))
if 'Skill脚本编排' in cjs: ok += 1
if 'startAskUserPolling' in cjs: ok += 1

idx = open('templates/index.html','r',encoding='utf-8').read()
print('Multi-foreign UI: %s' % ('quickForeignModelSelect' in idx))
print('Nav advanced: %s' % ("switchSettingsTab('advanced'" in idx))
print('Nav floweditor: %s' % ("switchSettingsTab('floweditor'" in idx))
if 'quickForeignModelSelect' in idx: ok += 1
if "switchSettingsTab('advanced'" in idx: ok += 1
if "switchSettingsTab('floweditor'" in idx: ok += 1

# Flow editor
fe = open('templates/includes/tab-流程编辑器.html','r',encoding='utf-8').read()
fe_ok = sum([1 if 'tab-panel' in fe else 0, 1 if '_flowModels' in fe else 0, 1 if 'addFlowStep' in fe else 0, 1 if 'previewFlowCode' in fe else 0])
print('Flow editor: %d/4' % fe_ok)
ok += fe_ok

# Advanced features
af = open('templates/includes/tab-高级功能.html','r',encoding='utf-8').read()
af_ok = sum([1 if 'tab-panel' in af else 0, 1 if 'loadTriggers' in af else 0, 1 if 'loadSchedTasks' in af else 0, 1 if 'generateSkill' in af else 0])
print('Advanced features: %d/4' % af_ok)
ok += af_ok

# Waste
w = 0
if '_try_pipeline' in e: w += 1; issues.append('waste:pipeline')
if e.count('from src.models.database import save_message') > 1: w += 1; issues.append('waste:dupimport')
print('Waste code: %d' % w)
if w == 0: ok += 1

# Database
if os.path.exists('data/sqlite/lingzhi.db'):
    conn = sqlite3.connect('data/sqlite/lingzhi.db')
    t = conn.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()
    print('Database: %d tables' % len(t))
    ok += 1
    conn.close()
else:
    print('Database: MISSING')
    issues.append('db missing')

# Summary
print('')
print('=' * 50)
print('TOTAL: %d OK, %d issues' % (ok, len(issues)))
print('=' * 50)
if issues:
    print('Issues:')
    for i, x in enumerate(issues, 1):
        print('  %d. %s' % (i, x))
else:
    print('ALL OK!')
