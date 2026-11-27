# -*- coding: utf-8 -*-
"""SPA 全面健康检查 · 2026-09-16
1) 页面路由与老版本链接目标逐一探测（/spa、/static/spa、/docs/guide、#ch16 锚点…）
2) 自动收集 spa/src 里所有 GET 接口，用 Flask test client 逐一探测（5xx=FAIL）
"""
import io, sys, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')

results = []
def check(name, cond, detail=''):
    results.append(cond)
    print(('PASS  ' if cond else 'FAIL  ') + name + (('  → ' + str(detail)) if detail else ''))

# ── 1. 静态链接目标 ──
from web_app import app
app.config['TESTING'] = True
# ★ web_app 把 panel/skills 等 Blueprint 注册在 if __name__ == '__main__' 块里，
#   import 时不会执行 → 测试环境手动补注册（与真实启动一致），否则误报 404
try:
    from src.core.panel_routes import bp as _panel_bp
    app.register_blueprint(_panel_bp)
except Exception:
    pass
try:
    from src.core.skills_routes import bp as _skills_bp
    app.register_blueprint(_skills_bp)
except Exception:
    pass
c = app.test_client()

def probe(path):
    try:
        r = c.get(path)
        return r.status_code
    except Exception as e:
        return 'ERR:' + str(e)[:60]

r = c.get('/spa')
check('/spa 302 → SPA', r.status_code == 302 and '/static/spa/index.html' in r.headers.get('Location', ''), r.status_code)
check('/static/spa/index.html 200', probe('/static/spa/index.html') == 200)
check('/（SPA 时代 302 跳转）302', r.status_code == 302 and '/static/spa' in r.headers.get('Location', ''),
      (r.status_code, r.headers.get('Location', '')))   # ★ #1587 对齐：根路径已改 302 跳 SPA，原期望 200（老界面已退役）
check('/docs/guide 200（修复后指南链接）', probe('/docs/guide') == 200)
check('/docs/guide.html 200（老界面用的链接）', probe('/docs/guide.html') == 200)

guide = open(r'H:\HarvenAI\docs\guide.html', encoding='utf-8').read()
check('guide.html 含 ch16 锚点（常见问题章）', 'id="ch16"' in guide)

spa_src = open(r'H:\HarvenAI\spa\src\pages\MorePage.vue', encoding='utf-8').read()
check('MorePage 已无 /guide 死链', 'href="/guide"' not in spa_src)
check('MorePage 已无 /#help 老界面链', '/#help' not in spa_src)

# ── 2. 自动收集 SPA 全部 GET 接口并探测 ──
endpoints = set()
for fp in glob.glob(r'H:\HarvenAI\spa\src\**\*.vue', recursive=True) + glob.glob(r'H:\HarvenAI\spa\src\*.js') + glob.glob(r'H:\HarvenAI\spa\src\**\*.js', recursive=True):
    try:
        src = open(fp, encoding='utf-8').read()
    except Exception:
        continue
    for m in re.finditer(r"""(?:getJSON|fetch)\(\s*[`'"](/api/[^`'"]+)""", src):
        ep = m.group(1)
        if '${' in ep:
            continue  # 带参数插值的跳过（需具体上下文）
        endpoints.add(ep.split('?')[0])

print('\n—— 收集到 %d 个 GET 接口，逐一探测 ——' % len(endpoints))
n_ok = n_warn = n_bad = 0
bads, warns = [], []
for ep in sorted(endpoints):
    st = probe(ep)
    if st == 200:
        n_ok += 1
    elif isinstance(st, int) and 400 <= st < 500:
        n_warn += 1; warns.append((ep, st))
    else:
        n_bad += 1; bads.append((ep, st))

print('200 OK: %d · 4xx(缺参数类): %d · 5xx/异常: %d' % (n_ok, n_warn, n_bad))
for ep, st in warns:
    print('WARN  %s → %s' % (ep, st))
for ep, st in bads:
    print('BAD   %s → %s' % (ep, st))

check('SPA GET 接口零 5xx', n_bad == 0, bads)

n_pass = sum(1 for x in results if x)
print('\n断言: %d/%d 通过' % (n_pass, len(results)))
sys.exit(0 if (n_pass == len(results)) else 1)
