# Hacker News — Show HN 帖子

## Title
Show HN: Harven AI – Local AI brain with multi-model collaboration and self-modifying code

## Body

Hi HN,

I built an open-source local AI platform called Harven AI. It's not another chat UI — it tries to do something different: multi-model collaboration.

### What's different

**1. Multi-model collaboration**

Instead of one model doing everything, Harven orchestrates multiple models:
- Local brain (Ollama) for general reasoning
- External brain (GPT/DeepSeek) for analysis
- Vertical models (MusicGen, Stable Diffusion, Bark) for specialized execution

Example: "Generate background music for my video" → external brain analyzes the mood → tunes parameters → local MusicGen executes. One model can't do this well; a team of specialized models can.

**2. 5-layer memory**

Full SQLite persistence across conversations. L0 raw dialog, L1 task memory (AI-extracted), L2 long-term consolidation, active retrieval, owner profile. The AI never forgets — it remembers your preferences, identity, and history.

**3. Controlled evolution (self-modifying code)**

The AI can modify its own code, but safely:
- User requests a feature ("add weather lookup")
- AI generates a code proposal
- User reviews the description
- Approve/Reject
- Applied with rollback

Can modify: skills/, templates/, config.yaml. Cannot touch: core engine (blacklist).

**4. AI-built UI panels**

AI generates custom management panels (accounting, fitness, todo) that run in iframe sandbox. You describe what you want, AI writes the HTML/JS/CSS, installs it, and you have a new panel.

### Technical details

- 113 Python modules, 80+ REST endpoints, 160K+ lines
- Built-in FAISS knowledge base (40+ file formats)
- 3-layer security (file scan + Docker sandbox + device fingerprint)
- Bilingual (Chinese/English, 853 i18n keys)
- Cross-platform: Windows/Linux/macOS, all with built-in Python 3.13 (no installation needed)

### Why local?

Privacy, cost, offline capability, and data sovereignty. Your conversations, knowledge base, and memory stay on your machine. No cloud, no tracking, no monthly fee.

### Comparison

- vs OpenWebUI: OpenWebUI is a chat UI for Ollama. Harven is a full platform (knowledge base + memory + skills + panels + collaboration + market).
- vs OpenClaw: OpenClaw connects to 50+ channels (Discord/Slack/WeChat). Harven focuses on understanding one person deeply (5-layer memory + knowledge base + controlled evolution).
- vs ChatGPT Desktop: ChatGPT is cloud-based. Harven is 100% local, works offline, open source.

### Links

- GitHub: https://github.com/AmenMonk/Harven
- License: Apache 2.0
- Downloads: Windows (16MB) / Linux (37MB) / macOS (52MB) — all with built-in Python

### Questions I'd love to discuss

1. Is multi-model collaboration a useful direction, or is single-model enough?
2. For self-modifying code: what safety mechanisms would you want to see?
3. The built-in Python packaging — is the size tradeoff (37MB Linux, 52MB macOS) worth it for "just works" UX?

I'm happy to answer any questions about the architecture, the collaboration mode, or the memory system.

---

**发布步骤**：
1. 打开 https://news.ycombinator.com
2. 登录（没有账号先注册，需要等几天才能发帖）
3. 点 "Submit"
4. Title 粘贴上面的 Title
5. URL 填 GitHub 仓库链接：https://github.com/AmenMonk/Harven
6. 或选 "text" 帖子，粘贴上面的 Body
7. 发布

**发布时间**：美国时间周二上午 9-11 点（北京时间晚上 9-11 点）
**注意**：新账号不能立即发帖，需要先参与几天评论
