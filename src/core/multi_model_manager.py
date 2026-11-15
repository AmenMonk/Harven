# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Multi-Model Manager for Harven AI
Supports multiple AI model providers with free models prioritized.
"""

import os
import re
import shutil
import sys
import json
import time
import logging
import threading
import requests
import yaml
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 独立性模块：外部API调用需要绕过出站白名单
try:
    from src.core.independence import bypass_independence
except ImportError:
    @contextmanager
    def bypass_independence():
        yield

# Load project config for Ollama settings
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_config_yaml = {}
_config_path = _PROJECT_ROOT / 'config.yaml'
if _config_path.exists():
    try:
        with open(_config_path, 'r', encoding='utf-8') as _f:
            _config_yaml = yaml.safe_load(_f) or {}
    except Exception as _e: logger.debug(f"[multi_model_manager] 忽略: {_e}")

# ★ #1507（2026-11-13）：外脑读超时放宽 120→240s（config.yaml brain_read_timeout 可覆盖）。
#   read timeout 是「相邻两个数据块之间」的哑火间隔，不是总时长——有字就续命。
#   120s 只够 low 思考档；high/max 深度思考（智谱官方可达数分钟）会被误砍，
#   且空流重试×2 = 白烧 3 次。240s 盖住绝大多数深度思考，真死连接代价仅多挂 2 分钟。
BRAIN_READ_TIMEOUT = 240
try:
    _v = int(_config_yaml.get('brain_read_timeout') or 0)
    if 60 <= _v <= 900:
        BRAIN_READ_TIMEOUT = _v
except Exception:
    pass

PROVIDERS = {
    'ollama': {
        'vision': True,
        'name': 'Ollama (本地)',
        'free': True,
        'needs_key': False,
        'base_url': 'http://localhost:11434/v1',
    },
    'deepseek': {
        'vision': False,
        'name': 'DeepSeek',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.deepseek.com/v1',
        'models': ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner'],
    },
    'qwen': {
        'vision': True,
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
        'vision': True,
        'name': '豆包 (火山引擎)',
        'free': False,
        'needs_key': True,
        'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
        'models': ['doubao-seed-2-0-pro-260215', 'doubao-seed-2-0-mini-260215', 'doubao-1-5-pro-32k-250115', 'doubao-1-5-lite-32k-250115'],
    },
    'openai': {
        'vision': True,
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
        'vision': True,
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
        'vision': False,
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
        'vision': False,
        'name': '百川智能 (Baichuan)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://api.baichuan-ai.com/v1',
        'models': ['Baichuan4-Turbo', 'Baichuan4-Air', 'Baichuan3-Turbo'],
    },
    'zhipu': {
        'vision': True,
        'name': '智谱AI (GLM)',
        'free': True,
        'needs_key': True,
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'models': ['glm-4-plus', 'glm-4-air', 'glm-4-flash', 'glm-4-long'],
    },
    'yi': {
        'vision': False,
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

# ★ #1309（2026-10-29）本地模型判定唯一真家：全项目凡「选的是不是本地」判定一律调这里，
#   禁止再手抄 (None,'','ollama') 清单——本次事故根因就是 cloud_link 手抄清单漏了
#   'harveneye'，用户选本地被误判外脑模式 → #1288 代选换模型（不可原谅级，开发者定性）。
LOCAL_MODEL_IDS = (None, '', 'ollama', 'harveneye')


def is_local_model(provider_id) -> bool:
    """provider_id 是否本地阵营（未选/Ollama/HarvenEye）。单一真家，全局复用。"""
    return (provider_id or '') in ('ollama', 'harveneye', '') or provider_id is None


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
                'model_name': _config_yaml.get('ollama', {}).get('default_model', '') or '',  # ★ 2026-09-26 审计：不内定，用户没选就是空
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
        """安全净化：移除可能残留的第三方 API Key 引用（不修改文件，仅内存清理）。
        同时检查 config.yaml 中的 model_keys，避免误禁用、自动补全。"""
        if 'models' not in config:
            return
        yaml_keys = {}
        try:
            cfg = yaml.safe_load(_config_path.read_text(encoding='utf-8')) or {}
            yaml_keys = cfg.get('model_keys', {})
        except Exception as _e: logger.debug(f"[multi_model_manager] 忽略: {_e}")
        for provider_id in list(config['models'].keys()):
            if provider_id == 'ollama':
                continue
            entry = config['models'][provider_id]
            # ★ 2026-08-25 P1: 用户选择大于一切——只补全 Key，绝不自动改 enabled 状态
            # （旧逻辑"无key自动禁用/有key自动启用"会反复横跳，覆盖用户手动设置）
            if not entry.get('api_key') and yaml_keys.get(provider_id):
                entry['api_key'] = yaml_keys[provider_id]

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

    def restore_mode_slot(self, mode):
        """★ #1288（2026-10-29）分槽记忆补上「读」半边：按查询模式换回该槽用户上次选的模型。

        背景：#1275/方案A 只做了「写槽位」（选模型时记 cloud/local 槽），切换查询模式时
        没人把 current_model 换回来——用户本地模式选了 HarvenEye 再点 🤖 外脑，
        current_model 仍是 harveneye，直接撞「外脑模式选了本地模型」报错（10-29 实测实锤）。
        换回的是用户自己上次选的模型（非代选，铁律 0 合规）。

        Args: mode: 'cloud' | 'local'
        Returns: 换回的 provider_id；槽位空/阵营不符/切换失败返回 ''（调用方走原报错路径）。
        """
        try:
            from src.utils.config import get_config
            _cfg = get_config()
            pid = ((_cfg.get('current_model_by_mode') or {}).get(mode) or '').strip()
        except Exception:
            pid = ''
        if not pid:
            return ''
        # 阵营校验：槽位里存的必须是该阵营的模型（脏数据不代选）
        if mode == 'cloud' and pid in ('ollama', 'harveneye', ''):
            return ''
        if mode == 'local' and pid not in ('ollama', 'harveneye'):
            return ''
        if pid == self.current_model:
            return pid
        r = self.set_current_model(pid)
        if not r.get('success'):
            return ''
        # 同步 json 槽位镜像（历史欠账：json 的 current_model_by_mode 一直是 None 与 yaml 不同步）
        try:
            self.config.setdefault('current_model_by_mode', {})[mode] = pid
            self.config['last_query_mode'] = mode
            self._save_config()
        except Exception:
            pass
        logger.info(f"[MultiModel] #1288 按模式槽位换回 {mode} 槽模型: {pid}")
        return pid

    def _get_all_providers(self):
        """合并内置 PROVIDERS 与自定义 providers，注入 config.yaml 的 API Key."""
        all_providers = dict(PROVIDERS)
        # ★ 2026-08-09: 从 config.yaml 注入 API Key（解决下拉框 "当前外脑" 不显示模型的问题）
        yaml_keys = {}
        try:
            cfg = yaml.safe_load(_config_path.read_text(encoding='utf-8')) or {}
            yaml_keys = cfg.get('model_keys', {})
        except Exception:
            pass
        # 注入来自配置文件中的 API Key
        for pid in all_providers:
            if pid not in all_providers:
                continue
            all_providers[pid] = dict(all_providers[pid])  # shallow copy
            all_providers[pid]['api_key'] = yaml_keys.get(pid, '')

        for cid, cinfo in self.config.get('custom_providers', {}).items():
            if cid not in all_providers:
                all_providers[cid] = {
                    'name': cinfo.get('name', cid),
                    'free': False,
                    'needs_key': True,
                    'base_url': cinfo.get('base_url', ''),
                    'models': cinfo.get('models', []),
                    'is_custom': True,
                    'api_key': cinfo.get('api_key', ''),
                }
        return all_providers

    def get_all_models(self):
        """Get all configured models with their status."""
        models = []
        all_providers = self._get_all_providers()
        for provider_id, provider_info in all_providers.items():
            model_entry = self.config.get('models', {}).get(provider_id, {})
            enabled = model_entry.get('enabled', False)
            has_key = bool(provider_info.get('api_key', ''))
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
                'api_key': provider_info.get('api_key', ''),
                'configured': has_key,
            })
        return models

    def add_custom_provider(self, provider_id, name, base_url, api_key='', model_name='', models=None):
        """添加一个自定义 provider（如硅基流动、Gemini 中转等）."""
        if not provider_id or not name or not base_url:
            return {'success': False, 'error': 'provider_id / name / base_url 均不能为空'}
        # ★ 2026-08-03: 内置provider允许覆盖配置（不创建新的，只更新key和model）
        if provider_id in PROVIDERS:
            # 内置provider → 只保存key和model_name，不创建custom_provider
            if 'models' not in self.config:
                self.config['models'] = {}
            self.config['models'][provider_id] = {
                'enabled': True,
                'api_key': api_key,
                'base_url': base_url,
                'model_name': model_name or PROVIDERS[provider_id].get('models', [''])[0],
                'enabled_at': time.time(),
            }
            self._save_config()
            return {'success': True, 'message': f'已配置内置模型 {name}'}

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
        # ★ 2026-08-25 P1: 删除模型时不再自动切本地——标记未选，由用户手动选择
        if self.current_model == provider_id:
            self.current_model = None
            self.config['current_model'] = None
            logger.info(f"[MultiModel] 当前模型 {provider_id} 已删除，请用户重新选择")
        self._save_config()
        return {'success': True}

    def enable_model(self, provider_id, api_key=None, base_url=None, model_name=None, skip_test=False):
        """启用模型并测试连接，若测试失败则不保存。
        
        Args:
            skip_test: True 表示跳过连接测试（已在调用前测试过）
        """
        all_providers = self._get_all_providers()
        if provider_id not in all_providers:
            return {'success': False, 'error': f'Unknown provider: {provider_id}'}

        provider = all_providers[provider_id]

        # 需要 key 的云端模型，才强制要求 api_key
        if provider['needs_key'] and not api_key:
            return {'success': False, 'error': 'API key required for this provider'}

        # 先测试连接，确认 API 密钥有效且网络可达（除非 skip_test=True）
        if provider['needs_key'] and not skip_test:
            test_result = self.test_connection(
                provider_id, api_key=api_key, base_url=base_url or provider['base_url']
            )
            if not test_result.get('success'):
                return {'success': False, 'error': f'连接测试失败: {test_result.get("error", "未知错误")}'}

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
        # ★ #1275 节点⑦（2026-10-29）阵营分槽守卫：本地模型（ollama/harveneye）
        #   绝不入外脑槽——本地模型改记 local 槽 + last_query_mode='local'
        #   （只纠正记录归属，不代选不拦截；外脑槽记本地模型=拧巴状态根源）
        if provider_id in ('ollama', 'harveneye'):
            try:
                from src.utils.config import get_config
                _cfg = get_config()
                _cfg['current_model'] = provider_id
                _slots = _cfg.setdefault('current_model_by_mode', {})
                _slots['local'] = provider_id
                _cfg['last_query_mode'] = 'local'
                _cfg.save()
                logger.info(f"[MultiModel] 本地模型 {provider_id} 已记入 local 槽（不入外脑槽）")
            except Exception as _e:
                logger.debug(f"[MultiModel] 本地槽记录失败（不影响本地切换）: {_e}")
            self.current_model = provider_id
            self.config['current_model'] = provider_id
            self._save_config()
            # ★ #1322 选择即换血：用户点了 HarvenEye/Ollama，当场预载进显存（开发者 10-30 拍板）
            try:
                from src.core import local_model_swap
                _target = provider_id
                if provider_id == 'ollama':
                    # ollama 阵营：具体模型名取用户配置的 default_model（不写死）
                    from src.utils.config import get_config as _gc2
                    _target = str((_gc2() or {}).get('default_model', '') or '').strip()
                if _target:
                    local_model_swap.swap_in_async(_target, reason='set_current_model')
            except Exception as _e:
                logger.debug(f"[MultiModel] 模型预载触发失败（不影响切换）: {_e}")
            return {'success': True}
        if provider_id not in all_providers:
            return {'success': False, 'error': f'Unknown provider: {provider_id}'}

        # ★ 2026-08-09: 自动启用模型（无需手动enable，config.yaml有key即可切换）
        if provider_id != 'ollama' and all_providers[provider_id]['needs_key']:
            if not self.config.get('models', {}).get(provider_id, {}).get('enabled'):
                # 自动启用
                self.config.setdefault('models', {})[provider_id] = {
                    'enabled': True,
                    'api_key': all_providers[provider_id].get('api_key', ''),
                    'base_url': all_providers[provider_id].get('base_url', ''),
                    'model_name': '',
                    'enabled_at': time.time(),
                }

        self.current_model = provider_id
        self.config['current_model'] = provider_id
        self._save_config()
        # ★ 2026-08-09: 同步更新 config.yaml 的 collab_foreign_model
        self._sync_config_yaml_foreign(provider_id)
        # ★ 2026-10-24 方案A 分槽记忆：外脑选择同步镜像到 config.yaml 的 current_model
        #   （此前只写本 json——本地「设为当前」只写 yaml，两持久层互踩：重启后引擎按 json 判定
        #   与用户在模型中心看到的状态对不上，只能重开模型中心重选一次才恢复）
        #   并记入 by_mode.cloud 槽 + last_query_mode，供切换模式时自动换回各自上次选的模型
        try:
            from src.utils.config import get_config
            _cfg = get_config()
            _cfg['current_model'] = provider_id
            _slots = _cfg.setdefault('current_model_by_mode', {})
            _slots['cloud'] = provider_id
            _cfg['last_query_mode'] = 'cloud'
            _cfg.save()
        except Exception as _e:
            logger.debug(f"[MultiModel] yaml 镜像/分槽记忆写入失败（不影响外脑切换）: {_e}")
        # ★ #1327（2026-10-30）：外脑分支补 json 槽位镜像——此前只有本地方向镜像，
        #   外脑切换后 json 的 slots.cloud/last_query_mode 长期陈旧，读 json 的
        #   展示/判定全被带偏（#1326「设了 deepseek 还显示 glm」实锤）
        try:
            self.config.setdefault('current_model_by_mode', {})['cloud'] = provider_id
            self.config['last_query_mode'] = 'cloud'
            self._save_config()
        except Exception as _e:
            logger.debug(f"[MultiModel] json 槽位镜像写入失败（不影响外脑切换）: {_e}")
        return {'success': True}

    def _sync_config_yaml_foreign(self, provider_id):
        """同步外脑模型到 config.yaml"""
        try:
            cfg = yaml.safe_load(_config_path.read_text(encoding='utf-8')) or {}
            cfg['collab_foreign_model'] = provider_id if provider_id != 'ollama' else 'deepseek'
            with open(_config_path, 'w', encoding='utf-8') as f:
                yaml.dump(dict(cfg), f, allow_unicode=True, default_flow_style=False)
        except Exception:
            pass

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
            return {'success': False, 'error': f'{provider_id} API Key未配置。请去 设置→模型管理→填入Key'}

        try:
            api_key = effective_key
            base_url = effective_url
            model_name = model_entry.get('model_name', provider.get('models', [''])[0] if provider.get('models') else '')
            if not isinstance(model_name, str) or not model_name.strip():  # ★ 2026-08-25 类型守护
                logger.warning(f"[MultiModel] {provider_id} 模型名非法({type(model_name).__name__})，回退首个可用")
                _mlist = provider.get('models') or ['']
                model_name = _mlist[0] if _mlist and isinstance(_mlist[0], str) else ''

            # Gemini uses different API format
            if provider.get('is_gemini'):
                url = f"{base_url}/models/{model_name}:generateContent"
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": "test"}]}],
                    "generationConfig": {"maxOutputTokens": 5},
                }
                params = {"key": api_key}
                with bypass_independence():
                    resp = requests.post(url, params=params, json=payload, timeout=(10, 15))
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
            with bypass_independence():
                resp = requests.post(chat_url, headers=headers, json=payload, timeout=(10, 15))

            if resp.status_code in [200, 201]:
                return {'success': True}
            elif resp.status_code == 401:
                return {'success': False, 'error': 'API 密钥无效（401 Unauthorized）'}
            elif resp.status_code == 403:
                return {'success': False, 'error': 'API 密钥无权访问（403 Forbidden）'}
            elif resp.status_code == 404:
                return {'success': False, 'error': f'接口地址不正确(404)：{base_url} 可能不支持 /chat/completions，请检查 base_url 是否正确'}
            else:
                # 如果 /chat/completions 失败，尝试 /models 作为备用
                test_url = f"{base_url}/models"
                try:
                    with bypass_independence():
                        resp2 = requests.get(test_url, headers=headers, timeout=(8, 10))
                    if resp2.status_code == 200:
                        return {'success': True, 'warning': '端点可访问，API 密钥未验证'}
                except requests.exceptions.ConnectTimeout as _e:
                    logger.debug(f"[multi_model_manager] 忽略: {_e}")  # 备用端点也不通，忽略
                except Exception as _e: logger.debug(f"[multi_model_manager] 忽略: {_e}")
                return {'success': False, 'error': f'HTTP {resp.status_code}: {resp.text[:200]}'}
        except requests.exceptions.ConnectTimeout:
            return {'success': False, 'error': f'连接超时(10s)：{base_url} 无法访问，请检查网络或 base_url 地址'}
        except requests.exceptions.ConnectionError as ce:
            return {'success': False, 'error': f'连接失败：{ce}'}
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

    # ═══════════════════════════════════════════════════════════════════
    # 生成方法
    # ═══════════════════════════════════════════════════════════════════

    def _generate_ollama(self, messages, stream=True):
        """Generate using Ollama - delegates to the existing ModelManager."""
        try:
            from src.core.model_manager import ModelManager
            base_url = _config_yaml.get('ollama', {}).get('base_url', 'http://localhost:11434')
            model_name = _config_yaml.get('ollama', {}).get('default_model', '') or ''  # ★ 2026-09-26 审计：不内定，空则 ModelManager 报"未选择模型"
            if not isinstance(model_name, str) or not model_name.strip(): model_name = 'qwen2.5:14b'  # ★ 2026-08-25 类型守护
            mm = ModelManager(base_url, model_name)
            response = mm.chat(messages, stream=stream, temperature=0.7)
            if stream:
                for chunk in response:
                    yield {'type': 'text', 'content': chunk}
            else:
                yield {'type': 'text', 'content': response}
        except Exception as e:
            yield {'type': 'error', 'content': f'Ollama generation failed: {e}'}

    def _friendly_http_error(self, provider_id, status, text):
        """★ 2026-08-26: HTTP 错误翻译成人话+行动引导（失败要告诉用户怎么办）"""
        try:
            _pname = self._get_all_providers().get(provider_id, {}).get('name', provider_id)
        except Exception:
            _pname = provider_id
        _raw = (text or '')[:150]
        if status == 402:
            return f"HTTP 402 余额不足 —— {_pname}账户欠费。→ 请到该平台官网充值，或在 设置→模型管理 切换其他外脑，或点 🏠本地 用本地模型（免费）"
        if status == 401:
            return f"HTTP 401 Key 无效 —— {_pname}的 API Key 不正确或已过期。→ 请到 设置→模型管理 检查 Key，或点 🏠本地 用本地模型"
        if status == 429:
            return f"HTTP 429 请求过频 —— {_pname}限流中（通常几十秒自动恢复）。→ 请等 30 秒再发，或点 🏠本地 用本地模型（不限流）"
        if status == 404:
            return f"HTTP 404 模型不存在 —— 模型名可能已改名或下线。→ 请到 设置→模型管理 检查模型名，或切换其他模型"
        if status >= 500:
            return f"HTTP {status} 服务商故障 —— {_pname}服务器异常。→ 请稍后重试，或切换其他模型 / 🏠本地"
        return f"HTTP {status}: {_raw}"

    def _generate_openai_compatible(self, provider_id, messages, stream=True, tools=None):
        """Generate using OpenAI-compatible API with connection retry.

        ★ 2026-07-11: 新增 tools 参数（Function Calling支持）
        """
        import time as _time

        # ★ 2026-08-26 修复: _show_think 定义随 thinking 参数块被删而丢失，
        # 导致 NameError 让所有外脑全挂。思考过程默认不转发（原 P3 行为）。
        _show_think = False

        all_providers = self._get_all_providers()
        provider = all_providers.get(provider_id, PROVIDERS.get(provider_id, {}))
        model_entry = self.config.get('models', {}).get(provider_id, {})

        api_key = model_entry.get('api_key', '')
        # ★ 三层回退：multi_models.json → config.yaml（统一数据源）
        if not api_key:
            api_key = self.config.get('model_keys', {}).get(provider_id, '')
        if not api_key:
            try:
                cfg = yaml.safe_load(_config_path.read_text(encoding='utf-8')) or {}
                yaml_keys = cfg.get('model_keys', {})
                api_key = yaml_keys.get(provider_id, '')
            except Exception as _e: logger.debug(f"[multi_model_manager] 忽略: {_e}")
        base_url = model_entry.get('base_url', provider['base_url'])
        model_name = model_entry.get('model_name', provider.get('models', [''])[0] if provider.get('models') else '')
        if not isinstance(model_name, str) or not model_name.strip():  # ★ 2026-08-25 类型守护
            logger.warning(f"[MultiModel] {provider_id} 模型名非法({type(model_name).__name__})，回退首个可用")
            _mlist = provider.get('models') or ['']
            model_name = _mlist[0] if _mlist and isinstance(_mlist[0], str) else ''

        if not api_key:
            yield {'type': 'error', 'content': f'{provider_id} API key not configured'}
            return

        headers = self._build_auth_headers(provider_id, api_key, provider)
        chat_url = f"{base_url}/chat/completions"
        
        # DEBUG: 确认实际请求目标
        logger.info(f"[MultiModel] 请求: {provider_id} → {chat_url}")

        payload = {
            'model': model_name,
            'messages': messages,
            'stream': stream,
            # ★ 2026-09-27 提速（用户反馈回答太慢）：不设 max_tokens 时服务端默认很小
            # （GLM 默认 1024），思考型模型+主聊天长输入首战必空 → 空流重试 2 轮 = 3 倍等待。
            # max_tokens 只是上限不按它计费，首战直接给 4096 一次出正文
            'max_tokens': 4096,
        }
        # ★ 2026-08-26 修正: 删除 thinking 参数注入——实测 glm-5.3 拒绝 disabled
        # (HTTP 400 "该模型始终思考，不支持关闭思考")，发了必挂。
        # 思考治理改为仅靠 reasoning 不转发显示（见下方流式处理）。
        # ★ 2026-10-21 慢回答真凶实锤（开发者「做生蚝面」33~36s 案例）：
        #   glm-5.3-Flash 默认深度思考 2270 tokens（占总耗时 80%+），裸调 47.9s/9字每秒。
        #   正确参数是 reasoning_effort（智谱官方 low/high/max 三档；thinking.type 仍 400）：
        #   实测 low 档 10.5s/47字每秒 = 快 4.5 倍，思考 tokens 归零。
        #   默认 low（日常问答/菜谱/工具选择足够）；config.yaml 可配 zhipu_reasoning_effort: high 调回。
        if provider_id == 'zhipu':
            try:
                _effort = 'low'
                _cfgp = Path(__file__).resolve().parent.parent / 'config.yaml'
                if _cfgp.exists():
                    import yaml as _yaml
                    _zcfg = (_yaml.safe_load(_cfgp.read_text(encoding='utf-8')) or {}).get('zhipu_reasoning_effort')
                    if _zcfg in ('low', 'high', 'max'):
                        _effort = _zcfg
                payload['reasoning_effort'] = _effort
                logger.info(f"[MultiModel] zhipu reasoning_effort={_effort}")
            except Exception:
                payload['reasoning_effort'] = 'low'
        # ★ 2026-08-27 修复: qwen 思考型模型（qwen3.7 等）流式输出可能全程只有
        # reasoning_content 而 content 为空 -> 哈文转发空流 -> 前端报"无响应"。
        # DashScope 支持 enable_thinking 参数且全系列兼容（非思考模型自动忽略）。
        if provider_id == 'qwen':
            payload['enable_thinking'] = False

        # ★ 2026-07-11: Function Calling — 添加 tools 参数
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = 'auto'
            logger.info(f"[MultiModel] Function Calling 已启用, 工具数: {len(tools)}")

        # 重试配置：最多重试2次（首次 + 2次重试 = 最多3次请求）
        max_retries = 2
        last_error = None
        
        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait = min(2 ** attempt, 8)  # 2s, 4s backoff
                logger.info(f"[MultiModel] {provider_id} 重试第{attempt}次 (等待{wait}s)")
                yield {'type': 'text', 'content': f'\n\n_[{provider_id} 重试第{attempt}次]_\n\n'}
                _time.sleep(wait)

            try:
                with bypass_independence():
                    if stream:
                        resp = requests.post(
                            chat_url, headers=headers, json=payload,
                            stream=True, timeout=(10, BRAIN_READ_TIMEOUT)  # #1507: connect=10s, read=默认240s(config brain_read_timeout)
                        )
                        if resp.status_code == 429:
                            last_error = f'HTTP 429 请求过频 —— 模型限流中（通常几十秒自动恢复）。→ 请等 30 秒再发，或点 🏠本地 用本地模型（不限流）'
                            continue  # 重试
                        if resp.status_code == 502 or resp.status_code == 503:
                            last_error = f'服务暂不可用({resp.status_code})，等待重试'
                            continue  # 重试
                        if resp.status_code != 200:
                            yield {'type': 'error', 'content': self._friendly_http_error(provider_id, resp.status_code, resp.text)}
                            return
                        # DEBUG: 确认实际收到的响应
                        first_content = ""
                        _yielded_any_content = False  # 2026-08-27: 空流检测
                        _saw_reasoning = False        # ★ 2026-09-27: 思考内容检测
                        _finish = ''                  # ★ 2026-09-27: 结束原因检测
                        _tc_streams = {}              # ★ #1328: 流式 tool_calls 分片累积（index→槽）
                        for line in resp.iter_lines():
                            if line:
                                line = line.decode('utf-8')
                                if line.startswith('data: '):
                                    data = line[6:]
                                    if data.strip() == '[DONE]':
                                        break
                                    try:
                                        chunk = json.loads(data)
                                        _ch0 = (chunk.get('choices') or [{}])[0]
                                        delta = _ch0.get('delta') or {}
                                        if _ch0.get('finish_reason'):
                                            _finish = _ch0['finish_reason']
                                        content = delta.get('content', '')
                                        reasoning = delta.get('reasoning_content', '')
                                        if reasoning:
                                            _saw_reasoning = True
                                        # ★ 2026-08-25 P3: 尊重 show_thinking 配置——默认不展示（省 token 后通常也无思考）
                                        if reasoning and _show_think:
                                            yield {'type': 'thinking', 'content': reasoning}
                                        # ★ #1328（2026-10-30）流式 tool_calls 收集：流式分支原先根本
                                        #   不收集 delta.tool_calls——模型带工具调用的流整条无正文，
                                        #   被空流重试白烧 2 次（Ollama 搬家任务实锤 finish=tool_calls
                                        #   →「模型未返回正文」）。id/首片带名，arguments 跨分片拼接。
                                        for _tcf in (delta.get('tool_calls') or []):
                                            _tix = _tcf.get('index', 0)
                                            _tslot = _tc_streams.setdefault(_tix, {
                                                'id': '', 'type': 'function',
                                                'function': {'name': '', 'arguments': ''}})
                                            if _tcf.get('id'):
                                                _tslot['id'] = _tcf['id']
                                            _tfn = _tcf.get('function') or {}
                                            if _tfn.get('name'):
                                                _tslot['function']['name'] = _tfn['name']
                                            if _tfn.get('arguments'):
                                                _tslot['function']['arguments'] += _tfn['arguments']
                                        if content:
                                            if not first_content:
                                                first_content = content
                                                logger.info(f"[MultiModel] {provider_id} 首字: {content[:50]}")
                                            yield {'type': 'text', 'content': content}
                                            _yielded_any_content = True
                                    except (json.JSONDecodeError, KeyError, IndexError):
                                        pass
                        # 成功到达这里 → 返回
                        logger.info(f"[MultiModel] {provider_id} 流式响应完成")
                        # ★ #1328：有工具调用必须透传回 FC 循环继续执行，绝不进空流重试
                        if _tc_streams:
                            _tcs_out = [_tc_streams[k] for k in sorted(_tc_streams)]
                            logger.info(f"[MultiModel] {provider_id} 流式返回 tool_calls: {len(_tcs_out)}个（finish={_finish or '无'}）")
                            yield {'type': 'tool_calls', 'content': _tcs_out}
                            return
                        if not _yielded_any_content:
                            # ★ 2026-09-27 修空回复 v2：放宽重试条件——实测除「思考吃光额度
                            # (finish=length+有 reasoning)」外，还有「思考完正文仍空但 finish=stop」
                            # 和「断流没带 finish」两种漏网（美团查询实锤，两次必现）。
                            # 只要全流无正文且还有重试额度，一律加大 max_tokens 重试，不再挑 finish 原因。
                            if attempt < max_retries:
                                payload['max_tokens'] = min(int(payload.get('max_tokens') or 1024) * 3, 8192)
                                logger.info(f"[MultiModel] {provider_id} 空流(finish={_finish or '无'},思考={'有' if _saw_reasoning else '无'})，加大到 {payload['max_tokens']} 重试")
                                continue
                            logger.warning(f"[MultiModel] {provider_id} 空流兜底失败: finish={_finish or '无'} saw_reasoning={_saw_reasoning} max_tokens={payload.get('max_tokens')}")
                            # ★ 2026-08-27: 全流无 content（如思考型模型思考截断）——给出明确提示
                            yield {'type': 'error', 'content': '模型未返回正文（可能为思考型模型思考被截断或服务端异常）。→ 请重试，或切换非思考模型（如 qwen-plus / glm-4-flash）'}
                        return
                    else:
                        resp = requests.post(
                            chat_url, headers=headers, json=payload,
                            timeout=(10, BRAIN_READ_TIMEOUT)  # #1507: connect=10s, read=默认240s(config brain_read_timeout)
                        )
                        if resp.status_code == 429:
                            last_error = f'HTTP 429 请求过频 —— 模型限流中（通常几十秒自动恢复）。→ 请等 30 秒再发，或点 🏠本地 用本地模型（不限流）'
                            continue
                        if resp.status_code == 502 or resp.status_code == 503:
                            last_error = f'服务暂不可用({resp.status_code})，等待重试'
                            continue
                        if resp.status_code != 200:
                            yield {'type': 'error', 'content': self._friendly_http_error(provider_id, resp.status_code, resp.text)}
                            return
                        result = resp.json()
                        msg = result['choices'][0]['message']
                        content = msg.get('content', '') or msg.get('reasoning_content', '')

                        # ★ 2026-07-11: Function Calling — 检测 tool_calls
                        tool_calls = msg.get('tool_calls')
                        if tool_calls:
                            logger.info(f"[MultiModel] {provider_id} 返回 tool_calls: {len(tool_calls)}个")
                            yield {'type': 'tool_calls', 'content': tool_calls}
                            if content:
                                yield {'type': 'text', 'content': content}
                            return

                        yield {'type': 'text', 'content': content}
                        return
            except requests.exceptions.ConnectTimeout:
                last_error = f'连接超时(10s)，{provider_id} 服务器无响应'
                continue
            except requests.exceptions.ReadTimeout:
                last_error = f'读取超时(120s)，{provider_id} 响应过慢'
                continue
            except requests.exceptions.ConnectionError as ce:
                last_error = f'连接失败: {ce}'
                continue
            except Exception as e:
                yield {'type': 'error', 'content': str(e)}
                return

        # 所有重试耗尽
        yield {'type': 'error', 'content': last_error or f'{provider_id} 请求失败（已重试{max_retries}次）'}

    def _generate_gemini(self, messages, stream=True):
        """Generate using Google Gemini API (non-OpenAI-compatible)."""
        import time as _time
        
        model_entry = self.config.get('models', {}).get('gemini', {})
        api_key = model_entry.get('api_key', '')
        base_url = model_entry.get('base_url', 'https://generativelanguage.googleapis.com/v1beta')
        model_name = model_entry.get('model_name', 'gemini-2.5-flash')

        if not api_key:
            yield {'type': 'error', 'content': 'Gemini API key not configured'}
            return

        # Convert OpenAI format messages to Gemini format
        # ★ 2026-09-24 看图路由：支持 OpenAI 视觉消息（content 为 list，含 image_url dataURL）
        gemini_contents = []
        system_instruction = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = content if isinstance(content, str) else ""
            elif role == "user":
                parts = []
                if isinstance(content, str):
                    parts = [{"text": content}]
                elif isinstance(content, list):
                    for _c in content:
                        if not isinstance(_c, dict):
                            continue
                        if _c.get("type") == "text":
                            parts.append({"text": _c.get("text", "")})
                        elif _c.get("type") == "image_url":
                            _url = (_c.get("image_url", {}) or {}).get("url", "")
                            _m = re.match(r'^data:([^;]+);base64,(.+)$', _url, re.S)
                            if _m:
                                parts.append({"inline_data": {"mime_type": _m.group(1), "data": _m.group(2)}})
                        elif _c.get("type") == "file":
                            # ★ 2026-09-26 文件直发：OpenAI file part → Gemini inline_data
                            _fobj = _c.get("file", {}) or {}
                            _fd = _fobj.get("file_data", "") or ""
                            _fm = re.match(r'^data:([^;]+);base64,(.+)$', _fd, re.S)
                            if _fm:
                                parts.append({"inline_data": {"mime_type": _fm.group(1), "data": _fm.group(2)}})
                gemini_contents.append({"role": "user", "parts": parts or [{"text": ""}]})
            elif role == "assistant":
                gemini_contents.append({"role": "model", "parts": [{"text": content if isinstance(content, str) else ""}]})

        url = f"{base_url}/models/{model_name}:{'streamGenerateContent' if stream else 'generateContent'}"
        params = {"key": api_key}
        payload = {
            "contents": gemini_contents,
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        max_retries = 2
        last_error = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait = min(2 ** attempt, 8)
                logger.info(f"[MultiModel] Gemini 重试第{attempt}次 (等待{wait}s)")
                _time.sleep(wait)

            try:
                if stream:
                    payload.setdefault("generationConfig", {})["temperature"] = 0.7
                    with bypass_independence():
                        resp = requests.post(
                            url, params=params, json=payload,
                            stream=True, timeout=(10, BRAIN_READ_TIMEOUT)  # #1507: read=默认240s
                        )
                    if resp.status_code == 429:
                        last_error = 'Gemini 请求过频(429)，需要冷却'
                        continue
                    if resp.status_code in (502, 503):
                        last_error = f'Gemini 服务暂不可用({resp.status_code})，等待重试'
                        continue
                    if resp.status_code != 200:
                        yield {'type': 'error', 'content': f'Gemini HTTP {resp.status_code}: {resp.text[:400]}'}
                        return

                    # Gemini streamGenerateContent 返回 SSE 格式: data: {...}\n\n
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode('utf-8', errors='replace').strip()
                        if not decoded:
                            continue
                        # 跳过标记行
                        if decoded == '[' or decoded == ']' or decoded == ',':
                            continue
                        # SSE: data: {...}
                        if decoded.startswith('data: '):
                            decoded = decoded[6:]
                        if not decoded:
                            continue
                        try:
                            chunk = json.loads(decoded)
                            candidates = chunk.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                for part in parts:
                                    if "text" in part:
                                        yield {'type': 'text', 'content': part["text"]}
                        except json.JSONDecodeError as _e:
                            logger.debug(f"[multi_model_manager] 忽略: {_e}")
                    return
                else:
                    with bypass_independence():
                        resp = requests.post(
                            url, params=params, json=payload,
                            timeout=(10, BRAIN_READ_TIMEOUT)  # #1507: read=默认240s
                        )
                    if resp.status_code == 429:
                        last_error = 'Gemini 请求过频(429)，需要冷却'
                        continue
                    if resp.status_code in (502, 503):
                        last_error = f'Gemini 服务暂不可用({resp.status_code})，等待重试'
                        continue
                    if resp.status_code != 200:
                        yield {'type': 'error', 'content': f'Gemini HTTP {resp.status_code}: {resp.text[:400]}'}
                        return
                    result = resp.json()
                    candidates = result.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts)
                        yield {'type': 'text', 'content': text}
                    else:
                        yield {'type': 'error', 'content': f'Gemini 无candidates: {json.dumps(result, ensure_ascii=False)[:300]}'}
                    return
            except requests.exceptions.ConnectTimeout:
                last_error = 'Gemini 连接超时(10s)'
                continue
            except requests.exceptions.ReadTimeout:
                last_error = 'Gemini 读取超时(120s)'
                continue
            except requests.exceptions.ConnectionError as ce:
                last_error = f'Gemini 连接失败: {ce}'
                continue
            except Exception as e:
                yield {'type': 'error', 'content': f'Gemini API error: {e}'}
                return

        yield {'type': 'error', 'content': last_error or f'Gemini 请求失败（已重试{max_retries}次）'}

    # ═══════════════════════════════════════════════════════════════════
    # 统一生成入口
    # ═══════════════════════════════════════════════════════════════════

    def generate_response(self, messages, stream=True, tools=None, model=None):
        """统一生成入口：根据 current_model 路由到对应生成器。

        engine.py 调用：mm.generate_response(all_messages, stream=True)
        自动路由到 _generate_ollama / _generate_openai_compatible / _generate_gemini

        ★ 2026-07-11: 新增 tools 参数（Function Calling支持）
        ★ #1265（2026-10-29）：新增 model 显式参数——调用方指定模型时直接传参，
          不再临时改写 self.current_model（全局换血）。旧做法在流式消费的长窗口内
          让所有并发读者（批改/工具/其他会话）读到临时值，14:02 批改误报实锤。
          self.current_model 语义从此收敛为「用户的持久选择」，只有 set_current_model
          / _apply_local_model 两条持久化链写它。
        """
        provider_id = model if model else self.current_model
        logger.info(f"[MultiModel] generate_response 路由 → {provider_id}, tools={'有' if tools else '无'}")

        # ★ #1241（2026-10-27）：HarvenEye 是本地混合模型引擎，不是外脑——
        #   落到 openai_compatible 会拿 PROVIDERS['harveneye'] 撞 KeyError 'base_url'（实锤）。
        # ★ 2026-11-14（15:53 Agent 三连拒实锤）：原实现直接报错拒答，用户消息石沉大海。
        #   改为「选谁用谁」的正确姿势：委托 Eye 本地对话通道真跑（本地引擎本地跑），
        #   通道不可用时才给人话指路（方案 A 兜底），绝不偷偷换模型。
        if provider_id == 'harveneye':
            logger.info("[MultiModel] harveneye → 委托 Eye 本地对话通道（本地引擎本地跑）")
            from src.core import eye_client as _eye
            if tools:
                logger.warning("[MultiModel] harveneye 本地通道不支持 Function Calling，tools 已忽略")

            def _eye_gen():
                try:
                    if stream:
                        stream_gen = _eye._inproc_chat_stream(messages)
                        if stream_gen is not None:
                            got = False
                            for piece in stream_gen:
                                if piece:
                                    got = True
                                    yield {'type': 'text', 'content': piece}
                            if got:
                                return
                        # 流式通道不可用/无产出 → 非流式兜底
                        ok, content = _eye.chat(messages)
                        if ok and content:
                            yield {'type': 'text', 'content': content}
                        else:
                            yield {'type': 'error', 'content':
                                   f'HarvenEye 本地通道未就绪（{content or "无响应"}）。'
                                   '请到设置页检查 HarvenEye 视觉模型，或点对话区 🏠本地 切 Ollama 对话'}
                    else:
                        ok, content = _eye.chat(messages)
                        if ok and content:
                            yield {'type': 'text', 'content': content}
                        else:
                            yield {'type': 'error', 'content':
                                   f'HarvenEye 本地通道未就绪（{content or "无响应"}）。'
                                   '请到设置页检查 HarvenEye 视觉模型，或点对话区 🏠本地 切 Ollama 对话'}
                except Exception as e:
                    logger.error(f"[MultiModel] harveneye 本地通道失败: {e}", exc_info=True)
                    yield {'type': 'error', 'content':
                           f'HarvenEye 本地对话失败: {e}。可点对话区 🏠本地 切 Ollama 对话'}
            return _eye_gen()

        if provider_id == 'ollama':
            return self._generate_ollama(messages, stream=stream)
        elif provider_id == 'gemini':
            return self._generate_gemini(messages, stream=stream)
        else:
            return self._generate_openai_compatible(provider_id, messages, stream=stream, tools=tools)


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
