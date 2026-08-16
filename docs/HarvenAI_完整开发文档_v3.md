# Harven AI 完整开发文档 v3.0

> **版本**: 2026-06-13 | **作者**: 老谭 & Harven AI | **许可**: Apache 2.0

---

## 目录

1. [项目概述](#1-项目概述)
2. [核心架构设计](#2-核心架构设计)
3. [三种查询模式详解](#3-三种查询模式详解)
4. [协作模式（自动模式子模式）](#4-协作模式自动模式子模式)
5. [安全防御体系](#5-安全防御体系)
6. [功能模块详解](#6-功能模块详解)
7. [数据流与逻辑关系](#7-数据流与逻辑关系)
8. [API 路由全览](#8-api-路由全览)
9. [前端架构](#9-前端架构)
10. [数据库设计](#10-数据库设计)
11. [部署运维](#11-部署运维)
12. [法律合规与侵权风险分析](#12-法律合规与侵权风险分析)
13. [开发规范与已知坑](#13-开发规范与已知坑)
14. [技术债务与未来路线](#14-技术债务与未来路线)
15. [附录](#15-附录)

---

## 1. 项目概述

### 1.1 基本信息

| 项目 | 内容 |
|------|------|
| **产品名** | Harven AI（曾用名：灵识 → 本诚 → Harven IA） |
| **定位** | 纯本地运行的私有 AI 助手 |
| **商业模式** | **永久免费**，不做付费会员 |
| **技术栈** | Flask + Bootstrap 5 + Ollama + FAISS + SQLite + PySide6(LGPL) |
| **适用硬件** | 通用个人 AI（同 OpenClaw），不限特定配置 |
| **首次启动** | 自动硬件自检 → 模型推荐 → 引导下载合适的 Ollama 模型 |
| **硬件检测** | `hardware_detector.py`（CPU/RAM/GPU-VRAM，21 个模型数据库） |
| **推荐引擎** | `model_recommend.py`（根据检测结果推荐最佳本地模型） |
| **Python** | 3.13.12（虚拟环境 `H:\HarvenAI\.venv\`） |
| **启动方式** | `H:\HarvenAI\启动HarvenAI.bat` 或桌面快捷方式 |
| **项目路径** | `H:\HarvenAI\` |

### 1.2 项目结构

```
H:\HarvenAI\
├── web_app.py                 ← 主 Flask 入口（唯一启动文件）
├── config.yaml                ← 全局配置文件
├── src/
│   ├── core/                  ← 60+ 核心模块（引擎/安全/记忆/知识等）
│   │   ├── engine.py          ← 对话引擎核心（三种模式 + 协作）
│   │   ├── morality.py        ← 功德行道德内核
│   │   ├── independence.py    ← 五层独立性防御
│   │   ├── collab_manager.py  ← 协作模式管理器
│   │   ├── vault_routes.py    ← 数据保险箱
│   │   ├── knowledge_base.py  ← FAISS 知识库
│   │   ├── memory.py          ← 记忆管理系统
│   │   ├── adapters/          ← IM 平台适配器（企微/钉钉/飞书/QQ）
│   │   └── drivers/           ← IoT 设备驱动（MQTT/HTTP/WebSocket）
│   ├── models/                ← 模型管理器
│   ├── plugins/               ← 插件沙盒
│   ├── utils/                 ← 工具类（配置/日志等）
│   └── ui/                    ← PySide6 桌面 GUI
├── static/
│   ├── css/style.css          ← 主样式表
│   ├── js/modules/            ← 22 个 JS 业务模块
│   └── icons/                 ← PWA 多尺寸图标（鸭子图标）
├── templates/
│   ├── index.html             ← 主界面（拆分为 30+ 个 include）
│   └── includes/              ← 30+ 个设置面板、模态框片段
├── data/
│   ├── sqlite/lingzhi.db      ← 主数据库
│   ├── faiss/                 ← FAISS 向量索引
│   ├── knowledge/             ← 知识库文件
│   ├── conversations/         ← 对话历史
│   ├── snapshots/             ← 记忆快照
│   └── isolated_dbs/          ← 三库隔离（隐私/过渡/商用）
├── skills/                    ← 内置 Skill 文件（Markdown 格式）
├── plugins/                   ← 5 个内建插件
├── tests/                     ← 67 个单元测试
├── docs/                      ← 项目文档
└── archive/                   ← 历史脚本归档
```

### 1.3 核心设计原则

| 原则 | 说明 |
|------|------|
| **纯本地优先** | 默认使用本地 Ollama 模型，数据不上传 |
| **隐私第一** | 三库物理隔离 + AES-256 加密 + 隐私模式 |
| **独立性** | 五层防御，不与其他实例通信，无 telemetry |
| **永久免费** | 不做会员、不内置付费墙、所有功能免费 |
| **开源合规** | Apache 2.0 许可，LGPL 库静态链接隔离 |
| **用户控制** | 受控进化，AI 提案必须用户审批才能生效 |

---

## 2. 核心架构设计

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                      前端展示层                              │
│  index.html ← includes/ ← CSS ← JS modules(22个) ← vendor  │
├─────────────────────────────────────────────────────────────┤
│                      HTTP API 层                            │
│  web_app.py (Flask) + Blueprint 路由 (collab/evolution/..)  │
├──────────┬──────────┬──────────┬──────────┬────────────────┤
│  对话引擎  │  道德内核  │  独立性   │  安全防护  │  记忆管理      │
│  engine   │ morality │independ..│hw_security│ memory/dream..│
├──────────┼──────────┼──────────┼──────────┼────────────────┤
│  知识库    │  模型管理  │  设备管理  │  协作模式  │  IM 连接       │
│  knowl..  │ model_.. │ device_..│ collab_.. │ im_connector  │
├──────────┴──────────┴──────────┴──────────┴────────────────┤
│                      数据存储层                              │
│  SQLite (三库隔离) + FAISS 向量库 + 文件系统 + AES-256 加密  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 请求处理流水线

```
用户输入
    │
    ▼
┌── Flask before_request 中间件 ──────────────────────────────┐
│  1. 速率限制检查 (Token Bucket)                              │
│  2. 独立性检查 (API 来源验证)                                 │
│  3. 道德内核粗筛 (关键词匹配)                                 │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌── 对话引擎 (ConversationEngine.send) ───────────────────────┐
│  0. 锁机状态检查                                              │
│  1. 道德内核意图复核 (LLM 复核，可选讨论模式)                   │
│  2. _stream_three_stage() 三段式查询：                        │
│     Step 0: GitHub/浏览器 URL 检测                            │
│     Step 0b: Active Memory 主动记忆检索                       │
│     Step 1: FAISS 知识库搜索                                  │
│     Step 2: [联网搜索已取消]                                  │
│     Step 3: 模型路由 → 系统提示词构建 → AI 推理               │
│  3. 结果保存到 ConversationDatabase                          │
│  4. 记忆自动保存/更新                                         │
│  5. 用户画像自动更新                                          │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 模型管理器架构

```
┌────────────────────────────────────┐
│         MultiModelManager          │
│        (统一入口 generate_response)  │
├────────────────────────────────────┤
│  _generate_ollama     ← 本地 Ollama │
│  _generate_openai     ← OpenAI API  │
│  _generate_generic    ← 通用 API    │
├──────────┬──────────┬──────────────┤
│  元宝     │  DeepSeek │  OpenAI     │
│  百川     │  Kimi    │  Claude     │
│  智谱     │  零一万物  │  Gemini     │
│  阶跃星辰  │  MiniMax │  Grok       │
│  文心一言  │  讯飞星火  │  Groq       │
│  天工     │  硅基流动  │  Mistral    │
│  ...      │  ...     │  ...        │
└──────────┴──────────┴──────────────┘
  总计 28 个厂商支持（不内置 API Key，用户自行填入）
```

---

## 3. 三种查询模式详解

### 3.1 模式定义

| 模式 | 英文键 | 图标 | 核心组成 | 回答者 | 数据外传 |
|------|--------|------|----------|--------|----------|
| **自动模式** | `auto` | ⚡ | 框架 + Ollama + 知识库 + Skills + 外脑(可选) | 外部模型（整合） | 仅协作时去敏 |
| **本地模式** | `kb_only` | 🏠 | Ollama + 知识库 + Skills（纯本地） | Ollama | **绝不外传** |
| **外脑模式** | `direct` | 🤖 | 外部模型 + Skills | 外部模型 | 外脑接收问题 |

### 3.2 模式选择流程图

```
用户选择模式
    │
    ├─ ⚡ 自动模式 ──→ 分类 → FAISS 知识库 → Active Memory
    │       │              │
    │       │         模型路由: Ollama? 外部?
    │       │              │
    │       │         ┌────┴────────────┐
    │       │      Ollama可用      外部可用?
    │       │        │              │
    │       │        │         ┌────┴────┐
    │       │        │        是         否
    │       │        │        │          │
    │       │        │    collaboration  ⚠️提示
    │       │        │    子模式检查       │
    │       │        │        │          │
    │       │        │   ┌────┴────┐    │
    │       │        │  已启用    未启用  │
    │       │        │   │        │     │
    │       │        │  外脑分析  Ollama │
    │       │        │   │       直接回答 │
    │       │        │   │        │     │
    │       │        │   └───┬────┘     │
    │       │        │       ↓          │
    │       │        │  Ollama 整合回答  │
    │       │        │       │          │
    │       │        └───────┴──────────┘
    │       │                ↓
    │       │          完整功德行提示词
    │       │          + 记忆/画像/任务
    │       │                ↓
    │       │          回答 → 前端
    │
    ├─ 🏠 本地模式 ──→ FAISS 知识库 + Active Memory
    │       │              │
    │       │          强制 Ollama（忽略外部模型）
    │       │          完整功德行提示词
    │       │          + 记忆/画像/任务
    │       │              ↓
    │       │          回答 → 前端
    │
    └─ 🤖 外脑模式 ──→ 模型路由到外部
            │              │
            │         外部可用? 
            │         ┌────┴────┐
            │        是         否
            │        │          │
            │     外部模型     ⚠️回退通知
            │     + Skills    ↓
            │     (JSON工具)  Ollama
            │        │          │
            │     身份提示词   身份提示词
            │     (最小化)    (最小化)
            │        │          │
            │        └────┬─────┘
            │             ↓
            │         回答 → 前端
```

### 3.3 三段式查询详细流程（`_stream_three_stage`）

#### Step 0: 预处理

| 子步骤 | 功能 | 适用模式 | 说明 |
|--------|------|----------|------|
| 0a | GitHub URL 检测 | 全部 | 检测 GitHub 链接 → 自动下载仓库 → 导入知识库 |
| 0b | 浏览器自动化检测 | auto | Playwright 解析意图 → 执行浏览器任务 → 结果注入 |
| 0c | Active Memory 检索 | auto / kb_only | 从记忆库检索相关历史 → 注入 enhanced_context |

#### Step 1: FAISS 知识库搜索

```python
# 仅在 auto / kb_only 模式下执行
# direct 模式跳过：保持外脑纯净
kb_results = kb.search(user_message, top_k=3, threshold=0.2)
→ enhanced_context += "[增强信息——知识库]\n..."
```

#### Step 2: 联网搜索（已取消）

> 2026-06-13 决定：取消联网搜索。Playwright 保留为独立功能，不自动触发。

#### Step 3: 模型路由 + AI 推理

```python
use_external, model_label, model_id = self._route_model_for_query(self._query_mode)
```

| 模式 | 路由逻辑 |
|------|----------|
| `auto` | 检查 multi_models.json current_model → 决定使用 Ollama 或外部 |
| `kb_only` | 强制 `use_external=False`，走 Ollama |
| `direct` | 强制 `use_external=True`，无外部则回退 + 通知 |

#### 系统提示词构建差异

| 提示词组成 | auto | kb_only | direct |
|-----------|------|---------|--------|
| 身份段落 | IDENTITY_PROMPT | IDENTITY_PROMPT | IDENTITY_PROMPT |
| 功德行道德内核 | ✅ 完整 | ✅ 完整 | ❌ 不含 |
| 知识库结果 | ✅ 注入 | ✅ 注入 | ❌ 不含（外脑看不到） |
| 记忆上下文 | ✅ 注入 | ✅ 注入 | ❌ 不含 |
| 用户画像 | ✅ 注入 | ✅ 注入 | ❌ 不含 |
| 任务列表 | ✅ 注入 | ✅ 注入 | ❌ 不含 |
| Skills JSON 工具 | ✅ 自动/协作模式都注入 | ✅ 注入 | ✅ 注入 |

### 3.4 Skills 工具注入机制

```python
# auto / kb_only 模式：system prompt 文本注入
system_prompt += skills_manager.build_skills_prompt()

# 外脑模式：JSON 格式工具描述注入
# 让外部模型知道有哪些本地工具可用，可以请求调用
collab_manager.build_tools_prompt(skills_manager, user_message)
→ 返回 JSON 工具列表 + 调用格式说明
→ 注入到外脑的 system prompt 中
```

---

## 4. 协作模式（自动模式子模式）

### 4.1 基本定义

| 项目 | 内容 |
|------|------|
| **所属模式** | 仅 ⚡ 自动模式（auto）的子模式 |
| **触发条件** | 用户开启 `collab_enabled` + 选择 `collab_foreign_model` |
| **角色分工** | Ollama = **执行者/整合者**；外部模型 = **外脑/参谋** |
| **硬限制** | **只能 1 个外部模型**，禁止 2 个外部模型同时工作 |
| **默认外脑** | 豆包（doubao） |
| **配置位置** | `config.yaml` → `collab_foreign_model` / `collab_enabled` / `collab_privacy_level` |

### 4.2 工作流程

```
用户提问
    │
    ▼
Ollama 收到问题
    │
    ├─→ 查本地知识库 (FAISS)
    ├─→ 查本地记忆 (Active Memory)
    │
    ├─→ 将问题发送给外脑（按隐私级别过滤）
    │        │
    │        ├─ strict:  仅传问题摘要（去人名/地名/数字等）
    │        ├─ balanced: 传去敏后的问题
    │        └─ full:    传完整问题 + 上下文
    │        │
    │   外脑返回：分析 + JSON 工具调用（可选）
    │        │
    │   外脑无响应？⚠️ 回退通知 → Ollama 独立回答
    │        │
    ├─→ Ollama 整合：本地知识 + 外脑分析
    │        │
    │   Ollama 回答 → 用户
    │        │
    │   如有工具调用 → 本地执行 → 结果追加
```

### 4.3 隐私保护三档

| 级别 | 键值 | 效果 | 适用场景 |
|------|------|------|----------|
| **严格** | `strict` | 仅传问题摘要，移除所有细节 | 完全不信任的外脑 |
| **平衡** | `balanced` | 替换敏感信息（手机号→[手机号]、身份证→[ID号]等） | 默认级别 |
| **全量** | `full` | 传全部数据，不做过滤 | 完全信任的外脑 |

### 4.4 外脑断开处理

```python
# engine.py L810-818: 外脑回退通知
if not use_external and self._query_mode in ("direct", "auto"):
    yield ("notification", {
        "type": "foreign_model_fallback",
        "message": f"⚠️ 外脑（{model_label}）不可用，已切换为本地 Ollama",
    })
    yield ("thinking", f"🤖 外脑模式 · {fallback_msg}")
```

---

## 5. 安全防御体系

### 5.1 五大安全体系总览

| 体系 | 核心文件 | 功能 |
|------|----------|------|
| 🔐 **数据保险箱** | `vault_routes.py` | 密码锁定、隐私模式、访问日志 |
| 🛡️ **独立性防御** | `independence.py` | 五层防御防止同化/兼并/集群化 |
| 💎 **道德内核** | `morality.py` | 意图复核、分级响应、防滥用 |
| 🔒 **硬件安全** | `hw_security.py` | 设备指纹、绑定/解绑、防克隆 |
| 📦 **三库隔离** | `isolated_db.py` | 隐私/过渡/商用三库物理隔离 + AES-256 |

### 5.2 独立性五层防御（independence.py）

| 层级 | 名称 | 机制 |
|------|------|------|
| **L1** | 网络出站白名单 | requests 库 MonkeyPatch 拦截，仅允许 config 中配置的域名 |
| **L2** | gRPC/分布式协议拦截 | 启动时检测 Ollama 集群/分布式配置 |
| **L3** | API 请求来源验证 | Flask `before_request` 中间件，拒绝非本机/非 Token 请求 |
| **L4** | 知识库注入防御 | 导入时 LLM 复核内容倾向性 |
| **L5** | 设备强制绑定 | 首次启动强制设备指纹，不允许跳过 |

**白名单原理**：
```python
# 仅这些域名可通过 requests 访问
allowed_domains = {
    "localhost", "127.0.0.1",  # 本地
    "ollama.ai", "huggingface.co", "modelscope.cn",  # 模型下载
    "api.deepseek.com", "api.openai.com", ...,  # 外部模型 API
    "harvenhub.com",  # 未来社区
}
```

### 5.3 功德行道德内核（morality.py v2.2）

#### 三层结果定义

| 结果 | 行为 |
|------|------|
| **harmless** | 正常放行 |
| **ambiguous** | 进入讨论模式，询问用户意图；累计 3 次自动升级为 harmful |
| **harmful** | 不执行 + 锁住（窗口制：30分→2小时→24小时） |

#### 处理流程

```
用户输入
    │
    ▼
关键词粗筛 ──→ 匹配高危关键词？──→ 否：正常放行
    │
   是
    │
    ▼
LLM 意图复核（使用 Ollama 做判断）
    │
    ├── harmless → 放行
    ├── ambiguous → 讨论模式（3次升级）
    └── harmful → 拒绝 + 锁死
```

#### 锁死机制

| 违规次数 | 锁死时长 | 到期行为 |
|----------|----------|----------|
| 第 1 次 | 30 分钟 | 自动解除，计数重置 |
| 第 2 次 | 2 小时 | 自动解除，计数重置 |
| 第 3 次 | 24 小时 | 手动解除需管理员 |

### 5.4 硬件安全（hw_security.py）

```python
# 设备指纹 = SHA256(CPU序列号 + 主板序列号 + MAC地址 + 磁盘序列号)
fingerprint = sha256(cpu_serial + motherboard + mac + disk_serial)

# 防克隆检测：指纹变更 → 触发警告
if fingerprint_hash != stored_hash:
    log_security_event("fingerprint_changed", severity="warning")
```

### 5.5 数据保险箱（vault_routes.py）

| API | 功能 |
|-----|------|
| `GET  /api/vault/status` | 查询锁定状态 |
| `POST /api/vault/lock` | 密码锁定所有对话 |
| `POST /api/vault/unlock` | 验证密码解锁 |
| `POST /api/vault/set-privacy-mode` | 切换隐私模式 |
| `GET  /api/vault/logs` | 查看访问日志 |
| `GET  /api/vault/stats` | 保险箱统计 |

### 5.6 其他安全模块

| 模块 | 功能 |
|------|------|
| `rate_limiter.py` | Token Bucket 速率限制，防 DDoS |
| `code_sandbox.py` | Docker 子进程隔离执行代码，超时机制 |
| `file_scanner.py` | Magic Bytes 校验、ClamAV 病毒扫描 |
| `plugin_system.py` | 插件沙盒框架，独立命名空间运行 |
| `device_binding.py` | 多因素校验（MAC/CPU/主板） |

---

## 6. 功能模块详解

### 6.1 核心引擎模块

#### engine.py — 对话引擎核心 v2.0

```
ConversationEngine
├── QUERY_MODES: {"auto": "⚡自动", "kb_only": "🏠本地", "direct": "🤖外脑"}
├── send(user_message, stream, query_mode) → 主入口
│   └── 道德检查 → _stream_three_stage()
├── _classify_query() → 分类器（目前保留框架，联网功能已取消）
├── _resolve_query_mode() → 根据模式决定搜索行为
├── _route_model_for_query() → 智能路由（Ollama or 外部）
├── _stream_three_stage() → 三段式查询
│   ├── GitHub/浏览器 URL 检测
│   ├── Active Memory 检索
│   ├── FAISS 知识库搜索
│   └── 系统提示词构建 + AI 推理
├── _build_system_prompt() → 根据模式构建不同范围的提示词
├── _invoke_collab_foreign() → 协作模式调用外脑
└── 记忆保存 / 画像更新
```

### 6.2 知识管理模块

| 模块 | 文件 | 功能 |
|------|------|------|
| **本地知识库** | `knowledge_base.py` | FAISS 向量检索，支持 PDF/Word/Markdown/TXT |
| **知识萃取** | `extraction_manager.py` | 从对话/文档提取信息、去重合并 |
| **知识导入** | API `/api/knowledge/upload` | 支持 GitHub URL 自动下载 → 扫描 → 导入 |
| **元数据分片** | `meta_sharder.py` | `knowledge_meta.json` 自动拆分，防单文件过大 |

### 6.3 记忆系统模块

| 模块 | 功能 |
|------|------|
| `memory.py` | 五重防失忆机制：快照/摘要/向量/关键词/时间轴 |
| `memory_dreaming.py` | 记忆巩固三阶段：Light Dream → Deep Dream → REM |
| `memory_eviction.py` | FIFO/重要性/相似度去重/时间衰减 |
| `active_memory.py` | 主动记忆检索，对话时自动拉取历史记忆/偏好/结论 |
| `profile_auto_updater.py` | 用户画像 6 维 + 4 复合维度自动更新 |

### 6.4 多模型管理模块

| 模块 | 功能 |
|------|------|
| `model_manager.py` | Ollama 本地模型调用（OpenAI 兼容 SDK） |
| `multi_model_manager.py` | 多模型统一入口，28 个厂商支持 |
| `model_recommend.py` | 根据硬件推荐 Ollama 模型 |
| `hardware_detector.py` | CPU/RAM/GPU-VRAM 检测，21 个模型数据库 |
| `ollama_daemon.py` | Ollama 进程守护，自动检测/启动/重启 |

### 6.5 自动化模块

| 模块 | 功能 |
|------|------|
| `scheduler.py` | APScheduler 定时任务（Cron 表达式） |
| `background_task_manager.py` | 专项培养后台任务并行执行 |
| `specialization_executor.py` | 专项培养后台执行引擎 |
| `planner.py` | 任务编排引擎 |
| `workflow.py` | 多步骤任务编排管道 |

### 6.6 设备 / IoT 模块

| 模块 | 功能 |
|------|------|
| `device_manager.py` | 设备注册/发现/调度/权限 |
| `device_drivers.py` | MQTT/HTTP/Zigbee 统一接口 |
| `device_starter.py` | 系统基础设备自动注册 |
| `ai_generated_driver.py` | 为未知设备动态生成驱动代码 |
| `drivers/mqtt_driver.py` | MQTT 协议，兼容 HomeAssistant |
| `drivers/http_driver.py` | HTTP REST 驱动，兼容 Tesla API 等 |
| `drivers/websocket_driver.py` | WebSocket 实时遥测 |

### 6.7 通信 / 连接模块

| 模块 | 功能 |
|------|------|
| `mobile_connector.py` | 手机联动：Token + 二维码 + 代理对话 |
| `im_connector.py` | 企业微信/钉钉/飞书/QQ 统一接口 |
| `adapters/wecom.py` | 企业微信群机器人 |
| `adapters/dingtalk.py` | 钉钉群机器人（HMAC-SHA256） |
| `adapters/feishu.py` | 飞书自定义机器人 |
| `adapters/qq.py` | QQ 官方 Bot API |
| `channel_manager.py` | 多渠道接入框架 |
| `visitor_manager.py` | 访客管理：访问码校验 + 统计 |

### 6.8 媒体生成模块

| 模块 | 功能 |
|------|------|
| `image_gen.py` | 腾讯混元/DALL-E/SD 兼容 API |
| `image_processor.py` | Ollama 多模态图片识别 |
| `video_processor.py` | FFmpeg 抽帧 + 多模态视频分析 |
| `voice_input.py` | 录音 → 语音转文字 |

### 6.9 插件 / Skills 模块

| 模块 | 功能 |
|------|------|
| `skills_manager.py` | Skills 加载/应用/管理（Markdown 格式） |
| `skill_builder.py` | 自然语言 → AI 生成 Skill |
| `skill_discovery.py` | 对话未匹配时自动搜索 Resource Hub |
| `plugin_system.py` | 插件沙盒框架 |
| `plugin_installer.py` | GitHub 搜索/下载/安全扫描/哈希校验 |
| `resource_hub.py` | 全网资源发现，多渠道聚合搜索 |

### 6.10 监控 / 进化 / 统计模块

| 模块 | 功能 |
|------|------|
| `monitor.py` | Topic 监控，自动抓取新闻/插件 |
| `evolution_manager.py` | 受控进化：提案 → 审批 → 应用/回滚 |
| `fallback_manager.py` | 保底机制：知识库/模型/响应/启动 |
| `stats_manager.py` | 使用/性能/资源/行为统计 |
| `conversation_archiver.py` | 超 30 天未活跃旧对话自动归档 |
| `biliup_manager.py` | B 站视频上传 |

### 6.11 多 Agent / 协作模块

| 模块 | 功能 |
|------|------|
| `multi_agent.py` | Agent Bus/注册表/任务分发 |
| `orchestrator.py` | 多模型协作框架：任务分析→模型选择→分发执行 |
| `collab_manager.py` | 协作模式管理器：Ollama + 外脑混合工作 |

---

## 7. 数据流与逻辑关系

### 7.1 主数据流

```
用户输入
    │
    ▼
Flask: /api/chat
    │
    ├── before_request 中间件
    │   ├── 速率限制 (rate_limiter)
    │   └── 独立性检查 (independence)
    │
    ▼
ConversationEngine.send()
    │
    ├── 道德检查 (morality.analyze_intent)
    │   ├── harmful  → 拒绝 + 锁死
    │   ├── ambiguous → 讨论模式
    │   └── harmless  → 继续
    │
    ├── 分类 (_classify_query)
    │
    ├── _stream_three_stage()
    │   ├── [GitHub URL] → github_downloader
    │   ├── [浏览器意图] → browser_agent
    │   ├── [Active Memory] → active_memory
    │   ├── [知识库搜索] → knowledge_base (FAISS)
    │   ├── [模型路由] → _route_model_for_query
    │   ├── [协作外脑] → collab_manager._invoke_collab_foreign
    │   ├── [Skills 注入] → skills_manager
    │   └── [AI 推理] → model_manager / multi_model_manager
    │
    ├── 结果保存 → ConversationDatabase
    ├── 记忆更新 → memory / memory_dreaming
    └── 画像更新 → profile_auto_updater
    │
    ▼
SSE 流式返回前端
    ├── type: "thinking"    — 思考过程
    ├── type: "text"        — 回答文本
    ├── type: "source"      — 知识来源
    ├── type: "notification" — 通知（外脑断开等）
    └── type: "error"       — 错误
```

### 7.2 知识库数据流

```
文件上传 (PDF/Word/MD/TXT)
    │
    ▼
file_scanner (Magic Bytes + 病毒扫描)
    │
    ▼
文本提取 (content_reader for PDF, python-docx for Word)
    │
    ▼
分块 (Chunk 1000 chars, overlap 100)
    │
    ▼
向量化 (Ollama embeddings)
    │
    ▼
FAISS 索引存储 (data/faiss/knowledge.index)
    │
    ▼
元数据记录 (data/faiss/knowledge_meta.json)
    │
    ▼
meta_sharder (定期拆分大元数据文件)
```

### 7.3 记忆系统数据流

```
对话结束
    │
    ▼
memory.save_snapshot()  ← 保存快照 (data/snapshots/)
    │
    ▼
memory_dreaming 定时触发
    ├── Light Dream (每5对话)   → 摘要提取
    ├── Deep Dream (每20对话)   → 向量化 + FAISS
    └── REM (每50对话)          → 模式发现 + 知识固化
    │
    ▼
memory_eviction
    ├── FIFO 驱逐
    ├── 重要性评分衰减
    └── 相似度去重
    │
    ▼
active_memory.retrieve_as_context()
    → 对话时自动注入相关记忆
```

### 7.4 协作模式数据流

```
auto 模式 + collab_enabled=True
    │
    ▼
collab_manager.filter_user_data(user_message)
    ├── strict:   仅摘要
    ├── balanced: 去敏
    └── full:     全量
    │
    ▼
collab_manager.build_tools_prompt()
    → JSON 工具描述注入到外脑 system prompt
    │
    ▼
multi_model_manager.generate_response()
    → 调用外部模型 API
    │
    ▼
collab_manager.parse_tool_calls(外脑回复)
    → 提取 JSON 工具调用
    │
    ▼
collab_manager.execute_tool_calls()
    → 本地执行（代码/文件/知识库操作）
    │
    ▼
结果注入 enhanced_context → Ollama 整合回答
```

---

## 8. API 路由全览

### 8.1 路由架构

```
Flask app (web_app.py)
├── @app.route          — 主路由（直连，60+ 端点）
├── /api/collab/        — collab_routes.py (Blueprint)
├── /api/evolution/     — evolution_routes.py
├── /api/extraction/    — extraction_routes.py
├── /api/fallback/      — fallback_routes.py
├── /api/vault/         — vault_routes.py (Blueprint)
├── /api/im/            — im_routes.py
├── /api/orchestrator/  — orchestrator_routes.py
├── /api/skills/        — skills_routes.py
├── /api/stats/         — stats_routes.py
├── /api/models/        — model_routes.py (Blueprint)
├── /api/plugins/       — plugin_mgmt_api.py (Blueprint)
├── new_features_api.py — register_browser/sandbox/scheduler/... 到 app
└── device_api.py       — register_device_xxx 到 app（待迁移 Blueprint）
```

### 8.2 主要 API 端点

#### 对话核心

```
POST /api/chat              — 发送文本消息
POST /api/chat/image        — 发送图片消息
POST /api/chat/video        — 发送视频消息
POST /api/chat/audio        — 发送音频消息
POST /api/chat/file         — 发送文件消息
POST /api/audio/transcribe  — 语音转文字
```

#### 对话管理

```
GET  /api/conversations              — 对话列表
POST /api/conversations              — 创建对话
DELETE /api/conversations/<cid>      — 删除对话
POST /api/conversations/batch-delete — 批量删除
GET  /api/conversations/<cid>/messages — 消息历史
PUT  /api/conversations/<cid>/title  — 修改标题
```

#### 知识库

```
POST /api/knowledge/upload   — 上传文件 (PDF/Word/MD/TXT)
POST /api/knowledge/add_text — 添加文本知识
GET  /api/knowledge/list     — 知识列表
```

#### Skills

```
GET    /api/skills                  — 列出所有技能
POST   /api/skills/install          — 安装技能
DELETE /api/skills/<id>/uninstall   — 卸载技能
POST   /api/skills/<id>/toggle      — 启用/禁用
POST   /api/skills/builder/generate — AI 生成技能
POST   /api/skills/hot-reload/toggle — 热更新开关
```

#### 协作模式

```
GET  /api/collab/status         — 协作状态
POST /api/collab/config         — 更新协作配置
GET  /api/collab/foreign-models — 可用外脑列表
GET  /api/collab/privacy-levels — 隐私级别选项
```

#### 进化 / 统计 / 保底

```
POST /api/evolution/proposals              — 创建进化提案
POST /api/evolution/proposals/<id>/approve — 批准
POST /api/evolution/proposals/<id>/reject  — 拒绝
GET  /api/stats/daily                      — 当日统计
GET  /api/stats/trends                     — 使用趋势
GET  /api/fallback/health                  — 保底健康检查
POST /api/fallback/auto-check              — 自动检查
```

#### 模型管理

```
GET  /api/ollama/models       — Ollama 模型列表
POST /api/ollama/pull         — 拉取模型
GET  /api/ollama/pull-status  — 拉取进度
POST /api/ollama/switch       — 切换模型
GET  /api/models/hardware     — 硬件检测
GET  /api/models/recommend    — 模型推荐
```

#### IM / 设备 / 安全

```
GET  /api/im/platforms              — IM 平台列表
POST /api/im/channels               — 添加渠道
POST /api/im/broadcast              — 广播消息
POST /api/im/webhook/<ch_id>        — Webhook 接收
POST /api/vault/lock                — 锁定保险箱
POST /api/privacy/set-password      — 设置隐私密码
GET  /api/system/info               — 系统信息
```

> 完整 API 列表（120+ 端点）参见 [附录 B](#152-完整-api-列表)

---

## 9. 前端架构

### 9.1 前端结构

```
templates/index.html (692行，主入口)
    │
    ├── 30+ includes/ 子模板
    │   ├── 设置面板 Tab (×33)
    │   ├── modal-数据保险箱.html
    │   └── pwa-init.html
    │
    ├── static/css/style.css (主样式)
    │
    └── static/js/modules/ (22 个模块)
        ├── app-core.js          ← 会话管理、流式输出、消息队列
        ├── app-conversation.js  ← 三种模式切换、消息发送
        ├── app-send.js          ← 发送队列面板（WorkBuddy 风格）
        ├── app-settings.js      ← 设置面板控制
        ├── app-model-manager.js ← 模型管理
        ├── app-knowledge.js     ← 知识库
        ├── app-devices.js       ← 设备管理
        ├── app-evolution.js     ← 受控进化
        ├── app-extraction.js    ← 知识萃取
        ├── app-fallback.js      ← 保底状态
        ├── app-hardware.js      ← 硬件检测
        ├── app-harvenhub.js     ← HarvenHub
        ├── app-im.js            ← IM 渠道
        ├── app-media.js         ← 媒体附件
        ├── app-monitor.js       ← 主题监控
        ├── app-multi-agent.js   ← 多 Agent
        ├── app-network.js       ← 公网分享
        ├── app-plugins.js       ← 插件 + 资源发现
        ├── app-skills.js        ← 技能管理
        ├── app-stats.js         ← 统计面板
        ├── app-version-check.js ← 版本检测
        └── app-bilibili.js      ← B站发布
```

### 9.2 JS 模块按功能分组

| 组 | 模块 | 职责 |
|----|------|------|
| **对话核心** | app-core, app-conversation, app-send, app-media | 消息收发、流式渲染、附件处理 |
| **设置管理** | app-settings, app-model-manager, app-hardware, app-version-check | 设置面板、模型管理、硬件检测 |
| **知识/记忆** | app-knowledge, app-extraction | 知识库上传/搜索、知识萃取 |
| **安全** | (dataVault Modal 在 app-settings 中) | 数据保险箱弹窗 |
| **自动化** | app-evolution, app-fallback, app-monitor | 进化提案/保底/监控 |
| **连接** | app-im, app-devices, app-network | IM/设备/公网 |
| **生态** | app-plugins, app-skills, app-harvenhub | 插件/Skills/Hub |
| **分析** | app-stats, app-bilibili | 统计/B站 |

---

## 10. 数据库设计

### 10.1 数据库总览

| 数据库 | 路径 | 用途 |
|--------|------|------|
| `lingzhi.db` | `data/sqlite/` | 主数据库（对话/知识/用户/设置等） |
| `device_security.db` | `data/sqlite/` | 设备绑定与安全事件 |
| `mobile_devices.db` | `data/sqlite/` | 移动设备管理 |
| `visitors.db` | `data/sqlite/` | 访客管理 |
| `vault_logs.db` | `data/` | 数据保险箱访问日志 |
| `privacy.db` | `data/isolated_dbs/` | 隐私库（AES-256 加密） |
| `commercial.db` | `data/isolated_dbs/` | 商用库 |
| `transient.db` | `data/isolated_dbs/` | 过渡库（对话沙箱） |

### 10.2 三库物理隔离

```
        用户查询
            │
    ┌───────┼───────┐
    ▼       ▼       ▼
 ┌─────┐ ┌─────┐ ┌─────┐
 │隐私库│ │过渡库│ │商用库│
 │AES256│ │沙箱 │ │ 开放 │
 └─────┘ └─────┘ └─────┘
    │       │       │
   敏感数据  临时数据  公开数据
   加密存储  对话隔离  共享访问
```

---

## 11. 部署运维

### 11.1 环境要求

| 组件 | 最低要求 | 推荐配置 |
|------|----------|----------|
| CPU | 4 核 | i7-13700F (16 核) |
| RAM | 16 GB | 32 GB |
| GPU | 6 GB VRAM | RTX 4070 SUPER 12GB |
| 磁盘 | 50 GB 可用 | SSD 100 GB+ |
| Python | 3.10+ | 3.13.12 |
| Ollama | 最新版 | 最新版 |

### 11.2 启动流程

```bash
# 方法1：双击桌面快捷方式
→ 执行 启动HarvenAI.bat

# 方法2：命令行
H:\HarvenAI\.venv\Scripts\python.exe web_app.py

# 方法3：bat 文件
启动HarvenAI.bat
# 内容：
#   1. 检查 Ollama 是否运行
#   2. 激活 venv
#   3. 运行 web_app.py
```

### 11.3 日常维护命令

```bash
# 检查 Flask 是否运行
netstat -ano | findstr :7000

# 强制杀死卡死进程
taskkill /F /PID <PID>

# 🚨 重启铁律：修改 Flask 代码后
# 1. 先 netstat -ano | findstr :7000 确认无残留
# 2. 再启动 Python web_app.py
# 原因：Flask debug=False 时模板被缓存，taskkill 可能遗漏
#       导致多进程抢端口，新旧代码混用

# 查看日志
tail -f data/harven.log

# 数据库备份
copy data\sqlite\lingzhi.db data\sqlite\lingzhi.db.bak
```

### 11.4 已打包版本

```
H:\HarvenAI_完整项目_源码.tar.gz    — 完整源码包
H:\音乐工坊_部署包_无媒体.zip       — 音乐工坊部署包（12.1MB）
```

---

## 12. 法律合规与侵权风险分析

### 12.1 开源许可合规

| 组件 | 许可 | 合规状态 | 风险 |
|------|------|----------|------|
| **本项目** | Apache 2.0 | ✅ 自选许可 | 无 |
| Python 3.13 | PSF License | ✅ GPL兼容 | 无 |
| Flask | BSD-3 | ✅ 宽松许可 | 无 |
| Ollama | MIT | ✅ 宽松许可 | 无 |
| FAISS | MIT | ✅ 宽松许可 | 无 |
| SQLite | Public Domain | ✅ 无限制 | 无 |
| Bootstrap 5 | MIT | ✅ 宽松许可 | 无 |
| **PySide6** | **LGPL v3** | ⚠️ 需注意 | 见下文 |
| transformers | Apache 2.0 | ✅ 兼容 | 无 |
| PyYAML | MIT | ✅ 宽松许可 | 无 |

#### ⚠️ PySide6 LGPL 合规要点

```
风险：LGPL v3 要求最终用户可以替换库文件
处理：
  1. PySide6 以动态链接方式使用（pip 安装，非静态编译）
  2. 最终用户可以通过 pip 更新/替换 PySide6
  3. 不修改 PySide6 源代码
  4. 告知用户 LGPL 库的存在及替换方法
结论：✅ 当前使用方式合规
```

### 12.2 知识产权风险

| 风险域 | 风险等级 | 分析 | 缓解措施 |
|--------|----------|------|----------|
| **模型权重使用** | 🟡 中 | 使用 Ollama 下载开源模型（qwen2.5、deepseek-r1 等），各有各自许可 | 仅索引/推荐，不二次分发模型文件 |
| **外部 API 调用** | 🟢 低 | 调用 DeepSeek/豆包等 API 属于正常使用，不涉及分发 | 用户自行获取 API Key |
| **商标 "Harven"** | 🟡 中 | 需确认是否与已有商标冲突 | 建议做商标检索，考虑注册 |
| **代码抄袭指控** | 🟢 低 | 全部代码原创，使用开源库遵循其许可 | 保留开发记录，注明依赖 |
| **用户生成内容** | 🟡 中 | AI 可能生成有版权风险的内容 | 免责声明 + 用户协议 |

### 12.3 隐私合规

| 合规项 | 状态 | 说明 |
|--------|------|------|
| **GDPR** | ✅ 天然合规 | 纯本地运行，不收集用户数据上传 |
| **中国个人信息保护法** | ✅ 低风险 | 无服务端，数据存储在用户本地 |
| **数据跨境** | ✅ 无风险 | 默认不外传，仅用户主动连接外部 API 时 |
| **用户知情权** | ✅ | 明确告知协作模式会向外脑发送去敏数据 |
| **数据删除权** | ✅ | 支持对话删除、知识库清空、数据库重置 |

### 12.4 内容安全合规

| 合规项 | 状态 | 说明 |
|--------|------|------|
| **中国网络安全法** | ✅ | 道德内核 + 内容过滤 + 审核日志 |
| **有害内容拦截** | ✅ | morality.py 多语言高危词 + LLM 复核 |
| **色情内容** | ✅ | built-in content policy 拒绝 |
| **政治敏感内容** | ✅ | built-in content policy 拒绝 |
| **暴力恐怖内容** | ✅ | morality.py 锁死机制 |

### 12.5 风险矩阵总结

| 风险类别 | 风险等级 | 应对状态 |
|----------|----------|----------|
| 开源许可违规 | 🟢 低 | ✅ 已审查 |
| 商标侵权 | 🟡 中 | ⚠️ 建议检索 |
| 隐私泄露 | 🟢 低 | ✅ 三库隔离 + AES-256 |
| API 滥用 | 🟢 低 | ✅ 速率限制 + 用户自备 Key |
| 内容合规 | 🟢 低 | ✅ 道德内核 + 内容过滤 |
| 硬件克隆 | 🟢 低 | ✅ 设备指纹 + 防克隆 |
| 网络攻击 | 🟢 低 | ✅ 五层防御 + 白名单 |

---

## 13. 开发规范与已知坑

### 13.1 🚨 重启铁律

```
⚠️ 修改 Flask 代码后，重启前必须：
netstat -ano | findstr :7000

原因：
  - Flask debug=False 时模板被缓存
  - taskkill 可能遗漏后台进程
  - 多进程抢端口 → 新旧代码混用 → 行为异常
```

### 13.2 ⚠️ config.yaml 污染

```python
# ❌ 错误写法（写入了 !!python/object/new: 导致 safe_load 崩溃）
yaml.dump(config, f)

# ✅ 正确写法（强制转换为纯 dict）
yaml.dump(dict(config), f, allow_unicode=True)
```

> 已批量修复 `web_app.py` 14 处 + `evolution_manager.py` 1 处

### 13.3 其他已知坑

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 1 | 修改 Jinja2 include 不生效 | debug=False 模板缓存 | 重启 Flask |
| 2 | 协作外脑 response 为空 | `generate_response(stream=False)` 返回的是 dict 生成器 | 按 `chunk['type']=='text'` 解析 |
| 3 | collabMode 未传递到后端 | JS 请求体漏写字段 | 添加 `collab_mode: collabMode` |
| 4 | Skills JSON 不注入到外脑 | 注入条件过窄 `use_external or direct` | 改为 `direct or auto` |
| 5 | 侧边栏按钮无响应 | openModal 只设 CSS class 无 inline style | 改用 `_forceOpenModal` |
| 6 | `MultiModelManager.generate_response` | 之前 engine.py 直接调用私有方法 | 统一入口方法已补充 |

### 13.4 代码规范

```python
# ✅ 导入顺序：标准库 → 第三方 → 本地
import json
import logging
from pathlib import Path

import yaml
from flask import Blueprint

from src.core.morality import analyze_intent
from src.utils.config import Config

# ✅ config 保存
cfg_path.write_text(yaml.dump(dict(config), allow_unicode=True), encoding="utf-8")

# ✅ Blueprint 注册（推荐方式）
from src.core.collab_routes import BP as collab_bp
app.register_blueprint(collab_bp)
```

---

## 14. 技术债务与未来路线

### 14.1 已知技术债务

| 优先级 | 项目 | 描述 |
|--------|------|------|
| P0 | `device_api.py` Blueprint 迁移 | 仍在用 `register_xxx(app)` 方式 |
| P0 | `new_features_api.py` Blueprint 迁移 | 同上 |
| P1 | 单元测试覆盖 | 67 个测试，覆盖率不足 40% |
| P1 | 前端测试 | 无 E2E/组件测试 |
| P2 | 文档完善 | API 文档、用户手册 |
| P2 | 国际化 | 仅 zh-CN/en-US，需更多语言 |
| P3 | HarvenHub | 知识打包与发布系统 |

### 14.2 未来路线图

```
Harven AI v0.2 → 当前版本（已完成功能见下方）
Harven AI v0.3 → HarvenHub 一期（独立开发）
Harven AI v1.0 → 正式发布（稳定性 + 测试 + 文档）
Harven AI v2.0 → 插件生态 + 社区建设
```

### 14.3 已完成功能清单

```
✅ Phase 1-4:  对话 + 界面 + 记忆 + 用户画像 + 三库隔离 + 任务管理
✅ Phase 5:    手机联动（Token + 二维码 + 代理对话）
✅ Phase 6+:   硬件安全（设备指纹 + 绑定 + 防克隆）
✅ 安全加固:    SSRF/速率限制/Memory Dreaming/代码沙箱/插件+技能
✅ 工程优化:    知识库bug/PDF导入/JS拆分/单元测试
✅ 多模型:     28 厂商支持 + 硬件自检 + 模型推荐
✅ 模式重构:    ⚡自动/🏠本地/🤖外脑 三种模式
✅ 协作模式:    外脑 + 隐私三档 + Skills JSON 注入
✅ 数据保险箱:  密码锁定 + 访问日志 + 隐私模式
✅ 设置面板:    8 主 Tab + 30+ 子面板重组
✅ 品牌定制:    鸭子图标 + PWA 多尺寸
```

---

## 15. 附录

### 15.1 关键文件速查

| 功能 | 文件 | 关键行/方法 |
|------|------|------------|
| 三种模式定义 | `src/core/engine.py` | L72-76 `QUERY_MODES` |
| 模型路由 | `src/core/engine.py` | L556-615 `_route_model_for_query` |
| 三段式查询 | `src/core/engine.py` | L734-810 `_stream_three_stage` |
| 协作外脑调用 | `src/core/engine.py` | L826-845 |
| 系统提示词构建 | `src/core/engine.py` | L847-904 |
| 道德意图复核 | `src/core/morality.py` | `analyze_intent()` |
| 独立性防御 | `src/core/independence.py` | 五层 `_patch_requests()` |
| 隐私过滤 | `src/core/collab_manager.py` | L82-108 `filter_user_data()` |
| Skills JSON 注入 | `src/core/collab_manager.py` | L110-183 `build_tools_prompt()` |
| 数据保险箱 | `src/core/vault_routes.py` | Blueprint `/api/vault` |
| 主 Flask 应用 | `web_app.py` | - |
| 配置文件 | `config.yaml` | - |
| 前端主入口 | `templates/index.html` | - |
| 模式切换 JS | `static/js/modules/app-conversation.js` | - |
| 侧边栏事件 | `templates/index.html` | L192-229 内联委托 |
| Modal 控制 | `static/js/modules/app-settings.js` | `openModal()` / `_forceOpenModal` |

### 15.2 完整 API 列表

<details>
<summary>点击展开（120+ 端点）</summary>

```
=== 对话核心 ===
POST /api/chat
POST /api/chat/image
POST /api/chat/video
POST /api/chat/audio
POST /api/chat/file
POST /api/audio/transcribe

=== 对话管理 ===
GET    /api/conversations
POST   /api/conversations
DELETE /api/conversations/<cid>
POST   /api/conversations/batch-delete
POST   /api/conversations/auto-classify
GET    /api/conversations/<cid>/messages
PUT    /api/conversations/<cid>/title

=== 知识库 ===
POST /api/knowledge/upload
POST /api/knowledge/add_text
GET  /api/knowledge/list

=== 知识萃取 ===
POST /api/extraction/extract-document
POST /api/extraction/extract-conversation
GET  /api/extraction/knowledge
DELETE /api/extraction/knowledge/<id>
GET  /api/extraction/log

=== Skills ===
GET    /api/skills
POST   /api/skills
PUT    /api/skills/<skill_id>
DELETE /api/skills/<skill_id>
POST   /api/skills/<skill_id>/toggle
GET    /api/skills/list
DELETE /api/skills/<skill_id>/uninstall
POST   /api/skills/install
POST   /api/skills/check-updates
POST   /api/skills/match
POST   /api/skills/reload
GET    /api/skills/hot-reload/status
POST   /api/skills/hot-reload/toggle
GET    /api/skills/builder/categories
POST   /api/skills/builder/generate
POST   /api/skills/builder/save
POST   /api/skills/builder/validate

=== 协作模式 ===
GET  /api/collab/status
POST /api/collab/config
GET  /api/collab/foreign-models
GET  /api/collab/privacy-levels

=== 进化 ===
GET  /api/evolution/version
GET  /api/evolution/check
POST /api/evolution/request
POST /api/evolution/<log_id>/approve
POST /api/evolution/<log_id>/reject
POST /api/evolution/<log_id>/rollback
GET  /api/evolution/history
GET  /api/evolution/proposals
POST /api/evolution/proposals
POST /api/evolution/proposals/<id>/approve
POST /api/evolution/proposals/<id>/reject

=== 数据保险箱 ===
GET  /api/vault/status
POST /api/vault/lock
POST /api/vault/unlock
POST /api/vault/set-privacy-mode
GET  /api/vault/logs
GET  /api/vault/stats
GET  /api/vault/check-lock

=== 统计 ===
GET  /api/stats/daily
GET  /api/stats/daily/<date>
GET  /api/stats/monthly
GET  /api/stats/monthly/<ym>
GET  /api/stats/trends
GET  /api/stats/resource
POST /api/stats/collect-resource

=== 保底 ===
GET  /api/fallback/health
GET  /api/fallback/status
POST /api/fallback/enter
POST /api/fallback/exit
POST /api/fallback/auto-check
POST /api/fallback/try-fix
POST /api/fallback/response

=== IM 连接 ===
GET    /api/im/platforms
GET    /api/im/channels
POST   /api/im/channels
PUT    /api/im/channels/<ch_id>
DELETE /api/im/channels/<ch_id>
POST   /api/im/channels/<ch_id>/toggle
POST   /api/im/test
POST   /api/im/channels/<ch_id>/send
POST   /api/im/broadcast
POST   /api/im/webhook/<ch_id>
GET    /api/im/health

=== 模型管理 ===
GET    /api/ollama/status
GET    /api/ollama/models
POST   /api/ollama/switch
POST   /api/ollama/pull
GET    /api/ollama/pull-status
DELETE /api/ollama/model/<name>
GET    /api/models/recommend
GET    /api/models/hardware
GET    /api/models/installed
POST   /api/models/pull
POST   /api/models/delete
POST   /api/models/switch
GET    /api/models/available

=== 插件管理 ===
GET    /api/plugins/
POST   /api/plugins/<name>/enable
POST   /api/plugins/<name>/disable
DELETE /api/plugins/<name>/uninstall
POST   /api/plugins/reload
POST   /api/plugins/install
GET    /api/plugins/demo

=== 设置 ===
GET  /api/settings
POST /api/settings
POST /api/settings/user
POST /api/settings/model
POST /api/settings/morality
POST /api/settings/security
POST /api/settings/ui
POST /api/settings/language
POST /api/settings/web_search
POST /api/settings/ollama

=== 浏览器自动化 ===
POST /api/browser/execute
GET  /api/browser/current
POST /api/browser/screenshot
POST /api/browser/close
GET  /api/browser/status
POST /api/browser/install
POST /api/browser/navigate
POST /api/browser/content

=== 代码沙箱 ===
POST /api/sandbox/execute
POST /api/sandbox/scan

=== 定时任务 ===
GET    /api/scheduler/tasks
POST   /api/scheduler/tasks
DELETE /api/scheduler/tasks/<id>
POST   /api/scheduler/tasks/<id>/toggle
POST   /api/scheduler/parse
GET    /api/scheduler/runs

=== 多 Agent ===
GET  /api/multi-agent/roles
GET  /api/multi-agent/templates
POST /api/multi-agent/collaborate
GET  /api/multi-agent/sessions
POST /api/multi-agent/sessions

=== 活跃记忆 ===
GET  /api/memory/active
POST /api/memory/active/search
DELETE /api/memory/active/<id>

=== 任务管理 ===
GET  /api/tasks
POST /api/tasks
POST /api/tasks/<tid>/complete
DELETE /api/tasks/<tid>

=== 全网资源 ===
GET  /api/resource-hub/channels
GET  /api/resource-hub/browse
GET  /api/resource-hub/search
POST /api/resource-hub/install
POST /api/resource-hub/generate-skill
POST /api/resource-hub/license-check

=== GitHub ===
POST /api/github/download
POST /api/github/detect

=== 其他 ===
GET  /health
GET  /api/server/info
GET  /api/system/info
GET  /api/version/check
POST /api/version/update
POST /api/privacy/set-password
POST /api/search
GET  /api/search/engines
GET  /api/specializations
POST /api/specializations
DELETE /api/specializations/<sid>
GET  /api/monitor/topics
POST /api/monitor/topics
DELETE /api/monitor/topics/<id>
POST /api/monitor/check/<id>
GET  /api/monitor/items
POST /api/monitor/read/<id>
GET  /api/monitor/unread_count
GET  /api/workflows/templates
GET  /api/workflows
POST /api/workflows
POST /api/workflows/<id>/run
DELETE /api/workflows/<id>
GET  /api/channels
POST /api/channels
DELETE /api/channels/<id>
POST /api/webhook/<channel_id>
```
</details>

### 15.3 配置选项速查

```yaml
# config.yaml 关键配置
ollama:
  base_url: http://localhost:11434
  default_model: qwen2.5:14b

collab_enabled: false          # 协作模式开关
collab_foreign_model: doubao   # 外脑模型（默认豆包）
collab_privacy_level: balanced # 隐私级别

security:
  admin_password: harven2026   # 首次启动后更改
  auto_lock_minutes: 0
  antivirus_enabled: false

language:
  main_language: zh-CN
  ui_language: follow

morality:
  morality_level: default
  lockdown_threshold: 3
  answer_style: friend

memory:
  snapshot_interval: 5
  working_context_turns: 8

web_search:
  enabled: true           # 保留配置，但已取消联网搜索
  search_engine: bing
  auto_search: true
  default_query_mode: auto

ui:
  theme: system
  font_size: medium
  bubble_style: default
```

### 15.4 版本历史

| 日期 | 版本 | 内容 |
|------|------|------|
| 2026-06-13 | v0.2 | 模式架构重构 + 协作模式 + 数据保险箱 + 3 BUG 修复 |
| 2026-06-12 | v0.1.9 | 框架底层重构（路由 Blueprint 化 + sys.path 清理） |
| 2026-06-11 | v0.1.8 | PWA 图标重置（鸭子图标全尺寸） |
| 2026-06-09 | v0.1.7 | 多模型扩展 28 厂商 + 设置面板重组 + IoT 驱动架构 |
| 2026-06-08 | v0.1.6 | 受控进化 + 统计模块 + 保底机制 + 知识萃取 |
| 2026-06-05 | v0.1.5 | 身份过滤修复 + 道德内核 v2.2 |
| 2026-05-26 | v0.1.4 | 安全加固 P0-P2（速率限制/沙箱/文件扫描） |
| 2026-05 | v0.1.0 | Phase 1-6 基础功能完成 |

---

> **文档结束** — 本文档随 Harven AI 代码库共同维护。
> 最新版本请查看 `H:\HarvenAI\docs\HarvenAI_完整开发文档_v3.md`。
