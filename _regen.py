import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')
from pathlib import Path
from src.core.panel_manager import get_panel_manager, PANELS_DIR
mgr = get_panel_manager()
done, skip = [], []
for pid, panel in mgr._panels.items():
    if panel.type != 'table':
        skip.append(pid); continue
    js = PANELS_DIR / pid / 'panel.js'
    if not js.is_file():
        skip.append(pid); continue
    first = js.read_text(encoding='utf-8').splitlines()[0].strip()
    if first != ('// %s panel logic' % pid):
        skip.append(pid); continue
    js.write_text(mgr._generate_panel_js(pid, panel.fields), encoding='utf-8')
    done.append(pid)
print('REGEN:', ','.join(done))
print('SKIP :', ','.join(skip))
acc = PANELS_DIR / 'accounting_daily_1' / 'panel.js'
print('accounting intact:', acc.exists() and 'HarvenMoneyBridge' not in acc.read_text(encoding='utf-8')[:2000])
sample = PANELS_DIR / 'fridge_stock_1' / 'panel.js'
print('fridge has bridge:', 'HarvenMoneyBridge' in sample.read_text(encoding='utf-8'))
