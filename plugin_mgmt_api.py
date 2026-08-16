"""
Harven AI - Plugin Management API（Blueprint 版本）
插件管理 REST 接口（完善版）

Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import json
import traceback
from pathlib import Path
from flask import Blueprint, jsonify, request

bp = Blueprint('plugins', __name__, url_prefix='/api/plugins')

from src.core.plugin_system import get_plugin_manager
import logging
logger = logging.getLogger(__name__)


@bp.route('/', methods=['GET'])
def plugins_list():
    """列出所有已安装插件"""
    try:
        pm = get_plugin_manager()
        plugins = pm.list_plugins()
        return jsonify({'success': True, 'plugins': plugins})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/<plugin_name>/enable', methods=['POST'])
def plugins_enable(plugin_name):
    """启用插件"""
    try:
        pm = get_plugin_manager()
        plugin = pm._plugins.get(plugin_name)
        if not plugin:
            return jsonify({'success': False, 'error': f'插件 {plugin_name} 不存在'}), 404
        plugin.enabled = True
        plugin.on_enable()
        return jsonify({'success': True, 'message': f'插件 {plugin_name} 已启用'})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/<plugin_name>/disable', methods=['POST'])
def plugins_disable(plugin_name):
    """禁用插件"""
    try:
        pm = get_plugin_manager()
        plugin = pm._plugins.get(plugin_name)
        if not plugin:
            return jsonify({'success': False, 'error': f'插件 {plugin_name} 不存在'}), 404
        plugin.enabled = False
        plugin.on_disable()
        return jsonify({'success': True, 'message': f'插件 {plugin_name} 已禁用'})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/<plugin_name>/uninstall', methods=['DELETE'])
def plugins_uninstall(plugin_name):
    """卸载插件"""
    try:
        pm = get_plugin_manager()
        if plugin_name not in pm._plugins:
            return jsonify({'success': False, 'error': f'插件 {plugin_name} 不存在'}), 404
        pm.unregister(plugin_name)
        # 尝试删除插件文件
        plugins_dir = Path(pm._plugins_dir)
        for f in plugins_dir.glob(f'{plugin_name}*.py'):
            f.unlink(missing_ok=True)
        return jsonify({'success': True, 'message': f'插件 {plugin_name} 已卸载'})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/reload', methods=['POST'])
def plugins_reload():
    """重新加载所有插件"""
    try:
        pm = get_plugin_manager()
        # 先注销所有插件
        for name in list(pm._plugins.keys()):
            pm.unregister(name)
        pm.load_plugins()
        return jsonify({'success': True, 'count': len(pm._plugins), 'plugins': pm.list_plugins()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/install', methods=['POST'])
def plugins_install():
    """安装插件 — 支持三种方式：GitHub仓库 / URL下载 / 直接代码"""
    try:
        data = request.json or {}
        repo_url = data.get('repo_url', '').strip()
        plugin_code = data.get('code', '').strip()
        plugin_name = data.get('name', '').strip()
        plugin_url = data.get('url', '').strip()
        dry_run = data.get('dry_run', False)

        pm = get_plugin_manager()
        plugins_dir = Path(pm._plugins_dir)
        plugins_dir.mkdir(exist_ok=True)

        # ── 方式1: GitHub 仓库安装 ──
        if repo_url:
            import re
            if not re.match(r'^https?://github\.com/[\w\-\.]+/[\w\-\.]+', repo_url, re.IGNORECASE):
                return jsonify({'error': '仅支持 GitHub 仓库 URL（如 https://github.com/user/repo）'}), 400
            try:
                from src.core.plugin_installer import install_plugin_from_github, extract_and_validate_plugin
                import tempfile, zipfile, os as _os
                with tempfile.TemporaryDirectory() as tmpdir:
                    import requests as req
                    zip_url = repo_url.rstrip('/') + '/archive/refs/heads/main.zip'
                    resp = req.get(zip_url, timeout=30)
                    resp.raise_for_status()
                    zip_path = _os.path.join(tmpdir, 'repo.zip')
                    with open(zip_path, 'wb') as f:
                        f.write(resp.content)
                    with zipfile.ZipFile(zip_path) as zf:
                        zf.extractall(tmpdir)
                    if dry_run:
                        # 只扫描不安装
                        extracted_dirs = [d for d in Path(tmpdir).iterdir() if d.is_dir()]
                        if not extracted_dirs:
                            return jsonify({'error': '仓库为空或格式不支持'}), 400
                        scan_dir = str(extracted_dirs[0])
                        result = install_plugin_from_github(repo_url, str(plugins_dir), scan_only=True)
                        return jsonify({'success': True, 'dry_run': True, 'scan_result': result})
                    else:
                        result = install_plugin_from_github(repo_url, str(plugins_dir))
                        if result.get('success'):
                            pm.load_plugins()
                            return jsonify(result)
                        else:
                            return jsonify(result), 400
            except Exception as e:
                import traceback; traceback.print_exc()
                return jsonify({'error': str(e)}), 500

        # ── 方式2: URL 下载代码 ──
        if plugin_url:
            import requests as req
            resp = req.get(plugin_url, timeout=10)
            resp.raise_for_status()
            plugin_code = resp.text
            if not plugin_name:
                plugin_name = Path(plugin_url).stem

        # ── 方式3: 直接粘贴代码 ──
        if not plugin_code:
            return jsonify({'success': False, 'error': '插件代码/repo_url/url 不能都为空'}), 400
        if not plugin_name:
            return jsonify({'success': False, 'error': '插件名称不能为空'}), 400

        # 安全检查
        dangerous = ['subprocess', 'os.system', 'exec(', 'eval(', '__import__', 'shutil.rmtree']
        for d in dangerous:
            if d in plugin_code:
                return jsonify({'success': False, 'error': f'安全检查失败：代码中含有危险调用 {d}'}), 400

        if not plugin_name.endswith('_plugin'):
            plugin_name = plugin_name + '_plugin'
        file_path = plugins_dir / f'{plugin_name}.py'
        file_path.write_text(plugin_code, encoding='utf-8')

        pm.load_plugins()
        return jsonify({'success': True, 'message': f'插件 {plugin_name} 安装成功', 'plugins': pm.list_plugins()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/demo', methods=['GET'])
def plugins_create_demo():
    """创建一个示例插件文件（帮助开发者了解格式）"""
    demo_code = '''"""
示例插件 - 天气查询
"""
from src.core.plugin_system import PluginBase, PluginResult
import logging
logger = logging.getLogger(__name__)


class WeatherPlugin(PluginBase):
    name = "weather_plugin"
    version = "1.0.0"
    description = "模拟天气查询插件"
    author = "Harven AI"

    def should_handle(self, message):
        keywords = ["天气", "weather", "气温", "下雨"]
        return 0.9 if any(k in message.lower() for k in keywords) else 0.0

    def execute(self, context):
        return PluginResult(
            success=True,
            data={"weather": "晴天 28°C"},
            message="今天天气晴朗，温度适宜，适合出行！"
        )
'''
    return jsonify({'success': True, 'demo_code': demo_code})
