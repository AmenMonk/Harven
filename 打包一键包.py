#!/usr/bin/env python3
"""
Harven AI 一键包打包器（#825 配套）
用法：python 打包一键包.py [r编号]
- 白名单打包（修复旧包中文文件名乱码问题）
- 校验：必备文件齐全、无乱码名、upgrade.ps1 带 BOM
- 产物：桌面 HarvenAI_v{版本}_Windows_{日期}_r{N}.zip
"""
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime

BASE = r"H:\HarvenAI"
STAGING = os.path.join(BASE, "_package_staging")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

# 根目录白名单（r6 实际内容 + #825 新增：一键升级.bat / upgrade.ps1；修复：正确中文名）
ROOT_FILES = [
    ".gitignore", "README.md", "README.txt", "CONTRIBUTING.md", "VERSION",
    "agent_registry.py", "device_api.py", "main.py", "model_routes.py",
    "multi_model_api.py", "new_features_api.py",
    "plugin_mgmt_api.py", "setup_home_modules.py", "web_app.py",
    "requirements.txt", "version.json", "config.example.yaml",
    "config.yaml.example", "install.sh", "start.sh",
    # 注(09-22 r13)：harven_launch.bat 是旧启动器（已被 启动HarvenAI.bat 取代），不进用户包
    # 注(#835)：harven_detach.bat 是开发机自动化脚本，不进用户包
    "启动HarvenAI.bat", "创建桌面快捷方式.bat", "安装哈文.bat",
    "一键升级.bat", "upgrade.ps1",
    "HarvenAI_安装详解_v2.0.md", "开始使用.html",
]
DIRS = ["locales", "plugins", "skills", "spa", "src",
        "static", "templates", "tools"]   # data/ 单独精选，不整目录拷
# (#840) tests/ 不进用户包：开发机回归脚本（硬编码开发机路径+连开发机数据库）
# docs/ 白名单 (#839)：只带用户向文档，内部开发文档（含个人痕迹）不进用户包
DOCS_FILES = ["guide.html", "安装指南.html"]

# data/ 精选
DATA_FILES = {"multi_models.example.json", "fallback_config.json", "attributions.json"}
DATA_SUBDIRS = ["sqlite", "faiss", "knowledge", "snapshots", "isolated_dbs",
                "conversations", "config"]

# (r20 教训) data/panels 硬白名单：r20 包混入了 staging 实例面板 accounting_daily_1/
# （第4步对 staging data/ 整目录收集，staging 是持久目录、历史上混入的 panels 垃圾
#  会被照收进包）。出厂包的 panels 只允许以下出厂模板内容，白名单外一律清掉/拒收。
PANELS_WHITELIST_FILES = {"_groups.json", "_order.json"}
PANELS_WHITELIST_DIRS = {"fridge_stock_1", "memory_album"}

EXCLUDE_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules",
                ".pytest_cache", ".idea", ".vscode", "logs", "archive",
                "_dev_scripts", "harvenhub_exports", "collaborations"}
EXCLUDE_EXT = {".pyc", ".pyo", ".log", ".bak", ".tmp", ".db"}
REQUIRED = ["启动HarvenAI.bat", "一键升级.bat", "upgrade.ps1", "安装哈文.bat",
            "web_app.py", "version.json", "requirements.txt", "main.py",
            "config.yaml.example", "README.md", "开始使用.html"]


def is_mojibake(name: str) -> bool:
    """识别 UTF-8 被当 GBK 解出的乱码名（可反解回正常中文即为乱码）"""
    if "?" in name or "*" in name:
        return True
    try:
        back = name.encode("gbk").decode("utf-8")
        return back != name and any("\u4e00" <= ch <= "\u9fff" for ch in back)
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def should_skip(path: str) -> bool:
    parts = set(path.replace("\\", "/").split("/"))
    if parts & EXCLUDE_DIRS:
        return True
    if os.path.splitext(path)[1].lower() in EXCLUDE_EXT:
        return True
    return False


def copy_tree(src: str, dst: str):
    os.makedirs(dst, exist_ok=True)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel = os.path.relpath(root, src)
        for f in files:
            s = os.path.join(root, f)
            if should_skip(os.path.relpath(s, src)):
                continue
            d = os.path.join(dst, rel, f) if rel != "." else os.path.join(dst, f)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)


def clean_staging_panels():
    """(r20 教训) 第3步前的自洁闸：staging data/panels 白名单外内容自动清除。
    根治源头——第4步整目录收集只应该见到白名单内容。"""
    panels_dir = os.path.join(STAGING, "data", "panels")
    if not os.path.isdir(panels_dir):
        return
    removed = []
    for name in sorted(os.listdir(panels_dir)):
        p = os.path.join(panels_dir, name)
        ok = (name in PANELS_WHITELIST_FILES if os.path.isfile(p)
              else name in PANELS_WHITELIST_DIRS)
        if not ok:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            removed.append(name)
    if removed:
        print(f"[守卫] data/panels 自洁：清除白名单外 {len(removed)} 项 → {removed}")


def main():
    rev = (sys.argv[1] if len(sys.argv) > 1 else "1").lstrip("rR") or "1"
    os.chdir(BASE)

    # 1) 根文件 → staging（合并覆盖，不清空 staging 以保留 python/ 等大件）
    #    live 没有的根文件回退用 staging 现存副本（如 开始使用.html / VERSION 等）
    copied = 0; fallback = 0; missing = []
    for name in ROOT_FILES:
        s = os.path.join(BASE, name)
        if not os.path.exists(s):
            s = os.path.join(STAGING, name)
        if os.path.exists(s):
            dst = os.path.join(STAGING, name)
            if os.path.abspath(s) != os.path.abspath(dst):
                shutil.copy2(s, dst)
                if s.startswith(STAGING):
                    fallback += 1
                else:
                    copied += 1
            else:
                fallback += 1   # 本来就在 staging，无需复制
        else:
            missing.append(name)
    for name in missing:
        print(f"[警告] live 和 staging 都没有，跳过：{name}")
    print(f"[1/5] 根文件 live {copied} + staging回退 {fallback} / {len(ROOT_FILES)}")

    # 2) 目录
    for d in DIRS:
        s = os.path.join(BASE, d)
        if os.path.exists(s):
            copy_tree(s, os.path.join(STAGING, d))
    print(f"[2/5] 目录 {len(DIRS)} 个已同步")

    # 2.5) docs/ 白名单
    os.makedirs(os.path.join(STAGING, "docs"), exist_ok=True)
    for f in DOCS_FILES:
        s = os.path.join(BASE, "docs", f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(STAGING, "docs", f))
        else:
            print(f"[警告] docs 白名单文件缺失：{f}")
    print(f"[2.5/5] docs 白名单 {len(DOCS_FILES)} 个已同步")

    # 3) data/ 精选（不覆盖已有精选之外的内容）
    clean_staging_panels()   # (r20 教训) 先自洁 panels，防历史混入物被第4步收进包
    live_data = os.path.join(BASE, "data")
    st_data = os.path.join(STAGING, "data")
    os.makedirs(st_data, exist_ok=True)
    for f in os.listdir(live_data):
        p = os.path.join(live_data, f)
        if os.path.isfile(p) and f in DATA_FILES:
            shutil.copy2(p, os.path.join(st_data, f))
        elif os.path.isdir(p) and f == "skills":
            copy_tree(p, os.path.join(st_data, "skills"))
    for sub in DATA_SUBDIRS:
        os.makedirs(os.path.join(st_data, sub), exist_ok=True)
        gk = os.path.join(st_data, sub, ".gitkeep")
        if not os.path.exists(gk):
            open(gk, "w").close()
    print("[3/5] data/ 精选完成")

    # 4) 校验
    problems = []
    collected = []  # (staging绝对路径, zip内名)
    for name in ROOT_FILES:
        p = os.path.join(STAGING, name)
        if os.path.exists(p):
            collected.append((p, name))
    for d in DIRS:
        dd = os.path.join(STAGING, d)
        if not os.path.isdir(dd):
            continue
        for root, dirs, files in os.walk(dd):
            dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS]
            for f in files:
                full = os.path.join(root, f)
                arc = os.path.relpath(full, STAGING)
                if not should_skip(arc):
                    collected.append((full, arc))
    # (#839) docs 白名单收集
    for f in DOCS_FILES:
        p = os.path.join(STAGING, "docs", f)
        if os.path.exists(p):
            collected.append((p, os.path.join("docs", f)))
    # ★ 2026-09-21 (#828) 修复：data/ 精选（skills + 3 个 json + 空目录占位）此前
    #   只同步进 staging 却从未进 ZIP —— 第4步收集漏了 data。staging data 在
    #   第3步被约束为精选内容，这里整目录收集是安全的。
    st_data_dir = os.path.join(STAGING, "data")
    if os.path.isdir(st_data_dir):
        for root, dirs, files in os.walk(st_data_dir):
            dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS]
            # (r20 教训) panels 硬白名单第二道闸：第4步收集只认白名单，
            # 万一第3步自洁后有新混入，这里直接报错停包而不是静默收进包。
            # 判法：取 panels/ 后的第一级路径段（白名单目录内未来加子目录也不误杀）
            rel_root = os.path.relpath(root, st_data_dir).replace("\\", "/")
            parts = rel_root.split("/")
            in_panels = parts[0] == "panels"
            first_seg = parts[1] if len(parts) > 1 else ""
            skip_files = False
            if in_panels and first_seg and first_seg not in PANELS_WHITELIST_DIRS:
                dirs[:] = []          # 剪枝：非白名单目录不再深入
                skip_files = True
            elif in_panels and not first_seg:
                dirs[:] = [d for d in dirs if d in PANELS_WHITELIST_DIRS]
            for f in files:
                if skip_files:
                    bad = os.path.join(rel_root, f).replace(os.sep, "/")
                    problems.append(f"data/panels 白名单外目录(拒收): {bad}")
                    continue
                full = os.path.join(root, f)
                arc = os.path.relpath(full, STAGING)
                if in_panels and not first_seg and f not in PANELS_WHITELIST_FILES:
                    problems.append(f"data/panels 白名单外文件(拒收): {arc}")
                    continue
                if not should_skip(arc):
                    collected.append((full, arc))
    names = {arc for _, arc in collected}
    for req in REQUIRED:
        if req not in names:
            problems.append(f"缺必备文件: {req}")
    moji = [arc for _, arc in collected if is_mojibake(os.path.basename(arc))]
    if moji:
        problems.append(f"乱码文件名 {len(moji)} 个: {moji[:5]}")
    ups = os.path.join(STAGING, "upgrade.ps1")
    with open(ups, "rb") as fh:
        if fh.read(3) != b"\xef\xbb\xbf":
            problems.append("upgrade.ps1 缺 BOM（中文会乱码）")
    if problems:
        print("[校验失败]")
        for p in problems:
            print("  ✗", p)
        sys.exit(2)
    print(f"[4/5] 校验通过：{len(collected)} 个文件，无乱码名，必备文件齐全")

    # 5) 打 ZIP
    ver = "0.3.1"
    try:
        vj = json.load(open(os.path.join(STAGING, "version.json"), encoding="utf-8"))
        ver = vj.get("version", ver)
    except Exception:
        pass
    today = datetime.now().strftime("%Y%m%d")
    zip_name = f"HarvenAI_v{ver}_Windows_{today}_r{rev}.zip"
    zip_path = os.path.join(DESKTOP, zip_name)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arc in sorted(collected, key=lambda x: x[1]):
            zf.write(full, arc)
    md5 = hashlib.md5(open(zip_path, "rb").read()).hexdigest()
    size_mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"[5/5] 完成：{zip_name}")
    print(f"  位置：{zip_path}")
    print(f"  大小：{size_mb:.2f} MB | 条目：{len(collected)} | MD5：{md5}")


if __name__ == "__main__":
    main()
