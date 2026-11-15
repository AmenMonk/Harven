# Harven AI 安装详解 v2.0

> 更新日期：2026-08-09 | 适用版本：哈文 v0.3.0+

---

## 一、系统要求

### 1.1 最低配置

| 项目 | 最低 | 推荐 |
|------|------|------|
| 操作系统 | Windows 10 64位 | Windows 11 |
| Python | 3.12 | 3.13+ |
| 内存 | 8GB | 32GB |
| 磁盘 | 5GB（不含模型） | 50GB（含模型） |
| GPU | 不需要（用外脑API） | RTX 3060 12GB+ |

### 1.2 可选组件

| 组件 | 用途 | 是否必须 |
|------|------|---------|
| Ollama | 本地模型推理 | 推荐（免费模型） |
| 外脑API Key | DeepSeek/GLM等 | 至少1个 |
| CUDA | GPU加速 | 有GPU时需要 |
| FFmpeg | 音视频合成 | 可选 |

---

## 二、安装步骤

### 2.1 一键安装（推荐）

```bash
# Windows
1. 下载哈文安装包
2. 解压到任意目录（如 H:\HarvenAI）
3. 双击「安装哈文.bat」（电脑没装 Python 也能装，会自动引导）
4. 等待安装完成（约 3~5 分钟，自动创建虚拟环境+安装依赖）
5. 初始密码 harven2026，首次登录后请修改
```

### 2.2 手动安装

#### Step 1：安装Python

```bash
# 下载 Python 3.12+
# https://www.python.org/downloads/
# 安装时勾选 "Add Python to PATH"
```

#### Step 2：安装Ollama（可选，推荐）

```bash
# 下载 Ollama
# https://ollama.ai
# 安装后拉取模型：
ollama pull qwen2.5:14b
```

#### Step 3：克隆仓库

```bash
git clone https://github.com/YOUR_USERNAME/HarvenAI.git
cd HarvenAI
```

#### Step 4：创建虚拟环境

```bash
python -m venv .venv
.venv\Scripts\activate
```

#### Step 5：安装依赖

```bash
pip install -r requirements.txt
```

#### Step 6：配置

```bash
# 复制配置模板
copy config.example.yaml config.yaml

# 编辑 config.yaml，填入你的API Key：
# model_keys:
#   deepseek: "sk-xxxxx"     # DeepSeek API Key
#   zhipu: "xxxxx"           # 智谱GLM API Key
# ollama:
#   default_model: "qwen2.5:14b"
# collab_mode: "off"
# port: 7000
```

#### Step 7：启动

```bash
python web_app.py
```

或双击`启动HarvenAI.bat`。

---

## 三、获取API Key

### 3.1 外脑模型（至少选1个）

| 提供商 | 注册地址 | 免费额度 | 推荐 |
|--------|---------|---------|------|
| DeepSeek | platform.deepseek.com | 有 | ⭐⭐⭐ |
| 智谱GLM | open.bigmodel.cn | 有 | ⭐⭐⭐ |
| 豆包 | volcengine.com | 有 | ⭐⭐ |
| 通义千问 | dashscope.aliyun.com | 有 | ⭐⭐ |
| Kimi | platform.moonshot.cn | 有 | ⭐⭐ |
| 零一万物 | platform.lingyiwanwu.com | 有 | ⭐ |
| 百川 | platform.baichuan-ai.com | 有 | ⭐ |
| 阶跃星辰 | platform.stepfun.com | 有 | ⭐ |

### 3.2 配置多个外脑

```yaml
# config.yaml
model_keys:
  deepseek: "sk-xxxxx"
  zhipu: "xxxxx"
  doubao: "xxxxx"
```

配置后可在哈文顶部栏下拉切换外脑。

---

## 四、功能配置

### 4.1 查询模式

| 模式 | 说明 | 需要 |
|------|------|------|
| 🏠 本地模式 | 本地模型+知识库 | Ollama |
| 🤖 外脑模式 | 外部模型+知识库 | API Key |

### 4.2 协作模式

| 模式 | 说明 |
|------|------|
| 🚫 关闭 | 纯对话，安全 |
| ☁️ 智能协作 | Skill编排+外脑+垂直模型 |
| 🏠 本地协作 | Skill编排+本地模型+垂直模型 |

### 4.3 垂直模型（可选）

| 模型 | 用途 | 安装方式 |
|------|------|---------|
| MusicGen | 生成音乐 | 垂直面板安装 |
| Bark | 文字转语音 | 垂直面板安装 |
| Stable Diffusion | 生成图片 | 垂直面板安装 |
| 华佗 | 医疗问答 | `ollama create huatuo -f Modelfile` |

---

## 五、Skill系统

### 5.1 内置Skill（10个）

| Skill | 触发词 | 功能 |
|-------|--------|------|
| 记账助手 | 记一笔/记账.*元 | 收支管理 |
| 代码审计 | 代码审计/代码健康 | 代码检测 |
| 财务月结 | 财务月结/月度报告 | 生成报告 |
| 健康追踪 | 记录运动/健康追踪 | 健康记录 |
| 音乐生成 | 生成音乐/写首歌 | MusicGen |
| 歌曲制作 | 写一首歌/歌曲制作 | 多模型编排 |
| 数据分析 | 数据分析/统计图表 | 分析建议 |
| Python专家 | Python代码/python问题 | 编程建议 |
| 技术写作 | 技术文档/写文档 | 写作建议 |
| 工作流提取 | 提取流程/工作流 | 流程分析 |

### 5.2 自定义Skill

1. 创建目录：`data/skills/你的skill名/`
2. 写`SKILL.md`（含trigger触发词）
3. 写`scripts/main.py`（可执行脚本）
4. 可用`_helpers.py`的函数

---

## 六、面板系统

### 6.1 创建面板

设置→我的面板→"+"→创建面板：
- 5种模板（记账/健身/待办/库存/空白）
- 7种字段类型（文本/数字/日期/下拉/多行文本/时间/图片）
- 图标选择、统计图表、卡片视图

### 6.2 记账面板

- 汇总卡片（总收入/支出/净收支）
- 分类柱状图
- 收支明细表格
- 卡片视图切换

---

## 七、高级功能

### 7.1 设置→高级功能

| 功能 | 说明 |
|------|------|
| 触发词管理 | 可视化编辑所有Skill的触发词 |
| 定时任务 | 添加/删除定时执行Skill |
| Obsidian同步 | 输入vault路径→同步.md到知识库 |
| AI生成Skill | 输入描述→AI生成Python脚本 |
| AirLLM状态 | 显示安装状态（低显存跑大模型） |
| 硬件信息 | GPU型号+显存 |

### 7.2 设置→流程编辑器

可视化创建多模型协作流程：
- 8种模型可选（通用/MusicGen/Bark/SD/华佗/FFmpeg/SQLite/写文件）
- 步骤排序（上移/下移）
- 预览生成的Python代码
- 保存为Skill

---

## 八、常见问题

### Q: 启动后白屏
- 检查Python版本是否3.12+
- 检查.venv是否创建成功
- 检查依赖是否安装完整

### Q: 外脑无响应
- 检查API Key是否正确
- 检查网络是否通畅
- 尝试切换其他外脑

### Q: 本地模型加载失败
- 检查Ollama是否运行：`ollama list`
- 检查显存是否足够
- 尝试更小模型：`ollama pull qwen2.5:7b`

### Q: Skill执行失败
- 检查协作模式是否开启（垂直Skill需要ON）
- 检查沙箱是否阻止了必要模块
- 查看日志：`logs/`目录

### Q: 面板不显示新功能
- 按Ctrl+Shift+R强制刷新浏览器
- 完全关闭哈文窗口重新打开
- 检查panel.js是否最新版本

---

## 九、文件结构

```
HarvenAI/
├── web_app.py              # Flask主应用
├── config.yaml             # 配置文件（不上传GitHub）
├── config.example.yaml     # 配置模板
├── .gitignore              # Git忽略
├── README.md               # 项目说明
├── CONTRIBUTING.md         # 贡献指南
├── requirements.txt        # Python依赖
├── 启动HarvenAI.bat         # 启动脚本
├── src/
│   ├── core/
│   │   ├── engine.py       # 对话引擎
│   │   ├── scripts_runner.py # Skill沙箱
│   │   ├── panel_manager.py  # 面板管理
│   │   ├── task_scheduler.py # 定时调度
│   │   ├── airllm_integration.py # AirLLM
│   │   ├── knowledge_base.py # 知识库
│   │   └── vertical_api.py   # 垂直模型API
│   ├── models/
│   │   └── database.py      # 数据库
│   └── ui/
│       └── main_window.py   # 桌面窗口
├── data/
│   ├── skills/              # Skill目录（10个）
│   │   ├── _helpers.py      # Skill辅助函数
│   │   ├── accounting/      # 记账
│   │   ├── code_audit/      # 代码审计
│   │   ├── finance_report/  # 财务月结
│   │   ├── health_tracker/  # 健康追踪
│   │   ├── music-gen/       # 音乐生成
│   │   ├── song_production/ # 歌曲制作（多模型编排）
│   │   └── ...
│   ├── panels/              # 面板目录
│   ├── sqlite/              # 数据库文件
│   ├── media/               # 生成的音频/图片
│   ├── reports/             # 生成的报告
│   └── scheduler.json       # 定时任务
├── templates/               # HTML模板
│   └── includes/
│       ├── tab-流程编辑器.html
│       ├── tab-高级功能.html
│       └── ...
├── static/                  # 静态资源
│   └── js/modules/
│       ├── app-conversation.js
│       ├── app-panels.js
│       └── ...
└── docs/                    # 文档
    └── HarvenAI_技术详解_v7.md
```

---

## 十、更新日志

### v2.0（2026-08-09）
- 全面更新：Skill系统/面板系统/流程编辑器/高级功能
- 新增10个Skill（6脚本+4提示词）
- 新增7种面板字段类型
- 新增流程编辑器
- 新增多外脑切换
- 新增定时任务
- 新增AirLLM集成
- 新增GitHub开源准备

### v1.2
- 基础安装说明
