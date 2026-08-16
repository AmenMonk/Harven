"""打包并上传到HarvenHub"""
import os, zipfile, requests, json

STAGING = 'H:/HarvenAI/_package_staging'
ZIP_PATH = 'H:/HarvenAI/HarvenAI_v0.2.1_install_package.zip'

# 1. 打包
print("1. 打包中...")
with zipfile.ZipFile(ZIP_PATH, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(STAGING):
        # 跳过 __pycache__, .git, data/sqlite, data/downloads
        if '__pycache__' in root or '.git' in root:
            continue
        for f in files:
            if f.endswith(('.pyc', '.pyo', '.log')):
                continue
            full = os.path.join(root, f)
            arc = os.path.relpath(full, STAGING)
            zf.write(full, arc)

size_mb = os.path.getsize(ZIP_PATH) / 1024 / 1024
print(f"   打包完成: {size_mb:.1f}MB")

# 2. 上传到HarvenHub
print("\n2. 上传到HarvenHub...")
API = 'https://harvenhub.com'
s = requests.Session()

# 登录
r = s.post(f'{API}/api/user/login', json={'username':'admin','password':'harvenhub2026'}, timeout=10)
if r.status_code != 200:
    print(f"   登录失败: {r.status_code}")
    exit(1)
print("   登录成功")

# 删除旧的安装包
r = s.get(f'{API}/api/hub/list?per_page=200&status=all&type=bundle', timeout=10)
for item in r.json().get('items', []):
    if 'HarvenAI' in item.get('name', '') and 'install' in item.get('name', '').lower():
        s.post(f'{API}/api/hub/admin/delete/{item["id"]}', timeout=10)
        print(f"   删除旧包: {item['name']}")

# 上传新包
with open(ZIP_PATH, 'rb') as f:
    files = {'file': ('HarvenAI_v0.2.1.zip', f, 'application/zip')}
    form = {
        'name': 'HarvenAI v0.2.1 完整安装包',
        'type': 'bundle',
        'category': 'system',
        'description': 'HarvenAI v0.2.1 完整安装包 (2026-08-03)。包含：引擎优先架构(Skill方式)、8个内置模型支持、代码执行优先、HarvenHub云端集成、手机二维码、FC工具(list_dir/read_file)、对话上下文自动新对话等v6.3全部更新。',
        'author_id': 'system',
        'version': '0.2.1',
    }
    r = requests.post(f'{API}/api/hub/upload', data=form, files=files, timeout=120)

if r.status_code == 200:
    data = r.json()
    if data.get('success'):
        print(f"\n✅ 上传成功!")
        print(f"   ID: {data.get('item_id', '')}")
        print(f"   下载: https://harvenhub.com/api/hub/download/{data.get('item_id', '')}")
    else:
        print(f"❌ 上传失败: {data}")
else:
    print(f"❌ HTTP {r.status_code}: {r.text[:200]}")
