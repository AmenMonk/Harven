# -*- coding: utf-8 -*-
"""#1242 知识库方案A测试：开关API/即时生效/嵌入预热"""
import os
import sys

sys.path.insert(0, r'H:\HarvenAI')
os.chdir(r'H:\HarvenAI')

PASS, FAIL = [], []

def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''))

import web_app
from src.models.database import get_preference, set_preference

# 还原保护
_saved = get_preference('kb_search_enabled', 'true')
print(f"[准备] kb_search_enabled 原值 = {_saved}")

try:
    # ── T1 API：GET 默认状态 ──
    print("\n[T1] /api/kb/toggle GET")
    c = web_app.app.test_client()
    r = c.get('/api/kb/toggle')
    j = r.get_json()
    check("T1a 接口 200 且结构对", r.status_code == 200 and 'enabled' in j, str(j))

    # ── T2 关闭 → engine 即时生效 ──
    print("\n[T2] 关闭 → engine 即时生效")
    r = c.post('/api/kb/toggle', json={'enabled': False})
    j = r.get_json()
    check("T2a POST 关成功", r.status_code == 200 and j.get('enabled') is False, str(j))
    check("T2b 偏好已落库", str(get_preference('kb_search_enabled')).lower() == 'false')
    from src.core.engine import ConversationEngine
    def _make_eng():
        eng = ConversationEngine.__new__(ConversationEngine)  # 免初始化轻量实例
        eng._query_mode = 'kb_only'
        eng._specialization_prompt = None
        eng._has_active_task = lambda: False
        return eng
    eng = _make_eng()
    do_search, _ = eng._resolve_query_mode({}, '帮我看看资料')
    check("T2c engine 检索已关（即时无重启）", do_search is False)
    r2 = c.get('/api/kb/toggle')
    check("T2d GET 回读=关", r2.get_json().get('enabled') is False)

    # ── T3 开启 → 恢复 ──
    print("\n[T3] 开启 → 恢复")
    r = c.post('/api/kb/toggle', json={'enabled': True})
    check("T3a POST 开成功", r.get_json().get('enabled') is True)
    eng2 = _make_eng()
    do_search2, _ = eng2._resolve_query_mode({}, '帮我看看资料')
    check("T3b engine 检索已恢复", do_search2 is True)

    # ── T4 嵌入预热函数 ──
    print("\n[T4] 嵌入预热")
    check("T4a 预热函数存在", hasattr(web_app, '_preload_knowledge_embedding'))
    web_app._preload_knowledge_embedding()
    check("T4b 预热执行不炸", True)
    from src.core.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    emb = getattr(kb, '_embedding_model', None)
    check("T4c 嵌入模型已就绪", emb is not None, type(emb).__name__ if emb else 'None')
finally:
    set_preference('kb_search_enabled', _saved)
    print(f"\n[还原] kb_search_enabled → {_saved}")

print('\n' + '═' * 50)
print(f'结果：{len(PASS)} PASS / {len(FAIL)} FAIL')
if FAIL:
    print('失败项：', FAIL)
    sys.exit(1)
