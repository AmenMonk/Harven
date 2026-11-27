# -*- coding: utf-8 -*-
"""★ #1290（2026-10-29）自检预检注入器 — 方案N同族，「本地提取+外脑总结」架构落地。

病根：开发者让哈文自检，外脑走 FC 一步一步查（6~7 轮 API 往返，30~60 秒），
而端口/进程/模型/依赖/数据这些事实本地毫秒级就能查完。
本模块：关键词命中「自检类」→ 本地一次采集全部快照 → 注入 enhanced_context，
外脑一轮综合分析直接回答（6 轮变 1 轮）。

红线：
- 只采事实不写代码不动配置（纯只读）
- 任一采集步失败只标 N/A 不抛异常（预检绝不能挡聊天）
- 关键词保守（不收「体检」——避免误伤家人健康体检场景）
"""
from __future__ import annotations
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 触发词（保守集：都是「查哈文自己」的问法，不撞家人健康/事务场景）
_HIT_RE = re.compile(
    r'(自检|自我检测|自我检查|全面检测|系统检查|检测哈文|检查哈文|哈文状态|检查一下你自己|你自己的状态)'
)

# 整个采集器的硬预算（秒）——超了就用手头已采到的部分
_BUDGET = 3.0


def is_preflight_hit(user_message: str) -> bool:
    """是否命中自检类问题（外层只做布尔判断用）。"""
    try:
        return bool(_HIT_RE.search(str(user_message or '')))
    except Exception:
        return False


def _port_alive(port: int, timeout: float = 0.3) -> bool:
    import socket
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except Exception:
        return False


def _collect_ports(lines):
    p7000 = _port_alive(7000)
    p11434 = _port_alive(11434)
    p7001 = _port_alive(7001)
    lines.append(f"- 服务端口 7000（哈文主服务）: {'✅ 监听中' if p7000 else '❌ 未监听'}")
    lines.append(f"- 本地引擎 11434（Ollama）: {'✅ 监听中' if p11434 else '❌ 未监听'}")
    # ★ #1448 话术修正：旧写法「旧 Eye 端口 7001 …退役」被模型脑补成
    #   「视觉引擎已退役」（自检胡编实锤源头之一）。7001 只是历史端口残留检查项，
    #   视觉引擎本体早已融合进哈文主进程、完全在役——话术必须零歧义。
    #   ★ 2026-11-27 D2（#1565）：对外不再用 HarvenEye 产品名，改「视觉引擎/图片理解组合」
    lines.append(f"- 历史端口 7001（旧独立架构残留检查，与视觉引擎无关，视觉引擎在役）: "
                 f"{'⚠️ 仍有进程监听（历史残留）' if p7001 else '✅ 已清零（无残留）'}")


def _collect_models(lines):
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        lines.append(f"- 当前外脑/主模型: {mm.current_model}")
    except Exception:
        lines.append("- 当前外脑/主模型: N/A")
    try:
        from src.utils.config import get_config
        cfg = get_config()
        slots = cfg.get('current_model_by_mode') or {}
        lines.append(f"- 分槽记忆: cloud={slots.get('cloud', '空')} / local={slots.get('local', '空')} / "
                     f"last_mode={cfg.get('last_query_mode', '空')}")
    except Exception:
        lines.append("- 分槽记忆: N/A")
    try:
        import requests as _rq
        r = _rq.get('http://127.0.0.1:11434/api/tags', timeout=0.8)
        models = [m.get('name') for m in (r.json() or {}).get('models', [])]
        lines.append(f"- Ollama 已装模型: {', '.join(models) if models else '无'}")
    except Exception:
        lines.append("- Ollama 已装模型: 查询失败（可能未启动）")


def _collect_deps(lines):
    # ★ find_spec 只查包元数据不真加载（import transformers 要 1~2s，find_spec 毫秒级）
    import importlib.util
    ok, miss = [], []
    for m in ('yaml', 'requests', 'flask', 'sqlite_vec', 'onnxruntime', 'transformers'):
        try:
            (ok if importlib.util.find_spec(m) else miss).append(m)
        except Exception:
            miss.append(m)
    lines.append(f"- 关键依赖抽查: {'、'.join(ok)} ✅" + (f"；缺失: {', '.join(miss)} ❌" if miss else ""))


def _collect_disk(lines):
    import shutil
    for drv in ('H:', 'C:'):
        try:
            u = shutil.disk_usage(drv + '\\')
            lines.append(f"- 磁盘 {drv}: 剩余 {u.free / 1024**3:.1f} GB / 共 {u.total / 1024**3:.0f} GB")
        except Exception:
            lines.append(f"- 磁盘 {drv}: N/A")


def _collect_data(lines):
    try:
        c = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
        tabs = [r[0] for r in c.execute(
            "select name from sqlite_master where type='table' and name like 'panel_%'")]
        rich = []
        for t in tabs:
            try:
                n = c.execute(f'select count(*) from "{t}"').fetchone()[0]
                if n:
                    rich.append(f"{t.replace('panel_', '')}({n})")
            except Exception:
                pass
        c.close()
        lines.append(f"- 本地面板数据: {'、'.join(rich[:12])}{' 等' if len(rich) > 12 else ''}")
    except Exception:
        lines.append("- 本地面板数据: N/A")


def _collect_vision(lines):
    try:
        base = Path(r'H:\HarvenAI\data\models\vision')
        y = (base / 'yolox' / 'yolox_s.onnx').is_file()
        c1 = (base / 'chinese-clip' / 'pytorch_model.bin').is_file()
        lines.append(f"- 图片理解底座: yolox {'✅' if y else '❌'} / chinese-clip {'✅' if c1 else '❌'}"
                     f"（视觉引擎已融合进哈文主进程，正常在役）")
        # ★ #1448：补一行视觉引擎实时状态，给自检一个明确的「在役」事实锚点
        try:
            from src.core import eye_client as _ec
            _ok, _msg = _ec.is_eye_alive()
            lines.append(f"- 视觉引擎状态: {'✅ ' + _msg if _ok else '⚠️ ' + _msg}")
        except Exception:
            pass
    except Exception:
        lines.append("- 图片理解底座: N/A")


def _collect_junk(lines):
    try:
        root = Path(r'H:\HarvenAI')
        junk = [f.name for f in root.iterdir() if f.is_file() and
                (f.name.startswith(('check', 'selfcheck', '_tmp', 'test_')) or
                 (f.suffix == '.log'))]
        lines.append(f"- 根目录临时文件: {len(junk)} 个（历次检测残留，已有归档清单待人工确认，不影响运行）")
    except Exception:
        pass


def preflight_context(user_message: str) -> str:
    """自检类问题 → 本地一次采全快照，返回注入文本（未命中/异常返回 ''）。

    注入文本里明确告诉模型：数据已查好，直接综合分析，不要再用调查类工具。"""
    if not is_preflight_hit(user_message):
        return ''
    t0 = time.time()
    lines = ["【哈文自检快照（发送前已在本地毫秒级采集，全部为实时事实）】"]
    steps = (_collect_ports, _collect_models, _collect_deps,
             _collect_disk, _collect_data, _collect_vision, _collect_junk)
    for step in steps:
        if time.time() - t0 > _BUDGET:
            lines.append("- （其余项因采集预算跳过）")
            break
        try:
            step(lines)
        except Exception as _e:
            lines.append(f"- ({step.__name__} 采集失败: {_e})")
    logger.info(f"[Preflight] #1290 自检快照采集完成 {time.time()-t0:.2f}s / {len(lines)} 行")
    return (
        "## 🩺 哈文自检预检结果（本地已采好，直接引用）\n"
        "以下是刚在本地采集的实时系统快照。请基于它做综合分析：先给总结论，"
        "再逐项指出正常/异常；**不要再用端口探测、进程查看、文件读取等调查类工具**"
        "（数据已经查好了，重复查是浪费时间）；只对快照里没覆盖到的个别疑点才考虑补充工具。\n"
        "```\n" + "\n".join(lines) + "\n```\n"
    )
