# Harven AI — Docker 部署（CPU 版）
# 用法: docker compose up -d --build → http://localhost:7000
FROM python:3.13-slim

WORKDIR /app

# 系统运行库（faiss/openblas/opencv 等需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libgomp1 libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用层缓存，代码改动不重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # 能力五件套（OCR/车牌）必须 --no-deps：声明依赖 opencv-python 会顶掉 headless 变体
    && pip install --no-cache-dir --no-deps rapidocr_onnxruntime==1.2.3 hyperlpr3==0.1.3

# 再拷代码
COPY . .

# 数据目录骨架 + 默认配置
RUN mkdir -p data/models data/faiss data/sqlite data/logs data/runtime \
    && cp -n config.example.yaml config.yaml || true

EXPOSE 7000

HEALTHCHECK --interval=60s --timeout=10s --start-period=150s --retries=3 \
    CMD curl -sf http://127.0.0.1:7000/ -o /dev/null || exit 1

CMD ["python", "web_app.py"]
