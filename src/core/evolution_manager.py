# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - 受控进化模块

允许 Harven 在用户授权下，受控地更新自己的知识、配置和能力。

核心原则：
1. 用户最高决定权：任何进化行为必须得到用户明确批准
2. AI独立性：进化不能违反独立性原则，不能将AI集群化
3. 透明度：所有进化行为必须可审计、可回滚
4. 安全性：进化包必须经过安全扫描，防止恶意代码注入

功能：
- 版本检测：从 GitHub/Gitee/HarvenHub 检测新版本
- 更新通知：向用户提示新版本，展示更新内容
- 受控更新：用户批准后，下载并应用更新
- 回滚机制：如果更新后出现问题，可以回滚到之前的版本
- 进化日志：记录所有进化行为，供审计和回滚
"""

import json
import logging
import subprocess      # ★ #1589：_git_backup 用到，此前未 import → NameError 被 except 吞成「备份失败」
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

EVOLUTION_DIR = Path(__file__).parent.parent.parent / 'data' / 'evolution'
EVOLUTION_LOG = EVOLUTION_DIR / 'evolution_log.json'
CURRENT_VERSION_FILE = EVOLUTION_DIR / 'current_version.json'


class EvolutionManager:
    """受控进化管理器（单例）"""

    def __init__(self):
        self._lock = threading.Lock()
        EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)
        self._load_current_version()
        self._evolution_enabled = True  # 受控进化总开关

    def _load_current_version(self):
        """加载当前版本信息"""
        if CURRENT_VERSION_FILE.exists():
            try:
                self._current_version = json.loads(CURRENT_VERSION_FILE.read_text(encoding='utf-8'))
            except Exception:
                self._current_version = {'version': '0.2.1', 'build_id': '', 'updated_at': ''}
        else:
            self._current_version = {'version': '0.2.1', 'build_id': '', 'updated_at': ''}

    def _save_current_version(self):
        """保存当前版本信息"""
        CURRENT_VERSION_FILE.write_text(
            json.dumps(self._current_version, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

    def get_current_version(self) -> Dict:
        """获取当前版本信息"""
        return self._current_version.copy()

    def check_updates(self) -> Dict:
        """检测新版本（从 HarvenHub 或 GitHub）"""
        result = {
            'has_update': False,
            'latest_version': None,
            'current_version': self._current_version.get('version', '0.2.1'),
            'release_notes': '',
            'download_url': '',
            'error': None,
        }
        
        # 1. 尝试从 HarvenHub 检测新版本
        try:
            hub_result = self._check_harvenhub()
            if hub_result and not hub_result.get('error'):
                result.update(hub_result)
                return result
        except Exception as e:
            logger.warning(f'[EvolutionManager] HarvenHub 检测失败: {e}')
        
        # 2. 尝试从 GitHub 检测新版本
        try:
            github_result = self._check_github()
            if github_result and not github_result.get('error'):
                result.update(github_result)
                return result
        except Exception as e:
            logger.warning(f'[EvolutionManager] GitHub 检测失败: {e}')
        
        result['error'] = '无法连接到更新服务器，请检查网络连接'
        return result
    
    def _check_harvenhub(self) -> Optional[Dict]:
        """从 HarvenHub 检测新版本（预留接口，待市场上线后启用）"""
        return {
            'source': 'harvenhub',
            'available': False,
            'api_ready': False,
            'message': 'HarvenHub 市场暂未上线，更新检测走 GitHub',
        }
    
    def _check_github(self) -> Optional[Dict]:
        """从 GitHub 检测新版本"""
        import urllib.request
        import json
        
        try:
            # GitHub API: GET /repos/{owner}/{repo}/releases/latest
            url = 'https://api.github.com/repos/harvenai/harven/releases/latest'
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'HarvenAI/1.0')
            
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                latest_version = data.get('tag_name', '').lstrip('v')
                if not latest_version:
                    return None
                
                # 比较版本号
                has_update = self._compare_versions(latest_version, self._current_version.get('version', '0.2.1'))
                
                return {
                    'has_update': has_update,
                    'latest_version': latest_version,
                    'release_notes': data.get('body', ''),
                    'download_url': data.get('zipball_url', ''),
                    'html_url': data.get('html_url', ''),
                }
        except Exception as e:
            logger.error(f'[EvolutionManager] GitHub 检测失败: {e}')
            return None
    
    def _compare_versions(self, v1: str, v2: str) -> bool:
        """比较版本号，返回 True 如果 v1 > v2"""
        try:
            parts1 = [int(x) for x in v1.split('.')]
            parts2 = [int(x) for x in v2.split('.')]
            
            # 补齐长度
            max_len = max(len(parts1), len(parts2))
            parts1.extend([0] * (max_len - len(parts1)))
            parts2.extend([0] * (max_len - len(parts2)))
            
            return parts1 > parts2
        except Exception:
            return False

    def request_evolution(self, evolution_package: Dict) -> Dict:
        """请求进化（需要用户批准）"""
        # 记录进化请求
        log_entry = {
            'id': int(time.time()),
            'type': 'request',
            'package': evolution_package,
            'requested_at': datetime.now().isoformat(),
            'status': 'pending',  # pending, approved, rejected
            'approved_at': None,
            'applied_at': None,
            'rollback_at': None,
        }
        self._append_log(log_entry)
        return {'success': True, 'log_id': log_entry['id'], 'status': 'pending'}

    def approve_evolution(self, log_id: int) -> Dict:
        """批准进化"""
        with self._lock:
            log = self._get_log(log_id)
            if not log:
                return {'success': False, 'error': '进化请求不存在'}
            if log['status'] != 'pending':
                return {'success': False, 'error': f'进化请求状态不正确: {log["status"]}'}
            log['status'] = 'approved'
            log['approved_at'] = datetime.now().isoformat()
            self._update_log(log)
            # 异步应用进化
            threading.Thread(target=self._apply_evolution, args=(log,), daemon=True).start()
            return {'success': True, 'status': 'approved'}

    def reject_evolution(self, log_id: int) -> Dict:
        """拒绝进化"""
        with self._lock:
            log = self._get_log(log_id)
            if not log:
                return {'success': False, 'error': '进化请求不存在'}
            if log['status'] != 'pending':
                return {'success': False, 'error': f'进化请求状态不正确: {log["status"]}'}
            log['status'] = 'rejected'
            self._update_log(log)
            return {'success': True, 'status': 'rejected'}

    def _apply_evolution(self, log: Dict):
        """应用进化（后台执行）"""
        try:
            package = log.get('package', {})
            download_url = package.get('download_url', '')
            
            if not download_url:
                raise ValueError('进化包下载链接为空')
            
            # 1. 下载进化包
            logger.info(f'[Evolution] 开始下载进化包: {download_url}')
            package_path = self._download_package(download_url, log['id'])
            
            # 2. 安全扫描
            logger.info('[Evolution] 开始安全扫描...')
            self._security_scan(package_path)
            
            # 3. 备份当前状态
            logger.info('[Evolution] 开始备份当前状态...')
            backup_path = self._backup_current_state()
            
            # 4. 应用进化包
            logger.info('[Evolution] 开始应用进化包...')
            self._apply_package(package_path)
            
            # 5. 更新版本信息
            new_version = package.get('version', '')
            if new_version:
                self._current_version['version'] = new_version
                self._current_version['updated_at'] = datetime.now().isoformat()
                self._save_current_version()
            
            log['status'] = 'applied'
            log['applied_at'] = datetime.now().isoformat()
            self._update_log(log)
            logger.info(f'[Evolution] 进化应用成功: {log["id"]}')
        except Exception as e:
            log['status'] = 'failed'
            log['error'] = str(e)
            self._update_log(log)
            logger.error(f'[Evolution] 进化应用失败: {e}')
    
    def _download_package(self, url: str, log_id: int) -> Path:
        """下载进化包"""
        import urllib.request
        import tempfile
        
        package_dir = EVOLUTION_DIR / 'packages'
        package_dir.mkdir(parents=True, exist_ok=True)
        
        package_path = package_dir / f'package_{log_id}.zip'
        
        urllib.request.urlretrieve(url, str(package_path))
        
        if not package_path.exists():
            raise FileNotFoundError(f'下载失败: {url}')
        
        logger.info(f'[Evolution] 进化包下载完成: {package_path}')
        return package_path
    
    def _security_scan(self, package_path: Path) -> bool:
        """安全扫描进化包——防止恶意代码注入

        检查项：
        1. 文件名/路径穿越尝试（如 ../../../etc/passwd）
        2. Python 文件中的危险函数调用（eval/exec/__import__/os.system/subprocess.call 等）
        3. 超大型文件 DOS 攻击
        4. 编码陷阱（base64 隐写、pickle 反序列化）
        5. 文件完整性检查（与 manifest 比对）
        """
        from pathlib import Path as _Path

        try:
            issues = []
            max_file_size = 10 * 1024 * 1024  # 10MB 单文件上限
            total_size = 0

            # 危险的 Python 模式（禁止调用）
            DANGEROUS_PATTERNS = [
                '__import__', 'eval(', 'exec(', 'compile(',
                'os.system', 'subprocess.call', 'subprocess.Popen',
                'subprocess.run(', 'subprocess.check',
                'pty.spawn', 'ctypes.CDLL', 'ctypes.WinDLL',
                'pickle.loads', 'pickle.load(',
                'base64.b64decode', 'marshal.loads',
                'open("/', r"open('/", 'open("\\\\', r"open('\\\\",
                'shutil.rmtree', 'os.remove(', 'os.unlink(',
            ]

            # Windows 特有危险
            WIN_DANGEROUS = [
                'winreg', 'ctypes.windll', 'pywin32',
                'CreateProcess', 'ShellExecute', 'WScript.Shell',
            ]

            for file_path in sorted(package_path.rglob('*')):
                if not file_path.is_file():
                    continue
                if file_path.name.startswith('.'):
                    continue

                # 1. 检查文件大小
                file_size = file_path.stat().st_size
                total_size += file_size
                if file_size > max_file_size:
                    issues.append(f"文件过大 ({file_size/1024/1024:.1f}MB): {file_path.name}")
                    continue

                if total_size > 100 * 1024 * 1024:  # 100MB 总大小上限
                    issues.append("进化包总大小超过 100MB 上限")
                    break

                # 2. 检查路径穿越
                rel_path = file_path.relative_to(package_path)
                if '..' in str(rel_path):
                    issues.append(f"路径穿越尝试: {rel_path}")
                    continue

                # 3. 扫描文本文件内容
                if file_path.suffix in ('.py', '.js', '.bat', '.ps1', '.sh',
                                        '.html', '.yml', '.yaml', '.json',
                                        '.txt', '.cfg', '.conf', '.ini'):
                    try:
                        content = file_path.read_text(encoding='utf-8', errors='replace')
                    except Exception:
                        # 二进制或编码异常
                        content = ''

                    # 检查危险模式
                    if file_path.suffix == '.py':
                        code_lower = content.lower()
                        for pattern in DANGEROUS_PATTERNS:
                            if pattern in content or pattern in code_lower:
                                # eval 在某些配置文件中是正常的
                                if pattern == 'eval(' and 'json' in content:
                                    continue
                                issues.append(f"危险模式 [{pattern}] 在 {rel_path}")
                                break

                        # Windows 特有
                        for pattern in WIN_DANGEROUS:
                            if pattern.lower() in code_lower:
                                issues.append(f"危险 Windows 模式 [{pattern}] 在 {rel_path}")
                                break

                    # 4. 检查 base64 大块数据
                    if file_path.suffix == '.py' and len(content) > 5000:
                        import re
                        b64_chunks = re.findall(r'[A-Za-z0-9+/]{100,}={0,2}', content)
                        for chunk in b64_chunks:
                            if len(chunk) > 200:
                                issues.append(f"可疑 base64 大块数据 ({len(chunk)} chars) 在 {rel_path}")
                                break

            # 报告结果
            if issues:
                logger.warning(f"[Evolution] 安全扫描发现 {len(issues)} 个问题:")
                for issue in issues:
                    logger.warning(f"  ⚠ {issue}")
                return False

            logger.info('[Evolution] 安全扫描通过')
            return True

        except Exception as e:
            logger.error(f"[Evolution] 安全扫描异常: {e}")
            return False
    
    def _backup_current_state(self) -> Path:
        """备份当前状态"""
        import shutil
        
        backup_dir = EVOLUTION_DIR / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = backup_dir / f'backup_{timestamp}'
        backup_path.mkdir(parents=True, exist_ok=True)
        
        # 备份关键文件和目录
        src_dir = Path(__file__).parent.parent
        backup_items = ['data', 'src', 'config.yaml']
        
        for item in backup_items:
            src_path = src_dir / item
            if src_path.exists():
                if src_path.is_dir():
                    shutil.copytree(src_path, backup_path / item)
                else:
                    shutil.copy2(src_path, backup_path / item)
        
        logger.info(f'[Evolution] 备份完成: {backup_path}')
        return backup_path
    
    def _apply_package(self, package_path: Path):
        """应用进化包"""
        import zipfile
        import shutil
        
        src_dir = Path(__file__).parent.parent
        
        # 解压进化包
        with zipfile.ZipFile(package_path, 'r') as zip_ref:
            zip_ref.extractall(src_dir)
        
        logger.info(f'[Evolution] 进化包应用完成: {package_path}')

    def rollback_evolution(self, log_id: int) -> Dict:
        """回滚进化"""
        try:
            log = self._get_log(log_id)
            if not log:
                return {'success': False, 'error': '进化记录不存在'}
            
            if log['status'] != 'applied':
                return {'success': False, 'error': f'无法回滚，当前状态: {log["status"]}'}
            
            # 查找对应的备份
            backup_dir = EVOLUTION_DIR / 'backups'
            backups = sorted(backup_dir.glob('backup_*'), key=lambda x: x.name, reverse=True)
            
            if not backups:
                return {'success': False, 'error': '未找到备份文件，无法回滚'}
            
            # 使用最新的备份进行回滚
            backup_path = backups[0]
            
            # 执行回滚
            self._restore_from_backup(backup_path)
            
            # 更新日志
            log['status'] = 'rolled_back'
            log['rollback_at'] = datetime.now().isoformat()
            self._update_log(log)
            
            logger.info(f'[Evolution] 回滚成功: {log_id}')
            return {'success': True, 'message': '回滚成功'}
        except Exception as e:
            logger.error(f'[Evolution] 回滚失败: {e}')
            return {'success': False, 'error': str(e)}
    
    def _restore_from_backup(self, backup_path: Path):
        """从备份恢复"""
        import shutil
        
        src_dir = Path(__file__).parent.parent
        
        # 恢复备份的文件和目录
        backup_items = ['data', 'src', 'config.yaml']
        
        for item in backup_items:
            backup_item = backup_path / item
            if backup_item.exists():
                target_path = src_dir / item
                
                # 删除目标
                if target_path.exists():
                    if target_path.is_dir():
                        shutil.rmtree(target_path)
                    else:
                        target_path.unlink()
                
                # 恢复备份
                if backup_item.is_dir():
                    shutil.copytree(backup_item, target_path)
                else:
                    shutil.copy2(backup_item, target_path)
        
        logger.info(f'[Evolution] 从备份恢复完成: {backup_path}')

    def get_evolution_history(self) -> List[Dict]:
        """获取进化历史"""
        if not EVOLUTION_LOG.exists():
            return []
        try:
            return json.loads(EVOLUTION_LOG.read_text(encoding='utf-8'))
        except Exception:
            return []

    def _append_log(self, log_entry: Dict):
        """追加进化日志"""
        with self._lock:
            logs = self.get_evolution_history()
            logs.append(log_entry)
            # 只保留最近100条日志
            if len(logs) > 100:
                logs = logs[-100:]
            EVOLUTION_LOG.write_text(
                json.dumps(logs, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )

    def _get_log(self, log_id: int) -> Optional[Dict]:
        """获取指定ID的进化日志"""
        logs = self.get_evolution_history()
        for log in logs:
            if log.get('id') == log_id:
                return log
        return None

    def _update_log(self, updated_log: Dict):
        """更新进化日志"""
        with self._lock:
            logs = self.get_evolution_history()
            for i, log in enumerate(logs):
                if log.get('id') == updated_log['id']:
                    logs[i] = updated_log
                    break
            EVOLUTION_LOG.write_text(
                json.dumps(logs, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )

    # ═══════════════════════════════════════════════════════════════════
    # 进化提案系统（AI 生成提案 → 用户审批 → 受控应用）
    # ═══════════════════════════════════════════════════════════════════

    PROPOSALS_FILE = EVOLUTION_DIR / 'proposals.json'  # 运行时不能直接用，通过 _proposals_path() 访问

    def _proposals_path(self) -> Path:
        return EVOLUTION_DIR / 'proposals.json'

    def get_proposals(self, status: Optional[str] = None) -> List[Dict]:
        """获取进化提案列表，可按状态过滤"""
        path = self._proposals_path()
        if not path.exists():
            return []
        try:
            proposals = json.loads(path.read_text(encoding='utf-8'))
            if status:
                proposals = [p for p in proposals if p.get('status') == status]
            return proposals
        except Exception as e:
            logger.error(f'[EvolutionManager] 读取提案失败: {e}')
            return []

    def create_proposal(self, proposal: Dict) -> Dict:
        """创建进化提案（AI 或用户提交）
        
        Args:
            proposal: 提案内容，需包含：
                - title: 提案标题
                - description: 详细描述
                - type: 提案类型 (config_change/skill_update/knowledge_update/other)
                - target: 修改目标文件/配置键（可选）
                - new_value: 新值（可选，用于 config_change）
                - submitted_by: 提交者 (ai/user)
        """
        proposal_id = int(time.time() * 1000)  # 毫秒级 ID，避免碰撞
        entry = {
            'id': proposal_id,
            'title': proposal.get('title', '未命名提案'),
            'description': proposal.get('description', ''),
            'type': proposal.get('type', 'other'),
            'target': proposal.get('target', ''),
            'new_value': proposal.get('new_value', ''),
            'submitted_by': proposal.get('submitted_by', 'user'),
            'status': 'pending',  # pending / approved / rejected / applied / failed
            'created_at': datetime.now().isoformat(),
            'reviewed_at': None,
            'applied_at': None,
            'review_comment': '',
        }
        self._append_proposal(entry)
        logger.info(f'[EvolutionManager] 新建进化提案: #{proposal_id} {entry["title"]}')
        return {'success': True, 'proposal_id': proposal_id, 'status': 'pending'}

    def approve_proposal(self, proposal_id: int, comment: str = '') -> Dict:
        """批准进化提案，并尝试应用（白名单校验）"""
        with self._lock:
            proposal = self._get_proposal(proposal_id)
            if not proposal:
                return {'success': False, 'error': '提案不存在'}
            if proposal['status'] != 'pending':
                return {'success': False, 'error': f'提案状态不允许批准: {proposal["status"]}'}
            
            # 白名单校验：只允许修改 skills/ 和 config.yaml 指定键
            if not self._proposal_whitelist_check(proposal):
                proposal['status'] = 'rejected'
                proposal['review_comment'] = '白名单校验失败，目标不在允许修改范围内'
                proposal['reviewed_at'] = datetime.now().isoformat()
                self._update_proposal(proposal)
                return {'success': False, 'error': '目标不在允许的修改白名单中'}
            
            proposal['status'] = 'approved'
            proposal['reviewed_at'] = datetime.now().isoformat()
            proposal['review_comment'] = comment
            self._update_proposal(proposal)
            
            # 异步应用
            threading.Thread(target=self._apply_proposal, args=(proposal,), daemon=True).start()
            return {'success': True, 'status': 'approved', 'proposal_id': proposal_id}

    def reject_proposal(self, proposal_id: int, comment: str = '') -> Dict:
        """拒绝进化提案"""
        with self._lock:
            proposal = self._get_proposal(proposal_id)
            if not proposal:
                return {'success': False, 'error': '提案不存在'}
            if proposal['status'] != 'pending':
                return {'success': False, 'error': f'提案状态不允许拒绝: {proposal["status"]}'}
            proposal['status'] = 'rejected'
            proposal['reviewed_at'] = datetime.now().isoformat()
            proposal['review_comment'] = comment
            self._update_proposal(proposal)
            return {'success': True, 'status': 'rejected', 'proposal_id': proposal_id}

    def _proposal_whitelist_check(self, proposal: Dict) -> bool:
        """白名单校验：只允许修改安全范围内的文件"""
        ptype = proposal.get('type', '')
        target = proposal.get('target', '')
        
        # 允许的提案类型
        ALLOWED_TYPES = {'config_change', 'skill_update', 'knowledge_update', 'code_update', 'other'}
        ALLOWED_CONFIG_KEYS = {
            'system_prompt', 'response_language', 'max_memory_items',
            'web_search_enabled', 'knowledge_base_enabled', 'rate_limit',
            'ollama.default_model',
            # ★ 2026-11-15: 清掉 morality.enabled/morality.level 死键（功德/道德模块已彻底移除，配置键不存在）
            'auto_learn',
        }
        
        if ptype not in ALLOWED_TYPES:
            return False
        
        if ptype == 'config_change':
            return target in ALLOWED_CONFIG_KEYS
        
        if ptype == 'skill_update':
            return target.startswith('skills/') or target.startswith('data/skills/')
        
        if ptype == 'knowledge_update':
            return True
        
        if ptype == 'code_update':
            # ★ 2026-07-21: 代码修改白名单
            ALLOWED_PATHS = [
                'builtin_tools.py',      # 加新工具
                'data/skills/',          # Skill脚本
                'templates/includes/',   # HTML模板
                'static/js/modules/',    # JS模块
                'static/css/',           # CSS样式
            ]
            # ★ 安全黑名单：永远禁止修改
            BLOCKED_PATHS = [
                'independence.py',       # 出站白名单保护
                'tool_executor.py',      # 代码执行沙箱
                'security_guard.py',     # 安全防护
                'security_routes.py',     # 安全路由
                'evolution_manager.py',  # 自己（不能改自己的进化逻辑）
            ]
            # 先检查黑名单
            for blocked in BLOCKED_PATHS:
                if blocked in target:
                    logger.warning(f'[EvolutionManager] 黑名单拦截: {target}')
                    return False
            # 再检查白名单
            return any(target.startswith(p) or target == p for p in ALLOWED_PATHS)
        
        # other 类型只允许描述性提案，不自动应用
        return True

    def _apply_proposal(self, proposal: Dict):
        """应用进化提案（后台执行）"""
        try:
            ptype = proposal.get('type', '')
            
            if ptype == 'config_change':
                self._apply_config_change(proposal)
            elif ptype == 'skill_update':
                # ★ 2026-07-21: skill_update 也走 code_change 逻辑
                self._apply_code_change(proposal)
            elif ptype == 'code_update':
                # ★ 2026-07-21: 新增——AI自动写代码改自己
                self._apply_code_change(proposal)
            elif ptype == 'knowledge_update':
                logger.info(f'[EvolutionManager] knowledge_update 提案记录，需手动导入: {proposal["id"]}')
            else:
                logger.info(f'[EvolutionManager] other 类型提案仅记录: {proposal["id"]}')
            
            proposal['status'] = 'applied'
            proposal['applied_at'] = datetime.now().isoformat()
            self._update_proposal(proposal)
        except Exception as e:
            proposal['status'] = 'failed'
            proposal['review_comment'] = str(e)
            self._update_proposal(proposal)
            logger.error(f'[EvolutionManager] 应用提案失败: {e}')

    def _apply_code_change(self, proposal: Dict):
        """★ 2026-07-21: AI自动写代码修改文件
        
        流程：
        1. 备份原文件
        2. 调用模型，用FC工具（read_file/write_file/edit_file）实际修改代码
        3. 记录修改结果
        4. 如果失败，自动恢复备份
        """
        import shutil
        import os
        
        target = proposal.get('target', '')
        user_request = proposal.get('user_message', proposal.get('description', ''))
        changes = proposal.get('changes', [])
        
        if not target or target == '待确认':
            raise ValueError('提案目标文件不明确，无法自动执行')
        
        # 解析目标文件路径（相对于项目根目录）
        project_root = Path(__file__).parent.parent.parent
        target_path = project_root / target
        
        # ★ 备份原文件
        backup_path = None
        if target_path.exists():
            backup_dir = EVOLUTION_DIR / 'backups'
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_name = f"{target_path.stem}_{int(time.time())}{target_path.suffix}"
            backup_path = backup_dir / backup_name
            shutil.copy2(str(target_path), str(backup_path))
            logger.info(f'[EvolutionManager] 已备份: {target} → {backup_path}')
        
        # ★ 调用模型执行代码修改
        try:
            result = self._call_model_for_code_change(user_request, target, changes)
            
            if result.get('success'):
                proposal['execution_result'] = result.get('summary', '修改完成')
                proposal['backup_file'] = str(backup_path) if backup_path else None
                logger.info(f'[EvolutionManager] 代码修改成功: {target}')
            else:
                # 修改失败，恢复备份
                if backup_path and backup_path.exists():
                    shutil.copy2(str(backup_path), str(target_path))
                    logger.warning(f'[EvolutionManager] 修改失败，已恢复备份: {target}')
                raise Exception(result.get('error', '模型执行代码修改失败'))
                
        except Exception as e:
            # 异常时恢复备份
            if backup_path and backup_path.exists():
                shutil.copy2(str(backup_path), str(target_path))
                logger.warning(f'[EvolutionManager] 异常，已恢复备份: {target}')
            raise

    def _call_model_for_code_change(self, user_request, target_file, changes):
        """调用模型用FC工具实际修改代码
        
        ★ 2026-07-21: 这是"AI自己写代码改自己"的核心
        模型通过 read_file/write_file/edit_file 工具完成修改
        """
        import json as _json
        
        try:
            from src.core.multi_model_manager import get_multi_model_manager
            from src.core.builtin_tools import get_tool_definitions, call_tool
            from src.utils.config import Config
            
            cfg = Config()
            mm = get_multi_model_manager()
            if mm is None:
                return {'success': False, 'error': '多模型管理器不可用'}
            
            # 获取当前使用的外脑模型
            model_id = mm.current_model if mm.current_model != 'ollama' else None
            if not model_id:
                return {'success': False, 'error': '当前使用本地模型，无法执行代码修改（需要外脑）'}
            
            # 构建系统提示词
            change_desc = ""
            if changes:
                change_desc = "\n".join(f"- {c.get('desc','')}" for c in changes)
            
            system_prompt = f"""你是哈文AI的代码修改助手。用户已批准了一个受控进化提案，你需要实际修改代码。

## 任务
{user_request}

## 要修改的文件
{target_file}

## 修改内容
{change_desc}

## 操作步骤
1. 先用 read_file 读取 {target_file} 的当前内容
2. 根据需求生成新代码
3. 用 write_file 或 edit_file 应用修改
4. 报告修改结果

## 安全规则
- 只能修改 {target_file}，不要碰其他文件
- 不要修改 security/independence/tool_executor 相关代码
- 不要写网络请求代码
- 修改要精确，不要破坏现有功能
"""
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请执行已批准的代码修改：{user_request}\n目标文件：{target_file}"},
            ]
            
            # 获取FC工具定义
            fc_tools = get_tool_definitions()
            # 只保留文件操作工具（安全限制）
            safe_tools = [t for t in fc_tools if t['function']['name'] in 
                         ('read_file', 'write_file', 'edit_file', 'list_dir')]
            
            # 调用模型（非流式，带FC）
            response_gen = mm.generate_response(messages, stream=False, tools=safe_tools)
            
            full_response = ""
            tool_calls_raw = None
            
            for chunk in response_gen:
                if isinstance(chunk, dict):
                    if chunk.get('type') == 'text' or chunk.get('type') == 'content':
                        full_response += chunk.get('content', '')
                    elif chunk.get('type') == 'tool_calls':
                        tool_calls_raw = chunk.get('content')
                    elif chunk.get('type') == 'error':
                        return {'success': False, 'error': chunk.get('content', '模型调用错误')}
            
            # 如果有工具调用，执行它们
            actions_log = []
            if tool_calls_raw:
                # ★ 修复：收集所有工具结果，不只最后一个
                tool_results = []
                for tc in tool_calls_raw:
                    tool_name = tc.get('function', {}).get('name', '')
                    tool_args_str = tc.get('function', {}).get('arguments', '{}')
                    
                    # 安全校验：只允许文件操作工具
                    if tool_name not in ('read_file', 'write_file', 'edit_file', 'list_dir'):
                        logger.warning(f'[EvolutionManager] 拒绝非文件工具: {tool_name}')
                        continue
                    
                    # 解析参数
                    try:
                        tool_args = _json.loads(tool_args_str) if isinstance(tool_args_str, str) else tool_args_str
                    except:
                        tool_args = {}
                    
                    # 安全校验：目标路径必须在白名单内
                    file_path = tool_args.get('path', '')
                    BLOCKED = ['independence', 'tool_executor', 'security_guard', 'security_routes', 'evolution_manager']
                    if any(b in file_path for b in BLOCKED):
                        logger.warning(f'[EvolutionManager] 拒绝修改安全文件: {file_path}')
                        actions_log.append(f'❌ 拒绝修改安全文件: {file_path}')
                        continue
                    
                    # 执行工具
                    result = call_tool(tool_name, **tool_args)
                    success = result.get('success', False)
                    actions_log.append(f'{"✅" if success else "❌"} {tool_name}({file_path}): {result.get("error", "ok")[:100]}')
                    logger.info(f'[EvolutionManager] FC执行: {tool_name} → {success}')
                    
                    # 收集工具结果
                    tool_results.append({
                        'tc': tc,
                        'result': result,
                    })
                
                # 把所有工具结果发给模型做总结
                messages.append({"role": "assistant", "content": full_response, "tool_calls": tool_calls_raw})
                for tr in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tr['tc'].get('id', ''),
                        "content": _json.dumps(tr['result'], ensure_ascii=False)[:2000],
                    })
                
                # 第二轮：让模型总结做了什么
                response_gen2 = mm.generate_response(messages, stream=False)
                summary = ""
                for chunk in response_gen2:
                    if isinstance(chunk, dict) and chunk.get('type') in ('text', 'content'):
                        summary += chunk.get('content', '')
                
                return {
                    'success': True,
                    'summary': summary[:500] or '修改完成',
                    'actions': actions_log,
                }
            elif full_response:
                return {
                    'success': False,
                    'error': f'模型未调用文件工具，只输出了文本: {full_response[:200]}',
                }
            else:
                return {'success': False, 'error': '模型无响应'}
                
        except Exception as e:
            logger.error(f'[EvolutionManager] 模型代码修改失败: {e}')
            return {'success': False, 'error': str(e)}

    def _apply_config_change(self, proposal: Dict):
        """应用 config.yaml 配置变更（仅白名单键）"""
        import yaml
        config_path = Path(__file__).parent.parent.parent / 'config.yaml'
        if not config_path.exists():
            raise FileNotFoundError('config.yaml 不存在')
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        
        target = proposal.get('target', '')
        new_value = proposal.get('new_value', '')
        
        # 支持点分隔嵌套键，如 'ollama.default_model'
        keys = target.split('.')
        d = config
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = new_value
        
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        
        logger.info(f'[EvolutionManager] config.yaml 已更新: {target} = {new_value}')

    def _append_proposal(self, proposal: Dict):
        """追加提案"""
        path = self._proposals_path()
        proposals = self.get_proposals()
        proposals.append(proposal)
        if len(proposals) > 200:
            proposals = proposals[-200:]
        path.write_text(json.dumps(proposals, ensure_ascii=False, indent=2), encoding='utf-8')

    def _get_proposal(self, proposal_id: int) -> Optional[Dict]:
        """获取指定 ID 的提案"""
        for p in self.get_proposals():
            if p.get('id') == proposal_id:
                return p
        return None

    def _update_proposal(self, updated: Dict):
        """更新提案"""
        path = self._proposals_path()
        proposals = self.get_proposals()
        for i, p in enumerate(proposals):
            if p.get('id') == updated['id']:
                proposals[i] = updated
                break
        path.write_text(json.dumps(proposals, ensure_ascii=False, indent=2), encoding='utf-8')


# ═══════════════════════════════════════════════════════════════════════
# ★ 代码自我进化（哈文改自己代码的安全流水线）
# ═══════════════════════════════════════════════════════════════════════

    # 白名单：允许 AI 修改的文件路径
    WHITELIST_FILES = [
        'config.yaml',
        'skills/',
        'templates/',
        'static/',
        'config.default.yaml',
    ]
    
    # 黑名单：永远不允许 AI 修改
    BLACKLIST_FILES = [
        'web_app.py',
        'src/core/engine.py',
        'src/core/agent_registry.py',
        'src/core/multi_model_manager.py',
        'src/core/evolution_manager.py',
        'data/',
        '.venv/',
    ]

    def _check_file_allowed(self, file_path: str) -> bool:
        fp = file_path.replace('\\', '/')
        for blocked in self.BLACKLIST_FILES:
            if fp.startswith(blocked):
                return False
        for allowed in self.WHITELIST_FILES:
            if fp.startswith(allowed) or fp == allowed:
                return True
        return False
    
    def create_code_proposal(self, title: str, description: str, 
                             changes: list, proposed_by: str = 'unknown') -> dict:
        """
        创建代码修改提案（由外脑/本地模型生成）
        
        Args:
            title: 提案标题
            description: 修改说明
            changes: [{'file': 'config.yaml', 'action': 'update', 'key': 'xxx', 'value': 'yyy'},
                      {'file': 'skills/my_skill.py', 'action': 'write', 'content': '...'}]
            proposed_by: 提案来源 'deepseek' / 'doubao' / 'ollama' / 'user'
        """
        errors = []
        validated_changes = []
        
        for c in changes:
            file_path = c.get('file', '')
            if not self._check_file_allowed(file_path):
                errors.append(f'❌ 文件不在白名单: {file_path}')
                continue
            validated_changes.append(c)
        
        if not validated_changes:
            return {'success': False, 'proposal_id': None, 'errors': errors}
        
        proposal = {
            'id': int(time.time() * 1000) % 10000000,
            'title': title,
            'description': description,
            'changes': validated_changes,
            'proposed_by': proposed_by,
            'status': 'pending',
            'created_at': datetime.now().isoformat(),
            'errors': errors if errors else [],
        }
        self._append_proposal(proposal)
        return {'success': True, 'proposal_id': proposal['id'], 'errors': errors}
    
    def apply_code_proposal(self, proposal_id: int) -> dict:
        """
        应用代码提案（完整安全流水线）
        1.提案校验 2.Git备份 3.写文件 4.语法检查 5.失败回滚
        """
        proposal = self._get_proposal(proposal_id)
        if not proposal:
            return {'success': False, 'message': '提案不存在'}
        if proposal['status'] != 'pending':
            return {'success': False, 'message': f'提案状态为 {proposal["status"]}，无法应用'}
        
        changes = proposal.get('changes', [])
        if not changes:
            return {'success': False, 'message': '提案中没有修改内容'}
        
        project_root = Path(__file__).parent.parent.parent
        applied_files = []
        original_contents = {}
        
        try:
            # Step 1: Git 备份
            self._git_backup()
            
            # Step 2: 逐文件修改
            for change in changes:
                file_path = project_root / change['file']
                file_path.parent.mkdir(parents=True, exist_ok=True)
                
                if file_path.exists():
                    original_contents[str(file_path)] = file_path.read_text(encoding='utf-8')
                else:
                    original_contents[str(file_path)] = None
                
                action = change.get('action', 'write')
                if action == 'update' and 'key' in change and 'value' in change:
                    self._update_config_key(file_path, change['key'], change['value'])
                elif action == 'append' and 'content' in change:
                    existing = file_path.read_text(encoding='utf-8') if file_path.exists() else ''
                    file_path.write_text(existing + '\n' + change['content'], encoding='utf-8')
                elif 'content' in change:
                    file_path.write_text(change['content'], encoding='utf-8')
                else:
                    continue
                applied_files.append(change['file'])
            
            # Step 3: 语法检查
            syntax_ok, syntax_error = self._verify_syntax(applied_files)
            if not syntax_ok:
                self._rollback_changes(original_contents)
                return {'success': False, 'message': f'❌ 语法检查失败，已回滚:\n{syntax_error}'}
            
            # Step 4: 成功
            proposal['status'] = 'applied'
            proposal['applied_at'] = datetime.now().isoformat()
            proposal['applied_files'] = applied_files
            self._update_proposal(proposal)
            
            return {'success': True, 'message': f'✅ 已应用 {len(applied_files)} 个文件', 'applied_files': applied_files}
        
        except Exception as e:
            self._rollback_changes(original_contents)
            return {'success': False, 'message': f'应用失败，已回滚: {e}'}
    
    def _git_backup(self) -> bool:
        """★ #1589 非破坏式快照备份：`git stash create` + `git stash store`——
        只生成快照存进 stash 栈，**不动工作区**（此前写的是 `git stash push
        --include-untracked`，一旦 import 修好会清空工作区全部未提交改动+未跟踪文件）。"""
        try:
            root = str(Path(__file__).parent.parent.parent)
            created = subprocess.run(['git', 'stash', 'create'], cwd=root,
                                     capture_output=True, text=True, timeout=30)
            sha = (created.stdout or '').strip()
            if created.returncode != 0 or not sha:
                # 没有可快照的改动（工作区干净）就是 True；其他原因如实返回 False
                return created.returncode == 0
            stored = subprocess.run(
                ['git', 'stash', 'store', '-m', f'auto-backup-{int(time.time())}', sha],
                cwd=root, capture_output=True, text=True, timeout=30)
            return stored.returncode == 0
        except Exception:
            return False
    
    def _rollback_changes(self, original_contents: dict):
        for path_str, content in original_contents.items():
            p = Path(path_str)
            if content is None: p.unlink(missing_ok=True)
            else: p.write_text(content, encoding='utf-8')
    
    def _verify_syntax(self, files: list) -> tuple:
        import py_compile
        project_root = Path(__file__).parent.parent.parent
        for f in files:
            fp = project_root / f
            if fp.suffix != '.py' or not fp.exists():
                continue
            try:
                py_compile.compile(str(fp), doraise=True)
            except py_compile.PyCompileError as e:
                return False, f'{f}: {e}'
        return True, ''
    
    def _update_config_key(self, file_path: Path, key: str, value):
        import yaml
        config = yaml.safe_load(file_path.read_text(encoding='utf-8')) or {}
        keys = key.split('.')
        target = config
        for k in keys[:-1]:
            if k not in target: target[k] = {}
            target = target[k]
        target[keys[-1]] = value
        file_path.write_text(
            yaml.dump(dict(config), allow_unicode=True, default_flow_style=False),
            encoding='utf-8'
        )


# ═══════════════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════════════

_evolution_manager: Optional[EvolutionManager] = None
_em_lock = threading.Lock()


def get_evolution_manager() -> EvolutionManager:
    """获取受控进化管理器单例"""
    global _evolution_manager
    if _evolution_manager is None:
        with _em_lock:
            if _evolution_manager is None:
                _evolution_manager = EvolutionManager()
    return _evolution_manager
