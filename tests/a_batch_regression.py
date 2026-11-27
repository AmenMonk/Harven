# -*- coding: utf-8 -*-
"""A批Token改造回归验证脚本（2026-09-13）"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, __file__.rsplit('\\tests\\', 1)[0] if '\\tests\\' in __file__ else __file__.rsplit('/tests/', 1)[0])
import os
_ROOT = sys.path[0]

PASS, FAIL = 0, 0
def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")

print("=" * 60)
print("A批回归 · builtin_tools.py")
print("=" * 60)
from src.core import builtin_tools as bt

# A1: 注册表无重复键（dict 本身不会重复，验证我们删掉的键确实只出现一次语义：详版描述在位）
reg = bt.TOOL_REGISTRY
check("A1 read_file 详版定义在位", '当用户问' in reg['read_file']['description'])
check("A1 list_dir 详版定义在位", '当用户问' in reg['list_dir']['description'])

# A5: 合并工具存在 + 旧名别名在位
for merged in ['search', 'task', 'skill', 'skill_script', 'tool_schema']:
    check(f"A5 合并工具 {merged} 存在", merged in reg)
for alias in ['search_content', 'search_files', 'search_knowledge', 'search_conversation',
              'task_create', 'task_list', 'task_update', 'task_get',
              'execute_skill', 'list_skills', 'execute_skill_script', 'list_skill_scripts']:
    check(f"A5 别名 {alias} 在位且带alias标记", alias in reg and reg[alias].get('alias') is True)

# 合并工具功能抽查（只读操作）
r1 = bt.call_tool('search', type='filename', pattern='*.py', path='src/core')
check("A5 search(filename) 可执行", isinstance(r1, dict) and r1.get('success') in (True, False), str(r1)[:120])
r2 = bt.call_tool('task_list')
check("A5 task(list) 可执行", isinstance(r2, dict))
r3 = bt.call_tool('task_create', subject='[回归测试] A批验证')
check("A5 task(create) 可执行", r3.get('success') is True, str(r3)[:120])
r4 = bt.call_tool('skill', action='list')
check("A5 skill(list) 可执行", isinstance(r4, dict))
r5 = bt.call_tool('skill_script', action='list')
check("A5 skill_script(list) 可执行", isinstance(r5, dict))
r6 = bt.call_tool('tool_schema', names='task,email_fetch')
check("A3 tool_schema 返回参数定义", r6.get('success') and len(r6.get('tools', [])) == 2 and r6['tools'][0]['function']['parameters'].get('required'))
r7 = bt.call_tool('tool_schema', names='task,not_exist_tool')
check("A3 tool_schema 报告未知工具", 'not_exist_tool' in r7.get('not_found', []))

# A3: 目录式注入
core_defs, catalog = bt.get_fc_bundle()
core_names = {d['function']['name'] for d in core_defs}
# ★ #950（2026-10-03）：常驻工具 8→11（query_panel/web_search/search_images 等后续批次入常驻），断言改为跟随 CORE_TOOL_NAMES 动态校验
# ★ #1258（2026-10-28）：11→15（set_reminder/add_inbox_note/describe_image/remember_fact/query_my_records 等入常驻），硬编码改动态；
#   且需排除 requires 门禁工具（web_search 联网授权制：点🌐才注入，默认不在常驻=正确行为）
_gated = {n for n, t in bt.TOOL_REGISTRY.items() if isinstance(t, dict) and t.get('requires')}
check("A3 常驻工具数=CORE_TOOL_NAMES", len(core_defs) == len(bt.CORE_TOOL_NAMES) - len(_gated & bt.CORE_TOOL_NAMES), f"实际{len(core_defs)} vs CORE {len(bt.CORE_TOOL_NAMES)}(门禁{_gated & bt.CORE_TOOL_NAMES})")
check("A3 常驻集合正确", core_names == bt.CORE_TOOL_NAMES - _gated)
check("A3 目录文本非空", len(catalog) > 200)
check("A3 目录不含别名工具", all(('alias' not in line) for line in [catalog]) and 'search_content:' not in catalog)
n_catalog = len([l for l in catalog.split('\n') if l.strip('- ').strip()])
print(f"  [INFO] 目录条目数: {n_catalog}, 目录体积: {len(catalog)} 字符 (约{len(catalog)//4} token)")
# 目录里的工具都能通过 tool_schema 拿到定义
cat_names = [l.split(':')[0].lstrip('- ').strip() for l in catalog.split('\n') if l.strip()]
all_defs = bt.get_tool_definitions()
all_names = {d['function']['name'] for d in all_defs}
missing = [n for n in cat_names if n not in all_names]
check("A3 目录每个工具都有完整定义可查", not missing, f"缺失: {missing}")

# A6: CLI-Anything 动态化
cli_in_defs = {'cli_anything_execute', 'cli_anything_install'} & all_names
cli_in_catalog = {'cli_anything_execute', 'cli_anything_install'} & set(cat_names)
hub_installed = bt._optional_available('cli_anything')
if hub_installed:
    check("A6 已装hub→execute/install在defs与目录", cli_in_defs and cli_in_catalog)
else:
    check("A6 未装hub→execute/install不进defs", not cli_in_defs, str(cli_in_defs))
    check("A6 未装hub→execute/install不进目录", not cli_in_catalog, str(cli_in_catalog))

# A4: 结果瘦身
import os
long_text = 'X' * 5000
slimmed = bt.slim_tool_result(long_text, tool_name='test_tool')
check("A4 超长结果被截断到~1000", len(slimmed) < 1200 and 'X' * 1000 in slimmed)
check("A4 落盘指针在位", 'data' in slimmed and 'tool_results' in slimmed and 'read_file' in slimmed)
full_file = slimmed.split('全文已存: ')[1].split('，')[0] if '全文已存: ' in slimmed else ''
check("A4 全文已落盘且可读", full_file and os.path.exists(full_file) and os.path.getsize(full_file) == 5000)
short = bt.slim_tool_result('short result', tool_name='t')
check("A4 短结果原样通过", short == 'short result')

print()
print("=" * 60)
print("A批回归 · engine.py")
print("=" * 60)
import ast
src = open(_ROOT + '/src/core/engine.py', encoding='utf-8').read()
tree = ast.parse(src)
# A1: 危险工具清单与实际危险键全等
danger_ast = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == '_DANGER_TOOLS':
        danger_ast = {e.value for e in node.value.elts}
real_danger = {k for k, v in reg.items()
               if k in {'delete_path', 'execute_command', 'email_send', 'write_file',
                        'edit_file', 'install_package', 'install_model', 'uninstall_model'}}
check("A1 危险清单与实际键全等", danger_ast == real_danger, f"engine={danger_ast} real={real_danger}")
check("A1 无幽灵名", not (danger_ast - set(reg.keys())))
check("A3 目录式注入接线(get_fc_bundle)", 'get_fc_bundle' in src and '目录式' in src)
check("A3 FC提示词含tool_schema指引", 'tool_schema' in src and '其他工具目录' in src)
check("A4 瘦身接线(slim_tool_result)", 'slim_tool_result' in src)
check("A7 账本埋点(b_plan_hits/fc_turns/fc_rounds)", all(k in src for k in ('b_plan_hits', 'fc_turns', 'fc_rounds')))

print()
print("=" * 60)
print("A批回归 · vertical_api.py / web_app.py / token_ledger")
print("=" * 60)
from src.core import vertical_api as va
pseudo = [m['id'] for m in va._BUILTIN_MODELS if m.get('pseudo')]
check("A2 三个伪模型已标记", sorted(pseudo) == ['code-generator', 'data-analysis', 'translation'], str(pseudo))
listed = va._get_all_models()
listed_ids = {m['id'] for m in listed}
check("A2 垂直列表无伪模型", not (set(pseudo) & listed_ids))
# ★ 2026-09-17 断言修正：9/14 定案「只显示实际可用模型」，SD/Bark 占位假模型被 _model_available 过滤属正确行为
# ★ 2026-11-27 断言修正：MusicGen 已于 11-14 卸载（被 ACE-Step 替代，_model_available 如实返回 False）
#   现可用真模型 = ace-step / z-image-turbo / huatuo-medical
check("A2 可用真模型在列(ACE-Step/华佗)", {'ace-step', 'huatuo-medical'} <= listed_ids)
check("A2 占位假模型已被过滤(SD/Bark)", not ({'stable-diffusion', 'bark-tts'} & listed_ids))
# 伪模型仍可被旧引用执行（execute入口按id查找）
m = next((m for m in va._BUILTIN_MODELS if m['id'] == 'translation'), None)
check("A2 伪模型定义保留（旧引用兼容）", m is not None and m['engine'] == 'model')

wsrc = open(_ROOT + '/web_app.py', encoding='utf-8').read()
check("A7 web_app注册token账本路由", 'token_ledger_bp' in wsrc)
from src.core import token_ledger as tl
tl.record('fc_turns', tools=8, catalog_chars=1700)
_s = tl.summary()
check("A7 账本record/summary可用", _s['counters']['fc_turns'] >= 1 and len(_s['recent']) >= 1)
check("A7 账本Blueprint可用", tl.bp is not None)

# token_ledger 与 slim 联动
check("A7 瘦身事件已入账本", tl.summary()['counters'].get('tool_result_slimmed', 0) >= 1)

print()
print("=" * 60)
print(f"结果: {PASS} PASS / {FAIL} FAIL")
print("=" * 60)
sys.exit(1 if FAIL else 0)
