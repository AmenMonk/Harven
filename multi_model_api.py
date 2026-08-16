"""
Multi-Model API routes for Harven AI
Supports: Ollama (local), DeepSeek, Qwen, Kimi, Doubao, OpenAI, Claude, and custom providers

Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import json
import threading
import traceback
from flask import jsonify, request
from src.core.multi_model_manager import MultiModelManager
import logging
logger = logging.getLogger(__name__)

# Global multi-model manager instance
_mmm = None
_mmm_lock = threading.Lock()


def get_mmm():
    """Get or create MultiModelManager instance"""
    global _mmm
    if _mmm is None:
        with _mmm_lock:
            if _mmm is None:
                _mmm = MultiModelManager()
    return _mmm


def register_multi_model_routes(app):
    """Register all multi-model API routes to the Flask app"""

    def get_models():
        """Get all available models and their status"""
        try:
            mmm = get_mmm()
            models = mmm.get_all_models()
            current = mmm.current_model
            return jsonify({
                'success': True,
                'models': models,
                'current': current
            })
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    def enable_model():
        """Enable a model with API key"""
        try:
            data = request.json or {}
            model_id = data.get('model_id', '') or data.get('model_type', '')
            api_key = data.get('api_key', '')
            base_url = data.get('base_url', '')
            model_name = data.get('model_name', '')

            if not model_id:
                return jsonify({'success': False, 'error': 'model_id required'}), 400

            mmm = get_mmm()
            result = mmm.enable_model(model_id, api_key, base_url, model_name)

            if result['success']:
                return jsonify({'success': True, 'message': f'{model_id} enabled'})
            else:
                return jsonify({'success': False, 'error': result['error']}), 400
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    def disable_model(model_id):
        """Disable a model"""
        try:
            mmm = get_mmm()
            mmm.disable_model(model_id)
            return jsonify({'success': True})
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    def set_current_model():
        """Set current active model"""
        try:
            data = request.json or {}
            model_id = data.get('model_id', '')

            if not model_id:
                return jsonify({'success': False, 'error': 'model_id required'}), 400

            mmm = get_mmm()
            result = mmm.set_current_model(model_id)

            if result['success']:
                return jsonify({'success': True, 'message': f'Current model set to {model_id}'})
            else:
                return jsonify({'success': False, 'error': result['error']}), 400
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    def test_model():
        """Test connection to a model provider — 测试通过后自动启用该模型"""
        try:
            data = request.json or {}
            model_id = data.get('model_id', '') or data.get('model_type', '')
            api_key = data.get('api_key', '')
            base_url = data.get('base_url', '')
            model_name = data.get('model_name', '')

            if not model_id:
                return jsonify({'success': False, 'error': 'model_id required'}), 400

            mmm = get_mmm()
            result = mmm.test_connection(model_id, api_key=api_key, base_url=base_url)

            if result['success']:
                # ★ 测试通过后自动启用该模型（跳过重复测试）
                mmm.enable_model(model_id, api_key=api_key, base_url=base_url, model_name=model_name, skip_test=True)
                logger.info(f"[MultiModel] 测试通过，已自动启用模型: {model_id}")
                return jsonify({'success': True, 'enabled': True, 'message': f'{model_id} 连接成功，已自动启用'})
            else:
                return jsonify({'success': False, 'error': result.get('error', '连接失败')}), 400
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    def add_custom_model():
        """Add a custom model provider"""
        try:
            data = request.json or {}
            provider_id = (data.get('provider_id') or '').strip().lower().replace(' ', '_')
            name = (data.get('name') or '').strip()
            base_url = (data.get('base_url') or '').strip().rstrip('/')
            api_key = (data.get('api_key') or '').strip()
            model_name = (data.get('model_name') or '').strip()

            if not provider_id or not name or not base_url:
                return jsonify({'success': False, 'error': 'provider_id、name、base_url 均为必填'}), 400

            mmm = get_mmm()
            result = mmm.add_custom_provider(
                provider_id=provider_id,
                name=name,
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
            )
            return jsonify(result)
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    def delete_custom_model(provider_id):
        """Delete a custom model provider"""
        try:
            mmm = get_mmm()
            result = mmm.delete_custom_provider(provider_id)
            return jsonify(result)
        except Exception as e:
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    # Register routes
    app.add_url_rule('/api/models', endpoint='mm_get_models', view_func=get_models, methods=['GET'])
    app.add_url_rule('/api/models/enable', endpoint='mm_enable_model', view_func=enable_model, methods=['POST'])
    app.add_url_rule('/api/models/disable/<model_id>', endpoint='mm_disable_model', view_func=disable_model, methods=['POST'])
    app.add_url_rule('/api/models/set_current', endpoint='mm_set_current', view_func=set_current_model, methods=['POST'])
    app.add_url_rule('/api/models/test', endpoint='mm_test_model', view_func=test_model, methods=['POST'])
    app.add_url_rule('/api/models/add_custom', endpoint='mm_add_custom', view_func=add_custom_model, methods=['POST'])
    app.add_url_rule('/api/models/delete_custom/<provider_id>', endpoint='mm_delete_custom', view_func=delete_custom_model, methods=['POST'])

    logger.info("[MultiModel] Routes registered successfully")
