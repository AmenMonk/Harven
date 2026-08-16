# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Multi-Model Manager for Harven AI
Supports multiple AI model providers with free models prioritized.
"""

import os
import shutil
import sys
import json
import time
import logging
import threading
import requests
import yaml
from pathlib import Path

# Load project config for Ollama settings
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_config_yaml = {}
_config_path = _PROJECT_ROOT / 'config.yaml'
if _config_path.exists():
    try:
        with open(_config_path, 'r', encoding='utf-8') as _f:
            _config_yaml = yaml.safe_load(_f) or {}
    except Exception:
        pass

PROVIDERS = {
    'ollama': {
        'name': 'Ollama (本地)',
        'free': True,
        'needs_key': False,
        'base_url': 'http://localhost:11434/v1',
    },
    'deepseek': {
        'name': 'DeepSeek',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.deepseek.com/v1',
        'models': ['deepseek-chat', 'deepseek-reasoner'],
    },
    'qwen': {
        'name': '通义千问 (DashScope)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'models': ['qwen-turbo', 'qwen-plus', 'qwen-max', 'qwen3-235b-a22b'],
    },
    'kimi': {
        'name': 'Kimi (Moonshot)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.moonshot.cn/v1',
        'models': ['moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'],
    },
    'doubao': {
        'name': '豆包 (火山引擎)',
        'free': False,
        'needs_key': True,
        'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
        'models': ['doubao-lite-4k', 'doubao-pro-32k', 'doubao-pro-128k'],
    },
    'openai': {
        'name': 'OpenAI',
        'free': False,
        'needs_key': True,
        'base_url': 'https://api.openai.com/v1',
        'models': ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'o4-mini'],
    },
    'claude': {
        'name': 'Claude (Anthropic)',
        'free': False,
        'needs_key': True,
        'base_url': 'https://api.anthropic.com/v1',
        'models': ['claude-sonnet-4-20250514', 'claude-3-5-sonnet-20241022'],
    },
    'gemini': {
        'name': 'Gemini (Google)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://generativelanguage.googleapis.com/v1beta',
        'models': ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.0-flash'],
        'is_gemini': True,
    },
    'groq': {
        'name': 'Groq',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.groq.com/openai/v1',
        'models': ['llama-3.3-70b-versatile', 'mixtral-8x7b-32768', 'gemma2-9b-it'],
    },
    'mistral': {
        'name': 'Mistral AI',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.mistral.ai/v1',
        'models': ['mistral-large-latest', 'mistral-small-latest', 'codestral-latest'],
    },
    'cohere': {
        'name': 'Cohere',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.cohere.ai/v1',
        'models': ['command-r-plus', 'command-r', 'command'],
    },
    'moonshot': {
        'name': 'Moonshot (月之暗面)',
        'free': False,
        'needs_key': True,
        'base_url': 'https://api.moonshot.cn/v1',
        'models': ['moonshot-v1-auto', 'moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'],
    },
    'siliconflow': {
        'name': '硅基流动 (SiliconFlow)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.siliconflow.cn/v1',
        'models': ['Qwen/Qwen2.5-7B-Instruct', 'deepseek-ai/DeepSeek-V3', 'Pro/Llama-3.3-70B-Instruct'],
    },
    'openrouter': {
        'name': 'OpenRouter',
        'free': False,
        'needs_key': True,
        'base_url': 'https://openrouter.ai/api/v1',
        'models': ['openai/gpt-4o', 'anthropic/claude-sonnet-4', 'google/gemini-2.5-pro'],
    },
    # ──────── 国内厂商 ────────
    'hunyuan': {
        'name': '腾讯元宝 (混元)',
        'free': False,
        'needs_key': True,
        'base_url': 'https://api.hunyuan.cloud.tencent.com/v1',
        'models': ['hunyuan-turbos-latest', 'hunyuan-pro', 'hunyuan-lite', 'hunyuan-t1-latest'],
    },
    'baichuan': {
        'name': '百川智能 (Baichuan)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.baichuan-ai.com/v1',
        'models': ['Baichuan4-Turbo', 'Baichuan4-Air', 'Baichuan3-Turbo'],
    },
    'zhipu': {
        'name': '智谱AI (GLM)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'models': ['glm-4-plus', 'glm-4-air', 'glm-4-flash', 'glm-4-long'],
    },
    'yi': {
        'name': '零一万物 (Yi)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.lingyiwanwu.com/v1',
        'models': ['yi-lightning', 'yi-large', 'yi-medium', 'yi-vision-plus'],
    },
    'stepfun': {
        'name': '阶跃星辰 (Step)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.stepfun.com/v1',
        'models': ['step-2-mini', 'step-2-16k', 'step-1-8k', 'step-1-flash'],
    },
    'minimax': {
        'name': 'MiniMax (海螺AI)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.minimaxi.chat/v1',
        'models': ['MiniMax-Text-01', 'abab6.5s-chat', 'abab5.5-chat'],
    },
    'ernie': {
        'name': '百度文心 (ERNIE)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://qianfan.baidubce.com/v2',
        'models': ['ernie-4.5-turbo-128k', 'ernie-4.5-8k', 'ernie-3.5-8k', 'ernie-speed-128k'],
        '_note': 'Bearer token via access_key/secret_key OAuth2',
    },
    'spark': {
        'name': '讯飞星火 (Spark)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://spark-api-open.xf-yun.com/v1',
        'models': ['x1', 'lite', 'generalv3.5', 'pro-128k'],
        '_note': 'API key 格式为 APIKey:APISecret',
    },
    'tiangong': {
        'name': '天工AI (SkyWork)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://sky-api.singularity-ai.com/saas/api/v4',
        'models': ['SkyChat-MegaVerse'],
    },
    'huggingface': {
        'name': 'HuggingFace (Inference)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api-inference.huggingface.co/v1',
        'models': ['meta-llama/Llama-3.3-70B-Instruct', 'Qwen/Qwen2.5-72B-Instruct', 'mistralai/Mixtral-8x7B-Instruct-v0.1'],
    },
    # ──────── 国际扩展 ────────
    'xai': {
        'name': 'xAI (Grok)',
        'free': False,
        'needs_key': True,
        'base_url': 'https://api.x.ai/v1',
        'models': ['grok-3', 'grok-3-mini', 'grok-2-latest'],
    },
    'perplexity': {
        'name': 'Perplexity AI',
        'free': False,
        'needs_key': True,
        'base_url': 'https://api.perplexity.ai',
        'models': ['sonar-pro', 'sonar', 'sonar-reasoning-pro', 'sonar-reasoning'],
    },
    'together': {
        'name': 'Together AI',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.together.xyz/v1',
        'models': ['meta-llama/Llama-3.3-70B-Instruct-Turbo', 'Qwen/Qwen2.5-72B-Instruct-Turbo', 'deepseek-ai/DeepSeek-V3'],
    },
}

CONFIG_FILE = Path(__file__).parent.parent.parent / 'data' / 'multi_models.json'
CONFIG_EXAMPLE = Path(__file__).parent.parent.parent / 'data' / 'multi_models.example.json'


class MultiModelManager:
    """Multi-model manager with support for built-in and custom providers."""
    def __init__(self):
        self.config_file = CONFIG_FILE
        self.config = self._load_config()
        self.current_model = self.config.get('current_model', 'ollama')
        # 确保 Ollama 始终处于已启用状态（本地模型无需 key）
        self._ensure_ollama_enabled()

    def _ensure_ollama_enabled(self):
        """确保 Ollama 默认处于启用状态"""
        if 'models' not in self.config:
            self.config['models'] = {}
        if not self.config['models'].get('ollama', {}).get('enabled'):
            self.config['models']['ollama'] = {
                'enabled': True,
                'api_key': None,
                'base_url': PROVIDERS['ollama']['base_url'],
                'model_name': _config_yaml.get('ollama', {}).get('default_model', 'qwen2.5:14b'),
                'enabled_at': time.time(),
            }
            self._save_config()

    def _load_config(self):
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 安全检查：确保没有任何第三方 API Key 残留（防止从旧版本迁移）
                    self._sanitize_config(config)
                    return config
            except (json.JSONDecodeError, IOError):
                pass
        # 首次启动：从出厂模板复制（仅包含 Ollama，无任何第三方密钥）
        return self._init_from_template()

    def _sanitize_config(self, config):
        """安全净化：移除可能残留的第三方 API Key 引用（不修改文件，仅内存清理）。"""
        if 'models' in config:
            for provider_id in list(config['models'].keys()):
                if provider_id == 'ollama':
                    continue
                entry = config['models'][provider_id]
                # 如果云端模型被启用但没有 api_key，则自动禁用
                if entry.get('enabled') and not entry.get('api_key'):
                    entry['enabled'] = False
                    logger.warning(f"[MultiModel] {provider_id} 已启用但无API密钥，自动禁用")

    def _init_from_template(self):
        """首次启动：从出厂模板创建配置文件，仅包含 Ollama 本地模型。"""
        if CONFIG_EXAMPLE.exists():
            try:
                shutil.copy2(CONFIG_EXAMPLE, self.config_file)
                logger.info("[MultiModel] 首次启动：已从出厂模板创建配置（仅Ollama本地模型）")
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[MultiModel] 从模板初始化失败: {e}")
        return {'models': {}, 'current_model': 'ollama'}

    def _save_config(self):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def _get_all_providers(self):
        """合并内置 PROVIDERS 与自定义 providers."""
        all_providers = dict(PROVIDERS)
        for cid, cinfo in self.config.get('custom_providers', {}).items():
            if cid not in all_providers:
                all_providers[cid] = {
                    'name': cinfo.get('name', cid),
                    'free': False,
                    'needs_key': True,
                    'base_url': cinfo.get('base_url', ''),
                    'models': cinfo.get('models', []),
                    'is_custom': True,
                }
        return all_providers

    def get_all_models(self):
        """Get all configured models with their status."""
        models = []
        all_providers = self._get_all_providers()
        for provider_id, provider_info in all_providers.items():
            model_entry = self.config.get('models', {}).get(provider_id, {})
            enabled = model_entry.get('enabled', False)
            models.append({
                'id': provider_id,
                'name': provider_info['name'],
                'enabled': enabled,
                'is_current': self.current_model == provider_id,
                'needs_key': provider_info['needs_key'],
                'free': provider_info['free'],
                'models': provider_info.get('models', []),
                'is_custom': provider_info.get('is_custom', False),
                'base_url_hint': provider_info.get('base_url', ''),
            })
        return models

    def add_custom_provider(self, provider_id, name, base_url, api_key='', model_name='', models=None):
        """添加一个自定义 provider（如硅基流动、Gemini 中转等）."""
        if not provider_id or not name or not base_url:
            return {'success': False, 'error': 'provider_id / name / base_url 均不能为空'}
        # 不允许覆盖内置 provider
        if provider_id in PROVIDERS:
            return {'success': False, 'error': f'"{provider_id}" 是内置模型 ID，请换一个名称'}

        if 'custom_providers' not in self.config:
            self.config['custom_providers'] = {}
        self.config['custom_providers'][provider_id] = {
            'name': name,
            'base_url': base_url,
            'models': models or ([model_name] if model_name else []),
        }
        # 同时自动启用
        if 'models' not in self.config:
            self.config['models'] = {}
        self.config['models'][provider_id] = {
            'enabled': True,
            'api_key': api_key,
            'base_url': base_url,
            'model_name': model_name,
            'enabled_at': time.time(),
        }
        self._save_config()
        return {'success': True}

    def delete_custom_provider(self, provider_id):
        """删除自定义 provider."""
        if provider_id in PROVIDERS:
            return {'success': False, 'error': '内置模型不能删除，只能禁用'}
        self.config.get('custom_providers', {}).pop(provider_id, None)
        self.config.get('models', {}).pop(provider_id, None)
        if self.current_model == provider_id:
            self.current_model = 'ollama'
            self.config['current_model'] = 'ollama'
        self._save_config()
        return {'success': True}

    def enable_model(self, provider_id, api_key=None, base_url=None, model_name=None):
        """Enable a model provider."""
        all_providers = self._get_all_providers()
        if provider_id not in all_providers:
            return {'success': False, 'error': f'Unknown provider: {provider_id}'}

        provider = all_providers[provider_id]

        # 需要 key 的云端模型，才强制要求 api_key
        if provider['needs_key'] and not api_key:
            return {'success': False, 'error': 'API key required for this provider'}

        if 'models' not in self.config:
            self.config['models'] = {}

        entry = self.config['models'].get(provider_id, {})
        self.config['models'][provider_id] = {
            'enabled': True,
            'api_key': api_key if provider['needs_key'] else entry.get('api_key'),
            'base_url': base_url or entry.get('base_url') or provider['base_url'],
            'model_name': model_name or entry.get('model_name') or (provider.get('models', [''])[0] if provider.get('models') else ''),
            'enabled_at': time.time(),
        }
        self._save_config()
        return {'success': True}

    def disable_model(self, provider_id):
        """Disable a model provider."""
        if provider_id in self.config.get('models', {}):
            self.config['models'][provider_id]['enabled'] = False
            self._save_config()
        return {'success': True}

    def set_current_model(self, provider_id):
        """Set the current active model."""
        all_providers = self._get_all_providers()
        if provider_id not in all_providers:
            return {'success': False, 'error': f'Unknown provider: {provider_id}'}

        model_entry = self.config.get('models', {}).get(provider_id, {})
        # Ollama 是本地模型，始终允许切换；其他模型要求已启用
        if all_providers[provider_id]['needs_key'] and not model_entry.get('enabled'):
            return {'success': False, 'error': 'Model not enabled. Please enable it first.'}

        self.current_model = provider_id
        self.config['current_model'] = provider_id
        self._save_config()
        return {'success': True}

    def test_connection(self, provider_id, api_key=None, base_url=None):
        """Test connection to a model provider.
        
        Args:
            api_key: 可选，覆盖已保存的 key（用于测试新输入的 key）
            base_url: 可选，覆盖已保存的 URL
        """
        all_providers = self._get_all_providers()
        if provider_id not in all_providers:
            return {'success': False, 'error': f'Unknown provider: {provider_id}'}

        provider = all_providers[provider_id]
        model_entry = self.config.get('models', {}).get(provider_id, {})

        # 优先使用传入的 key/url，否则用已保存的
        effective_key = api_key or model_entry.get('api_key', '')
        effective_url = base_url or model_entry.get('base_url', provider['base_url'])

        if provider['needs_key'] and not effective_key:
            return {'success': False, 'error': 'API key not configured'}

        try:
            api_key = effective_key
            base_url = effective_url
            model_name = model_entry.get('model_name', provider.get('models', [''])[0] if provider.get('models') else '')

            # Gemini uses different API format
            if provider.get('is_gemini'):
                url = f"{base_url}/models/{model_name}:generateContent"
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": "test"}]}],
                    "generationConfig": {"maxOutputTokens": 5},
                }
                params = {"key": api_key}
                resp = requests.post(url, params=params, json=payload, timeout=15)
                if resp.status_code == 200:
                    return {'success': True}
                return {'success': False, 'error': f'Status {resp.status_code}: {resp.text[:200]}'}

            headers = self._build_auth_headers(provider_id, effective_key, provider)

            # 优先使用 /chat/completions 验证 API 密钥有效性
            # （/models 端点无需有效密钥也可能返回 200，造成假阳性）
            chat_url = f"{base_url}/chat/completions"
            payload = {
                'model': model_name,
                'messages': [{'role': 'user', 'content': 'test'}],
                'max_tokens': 10,
            }
            resp = requests.post(chat_url, headers=headers, json=payload, timeout=15)

            if resp.status_code in [200, 201]:
                return {'success': True}
            elif resp.status_code == 401:
                return {'success': False, 'error': 'API 密钥无效（401 Unauthorized）'}
            elif resp.status_code == 403:
                return {'success': False, 'error': 'API 密钥无权访问（403 Forbidden）'}
            else:
                # 如果 /chat/completions 失败，尝试 /models 作为备用
                test_url = f"{base_url}/models"
                try:
                    resp2 = requests.get(test_url, headers=headers, timeout=10)
                    if resp2.status_code == 200:
                        return {'success': True, 'warning': '端点可访问，API 密钥未验证'}
                except Exception:
                    pass
                return {'success': False, 'error': f'Status {resp.status_code}: {resp.text[:200]}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _build_auth_headers(self, provider_id, api_key, provider):
        """构建鉴权头，处理不同厂商的特殊鉴权方式。
        
        - 标准 Bearer：大多数厂商
        - 讯飞星火：api_key 格式为 'APIKey:APISecret'，使用 Bearer APIKey
        - 百度文心：api_key 直接作为 Bearer token（需用户自行在百度云换取 access_token）
        """
        headers = {'Content-Type': 'application/json'}
        if not api_key:
            return headers
        if provider_id == 'spark':
            # 讯飞：用户填 "APIKey:APISecret"，实际 Bearer 用 APIKey 部分
            spark_key = api_key.split(':')[0] if ':' in api_key else api_key
            headers['Authorization'] = f'Bearer {spark_key}'
        else:
            headers['Authorization'] = f'Bearer {api_key}'
        return headers


        """Generate response using the current model."""
        provider_id = self.current_model
        all_providers = self._get_all_providers()

        if provider_id == 'ollama':
            return self._generate_ollama(messages, stream)
        elif provider_id == 'gemini':
            return self._generate_gemini(messages, stream)
        elif provider_id in all_providers:
            return self._generate_openai_compatible(provider_id, messages, stream)
        else:
            def _err():
                yield {'type': 'error', 'content': f'未知 provider: {provider_id}'}
            return _err()

    def _generate_ollama(self, messages, stream=True):
        """Generate using Ollama - delegates to the existing ModelManager."""
        try:
            from src.core.model_manager import ModelManager
            base_url = _config_yaml.get('ollama', {}).get('base_url', 'http://localhost:11434')
            model_name = _config_yaml.get('ollama', {}).get('default_model', 'qwen2.5:14b')
            mm = ModelManager(base_url, model_name)
            response = mm.chat(messages, stream=stream, temperature=0.7)
            if stream:
                for chunk in response:
                    yield {'type': 'text', 'content': chunk}
            else:
                yield {'type': 'text', 'content': response}
        except Exception as e:
            yield {'type': 'error', 'content': f'Ollama generation failed: {e}'}

    def _generate_openai_compatible(self, provider_id, messages, stream=True):
        """Generate using OpenAI-compatible API."""
        all_providers = self._get_all_providers()
        provider = all_providers.get(provider_id, PROVIDERS.get(provider_id, {}))
        model_entry = self.config.get('models', {}).get(provider_id, {})

        api_key = model_entry.get('api_key', '')
        base_url = model_entry.get('base_url', provider['base_url'])
        model_name = model_entry.get('model_name', provider.get('models', [''])[0] if provider.get('models') else '')

        headers = self._build_auth_headers(provider_id, api_key, provider)
        chat_url = f"{base_url}/chat/completions"
        payload = {
            'model': model_name,
            'messages': messages,
            'stream': stream,
        }

        try:
            if stream:
                resp = requests.post(chat_url, headers=headers, json=payload, stream=True, timeout=60)
                for line in resp.iter_lines():
                    if line:
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            data = line[6:]
                            if data.strip() == '[DONE]':
                                break
                            try:
                                chunk = json.loads(data)
                                content = chunk['choices'][0]['delta'].get('content', '')
                                if content:
                                    yield {'type': 'text', 'content': content}
                            except (json.JSONDecodeError, KeyError, IndexError):
                                pass
            else:
                resp = requests.post(chat_url, headers=headers, json=payload, timeout=60)
                result = resp.json()
                content = result['choices'][0]['message']['content']
                yield {'type': 'text', 'content': content}
        except Exception as e:
            yield {'type': 'error', 'content': str(e)}

    def _generate_gemini(self, messages, stream=True):
        """Generate using Google Gemini API (non-OpenAI-compatible)."""
        model_entry = self.config.get('models', {}).get('gemini', {})
        api_key = model_entry.get('api_key', '')
        base_url = model_entry.get('base_url', 'https://generativelanguage.googleapis.com/v1beta')
        model_name = model_entry.get('model_name', 'gemini-2.5-flash')

        if not api_key:
            yield {'type': 'error', 'content': 'Gemini API key not configured'}
            return

        # Convert OpenAI format messages to Gemini format
        gemini_contents = []
        system_instruction = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = content
            elif role == "user":
                gemini_contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                gemini_contents.append({"role": "model", "parts": [{"text": content}]})

        url = f"{base_url}/models/{model_name}:{'streamGenerateContent' if stream else 'generateContent'}"
        params = {"key": api_key}
        payload = {
            "contents": gemini_contents,
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        try:
            if stream:
                payload["generationConfig"] = {"temperature": 0.7}
                resp = requests.post(url, params=params, json=payload, stream=True, timeout=120)
                for line in resp.iter_lines():
                    if line:
                        line = line.decode('utf-8')
                        if line.startswith('['):
                            line = line[1:]
                        if line.endswith(','):
                            line = line[:-1]
                        if line.endswith(']'):
                            line = line[:-1]
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            candidates = chunk.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                for part in parts:
                                    if "text" in part:
                                        yield {'type': 'text', 'content': part["text"]}
                        except json.JSONDecodeError:
                            pass
            else:
                resp = requests.post(url, params=params, json=payload, timeout=120)
                result = resp.json()
                candidates = result.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts)
                    yield {'type': 'text', 'content': text}
                else:
                    yield {'type': 'error', 'content': str(result)}
        except Exception as e:
            yield {'type': 'error', 'content': f'Gemini API error: {e}'}

    # ═══════════════════════════════════════════════════════════════════
    # 统一生成入口
    # ═══════════════════════════════════════════════════════════════════

    def generate_response(self, messages, stream=True):
        """统一生成入口：根据 current_model 路由到对应生成器。

        engine.py 调用：mm.generate_response(all_messages, stream=True)
        自动路由到 _generate_ollama / _generate_openai_compatible / _generate_gemini
        """
        provider_id = self.current_model
        logger.info(f"[MultiModel] generate_response 路由 → {provider_id}")

        if provider_id == 'ollama':
            return self._generate_ollama(messages, stream=stream)
        elif provider_id == 'gemini':
            return self._generate_gemini(messages, stream=stream)
        else:
            return self._generate_openai_compatible(provider_id, messages, stream=stream)


# Global instance
_manager = None
_manager_lock = threading.Lock()

def get_multi_model_manager():
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = MultiModelManager()
    return _manager
