# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""Harven AI — 跨平台系统调用抽象层
统一 Windows/Linux/macOS 的系统操作接口"""
import logging
logger = logging.getLogger(__name__)
import os
import sys
import subprocess
import platform

_SYSTEM = platform.system().lower()  # windows / linux / darwin

def get_os_type() -> str:
    return _SYSTEM

def list_processes() -> str:
    """列出进程（跨平台）"""
    try:
        if _SYSTEM == 'windows':
            return subprocess.check_output(['tasklist', '/FO', 'CSV'], timeout=10, text=True)
        else:
            return subprocess.check_output(['ps', 'aux', '--sort=-%mem'], timeout=10, text=True)
    except Exception as e:
        return f'进程列表获取失败: {e}'

def list_directory(path: str = '.') -> str:
    """列出目录（跨平台）"""
    try:
        if _SYSTEM == 'windows':
            return subprocess.check_output(['cmd', '/c', 'dir', path], timeout=10, text=True, shell=True)
        else:
            return subprocess.check_output(['ls', '-la', path], timeout=10, text=True)
    except Exception as e:
        return f'目录列表失败: {e}'

def get_system_info() -> dict:
    """获取系统信息（跨平台）"""
    info = {
        'os': _SYSTEM,
        'os_version': platform.release(),
        'machine': platform.machine(),
        'python_version': sys.version,
        'cpu_count': os.cpu_count(),
    }
    try:
        import psutil
        info['memory_total_gb'] = round(psutil.virtual_memory().total / (1024**3), 1)
        info['memory_available_gb'] = round(psutil.virtual_memory().available / (1024**3), 1)
        info['disk_usage_percent'] = psutil.disk_usage('/').percent
    except ImportError as _e:
        logger.debug(f"[platform_utils] 忽略: {_e}")
    return info

def get_ollama_path() -> str:
    """获取 Ollama 路径（跨平台）"""
    try:
        if _SYSTEM == 'windows':
            result = subprocess.check_output(['where', 'ollama'], timeout=5, text=True)
        else:
            result = subprocess.check_output(['which', 'ollama'], timeout=5, text=True)
        return result.strip().split('\n')[0]
    except Exception:
        return 'ollama'  # 默认路径

def run_command(cmd: list, timeout: int = 30) -> dict:
    """安全执行系统命令（跨平台）"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            'success': result.returncode == 0,
            'stdout': result.stdout[:5000],
            'stderr': result.stderr[:1000],
            'returncode': result.returncode
        }
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': '命令超时'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def is_port_in_use(port: int) -> bool:
    """检查端口是否被占用（跨平台）"""
    try:
        if _SYSTEM == 'windows':
            result = subprocess.check_output(f'netstat -ano | findstr :{port} ', timeout=5, text=True, shell=True)
        else:
            result = subprocess.check_output(f'lsof -i :{port}', timeout=5, text=True, shell=True)
        return bool(result.strip())
    except Exception:
        return False

def kill_process_by_pid(pid: int) -> bool:
    """终止进程（跨平台）"""
    try:
        if _SYSTEM == 'windows':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], timeout=10, capture_output=True)
        else:
            subprocess.run(['kill', '-9', str(pid)], timeout=10, capture_output=True)
        return True
    except Exception:
        return False
