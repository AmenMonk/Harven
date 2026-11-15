# Docker 部署

## 极简上手（5 行）

```bash
git clone https://github.com/AmenMonk/Harven.git
cd Harven
docker compose up -d --build
# 等构建完成（首次约 5~10 分钟）
浏览器打开 http://localhost:7000
```

默认管理员密码 `harven2026`，首次登录会要求修改。

## 数据在哪

你的所有数据（账本/面板/知识库/照片）都在宿主机 `./data` 目录里，容器删了数据也在。备份 = 复制这个目录。

## 自定义配置

```bash
cp config.example.yaml config.yaml   # 编辑后：
# 取消 docker-compose.yml 里 config 挂载行的注释，再 docker compose up -d
```

## 外脑模型

镜像不带任何模型（哈文铁律）。安装后在设置页填入你的外脑 API Key（DeepSeek / GLM / Kimi 等），或连接本地 Ollama（地址填 `http://host.docker.internal:11434`）。

## GPU（可选 · 实验性）

CPU 版开箱即用。有 NVIDIA 显卡想跑本地模型：先装 [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)，再在 docker-compose.yml 的 harven 服务下加：

```yaml
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `docker compose up -d --build` | 构建 + 启动 |
| `docker compose logs -f harven` | 看运行日志 |
| `docker compose down` | 停止（数据保留） |
| `docker compose down -v` | 停止（named 卷才受影响，bind 挂载不受影响） |
