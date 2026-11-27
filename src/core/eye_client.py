"""★ #1233（2026-10-26）M4-1 HarvenEye 客户端 — 哈文 ↔ HarvenEye 握手的哈文侧。

═══ 隔离红线（规则文档第六章，永久有效）═══
本模块对 HarvenEye **只发 HTTP 请求，禁止 import 其任何引擎代码**——
两套代码库物理隔离，避免 AGPL 授权争议衍生到哈文。改 HarvenEye 代码
（H:\\HarvenEye\\）永远碰不到本文件，反之亦然。

接口（HarvenEye 默认 http://127.0.0.1:7001，config.yaml 可配 harveneye.base_url）:
  POST /recognize  {image_path, preset?, do_index?, force_vlm?}  识别主入口（L0~L3 溯源）
  POST /search     {query, top_k?}                               以文搜图（关键词+语义双路）
  POST /ask        {image_path, question?}                       本地 VLM 直问（503=降级）
  GET  /status                                                   引擎/库状态（探活用）

优雅降级：HarvenEye 没启动时返回 success=False + 小白话提示，绝不炸哈文
主流程——哈文的看图说话（describe_image 等本地视觉）不依赖 HarvenEye，
两边是「可选加速 + 深度识别」关系，缺谁都能跑。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import requests

# ★ 本地回环专用 Session：trust_env=False 屏蔽系统代理（HTTP_PROXY 等环境变量）。
#   127.0.0.1 永不该走代理——否则装了代理软件/沙箱注入代理的机器上，
#   「Eye 未启动」会被误报成各种代理错误（#1233 测试实锤）
_session = requests.Session()
_session.trust_env = False

# ── 配置读取（环境变量 > config.yaml > 出厂默认）──

def _base_url() -> str:
    env = os.environ.get('HARVENEYE_BASE_URL', '').strip()
    if env:
        return env.rstrip('/')
    try:
        from src.utils.config import get_config
        cfg = str(get_config().get('harveneye.base_url', '') or '').strip()
        if cfg:
            return cfg.rstrip('/')
    except Exception:
        pass
    return 'http://127.0.0.1:7001'

# 识别/问图走 VLM 兜底可能数十秒，给足；搜图/探活要快，超时即视为未启动
_TIMEOUT_HEAVY = float(os.environ.get('HARVENEYE_TIMEOUT', '180'))
_TIMEOUT_LIGHT = 8.0

_DOWN_HINT = ('视觉引擎没有启动。告诉用户：本地视觉引擎未运行，'
              '到 设置→视觉引擎 页检查，或双击 HarvenEye 目录里的 start_eye.bat 启动（会自动开浏览器控制台）。'
              '看图说话等基础视觉功能不受影响，仍可正常使用')


# ★ 节点⑥（2026-10-29）v1.3 融合退役 7001 后的统一话术：视觉子系统在哈文进程内，
# 不存在“启动 Eye”；缺的是底座引擎安装/理解大脑钉选。
_DOWN_HINT_NEW = ('视觉引擎未就绪。告诉用户：到 设置→视觉引擎 页一键安装底座，'
                  '并在“理解大脑”里选一个视觉模型（外脑或本地 GGUF）后即可看图/批改')


def _post(path: str, payload: dict, timeout: float) -> dict:
    """统一 POST。返回 {'ok': True, 'data': ...} 或 {'ok': False, 'error': ...}。
    任何网络异常都在这里转成友好提示，工具层不感知 HTTP 细节。"""
    url = _base_url() + path
    try:
        r = _session.post(url, json=payload, timeout=timeout)
    except requests.exceptions.ConnectionError:
        return {'ok': False, 'error': _DOWN_HINT, 'down': True}
    except requests.exceptions.Timeout:
        return {'ok': False, 'error': f'视觉引擎响应超时（{timeout:.0f}秒），识别任务可能太重，稍后再试'}
    except Exception as e:
        return {'ok': False, 'error': f'视觉引擎调用失败: {e}'}
    try:
        data = r.json()
    except Exception:
        return {'ok': False, 'error': f'视觉引擎返回了非 JSON 响应（HTTP {r.status_code}）'}
    if r.status_code >= 400:
        # ★ #1260（2026-10-29 A1 四件套）：503 话术按 Eye 真实状态分流——
        #   Eye 端 503 有两种：① models_ready=False 模型真没就绪；② VlmDegraded 推理超时/失败。
        #   旧版一刀切报「还没就绪」——120s 截断被误报成未就绪，用户重试必败（实测批改实测）
        if r.status_code == 503:
            if data.get('models_ready') is False:
                return {'ok': False, 'error': '视觉引擎的视觉大模型还没就绪（引擎正常，深度问答暂不可用）。'
                                              '基础识别/搜图不受影响，可先用 eye_recognize',
                        'degraded': True, 'detail': data.get('error', '')}
            detail = str(data.get('error', ''))[:100]
            return {'ok': False,
                    'error': (f'视觉引擎处理超时或失败（{detail}）。可能是这页题目太多/图片太大一次批不完——'
                              '稍等片刻重试一次；反复失败请把作业分开拍（一页少些题）'),
                    'degraded': True, 'timeout_like': True, 'detail': detail}
        # ★ B1 版本配套锁：404 = Eye 缺接口（新版哈文调旧版 Eye），给升级引导不报干巴巴 HTTP 码
        if r.status_code == 404:
            return {'ok': False,
                    'error': '视觉引擎版本过旧，缺少该接口。请重启哈文（托管会自动拉起配套的新版引擎）；'
                             '若仍报错，请更新后重启',
                    'old_eye': True}
        return {'ok': False, 'error': data.get('error', f'视觉引擎返回 HTTP {r.status_code}')}
    return {'ok': True, 'data': data}


def _get(path: str, timeout: float = _TIMEOUT_LIGHT) -> dict:
    try:
        r = _session.get(_base_url() + path, timeout=timeout)
        return {'ok': True, 'data': r.json()}
    except requests.exceptions.ConnectionError:
        return {'ok': False, 'error': _DOWN_HINT, 'down': True}
    except Exception as e:
        return {'ok': False, 'error': f'视觉引擎调用失败: {e}'}


def resolve_image_path(image_path) -> str:
    """把 AI 给的各类「图指代」规整成本机绝对路径（HarvenEye 同机读图，零传输）。

    支持：
      1. 本机绝对/相对路径（原样归一化）
      2. 哈文图库 URL：/api/images/file/<id> → 查 img_store 拿库仓绝对路径
      3. 纯数字图库 id
    返回 '' 表示解析失败（错误信息由调用方组装）。"""
    s = str(image_path or '').strip().strip('"\'')
    if not s:
        return ''
    # 纯数字 → 图库 id
    if s.isdigit():
        try:
            from src.core import img_store
            p = img_store.get_file(int(s))
            return str(p) if p else ''
        except Exception:
            return ''
    # 哈文图库 URL
    if '/api/images/file/' in s:
        try:
            tail = s.split('/api/images/file/')[-1].split('?')[0].strip('/')
            if tail.isdigit():
                from src.core import img_store
                p = img_store.get_file(int(tail))
                return str(p) if p else ''
        except Exception:
            return ''
    # 普通路径
    p = Path(s)
    return str(p.resolve()) if p.exists() else s


def _pointer_to_url(pointer) -> str:
    """Eye 库的 pointer（哈文库仓绝对路径）反查哈文图库 id → 展示 url。
    查不到（外部原图未入图库）返回 ''。"""
    if not pointer:
        return ''
    try:
        norm = str(pointer).replace('\\', '/').lower()
        marker = 'data/media/'
        i = norm.find(marker)
        if i < 0:
            return ''
        from src.core import img_store
        c = img_store._conn()
        row = c.execute("SELECT id FROM images_index WHERE lower(replace(path, char(92), '/'))=?",
                        (norm[i:],)).fetchone()
        c.close()
        return f'/api/images/file/{row["id"]}' if row else ''
    except Exception:
        return ''


# ═══════════════════════════════════════════════════════════════
# ★ #1234 M4-2 相册互认：哈文拍照链入库 → 自动同步 HarvenEye 视觉索引
#   fire-and-forget：后台线程一发即忘，Eye 离线静默跳过，绝不拖慢/搞炸哈文入库主链
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# ★ #1274 节点③（v1.3 融合，2026-10-29）理解槽进程内优先
#   规则（铁律 0 同源，vision_link 解析器单一真家）：
#   - 用户钉了视觉模型 / 当前外脑带 vision 能力位 → 走进程内通道
#     （api=外脑 / llama_cpp=进程内 GGUF），失败如实报错，绝不回落 7001 代选
#   - 无钉选、无 vision 外脑 → 保持旧行为走 7001（过渡期旧 Eye 兜底，
#     含 current_model=harveneye 的存量选型）
# ═══════════════════════════════════════════════════════════════

def _inproc_channel() -> dict:
    """解析用户的视觉通道选择；无选择返回 {}（调用方交回 7001 旧链）。"""
    try:
        from src.core import vision_link
        return vision_link.resolve_understand_channel()
    except Exception:
        return {}


def _to_openai_vision(messages) -> list[dict]:
    """Ollama 风格 messages（content str + images:[b64]）→ OpenAI 视觉格式
    （content 列表 + image_url dataURI）。无图消息原样透传。"""
    out = []
    for m in messages or []:
        m = dict(m)
        imgs = m.pop('images', None) or []
        content = m.get('content', '')
        if imgs:
            parts = [{'type': 'text', 'text': content or ''}]
            for b in imgs:
                b = str(b or '')
                if b and not b.startswith('data:'):
                    b = 'data:image/jpeg;base64,' + b
                if b:
                    parts.append({'type': 'image_url', 'image_url': {'url': b}})
            m['content'] = parts
        out.append(m)
    return out


def _attach_path(messages, image_path: str) -> list[dict]:
    """把本机图片路径转 dataURI 附到最后一条 user 消息（进程内读图，零 HTTP）。"""
    import base64 as _b64
    import mimetypes as _mt
    mime = _mt.guess_type(image_path)[0] or 'image/jpeg'
    with open(image_path, 'rb') as f:
        uri = f'data:{mime};base64,' + _b64.b64encode(f.read()).decode()
    msgs = _to_openai_vision(messages)
    part = {'type': 'image_url', 'image_url': {'url': uri}}
    for m in reversed(msgs):
        if m.get('role') == 'user':
            c = m.get('content')
            if isinstance(c, str):
                m['content'] = [{'type': 'text', 'text': c}, part]
            elif isinstance(c, list):
                m['content'] = c + [part]
            return msgs
    msgs.append({'role': 'user', 'content': [{'type': 'text', 'text': ''}, part]})
    return msgs


def _attach_ollama(messages, image_path: str) -> list[dict]:
    """把本机图片转 b64 附到最后一条 user 消息（Ollama 原生 images 字段，进程内读图）。"""
    import base64 as _b64
    with open(image_path, 'rb') as f:
        b64 = _b64.b64encode(f.read()).decode()
    msgs = [dict(m) for m in (messages or [])]
    for m in reversed(msgs):
        if m.get('role') == 'user':
            m['images'] = (m.get('images') or []) + [b64]
            return msgs
    msgs.append({'role': 'user', 'content': '', 'images': [b64]})
    return msgs


def _msgs_for_channel(messages, image_path: str, ch: dict) -> list[dict]:
    """★ 节点⑥ 按通道分流消息格式：api=OpenAI 视觉格式；ollama=原生 images b64；
    llama_cpp 同 ollama 透传（引擎层自行适配，不支持多轮时如实报错）。
    ★ #1438（2026-11-04，定版拍板）：llama_cpp 引擎（Qwen3VLChatHandler）只认
    OpenAI 视觉格式（content 列表 + image_url dataURI），Ollama 风格 images 独立
    字段会被静默丢弃（Media count: 0 → 模型纯文本瞎答「我不能看图」实锤，
    全新安装真图实测抓到）——先 _to_openai_vision 统一转换再走。
    真图验收：蓝天/绿草/红球/橙球/棕柱 5/5 全中（Media count: 1）。"""
    if ch.get('provider') == 'api':
        return _attach_path(messages, image_path) if image_path else _to_openai_vision(messages)
    if ch.get('provider') == 'llama_cpp':
        msgs = _to_openai_vision(messages)
        return _attach_path(msgs, image_path) if image_path else msgs
    return _attach_ollama(messages, image_path) if image_path else [dict(m) for m in (messages or [])]


def _inproc_chat(messages, image_path: str = ''):
    """进程内理解槽对话（非流式）。
    返回 (True, text) / (False, err)；无用户通道选择返回 None（调用方交回 7001）。"""
    ch = _inproc_channel()
    if not ch:
        return None
    try:
        from src.core import vision_link
        msgs = _msgs_for_channel(messages, image_path, ch)
        text = vision_link.chat(msgs, stream=False)
        return True, str(text or '').strip()
    except Exception as e:
        return False, f'视觉模型处理失败（{str(e)[:140]}）。可稍等重试一次；反复失败请到 设置→模型管理 检查视觉模型选择'


def auto_index_enabled() -> bool:
    """config.yaml harveneye.auto_index 开关（默认 true）。"""
    try:
        from src.utils.config import get_config
        return bool(get_config().get('harveneye.auto_index', True))
    except Exception:
        return True


def notify_index(abs_path, img_id=None):
    """把一张哈文图库照片后台送进视觉索引（★ 节点⑥ 进程内，退役 7001）。
    pointer=哈文原图真家（Lightroom catalog 模式：哈文管原图，索引只存视觉特征指针）。
    后台线程 + 失败静默（设计内行为）。"""
    if not auto_index_enabled():
        return
    p = str(abs_path or '')
    if not p or not Path(p).exists():
        return

    def _job():
        try:
            time.sleep(15)   # ★ #1248 让路：等看图回答先跑完（GPU 串行，并发抢显存拖慢回答实锤）
            from src.core import vision_link
            vision_link.get_pipeline().index_image(p, pointer=p)
        except Exception:
            pass  # 引擎未就绪=静默跳过，这是设计内行为不是错误

    threading.Thread(target=_job, name=f'eye-index-{img_id or ""}',
                     daemon=True).start()


def is_eye_alive() -> tuple[bool, str]:
    """探活（★ 节点⑥ 进程内）：视觉子系统组件自检汇报。"""
    try:
        from src.vision import installer
        from src.core import vision_link
        st = installer.engine_status()
        eng = '就绪' if st.get('ready') else '未安装（设置→视觉引擎 可安装）'
        ch = vision_link.resolve_understand_channel()
        brain = (f"{ch.get('provider', '')} {ch.get('api_model', '') or ch.get('model', '') or '本地 GGUF'}").strip()
        return True, f'HarvenEye 融合版（底座{eng}，理解大脑={brain}）'
    except Exception as e:
        return False, str(e)[:120]


# ═══════════════════════════════════════════════════════════════
# ★ #1238 Eye 对话接入（HarvenEye = 混合模型引擎，本地模型阵营候选）
#   门禁：模型未装 → models_ready=False → 哈文本地模型列表不显示（开发者铁律）
# ═══════════════════════════════════════════════════════════════

def models_ready(timeout: float = _TIMEOUT_LIGHT) -> dict:
    """门禁探测（★ 节点⑥ 进程内，退役 7001）。
    返回 {'ready': bool, 'model': str, 'missing': [...]} 或 {'ready': False, 'error': ...}。"""
    ch = _inproc_channel()
    if ch:
        if ch.get('provider') == 'api':
            return {'ready': True, 'model': ch.get('api_model', ''), 'missing': []}
        if ch.get('provider') == 'ollama':
            return {'ready': True, 'model': ch.get('model', ''), 'missing': []}
        if ch.get('provider') == 'llama_cpp':
            from pathlib import Path as _P
            return {'ready': True,
                    'model': _P(ch['gguf']).stem if ch.get('gguf') else '本地视觉模型',
                    'missing': []}   # resolve 已验文件在位；加载失败在调用时如实报
    return {'ready': False, 'error': _DOWN_HINT_NEW, 'missing': ['视觉模型']}


def chat(messages, stream=False, timeout: float = 120.0, image_path: str = ''):
    """通用对话（非流式）：messages 透传 Eye /chat → understand 槽 VLM。
    image_path 非空时带图对话（★ 节点⑥ 进程内，退役 7001；#1241 手机看图链）。
    Returns (ok, content或错误说明)。"""
    _r = _inproc_chat(messages, image_path)
    if _r is not None:
        return _r
    return False, _DOWN_HINT_NEW


def _inproc_chat_stream(messages, channel=None):
    """进程内流式生成器（★ 节点⑥：消息格式按通道分流）。无通道返回 None。
    ★ #1418：channel 显式传入时直接用（engine 出厂大脑分支用，跳过内部解析）。"""
    ch = channel if channel is not None else _inproc_channel()
    if not ch:
        return None
    msgs = _msgs_for_channel(messages, '', ch)
    if ch.get('provider') == 'api':
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        for chunk in mm.generate_response(msgs, stream=True,
                                          model=ch.get('api_model') or None):
            if isinstance(chunk, dict):
                if chunk.get('type') == 'error':
                    raise RuntimeError(str(chunk.get('content', '')))
                if chunk.get('type') == 'text':
                    piece = chunk.get('content', '') or ''
                    if piece:
                        yield piece
            elif chunk:
                yield str(chunk)
    else:
        # ★ #1323（2026-10-30）：ollama 通道真流式（NDJSON 逐行透传，首响体感秒级）；
        # ★ #1446（2026-11-05 r50）：llama_cpp 通道也真流式（引擎层逐 token 生成器）。
        #   此前 llama_cpp 一次性吐全文 → 切 harveneye 模型打字机消失实锤，全通道恢复。
        from src.core import vision_link
        if ch.get('provider') == 'ollama':
            resp = vision_link.chat(msgs, stream=True)
            # ★ #1459（2026-11-05）：通道判定与实际引擎可能错位（vision_link 解析器说了算）。
            #   #1446 后 llama_cpp 引擎 stream=True 返回逐 token 生成器——生成器不支持
            #   with 上下文管理器协议（'generator' object does not support... 实锤），
            #   且产出是 str 片段不是 NDJSON bytes → 先按类型分流再解析。
            import inspect as _insp
            if _insp.isgenerator(resp):
                try:
                    for piece in resp:
                        if piece:
                            yield piece
                except Exception as _ge:
                    raise RuntimeError(
                        f'视觉模型处理失败（{str(_ge)[:140]}）。可稍等重试一次；'
                        '反复失败请到 设置→模型管理 检查视觉模型选择') from _ge
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
                return
            with resp:
                for raw in resp:
                    line = (raw or b'').decode('utf-8', 'replace').strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    piece = ((obj.get('message') or {}).get('content') or '')
                    if piece:
                        yield piece
                    if obj.get('done'):
                        break
            return
        try:
            for piece in vision_link.chat(msgs, stream=True):
                if piece:
                    yield piece
        except Exception as e:
            raise RuntimeError(
                f'视觉模型处理失败（{str(e)[:140]}）。可稍等重试一次；'
                '反复失败请到 设置→模型管理 检查视觉模型选择') from e


def chat_stream(messages, timeout: float = 150.0, channel=None):
    """通用对话（流式生成器）★ 节点⑥ 进程内（退役 7001 SSE）：
    api 走 mm 流式；ollama 走 NDJSON 流式；llama_cpp 走引擎逐 token 流式（#1446）。
    逐块 yield 文本；失败 raise RuntimeError。
    ★ #1418：channel 显式传入时直接用（出厂大脑分支，保证标注与实际通道一致）。"""
    _gen = _inproc_chat_stream(messages, channel=channel)
    if _gen is not None:
        yield from _gen
        return
    raise RuntimeError(_DOWN_HINT_NEW)


# ═══════════════════════════════════════════════════════════════
# ★ #1267（2026-10-29）统一 Eye 视觉通道——五处本地看图分支共用
# ═══════════════════════════════════════════════════════════════

def is_selected() -> bool:
    """双源判定「用户当前选的主模型 = HarvenEye」（yaml OR mm 任一命中）。
    背景：yaml 镜像与 mm 内存可能瞬时错位（#1265 换血竞态的同族病），
    单源判定两头撞墙（14:02 批改误报实锤）。五处本地看图分支统一用这个入口，
    命中即走 Eye 视觉通道（铁律 0：选谁给谁——Eye 底座 qwen3-vl 就是通用视觉模型）。"""
    try:
        from src.utils.config import get_config as _gc
        if ((_gc() or {}).get('current_model', '') or '').strip() == 'harveneye':
            return True
    except Exception:
        pass
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        return (get_multi_model_manager().current_model or '').strip() == 'harveneye'
    except Exception:
        return False


def vision_chat(prompt, images_b64, timeout: float = 150.0, system: str = ''):
    """看图统一入口（非流式，支持多图）：走 Eye /chat → understand 槽（qwen3-vl 通用视觉模型）。
    images_b64：纯 base64 或带 data: 前缀均可。返回 (ok, content或错误说明)。
    ★ 内置 A1 重试语义：还没就绪/超时 → 等 15s 重试一次（冷加载窗口/前一轮长任务）。"""
    imgs = []
    for b in images_b64 or []:
        if b and b.startswith('data:'):
            b = b.split(',', 1)[1]
        if b:
            imgs.append(b)
    msgs = []
    if system:
        msgs.append({'role': 'system', 'content': system})
    msgs.append({'role': 'user', 'content': prompt, 'images': imgs})
    ok, ans = chat(msgs, timeout=timeout)
    if not ok and ('还没就绪' in ans or '未就绪' in ans or '超时' in ans):
        time.sleep(15)
        ok, ans = chat(msgs, timeout=timeout)
    return ok, ans


def eye_models() -> list[str]:
    """Eye understand 槽模型名（显示用，如 qwen3-vl:8b-instruct）；Eye 离线返回 []。"""
    r = models_ready()
    return [r['model']] if r.get('ready') and r.get('model') else []


# ═══════════════════════════════════════════════════════════════
# ★ #1257（2026-10-28）CLIP 零样本分类 —— 相册归位第二级（批4）
# 相册名称库（候选词组）数据真家在哈文（img_albums 表+别名组），
# Eye 只做向量比对，不维护词库——两库分工红线。
# ═══════════════════════════════════════════════════════════════

def classify(image_path, candidates, threshold=0.22, timeout: float = 15.0):
    """CLIP 零样本分类（★ 节点⑥ 进程内，退役 7001）：candidates=[{name, aliases:[...]}]。
    返回格式与旧 Eye /classify 逐字段一致：{ok, hit, top:{name,similarity,via}, ranking, threshold}。
    引擎未就绪/参数错 → ok=False + 友好 error（同 _post 纪律）。"""
    try:
        import numpy as np
        from src.core import vision_link
        rp = resolve_image_path(image_path)
        if not rp or not Path(rp).exists():
            return {'ok': False, 'error': f'图片找不到: {image_path}'}
        if not candidates:
            return {'ok': False, 'error': 'candidates 为空'}
        p = vision_link.get_pipeline()
        vec = np.array(p.index.infer(rp).data['vector'], dtype=np.float32)
        scored = []
        for cand in candidates:
            name = str(cand.get('name') or '').strip()
            if not name:
                continue
            best, best_text = -1.0, name
            for text in [name] + [str(a).strip() for a in (cand.get('aliases') or []) if str(a).strip()]:
                try:
                    sim = float(np.dot(vec, p.index.text_to_vec(text)))
                except Exception:
                    continue
                if sim > best:
                    best, best_text = sim, text
            scored.append({'name': name, 'similarity': round(best, 4), 'via': best_text})
        scored.sort(key=lambda x: -x['similarity'])
        top = scored[0] if scored else None
        return {'ok': True, 'hit': bool(top and top['similarity'] >= threshold),
                'top': top, 'ranking': scored[:6], 'threshold': threshold}
    except Exception as e:
        return {'ok': False, 'error': f'CLIP 分类失败: {str(e)[:120]}'}


# ═══════════════════════════════════════════════════════════════
# FC 工具函数（在 builtin_tools.TOOL_REGISTRY 注册，#1233）
# 载荷消费约定（规则文档写死）：labels→相册归位 / caption→随身记知识库 /
# objects→面板结构化 / confidence→对话策略（低置信先问用户） / engine+version→审计
# ═══════════════════════════════════════════════════════════════

def recognize(image_path, preset='fast', do_index=False, force_vlm=False,
              skip_vlm=True, timeout: float = 60.0):
    """★ #1244：轻量 recognize（起名/补标用，返回原始 dict 不带 AI 导读）。
    默认 skip_vlm=True 禁用 L3 兜底——标签任务秒回（<0.5s），素材不足如实返回。
    疑难图深度识别请传 skip_vlm=False。★ 节点⑥ 进程内（退役 7001），返回格式不变。"""
    rp = resolve_image_path(image_path)
    if not rp or not Path(rp).exists():
        return {'ok': False, 'error': f'图片找不到: {image_path}'}
    try:
        from src.core import vision_link
        _p = vision_link.get_pipeline()
        if preset and preset != _p.preset:
            _p.switch_preset(preset)   # ★ 档位切换（fast/quality），与旧 Eye /recognize 语义一致
        d = vision_link.recognize(rp, do_index=bool(do_index),
                                  force_vlm=bool(force_vlm), skip_vlm=bool(skip_vlm))
        return {'ok': True, 'data': d}
    except Exception as e:
        return {'ok': False, 'error': f'识别失败: {str(e)[:140]}'}


def eye_recognize_tool(image_path='', preset='', do_index=False, force_vlm=False):
    """★ #1233 M4-1 HarvenEye 识别工具：一张图跑本地视觉分级识别（L0 秒回 → L1 检测 →
    L2 语义 → L3 VLM 兜底，逐级溯源可解释）。身份：HarvenEye（本地视觉引擎，零费用零上传）。"""
    rp = resolve_image_path(image_path)
    if not rp or not Path(rp).exists():
        return {'success': False,
                'error': f'图片找不到: {image_path}。传本机图片的绝对路径、图库图片 id 或 search_images 返回的 url'}
    r = recognize(rp, preset=preset or 'fast', do_index=bool(do_index),
                  force_vlm=bool(force_vlm), skip_vlm=False)
    if not r.get('ok'):
        return {'success': False, 'error': r.get('error', '识别失败')}
    d = r['data']
    # 给 AI 的导读：教它怎么消费分级结果（不贴原始 trace 全文，省 token）
    note = ('HarvenEye 分级识别完成。final_layer=最终生效层：L0=重复图秒回（matched_id 可去图库核对）、'
            'L1=物体检测（objects 是检测到的目标+置信）、L2=语义相似、L3=视觉大模型描述。'
            'labels 可用于归类，caption 是图片描述，confidence 低于 0.35 时先向用户确认再下结论')
    return {'success': True, 'final_layer': d.get('final_layer'),
            'labels': d.get('labels'), 'caption': d.get('caption'),
            'objects': d.get('objects'), 'confidence': d.get('confidence'),
            'total_ms': d.get('total_ms'), 'item': d.get('item'),
            'engine': 'HarvenEye', 'note': note}


def eye_search_tool(query='', top_k=20):
    """★ #1233 M4-1 HarvenEye 以文搜图：关键词+语义双路，搜的是 HarvenEye 视觉索引库
    （哈文拍照链入库的图都会同步进来）。身份：HarvenEye。"""
    q = str(query or '').strip()
    if not q:
        return {'success': False, 'error': 'query 不能为空，说清楚要找什么（如「冰箱」「数学作业」）'}
    try:
        from src.core import vision_link
        d = vision_link.get_pipeline().search_text(q, top_k=max(1, min(int(top_k or 20), 50)))
    except Exception as e:
        return {'success': False, 'error': f'搜索失败: {str(e)[:120]}'}
    kw, se = d.get('keyword', []) or [], d.get('semantic', []) or []
    # 指针转哈文图库 url（HarvenEye 库里存的是指针，指向哈文图库真家）
    def _with_url(it):
        out = dict(it)
        url = ''
        if it.get('pointer_id'):
            url = f'/api/images/file/{it["pointer_id"]}'
        if not url:
            url = _pointer_to_url(it.get('pointer'))
        if url:
            out['url'] = url
        out.pop('pointer', None)   # 不给 AI 本机裸路径
        out.pop('pointer_id', None)
        return out
    note = (f'HarvenEye 搜到 关键词命中 {len(kw)} 张 + 语义命中 {len(se)} 张（去重后展示）。'
            '有 url 的用 Markdown 图片语法 ![](url) 展示；只有 pointer 的说明原图不在哈文图库，报路径即可')
    return {'success': True, 'keyword': [_with_url(x) for x in kw],
            'semantic': [_with_url(x) for x in se],
            'count': len(kw) + len(se), 'engine': 'HarvenEye', 'note': note}


def eye_ask_tool(image_path='', question=''):
    """★ HarvenEye 问图（节点⑥ 进程内，退役 7001）：理解槽直问一张图。
    返回的是模型原文，由哈文组织成人话再讲给用户。"""
    rp = resolve_image_path(image_path)
    if not rp or not Path(rp).exists():
        return {'success': False,
                'error': f'图片找不到: {image_path}。传本机图片的绝对路径、图库图片 id 或 search_images 返回的 url'}
    try:
        from src.core import vision_link
        # ★ #1298（2026-10-29）显式传 120s 超时：旧版不传吃了全局 VLM_TIMEOUT_S=20（打标场景的阈值），
        #   读卷子/说明书等重活（视觉 token 预填充+逐题长答）远超 20s 必炸——
        #   实锤：22:13 已批准卷子复查两次 eye_ask 各卡 20s success=False
        text = vision_link.understand_image(
            rp, str(question or '') or '请用中文详细描述这张图片里的内容。', timeout_s=120.0)
        ch = _inproc_channel() or {}
        return {'success': True, 'text': str(text).strip(),
                'model': ch.get('api_model') or ch.get('model') or '本地视觉模型',
                'engine': 'HarvenEye',
                'note': 'text 是视觉模型的原文，用自己的语气讲给用户；'
                        '涉及批改/分析建议时结合用户情况给出整改建议，不要原样贴 JSON'}
    except Exception as e:
        return {'success': False,
                'error': f'视觉模型处理失败（{str(e)[:140]}）。可稍等重试一次'}
