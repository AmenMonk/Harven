import hashlib, shutil, os
from pathlib import Path
SRC = Path(r'H:\HarvenAI')
DST = Path(r'H:\HarvenAI\_package_staging')
files = [
    'spa/src/panelApiBridge.js',
    'spa/src/main.js',
    'static/spa/index.html',
    'static/spa/assets/index-BQ1eFizX.js',
    'src/core/panel_manager.py',
    'src/core/kid_archive.py',
    'data/panels/kid_archive/kid.js',
    'data/panels/kid_archive/index.html',
    'data/panels/home_items/panel.js',
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
        else:
            bad.append(f)
    except Exception as e:
        bad.append(f'{f}: {e}')
print(f'{ok}/{len(files)} synced')
for b in bad:
    print('BAD', b)