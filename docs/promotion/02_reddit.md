# Reddit r/LocalLLaMA 帖子

## Title
Show & Tell: Harven AI — Local AI brain with multi-model collaboration (Windows/Linux/macOS, built-in Python)

## Body

Hi r/LocalLLaMA,

I've been working on an open-source local AI project for 3 months and wanted to share it with this community.

### What it is

**Harven AI** is not another Ollama WebUI. It's a complete local AI platform with some features I haven't seen elsewhere:

### Key Differentiators

1. **Multi-model collaboration** — Instead of single-model chat, Harven orchestrates multiple models:
   - Local brain (Ollama) thinks
   - External brain (GPT/DeepSeek) analyzes
   - Vertical models (MusicGen/Stable Diffusion/Bark) execute
   - This is a "collaboration mode" where different models handle different parts of a task

2. **5-layer memory** — Full SQLite persistence, never forgets across conversations:
   - L0: Raw dialog (full persistence)
   - L1: Task memory (AI semantic extraction)
   - L2: Long-term memory (cross-dialog consolidation)
   - Active retrieval (auto-pull relevant history)
   - Owner profile (remembers your preferences)

3. **Controlled evolution** — AI can modify its own code:
   - You say "add a weather feature"
   - AI generates code proposal
   - You review and approve/reject
   - Applied with rollback support
   - Can modify: skills/, templates/, config.yaml
   - Cannot touch: core engine (safety blacklist)

4. **AI-built UI panels** — AI generates custom management panels (accounting/fitness/todo) in iframe sandbox

### Technical specs

| Metric | Value |
|--------|-------|
| Python modules | 113 |
| REST API endpoints | 80+ |
| Lines of code | 160,000+ |
| Built-in skills | 10 |
| i18n keys | 853 (Chinese + English) |

### Cross-platform

All platforms ship with **built-in Python 3.13** — no Python installation required:

| Platform | Size | Python | Launch |
|----------|------|--------|--------|
| Windows | 16 MB | Built-in 3.13 | Double-click .bat |
| Linux | 37 MB | Built-in 3.13.15 | Double-click .desktop |
| macOS | 52 MB | Built-in 3.13.15 (Universal) | Double-click .command |

### vs Other solutions

| | Harven AI | OpenWebUI | OpenClaw | ChatGPT |
|---|-----------|-----------|----------|---------|
| Privacy | ✅ Local | ✅ Local | ✅ Local | ❌ Cloud |
| Memory | ✅ 5-layer | ⚠️ Session | ⚠️ File | ❌ Forgets |
| Knowledge base | ✅ Built-in FAISS | ❌ None | ⚠️ Plugins | ❌ None |
| Multi-model | ✅ Collaboration | ❌ Single | ❌ Single | ❌ Single |
| Self-modifying | ✅ Controlled | ❌ No | ❌ No | ❌ No |

### Links

- **GitHub**: https://github.com/AmenMonk/Harven
- **License**: Apache 2.0
- **Download**: See GitHub Releases for all 3 platforms

### What I'd love feedback on

1. Is the multi-model collaboration approach interesting?
2. The "controlled evolution" (AI modifies its own code) — is this a direction worth pursuing?
3. Cross-platform packaging with built-in Python — how's the size tradeoff?
4. Any features you'd want to see?

Happy to answer any questions!

---

**发布步骤**：
1. 打开 https://reddit.com/r/LocalLLaMA
2. 登录（没有账号先注册）
3. 点 "Create Post" → 选 "Share" 类型
4. 标题粘贴上面的 Title
5. 正文粘贴上面的 Body（Markdown 格式）
6. 发布前先在社区评论 2-3 个其他帖子（避免被当纯广告）

**发布时间**：美国时间周二/周三上午 9-11 点（北京时间晚上 9-11 点）
