# -*- coding: utf-8 -*-
"""模型组合工坊 · 骨架核心包（2026-11-24 骨架开工，设计稿 v1.15 施工）。

模块地图：
  recipe       配方单 schema（YAML；A1 双模式环节/B4 破坏力等级/A3 聚段显存）
  registry     能力登记表（组合即模型；B7 命名查重；能力交集诚实登记）
  channels     通道适配层（B2：internal/llama_cpp/ollama/cloud 四插头）
  residency    驻留池（模型单例共享/keep_alive/LRU/A2 铁规显存体检）
  logistics    物流单（B8：chains_log 异步批量记账）
  gates        三道门（结构/能力/试跑，§8）
  orchestrator 编排器（一次激活/preflight 三查/B1 身份标注/失败回退）

使用：
  from core.chain_engine import ensure_chain_engine
  ensure_chain_engine()   # 建表 + 注册内置组合
"""
from .recipe import Recipe, Step, load, load_all, save, delete  # noqa: F401
from .registry import get_registry, CHAINS_DIR                  # noqa: F401
from .residency import get_pool, VramShortage                   # noqa: F401
from .logistics import get_logistics, ensure_table              # noqa: F401
from .gates import run_all_gates                                # noqa: F401
from .orchestrator import get_orchestrator                      # noqa: F401

__version__ = "0.1.0"


def ensure_chain_engine() -> dict:
    """开机就绪：建表 + 装载配方单 + 注册内置组合（幂等）。"""
    from .builtin_chains import register_builtin_chains
    lg = get_logistics()
    reg = get_registry()
    n_builtin = register_builtin_chains(reg)
    return {"logistics_table": "chains_log", "chains_loaded": len(reg.list_chains()),
            "builtin_registered": n_builtin}


# 工坊 API 蓝图（/api/chains；放包尾避免循环导入）
from .routes import bp  # noqa: E402,F401
