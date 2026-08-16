import os, shutil

src = 'H:/HarvenAI'
dst = 'H:/HarvenAI/_package_staging'

# 需要同步的文件列表
sync_files = [
    # 核心
    'web_app.py',
    'src/core/engine.py',
    'src/core/scripts_runner.py',
    'src/core/panel_manager.py',
    'src/core/panel_routes.py',
    'src/core/task_scheduler.py',
    'src/core/airllm_integration.py',
    'src/core/vertical_api.py',
    'src/core/knowledge_base.py',
    'src/core/builtin_tools.py',
    'src/models/database.py',
    # helpers
    'data/skills/_helpers.py',
    # GitHub
    '.gitignore',
    'README.md',
    'CONTRIBUTING.md',
    'config.example.yaml',
    # 文档
    'docs/HarvenAI_技术详解_v7.md',
    'HarvenAI_安装详解_v2.0.md',
    # 模板
    'templates/index.html',
    'templates/includes/tab-流程编辑器.html',
    'templates/includes/tab-高级功能.html',
    'templates/includes/modal-panel-manage.html',
    # JS
    'static/js/modules/app-panels.js',
    'static/js/modules/app-conversation.js',
    'static/js/modules/app-settings.js',
]

# 同步单个文件
synced = 0
missing = 0
for f in sync_files:
    src_path = os.path.join(src, f)
    dst_path = os.path.join(dst, f)
    if os.path.exists(src_path):
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copy2(src_path, dst_path)
        synced += 1
    else:
        print('MISSING: ' + f)
        missing += 1

# 同步Skill目录
skills_dir = os.path.join(src, 'data/skills')
for item in os.listdir(skills_dir):
    s = os.path.join(skills_dir, item)
    d = os.path.join(dst, 'data/skills', item)
    if os.path.isdir(s) and not item.startswith('_'):
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(s, d)
        synced += 1
    elif item == '_helpers.py':
        shutil.copy2(s, os.path.join(dst, 'data/skills', item))

# 同步面板目录
panels_dir = os.path.join(src, 'data/panels')
for item in os.listdir(panels_dir):
    s = os.path.join(panels_dir, item)
    d = os.path.join(dst, 'data/panels', item)
    if os.path.isdir(s):
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(s, d)
        synced += 1

# 同步pipelines目录
pipes_dir = os.path.join(src, 'data/pipelines')
if os.path.exists(pipes_dir):
    d = os.path.join(dst, 'data/pipelines')
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(pipes_dir, d)

# 同步static
static_src = os.path.join(src, 'static')
static_dst = os.path.join(dst, 'static')
for root, dirs, files in os.walk(static_src):
    rel = os.path.relpath(root, static_src)
    dst_root = os.path.join(static_dst, rel) if rel != '.' else static_dst
    os.makedirs(dst_root, exist_ok=True)
    for f in files:
        if f.endswith(('.js', '.css', '.ico', '.png', '.svg')):
            shutil.copy2(os.path.join(root, f), os.path.join(dst_root, f))

# 同步templates
tmpl_src = os.path.join(src, 'templates')
tmpl_dst = os.path.join(dst, 'templates')
for root, dirs, files in os.walk(tmpl_src):
    rel = os.path.relpath(root, tmpl_src)
    dst_root = os.path.join(tmpl_dst, rel) if rel != '.' else tmpl_dst
    os.makedirs(dst_root, exist_ok=True)
    for f in files:
        if f.endswith(('.html', '.css', '.js')):
            shutil.copy2(os.path.join(root, f), os.path.join(dst_root, f))

# 同步src完整
src_core = os.path.join(src, 'src')
src_dst = os.path.join(dst, 'src')
for root, dirs, files in os.walk(src_core):
    rel = os.path.relpath(root, src_core)
    dst_root = os.path.join(src_dst, rel) if rel != '.' else src_dst
    os.makedirs(dst_root, exist_ok=True)
    for f in files:
        if f.endswith('.py'):
            shutil.copy2(os.path.join(root, f), os.path.join(dst_root, f))

# 同步locales
loc_src = os.path.join(src, 'locales')
loc_dst = os.path.join(dst, 'locales')
if os.path.exists(loc_src):
    if os.path.exists(loc_dst):
        shutil.rmtree(loc_dst)
    shutil.copytree(loc_src, loc_dst)

# 同步docs
docs_src = os.path.join(src, 'docs')
docs_dst = os.path.join(dst, 'docs')
if os.path.exists(docs_src):
    os.makedirs(docs_dst, exist_ok=True)
    for f in os.listdir(docs_src):
        if f.endswith('.md'):
            shutil.copy2(os.path.join(docs_src, f), os.path.join(docs_dst, f))

# 删除旧的检查脚本
for f in ['check_all.py', 'check_full.py', 'check_final.py', 'check_final2.py', 'check_final3.py', 'check_v2.py', 'check_result.txt', 'check_output.txt', 'check_err.txt', 'list_skills.py', 'test_apis.py', 'test_api.py']:
    p = os.path.join(dst, f)
    if os.path.exists(p):
        os.remove(p)

# 写版本文件
with open(os.path.join(dst, 'VERSION'), 'w', encoding='utf-8') as f:
    f.write('v0.3.1\n')
    f.write('build: 2026-08-17\n')
    f.write('skills: 10\n')
    f.write('apis: 20+\n')
    f.write('i18n: zh_CN + en_US (853 keys)\n')

print('Synced: %d files' % synced)
print('Missing: %d' % missing)
print('Version: v0.3.1 (2026-08-17)')
