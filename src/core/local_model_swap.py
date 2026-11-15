# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""选择即换血（★ #1322，2026-10-30 定版拍板「开发」三件套之一）

开发者原话：「用户选择了本地的哪个模型，模型就应该快速切换过去，不要等着用的时候再换」。

机制：用户切本地模型那一刻（set_model / mm.set_current_model / 哈文启动热身），
后台线程立即：
  1. 解析目标 Ollama 模型名（运行时解析：ollama 阵营=config/界面选择，harveneye=Eye /status
     的理解槽模型名——**纯通用机制，不写死任何模型名**，用户换什么模型都适用）
  2. 先卸旧：Ollama /api/ps 列出已加载模型，>2GB 且非目标的全部 keep_alive=0 卸载
     （12GB 显存装不下两个大模型，先卸后载一次到位；嵌入类小模型不动）
  3. 空请求预载目标（num_predict=1 只载不生成，keep_alive 30m）
  4. 完成写管家议程「✅ 模型已就绪」反馈用户

失败绝不影响使用：任何异常静默降级为旧行为（首次提问时 Ollama 自动加载）。
"""
import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)

_ollama_base_default = 'http://localhost:11434'
_last_swap = ''            # 去重：同一目标不重复换血（也防 set_model/set_current_model 双入口重复触发）
_swap_lock = threading.Lock()


def _ollama_base() -> str:
    try:
        from src.utils.config import get_config
        _mm = ((get_config() or {}).get('model_manager', {}) or {})
        return (_mm.get('base_url') or _ollama_base_default)
    except Exception:
        return _ollama_base_default


def _resolve_target(provider_or_model):
    """本地选择 → (实际 Ollama 模型名, 人读显示名)；解析不出返回 ('','')（诚实跳过，不兜底代选）。"""
    name = (provider_or_model or '').strip()
    label = name
    if not name:
        return '', ''
    if name == 'harveneye':
        # ★ #1323：融合版理解槽在哈文进程内——直接解析进程内理解通道拿真实
        #   Ollama 模型名（钉选 GGUF 注册版 hv-pin-* / 出厂默认），不再问 7001
        #   独立 Eye（历史遗留进程，模型清单与融合版不一致，问它会预载错模型）。
        try:
            from src.core import vision_link as _vl
            ch = _vl.resolve_understand_channel() or {}
            if ch.get('provider') != 'ollama':
                return '', ''                   # 钉的是外脑/解析不出 → 无需预载
            name = (ch.get('model') or '').strip()
            # ★ #1324 就绪提醒给用户看人读名（如 qwen3-vl-8b-q6k），不吐 hv-pin-<哈希>
            label = (ch.get('label') or name).strip()
        except Exception as e:
            logger.debug(f'[ModelSwap] 进程内理解槽解析失败（跳过预载）: {e}')
            return '', ''
    return name, label


def _same_model(a: str, b: str) -> bool:
    """Ollama 模型名归一化比较：无 tag 的名字等价于 :latest（#1325）。
    hv-pin-xxx vs hv-pin-xxx:latest → True；qwen3-vl:8b-instruct 带真 tag 精确比。"""
    a = (a or '').strip(); b = (b or '').strip()
    if a == b:
        return True
    a2 = a[:-len(':latest')] if a.endswith(':latest') else a
    b2 = b[:-len(':latest')] if b.endswith(':latest') else b
    return a2 == b2


def _post_generate(base: str, model: str, keep_alive, num_predict: int = 1, timeout: int = 180):
    payload = json.dumps({'model': model, 'prompt': ' ', 'keep_alive': keep_alive,
                          'options': {'num_predict': num_predict,
                                      # ★ #1327：预载即 16K 上下文（与对话链 #1270 同源配置）——
                                      # 否则热身以默认 4096 载入 runner，首问触发重载白等一次
                                      'num_ctx': 16384}}).encode('utf-8')
    req = urllib.request.Request(f'{base}/api/generate', data=payload,
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def swap_in_async(provider_or_model, reason: str = 'user_select'):
    """入口：后台线程执行换血，立即返回不阻塞切换动作。"""
    threading.Thread(target=_swap_in, args=(provider_or_model, reason),
                     name='model-swap', daemon=True).start()


def _swap_in(target_raw, reason: str):
    global _last_swap
    name, label = _resolve_target(target_raw)
    if not name:
        logger.debug(f'[ModelSwap] 目标解析不出（{target_raw!r}），跳过换血')
        return
    with _swap_lock:
        if name == _last_swap:
            return                      # 已是当前热模型，不重复折腾
        _last_swap = name
    base = _ollama_base()
    try:
        # 1) 卸旧腾显存：已加载、>2GB、非目标 → keep_alive=0（嵌入类小模型不动）
        try:
            with urllib.request.urlopen(f'{base}/api/ps', timeout=5) as r:
                loaded = ((json.loads(r.read().decode('utf-8')) or {}).get('models')) or []
            for m in loaded:
                mname = (m.get('name') or '').strip()
                if mname and not _same_model(mname, name) and (m.get('size') or 0) > 2 * 1024 ** 3:
                    _post_generate(base, mname, keep_alive=0, num_predict=0, timeout=30)
                    logger.info(f'[ModelSwap] 已卸载闲置大模型 {mname}（腾显存给 {name}）')
        except Exception as e:
            logger.debug(f'[ModelSwap] 卸旧失败（继续尝试预载）: {e}')
        # 2) 预载目标（空请求只载不生成，keep_alive 30m 与对话链一致）
        t0 = time.time()
        _post_generate(base, name, keep_alive='30m')
        cost = time.time() - t0
        logger.info(f'[ModelSwap] ✅ {name} 已预载就绪（{cost:.1f}s，keep_alive 30m，来源={reason}）')
        # 3) 就绪反馈进管家议程（dedup 每模型终身一条，不刷屏）
        try:
            from src.core.butler_agenda import _add_item
            _add_item(f'model_swap:{name}', 'model_swap', 'info', 'low',
                      f'✅ 模型已就绪：{label}（预载完成，提问秒回）',
                      detail=f'你选择的本地模型已自动预载进显存（耗时 {cost:.0f} 秒）。'
                             f'之后提问不用等加载，直接秒答。')
        except Exception as e:
            logger.debug(f'[ModelSwap] 就绪议程写入失败（不影响）: {e}')
    except Exception as e:
        with _swap_lock:
            if _last_swap == name:
                _last_swap = ''         # 失败回退，下次切换可重试
        logger.debug(f'[ModelSwap] 换血失败（静默降级，首次提问会自动加载）: {e}')
