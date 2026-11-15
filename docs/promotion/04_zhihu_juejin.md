# 知乎/掘金 技术文章

## 标题
我用 Python 做了个本地 AI 大脑——3 个月开发总结

## 正文

3 个月前，我决定做一个跟 ChatGPT 不一样的 AI 助手。

不是又一个套壳聊天机器人，是一个**真正属于你自己的 AI 大脑**——数据在你电脑上，断网能用，永久免费，而且能自己进化。

今天开源了，想分享一下这 3 个月的开发历程和一些技术思考。

---

## 一、为什么做这个？

用 ChatGPT 的时候我总在想几个问题：

1. **隐私**：我跟 AI 说的每一句话都存在云端服务器上，我没办法控制
2. **遗忘**：每次新对话，AI 就忘了我之前说什么。我有 100 多次对话历史，但它一个都不记得
3. **成本**：每月 20 美元，一年 1700 多人民币，但很多时候我只是想问个简单问题
4. **黑盒**：我不知道 AI 怎么处理我的数据，服务条款说变就变

Ollama 解决了"本地跑模型"的问题，但 OpenWebUI 只是个聊天界面——没有记忆系统、没有知识库、没有自动化能力。

所以我做了 **Harven AI（哈文）**。

---

## 二、哈文是什么

一句话：**住在电脑里的 AI 大脑，不在云端。**

### 4 个独创特色

#### 1. 受控进化——AI 能改自己的代码

这是我最自豪的功能。你可以跟哈文说：

> "帮我加一个天气查询功能"

哈文会：
1. 分析需求
2. 生成代码修改提案
3. 给你看修改描述
4. 你批准 → 应用（可回滚）
5. 你拒绝 → 不动

**能改**：skills/（技能脚本）、templates/（页面）、config.yaml（配置）
**不能改**：engine.py、web_app.py 等核心代码（安全黑名单）

这意味着哈文可以**自我进化**——用户提需求，AI 自己改自己，用户审批。不需要等开发者更新。

#### 2. 五重记忆——永不遗忘

ChatGPT 跨对话会遗忘。哈文不会：

| 层 | 功能 |
|----|------|
| L0 原始对话 | 全量 SQLite 持久化，永不丢失 |
| L1 任务记忆 | AI 语义提取关键信息，结构化存储 |
| L2 长期记忆 | 跨对话的知识提炼与固化 |
| 主动检索 | 对话时自动拉取相关历史 |
| 主人认知 | 记住你的偏好、身份、习惯 |

你跟哈文说过的每一句话它都记得。3 个月后你问"我上次说想买什么书来着"，它能回答。

#### 3. 协作模式——多模型分工

这是跟所有聊天机器人最大的区别。

普通 AI 是"单模型干所有事"。哈文的协作模式是"多模型分工"：

**云地协作**：
```
外脑（GPT/DeepSeek）分析需求 → 调垂直模型参数 → 本地垂直模型执行
```

**本地双模**：
```
本地通用模型调度 → 调垂直参数 → 垂直模型执行
```

举个例子：你说"给我生成一首欢快的背景音乐"——外脑分析"欢快"的节奏/调性 → 调 MusicGen 参数 → 本地 MusicGen 生成音乐。一个模型搞不定这种任务。

#### 4. AI 造 UI 面板

你跟哈文说"帮我做个记账面板"：
1. AI 生成 HTML/JS/CSS 代码
2. 安装到 data/panels/ 目录
3. iframe 沙盒隔离运行
4. 你有了新的记账面板

这不是对话，是 **AI 帮你造工具**。

---

## 三、技术架构

### 代码规模

| 指标 | 数量 |
|------|------|
| Python 模块 | 113 个 |
| REST API 端点 | 80+ 个 |
| 代码行数 | 16 万行 |
| 内置 Skill | 10 个 |
| 双语 i18n key | 853 个 |

### 技术栈

- **后端**：Python + Flask + SQLite + FAISS
- **前端**：HTML + Bootstrap5 + JavaScript（SSE 流式）
- **AI**：Ollama（本地）+ DeepSeek/GLM/豆包（外脑）
- **垂直模型**：MusicGen / Stable Diffusion / Bark / MiniCPM-V / 华佗
- **桌面**：pywebview（可选）

### 三层安全防护

1. **文件扫描**（5 层检查：扩展名/大小/Magic Bytes/内容模式/杀毒）
2. **代码沙箱**（Docker 隔离优先 / 子进程降级）
3. **设备指纹**（防克隆）

---

## 四、三平台支持——最痛苦的部分

让一个 Python 应用在 Windows/Linux/macOS 上都能跑，比想象中难多了。

### 问题

1. Windows 的 python.exe 在 Linux 上跑不了
2. Linux 的 apt 和 macOS 的 brew 命令不一样
3. 截图：Windows 用 Win+Shift+S，Linux 用 scrot，macOS 用 screencapture
4. 清回收站：Windows 用 Clear-RecycleBin，Linux 用 rm Trash，macOS 用 rm .Trash
5. 路径格式：Windows 是 C:\Users\，Linux 是 /home/，macOS 是 /Users/

### 解决方案

写了一个 `platform.py` 模块统一处理：

```python
IS_WINDOWS = sys.platform.startswith('win')
IS_LINUX = sys.platform.startswith('linux')
IS_MACOS = sys.platform == 'darwin'

def install_package_cmd(pkg_name):
    if IS_WINDOWS:
        return ['winget', 'install', pkg_name]
    elif IS_MACOS:
        return ['brew', 'install', pkg_name]
    else:  # Linux
        return ['sudo', 'apt', 'install', '-y', pkg_name]
```

### 内置 Python——最聪明的决定

一开始 Linux/macOS 版让用户自己装 Python，结果发现门槛太高。

后来从 [python-build-standalone](https://github.com/indygreg/python-build-standalone) 下载了 Linux 和 macOS 的 portable Python，内置到安装包里：

| 平台 | 大小 | Python 来源 |
|------|------|-----------|
| Windows | 16 MB | Python embeddable（微软官方精简版） |
| Linux | 37 MB | python-build-standalone stripped |
| macOS | 52 MB | python-build-standalone（Apple Silicon + Intel 双架构） |

用户解压双击就能用，不需要装 Python。这个体验提升太大了。

---

## 五、中英双语——比想象中复杂

不只是翻译界面文字，还要：
- AI 回答语言跟随界面（中文界面→中文回答，英文界面→英文回答）
- 语音识别语言跟随（中文界面→zh 识别，英文界面→en 识别）
- 命令路由支持英文（"uninstall and delete norton"→FC 模式）
- FC 提示词双语（按用户消息语言选）

853 个双语 key，覆盖 28 个设置 tab + 主框架 + 首页 + HarvenHub。

---

## 六、HarvenHub 社区市场

用户可以上传/下载：
- **Skill**：可执行脚本技能
- **Agent**：角色扮演智能体
- **知识包**：文档集合
- **Bundle**：Skill + Agent + 知识包的组合

每个 Skill 标注支持的平台，下载时检测兼容性。

---

## 七、跟其他项目对比

| 维度 | 哈文 | OpenWebUI | OpenClaw | ChatGPT |
|------|------|-----------|----------|---------|
| 隐私 | ✅ 本地 | ✅ 本地 | ✅ 本地 | ❌ 云端 |
| 记忆 | ✅ 五重 | ⚠️ 会话级 | ⚠️ 文件级 | ❌ 遗忘 |
| 知识库 | ✅ 内置 FAISS | ❌ 无 | ⚠️ 插件 | ❌ 无 |
| 多模型协作 | ✅ 独创 | ❌ 单模型 | ❌ 单模型 | ❌ 单模型 |
| 自我修改 | ✅ 受控进化 | ❌ 无 | ❌ 无 | ❌ 无 |
| 离线 | ✅ 可用 | ✅ 可用 | ✅ 可用 | ❌ 不可用 |
| 开源 | ✅ Apache 2.0 | ✅ 开源 | ✅ MIT | ❌ 闭源 |

---

## 八、3 个月开发心得

### 最值得的 3 个决定

1. **内置 Python**：用户体验提升巨大，三平台解压即用
2. **SQLite 持久化记忆**：比文件化记忆好太多，查询快、结构化
3. **受控进化**：AI 改自己代码这个方向，反馈最积极

### 最坑的 3 个问题

1. **跨平台**：Windows/Linux/macOS 的差异比想象中大（回收站/截图/包管理器/路径）
2. **i18n**：不只是翻译，还有 AI 回答语言/STT 语言/命令路由/FC 提示词
3. **GitHub 网络**：在中国 push 代码到 GitHub 比写代码还难（最后用 API 推送的）

### 学到的

- **用户体验 > 技术优雅**：内置 Python 37MB 比"让用户自己装 Python"好太多
- **安全要分层**：单靠 prompt 限制不够，要有代码沙箱 + 文件扫描 + 设备指纹
- **社区很重要**：一个人开发 3 个月，很多功能是边用边改出来的

---

## 九、开源信息

- **GitHub**: https://github.com/AmenMonk/Harven
- **License**: Apache 2.0
- **下载**: 三平台安装包在 GitHub Release 里
- **文档**: 完整使用指南 + 技术详解

欢迎 star、提 issue、贡献代码！

---

## 十、下一步

- 更好的记忆系统（LLM 驱动的语义理解）
- 更多垂直模型（视频生成/代码生成）
- 移动端 App
- 智能家居集成

如果你对本地 AI 感兴趣，欢迎一起参与开发！

---

**发布步骤**：
1. **知乎**：
   - 打开 https://zhihu.com
   - 点"写文章"
   - 标题粘贴上面的标题
   - 正文粘贴上面的正文
   - 添加标签：开源、AI、Python、人工智能、本地部署
   - 发布

2. **掘金**：
   - 打开 https://juejin.cn
   - 点"写文章"
   - 标题和正文同上
   - 分类选"人工智能"或"后端"
   - 标签加：Python、AI、开源、本地部署
   - 发布

**发布时间**：工作日晚上 8-10 点阅读量最高
