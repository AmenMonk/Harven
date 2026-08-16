# Harven AI 安装详解

## 总览

双击 `一键安装启动.bat` → 4 步自动 → 首次引导 3 步 → 进入对话。

```
双击 bat
├── [1/4] 检查 Python
├── [2/4] 创建 .venv 虚拟环境
├── [3/4] pip install -r requirements.txt
├── [4/4] 生成 config.yaml
├── 检查 Ollama（可选）
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
| 内存 | 8 GB | 12-32 GB |
| 显卡 | 不需要 | NVIDIA 6 GB+ 显存 |
| 磁盘 | 10 GB（不含模型） | 30 GB+ |
| 系统 | Windows 10/11 | Windows 10/11 |
| Python | 3.10 ~ 3.13 | 3.10 ~ 3.13 |

### 1.2 安装 Python

1. 打开 https://www.python.org/downloads/
2. 下载 Python 3.10 ~ 3.13 版本
3. 安装时**必须勾选** `Add Python to PATH`
4. 验证：打开 cmd 输入 `python --version`，显示版本号即成功

### 1.3 解压安装包

```
HarvenAI 安装包
├── 一键安装启动.bat     ← 双击这个
├── web_app.py
├── src/                 ← 80+ 核心模块
├── static/              ← 前端 JS/CSS
├── templates/           ← HTML 模板
├── data/                ← 运行时数据（自动创建）
├── requirements.txt     ← Python 依赖清单
├── config.yaml.example  ← 配置模板
└── Dockerfile           ← Linux/macOS 用
```

> ⚠️ 路径不要含中文，不要放桌面（路径太长会出问题），直接放 H:\HarvenAI\ 或 D:\HarvenAI\

---

## 二、bat 脚本 4 步详解

### [1/4] 检查 Python

```bat
python --version
```

| 结果 | 下一步 |
|------|--------|
| 显示 `Python 3.13.12` | ✅ 继续 |
| 显示 `Python 3.9.x` | ❌ 版本太低，需升级 |
| `python 不是内部命令` | ❌ 没装或没加 PATH |

### [2/4] 创建虚拟环境

```bat
python -m venv .venv
```

生成 `HarvenAI\.venv\` 目录（约 20 MB），包含独立 Python 副本。

| 说明 | 细节 |
|------|------|
| 位置 | `H:\HarvenAI\.venv\` |
| 作用 | 隔离依赖包，不污染系统 Python |
| 多久一次 | 首次执行，之后跳过 |
| 如果失败 | 检查磁盘空间、杀毒软件拦截 |

### [3/4] 安装依赖

```bat
pip install -r requirements.txt
```

**核心依赖**（约 30 个包，总 ~300 MB）：

| 包 | 大小 | 用途 |
|------|------|------|
| `flask` | 2 MB | Web 服务器 |
| `faiss-cpu` | 20 MB | 向量检索（知识库） |
| `llama-cpp-python` | 60 MB | 本地 GGUF 推理 |
| `numpy` | 20 MB | 数值计算 |
| `psutil` | 5 MB | 硬件检测 |
| `pyyaml` | 1 MB | 配置解析 |
| `pypdf/pdfplumber` | 10 MB | PDF 导入 |
| `python-docx` | 8 MB | Word 导入 |
| `openpyxl` | 8 MB | Excel 导入 |
| `watchdog` | 3 MB | 技能热更新 |
| `ollama` | 0.2 MB | Ollama API 客户端 |
| `requests` | 0.5 MB | HTTP 请求 |

| 状态 | 含义 |
|------|------|
| 首次安装 | 下载+编译 llama-cpp-python（3-5分钟） |
| 已安装过 | 检查 `requirements.txt` 修改时间，不变则跳过（秒过） |
| 部分失败 | 输出 `[警告]` 但不中断，尝试继续启动 |

> 💡 llama-cpp-python 编译最慢。如果卡住，耐心等待——它不是假死。

### [4/4] 初始化配置

```bat
copy config.yaml.example config.yaml     ← 首次运行
copy .env.example .env                   ← 首次运行
mkdir data\sqlite                        ← 确保目录存在
```

| 文件 | 生成条件 | 内容 |
|------|---------|------|
| `config.yaml` | 不存在时从 `.example` 复制 | 模型、安全、联网等设置 |
| `.env` | 同上 | API Key 等敏感信息 |
| `data/sqlite/` | 始终创建 | 数据库目录 |

---

## 三、Ollama 检测

```bat
curl http://localhost:11434/api/tags
```

| 检测结果 | 影响 |
|---------|------|
| ✅ 已运行 | 自动调用 Ollama 模型 |
| ❌ 未安装 | 显示下载提示，但不阻止启动 → 用魔搭直连 |
| 💡 下载加速 | 自动设置 `OLLAMA_HF_MIRROR=https://modelscope.cn` |

> Harven **不强制 Ollama**。没有 Ollama 时自动走魔搭直连（llama-cpp-python + GGUF）。

---

## 四、启动服务

```bat
python web_app.py
```

| 输出 | 含义 |
|------|------|
| `[Harven] BUILD_ID = abc123def456` | 缓存 ID（每次随机） |
| `[Startup] 出站白名单已激活` | 37 个 AI 厂商域名加入白名单 |
| `[Startup] Blueprint 路由已注册` | 各功能模块 API 加载完成 |
| `Running on http://0.0.0.0:7000` | 启动成功 |

**如果没看到最后一行**：
| 问题 | 解决 |
|------|------|
| `ImportError: faiss` | `pip install faiss-cpu` 手动装 |
| `端口 5000 被占用` | `netstat -ano | findstr :7000` 找到进程 kill |
| `Ollama 连接超时` | 正常——无 Ollama 时自动切魔搭 |

---

## 五、首次引导（浏览器 3 步）

打开 `http://localhost:7000`：

### Step 1 — 同意协议

Apache 2.0 开源协议说明 + 隐私声明（数据不出本机）。

点击「✅ 同意并继续」。

### Step 2 — 硬件检测 + 推荐

后台调用 `detect_hardware()` 检测：

```
CPU: 13th Gen Intel Core i7-13700F
RAM: 32 GB
GPU: NVIDIA GeForce RTX 4070 SUPER (12 GB)
VRAM: 12 GB — 🟢 高端
```

根据 VRAM 列出推荐模型：

```
✅ 推荐（你的硬件能流畅跑）
  Qwen2.5 14B — 中文综合最强
  DeepSeek-R1 14B — 深度推理
  Qwen2.5 7B — 轻量快速

⚠️ 勉强（可能慢一点）
  Qwen2.5 32B — 需要 22GB 显存

❌ 不支持
  Llama 3.3 70B — 需要 44GB 显存
```

选一个模型点「下载」，Real-time 进度条显示下载进度（ModelScope 国内满速）。

### Step 3 — 完成

下载完成后自动写入 `data/sqlite/first_run.json`：

```json
{"completed": true, "timestamp": "2026-06-25T01:30:00"}
```

下次打开直接进主界面，不再走引导。

---

## 六、Linux/macOS 安装

```bash
git clone https://github.com/your/harven-ai.git
cd harven-ai
docker compose up -d
# → http://localhost:7000
```

或手动：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml
python web_app.py
```

---

## 七、常见问题

### Q: 安装包有多大？
A: 纯代码 ~5 MB。pip 依赖 ~300 MB。模型另算（6-20 GB）。

### Q: 一定要装模型吗？
A: 不装本地模型也能用——切到 🤖 外脑模式，配 API Key 直接用 DeepSeek/豆包。

### Q: 能换安装目录吗？
A: 可以，但路径不要有中文。复制整个文件夹到新位置，重新双击 bat 即可。

### Q: 怎么卸载？
A: 删掉整个 HarvenAI 文件夹即可。没有任何注册表、系统服务残留。

### Q: 多人共用一台电脑？
A: 启动后局域网其他设备访问 `http://IP:7000`，输入远程密码（默认 harven2026）。本机访问免密码。
