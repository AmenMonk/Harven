import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI\src')
from pathlib import Path
from core.panel_manager import PanelManager, PANELS_DIR
pm = PanelManager()
pid = 'dream_journal'
pdir = PANELS_DIR / pid
pj = json.loads((pdir / 'panel.json').read_text(encoding='utf-8'))
js = pm._generate_panel_js(pid, pj['fields'])
(pdir / 'panel.js').write_text(js, encoding='utf-8')
from core.panel_manager import Panel
panel = Panel.from_dict(pj)
pm._sync_generated_panel(panel)
print('DREAM-GEN-OK', 'html=', (pdir/'index.html').is_file(), 'css=', (pdir/'panel.css').is_file(), 'js=', (pdir/'panel.js').is_file())