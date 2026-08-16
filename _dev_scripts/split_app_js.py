"""
将 app.js 按功能域拆分为多个模块文件
"""
import os
import re
import sys
import shutil
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

SRC = Path(__file__).parent.parent / "static" / "js" / "app.js"
OUT_DIR = Path(__file__).parent.parent / "static" / "js" / "modules"

# 定义拆分边界（(起始行号_1based, 文件名)）
# 从 section_analysis 得到的分割点
SPLITS = [
    (1,    "app-core.js"),         # 消息队列 Init Input (1-111)
    (112,  "app-conversation.js"), # Ollama状态 Conversations Messages (112-629)
    (629,  "app-send.js"),         # Send Message 消息队列 (629-1061)
    (1062, "app-media.js"),        # Voice Video Audio Attach Camera File Image (1062-1400)
    (1401, "app-knowledge.js"),    # Specializations KnowledgeBase MultiModel Tasks (1401-1937)
    (1938, "app-monitor.js"),      # Monitor Settings OllamaDetail Modal AutoClassify Helpers (1938-2705)
    (2706, "app-plugins.js"),      # Plugin Market Skills PluginMgmt Settings Tab (2706-3389)
    (3390, "app-network.js"),      # Cloudflare 访问码 访客统计 Scheduler Workflow Sandbox Browser Channels (3390-4113)
    (4114, "app-devices.js"),      # 设备管理 登录管理 (4114-4475)
]

def split_app_js():
    print(f"读取 {SRC}...")
    with open(str(SRC), "r", encoding="utf-8") as f:
        lines = f.readlines()
    total = len(lines)
    print(f"总行数: {total}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 生成分割区间
    boundaries = [(SPLITS[i][0] - 1, SPLITS[i+1][0] - 1, SPLITS[i][1])
                  for i in range(len(SPLITS) - 1)]
    # 最后一个模块到末尾
    boundaries.append((SPLITS[-1][0] - 1, total, SPLITS[-1][1]))

    script_tags = []
    for start_idx, end_idx, fname in boundaries:
        chunk = lines[start_idx:end_idx]
        out_path = OUT_DIR / fname
        with open(str(out_path), "w", encoding="utf-8") as f:
            # 写入文件说明注释
            module_comment = fname.replace("app-", "").replace(".js", "").upper()
            f.write(f"/* ===== Harven AI — {module_comment} module ===== */\n")
            f.writelines(chunk)
        size = out_path.stat().st_size // 1024
        line_count = len(chunk)
        print(f"  [OK] {fname}: {line_count} lines, {size}KB")
        script_tags.append(f'<script src="/static/js/modules/{fname}"></script>')

    # 备份原始 app.js
    backup_path = SRC.parent / "app.js.bak"
    shutil.copy2(str(SRC), str(backup_path))
    print(f"\n[OK] Backed up original to {backup_path}")

    # 生成 HTML script 标签
    print("\n\n=== 在 index.html 中替换原来的 <script src=...app.js...> 为以下内容 ===")
    for tag in script_tags:
        print(f"    {tag}")

    print(f"\n[OK] Module files written to {OUT_DIR}")
    return script_tags

if __name__ == "__main__":
    split_app_js()
