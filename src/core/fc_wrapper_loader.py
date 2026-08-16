"""
HarvenAI FC封装系统 - 加载器
启动时扫描 data/fc_wrappers/*.py，检测依赖是否安装，注册FC工具
"""
import os, importlib, logging
logger = logging.getLogger(__name__)

WRAPPERS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'fc_wrappers')

# 包名 → import名 映射
PKG_IMPORT_MAP = {
    'python-docx': 'docx',
    'python-pptx': 'pptx',
    'browser-use': 'browser_use',
    'windows-use': 'windows_use',
}

def is_package_installed(pkg_name):
    """检测Python包是否已安装"""
    import_name = PKG_IMPORT_MAP.get(pkg_name, pkg_name.replace('-', '_'))
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False

def load_fc_wrappers():
    """加载所有FC封装脚本，返回FC工具定义和函数"""
    tools = []  # FC工具定义
    handlers = {}  # 工具名 → 函数

    if not os.path.exists(WRAPPERS_DIR):
        return tools, handlers

    for fname in sorted(os.listdir(WRAPPERS_DIR)):
        if not fname.endswith('.py') or fname.startswith('_') or fname.startswith('loader'):
            continue

        fpath = os.path.join(WRAPPERS_DIR, fname)
        try:
            # 读取文件内容
            with open(fpath, 'r', encoding='utf-8') as f:
                code = f.read()

            # 检测依赖
            pkg_name = fname.replace('.py', '').replace('_', '-')
            if pkg_name == 'cli-anything-hub':
                pkg_name = 'cli_anything_hub'
            import_name = PKG_IMPORT_MAP.get(pkg_name, pkg_name.replace('-', '_'))

            # ★ 2026-07-29: 支持DEPENDENCY字段（封装实际依赖的包）
            # 先执行代码到DEPENDENCY定义，如果有就用它
            dep_match = None
            try:
                _ns_check = {}
                exec(compile(code.split('def ')[0], fname, 'exec'), _ns_check)
                dep_match = _ns_check.get('DEPENDENCY')
            except Exception:
                pass
            if dep_match:
                import_name = dep_match

            try:
                importlib.import_module(import_name)
            except ImportError:
                logger.debug(f"[FC Wrapper] {fname}: 依赖 {import_name} 未安装，跳过")
                continue

            # 执行封装脚本
            ns = {}
            exec(code, ns)

            # 提取工具定义和处理函数
            wrapper_tools = ns.get('TOOL_DEFINITIONS', [])
            for tool_def in wrapper_tools:
                tool_name = tool_def.get('name', '')
                handler = ns.get(tool_name)
                if handler and callable(handler):
                    tools.append(tool_def)
                    handlers[tool_name] = handler
                    logger.info(f"[FC Wrapper] 注册: {tool_name} (from {fname})")

        except Exception as e:
            logger.error(f"[FC Wrapper] 加载 {fname} 失败: {e}")

    return tools, handlers
