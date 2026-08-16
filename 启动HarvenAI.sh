#!/bin/bash
# Harven AI 启动器 (Linux/macOS版)
# 首次运行自动创建虚拟环境 + 安装依赖

set -e

echo "========================================"
echo "  欢迎使用 Harven AI - 你的专属私有AI"
echo "========================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未检测到 Python 3.10+"
    echo "  Linux: sudo apt install python3 python3-venv python3-pip"
    echo "  macOS: brew install python@3.12"
    exit 1
fi

# 检查 Ollama
if ! command -v ollama &> /dev/null; then
    echo "[提示] 未检测到 Ollama (本地AI模型运行环境)"
    echo ""
    echo "  安装方法:"
    echo "    Linux: curl -fsSL https://ollama.com/install.sh | sh"
    echo "    macOS: 下载 https://ollama.com/download/Ollama-darwin.zip"
    echo ""
    read -p "是否用国内镜像加速下载? (y/n): " use_mirror
    if [ "$use_mirror" = "y" ]; then
        echo "请手动设置 Ollama 镜像:"
        echo "  export OLLAMA_MIRROR=https://modelscope.cn/api/v1/models"
    fi
    echo "安装完成后重启本启动器"
    exit 1
fi

# 创建虚拟环境
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "[首次运行] 正在创建 Python 虚拟环境..."
    python3 -m venv .venv
    echo "[首次运行] 正在安装依赖（约 3-5 分钟）..."
    .venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
fi

echo "[启动] 正在启动 Harven AI..."
echo ""
echo "启动成功后，请在浏览器中打开:"
echo "  http://localhost:7000"
echo ""
echo "按 Ctrl+C 可停止服务"
echo "========================================"
echo ""

.venv/bin/python web_app.py
