import shutil, os
# ★ 禁止生成 .pyc 文件（否则会混进ZIP）
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True

src = 'H:/HarvenAI/_package_staging'

# ★ 确保顶层 skills/ 目录存在（Hub安装的skills）
skills_src = 'H:/HarvenAI/skills'
skills_dst = os.path.join(src, 'skills')
os.makedirs(skills_dst, exist_ok=True)
if os.path.isdir(skills_src):
    for item in os.listdir(skills_src):
        s = os.path.join(skills_src, item)
        d = os.path.join(skills_dst, item)
        if os.path.isdir(s) and not os.path.exists(d):
            shutil.copytree(s, d)
        elif os.path.isfile(s) and not os.path.exists(d):
            shutil.copy2(s, d)

# ★ 2026-08-12: 精简ZIP根目录，只留用户需要的文件
# 删除开发/部署文件
DEV_FILES = [
    '.gitignore', '.dockerignore', '.env.example',
    'CONTRIBUTING.md', 'DEPLOY.md', 'DISCLAIMER.md', 'NOTICE',
    'LICENSE.md', 'LICENSES_THIRD_PARTY.md',
    'Dockerfile', 'docker-compose.yml', 'docker-entrypoint.sh',
    'main.py', 'make_zip.py',
    'HarvenAI_安装详解_v2.0.md', '打包说明.md', 'version.json',
    'install.ps1',
]
for f in DEV_FILES:
    p = os.path.join(src, f)
    if os.path.exists(p):
        os.remove(p)
        print('Removed: ' + f)

# 移动多余的启动脚本到 tools/ 目录，避免用户困惑
# ★ 启动HarvenAI.bat 必须留在根目录！安装哈文.bat 会调用它
EXTRA_BATS = ['启动HarvenAI.ps1', '启动HarvenAI_公网版.bat',
              '一键安装启动.bat', 'HarvenAI.bat']
tools_dir = os.path.join(src, 'tools')
os.makedirs(tools_dir, exist_ok=True)
for f in EXTRA_BATS:
    p = os.path.join(src, f)
    if os.path.exists(p):
        shutil.move(p, os.path.join(tools_dir, f))
        print('Moved: ' + f + ' → tools/')

# 移动技术文档到 tools/
DOC_FILES = ['HarvenAI_技术详解.html', 'HarvenAI_技术详解.md']
for f in DOC_FILES:
    p = os.path.join(src, f)
    if os.path.exists(p):
        shutil.move(p, os.path.join(tools_dir, f))
        print('Moved: ' + f + ' → tools/')

# 移动开发者文件到 tools/（用户不需要这些）
for f in ['LICENSE', 'VERSION']:
    p = os.path.join(src, f)
    if os.path.exists(p):
        shutil.move(p, os.path.join(tools_dir, f))
        print('Moved: ' + f + ' → tools/')
# ★ config.example.yaml 必须留在根目录！安装脚本需要它创建 config.yaml

# 创建简单的安装说明（替换复杂的README）
README_CONTENT = '''欢迎使用 Harven AI 哈文！

怎么装？ -> 双击文件夹里的 "安装哈文.bat"，等5-15分钟
怎么用？ -> 打开 docs/guide.html 看指南，或者在哈文里输入 /help
初始密码 -> harven2026（进去后改掉）

简单吧！
'''
with open(os.path.join(src, 'README.txt'), 'w', encoding='utf-8') as f:
    f.write(README_CONTENT)
print('Created: README.txt (简单版)')

# 删除旧的复杂README
for f in ['README.md']:
    p = os.path.join(src, f)
    if os.path.exists(p):
        os.remove(p)
        print('Removed: ' + f)

# 删除敏感文件
for f in ['config.yaml', 'config.yaml.example']:
    p = os.path.join(src, f)
    if os.path.exists(p):
        os.remove(p)
        print('Deleted: ' + f)

# 删除临时脚本
for f in os.listdir(src):
    if f.startswith(('check_', 'sync_', 'verify_', 'list_skills', 'test_')):
        p = os.path.join(src, f)
        if os.path.isfile(p):
            os.remove(p)
            print('Deleted: ' + f)

# 打包前清理 pycache
for root, dirs, files in os.walk(src):
    for d in dirs[:]:
        if d == '__pycache__':
            shutil.rmtree(os.path.join(root, d), ignore_errors=True)

# ★ 2026-08-13: 修复bat文件换行符为CRLF + 加UTF-8 BOM
# LF换行的bat文件在Windows CMD里会"一晃就没了"
for root, dirs, files in os.walk(src):
    for f in files:
        if f.endswith('.bat'):
            fp = os.path.join(root, f)
            with open(fp, 'rb') as bf:
                raw = bf.read()
            # 去掉已有BOM
            if raw[:3] == b'\xef\xbb\xbf':
                raw = raw[3:]
            # LF → CRLF
            raw = raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            # ★ 只替换开头的 `pip install` 为 `python -m pip install`（不影响echo提示文本）
            import re as _re
            raw = _re.sub(rb'^(\s*)pip install', rb'\1python -m pip install', raw, flags=_re.MULTILINE)
            # 修复之前重复替换导致的 `python -m python -m pip install` → `python -m pip install`
            raw = raw.replace(b'python -m python -m pip install', b'python -m pip install')
            # 加UTF-8 BOM（让CMD正确识别编码）
            with open(fp, 'wb') as bf:
                bf.write(b'\xef\xbb\xbf' + raw)
print('Fixed: 所有.bat文件已转为CRLF+UTF-8 BOM + pip命令加python -m')

# 复制安装脚本到用户可见位置
# 安装哈文.bat 保持不变在根目录

# ★ 2026-08-12: 创建开始使用.html（用户解压后第一个看的文件）
# 使用 base64 嵌入鸭子图片，确保所有浏览器都能显示
import base64 as _b64
with open(os.path.join(src, 'static', 'icon-duck.png'), 'rb') as _f:
    _duck_b64 = _b64.b64encode(_f.read()).decode('ascii')
START_HTML = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>开始使用 Harven AI</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;max-width:580px;margin:40px auto;padding:16px;text-align:center;background:#f0f2f5}}
.card{{background:#fff;border-radius:14px;padding:36px 24px;box-shadow:0 2px 12px rgba(0,0,0,0.08)}}
.duck{{width:120px;height:120px;margin:12px auto}}
h1{{font-size:24px;margin:0 0 4px;color:#222}}
.sub{{color:#777;font-size:14px;margin-bottom:24px}}
.step{{background:#f0f4ff;border-radius:10px;padding:16px;margin:12px 0;text-align:left;font-size:14px}}
.step b{{color:#4455cc}}
.step p{{margin:4px 0;color:#555}}
.highlight{{display:inline-block;background:#e8edff;border:2px dashed #667eea;padding:10px 20px;border-radius:8px;font-size:18px;font-weight:bold;color:#4455cc;margin:8px 0;font-family:monospace}}
code{{background:#eee;padding:2px 6px;border-radius:3px;font-size:13px;color:#333}}
.warn{{background:#fff3cd;border:1px solid #ffe082;border-radius:8px;padding:10px;margin:14px 0;font-size:13px;color:#664d03;text-align:left}}
.foot{{color:#aaa;font-size:12px;margin-top:24px}}
</style></head>
<body>
<div class="card">
<img class="duck" src="data:image/png;base64,{_duck_b64}" alt="Harven 哈文">
<h1>Harven AI 哈文</h1>
<div class="sub">装在你家电脑里的 AI 助手 - 不需要装Python，一键安装</div>

<div class="step">
  <p><b>第一步：双击安装</b></p>
  <div class="highlight">安装哈文.bat</div>
  <p>在文件夹里找到上面这个文件，用鼠标双击它。</p>
  <p>安装包已自带Python，不需要额外安装任何东西。等5-15分钟自动装好。</p>
</div>

<div class="step">
  <p><b>第二步：打开哈文</b></p>
  <p>桌面出现哈文图标后，双击打开。浏览器会自动打开哈文。</p>
</div>

<div class="step">
  <p><b>第三步：开始使用</b></p>
  <p>哈文会自动打开。第一次不用密码，直接就能用。</p>
  <p>想设密码的话，去 <b>设置 → 安全隐私</b> 自己设一个。</p>
</div>

<div class="warn">
  <b>&#x26A0; 杀毒软件弹窗？</b> 点"允许"或"信任"。哈文是安全的，不打广告不偷隐私。
</div>

<div class="step">
  <p><b>不需要装这些东西：</b></p>
  <p>不需要装Python（安装包已自带）</p>
  <p>不需要装Ollama（以后想用本地模型再装）</p>
  <p>不需要装任何编程工具</p>
</div>

<div class="step">
  <p><b>装完后想看看怎么用？</b></p>
  <p>打开 <code>docs/guide.html</code> 看完整指南，或者在哈文里输入 <code>/help</code> 查帮助。</p>
</div>

<div class="foot">
  文件夹里其他文件不用管，只双击 <b>安装哈文.bat</b> 就行。非常简单！
</div>
</div>
</body></html>'''
with open(os.path.join(src, '开始使用.html'), 'w', encoding='utf-8') as f:
    f.write(START_HTML)
print('Created: 开始使用.html (含真正的鸭子图片)')

# 打包ZIP
print('Creating ZIP...')
# ★ 自定义打包：排除 __pycache__ 和 .pyc
import zipfile as _zipf
zip_path = 'H:/HarvenAI/HarvenAI_v0.3.0'
with _zipf.ZipFile(zip_path + '.zip', 'w', _zipf.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(src):
        # 排除 __pycache__
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if f.endswith('.pyc'):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, src).replace(os.sep, '/')
            zf.write(full, rel)
size = os.path.getsize(zip_path + '.zip')
print('Done!')
print('ZIP path: ' + zip_path + '.zip')
print('Size: %.1f MB' % (size / 1024 / 1024))

# 验证ZIP内容
import zipfile
with zipfile.ZipFile(zip_path + '.zip', 'r') as z:
    names = z.namelist()
    root_items = set()
    for n in names:
        parts = n.split('/')
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == ''):
            root_items.add(parts[0])
    print('Files in ZIP: %d (根目录: %d项)' % (len(names), len(root_items)))
    print('根目录: %s' % ', '.join(sorted(root_items)))
    # 检查关键文件
    key_files = ['web_app.py', 'VERSION', '安装哈文.bat', 'README.txt',
                 'docs/guide.html', 'data/skills/_helpers.py']
    for kf in key_files:
        found = any(kf in n for n in names)
        print('  %s %s' % ('OK' if found else 'MISSING', kf))
