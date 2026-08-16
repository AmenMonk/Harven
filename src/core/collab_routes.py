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

        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(dict(cfg), f, allow_unicode=True, default_flow_style=False)
        logger.info("[Collab] 配置已持久化到 config.yaml")
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
