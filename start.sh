#!/bin/bash
# Harven AI 启动脚本 (Linux / macOS)
# ★ 2026-08-17: 内置 portable Python 3.13，跟 Windows 版一样开箱即用

set -e
cd "$(dirname "$0")"

echo "════════════════════════════════════════════"
echo "  🦆 Harven AI 哈文 — 启动中..."
echo "════════════════════════════════════════════"

# ── 确定 Python 命令 ──
# 优先级：venv > 内置 python/ > 系统 python3
PYTHON=""
PY_DIR=""

if [ -d "venv" ]; then
    if [ -z "$VIRTUAL_ENV" ]; then
        echo "🔄 激活虚拟环境..."
        source venv/bin/activate
    fi
    PYTHON=python
elif [ -f "python/bin/python3.13" ]; then
    # Linux 内置 Python
    PY_DIR="python"
    PYTHON="python/bin/python3.13"
    echo "✅ 使用内置 Python: $($PYTHON --version 2>&1)"
elif [ -f "python/bin/python3" ]; then
    PY_DIR="python"
    PYTHON="python/bin/python3"
    echo "✅ 使用内置 Python: $($PYTHON --version 2>&1)"
else
    # macOS: 根据架构选 Python
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
        PY_DIR="python_aarch64"
    else
        PY_DIR="python_x86_64"
    fi
    if [ -f "$PY_DIR/bin/python3.13" ]; then
        PYTHON="$PY_DIR/bin/python3.13"
        echo "✅ 使用内置 Python ($ARCH): $($PYTHON --version 2>&1)"
    elif [ -f "$PY_DIR/bin/python3" ]; then
        PYTHON="$PY_DIR/bin/python3"
        echo "✅ 使用内置 Python ($ARCH): $($PYTHON --version 2>&1)"
    else
        # 回退：系统 Python
        for cmd in python3 python; do
            if command -v $cmd &>/dev/null; then
                PYTHON=$cmd
                echo "⚠️ 使用系统 Python: $($PYTHON --version 2>&1)"
                break
            fi
        done
    fi
fi

if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python，请先运行: bash install.sh"
    exit 1
fi

# ── 检查依赖 ──
if ! $PYTHON -c "import flask" &>/dev/null; then
    echo "📦 依赖未安装，正在安装..."
    $PYTHON -m pip install -r requirements.txt
fi

# ── 检查 config.yaml ──
if [ ! -f "config.yaml" ]; then
    echo "📝 创建默认配置..."
    cp config.yaml.example config.yaml 2>/dev/null || true
fi

# ── 启动 ──
echo "🚀 启动 Harven AI..."
echo "   浏览器打开: http://localhost:7000"
echo "   初始密码:   harven2026"
echo "   按 Ctrl+C 停止"
echo "════════════════════════════════════════════"

exec $PYTHON web_app.py
