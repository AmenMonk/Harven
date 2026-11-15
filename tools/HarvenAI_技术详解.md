# Harven AI 技术详解

> **版本**: 2026-07-24 v7.0 云端社区市场 + CLI-Anything + 代码安全  
> **许可**: Apache-2.0  
> **作者**: AmenMonk <38552540@qq.com>

---

## 一、产品定位

### 1.1 一句话定位

**Harven AI 是一款纯本地运行的私有 AI 助手，永久免费，不做付费会员。**

### 1.2 核心理念

| 维度 | 说明 |
|------|------|
| **隐私优先** | 100% 本地运行，数据不出设备，无遥测、无隐私泄露 |
| **永久免费** | 不设付费墙，所有功能免费开放 |
| **开源可控** | Apache-2.0 协议，源码完全透明 |
| **设备绑定安全** | 硬件指纹+设备绑定，防止未授权克隆 |
| **独立性** | 不与其他实例通信，不外传设备指纹 |

### 1.3 核心功用

- **AI 对话**：两种模式自由切换 —— 🏠本地引擎（魔搭GGUF/Ollama）/ 🤖外脑（28 厂商），协作两模式独立下拉
- **知识库管理**：本地文件（PDF/TXT/DOCX）导入后 FAISS 向量检索，打造个人知识助手
- **联网搜索增强**：内置 Bing（免费网页解析）+ SearXNG（自托管），可按需开关
- **多模型外脑**：支持 28 个国内外模型厂商，可自由扩展
- **技能系统**：可安装社区技能包，扩展 AI 能力
- **CLI-Anything集成**：通过FC工具控制Blender/GIMP等79个创意软件（🆕 v7.0）
- **HarvenHub云端市场**：浏览/搜索/下载/上传社区内容（🆕 v7.0，harvenhub.com）
- **代码安全过滤**：三层过滤防止模型输出代码，FC结果自动yield给用户（🆕 v7.0）
- **IoT 设备联动**：MQTT/HTTP/WebSocket 协议接入智能设备
- **手机联动**：Token + 二维码 + 代理对话，手机端远程访问
- **受控进化**：AI 生成改进提案 → 用户审批 → 自动应用，渐进式自我优化

---

## 二、技术架构

### 2.1 技术栈总览

```
┌─────────────────────────────────────────────────┐
│                  前端层                           │
│  Bootstrap 5 + 原生 JS (22模块) + PWA + SSE流    │
├─────────────────────────────────────────────────┤
│                  API 层                           │
│  Flask (Blueprint) + RESTful + SSE 流式响应       │
├───────────┬───────────┬───────────┬─────────────┤
│  AI 引擎   │  知识系统  │  安全系统  │  扩展系统    │
│ engine.py │ knowledge │ morality  │ skills      │
│ multi_    │ _base.py  │ safety    │ plugins     │
│ model     │ FAISS     │ sandbox   │ evolution   │
│ web_search│ memory    │ rate_limit│ devices     │
├───────────┴───────────┴───────────┴─────────────┤
│                  数据层                           │
│  SQLite (WAL) + FAISS (向量) + JSON (配置/状态)    │
├─────────────────────────────────────────────────┤
│                  运行环境                         │
│  Python 3.10+ · 云模型（即开即用）/ 魔搭GGUF（默认）/ Ollama（可选） │
│  Windows / Linux / macOS · 首次启动自动检测硬件推荐模型    │
└─────────────────────────────────────────────────┘
```

### 2.2 后端架构（80+ 核心模块）

```
src/core/
├── 引擎层 (5)
│   engine.py              → AI 对话核心引擎（三段式查询）
│   model_manager.py       → 统一模型管理（Ollama + GGUF 自动切换）
│   multi_model_manager.py → 24 厂商多模型接入
│   llama_engine.py        → llama-cpp-python 本地推理（无需 Ollama）🆕
│   model_catalog.py       → 模型目录加载器（40+ 模型智能推荐）🆕
│
├── 知识系统 (6)
│   knowledge_base.py      → FAISS 向量知识库
│   memory.py              → 短期/长期记忆管理
│   memory_dreaming.py     → 记忆梦游（离散记忆关联重组）
│   active_memory.py       → 活跃记忆（当前上下文快速访问）
│   memory_eviction.py     → 记忆淘汰策略
│   content_reader.py      → 多格式文件导入（PDF/TXT/DOCX等）
│
├── 联网搜索 (1)
│   web_search.py          → Bing（免费网页解析）+ SearXNG 联网搜索
│
├── 安全系统 (7)
│   morality.py            → 道德约束：「诚实」原则，不审核不锁死
│   hw_security.py         → 硬件安全（设备指纹/绑定/防克隆）
│   code_sandbox.py        → 代码执行沙箱
│   rate_limiter.py        → 速率限制（防滥用）
│   file_scanner.py        → 文件安全扫描
│   independence.py        → 独立性防御（出站白名单）
│   isolated_db.py         → 数据库三库物理隔离
│
├── 技能与插件 (6)
│   skills_manager.py      → 技能生命周期管理
│   skills_routes.py       → 技能 API 路由
│   skill_builder.py       → 技能构建器
│   skill_discovery.py     → 社区技能发现
│   plugin_system.py       → 插件引擎
│   plugin_installer.py    → 插件安装器
│
├── 进化系统 (2)
│   evolution_manager.py   → 受控进化（提案/审批/回滚）
│   evolution_routes.py    → 进化 API 路由
│
├── 设备与 IoT (5)
│   device_manager.py      → 设备管理
│   device_api.py          → IoT 设备 API（根目录）
│   device_binding.py      → 设备绑定
│   device_starter.py      → 设备启动引导
│   device_drivers.py       → IoT 驱动框架
│
├── 联网与 IM (5)
│   mobile_connector.py    → 手机联动（Token + 二维码）
│   im_connector.py        → IM 连接（企微/钉钉/飞书/QQ）
│   im_routes.py           → IM API 路由
│   browser_agent.py       → 浏览器代理
│   github_downloader.py   → GitHub 下载器
│
├── 数据库 (3)
│   db_router.py           → 三库路由器
│   meta_sharder.py        → 元数据分片
│   conversation_archiver.py → 对话归档
│
├── 工具与基建 (19)
│   event_bus.py           → 事件总线
│   scheduler.py           → 定时任务调度
│   gen_scheduler.py       → 通用调度器
│   planner.py             → 任务规划器
│   task_manager.py        → 任务管理器
│   workflow.py            → 工作流引擎
│   monitor.py             → 系统监控
│   ollama_daemon.py       → Ollama 守护进程
│   hardware_detector.py   → 硬件检测 + 模型推荐
│   stats_manager.py       → 统计管理器
│   stats_routes.py        → 统计 API
│   fallback_manager.py    → 保底机制
│   fallback_routes.py     → 保底 API
│   extraction_manager.py  → 知识萃取
│   extraction_routes.py   → 萃取 API
│   harvenhub_api.py       → HarvenHub 市场 API
│   harvenhub_market.py    → HarvenHub 市场
│   ...                     → 更多
│
├── 用户系统 (3)
│   user_profile.py        → 用户画像
│   visitor_manager.py     → 访客管理
│   profile_auto_updater.py → 画像自动更新
│
├── 模型管理 (4) 🆕
│   model_recommend.py     → 模型推荐引擎（硬件+需求→双路径）
│   model_downloader.py    → 魔搭 GGUF 模型下载器
│   model_syncer.py        → ModelScope 自动同步新模型
│   model_catalog.json     → 38 个模型元数据库
│
├── Agent 系统 (4) 🆕
│   agent_registry.py      → Agent 注册中心（意图路由）
│   agent_runner.py        → Agent 执行引擎
│   agent_routes.py        → Agent API 接口
│   multi_agent.py         → 多智能体协作
│
└── 其他 (8)
    collab_manager.py       → 协作模式管理器
    collab_routes.py        → 协作 API
    learner.py              → 持续学习引擎
    image_gen.py            → 图片生成
    image_processor.py      → 图片处理
    tool_executor.py        → 代码块自动执行
    model_installer.py      → 模型安装器
    channel_manager.py      → 渠道管理
    resource_hub.py         → 资源中心
    background_task_manager.py → 后台任务
    specialization_executor.py → 专项培养
```

### 2.3 前端架构（22 个 JS 模块）

```
static/js/modules/
├── app-core.js            → 核心初始化 + 全局状态
├── app-conversation.js    → 对话核心逻辑 + 多选
├── app-send.js            → 消息发送 + 三种查询模式切换
├── app-settings.js        → 设置面板（8个主Tab）
├── app-knowledge.js       → 知识库管理
├── app-model-manager.js   → 多模型管理（24厂商）
├── app-network.js         → 联网搜索 + 浏览器代理
├── app-plugins.js         → 插件管理
├── app-skills.js          → 技能系统
├── app-evolution.js       → 受控进化
├── app-stats.js           → 统计面板
├── app-fallback.js        → 保底机制
├── app-extraction.js      → 知识萃取
├── app-hardware.js        → 硬件检测
├── app-devices.js         → IoT 设备
├── app-harvenhub.js       → HarvenHub 市场
├── app-media.js           → 多媒体处理
├── app-im.js              → IM 消息
├── app-bilibili.js        → B站管理
├── app-multi-agent.js     → 多Agent协作
├── app-monitor.js         → 系统监控
└── app-version-check.js   → 版本检测
```

### 2.4 模板结构（28 个 include 片段 + 3 个独立页面）

```
templates/
├── index.html             → 主页面（692 行，从 2228 行拆分）
├── first_run.html         → 首次运行引导页
├── mobile.html            → 手机端落地页
├── mobile-chat.html       → 手机端聊天界面
└── includes/
    ├── pwa-init.html              → PWA 初始化
    ├── model-tab-switch.html      → 模型子标签切换
    ├── force-populate.html        → 强制数据填充
    │
    ├── 基础设置 (4)
    │   ├── tab-基本信息.html
    │   ├── tab-语言.html
    │   ├── tab-外观.html
    │   └── tab-安全隐私.html
    │
    ├── 模型管理 (6)
    │   ├── tab-ai模型.html
    │   ├── tab-multimodel.html
    │   ├── tab-已安装.html
    │   ├── tab-模型市场.html
    │   ├── tab-手动拉取.html
    │   └── tab-本地模型推荐.html
    │
    ├── 高级功能 (8)
    │   ├── tab-skills.html
    │   ├── tab-skill-builder.html
    │   ├── tab-agent.html
    │   ├── tab-agents.html
    │   ├── tab-agent-工坊.html 🆕
    │   ├── tab-learner.html
    │   ├── tab-工作流编辑器.html
    │   └── tab-代码沙箱.html
    │
    ├── 扩展功能 (5)
    │   ├── tab-external-models.html
    │   ├── tab-local-services.html
    │   ├── tab-关于.html
    │   ├── tab-硬件检测.html
    │   └── tab-分享联动.html
    │
    ├── 其他 (2)
    │   ├── tab-个人画像.html
    │   └── tab-渠道接入.html
    │
    └── 待激活 (2)
        ├── modal-数据保险箱.html    # 🟡 P1 审计日志+备份导出（密码保护由 Skill 覆盖，不做）
        └── tab-保底状态.html       # ✅ 已完成（后端 6 API + 前端 JS 对接）
```

---

## 三、核心功能详解

### 3.1 三种查询模式

Harven AI 前端提供两个按钮 + 一个下拉菜单，对应用户可选的三种工作模式。**所有模式共享**知识库检索、Skills 注入、用户画像、习惯上下文、诚实约束——区别在于最终回答由谁生成：

| 按钮 | 模式 | 回答引擎 | 共享能力 | 适用场景 |
|------|------|---------|---------|----------|
| 🏠 **本地** | `local` | 本地引擎（魔搭GGUF/Ollama） | 知识库 + Skills + 画像 + 习惯 + 诚实 | 离线/隐私，数据不外传 |
| 🤖 **外脑** | `direct` | 外部模型（DeepSeek/豆包等） | 知识库 + Skills + 画像 + 习惯 + 诚实 | 需要更强推理能力 |

**协作体系 v2.0**（独立下拉菜单，两模式）：

| 模式 | 描述 | 大脑 | 执行者 |
|------|------|------|--------|
| ☁️ **云地协作** | 外脑当大脑 + 垂直模型当工具 | 外脑（DeepSeek/豆包等） | 本地垂直模型（MusicGen/Bark/SD）+ 本地通用模型最终整合 |
| 🏠 **本地双模** | 本地通用模型 + 垂直模型联动 | 本地通用模型（Qwen等） | 本地垂直模型 |

| 特性 | 说明 |
|------|------|
| **开关** | 输入框上方独立三位下拉菜单：🚫关闭 ☁️云地 🏠双模 |
| **外脑选择** | 设置→模型管理中手动指定（config.yaml `collab_foreign_model`） |
| **隐私保护** | 三档：strict（仅传摘要）/ balanced（去敏）/ full（完整+上下文） |
| **安全规则** | 禁止两个外部模型同时工作；协作模式下跳过自动路由 |

> **注意**：旧 `collab_enabled: true/false` 已废弃，改用 `collab_mode: "off" | "cloud_local" | "local_dual"`。兼容：`collab_enabled: true` → 自动转为 `cloud_local`。

### 3.2 多模型系统（24 个厂商 + 双路径本地）

通过 `MultiModelManager` 统一管理，支持本地双路径（魔搭直连 / Ollama）和 24 个云端厂商：

**国际厂商**（11个）：
OpenAI、Claude(Anthropic)、Gemini(Google)、Groq、Perplexity、Mistral、Together、OpenRouter、HuggingFace、Cohere、xAI(Grok)

**国内厂商**（14 个）：

**特点**：
- 出厂不内置任何 API 密钥，用户自行配置
- 统一 OpenAI-compatible API 接口（`_generate_openai_compatible()`）
- Gemini/Claude 特殊鉴权（`_build_auth_headers()` 处理讯飞等非标准鉴权）
- 免费优先（标注 `"free": True` 的厂商排前面）

### 3.3 联网搜索系统

**技术实现**：
- Bing 网页解析（免费，无需 API Key，`web_search.py` 直接解析搜索结果页）
- SearXNG 自托管元搜索（需本地 Docker，隐私最强）
- 用户可在设置中自由切换 关闭/Bing/SearXNG 三种模式
- 搜索结果注入上下文，本地引擎整合分析

**特色**：
- 搜索失败时注入 ⚠️ 标记到上下文，模型诚实告知
- 对话中传 `web_search: true` 即可触发，无需全局开关

### 3.4 知识库系统

**核心组件**：

| 组件 | 功能 |
|------|------|
| `knowledge_base.py` | FAISS 向量库（HNSW 索引），支持 PDF/TXT/DOCX/MD 导入 |
| `content_reader.py` | 网页内容提取（Bilibili/抖音等链接解析） |
| `memory.py` | 短期（对话缓存）+ 长期（向量持久化）双轨记忆 |
| `memory_dreaming.py` | 闲置时离散记忆片段随机组合，模拟"梦游"发现关联 |
| `memory_eviction.py` | 基于使用频率/时间的记忆淘汰策略 |
| `active_memory.py` | 活跃记忆高速缓存，加速上下文召回 |

**技术细节**：
- FAISS 索引存储在 `data/faiss/`
- 内容分块存储，支持语义相似度检索
- SQLite 存储元数据（文件路径、导入时间、大小等）

### 3.5 安全系统

**多层安全架构**（非序列化流程，各层独立工作）：

| 安全层 | 作用 |
|--------|------|
| **Prompt Injection 防御** | 30 条关键词检测，"忽略指令"、"绕过限制"等攻击 |
| **道德约束** | 「诚实」原则，analyze_intent 始终返回 safe，不审核不锁死 |
| **设备指纹** | 收集 MAC/CPU/主板信息，绑定设备防克隆 |
| **出站白名单** | 37 个域名白名单，外部连接仅限已注册的 AI 厂商 |
| **代码沙箱** | 隔离执行环境，限制系统调用 |
| **速率限制** | API 级别限制，防止资源耗尽 |
| **文件扫描** | 5 层扫描（扩展名/大小/Magic字节/内容模式/ClamAV） |
| **三库隔离** | 对话库/知识库/配置库物理分离 |

### 3.5.1 智能状态指示器（🆕 v6.0）

输入框上方实时显示模型状态，自动识别通用/垂直分类：

| 场景 | 状态显示 | 说明 |
|------|---------|------|
| 本地通用模型在线 | 🟢 本地通用模型 · qwen2.5:14b | 正常可用 |
| 仅有垂直模型 | 🟡 垂直模型就绪 · musicgen-small | 需要语言模型才能对话 |
| 两者都有 | 🟢 本地通用 + 🎵 musicgen-small | 完整能力 |
| 外脑模式 | 🔵 外脑 · deepseek | 云端推理 |
| Ollama 离线 | 🔴 本地引擎离线 | 需启动/安装 Ollama |

> 实现：`checkOllamaStatus()` 拉取模型列表 → `_NON_CHAT_KW` 关键词分类 → 前端动态渲染。

### 3.5.2 垂直模型路由器（🆕 v6.0）

| 文件 | 职责 |
|------|------|
| `vertical_router.py` | 意图检测→参数提取→完整度评分→确认卡/自动执行 |
| `data/vertical_params.json` | MusicGen/Bark/SD 的参数定义+触发词+权重 |

**工作流**：
```
用户消息 → detect_vertical_intent() → extract_params()
  → score_completeness()
    ├─ ≥80%: 自动执行
    └─ 40-80%: 弹出确认卡（用户补充参数）
```

**用户偏好记忆**：`apply_user_prefs()` 记录用户常用参数（情绪/风格等），下次自动填充。

### 3.5.3 长任务分片器（🆕 v6.0）

| 文件 | 职责 |
|------|------|
| `task_sharder.py` | 任务拆分→子任务执行→checkpoint 中断续跑→SSE 进度 |

长任务（PDF 分析、批量处理等）自动拆分为子任务，SQLite checkpoint 支持中断续跑。前端 `renderTaskProgress()` 渲染渐变色进度条。

### 3.5.4 提示词注入统一（🆕 v6.0）

**四种模式注入完全一致**：诚实原则 + 用户画像 + 习惯 + 知识库片段 + Skills。哈文调度层对所有模式一视同仁，唯一区别是大佬人选（谁负责最终回答）。

> 旧设计中外脑「最简身份」、云地「外脑说明书」已废弃，统一为完整注入。

### 3.6 Agent 调度引擎

**Harven AI = Agent 管理与调度平台**。Agent 是 Harven 的一等公民，其他模块为其服务。

**核心文件**：
| 文件 | 职责 |
|------|------|
| `agent_registry.py` | Agent 注册中心：注册/发现/路由，13 个内置 Agent |
| `agent_routes.py` | API 接口：GET/POST/PUT/DELETE Agent |
| `agent_runner.py` | Agent 执行引擎（函数模块，run_script_agent） |
| `multi_agent.py` | 多 Agent 协作调度 |

**内置 Agent 清单**（13 个）：
| Agent | 触发意图 | 特长 |
|-------|---------|------|
| code_reviewer | 审查/检查代码/debug | 代码审查 + 安全分析 |
| coder | 写代码/生成代码 | 代码生成 |
| translator | 翻译 | 多语言翻译 |
| teacher | 解释/教学 | 教学讲解 |
| file_explorer | 列出文件/检索 | 文件系统浏览 |
| researcher | 研究/调研 | 深度分析 |
| data_analyst | 分析数据/表格 | 数据洞察 |
| planner | 计划/规划 | 任务拆解 |
| critic | 批判/找漏洞 | 批判性思维 |
| ops_agent | 安装/运维/日志 | 系统运维 |
| computer_use | 控制电脑/截图 | 桌面操作 |
| music_generator | 电子音乐/EDM | 音乐生成 |
| ace_step | 作曲/生成音乐 | 音乐创作（MusicGen） |

**Agent 路由机制**：
```
用户消息 → 正则意图匹配(INTENT_ROUTES) → 路由到对应 Agent
         → Agent 独立 system_prompt + 专属 Skills → 生成回复
```

**自生成 Agent**：通过设置面板「🏭 Agent 工坊」填写名称+角色描述→AI 自动生成 system_prompt→一键注册，或直接调用 `POST /api/agents/register`。自定义 Agent 自动加入路由表。删除只影响自定义 Agent，内置 13 个不受影响。

### 3.7 模型目录系统（🆕 v2026-06-24）

**设计理念**：统一管理 38 个本地模型，支持 LLM/音乐/图片/视频/多模态/嵌入六类模型。

**核心文件**：

| 文件 | 职责 |
|------|------|
| `data/model_catalog.json` | 38 个模型的完整元数据（名称/VRAM/RAM/下载源/描述/标签） |
| `src/core/model_catalog.py` | 目录加载器 + 推荐引擎（`recommend_by_user_need()` 按硬件+需求返回双路径） |
| `src/core/model_recommend.py` | 硬件推荐（`recommend_by_hardware()` 按 VRAM/RAM 过滤） |
| `src/core/model_downloader.py` | 魔搭下载器：从 ModelScope 拉取 GGUF 模型 |
| `src/core/model_syncer.py` | 同步器：从 ModelScope API 自动搜索新模型 → 更新 catalog |
| `src/core/llama_engine.py` | 本地推理：llama-cpp-python 直接跑 GGUF，**无需 Ollama** |

**模型数据库覆盖**：

| 类别 | 数量 | 代表 |
|------|------|------|
| 💬 LLM 对话 | 14 | Qwen2.5(3B/7B/14B/32B)、DeepSeek-R1、Llama 3.3、Yi、ChatGLM、百川、InternLM、Mistral、Phi-3、Gemma 2 |
| 🎵 音乐生成 | 5 | MusicGen(Medium/Small)、AudioCraft、Riffusion、Suno Bark |
| 🎨 图片生成 | 6 | SDXL、SD 3.5、FLUX.1、Kolors（可图）、混元 DiT、LCM 极速版 |
| 🎬 视频生成 | 4 | CogVideoX(5B/2B)、AnimateDiff、OpenSora |
| 👁️ 多模态 | 4 | MiniCPM-V、Qwen2.5-VL、InternVL2、LLaVA |
| 🔍 向量嵌入 | 4 | BGE-M3、BGE 中文、Nomic、GTE |

**API 接口**：
```
GET  /api/models/catalog?category=music        → 按类别查看
GET  /api/models/catalog?vram=12&ram=32        → 按硬件过滤
POST /api/models/catalog/recommend             → 用户需求 → 双路径推荐
POST /api/models/sync                          → 手动触发 ModelScope 同步
```

### 3.8 双路径本地模型系统（🆕 v2026-06-24）

**理念**：新用户不强制装任何软件。两条路自由选。

**路径对比**：

| | 🇨🇳 魔搭直连（默认推荐） | 🐪 Ollama（可选） |
|------|---------------------|-------------------|
| 依赖 | 0（纯 Python） | 需安装 Ollama 服务 |
| 下载速度 | 国内满速（ModelScope） | 需科学上网（部分模型） |
| 推理引擎 | llama-cpp-python（内置） | Ollama API |
| 模型管理 | JSON 目录 → 哈文自动 | `ollama pull` 命令 |
| 适合 | 新用户、低配电脑、国内网络 | 进阶用户、频繁切换模型 |

**引擎自动切换逻辑**（`model_manager.py`）：
```
启动 → 检测 Ollama 是否运行
    ├── 是 → 用 Ollama 调用模型
    └── 否 → 扫描 data/models/*.gguf → llama-cpp-python 直接推理
         └── 都没有 → 提示用户从 AI模型 页选择路径
```

### 3.9 硬件检测 & 智能推荐（🆕 v2026-06-24）

**检测覆盖**：

| 项目 | 检测方式 |
|------|---------|
| CPU（型号/核心/线程） | PowerShell `Get-CimInstance` |
| RAM（总量/可用） | `psutil` 库 |
| GPU（型号/VRAM/厂商） | `nvidia-smi` + WMI |
| 磁盘类型（SSD/HDD） | PowerShell `Get-PhysicalDisk` |
| Ollama 状态 | `ollama --version` + `ollama list`（模型列表） |

**硬件适配表**：

| 硬件档位 | VRAM | 推荐模型（按类别） | 说明 |
|---------|------|---------|------|
| ⚪ 服务器 | ≥48GB | 💬Llama 3.3 70B/Qwen2.5 32B · 🎨FLUX/SD3.5 · 🎬CogVideoX 5B · 🎵MusicGen · 👁️全模态 | 所有模型随便跑 |
| 🟣 旗舰 | 24-48GB | 💬Qwen2.5 32B/Baichuan2 13B · 🎨SD3.5 · 🎬CogVideoX · 👁️全模态 | 大模型+视频全开 |
| 🟢 高端 | 12-24GB | 💬Qwen2.5 14B/DeepSeek-R1 14B · 🎨SDXL/SD3.5 · 🎵MusicGen · 👁️MiniCPM-V | 六类模型可跑，视频有限 |
| 🟡 中等 | 8-12GB | 💬Yi 1.5 9B/Gemma 2 9B/Qwen2.5 7B · 🎨SDXL · 🎵MusicGen · 👁️Qwen2.5-VL | 无视频生成 |
| 🟠 入门 | 4-8GB | 💬Qwen2.5 3B/Llama 3.2 3B/ChatGLM3 6B/Phi-3 · 🎨LCM极速版 · 🔍嵌入 | 轻量LLM+嵌入 |
| 🔴 低配 | <4GB | 🎨LCM极速版 · 🔍嵌入（BGE/GTE/Nomic） | 无LLM，纯CPU嵌入 |

### 3.10 存储优化（🆕 v2026-06-24）

**自适应存储策略**：

| 存储层 | 优化 | 效果 |
|--------|------|------|
| SQLite | WAL 模式 + `busy_timeout=5000` + `synchronous=NORMAL` | 读写快 2-5×，解决锁阻塞 |
| FAISS | `read_index(path, IO_FLAG_MMAP)` 磁盘映射 | 启动快 5×，不占满内存 |
| 磁盘适配 | 自动检测 SSD/HDD → HDD 用户优先 mmap | 低配电脑不卡顿 |
| 数据安全 | 备份 `model_catalog.json.bak` + SHA256 校验 | 防数据丢失 |

### 3.11 受控进化系统

**设计理念**：AI 可以在对话中提出改进建议，但必须经用户审核批准后才生效。

**工作流程**：
```
1. 用户或 AI 提交 proposal（通过设置面板或对话触发）
2. proposal 进入 pending_proposals 队列
3. 前端显示"待审批"角标 + 提案详情
4. 用户 approve → 白名单校验 → 仅 config_change 类型自动应用
5. 其他类型（skill/knowledge/other）标记为需手动处理
```

**安全机制**：
- 白名单控制：仅允许修改 `skills/` 和 `config.yaml` 指定键
- 修改前 diff 预览
- 修改后自动验证（前后端一致性检查）
- 异常时自动回滚

### 3.12 手机联动

**连接方式**：
1. 桌面端生成 Token + 二维码
2. 手机端扫描二维码 → 获得临时 Token
3. 手机通过代理 API 发送消息 → 桌面端 AI 处理 → 返回结果

**安全**：Token 时效性 + 设备验证，不暴露桌面端敏感信息。

### 3.13 IoT 设备联动

**万能接口原则**：无现成驱动时 Harven AI 临时生成驱动代码，动态加载。

**协议支持**：
- MQTT（物联网标准协议）
- HTTP（REST API）
- WebSocket（实时双向通信）
- AI Generated Driver（AI 临时生成的协议适配器）

**路由**：
```
POST /api/devices/ai-driver/generate  → AI 分析协议生成驱动
GET  /api/devices/ai-driver/list      → 查看已生成驱动列表
GET  /api/devices/ai-driver/{id}      → 查看/删除驱动
GET  /api/devices/ai-driver/{id}/code → 查看驱动源码
```

### 3.14 技能系统

**Markdown 即技能**：用户写 `.md` 文件（YAML 头 + 指令），放入 `data/skills/`，AI 自动加载。

| 功能 | 文件 | 说明 |
|------|------|------|
| 技能管理 | `skills_manager.py` | 创建/编辑/启停/热更新（watchdog） |
| 技能构建器 | `skill_builder.py` | AI 根据描述自动生成技能文件 |
| 技能发现 | `skill_discovery.py` | 社区技能搜索/安装 |
| API 路由 | `skills_routes.py` | 20+ REST 接口 + HarvenHub 市场查询 |

### 3.15 插件系统

| 组件 | 说明 |
|------|------|
| `plugin_system.py` | 插件引擎：注册/匹配/执行 |
| `plugin_installer.py` | 插件安装器：从 HarvenHub 或本地安装 |

### 3.16 IM 连接器

支持 4 个即时通讯平台，用于远程交互：

| 平台 | 状态 |
|------|------|
| 企业微信 | 适配器已注册 |
| 钉钉 | 适配器已注册 |
| 飞书 | 适配器已注册 |
| QQ | 适配器已注册（需 Bot AppID） |

核心文件：`im_connector.py`（4 平台适配器）+ `im_routes.py`（12 条 API）+ `channel_manager.py`（渠道管理）

---

### 3.17 CLI-Anything 集成（🆕 v7.0）

CLI-Anything 是 AI 控制桌面软件的命令行桥接器，支持79个创意软件（Blender/GIMP/Inkscape等）。

**三层架构：**
```
哈文（大脑）→ CLI-Anything（手）→ Blender.exe（工具）
```

**FC工具（3个）：**
- `cli_anything_list` — 列出已安装的CLI模块
- `cli_anything_install(software)` — 安装子模块（返回含下载地址）
- `cli_anything_execute(software, command, params)` — 执行软件命令

**关键修复（2026-07-22）：**
- `import sys` 缺失导致 `sys.executable` 报 NameError → 已修复
- Hub已安装时返回 success=True（不再返回False）
- 安装成功后自动提示软件本体下载地址（10个软件的官网URL）

**路由跳过逻辑：**
- 用户消息含CLI软件名（blender/gimp/inkscape等）→ 跳过Agent路由 + 跳过搜索
- 对话历史含CLI软件名 → follow-up消息也跳过Agent路由

核心文件：`builtin_tools.py`（cli_anything_install + _SOFTWARE_URLS + _build_install_msg）

---

### 3.18 代码安全过滤系统（🆕 v7.0）

防止模型输出代码作为文本（应该通过FC工具调用，不应写代码）。

**三层过滤：**

| 层 | 检测内容 | 位置 |
|----|---------|------|
| 第1层 | 代码块（```python/json/bash等）| engine.py 后处理 |
| 第2层 | 代码特征检测（2+特征=代码）| engine.py 后处理 |
| 第3层 | 裸import语句拦截 | engine.py 后处理 |

**代码特征检测（2+特征判定为代码）：**
- `import X` / `from X import`
- `def function(`
- `subprocess.方法(`
- `.run(capture_output/timeout)`
- `__import__(` / `os.system(`
- `print()` + `if:` 同时出现

**FC结果自动yield：**
- 工具调用成功后，自动yield结果摘要给用户（不依赖模型转述）
- 解决DeepSeek忽略FC结果的问题
- `execute_tool_call`返回JSON字符串 → 先json.loads解析 → 检查success/message

**补充替代回复：**
- 代码被过滤后回复<50字时，自动补充替代文本

核心文件：`engine.py`（_code_features + FC yield + 补充回复）+ `tool_executor.py`（is_safe_code）

---

### 3.19 HarvenHub 云端社区市场（🆕 v7.0）

HarvenHub 是哈文的云端社区市场，用户可以浏览/搜索/下载/上传社区内容。

**部署架构：**
```
用户浏览器/哈文 → https://harvenhub.com（DNS→82.157.182.80）
  → Nginx(443 HTTPS) → Docker(5001) → gunicorn(4 workers) → Flask → SQLite
```

**服务器：** 腾讯云轻量应用服务器（北京，2核4GB，60GB系统盘）

**云端客户端（harvenhub_client.py）：**
- `_safe_request()` — 包装requests，用bypass_independence绕过出站白名单
- `hub_browse()` — 浏览云端市场（优先云端，失败回退本地）
- `hub_search()` — 搜索云端市场
- `hub_download()` — 从云端下载文件
- `hub_upload()` — 上传到云端
- `hub_check_connection()` — 检查连接状态

**浏览/搜索路由（云地双源）：**
```
/api/harvenhub-market/browse → 优先查云端 → 失败回退本地
/api/harvenhub-market/search → 优先搜云端 → 失败回退本地
```

**HarvenHub安全修复（6项）：**
1. debug=True → False（环境变量控制）
2. SECRET_KEY硬编码 → os.environ.get()
3. admin API零鉴权 → @_require_admin装饰器（7个接口）
4. 密码裸SHA256 → werkzeug generate_password_hash（PBKDF2+盐）
5. CORS origins=["*"] → 从config.yaml读取
6. 管理员密码硬编码 → 从config.yaml读取

**用户端网页（5页面SPA）：**
- 首页（Hero+热门内容）+ 浏览+搜索+详情+关于

核心文件：`harvenhub_client.py` + `web_app.py`（4个云端路由）+ `config.yaml`（harvenhub_server_url）

---

### 3.20 方案C联网控制（🆕 v7.0）

三层防幻觉机制，控制模型何时联网、如何联网。

**三层：**
1. **tool_executor子进程安全** — 执行代码的子进程加载independence模块，堵安全漏洞
2. **_MODEL_INTERNET_CAPABILITY标记** — 12个外脑模型的联网能力标记
3. **透明告知** — 🌐没开+不联网→提示用户开启；🌐没开+外脑自带联网→透明告知

**事实查询搜索增强：**
- 搜索后抓取前3个URL正文（`_fetch_url_content` + `_fetch_pages_concurrent`）
- 让模型整合出具体数据（气温/股价等）
- 关键：必须用 `with bypass_independence()` 绕过出站白名单

**短消息误判修复：**
- ≤4字符消息含联网关键词（天气/股价等）时不判casual，否则搜索被跳过

**安全检查format误杀修复：**
- `format`从BLOCKED_COMMANDS移出，改用正则匹配`format+盘符`（如format C:）
- URL参数中的format不再被误杀

核心文件：`engine.py` + `tool_executor.py` + `independence.py`

---

## 四、竞品对比

### 4.1 Harven vs 竞品 详细对比（基于官方资料核实）

#### 基础架构

| 维度 | Harven AI | OpenClaw（龙虾） | Hermes（爱马仕） |
|------|-----------|-----------------|-----------------|
| **定位** | 行业垂直 AI 平台 | 个人本地优先 AI 工作助手（网关多渠道核心） | 企业级自学习 AI Agent 运行时 |
| **开源协议** | Apache 2.0 | MIT 开源 | MIT 开源 |
| **代码规模** | 80+ 核心模块 + 22 JS 模块 | ~20 TS/JS 核心模块，轻量化 | Python 完整工程化模块，分层微运行时 |
| **后端框架** | Flask Blueprint 模块化 | Node.js/TS 单体网关架构 | Python 异步分层运行时，支持微服务拆分 |
| **前端框架** | Bootstrap 5 + 原生 ES6 | 无独立 Web 前端，依托 IM 客户端交互 | 轻量化 Web 控制台，配套 CLI |
| **本地运行** | ✅ 本地优先，支持远程同网访问 | ✅ 纯本地自托管优先，可远端部署 | ✅ 本地 GPU 私有化部署+企业云端集群双模式 |
| **离线可用** | ✅ GGUF 离线推理 | ⚠️ 仅转发本地模型 API，无内置 GGUF 推理内核 | ⚠️ 支持对接本地 LLM，无原生离线量化推理 |

#### 模型能力

| 维度 | Harven AI | OpenClaw（龙虾） | Hermes（爱马仕） |
|------|-----------|-----------------|-----------------|
| **本地模型** | 双路径（魔搭 GGUF + Ollama） | ❌ 无内置本地模型加载，仅转发第三方本地 API | ❌ 无原生 GGUF 加载，仅兼容外部本地推理服务 |
| **本地模型数** | 38 个（LLM/音乐/图片/视频/多模态/嵌入） | 0 | 0 |
| **云端厂商** | 24 个（DeepSeek/豆包/通义...） | 全兼容主流云端，需用户自行填入 API Key | 18+ 主流厂商内置适配，无需手动配置 |
| **模型切换** | 前端下拉即切 + 对话口号切换 | 修改配置文件重启生效 | 控制台一键切换，支持自动故障降级切换 |
| **协作模式** | ✅ v2.0 两模式（云地/双模）+ 垂直模型调度 | ❌ 无混合内外脑协同逻辑 | ✅ 内置多模型分流执行 |
| **硬件推荐** | 6 档 VRAM 适配 + 双路径推荐 | 无硬件分级适配，仅做资源限制 | GPU 显存分级调度，企业集群资源调度 |

#### Agent & 自动化

| 维度 | Harven AI | OpenClaw（龙虾） | Hermes（爱马仕） |
|------|-----------|-----------------|-----------------|
| **内置 Agent** | 13 个（审查/编码/分析/翻译/运维/音乐...） | 少量通用预设 Agent（邮件/浏览器/文件） | 企业级通用执行 Agent，无垂直专项内置 |
| **自注册 Agent** | ✅ Agent 工坊：一句话创建 | ❌ 仅手动编写 Skill 脚本扩展 | ✅ 自动从工作流提炼生成可复用 Agent 技能 |
| **意图路由** | 正则匹配 → 自动分发到专项 Agent | 无统一意图路由，单 Agent 串行执行 | 内置任务意图分类器，自动分流执行链路 |
| **多 Agent 协作** | ✅ MultiAgentOrchestrator | ✅ 原生多 Agent 集群网关调度 | 🚧 多 Agent 协同功能开发中 |

#### 知识 & 记忆

| 维度 | Harven AI | OpenClaw（龙虾） | Hermes（爱马仕） |
|------|-----------|-----------------|-----------------|
| **知识库** | FAISS 向量检索（HNSW 索引） | ❌ 无原生向量知识库，仅本地 Markdown 文本存储 | 企业向量库对接，无内置本地 FAISS |
| **导入格式** | PDF/TXT/DOCX/MD/B 站/抖音 | 仅 Markdown 纯文本 | PDF/Word/Excel 结构化文档 |
| **记忆系统** | 6 模块（短期/长期/活跃/梦游/淘汰/萃取） | 双层文件记忆（当日+昨日会话 MD 日志） | 分层经验记忆，自动沉淀复用工作流程 |
| **用户画像** | 自动提取+持续更新+习惯追踪 | 基础用户偏好记录，无画像萃取机制 | 深度用户行为画像，用于自优化执行逻辑 |
| **离线知识库** | ✅ 完全本地向量存储检索 | ❌ 无向量检索，仅本地静态文本 | ❌ 依赖外部向量服务，本地仅缓存文本 |

#### 安全 & 隐私

| 维度 | Harven AI | OpenClaw（龙虾） | Hermes（爱马仕） |
|------|-----------|-----------------|-----------------|
| **数据位置** | 本地模式数据不出设备 | 记忆/配置本地存储，模型请求需外发 API | 企业数据本地私有化，云端部署则数据存服务商集群 |
| **安全层数** | 8 层（限速/注入防御/沙箱/白名单/扫描/设备指纹/三库隔离） | 基础权限校验，默认安全边界薄弱 | 企业完整安全隔离：沙箱、权限分组、操作审计、执行白名单 |
| **出站白名单** | 37 个域名白名单 | 无内置域名白名单，全量出站放行 | 全局 API/域名访问黑白名单管控 |
| **代码沙箱** | AST+正则双重检测，5 层文件扫描 | 极简命令权限拦截，无代码静态检测 | Docker/容器级执行沙箱，完整代码执行隔离 |
| **三库隔离** | 对话/知识/配置物理分离 | 所有数据统一文件目录混合存储 | 业务库、知识库、审计库物理分库隔离 |

#### 扩展 & 生态

| 维度 | Harven AI | OpenClaw（龙虾） | Hermes（爱马仕） |
|------|-----------|-----------------|-----------------|
| **技能系统** | Markdown 即技能，20+ API，热更新 | Lobster DSL 脚本技能，修改需重启网关 | 自动生成可复用 Skill，支持版本迭代 |
| **插件系统** | Python 插件，前置拦截+匹配执行 | TS 自定义插件，网关级扩展 | Python/Go 双语言插件，执行前置校验 |
| **IoT 联动** | MQTT/HTTP/WebSocket + AI 万能驱动 | ❌ 无 IoT 原生适配 | 企业物联网接口有限适配 |
| **手机联动** | Token+二维码+代理对话+推送 | 仅 IM 消息互通，无独立移动端联动 | 移动端 Web 控制台，远程指令下发 |
| **IM 连接** | 企微/钉钉/飞书/QQ 四平台 | 20+ 海外 IM（Telegram/WhatsApp/Discord 为主） | 国内外主流 IM 全覆盖，含国内钉钉/企微 |
| **受控进化** | AI 提案→用户审批→白名单应用 | ❌ AI 无法自主更新配置/技能 | AI 自动生成技能，人工审核入库 |
| **社区市场** | HarvenHub（技能/Agent/插件共享）| ClawHub 技能社区 | 官方企业技能市场，无社区共享生态 |

#### 部署 & 运维

| 维度 | Harven AI | OpenClaw（龙虾） | Hermes（爱马仕） |
|------|-----------|-----------------|-----------------|
| **安装方式** | 一键 bat + pip install（无需 Docker/Node） | Docker 一键部署/手动 Node 环境配置 | Docker 容器、企业集群部署脚本双方案 |
| **首次引导** | 两条路径：云模型（填 Key 即用）/ 本地（硬件检测→推荐→下载） | 无初始化向导，纯配置文件初始化 | 企业级分步初始化向导（权限、存储、模型） |
| **依赖项** | 0（云模型即开即用，无需本地服务） | 依赖 Node 运行时、外部 LLM API | Python 全栈依赖，支持离线依赖包打包 |
| **数据库** | SQLite WAL + FAISS mmap 自适应 | 纯文件 MD 存储，无结构化数据库 | PostgreSQL 企业数据库，支持本地 SQLite 单机模式 |
| **系统监控** | 内置 Monitor + 统计面板 | 极简日志输出，无可视化监控面板 | 完整运维监控面板、资源告警、操作审计日志 |
| **保底机制** | FallbackManager 自动降级 | 无模型故障自动切换 | 双模型自动降级，多任务独立故障兜底 |

#### 未来路线

| 方向 | Harven AI | OpenClaw（龙虾） | Hermes（爱马仕） |
|------|-----------|-----------------|-----------------|
| **行业模板** | 🚧 制造业/医疗/法律等行业 preset | ❌ 无行业垂直模板 | 🚧 金融/政企标准化行业工作流模板 |
| **AI 员工** | 🚧 Agent+IM→自动打电话/发邮件/找信息 | ✅ 原生支持 IM 自动化任务执行 | 🚧 企业数字员工自动化流程 |
| **多设备协同** | 🚧 一机部署，多终端（手机/平板/工控机）访问 | 单设备单实例，无跨设备同步 | ✅ 多终端统一账号、会话同步 |
| **视觉能力** | 🚧 本地多模态模型，看图识图 | 仅转发云端视觉模型 API | 内置云端多模态推理调度 |
| **语音交互** | 🚧 本地 TTS/STT，纯离线语音对话 | 依赖第三方语音云端接口 | 云端语音能力，无本地离线语音 |

### 4.2 三方优劣势分析

#### Harven AI 的优势 ✅

1. **完整的离线闭环**：唯一同时内置 GGUF 推理引擎 + FAISS 向量库的产品，断网完整可用
2. **垂直行业有备而来**：IoT 万能驱动、国内四平台 IM、手机联动、Agent 工坊，为行业落地铺好了路
3. **轻量双轨部署**：云模型即开即用（零本地依赖），或一键 bat 本地部署，无需 Docker/Node/容器
4. **8 层本地安全**：出站白名单 + AST 沙箱 + 三库隔离，竞品中安全层数最多
5. **记忆系统最强**：6 模块记忆体系（含梦游/萃取），竞品仅简单文件存储
6. **硬件适配最细**：6 档 VRAM 分级 + 双路径推荐，覆盖低配到服务器

#### Harven AI 的不足 ❌

1. **前端交互弱**：Bootstrap 原生 JS，无 OpenClaw 的多 IM 客户端交互、无 Hermes 的 CLI 工具
3. **海外生态空白**：IM 连接器只有国内四平台，无 Telegram/WhatsApp/Discord
4. **社区未启动**：HarvenHub 代码有了但没上线
5. **可视化/CLI 缺位**：无 OpenClaw 的 IM 客户端交互、无 Hermes 的 CLI 工具

#### 向竞品学什么（优先级排序）

| 优先级 | 学习项 | 来源 | 状态 | 理由 |
|--------|--------|------|------|------|
| 🔴 P0 | **HarvenHub 上线** | ClawHub | 待做 | 技能/Agent/插件分享是生态根基，代码已写，差最后一步 |
| ✅ | **多终端会话同步** | Hermes | 已完成 | 远程账号密码 + cookie 持久化 + 安全隐私页 |
| ✅ | **操作审计日志** | Hermes | 已完成 | 数据保险箱 tab：备份导出 + 操作日志 |
| 🟡 P1 | **IM 自动化任务** | OpenClaw | 重点做 | AI 员工场景：定时发邮件/自动回消息/触发工作流 |
| ✅ | **语音交互打通** | 两者 | 已完成 | 🎤录音→/api/audio/transcribe→输入框，已存在 |
| ✅ | **Docker 部署** | 两者 | 已完成 | Dockerfile + docker-compose.yml |
| 🟡 P1 | **工作流自动沉淀** | Hermes | 重点做 | 自动从对话提炼工作流 → 生成 Agent Skill |
| ⚪ P3 | **多 IM 扩展** | OpenClaw | 暂缓 | 国内四平台够用 |
| ⚪ P3 | **容器沙箱** | Hermes | 暂缓 | 企业版可考虑 |
| ⚪ P3 | **集群方案** | Hermes | 暂缓 | 单机定位足够 |

#### Harven 的差异化定位 🎯

```
OpenClaw = 个人AI + IM聚合器（强在连接）
Hermes  = 企业AI + 自学习引擎（强在执行）
Harven  = 行业AI + 离线闭环（强在深度）

Harven 不跟他们在同一个维度竞争：
- 不对标 OpenClaw 的 IM 数量 → 专注国内垂直场景
- 不对标 Hermes 的企业集群 → 专注单机极致体验
- 走通「本地模型 + 行业知识库 + IoT/Agent 连接层」的行业垂直模式
```

### 4.3 行业垂直 AI 模式


#### 核心逻辑

```
本地模型（大脑）
  + 行业知识库（专业领域知识，大模型不具备）
  + 连接层（Agent / Skill / 中间代码）
  + 外部系统（IoT 设备 / 客户管理软件 / 邮件 / 电话）
  = 行业 AI 解决方案
```

通用大模型不懂你的行业术语、不了解你的设备协议、不知道你的客户数据。Harven 把这一切补上：

| 层 | 作用 | 举例 |
|------|------|------|
| 🧠 **本地模型** | 推理、理解、生成 | 分析设备日志、阅读合同条款 |
| 📚 **行业知识库** | 专业术语、操作手册、历史案例 | 化工厂安全手册、律所判例库 |
| 🔌 **连接层** | 对接外部系统、执行动作 | 自动发邮件、调 CRM 接口、打电话 |
| ⚙️ **IoT / 设备** | 采集数据、接收指令 | 产线传感器、数控机床、环境监控 |

#### 典型场景

| 行业 | 知识库 | 连接层 | 效果 |
|------|--------|--------|------|
| 🏭 **制造业** | 设备手册、故障代码表 | MQTT → PLC 控制 | 产线异常时 AI 自动排查+通知 |
| 🏥 **医疗** | 药品说明书、诊疗指南 | 对接 HIS 系统 | 医生问"xx药禁忌"，AI 本地秒回 |
| ⚖️ **法律** | 法规库、判例库、合同模板 | 邮件自动发送 | 律师口述需求，AI 起草+发送合同 |
| 🏗️ **建筑** | 施工规范、材料参数 | 对接 BIM 软件 | 设计师问规范参数，AI 直接引用原文 |
| 🛒 **零售** | 商品库、客户偏好 | CRM + 短信/邮件 | AI 自动给流失客户发优惠券 |
| 🚛 **物流** | 路线手册、报关文件 | GPS + 订单系统 | AI 实时调度+异常预警 |

#### 为什么外脑不够，必须本地

```
🤖 外脑（DeepSeek/豆包等）→ 通用能力强，但：
  ❌ 不懂你的行业黑话
  ❌ 不知道你的设备协议
  ❌ 看不到你的客户数据

🏠 本地 Harven → 本地模型 + 知识库 = 懂你的AI
  ✅ 行业术语精准
  ✅ 设备协议正确
  ✅ 数据不出内网

🤝 协作模式 → 外脑的广度 + 本地的深度
  日常用本地处理敏感业务，疑难问题外脑辅助
```

> **愿景**：为每一个行业提供可私有部署的 AI 解决方案。不是做一个"万能的 AI"，而是做一万个"懂一个行业的 AI"。

### 4.4 开源策略

**Apache 2.0 协议**：任何人可以自由使用、修改、分发、商用。

| 策略 | 说明 |
|------|------|
| **源码完全开放** | GitHub 全量源码，无闭源模块 |
| **永久免费** | 不设付费墙，不搞会员制 |
| **社区驱动** | Agent/Skill/插件可由社区贡献 |
| **可私有部署** | 一键安装包，零配置启动 |
| **数据自主** | 所有数据存本地，无云端依赖 |

## 五、数据库设计

### 5.1 三库物理隔离

| 数据库 | 路径 | 用途 |
|--------|------|------|
| **对话库** | `data/sqlite/lingzhi.db` | 用户对话记录、消息、设置 |
| **知识库** | `data/knowledge/` + FAISS | 导入的文档、向量索引 |
| **配置库** | `config.yaml` + `data/config/` | 系统配置、插件配置 |

### 5.2 主数据库表结构

```sql
-- lingzhi.db（22 张表，精简选中核心表）

-- 用户画像（单行，自动更新）
user_profile (id, name, master_name, language, created_at, updated_at)

-- 对话表
conversations (id, title, specialization_id, model, category, is_archived, created_at, updated_at)

-- 消息表
messages (id, conversation_id, role, content, metadata, created_at)

-- 设置表（键值对）
user_preferences (key, value, updated_at)

-- 设备表（IoT 联动）
devices (device_id, name, device_type, protocol, address, driver_id, status, ...)
device_commands (id, device_id, command, params, success, message, timestamp)

-- 任务表
structured_tasks (id, conversation_id, title, description, status, priority, progress, ...)
scheduled_tasks (task_id, name, prompt, schedule_type, schedule_config, enabled, ...)

-- 工作流
workflows (workflow_id, name, description, steps_json, status, current_step_idx, ...)

-- 市场 & 社区
marketplace_items (id, name, description, author, item_type, download_count, rating_avg, ...)

-- 其余：active_memory / background_tasks / cognitive_locks / collaborations / device_permissions / lockdown_log / specializations / evolution
```

### 5.3 SQLite + FAISS 配置优化（🆕 v2026-06-24）

```python
# SQLite 性能
PRAGMA journal_mode=WAL        # 写操作快 2-5×
PRAGMA synchronous=NORMAL      # 减少磁盘 fsync
PRAGMA busy_timeout=5000       # 5秒锁等待，避免阻塞
PRAGMA foreign_keys=ON         # 强制外键约束

# FAISS 磁盘映射（SSD/HDD 自适应）
faiss.read_index(path, faiss.IO_FLAG_MMAP)  # 不占满内存，启动快 5×
```

**自动适配**：启动时检测硬件，所有用户均使用 FAISS mmap 策略以避免内存占满。

```python
# 解决多线程并发锁死（2026-06-12 修复）
conn = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
conn.execute("PRAGMA journal_mode=WAL")          # 写前日志，读写并发
conn.execute("PRAGMA foreign_keys=ON")            # 外键约束
conn.execute("PRAGMA busy_timeout=5000")          # 忙等待 5 秒
conn.execute("PRAGMA synchronous=NORMAL")         # WAL 正常同步
```

---

## 六、启动流程

### 6.1 首次启动

```
双击 一键安装启动.bat
  ├── 检查 Python 是否安装
  ├── 创建虚拟环境 .venv/
  ├── pip install -r requirements.txt
  ├── 从 config.yaml.example 生成 config.yaml
  ├── 检测 Ollama 服务（可选，不影响使用）
  └── python web_app.py 启动服务

浏览器 http://localhost:7000
  ├── 检测 first_run_done 标记
  ├── 未完成 → 跳转 /first_run
  │     ├── [有 GGUF/Ollama] Step 1: 同意协议 → 硬件检测 → 推荐模型 → 下载
  │     ├── [都未安装] 直接进入界面 → 三选一：
  │     │     ① 下载魔搭GGUF模型（内置 llama.cpp，纯本地）
  │     │     ② 填云 API Key（零等待即用，DeepSeek 5 分钟注册）
  │     │     ③ 装 Ollama 后重启（可选）
  └── 已完成 → 进入主界面
```

### 6.2 web_app.py 初始化顺序

```python
1. 生成 BUILD_ID（UUID，用于缓存破坏）
2. 初始化 Config 对象（config.yaml）
3. 创建 Flask app
4. 激活出站白名单（independence.py）
5. 注册 Blueprint 路由（models / skills / evolution / stats / fallback / extraction）
6. 注册设备 API / 新特性 API（register_xxx 方式）
7. 注册 IM / 手机联动路由
8. 注册 HarvenHub 市场路由
9. 启动 gen_scheduler 后台任务
10. app.run(debug=False, threaded=True, host='0.0.0.0', port=5000)
```

---

## 七、部署说明

### 7.1 环境要求

Harven AI 支持两种运行方式，CPU-only 即可起步。

#### ☁️ 云模型模式（推荐入门 — 无需任何硬件门槛）

| 组件 | 要求 |
|------|------|
| **CPU** | 任意（能开浏览器就行）|
| **内存** | 4 GB |
| **显卡** | 不需要 |
| **磁盘** | 500 MB（安装包 + .venv）|
| **Python** | 3.10+ |
| **Ollama** | ❌ 不需要 |

> 只需填一个 API Key（DeepSeek 注册 5 分钟，¥1/百万字），即可使用云端最强模型。
> 通义千问、智谱 GLM 有免费额度，完全零成本起步。

#### 最低门槛（CPU-only 本地模式）
| 组件 | 要求 |
|------|------|
| **CPU** | 4 核（任意架构） |
| **内存** | 8 GB |
| **显卡** | 不需要（纯 CPU 推理） |
| **磁盘** | 10 GB 可用 |
| **Python** | 3.10+ |
| **Ollama** | 可选（魔搭直连无需 Ollama） |

> 纯 CPU 可运行 qwen2.5:3b / 7b 量化版，速度约 3-5 tokens/s，适合轻度使用。

#### GPU 加速模式（模型越大要求越高）
| 模型规模 | 显存 | 内存 | 参考显卡 |
|----------|------|------|----------|
| 3B–7B | 0–6 GB | 8–12 GB | GTX 1660 / 无独显 |
| 8B–14B | 6–10 GB | 12–16 GB | RTX 3060+ |
| 32B | 12–24 GB | 32 GB+ | RTX 4080+ |
| 70B | 24–48 GB | 48 GB+ | RTX A6000 / 多卡 |

> **推荐机制**：启动时 `detect_hardware()` 自动获取当前设备的 CPU、内存、显存，
> 与内置模型数据库（38 个模型，每个标注 `min_ram_gb` / `min_vram_gb`）逐一比对，
> 分成三档——✅ 推荐（硬件满足）/ ⚠️ 勉强（接近边界）/ ❌ 不支持（硬件不足），
> 最终在前端展示当前设备能流畅运行的模型清单。
>
> 以上表格为通用参考，实际推荐以 Harven AI 硬件检测结果为准。

### 7.2 一键安装

详见 `HarvenAI_安装详解.md`。简版：

**Windows（推荐）**：
```
1. 解压到任意目录（无中文路径）
2. 双击 一键安装启动.bat → [1/4]Python→[2/4]venv→[3/4]pip→[4/4]config
3. 浏览器 http://localhost:7000 → 首次引导 3 步 → 完成
```

**Linux / macOS（Docker）**：
```
docker compose up -d
# → http://localhost:7000
```

> ⚠️ **Flask启动铁律（v7.0）**：禁止用 Git Bash 的 `&` 后台启动（会segfault），必须用 `启动HarvenAI.bat` 或 PowerShell `Start-Process`。桌面快捷方式走的是 bat 文件，不受影响。

### 7.3 配置文件

安装包只包含 `config.yaml.example`，首次启动自动生成 `config.yaml`。用户可按需修改：
- 默认模型（`default_model`，空字符串时触发硬件检测引导）
- 道德约束「诚实」原则（on/off）
- 联网搜索设置（web_search.*）
- 远程访问密码（security.remote_password，默认 harven2026，在设置→安全隐私查看和修改）

---

## 八、版本历史

| 日期 | 里程碑 |
|------|--------|
| 2026-05 | Phase 1-4：对话+界面+记忆+用户画像 |
| 2026-06-05 | Phase 5：手机联动 + 安全加固 P0-P2 |
| 2026-06-07 | 工程优化：JS 拆 9 模块、单元测试 67 个 |
| 2026-06-08 | 受控进化 + 统计 + 保底 + 萃取 + 品牌定制 |
| 2026-06-09 | 多模型 24 厂商 + IoT 万能驱动 + 设置面板重组 |
| 2026-06-09 | E→H 盘迁移合并完成 |
| 2026-06-12 | 框架底层重构 + 全面体检 + P0 锁死修复 |
| 2026-06-22 | 设置面板 HTML 嵌套 bug 修复（缺 </div> + 缺 class） |
| 2026-06-23 | 联网搜索重新启用（Bing 免费 + SearXNG） |
| 2026-06-24 | 模型目录系统 38 模型 + 双路径下载 + llama_engine 接入 |
| 2026-06-24 | 硬件检测 + 智能推荐引擎（6档VRAM适配）+ ModelScope 同步器 |
| 2026-06-24 | SQLite WAL + FAISS mmap 存储优化 |
| 2026-06-24 | 道德系统简化为「纯诚实」原则，取消不伤害教条和锁死机制 |
| 2026-06-24 | Agent 工坊上线：一句话创建 Agent，自动生成 system_prompt |
| 2026-06-25 | Docker 跨平台部署 + 保底机制前端对接 + GGUF 导入功能 + 全文档核查 |
| 2026-07-08 | HarvenHub 社区市场开发（服务端+客户端，端口5001） |
| 2026-07-14 | Skill系统升级为可执行脚本 + UI面板系统V1 |
| 2026-07-17 | CLI-Anything B+方案集成（Skill模式+设置面板推荐） |
| 2026-07-20 | 外部模型安装器零推荐 + NUCLEAR覆盖坑记录 |
| 2026-07-21 | 方案C联网控制 + 事实查询搜索增强 + format误杀修复 |
| 2026-07-22 | CLI-Anything根因修复（import sys）+ 代码过滤器拓宽 + FC结果yield |
| 2026-07-23 | HarvenHub安全修复6项 + Docker部署包创建 |
| 2026-07-24 | HarvenHub部署到腾讯云（harvenhub.com上线）+ 哈文云端客户端 + 用户端5页面 + 安装包重打包 |

---

## 九、优势总结

| 优势 | 说明 |
|------|------|
| **隐私安全** | 100% 本地运行，数据永远不出设备 |
| **永久免费** | 开源协议，无付费墙，无功能限制 |
| **离线可用** | 魔搭 GGUF 或 Ollama，断网也能对话 |
| **联网增强** | 需要时可自动搜索 Bing 或 SearXNG |
| **双路径模型** | 魔搭直连（零依赖）+ Ollama（进阶），用户自由选 |
| **智能推荐** | 硬件检测后根据用户需求双路径推荐（最佳+妥协） |
| **多模型生态** | 24 个厂商外脑 + 38 本地模型（LLM/音乐/图片/视频） |
| **知识库** | 导入个人文档，FAISS 向量检索（mmap 自适应优化） |
| **Agent 平台** | 13 个内置 Agent + 自注册，意图自动路由 |
| **受控进化** | AI 自我改进但必须经用户审批，安全可控 |
| **IoT 万能接口** | 无现成驱动时可 AI 临时生成，适配任何设备 |
| **模块化架构** | 80+ 核心模块 + 22 JS 模块，高内聚低耦合 |
| **一键安装** | 双击 bat（Windows）/ docker compose up（全平台），魔搭下载国内满速，带进度条 |

---

## 十、哈文干预边界规范（2026-09-26 定稿）

> 本章是哈文"什么该管、什么不该管、怎么管"的总守则。新功能开发前先过一遍三层判据，
> 避免再犯"哈文替模型做主"类错误（历史教训：GLM-5.3-Flash 被关键词表误判为不支持看图、
> 扫描版 PDF 解析失败诱发模型 5 轮全盘搜文件）。

### 10.1 总原则（一句话）

**模型自己能感知、能负责的事 → 模型干；模型感知不到、负不了责、或权利属于用户的事 → 哈文才干预。**

哈文=管家（安排路线、把门、翻译），模型=大脑（理解、回答）。

### 10.2 三层判据：哈文必管的三种情况

| 层 | 判据 | 实例 | 干预方式 |
|---|------|------|---------|
| **物理层** | 问题发生在图/文到达模型**之前**，模型根本不知道 | 大图被 Ollama 静默吞掉、文件解码、扫描版 PDF 抽不出字、上下文超长 | 技术处理（归一化/转码/截断），**但不下结论**——截断要标注"已截断" |
| **安全层** | 出错**不可逆**，模型担不起责 | 删文件、清回收站、发邮件、卸载、路径越界 | 硬拦截 + 确认门（FC 危险门） |
| **选择权层** | 事情属于**用户的权利** | 用哪个模型、图发本地还是云端、什么模式 | 只执行 + 透明标注（谁在看图/谁在回答），绝不替选 |

**反过来说：理解层（图能不能看、文里写了什么）、表达层（用什么语言回答）、规划层（要不要调工具、调哪个）——全是模型的事，哈文不预判、不代劳、不强制。**

### 10.3 六种干预方式（强度从高到低）

```
拦截（不许做）→ 确认门（做了要用户点头）→ 注入（喂信息不喂结论）
→ 转译（把技术报错说成人话）→ 标注（亮牌谁在干活）→ 引导（给路标不挡路）
```

### 10.4 场景对照表

| 场景 | 哈文管什么 | 怎么管 | 哈文不管什么 |
|------|-----------|--------|----------------|
| **电脑操作**（FC/快车道） | 安全：危险命令确认门、路径限制、三层防护 | 拦截+确认 | 任务怎么完成——模型自己规划调哪些工具（提示词只引导不强制） |
| **快车道预设词汇**（B 方案） | 确定性命令直通（换壁纸/查配置） | 哈文直接执行 | 词汇表之外的一律放行给模型，不做模糊匹配抢答 |
| **面板内容录入** | 格式校验、存库、分类归档（数据库的活，不是 AI 的活） | 哈文程序直接干 | 不让 AI 自由发挥写库（AI 只在用户明确说"帮我记…"时当转写员） |
| **录入后的提取/分析** | 把**原文**喂给模型 | 注入+标注来源 | 分析结论哈文不出——体检数据什么意思、账单说明什么，模型说 |
| **AI 教学/深度互动** | 注入学科上下文、孩子画像、进度记录；安全层兜底敏感内容 | 注入 | 教学方法、讲解方式、互动节奏全归模型 |
| **看图/文件** | 物理层（归一化/本地解析）+路由跟随模式+报错转译 | 技术+转译+标注 | 模型能不能看——外脑直发让模型自己说 |
| **语音/照片上传** | 物理层转写（Whisper/压缩） | 技术 | 转写结果直接给模型，不二次"理解" |

### 10.5 已固化的机制决策（2026-09-24~26 落地）

| 机制 | 决策 |
|------|------|
| **外脑看图** | 零门槛直发：删关键词预判表，图片/文件直接发当前外脑，能不能看由外脑自己说；报错转译人话+两条出路（`image_processor.translate_vision_error`） |
| **文件直发优先** | 外脑模式 + 单文件 pdf/docx/pptx/xlsx → base64 直发外脑原生读取（扫描版 PDF 也能读）；失败自动降级本地解析文本管线 |
| **本地看图** | 保留 Ollama 官方 `/api/show` capabilities 判断（运行时事实，防纯文本模型被传图后静默吞图）；关键词仅作查询失败兜底 |
| **文件解析失败话术** | 不再拼"读取失败"结论（会诱发模型全盘搜文件），改为"系统注"：事实+引导（切外脑重发/拍照看图）+明确"请勿搜索此文件" |
| **FC 提示词** | 文件场景禁令：消息含【文件:…】内容时直接分析，严禁全盘搜索（Downloads/Desktop/H盘等） |
| **FC 语言判定** | 扩检测范围到最近 6 轮对话历史，仍判不出默认中文（哈文是中文产品） |
| **大图归一化** | 相机原图 4MB+ Ollama 会静默丢图，看图前统一压缩到安全区（技术性修复，保留） |

### 10.6 用户不混乱的四条规则

1. **一个心智模型**：哈文=管家，模型=大脑。对外宣传照此口径，小白秒懂
2. **所有干预可见**：干预了就亮牌——来源标签、工具调用折叠卡、截断标注。用户永远知道"刚才哈文替我做了什么"
3. **干预永不堵死路**：拦下时必给两条以上出路（装 minicpm-v 或切外脑），不给"仅此一条"的命令式提示
4. **同一条通路**：手机/电脑、APP/网页规则完全一致

### 10.7 待对齐清单（同框架下的后续增强）

- **上下文截断透明化**：历史裁剪属物理层该做，长对话时轻提示"太久之前的内容已收起"
- **记忆注入查看入口**：个人设置里给"本次对话带入的记忆"查看入口（隐私透明）
- **AI 主动搭话开关**：主动权干预——AI 主动找用户必须有开关且默认克制（选择权层延伸）
- 方案 N 家庭提醒注入 ✓ 已克制；蒸馏三道门 ✓ 符合确认门原则，均不用动

---

*Harven AI — 你的私有本地 AI 助手*  
*永久免费，不做付费会员*
