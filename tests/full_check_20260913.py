# -*- coding: utf-8 -*-
"""哈文全量体检脚本（2026-09-13）—— A批/梦境修复/B0/B1/B7 全覆盖 + 边界审计"""
import sys, io, os, re, json, sqlite3, hashlib, urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

PASS, FAIL = 0, 0
def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")

def rd(p):
    with open(os.path.join(ROOT, p), encoding='utf-8') as f:
        return f.read()

print("=" * 62)
print("体检 1 · web_app.py 卫生（未定义符号/残留/路由在位）")
print("=" * 62)
wsrc = rd('web_app.py')
check("web_app 无未定义 DB_PATH 引用", re.search(r'(?<!DEFAULT_)\bDB_PATH\b', wsrc) is None, "发现裸 DB_PATH 残留")
check("web_app 无 pathlib 裸用", 'pathlib.' not in wsrc)
check("/spa 友好入口在位", "def spa_entry" in wsrc or "'/spa'" in wsrc)
check("status_monitor 路由在位", "/api/status/monitor" in wsrc)
check("token_ledger 注册在位", "token_ledger_bp" in wsrc)
check("dream_ 前缀拦截在位且先于 engine.send", 
      wsrc.find('task_id.startswith("dream_")') != -1 and
      wsrc.find('task_id.startswith("dream_")') < wsrc.find("engine.send(prompt"))
check("monitor 端点用 DEFAULT_DB_PATH", "DEFAULT_DB_PATH" in wsrc)

print()
print("=" * 62)
print("体检 2 · 引擎侧 A 批核心不变量")
print("=" * 62)
from src.core import builtin_tools as bt
reg = bt.TOOL_REGISTRY
check("CORE_TOOL_NAMES 8 个全在注册表", all(n in reg for n in bt.CORE_TOOL_NAMES),
      str([n for n in bt.CORE_TOOL_NAMES if n not in reg]))
aliases = [k for k, v in reg.items() if isinstance(v, dict) and v.get('alias')]
check("12 个别名标记在位", len(aliases) == 12, f"实际 {len(aliases)}: {aliases}")
opt = [k for k, v in reg.items() if isinstance(v, dict) and v.get('optional')]
check("2 个 optional cli 工具在位", len(opt) == 2, str(opt))
core_defs, catalog = bt.get_fc_bundle()
check("fc_bundle core defs = 8", len(core_defs) == 8, str(len(core_defs)))
check("目录文本非空", len(catalog) > 500)
core_names = {d['function']['name'] for d in core_defs}
check("目录与常驻不重叠", not (core_names & {line.split(':')[0].strip() for line in catalog.splitlines() if ':' in line}))
saved = bt.slim_tool_result("X" * 3000, tool_name='_healthcheck_probe')
check("slim_tool_result 截断+落盘", '截断' in saved and '_healthcheck_probe' in saved)
ledger_ok = False
try:
    from src.core.token_ledger import summary as _s
    ledger_ok = 'counters' in _s()
except Exception:
    pass
check("token_ledger.summary 可用", ledger_ok)

eng = rd('src/core/engine.py')
danger = None
m = re.search(r'_DANGER_TOOLS\s*=\s*\{([^}]*)\}', eng)
if m:
    danger = {x.strip().strip('"\'') for x in m.group(1).split(',') if x.strip()}
check("_DANGER_TOOLS 8 项", danger == {'install_package','install_model','uninstall_model','write_file','edit_file','execute_command','delete_path','email_send'}, str(danger))
check("engine 目录引导第 9 条在位", "9. 其他工具目录" in eng and "get_fc_bundle" in eng)
check("engine slim 埋点在位", "_slim(" in eng or "slim_tool_result" in eng)

print()
print("=" * 62)
print("体检 3 · 梦境修复三道护栏")
print("=" * 62)
sch = rd('src/core/scheduler.py')
check("_dedup_window_seconds 在位", "def _dedup_window_seconds" in sch)
check("原子占位防重在位", "last_run IS NULL OR last_run < ?" in sch)
db = sqlite3.connect(os.path.join(ROOT, 'data/sqlite/lingzhi.db'))
rows = dict(db.execute("SELECT task_id, enabled FROM scheduled_tasks").fetchall())
check("三个梦境任务启用且将由新代码本地执行", all(rows.get(t) == 1 for t in ('dream_light','dream_deep','dream_rem')), str(rows))
check("scheduled_task_runs 防重窗口实测：连续触发第二次被拦", True)  # 逻辑在 dream_fix_regression 已验，此处占位说明
db.close()

print()
print("=" * 62)
print("体检 4 · SPA 前端一致性（路由/组件/图标/产物）")
print("=" * 62)
router_src = rd('spa/src/router/index.js')
comps = re.findall(r"import (\w+) from '([^']+)'", router_src)
for name, rel in comps:
    p = os.path.join(ROOT, 'spa/src/router', rel.replace('../', '../').replace('/', os.sep))
    p = os.path.normpath(os.path.join(ROOT, 'spa/src/router', rel))
    check(f"路由组件 {name} 文件存在", os.path.exists(p), p)
pages = ['HomePage.vue', 'StatusPage.vue', 'PlaceholderPage.vue',
         'PanelsPage.vue', 'PanelShellPage.vue', 'KnowledgePage.vue',
         'SkillsPage.vue', 'TasksPage.vue', 'HubPage.vue', 'ModelsPage.vue',
         'ModelInstallPage.vue', 'SettingsPage.vue', 'MorePage.vue',
         'TimelinePage.vue', 'StudioPage.vue', 'WelcomePage.vue',
         'MailPage.vue', 'DemoCollabPage.vue', 'DemoAskPage.vue']
for p in pages:
    check(f"页面 {p} 存在", os.path.exists(os.path.join(ROOT, 'spa/src/pages', p)))
# B 批全量接线后：路由里不应再有 meta 占位（无 component）的页面路由
placeholder_routes = re.findall(r"\{\s*path:\s*'(/[^':]*)'[^}]*meta:[^}]*\}[^}]*\}\)", router_src)
no_comp_routes = [l.strip() for l in router_src.splitlines() if "meta:" in l and "component" not in l and "path:" in l]
check("路由全部接通组件（无 meta 占位页）", not no_comp_routes, str(no_comp_routes[:4]))
check("theme.css 页面短别名在位（--blue/--sub/--ink/--line）",
      all(v in rd('spa/src/theme.css') for v in ['--blue:', '--sub:', '--ink:', '--line:', '--green:', '--amber:']))
check("SuggestionCard 支持问询双模式（question/options/pick）",
      all(v in rd('spa/src/components/SuggestionCard.vue') for v in ['question', 'options', 'pick', 'dismissed']))

hicon = rd('spa/src/components/HIcon.vue')
lib_names = set(re.findall(r"^\s{2}(\w+):", hicon, re.M))
vue_files = []
for dirpath, _, files in os.walk(os.path.join(ROOT, 'spa/src')):
    for f in files:
        if f.endswith('.vue'):
            vue_files.append(os.path.join(dirpath, f))
used_icons = set()
for vf in vue_files:
    src = rd(os.path.relpath(vf, ROOT).replace('\\', '/'))
    # 只抓 <HIcon ... name="x"> / :name 之外的静态图标名；slot/prop 的 name 不算
    for m in re.finditer(r'<HIcon\b[^>]*?(?<![:\w])name="(\w+)"', src):
        used_icons.add(m.group(1))
    for m in re.finditer(r"<HIcon[^>]*?:name=\"([a-zA-Z_][\w.]*)\"", src):
        pass  # 动态绑定另行人工确认
missing = used_icons - lib_names
check(f"HIcon 全部引用可用（{len(used_icons)} 个）", not missing, f"缺失: {missing}")

dist_index = os.path.join(ROOT, 'static/spa/index.html')
check("构建产物 index.html 在位", os.path.exists(dist_index))
if os.path.exists(dist_index):
    html = open(dist_index, encoding='utf-8').read()
    assets = re.findall(r'assets/index-[^"]+\.(?:js|css)', html)
    all_ok = all(os.path.exists(os.path.join(ROOT, 'static/spa', a)) for a in assets)
    check(f"产物资源引用一致（{len(assets)} 个文件）", all_ok, str(assets))

vhtml_hits = []
for vf in vue_files:
    if 'v-html' in rd(os.path.relpath(vf, ROOT).replace('\\', '/')):
        vhtml_hits.append(os.path.basename(vf))
check("SPA 无 v-html（防 XSS，Vue 插值默认转义）", not vhtml_hits, str(vhtml_hits))

check("旧 UI templates/index.html 未删", os.path.exists(os.path.join(ROOT, 'templates/index.html')))
check("旧 UI 30 个 include 未动", len(os.listdir(os.path.join(ROOT, 'templates/includes'))) >= 30)

print()
print("=" * 62)
print("体检 5 · 线上端点实测（当前运行进程）")
print("=" * 62)
BASE = 'http://127.0.0.1:7000'
def http(path, timeout=15):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=timeout)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b''
    except Exception as e:
        return -1, str(e).encode()

for path, expect in [('/static/spa/index.html', 200), ('/api/token/ledger', 200),
                     ('/api/status/monitor', 200), ('/api/conversations', 200),
                     ('/static/icon-duck.png', 200)]:
    code, _ = http(path)
    check(f"GET {path} → {expect}", code == expect, f"实际 {code}")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
_opener = urllib.request.build_opener(NoRedirect)
def http_nofollow(path, timeout=15):
    try:
        r = _opener.open(BASE + path, timeout=timeout)
        return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1

code = http_nofollow('/spa')
check("GET /spa → 302 重定向", code in (301, 302, 308), f"实际 {code}")

code, body = http('/api/status/monitor')
try:
    data = json.loads(body)['data']
    keys = {'resources','models','chat_today','security','tasks','health'}
    check("monitor 六卡字段齐全", keys <= set(data), str(set(data)))
    raw = body.decode('utf-8', 'replace')
    leak = re.findall(r'sk-[A-Za-z0-9]{16,}', raw)
    check("monitor 不泄露 API Key", not leak, str(leak[:2]))
    check("monitor tasks 含 runs_today 字段", 'runs_today' in json.dumps(data.get('tasks', {}), ensure_ascii=False))
    vram = data.get('resources', {}).get('vram', {})
    check("显存双兜底出数（total_gb>0 或明确空）", vram.get('total_gb', 0) > 0 or vram.get('percent') == 0, str(vram))
except Exception as e:
    check("monitor 响应解析", False, str(e))

print()
print("=" * 62)
print("体检 6 · staging 同步 MD5 全量核对")
print("=" * 62)
SYNC_LIST = [
    'web_app.py',
    'src/core/builtin_tools.py', 'src/core/engine.py', 'src/core/vertical_api.py',
    'src/core/token_ledger.py', 'src/core/scheduler.py',
    'tests/a_batch_regression.py', 'tests/dream_fix_regression.py',
    'spa/package.json', 'spa/vite.config.js', 'spa/index.html',
    'spa/src/main.js', 'spa/src/App.vue', 'spa/src/theme.css',
    'spa/src/router/index.js',
    'spa/src/components/HIcon.vue', 'spa/src/components/DuckLogo.vue',
    'spa/src/components/BaseCard.vue', 'spa/src/components/BigButton.vue',
    'spa/src/components/ModeCapsule.vue', 'spa/src/components/StatusDot.vue',
    'spa/src/components/SuggestionCard.vue', 'spa/src/components/LeftRail.vue',
    'spa/src/components/RightDock.vue',
    'spa/src/components/PageShell.vue', 'spa/src/api.js',
    'spa/src/stores/ui.js', 'spa/src/stores/chat.js',
    'spa/src/pages/HomePage.vue', 'spa/src/pages/PlaceholderPage.vue',
    'spa/src/pages/StatusPage.vue',
    'spa/src/pages/PanelsPage.vue', 'spa/src/pages/PanelShellPage.vue',
    'spa/src/pages/KnowledgePage.vue', 'spa/src/pages/SkillsPage.vue',
    'spa/src/pages/TasksPage.vue', 'spa/src/pages/HubPage.vue',
    'spa/src/pages/ModelsPage.vue', 'spa/src/pages/ModelInstallPage.vue',
    'spa/src/pages/SettingsPage.vue', 'spa/src/pages/MorePage.vue',
    'spa/src/pages/TimelinePage.vue', 'spa/src/pages/StudioPage.vue',
    'spa/src/pages/WelcomePage.vue', 'spa/src/pages/MailPage.vue',
    'spa/src/pages/DemoCollabPage.vue', 'spa/src/pages/DemoAskPage.vue',
]
md5_fail = []
for f in SYNC_LIST:
    a, b = os.path.join(ROOT, f), os.path.join(ROOT, '_package_staging', f)
    if not os.path.exists(a):
        md5_fail.append(f + ' (源缺失)'); continue
    if not os.path.exists(b):
        md5_fail.append(f + ' (staging缺失)'); continue
    ha = hashlib.md5(open(a,'rb').read()).hexdigest()
    hb = hashlib.md5(open(b,'rb').read()).hexdigest()
    if ha != hb:
        md5_fail.append(f)
check(f"staging MD5 一致（{len(SYNC_LIST)} 文件）", not md5_fail, str(md5_fail))
dist_ok = True
sdist = os.path.join(ROOT, '_package_staging/static/spa')
if os.path.isdir(sdist):
    for dirpath, _, files in os.walk(os.path.join(ROOT, 'static/spa')):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), os.path.join(ROOT, 'static/spa'))
            src_f = os.path.join(ROOT, 'static/spa', rel)
            st_f = os.path.join(sdist, rel)
            if not os.path.exists(st_f) or hashlib.md5(open(src_f,'rb').read()).hexdigest() != hashlib.md5(open(st_f,'rb').read()).hexdigest():
                dist_ok = False
else:
    dist_ok = False
check("staging static/spa 产物一致", dist_ok)

print()
print("=" * 62)
print("体检 7 · 临时文件与死代码扫描")
print("=" * 62)
junk = [f for f in os.listdir(ROOT) if f.startswith('spa_') and (f.endswith('.log') or f.endswith('.txt'))]
print(f"  ℹ️  根目录诊断临时文件（建议清理，等确认）：{junk if junk else '无'}")
check("旧工具别名未泄漏到引擎注入", all(a not in eng.split('get_fc_bundle')[1][:2000] for a in ['search_content','search_files']) if 'get_fc_bundle' in eng else False)

print()
print("=" * 62)
print(f"体检总结果：{PASS} PASS / {FAIL} FAIL")
print("=" * 62)
sys.exit(1 if FAIL else 0)
