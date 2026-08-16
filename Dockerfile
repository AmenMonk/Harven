# Harven AI 跨平台 Docker 部署
# 用法: docker compose up -d → http://localhost:7000
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

RUN mkdir -p data/models data/faiss data/sqlite

EXPOSE 5000
CMD ["python", "web_app.py"]