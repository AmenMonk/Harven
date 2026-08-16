# 🦆 Harven AI — 本地优先的个人AI助手

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## 什么是 Harven AI？

Harven AI 是一个**本地优先**的个人AI助手，数据留在你的电脑上，不花钱，不开云端账号。

### 核心特点

- 🔒 **本地优先** — 数据在自己电脑上，隐私安全
- 🆓 **永久免费** — 本地模型（Ollama）不花钱，外脑API有免费额度
- 🧠 **多模型协作** — 通用模型思考 + 垂直模型执行（音乐/图片/语音/医疗）
- 📋 **Skill系统** — 可执行脚本编排多模型完成长效任务
- 📊 **面板系统** — 记账/健身/待办等可视化UI面板
- 🏠 **家庭场景** — 记账/提醒/管理/监控

## 快速开始

### 安装

1. 确保已安装 [Python 3.12+](https://www.python.org/) 和 [Ollama](https://ollama.ai)
2. 克隆仓库：
   ```bash
   git clone https://github.com/YOUR_USERNAME/HarvenAI.git
   cd HarvenAI
   ```
3. 运行一键安装：
   ```bash
   # Windows
   一键安装启动.bat
   ```
4. 或手动安装：
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy config.example.yaml config.yaml
   # 编辑 config.yaml 填入API Key
   python web_app.py
   ```

### 使用

- 双击桌面"哈文.lnk"启动
- 或运行 `启动HarvenAI.bat`

## 功能

### 对话模式
- 🏠 **本地模式** — 本地模型+知识库+Skills+画像
- 🤖 **外脑模式** — 外部模型（DeepSeek/GLM等）+知识库+Skills
- 🎨 **垂直模式** — 音乐生成/图片生成/语音合成/医疗问答

### Skill系统
- 📒 记账助手 — 收支管理+SQLite持久化
- 📋 代码审计 — P0/P1/P2问题检测
- 📊 财务月结 — 查数据→统计→对比→生成报告
- 💪 健康追踪 — 运动/体重/睡眠记录
- 🎵 音乐生成 — MusicGen本地生成
- 🎵 歌曲制作 — 多模型协作（写词→作曲→配声→合成）

### 面板系统
- 创建面板（5种模板：记账/健身/待办/库存/空白）
- 7种字段类型（文本/数字/日期/下拉/多行文本/时间/图片）
- 表格视图+卡片视图切换
- 统计图表（汇总卡片+分类柱状图）

### 协作模式
- 🚫 关闭 — 纯对话，安全
- ☁️ 智能协作 — Skill编排+外脑+垂直模型
- 🏠 本地协作 — Skill编排+本地模型+垂直模型

## 技术栈

- **后端**：Python + Flask + SQLite + FAISS
- **前端**：HTML + Bootstrap5 + JavaScript
- **AI**：Ollama（本地）+ DeepSeek/GLM/豆包等（外脑）
- **垂直模型**：MusicGen / Stable Diffusion / Bark / 华佗
- **桌面**：pywebview

## 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建分支：`git checkout -b feature/你的功能`
3. 提交：`git commit -m "添加XX功能"`
4. 推送：`git push origin feature/你的功能`
5. 发起 Pull Request

## License

Apache License 2.0 — 可自由使用、修改、分发。
