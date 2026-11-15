import hashlib, shutil
from pathlib import Path
# ★ #950（2026-10-03）批次同步：#943 五道闸 + #946/#947/#948 哈文侧 + SPA 两次构建
SRC = Path(r'H:\HarvenAI')
DST = Path(r'H:\HarvenAI\_package_staging')
files = [
    'src/core/builtin_tools.py',
    'src/core/cloud_link.py',
    'static/spa/index.html',
    'static/spa/assets/index-DXJZ2O3F.js',
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
print(f'{ok}/{len(files)} synced' + (f'  BAD: {bad}' if bad else ''))
