# -*- coding: utf-8 -*-
"""#1432 带图录入分诊冒烟 · 2026-11-04 r48
验证：带图 + 录入触发词 → 确定性回复 + 默录链落账（待确认/直入）；
带图看图提问 → 仍走看图直答不误拦。
"""
import io, sys, json, base64, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')

results = []
def check(name, cond, detail=''):
    results.append(cond)
    print(('PASS  ' if cond else 'FAIL  ') + name + (('  → ' + str(detail)) if detail and not cond else ''))

TINY_PNG = base64.b64encode(bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108020000009077'
    '53de0000000c4944415408d763f8cfc000000301010018dd8db00000000049'
    '454e44ae426082')).decode()
DATAURL = 'data:image/png;base64,' + TINY_PNG

from web_app import app
app.config['TESTING'] = True
from src.core.panel_routes import bp as _panel_bp
app.register_blueprint(_panel_bp)
c = app.test_client()

def chat_stream(payload, max_events=80):
    r = c.post('/api/chat', data=json.dumps(payload), content_type='application/json')
    types, texts, cards = [], '', []
    buf = ''
    for chunk in r.response:
        buf += chunk.decode('utf-8', errors='ignore')
        while '\n\n' in buf:
            line, buf = buf.split('\n\n', 1)
            line = line.strip()
            if not line.startswith('data: '):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get('type', '')
            types.append(t)
            if t == 'text':
                texts += ev.get('content', '')
            if t == 'confirm_card':
                cards.append(ev.get('content'))
            if len(types) >= max_events:
                return types, texts, cards
    return types, texts, cards

# ── 1. 代码特征在场 ─────────────────────────────
eng_src = open(r'H:\HarvenAI\src\core\engine.py', encoding='utf-8').read()
pa_src = open(r'H:\HarvenAI\src\core\panel_assist.py', encoding='utf-8').read()
check('engine 分诊已挂 send()', '#1432' in eng_src and '_hit_intake' in eng_src)
check('panel_assist 触发词已补', "'存入'" in pa_src and "'存储'" in pa_src)

# ── 2. 词表单测：classify_home 现在认这句话 ─────
from src.core.panel_assist import classify_home
cls = classify_home('存入面板电脑存储 书房')
check('classify_home 触发', cls.get('trigger') is True, cls)
check('classify_home 路由 home_items', cls.get('panel_id') == 'home_items', cls)
cls2 = classify_home('这张图里是什么？')
check('看图提问不误触发', cls2.get('trigger') is False, cls2)

# ── 3. 带图 + 触发词 → 确定性回复，不走看图 ─────
types1, text1, cards1 = chat_stream({
    'message': '存入面板电脑存储 书房', 'query_mode': 'kb_only',
    'images': [DATAURL]})
full1 = text1
check('带图+触发词：有响应', len(types1) > 0)
check('带图+触发词：确定性录入回复', '已收到' in full1 and ('待确认' in full1 or '入档' in full1), full1[:120])
check('带图+触发词：未走看图直答', not any('vision' in t for t in types1), types1[:12])

# ── 4. 等 worker 落账（抽取可能走视觉 LLM，最多等 90s） ──
print('[等待默录 worker 落账…]')
recorded = False
detail = ''
for i in range(18):
    time.sleep(5)
    try:
        from src.core.panel_assist import list_pending
        pend = [p for p in list_pending('home_items') if '书房' in (p.get('source_text') or '') or '存入面板' in (p.get('source_text') or '')]
        if pend:
            recorded = True
            detail = f"pending#{pend[0]['id']} records={pend[0]['records']}"
            break
        import sqlite3
        from src.core.panel_manager import DB_PATH as _PM_DB
        _c = sqlite3.connect(str(_PM_DB))
        row = _c.execute("SELECT * FROM panel_home_items ORDER BY id DESC LIMIT 1").fetchone()
        _c.close()
        if row and ('书房' in str(row) or '存储' in str(row)) and str(row[0]) > '27':
            recorded = True
            detail = f'panel row={row}'
            break
    except Exception as e:
        detail = str(e)[:80]
check('默录链落账（待确认或直入）', recorded, detail)

# ── 5. 带图 + 看图提问 → 仍走看图（不误拦） ─────
types2, text2, cards2 = chat_stream({
    'message': '这张图里是什么？', 'query_mode': 'kb_only',
    'images': [DATAURL]}, max_events=30)
check('带图+看图提问：未被误拦', len(types2) > 0 and '已收到' not in text2, types2[:10])

print(f"--- {sum(results)}/{len(results)} passed ---")
sys.exit(0 if all(results) else 1)
