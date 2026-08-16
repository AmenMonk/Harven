#!/bin/bash
set -e

echo "========================================"
echo "  Harven AI — Docker 启动中..."
echo "========================================"

# Ollama 连接地址（docker-compose 服务名作为 hostname）
OLLAMA_HOST="${OLLAMA_HOST:-ollama}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
OLLAMA_URL="http://${OLLAMA_HOST}:${OLLAMA_PORT}"

echo "[Entrypoint] Ollama 地址: $OLLAMA_URL"

# 等待 Ollama 就绪
echo "[Entrypoint] 等待 Ollama 服务就绪..."
for i in $(seq 1 30); do
    if curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        echo "[Entrypoint] Ollama 已就绪 (尝试 $i)"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "[Entrypoint] ⚠️ Ollama 连接超时，继续启动（后续可手动连接）"
    fi
    sleep 2
done

# 自动更新 config.yaml 中的 Ollama 地址
CONFIG_FILE="/app/config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    echo "[Entrypoint] 更新 config.yaml Ollama 地址 -> $OLLAMA_URL"
    python3 -c "
import yaml
with open('$CONFIG_FILE', 'r') as f:
    cfg = yaml.safe_load(f) or {}
if 'ollama' not in cfg:
    cfg['ollama'] = {}
cfg['ollama']['base_url'] = '$OLLAMA_URL'
cfg['ollama']['default_model'] = cfg.get('ollama', {}).get('default_model', 'qwen2.5:7b')
if 'hardware' not in cfg:
    cfg['hardware'] = {}
cfg['hardware']['allow_cpu_only'] = True
with open('$CONFIG_FILE', 'w') as f:
    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
print('[Entrypoint] config.yaml 已更新')
"
fi

# 启动 Harven AI（使用 Waitress 生产级 WSGI）
HARVEN_HOST="${HARVEN_HOST:-0.0.0.0}"
HARVEN_PORT="${HARVEN_PORT:-5000}"

echo "[Entrypoint] 启动 Harven AI Web 服务..."
echo "[Entrypoint] 访问地址: http://${HARVEN_HOST}:${HARVEN_PORT}"
echo "========================================"

exec python3 -c "
from waitress import serve
from web_app import app
import os

host = os.environ.get('HARVEN_HOST', '0.0.0.0')
port = int(os.environ.get('HARVEN_PORT', 5000))

print(f'[Waitress] Harven AI 运行在 {host}:{port}')
serve(app, host=host, port=port, threads=8)
"
