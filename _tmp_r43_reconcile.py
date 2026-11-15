# -*- coding: utf-8 -*-
"""r43 解包对账（试安装专用，跑完即弃）"""
import zipfile, os, sys

os.chdir(r'H:\HarvenAI\_r43_install_test')
zf = zipfile.ZipFile(r'C:\Users\usera\Desktop\HarvenAI_v0.3.1_Windows_20261030_r43.zip')
znames = [n for n in zf.namelist() if not n.endswith('/')]
local = []
for root, dirs, files in os.walk('.'):
    for f in files:
        p = os.path.relpath(os.path.join(root, f), '.').replace(os.sep, '/')
        local.append(p)
zs, ls = set(znames), set(local)
miss = sorted(zs - ls)
extra = sorted(ls - zs)
print(f'zip 条目: {len(znames)}  本地落地: {len(local)}  缺失: {len(miss)}  多出: {len(extra)}')
for m in miss[:10]:
    print('  MISS ' + m)
for e in extra[:10]:
    print('  EXTRA ' + e)
print('中文模板抽检:', 'OK' if os.path.exists('templates/includes/tab-ai模型.html') else 'MISS')
print('安装指南抽检:', 'OK' if os.path.exists('docs/安装指南.html') else 'MISS')
sys.exit(1 if (miss or extra) else 0)
