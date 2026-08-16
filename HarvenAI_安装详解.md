# Harven AI 安装详解 v2.0

> **最后更新**: 2026-06-26 | **适用版本**: v0.9+

---

## 总览

双击 `一键安装启动.bat` → 4 步自动 → 首次引导 → 进入对话。

```
双击 bat
├── [1/4] 检查 Python（需 3.11+）
├── [2/4] 创建 .venv 虚拟环境
├── [3/4] pip install -r requirements.txt
├── [4/4] 生成 config.yaml（如不存在）
├── 检查 本地通用模型（可选）
└── python web_app.py

→ 首次引导页
    ├── Step 1: 同意协议
    ├── Step 2: 硬件检测 + 模型推荐
    └── Step 3: 下载/选择模型 → 完成
```

---

## 一、前置准备

### 1.1 硬件最低要求

| 组件 | CPU-Only 模式 | GPU 加速模式 |
|------|-------------|------------|
| CPU | 4 核 | 4 核+ |
| 内存 | 16 GB | 32 GB |
| 磁盘 | 50 GB 可用 | 100 GB+ SSD |
| 显卡 | 无要求 | RTX 3060 12GB+ |
| 系统 | Windows 10/11 64-bit | Windows 11 |

### 1.2 需要自行安装

| 软件 | 说明 | 下载 |
|------|------|------|
| **Python 3.11+** | 安装时勾选 "Add Python to PATH" | python.org |
| **本地通用模型**（推荐） | 本地模型引擎 | ollama.com/download |

---

## 二、Windows 安装（推荐）

### 2.1 一键安装

```powershell
# 1. 解压 HarvenAI_安装包.zip 到任意目录（路径不要有中文）
# 2. 双击 一键安装启动.bat
# 3. 等待完成（首次 pip install 需 3-5 分钟）
```

### 2.2 手动安装

```powershell
cd 解压目录
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.yaml.example config.yaml
python web_app.py
```

### 2.3 下载本地模型

```powershell
# 推荐默认模型（通用对话）
ollama pull qwen2.5:14b

# 向量化模型（知识库搜索必需）
ollama pull nomic-embed-text

# 可选模型
ollama pull deepseek-r1:14b    # 推理增强
ollama pull minicpm-v:8b       # 多模态/图片
```

### 2.4 浏览器访问

打开 **http://localhost:7000** → 首次引导 3 步 → 完成。

---

## 三、Linux / macOS（Docker）

```bash
docker compose up -d
# → http://localhost:7000
```

> Docker 部署会自动检测 Ollama 地址并配置 Waitress 生产服务器。

---

## 四、三种模式速览

| 按钮 | 引擎 | 共享能力 | 场景 |
|------|------|---------|------|
| 🏠 本地 | 本地引擎 | 知识库+Skills+画像+习惯+诚实 | 离线/隐私 |
| 🤖 外脑 | 外部模型 | 知识库+Skills+画像+习惯+诚实 | 强推理 |
| 🤝 协作 | 外脑→本地整合 | 知识库+Skills+画像+习惯+诚实 | 综合最强 |

---

## 五、配置外脑（可选）

编辑 `config.yaml`：

```yaml
# DeepSeek 示例
deepseek_api_key: "sk-xxx"
deepseek_enabled: true

# 协作模式
collab_enabled: false             # 默认关闭
collab_foreign_model: "deepseek"  # 协作外脑
collab_privacy_level: "balanced"  # strict/balanced/full
```

---

## 六、常见问题

### Q: 启动后页面空白？
A: 检查 本地通用模型 是否运行：`ollama list`

### Q: 外脑模式无法连接？
A: 检查 `config.yaml` 中 API Key 是否配置正确

### Q: "本地模型未返回内容"？
A: 模型可能未加载，等待 10 秒重试；或换轻量模型

### Q: 如何备份数据？
A: 复制 `data/` 目录即可（含记忆/知识库/对话/画像）

---

## 七、目录结构

```
HarvenAI/
├── web_app.py              # 主入口
├── 一键安装启动.bat         # Windows 一键脚本
├── config.yaml.example     # 配置模板
├── requirements.txt        # Python 依赖
├── src/                    # 后端代码（60+ 模块）
├── templates/              # HTML 模板
├── static/                 # JS/CSS/图标
├── skills/                 # Agent 脚本
├── data/
│   ├── sqlite/             # 数据库
│   ├── models/             # 本地模型文件
│   └── skills/             # Skill 定义
└── docs/                   # 开发文档
```

---

> **安装包**: `HarvenAI_安装包_YYYYMMDD.zip` | **启动脚本**: `启动HarvenAI.bat`
> **技术支持**: 内存使用约 6-10GB（含模型），磁盘建议预留 30GB+
