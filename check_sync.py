import os, hashlib

def md5_file(path):
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()[:12]

# 1. 技术详解
print("=== 技术详解 ===")
for p in ['H:/HarvenAI/docs', 'H:/HarvenAI']:
    if os.path.isdir(p):
        for f in os.listdir(p):
            if '技术详解' in f or '技术文档' in f:
                fp = os.path.join(p, f)
                print(f"  {f} ({os.path.getsize(fp)//1024}KB)")

# 2. 打包目录同步状态
print("\n=== 打包目录同步 ===")
files = [
    'web_app.py', 'src/core/builtin_tools.py', 'src/core/harvenhub_client.py',
    'src/core/mobile_connector.py', 'static/js/modules/app-harvenhub.js',
    'static/js/modules/app-settings.js', 'templates/includes/tab-分享联动.html',
    'templates/index.html', 'main.py', 'config.yaml', 'version.json',
    'src/core/evolution_manager.py'
]
for f in files:
    src = f'H:/HarvenAI/{f}'
    dst = f'H:/HarvenAI/_package_staging/{f}'
    s = md5_file(src)
    d = md5_file(dst)
    if s == d:
        print(f"  ✅ {f}")
    elif d is None:
        print(f"  ❌ {f} (缺失)")
    else:
        print(f"  ❌ {f} (MD5: {s} vs {d})")
