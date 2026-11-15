# -*- coding: utf-8 -*-
import hashlib, shutil
from pathlib import Path
SRC = Path(r'H:\HarvenAI')
DST = Path(r'H:\HarvenAI\_package_staging')
files = [
    'web_app.py',
    'src/core/pet_care.py', 'src/core/pet_routes.py', 'src/core/photo_center.py',
    'data/panels/dream_journal/panel.json', 'data/panels/dream_journal/index.html',
    'data/panels/dream_journal/panel.js', 'data/panels/dream_journal/panel.css',
    'data/panels/pet_care_1/panel.json', 'data/panels/pet_care_1/index.html',
    'data/panels/pet_care_1/pet.js', 'data/panels/pet_care_1/panel.css',
    'spa/src/pages/PhotosPage.vue', 'spa/src/router/index.js',
    'spa/src/pages/MorePage.vue', 'spa/src/navCommands.js',
    'static/spa/index.html', 'static/spa/assets/index-D9ZMU__I.js', 'static/spa/assets/index-DzMqlmuQ.css',
]
ok, bad = 0, []
for f in files:
    s, d = SRC / f, DST / f
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        if hashlib.md5(s.read_bytes()).hexdigest() == hashlib.md5(d.read_bytes()).hexdigest():
            ok += 1
        else:
            bad.append(f)
    except Exception as e:
        bad.append('%s: %s' % (f, e))
# staging 里宠物旧生成品 panel.js 删除（本地已删）
old = DST / 'data' / 'panels' / 'pet_care_1' / 'panel.js'
if old.exists():
    old.unlink()
    print('staging old panel.js removed')
print('%d/%d synced' % (ok, len(files)))
for b in bad:
    print('BAD', b)
