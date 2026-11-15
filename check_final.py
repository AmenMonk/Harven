import os, json, requests, re, subprocess, sys

print('=' * 70)
print('HarvenAI 最终全面检测报告')
print('=' * 70)

issues = []
ok_count = 0

# ========== 1. Python语法 ==========
print('\n[1. Python语法]')
py_files = []
for root, dirs, files in os.walk('H:/HarvenAI/src'):
    for f in files:
        if f.endswith('.py'):
            py_files.append(os.path.join(root, f))
py_files.append('H:/HarvenAI/web_app.py')
for pf in sorted(py_files):
    try:
        import py_compile
        py_compile.compile(pf, doraise=True)
        ok_count += 1
    except Exception as e:
        print(f'  ERR {os.path.relpath(pf, "H:/HarvenAI")}: {str(e)[:80]}')
        issues.append(f'语法错误: {os.path.basename(pf)}')
print(f'  OK {len(py_files)}个Python文件')

# ========== 2. JS语法 ==========
print('\n[2. JS语法]')
js_files = []
for root, dirs, files in os.walk('H:/HarvenAI/static/js'):
    for f in files:
        if f.endswith('.js'):
            js_files.append(os.path.join(root, f))
for jf in sorted(js_files):
    result = subprocess.run(['node', '-c', jf], capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        ok_count += 1
    else:
        print(f'  ERR {os.path.basename(jf)}: {result.stderr[:80]}')
        issues.append(f'JS错误: {os.path.basename(jf)}')
print(f'  OK {len(js_files)}个JS文件')

# ========== 3. Skill系统 ==========
print('\n[3. Skill系统]')
skills_dir = 'H:/HarvenAI/data/skills'
for item in sorted(os.listdir(skills_dir)):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full) and not item.startswith('_'):
        has_script = os.path.exists(os.path.join(full, 'scripts', 'main.py'))
        skill_md = os.path.join(full, 'SKILL.md')
        has_trigger = False
        trigger_bad = False
        trigger_count = 0
        if os.path.exists(skill_md):
            # 检查BOM
            with open(skill_md, 'rb') as f:
                if f.read(3) == b'\xef\xbb\xbf':
                    issues.append(f'{item}: SKILL.md有BOM')
                    print(f'  WARN {item}: BOM')
            with open(skill_md, 'r', encoding='utf-8-sig') as f:
                content = f.read(500)
            if 'trigger:' in content:
                has_trigger = True
                idx = content.index('trigger:')
                trigger_line = content[idx:].split('\n')[0]
                trigger_count = len([t for t in trigger_line.split('|') if t.strip()])
            elif 'triggers:' in content:
                trigger_bad = True
        s_issues = []
        if not has_script: s_issues.append('纯提示词')
        if not has_trigger: s_issues.append('无触发词')
        if trigger_bad: s_issues.append('格式错')
        if s_issues:
            print(f'  {"WARN" if "纯提示词" in s_issues else "ERR"} {item}: {",".join(s_issues)}')
            if '纯提示词' not in s_issues:
                issues.append(f'Skill: {item} - {",".join(s_issues)}')
        else:
            print(f'  OK {item}: 脚本+{trigger_count}触发词')
            ok_count += 1

# ========== 4. _helpers.py ==========
print('\n[4. _helpers.py]')
helpers = os.path.join(skills_dir, '_helpers.py')
if os.path.exists(helpers):
    with open(helpers, 'r', encoding='utf-8') as f:
        h = f.read()
    expected = ['call_vertical', 'call_model', 'ask_user', 'check_gpu', 'get_db_path', 'get_media_dir', 'get_reports_dir']
    for func in expected:
        if f'def {func}' in h:
            ok_count += 1
        else:
            print(f'  MISSING {func}')
            issues.append(f'helpers: 缺{func}')
    print(f'  OK {len(expected)}个函数')

# ========== 5. 引擎流程 ==========
print('\n[5. 引擎流程]')
with open('H:/HarvenAI/src/core/engine.py', 'r', encoding='utf-8') as f:
    engine = f.read()
flow_checks = {
    '_try_quick_command': '_try_quick_command' in engine,
    '_try_smart_split': '_try_smart_split' in engine,
    '_try_skill_trigger_match': '_try_skill_trigger_match' in engine,
    '_try_skill_trigger': '_try_skill_trigger' in engine,
    '_try_pipeline已删': '_try_pipeline' not in engine,
    'smart_split阈值≥1': 'len(amounts) < 1' in engine,
    'quick_command存消息': 'save_message' in engine and 'quick_result' in engine,
    'skill_trigger存消息': 'full_response' in engine and 'save_message' in engine,
    'smart_split存消息': '_split_result' in engine and 'save_message' in engine,
    '协作OFF阻止垂直Skill': '_vertical_skills' in engine,
    'Skill名称动态显示': '检测到【' in engine,
    '流式执行': 'run_skill_script_streaming' in engine,
    'save_message顶部导入': 'from src.models.database import save_message' in engine,
    'collab_mode检查': 'collab_mode' in engine,
}
for name, ok in flow_checks.items():
    if ok:
        ok_count += 1
    else:
        print(f'  ERR {name}')
        issues.append(f'引擎: {name}')
print(f'  OK {sum(flow_checks.values())}/{len(flow_checks)}')

# ========== 6. 触发词误触检查 ==========
print('\n[6. 触发词安全性]')
danger_skills = {}
for item in sorted(os.listdir(skills_dir)):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full) and not item.startswith('_'):
        skill_md = os.path.join(full, 'SKILL.md')
        if os.path.exists(skill_md):
            with open(skill_md, 'r', encoding='utf-8-sig') as f:
                content = f.read(500)
            if 'trigger:' in content:
                idx = content.index('trigger:')
                trigger_line = content[idx:].split('\n')[0].split(':', 1)[1].strip()
                words = [w.strip() for w in trigger_line.split('|') if w.strip() and not any(c in w for c in ['.*', '\\'])]
                danger = [w for w in words if len(w) <= 3 and w not in ['BGM', '月结', 'audit', 'scan code']]
                if danger:
                    danger_skills[item] = danger
if danger_skills:
    for skill, words in danger_skills.items():
        print(f'  WARN {skill}: {len(words)}个短词({",".join(words[:3])})')
    issues.append(f'触发词: {sum(len(v) for v in danger_skills.values())}个短词可能误触')
else:
    print('  OK 无危险短词')
    ok_count += 1

# ========== 7. 面板系统 ==========
print('\n[7. 面板系统]')
panels_dir = 'H:/HarvenAI/data/panels'
if os.path.exists(panels_dir):
    for pid in sorted(os.listdir(panels_dir)):
        pdir = os.path.join(panels_dir, pid)
        if not os.path.isdir(pdir): continue
        files = os.listdir(pdir)
        p_issues = []
        for req in ['panel.json', 'index.html', 'panel.js']:
            if req not in files: p_issues.append(f'缺{req}')
        js_path = os.path.join(pdir, 'panel.js')
        if os.path.exists(js_path):
            with open(js_path, 'r', encoding='utf-8') as f:
                pjs = f.read()
            if 'toggleView' not in pjs: p_issues.append('无卡片视图')
            if 'renderCards' not in pjs: p_issues.append('无renderCards')
            if 'formatValue' not in pjs: p_issues.append('无formatValue')
        if p_issues:
            print(f'  WARN {pid}: {",".join(p_issues)}')
        else:
            print(f'  OK {pid}')
            ok_count += 1

# ========== 8. 创建面板字段类型 ==========
print('\n[8. 创建面板字段类型]')
with open('H:/HarvenAI/static/js/modules/app-panels.js', 'r', encoding='utf-8') as f:
    pjs = f.read()
field_types = {'text': '文本', 'number': '数字', 'date': '日期', 'select': '下拉', 'textarea': '多行文本', 'time': '时间', 'image': '图片'}
for val, name in field_types.items():
    if f'value="{val}"' in pjs:
        ok_count += 1
    else:
        print(f'  MISSING {name}')
        issues.append(f'字段类型: 缺{name}')
print(f'  OK {sum(1 for v in field_types if f"value=\"{v}\"" in pjs)}/{len(field_types)}种')

# ========== 9. API端点 ==========
print('\n[9. API端点]')
try:
    r = requests.get('http://localhost:7000/', timeout=3)
    flask_ok = r.status_code == 200
except:
    flask_ok = False
if flask_ok:
    apis = [
        ('/api/accounting/summary', 'GET', 200),
        ('/api/accounting/records', 'GET', 200),
        ('/api/flows', 'GET', 200),
        ('/api/panels', 'GET', 200),
        ('/api/skills/triggers', 'GET', 200),
        ('/api/scheduler/list', 'GET', 200),
        ('/api/hub/flows', 'GET', 200),
        ('/api/airllm/status', 'GET', 200),
        ('/api/hardware/gpu', 'GET', 200),
        ('/api/multi-model/list', 'GET', 200),
        ('/api/ask_user/pending', 'GET', 200),
        ('/api/conversations/by-mode?mode=main', 'GET', 200),
    ]
    for path, method, expected_code in apis:
        try:
            r = requests.get(f'http://localhost:7000{path}', timeout=5)
            if r.status_code == expected_code:
                ok_count += 1
            else:
                print(f'  ERR {path}: {r.status_code}')
                issues.append(f'API: {path} 返回{r.status_code}')
        except Exception as e:
            print(f'  ERR {path}: {e}')
            issues.append(f'API: {path} 不可达')
    print(f'  OK {len(apis)}个API端点')
else:
    print('  ERR Flask未运行')
    issues.append('Flask未运行')

# ========== 10. 沙箱配置 ==========
print('\n[10. 沙箱配置]')
with open('H:/HarvenAI/src/core/scripts_runner.py', 'r', encoding='utf-8') as f:
    sr = f.read()
sandbox_checks = {
    '超时300秒': 'timeout: int = 300' in sr,
    '禁用subprocess': 'subprocess' in sr and 'BANNED' in sr,
    '禁用socket': 'socket' in sr and 'BANNED' in sr,
    '流式执行函数': 'run_skill_script_streaming' in sr,
}
for name, ok in sandbox_checks.items():
    if ok: ok_count += 1
    else: print(f'  MISSING {name}'); issues.append(f'沙箱: {name}')
print(f'  OK {sum(sandbox_checks.values())}/{len(sandbox_checks)}')

# ========== 11. 定时调度器 ==========
print('\n[11. 定时调度器]')
sched_path = 'H:/HarvenAI/src/core/task_scheduler.py'
if os.path.exists(sched_path):
    with open(sched_path, 'r', encoding='utf-8') as f:
        ts = f.read()
    if 'start_scheduler' in ts and '_check_and_run' in ts:
        print('  OK 调度器存在')
        ok_count += 1
    else:
        print('  ERR 调度器不完整')
        issues.append('调度器: 不完整')
else:
    print('  MISSING 调度器文件不存在')
    issues.append('调度器: 文件不存在')

# ========== 12. AirLLM ==========
print('\n[12. AirLLM集成]')
airllm_path = 'H:/HarvenAI/src/core/airllm_integration.py'
if os.path.exists(airllm_path):
    print('  OK 模块存在')
    ok_count += 1
else:
    print('  MISSING 模块不存在')
    issues.append('AirLLM: 模块不存在')

# ========== 13. GitHub开源文件 ==========
print('\n[13. GitHub开源准备]')
gh_files = ['.gitignore', 'README.md', 'CONTRIBUTING.md', 'config.example.yaml']
for f in gh_files:
    if os.path.exists(f'H:/HarvenAI/{f}'):
        ok_count += 1
    else:
        print(f'  MISSING {f}')
        issues.append(f'GitHub: 缺{f}')
print(f'  OK {sum(1 for f in gh_files if os.path.exists(f"H:/HarvenAI/{f}"))}/{len(gh_files)}')

# ========== 14. 协作帮助文案 ==========
print('\n[14. 协作帮助文案]')
with open('H:/HarvenAI/static/js/modules/app-conversation.js', 'r', encoding='utf-8') as f:
    cjs = f.read()
if 'Skill脚本编排' in cjs and '多模型协作' in cjs:
    print('  OK 已更新')
    ok_count += 1
else:
    print('  ERR 还是旧版')
    issues.append('文案: 协作帮助旧版')

# ========== 15. ask_user前端 ==========
print('\n[15. ask_user前端轮询]')
if 'startAskUserPolling' in cjs and '_answerAskUser' in cjs:
    print('  OK 轮询+回答')
    ok_count += 1
else:
    print('  MISSING 轮询功能')
    issues.append('ask_user: 前端轮询缺失')

# ========== 16. 多外脑UI ==========
print('\n[16. 多外脑UI切换]')
if 'quickForeignModelSelect' in open('H:/HarvenAI/templates/index.html', 'r', encoding='utf-8').read():
    print('  OK 下拉框存在')
    ok_count += 1
else:
    print('  MISSING 下拉框')
    issues.append('多外脑: UI缺失')

# ========== 17. 流程编辑器 ==========
print('\n[17. 流程编辑器]')
fe_path = 'H:/HarvenAI/templates/includes/tab-流程编辑器.html'
if os.path.exists(fe_path):
    with open(fe_path, 'r', encoding='utf-8') as f:
        fe = f.read()
    fe_checks = {
        'tab-panel类': 'class="tab-panel"' in fe,
        '模型选择': '_flowModels' in fe,
        '添加步骤': 'addFlowStep' in fe,
        '预览代码': 'previewFlowCode' in fe,
        '保存流程': 'saveFlow' in fe,
        '已保存流程': 'loadSavedFlows' in fe,
    }
    for name, ok in fe_checks.items():
        if ok: ok_count += 1
        else: print(f'  MISSING {name}'); issues.append(f'流程编辑器: {name}')
    print(f'  OK {sum(fe_checks.values())}/{len(fe_checks)}')
else:
    print('  MISSING 文件不存在')
    issues.append('流程编辑器: 文件不存在')

# ========== 18. 高级功能面板 ==========
print('\n[18. 高级功能面板]')
af_path = 'H:/HarvenAI/templates/includes/tab-高级功能.html'
if os.path.exists(af_path):
    with open(af_path, 'r', encoding='utf-8') as f:
        af = f.read()
    af_checks = {
        'tab-panel类': 'class="tab-panel"' in af,
        '触发词管理': 'loadTriggers' in af,
        '定时任务': 'loadSchedTasks' in af,
        'Obsidian': 'syncObsidian' in af,
        'AI生成': 'generateSkill' in af,
        'AirLLM': 'loadAirllm' in af,
        '硬件信息': 'loadGpuInfo' in af,
    }
    for name, ok in af_checks.items():
        if ok: ok_count += 1
        else: print(f'  MISSING {name}'); issues.append(f'高级功能: {name}')
    print(f'  OK {sum(af_checks.values())}/{len(af_checks)}')
else:
    print('  MISSING 文件不存在')
    issues.append('高级功能: 文件不存在')

# ========== 19. 设置导航 ==========
print('\n[19. 设置导航]')
idx_html = open('H:/HarvenAI/templates/index.html', 'r', encoding='utf-8').read()
nav_checks = {
    '高级功能按钮': 'switchSettingsTab(\'advanced\'' in idx_html,
    '流程编辑器按钮': 'switchSettingsTab(\'floweditor\'' in idx_html,
    '面板映射advanced': "'tabAdvancedFeatures'" in idx_html,
    '面板映射floweditor': "'tabFlowEditor'" in idx_html,
}
for name, ok in nav_checks.items():
    if ok: ok_count += 1
    else: print(f'  MISSING {name}'); issues.append(f'导航: {name}')
print(f'  OK {sum(nav_checks.values())}/{len(nav_checks)}')

# ========== 20. 废弃代码检查 ==========
print('\n[20. 废弃代码]')
# 检查_try_pipeline是否真的删了
if '_try_pipeline' in engine:
    print('  ERR _try_pipeline还在engine.py')
    issues.append('废弃: _try_pipeline还在')
else:
    print('  OK _try_pipeline已删除')
    ok_count += 1

# 检查save_message重复import
if engine.count('from src.models.database import save_message') > 1:
    print('  WARN save_message重复import')
    issues.append('废弃: save_message重复import')
else:
    print('  OK save_message无重复import')
    ok_count += 1

# 检查triggers格式（复数）
for item in os.listdir(skills_dir):
    full = os.path.join(skills_dir, item)
    if os.path.isdir(full):
        skill_md = os.path.join(full, 'SKILL.md')
        if os.path.exists(skill_md):
            with open(skill_md, 'r', encoding='utf-8-sig') as f:
                if 'triggers:' in f.read(500):
                    print(f'  WARN {item}: triggers格式错')
                    issues.append(f'废弃: {item} triggers格式')
                    break
else:
    print('  OK 无triggers格式错误')
    ok_count += 1

# ========== 21. 数据库 ==========
print('\n[21. 数据库]')
db_path = 'H:/HarvenAI/data/sqlite/lingzhi.db'
if os.path.exists(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall()]
    print(f'  OK {len(tables)}个表')
    ok_count += 1
    # 检查空对话
    try:
        empty = conn.execute('SELECT COUNT(*) FROM conversations WHERE id NOT IN (SELECT DISTINCT conversation_id FROM messages)').fetchone()[0]
        if empty > 0:
            print(f'  WARN {empty}个空对话')
        else:
            print(f'  OK 无空对话')
            ok_count += 1
    except:
        pass
    conn.close()
else:
    print('  ERR 数据库不存在')
    issues.append('数据库: 不存在')

# ========== 总结 ==========
print('\n' + '=' * 70)
print(f'检测完成: {ok_count}项正常, {len(issues)}个问题')
print('=' * 70)
if issues:
    print('\n问题清单:')
    for i, issue in enumerate(issues, 1):
        print(f'  {i}. {issue}')
else:
    print('\n✅ 全部正常！无问题！')
