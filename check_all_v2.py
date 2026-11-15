#!/usr/bin/env python3
"""HarvenAI 终极全面检测 v2"""
import os, sys, json, py_compile, sqlite3, zipfile, requests
from pathlib import Path

PROJECT = Path('H:/HarvenAI')
issues = []
ok = 0

def check(name, condition, detail=''):
    global ok
    if condition:
        ok += 1
        return f'✅ {name}'
    else:
        issues.append(f'{name}: {detail or "FAIL"}')
        return f'❌ {name}'

print('='*60)
print('HarvenAI 全面检测 v2')
print('='*60)

# ===== 1. Python语法 =====
print('\n【1. Python语法】')
py_files = []
for ext in ['src', 'scripts']:
    d = PROJECT / ext
    if d.exists():
        for r, ds, fs in os.walk(str(d)):
            if '__pycache__' in r: continue
            for f in fs:
                if f.endswith('.py') and not f.startswith('test_'):
                    py_files.append(os.path.join(r, f))
py_files.append(str(PROJECT / 'web_app.py'))
py_ok = 0
py_err = []
for p in py_files:
    try: py_compile.compile(p, doraise=True); py_ok += 1
    except Exception as e: py_err.append(f'{os.path.basename(p)}: {str(e)[:80]}')
print(f'  Python文件: {py_ok}/{len(py_files)} OK')
if py_err:
    for e in py_err: print(f'    {e}')
    issues.extend(py_err)

# ===== 2. 引擎流程 =====
print('\n【2. 引擎执行流程】')
e = (PROJECT / 'src/core/engine.py').read_text(encoding='utf-8')
flow = [
    ('_try_quick_command', '_try_quick_command' in e, '快速命令层'),
    ('_try_smart_split', '_try_smart_split' in e, '智能拆分'),
    ('_try_skill_trigger_match', '_try_skill_trigger_match' in e, 'Skill匹配'),
    ('save_message(快速命令)', 'save_message(self.conversation_id, "user", user_message)' in e and 'quick_result' in e, '存消息'),
    ('save_message(Skill)', 'save_message(self.conversation_id, "assistant", full_response)' in e, '存Skill结果'),
    ('collab检查', 'collab_mode' in e, '协作模式'),
    ('_try_pipeline已删', '_try_pipeline' not in e, '废弃代码清理'),
    ('阈值>=1', 'len(amounts) < 1' in e, '智能拆分'),
    ('流式执行', 'run_skill_script_streaming' in e, 'Skill流式'),
    ('call_model多外脑', 'model' in e and 'switch_foreign_model' in open(str(PROJECT/'web_app.py'),encoding='utf-8').read(), '多外脑'),
]
for name, cond, desc in flow:
    print(f'  {check(name, cond, desc)}')

# ===== 3. _helpers.py =====
print('\n【3. _helpers.py函数】')
h = (PROJECT / 'data/skills/_helpers.py').read_text(encoding='utf-8')
funcs = ['call_vertical', 'call_model', 'ask_user', 'check_gpu', 'get_db_path', 'get_media_dir', 'get_reports_dir']
for f in funcs:
    print(f'  {check(f, f"def {f}" in h)}')

# ===== 4. Skill系统 =====
print('\n【4. Skill系统】')
sd = PROJECT / 'data/skills'
skills = sorted([d for d in sd.iterdir() if d.is_dir() and not d.name.startswith('_')])
for s in skills:
    sm = s / 'SKILL.md'
    sp = s / 'scripts/main.py'
    has_trigger = sm.exists() and 'trigger:' in sm.read_text(encoding='utf-8-sig')[:500]
    has_script = sp.exists()
    ok_count = sum([has_trigger, has_script])
    label = 'SCRIPT' if has_script else 'PROMPT'
    print(f'  {check(s.name, ok_count>=1, f"{label} t={has_trigger} s={has_script}")}')

# ===== 5. 配置检查 =====
print('\n【5. 配置检查】')
cfg_path = PROJECT / 'config.example.yaml'
cfg_ex = cfg_path.read_text(encoding='utf-8') if cfg_path.exists() else ''
print(f'  {check("config.example.yaml存在", cfg_path.exists())}')
print(f'  {check("independence配置", "independence:" in cfg_ex)}')
print(f'  {check("whitelist_enabled: false", "whitelist_enabled: false" in cfg_ex, "默认不拦")}')
print(f'  {check("有OpenAI域名", "api.openai.com" in cfg_ex)}')
print(f'  {check("有8个API Key槽位", cfg_ex.count(": \"\"") >= 8, f"有{cfg_ex.count(': \"\"')}个")}')

# ===== 6. 面板系统 =====
print('\n【6. 面板系统】')
pd = PROJECT / 'data/panels'
if pd.exists():
    for p in pd.iterdir():
        if p.is_dir():
            has_json = (p/'panel.json').exists()
            has_html = (p/'index.html').exists()
            has_js = (p/'panel.js').exists()
            js_code = (p/'panel.js').read_text(encoding='utf-8') if has_js else ''
            has_card = 'toggleView' in js_code
            has_fmt = 'formatValue' in js_code
            print(f'  {check(p.name, has_json and has_html and has_js and has_card, f"卡片视图={has_card} 格式化={has_fmt}")}')
else:
    issues.append('data/panels/不存在')

# ===== 7. 安装脚本 =====
print('\n【7. 安装脚本】')
bats = ['安装哈文.bat', '启动HarvenAI.bat']
for b in bats:
    bp = PROJECT / b
    content = bp.read_text(encoding='utf-8') if bp.exists() else ''
    has_duck = 'duck.ico' in content or 'duck.png' in content or 'icon-duck' in content
    has_python = 'python' in content.lower()
    has_start = '启动' in content or 'start' in content.lower()
    print(f'  {check(b, bp.exists() and has_duck and has_start, f"鸭子图标={has_duck} 启动={has_start}")}')

# ===== 8. 图标检查 =====
print('\n【8. 图标检查】')
icons = ['static/icon-duck.png', 'static/icons/duck.ico', 'static/icons/duck_32.png', 
         'static/icons/duck_192.png', 'static/icons/duck_64.png', 'static/icons/duck_16.png']
for i in icons:
    ip = PROJECT / i
    print(f'  {check(i, ip.exists(), f"{ip.stat().st_size//1024}KB" if ip.exists() else "缺失")}')

# ===== 9. 数据库检查 =====
print('\n【9. 数据库检查】')
dbp = PROJECT / 'data/sqlite/lingzhi.db'
if dbp.exists():
    conn = sqlite3.connect(str(dbp))
    tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    print(f'  {check("数据库存在", True, f"{len(tables)}张表")}')
    needed = ['conversations', 'messages', 'skill_accounting']
    for t in needed:
        print(f'  {check(f"表{t}", t in tables)}')
else:
    print(f'  {check("数据库", False, "文件不存在")}')

# ===== 10. API端点测试 =====
print('\n【10. API端点测试】')
try:
    r = requests.get('http://localhost:7000/', timeout=3)
    flask_ok = r.status_code == 200
except:
    flask_ok = False

if flask_ok:
    apis = {
        '/api/accounting/summary': '记账汇总',
        '/api/flows': '流程列表',
        '/api/panels': '面板列表',
        '/api/skills/triggers': '触发词管理',
        '/api/scheduler/list': '定时任务',
        '/api/hub/flows': 'Hub流程',
        '/api/airllm/status': 'AirLLM',
        '/api/hardware/gpu': 'GPU信息',
        '/api/multi-model/list': '外脑列表',
        '/api/ask_user/pending': 'ask_user',
        '/api/outbound/status': '出站控制',
    }
    for api, desc in apis.items():
        try:
            r = requests.get(f'http://localhost:7000{api}', timeout=5)
            print(f'  {check(desc, r.status_code == 200, f"HTTP {r.status_code}")}')
        except Exception as ex:
            print(f'  {check(desc, False, str(ex)[:50])}')
else:
    print(f'  {check("Flask运行中", False, "Flask未启动")}')


# ===== 11. 废弃代码检查 =====
print('\n【11. 废弃代码检查】')
waste = [
    (PROJECT / 'skills', '旧skills/目录'),
    (PROJECT / '_package_staging/skills', 'staging旧skills/目录'),
    (PROJECT / 'src/core/try_pipeline', '废弃pipelines'),
]
for p, desc in waste:
    print(f'  {check(desc, not p.exists(), "已删除" if not p.exists() else "还在!")}')

# ===== 12. 页面功能检查 =====
print('\n【12. 页面功能检查】')
idx = (PROJECT / 'templates/index.html').read_text(encoding='utf-8')
pages = [
    ('设置-基本', 'switchSettingsTab' in idx),
    ('设置-高级功能', 'tabAdvancedFeatures' in idx),
    ('设置-流程编辑器', 'tabFlowEditor' in idx),
    ('HarvenHub入口', 'btnHarvenHub' in idx),
    ('多模型设置', 'multi_model' in idx.lower() or 'foreignModel' in idx or 'quickForeignModelSelect' in idx),
    ('协作模式帮助', 'showCollabHelp' in idx or 'Skill脚本编排' in idx),
    ('NUCLEAR覆盖', 'window.switchSettingsTab' in idx),
    ('ask_user前端', 'startAskUserPolling' in idx),
    ('面板管理modal有JS', 'applyPanelTemplate' in open(str(PROJECT/'static/js/modules/app-panels.js'),encoding='utf-8').read()),
]
for name, ok_flag in pages:
    print(f'  {check(name, ok_flag)}')

# ===== 13. 安装包检查 =====
print('\n【13. 安装包检查】')
zip_path = PROJECT / 'HarvenAI_v0.3.0.zip'
if zip_path.exists():
    z = zipfile.ZipFile(str(zip_path))
    names = z.namelist()
    zip_checks = [
        ('安装哈文.bat', '安装哈文.bat' in names),
        ('启动HarvenAI.bat', '启动HarvenAI.bat' in names),
        ('config.example.yaml', 'config.example.yaml' in names),
        ('duck.ico', 'static/icons/duck.ico' in names),
        ('icon-duck.png', 'static/icon-duck.png' in names),
        ('无旧skills/', 'skills/computer_use.py' not in names),
        ('code_audit动态路径', 'Path(__file__)' in z.read('data/skills/code_audit/scripts/main.py').decode('utf-8')),
        ('默认不拦截', '_whitelist_enabled: bool = False' in z.read('src/core/independence.py').decode('utf-8')),
    ]
    for name, ok_flag in zip_checks:
        print(f'  {check(name, ok_flag)}')
    print(f'  文件数: {len(names)}')
    print(f'  大小: {zip_path.stat().st_size//1024//1024}MB')
    z.close()
else:
    print(f'  {check("安装包存在", False)}')

# ===== 14. 桌面快捷方式 =====
print('\n【14. 桌面快捷方式】')
import subprocess
try:
    result = subprocess.run(['powershell', '-Command', 
        "Get-ChildItem ([Environment]::GetFolderPath('Desktop')) -Filter 'Harven AI.lnk' | Select-Object -ExpandProperty FullName"],
        capture_output=True, text=True, timeout=10)
    shortcut = result.stdout.strip()
    if shortcut:
        print(f'  ✅ 桌面快捷方式: {shortcut}')
    else:
        print(f'  ⚠️ 桌面快捷方式未找到')
except:
    print(f'  ⚠️ 无法检查桌面')

# ===== 总结 =====
print('\n' + '='*60)
total = ok + len(issues)
print(f'总计: {ok}/{total} OK')
if issues:
    print(f'问题: {len(issues)}个')
    for i, issue in enumerate(issues, 1):
        print(f'  {i}. {issue}')
else:
    print('全部通过! 🎉')
print('='*60)
