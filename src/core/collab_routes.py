# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
协作模式 API 路由
- 获取/更新协作配置
- 协作外脑列表
- 隐私级别选项
"""
import os
import logging
import yaml
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

BP = Blueprint('collab', __name__, url_prefix='/api/collab')


@BP.route('/status', methods=['GET'])
def get_collab_status():
    """获取当前协作模式状态（直接读 config.yaml，保证与 engine.py 一致）"""
    try:
        from pathlib import Path
        from src.core.collab_manager import get_collab_manager
        cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
        cfg = {}
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        cm = get_collab_manager(cfg)
        # ★ 2026-09-16 local_dual 迁移落盘：旧配置一次写回 cloud_local，重启后不再重复迁移
        if cfg.get('collab_mode') == 'local_dual':
            try:
                _save_to_config({'mode': 'cloud_local'})
                logger.info("[Collab] local_dual 旧配置已迁移落盘为 cloud_local")
            except Exception as me:
                logger.warning(f"[Collab] local_dual 迁移落盘失败（不影响本次运行）: {me}")
        return jsonify({"success": True, "data": cm.to_dict()})
    except Exception as e:
        logger.error(f"[Collab] 获取状态失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@BP.route('/config', methods=['GET', 'POST'])
def collab_config():
    """获取或更新协作模式配置"""
    if request.method == 'GET':
        # ★ 2026-07-25: 补充GET方法，返回当前配置
        try:
            import yaml
            import os
            cfg_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config.yaml')
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
            collab_cfg = cfg.get('collaboration', {})
            return jsonify({
                "success": True,
                "config": {
                    "mode": collab_cfg.get('mode', 'off'),
                    "model": collab_cfg.get('model', 'deepseek'),
                    "privacy_level": collab_cfg.get('privacy_level', 'balanced'),
                }
            })
        except Exception as e:
            return jsonify({"success": True, "config": {"mode": "off", "model": "deepseek"}})

    # POST: 更新配置
    try:
        from src.core.collab_manager import COLLAB_MODES

        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"success": False, "error": "请求体不能为空"}), 400

        # 验证 mode
        if 'mode' in data:
            mode_val = data['mode']
            if mode_val not in COLLAB_MODES:
                return jsonify({
                    "success": False,
                    "error": f"无效的协作模式: {mode_val}，有效选项: {list(COLLAB_MODES.keys())}"
                }), 400

        # 验证 enabled（兼容旧调用）
        if 'enabled' in data:
            enabled = data['enabled']
            if enabled is not None and not isinstance(enabled, bool):
                return jsonify({"success": False, "error": "enabled 必须是布尔值"}), 400

        # 验证 privacy_level
        if 'privacy_level' in data:
            from src.core.collab_manager import PRIVACY_LEVELS
            if data['privacy_level'] not in PRIVACY_LEVELS:
                return jsonify({
                    "success": False,
                    "error": f"无效的隐私级别: {data['privacy_level']}，有效选项: {list(PRIVACY_LEVELS.keys())}"
                }), 400

        # 验证 foreign_model（如果提供）
        if 'foreign_model' in data and data['foreign_model']:
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            if mm:
                all_models = mm.get_all_models()
                valid_ids = {m['id'] for m in all_models}
                valid_ids.discard('ollama')
                if data['foreign_model'] not in valid_ids:
                    return jsonify({
                        "success": False,
                        "error": f"不支持的外脑模型: {data['foreign_model']}，可用: {sorted(valid_ids)}"
                    }), 400

        from src.core.collab_manager import get_collab_manager
        cm = get_collab_manager()
        cm.update(
            mode=data.get('mode'),
            enabled=data.get('enabled'),  # 兼容旧调用
            foreign_model=data.get('foreign_model'),
            privacy_level=data.get('privacy_level'),
        )
        
        # ★ 深度搜索开关
        if 'deep_search' in data:
            cm.deep_search = bool(data['deep_search'])

        # 持久化到 config.yaml
        save_ok = _save_to_config(data)
        if not save_ok:
            logger.warning("[Collab] 配置已更新到运行时，但持久化到文件失败")

        logger.info(f"[Collab] 配置已更新: {data}")
        return jsonify({"success": True, "data": cm.to_dict()})
    except Exception as e:
        logger.error(f"[Collab] 更新配置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@BP.route('/foreign-models', methods=['GET'])
def get_foreign_models():
    """获取可用的外脑模型列表（用于协作外脑选择器）"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        if mm is None:
            return jsonify({"success": False, "error": "多模型管理器未初始化"}), 500

        all_models = mm.get_all_models()
        # 过滤：排除 ollama，只保留有 API key 的外部模型
        foreign = []
        for m in all_models:
            if m.get('id') == 'ollama':
                continue
            # 检查是否有 API key
            cfg_entry = mm.config.get('models', {}).get(m['id'], {})
            has_key = bool(cfg_entry.get('api_key'))
            foreign.append({
                "id": m['id'],
                "name": m.get('name', m['id']),
                "has_key": has_key,
                "enabled": m.get('enabled', False),
                "base_url": m.get('base_url', ''),
            })

        # 把有 key 的放在前面
        foreign.sort(key=lambda x: (not x['has_key'], x['name']))
        return jsonify({"success": True, "data": foreign})
    except Exception as e:
        logger.error(f"[Collab] 获取外脑列表失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@BP.route('/privacy-levels', methods=['GET'])
def get_privacy_levels():
    """获取隐私级别选项"""
    from src.core.collab_manager import PRIVACY_LEVELS
    return jsonify({"success": True, "data": PRIVACY_LEVELS})


# ═══ 2026-09-14 V1：协作流水线（对话站+执行站编排） ═══

def _default_pipeline(cfg: dict) -> list:
    """未配置时的默认管线 = 经典两步（等价旧行为）"""
    fm = cfg.get('collab_foreign_model', 'deepseek')
    return [{"type": "chat", "participant": fm}, {"type": "chat", "participant": "local"}]


def _validate_pipeline(steps) -> tuple:
    """校验管线。返回 (cleaned_steps, None) 或 (None, 错误信息)"""
    if not isinstance(steps, list) or not (2 <= len(steps) <= 4):
        return None, "流水线需要 2~4 站"
    cleaned = []
    for idx, st in enumerate(steps):
        if not isinstance(st, dict) or st.get('type') not in ('chat', 'tool'):
            return None, f"第{idx+1}站类型无效（须为 chat/tool）"
        if st['type'] == 'chat':
            pid = str(st.get('participant', '')).strip()
            if not pid:
                return None, f"第{idx+1}站没选参与者"
            if pid == 'local':
                pass  # 当前选中的本地模型，运行时解析
            elif pid == 'harveneye':
                # ★ 2026-11-15（老谭定版）：含通用大脑的 HarvenEye 才能当站——
                # 门禁=视觉大脑通道就绪（models_ready），没配大脑的 HarvenEye 不进流水线
                try:
                    from src.core import eye_client as _ec
                    _mr = _ec.models_ready()
                    if not _mr.get('ready'):
                        return None, f"第{idx+1}站 HarvenEye 视觉大脑未就绪（先去模型中心装/钉选视觉大脑）"
                except Exception as e:
                    return None, f"第{idx+1}站 HarvenEye 门禁校验失败: {e}"
            elif pid.startswith('ollama:'):
                # ★ 2026-09-16：本地站可选指定 Ollama 模型
                if pid[7:] not in _ollama_installed_models():
                    return None, f"第{idx+1}站本地模型「{pid[7:]}」未安装或 Ollama 未启动"
            elif pid.startswith('gguf:'):
                # ★ 2026-09-16：本地站可选 GGUF 垂直模型（llama-cpp，不经过 Ollama）
                try:
                    from src.core.vertical_api import _get_all_models
                    if not any(x.get('id') == pid[5:] and x.get('engine') == 'llama_cpp'
                               for x in _get_all_models()):
                        return None, f"第{idx+1}站 GGUF 模型「{pid[5:]}」不存在"
                except Exception as e:
                    return None, f"第{idx+1}站 GGUF 模型校验失败: {e}"
            else:
                try:
                    from src.core.multi_model_manager import get_multi_model_manager
                    mm = get_multi_model_manager()
                    if pid not in (mm._get_all_providers() or {}):
                        return None, f"第{idx+1}站外脑「{pid}」不存在"
                except Exception as e:
                    return None, f"第{idx+1}站外脑校验失败: {e}"
            cleaned.append({"type": "chat", "participant": pid})
        else:
            mid = str(st.get('model', '')).strip()
            try:
                from src.core.vertical_api import _get_all_models
                m = next((x for x in _get_all_models()
                          if x.get('id') == mid and x.get('engine') == 'tool_executor'), None)
            except Exception as e:
                return None, f"第{idx+1}站垂直模型校验失败: {e}"
            if not m:
                return None, f"第{idx+1}站「{mid}」不是可执行的垂直工具模型（画图/作曲/语音类）"
            cleaned.append({"type": "tool", "model": mid})
    if cleaned[-1]['type'] != 'chat':
        return None, "最后一站必须是对话站（负责整合作答）"
    return cleaned, None


@BP.route('/pipeline', methods=['GET', 'POST'])
def collab_pipeline():
    """获取/保存协作流水线编排"""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config.yaml"
    )
    if request.method == 'GET':
        try:
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = yaml.safe_load(f) or {}
            steps = cfg.get('collab_pipeline') or _default_pipeline(cfg)
            return jsonify({"success": True, "data": {"pipeline": steps}})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # POST: 保存编排
    try:
        data = request.get_json(force=True, silent=True) or {}
        steps = data.get('pipeline')
        cleaned, err = _validate_pipeline(steps)
        if err:
            return jsonify({"success": False, "error": err}), 400
        if not _save_to_config({"pipeline": cleaned}):
            return jsonify({"success": False, "error": "保存到 config.yaml 失败"}), 500
        logger.info(f"[Collab] 流水线已保存: {cleaned}")
        return jsonify({"success": True, "data": {"pipeline": cleaned}})
    except Exception as e:
        logger.error(f"[Collab] 保存流水线失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@BP.route('/participants', methods=['GET'])
def collab_participants():
    """流水线可选参与者：chat=对话站（外脑+本地模型列表），tool=生成站（画图/作曲/语音）"""
    # ★ 2026-09-16 已定案：本地 ≠ Ollama 一种 —— 本地站可选全部本地模型（Ollama 已装 + GGUF 垂直模型）
    chat = [{"id": "local", "name": "🏠 本地（当前选中的）", "kind": "local", "has_key": True}]
    # ★ 2026-11-15（老谭定版）：含通用大脑的 HarvenEye = 本地通用模型能力升级版，进本地站下拉，
    # 名字就叫 HarvenEye；没配大脑的（models_ready=False）不进——与模型中心同款门禁
    try:
        from src.core import eye_client as _eye_cli
        _eye_ready = _eye_cli.models_ready()
        if _eye_ready.get('ready'):
            chat.append({"id": "harveneye", "name": "🏠 HarvenEye", "kind": "local", "has_key": True})
    except Exception as e:
        logger.debug(f"[Collab] HarvenEye 门禁探测跳过: {e}")
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        providers = mm._get_all_providers() or {}
        for pid, info in providers.items():
            if pid == 'ollama':
                # ★ 2026-09-16 已定案：UI 不出现 Ollama 字样、本地≠Ollama——
                #   这个笼统的 "Ollama (本地)" 外脑条目由下方逐模型本地清单（ollama:xxx）取代
                continue
            model_cfg = mm.config.get('models', {}).get(pid, {})
            has_key = bool(model_cfg.get('api_key')) or not info.get('needs_key', False)
            chat.append({"id": pid, "name": info.get('name', pid), "kind": "external", "has_key": has_key})
        chat.sort(key=lambda x: (x['kind'] != 'external' or not x['has_key'], x['name']))
    except Exception as e:
        logger.warning(f"[Collab] 外脑参与者加载失败: {e}")

    # 本地模型清单：Ollama 已装模型（全名，如 qwen2.5:14b / huatuo:latest）
    try:
        for name in _ollama_installed_models():
            chat.append({"id": f"ollama:{name}", "name": f"🏠 本地 {name}", "kind": "local", "has_key": True})
    except Exception as e:
        logger.warning(f"[Collab] 本地 Ollama 模型清单加载失败: {e}")

    # GGUF 垂直对话模型（llama-cpp 引擎，不经过 Ollama）
    try:
        from src.core.vertical_api import _get_all_models
        for m in _get_all_models():
            if m.get('engine') == 'llama_cpp' and m.get('model_path'):
                chat.append({"id": f"gguf:{m.get('id')}", "name": f"🏠 本地 {m.get('name', m.get('id'))}",
                             "kind": "gguf", "has_key": True})
    except Exception as e:
        logger.warning(f"[Collab] GGUF 模型清单加载失败: {e}")

    tool = []
    est_map = {
        'image_generation': '约20秒~2分钟',
        'music_generation': '约2~8分钟',
        'speech_synthesis': '约10~60秒',
    }
    try:
        from src.core.vertical_api import _get_all_models
        for m in _get_all_models():
            if m.get('engine') != 'tool_executor':
                continue
            tool.append({
                "id": m.get('id'), "name": m.get('name'), "icon": m.get('icon', '🧩'),
                "description": m.get('description', ''),
                "estimate": est_map.get(m.get('type'), '约1~5分钟'),
            })
    except Exception as e:
        logger.warning(f"[Collab] 垂直工具参与者加载失败: {e}")

    return jsonify({"success": True, "data": {"chat": chat, "tool": tool}})


def _ollama_installed_models() -> list:
    """Ollama 已装模型全名清单（禁代理直连本机，失败=空列表不骗人）

    ★ 2026-11-15：过滤 hv-pin-* 钉选注册件（#1324 视觉钉选链路的 Ollama 内部件）——
    /api/ollama/models 与 /api/models/local-all 均有同款过滤，本接口此前漏了，
    导致协作流水线本地站下拉露出 hv-pin-<哈希> 内部名（老谭 11-15 截图实锤）。
    """
    import json as _json
    import urllib.request
    try:
        req = urllib.request.Request('http://127.0.0.1:11434/api/tags')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as resp:
            return [m.get('name') for m in _json.loads(resp.read().decode('utf-8')).get('models', [])
                    if m.get('name') and not str(m.get('name')).startswith('hv-pin-')]
    except Exception:
        return []


def _save_to_config(data: dict) -> bool:
    """将协作配置持久化到 config.yaml。返回 True/False"""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config.yaml"
    )
    try:
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}

        # P2: collab_mode 持久化 — 确保写入的值与用户选择一致
        if 'mode' in data:
            cfg['collab_mode'] = data['mode']
            # 同步 collab_enabled 字段（兼容旧逻辑）
            cfg['collab_enabled'] = (data['mode'] != 'off')
        if 'enabled' in data:
            # 不再让 enabled 单独覆盖 collab_mode（这是之前 collab_mode 恢复 local_dual 的根因）
            cfg['collab_enabled'] = data['enabled']
        if 'foreign_model' in data:
            cfg['collab_foreign_model'] = data['foreign_model']
        if 'privacy_level' in data:
            cfg['collab_privacy_level'] = data['privacy_level']
        if 'deep_search' in data:
            cfg['collab_deep_search'] = data['deep_search']
        if 'pipeline' in data:
            cfg['collab_pipeline'] = data['pipeline']

        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(dict(cfg), f, allow_unicode=True, default_flow_style=False)
        # ★ 2026-11-15 根治「保存成功但执行还是旧流水线」：写文件成功后把本次改动同步进内存单例。
        # 实锤（18:28 保存 ace-step 生成站 → 18:32 执行仍跑旧三对话站）：
        #   保存/展示两端读写 config.yaml 文件，唯独执行端 engine._run_collab_pipeline
        #   读 get_config() 内存快照（启动时加载）→ 不重启永不生效。
        # 只同步本次改动的 6 个键（不全量覆盖，避免冲掉内存里尚未落盘的其它运行时改动）
        try:
            from src.utils.config import get_config as _gc
            _mem = _gc()
            _KEYMAP = {'pipeline': 'collab_pipeline', 'mode': 'collab_mode',
                       'enabled': 'collab_enabled', 'foreign_model': 'collab_foreign_model',
                       'privacy_level': 'collab_privacy_level', 'deep_search': 'collab_deep_search'}
            for _k, _v in data.items():
                _ck = _KEYMAP.get(_k)
                if _ck is not None:
                    _mem[_ck] = _v
        except Exception as _e:
            logger.warning(f"[Collab] 内存配置同步失败（文件已保存，重启后生效）: {_e}")
        logger.info("[Collab] 配置已持久化到 config.yaml + 内存单例已同步")
        return True
    except Exception as e:
        logger.error(f"[Collab] 保存配置失败: {e}")
        return False


@BP.route('/memory-check', methods=['GET'])
def check_memory():
    """检查当前内存是否支持多模型并行"""
    try:
        from pathlib import Path
        cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
        cfg = {}
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

        from src.core.collab_manager import get_collab_manager
        cm = get_collab_manager(cfg)

        # 构建当前激活的模型列表
        active = []
        if cm.mode != 'off':
            # 本地语言模型
            default_model = cfg.get('default_model', '')
            if default_model:
                active.append(default_model)
            # 当前选中的模型（如果不同于默认）
            current_model = cfg.get('current_model', '')
            if current_model and current_model != default_model and not current_model.startswith('local:'):
                # 去掉 local: 前缀
                clean = current_model.replace('local:', '')
                if clean not in active:
                    active.append(clean)

        from src.core.hardware_detector import detect_hardware, get_memory_warnings
        spec = detect_hardware()
        mem_info = get_memory_warnings(spec, active)

        return jsonify({
            "success": True,
            "data": {
                "collab_mode": cm.mode,
                "active_models": active,
                "memory": mem_info,
            }
        })
    except Exception as e:
        logger.error(f"[Collab] 内存检查失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ─── 本地双模配置（大脑模型 + 工具模型）───

@BP.route('/dual-config', methods=['GET'])
def get_dual_config():
    """获取本地双模的大脑/工具模型配置"""
    try:
        from src.utils.config import get_config
        cfg = get_config()
        brain = cfg.get('local_dual_brain', '')
        tools = cfg.get('local_dual_tools', [])
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(',') if t.strip()]
        return jsonify({
            "success": True,
            "brain": brain,
            "tools": tools
        })
    except Exception as e:
        logger.error(f"[Collab] 获取双模配置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@BP.route('/dual-config', methods=['POST'])
def set_dual_config():
    """保存本地双模的大脑/工具模型配置"""
    try:
        from src.utils.config import get_config
        data = request.get_json(force=True, silent=True) or {}
        cfg = get_config()

        if 'brain' in data:
            cfg.set('local_dual_brain', data['brain'])
        if 'tools' in data:
            cfg.set('local_dual_tools', data['tools'])

        # 持久化到 config.yaml（复用 Config.save）
        cfg.save()

        return jsonify({"success": True, "message": "双模配置已保存"})
    except Exception as e:
        logger.error(f"[Collab] 保存双模配置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
