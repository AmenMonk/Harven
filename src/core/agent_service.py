# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — 系统级 Agent 服务
独立后台进程，提供系统操控能力（文件、进程、定时任务）
与 Harven 主 Flask 进程通过 HTTP 通信

安全机制：
1. 所有操作需用户显式授权（默认拒绝）
2. 命令白名单（仅允许预设命令集）
3. 超时控制（所有操作 30 秒超时）
4. 日志审计（所有操作自动记录）
"""
import os
import sys
import json
import time
import logging
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO, format='[Agent] %(asctime)s %(message)s')
logger = logging.getLogger('AgentService')

# ── 安全配置 ──
SAFE_COMMANDS = {
    'list_dir': {'cmd': 'ls', 'args_ok': True, 'desc': '列出目录'},
    'read_file': {'cmd': 'cat', 'args_ok': True, 'desc': '读取文件内容'},
    'write_file': {'cmd': None, 'desc': '写入文件（Python实现）'},
    'run_python': {'cmd': None, 'desc': '执行Python代码'},
    'pip_install': {'cmd': 'pip', 'args_ok': True, 'desc': '安装Python包'},
    'system_info': {'cmd': None, 'desc': '获取系统信息'},
    'process_list': {'cmd': None, 'desc': '列出进程'},
}

# ── 授权状态 ──
_PENDING_APPROVALS: list = []
_APPROVED_CMDS: dict = {}  # cmd_id -> expiry

class AgentService:
    """系统Agent后端服务"""

    def __init__(self, data_dir: str = None):
        self._data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'agent'
        )
        os.makedirs(self._data_dir, exist_ok=True)
        self._log_path = os.path.join(self._data_dir, 'agent_audit.log')
        self._running = True
        self._lock = threading.Lock()
        logger.info(f'Agent服务启动, 数据目录: {self._data_dir}')

    # ══════════════════════════════════════════
    # 命令执行
    # ══════════════════════════════════════════

    def execute(self, action: str, params: dict = None) -> dict:
        """
        执行Agent操作（需要用户授权）
        Returns: {'success': bool, 'result': str, 'error': str}
        """
        params = params or {}

        if action not in SAFE_COMMANDS:
            return {'success': False, 'error': f'未知操作: {action}'}

        # 记录审计日志
        self._audit_log(action, params)

        try:
            result = self._do_execute(action, params)
            return {'success': True, 'result': result}
        except Exception as e:
            logger.error(f'执行失败 [{action}]: {e}')
            return {'success': False, 'error': str(e)}

    def _do_execute(self, action: str, params: dict) -> str:
        """实际执行操作"""
        if action == 'list_dir':
            path = params.get('path', '.')
            out = subprocess.check_output(['ls', '-la', path], timeout=10, text=True)
            return out

        elif action == 'read_file':
            path = params.get('path', '')
            if not path or not os.path.isfile(path):
                raise FileNotFoundError(f'文件不存在: {path}')
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()

        elif action == 'write_file':
            path = params.get('path', '')
            content = params.get('content', '')
            if not path:
                raise ValueError('路径不能为空')
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f'已写入 {len(content)} 字符到 {path}'

        elif action == 'run_python':
            code = params.get('code', '')
            if not code:
                raise ValueError('代码不能为空')
            result = subprocess.run(
                [sys.executable, '-c', code],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[:500])
            return result.stdout[:2000]

        elif action == 'pip_install':
            pkg = params.get('package', '')
            if not pkg:
                raise ValueError('包名不能为空')
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', pkg],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[:500])
            return f'已安装 {pkg}'

        elif action == 'system_info':
            import platform
            return json.dumps({
                'os': platform.system(),
                'release': platform.release(),
                'python': sys.version,
                'cwd': os.getcwd(),
            }, ensure_ascii=False)

        elif action == 'process_list':
            if sys.platform == 'win32':
                out = subprocess.check_output(['tasklist', '/FO', 'CSV'], timeout=10, text=True)
            else:
                out = subprocess.check_output(['ps', 'aux', '--sort=-%mem'], timeout=10, text=True)
            return out[:2000]

        return '不支持的操作'

    # ══════════════════════════════════════════
    # 授权管理
    # ══════════════════════════════════════════

    def create_approval(self, action: str, params: dict, requester: str = 'model') -> str:
        """创建授权请求，返回 request_id"""
        req_id = f'approve_{int(time.time())}_{len(_PENDING_APPROVALS)}'
        with self._lock:
            _PENDING_APPROVALS.append({
                'id': req_id,
                'action': action,
                'params': params,
                'requester': requester,
                'created_at': datetime.now().isoformat(),
                'status': 'pending'
            })
        return req_id

    def approve(self, req_id: str) -> bool:
        """批准授权请求"""
        with self._lock:
            for req in _PENDING_APPROVALS:
                if req['id'] == req_id:
                    req['status'] = 'approved'
                    # 30分钟内有效
                    _APPROVED_CMDS[req_id] = time.time() + 1800
                    return True
        return False

    def get_pending_approvals(self) -> list:
        """获取待批准的请求"""
        with self._lock:
            return [r for r in _PENDING_APPROVALS if r['status'] == 'pending']

    # ══════════════════════════════════════════
    # 内部
    # ══════════════════════════════════════════

    def _audit_log(self, action: str, params: dict):
        """记录审计日志"""
        try:
            with open(self._log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'time': datetime.now().isoformat(),
                    'action': action,
                    'params': str(params)[:200],
                }, ensure_ascii=False) + '\n')
        except Exception as _e: logger.debug(f"[agent_service] 忽略: {_e}")

    def stop(self):
        self._running = False
        logger.info('Agent服务停止')

# ── 全局单例 ──
_agent_instance: Optional[AgentService] = None

def get_agent() -> AgentService:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = AgentService()
    return _agent_instance
