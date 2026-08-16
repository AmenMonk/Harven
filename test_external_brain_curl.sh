#!/bin/bash
# Harven AI 外脑连接诚实测试脚本
# 使用 curl 测试，避免 Python 环境混乱

BASE_URL="http://127.0.0.1:7000"

echo "=========================================="
echo "Harven AI 外脑连接诚实测试"
echo "=========================================="
echo ""

# 首先检查服务器是否可访问
echo "步骤1: 检查服务器是否可访问..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" ${BASE_URL}/)
if [ "$HTTP_CODE" != "200" ]; then
    echo "❌ 服务器不可访问 (HTTP: $HTTP_CODE)"
    echo "请先启动 Flask 服务器"
    exit 1
fi
echo "✅ 服务器可访问 (HTTP: $HTTP_CODE)"
echo ""

# 测试 direct 模式（外脑模式）
echo "步骤2: 测试 direct 模式（外脑模式）..."
echo "发送请求到 /api/chat (direct 模式)..."

# 构造 JSON 请求
JSON_DATA='{"message":"你好，请用一个词回答：苹果","conversation_id":"test_direct_'$(date +%s)'","query_mode":"direct","stream":true}'

# 发送请求并保存原始响应
RESPONSE_FILE="/tmp/harven_test_response.txt"
curl -s -X POST "${BASE_URL}/api/chat" \
    -H "Content-Type: application/json" \
    -d "${JSON_DATA}" \
    -N \
    --max-time 30 \
    > "${RESPONSE_FILE}" 2>&1 &

CURL_PID=$!
echo "请求已发送 (PID: $CURL_PID)"
echo "等待响应（最多30秒）..."

# 等待一下让响应开始
sleep 3

# 检查响应文件是否存在并有内容
if [ ! -f "${RESPONSE_FILE}" ]; then
    echo "❌ 响应文件不存在"
    exit 1
fi

echo ""
echo "原始响应（前100行）:"
echo "=========================================="
head -100 "${RESPONSE_FILE}"
echo "=========================================="
echo ""

# 分析响应
echo "步骤3: 分析响应..."
echo "检查是否收到文本内容..."

# 查找 SSE 事件
TEXT_FOUND=$(grep -c "event: text" "${RESPONSE_FILE}" || echo "0")
ERROR_FOUND=$(grep -c "event: error" "${RESPONSE_FILE}" || echo "0")

echo "  - text 事件数量: $TEXT_FOUND"
echo "  - error 事件数量: $ERROR_FOUND"

if [ "$ERROR_FOUND" -gt "0" ]; then
    echo ""
    echo "❌ 收到错误事件:"
    grep "event: error" -A 1 "${RESPONSE_FILE}" | head -20
    echo ""
    echo "结论: 外脑连接失败"
    exit 1
elif [ "$TEXT_FOUND" -gt "0" ]; then
    echo ""
    echo "✅ 收到文本事件:"
    grep "event: text" -A 1 "${RESPONSE_FILE}" | head -20
    echo ""
    echo "结论: 外脑连接成功"
    exit 0
else
    echo ""
    echo "❓ 未收到明确的 text 或 error 事件"
    echo "原始响应:"
    cat "${RESPONSE_FILE}" | head -50
    echo ""
    echo "结论: 无法确定外脑连接状态"
    exit 2
fi
