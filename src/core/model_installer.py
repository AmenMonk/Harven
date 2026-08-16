# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — 通用模型安装器（Universal Model Installer）

解决的问题：
  Ollama 只有文字 LLM，但用户需要音乐/语音/图像/视频等模型
  每种模型的安装方式不同（有的要平台、有的要 pip、有的要下载权重）

安装模式（5 种）：
  pip      → pip install xxx（Python 包，如 audiocraft、coqui-tts）
  huggingface → 下载 HF 权重文件到本地
  exe      → 下载并安装可执行文件（如某些独立工具）
  git      → git clone 仓库
  docker   → docker pull 镜像

用户只需说"我要音乐模型"→ 哈文检测 → 展示安装选项 → 一键安装
"""

import os
import sys
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 已知模型列表（持续扩充）
MODEL_CATALOG = {
    # ═══ 音乐类（新增）═══
    "ace_step": {
        "name": "ACE-Step 1.5 (音乐生成)",
        "category": "音乐",
        "install": {
            "type": "portable", 
            "repo": "https://github.com/ACE-Step/ACE-Step-1.5.git",
            "windows_url": "https://files.acemusic.ai/acemusic/win/ACE-Step-1.5.7z",
            "desc": "ACE Studio + 阶跃星辰 · 开源音乐生成"
        },
        "model_size": "~5GB (2B turbo + 1.7B LM)",
        "requirements": {"ram": "16GB", "gpu": "推荐 8GB+", "disk": "15GB"},
        "script_agent": "ace_step_agent",
        "extra_info": "安装后运行 start_api_server.bat 启动 REST API（端口 8001），哈文自动连接。\nMIT 许可证，可商用。\n支持中文提示词、国风、五声音阶。",
    },
    "musicgen": {
        "name": "MusicGen (音乐生成)",
        "category": "音乐",
        "install": {"type": "pip", "package": "audiocraft", "desc": "Meta AudioCraft 音乐生成"},
        "model_size": "1.5GB",
        "requirements": {"ram": "8GB", "gpu": "推荐", "disk": "3GB"},
        "script_agent": "music_agent",
    },
    "coqui_tts": {
        "name": "Coqui TTS (语音合成)",
        "category": "语音",
        "install": {"type": "pip", "package": "TTS", "desc": "Coqui AI 文本转语音"},
        "model_size": "~500MB (单模型)",
        "requirements": {"ram": "4GB", "gpu": "可选", "disk": "2GB"},
        "script_agent": "tts_agent",
    },
    "whisper": {
        "name": "Whisper (语音识别)",
        "category": "语音",
        "install": {"type": "pip", "package": "openai-whisper", "desc": "OpenAI Whisper 语音转文字"},
        "model_size": "~1.5GB (large)",
        "requirements": {"ram": "4GB", "gpu": "可选", "disk": "3GB"},
        "script_agent": "whisper_agent",
    },
    # ═══ 图像类 ═══
    "stable_diffusion": {
        "name": "Stable Diffusion (图像生成)",
        "category": "图像",
        "install": {"type": "pip", "package": "diffusers", "desc": "HuggingFace Diffusers + SD 模型"},
        "model_size": "~5GB (SD 1.5)",
        "requirements": {"ram": "8GB", "gpu": "需要", "disk": "10GB"},
        "script_agent": "sd_agent",
    },
    # ═══ 视频类 ═══
    "stable_video_diffusion": {
        "name": "SVD (视频生成)",
        "category": "视频",
        "install": {"type": "pip", "package": "diffusers[torch]", "desc": "Stable Video Diffusion"},
        "model_size": "~5GB",
        "requirements": {"ram": "16GB", "gpu": "需要", "disk": "15GB"},
        "script_agent": "svd_agent",
    },
}

def get_catalog() -> dict:
    """获取所有可安装的模型"""
    return MODEL_CATALOG

def install_model(model_id: str, progress_callback=None) -> dict:
    """一键安装模型
    
    Args:
        model_id: 模型 ID（如 'musicgen', 'coqui_tts'）
        progress_callback: 可选回调，接收 (stage, message)
    
    Returns:
        {'success': bool, 'message': str, 'next_step': str}
    """
    if model_id not in MODEL_CATALOG:
        return {'success': False, 'message': f'未知模型: {model_id}\n可用: {", ".join(MODEL_CATALOG.keys())}'}
    
    info = MODEL_CATALOG[model_id]
    install = info['install']
    
    if progress_callback:
        progress_callback('check', f"检查 {info['name']}...")
    
    # 1. 检查依赖
    if not _check_requirements(info['requirements']):
        return {
            'success': False,
            'message': f"{info['name']} 硬件不满足要求:\n{_format_requirements(info['requirements'])}"
        }
    
    # 2. 安装
    if progress_callback:
        progress_callback('install', f"正在安装 {info['name']}...")
    
    if install['type'] == 'pip':
        result = _pip_install(install['package'], progress_callback)
    elif install['type'] == 'git':
        result = _git_clone(install['repo'], progress_callback)
    elif install['type'] == 'huggingface':
        result = _hf_download(install['model_id'], progress_callback)
    elif install['type'] == 'portable':
        result = _download_portable(model_id, info, progress_callback)
    else:
        return {'success': False, 'message': f'不支持的安装类型: {install["type"]}'}
    
    if not result['success']:
        return result
    
    # 3. 注册到 Agent
    if progress_callback:
        progress_callback('register', '注册到 Agent 系统...')
    
    _register_agent(model_id, info)
    
    return {
        'success': True,
        'message': f'✅ {info["name"]} 安装完成！',
        'next_step': f'在哈文中使用触发词即可调用此模型',
        'agent_id': info.get('script_agent', model_id)
    }


def check_model_installed(model_id: str) -> bool:
    """检查模型是否已安装"""
    if model_id not in MODEL_CATALOG:
        return False
    info = MODEL_CATALOG[model_id]
    if info['install']['type'] == 'pip':
        try:
            pkg = info['install']['package'].split('[')[0]  # 去掉 extras
            __import__(pkg.replace('-', '_'))
            return True
        except ImportError:
            return False
    return False


def _pip_install(package: str, callback=None) -> dict:
    """pip 安装"""
    try:
        venv_python = sys.executable
        cmd = [venv_python, '-m', 'pip', 'install', package, '--quiet']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            # 尝试清华镜像
            cmd.append('-i')
            cmd.append('https://pypi.tuna.tsinghua.edu.cn/simple')
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return {'success': False, 'message': f'pip 安装失败:\n{result.stderr[:500]}'}
        return {'success': True, 'message': f'pip install {package} 完成'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def _git_clone(repo_url, callback=None) -> dict:
    """Git 克隆并安装"""
    # 检查 git
    git_check = subprocess.run(['git', '--version'], capture_output=True)
    if git_check.returncode != 0:
        return {'success': False, 'message': '未安装 Git。\n请先安装: https://git-scm.com/download/win\n\n或下载 ACE-Step 便携包: https://files.acemusic.ai/acemusic/win/ACE-Step-1.5.7z'}
    
    try:
        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        target_dir = Path.home() / '.harven' / 'models' / repo_name
        
        if target_dir.exists():
            return {'success': True, 'message': f'已存在: {target_dir}'}
        
        if callback: callback('install', f"git clone {repo_name}（约1分钟）...")
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', repo_url, str(target_dir)],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            return {'success': False, 'message': f'git clone 失败:\n{result.stderr[:300]}'}
        
        # uv sync
        pyproject = target_dir / 'pyproject.toml'
        if pyproject.exists():
            if callback: callback('install', f"安装依赖（约2-5分钟）...")
            uv_check = subprocess.run(['uv', '--version'], capture_output=True)
            if uv_check.returncode != 0:
                # 尝试安装 uv
                subprocess.run([sys.executable, '-m', 'pip', 'install', 'uv', '--quiet'], timeout=60)
            result2 = subprocess.run(
                [sys.executable, '-m', 'uv', 'sync'], cwd=str(target_dir),
                capture_output=True, text=True, timeout=600
            )
            # uv 可能安装失败，尝试 pip
            if result2.returncode != 0:
                req = target_dir / 'requirements.txt'
                if req.exists():
                    result2 = subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', '-r', str(req), '--quiet'],
                        cwd=str(target_dir), capture_output=True, text=True, timeout=600
                    )
        
        return {
            'success': True,
            'message': f'✅ {repo_name} 安装完成！\n路径: {target_dir}\n\n下一步：\n1. 进入 {target_dir.name} 目录\n2. 双击 start_api_server.bat',
        }
    except subprocess.TimeoutExpired:
        return {'success': False, 'message': '安装超时，请检查网络'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def _download_portable(model_id: str, info: dict, callback=None) -> dict:
    """下载便携包（.7z / .zip）并解压"""
    import platform
    install = info['install']
    url = install.get('windows_url', '')
    if not url and platform.system() == 'Windows':
        url = install.get('windows_url', '')
    
    if not url:
        return {'success': False, 'message': '没有可用的便携包下载链接'}
    
    target_dir = Path.home() / '.harven' / 'models' / model_id
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # 下载
    if callback: callback('install', '下载便携包（约 3-5GB，请耐心等待）...')
    try:
        import urllib.request
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix='.7z', delete=False)
        tmp.close()
        
        urllib.request.urlretrieve(url, tmp.name)
        
        # 解压
        if callback: callback('install', '解压中...')
        try:
            import py7zr
            with py7zr.SevenZipFile(tmp.name, 'r') as archive:
                archive.extractall(target_dir)
        except ImportError:
            # 无 py7zr，用 subprocess 调 7z
            result = subprocess.run(['7z', 'x', tmp.name, f'-o{target_dir}', '-y'], 
                                    capture_output=True, timeout=600)
            if result.returncode != 0:
                # 最后尝试：引导用户手动操作
                return {
                    'success': False,
                    'message': f'解压失败。\n请手动解压到: {target_dir}\n下载文件: {tmp.name}'
                }
        
        os.unlink(tmp.name)
        return {
            'success': True,
            'message': f'✅ {info["name"]} 安装完成！\n路径: {target_dir}\n\n下一步：\n1. 进入 {model_id} 目录\n2. 双击 start_api_server.bat',
        }
    except Exception as e:
        return {'success': False, 'message': f'下载失败: {e}\n\n请手动下载: {url}\n解压到: {target_dir}'}

def _hf_download(model_id, callback=None) -> dict:
    """HuggingFace 下载"""
    try:
        from huggingface_hub import snapshot_download
        target = Path.home() / '.harven' / 'models' / model_id.replace('/', '_')
        target.mkdir(parents=True, exist_ok=True)
        snapshot_download(model_id, local_dir=str(target))
        return {'success': True, 'message': f'模型已下载到 {target}'}
    except ImportError:
        return {'success': False, 'message': '请先: pip install huggingface_hub'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def _check_requirements(reqs: dict) -> bool:
    """检查硬件要求"""
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024**3)
        if 'ram' in reqs:
            min_ram = float(reqs['ram'].replace('GB','').strip())
            if ram_gb < min_ram:
                return False
        return True
    except ImportError:
        return True  # 没有 psutil 就跳过检查


def _format_requirements(reqs: dict) -> str:
    return '\n'.join(f'  - {k}: {v}' for k, v in reqs.items())


def _register_agent(model_id: str, info: dict):
    """安装后自动注册到 Agent 系统"""
    try:
        from src.core.agent_registry import get_registry
        registry = get_registry()
        script_name = info.get('script_agent', model_id)
        
        registry.register(
            agent_id=model_id,
            name=info['name'],
            role=info['category'],
            system_prompt=f"脚本型Agent（{script_name}.py），{info['category']}类模型。",
            model_pref="script",
            skills=[script_name],
            triggers=[info['category']]  # 触发词用分类名
        )
    except Exception as e:
        logger.warning(f"[ModelInstaller] Agent 注册跳过: {e}")
