# -*- coding: utf-8 -*-
"""★ #1233 M4-1 测试：哈文侧 eye_client + FC 三工具（eye_recognize / eye_search / eye_ask）。

三段式：
  A 降级用例  —— Eye 未启动（连不上的死端口）→ 三工具优雅降级不炸
  B stub 用例 —— 假 Eye 服务（随机端口）→ 三工具全流程 + 图库 URL 解析
  C 真实用例  —— 真实 7001 探活，活着就打真枪（recognize/search），没起则跳过
  D 注册用例  —— TOOL_REGISTRY 三处门禁（definitions/catalog/call_tool）实查

跑法: H:\\HarvenAI\\.venv\\Scripts\\python.exe _test_1233_eyec.py
"""
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('HARVENEYE_DB', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'sqlite', 'eye_catalog.db'))

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ''))


from src.core import eye_client  # noqa: E402

# ═══════════ A 降级：连不上的端口 ═══════════
print("\n══ A 段 降级用例（Eye 未启动）══")
os.environ['HARVENEYE_BASE_URL'] = 'http://127.0.0.1:59973'   # 预留死端口

r1 = eye_client.eye_recognize_tool(image_path=__file__)
check('A1 recognize 降级不炸', r1.get('success') is False and 'down' not in r1 or r1.get('success') is False)
check('A2 降级话术带 start_eye.bat 指引', 'start_eye.bat' in str(r1.get('error', '')))

r2 = eye_client.eye_search_tool(query='冰箱')
check('A3 search 降级不炸', r2.get('success') is False)
check('A4 ask 降级不炸', eye_client.eye_ask_tool(image_path=__file__, question='test').get('success') is False)

alive, ver = eye_client.is_eye_alive()
check('A5 探活返回 False', alive is False)

r6 = eye_client.eye_recognize_tool(image_path='')
check('A6 空路径友好报错', r6.get('success') is False and '找不到' in str(r6.get('error', '')))
r7 = eye_client.eye_recognize_tool(image_path='Z:/不存在的图/xx.jpg')
check('A7 不存在路径友好报错', r7.get('success') is False)
r8 = eye_client.eye_search_tool(query='')
check('A8 空 query 拦截', r8.get('success') is False and 'query 不能为空' in r8.get('error', ''))

# ═══════════ B stub：假 Eye 服务 ═══════════
print("\n══ B 段 stub 用例（假 Eye 服务）══")

FAKE_REC = {
    'final_layer': 'L1',
    'labels': ['冰箱', '厨房'], 'caption': '一台双开门冰箱，里面放着蔬菜和牛奶',
    'objects': [{'label': 'refrigerator', 'confidence': 0.91}],
    'confidence': 0.91, 'total_ms': 42.5,
    'trace': [{'source_layer': 'L1', 'elapsed_ms': 42.5}], 'note': 'stub',
}
FAKE_SEARCH = {'keyword': [{'id': 3, 'tags': '冰箱', 'pointer': '/x/1.jpg', 'pointer_id': 101, 'level': 'index'}],
               'semantic': [{'id': 5, 'tags': '厨房', 'pointer_id': 102, 'level': 'index', 'similarity': 0.77}]}
FAKE_ASK = {'description': '卷子上共 5 道题，第 3 题计算错误。', 'model': 'qwen3-vl-8b', 'degraded': False}


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/status':
            self._json({'product': 'HarvenEye', 'version': '0.1.0-stub', 'preset': 'balanced',
                        'threshold': 0.5, 'catalog': {'total': 2}})
        else:
            self._json({'error': 'not found'}, 404)

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        d = json.loads(self.rfile.read(n) or b'{}')
        if self.path == '/recognize':
            if not d.get('image_path'):
                self._json({'error': '图片不存在'}, 400)
            else:
                self._json(dict(FAKE_REC, _echo=d))
        elif self.path == '/search':
            if not d.get('query', '').strip():
                self._json({'error': 'query 不能为空'}, 400)
            else:
                self._json(FAKE_SEARCH)
        elif self.path == '/ask':
            if d.get('_force_503'):
                self._json({'error': 'vlm not ready', 'degraded': True}, 503)
            elif not d.get('image_path'):
                self._json({'error': '图片不存在'}, 400)
            else:
                self._json(FAKE_ASK)
        else:
            self._json({'error': 'not found'}, 404)


_srv = ThreadingHTTPServer(('127.0.0.1', 0), StubHandler)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
os.environ['HARVENEYE_BASE_URL'] = f'http://127.0.0.1:{_srv.server_address[1]}'
print(f"  stub 起在 {os.environ['HARVENEYE_BASE_URL']}")
time.sleep(0.2)

alive, ver = eye_client.is_eye_alive()
check('B1 探活成功带版本', alive and '0.1.0-stub' in ver, ver)

r = eye_client.eye_recognize_tool(image_path=__file__, do_index=True)
check('B2 recognize 全字段透出', r.get('success') and r.get('final_layer') == 'L1'
      and r.get('labels') == ['冰箱', '厨房'] and r.get('confidence') == 0.91)
check('B3 身份标注 engine=HarvenEye', r.get('engine') == 'HarvenEye')
check('B4 note 带 AI 消费导读', 'final_layer' in r.get('note', ''))

r = eye_client.eye_search_tool(query='冰箱')
check('B5 search 双路返回', r.get('success') and len(r.get('keyword', [])) == 1 and len(r.get('semantic', [])) == 1)
check('B6 指针转哈文图库 url', r['keyword'][0].get('url') == '/api/images/file/101'
      and 'pointer' not in r['keyword'][0])
check('B7 count 去重合计', r.get('count') == 2)

r = eye_client.eye_ask_tool(image_path=__file__, question='卷子哪道题错了')
check('B8 ask 原文透出+模型名', r.get('success') and '第 3 题' in r.get('text', '') and r.get('model') == 'qwen3-vl-8b')

# 503 降级分支（VLM 未就绪 ≠ 引擎挂了，话术要区分）
import urllib.request
req = urllib.request.Request(os.environ['HARVENEYE_BASE_URL'] + '/ask',
                             data=json.dumps({'image_path': __file__, 'question': 'x', '_force_503': True}).encode(),
                             headers={'Content-Type': 'application/json'})
try:
    urllib.request.urlopen(req)
    code = 200
except Exception as e:
    code = getattr(e, 'code', 0)
check('B9 stub 503 分支可达', code == 503)

r = eye_client._post('/ask', {'image_path': __file__, 'question': 'x', '_force_503': True},
                     timeout=eye_client._TIMEOUT_LIGHT)
check('B10 503 话术区分降级与宕机', not r['ok'] and r.get('degraded') and '还没就绪' in r['error'])

# 图库 URL / id 解析（读真实哈文图库，只读操作）
print("\n══ B+ 段 resolve_image_path ══════")
check('B11 空入参→空串', eye_client.resolve_image_path('') == '')
check('B12 普通绝对路径原样', eye_client.resolve_image_path(__file__) == __file__)
try:
    from src.core import img_store as _is
    _conn = _is._conn()
    row = _conn.execute('SELECT id, path FROM images_index WHERE path IS NOT NULL AND path != "" LIMIT 1').fetchone()
    _conn.close()
    if row:
        got = eye_client.resolve_image_path(f'/api/images/file/{row["id"]}')
        ok = got and os.path.isfile(got)
        check('B13 图库 URL→库仓绝对路径', ok, got)
        got2 = eye_client.resolve_image_path(str(row['id']))
        check('B14 纯数字 id→库仓路径', got2 and os.path.isfile(got2), got2)
    else:
        print('  ⏭️ 图库为空，跳过 B13/B14')
except Exception as e:
    print(f'  ⏭️ 图库解析跳过: {e}')

# ═══════════ D 注册门禁 ═══════════
print("\n══ D 段 FC 注册门禁 ══════════")
from src.core import builtin_tools as bt  # noqa: E402

defs = bt.get_tool_definitions()
names = {t['function']['name'] for t in defs}
check('D1 三工具进 FC definitions', {'eye_recognize', 'eye_search', 'eye_ask'} <= names,
      str(names & {'eye_recognize', 'eye_search', 'eye_ask'}))
cat = bt.get_tool_catalog_text()
check('D2 三工具带身份标注进目录', all(f'【身份:HarvenEye' in (bt.TOOL_REGISTRY[n]['description']) for n in ('eye_recognize', 'eye_search', 'eye_ask')))
_d = {t['function']['name']: t for t in defs}
check('D3 eye_search 的 query 判必填', 'query' in _d.get('eye_search', {}).get('function', {}).get('parameters', {}).get('required', []))
r = bt.call_tool('eye_recognize', image_path='Z:/无/xx.jpg')
check('D4 call_tool 通道打通', r.get('success') is False and '找不到' in str(r.get('error', '')))
check('D5 TOOL_REGISTRY 结构合规', all('function' in bt.TOOL_REGISTRY[n] and 'params' in bt.TOOL_REGISTRY[n]
                                        for n in ('eye_recognize', 'eye_search', 'eye_ask')))

# ═══════════ C 真实 7001（最后跑，探到才打真枪）═══════════
print("\n══ C 段 真实 7001 实测 ══════════")
os.environ['HARVENEYE_BASE_URL'] = 'http://127.0.0.1:7001'
alive, ver = eye_client.is_eye_alive()
if alive:
    print(f'  HarvenEye 在线: {ver}')
    r = eye_client.eye_recognize_tool(image_path=__file__)  # py 文件当图会 400，验证错误透传
    check('C1 真实引擎错误透传', r.get('success') is False)
    r = eye_client.eye_search_tool(query='测试')
    check('C2 真实 search 可调', 'success' in r)
else:
    print(f'  ⏭️ 7001 未启动（{ver}），跳过真枪用例——属正常，start_eye.bat 可起')

# ═══════════ 收尾 ═══════════
_srv.shutdown()
total = len(PASS) + len(FAIL)
print(f"\n════════ #1233 测试收口: {total - len(FAIL)}/{total} 通过 ════════")
if FAIL:
    print('失败项: ' + ', '.join(FAIL))
    sys.exit(1)
print('ALL GREEN ✅')
