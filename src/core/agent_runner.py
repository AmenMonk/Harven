# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — 可扩展 Agent 执行器（Extensible Agent Runner）

用途：让哈文能运行任何本地 AI 模型，不限于 Ollama 支持的模型

工作方式：
1. 用户在 Agent 面板注册一个「自定义脚本型 Agent」
2. 脚本型 Agent 的 model_pref = "script"
3. Agent 注册时指定 script_path（Python 文件路径）
4. 路由到此 Agent 时，不调 Ollama/API，直接执行脚本
5. 脚本接收用户消息，返回生成结果

脚本模板（任意放在 skills/ 目录下的 .py 文件）：
```python
# skills/my_music_agent.py
def generate(user_message: str, params: dict) -> dict:
    '''
    params: Agent 配置参数
    Returns: {'success': True/False, 'output': str, 'file': str/None}
    '''
    # 你的模型调用逻辑
    from some_model import generate_music
    result_path = generate_music(prompt=user_message)
    return {'success': True, 'output': f'音乐已生成: {result_path}', 'file': result_path}
```

这样任何不在 Ollama 里的模型（音乐、语音、图像、视频）都能接入哈文。
"""

import os
import sys
import json
import logging
import importlib.util
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"

def load_script_agent(script_name: str) -> Optional[object]:
    """从 skills/ 目录加载一个脚本型 Agent"""
    script_path = SKILLS_DIR / f"{script_name}.py"
    if not script_path.exists():
        script_path = Path(script_name)
        if not script_path.exists():
            logger.error(f"[AgentRunner] 脚本不存在: {script_name}")
            return None
    
    try:
        spec = importlib.util.spec_from_file_location(script_name, script_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[script_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        logger.error(f"[AgentRunner] 加载脚本失败: {e}")
        return None

def run_script_agent(script_name: str, user_message: str, agent_params: dict = None) -> dict:
    """运行脚本型 Agent
    
    Returns: {'success': bool, 'output': str, 'file': str, 'error': str}
    """
    module = load_script_agent(script_name)
    if not module:
        return {'success': False, 'error': f'脚本 {script_name} 加载失败'}
    
    if not hasattr(module, 'generate'):
        return {'success': False, 'error': f'脚本缺少 generate() 函数'}
    
    try:
        params = agent_params or {}
        result = module.generate(user_message, params)
        if isinstance(result, str):
            return {'success': True, 'output': result}
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}


def install_pip_package(package_name: str) -> bool:
    """安装 Python 包（供 Agent 脚本使用）"""
    try:
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', package_name],
            capture_output=True, text=True, timeout=300
        )
        return True
    except Exception:
        return False


# ═══════════════════════════════════════
# 预置脚本 Agent 模板
# ═══════════════════════════════════════

AGENT_TEMPLATE = '''# {name} — Harven 脚本型 Agent
# 自动生成模板，修改 generate() 函数即可

def generate(user_message: str, params: dict) -> dict:
    """
    接收用户消息，返回生成结果
    
    Args:
        user_message: 用户的原始消息
        params: Agent 配置（model_pref, skills 等）
    
    Returns:
        dict: {{'success': True/False, 'output': str, 'file': str/None}}
    """
    # TODO: 在这里写你的模型调用逻辑
    # 示例：
    # from your_model import generate_content
    # result_path = generate_content(prompt=user_message)
    # return {{'success': True, 'output': '生成了: ' + result_path, 'file': result_path}}
    
    return {{
        'success': False,
        'error': '未实现。请编辑 generate() 函数添加模型调用逻辑。\n'
                 '参考: H:\\\\HarvenAI\\\\skills\\\\agent_template.py'
    }}
'''
