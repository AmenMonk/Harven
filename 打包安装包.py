#!/usr/bin/env python3
"""
Harven AI 安装包打包脚本
用法：python 打包安装包.py
"""

import os
import shutil
import zipfile
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
STAGING_DIR = BASE_DIR / "_package_staging"
ZIP_NAME = f"HarvenAI_v0.3.1_Windows_{datetime.now().strftime('%Y%m%d')}.zip"
ZIP_PATH = BASE_DIR / ZIP_NAME

# ===== 打包规则 =====

# 根目录需包含的文件（精确列表）
ROOT_INCLUDE = {
    "main.py", "web_app.py", "device_api.py", "model_routes.py",
    "multi_model_api.py", "new_features_api.py", "plugin_mgmt_api.py",
    "requirements.txt", "version.json",
    "README.md", "DEPLOY.md", "DISCLAIMER.md", "NOTICE",
    "LICENSE", "LICENSE.md", "LICENSES_THIRD_PARTY.md",
    "Dockerfile", "docker-compose.yml", "docker-entrypoint.sh", ".dockerignore",
    ".gitignore",
    "config.yaml.example", ".env.example",
    "一键安装启动.bat", "启动HarvenAI.bat", "启动HarvenAI_公网版.bat",
    "HarvenAI.bat", "启动HarvenAI.ps1",
    "打包说明.md",
    "HarvenAI_技术详解.html", "HarvenAI_技术详解.md",
}

# 需完整复制的目录
DIR_INCLUDE = {
    "src", "templates", "static", "plugins", "skills", "locales", "docs",
}

# data/ 下需保留的文件（排除运行时数据）
DATA_INCLUDE_FILES = {
    "multi_models.example.json", "fallback_config.json",
    "attributions.json",
}

# data/ 下需保留的子目录（含 .gitkeep）
DATA_SUBDIRS = {
    "sqlite", "faiss", "knowledge", "snapshots",
    "isolated_dbs", "conversations", "config",
}

# 全局排除模式
EXCLUDE_PATTERNS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".pytest_cache", ".idea", ".vscode",
}
EXCLUDE_EXT = {".pyc", ".pyo", ".log", ".bak", ".tmp"}
EXCLUDE_FILENAMES = {
    "first_run.json",          # 强制触发首次运行向导
    "independence.lock", "system_prompt.hash", "imported_files.json",
    "dream_state.json", "device_binding.json",
    "NUL", "NULL",
}


def should_exclude(path: Path) -> bool:
    """判断是否应排除"""
    # 检查路径任何部分是否匹配排除目录
    for part in path.parts:
        if part in EXCLUDE_PATTERNS:
            return True
    # 排除扩展名
    if path.suffix in EXCLUDE_EXT:
        return True
    # 排除特定文件名
    if path.name in EXCLUDE_FILENAMES:
        return True
    # 排除 .db 文件
    if path.suffix == ".db":
        return True
    # 排除 .index 文件（FAISS 索引可重建）
    if path.suffix == ".index" and "faiss" in path.parts:
        return True
    # 排除特定目录内容
    parts = set(path.parts)
    if "snapshots" in parts and path.suffix == ".json":
        return True
    if "harvenhub_exports" in parts:
        return True
    if "models" in parts and ".cache" in parts:
        return True
    if "collaborations" in parts:
        return True
    if "logs" in parts:
        return True
    if "archive" in parts:
        return True
    if "_dev_scripts" in parts:
        return True
    # 排除乱码 .bat 文件
    if path.suffix == ".bat" and any(ord(c) > 127 and ord(c) < 0x4e00 for c in path.name):
        return True
    return False


def copy_filtered(src: Path, dst: Path):
    """复制目录，过滤排除项"""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if should_exclude(item):
            continue
        dst_item = dst / item.name
        if item.is_dir():
            copy_filtered(item, dst_item)
        else:
            shutil.copy2(item, dst_item)


def build_staging():
    """构建临时打包目录"""
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir()

    # 1. 根目录文件
    print("[1/5] 复制根目录文件...")
    for name in sorted(ROOT_INCLUDE):
        src = BASE_DIR / name
        if src.exists():
            shutil.copy2(src, STAGING_DIR / name)
        else:
            print(f"  [跳过] 不存在: {name}")

    # 2. 完整目录
    print("[2/5] 复制源码目录...")
    for dname in sorted(DIR_INCLUDE):
        src = BASE_DIR / dname
        if src.exists():
            copy_filtered(src, STAGING_DIR / dname)
        else:
            print(f"  [跳过] 目录不存在: {dname}")

    # 3. data/ 目录（精选）
    print("[3/5] 复制 data/ 配置...")
    data_dst = STAGING_DIR / "data"
    data_dst.mkdir(exist_ok=True)

    # data/ 根下的 .json 配置文件
    data_src = BASE_DIR / "data"
    if data_src.exists():
        for f in data_src.iterdir():
            if f.is_file() and f.name in DATA_INCLUDE_FILES:
                shutil.copy2(f, data_dst / f.name)
            elif f.is_dir() and f.name == "skills":
                # 复制 data/skills/
                copy_filtered(f, data_dst / "skills")

    # data/ 子目录 + .gitkeep
    for sub in sorted(DATA_SUBDIRS):
        sub_dst = data_dst / sub
        sub_dst.mkdir(exist_ok=True)
        gitkeep = sub_dst / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("")

    # data/faiss/.gitkeep
    faiss_dst = data_dst / "faiss"
    faiss_dst.mkdir(exist_ok=True)
    gitkeep = faiss_dst / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("")

    # 4. 清理 staging 中的冗余文件
    print("[4/5] 清理临时文件...")
    for root, dirs, files in os.walk(STAGING_DIR):
        # 删除 __pycache__
        if "__pycache__" in dirs:
            shutil.rmtree(os.path.join(root, "__pycache__"))
            dirs.remove("__pycache__")
        # 删除 .bak 文件
        for f in files:
            if f.endswith(".bak") or "broken_" in f:
                os.remove(os.path.join(root, f))

    # 5. 打包成 ZIP
    print(f"[5/5] 创建 ZIP: {ZIP_NAME} ...")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(STAGING_DIR):
            for f in files:
                full = os.path.join(root, f)
                arcname = os.path.relpath(full, STAGING_DIR)
                zf.write(full, arcname)

    # 清理 staging
    shutil.rmtree(STAGING_DIR)

    size_mb = ZIP_PATH.stat().st_size / 1024 / 1024
    print()
    print("╔════════════════════════════════════════╗")
    print("║          打包完成！                    ║")
    print("╚════════════════════════════════════════╝")
    print(f"\n  文件：{ZIP_PATH.name}")
    print(f"  大小：{size_mb:.1f} MB")
    print(f"\n  路径：{ZIP_PATH}")
    print("\n  新电脑安装步骤：")
    print("  1. 解压 ZIP 到任意目录（路径不含中文）")
    print("  2. 先安装 Ollama：https://ollama.com/download/windows")
    print("  3. 双击 一键安装启动.bat")
    print("  4. 跟随引导页完成初始化\n")


if __name__ == "__main__":
    build_staging()
