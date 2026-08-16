# Harven AI — 云端一键部署指南

> **一句话部署**：租一台云服务器，复制粘贴 3 条命令，5 分钟上线。

---

## 📋 你需要准备

| 项目 | 说明 |
|------|------|
| 云服务器 | 阿里云/腾讯云/华为云 轻量应用服务器 |
| 系统 | Ubuntu 22.04（推荐）或 Debian 12 |
| 最低配置 | 2核 8G（CPU模式） / 4核 16G（推荐） |
| 月费参考 | ¥68-200（轻量应用服务器） |
| 域名 | 不需要（直接用 IP 访问） |

> 💡 **省钱技巧**：新用户通常有 3 折优惠，¥200 能买一年 4核16G。

---

## 🚀 一键部署（3 步）

### 第 1 步：连接服务器

```bash
ssh root@你的服务器IP
```

### 第 2 步：安装 Docker（复制整段运行）

```bash
curl -fsSL https://get.docker.com | bash
```

安装完成后，**退出 SSH 重新登录**（或运行 `newgrp docker`）。

### 第 3 步：部署 Harven AI

```bash
# 下载项目
git clone https://github.com/AmenMonk/harven-ai.git /opt/harven
cd /opt/harven

# 一键启动（首次需要 5-10 分钟拉取镜像和模型）
docker-compose up -d

# 查看启动日志
docker-compose logs -f
```

看到 `[Waitress] Harven AI 运行在 0.0.0.0:7000` 就成功了！

然后浏览器打开：**`http://你的服务器IP:7000`**

---

## 📊 启动后检查

```bash
# 检查容器状态
docker-compose ps

# 检查 Ollama 模型
curl http://localhost:11434/api/tags

# 测试 Harven AI
curl http://localhost:7000/health
```

正常输出：
```
harven-ollama    Up (healthy)
harven-ai        Up (healthy)
```

---

## ⚙️ 自定义配置

### 切换模型

编辑 `config.yaml`（或用 Web 端设置页面）：

```yaml
ollama:
  base_url: http://ollama:11434
  default_model: qwen2.5:14b  # 改为你想要的模型
```

然后在 Ollama 容器中拉取模型：

```bash
docker exec harven-ollama ollama pull qwen2.5:14b
docker-compose restart harven
```

### 修改端口

编辑 `docker-compose.yml`：

```yaml
services:
  harven:
    ports:
      - "8080:7000"  # 改为 8080 端口
```

### 使用域名 + HTTPS（可选）

```bash
# 安装 Nginx
apt install nginx -y

# 配置反向代理
cat > /etc/nginx/sites-available/harven << 'EOF'
server {
    listen 80;
    server_name 你的域名.com;

    location / {
        proxy_pass http://127.0.0.1:7000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF

ln -s /etc/nginx/sites-available/harven /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 安装 SSL 证书（免费 Let's Encrypt）
apt install certbot python3-certbot-nginx -y
certbot --nginx -d 你的域名.com
```

---

## 🛠️ 常用管理命令

```bash
# 重启
docker-compose restart

# 停止
docker-compose down

# 更新
git pull
docker-compose up -d --build

# 查看日志
docker-compose logs -f harven

# 进入容器调试
docker exec -it harven-ai bash

# 备份数据
tar -czf harven_backup_$(date +%Y%m%d).tar.gz data/
```

---

## ❓ 常见问题

### Q: 启动后访问不了？

```bash
# 检查防火墙
ufw allow 5000

# 检查云服务商安全组（阿里云/腾讯云控制台 → 安全组 → 入方向放行 5000）
```

### Q: 内存不够？

Ollama + 7B 模型至少需要 8GB 内存。换更大实例，或在 `docker-compose.yml` 中限制：

```yaml
services:
  ollama:
    deploy:
      resources:
        limits:
          memory: 6G
```

### Q: 速度太慢？

- CPU 模式：升级到 4核+ 实例
- GPU 模式：需要 GPU 云服务器（阿里云 GPU 实例 ¥3-5/小时）

### Q: 数据在哪里？

所有数据存储在 Docker volume 中：

```bash
docker volume ls | grep harven
# harven_ollama_data   → 模型文件
# harven_harven_data   → 对话记录/配置/知识库
```

---

## 🏠 也可以用自己电脑（不需要买服务器）

如果你的电脑配置够用（参考 README 硬件要求），直接本地运行即可：

```bash
# 安装 Ollama: https://ollama.com/download
ollama pull qwen2.5:14b

# 安装 Python 依赖
pip install -r requirements.txt

# 启动
python web_app.py
# 打开 http://localhost:7000
```

---

## 📞 支持

- 作者：AmenMonk
- 邮箱：38552540@qq.com
- 项目地址：_（发布时填写 GitHub 地址）_
