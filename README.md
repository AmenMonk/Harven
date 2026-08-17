# 🦆 Harven AI — Personal AI Brain

**Not another chat UI. Multi-model collaboration: local brain thinks, external brain analyzes, vertical models execute. 5-layer memory never forgets. AI builds your UI panels.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Platform: Windows/Linux/macOS](https://img.shields.io/badge/Platform-Windows%2FLinux%2FmacOS-green)](#downloads)
[![Python: 3.13 built-in](https://img.shields.io/badge/Python-3.13%20built--in-orange)](#downloads)
[![Bilingual: 中文/EN](https://img.shields.io/badge/i18n-中文%2FEN-yellow)](#bilingual)

个人专属AI大脑 — 纯本地运行，隐私安全，永久免费。

---

## 📥 Downloads

| Platform | Size | Python | Launch |
|----------|------|--------|--------|
| **[Windows](https://github.com/AmenMonk/Harven/releases/latest)** | 16.1 MB | Built-in 3.13 | Double-click `.bat` |
| **[Linux](https://github.com/AmenMonk/Harven/releases/latest)** | 37.4 MB | Built-in 3.13.15 | Double-click `.desktop` |
| **[macOS](https://github.com/AmenMonk/Harven/releases/latest)** | 52.1 MB | Built-in 3.13.15 (Universal) | Double-click `.command` |

> 📖 [Documentation Center](https://github.com/AmenMonk/Harven/releases/latest) (guide + technical docs)

**No Python installation required** — all platforms ship with built-in Python 3.13.

---

## 🤔 Why Harven AI?

| Feature | Harven AI | ChatGPT | OpenWebUI | OpenClaw |
|---------|-----------|---------|-----------|----------|
| **Privacy** | ✅ 100% local | ❌ Cloud | ✅ Local | ✅ Local |
| **Cost** | ✅ Free forever | ❌ $20/mo | ✅ Free | ✅ Free |
| **Offline** | ✅ Works offline | ❌ No | ✅ Yes | ✅ Yes |
| **Memory** | ✅ 5-layer, never forgets | ❌ Forgets across chats | ⚠️ Session only | ⚠️ File-based |
| **Knowledge Base** | ✅ Built-in FAISS + 40 formats | ❌ None | ❌ None | ⚠️ Via plugins |
| **Multi-model Collaboration** | ✅ Analyze→Tune→Execute | ❌ Single model | ❌ Single model | ❌ Single model |
| **Vertical Models** | ✅ Music/Image/Voice/Medical | ⚠️ Limited | ❌ None | ❌ None |
| **AI Builds UI Panels** | ✅ AI generates panels | ❌ No | ❌ No | ❌ No |
| **Self-Modifying Code** | ✅ Controlled evolution | ❌ No | ❌ No | ❌ No |
| **Skill + Bundle Ecosystem** | ✅ HarvenHub market | ❌ Plugins | ⚠️ Limited | ✅ Skills |
| **IoT Device Control** | ✅ AI-generated drivers | ❌ No | ❌ No | ❌ No |
| **Open Source** | ✅ Apache 2.0 | ❌ Closed | ✅ Open | ✅ MIT |

---

## 🌟 Core Innovations (4 Unique Features)

### 1. 🧬 Controlled Evolution (独创)
AI modifies its own code — safely.
```
User: "帮我加一个天气查询功能"
  → Harven generates code proposal
  → User reviews description
  → Approve / Reject
  → Apply (with rollback)
```
**Can modify:** skills/ templates/ static/ config.yaml
**Cannot touch:** engine.py web_app.py (core blacklist)

### 2. 🧠 5-Layer Memory (永不遗忘)
| Layer | Function |
|-------|----------|
| L0 Raw Dialog | Full SQLite persistence, never lost |
| L1 Task Memory | AI semantic extraction, structured storage |
| L2 Long-term | Cross-dialog knowledge consolidation |
| Active Retrieval | Auto-pull relevant history during chat |
| Owner Profile | Remembers your preferences, identity, habits |

*vs ChatGPT: forgets everything when chat ends. vs OpenClaw: file-based MEMORY.md.*

### 3. 🤝 Collaboration Mode (多模型分工)
| Mode | Flow | Use Case |
|------|------|----------|
| ☁️ Cloud-Local | External brain analyzes → tune params → **local vertical model executes** | Privacy + capability |
| 🏠 Local-Dual | Local general model schedules → tune params → vertical model executes | Fully offline |

*Single-model chatbots can't do this. Harven orchestrates multiple models like a team.*

### 4. 🎨 Vertical Model Orchestration (专业执行)
| Model | Capability |
|-------|-----------|
| MusicGen | Generate music from text |
| Stable Diffusion | Image generation |
| Bark | Voice synthesis |
| MiniCPM-V | Image understanding |
| HuaTuo | Medical Q&A |

*Vertical models execute independently — no knowledge base/skills/persona interference.*

---

## 🗺️ Feature Panorama (25+ Features)

### 💬 Conversation & Reasoning
- 🏠 **Local Mode** — Local model + knowledge base + skills + persona
- 🤖 **External Brain** — GPT/DeepSeek/GLM/Doubao + knowledge base + skills
- 🎨 **Vertical Mode** — Music/image/voice/medical specialized execution
- 🤝 **Collaboration Mode** — Multi-model teamwork (cloud-local / local-dual)
- 🌐 **Web Search** — Bing built-in + SearXNG enhancement
- 🎙️ **Voice Input** — faster-whisper STT, language follows UI

### 🧠 Memory & Knowledge
- 📚 **3-Layer Knowledge Base** — Documents (40+ formats) → FAISS vectors → Auto-injection
- 🧠 **5-Layer Memory** — L0 raw + L1 task + L2 long-term + active retrieval + owner profile
- 🗂️ **Knowledge Packs** — Shareable knowledge bundles
- 📦 **Bundle** — Skill + Agent + Knowledge one-click solution

### 🔧 Tools & Automation
- ⚡ **Skill System** — Executable scripts orchestrating multi-model tasks
- 🤖 **Agent (专项培养)** — Role-based personas with trigger-word routing
- 📊 **UI Panel System** — AI builds accounting/fitness/todo panels (iframe sandbox)
- 🔄 **Workflow Editor** — Visual flow orchestration
- ⏰ **Scheduled Tasks** — apscheduler-based automation
- 📡 **Monitor Center** — Auto-track topic updates
- 🧬 **Controlled Evolution** — AI modifies its own code (user-approved)

### 🔒 Security & Privacy
- 🛡️ **3-Layer Defense** — File scan (5 checks) + Code sandbox (Docker) + Device fingerprint
- 🦠 **Antivirus** — ClamAV integration
- 🔐 **Data Vault** — Encrypted sensitive info storage
- 🔑 **Privacy Levels** — Control what external brain sees
- 🆔 **Device Binding** — Prevent unauthorized cloning

### 🌐 Connectivity
- 📱 **Mobile Link** — Scan QR for remote chat
- 💬 **IM Channels** — WeChat Work / DingTalk / Feishu
- 🏠 **IoT Control** — MQTT/HTTP/WebSocket + **AI-generated drivers**
- 🌍 **Public Network** — Access from phone anywhere
- 🛒 **HarvenHub Market** — Community skill/agent/bundle sharing

### 🎨 Customization
- 🌗 **Themes** — Dark/Light/System
- 📝 **Bilingual i18n** — 中文/English (853 keys, full coverage)
- 🦆 **Brand Customization** — Custom name/icon/avatar
- 💾 **Backup & Restore** — One-click data vault
- 🛠️ **Fallback Mode** — Auto-recovery from failures

---

## 🚀 Quick Start

### Windows
```bash
# 1. Download HarvenAI_v0.3.1_Windows.zip
# 2. Extract
# 3. Double-click 安装哈文.bat
# 4. Browser opens http://localhost:7000
# 5. Password: harven2026
```

### Linux
```bash
# 1. Download HarvenAI_v0.3.1_Linux.zip
unzip HarvenAI_v0.3.1_Linux.zip -d HarvenAI
cd HarvenAI
bash install.sh    # Installs dependencies + creates desktop shortcut
# Double-click Harven AI icon on desktop
```

### macOS
```bash
# 1. Download HarvenAI_v0.3.1_macOS.zip
unzip HarvenAI_v0.3.1_macOS.zip -d HarvenAI
cd HarvenAI
bash install.sh    # Installs dependencies + creates desktop shortcut
# Double-click HarvenAI.command on desktop
```

**No Python installation needed** — built-in Python 3.13 on all platforms.

---

## 🏗️ Technical Architecture

| Metric | Value |
|--------|-------|
| **Python modules** | 113 |
| **REST API endpoints** | 80+ |
| **Lines of code** | 160,000+ |
| **Built-in skills** | 10 |
| **i18n keys** | 853 (zh_CN + en_US) |
| **Supported file formats** | 40+ (txt/md/csv/pdf/docx/xlsx/pptx) |

### Tech Stack
| Layer | Technology |
|-------|-----------|
| **Backend** | Python + Flask + SQLite + FAISS |
| **Frontend** | HTML + Bootstrap5 + JavaScript (SSE streaming) |
| **AI** | Ollama (local) + DeepSeek/GLM/Doubao/OpenAI (external) |
| **Vertical Models** | MusicGen / Stable Diffusion / Bark / MiniCPM-V / HuaTuo |
| **Desktop** | pywebview (optional) |
| **Voice** | faster-whisper (STT) |
| **Search** | Bing + SearXNG |
| **Sandbox** | Docker (preferred) / subprocess (fallback) |

### Cross-Platform Support
| Platform | Python Source | Package Manager | Screenshot | Trash |
|----------|-------------|-----------------|------------|-------|
| Windows | Built-in 3.13 | winget | Win+Shift+S | Clear-RecycleBin |
| Linux | Built-in 3.13.15 | apt/yum/pacman | scrot | rm ~/.local/share/Trash |
| macOS | Built-in 3.13.15 (Universal) | brew | screencapture | rm ~/.Trash |

---

## 🌍 Bilingual (中英双语)

Harven AI is fully bilingual — interface, AI responses, voice recognition, and system messages all switch together.

| Component | 中文 | English |
|-----------|------|---------|
| UI (28 tabs) | ✅ | ✅ |
| AI responses | ✅ | ✅ |
| Voice STT | zh | en |
| Command routing | ✅ | ✅ |
| FC prompts | ✅ | ✅ |

Settings → Language → "界面与回答语言" → 跟随系统 / 简体中文 / English

---

## 🤝 Contributing

Contributions welcome! 

1. Fork the repository
2. Create feature branch: `git checkout -b feature/your-feature`
3. Commit: `git commit -m "Add: your feature"`
4. Push: `git push origin feature/your-feature`
5. Open a Pull Request

### Development Setup
```bash
git clone https://github.com/AmenMonk/Harven.git
cd Harven
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp config.yaml.example config.yaml
python web_app.py
```

### Project Structure
```
HarvenAI/
├── src/core/          # 30+ kernel modules (engine, memory, security, etc.)
├── skills/            # 10 built-in skills (accounting, code audit, music, etc.)
├── templates/         # Web UI (index.html + 28 setting tabs)
├── static/            # JS/CSS/icons
├── locales/           # i18n (zh_CN.json + en_US.json)
├── data/              # User data (sqlite, faiss, knowledge, skills)
├── docs/              # Documentation center
├── web_app.py         # Flask web server (80+ API endpoints)
└── platform.py        # Cross-platform abstraction
```

---

## 📄 License

**Apache License 2.0** — Free to use, modify, and distribute.

---

## 👤 Author

**AmenMonk** · 📧 38552540@qq.com

> 🦆 Harven AI — Your personal AI brain that lives on your computer, not in the cloud.
> 
> 哈文 — 住在电脑里的AI大脑，数据主权在你手里。

---

## ⭐ Star History

If you find Harven AI useful, please consider giving it a star! ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=AmenMonk/Harven&type=Date)](https://star-history.com/#AmenMonk/Harven&Date)
