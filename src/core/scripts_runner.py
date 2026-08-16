# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — 中级 Skill 脚本执行引擎

让 Skill 从「纯提示词」升级为「可执行代码」：
- 沙箱执行：AST 预扫描禁止危险模块/函数
- 路径限制：脚本只能读写 skills/ 和 data/ 目录
- 结果捕获：stdout + 结构化返回值
- 支持多种脚本入口：main.py / run.py / 自定义

目录结构：
  data/skills/<skill-name>/
    SKILL.md          ← 声明 name / triggers / scripts
    scripts/
      main.py          ← 默认入口
      utils.py         ← 辅助模块
"""

import ast
import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent

# ★ 禁用的危险模块（AST 预扫描）
# 注意：os 和 sys 不在此列表 → 允许基础操作（makedirs/environ/path等）
#      但 os.system/os.popen 等危险调用由 AST visit_Call 单独拦截
BANNED_MODULES = {
    "subprocess",  # 直接执行系统命令
    "socket",      # 网络连接
    "shutil",      # 文件删除/移动
    "ctypes",      # 调用 C DLL
    "multiprocessing",
    "signal",
    "http.client","urllib.request","requests",  # 网络请求（脚本不能对外通信）
}

# 允许模块白名单
BANNED_FUNCTIONS = {
    "eval", "exec", "compile", "__import__",
}

# ★ 允许的模块白名单（比黑名单更安全）  
ALLOWED_MODULES = {
    "json", "csv", "re", "math", "random", "datetime",
    "collections", "itertools", "functools", "typing",
    "pathlib", "sqlite3", "hashlib", "base64", "uuid",
    "logging", "warnings", "copy", "textwrap",
    "html", "xml.etree.ElementTree", "urllib.parse",
    "numbers", "decimal", "fractions", "statistics",
    "string", "enum", "dataclasses",
    "configparser", "argparse",
    "PIL", "PIL.Image",  # 图片处理
    "io", "gzip", "zipfile", "tarfile",
}

# Skill 脚本的默认入口文件名
DEFAULT_ENTRY_SCRIPTS = ["main.py", "run.py", "handler.py", "index.py"]


# ═══════════════════════════════════════════════════════════════════════
# AST 安全检查
# ═══════════════════════════════════════════════════════════════════════

class ScriptSecurityError(Exception):
    """脚本安全违规异常"""
    pass


class ScriptSecurityScanner(ast.NodeVisitor):
    """用 AST 扫描脚本，检查危险操作"""

    def __init__(self, filename: str = "<script>"):
        self.filename = filename
        self.violations: List[str] = []
        self.imported_modules: set = set()

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self._check_import(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            full_name = f"{module}.{alias.name}" if module else alias.name
            self._check_import(full_name, node.lineno)
        self.generic_visit(node)

    def _check_import(self, name: str, lineno: int):
        """检查导入的模块是否安全"""
        # 获取顶级模块名
        top_module = name.split(".")[0]

        # 先检查白名单
        if top_module in ALLOWED_MODULES or name in ALLOWED_MODULES:
            self.imported_modules.add(name)
            return

        # 检查黑名单
        if top_module in BANNED_MODULES:
            self.violations.append(
                f"L{lineno}: 禁止导入 {top_module} 模块 ({name})"
            )
            return

        # 不在白名单也不在黑名单 → 安全通过（可选严格模式）
        self.imported_modules.add(name)

    def visit_Call(self, node: ast.Call):
        # 检查直接调用危险函数
        if isinstance(node.func, ast.Name):
            if node.func.id in BANNED_FUNCTIONS:
                self.violations.append(
                    f"L{node.lineno}: 禁止调用 {node.func.id}()"
                )
        # 检查 os.xxx / subprocess.xxx 等危险属性调用
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                obj_name = node.func.value.id
                attr_name = node.func.attr
                # os.system / os.popen → 拦截
                if obj_name == "os" and attr_name in ("system", "popen", "spawn", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "fork", "kill"):
                    self.violations.append(
                        f"L{node.lineno}: 禁止调用 os.{attr_name}()"
                    )

        self.generic_visit(node)


def scan_script_security(script_path: str) -> List[str]:
    """扫描脚本文件的安全性

    Args:
        script_path: .py 脚本文件路径

    Returns:
        违规列表（空列表 = 安全）

    Raises:
        ScriptSecurityError: 发现安全违规
    """
    with open(script_path, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source, filename=script_path)
    scanner = ScriptSecurityScanner(script_path)
    scanner.visit(tree)

    if scanner.violations:
        raise ScriptSecurityError(
            f"脚本 {os.path.basename(script_path)} 安全违规:\n" +
            "\n".join(f"  - {v}" for v in scanner.violations)
        )

    return scanner.violations


# ═══════════════════════════════════════════════════════════════════════
# 脚本执行器
# ═══════════════════════════════════════════════════════════════════════

def _find_skill_script(skill_name: str) -> Optional[str]:
    """查找技能的主脚本

    查找顺序：
    1. data/skills/<skill-name>/scripts/main.py
    2. data/skills/<skill-name>/scripts/run.py
    3. data/skills/<skill-name>/main.py (旧 music-gen 格式)
    4. data/skills/<skill-name>/generate.py (旧格式)
    """
    skill_dir = PROJECT_ROOT / "data" / "skills" / skill_name

    if not skill_dir.exists():
        return None

    # 优先 scripts/ 子目录
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.is_dir():
        for entry_name in DEFAULT_ENTRY_SCRIPTS:
            entry = scripts_dir / entry_name
            if entry.exists():
                return str(entry)

    # 回退：skill 目录下直接找
    for entry_name in DEFAULT_ENTRY_SCRIPTS:
        entry = skill_dir / entry_name
        if entry.exists():
            return str(entry)

    # 旧格式兼容
    for legacy in ["generate.py", "handler.py"]:
        legacy_path = skill_dir / legacy
        if legacy_path.exists():
            return str(legacy_path)

    return None


def run_skill_script(
    skill_name: str,
    action: str = "run",
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 300,
) -> Dict[str, Any]:
    """执行技能的可执行脚本

    工作流程：
    1. 找到技能的 scripts/ 目录
    2. AST 安全检查
    3. 注入 I/O Helper
    4. subprocess 执行
    5. 捕获 stdout/stderr → 返回结构化结果

    Args:
        skill_name: 技能名称（对应 data/skills/<skill_name>/）
        action: 操作名（传给脚本的 --action 参数）
        params: 其他参数（传给脚本的 --key value 参数）
        timeout: 执行超时（秒）

    Returns:
        {
            success: bool,
            output: str,        # stdout
            error: str,         # stderr 或异常信息
            skill_name: str,
            action: str,
        }
    """
    params = params or {}

    # 1. 查找脚本
    script_path = _find_skill_script(skill_name)
    if not script_path:
        return {
            "success": False,
            "output": "",
            "error": f"技能 '{skill_name}' 没有可执行脚本。请确保存在 data/skills/{skill_name}/scripts/main.py",
            "skill_name": skill_name,
            "action": action,
        }

    logger.info(f"[ScriptsRunner] 执行技能脚本: {skill_name} → {script_path}")

    # 2. 安全检查
    try:
        # 扫描主脚本
        violations = scan_script_security(script_path)
        if violations:
            return {
                "success": False,
                "output": "",
                "error": f"安全违规:\n" + "\n".join(f"  - {v}" for v in violations),
                "skill_name": skill_name,
                "action": action,
            }

        # 扫描 scripts/ 目录下其他 .py 文件
        scripts_dir = os.path.dirname(script_path)
        for fname in os.listdir(scripts_dir):
            if fname.endswith(".py") and fname != os.path.basename(script_path):
                scan_script_security(os.path.join(scripts_dir, fname))

    except ScriptSecurityError as e:
        logger.error(f"[ScriptsRunner] 安全扫描失败: {e}")
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "skill_name": skill_name,
            "action": action,
        }

    # 3. 构建命令行参数
    cmd = [sys.executable, "-u", script_path, "--action", action]

    # 通过环境变量传递结构化参数
    env = os.environ.copy()
    env["HARVEN_SKILL_PARAMS"] = json.dumps(params, ensure_ascii=False)
    env["HARVEN_SKILL_NAME"] = skill_name
    env["HARVEN_SKILL_ACTION"] = action
    env["HARVEN_PROJECT_ROOT"] = str(PROJECT_ROOT)

    # 4. 执行
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        logger.info(
            f"[ScriptsRunner] 脚本完成 (rc={result.returncode}) | "
            f"stdout={len(stdout)}字 stderr={len(stderr)}字"
        )

        return {
            "success": result.returncode == 0,
            "output": stdout[:50000],  # 限制输出长度
            "error": stderr[:5000] if result.returncode != 0 else "",
            "skill_name": skill_name,
            "action": action,
            "return_code": result.returncode,
            "stdout_full_length": len(stdout),
        }

    except subprocess.TimeoutExpired:
        logger.error(f"[ScriptsRunner] 脚本超时 (>{timeout}s): {skill_name}")
        return {
            "success": False,
            "output": "",
            "error": f"技能脚本执行超时（>{timeout}秒）",
            "skill_name": skill_name,
            "action": action,
        }
    except Exception as e:
        logger.error(f"[ScriptsRunner] 脚本执行异常: {e}")
        return {
            "success": False,
            "output": "",
            "error": f"脚本执行异常: {str(e)[:500]}",
            "skill_name": skill_name,
            "action": action,
        }


def run_skill_script_streaming(
    skill_name: str,
    action: str = "run",
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 300,
):
    """★ 2026-08-08: 流式执行Skill脚本 — yield每行stdout作为进度

    Yields:
        str: 每行输出（实时进度）
        dict: 最终结果（success/output/error）
    """
    params = params or {}
    script_path = _find_skill_script(skill_name)
    if not script_path:
        yield {"success": False, "output": "", "error": f"技能 '{skill_name}' 没有可执行脚本", "skill_name": skill_name, "action": action}
        return

    try:
        violations = scan_script_security(script_path)
        if violations:
            yield {"success": False, "output": "", "error": "安全违规", "skill_name": skill_name, "action": action}
            return
    except ScriptSecurityError as e:
        yield {"success": False, "output": "", "error": str(e), "skill_name": skill_name, "action": action}
        return

    cmd = [sys.executable, "-u", script_path, "--action", action]
    env = os.environ.copy()
    env["HARVEN_SKILL_PARAMS"] = json.dumps(params, ensure_ascii=False)
    env["HARVEN_SKILL_NAME"] = skill_name
    env["HARVEN_SKILL_ACTION"] = action
    env["HARVEN_PROJECT_ROOT"] = str(PROJECT_ROOT)

    try:
        import threading
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=str(PROJECT_ROOT),
        )
        stdout_lines = []
        stderr_lines = []

        def read_stderr():
            try:
                for line in proc.stderr:
                    stderr_lines.append(line)
            except:
                pass

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        for line in proc.stdout:
            stdout_lines.append(line)
            line_stripped = line.strip()
            if line_stripped:
                yield line_stripped

        proc.wait(timeout=timeout)
        stderr_thread.join(timeout=5)

        stdout = ''.join(stdout_lines).strip()
        stderr = ''.join(stderr_lines).strip()

        yield {
            "success": proc.returncode == 0,
            "output": stdout[:50000],
            "error": stderr[:5000] if proc.returncode != 0 else "",
            "skill_name": skill_name,
            "action": action,
        }

    except subprocess.TimeoutExpired:
        proc.kill()
        yield {"success": False, "output": "", "error": f"超时（>{timeout}秒）", "skill_name": skill_name, "action": action}
    except Exception as e:
        yield {"success": False, "output": "", "error": str(e)[:500], "skill_name": skill_name, "action": action}


def list_skill_scripts() -> List[Dict[str, Any]]:
    """列出所有拥有可执行脚本的技能

    Returns:
        [{skill_name, entry_point, has_scripts_dir, actions: []}, ...]
    """
    skills_dir = PROJECT_ROOT / "data" / "skills"
    if not skills_dir.exists():
        return []

    result = []
    for item in sorted(skills_dir.iterdir()):
        if not item.is_dir():
            continue

        skill_name = item.name
        script_path = _find_skill_script(skill_name)
        if not script_path:
            continue

        scripts_dir = item / "scripts"
        has_scripts_dir = scripts_dir.is_dir()

        # 列出 scripts 目录下的所有 .py 文件
        actions = []
        scan_dir = scripts_dir if has_scripts_dir else item
        for py_file in sorted(scan_dir.glob("*.py")):
            if py_file.name not in ("__init__.py",):
                action_name = py_file.stem
                actions.append({"name": action_name, "file": str(py_file.relative_to(PROJECT_ROOT))})

        result.append({
            "skill_name": skill_name,
            "entry_point": str(Path(script_path).relative_to(PROJECT_ROOT)),
            "has_scripts_dir": has_scripts_dir,
            "actions": actions,
        })

    return result


def get_skill_script_info(skill_name: str) -> Optional[Dict[str, Any]]:
    """获取单个技能脚本的详细信息"""
    script_path = _find_skill_script(skill_name)
    if not script_path:
        return None

    skill_dir = PROJECT_ROOT / "data" / "skills" / skill_name
    scripts_dir = skill_dir / "scripts"
    has_scripts_dir = scripts_dir.is_dir()

    actions = []
    scan_dir = scripts_dir if has_scripts_dir else skill_dir
    for py_file in sorted(scan_dir.glob("*.py")):
        if py_file.name not in ("__init__.py",):
            actions.append({"name": py_file.stem, "file": str(py_file.relative_to(PROJECT_ROOT))})

    return {
        "skill_name": skill_name,
        "entry_point": str(Path(script_path).relative_to(PROJECT_ROOT)),
        "has_scripts_dir": has_scripts_dir,
        "actions": actions,
        "skill_dir": str(skill_dir.relative_to(PROJECT_ROOT)),
    }
