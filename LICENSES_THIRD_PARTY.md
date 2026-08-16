# Harven AI — 第三方依赖许可证声明

> 本文件由 `pip-licenses` 自动扫描生成，并经人工复核。  
> 最后更新：2026-05-27  
> 项目许可证：Apache 2.0（见 `LICENSE`）

---

## ✅ 许可证兼容性总览

| 许可证类型 | 兼容 Apache 2.0 | 允许商业使用 | 说明 |
|---|---|---|---|
| MIT | ✅ 完全兼容 | ✅ | 无任何限制 |
| BSD-2/3-Clause | ✅ 完全兼容 | ✅ | 需保留版权声明 |
| Apache 2.0 | ✅ 完全兼容 | ✅ | 需保留 NOTICE 文件 |
| **LGPL v2/v3** | ✅ 兼容（动态链接） | ✅ | PySide6 采用此协议，可正常使用 |
| MPL 2.0 | ⚠️ 条件兼容 | ✅ | 修改 MPL 文件须开源该文件 |
| **GPL v2/v3** | ❌ 不兼容 | ⚠️ 有限制 | 传染性，禁止在 Apache 2.0 项目中混用 |
| **AGPL v3** | ❌ 不兼容 | ⚠️ 网络分发也须开源 | 比 GPL 更严格，SaaS 部署也受约束 |

---

## ✅ 核心依赖（无风险）

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| Flask | ≥3.0 | BSD-3-Clause | Web 框架 |
| Werkzeug | ≥3.0 | BSD-3-Clause | WSGI 工具 |
| flask-cors | ≥6.0 | MIT | 跨域支持 |
| openai | ≥1.0 | Apache 2.0 | Ollama 兼容 SDK |
| litellm | ≥1.0 | MIT | 多模型统一接口 |
| faiss-cpu | ≥1.7 | MIT | 向量搜索 |
| numpy | ≥1.24 | BSD-3-Clause | 数值计算 |
| PyYAML | ≥6.0 | MIT | 配置文件 |
| requests | ≥2.31 | Apache 2.0 | HTTP 请求 |
| Pillow | ≥10.0 | MIT-CMU | 图片处理 |
| tiktoken | ≥0.5 | MIT | Token 计数 |
| faster-whisper | ≥1.0 | MIT | 本地语音转写 |
| opencv-python-headless | ≥4.8 | Apache 2.0 | 视频处理 |
| python-dotenv | ≥1.0 | BSD-3-Clause | 环境变量 |
| tqdm | ≥4.65 | MPL-2.0 AND MIT | 进度条（双协议，MIT 部分适用） |
| apscheduler | ≥3.10 | Apache 2.0 | 定时任务 |
| playwright | ≥1.40 | Apache 2.0 | 浏览器自动化 |
| cryptography | ≥40.0 | Apache-2.0 OR BSD-3-Clause | 加密库（AES-256） |
| sentence-transformers | ≥2.0 | Apache 2.0 | 语义向量化 |
| transformers | ≥4.0 | Apache 2.0 | Hugging Face 模型 |
| huggingface_hub | ≥0.20 | Apache 2.0 | 模型下载 |
| PySide6 | ≥6.5 | **LGPL v3** | 桌面 UI（兼容 Apache 2.0） |
| PySide6-Essentials | ≥6.5 | LGPL v3 | Qt 核心组件 |
| modelscope | ≥1.10 | Apache 2.0 | 国内模型下载（Whisper） |

---

## ⚠️ 可选依赖（含高危许可证，已降为按需导入）

> 这些包**未列入** `requirements.txt` 核心安装列表，仅在用户本地可选安装，并在代码中以 `try/except ImportError` 保护调用。

| 包 | 许可证 | 风险 | 替代方案 |
|---|---|---|---|
| **PyMuPDF (fitz)** | AGPL-3.0 或 Artifex 商业证 | 🔴 高：AGPL 有网络分发传染性，商业部署须购买商业证 | `pypdf`（MIT）、`pdfplumber`（MIT） |
| **EbookLib** | AGPL-3.0+ | 🔴 高：同上 | 自行解析 EPUB（zipfile + xml） |
| **imageio-ffmpeg** | GPL-2.0 | 🟡 中：仅限私用，不可二进制分发 | 系统自带 `ffmpeg`（用户自行安装） |

**行动项（如需商业化）：**
1. 将 `knowledge_base.py` 中 `import fitz` 替换为 `pypdf` 或 `pdfplumber`
2. 将 `video_processor.py` 中 `imageio_ffmpeg.get_ffmpeg_exe()` 改为调用系统 `ffmpeg`
3. 移除 `ebooklib` 依赖，改用 Python 标准库解析 EPUB

---

## 🤖 AI 模型使用条款

### Ollama（本地推理引擎）
- **许可证**：MIT
- **用途**：本地运行开源 LLM，无网络传输
- **限制**：无

### Qwen2.5 系列（默认推荐模型）
- **许可证**：Qwen License（基于 Apache 2.0，附商业使用条款）
- **商业使用**：月活跃用户 **≤ 100 万** 免费；超过需向阿里云申请商业授权
- **用途**：本地推理，不向模型提供商传输用户数据
- **文档**：https://huggingface.co/Qwen/Qwen2.5-14B

### Llama 3 系列（可选）
- **许可证**：Meta Llama 3 Community License
- **商业使用**：MAU ≤ 7 亿免费；超过需与 Meta 签订协议
- **限制**：不可用于训练其他 LLM，产品名不可使用 "Llama" 前缀

### faster-whisper（语音识别）
- **基础模型**：OpenAI Whisper（MIT 许可证）
- **推理库**：faster-whisper（MIT）
- **限制**：无

---

## 🔍 搜索 API 服务条款提示

> Harven AI 内置联网搜索功能，默认使用公开搜索引擎 API。以下为用户须知：

| 搜索引擎 | 使用限制 |
|---|---|
| DuckDuckGo | 禁止大规模爬取，个人使用可接受；勿高频调用 |
| Bing Search API | 官方 API 有 ToS，直接爬取违反其服务条款 |
| 百度 | 爬取行为违反其 `robots.txt`，仅限少量查询 |

**建议**：如需在生产环境高频搜索，接入 [Exa](https://exa.ai)（商业 API）或 [SearXNG](https://github.com/searxng/searxng)（自建，AGPL）。

---

## 📋 分发清单

若进行公开分发（开源或商业），须包含以下内容：

- [x] `LICENSE`（Apache 2.0 全文）
- [x] `LICENSES_THIRD_PARTY.md`（本文件）
- [ ] `NOTICE`（Apache 2.0 要求：包含所有 Apache 2.0 依赖的版权声明）
- [ ] 若包含 PySide6：在安装包中标注 "包含 Qt/PySide6（LGPL v3）" 并提供源码获取途径

---

*此文件须随项目代码一起发布，不得删除。*
