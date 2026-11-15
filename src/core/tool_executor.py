# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — 工具执行层（Tool Execution Layer）
让模型输出能真正操作电脑：执行代码、读写文件、运行命令

设计思路（学习 WorkBuddy/OpenClaw）：
1. 模型输出 Python 代码 → 系统自动执行 → 结果返回对话
2. 安全的沙箱：限制文件范围、超时、禁止危险操作
3. 可开关：设置中可以关闭
"""

import os
import re
import subprocess
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目根目录（由脚本位置自动定位，任意电脑通用）
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 安全限制
ALLOWED_DIRS = []  # 空列表 = 允许所有目录（家庭用户场景）
# ★ 2026-07-25: 合并 security_guard 的 DANGEROUS_COMMANDS，统一一份列表
try:
    from src.core.security_guard import DANGEROUS_COMMANDS as _DC
    BLOCKED_COMMANDS = list(set(_DC + [
        '__import__("os").system',
        '__import__\\(.*\\)',  # 拦截各种 __import__ 变体
    ]))
except Exception:
    BLOCKED_COMMANDS = [
        'rm -rf', 'rm -f ', 'rm -r ', 'rm -fr',
        'rmdir /s', 'rmdir /q', 'del /s', 'del /f', 'del /q', 'del /a',
        'format ', 'mkfs', 'shutdown', 'reboot', 'halt', 'dd if=',
        'reg delete', 'taskkill /f /im explorer', 'taskkill /f /im svchost',
        'chmod 777 /', 'kill -9', 'systemctl stop', 'net stop', 'sc delete',
        '__import__("os").system',
    ]
# ★ 2026-07-21: format 从 BLOCKED_COMMANDS 移出，改为正则匹配
#   原因：简单子串匹配会误杀 URL 参数中的 format（如 wttr.in?format=%C）
#   现在只拦截作为磁盘格式化命令使用的情况（format C: / format D: 等）
BLOCKED_FORMAT_RE = re.compile(r'\bformat\s+[a-z]:', re.IGNORECASE)
# 也拦截 subprocess/os 调用中的 format 命令参数（如 ["format", "D:"]）
FORMAT_AS_CMD_RE = re.compile(r'["\']format["\']\s*,\s*["\'][a-z]:', re.IGNORECASE)
MAX_EXEC_TIME = 120  # 秒（普通代码，2026-07-17: 30→120秒，解决复杂命令被杀问题）
MAX_DOWNLOAD_TIME = 300  # ★ 2026-07-07: 下载类操作给5分钟

def init_allowed_dirs():
    """初始化允许的目录列表（用户家目录 + 项目目录）"""
    global ALLOWED_DIRS
    if not ALLOWED_DIRS:
        ALLOWED_DIRS = [
            str(Path.home()),
            str(Path(__file__).parent.parent.parent),
        ]
        # 常见盘符
        for drive in ['D:\\', 'E:\\', 'F:\\', 'G:\\']:
            if os.path.exists(drive):
                ALLOWED_DIRS.append(drive)

def is_path_allowed(path: str) -> bool:
    """检查路径是否在允许范围内"""
    if not ALLOWED_DIRS:
        return True
    resolved = str(Path(path).resolve())
    return any(resolved.startswith(str(Path(d).resolve())) for d in ALLOWED_DIRS if d)

def extract_code_blocks(text: str) -> list:
    """从模型输出中提取 Python 代码块

    分两层提取：
    1. 有栅栏的 ```python ... ``` 代码块（标准）
    2. 无栅栏但包含可执行命令的代码（fallback）
       - os.startfile(...)、subprocess.run(...)、open(...) 等
       - 自动包装 import 确保可执行
    """
    blocks = []

    # ===== Layer 1: 有栅栏的 ```python ... ``` =====
    fenced = r'```python\s*\n(.*?)```'
    seen = set()
    for match in re.finditer(fenced, text, re.DOTALL):
        code = match.group(1).strip()
        if code and len(code) >= 10:
            blocks.append(code)
            seen.add(code.strip())

    # ===== Layer 2: 无栅栏但包含可执行模式 =====
    # 提取独立的 Python 代码行（import + 函数调用）
    actionable_patterns = [
        # os.startfile('...')
        r'(?:^|\n)(import\s+os\s*\n+os\.startfile\([^)]+\))(?:\n|$)',
        # subprocess.run(...)
        r'(?:^|\n)(import\s+subprocess\s*\n+subprocess\.\w+\([^)]+\))(?:\n|$)',
        # 单独一行 os.startfile（如模型省略了 import os）
        r'(?:^|\n)(os\.startfile\([^)]+\))(?:\n|$)',
        # open(...).read() 或 with open(...) as f: 读取文件
        r'(?:^|\n)((?:import\s+\w+\s*\n+)?(?:with\s+open\([^)]+\)\s+as\s+\w+\s*:\s*\n\s+\w+\.\w+\([^)]*\)|\w+\s*=\s*open\([^)]+\)\.read\(\)))',
        # 写入文件：with open(..., 'w') as f: f.write(...)
        r'(?:^|\n)((?:import\s+\w+\s*\n+)?with\s+open\([^)]+,\s*[\'\"]w[\'\"]\)\s+as\s+\w+\s*:\s*\n\s+\w+\.write\([^)]+\))',
    ]

    for pattern in actionable_patterns:
        for match in re.finditer(pattern, text, re.MULTILINE):
            code = match.group(1).strip()
            if code and len(code) >= 10 and code.strip() not in seen:
                # 自动补上缺失的 import os（DeepSeek 常省略）
                if 'os.startfile' in code and 'import os' not in code:
                    code = 'import os\n' + code
                if 'subprocess.' in code and 'import subprocess' not in code:
                    code = 'import subprocess\n' + code
                blocks.append(code)
                seen.add(code.strip())

    return blocks

def is_safe_code(code: str) -> tuple:
    """安全检查：返回 (安全, 原因)"""
    code_lower = code.lower()
    
    # 检查危险命令（简单子串匹配）
    for dangerous in BLOCKED_COMMANDS:
        if dangerous.lower() in code_lower:
            return False, f"包含危险命令: {dangerous}"
    
    # ★ 2026-07-21: format 用正则检查（只拦截 format C: 磁盘格式化，不拦截URL参数）
    if BLOCKED_FORMAT_RE.search(code):
        return False, "包含危险命令: format (磁盘格式化)"
    if FORMAT_AS_CMD_RE.search(code):
        return False, "包含危险命令: format (磁盘格式化)"
    
    # 检查危险模块
    dangerous_imports = ['ctypes', 'winreg', 'win32api', 'win32com']
    for imp in dangerous_imports:
        if f'import {imp}' in code_lower:
            return False, f"禁止导入: {imp}"
    
    # ★ 2026-07-21: 拦截网络请求代码
    # 模型经常无视搜索结果自己写代码联网 → 被拦后编造数据
    # 直接在代码执行前拦截，明确告知模型用搜索结果
    _network_patterns = [
        'requests.get', 'requests.post', 'requests.put', 'requests.delete',
        'requests.head', 'requests.request', 'requests.session',
        'urllib.request', 'urlopen', 'httpx.get', 'httpx.post',
        'urllib3', 'http.client', 'socket.create_connection',
        'webbrowser.open', 'webbrowser.open_new', 'webbrowser.get',
    ]
    for pattern in _network_patterns:
        if pattern in code_lower:
            return False, (
                "代码包含网络请求（{}）。"
                "系统已提供搜索结果，请直接使用搜索结果中的数据回答用户问题，"
                "不要自己写代码联网。如搜索结果不足，请告知用户。"
            ).format(pattern)
    
    return True, ""


# ★ 2026-08-10: 自动补全缺失的 import
_COMMON_IMPORT_MAP = {
    'datetime': ['datetime.now()', 'datetime.datetime', 'datetime.date', 'datetime.timedelta'],
    'json': ['json.dumps(', 'json.loads(', 'json.dump(', 'json.load('],
    're': ['re.sub(', 're.match(', 're.search(', 're.compile(', 're.findall('],
    'os': ['os.path.join(', 'os.path.exists(', 'os.listdir(', 'os.makedirs(',
           'os.environ', 'os.getcwd(', 'os.path.dirname(', 'os.path.abspath('],
    'math': ['math.sqrt(', 'math.pi', 'math.ceil(', 'math.floor('],
    'random': ['random.randint(', 'random.choice(', 'random.random('],
    'collections': ['collections.Counter(', 'collections.defaultdict('],
    'hashlib': ['hashlib.md5(', 'hashlib.sha256(', 'hashlib.sha1('],
    'hmac': ['hmac.new('],
    'time': ['time.time(', 'time.sleep(', 'time.strftime('],
    'sys': ['sys.argv', 'sys.exit(', 'sys.path'],
}


def _auto_fix_imports(code: str) -> str:
    imports_to_add = []
    for module, patterns in _COMMON_IMPORT_MAP.items():
        already_imported = bool(re.search(rf'(^|\n)\s*import\s+{module}\b|\s*from\s+{module}\s+import', code))
        if already_imported:
            continue
        if any(p in code for p in patterns):
            imports_to_add.append(f"import {module}")
    if not imports_to_add:
        return code
    return '\n'.join(imports_to_add) + '\n' + code

def execute_python_code(code: str, timeout: int = MAX_EXEC_TIME) -> dict:
    """在沙箱中执行 Python 代码
    
    Returns: {'success': bool, 'output': str, 'error': str}
    """
    init_allowed_dirs()
    
    # 安全检查
    safe, reason = is_safe_code(code)
    if not safe:
        return {'success': False, 'output': '', 'error': f'安全检查: {reason}'}
    
    # ★ 2026-08-10: 自动补全缺失的 import
    code = _auto_fix_imports(code)
    
    # ★ Windows os.startfile() 用 ShellExecuteW 解析路径，
    #   不认 subprocess cwd 也不认 os.chdir() — 只认 绝对路径。
    #   所以用 monkey-patch 让 os.startfile() 自动把相对路径转绝对路径。
    #   注意：用 repr() 生成安全的字符串字面量，避免 \\ 转义问题。
    _proot_repr = repr(str(PROJECT_ROOT))  # e.g. 'H:\\\\HarvenAI' → 代码中 'H:\\HarvenAI'
    _proot_str = str(PROJECT_ROOT).replace("\\", "/")
    _patch = (
        # ★ 2026-07-21: 加载独立性模块，让子进程也有出站白名单保护
        #   防止模型写代码绕过 independence 模块的域名拦截
        "import sys as _sys\n"
        f"_sys.path.insert(0, '{_proot_str}')\n"
        "try:\n"
        "    from src.core.independence import _apply_outbound_patch, load_allowed_domains\n"
        "    load_allowed_domains()\n"
        "    _apply_outbound_patch()\n"
        "except Exception as _ie:\n"
        "    pass\n"
        # os.startfile 路径补丁
        f"import os as _os; _sf=_os.startfile\n"
        f"def _sfx(p):\n"
        f"  if not _os.path.isabs(p) and ':' not in p[:3]:\n"
        f"    p=_os.path.abspath(_os.path.join({_proot_repr},p))\n"
        f"  return _sf(p)\n"
        f"_os.startfile=_sfx\n"
    )
    code = _patch + code
    
    try:
        # 写入临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(code)
            tmp_path = f.name

        # ★ 2026-07-08: 用.venv的python执行（modelscope/openai等库装在.venv里）
        venv_python = str(Path(__file__).parent.parent.parent / '.venv' / 'Scripts' / 'python.exe')
        if not os.path.exists(venv_python):
            venv_python = 'python'  # 降级：没.venv就用系统python
        logger.info(f"[ToolExec] 用Python执行: {venv_python}")

        # 执行
        result = subprocess.run(
            [venv_python, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, 'PYTHONUNBUFFERED': '1'}
        )
        
        # 清理
        os.unlink(tmp_path)
        
        output = result.stdout.strip()
        error = result.stderr.strip()

        # ★ 2026-07-21: 去除 ANSI 颜色码（wttr.in 等服务会返回终端颜色码）
        output = re.sub(r'\x1b\[[0-9;]*m', '', output)

        # ★ 2026-07-18: 过滤 stderr 中的常见警告（Triton/xformers等），只保留真正的错误
        if error:
            warning_patterns = [
                'triton not found', 'Triton is not available', 'xformers',
                'WARNING:', 'UserWarning:', 'FutureWarning:',
                'flop_counter', 'optimizations will not be enabled',
            ]
            error_lines = [l for l in error.split('\n') if l.strip()
                          and not any(w in l for w in warning_patterns)]
            error = '\n'.join(error_lines).strip()

        if result.returncode != 0:
            return {
                'success': False,
                'output': output[:10000],
                'error': error[:3000] or f'退出码: {result.returncode}'
            }

        # 成功时：stderr 只保留过滤后的真正错误（通常为空）
        return {
            'success': True,
            'output': output[:10000] or '(执行成功，无输出)',
            'error': error[:500] if error else ''
        }
    
    except subprocess.TimeoutExpired:
        return {'success': False, 'output': '', 'error': f'代码执行超时（>{timeout}秒）'}
    except Exception as e:
        return {'success': False, 'output': '', 'error': str(e)}

def execute_shell_command(command: str, timeout: int = 30) -> dict:
    """执行 shell 命令（受限）"""
    # 安全限制：只允许读取类命令
    allowed_cmds = ['dir', 'ls', 'cat', 'type', 'echo', 'where', 'which', 'python', 'pip', 'node', 'npm']
    cmd_parts = command.strip().split()
    if not cmd_parts:
        return {'success': False, 'output': '', 'error': '空命令'}
    
    main_cmd = os.path.basename(cmd_parts[0]).lower()
    if main_cmd not in allowed_cmds and not cmd_parts[0].startswith(('python', 'pip', 'node')):
        return {'success': False, 'output': '', 'error': f'不允许的命令: {main_cmd}'}
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.home())
        )
        return {
            'success': result.returncode == 0,
            'output': result.stdout.strip()[:5000],
            'error': result.stderr.strip()[:3000] if result.returncode != 0 else ''
        }
    except Exception as e:
        return {'success': False, 'output': '', 'error': str(e)}


def execute_tools_from_response(response_text: str) -> list:
    """从模型响应中检测并执行工具调用，返回执行结果列表

    Returns: [{'code': str, 'success': bool, 'output': str, 'error': str}, ...]

    ★ 2026-07-04 安全加固：拦截通用模型幻觉出的"越权调用垂直模型"代码
    本地双模协作模式下，通用模型可能幻觉出 os.startfile('skills/musicgen-v2.py')
    等不存在的脚本路径。这些代码执行必然失败，还会暴露错误给用户。
    在执行前拦截，返回明确的友好提示。
    """
    code_blocks = extract_code_blocks(response_text)
    if not code_blocks:
        return []

    # ★ 越权调用模式黑名单（通用模型不应该自己调用垂直模型）
    VERTICAL_HALLUCINATION_PATTERNS = [
        r"os\.startfile\s*\(\s*['\"]skills?/",
        r"os\.startfile\s*\(\s*['\"]skills?\\",
        r"subprocess\.\w+\s*\(\s*['\"].*musicgen",
        r"subprocess\.\w+\s*\(\s*['\"].*bark",
        r"subprocess\.\w+\s*\(\s*['\"].*stable.diffusion",
        r"import\s+musicgen",
        r"from\s+musicgen",
        r"import\s+transformers.*MusicGen",
        r"from\s+transformers.*import.*MusicGen",
    ]

    # ★ 2026-07-06 F2: 拦截模型自己写的联网搜索代码（系统有内置Bing搜索）
    WEB_SEARCH_HALLUCINATION_PATTERNS = [
        # 直接调用搜索引擎
        r"requests\.get\s*\(\s*['\"].*(?:bing|google|baidu|sogou|duckduckgo)",
        r"requests\.post\s*\(\s*['\"].*(?:bing|google|baidu|sogou|duckduckgo)",
        r"urllib\.request\.urlopen\s*\(\s*['\"].*(?:bing|google|baidu)",
        r"urllib\.request\.urlretrieve\s*\(\s*['\"].*(?:search|bing|google)",
        # 变量URL形式：url = "https://www.bing.com/search"
        r"['\"]https?://[^'\"]*(?:bing|google|baidu|sogou|duckduckgo)[^'\"]*search",
        r"['\"]https?://[^'\"]*(?:bing|google|baidu|sogou|duckduckgo)[^'\"]*/s[\"'/?]",
        # requests.get + params= 形式（搜索请求特征）
        r"requests\.get\s*\([^)]*params\s*=",
        # ★ 2026-07-06: 新增 — 拦截所有 requests.get 访问财经/天气/新闻网站
        r"requests\.get\s*\(\s*['\"].*(?:finance|sina|eastmoney|xueqiu|weather|tianqi)",
        r"['\"]https?://[^'\"]*(?:finance\.sina|eastmoney|tianqi\.com|weather\.com)",
        # ★ 2026-07-06: 新增 — 拦截 import requests（模型不应该用requests，系统有内置搜索）
        r"import\s+requests\b.*requests\.get",
    ]

    results = []
    for code in code_blocks:
        # ★ 拦截越权调用
        is_violation = False
        for pattern in VERTICAL_HALLUCINATION_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                is_violation = True
                break

        if is_violation:
            logger.warning(f"[ToolExec] 拦截越权调用垂直模型的代码（{len(code)}字符），不执行")
            results.append({
                'code': code[:500],
                'success': False,
                'output': '',
                'error': '已拦截：通用模型试图直接调用垂直模型，应通过 vertical_api 调度。请在垂直面板操作。'
            })
            continue

        # ★ 2026-07-06 F2: 拦截模型自己写的联网搜索代码
        is_web_violation = False
        for pattern in WEB_SEARCH_HALLUCINATION_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                is_web_violation = True
                break

        if is_web_violation:
            logger.warning(f"[ToolExec] 拦截模型自己写的联网搜索代码（{len(code)}字符）")
            results.append({
                'code': code[:500],
                'success': False,
                'output': '',
                'error': '已拦截：模型不应自己写代码搜索网页。系统有内置Bing搜索，说"搜索XX"或含"天气/股价/新闻"等关键词会自动触发。'
            })
            continue

        logger.info(f"[ToolExec] 检测到代码块 ({len(code)}字符)，正在执行...")

        # ★ 2026-07-07: 下载/安装类操作给5分钟超时
        code_lower = code.lower()
        is_download_op = any(kw in code_lower for kw in [
            'snapshot_download', 'urlretrieve', 'pip install',
            'ollama pull', 'git clone', 'modelscope download'
        ])
        exec_timeout = MAX_DOWNLOAD_TIME if is_download_op else MAX_EXEC_TIME

        result = execute_python_code(code, timeout=exec_timeout)
        results.append({
            'code': code[:500],  # 截断（200→500，2026-07-17）
            'success': result['success'],
            'output': result['output'],
            'error': result['error']
        })

    return results


# ═══════════════════════════════════════════════════════════════
# ToolExecutor 类（2026-07-03 新增）
# ═══════════════════════════════════════════════════════════════
# 为 vertical_api.py 提供统一的类接口。
# 通用方法委托给模块级函数；ML 专用方法（图片/音乐/语音）
# 返回"模型未安装"的明确提示，避免 ImportError 崩溃。
# ═══════════════════════════════════════════════════════════════


class ToolExecutor:
    """工具执行器 — 统一类接口

    通用能力（已实现）：
        - execute_python(code)      执行 Python 代码
        - execute_shell(command)    执行受限 shell 命令
        - execute_from_response()   从模型响应提取并执行代码

    ML 专用能力（桩方法，需配套本地模型）：
        - run_image_gen(model_id, prompt, style=...)  → Stable Diffusion
        - run_musicgen(model_id, prompt, duration=...) → MusicGen
        - run_bark(model_id, text, voice_preset=...)  → Bark TTS

    ML 模型未安装时返回明确错误，不崩溃。
    """

    def __init__(self):
        init_allowed_dirs()

    # ── 通用方法（委托给模块级函数） ──

    def execute_python(self, code: str, timeout: int = MAX_EXEC_TIME) -> dict:
        """执行 Python 代码（沙箱）"""
        return execute_python_code(code, timeout=timeout)

    def execute_shell(self, command: str, timeout: int = 30) -> dict:
        """执行受限 shell 命令"""
        return execute_shell_command(command, timeout=timeout)

    def execute_from_response(self, response_text: str) -> list:
        """从模型响应中提取并执行代码块"""
        return execute_tools_from_response(response_text)

    # ── ML 专用方法（桩方法） ──
    # 当用户安装了 Stable Diffusion / MusicGen / Bark 等本地模型后，
    # 这些方法应替换为真正的推理调用。当前返回"未安装"提示。

    def run_image_gen(self, model_id: str, prompt: str, style: str = 'realistic',
                      width: int = 512, height: int = 512, steps: int = 20) -> dict:
        """Stable Diffusion 图片生成（需本地模型）"""
        logger.warning(f"[ToolExecutor] run_image_gen 被调用，但本地 SD 模型未安装")
        return {
            'success': False,
            'error': (
                f'图片生成模型（{model_id}）未安装。\n'
                'Harven AI 出厂不预置 Stable Diffusion 等大型 ML 模型。\n'
                '如需图片生成，请：\n'
                '  1. 在设置→模型管理中下载 SD 模型，或\n'
                '  2. 切换到「外脑模式」使用云端图片生成 API。'
            ),
            'output': ''
        }

    # ── 本地大模型推理（subprocess 隔离模式，2026-11-14）──
    # ★ 设计：ACE-Step/Z-Image/Wan2.2 依赖重（librosa/diffusers hooks 等），推理独占 GPU 8-10GB，
    #   一律走 subprocess 调 H:\AI_Models\*_gen.py 独立脚本，跑完进程退出自动释放显存，
    #   不污染哈文主进程内存。生成前先卸载 Ollama 驻留模型（keep_alive=0）腾显存。
    _GEN_SCRIPTS = {
        'run_ace_step': r'H:\AI_Models\acestep_gen.py',
        'run_zimage': r'H:\AI_Models\zimage_gen.py',
        'run_wan22': r'H:\AI_Models\wan22_gen.py',
    }
    _GEN_TIMEOUTS = {'run_ace_step': 900, 'run_zimage': 600, 'run_wan22': 3600}

    def _run_gen_subprocess(self, method: str, payload: dict) -> dict:
        """通用大模型推理 subprocess 执行器"""
        import sys as _sys
        import json as _json
        import time as _time
        script = self._GEN_SCRIPTS.get(method)
        if not script or not os.path.exists(script):
            return {'success': False, 'error': f'推理脚本缺失: {script}', 'output': ''}
        # 腾显存：卸载 Ollama 全部已加载模型（不只 huatuo——qwen3-vl 驻留 8GB 会挤爆生成进程）
        try:
            import urllib.request
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request('http://127.0.0.1:11434/api/ps')
            loaded = _json.loads(opener.open(req, timeout=3).read()).get('models', [])
            for m in loaded:
                try:
                    req2 = urllib.request.Request(
                        'http://127.0.0.1:11434/api/generate',
                        data=_json.dumps({'model': m['name'], 'prompt': '', 'keep_alive': 0}).encode(),
                        headers={'Content-Type': 'application/json'})
                    opener.open(req2, timeout=3).read()
                except Exception:
                    pass
            if loaded:
                _time.sleep(2)  # 等显存真正释放
        except Exception:
            pass
        # ★ 2026-11-14: 生成失败自动重试 1 次（哈文打标签线程会周期性拉起视觉模型抢显存，
        #   首跑 OOM 时第二跑通常已错峰）
        for attempt in (1, 2):
            try:
                proc = subprocess.run(
                    [_sys.executable, script, '--json', _json.dumps(payload, ensure_ascii=False)],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    timeout=self._GEN_TIMEOUTS.get(method, 900))
            except subprocess.TimeoutExpired:
                return {'success': False, 'error': '生成超时（任务过大，请缩短时长/步数）', 'output': ''}
            except Exception as e:
                logger.error(f"[{method}] 生成失败: {e}", exc_info=True)
                return {'success': False, 'error': f'生成失败: {e}', 'output': ''}
            # 解析脚本最后一行 JSON 结果
            result = None
            for line in reversed((proc.stdout or '').strip().splitlines()):
                line = line.strip()
                if line.startswith('{'):
                    try:
                        result = _json.loads(line)
                        break
                    except Exception:
                        continue
            # 脚本输出 JSON success 为准（退出码异常但结果成功也接受）
            ok = bool(result and result.get('success'))
            if ok:
                break
            if attempt == 1:
                logger.warning(f"[{method}] 第 1 次失败（exit={proc.returncode}），5 秒后重试…")
                _time.sleep(5)
        else:
            err_tail = ((proc.stderr or '')[-400:] or (result or {}).get('error') or '推理脚本无有效输出')
            return {'success': False, 'error': f'生成失败(重试后仍失败, exit={proc.returncode}): {err_tail}', 'output': ''}
        rel_file = result['file']
        secs = result.get('seconds', '?')
        # ★ 2026-11-14: 产物统一拷进 data/media/（前端走 /data/media/ 相对路径，与 MusicGen 时代一致；
        #   绝对路径 H:/... 会被转成 /H:/... 前端读不到）
        try:
            import shutil as _shutil
            src = os.path.abspath(rel_file)
            media_dir = PROJECT_ROOT / 'data' / 'media'
            media_dir.mkdir(parents=True, exist_ok=True)
            dst = media_dir / os.path.basename(src)
            if src != str(dst) and os.path.exists(src):
                _shutil.copy2(src, dst)
                rel_file = f"data/media/{dst.name}"
        except Exception as e:
            logger.warning(f"[{method}] 产物拷贝 data/media 失败（保留原路径）: {e}")
        # ★ 2026-11-15 (#1521 老谭拍板 A+B): wav 自动转 mp3（体积约 1/10，内嵌播放/下载都快）
        #   仅对 .wav 生效（zimage=png / wan22=mp4 天然不进此分支）；转码失败降级保留 wav 不报错
        mp3_rel = ''
        if rel_file.lower().endswith('.wav'):
            try:
                wav_abs = os.path.abspath(rel_file)
                mp3_abs = os.path.splitext(wav_abs)[0] + '.mp3'
                _ff = ''
                try:
                    import imageio_ffmpeg as _iif
                    _ff = _iif.get_ffmpeg_exe()  # 优先用 venv 内置 ffmpeg（装机用户免装系统 ffmpeg）
                except Exception:
                    pass
                _cmd = [_ff or 'ffmpeg', '-y', '-loglevel', 'error', '-i', wav_abs,
                        '-codec:a', 'libmp3lame', '-q:a', '4', mp3_abs]
                _proc = subprocess.run(_cmd, capture_output=True, text=True, timeout=600)
                if _proc.returncode == 0 and os.path.exists(mp3_abs):
                    mp3_rel = f"data/media/{os.path.basename(mp3_abs)}"
                    logger.info(f"[{method}] wav→mp3 完成: {mp3_rel} "
                                f"({os.path.getsize(wav_abs)//1024}KB → {os.path.getsize(mp3_abs)//1024}KB)")
                else:
                    logger.warning(f"[{method}] wav→mp3 失败（保留 wav）: {(_proc.stderr or '')[-200:]}")
            except Exception as e:
                logger.warning(f"[{method}] wav→mp3 异常（保留 wav）: {e}")
        out_line = f'生成成功（耗时 {secs}s，seed={result.get("seed")}）\n文件: {rel_file}'
        if mp3_rel:
            out_line += f'\nMP3: {mp3_rel}'
        return {'success': True, 'file': rel_file, 'mp3': mp3_rel,
                'output': out_line, 'error': ''}

    def run_ace_step(self, model_id: str, prompt: str, lyrics: str = '[inst]',
                     duration: int = 30, steps: int = 27, seed: str = '-1') -> dict:
        """ACE-Step 音乐生成（3.5B 本地，支持人声歌曲）"""
        return self._run_gen_subprocess('run_ace_step', {
            'prompt': prompt, 'lyrics': lyrics or '[inst]',
            'duration': duration, 'steps': steps, 'seed': seed})

    def run_zimage(self, model_id: str, prompt: str, width: int = 1024,
                   height: int = 1024, steps: int = 8, seed: str = '-1') -> dict:
        """Z-Image-Turbo 图片生成（6B 本地，8 步极速）"""
        return self._run_gen_subprocess('run_zimage', {
            'prompt': prompt, 'width': width, 'height': height,
            'steps': steps, 'seed': seed})

    def run_wan22(self, model_id: str, prompt: str, seconds: int = 5,
                  steps: int = 30, seed: str = '-1') -> dict:
        """Wan2.2 T2V 视频生成（14B GGUF 双专家，较慢）"""
        return self._run_gen_subprocess('run_wan22', {
            'prompt': prompt, 'seconds': seconds, 'steps': steps, 'seed': seed})

    def run_bark(self, model_id: str, text: str, voice_preset: str = 'v2/zh_speaker_0') -> dict:
        """Bark TTS 语音合成（需本地模型）"""
        logger.warning(f"[ToolExecutor] run_bark 被调用，但本地 Bark 模型未安装")
        return {
            'success': False,
            'error': (
                f'语音合成模型（{model_id}）未安装。\n'
                'Harven AI 出厂不预置 Bark TTS 等大型 ML 模型。\n'
                '请先在设置→模型管理中下载对应模型。'
            ),
            'output': ''
        }
