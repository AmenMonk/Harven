#!/bin/bash
# Harven AI 一键安装脚本 (Linux / macOS)
# ★ 2026-08-17: 内置 portable Python 3.13，跟 Windows 版一样开箱即用

set -e
cd "$(dirname "$0")"

echo "════════════════════════════════════════════"
echo "  🦆 Harven AI 哈文 — 一键安装"
echo "════════════════════════════════════════════"

# ── 确定 Python 目录 ──
# Linux: python/
# macOS: python_aarch64/ 或 python_x86_64/
PY_DIR=""
ARCH=$(uname -m)
OS_TYPE=$(uname -s)

if [ -f "python/bin/python3.13" ]; then
    PY_DIR="python"
elif [ -f "python_aarch64/bin/python3.13" ] && [ "$ARCH" = "arm64" -o "$ARCH" = "aarch64" ]; then
    PY_DIR="python_aarch64"
elif [ -f "python_x86_64/bin/python3.13" ]; then
    PY_DIR="python_x86_64"
elif [ -f "python/bin/python3" ]; then
    PY_DIR="python"
fi

if [ -n "$PY_DIR" ]; then
    # ★ 修复 symlinks（zip 不保留 Unix symlinks）
    if [ ! -L "$PY_DIR/bin/python3" ] && [ -f "$PY_DIR/bin/python3.13" ]; then
        echo "🔗 创建 python3 符号链接..."
        cd "$PY_DIR/bin"
        ln -sf python3.13 python3 2>/dev/null || true
        ln -sf python3.13 python 2>/dev/null || true
        ln -sf idle3.13 idle3 2>/dev/null || true
        ln -sf pydoc3.13 pydoc3 2>/dev/null || true
        cd - >/dev/null
    fi
    PYTHON="$PY_DIR/bin/python3.13"
    echo "✅ 使用内置 Python: $($PYTHON --version 2>&1)"
else
    # 回退：系统 Python 3.10+
    echo "⚠️ 未找到内置 Python，尝试系统 Python..."
    for cmd in python3 python; do
        if command -v $cmd &>/dev/null; then
            ver=$($cmd --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
            major=$(echo $ver | cut -d. -f1)
            minor=$(echo $ver | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
                PYTHON=$cmd
                echo "✅ 使用系统 Python: $($PYTHON --version 2>&1)"
                break
            fi
        fi
    done
    if [ -z "$PYTHON" ]; then
        echo "❌ 未找到 Python 3.10+"
        exit 1
    fi
fi

# ── 确保 start.sh 有执行权限 ──
chmod +x start.sh 2>/dev/null || true

# ── 创建虚拟环境 ──
if [ ! -d "venv" ]; then
    echo "🔧 创建虚拟环境..."
    $PYTHON -m venv venv
fi
source venv/bin/activate

# ── 安装依赖 ──
echo "📦 安装依赖（首次 3-10 分钟）..."
pip install --upgrade pip
pip install -r requirements.txt

# ── 创建配置 ──
if [ ! -f "config.yaml" ]; then
    echo "📝 创建默认配置..."
    cp config.yaml.example config.yaml 2>/dev/null || true
fi

# ── 创建数据目录 ──
mkdir -p data/sqlite data/faiss data/knowledge data/snapshots data/conversations data/config data/isolated_dbs

# ── 部署桌面快捷方式（双击启动） ──
# ★ 2026-08-17: 三平台一致体验 — Linux/macOS 也支持双击启动
HARVEN_DIR="$(pwd)"
if [ "$OS_TYPE" = "Darwin" ]; then
    # macOS: .command 文件（双击在 Terminal 运行）
    chmod +x HarvenAI.command 2>/dev/null || true
    # 复制到桌面
    USER_DESKTOP="$HOME/Desktop"
    if [ -d "$USER_DESKTOP" ]; then
        cp HarvenAI.command "$USER_DESKTOP/HarvenAI.command"
        chmod +x "$USER_DESKTOP/HarvenAI.command"
        echo "✅ 桌面快捷方式已创建：双击 HarvenAI.command 启动"
    fi
elif [ "$OS_TYPE" = "Linux" ]; then
    # Linux: .desktop 文件
    chmod +x HarvenAI.desktop 2>/dev/null || true
    USER_DESKTOP="$HOME/Desktop"
    USER_APPS="$HOME/.local/share/applications"
    mkdir -p "$USER_APPS" 2>/dev/null
    # 生成带绝对路径的 .desktop 文件
    if [ -d "$USER_DESKTOP" ]; then
        sed "s|Exec=bash -c.*|Exec=bash -c 'cd \"$HARVEN_DIR\" && bash start.sh'|" HarvenAI.desktop > "$USER_DESKTOP/HarvenAI.desktop"
        sed -i "s|Icon=icon-duck|Icon=$HARVEN_DIR/static/icon-duck.png|" "$USER_DESKTOP/HarvenAI.desktop" 2>/dev/null || true
        chmod +x "$USER_DESKTOP/HarvenAI.desktop"
        echo "✅ 桌面快捷方式已创建：双击 Harven AI 启动"
    fi
    # 也装到应用菜单
    if [ -d "$USER_APPS" ]; then
        sed "s|Exec=bash -c.*|Exec=bash -c 'cd \"$HARVEN_DIR\" && bash start.sh'|" HarvenAI.desktop > "$USER_APPS/HarvenAI.desktop"
        sed -i "s|Icon=icon-duck|Icon=$HARVEN_DIR/static/icon-duck.png|" "$USER_APPS/HarvenAI.desktop" 2>/dev/null || true
    fi
fi

echo ""
echo "════════════════════════════════════════════"
echo "  ✅ 安装完成！"
echo "════════════════════════════════════════════"
echo ""
if [ "$OS_TYPE" = "Darwin" ]; then
echo "  启动方法：双击桌面的 HarvenAI.command"
elif [ "$OS_TYPE" = "Linux" ]; then
echo "  启动方法：双击桌面的 Harven AI 图标"
else
echo "  启动方法：bash start.sh"
fi
echo "  或终端运行：bash start.sh"
echo "  浏览器打开：http://localhost:7000"
echo "  初始密码：  harven2026"
echo ""
echo "  可选：安装 Ollama（本地模型）"
echo "    macOS:  brew install ollama"
echo "    Linux:  curl -fsSL https://ollama.com/install.sh | sh"
echo "════════════════════════════════════════════"
