# Harven AI 哈文 技术详解 v6.3

> 版本：v6.3 | 更新日期：2026-08-03 | 适用版本：哈文 v0.2.1+
> 作者：AmenMonk | 许可证：Apache 2.0

---

## 目录

- [第一部分：项目概述](#第一部分项目概述)
- [第二部分：架构设计](#第二部分架构设计)
- [第三部分：核心功能详解（29项）](#第三部分核心功能详解29项)
- [第四部分：设置面板（8个Tab）](#第四部分设置面板8个tab)
- [第五部分：安装部署](#第五部分安装部署)
- [第六部分：开发者指南](#第六部分开发者指南)
- [第七部分：能力边界](#第七部分能力边界)

---

## 第一部分：项目概述

### 1.1 产品定位

**Harven AI（哈文）** 是一个纯本地运行的私有AI平台。

| 定位 | 说明 |
|------|------|
| 产品名 | Harven AI（哈文） |
| 中文名 | 哈文 |
| 定位 | 纯本地运行私有AI平台 |
| 价格 | **永久免费，不做付费会员** |
| 数据主权 | 所有数据存储在用户本地，不外传 |
| 目标用户 | 注重隐私的中产高知家庭 + 小微个体户 |

### 1.2 核心差异化

| 差异化 | 说明 |
|--------|------|
| 全能一体化 | 模型+知识库+操作电脑+垂直模型+设备管理，一个平台全搞定 |
| 数据主权在用户 | 纯本地，不依赖云端 |
| 全生活场景 | 不只管智能家居，是家庭财务+菜谱+学习+提醒 |
| 普通人可用 | 对话驱动，不需要编程 |

### 1.3 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| Web框架 | Flask 3.1.3 | Python Web服务 |
| AI推理 | Ollama / 外部API | 本地模型+外部模型 |
| 向量检索 | FAISS 1.14.2 | 知识库语义搜索 |
| 数据库 | SQLite | 所有数据本地存储 |
| 桌面UI | PySide6 6.11.1（LGPL） | 桌面版界面 |
| AI SDK | openai 2.41.0 | 兼容SDK连接Ollama/云端 |
| 模型下载 | modelscope 1.37.1 | ModelScope模型下载 |
| 多模型 | litellm 1.88.0 | 多模型统一接口 |
| 浏览器 | playwright 1.58.0 | 浏览器自动化 |
| PDF解析 | pypdf 6.13.0 + pdfplumber | PDF文字+表格提取 |
| 语音 | faster-whisper 1.2.1 | 本地语音转写 |

### 1.4 目标硬件

哈文是给大众用的，支持各种配置：

| 配置 | 哈文怎么适配 |
|------|------------|
| 24GB显存（4090） | 硬件检测→推荐跑32B模型（70B需40GB+显存） |
| 12GB显存（4070） | 硬件检测→推荐跑14B模型 |
| 8GB显存（3060） | 硬件检测→推荐跑7B模型 |
| 没有独立显卡 | 硬件检测→推荐CPU跑或外脑模式 |
| 不想用本地模型 | 外脑模式→用云端模型（不占显存） |

---

## 第二部分：架构设计

### 2.1 整体架构图

```
用户浏览器
    ↓ HTTP
Flask Web服务（web_app.py）
    ↓
对话引擎（engine.py）
    ├── 查询模式选择（本地/外脑）
    ├── 知识库搜索（knowledge_base.py）
    ├── Skills注入（skill_manager.py）
    ├── 用户画像+习惯注入
    ├── 模型调用（MultiModelManager）
    ├── 工具执行（tool_executor.py）
    └── 记忆系统（memory.py）
        ↓
    模型输出
        ↓
    tool_executor提取代码块→执行→返回结果
```

### 2.2 两种查询模式

两种查询模式共享同一套能力：**知识库 + Skills + 用户画像 + 习惯 + 诚实**

| 模式 | 通用模型 | 知识库 | Skills | 画像 | 习惯 | 垂直模型 |
|------|---------|--------|--------|------|------|---------|
| 🏠本地模式 | 本地通用模型（Ollama是备选） | ✅ | ✅ | ✅ | ✅ | 独立调用 |
| 🤖外脑模式 | 外部模型（DeepSeek/GLM/豆包等） | ✅ | ✅ | ✅ | ✅ | 独立调用 |

**区别：只是通用推理模型不同（本地 vs 外部），其他能力完全一样。**

### 2.3 协作模式（独立开关）

协作模式是独立开关，与查询模式配合使用：

| 协作模式 | 流程 | 知识库 | Skills |
|---------|------|--------|--------|
| ☁️云地协作 | 外脑分析→调垂直参数→给建议→本地垂直模型执行 | ❌不加 | ❌不加 |
| 🏠本地双模 | 本地通用模型调度→调垂直参数→给建议→垂直模型执行 | ❌不加 | ❌不加 |

**协作模式只调参数+给建议，不加知识库和Skills（会干扰调参数）。**

**协作模式开启时，互联网搜索自动关闭（避免冲突）。**

### 2.4 垂直模型

垂直模型独立调用，不经过知识库/Skills/画像/习惯：

```
用户说"做一首音乐"
    ↓
通用模型理解需求 → 调出MusicGen参数（description=音乐, duration=10s）
    ↓
垂直模型执行：MusicGen接收参数 → 生成音乐 → 返回
不经过知识库/Skills/画像/习惯
```

### 2.5 代码执行机制

```
模型生成 ```python 代码块
    ↓
tool_executor.py 提取代码块
    ↓
安全检查（黑名单+危险模块）
    ↓
拦截检查（越权调用+搜索代码）
    ↓
写入临时文件 → 用.venv的python执行
    ↓
返回 stdout/stderr 结果
    ↓
结果注入对话上下文 → 模型看到结果继续回答
```

**关键：用 `.venv\Scripts\python.exe` 执行（modelscope/openai等库装在.venv里）。**

**★ v6.0 新增机制：**

1. **工具执行后二次推理**：代码执行完毕后，引擎自动发起第二次模型调用，让模型看到执行结果并继续解读（不再"说完就停"）

2. **stderr 智能过滤**：自动过滤 Triton/xformers 等无害警告，只保留真正的错误信息。成功时 stderr 截断到 500 字符，失败时保留 3000 字符

3. **代码块格式强制**（★ v6.2修订）：FC未启用时，系统提示词强制模型使用 ` ```python ` 代码块格式；**FC启用时，改为强制调用FC工具（不写代码）**——两者互斥，按FC状态自动切换

4. **联网意图不阻断**（★ v6.2修订）：分类器误判为搜索请求时不再 `return` 阻断流程；**FC启用时走`web_search`工具，FC未启用时走内置Bing搜索**——按FC状态选择路径

5. **目录结构注入**：系统提示词包含完整项目目录结构（web_app.py/data/sqlite/data/skills等），模型不再猜测路径

6. **代码审计 Skill**：`data/skills/code_audit/` 可执行技能，说"检测废代码"自动触发，系统性扫描9类问题（废import/断链路由/废工具/静默吞错/超时/截断等），直接格式化输出报告（不经过模型解读，避免跑偏）

7. **Skill 触发词自动执行**（★ v6.2修订）：引擎检测用户消息匹配 Skill 触发词时自动执行脚本。**FC启用时优先走FC的`execute_skill`工具；FC未启用或模型不调FC时走触发词自动执行**——FC路径优先，触发词兜底

8. **记忆系统方案C**：只在用户说"记住/我喜欢/我叫"等关键词时才做 AI 提取（不是每轮都提）；用引擎当前模型（DeepSeek/ModelScope/Ollama）提取，不硬编码 Ollama；L0 原始对话始终保存

9. **iframe sandbox**：`allow-scripts allow-forms allow-modals`（allow-modals 让 confirm() 对话框正常工作）

### 2.6 三层安全防护

| 层级 | 功能 | 说明 |
|------|------|------|
| 第一层 | 文件扫描 | 5层检查：扩展名/大小/Magic Bytes/内容模式/杀毒扫描 |
| 第二层 | 代码沙箱 | Docker容器隔离（优先）/ 子进程隔离（降级），拦截越权调用+联网搜索代码 |
| 第三层 | 注入审查 | 上传文档时LLM复核内容，防止提示注入 |

---

## 第三部分：核心功能详解（29项）

### 3.1 本地知识库（三层架构）

存储和查询用户上传的文档。

**三层架构：**
```
文档层：上传txt/md/csv/pdf/docx/xlsx/pptx → 存储到data/knowledge/
向量层：自动向量化 → FAISS索引 → 语义搜索
注入层：对话时自动搜索相关内容 → 注入回答
```

**使用方式：**
- 点📚知识库按钮 → 拖拽文件或粘贴文本
- 对话时自动搜索知识库，注入相关内容辅助回答

### 3.2 代码执行

编写Python代码→系统自动运行→返回结果。

**这是操控电脑的核心方式。**

**能做什么：**
- 执行Python代码（```python代码块自动执行）
- 读写文件（open/os.listdir）
- 安装Python包（subprocess.run(['pip','install','包名'])）
- 运行系统命令（subprocess.run()）
- 打开文件（os.startfile()）
- 下载文件（urllib.request.urlretrieve()）
- 安装模型（ollama pull / modelscope / huggingface / git clone）

**不能做什么：**
- rm -rf / del /S / format / shutdown / reboot（危险命令）
- ctypes / winreg / win32api / win32com（危险模块）
- 超时120秒的代码会被终止（下载类5分钟）
- 不要自己写requests搜索网页（系统有内置Bing搜索）

**执行环境：** 用 `.venv\Scripts\python.exe` 执行（预装了所有依赖）

### 3.3 文件管理

通过Python代码读写本地文件、列出目录。

```python
import os
# 列出D盘文件
files = os.listdir('D:\\')
for f in files:
    print(f)
```

### 3.4 任务自动化（软件安装）

帮用户下载和安装软件/模型。

**五种安装方式：**
- pip安装：`subprocess.run(['pip', 'install', '包名'], timeout=120)`
- HuggingFace：`from huggingface_hub import snapshot_download; snapshot_download('模型名')`
- ModelScope：`from modelscope import snapshot_download; snapshot_download('模型名')`
- Ollama：`subprocess.run(['ollama', 'pull', '模型名'], timeout=300)`
- GitHub：`subprocess.run(['git', 'clone', 'URL'], timeout=120)`

### 3.5 专项培养

给对话设定专职角色。

| 角色 | 效果 |
|------|------|
| 翻译助手 | 发什么都翻译 |
| 代码审查员 | 审查代码质量 |
| 周报生成器 | 自动生成周报 |

**创建方式：** 点🤖专项按钮新建，或对话里说"创建翻译助手"

### 3.6 监控中心

自动追踪关注话题的最新动态。

**RSS源（6个）：** 36氪 / IT之家 / 开源中国 / InfoQ / Solidot / 少数派

**使用：** 添加主题+关键词（如"AI动态"+关键词"AI,GPT,大模型"）→ 系统定时抓取匹配新闻

### 3.7 垂直模型

多种专业垂直模型的统称（已内置6种+支持动态扩展）。

| 模型 | 图标 | 说明 |
|------|------|------|
| 音乐生成 | 🎵 | MusicGen / ACE-Step |
| 图片生成 | 🎨 | Stable Diffusion |
| 语音合成 | 🔊 | Coqui TTS |
| 代码生成 | 💻 | 代码辅助 |
| 数据分析 | 📊 | 数据处理 |
| 文档翻译 | 🌐 | 多语言翻译 |

**使用：** 点🎨垂直按钮选择模型 → 填参数 → 执行

**★ 垂直模型独立调用，不经过知识库/Skills/画像/习惯。**

### 3.8 图片识别

查看和分析用户上传的图片。

- 视觉模型：minicpm-v:8b
- 上传图片→系统自动分析→返回描述
- 不支持视觉时提示"请切换到minicpm-v:8b或外脑模式"

### 3.9 联网搜索

Bing内置+SearXNG可选增强。

| 触发方式 | 说明 |
|---------|------|
| 点🌐互联网按钮 | 手动开启 |
| 关键词触发 | 消息含"搜索/天气/新闻/股价/上证/指数"等 |
| SearXNG可选 | 用户自建后填写URL，优先使用SearXNG聚合搜索 |

- 事实查询（天气/股价）直接展示搜索结果卡片
- 知识查询经模型整合回答
- 协作模式开启时自动关闭（避免冲突）

### 3.10 模型安装

对话驱动安装各种模型。

**用户说"帮我装MusicGen/装华佗模型/装个qwen模型"→哈文直接生成代码块执行。**

**预装的库（不需要再安装）：**
modelscope / openai / flask / faiss / requests / beautifulsoup4 / faster-whisper / pillow / pypdf / playwright / apscheduler / litellm

### 3.11 通用模型管理

Ollama管理本地通用模型。

- 🏠本地模式用的通用模型通过Ollama管理（ollama pull qwen2.5等）
- Ollama是备选引擎，不是唯一方式
- 用户可在设置→模型管理→本地模型中下载GGUF模型文件

### 3.12 查询模式

两种模式可选（共享知识库+Skills+画像+习惯）。

| 模式 | 模型 | 数据 |
|------|------|------|
| 🏠本地模式 | 本地通用模型（Ollama是备选） | 不外传 |
| 🤖外脑模式 | 外部模型（DeepSeek/GLM/豆包等） | 远程推理 |

**区别：只是通用推理模型不同，其他能力完全一样。**

### 3.13 协作模式（独立开关）

| 模式 | 流程 |
|------|------|
| ☁️云地协作 | 外脑分析→调垂直参数→给建议→本地垂直模型执行 |
| 🏠本地双模 | 本地通用模型调度→调垂直参数→给建议→垂直模型执行 |

**协作模式只调参数+给建议，不加知识库和Skills（会干扰调参数）。**

### 3.14 硬件检测

检测电脑配置，推荐合适模型。

- 检测CPU/GPU/内存
- 21个模型数据库
- 同步API+前端动画进度条

### 3.15 多模型管理

配置多个外部模型API。

**支持28家厂商：**
- 国内：元宝/百川/智谱/零一/阶跃/MiniMax/文心/星火/天工/硅基流动
- 国际：OpenAI/Claude/Gemini/Grok/Groq/Perplexity/Mistral/Together/OpenRouter/HuggingFace等

### 3.16 插件管理

安装/管理扩展插件（设置→🧩插件管理）。

### 3.17 Skills系统

**★ v6.0 中级可执行脚本系统**（2026-07-14 升级）：

Skills 从纯提示词升级为**可执行脚本技能**，支持实际运行代码。

| 创建方式 | 说明 |
|---------|------|
| 手动创建 | 设置→🧩插件管理→创建Skill |
| AI自动生成 | 设置→🧩插件管理→Skill Builder |
| 对话创建 | 说"帮我创建翻译技能"→自动生成 |
| 上传ZIP | `POST /api/skills/scripts` 上传完整技能包 |

**Skill 目录格式（可执行脚本版）：**
```
data/skills/<name>/
├── SKILL.md          ← 技能描述 + 触发词
└── scripts/
    └── main.py       ← 可执行脚本（AST沙箱 + subprocess执行）
```

**脚本执行引擎**（`src/core/scripts_runner.py`）：
- AST 沙箱：静态分析拦截危险调用
- subprocess 隔离执行
- 禁用的模块：subprocess/socket/ctypes（防止越权）
- 新增端点：`/api/skills/scripts`（列表/运行/信息/上传ZIP）

**示例 — 记账助手**（`data/skills/accounting/`）：
- `add`: 记一笔账 → SQLite 写入
- `query`: 查账 → SQLite 查询
- `summary`: 月度汇总 → 统计计算
- `delete`: 删除记录

**Skill 触发词自动执行**：引擎检测用户消息匹配 Skill 触发词时自动执行脚本，不依赖 FC（Function Calling），所有模式共享。

**原理（旧版兼容）：** 用户消息含触发词→自动注入Skill的prompt→模型按规则执行

### 3.18 Agent智能体

专项子角色（比Skill更完整）。

| 类型 | 说明 |
|------|------|
| 预置Agent | 代码审查员/数据分析师/coder等（4个内置） |
| 自定义Agent | 设置→🧩插件管理→🧩Agent（注册中心，填写ID/名称/角色/提示词/模型偏好/触发词） |
| Agent工坊 | 设置→🧩插件管理→🏭工坊（创建Agent包，含Skill+模板+知识包组合） |

**关系：** Agent包含多个Skill，Skill是Agent的工具
**区别：** Skill是"工具"（翻译工具），Agent是"工人"（翻译员带工具）
**路径：** Agent注册中心和工坊都已从独立主标签移至插件管理子导航（2026-07-20重构）

### 3.19 安全防护（三层）

| 层级 | 功能 | 说明 |
|------|------|------|
| 文件扫描 | 5层检查 | 扩展名/大小/Magic Bytes/内容模式/杀毒扫描 |
| 代码沙箱 | 隔离执行 | Docker容器隔离（优先）/ 子进程隔离（降级） |
| 注入审查 | LLM复核 | 上传文档时LLM复核内容，防止提示注入 |

### 3.20 杀毒扫描

可挂载第三方杀毒软件（ClamAV）。

- 配置：设置→安全隐私→第三方杀毒软件路径
- 未装杀毒时跳过，前4层文件扫描仍生效

### 3.21 设备管理

IoT设备驱动控制。

**驱动类型：** MQTT / HTTP / WebSocket / AI临时生成驱动

### 3.22 手机联动

手机扫码连接哈文，远程对话。

- 配置：设置→关于→📱手机+公网

### 3.23 IM连接

企业微信/钉钉/飞书接入。

- 配置：设置→关于→📡渠道接入

### 3.24 数据保险箱

备份/恢复所有数据。

- 配置：设置→💾数据

### 3.25 品牌定制

自定义名称/图标/外观。

- 配置：设置→🎨外观定制

### 3.26 受控进化

用户说"帮我加功能/改代码/优化XX"→生成修改提案→用户审批→应用。

**能改：** skills/（技能脚本）、templates/（页面）、static/（样式）、config.yaml（配置）
**不能改：** engine.py/web_app.py等核心代码（安全黑名单）

**流程：** 用户说需求→哈文生成提案→用户看描述→批准/拒绝→可回滚

### 3.27 知识包

整理好的知识库文档打包，可分享给其他用户。

| 操作 | 方式 |
|------|------|
| 制作 | 用户说"帮我把知识库里的XX打包成知识包"→生成代码打包 |
| 安装 | 用户说"帮我安装XX知识包"→生成代码解压→导入知识库 |
| 格式 | 知识包.zip（documents/ + metadata.yaml） |

### 3.28 Bundle（集合包）

多个Skill+Agent+知识包的组合，完整解决方案。

| 操作 | 方式 |
|------|------|
| 制作 | 用户说"帮我创建Bundle，包含记账Skill+健康Agent+营养学知识包"→生成代码打包 |
| 安装 | 用户说"帮我安装Bundle"→生成代码解压→自动安装所有Skill/Agent/知识包 |
| 使用 | 安装后Skill/Agent/知识包自动生效（不需要额外操作） |

**Bundle格式：**
```
Bundle名称.zip
├── bundle.yaml    ← 配置清单
├── skills/        ← Skill文件
├── agents/        ← Agent配置
└── knowledge/     ← 知识包文档
```

### 3.29 HarvenHub 社区市场

哈文的社区市场，用户上传/下载/分享内容。

**Hub支持的内容类型：**

| 类型 | 说明 |
|------|------|
| ⚡ Skill | 技能脚本 |
| 🤖 Agent | 智能体 |
| 📚 知识包 | 知识库文档包 |
| 📦 Bundle | 集合包（Skill+Agent+知识包） |
| 📋 模板 | 画板/工作流模板 |
| 🧠 模型/工具 | 66个预置模型+工具下载链接 |

**Hub分类：**
🏠家庭 / 🏢企业 / 🏭工厂 / 🏫学校 / 🏥医疗 / 🍽️餐饮 / 🌾农业 / 🛒电商 / 🔧工具 / 📦其他

**预置内容（66个）：**
- 通用模型24个（qwen2.5/deepseek-r1/llama3/gemma2/phi3/yi/mistral等）
- 垂直模型13个（MusicGen/SD/TTS/Whisper/Bark/华佗等）
- 工具12个（Ollama/Python/Git/Docker/FFmpeg/winget安装）
- 代码模型7个（codellama/deepseek-coder/qwen2.5-coder等）
- 推理模型5个（deepseek-r1系列）
- 视觉模型5个（minicpm-v/llava/moondream）

**Hub架构：**
```
哈文客户端（5000端口）
  └── ☁️ Hub云端Tab → 连接Hub服务端

Hub服务端（5001端口，独立项目 H:\HarvenHub\）
  ├── API服务（Flask）
  ├── 数据库（SQLite：users/items/reviews/requests/downloads/reports）
  ├── 文件存储（storage/files/）
  ├── 自动审核（4层：文件类型/大小/病毒/代码扫描）
  ├── 用户系统（注册/登录/快速登录）
  └── 管理后台（http://127.0.0.1:5001/admin）
```

**Hub API：**

| API | 功能 |
|-----|------|
| GET /api/hub/list | 内容列表 |
| GET /api/hub/search | 搜索 |
| GET /api/hub/detail/<id> | 详情 |
| GET /api/hub/download/<id> | 下载 |
| POST /api/hub/upload | 上传 |
| POST /api/hub/review | 评价 |
| POST /api/hub/request | 发布需求 |
| POST /api/hub/report | 举报 |
| GET /api/hub/categories | 分类 |
| GET /api/hub/stats | 统计 |
| POST /api/user/register | 注册 |
| POST /api/user/login | 登录 |
| POST /api/user/quick_login | 快速登录 |
| POST /api/hub/install | 下载并自动安装（哈文后端） |
| POST /api/hub/execute | 执行安装命令（哈文后端） |

**模型/工具安装方式：**
- Ollama模型 → `ollama pull 模型名`
- pip安装 → `pip install 包名`
- winget安装 → `winget install 工具名`（Windows快速安装）
- ModelScope → `snapshot_download('模型名')`
- GitHub → `git clone URL`
- URL → 打开浏览器下载

### 3.30 CLI-Anything B+ 集成（2026-07-17）

将 79 个创意软件 CLI 封装一键接入哈文，通过对话直接控制 Blender/GIMP/Inkscape 等软件。

| 集成方式 | 说明 |
|---------|------|
| 推荐工具卡片 | 设置→🧩插件管理→🛠️技能顶部，一键安装 CLI-Anything Hub |
| FC 工具 | `cli_anything_list` / `cli_anything_execute` / `cli_anything_install`（3个工具） |
| 增强上下文 | 对话中检测关键词（blender/gimp 等）→ enhanced_context 自动注入安装提示 |

**设计原则（B+方案）：** Skill 模式 + 设置面板被动提醒，不内置代码只内置安装能力。目标软件由用户自行安装。

### 3.31 UI 面板系统 V1（2026-07-14）

用户可安装自定义 UI 管理面板（记账/宠物/健身等），ifarme 沙盒隔离。

| 组件 | 说明 |
|------|------|
| PanelManager | 单例，扫描 `data/panels/`，自动建 SQLite 表 |
| panel_routes.py | Blueprint `/api/panels`（列表/安装/卸载/创建/数据CRUD） |
| 面板格式 | `data/panels/<id>/panel.json` + `index.html` + `panel.css` + `panel.js` |
| 前端通信 | iframe 沙盒 + postMessage（HarvenPanel API） |
| 前端组件 | sidebar-panels.html + panel-viewer.html + modal-panel-manage.html + app-panels.js |

### 3.32 外部模型安装器（零推荐+搜索优先）

2026-07-20 重写：所有模型不推荐，用户根据配置自行选择。

| 特性 | 说明 |
|------|------|
| 搜索融合 | 两层数据源：安装器 MODEL_CATALOG（6个）+ model_catalog.json（44个），去重 |
| 硬件检测 API | `GET /api/models/installer/hardware` 检测 VRAM/RAM |
| 分类标准化 | 中文→英文映射（"音乐"→"music"） |
| 界面 | 页面加载时空白，用户搜索后才显示结果 |

### 3.33 SearXNG 三态检测

Bing 搜索已内置（无需 Docker/Key），SearXNG 为可选增强。新增实时连通性检测：

| 状态 | 显示 | 触发条件 |
|------|------|---------|
| ✅ 已启用 | 绿色 | SearXNG URL 已配置 + 连通性检测通过 |
| ⚠️ 已配置但未响应 | 黄色 | URL 已填但 5 秒内无响应 |
| ✅ Bing 已就绪 | 绿色 | 使用内置 Bing 搜索（默认） |

**检测 API：** `POST /api/settings/searxng-test`，真实请求 SearXNG API（5秒超时）

---

## 第四部分：设置面板（8个Tab）

| Tab | 内容 |
|-----|------|
| ①基本设置 | 联网搜索（Bing/SearXNG）+ 语言 + 数据库状态 |
| ②模型管理 | 本地模型 + 外部模型（搜索优先安装器）+ 协作外脑 |
| ③个人画像 | 用户偏好/性格/职业 |
| ④外观定制 | 名称/图标/主题 |
| ⑤安全隐私 | 杀毒 + 代码沙箱 + 隐私模式 + 管理员密码 |
| ⑥💾数据 | 数据保险箱（备份/恢复）+ 设备命令日志 |
| ⑦🧩插件管理 | 5个子Tab：🛠️技能（含CLI-Anything推荐）/🧱构建/🧩Agent/🏭工坊/📦外部模型 |
| ⑧关于&连接 | 手机 + 公网 + IM连接 + HarvenHub + 版本信息 |

---

## 第五部分：安装部署

### 5.1 环境要求

| 要求 | 说明 |
|------|------|
| 操作系统 | Windows 10/11（64位） |
| Python | 3.13+（或使用嵌入式Python） |
| 内存 | 最低8GB，推荐16GB+ |
| 显卡 | 可选（没有显卡可用外脑模式） |
| 磁盘 | 最低5GB（含依赖包） |

### 5.2 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/AmenMonk/HarvenAI.git
cd HarvenAI

# 2. 创建虚拟环境
python -m venv .venv

# 3. 激活虚拟环境
.venv\Scripts\activate

# 4. 安装依赖
pip install -r requirements.txt

# 5. 启动
python web_app.py
```

### 5.3 预装库列表（requirements.txt）

| 类别 | 库 | 版本 |
|------|-----|------|
| Web框架 | flask / flask-cors / Werkzeug | 3.1.3 / 6.0.4 / 3.1.8 |
| AI/模型 | openai / modelscope / llama-cpp-python / litellm | 2.41.0 / 1.37.1 / 0.3+ / 1.88.0 |
| 向量检索 | faiss-cpu / numpy | 1.14.2 / 2.4.6 |
| 数据处理 | PyYAML / requests / beautifulsoup4 / pillow / tiktoken | 6.0.3 / 2.34.2 / 4.15.0 / 12.2.0 / 0.13.0 |
| 语音 | faster-whisper / opencv-python-headless | 1.2.1 / 4.13.0.92 |
| 桌面UI | PySide6（LGPL） | 6.11.1 |
| PDF解析 | pypdf / pdfplumber | 6.13.0 / 0.11.9 |
| 浏览器 | playwright | 1.58.0 |
| 调度 | apscheduler / watchdog | 3.11.1 / 6.0.0 |

### 5.4 按需安装库

| 库 | 用途 | 安装命令 |
|-----|------|---------|
| audiocraft | MusicGen音乐生成 | pip install audiocraft |
| diffusers | Stable Diffusion图片生成 | pip install diffusers |
| TTS | Coqui语音合成 | pip install TTS |
| torch | 深度学习 | pip install torch |

### 5.5 启动方式

| 方式 | 命令 |
|------|------|
| 命令行 | `python web_app.py` |
| 桌面快捷方式 | 双击"Harven AI.lnk" |
| 批处理 | 双击"启动HarvenAI.bat" |

### 5.6 首次启动流程

```
首次启动
    ↓
config.yaml default_model为空 → 跳转/first_run
    ↓
硬件检测 → CPU/GPU/内存检测
    ↓
模型推荐 → 根据配置推荐合适模型
    ↓
用户选择 → 下载安装模型
    ↓
管理员密码设置 → 初始密码harven2026，首次登录强制改密
    ↓
进入主界面
```

---

## 第六部分：开发者指南

### 6.1 目录结构

```
HarvenAI/
├── src/
│   ├── core/                    # 核心模块
│   │   ├── engine.py            # 对话引擎（核心）
│   │   ├── morality.py          # 诚实原则+系统提示词
│   │   ├── tool_executor.py     # 代码执行层
│   │   ├── memory.py            # 记忆系统
│   │   ├── knowledge_base.py    # 知识库
│   │   ├── multi_model_manager.py  # 多模型管理
│   │   ├── hardware_detector.py # 硬件检测
│   │   ├── model_installer.py   # 模型安装器
│   │   ├── file_scanner.py      # 文件安全扫描
│   │   ├── code_sandbox.py      # 代码沙箱
│   │   ├── agent_registry.py    # Agent注册表
│   │   ├── skill_manager.py     # Skill管理
│   │   ├── scripts_runner.py    # Skill脚本执行引擎（AST沙箱）
│   │   ├── panel_manager.py     # UI面板管理器
│   │   ├── panel_routes.py      # 面板API路由
│   │   ├── task_manager.py      # 任务管理
│   │   ├── collab_manager.py    # 协作模式管理
│   │   └── device_manager.py    # 设备管理
│   ├── models/
│   │   └── database.py          # 数据库模型
│   ├── utils/
│   │   └── config.py            # 配置管理
│   └── ui/
│       └── main_window.py       # PySide6桌面窗口
├── templates/
│   ├── index.html               # 主页面
│   └── includes/                # 30个include文件
├── static/
│   ├── css/                     # 样式
│   ├── js/
│   │   ├── app.js               # 主JS
│   │   └── modules/             # 18个JS模块
│   └── icons/                   # 图标
├── skills/                      # 技能脚本（旧版兼容）
├── data/
│   ├── skills/                  # 可执行Skill（accounting/code_audit/music-gen等）
│   ├── panels/                  # UI面板（accounting等）
│   └── sqlite/
│       └── lingzhi.db           # SQLite数据库
├── config.yaml                  # 配置文件
├── web_app.py                   # Flask入口
├── requirements.txt             # 依赖列表
└── 启动HarvenAI.bat             # 启动脚本
```

### 6.2 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 对话引擎 | engine.py | 管理对话流程、模型调用、工具执行 |
| 诚实原则 | morality.py | 系统提示词、身份、能力说明 |
| 工具执行 | tool_executor.py | 提取代码块、安全检查、执行 |
| 记忆系统 | memory.py | 短期记忆、长期记忆、跨对话记忆 |
| 知识库 | knowledge_base.py | 文档上传、向量化、语义搜索 |
| 多模型管理 | multi_model_manager.py | 本地模型+外部模型统一管理 |
| 硬件检测 | hardware_detector.py | CPU/GPU/内存检测、模型推荐 |
| 文件扫描 | file_scanner.py | 5层安全扫描 |
| Agent注册 | agent_registry.py | 预置+自定义Agent |
| 协作管理 | collab_manager.py | 云地协作+本地双模 |

### 6.3 前端JS模块（18个）

| 模块 | 文件 | 职责 |
|------|------|------|
| 核心框架 | app-core.js | 主框架 |
| 对话 | app-conversation.js | 对话+查询模式 |
| 发送 | app-send.js | 消息发送 |
| 模型管理 | app-model-manager.js | 模型配置 |
| 知识库 | app-knowledge.js | 知识库管理 |
| 设置 | app-settings.js | 设置面板 |
| Skills | app-skills.js | 技能管理（兼容旧版） |
| 硬件 | app-hardware.js | 硬件检测 |
| 网络 | app-network.js | 联网搜索 |
| 设备 | app-devices.js | IoT设备 |
| 插件 | app-plugins.js | 插件管理 + Skills核心功能（1423行）+ CLI-Anything推荐工具 |
| 进化 | app-evolution.js | 受控进化 |
| 统计 | app-stats.js | 统计模块 |
| 保底 | app-fallback.js | 保底机制 |
| 知识萃取 | app-extraction.js | 知识萃取 |
| 版本检查 | app-version-check.js | 版本检测 |
| HarvenHub | app-harvenhub.js | 社区市场 |
| 媒体 | app-media.js | 媒体播放 |

### 6.4 数据库结构

| 表名 | 说明 |
|------|------|
| user_profile | 用户信息 |
| user_preferences | 用户偏好 |
| specializations | 专项培养 |
| conversations | 对话记录 |
| messages | 消息记录 |
| devices | IoT设备 |
| device_commands | 设备命令 |
| device_permissions | 设备权限 |
| structured_tasks | 任务列表 |
| cognitive_locks | 长期记忆 |

### 6.5 配置文件（config.yaml）

```yaml
# 核心配置
default_model: ""              # 默认模型（空=首次启动硬件检测）
admin_password: "harven2026"   # 管理员密码（首次登录强制修改）
search_provider: "bing"        # 搜索引擎（bing/searxng）
tool_execution_enabled: true   # 代码执行开关
searxng_url: ""                # SearXNG自建实例URL（可选）

# 查询模式
default_query_mode: "auto"     # kb_only/direct/auto（auto行为同direct）

# 协作模式
collab_mode: "off"             # off/cloud_local/local_dual
collab_enabled: false          # 协作模式开关

# 模型配置
ollama:
  base_url: "http://localhost:11434"

backends:
  local:
    default_model: "qwen2.5:14b"  # 本地默认模型
  external:
    default_model: ""              # 外脑默认模型（空=使用设置面板选择）
```

### 6.6 ⚠️ 开发者五大铁律（踩坑总结）

这些是实际开发中反复踩坑后总结的关键教训，对维护者至关重要：

| # | 铁律 | 说明 |
|---|------|------|
| 1 | **重启铁律** | 改 Flask 后重启前必须 `netstat -ano \| findstr :7000` 确认无残留进程；`debug=False` 时模板被缓存，`taskkill` 可能遗漏后台进程 |
| 2 | **HTML 铁律** | 改 `templates/includes/*.html` 后必须检查 `<div>` 开闭平衡（少一个 `</div>` → 后续面板全失效，已踩坑 2 次） |
| 3 | **Flask 线程铁律** | `app.run()` 必须 `threaded=True`（单线程一个请求挂起拖垮整个服务） |
| 4 | **config.yaml 铁律** | 保存 config 用 `yaml.dump(dict(config),...)` 而非 `yaml.dump(config,...)`，否则 PyYAML 写 `!!python/object/new:` 导致 `safe_load` 崩溃 |
| 5 | **NUCLEAR 覆盖铁律** | `index.html` 中的 `switchSettingsTab` 用 `window.switchSettingsTab = function(){...}` 完全覆盖 `app-settings.js` 版本。**改设置面板切换逻辑必须改 index.html 的 NUCLEAR 版**，改 app-settings.js 不生效 |

---

## 第七部分：能力边界

### 7.1 能做什么（29项功能）

1. 本地知识库（三层架构）
2. 代码执行（操控电脑的核心方式）
3. 文件管理（读写/目录/打开文件）
4. 任务自动化（软件安装/模型下载）
5. 专项培养（对话角色设定）
6. 监控中心（RSS话题追踪）
7. 垂直模型（6种内置+动态扩展）
8. 图片识别（视觉模型）
9. 联网搜索（Bing+SearXNG三态检测）
10. 模型安装（pip/HF/ModelScope/Ollama/GitHub）
11. 通用模型管理（Ollama管理）
12. 查询模式（本地/外脑）
13. 协作模式（云地协作/本地双模）
14. 硬件检测（自动推荐模型）
15. 多模型管理（28家厂商）
16. 插件管理（5个子Tab）
17. Skills系统（可执行脚本+Skill Builder）
18. Agent智能体（注册中心+Agent工坊）
19. 安全防护（三层）
20. 杀毒扫描（ClamAV）
21. 设备管理（IoT驱动）
22. 手机联动（远程对话）
23. IM连接（企微/钉钉/飞书）
24. 数据保险箱（备份/恢复）
25. 品牌定制（名称/图标/外观）
26. 受控进化（提案/审批/应用/回滚）
27. CLI-Anything集成（79个软件CLI封装，FC工具+推荐卡片）
28. UI面板系统V1（iframe沙盒+postMessage，自定义管理面板）
29. 外部模型安装器（零推荐+搜索优先+硬件检测）

### 7.2 不能做什么

| 限制 | 说明 |
|------|------|
| 控制桌面界面 | 不能移动鼠标/点击按钮 |
| 打开GUI应用窗口 | 不能直接控制其他软件窗口 |
| rm -rf / del /S / format / shutdown / reboot | 危险命令黑名单 |
| ctypes / winreg / win32api / win32com | 危险模块黑名单 |
| 超时120秒（下载5分钟） | 代码执行超时限制 |

### 7.3 安全限制

| 限制类型 | 具体内容 |
|---------|---------|
| 危险命令 | rm -rf / del /S / format / shutdown / reboot |
| 危险模块 | ctypes / winreg / win32api / win32com |
| 越权调用 | 通用模型不能直接调用垂直模型 |
| 联网搜索代码 | 模型不能自己写requests搜索（系统有内置Bing） |
| 文件上传 | 5层安全扫描（扩展名/大小/Magic Bytes/内容模式/杀毒） |
| 代码执行 | 120秒超时（下载类5分钟） |

### 7.4 代码执行环境

| 项目 | 说明 |
|------|------|
| Python环境 | `.venv\Scripts\python.exe`（预装所有依赖） |
| 预装库 | modelscope / openai / flask / faiss / requests 等12个 |
| 超时 | 普通代码120秒，下载/安装类5分钟 |
| 沙箱 | Docker容器隔离（优先）/ 子进程隔离（降级） |

---

## 附录

### A. 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v6.3 | 2026-08-03 | **引擎优先架构（FC→Skill转变）**：_try_quick_command扩展（list_dir/read_file/check_software加到快速命令，正则匹配"下载了什么"→秒回不经过模型）；代码执行优先（模型写的代码先execute_python_code()执行，不再直接过滤）；去掉FC误导警告（不再说"应该用内置工具"，改为"安全限制未执行"）；**多模型设置修复**：8个内置模型全显示（之前只4个）；模型选择改为input+datalist（可输入任意模型名如glm-5.2）；保存API Key修复（之前只存4个→现在全存，空输入不清空已有key）；外脑下拉框动态加载（含自定义模型）；底部状态简化（只显示当前模型）；**GLM配置修复**：删除错误的"哈文GLM"自定义provider，正确配置zhipu的base_url；**对话上下文**：查看旧对话后发消息→自动开新对话；历史≥6条→显示"建议新对话"提示 |
| v7.0 | 2026-08-09 | **出站控制改革**：默认不拦截（正常模式）——`_whitelist_enabled`默认False，新安装即可使用任意外脑API（DeepSeek/GLM/OpenAI/Claude/Gemini等），不再被"不在允许域名列表中"拦截；内置17个常用API域名+config.yaml可扩展；出站请求日志记录（`_outbound_log`，最多200条）；新增`/api/outbound/status`+`/api/outbound/toggle`API；严格模式可选开启（`independence.whitelist_enabled: true`）；沙箱防护恶意代码（Skill脚本禁subprocess/socket），白名单只作可选功能 |
| v6.2 | 2026-08-02 | **HarvenHub云端集成**：新增`/api/harvenhub-cloud/browse`端点代理harvenhub.com；skill安装自动下载+解压到`data/skills/`；文件名解析修复（RFC 5987）；FC工具安装后自动重新加载（不需重启）；工具检测改为子进程（避免`import windows_use`崩溃主进程）；正确import名映射（python-docx→docx, python-pptx→pptx）；**二维码修复**：SVG→base64 PNG（1.3KB）；手机IP自动检测（切tab时触发）；**FC工具扩展**：list_dir+read_file注册到TOOL_REGISTRY（共40个工具）；**HarvenHub后端**：需求广场/评价/下载外键约束修复（users表加guest/anonymous）；重试3次解决database locked；管理后台加需求删除+用户删除按钮；`/version.json`路由；**端口5000→7000**（解决Windows Docker/macOS AirPlay冲突）；版本0.2.0→0.2.1 |
| v6.1 | 2026-07-20 | 设置面板重构（独立💾数据Tab+插件管理5子Tab）；Agent注册中心/工坊移至插件管理子导航；Skills升级为可执行脚本系统（scripts_runner.py）；CLI-Anything B+集成；UI面板系统V1；外部模型安装器零推荐重写；SearXNG三态检测；config.yaml修正（default_query_mode）；目录结构补齐；开发者五大铁律 |
| v6.0 | 2026-07-18 | 超时30→120秒；工具执行后二次推理；stderr智能过滤；代码审计Skill；Skill触发词自动执行；记忆系统方案C（按需提取+用引擎模型）；联网意图不阻断；目录结构注入提示词；iframe allow-modals；代码块格式强制；P0/P1/P2全归零；134条垃圾记忆清理 |
| v5.0 | 2026-07-08 | 清理功德行残留+auto模式残留+DuckDuckGo残留；LOCAL_CAPABILITIES强化；.venv Python修复 |
| v4.0 | 2026-07-03 | 功德行删除，纯诚实原则 |
| v3.0 | 2026-06-13 | 模式架构重构 |

### B. 许可证

- 主许可证：Apache 2.0
- PySide6：LGPL（与Apache 2.0兼容）
- 禁止使用：PyQt6（GPL-3.0）、PyMuPDF（AGPL-3.0）

### C. 项目信息

- 项目路径：H:\HarvenAI\
- 数据库：data/sqlite/lingzhi.db
- 配置文件：config.yaml
- 启动命令：`python web_app.py`
- 联系邮箱：38552540@qq.com

---

*本文档由 Harven AI 团队维护，最后更新：2026-08-02*

---

## 附录D：v6.2变更详解（2026-08-02）

### D.1 HarvenHub云端集成

| 功能 | 端点 | 说明 |
|------|------|------|
| 云端浏览 | `GET /api/harvenhub-cloud/browse` | 代理harvenhub.com的API，自动转换字段名（type→item_type） |
| 云端下载 | `POST /api/harvenhub-cloud/download/<id>` | 下载skill→自动安装到`data/skills/<name>/SKILL.md` |
| 云端上传 | `POST /api/harvenhub-cloud/upload` | 上传skill/tool到社区 |
| 连接检查 | `GET /api/harvenhub-cloud/check` | 检查与harvenhub.com的连接状态 |

**前端改动**：`hhMarketLoad()`从本地`/api/harvenhub-market/browse`改为云端`/api/harvenhub-cloud/browse`，显示102个云端内容（21 skill + 14 tool + 67 model）。

**安装流程**：
```
用户点"安装" → POST /api/harvenhub-cloud/download/<id>
  → hub_download() 下载文件到data/downloads/
  → 获取item信息判断类型
  → 如果type=skill：复制/解压到data/skills/<name>/
  → 返回安装成功
```

**文件名解析修复**：Content-Disposition头用正则+RFC 5987正确解析，解决中文文件名[Errno 22]问题。

### D.2 FC工具系统

| 改动 | 说明 |
|------|------|
| 安装后自动加载 | `install_recommended_tool()`调用`load_fc_wrappers()`，不需重启 |
| 工具检测子进程化 | 用`subprocess.run([python, '-c', 'import xxx'])`检测，避免`import windows_use`崩溃主进程 |
| 正确import名映射 | `PKG_IMPORT_MAP`：python-docx→docx, python-pptx→pptx, windows-use→windows_use |
| 去掉pip show兜底 | pip show有假阳性（包存在但import崩溃），只用子进程import检测 |
| 新增FC工具 | `list_dir`（列目录）+ `read_file`（读文件），TOOL_REGISTRY共40个工具 |
| 方案A扩展 | 当模型有FC工具但不主动调用时，引擎自动检测关键词并执行：A-1检查软件、A-2列目录（"下载了什么"→自动list_dir）、A-3读文件（"看看文件"→自动read_file） |

### D.3 HarvenHub后端修复

| 问题 | 原因 | 修复 |
|------|------|------|
| 需求广场500 | `user_id='anonymous'`外键约束失败 | users表加guest/anonymous用户 + PRAGMA foreign_keys=OFF |
| 下载500 | `downloads.user_id='anonymous'`同上 | 同上 |
| 评价500 | `reviews.user_id`同上 | 同上 + 重试3次 |
| 上传500 | items插入外键约束 | users表已有system用户，修复后正常 |
| database locked | SQLite并发写入 | PRAGMA busy_timeout=5000 + 重试3次 + WAL模式 |

### D.4 手机连接

| 改动 | 说明 |
|------|------|
| 二维码格式 | SVG→base64 PNG（1.3KB vs 67KB），`<img src="data:image/png;base64,...">` |
| IP自动检测 | 切到"手机+公网"tab时调用`detectLocalAddr()`，从API获取真实IP |
| 端口5000→7000 | 解决Windows Docker/macOS AirPlay冲突，防火墙规则已加 |
| 二维码API修复 | 返回JSON而非raw SVG，`int()`→`float()`支持小数小时参数 |

### D.5 其他改动

| 改动 | 文件 | 说明 |
|------|------|------|
| 版本0.2.1 | version.json/config.yaml/evolution_manager.py | 版本号+changelog更新 |
| 出站白名单 | config.yaml | 加`harvenhub.com`到allowed_domains |
| 管理后台按钮 | admin.html | 需求删除+用户删除按钮 |
| version.json路由 | HarvenHub __init__.py | `GET /version.json`返回版本信息 |
| 插件子导航 | index.html NUCLEAR覆盖 | 切到关于tab时隐藏插件子导航 |

---

## 附录E：v7.0变更详解（2026-08-09）

### E.1 引擎6层流程（v7.0核心架构）

```
用户发消息
  ↓
第1层：_try_quick_command（正则匹配，秒回，不经过模型）
  ├─ "下载了什么|什么.*下载|下了.*啥|最近.*下载|昨天.*下载" → list_dir(Downloads)
  ├─ "目录.*什么|文件夹.*什么|有什么文件|看看.*目录|查看.*目录" → list_dir(指定路径)
  ├─ "安装.*模型|下载.*模型" → 模型安装
  ├─ "卸载.*模型|删掉.*模型" → 模型卸载
  ├─ "本地.*模型|查看本地模型" → 列出已装模型
  └─ 没匹配 ↓

第2层：_try_smart_split（智能拆分，阈值≥1）
  ├─ 检测金额：正则 (\d+(?:\.\d+)?)\s*元
  ├─ ≥1个金额 → 拆分多条记账
  ├─ 检测提醒：该(.+?)了|记得(.+?)|提醒我(.+?)|别忘了(.+?)
  ├─ 含提醒词 → 生成提醒
  ├─ 分类推断：买菜/交通/学习/餐饮（关键词匹配）
  └─ 没匹配 ↓

第3层：协作模式检查（安全开关）
  ├─ OFF → _collab_on = False
  │   ├─ 安全Skill可用（accounting/finance_report/code_audit/health_tracker）
  │   └─ 垂直Skill被阻止（music-gen/song_production）
  └─ ON → _collab_on = True
      └─ 所有Skill可用 ↓

第4层：_try_skill_trigger_match → run_skill_script_streaming（流式执行）
  ├─ 扫描所有Skill的trigger词（支持|分隔+正则）
  ├─ 匹配 → 检查协作模式（垂直Skill在OFF时阻止）
  ├─ 执行 → run_skill_script_streaming() 逐行yield stdout
  ├─ Skill名称动态显示：f"检测到【{skill_name}】触发词"
  └─ 没匹配 ↓

第5层：旧协作逻辑（fallback）
  ├─ cloud_local：外脑分析→调垂直参数→垂直执行
  ├─ local_dual：本地模型调度→调垂直参数→垂直执行
  ├─ 失败 → 提示"模型未返回内容"（含协作模式建议）
  └─ 非协作模式 → 跳过 ↓

第6层：通用模型回答
  ├─ 本地模式 → Ollama模型
  └─ 外脑模式 → DeepSeek/GLM/豆包等
```

**消息存储**：所有层级（1-4）执行结果都存入conversations+messages表，解决空对话问题。

**流式执行**：`run_skill_script_streaming()`使用`subprocess.Popen`逐行读取stdout，通过`yield`实时显示进度。

### E.2 Skill系统v2（10个Skill）

#### E.2.1 Skill完整清单

| Skill | 类型 | 触发词 | 功能 |
|-------|------|--------|------|
| accounting | 脚本 | 记一笔\|记账.*元\|花了.*元\|支出.*元\|收入.*元\|月度汇总\|查账\|我的账单\|花了多少\|余额多少 | 家庭记账（SQLite CRUD） |
| code_audit | 脚本 | 检测废代码\|代码审计\|代码健康\|废命令\|scan code\|audit code | 代码健康检测（P0/P1/P2） |
| finance_report | 脚本 | 财务月结\|财务报告\|月度报告\|财务汇总\|收支报告\|本月报告\|月结 | 生成Markdown财务报告 |
| health_tracker | 脚本 | 记录运动\|记录体重\|健康追踪\|运动记录\|健身记录\|健康数据 | 健康数据记录 |
| music-gen | 脚本 | 生成音乐\|创作音乐\|写首歌\|作曲\|写.*音乐\|做.*音乐\|乡村音乐\|BGM\|背景音乐\|编曲 | 调MusicGen生成音乐 |
| song_production | 脚本 | 写一首歌\|制作歌曲\|做一首完整的歌\|写歌配乐\|歌曲制作\|编一首歌 | 多模型编排（写词→作曲→配声→合成） |
| data_analyst | 提示词 | 数据分析\|分析数据\|数据报表\|统计图表 | 数据分析建议 |
| python_expert | 提示词 | Python代码\|python问题\|写Python\|python脚本 | Python编程建议 |
| tech_writer | 提示词 | 技术文档\|写文档\|技术写作\|API文档 | 技术写作建议 |
| workflow-extractor | 提示词 | 提取流程\|工作流\|流程分析\|workflow | 工作流分析 |

#### E.2.2 _helpers.py（7个函数）

```python
# data/skills/_helpers.py
call_vertical(model_id, params)     # 调垂直模型（POST /api/vertical/execute）
call_model(prompt, model=None)      # 调通用模型（POST /api/chat/internal，支持多外脑）
ask_user(question, options=None)    # 问用户（文件通信：问题文件→回答文件，120秒超时）
check_gpu()                         # 检查GPU显存（nvidia-smi）
get_db_path()                       # 返回 data/sqlite/lingzhi.db 路径
get_media_dir()                     # 返回 data/media/ 路径（自动创建）
get_reports_dir()                   # 返回 data/reports/ 路径（自动创建）
```

#### E.2.3 沙箱配置

| 配置 | 值 | 说明 |
|------|-----|------|
| 超时 | 300秒 | 支持长时间任务（MusicGen等） |
| 禁用模块 | subprocess/socket/shutil/ctypes/requests/urllib.request | 防止越权 |
| 禁用函数 | eval/exec/compile/__import__ | 防止注入 |
| 流式执行 | run_skill_script_streaming() | Popen逐行yield stdout |
| AST扫描 | scan_script_security() | 预扫描所有.py文件 |

#### E.2.4 song_production多模型编排

```python
# Step 1: 通用模型写歌词
lyrics = call_model("写一首乡村风格歌词，4行")

# Step 2: MusicGen生成音乐
music = call_vertical('musicgen-small', {'genre': 'country', 'duration': 30})

# Step 3: Bark演唱歌词
vocals = call_vertical('bark', {'text': lyrics[:200]})

# Step 4: FFmpeg合成
subprocess.run(['ffmpeg', '-y', '-i', music, '-i', vocals,
    '-filter_complex', 'amix=inputs=2:duration=longest', output])
```

#### E.2.5 触发词管理

- API：`GET /api/skills/triggers`（列表）、`PUT /api/skills/triggers/<id>`（更新）
- UI：设置→高级功能→Skill触发词管理（可视化编辑）
- 格式：`trigger: 词1|词2|词.*正则`（单数trigger，不是triggers）

#### E.2.6 触发词安全规则

- 必须含特定前缀或后缀（如"记账.*元"要求"记账"后跟"元"）
- 短词（≤3字符）容易误触：BGM/月结/audit 保留，其他改严格
- 协作OFF时垂直Skill的触发词不触发

### E.3 协作模式v2（安全开关）

#### E.3.1 三种模式

| 模式 | _collab_on | 安全Skill | 垂直Skill | call_vertical | 旧协作逻辑 |
|------|-----------|-----------|-----------|---------------|-----------|
| 🚫 OFF | False | ✅可用 | ❌阻止 | ❌阻止 | ❌跳过 |
| ☁️ 智能协作 | True | ✅可用 | ✅可用 | ✅可用 | ✅fallback |
| 🏠 本地协作 | True | ✅可用 | ✅可用 | ✅可用 | ✅fallback |

#### E.3.2 垂直Skill阻止机制

```python
_vertical_skills = ['music-gen', 'musicgen', 'bark', 'stable-diffusion', 'huatuo']
if not _collab_on and _skill_name and any(v in _skill_name.lower() for v in _vertical_skills):
    yield ("text", f"⚠️ 【{_skill_name}】需要开启协作模式才能执行。")
    return
```

#### E.3.3 多外脑支持

```python
# call_model()支持model参数
lyrics = call_model("写歌词", model="deepseek")  # 临时切换到DeepSeek
analysis = call_model("分析乐理", model="zhipu")  # 临时切换到GLM
# 引擎：switch_foreign_model() → 调用 → 恢复原模型
```

- API：`GET /api/multi-model/list`（列表）、`POST /api/multi-model/switch`（切换）
- UI：顶部栏下拉框快速切换

#### E.3.4 ask_user交互机制

```
Skill脚本调ask_user("什么风格？")
  ↓
写问题到 data/tmp/ask_user/{id}_question.json
  ↓
前端每2秒轮询 /api/ask_user/pending
  ↓
检测到问题 → 显示模态框 → 用户回答
  ↓
POST /api/ask_user/answer → 写 {id}_answer.txt
  ↓
Skill脚本读取回答 → 继续执行
```

### E.4 面板系统v2

#### E.4.1 创建面板功能

| 功能 | 说明 |
|------|------|
| 模板选择 | 5种预设：空白/💰记账/💪健身/✅待办/📦库存 |
| 图标选择 | 8个emoji：💰💪✅📦📅📊🏠🎯 |
| 统计图表 | 汇总卡片（总记录/分类数/数值合计）+分类柱状图 |
| 搜索筛选 | 可选生成搜索框+类型筛选 |
| 预览 | 创建前预览面板效果 |
| 卡片视图 | 表格↔卡片视图切换（toggleView） |
| 字段类型 | 7种：文本/数字/日期/下拉/多行文本/时间/图片 |

#### E.4.2 面板JS新功能

| 函数 | 说明 |
|------|------|
| toggleView() | 切换表格/卡片视图 |
| renderCards(records) | 渲染卡片视图 |
| renderTable(records) | 渲染表格视图 |
| formatValue(f, val) | 格式化字段值（图片缩略图/多行文本截断） |

#### E.4.3 记账API

| 端点 | 方法 | 说明 |
|------|------|------|
| /api/accounting/summary | GET | 总收入/总支出/分类统计/月度对比 |
| /api/accounting/records | GET | 收支明细（去重，200条） |

### E.5 流程编辑器

#### E.5.1 可选模型（8种）

| 模型 | 标识 | 说明 |
|------|------|------|
| 🤖 通用模型 | call_model | 写词/分析/总结 |
| 🎵 MusicGen | call_vertical_musicgen | 生成音乐 |
| 🎤 Bark | call_vertical_bark | 文字转语音 |
| 🎨 SD | call_vertical_sd | 生成图片 |
| 🏥 华佗 | call_vertical_huatuo | 医疗问答 |
| 🎬 FFmpeg | ffmpeg_merge | 合并音频/视频 |
| 💾 SQLite | sqlite_query | 查数据库 |
| 📝 写文件 | write_file | 保存到文件 |

#### E.5.2 代码生成

流程编辑器自动生成Python脚本：
- 导入_helpers.py
- 每步生成调用代码
- 支持参数传递（上一步结果→下一步输入）
- 保存为Skill（调/api/skills/generate）

### E.6 高级功能面板

设置→⚙️高级功能：

| 功能 | API | 说明 |
|------|-----|------|
| 触发词管理 | GET/PUT /api/skills/triggers | 可视化编辑所有Skill触发词 |
| 定时任务 | GET/POST/DELETE /api/scheduler/* | 添加/删除/查看定时任务 |
| Obsidian同步 | POST /api/knowledge/obsidian | 输入vault路径→同步.md到知识库 |
| AI生成Skill | POST /api/skills/generate | 输入描述→AI生成Python脚本 |
| AirLLM状态 | GET /api/airllm/status | 显示安装状态+说明 |
| 硬件信息 | GET /api/hardware/gpu | GPU型号+显存 |

### E.7 定时任务调度器

```python
# src/core/task_scheduler.py
# 后台线程每60秒检查 data/scheduler.json
# 到时间自动执行对应Skill

# 任务格式：
{
    "id": "1234567890",
    "name": "每日财务报告",
    "skill": "finance_report",
    "time": "07:00",
    "enabled": true,
    "last_run": "2026-08-09 07:00"
}
```

### E.8 AirLLM集成

```python
# src/core/airllm_integration.py
# 检查AirLLM安装状态
# 4GB显存可跑70B模型（逐层推理）
# 24GB显存用户不需要（Ollama直接跑32B以下更快）

# API: GET /api/airllm/status
# 返回: {installed, description, benefit, warning, install_cmd, models}
```

### E.9 智能消息拆分详细规则

#### E.9.1 触发条件

```python
# 阈值≥1（单个金额也拆分）
amounts = re.findall(r'(\d+(?:\.\d+)?)\s*元', user_message)
remind_words = re.findall(r'该(.+?)了|记得(.+?)|提醒我(.+?)|别忘了(.+?)', user_message)

if len(amounts) < 1 and len(remind_words) < 1:
    return None  # 不拆分
```

#### E.9.2 金额检测正则

```python
# 3种匹配模式
patterns = [
    r'买(\w+?)\s*(?:花(?:费)?|用)?\s*(\d+(?:\.\d+)?)\s*元',  # 买海鲜50元
    r'(\w+?)\s*(?:花费|花了|支出|用)\s*(\d+(?:\.\d+)?)\s*元',  # 加油花了600元
    r'(\w+?)\s+(\d+(?:\.\d+)?)\s*元',                          # 咖啡30元
]
```

#### E.9.3 分类推断

```python
cat_map = {
    '菜': '买菜', '海鲜': '买菜', '肉': '买菜', '水果': '买菜',
    '加油': '交通', '油': '交通', '车': '交通', '打车': '交通',
    '书': '学习', '包': '学习', '笔': '学习',
    '饭': '餐饮', '吃': '餐饮', '餐': '餐饮', '咖啡': '餐饮',
}
```

### E.10 设置面板（10个Tab）

| Tab | 内容 |
|-----|------|
| ①基本设置 | 联网搜索（Bing/SearXNG三态）+语言+数据库状态 |
| ②模型管理 | 本地模型+外部模型（搜索优先安装器）+多模型设置（8个提供商）+协作外脑 |
| ③个人画像 | 用户偏好/性格/职业 |
| ④外观定制 | 名称/图标/主题 |
| ⑤安全隐私 | 杀毒+代码沙箱+隐私模式+管理员密码 |
| ⑥💾数据 | 数据保险箱（备份/恢复）+设备命令日志 |
| ⑦🧩插件管理 | 5个子Tab：🛠️技能/🧱构建/🧩Agent/🏭工坊/📦外部模型 |
| ⑧关于&连接 | 手机+公网+IM连接+HarvenHub+版本信息 |
| ⑨⚙️高级功能 | 触发词管理+定时任务+Obsidian+AI生成+AirLLM+硬件 |
| ⑩📋流程编辑器 | 可视化创建多模型流程（8种模型+步骤排序+代码预览） |

### E.11 API完整清单（v7.0）

#### 记账API
| 端点 | 方法 | 说明 |
|------|------|------|
| /api/accounting/summary | GET | 收支汇总 |
| /api/accounting/records | GET | 收支明细 |

#### Skill/流程API
| 端点 | 方法 | 说明 |
|------|------|------|
| /api/skills/triggers | GET | 列出所有触发词 |
| /api/skills/triggers/<id> | PUT | 更新触发词 |
| /api/skills/generate | POST | AI自动生成Skill |
| /api/flows | GET | 流程列表 |
| /api/flows/<id>/test | POST | 测试运行流程 |
| /api/hub/flows | GET | 可分享流程列表 |

#### 对话/协作API
| 端点 | 方法 | 说明 |
|------|------|------|
| /api/chat/internal | POST | 内部模型调用（多外脑） |
| /api/chat/smart-split | POST | 智能拆分消息 |
| /api/conversations/by-mode | GET | 按模式筛选对话 |
| /api/multi-model/list | GET | 外脑列表 |
| /api/multi-model/switch | POST | 切换外脑 |

#### 高级功能API
| 端点 | 方法 | 说明 |
|------|------|------|
| /api/ask_user/pending | GET | 检查待回答问题 |
| /api/ask_user/answer | POST | 提交回答 |
| /api/hardware/gpu | GET | GPU信息 |
| /api/airllm/status | GET | AirLLM状态 |
| /api/knowledge/obsidian | POST | Obsidian目录同步 |
| /api/scheduler/list | GET | 定时任务列表 |
| /api/scheduler/add | POST | 添加定时任务 |
| /api/scheduler/<id> | DELETE | 删除定时任务 |

#### 面板API
| 端点 | 方法 | 说明 |
|------|------|------|
| /api/panels | GET | 面板列表 |
| /api/panels/create | POST | 创建面板（支持with_chart/with_search参数） |
| /api/panels/<id>/data | GET/POST | 面板数据CRUD |
| /api/panels/<id>/data/<row_id> | PUT/DELETE | 更新/删除记录 |

### E.12 GitHub开源准备

| 文件 | 说明 |
|------|------|
| .gitignore | 排除config.yaml/db/venv/__pycache__/模型缓存/临时文件 |
| config.example.yaml | 配置模板（空API Key，8个提供商） |
| README.md | 项目介绍+安装+功能+技术栈+贡献指南 |
| CONTRIBUTING.md | 贡献流程+代码规范+方向 |

### E.13 新增文件清单

| 文件 | 说明 |
|------|------|
| src/core/task_scheduler.py | 定时任务调度器（后台线程） |
| src/core/airllm_integration.py | AirLLM集成模块 |
| data/skills/_helpers.py | Skill辅助函数（7个） |
| data/skills/song_production/ | 多模型编排Skill |
| templates/includes/tab-流程编辑器.html | 流程编辑器UI |
| templates/includes/tab-高级功能.html | 高级功能面板UI |
| .gitignore | Git忽略文件 |
| config.example.yaml | 配置模板 |
| README.md | 项目说明 |
| CONTRIBUTING.md | 贡献指南 |
| VERSION | 版本信息 |

### E.14 开发者六大铁律（v7.0新增第6条）

| # | 铁律 | 说明 |
|---|------|------|
| 1 | 重启铁律 | 改Flask后重启前必须netstat确认无残留进程 |
| 2 | HTML铁律 | 改templates/includes/*.html后检查div开闭平衡 |
| 3 | Flask线程铁律 | app.run()必须threaded=True |
| 4 | config.yaml铁律 | 保存config用yaml.dump(dict(config),...) |
| 5 | NUCLEAR覆盖铁律 | switchSettingsTab的NUCLEAR版在index.html |
| 6 | **面板类名铁律** | 新面板必须用class="tab-panel"（不是settings-subpanel），否则_showPanel找不到 |

### E.15 v7.0版本信息

```
版本: v0.3.0
构建: 2026-08-09
Python文件: 112个
JS文件: 25个
Skill: 10个（6脚本+4提示词）
API端点: 20+
_helpers.py函数: 7个
```

---

*本文档由 Harven AI 团队维护，最后更新：2026-08-09*
