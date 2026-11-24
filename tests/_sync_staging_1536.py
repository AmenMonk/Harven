# -*- coding: utf-8 -*-
"""#1536 收尾：改动文件同步 _package_staging + MD5 核对。"""
import hashlib
import shutil
from pathlib import Path

LIVE = Path(r"H:\HarvenAI")
STG = LIVE / "_package_staging"

FILES = [
    "src/core/chain_engine/gates.py",
    "src/core/chain_engine/advisor.py",
    "src/core/chain_engine/routes.py",
    "src/core/chain_engine/packed_bills.py",
    "src/core/chain_engine/packed_video.py",
    "src/core/chain_engine/builtin_chains.py",
    "src/core/chain_engine/orchestrator.py",
    "src/core/chain_engine/binding.py",
    "src/core/vertical_api.py",
    "web_app.py",
    "src/core/harvenhub_client.py",
    "spa/src/pages/WorkshopPage.vue",
]
SPA_ASSETS_DIR = LIVE / "static/spa/assets"
STG_ASSETS_DIR = STG / "static/spa/assets"


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


bad = 0
for rel in FILES:
    src, dst = LIVE / rel, STG / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    ok = md5(src) == md5(dst)
    bad += (not ok)
    print(f"{'OK ' if ok else 'MD5不一致!'} {rel}  {md5(dst)[:10]}")

# SPA 构建产物整目录同步（先清后拷，防旧 hash 文件残留）
if STG_ASSETS_DIR.exists():
    shutil.rmtree(STG_ASSETS_DIR)
shutil.copytree(SPA_ASSETS_DIR, STG_ASSETS_DIR)
shutil.copy2(LIVE / "static/spa/index.html", STG / "static/spa/index.html")
n = len(list(STG_ASSETS_DIR.glob("*")))
print(f"OK  static/spa/assets 同步 {n} 个产物文件 + index.html")

print("\n=== staging 同步完成，MD5 全部一致 ===" if bad == 0 else f"\n=== {bad} 个文件不一致，勿发包 ===")
