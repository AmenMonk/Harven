#!/usr/bin/env python3
"""
Harven AI — 打包前清理脚本
用途：在打包安装包/开源前，清除所有用户敏感数据

使用方法：
    python clean_before_pack.py          # 清理敏感数据
    python clean_before_pack.py --check  # 只检查不清理

清理内容：
1. API Keys（multi_models.json + config.yaml）
2. 对话历史（snapshots/ + conversations/ + sqlite/）
3. 知识库索引（faiss/）
4. 日志文件（*.log）
5. 设备绑定信息（device_binding.json + independence.lock）
6. 备份文件（backups/）
7. 导出文件（harvenhub_exports/）
"""

import logging
logger = logging.getLogger(__name__)
import os
import sys
import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent if __file__.startswith('archive') else Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"

# 颜色输出
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RESET = '\033[0m'

def color_print(color, msg):
    print(f"{color}{msg}{RESET}")

# ═══════════════════════════════════════════════════
# 清理项定义
# ═══════════════════════════════════════════════════

CLEAN_ITEMS = [
    # (描述, 文件/目录路径, 类型: file/dir/pattern, 是否保留目录)
    ("API Keys (multi_models.json)", "data/multi_models.json", "sanitize_json", True),
    ("API Keys (config.yaml model_keys)", "config.yaml", "sanitize_yaml", True),

    ("对话快照", "data/snapshots/", "dir", False),
    ("对话记录", "data/conversations/", "dir", True),
    ("SQLite 数据库", "data/sqlite/", "dir", True),
    ("FAISS 索引", "data/faiss/", "dir", True),
    ("孤立数据库", "data/isolated_dbs/", "dir", True),
    ("用户配置", "data/config/", "dir", True),

    ("日志文件", "data/harven.log", "file", False),
    ("日志文件", "data/vault_logs.db", "file", False),
    ("日志文件", "data/visitors.db", "file", False),

    ("设备绑定", "data/device_binding.json", "file", False),
    ("独立性锁", "data/independence.lock", "file", False),
    ("导入记录", "data/imported_files.json", "file", False),
    ("梦境状态", "data/dream_state.json", "file", False),
    ("模型目录", "data/model_catalog.json", "file", False),
    ("回退配置", "data/fallback_config.json", "file", False),
    ("首次启动", "data/sqlite/first_run.json", "file", False),

    ("备份目录", "data/backups/", "dir", False),
    ("导出目录", "data/harvenhub_exports/", "dir", False),
    ("协作记录", "data/collaborations/", "dir", False),
    ("媒体文件", "data/media/", "dir", True),

    ("协作数据", "data/collaborations/", "dir", False),

    # 归档脚本中的测试数据
    ("进化提案", "data/evolution/proposals.json", "file", False),
]


def sanitize_multi_models_json(filepath: Path) -> bool:
    """清理 multi_models.json 中的 API Keys"""
    if not filepath.exists():
        return False

    try:
        data = json.loads(filepath.read_text(encoding='utf-8'))
        changed = False

        # 清理 models 中的 api_key
        if 'models' in data:
            for model_id, model_cfg in data['models'].items():
                if isinstance(model_cfg, dict) and model_cfg.get('api_key'):
                    color_print(YELLOW, f"  → 清除 {model_id} 的 api_key")
                    model_cfg['api_key'] = ""
                    changed = True

            # 所有模型设为未启用
            for model_id, model_cfg in data['models'].items():
                if isinstance(model_cfg, dict):
                    model_cfg['enabled'] = False
            data['current_model'] = ""

        if changed:
            filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

        return changed
    except Exception as e:
        color_print(RED, f"  ✗ 清理失败: {e}")
        return False


def sanitize_config_yaml(filepath: Path) -> bool:
    """清理 config.yaml 中的 API Keys 和敏感配置"""
    if not filepath.exists():
        return False

    try:
        content = filepath.read_text(encoding='utf-8')
        changed = False
        lines = content.split('\n')
        new_lines = []

        in_model_keys = False
        for line in lines:
            stripped = line.strip()

            # 检测 model_keys 段
            if stripped == 'model_keys:':
                in_model_keys = True
                new_lines.append(line)
                continue
            elif in_model_keys and stripped and not stripped.startswith(' ') and not stripped.startswith('#'):
                in_model_keys = False

            # 清理 model_keys 下的 api_key
            if in_model_keys and ':' in stripped and not stripped.startswith('#'):
                key, _, val = stripped.partition(':')
                if val.strip() and val.strip() != "''" and val.strip() != '""':
                    color_print(YELLOW, f"  → 清理 config.yaml: {key.strip()} = ***")
                    new_lines.append(f"{line.split(':')[0]}: ''")
                    changed = True
                    continue

            # 清理 bing_api_key
            if 'bing_api_key:' in stripped and "''" not in stripped and not stripped.startswith('#'):
                color_print(YELLOW, f"  → 清理 bing_api_key")
                new_lines.append("bing_api_key: ''")
                changed = True
                continue

            # 清理 flask_secret_key
            if 'flask_secret_key:' in stripped and "''" not in stripped and not stripped.startswith('#'):
                color_print(YELLOW, f"  → 清理 flask_secret_key")
                new_lines.append("flask_secret_key: ''")
                changed = True
                continue

            # 清理 admin_password
            if 'admin_password:' in stripped and "''" not in stripped and "harven2026" not in stripped and not stripped.startswith('#'):
                color_print(YELLOW, f"  → 清理 admin_password")
                new_lines.append("admin_password: ''")
                changed = True
                continue

            new_lines.append(line)

        if changed:
            filepath.write_text('\n'.join(new_lines), encoding='utf-8')

        return changed
    except Exception as e:
        color_print(RED, f"  ✗ 清理失败: {e}")
        return False


def clean_item(desc, path_str, clean_type, keep_dir, check_only=False):
    """执行单个清理项"""
    full_path = PROJECT_ROOT / path_str

    if clean_type == 'sanitize_json':
        if not full_path.exists():
            return
        color_print(GREEN if check_only else YELLOW, f"  [{'检查' if check_only else '清理'}] {desc}")
        if not check_only:
            sanitize_multi_models_json(full_path)
        return

    if clean_type == 'sanitize_yaml':
        if not full_path.exists():
            return
        color_print(GREEN if check_only else YELLOW, f"  [{'检查' if check_only else '清理'}] {desc}")
        if not check_only:
            sanitize_config_yaml(full_path)
        return

    if not full_path.exists():
        return

    if clean_type == 'file':
        color_print(GREEN if check_only else YELLOW, f"  [{'检查' if check_only else '删除'}] {desc}: {path_str}")
        if not check_only:
            full_path.unlink()
    elif clean_type == 'dir':
        if full_path.is_dir():
            files = list(full_path.glob('*'))
            if files:
                color_print(GREEN if check_only else YELLOW,
                           f"  [{'检查' if check_only else '清空'}] {desc}: {path_str} ({len(files)} 项)")
                if not check_only:
                    if keep_dir:
                        # 保留目录，删除内容
                        for f in files:
                            if f.is_file():
                                f.unlink()
                            elif f.is_dir():
                                shutil.rmtree(f)
                    else:
                        # 删除整个目录
                        shutil.rmtree(full_path)


def scan_for_secrets():
    """扫描所有文件中的 API Key 残留"""
    color_print(GREEN, "\n🔍 扫描 API Key 残留...")

    import re
    key_pattern = re.compile(r'sk-[a-f0-9]{20,}', re.IGNORECASE)
    found = False

    for root, dirs, files in os.walk(PROJECT_ROOT):
        # 跳过目录
        dirs[:] = [d for d in dirs if d not in ['.venv', '__pycache__', '.git', 'node_modules',
                                                   'archive', 'data/models', '.workbuddy']]

        for fname in files:
            fpath = Path(root) / fname
            ext = fpath.suffix.lower()

            # 跳过二进制文件
            if ext in ['.bin', '.safetensors', '.gguf', '.onnx', '.pyc', '.pyo', '.dll', '.exe',
                       '.jpg', '.png', '.gif', '.bmp', '.ico', '.zip', '.db']:
                continue

            try:
                content = fpath.read_text(encoding='utf-8', errors='ignore')
                matches = key_pattern.findall(content)
                if matches:
                    rel_path = fpath.relative_to(PROJECT_ROOT)
                    color_print(RED, f"  ⚠ 发现密钥: {rel_path}")
                    for m in matches[:2]:
                        color_print(RED, f"    → {m[:15]}...{m[-4:]}")
                    found = True
            except Exception as _e: logger.debug(f"[clean_before_pack] 忽略: {_e}")

    if not found:
        color_print(GREEN, "  ✅ 未发现 API Key 残留")
    return found


# ═══════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════

def main():
    check_only = '--check' in sys.argv

    color_print(GREEN, "=" * 60)
    color_print(GREEN, "Harven AI — 打包前清理脚本")
    color_print(GREEN, f"模式: {'只检查' if check_only else '清理'}")
    color_print(GREEN, f"项目根: {PROJECT_ROOT}")
    color_print(GREEN, "=" * 60)

    # 1. 清理各项
    color_print(GREEN, "\n📋 清理项目清单:")
    for desc, path, clean_type, keep_dir in CLEAN_ITEMS:
        clean_item(desc, path, clean_type, keep_dir, check_only)

    # 2. 扫描残留密钥
    found_secrets = scan_for_secrets()

    # 3. 总结
    color_print(GREEN, "\n" + "=" * 60)
    if check_only:
        color_print(GREEN, "✅ 检查完成")
        if found_secrets:
            color_print(RED, "⚠ 发现 API Key 残留，请运行: python clean_before_pack.py")
        else:
            color_print(GREEN, "✅ 无敏感数据残留")
    else:
        color_print(GREEN, "✅ 清理完成")
        if found_secrets:
            color_print(RED, "⚠ 仍有密钥残留，请手动检查上述文件")
        else:
            color_print(GREEN, "✅ 所有敏感数据已清除，可安全打包/开源")
    color_print(GREEN, "=" * 60)


if __name__ == '__main__':
    main()
