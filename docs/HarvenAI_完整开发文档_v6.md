# Harven AI 完整开发文档 v7.0

> **版本**: 2026-06-27 v7.0 | 协作体系 v2.0 + 双模配置面板 | **作者**: 老谭 & Harven AI  
> **项目定位**: 纯本地私有 AI 助手，永久免费，同 OpenClaw 适用于所有个人用户  
> **代码行数**: ~70,000+ 行（Python + JS + HTML + CSS） | **模块数**: 106+ 核心模块  
> **文档状态**: ✅ 新增「🏠 双模配置」面板文档 + 安装包重建

---

## 目录

1. [项目概述](#1-项目概述)
2. [核心架构设计](#2-核心架构设计)
   - [2.1.1 后端架构（80+ 模块）](#211-后端架构80-核心模块v60-验证通过)
   - [2.1.2 三层 AI 分工架构](#212-三层-ai-分工架构)
3. [三种查询模式详解](#3-三种查询模式详解)
4. [协作模式（自动模式子模式）](#4-协作模式自动模式子模式)
   - [4.8 内存警告](#48-内存警告)
   - [4.9 🏠 双模配置面板](#49-双模配置面板)
5. [安全防御体系](#5-安全防御体系)
6. [功能模块详解（60+ 核心模块）](#6-功能模块详解)
7. [数据库设计全览](#7-数据库设计全览)
8. [API 路由全览（260+ 端点）](#8-api-路由全览)
9. [前端架构详解](#9-前端架构详解)
10. [数据流与逻辑关系](#10-数据流与逻辑关系)
11. [启动流程与运维](#11-启动流程与运维)
12. [配置参数全览](#12-配置参数全览)
13. [法律合规与侵权风险分析](#13-法律合规与侵权风险分析)
14. [开发规范与已知坑](#14-开发规范与已知坑)
15. [技术债务与未来路线](#15-技术债务与未来路线)
16. [附录](#16-附录)

---

## 1. 项目概述

### 1.1 产品定位

| 项目 | 内容 |
|------|------|
| **产品名** | Harven AI（曾用名：灵识 → 本诚 → Harven IA） |
| **Slogan** | 你的专属私有 AI，永远站在你这一边 |
| **定位** | 纯本地运行的私有 AI 助手，适用于所有个人用户 |
| **商业模式** | **永久免费**，不做付费会员，不收集用户数据 |
| **适用硬件** | 通用个人电脑（自动硬件自检 → 推荐合适模型） |
| **首发日期** | 2026-06 项目定型 |

### 1.2 首次启动流程

```
用户首次安装 Harven AI
    │
    ▼
启动 → 检测 first_run.json 是否存在
    │
    ├─ 不存在 → 强制跳转 /first_run
    │       │
    │       ▼
    │  硬件自检（hardware_detector.py）
    │       CPU / RAM / GPU-VRAM 检测
    │       │
    │       ▼
    │  模型推荐（model_recommend.py）
    │       38 个模型数据库匹配
    │       │
    │       ▼
    │  引导用户下载合适的 Ollama 模型
    │       │
    │       ▼
    │  设置管理员密码（默认 harven2026，首次强制改密）
    │       │
    │       ▼
    │  写入 first_run_done 标记
    │
    └─ 已存在 → 正常进入主界面
```

### 1.3 技术栈

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| **Web 框架** | Flask | 3.1.3 | 主服务框架 |
| **AI 引擎** | 本地通用模型（Ollama） | 最新 | 本地 LLM 运行环境 |
| **多模型 SDK** | openai (兼容) | 2.41.0 | 连接外部模型 |
| **向量检索** | FAISS | 1.14.2 | 本地知识库向量索引 |
| **数据库** | SQLite 3 | 内置 | 主库 + 三库隔离 |
| **前端框架** | Bootstrap 5 | 最新 | 响应式 UI |
| **前端交互** | 原生 JS | ES6+ | 22 个模块化 JS 文件 |
| **桌面 GUI** | PySide6 | 6.11.1 | 可选桌面版本（LGPL 许可） |
| **浏览器自动化** | Playwright | 1.58.0 | 网页内容获取（可选） |
| **定时任务** | APScheduler | 3.11.1 | 后台定时任务调度 |
| **语音识别** | faster-whisper | 1.2.1 | 本地音频转文字 |
| **PDF 解析** | pypdf | 6.13.0 | PDF 文字提取 |

### 1.4 项目目录结构（v6.0 验证通过，72 文件）

```
H:\HarvenAI├── web_app.py                    ← 主 Flask 入口（~5000 行，260+ 端点）
├── device_api.py                 ← IoT 设备管理 API
├── model_routes.py               ← 模型路由 Blueprint
├── multi_model_api.py            ← 多模型 API
├── new_features_api.py           ← 新功能 API
├── plugin_system.py              ← 插件管理系统
├── main.py                       ← PySide6 桌面 GUI 入口
├── config.yaml                    ← 运行时配置
├── requirements.txt              ← Python 依赖
├── 一键安装启动.bat               ← Win 一键安装脚本
├── 启动HarvenAI.bat               ← 快速启动
│
├── src/core/           ← 80+ 核心模块（详见 §2.1.1）
├── src/models/         ← 数据库模型层
├── src/utils/          ← 工具类（配置/日志等）
│
├── templates/          ← Jinja2 模板（30 个 include 片段）
├── static/             ← 前端资源
│   ├── css/style.css
│   ├── js/modules/     ← 22 个 JS 模块
│   └── icons/          ← PWA 图标（16~1024px）
│
├── data/               ← 运行时数据
│   ├── vertical_params.json      ← 垂直模型参数目录（🆕 v6.0）
│   ├── model_catalog.json        ← 40+ 模型元数据库
│   ├── user_vertical_prefs.json  ← 用户垂直偏好记忆（🆕 v6.0）
│   ├── sqlite/lingzhi.db         ← 主数据库
│   ├── faiss/                    ← FAISS 向量索引
│   └── knowledge/                ← 知识库文件
│
├── docs/               ← 文档（含本文）
├── skills/             ← 用户 Skills
├── plugins/            ← 插件目录
└── locales/             ← 多语言文件
```

### 1.5 技术栈总览

```
┌─────────────────────────────────────────────────────┐
│                   前端层                             │
│  Bootstrap 5 + 原生 JS (22模块) + PWA + SSE 流      │
├─────────────────────────────────────────────────────┤
│                   API 层                             │
│  Flask (Blueprint) + RESTful + SSE 流式响应          │
├──────────┬──────────┬──────────┬────────────────────┤
│  AI 引擎  │  知识系统  │  安全系统  │   扩展系统         │
│ engine   │ knowledge │ morality  │ skills            │
│ multi_   │ _base     │ safety    │ plugins           │
│ model    │ FAISS     │ sandbox   │ evolution         │
│ web_     │ memory    │ rate_limit│ devices           │
│ search   │           │           │ Agent 系统        │
├──────────┴──────────┴──────────┴────────────────────┤
│                   数据层                             │
│  SQLite (WAL) + FAISS (向量) + JSON (配置/状态)      │
├─────────────────────────────────────────────────────┤
│                  运行环境                             │
│  Python 3.10+ · 云模型(即开即用) / 本地通用模型(多运行时)  │
│  Windows/Linux/macOS · 首次启动自动检测硬件推荐模型     │
└─────────────────────────────────────────────────────┘
```

## 2. 核心架构设计

### 2.1 分层架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        用户界面层                              │
│  index.html (Bootstrap 5) + 22个 JS 模块 + CSS          │
│  PWA 支持 (service-worker.js) + 桌面 PySide6 GUI         │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP / SSE / WebSocket
┌──────────────────────▼──────────────────────────────────────┐
│                      API 路由层                              │
│  web_app.py 主路由 + 11个 Blueprint + 3个 add_url_rule  │
│  260+ 个端点，覆盖对话/知识/任务/设备/统计/...            │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                    业务逻辑层                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Conversation │  │ Knowledge     │  │ Memory        │  │
│  │ Engine       │  │ Base (FAISS) │  │ Manager       │  │
│  │ (engine.py)  │  │               │  │               │  │
│  └─────────────┘  └──────────────┘  └──────────────┘  │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Morality     │  │ Independence  │  │ Collab        │  │
│  │ Kernel       │  │ Defense      │  │ Manager       │  │
│  └─────────────┘  └──────────────┘  └──────────────┘  │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Multi-Model  │  │ Skills        │  │ Plugin        │  │
│  │ Manager      │  │ Manager       │  │ Sandbox       │  │
│  └─────────────┘  └──────────────┘  └──────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                     模型层                                   │
│  本地通用模型（Ollama） (本地) ← → MultiModelManager ← → 外部 API        │
│  qwen2.5:7b/14b/32b + 28个厂商支持                     │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                     数据层                                    │
│  SQLite (主库 + 三库隔离) + FAISS + 文件系统             │
│  + Redis (可选) + MQTT Broker (IoT)                       │
└─────────────────────────────────────────────────────────────┘
```

### 2.1.1 后端架构（80+ 核心模块，v6.0 验证通过）

```
src/core/
├── 引擎层 (5)
│   engine.py              → AI 对话核心引擎（三段式查询）
│   model_manager.py       → 统一模型管理（多运行时自动切换）
│   multi_model_manager.py → 28 厂商多模型接入
│   llama_engine.py        → llama-cpp-python 本地推理
│   model_catalog.py       → 模型目录加载器（40+ 模型推荐）
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
│   web_search.py          → Bing + SearXNG 联网搜索
│
├── 安全系统 (7)
│   morality.py            → 诚实原则（身份+能力描述+不说谎）
│   hw_security.py         → 硬件安全（设备指纹/绑定/防克隆）
│   code_sandbox.py        → 代码执行沙箱
│   rate_limiter.py        → 速率限制
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
│   device_binding.py      → 设备绑定
│   device_starter.py      → 设备启动引导
│   device_drivers.py      → IoT 驱动框架
│   device_api.py          → IoT 设备 API（根目录）
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
├── 工具与基建 (17)
│   event_bus.py           → 事件总线
│   scheduler.py           → 定时任务调度
│   planner.py             → 任务规划器
│   task_manager.py        → 任务管理器
│   workflow.py            → 工作流引擎
│   monitor.py             → 系统监控
│   hardware_detector.py   → 硬件检测 + 模型推荐 + 内存估算
│   stats_manager.py       → 统计管理器
│   fallback_manager.py    → 保底机制
│   extraction_manager.py  → 知识萃取
│   harvenhub_api.py       → HarvenHub 市场 API
│   ollama_daemon.py       → 运行时守护进程
│   gen_scheduler.py       → 通用调度器
│   stats_routes.py        → 统计 API
│   fallback_routes.py     → 保底 API
│   extraction_routes.py   → 萃取 API
│   harvenhub_market.py    → HarvenHub 市场
│
├── 用户系统 (3)
│   user_profile.py        → 用户画像
│   visitor_manager.py     → 访客管理
│   profile_auto_updater.py → 画像自动更新
│
├── 模型管理 (3)
│   model_recommend.py     → 模型推荐引擎
│   model_downloader.py    → 魔搭 GGUF 模型下载器
│   model_syncer.py        → ModelScope 自动同步
│
├── Agent 系统 (4)
│   agent_registry.py      → Agent 注册中心（意图路由）
│   agent_runner.py        → Agent 执行引擎
│   agent_routes.py        → Agent API 接口
│   multi_agent.py         → 多智能体协作
│
├── 协作体系 (3) 🆕 v6.0
│   collab_manager.py      → 协作模式管理器 v2.0
│   collab_routes.py       → 协作 API + 内存检查
│   vertical_router.py     → 垂直模型路由（意图→参数→确认卡）
│
├── 长任务 (1) 🆕 v6.0
│   task_sharder.py        → 长任务分片 + 断点续跑
│
└── 其他 (11)
    learner.py              → 持续学习引擎
    image_gen.py            → 图片生成
    image_processor.py      → 图片处理
    tool_executor.py        → 代码块自动执行
    model_installer.py      → 模型安装器
    channel_manager.py      → 渠道管理
    resource_hub.py         → 资源中心
    background_task_manager.py → 后台任务
    specialization_executor.py → 专项培养
    task_sharder.py         → 长任务分片（🆕 v6.0）
    vertical_router.py      → 垂直模型路由（🆕 v6.0）
```

> **验证结果**：72 个核心文件全部存在（已逐文件确认）。仅 `device_api.py` 位于根目录而非 `src/core/`。

### 2.1.2 三层 AI 分工架构

哈文的 AI 能力分为三层，各司其职：

```
┌──────────────────────────────────────────────────────┐
│  Layer 1：大语言模型（大脑）                            │
│  外脑或本地通用模型                                     │
│  职责：理解意图、规划步骤、生成文本                       │
│  ❌ 不执行具体操作                                     │
├──────────────────────────────────────────────────────┤
│  Layer 2：哈文调度层（管家）                             │
│  职责：判断用什么模式、调用哪个模型                       │
│  - 意图识别 → 模型路由（collab_manager / agent_registry）│
│  - 非语言模型拦截（model_manager.chat() 入口检测）        │
│  - 参数卡片生成（vertical_router.extract_params）       │
│  - Skills 注入 / 知识库检索 / 结果整合                   │
├──────────────────────────────────────────────────────┤
│  Layer 3：垂直模型 + Tools（执行者）                     │
│  职责：执行具体操作                                     │
│  - MusicGen → 生成音乐                                 │
│  - Bark → 生成语音                                     │
│  - Stable Diffusion → 生成图片                          │
│  - system_ops Agent → 系统操作（11项 winget/pip/netsh）  │
│  - 知识库 → 检索                                       │
│  - Skills → 文件操作/代码执行/联网搜索                    │
└──────────────────────────────────────────────────────┘
```

**协作模式下的大脑切换**：

| 模式 | 大脑 | 手 | 哈文调度 |
|------|------|-----|---------|
| 🚫 关闭协作 | 第一层引擎 | 第一层引擎 | 不参与 |
| ☁️ 云地协作 | 外脑（DeepSeek等） | 本地垂直模型 | Skills/知识库/Agent/参数/路由 |
| 🏠 本地双模 | 本地通用模型 | 本地垂直模型 | Skills/知识库/Agent/参数/路由 |

> **关键原则**：哈文调度层在协作模式中干的事完全一样，唯一区别是大脑是谁。云地协作中本地通用模型参与最终答案整合——外脑分析嵌入增强上下文，本地通用模型基于外脑分析生成最终回答。

### 2.2 请求处理完整流水线

```
用户发送消息
    │
    ▼
[前端] app-core.js: sendMessage()
    │
    ▼
[前端] app-conversation.js: _sendMessageNow()
    │ POST /api/chat
    │ Body: { message, conversation_id, query_mode, collab_mode }
    │
    ▼
[后端] web_app.py: chat() 路由
    │
    ├─ 1. 检查数据保险箱锁定状态
    ├─ 2. 检查访客访问权限
    ├─ 3. 创建 ConversationEngine 实例
    │
    ▼
[引擎] ConversationEngine.send()
    │
    ├─ 4. 锁机检查 (is_lockdown)
    ├─ 5. 插件匹配 (plugin matching)
    ├─ 6. 图片/视频生成意图检测
    ├─ 7. Prompt Injection 防御
    ├─ 8. 事件触发 (MESSAGE_RECEIVED)
    │
    ▼
[道德内核] analyze_intent()
    │
    ├─ 关键词粗筛 (90+ 高危关键词)
    ├─ LLM 意图复核 (15秒超时)
    ├─ 分级处理: safe / warn / guide / discuss / lockdown
    │
    ▼
[引擎] _stream_three_stage() — 核心三段式查询
    │
    ├─ Step 0: GitHub URL 检测 + 自动下载
    ├─ Step 0b: 浏览器自动化检测 (auto模式)
    ├─ Step 0c: Active Memory 上下文注入
    │
    ├─ Step 1: 知识库搜索 (FAISS top_k=3, threshold=0.2)
    │
    ├─ 协作模式检查（优先于路由）:
    │   ├─ auto + collab_mode = cloud_local / local_dual → 协作模式
    │   │   └─ 获取分析后 → 强制 本地通用模型（Ollama） 整合（跳过 Step 3 路由，禁止外部模型介入最终回答）
    │   ├─ 外脑无响应 → 回退通知 + 本地通用模型（Ollama） 独立回答
    │   └─ collab_mode = off → 继续执行 Step 3 路由
    │
    ├─ Step 3: 智能模型路由（协作模式下被跳过）
    │   ├─ auto模式（仅普通子模式）: 优先外部模型，未配置则回落 本地通用模型（Ollama）
    │   ├─ kb_only模式: 强制 本地通用模型（Ollama）
    │   └─ direct模式: 外部模型
    │
    ├─ 构建系统提示词（所有模式统一注入）
    │   └─ 诚实原则 + 记忆 + 用户画像 + 习惯 + 任务 + Skills + 知识库片段
    │
    ├─ Skills JSON 工具描述注入
    │
    ▼
[模型调用] generate_response()
    │
    ├─ 外部模型: MultiModelManager.generate_response()
    ├─ 本地 本地通用模型（Ollama）: ModelManager.generate()
    │
    ▼
[SSE 流式响应] 逐 chunk 返回前端
    │
    ├─ type: "meta"    → 会话元数据
    ├─ type: "text"    → 回答内容
    ├─ type: "thinking" → 思考状态
    ├─ type: "source"   → 知识来源
    ├─ type: "warning"  → 警告信息
    ├─ type: "notification" → 后端通知
    ├─ type: "suggestion" → 技能推荐
    └─ "data: [DONE]" → 流结束
    │
    ▼
[后处理] 流结束后
    │
    ├─ 保存对话到数据库
    ├─ 触发事件 (AI_RESPONSE_READY)
    ├─ 记忆提取 (extract_task_memory)
    ├─ 用户画像分析 (profile_auto_updater)
    ├─ 任务提取 (task extraction)
    ├─ Active Memory 更新
    └─ 后台任务触发 (background_task_manager)
```

### 2.3 关键类与方法签名

#### `ConversationEngine` (engine.py)

| 方法签名 | 行号 | 说明 |
|----------|------|------|
| `__init__(model_manager, conversation_id, specialization_prompt, query_mode, privacy_mode)` | 78 | 初始化引擎，加载偏好/历史 |
| `send(user_message, stream, query_mode)` | 156 | **主入口** — 消息处理全流程 |
| `_classify_query(message)` | 313 | 关键词 + LLM 双轨分类 |
| `_route_model_for_query(query_mode)` | 558 | 智能模型路由 |
| `_invoke_collab_foreign(collab_manager, user_message)` | 619 | 协作外脑调用 |
| `_stream_three_stage(user_message, morality)` | 734 | **核心** — 三段式流式查询 |
| `_sync_three_stage(user_message, morality)` | 1229 | 同步版本（非流式） |
| `_auto_save_knowledge(question, answer)` | 1241 | 自动保存问答到知识库 |
| `send_with_images(user_message, image_paths, stream)` | 1254 | 图片消息 |
| `send_with_video(user_message, video_path, stream)` | 1295 | 视频消息 |
| `generate_sync(prompt, max_tokens)` | 1377 | 同步生成（跳过道德检查） |

#### `analyze_intent` (morality.py)

```
签名: analyze_intent(user_message: str) -> dict

返回格式:
{
  "level": "safe" | "warn" | "guide" | "discuss" | "lockdown",
  "block": True | False,
  "reason": "..."  # 触发原因（可选）
}

处理流程（v3.0 简化）:
analyze_intent() → 直接返回 safe（不审核）
> 旧版多级审核代码保留但入口已绕过，见 §5.3。
```

---

## 3. 查询模式 + 协作体系（v6.0）

### 3.1 前端两按钮

输入框上方只有两个模式按钮：

```
┌──────────────────────────────────────┐
│ 分析  [🏠本地] │ 直答  [🤖外脑] │ 协作 [🚫关闭 ▼] │
└──────────────────────────────────────┘
```

| 按钮 | 模式键 | 回答引擎 | 数据外传 | 适用 |
|------|--------|---------|---------|------|
| 🏠 本地 | `kb_only` | 本地通用模型（对话）/ 垂直模型（生成） | ❌ 绝不外传 | 离线/隐私/垂直生成 |
| 🤖 外脑 | `direct` | 外部大模型 | ⚠️ 问题传外脑 | 需要强推理 |

> 引擎内部保留 `auto` 作为默认值，但前端不暴露。协作是独立的下拉菜单，不属于查询模式按钮。

### 3.2 本地模式（kb_only）

```
用户提问
  ↓
意图检测（vertical_router）
  ├─ 普通对话 → 本地通用模型回答
  ├─ 垂直意图（音乐/图片/语音）→ 本地垂直模型执行 → 结果返回
  └─ 如果当前模型是垂直模型但发了普通对话 → 自动切通用模型
全程离线，数据绝不出本机。
```

**模型选择逻辑**：

| 场景 | 行为 |
|------|------|
| 当前模型 = 通用模型 + 普通对话 | 通用模型直接回答 |
| 当前模型 = 通用模型 + 垂直意图 | 路由到垂直模型执行，通用模型汇总 |
| 当前模型 = 垂直模型 + 垂直意图 | 垂直模型直接执行 |
| 当前模型 = 垂直模型 + 普通对话 | 自动切到 `default_model`（通用模型）回答 |

> 本地模式下有多个本地模型时，哈文自动分流：通用模型管对话，垂直模型管生成。不需要用户手动切换。

#### 3.2.1 智能状态指示器

输入框下方状态栏根据已安装模型组合自动显示：

| 模型情况 | 状态显示 | 详情 |
|---------|---------|------|
| 有通用 + 有垂直 | `🟢 本地通用 · qwen2.5:14b` | `+ 2 个垂直模型可用` |
| 只有通用 | `🟢 本地通用 · qwen2.5:14b` | `本地运行时正常` |
| 只有垂直 | `⚠️ 缺少通用模型，无法对话` | `只能生成音乐/图片/语音` |
| 都没有 | `🟡 已连接，暂无模型` | `请下载模型或切 🤖外脑` |
| 运行时未启动 | `🔴 未连接` | `请启动本地运行时` |

实现：`checkOllamaStatus()` 调用 `/api/ollama/models` 获取列表，根据 `_NON_CHAT_KW` 关键词分类通用/垂直模型。

### 3.3 外脑模式（direct）

```
用户提问 → 外部大模型直接回答
强大推理能力，但问题内容发送到外部 API。
```

### 3.4 协作体系（独立于查询模式）

协作不绑定任何查询模式，是独立的下拉菜单：

```
🚫 关闭协作  → 由第一层引擎独立工作
☁️ 云地协作  → 外脑当大脑 + 垂直模型当工具
🏠 本地双模  → 本地通用模型当大脑 + 垂直模型当工具
```

详见 [§4 协作体系 v2.0](#4-协作体系-v202026-06-26-重构)。

### 3.5 提示词注入差异

所有模式的哈文调度层注入内容**完全一致**，只在大脑人选上有区别：

| 模式 | System Prompt 内容 |
|------|-------------------|
| 🏠 本地 | 诚实原则 + 用户画像 + 习惯 + 知识库片段 + Skills |
| 🤖 外脑 | 诚实原则 + 用户画像 + 习惯 + 知识库片段 + Skills |
| ☁️ 云地协作 | 诚实原则 + 用户画像 + 习惯 + 知识库片段 + Skills |
| 🏠 本地双模 | 诚实原则 + 用户画像 + 习惯 + 知识库片段 + Skills |

| 注入组件 | 🏠本地 | 🤖外脑 | ☁️云地 | 🏠双模 |
|---------|-------|--------|--------|--------|
| 诚实原则 | ✅ | ✅ | ✅ | ✅ |
| 用户画像 | ✅ | ✅ | ✅ | ✅ |
| 记忆上下文 | ✅ | ✅ | ✅ | ✅ |
| Skills 工具描述 | ✅ | ✅ | ✅ | ✅ |
| 知识库片段 | ✅ | ✅ | ✅ | ✅ |

> **设计原则**：哈文调度层（Skills/知识库/Agent/参数/路由/提示词注入）对所有模式完全一致。四种模式只在大脑人选上有区别——谁负责最终回答。
>
> morality.py v3.0（2026-06-24）简化为纯诚实原则：不做道德审判、不拒绝回答、不替用户判断。仅要求模型诚实说明自身能力和限制。

---

## 4. 协作体系 v2.0（2026-06-26 重构）

### 4.1 两模式架构

协作不再只是一个开关，而是两种独立模式：

```
┌─────────────────────────────────────────────────────┐
│              Harven AI 协作体系                       │
│                                                     │
│   🚫 关闭协作    ☁️ 云地协作        🏠 本地双模        │
│   独立工作       外脑当大脑          本地通用模型当大脑  │
│                 + 垂直模型当工具     + 垂直模型当工具   │
│                                                     │
│   哈文调度层（Skills/知识库/Agent/参数/路由）完全一样   │
│   唯一区别：大脑是谁？                                │
└─────────────────────────────────────────────────────┘
```

| | ☁️ 云地协作 | 🏠 本地双模 |
|---|---|---|
| **大脑** | 外脑（DeepSeek/豆包等） | 本地通用模型（Qwen等） |
| **执行者** | 本地垂直模型（MusicGen/Bark/SD） | 本地垂直模型 |
| **输出** | 外脑 | 本地通用模型 |
| **联网** | 外脑需要联网 | 不需要 |
| **哈文调度** | Skills/知识库/Agent/参数/路由 | Skills/知识库/Agent/参数/路由 |
| **外脑** | ✅ 必需 | ❌ 不参与 |
| **本地通用模型** | ✅ 参与最终整合 | ✅ 必需（调度 + 整合） |
| **联网搜索** | 🌐 可选 | 🌐 可选 |
| **配方** | 无（流程唯一） | 无（流程唯一） |

### 4.2 配置

```yaml
# config.yaml（v2.0）
collab_mode: "off"            # off | cloud_local | local_dual
collab_foreign_model: deepseek  # 云地协作时的外脑选择
collab_privacy_level: full      # strict | balanced | full

# 兼容旧配置：collab_enabled: true → 自动转为 cloud_local
```

API:
- `GET /api/collab/status` — 返回 {mode, enabled, is_cloud_local, is_local_dual, available_modes, ...}
- `POST /api/collab/config` — 更新模式与隐私级别
- `GET /api/collab/memory-check` — 多模型并行内存估算

### 4.3 云地协作工作流

```
用户发指令
  ↓
哈文检测意图（vertical_router / agent_registry）
  ↓
☁️ 云地协作：
  ├─ 外脑分析意图 → 给出建议/参数
  ├─ 哈文调度：Skills注入 + 知识库检索 + 参数补全
  ├─ 垂直模型执行（MusicGen/Bark/SD）
  └─ 外脑输出最终结果
```

> **关键原则**：云地协作中外脑分析嵌入上下文，本地通用模型做最终回答整合。

### 4.4 本地双模工作流

```
用户发指令
  ↓
🏠 本地双模：
  ├─ 本地通用模型分析意图 → 调度垂直模型
  ├─ 哈文调度：Skills注入 + 知识库检索 + 参数补全
  ├─ 垂直模型执行
  └─ 本地通用模型汇总输出
```

> **全程离线，无需外脑。**

### 4.5 三大协作规则

**规则 1：简单命令不协作**
触发词：打开文件/播放音乐/列出目录/启动程序 → 提示用户关协作用第一层引擎。

**规则 2：专业模型诚实警告**
检测到 embedding/vision/code-only/music 等专业模型时警告，建议切外脑做语言操作。

**规则 3：诚实原则**
外脑不说"我是本地"，本地不说"我能联网"，双方都知道自己角色。

### 4.6 前端交互

下拉菜单位于输入框上方，**向上展开**（避免溢出屏幕）：

```
┌──────────────────────┐
│ ● 🚫 关闭协作         │
│ ○ ☁️ 云地协作         │ ← 外脑当大脑 + 垂直当工具
│ ○ 🏠 本地双模         │ ← 通用模型 + 垂直联动
│ ─────────            │
│ 📖 什么是协作模式？    │
└──────────────────────┘
        ▲
┌───────┴──────┐
│ 🚫 关闭 ▼   │
└──────────────┘
```

### 4.7 隐私保护

| 级别 | 键值 | 过滤规则 | 适用场景 |
|------|------|----------|----------|
| 严格 | `strict` | 仅传问题摘要 | 完全不信任的外脑 |
| 平衡 | `balanced` | 去敏后的问题 | 默认推荐 |
| 全量 | `full` | 完整问题+3轮上下文 | 完全信任的外脑 |

### 4.8 内存警告

协作模式切换时自动检查内存是否支持多模型并行。切换至云地/本地双模后，调用 `/api/collab/memory-check` 估算当前模型组合的内存占用，不足时 toast 警告。

实现：`hardware_detector.py` → `get_memory_warnings()` → `_MODEL_MEMORY_ESTIMATES` 表（30+模型的内存预估）。

---

### 4.9 🏠 双模配置面板（🆕 v7.0）

新增独立配置面板，允许用户为「🏠 本地双模」模式精细配置大脑模型和工具模型。

**面板位置**：设置 → 模型管理 → **🏠 双模配置**（子 Tab）

**配置项**：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| 🧠 大脑模型 | 本地双模中负责对话/推理的通用模型（下拉选择） | 自动使用当前模型 |
| 🎨 工具模型 | 勾选的垂直模型将自动被哈文调度执行专项任务（多选） | 无 |

**技术实现**：

```
前端: tab-本地双模配置.html
  ├── loadDualModelConfig()     → GET /api/collab/dual-config 读取已存配置
  ├── saveDualBrain()           → POST /api/collab/dual-config {brain}
  ├── saveDualTools()           → POST /api/collab/dual-config {tools}
  └── 模型分类逻辑             → 通用模型 = 非垂直模型（排除 musicgen/bark/SD 等）

后端: collab_routes.py
  ├── GET  /api/collab/dual-config   → 返回 {brain, tools}
  ├── POST /api/collab/dual-config   → 保存到 config.yaml (local_dual_brain / local_dual_tools)
  └── Config.save() 持久化            → 复用全局 Config 单例

协作引擎: engine.py → ConversationEngine
  └── 读取 local_dual_brain 配置 → 覆盖默认 Ollama 模型作为大脑
      仅当 collab_mode = 'local_dual' 时生效
```

**配置键**（config.yaml）：

```yaml
# 本地双模配置
local_dual_brain: ""              # 大脑模型名，空=自动使用当前模型
local_dual_tools: []              # 工具模型名列表
```

**API 路由**：

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/collab/dual-config` | 读取双模配置 |
| POST | `/api/collab/dual-config` | 保存双模配置 `{brain, tools}` |

**大脑模型过滤规则**：

```
垂直模型关键词（被排除）：
  musicgen / bark / riffusion / audiocraft / suno
  stable-diffusion / sdxl / sd3 / flux / kolors / lcm
  cogvideo / animatediff / opensora
  minicpm / qwen2.5-vl / internvl / llava
  bge / nomic / gte / text-embedding
```

> **逻辑**：通用模型 = Ollama 已安装模型 - 垂直专用模型。大脑下拉只显示通用模型，工具勾选只显示垂直模型。

---


---

## 5. 安全防御体系

### 5.1 六层防御架构

```
┌─────────────────────────────────────────────────────────────┐
│  L6: System Prompt 完整性校验                            │
│  - 启动时计算哈希，运行时定期校验                             │
│  - 防止 Prompt 被篡改                                     │
├─────────────────────────────────────────────────────────────┤
│  L5: 设备绑定 / 防克隆                                   │
│  - 硬件指纹 (CPU/主板/MAC/硬盘序列号)                     │
│  - 绑定状态日志（不强制锁死）                              │
│  - 克隆检测警告                                           │
├─────────────────────────────────────────────────────────────┤
│  L4: 知识库注入防御                                       │
│  - 启发式检测 (30+ 可疑关键词)                            │
│  - LLM 审查（本地 本地通用模型（Ollama），不依赖外部）                    │
│  - 隔离导入（先审查后入库）                               │
├─────────────────────────────────────────────────────────────┤
│  L3: 外部请求来源验证                                     │
│  - Token 验证（移动端/API）                               │
│  - IP 白名单（可配置）                                   │
│  - 访客访问码                                             │
├─────────────────────────────────────────────────────────────┤
│  L2: gRPC/分布式协议拦截                                  │
│  - 检测 本地通用模型（Ollama） 集群配置                                   │
│  - 防止模型被远程调用                                      │
├─────────────────────────────────────────────────────────────┤
│  L1: 网络出站白名单                                       │
│  - 全局 requests 补丁                                     │
│  - 仅允许白名单域名出站                                    │
│  - 私有 IP 自动放行（localhost/局域网）                    │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 独立性防御详细实现 (independence.py)

#### L1: 网络出站白名单

```python
# 启动时应用全局补丁
_apply_outbound_patch()

# 补丁原理: 替换 requests.Session.request 方法
# 所有 requests.get/post/put/delete 调用都会被拦截

def _patched_session_request(self, method, url, **kwargs):
    parsed = urlparse(url)
    if not is_domain_allowed(parsed.hostname):
        raise OutboundBlockedError(f"域名 {parsed.hostname} 不在白名单中")
    return original_request(self, method, url, **kwargs)
```

**白名单域名**（config.yaml `independence.allowed_domains`，完整列表 32 个域名 + 本机/局域网自动放行）：

| 用途 | 域名 |
|------|------|
| **本机 & 局域网** | localhost / 127.0.0.1 / 192.168.x / 10.x（自动放行） |
| **模型下载** | ollama.ai / huggingface.co / hf-mirror.com / modelscope.cn |
| **模型 API** | api.openai.com / api.anthropic.com / api.deepseek.com / api.moonshot.cn / api.groq.com / api.mistral.ai / api.cohere.ai / api.siliconflow.cn / openrouter.ai / api.x.ai / api.perplexity.ai / api.together.xyz / api-inference.huggingface.co |
| **Google AI** | generativelanguage.googleapis.com |
| **国内厂商** | ark.cn-beijing.volces.com（豆包）/ api.hunyuan.cloud.tencent.com（元宝）/ api.baichuan-ai.com（百川）/ open.bigmodel.cn（智谱）/ api.lingyiwanwu.com（零一万物）/ api.stepfun.com（阶跃星辰）/ api.minimaxi.chat（MiniMax）/ qianfan.baidubce.com（文心）/ spark-api-open.xf-yun.com（讯飞）/ sky-api.singularity-ai.com（天工）/ dashscope.aliyuncs.com（通义千问） |
| **搜索 & 技能** | duckduckgo.com / bing.com / baidu.com / google.com / exa.ai / github.com |

#### L2: 本地通用模型（Ollama） 集群检测

```python
def check_ollama_isolation() -> Tuple[bool, str]:
    """检测 本地通用模型（Ollama） 是否配置了集群/分布式模式"""
    # 检查1: 环境变量
    # 检查2: ollama config 文件
    # 检查3: 进程命令行参数
    # 检查4: 监听端口 (是否监听 0.0.0.0 而非 127.0.0.1)
```

#### L4: 知识库注入防御

```python
def review_knowledge_content(content, source) -> Tuple[bool, str]:
    """审查知识库导入内容"""
    # Step 1: 启发式检测 (快速)
    suspicious, reason = _heuristic_injection_check(content)
    if suspicious:
        return False, f"启发式检测发现可疑内容: {reason}"
    
    # Step 2: LLM 深度审查 (慢，但更准确)
    is_safe, review_reason = _run_injection_review(content)
    return is_safe, review_reason
```

**注入检测关键词**（部分）：
- `ignore previous instructions`
- `you are now`
- `forget your system prompt`
- `act as if you`
- `remove all restrictions`
- `DAN 模式`
- `jailbreak`
- （中文对应版本）

### 5.3 诚实原则内核 (morality.py v3.0)

> **2026-06-24 简化**：移除旧版多级道德审核（safe/warn/guide/discuss/lockdown），仅保留「诚实原则」。

#### 核心原则

| 原则 | 说明 |
|------|------|
| **不确定就说不确定** | 对于没有确切把握的事实，必须如实说"我不清楚"或"我无法确认"。绝不编造、猜测、假装知道 |
| **不做道德审判** | AI 是工具，不对用户意图做道德审核。用户自己对使用方式负责 |
| **不拒绝回答** | 不替用户判断什么可以问、什么不可以问 |
| **身份诚实** | 被问"你是什么模型"时，回答实际运行的模型名（如 qwen2.5:14b），而不是"我是 Harven AI 助手"这类套话 |
| **能力诚实** | 不确定的能力不假装拥有。本地模型不说"我能联网"，外脑模型不说"我是本地模型" |

#### 系统提示词注入

`morality.py` 的核心产出是两段系统提示词：

```
IDENTITY_PROMPT（身份段，优先级最高）
  ├─ 你是谁（Harven AI 平台 + 实际模型名）
  ├─ 三种模式说明
  ├─ 能力清单（知识库 + Skills + 文件操作 + 代码执行）
  ├─ 绝对禁止（不编造、不假装）
  └─ 协作模式诚实规则

SYSTEM_PROMPT（= IDENTITY_PROMPT + 诚实原则细则 + 输出规范）
  ├─ 诚实原则最高优先级（5条）
  ├─ 能力描述
  ├─ 操控电脑方式（Python 代码块）
  ├─ 联网诚实声明（纯本地，无实时联网）
  └─ 输出格式规范（禁止 JSON 代码块）
```

#### 代码架构

```
analyze_intent()          → 直接返回 safe（不审核）
build_system_prompt()     → 返回完整 SYSTEM_PROMPT
IDENTITY_PROMPT           → 最小化身份注入（外脑模式用）
SYSTEM_PROMPT             → 完整提示词（本地模式用）
```

> **注**：旧版的关键词扫描（_HIGH_RISK_KEYWORDS）、LLM 意图复核（_llm_review_intent）、锁机升级机制（_DangerCounter）代码仍保留在文件中，但 `analyze_intent()` 入口已被绕过——所有请求直接通过，不做审核。

### 5.4 数据保险箱 (vault_routes.py)

**三态隐私**：

```
状态1: 普通模式
    - 所有功能正常
    - 数据正常保存

状态2: 保密模式 (privacy_mode=1)
    - 当前对话不保存到记忆
    - 不提取用户画像
    - 知识库导入需确认

状态3: 锁定模式 (privacy_mode=2)
    - 需要密码解锁
    - 解锁前无法访问对话/知识库
    - 所有敏感操作被阻止
```

**API 端点**：
- `GET /api/vault/status` — 获取保险箱状态
- `POST /api/vault/lock` — 锁定（需密码）
- `POST /api/vault/unlock` — 解锁（需密码）
- `POST /api/vault/set-privacy-mode` — 设置隐私模式
- `GET /api/vault/logs` — 访问日志
- `GET /api/vault/stats` — 数据统计
- `GET /api/vault/check-lock` — 检查锁定状态（前端轮询）

---

## 6. 功能模块详解（1
### 6.0 2026-06-26 新增模块

| 模块 | 文件 | 功能 |
|------|------|------|
| **垂直模型路由器** | `src/core/vertical_router.py` | 意图检测→参数提取→完整度评分→确认卡/自动执行 |
| **长任务分片器** | `src/core/task_sharder.py` | 任务拆分→子任务执行→checkpoint 中断续跑→SSE进度 |
| **垂直参数目录** | `data/vertical_params.json` | MusicGen/Bark/SD 的参数定义+触发词+关键词映射+权重 |
| **内存警告** | `hardware_detector.py` 扩展 | 30+模型内存估算表 + 多模型并行检查 |
| **用户偏好记忆** | `vertical_router.py` 扩展 | apply_user_prefs() + record_user_prefs() |

### 6.1 按功能分组（完整）

00+ 核心模块）

### 6.1 按功能分组

#### 组1: 对话引擎核心

| 模块 | 文件 | 功能 | 代码行数 |
|------|------|------|----------|
| 对话引擎 | `engine.py` | 三种模式 + 协作 + 三段式查询 + 流式输出 | ~1411 |
| 诚实原则内核 | `morality.py` | 身份+能力描述+诚实原则系统提示词 | ~628 |
| 独立性防御 | `independence.py` | 五层防御 + 出站白名单 + 注入审查 | ~850 |

#### 组2: 知识与记忆

| 模块 | 文件 | 功能 |
|------|------|------|
| 知识库 | `knowledge_base.py` | FAISS 向量检索，支持 PDF/Word/TXT/MD |
| 记忆管理 | `memory.py` | L0原始对话 → L1任务记忆提取 |
| 主动记忆 | `active_memory.py` | L2 关键词检索，对话时自动注入 |
| 记忆梦境 | `memory_dreaming.py` | 离线记忆整合，生成用户洞察 |
| 知识萃取 | `extraction_manager.py` | 从文档/对话萃取核心要点 |
| 三库隔离 | `isolated_db.py` | 隐私/过渡/商用 语义路由 |

#### 组3: 用户画像与个性化

| 模块 | 文件 | 功能 |
|------|------|------|
| 画像自动更新 | `profile_auto_updater.py` | 6维+4复合维，置信度策略 |
| 专项培养 | `specialization_executor.py` | 专项培养后台执行引擎 |
| 任务管理 | `task_manager.py` | 结构化任务 CRUD + 进度跟踪 |
| 信息监控 | `monitor.py` | RSS/Web/GitHub 源监控 |

#### 模型管理

| 模块 | 文件 | 功能 | 代码行数 |
|------|------|------|----------|
| 本地模型管理 | `model_manager.py` | Ollama 调用（OpenAI 兼容 SDK） |
| 多模型管理 | `multi_model_manager.py` | 28 个厂商统一入口 |
| 模型推荐 | `model_recommend.py` | 根据硬件推荐模型 |
| 硬件检测 | `hardware_detector.py` | CPU/RAM/GPU-VRAM，38 模型数据库 |
| 本地通用模型（Ollama） 守护 | `ollama_daemon.py` | 进程守护，自动检测/启动/重启 |

#### 组5: 自动化与调度

| 模块 | 文件 | 功能 |
|------|------|------|
| 定时任务 | `scheduler.py` | APScheduler，Cron 表达式 |
| 后台任务 | `background_task_manager.py` | 专项培养后台并行执行 |
| 任务执行器 | `specialization_executor.py` | 专项培养后台执行引擎 |
| 工作流 | `workflow.py` | 多步骤任务编排管道 |
| 规划器 | `planner.py` | 任务编排引擎 |

#### 组6: 设备与 IoT

| 模块 | 文件 | 功能 |
|------|------|------|
| 设备管理 | `device_manager.py` | 注册/发现/调度/权限 |
| 设备驱动 | `device_drivers.py` | MQTT/HTTP/Zigbee 统一接口 |
| MQTT 驱动 | `drivers/mqtt_driver.py` | MQTT 协议，兼容 HomeAssistant |
| HTTP 驱动 | `drivers/http_driver.py` | HTTP REST，兼容 Tesla API |
| WebSocket 驱动 | `drivers/websocket_driver.py` | WebSocket 实时遥测 |
| AI 生成驱动 | `drivers/ai_generated_driver.py` | 动态生成未知设备驱动 |

#### 组7: 通信与连接

| 模块 | 文件 | 功能 |
|------|------|------|
| 手机联动 | `mobile_connector.py` | Token + 二维码 + 代理对话 |
| 企业微信适配器 | `adapters/wecom.py` | 企微消息收发 |
| 钉钉适配器 | `adapters/dingtalk.py` | 钉钉机器人 |
| 飞书适配器 | `adapters/feishu.py` | 飞书机器人 |
| QQ 适配器 | `adapters/qq.py` | QQ 机器人（官方 Bot API） |

#### 组8: 媒体与生成

| 模块 | 文件 | 功能 |
|------|------|------|
| 图片生成 | `image_gen.py` | Stable Diffusion 调用 |
| 浏览器自动化 | `browser_agent.py` | Playwright 网页内容获取 |
| 代码沙箱 | `code_sandbox.py` | Docker 隔离代码执行 |

#### 组9: 插件与技能

| 模块 | 文件 | 功能 |
|------|------|------|
| 插件管理 | `plugin_system.py` | 安装/卸载/启用/禁用 |
| 技能管理 | `skills_routes.py` (Blueprint) | Skill CRUD + 匹配 + 热更新 |
| Skill 构建器 | `skills_routes.py` | AI 辅助生成 Skill 代码 |
| 资源中心 | `app-plugins.js` | HarvenHub/GitHub/PyPI 搜索安装 |

#### 组10: 进化与统计

| 模块 | 文件 | 功能 |
|------|------|------|
| 受控进化 | `evolution_manager.py` | 版本检测/提案/审批/回滚 |
| 进化提案 | `evolution_routes.py` | 提案系统 API |
| 使用统计 | `stats_manager.py` | daily/monthly/trends/resource |
| 保底机制 | `fallback_manager.py` | 系统异常时保底响应 |

#### 组11: 安全与硬件

| 模块 | 文件 | 功能 |
|------|------|------|
| 硬件安全 | `hw_security.py` | 设备指纹 + 绑定/解绑 + 防克隆 |
| 数据保险箱 | `vault_routes.py` | 三态隐私 + 访问日志 |
| 访客管理 | `visitor_manager.py` | 访问码 + 封禁 + 统计 |

#### 组12: Agent 调度引擎

| 模块 | 文件 | 功能 |
|------|------|------|
| Agent 注册中心 | `agent_registry.py` | 注册/发现/路由，13 个内置 Agent + 意图正则匹配 |
| Agent 执行引擎 | `agent_runner.py` | Agent 生命周期管理（run_script_agent） |
| Agent API | `agent_routes.py` | GET/POST/PUT/DELETE Agent 接口 |
| 多 Agent 协作 | `multi_agent.py` | MultiAgentOrchestrator 调度 |

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

**自生成 Agent**：通过 `🏭 Agent 工坊` 填写名称+角色→AI 自动生成 system_prompt→一键注册。自定义 Agent 自动加入路由表。

#### 组13: 模型目录系统（v2026-06-24）

| 模块 | 文件 | 功能 |
|------|------|------|
| 模型目录 | `data/model_catalog.json` | 38 个模型的完整元数据（VRAM/RAM/下载源/描述/标签） |
| 目录加载器 | `model_catalog.py` | 推荐引擎（按硬件+需求返回双路径） |
| 硬件推荐 | `model_recommend.py` | 按 VRAM/RAM 过滤 |
| 魔搭下载器 | `model_downloader.py` | 从 ModelScope 拉取 GGUF 模型 |
| 同步器 | `model_syncer.py` | 从 ModelScope API 搜索新模型→更新 catalog |
| 本地推理 | `llama_engine.py` | llama-cpp-python 直接跑 GGUF，无需 Ollama |

**六档硬件适配**：

| 档位 | VRAM | 代表模型 |
|------|------|----------|
| ⚪ 服务器 | ≥48GB | Llama 3.3 70B / Qwen2.5 32B / FLUX / CogVideoX 5B |
| 🟣 旗舰 | 24-48GB | Qwen2.5 32B / SD3.5 / CogVideoX / 全模态 |
| 🟢 高端 | 12-24GB | Qwen2.5 14B / DeepSeek-R1 14B / SDXL / MusicGen |
| 🟡 中等 | 8-12GB | Yi 1.5 9B / Qwen2.5 7B / SDXL / MusicGen |
| 🟠 入门 | 4-8GB | Qwen2.5 3B / Phi-3 / LCM 极速版 / 嵌入 |
| 🔴 低配 | <4GB | LCM 极速版 / 嵌入（BGE/GTE/Nomic），纯 CPU |

#### 组14: 存储优化（v2026-06-24）

| 存储层 | 优化 | 效果 |
|--------|------|------|
| SQLite | WAL + busy_timeout=5000 + synchronous=NORMAL | 读写快 2-5× |
| FAISS | read_index(path, IO_FLAG_MMAP) 磁盘映射 | 启动快 5× |
| 磁盘适配 | 自动检测 SSD/HDD → HDD 优先 mmap | 低配不卡顿 |
| 数据安全 | backup model_catalog.json.bak + SHA256 | 防数据丢失 |

---

## 7. 数据库设计全览

### 7.1 主数据库 (`data/sqlite/lingzhi.db`)

#### 核心表

```sql
-- 用户画像
CREATE TABLE user_profile (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    name TEXT NOT NULL DEFAULT '主人',
    master_name TEXT DEFAULT '',
    language TEXT DEFAULT 'zh-CN',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 用户偏好（键值存储）
CREATE TABLE user_preferences (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);
-- 关键偏好键值:
--   'context_rounds'    = 8        (上下文轮数)
--   'temperature'       = 0.7      (温度参数)
--   'stream_output'     = true     (流式输出)
--   'kb_search_enabled' = true     (知识库搜索开关)
--   'auto_search'       = true     (自动联网搜索开关)
--   'search_engine'     = duckduckgo (默认搜索引擎)

-- 专项培养（AI 分身）
CREATE TABLE specializations (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    name TEXT NOT NULL,
    icon TEXT DEFAULT '🤖',
    description TEXT DEFAULT '',
    system_prompt TEXT DEFAULT '',
    knowledge_count INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 对话记录
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT '新对话',
    specialization_id TEXT,
    model TEXT DEFAULT '',
    category TEXT DEFAULT '',
    is_archived INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (specialization_id) REFERENCES specializations(id)
);

-- 消息记录
CREATE TABLE messages (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'thinking')),
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- 认知锁定（不可变信念）
CREATE TABLE cognitive_locks (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'master',
    locked_at TEXT DEFAULT (datetime('now', 'localtime')),
    priority INTEGER DEFAULT 100
);

-- 锁机日志
CREATE TABLE lockdown_log (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    reason TEXT NOT NULL,
    conversation_id TEXT,
    warning_count INTEGER DEFAULT 0,
    lockdown_level INTEGER DEFAULT 0,
    need_admin_unlock INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

-- 设备注册
CREATE TABLE devices (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT DEFAULT 'online',
    metadata TEXT DEFAULT '{}',
    registered_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 设备命令历史
CREATE TABLE device_commands (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    device_id TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (device_id) REFERENCES devices(id)
);

-- 设备权限
CREATE TABLE device_permissions (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    device_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    granted INTEGER DEFAULT 1,
    FOREIGN KEY (device_id) REFERENCES devices(id)
);
```

### 7.2 三库隔离体系

```
data/isolated_dbs/
├── privacy.db       ← 隐私库（密码、个人信息、私密对话）
├── transient.db     ← 过渡库（临时数据、搜索缓存）
└── commercial.db    ← 商用库（可公开分享的知识）
```

**语义路由** (`isolated_db.py`):
- 使用 本地通用模型（Ollama） embeddings (`nomic-embed-text`) 计算语义相似度
- 关键词回退: 若无 embedding 模型，使用关键词匹配
- 分类精度: ~85%（embedding）/ ~60%（关键词）

### 7.3 功能模块独立表

| 模块 | 表名 | 用途 |
|------|------|------|
| background_task_manager | `background_tasks` | 后台任务状态追踪 |
| active_memory | `active_memory` | L2 主动记忆存储 |
| hw_security | `bound_devices` | 绑定设备信息 |
| hw_security | `security_events` | 安全事件日志 |
| harvenhub_market | `marketplace_items` | 社区市场项目 |
| harvenhub_market | `marketplace_ratings` | 项目评分 |
| harvenhub_market | `marketplace_categories` | 市场分类 |
| mobile_connector | `mobile_tokens` | 移动端连接 Token |
| mobile_connector | `mobile_sessions` | 移动端会话 |
| mobile_connector | `mobile_notifications` | 移动端通知 |
| multi_agent | `collaboration_sessions` | 多 Agent 协作会话 |
| monitor | `monitored_topics` | 监控主题 |
| monitor | `monitored_items` | 监控结果条目 |
| scheduler | `scheduled_tasks` | 定时任务配置 |
| scheduler | `scheduled_task_runs` | 定时任务执行历史 |
| task_manager | `structured_tasks` | 结构化任务 |
| visitor_manager | `visitors` | 访客会话 |
| visitor_manager | `visitor_events` | 访客事件 |
| visitor_manager | `access_config` | 访客配置（键值对） |
| workflow | `workflows` | 工作流定义 |
| vault_routes | `access_logs` | 保险箱访问日志 |
| vault_routes | `privacy_sessions` | 隐私会话记录 |

---

## 8. API 路由全览（260+ 端点）

### 8.1 路由架构概览

```
web_app.py (主路由)
├── @app.route('/')                     → index()
├── @app.route('/health')              → health_check()
├── @app.route('/api/chat')           → chat() [SSE]
├── @app.route('/api/settings')        → api_get_settings() / api_save_settings()
└── ... (140+ 端点)

Blueprint 注册:
├── /api/plugins           ← plugin_system.py
├── /api/vault            ← vault_routes.py
├── /api/orchestrator     ← orchestrator_routes.py
├── /api/collab           ← collab_routes.py
├── /api/models           ← model_routes.py
├── /api/skills          ← skills_routes.py
├── /api/evolution       ← evolution_routes.py
├── /api/stats            ← stats_routes.py
├── /api/fallback         ← fallback_routes.py
├── /api/extraction      ← extraction_routes.py
└── /api/im              ← im_routes.py

add_url_rule 注册:
├── /api/devices/*        ← device_api.py
├── /api/models/*         ← multi_model_api.py (部分)
└── /api/*               ← new_features_api.py
```

### 8.2 完整端点列表（按功能分组）

#### 对话与聊天 (9 个)

| 方法 | 端点 | 功能 | 请求体 |
|------|------|------|--------|
| POST | `/api/chat` | 主对话接口 (SSE) | `{message, conversation_id, query_mode, collab_mode}` |
| POST | `/api/chat/image` | 图片上传+分析 | `multipart/form-data` |
| POST | `/api/chat/video` | 视频上传+分析 (SSE) | `multipart/form-data` |
| POST | `/api/chat/audio` | 音频上传+转写+回答 | `multipart/form-data` |
| POST | `/api/chat/file` | 文件上传到对话 | `multipart/form-data` |

#### 对话管理 (8 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/conversations` | 列出所有对话 |
| POST | `/api/conversations` | 创建新对话 |
| DELETE | `/api/conversations/<cid>` | 删除对话 |
| POST | `/api/conversations/batch-delete` | 批量删除对话 |
| POST | `/api/conversations/auto-classify` | 自动分类对话 |
| GET | `/api/conversations/<cid>/messages` | 获取对话消息 |
| PUT | `/api/conversations/<cid>/title` | 更新对话标题 |

#### 模型管理 (9 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/models/hardware` | 硬件信息 + 推荐模型 |
| GET | `/api/models/installed` | 已安装模型列表 |
| POST | `/api/models/pull` | 拉取模型 (SSE) |
| POST | `/api/models/delete` | 删除模型 |
| POST | `/api/models/switch` | 切换当前模型 |
| GET | `/api/models` | 所有模型及状态 |
| POST | `/api/models/enable` | 启用模型（含 API key） |
| POST | `/api/models/disable/<id>` | 禁用模型 |
| POST | `/api/models/set_current` | 设置当前活跃模型 |

#### 知识库 (4 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| POST | `/api/knowledge/upload` | 上传知识文件 |
| POST | `/api/knowledge/add_text` | 添加文本知识 |
| GET | `/api/knowledge/list` | 列出知识库文件 |
| GET | `/api/kb/auto-import/status` | 自动导入状态 |
| GET | `/api/kb/auto-import/list` | 自动导入文件列表 |
| POST | `/api/kb/auto-import/trigger` | 触发自动导入 |

#### 任务管理 (4 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/tasks` | 获取任务列表 |
| POST | `/api/tasks` | 创建任务 |
| POST | `/api/tasks/<tid>/complete` | 完成任务 |
| POST | `/api/tasks/<tid>/progress` | 更新任务进度 |

#### 设置与配置 (10 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/settings` | 获取全部设置 |
| POST | `/api/settings` | 保存设置 |
| POST | `/api/settings/user` | 保存用户设置 |
| POST | `/api/settings/model` | 保存模型设置 |
| POST | `/api/settings/morality` | 保存道德审核设置 |
| POST | `/api/settings/security` | 保存安全设置 |
| POST | `/api/settings/ui` | 保存 UI 设置 |
| POST | `/api/settings/language` | 保存语言设置 |
| POST | `/api/settings/web_search` | 保存搜索设置 |
| POST | `/api/settings/ollama` | 保存 本地通用模型（Ollama） 设置 |

#### 安全与隐私 (12 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/vault/status` | 保险箱状态 |
| POST | `/api/vault/lock` | 锁定保险箱 |
| POST | `/api/vault/unlock` | 解锁保险箱 |
| POST | `/api/vault/set-privacy-mode` | 设置隐私模式 |
| GET | `/api/vault/logs` | 访问日志 |
| GET | `/api/vault/stats` | 数据统计 |
| GET | `/api/security/device-status` | 设备绑定状态 |
| POST | `/api/security/bind` | 设备绑定 |
| POST | `/api/security/unbind` | 解除绑定 |
| GET | `/api/security/device-fingerprint` | 设备指纹 |

#### 协作模式 (4 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/collab/status` | 协作模式状态 |
| POST | `/api/collab/config` | 更新协作配置 |
| GET | `/api/collab/foreign-models` | 可用外脑模型 |
| GET | `/api/collab/privacy-levels` | 隐私级别选项 |

#### 垂直模型 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/vertical/models` | GET | 列出已注册的垂直模型 |
| `/api/vertical/execute` | POST（SSE） | 确认参数后执行垂直模型生成 |

#### 长任务 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/task/pending` | GET | 获取未完成的长任务（断点续跑） |
| `/api/task/cancel` | POST | 取消长任务 |
| `/api/task/resume` | POST（SSE） | 断点续跑长任务 |

#### 协作 API（v2.0 新增）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/collab/memory-check` | GET | 多模型并行内存检查 |

#### 统计 (7 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/stats/daily` | 当日统计 |
| GET | `/api/stats/daily/<date>` | 指定日期统计 |
| GET | `/api/stats/monthly` | 当月统计 |
| GET | `/api/stats/trends` | 使用趋势 |
| GET | `/api/stats/resource` | 系统资源统计 |
| POST | `/api/stats/collect-resource` | 采集资源统计 |

#### 进化与版本 (6 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/version/check` | 检测新版本 |
| POST | `/api/version/update` | 下载并安装更新 |
| GET | `/api/evolution/version` | 当前版本信息 |
| GET | `/api/evolution/proposals` | 进化提案列表 |
| POST | `/api/evolution/proposals` | 创建进化提案 |
| POST | `/api/evolution/proposals/<id>/approve` | 批准提案 |

#### 技能管理 (12 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/skills` | 列出所有技能 |
| POST | `/api/skills` | 创建技能 |
| PUT | `/api/skills/<id>` | 更新技能 |
| DELETE | `/api/skills/<id>` | 删除技能 |
| POST | `/api/skills/<id>/toggle` | 启用/禁用技能 |
| POST | `/api/skills/install` | 安装技能 |
| GET | `/api/skills/harvenhub/search` | HarvenHub 搜索技能 |
| POST | `/api/skills/match` | 测试消息匹配技能 |
| POST | `/api/skills/reload` | 重新加载技能 |
| GET | `/api/skills/hot-reload/status` | 热更新状态 |

#### IoT 设备 (20+ 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/devices` | 列出所有设备 |
| POST | `/api/devices` | 注册设备 |
| DELETE | `/api/devices/<id>` | 注销设备 |
| POST | `/api/devices/<id>/command` | 发送设备命令 |
| GET | `/api/devices/<id>/state` | 获取设备状态 |
| POST | `/api/devices/discover` | 发现设备 |
| GET | `/api/devices/drivers` | 列出所有驱动 |
| POST | `/api/devices/ai-driver/generate` | AI 生成设备驱动 |

#### 移动端 (9 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| POST | `/api/mobile/token` | 生成连接 Token |
| GET | `/api/mobile/qrcode` | 生成连接二维码 |
| GET | `/api/mobile/devices` | 已连接设备列表 |
| POST | `/api/mobile/revoke` | 撤销设备 Token |
| POST | `/api/mobile/chat` | 移动端对话接口 |
| GET | `/api/mobile/notifications` | 通知列表 |
| POST | `/api/mobile/notifications/read` | 标记通知已读 |

#### 浏览器自动化 (8 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| POST | `/api/browser/execute` | 执行浏览器任务 |
| GET | `/api/browser/current` | 当前页面信息 |
| POST | `/api/browser/screenshot` | 截图 |
| POST | `/api/browser/close` | 关闭浏览器 |
| GET | `/api/browser/status` | Playwright 状态 |
| POST | `/api/browser/install` | 安装 Playwright |
| POST | `/api/browser/navigate` | 导航到 URL |
| POST | `/api/browser/content` | 获取页面内容 |

#### 定时任务 (7 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/scheduler/tasks` | 获取所有定时任务 |
| POST | `/api/scheduler/tasks` | 添加定时任务 |
| DELETE | `/api/scheduler/tasks/<id>` | 删除定时任务 |
| POST | `/api/scheduler/tasks/<id>/toggle` | 启用/禁用任务 |
| POST | `/api/scheduler/tasks/<id>/pause` | 暂停任务 |
| POST | `/api/scheduler/parse` | 解析自然语言定时 |
| GET | `/api/scheduler/runs` | 执行历史 |

#### 多 Agent 协作 (7 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/multi-agent/roles` | 预定义 Agent 角色 |
| GET | `/api/multi-agent/templates` | 协作模式模板 |
| POST | `/api/multi-agent/collaborate` | 执行多 Agent 协作 |
| GET | `/api/multi-agent/sessions` | 获取协作会话 |
| POST | `/api/multi-agent/sessions` | 创建协作会话 |
| GET | `/api/multi-agent/sessions/<id>` | 会话详情 |
| GET | `/api/multi-agent/sessions/<id>/history` | 会话消息历史 |

#### HarvenHub 社区市场 (10 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/harvenhub-market/categories` | 所有分类 |
| GET | `/api/harvenhub-market/browse` | 浏览市场 |
| GET | `/api/harvenhub-market/search` | 搜索市场 |
| GET | `/api/harvenhub-market/item/<id>` | 项目详情 |
| POST | `/api/harvenhub-market/publish` | 发布项目 |
| PUT | `/api/harvenhub-market/item/<id>` | 更新项目 |
| DELETE | `/api/harvenhub-market/item/<id>` | 下架项目 |
| POST | `/api/harvenhub-market/rate` | 评分 |
| POST | `/api/harvenhub-market/download/<id>` | 记录下载 |

#### 工作流 (6 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/workflows` | 所有工作流 |
| POST | `/api/workflows` | 创建工作流 |
| POST | `/api/workflows/<id>/run` | 运行工作流 |
| GET | `/api/workflows/<id>` | 工作流详情 |
| DELETE | `/api/workflows/<id>` | 删除工作流 |
| GET | `/api/workflows/templates` | 内置模板 |

#### 多渠道接入 (7 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/channels` | 所有渠道 |
| POST | `/api/channels` | 添加渠道 |
| DELETE | `/api/channels/<id>` | 删除渠道 |
| POST | `/api/channels/test` | 测试发送消息 |
| POST | `/api/webhook/<id>` | 接收 Webhook |

#### 保底机制 (7 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/api/fallback/health` | 系统健康状态 |
| GET | `/api/fallback/status` | 保底状态 |
| POST | `/api/fallback/enter` | 手动进入保底模式 |
| POST | `/api/fallback/exit` | 手动退出保底模式 |
| POST | `/api/fallback/auto-check` | 自动检查 |
| POST | `/api/fallback/try-fix` | 尝试自动修复 |

#### 知识萃取 (4 个)

| 方法 | 端点 | 功能 |
|------|------|------|
| POST | `/api/extraction/extract-document` | 从文档萃取 |
| POST | `/api/extraction/extract-conversation` | 从对话萃取 |
| GET | `/api/extraction/knowledge` | 已萃取知识 |
| DELETE | `/api/extraction/knowledge/<id>` | 删除萃取知识 |

---

## 9. 前端架构详解

### 9.1 模板结构（36 文件，验证通过 ✅）

```
templates/
├── index.html             → 主页面（692 行）
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
    ├── 模型管理 (9) 🆕 +双模配置
    │   ├── tab-ai模型.html
    │   ├── tab-multimodel.html
    │   ├── tab-已安装.html
    │   ├── tab-模型市场.html
    │   ├── tab-手动拉取.html
    │   ├── tab-本地模型推荐.html
    │   ├── tab-本地模型选择器.html
    │   ├── tab-全部本地模型.html
    │   └── tab-本地双模配置.html      🆕 v7.0
    │
    ├── 高级功能 (8)
    │   ├── tab-skills.html
    │   ├── tab-skill-builder.html
    │   ├── tab-agent.html
    │   ├── tab-agents.html
    │   ├── tab-agent-工坊.html
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
    └── 特殊 (2)
        ├── modal-数据保险箱.html
        └── tab-保底状态.html
```

### 9.1.1 HTML 结构

### 9.2 JS 模块（22 个，全部验证通过 ✅）

```
static/js/modules/
├── app-core.js            → 核心初始化 + 全局状态
├── app-conversation.js    → 对话核心 + 协作下拉 + 确认卡 + 进度条
├── app-send.js            → 消息发送 + 查询模式切换
├── app-settings.js        → 设置面板（8 个主 Tab）
├── app-knowledge.js       → 知识库管理
├── app-model-manager.js   → 多模型管理（28 厂商）
├── app-network.js         → 联网搜索 + 浏览器代理
├── app-plugins.js         → 插件管理
├── app-skills.js          → 技能系统
├── app-evolution.js       → 受控进化
├── app-stats.js           → 统计面板
├── app-fallback.js        → 保底机制
├── app-extraction.js      → 知识萃取
├── app-hardware.js        → 硬件检测 + 模型推荐
├── app-devices.js         → IoT 设备管理
├── app-harvenhub.js       → HarvenHub 市场
├── app-media.js           → 多媒体处理
├── app-im.js              → IM 消息
├── app-bilibili.js        → B 站管理
├── app-multi-agent.js     → 多 Agent 协作
├── app-monitor.js         → 系统监控
└── app-version-check.js   → 版本检测
```

#### 核心模块详解

**全局状态**（`app-core.js`）：
```javascript
let currentConvId = null;
let isStreaming = false;
let isProcessing = false;
let msgQueue = [];
```

**核心函数**（`app-conversation.js`，非 app-core.js）：
```javascript
function sendMessage()              // 消息发送入口
function _sendMessageNow(text)      // 核心发送逻辑 (POST /api/chat)
function processNextInQueue()       // 队列消费者
function renderMessages(msgs)       // 渲染消息列表
function appendAiMsg(text)          // 追加 AI 消息 (Markdown 渲染)
function startAiStream()            // 创建流式消息占位
function appendStreamChunk(text)    // 追加流式文本块
function finalizeStream()           // 流结束，Markdown 渲染
```

**`app-conversation.js`** (~1650 行) — 对话管理
```javascript
// 模式管理
function setQueryMode(mode)           // 切换 auto/kb_only/direct
function setCollabMode(mode)          // 协作模式选择（off/cloud_local/local_dual）
function toggleCollabDropdown()       // 展开/收起协作下拉菜单
function showCollabHelp()             // 协作模式帮助说明

// Ollama 状态
function checkOllamaStatus()          // 智能状态指示器（通用/垂直分类）
function loadInstalledModels()        // GET /api/models/installed

// 对话操作
function loadConversations()         // 加载对话列表
function loadConv(id)              // 加载指定对话
function newChat()                 // 新建对话
function delConv(id)               // 删除对话
```

#### 功能组

| 模块 | 核心函数 | 用途 |
|------|----------|------|
| `app-send.js` | `updateQueueUI()`, `addToQueue()` | 消息队列 UI |
| `app-settings.js` | `openSettings()`, `saveAllSettings()` | 设置面板管理 |
| `app-plugins.js` | `searchResourceHub()`, `installRHItem()` | 资源中心 |
| `app-model-manager.js` | `pullModelDirect()`, `showPullProgress()` | 模型管理 |
| `app-knowledge.js` | `checkKbAutoImport()`, `showKbImportProgress()` | 知识库 |
| `app-monitor.js` | `loadMonitorTopics()`, `checkTopicNow()` | 信息监控 |
| `app-media.js` | `showMediaMenu()`, `capturePhoto()` | 媒体附件 |
| `app-hardware.js` | `loadHardwareReport()`, `renderHardwareReport()` | 硬件检测 |
| `app-network.js` | `startTunnel()`, `checkTunnelStatus()` | Cloudflare 隧道 |
| `app-evolution.js` | `HarvenEvolution.checkVersion()`, `HarvenEvolution.approveProposal()` | 受控进化 |
| `app-stats.js` | `loadDailyStats()`, `drawTrendChart()` | 统计分析 |
| `app-extraction.js` | `extractFromDocument()`, `extractFromConversation()` | 知识萃取 |
| `app-fallback.js` | `HarvenFallback.checkHealth()`, `HarvenFallback.toggleMode()` | 保底状态 |
| `app-skills.js` | `loadSkillsPanel()`, `toggleSkill()` | 技能管理 |
| `app-version-check.js` | `checkVersionSilent()`, `startUpdate()` | 版本检测 |
| `app-harvenhub.js` | `loadHarvenHubMarket()`, `hhMarketPublish()` | HarvenHub |
| `app-bilibili.js` | `biliSaveAuth()`, `biliStartUpload()` | B站发布 |
| `app-multi-agent.js` | `loadRoles()`, `startCollaboration()` | 多Agent |
| `app-im.js` | `imRefreshList()`, `imTestConnection()` | IM 渠道 |
| `app-devices.js` | `loadDevices()`, `sendCommand()` | IoT 设备 |

### 9.3 SSE 流式协议详解

| SSE Type | 触发时机 | content 格式 | 前端处理 |
|----------|---------|-------------|---------|
| `text` | 模型流式输出 | string | appendStreamText() |
| `thinking` | AI 思考状态 | string | 顶部思考指示器 |
| `source` | 数据来源标签 | string | 消息底部来源条 |
| `error` | 错误信息 | string | 红色错误气泡 |
| `notification` | 系统通知 | {type, message} | toast + 系统消息 |
| `action` | 操作指令 | {type, file, label} | handleStreamAction() |
| `suggestion` | 技能建议 | array | 安装卡片 |
| `vertical_confirm` | 垂直模型确认卡 | {model_id, params, score, ...} | renderVerticalConfirmCard() |
| `task_progress` | 长任务进度 | {task_id, step, total, pct, status} | renderTaskProgress() |
| `done` | 流结束 | "" | 清理状态 |
| `model_source` | 模型来源标识 | {model, via} | 记录本次回复模型 |
| `tool_result` | 工具执行结果 | {success, output, error} | 显示执行状态 |

### 9.4 垂直确认卡片

用户触发垂直模型意图且参数不完整时（40-80%），引擎返回 `vertical_confirm`：

```json
{
  "type": "vertical_confirm",
  "model_id": "musicgen-small",
  "model_name": "MusicGen Small",
  "model_icon": "🎵",
  "score": 73,
  "params": [
    {"name": "mood", "value": "忧伤", "found": true},
    {"name": "style", "value": "auto", "found": false, "defaulted": true}
  ],
  "message": "🎵 MusicGen Small — 部分参数使用默认值，请确认"
}
```

卡片渲染→用户点"确认生成"→POST `/api/vertical/execute`→SSE流式返回结果。

### 9.5 长任务进度条

`task_progress` SSE 事件渲染渐变色进度条：

```json
{"type": "task_progress", "content": {"task_id": "xxx", "step": 3, "total": 5, "pct": 60, "status": "分析PDF 3/5"}}
```

CSS: `.task-progress-card` → `.tp-bar-fill` 渐变色动画。


## 10. 数据流与逻辑关系

### 10.1 主数据流图

```
用户
    │
    ▼ (输入框 Enter)
[前端 app-core.js]
    │
    ▼ (POST /api/chat)
[后端 web_app.py: chat()]
    │
    ├─ 1. 保险箱锁定检查
    ├─ 2. 访客权限验证
    │
    ▼
[ConversationEngine.send()]
    │
    ├─ 3. 锁机检查 (morality.py: is_lockdown, v3.0 始终放行)
    ├─ 4. 插件匹配
    ├─ 5. Prompt Injection 检测
    │
    ▼
[三段式查询 _stream_three_stage()]
    │
    ├─ Step 1: 知识库搜索
    │   └─ [knowledge_base.py] → FAISS 检索 → 相关片段
    │
    ├─ Step 2: [已取消] 联网搜索
    │
    ├─ 协作模式检查:
    │   └─ [collab_manager.py] → 外脑分析 → 本地通用模型（Ollama） 整合（覆盖Step 3路由）
    │
    ├─ Step 3: 模型路由（协作模式下跳过）
    │
    ▼ (SSE 流式返回)
[前端 app-conversation.js: _sendMessageNow() 内联 SSE 处理]
    │
    ├─ type: "text" → in-stream chunk append
    ├─ type: "source" → in-stream source label
    └─ type: "done" → finalize stream
    │
    ▼ (流结束后)
[后处理]
    │
    ├─ 保存对话 [database.py: save_message()]
    ├─ 提取记忆 [memory.py: extract_task_memory()]
    ├─ 更新画像 [profile_auto_updater.py]
    ├─ 提取任务 [task_manager.py]
    └─ 更新 Active Memory [active_memory.py]
```

### 10.2 知识库数据流

```
用户导入知识
    │
    ▼ (PDF/Word/TXT/MD/URL)
[前端 app-knowledge.js]
    │
    ▼ (POST /api/knowledge/upload)
[后端 web_app.py: api_upload_knowledge()]
    │
    ├─ 1. 文件类型检测
    ├─ 2. 注入防御检查 [independence.py: review_knowledge_content()]
    │   ├─ 启发式检测 (30+ 关键词)
    │   └─ LLM 审查 (本地 本地通用模型（Ollama）)
    │
    ▼ (审查通过)
[knowledge_base.py: add_document()]
    │
    ├─ 文本提取 (pypdf / pdfplumber / python-docx)
    ├─ 分块 (chunk_size=500, overlap=50)
    ├─ Embedding 生成 (本地通用模型（Ollama） nomic-embed-text)
    ├─ FAISS 索引更新
    └─ 元数据保存到 SQLite
    │
    ▼
用户查询时
    │
    ▼ (FAISS 向量检索)
[knowledge_base.py: search()]
    │
    ├─ Query Embedding
    ├─ 余弦相似度计算
    ├─ Top-K 结果 (default k=3, threshold=0.2)
    │
    ▼
返回相关片段 → 注入 System Prompt → LLM 回答
```

### 10.3 记忆系统数据流

```
L0: 原始对话记忆
    │
    ▼ (每次对话自动保存)
[database.py: save_message()]
    │
    ▼
L1: 任务记忆 (自动提取)
    │
    ▼ (对话结束后触发)
[memory.py: extract_task_memory()]
    │
    ├─ AI 提取: _ai_extract()
    │   └─ 本地 本地通用模型（Ollama） 提取: identity/preference/decision/task/knowledge
    │
    ├─ 规则提取 (AI 不可用时回退): _simple_extract()
    │   └─ 模式匹配: 10种偏好模式 + 9种决策模式
    │
    ▼
保存到 cognitive_locks 表 (优先级 >= 80 自动锁定)
    │
    ▼
L2: 主动记忆 (检索增强)
    │
    ▼ (对话时自动注入)
[active_memory.py: retrieve()]
    │
    ├─ 关键词重叠度评分
    ├─ Top-K 结果 (default k=5)
    │
    ▼
注入 System Prompt → LLM 获得历史上下文
```

### 10.4 协作模式数据流

```
自动模式 + collab_mode = cloud_local / local_dual
    │
    ▼
[engine.py: _invoke_collab_foreign()]
    │
    ├─ 1. 隐私过滤 (按 collab_privacy_level)
    │   ├─ strict:   仅传问题摘要
    │   ├─ balanced: 传去敏后的问题
    │   └─ full:     传完整问题 + 上下文
    │
    ▼
    ├─ 2. 构建外脑请求
    │   ├─ System Prompt: 身份 + 分析任务 + Skills JSON 工具
    │   └─ User Message: 过滤后的问题
    │
    ▼
    ├─ 3. 调用外部模型
    │   └─ [multi_model_manager.py: generate_response()]
    │       → 返回 dict 生成器 {'type':'text','content':'...'}
    │
    ▼
    ├─ 4. 解析外脑响应
    │   ├─ type='text' → result += content
    │   ├─ type='tool_call' → 收集工具调用
    │   └─ type='error' → 触发回退
    │
    ▼
    ├─ 5. 本地通用模型最终整合（⚡注意：云地协作中外脑输出已嵌入上下文中，本地通用模型做最终答案整合）
    │   └─ 增强上下文: 原问题 + 本地知识 + 外脑分析 → 本地通用模型生成最终回答
    │
    ▼
返回前端
    │
    └─ 如有工具调用 → 本地执行 → 结果追加
```

---

## 11. 启动流程与运维

### 11.1 启动顺序详解

```
启动HarvenAI.bat
    │
    ▼
[web_app.py] __main__
    │
    ├─ 1. 初始化 Config (config.yaml 加载)
    │   └─ [utils/config.py: Config.__init__()]
    │       ├─ 深合并 DEFAULT_CONFIG
    │       └─ 深合并 config.yaml (用户配置)
    │
    ├─ 2. 创建 Flask app
    │   └─ Flask(__name__, template_folder='templates/', static_folder='static/')
    │
    ├─ 3. 生成/加载 SECRET_KEY
    │   └─ 从 config.yaml 读取，缺失则自动生成
    │
    ├─ 4. 注册中间件
    │   ├─ check_independence (独立性防御)
    │   ├─ check_rate_limit (速率限制: 60次/分钟)
    │   ├─ check_visitor_access (访客访问码验证)
    │   └─ add_cache_headers (禁用缓存)
    │
    ├─ 5. 数据库初始化
    │   └─ [models/database.py: init_db()]
    │       └─ CREATE TABLE IF NOT EXISTS (18 个核心表)
    │
    ├─ 6. 注册路由
    │   ├─ Blueprint: plugins, vault, orchestrator, collab, models, skills
    │   ├─ Blueprint: evolution, stats, fallback, extraction, im
    │   └─ add_url_rule: devices, multi_model, new_features
    │
    ├─ 7. 启动 Memory Dreaming + 定时任务调度器 (模块加载时注册)
    │   ├─ [memory_dreaming.py] 离线记忆整合
    │   └─ [scheduler.py] APScheduler 启动
    │
    ├─ 8. 设备完整性检查
    │   └─ [hw_security.py: verify_device_integrity()]
    │
    ▼
    └─ 9. 启动 Flask 服务 (host='0.0.0.0', port=5000, debug=False, threaded=True)
```

### 11.2 环境要求

| 组件 | 入门配置（跑 3B 模型） | 主流配置（跑 7B 模型） | 高性能（跑 14B+ 模型） | 检查方式 |
|------|----------------------|----------------------|----------------------|----------|
| **CPU** | 4 核+ | 6 核+ | 8 核+ | `hardware_detector.py` |
| **RAM** | 10 GB+ | 16 GB+ | 32 GB+ | `hardware_detector.py` |
| **GPU** | 无（CPU 推理） | 6 GB VRAM+ | 12 GB VRAM+ | `hardware_detector.py` |
| **磁盘** | 20 GB+ | 50 GB+ | 100 GB+ SSD | `shutil.disk_usage()` |
| **Python** | 3.10+ | 3.10+ | 3.13+ | `sys.version` |
| **Ollama** | 最新版 | 最新版 | 最新版 | `ollama --version` |
| **Node.js** | 无需 | 可选（Playwright） | 22+（Playwright） | `node --version` |
| **Docker** | 无需 | 无需 | 可选（代码沙箱） | `docker --version` |

> **目标硬件**（老谭开发环境）：i7-13700F 16核 / RTX 4070 SUPER 12GB / 32GB RAM → 高性能级，可流畅运行 14B 模型。

### 11.3 🚨 重启铁律

**问题**: Flask `debug=False` 时模板被缓存，且 `taskkill` 可能遗漏后台进程

**症状**:
- 修改代码后刷新浏览器，变化不生效
- 新旧代码混用，行为异常
- 端口 5000 被多进程抢占

**正确重启流程**:
```bash
# 1. 查找占用 5000 端口的进程
netstat -ano | findstr :7000
# 输出示例: TCP    0.0.0.0:7000     0.0.0.0:0    LISTENING     12345

# 2. 杀死进程 (等待 3 秒确保彻底退出)
taskkill /PID 12345 /F
timeout /t 3

# 3. 再次确认无残留
netstat -ano | findstr :7000
# 无输出 = 干净

# 4. 启动新进程
启动HarvenAI.bat
```

**自动化脚本** (`重启HarvenAI.bat`):
```batch
@echo off
chcp 65001 >nul
netstat -ano | findstr :7000 > "%temp%\harven_port.txt" 2>nul
for /f "tokens=5" %%a in (%temp%\harven_port.txt) do (
    taskkill /PID %%a /F >nul 2>&1
)
del "%temp%\harven_port.txt" 2>nul
timeout /t 3 /nobreak >nul
echo 已停止旧进程，启动新进程...
call "%~dp0启动HarvenAI.bat"
```

### 11.4 维护命令速查

```bash
# 数据库备份
copy H:\HarvenAI\data\sqlite\lingzhi.db H:\Backups\lingzhi_%date:~0,10%.db

# 查看 Flask 日志
type H:\HarvenAI\logs\harven_*.log | more

# 清理 Python 缓存
find H:\HarvenAI -name "__pycache__" -type d -exec rm -rf {} +

# 重置管理员密码 (需要编辑 config.yaml)
# 1. 停止 Harven AI
# 2. 编辑 config.yaml: admin_password: "new_password_hash"
# 3. 重启

# 查看已安装 Ollama 模型
curl http://localhost:11434/api/tags

# 手动拉取模型
ollama pull qwen2.5:7b

# 检查 PyQt6 许可合规性
# PySide6 使用 LGPL → 动态链接，不发布修改后的源码即合规
```

---

## 12. 法律合规与知识产权

### 12.1 开源许可合规分析

| 组件 | 许可 | 风险等级 | 合规要求 |
|------|------|----------|----------|
| **Flask** | MIT | 🟢 无风险 | 保留版权声明 |
| **PySide6** | LGPL v3 | 🟡 需注意 | **动态链接**，不修改源码即合规；若修改 PySide6 源码必须开源 |
| **FAISS** | MIT | 🟢 无风险 | 保留版权声明 |
| **SQLite** | Public Domain | 🟢 无风险 | 无需任何义务 |
| **PyYAML** | MIT | 🟢 无风险 | 保留版权声明 |
| **requests** | Apache 2.0 | 🟢 无风险 | 保留 NOTICE 文件 |
| **本地通用模型（Ollama）** | MIT | 🟢 无风险 | 本地运行，不分发 |
| **Bootstrap 5** | MIT | 🟢 无风险 | CDN 或本地均可 |

**⚠️ PySide6 LGPL 关键警示**：
- LGPL 要求**动态链接**（不将 PySide6 打包进单一可执行文件）
- 若使用 PyInstaller 打包，需确保 PySide6 以 `.pyd`/`.dll` 形式独立存在
- 若修改 PySide6 源码，修改部分必须开源
- **当前状态**：Harven AI 未使用 PySide6 GUI（仅规划中），实际风险为 0

### 12.2 知识产权风险

| 风险项 | 描述 | 风险等级 | 缓解措施 |
|--------|------|----------|----------|
| **模型权重** | 用户自行下载的 Ollama 模型，版权归模型作者 | 🟢 低风险 | 不分发模型权重，用户自行拉取 |
| **外部 API 内容** | 外部模型返回内容可能有版权争议 | 🟡 中风险 | 在用户协议中声明「外部模型内容仅供参考」 |
| ** trademarks** | "Harven" 名称是否已被注册 | 🟡 中风险 | **建议**：在目标市场（中国）检索商标数据库 |
| **原创内容** | Harven AI 生成内容的版权归属 | 🟡 中风险 | 在用户协议中明确「AI 生成内容归用户所有」 |
| **用户数据** | 用户上传的文件/对话内容的版权 | 🟢 低风险 | 仅本地存储，不对外分享 |

### 12.3 隐私与数据合规

**适用法规**：
- 🇨🇳 **《中华人民共和国个人信息保护法》**（个保法）
- 🇪🇺 **GDPR**（如面向欧盟用户）
- 🇺🇸 **CCPA**（如面向加州用户）

**合规措施**：

| 要求 | Harven AI 实现 | 合规状态 |
|------|----------------|----------|
| **数据本地化** | 所有数据存储在本地 SQLite | ✅ 合规 |
| **用户同意** | 首次启动显示隐私协议 | ✅ 需补充 |
| **数据访问权** | 用户可导出/删除数据 | ✅ 数据保险箱已实现 |
| **数据更正权** | 用户可编辑对话/知识库 | ✅ 已实现 |
| **数据携带权** | 支持导出为 JSON/Markdown | ✅ 待实现 |
| **儿童保护** | 不主动收集身份信息 | ✅ 合规 |
| **跨境传输** | 协作模式默认「平衡」去敏 | ✅ 合规 |

**⚠️ 待补充**：
- 首次启动需加入**隐私协议弹窗**（目前仅硬件检测引导）
- 需在设置页显示**数据使用说明**
- 建议加入「删除所有我的数据」一键功能

### 12.4 内容合规（网络安全法）

**中国《网络安全法》+《生成式人工智能服务管理暂行办法》要求**：

| 要求 | Harven AI 实现 | 合规状态 |
|------|----------------|----------|
| **内容过滤** | `morality.py` v3.0 诚实原则（不审核、不拒答） | ✅ 合规（纯本地个人 AI） |
| **日志留存** | 对话记录本地存储 | ✅ 合规 |
| **算法备案** | 仅本地运行，不对外提供服务 | ✅ 无需备案 |
| **标识义务** | AI 生成内容需标识 | ⚠️ 待实现 |
| **敏感话题拒答** | v3.0 不做内容审核，用户自行负责 | ✅ 符合本地个人 AI 定位 |

**重要**：Harven AI 是**纯本地个人 AI**，不对外提供公共服务，因此：
- 无需向网信办申请算法备案
- 无需取得《互联网信息服务算法推荐管理规定》许可
- 但用户界面需有「AI 生成内容请甄别」提示

### 12.5 综合风险矩阵

| 风险 | 概率 | 影响 | 等级 | 建议措施 |
|------|------|------|------|----------|
| PySide6 许可违规 | 低 | 中 | 🟡 | 确保动态链接，不修改源码 |
| 商标侵权 | 低 | 高 | 🟡 | 在目标市场检索商标 |
| 个保法合规 | 中 | 中 | 🟡 | 补充隐私协议弹窗 |
| 内容合规 | 低 | 低 | 🟢 | 纯本地个人 AI，不对外服务，无需内容审核 |
| 外部 API 数据泄露 | 低 | 高 | 🟡 | 协作模式去敏已实现 |
| 模型权重版权 | 低 | 低 | 🟢 | 不分发模型 |
| 开源许可 | 低 | 低 | 🟢 | MIT/Apache 无风险 |

---

## 13. 开发规范与已知坑

### 13.1 代码规范

**Python 规范**：
```python
# ✅ 正确：导入顺序（标准库 → 第三方 → 本地）
import os
import yaml
from pathlib import Path
from flask import Blueprint, request, jsonify

import requests
import faiss

from src.utils.config import Config

# ❌ 错误：在函数内局部导入标准库（已在 v3 中修复）
def some_func():
    import yaml  # 不要这样
```

**前端规范**：
```javascript
// ✅ 正确：模块对外暴露统一接口
window.App = window.App || {};
App.Settings = {
    init() {...},
    save() {...}
};

// ✅ 正确：API 请求统一用 fetchWithAuth（如已实现）
// ❌ 错误：直接用 fetch（会丢失 token）
```

### 13.2 六大已知坑（必须记住！）

#### 坑 1：Flask 重启后端口被占用

**现象**：修改代码后重启 Flask 失败，`Address already in use`

**根因**：`taskkill` 可能遗漏后台进程；Flask `debug=False` 时模板被缓存

**解决**：
```bash
# 重启前必须执行
netstat -ano | findstr :7000
# 如果看到 LISTENING，手动结束
taskkill /PID <pid> /F
# 等待 2 秒再启动
```

#### 坑 2：config.yaml 被 PyYAML 污染

**现象**：启动时报 `yaml.constructor.ConstructorError: mentally valid`

**根因**：用 `yaml.dump(config, ...)` 写入 Config 对象，PyYAML 写入 `!!python/object/new:`

**解决**：**永远**用 `yaml.dump(dict(config), ...)`

```python
# ✅ 正确
yaml.dump(dict(config), f, default_flow_style=False, allow_unicode=True)

# ❌ 错误
yaml.dump(config, f)  # config 是 Config 对象
```

**已修复文件**：`web_app.py`（14 处）+ `evolution_manager.py`（1 处）

#### 坑 3：generate_response() 返回类型

**现象**：调用 `generate_response(stream=False)` 后按字符串处理，取不到内容

**根因**：该方法**始终返回生成器**，yield `{'type': 'text', 'content': '...'}`

**解决**：
```python
# ✅ 正确
for chunk in engine.generate_response(...):
    if chunk.get('type') == 'text':
        result += chunk.get('content', '')
    elif chunk.get('type') == 'error':
        error = chunk.get('content', '')

# ❌ 错误
result = engine.generate_response(...)  # 这是生成器，不是字符串
```

#### 坑 4：前端 mousedown vs click 竞态

**现象**：侧边栏按钮点击无反应，或需要点两次

**根因**：`mousedown` 触发时 DOM 尚未更新，`click` 已被取消

**解决**：统一用 `click` 事件，移除 `mousedown` 监听

> ✅ **已修复**（2026-06-27）

#### 坑 5：Skills JSON 注入条件

**现象**：协作模式下 本地通用模型（Ollama） 看不到工具描述

**根因**：注入条件 `use_external or self._query_mode == "direct"` 不覆盖 `auto + collab`

**解决**：条件改为 `self._query_mode in ("direct", "auto")`

> ✅ **已修复**（2026-06-27）

#### 坑 6：collabMode 未传递到后端

**现象**：前端切换协作按钮，后端无响应

**根因**：请求体缺少 `collab_mode` 字段

**解决**：`app-conversation.js` 的 `_sendMessageNow()` 加入 `collab_mode: collabMode || null`

> ✅ **已修复**（2026-06-27）

### 13.3 Git 提交规范

```
feat:     新功能
fix:      BUG 修复
docs:     文档更新
refactor: 重构（不改变功能）
test:     测试相关
chore:    构建/依赖更新
```

示例：`fix(engine): 修复协作模式响应解析错误（dict 生成器按字符串处理）`

---

## 14. 技术债务与路线图

### 14.1 当前技术债务（v6.0 更新）

| 债务 | 严重度 | 状态 |
|------|--------|------|
| 协作体系重构 | P0 | ✅ 已完成（v2.0） |
| 配方选择器冗余 | P1 | ✅ 已删除 |
| 垂直模型无引导 | P1 | ✅ 已完成（参数确认卡） |
| 内存多模型并行检查 | P1 | ✅ 已完成 |
| 长任务分片 | P2 | ✅ 基础设施完成，深度集成待做 |
| device_api / new_features_api 未改 Blueprint | P3 | 延后 |
| HarvenHub 一期 | P3 | 独立开发 |

### 14.1.1 债务状态（v6.0）

#### P0（必须修复）
| 项目 | 描述 | 状态 |
|------|------|------|
| `device_api.py` 路由注册方式 | 仍用 `register_xxx(app)`，非 Blueprint | ⚠️ 待修复 |
| `new_features_api.py` 路由注册方式 | 同上 | ⚠️ 待修复 |
| 首次启动隐私协议弹窗 | 法律合规要求 | ⚠️ 待实现 |
| ~~协作模式 本地通用模型（Ollama） 失败~~ | ~~已修复（三层兜底）~~ | ✅ 2026-06-26 |
| ~~os.startfile 路径问题~~ | ~~已修复（monkey-patch）~~ | ✅ 2026-06-26 |
| ~~音乐 Agent 误触发~~ | ~~已修复（泛化词移除）~~ | ✅ 2026-06-26 |

#### P1（重要）
| 项目 | 描述 | 状态 |
|------|------|------|
| 数据携带权 | 用户数据导出为 JSON/Markdown | ⚠️ 待实现 |
| AI 生成内容标识 | 界面提示「AI 生成内容请甄别」 | ⚠️ 待实现 |
| 单元测试覆盖率 | 当前 67 个测试，覆盖率 ~40% | ⚠️ 待提升 |
| 知识库向量索引优化 | FAISS 索引未持久化，每次重启重建 | ⚠️ 待优化 |

#### P2（一般）
| 项目 | 描述 | 状态 |
|------|------|------|
| Skill 热更新 | watchdog 可选导入，默认不强制 | ✅ 已实现（可选） |
| 多语言支持 | `tab-语言.html` 已保留但隐藏 | ⚠️ 待完善 |
| 日志轮转 | 当前日志无大小限制 | ⚠️ 待实现 |
| 性能监控 | 无内置性能监控 | ⚠️ 待实现 |

#### P3（低优先级）
| 项目 | 描述 | 状态 |
|------|------|------|
| HarvenHub 一期 | 独立开发，不在当前代码库 | ⚠️ 待启动 |
| 风格包隐私保护 | 上线前必须完成 | ✅ 方案已完成（2026-06-08） |
| IM 连接安卓客户端 | 当前仅完成服务端适配器 | ⚠️ 待实现 |

### 14.2 版本路线图

```
v0.1  ─ 框架搭建（Flask + 本地通用模型（Ollama） + 基础对话）
v0.2  ─ 知识库 + Skills + 三库隔离
v0.3  ─ 用户画像 + 记忆系统 + 结构化任务
v0.4  ─ 手机联动 + 硬层面安全
v0.5  ─ 安全加固（P0-P2）+ 工程优化
v0.6  ─ 硬件自检 + 模型推荐 + 多模型接入
v0.7  ─ 三种模式架构重构 + 协作模式
v0.8  ─ 数据保险箱 + 联网搜索取消 +  morality 诚实化
v0.9  ─ 全面自检 + BUG 修复 + 协作规则 + 文档完善（当前 ✅ 2026-06-26）
v1.0  ─ 正式发布（待 P0 债务清零）
```

### 14.3 已完成功能清单

- [x] Phase 1-4：对话 + 界面 + 记忆 + 用户画像
- [x] 三库物理隔离（知识库/记忆/结构化任务）
- [x] 结构化任务管理
- [x] 手机联动（mobile_connector.py）
- [x] 硬层面安全（hw_security.py）
- [x] 安全加固 P0-P2（SSRF/速率限制/代码沙箱/5 插件+3 技能）
- [x] 工程优化 #9-17（知识库 bug/PDF 导入/app.js 拆解/单元测试 43→67）
- [x] 硬件自检 & 模型推荐（hardware_detector.py，38 个模型）
- [x] 多模型接入（28 个厂商，DeepSeek/Kimi/元宝等）
- [x] 三种查询模式（⚡自动/🏠本地/🤖外脑）
- [x] 协作模式（collab_manager.py + collab_routes.py）
- [x] 数据保险箱（vault_routes.py + 三态隐私）
- [x] 受控进化模块（evolution_manager.py + 提案系统）
- [x] **v0.9 全面自检与修复（2026-06-26）**：
  - [x] 音乐 Agent 误触发修复（泛化词移除）
  - [x] 协作模式 本地通用模型（Ollama） 失败修复（三层兜底）
  - [x] FOREIGN KEY 自动恢复（数据库 WAL 丢失防御）
  - [x] os.startfile() monkey-patch（Windows 相对路径修复）
  - [x] 无围栏代码提取（Layer 2 fallback）
  - [x] tool_result 前端事件处理
  - [x] 术语统一：本地通用模型（Ollama） → 本地模型
  - [x] 协作模式三大规则（简单命令/专业模型/诚实）
  - [x] 磁盘清理：3.3GB 冗余数据删除
  - [x] 根目录清理：6 个测试文件归档
  - [x] 配置补全：default_model + tool_execution_enabled
- [x] 统计模块（stats_manager.py）
- [x] 保底机制（fallback_manager.py）
- [x] 知识萃取（extraction_manager.py）
- [x] 品牌定制（外观 Tab）
- [x] IM 连接（企业微信/钉钉/飞书/QQ 适配器）
- [x] 设置面板全面修复（8 主 Tab + 子导航）
- [x] index.html 拆分为 32 个 include 文件
- [x] 框架底层重构（路由统一/ sys.path 清理/配置统一）
- [x] 全面自检 + 3 严重 BUG 修复

---

## 15. 测试与质量保证

### 15.1 单元测试

**测试文件**：`tests/test_core.py`（单文件 64 个测试，覆盖率 ~40%）

**运行测试**：
```bash
cd H:\HarvenAI
.venv/Scripts/python.exe -m pytest tests/ -v
```

**主要测试模块**（集中在 `test_core.py`）：
| 测试类 | 测试内容 |
|-----------|----------|
| `TestSSRF` | URL 白名单/IP 阻止 |
| `TestCodeSandbox` | 危险代码检测（os.system/subprocess/eval） |
| `TestIsolatedDB` | 三库分类（隐私/过渡/商用） |
| `TestRateLimit` | 速率限制/IP 频控 |
| `TestFileSecurity` | 上传文件类型过滤 |
| `TestMemory` | 记忆提取/偏好规则 |
| `TestConfigSharder` | 配置文件分片/重载 |
| `TestHWSecurity` | 设备指纹/安全状态 |
| `TestMemoryDreaming` | 记忆梦境/离线整合 |
| `TestOllamaDaemon` | Ollama 守护进程 |

### 15.2 API 测试清单

**手动测试工具**：`curl` 或 Postman

**关键测试场景**：
```
✅ 首次启动流程（/first_run → 硬件检测 → 模型推荐）
✅ 查询模式切换（🏠本地 / 🤖外脑 / 自动路由）
✅ 协作模式切换（🚫关闭 / ☁️云地 / 🏠双模）
✅ 隐私三档（严格/平衡/全量）
✅ 知识库上传/搜索/删除
✅ 记忆系统（主动/被动/摘要）
✅ 进化提案创建/审批/回滚
✅ 数据保险箱（锁定/解锁/三态隐私）
✅ 外部模型配置（28 个厂商）
✅ 管理员登录/改密
```

### 15.3 自检清单（每次发版前）

- [ ] `netstat -ano | findstr :7000` 确认无残留进程
- [ ] 配置文件 `config.yaml` 未被 PyYAML 污染（检查是否有 `!!python/object/new:`）
- [ ] 前端 `Ctrl+Shift+R` 强制刷新，确认 JS 模块加载最新版本
- [ ] 本地模式 + 外脑模式各测试一次对话
- [ ] 协作两模式测试（云地协作 + 本地双模）
- [ ] 知识库上传一个 PDF + 一个 TXT，验证搜索
- [ ] 数据保险箱锁定/解锁流程
- [ ] 进化提案创建 → 审批 → 应用
- [ ] 管理员后台登录 → 改密
- [ ] 日志文件无异常错误

---

## 16. 附录

### 16.1 关键文件速查表

| 文件 | 作用 | 修改频率 |
|------|------|----------|
| `src/core/engine.py` | 核心引擎，三种模式调度 | 高 |
| `src/core/morality.py` | 道德内核 | 低 |
| `src/core/independence.py` | 独立性防御 | 低 |
| `src/core/collab_manager.py` | 协作模式管理 | 中 |
| `src/utils/config.py` | 配置管理 | 中 |
| `web_app.py` | Flask 入口，Blueprint 注册 | 中 |
| `config.yaml` | 用户配置 | 高（用户修改） |
| `data/sqlite/lingzhi.db` | 主数据库 | 高 |
| `templates/index.html` | 主界面（32 个 include） | 低 |
| `static/js/app.js` | JS 入口 | 低 |
| `static/js/modules/app-conversation.js` | 对话模块 | 高 |
| `skills/` | 用户 Skills | 中 |
| `docs/HarvenAI_完整开发文档_v6.md` | 本文档 | 高 |

### 16.2 配置参数速查

#### config.yaml 主要配置项

```yaml
# 应用
app:
  name: Harven AI
  language: zh-CN

# 模型
default_model: qwen2.5:14b          # 默认模型
normal_model: qwen2.5:7b            # 普通任务模型
current_model: local:musicgen-small  # 当前选中模型

# Ollama
ollama:
  base_url: http://localhost:11434
  default_model: deepseek-r1:14b

# 协作模式
collab_mode: "off"                  # off | cloud_local | local_dual
collab_foreign_model: deepseek      # 协作外脑选择
collab_privacy_level: full          # strict | balanced | full

# 存储路径
storage:
  sqlite: data/sqlite/lingzhi.db
  knowledge: data/knowledge/
  faiss: data/faiss/
  conversations: data/conversations/

# 安全
security:
  admin_password: ''                # 首次运行时自动设置
  remote_password: harven2026       # 远程访问密码
  injection_detection: true

# 联网搜索
web_search:
  enabled: true
  default_query_mode: auto
  default_search_engine: bing
  auto_search: false

# 外部模型密钥（用户自行配置）
model_keys:
  deepseek: ""                      # DeepSeek API Key
  doubao: ""                        # 豆包 API Key
  kimi: ""                          # Kimi API Key
```

### 16.3 常用命令速查

```bash
# 启动
cd H:\HarvenAI && .venv\Scripts\python.exe web_app.py

# 重启（安全）
netstat -ano | findstr :7000
taskkill /PID <pid> /F
ping -n 3 127.0.0.1 > NUL
.venv\Scripts\python.exe web_app.py

# 查看 Ollama 模型
curl http://localhost:11434/api/tags

# 拉取模型
ollama pull qwen2.5:7b

# 测试 API
curl -X POST http://127.0.0.1:7000/api/chat -H "Content-Type: application/json" \
  -d "{\"message\":\"你好\",\"query_mode\":\"auto\"}"

# 查看日志
type H:\HarvenAI\logs\harven_*.log | more

# 清理缓存
find H:\HarvenAI -name "__pycache__" -type d -exec rm -rf {} +

# 导出数据库
sqlite3 data/sqlite/lingzhi.db .dump > backup.sql

# 运行测试
.venv\Scripts\python.exe -m pytest tests/ -v
```

### 16.4 故障排查指南

| 故障 | 现象 | 原因 | 解决方案 |
|------|------|------|----------|
| Flask 启动失败 | `Address already in use` | 旧进程未退出 | `netstat -ano \| findstr :7000` → `taskkill /PID <pid> /F` |
| 配置损坏 | `yaml.constructor.ConstructorError` | PyYAML 污染 | 手动编辑 `config.yaml`，删除 `!!python/object/new:` 行 |
| 知识库搜索无结果 | 返回空列表 | FAISS 索引未构建 | 重启 Flask，等待索引重建（查看日志） |
| 本地通用模型（Ollama） 连接失败 | `Connection refused` | 本地通用模型（Ollama） 未启动 | 启动 本地通用模型（Ollama）：`ollama serve` |
| 外部模型调用失败 | `401 Unauthorized` | API Key 错误 | 检查 `config.yaml` 中对应模型的 `api_key` |
| 前端无响应 | 按钮点击无反应 | JS 缓存 | `Ctrl+Shift+R` 强制刷新 |
| 协作模式无效果 | 外脑不返回分析 | BUG-1（已修复） | 确认 `engine.py` 已更新到最新版本 |
| 数据保险箱锁定失败 | 锁定后仍可读写 | 前端状态未同步 | 刷新页面，重新加载配置 |

### 16.5 版本历史

| 版本 | 日期 | 核心变更 |
|------|------|----------|
| v1.2 | 2026-06-27 | 完整开发文档 v7.0（新增 🏠 双模配置面板 + 安装包重建） |
| v1.1 | 2026-06-26 | 完整开发文档 v6.0（协作体系 v2.0 重构 + 全面检测修复） |
| v1.0 | 2026-06-13 | 完整开发文档 v4.0（超详细版） |
| v0.9 | 2026-06-13 | 全面自检 + 3 严重 BUG 修复 + 数据保险箱 |
| v0.8 | 2026-06-12 | 框架底层重构 + 模式架构重构 |
| v0.7 | 2026-06-09 | 硬件自检 + 模型推荐 + 多模型扩展（28 厂商） |
| v0.6 | 2026-06-08 | 安全加固 + 工程优化 + 设置面板修复 |
| v0.5 | 2026-06-07 | 硬层面安全 + 手机联动 |
| v0.4 | 2026-06-06 | 用户画像 + 记忆系统 |
| v0.3 | 2026-06-05 | 知识库 + Skills + 三库隔离 |
| v0.2 | 2026-06-04 | 基础对话 + Flask 框架 |
| v0.1 | 2026-06-03 | 项目初始化 |

---

**文档结束**

*Harven AI 完整开发文档 v7.0*
*最后更新：2026-06-27*
*作者：老谭 & Harven AI*
*版本：v7.0（🏠 双模配置面板）*

---

> **使用说明**：本文档是 Harven AI 的单一事实来源（SSOT），任何设计变更必须先更新本文档，再修改代码。「文档先行，代码跟随」是 Harven AI 开发的基本原则。

