import hashlib, shutil
from pathlib import Path
# ★ v8 批次（#975~#979）同步：cloud_link 手机双写根治 + phone_local 麦克风预检 + HomePage 静音排查 + SPA 产物
SRC = Path(r'H:\HarvenAI')
DST = Path(r'H:\HarvenAI\_package_staging')
files = [
    'src/core/cloud_link.py',
    'templates/phone_local.html',
    'spa/src/pages/HomePage.vue',
    'static/spa/index.html',
    'static/spa/assets/index-B6CKBULE.js',
    'static/spa/assets/index-BONgDF4g.css',
]
ok, bad = 0, []
for f in files:
    s, d = SRC / f, DST / f
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        h1 = hashlib.md5(s.read_bytes()).hexdigest()
        h2 = hashlib.md5(d.read_bytes()).hexdigest()
        if h1 == h2:
            ok += 1
            print(f'  OK  {f}  md5={h1[:8]}')
        else:
            bad.append(f)
    except Exception as e:
        bad.append(f'{f}: {e}')

# 清理 staging 旧 SPA 产物（live 构建已换代，staging 不留死文件）
for old in ['static/spa/assets/index-CAp9qcWY.js', 'static/spa/assets/index-C8P49gWP.css']:
    p = DST / old
    if p.exists() and old not in files:
        p.unlink()
        print(f'  DEL  {old} (旧产物)')

print(f'{ok}/{len(files)} synced' + (f'  BAD: {bad}' if bad else ''))
