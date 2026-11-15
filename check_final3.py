import os, sys, json, requests, subprocess, py_compile

issues = []
ok_count = 0
output = []

def log(s):
    output.append(s)

# 1
py_files = []
for root, dirs, files in os.walk('H:/HarvenAI/src'):
    for f in files:
        if f.endswith('.py'):
            py_files.append(os.path.join(root, f))
py_files.append('H:/HarvenAI/web_app.py')
py_ok = 0
for pf in py_files:
    try:
        py_compile.compile(pf, doraise=True)
        py_ok += 1
    except:
        issues.append('py: ' + os.path.basename(pf))
log('[1. Python] ' + str(py_ok) + '/' + str(len(py_files)))
ok_count += py_ok

# 2
js_files = []
for root, dirs, files in os.walk('H:/HarvenAI/static/js'):
    for f in files:
        if f.endswith('.js'):
            js_files.append(os.path.join(root, f))
js_ok = 0
for jf in js_files:
    r = subprocess.run(['node', '-c', jf], capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        js_ok += 1
    else:
        issues.append('js: ' + os.path.basename(jf))
log('[2. JS] ' + str(js_ok) + '/' + str(len(js_files)))
ok_count += js_ok

# 3
skills_dir = 'H:/HarvenAI/data/skills'
skill_ok = 0
skill_total = 0
for item in os.listdir(skills_dir):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full) and not item.startswith('_'):
        skill_total += 1
        has_script = os.path.exists(os.path.join(full, 'scripts', 'main.py'))
        sm = os.path.join(full, 'SKILL.md')
        has_trigger = False
        if os.path.exists(sm):
            with open(sm, 'r', encoding='utf-8-sig') as f:
                c = f.read(500)
            if 'trigger:' in c:
                has_trigger = True
        if has_script and has_trigger:
            skill_ok += 1
log('[3. Skills] ' + str(skill_ok) + 'script/' + str(skill_total) + 'total')
ok_count += skill_ok

# 4
h = open(os.path.join(skills_dir, '_helpers.py'), 'r', encoding='utf-8').read()
funcs = ['call_vertical','call_model','ask_user','check_gpu','get_db_path','get_media_dir','get_reports_dir']
log('[4. helpers] ' + str(sum(1 for f in funcs if 'def '+f in h)) + '/' + str(len(funcs)))
ok_count += sum(1 for f in funcs if 'def '+f in h)

# 5
engine = open('H:/HarvenAI/src/core/engine.py','r',encoding='utf-8').read()
ec = {'quick_cmd':'_try_quick_command' in engine,'smart_split':'_try_smart_split' in engine,'skill_match':'_try_skill_trigger_match' in engine,'no_pipeline':'_try_pipeline' not in engine,'split_ge1':'len(amounts) < 1' in engine,'msg_save':'save_message' in engine,'collab_block':'_vertical_skills' in engine,'dynamic_name':'_skill_name' in engine,'streaming':'run_skill_script_streaming' in engine,'save_import':'from src.models.database import save_message' in engine,'collab':'collab_mode' in engine,'msg_save_split':'_split_result' in engine}
log('[5. engine] ' + str(sum(ec.values())) + '/' + str(len(ec)))
ok_count += sum(ec.values())
for n,ok in ec.items():
    if not ok: issues.append('engine: '+n)

# 6
danger = 0
for item in os.listdir(skills_dir):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full) and not item.startswith('_'):
        sm = os.path.join(full, 'SKILL.md')
        if os.path.exists(sm):
            with open(sm, 'r', encoding='utf-8-sig') as f:
                c = f.read(500)
            if 'trigger:' in c:
                idx = c.index('trigger:')
                line = c[idx:].split('\n')[0].split(':',1)[1].strip()
                words = [w.strip() for w in line.split('|') if w.strip() and '.*' not in w and '\\' not in w]
                danger += len([w for w in words if len(w)<=3 and w not in ['BGM','月结','audit']])
log('[6. triggers] ' + str(danger) + ' short words')
if danger == 0: ok_count += 1
else: issues.append('trigger: '+str(danger)+' short')

# 7
panel_ok = 0
panels_dir = 'H:/HarvenAI/data/panels'
for pid in os.listdir(panels_dir):
    pdir = os.path.join(panels_dir, pid)
    if os.path.isdir(pdir):
        jp = os.path.join(pdir, 'panel.js')
        if os.path.exists(jp):
            pjs = open(jp,'r',encoding='utf-8').read()
            if 'toggleView' in pjs and 'renderCards' in pjs:
                panel_ok += 1
log('[7. panels] ' + str(panel_ok))
ok_count += panel_ok

# 8
pjs = open('H:/HarvenAI/static/js/modules/app-panels.js','r',encoding='utf-8').read()
ft = sum(1 for v in ['text','number','date','select','textarea','time','image'] if 'value="'+v+'"' in pjs)
log('[8. field types] ' + str(ft) + '/7')
ok_count += ft

# 9
try:
    r = requests.get('http://localhost:7000/', timeout=3)
    flask_ok = r.status_code == 200
except:
    flask_ok = False
if flask_ok:
    apis = ['/api/accounting/summary','/api/flows','/api/panels','/api/skills/triggers','/api/scheduler/list','/api/hub/flows','/api/airllm/status','/api/hardware/gpu','/api/multi-model/list','/api/ask_user/pending']
    api_ok = 0
    for api in apis:
        try:
            r = requests.get('http://localhost:7000'+api, timeout=5)
            if r.status_code == 200: api_ok += 1
            else: issues.append('api: '+api)
        except:
            issues.append('api: '+api)
    log('[9. API] ' + str(api_ok) + '/' + str(len(apis)))
    ok_count += api_ok
else:
    log('[9. API] Flask not running')
    issues.append('Flask not running')

# 10-21
sr = open('H:/HarvenAI/src/core/scripts_runner.py','r',encoding='utf-8').read()
log('[10. sandbox] ' + str(sum([1 if 'timeout: int = 300' in sr else 0, 1 if 'run_skill_script_streaming' in sr else 0])) + '/2')
ok_count += sum([1 if 'timeout: int = 300' in sr else 0, 1 if 'run_skill_script_streaming' in sr else 0])

log('[11. scheduler] ' + ('OK' if os.path.exists('H:/HarvenAI/src/core/task_scheduler.py') else 'MISSING'))
if os.path.exists('H:/HarvenAI/src/core/task_scheduler.py'): ok_count += 1
else: issues.append('scheduler missing')

log('[12. AirLLM] ' + ('OK' if os.path.exists('H:/HarvenAI/src/core/airllm_integration.py') else 'MISSING'))
if os.path.exists('H:/HarvenAI/src/core/airllm_integration.py'): ok_count += 1
else: issues.append('airllm missing')

gh = ['.gitignore','README.md','CONTRIBUTING.md','config.example.yaml']
gh_ok = sum(1 for f in gh if os.path.exists('H:/HarvenAI/'+f))
log('[13. GitHub] ' + str(gh_ok) + '/4')
ok_count += gh_ok

cjs = open('H:/HarvenAI/static/js/modules/app-conversation.js','r',encoding='utf-8').read()
log('[14. collab help] ' + ('OK' if 'Skill脚本编排' in cjs else 'OLD'))
if 'Skill脚本编排' in cjs: ok_count += 1
else: issues.append('collab help old')

log('[15. ask_user UI] ' + ('OK' if 'startAskUserPolling' in cjs else 'MISSING'))
if 'startAskUserPolling' in cjs: ok_count += 1
else: issues.append('ask_user ui missing')

idx = open('H:/HarvenAI/templates/index.html','r',encoding='utf-8').read()
log('[16. multi-foreign] ' + ('OK' if 'quickForeignModelSelect' in idx else 'MISSING'))
if 'quickForeignModelSelect' in idx: ok_count += 1
else: issues.append('multi-foreign missing')

fe = open('H:/HarvenAI/templates/includes/tab-流程编辑器.html','r',encoding='utf-8').read() if os.path.exists('H:/HarvenAI/templates/includes/tab-流程编辑器.html') else ''
fe_ok = sum([1 if 'tab-panel' in fe else 0, 1 if '_flowModels' in fe else 0, 1 if 'addFlowStep' in fe else 0, 1 if 'previewFlowCode' in fe else 0])
log('[17. flow editor] ' + str(fe_ok) + '/4')
ok_count += fe_ok

af = open('H:/HarvenAI/templates/includes/tab-高级功能.html','r',encoding='utf-8').read() if os.path.exists('H:/HarvenAI/templates/includes/tab-高级功能.html') else ''
af_ok = sum([1 if 'tab-panel' in af else 0, 1 if 'loadTriggers' in af else 0, 1 if 'loadSchedTasks' in af else 0, 1 if 'generateSkill' in af else 0])
log('[18. advanced] ' + str(af_ok) + '/4')
ok_count += af_ok

nav = sum([1 if "switchSettingsTab('advanced'" in idx else 0, 1 if "switchSettingsTab('floweditor'" in idx else 0, 1 if "'tabAdvancedFeatures'" in idx else 0, 1 if "'tabFlowEditor'" in idx else 0])
log('[19. navigation] ' + str(nav) + '/4')
ok_count += nav

waste = 0
if '_try_pipeline' in engine: waste += 1; issues.append('waste: pipeline')
if engine.count('from src.models.database import save_message') > 1: waste += 1; issues.append('waste: dup import')
log('[20. waste] ' + ('OK' if waste == 0 else str(waste)))
if waste == 0: ok_count += 1

db = 'H:/HarvenAI/data/sqlite/lingzhi.db'
if os.path.exists(db):
    import sqlite3
    conn = sqlite3.connect(db)
    t = conn.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()
    log('[21. database] ' + str(len(t)) + ' tables')
    ok_count += 1
    conn.close()
else:
    log('[21. database] MISSING')
    issues.append('db missing')

# Summary
log('')
log('=' * 60)
log('TOTAL: ' + str(ok_count) + ' OK, ' + str(len(issues)) + ' issues')
log('=' * 60)
if issues:
    log('Issues:')
    for i, iss in enumerate(issues, 1):
        log('  ' + str(i) + '. ' + iss)
else:
    log('ALL OK!')

# Write to file
with open('H:/HarvenAI/check_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))
print('Done. See check_result.txt')
