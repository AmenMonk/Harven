# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
代码执行沙箱模块 — Harven AI
安全地执行 AI 生成或用户提供的代码片段

安全措施：
1. Docker 容器隔离（优先，真正的沙箱隔离）
2. 子进程隔离（降级方案，Docker 不可用时）
3. 超时机制（默认 15 秒）
4. 双重安全检测：Regex 黑名单 + AST 静态分析
5. 输出长度限制（防止刷屏）
6. 内存限制 + 网络隔离（Docker 模式）
"""
import re
import ast
import sys
import subprocess
import tempfile
import os
import json
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List, Set

logger = logging.getLogger(__name__)

# ─── Docker 可用性检测 ───

_docker_available_cache = None

def is_docker_available() -> bool:
    """检测 Docker 是否可用"""
    global _docker_available_cache
    if _docker_available_cache is not None:
        return _docker_available_cache
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True, text=True, timeout=5
        )
        _docker_available_cache = result.returncode == 0
        if _docker_available_cache:
            logger.info("[Sandbox] Docker 可用，将使用容器隔离模式")
        else:
            logger.info("[Sandbox] Docker 不可用，降级为子进程模式")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _docker_available_cache = False
        logger.info("[Sandbox] Docker 未安装，使用子进程模式")
    return _docker_available_cache

# ─── 危险模式黑名单 ───
DANGEROUS_PATTERNS = [
    # 系统破坏
    r"os\.system\s*\(",
    r"os\.popen\s*\(",
    r"subprocess\.(?:call|run|Popen|check_output|check_call)\s*\(",
    r"shutil\.rmtree\s*\(",
    r"shutil\.disk_usage\s*\(",         # 可能被滥用
    r"__import__\s*\(",
    r"importlib\.import_module\s*\(",
    # 文件破坏
    r"open\s*\([^)]*['\"]w['\"]",       # 写文件（慎重）
    r"\.unlink\s*\(",
    r"\.rmdir\s*\(",
    r"Path\s*\([^)]*\)\.rm",
    # 网络（沙箱模式下禁止）
    r"socket\.socket\s*\(",
    r"requests\.(?:get|post|put|delete|patch)\s*\(",
    # 代码注入
    r"exec\s*\(",
    r"eval\s*\(",
    r"compile\s*\(",
    r"globals\s*\(\s*\)\s*\[",
    r"locals\s*\(\s*\)\s*\[",
]

# ─── 安全检测（Regex + AST 双重防护） ───

# AST 危险函数/属性集合
_DANGEROUS_FUNCTIONS: Set[str] = {
    "eval", "exec", "compile", "globals", "locals", "__import__",
    "getattr", "setattr", "delattr", "breakpoint",
}
_DANGEROUS_MODULES: Set[str] = {
    "os", "subprocess", "shutil", "sys", "socket", "ctypes",
    "multiprocessing", "signal", "importlib",
}
_DANGEROUS_ATTRS: Set[str] = {
    "system", "popen", "call", "run", "Popen", "check_output",
    "check_call", "rmtree", "unlink", "rmdir", "remove",
    "socket", "connect", "bind", "listen",
}

def _scan_ast_for_danger(code: str) -> List[str]:
    """
    AST 静态分析：检测代码抽象语法树中的危险调用

    相比 Regex 黑名单的优势：
    - 可检测拼接绕过: exec('os.sy'+'stem("whoami")')
    - 可检测别名导入: import os as _o; _o.system("whoami")
    - 可检测属性访问: getattr(os, 'system')("whoami")
    """
    warnings = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # 语法错误无法解析 AST，跳过 AST 检查
        return warnings

    # 跟踪变量别名 (name -> actual_name)
    aliases: Dict[str, str] = {}

    for node in ast.walk(tree):
        # 1. 检测 import 别名
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                actual_name = alias.asname or alias.name
                if module in _DANGEROUS_MODULES:
                    warnings.append(f"AST: 导入了危险模块 `{alias.name}`"
                                    + (f" (别名: {actual_name})" if alias.asname else ""))
                    if alias.asname:
                        aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _DANGEROUS_MODULES:
                for alias in node.names:
                    warnings.append(f"AST: 从 `{node.module}` 导入了 `{alias.name}`")
                    if alias.asname:
                        aliases[alias.asname] = f"{node.module}.{alias.name}"

        # 2. 检测危险函数调用 eval/exec/compile
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                if func_name in _DANGEROUS_FUNCTIONS:
                    warnings.append(f"AST: 调用了危险函数 `{func_name}()`")
                elif func_name in aliases:
                    resolved = aliases[func_name]
                    if resolved.split(".")[0] in _DANGEROUS_MODULES:
                        warnings.append(f"AST: 通过别名 `{func_name}()` 调用了 `{resolved}`")

            # 3. 检测 os.system / subprocess.run 等属性链调用
            elif isinstance(node.func, ast.Attribute):
                attr_chain = []
                current = node.func
                while isinstance(current, ast.Attribute):
                    attr_chain.insert(0, current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    attr_chain.insert(0, current.id)

                full_chain = ".".join(attr_chain)
                # 直接危险调用
                if any(d in attr_chain for d in _DANGEROUS_ATTRS):
                    root = attr_chain[0]
                    if root in _DANGEROUS_MODULES or root in aliases:
                        warnings.append(f"AST: 调用了 `{full_chain}()`")
                # getattr 绕过: getattr(os, 'system')("whoami")
                if "getattr" in attr_chain and len(node.args) >= 2:
                    warnings.append("AST: 检测到 getattr() 动态属性访问，可能用于绕过检测")

            # 4. 检测 __import__ 的动态导入
            elif isinstance(node.func, ast.Name) and node.func.id == "__import__":
                warnings.append("AST: 调用了 `__import__()` 动态导入")

    return warnings


def scan_code_safety(code: str) -> Tuple[bool, List[str]]:
    """
    双重安全检测：Regex 黑名单 + AST 静态分析

    返回 (is_safe: bool, warnings: List[str])
    """
    warnings = []

    # 1. Regex 黑名单（快速模式匹配）
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, code):
            warnings.append(f"Regex: 检测到危险模式 `{pattern}`")

    # 2. AST 静态分析（语义级检测，防止绕过）
    try:
        ast_warnings = _scan_ast_for_danger(code)
        warnings.extend(ast_warnings)
    except Exception as e:
        logger.warning(f"[Sandbox] AST 分析异常: {e}")

    return len(warnings) == 0, warnings


# ─── 执行结果 ───
class ExecutionResult:
    def __init__(self,
                 success: bool,
                 stdout: str = "",
                 stderr: str = "",
                 return_value: Any = None,
                 error: str = "",
                 duration_ms: float = 0):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.return_value = return_value
        self.error = error
        self.duration_ms = duration_ms

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }

    def format_for_display(self) -> str:
        parts = []
        if self.stdout:
            parts.append(f"**输出：**\n```\n{self.stdout[:2000]}\n```")
        if self.stderr:
            parts.append(f"**错误输出：**\n```\n{self.stderr[:500]}\n```")
        if self.error:
            parts.append(f"**执行错误：** {self.error}")
        if not parts:
            parts.append("✅ 执行完成（无输出）")
        parts.append(f"⏱️ 耗时：{self.duration_ms:.0f}ms")
        return "\n\n".join(parts)


# ─── 沙箱执行器 ───
class CodeSandbox:
    """
    子进程隔离代码沙箱
    使用系统 Python 子进程执行，带超时和资源限制
    """

    def __init__(self,
                 timeout: int = 15,
                 max_output_chars: int = 5000,
                 allow_dangerous: bool = False,
                 prefer_docker: bool = True):
        self.timeout = timeout
        self.max_output_chars = max_output_chars
        self.allow_dangerous = allow_dangerous
        self.prefer_docker = prefer_docker
        self._docker_available = is_docker_available() if prefer_docker else False
        self._docker_image = "python:3.12-slim"
        self._node_image = "node:22-slim"
        # 使用 WorkBuddy 托管的 Python
        self._python_path = self._find_python()

    def _ensure_docker_image(self, image: str) -> bool:
        """确保 Docker 镜像存在"""
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                logger.info(f"[Sandbox] 拉取 Docker 镜像: {image}")
                subprocess.run(
                    ["docker", "pull", image],
                    capture_output=True, text=True, timeout=120
                )
            return True
        except Exception as e:
            logger.warning(f"[Sandbox] Docker 镜像准备失败: {e}")
            return False

    def _find_python(self) -> str:
        """找到可用的 Python 解释器（跨平台）"""
        candidates = [sys.executable]
        # 常见路径
        if sys.platform == "win32":
            candidates += ["python.exe", "python3.exe"]
        else:
            candidates += ["python3", "python", "/usr/bin/python3", "/usr/bin/python"]
        for p in candidates:
            if os.path.exists(p) or (not os.path.isabs(p) and self._which(p)):
                return p if os.path.isabs(p) else self._which(p) or sys.executable
        return sys.executable

    @staticmethod
    def _which(cmd: str) -> str:
        """跨平台 which"""
        try:
            import shutil
            return shutil.which(cmd) or ""
        except Exception:
            return ""

    def execute(self, code: str, language: str = "python") -> ExecutionResult:
        """
        执行代码，返回 ExecutionResult
        """
        import time
        start = time.time()

        # 安全检测
        if not self.allow_dangerous:
            is_safe, warnings = scan_code_safety(code)
            if not is_safe:
                warning_text = "\n".join(warnings)
                return ExecutionResult(
                    success=False,
                    error=f"⛔ 安全检测未通过，拒绝执行：\n{warning_text}"
                )

        if language == "python":
            return self._execute_python(code, start)
        elif language in ("javascript", "js"):
            return self._execute_js(code, start)
        elif language == "shell":
            return self._execute_shell(code, start)
        else:
            return ExecutionResult(False, error=f"不支持的语言：{language}（当前支持 python / javascript / shell）")

    def _execute_python(self, code: str, start_time: float) -> ExecutionResult:
        import time
        
        # 优先使用 Docker（如果可用）
        if self._docker_available and not self.allow_dangerous:
            return self._execute_python_docker(code, start_time)
        
        return self._execute_python_subprocess(code, start_time)

    def _execute_python_docker(self, code: str, start_time: float) -> ExecutionResult:
        """Docker 容器隔离执行 Python 代码"""
        import time
        if not self._ensure_docker_image(self._docker_image):
            return self._execute_python_subprocess(code, start_time)
        
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                tmp_path = f.name
            
            container_name = f"harven_sandbox_{os.getpid()}_{int(time.time())}"
            work_dir = "/sandbox"
            script_file = os.path.basename(tmp_path)
            
            # 复制文件到临时目录，mount 到容器
            sandbox_dir = Path(tmp_path).parent
            
            cmd = [
                "docker", "run", "--rm",
                "--name", container_name,
                "--memory", "256m",           # 内存限制 256MB
                "--memory-swap", "256m",       # 禁止 swap
                "--cpus", "1",                 # CPU 限制
                "--network", "none",           # 网络隔离
                "--read-only",                  # 只读文件系统
                f"--tmpfs", f"{work_dir}:exec", # tmpfs 临时写空间
                "-v", f"{sandbox_dir}:{work_dir}:ro",
                "-w", work_dir,
                self._docker_image,
                "python", "-u", script_file,
            ]
            
            proc = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=self.timeout + 5,  # Docker 额外 5s 容错
                encoding="utf-8", errors="replace",
            )
            duration = (time.time() - start_time) * 1000
            
            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=proc.stdout[:self.max_output_chars],
                stderr=proc.stderr[:500],
                error="" if proc.returncode == 0 else f"退出码 {proc.returncode}",
                duration_ms=duration
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, error=f"⏰ 执行超时（{self.timeout}秒）")
        except Exception as e:
            return ExecutionResult(False, error=f"Docker 执行失败: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception as _e: logger.debug(f"[code_sandbox] 忽略: {_e}")

    def _execute_python_subprocess(self, code: str, start_time: float) -> ExecutionResult:
        import time
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                tmp_path = f.name

            proc = subprocess.run(
                [self._python_path, tmp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )
            duration = (time.time() - start_time) * 1000

            stdout = proc.stdout[:self.max_output_chars]
            stderr = proc.stderr[:500]
            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                error="" if proc.returncode == 0 else f"退出码 {proc.returncode}",
                duration_ms=duration
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, error=f"⏰ 执行超时（{self.timeout}秒）")
        except Exception as e:
            return ExecutionResult(False, error=str(e))
        finally:
            try:
                os.unlink(tmp_path)
            except Exception as _e: logger.debug(f"[code_sandbox] 忽略: {_e}")

    def _execute_js(self, code: str, start_time: float) -> ExecutionResult:
        """执行 JavaScript（需要 Node.js）"""
        import time
        node_path = self._find_node()
        if not node_path:
            return ExecutionResult(False, error="Node.js 未安装，无法执行 JavaScript")

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                tmp_path = f.name

            proc = subprocess.run(
                [node_path, tmp_path],
                capture_output=True, text=True, timeout=self.timeout,
                encoding="utf-8", errors="replace"
            )
            duration = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=proc.stdout[:self.max_output_chars],
                stderr=proc.stderr[:500],
                error="" if proc.returncode == 0 else f"退出码 {proc.returncode}",
                duration_ms=duration
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, error=f"⏰ 执行超时（{self.timeout}秒）")
        except Exception as e:
            return ExecutionResult(False, error=str(e))
        finally:
            try:
                os.unlink(tmp_path)
            except Exception as _e: logger.debug(f"[code_sandbox] 忽略: {_e}")

    def _execute_shell(self, code: str, start_time: float) -> ExecutionResult:
        """执行 Shell 命令（Windows PowerShell）"""
        import time
        import re as _re
        # Shell 模式强制安全检测，不受 allow_dangerous 影响
        # ★ 2026-10-20 FC慢治理#3: 正则词边界（与 builtin_tools 同款）—
        #   子串 'format' 误伤 Format-List 实锤；Python 代码含 "format" 字符串不误杀
        _danger_patterns = [
            (r'\brm\s+(-[a-z]*r[a-z]*f|-rf|--recursive)', '递归强制删除'),
            (r'\bformat(\.com)?\s+[a-z]:', '磁盘格式化'),
            (r'\bdel\s+/f', '强制删除'),
            (r'\b(rmdir|rd)\s+/(s|q)', '递归删除'),
            (r'\bdd\s+if=', '磁盘写入'),
        ]
        _code_lower = code.lower()
        for _pat, _cat in _danger_patterns:
            if _re.search(_pat, _code_lower):
                return ExecutionResult(False, error=f"⛔ 检测到危险 Shell 命令：{_cat}")

        try:
            proc = subprocess.run(
                ["powershell", "-NonInteractive", "-Command", code],
                capture_output=True, text=True, timeout=self.timeout,
                encoding="utf-8", errors="replace"
            )
            duration = (time.time() - start_time) * 1000
            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=proc.stdout[:self.max_output_chars],
                stderr=proc.stderr[:500],
                duration_ms=duration
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(False, error=f"⏰ 执行超时（{self.timeout}秒）")
        except Exception as e:
            return ExecutionResult(False, error=str(e))

    def _find_node(self) -> Optional[str]:
        """查找 Node.js"""
        import shutil
        return shutil.which("node")


# ─── 从对话中提取代码块 ───
def extract_code_blocks(text: str) -> List[Dict[str, str]]:
    """
    从 Markdown 文本中提取代码块
    返回 [{"language": "python", "code": "..."}, ...]
    """
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    blocks = []
    for m in pattern.finditer(text):
        lang = m.group(1).lower() or "python"
        code = m.group(2).strip()
        if code:
            blocks.append({"language": lang, "code": code})
    return blocks


# ─── 自然语言意图检测 ───
def detect_code_execution_intent(user_message: str) -> bool:
    """
    检测用户是否想执行代码
    """
    patterns = [
        r"运行(?:一下|这个|这段|代码)?",
        r"执行(?:一下|这个|这段|代码)?",
        r"跑(?:一下|这个|这段|一遍)?",
        r"run\s+(?:this|the\s+code)",
        r"execute\s+(?:this|the\s+code)",
        r"帮我测试",
        r"测试一下这段",
        r"把这段.*?(?:运行|执行)",
    ]
    msg = user_message.lower()
    for p in patterns:
        if re.search(p, msg, re.IGNORECASE):
            return True
    return False


# ─── 单例 ───
_sandbox: Optional[CodeSandbox] = None

def get_sandbox(allow_dangerous: bool = False) -> CodeSandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = CodeSandbox(allow_dangerous=allow_dangerous)
    return _sandbox


# ─── 便捷入口 ───
def execute_code_from_message(user_message: str, code_to_execute: str = None,
                               language: str = "python") -> Optional[str]:
    """
    检测并执行代码，返回格式化结果字符串
    返回 None 表示没有执行意图
    """
    if not detect_code_execution_intent(user_message) and code_to_execute is None:
        return None

    sandbox = get_sandbox()
    if code_to_execute:
        result = sandbox.execute(code_to_execute, language)
    else:
        return None

    return result.format_for_display()
