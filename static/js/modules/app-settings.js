/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — SETTINGS module ===== */
console.log('[app-settings] ⏳ 开始加载...');

let taskFilter = 'all';
let _lastModalOpenTime = 0;  // ★ 必须在 openModal 之前声明

// ===== Modal =====
function openModal(id) {
    var el = document.getElementById(id);
    if (!el) { console.error('[openModal] 元素不存在:', id); return; }
    _lastModalOpenTime = Date.now();
    el.classList.add('active');
    el.style.display = 'flex';
    el.style.visibility = 'visible';
    el.style.opacity = '1';
    el.style.animation = 'none';  // 🔑 禁用动画避免 opacity 卡 0
    console.log('[openModal] ✅ 已打开:', id, '| display:', el.style.display);

    // 打开设置面板时重置脏状态
    if (id === 'settingsModal') {
        window._settingsDirty = false;
        if (typeof _updateSaveBtnState === 'function') setTimeout(_updateSaveBtnState, 100);
    }

    // Lazy-load data per modal type
    if (id === 'settingsModal' && typeof loadSettings === 'function') {
        loadSettings();
        // 🔑 同步初始化面板显隐（纯 class toggle，无需等 RAF）
        initSettingsPanelVisibility();
        setTimeout(() => {
            const tab = document.getElementById('tab-plugins');
            if (tab && tab.classList.contains('active')) loadInstalledPlugins();
            if (typeof _updateStbArrows === 'function') _updateStbArrows();
        }, 200);
    }
    if (id === 'monitorModal') {
        if (typeof loadMonitorTopics === 'function') loadMonitorTopics();
        if (typeof updateMonitorBadge === 'function') updateMonitorBadge();
    }
    if (id === 'taskModal' && typeof loadTasks === 'function') loadTasks();
    if (id === 'kbModal' && typeof loadKbList === 'function') loadKbList();
    if (id === 'specModal' && typeof loadSpecializations === 'function') loadSpecializations();
}

// 左侧边栏快捷入口（包装函数，保持 openModal 的懒加载逻辑）
function openKbModal() { openModal('kbModal'); }
function openTaskModal() { openModal('taskModal'); }
function openMonitorModal() { openModal('monitorModal'); }

// 立即导出，确保 HTML 中的 onclick 能调用
window.openModal = openModal;
window.openKbModal = openKbModal;
window.openTaskModal = openTaskModal;
window.openMonitorModal = openMonitorModal;

// 处理页面加载前调用的 openModal（队列）
window._openModalReady = true;
if (window._openModalQueue && window._openModalQueue.length) {
    console.log('[Harven] 补执行延迟的 openModal:', window._openModalQueue);
    window._openModalQueue.forEach(function(id) { openModal(id); });
    window._openModalQueue = [];
}

function closeModal(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('active');
    // 清理 _forceOpenModal 可能留下的内联样式
    el.style.display = '';
    el.style.visibility = '';
    el.style.opacity = '';
    el.style.zIndex = '';
    el.style.pointerEvents = '';
}

async function loadTasks() {
    try {
        const resp = await fetch('/api/tasks');
        const tasks = await resp.json();
        renderTasks(tasks || []);
    } catch (e) {
        console.error('[Harven] 加载任务失败:', e);
        renderTasks([]);
    }
}

function renderTasks(tasks) {
    const el = document.getElementById('taskList');
    const empty = document.getElementById('taskEmpty');
    const filtered = taskFilter === 'all' ? tasks : tasks.filter(t => t.status === taskFilter);

    if (!filtered.length) {
        el.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';

    // Sort: pending first, then by creation time
    const sorted = filtered.slice().sort((a, b) => {
        if (a.status === 'done' && b.status !== 'done') return 1;
        if (a.status !== 'done' && b.status === 'done') return -1;
        return 0;
    });

    const priorityEmoji = { high: '🔴', medium: '🟡', low: '🟢' };
    el.innerHTML = sorted.map(t => `
        <div class="task-item ${t.status === 'done' ? 'done' : ''}" data-id="${t.id}">
            <div class="task-item-check" onclick="toggleTask('${t.id}')">${t.status === 'done' ? '✓' : ''}</div>
            <div class="task-item-body">
                <div class="task-item-title">${escHtml(t.title || t.content || '未命名')}</div>
                <div class="task-item-meta">
                    <span>${priorityEmoji[t.priority] || '🟡'} ${t.priority || 'medium'}</span>
                    ${t.progress !== undefined ? `
                    <span class="task-item-progress"><div class="task-item-progress-bar" style="width:${t.progress || 0}%"></div></span>
                    <span>${t.progress || 0}%</span>` : ''}
                </div>
            </div>
            <div class="task-item-actions">
                <button onclick="deleteTask('${t.id}')" title="删除">🗑</button>
            </div>
        </div>
    `).join('');
}

function filterTasks(filter, btn) {
    taskFilter = filter;
    document.querySelectorAll('.task-filter').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    loadTasks();
}

async function addTask() {
    const title = document.getElementById('taskTitleInput').value.trim();
    if (!title) return;
    const priority = document.getElementById('taskPriority').value;
    try {
        await fetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, priority })
        });
        document.getElementById('taskTitleInput').value = '';
        loadTasks();
        toast('任务已添加');
    } catch { toast('添加失败'); }
}

async function toggleTask(id) {
    try {
        await fetch(`/api/tasks/${id}/complete`, { method: 'POST' });
        loadTasks();
    } catch {}
}

async function deleteTask(id) {
    try {
        await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
        loadTasks();
        toast('任务已删除');
    } catch {}
}

// ===== Settings =====
// Safe getter for DOM elements that may not exist in template
function _safeGetEl(id, prop, fallback) {
    const el = document.getElementById(id);
    return el ? el[prop] : fallback;
}
async function loadSettings() {
    let currentSettings = {};  // ★ 移到函数内，避免 TDZ 错误
    // 辅助：安全设置DOM值（元素存在才写）
    var _s = function(id, prop, val){
        var el = document.getElementById(id);
        if (!el) return;
        if (prop === 'checked') el.checked = val;
        else el.value = val;
    };
    try {
        const r = await fetch('/api/settings');
        currentSettings = await r.json();
        const u = currentSettings.user || {};
        const o = currentSettings.ollama || {};
        const ui = currentSettings.ui || {};
        const mem = currentSettings.memory || {};
        const app = currentSettings.app || {};

        // 基本信息
        _s('setNickname','value',u.nickname||'');
        _s('setAiName','value',app.name||'Harven AI');
        _s('setMainLang','value',app.language||'zh-CN');
        // ★ 2026-08-16: 界面语言（i18n）
        _s('setUiLang','value',(currentSettings.language||{}).ui_lang||'follow');
        _s('setLangMode','value',(currentSettings.language||{}).mode||'translate');

        // AI模型
        const activeModel = o.default_model || '';
        await populateModelSelect(activeModel);
        _s('setReasoning','value',o.reasoning_mode||'standard');
        _s('setScheduling','value',o.model_scheduling||'auto');
        _s('setOllamaUrl','value',o.base_url||'http://localhost:11434');
        _s('setTemperature','value',(currentSettings.ai||{}).temperature||0.7);
        var tempEl = document.getElementById('tempValue');
        if (tempEl) tempEl.textContent = (currentSettings.ai||{}).temperature||0.7;
        _s('setContextTurns','value',mem.working_context_turns||20);
        previewModel(activeModel);

        // 个人画像
        _s('setProfession','value',u.profession||'');
        _s('setAnswerPref','value',u.answer_preference||'detailed');
        userInterests = u.interests ? u.interests.slice() : [];
        renderInterestTags();

        // 安全隐私
        _s('setAutoLock','value',(currentSettings.security||{}).auto_lock_minutes||0);
        _s('setAntivirusPath','value',(currentSettings.security||{}).antivirus_path||'');
        _s('setAntivirusEnabled','checked',(currentSettings.security||{}).antivirus_enabled===true);

        // 外观
        const theme = ui.theme || 'dark';
        document.querySelectorAll('.theme-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.theme === theme);
        });
        _s('setFontSize','value',ui.font_size||'medium');
        _s('setBubbleStyle','value',ui.bubble_style||'default');

        // 本地引擎状态
        checkOllamaStatusDetail();

        // System info (about tab)
        loadSystemInfo();
        // 协作模式设置
        if (typeof loadCollabSettings === 'function') {
            loadCollabSettings().catch(() => {});
        }
    } catch (e) { console.error('Load settings error:', e); }
}

// ===== VRAM 不足的模型标记（从硬件检测 API 获取） =====
let _detectedVramGb = null;

async function getDetectedVram() {
    if (_detectedVramGb !== null) return _detectedVramGb;
    try {
        const r = await fetch('/api/hardware/detect');
        if (r.ok) {
            const d = await r.json();
            _detectedVramGb = d.gpu_vram_gb || 0;
            return _detectedVramGb;
        }
    } catch (e) { /* ignore */ }
    _detectedVramGb = 0;
    return 0;
}

// 模型→显存需求映射（GB，保守估算，含系统开销）
const MODEL_VRAM_REQ = {
    'qwen2.5:3b': 4,
    'qwen2.5:7b': 7,
    'qwen2.5:14b': 11,
    'qwen2.5:32b': 22,
    'qwen3:14b': 12,
    'qwen3.5:14b': 12,
    'qwen3.6:35b-a3b': 24,
    'deepseek-r1:8b': 8,
    'deepseek-r1:14b': 12,
    'deepseek-r1:32b': 22,
    'minicpm-v:8b': 8,
    'phi3:3.8b': 5,
    'llama3.2:8b': 9,
};

function vramWarning(modelId) {
    const req = MODEL_VRAM_REQ[modelId];
    if (!req || _detectedVramGb === null) return '';
    if (req > _detectedVramGb) {
        return ' ⚠️ 显存不足';
    }
    if (req > _detectedVramGb * 0.9) {
        return ' ⚡ 显存紧张';
    }
    return '';
}

// ===== 动态加载模型下拉列表 =====
async function populateModelSelect(activeModel) {
    const sel = document.getElementById('setModel');
    if (!sel) { console.warn('[populateModelSelect] #setModel 不存在!'); return; }
    try {
        // 先快速填充Ollama模型列表
        const modelsR = await fetch('/api/ollama/models');
        if (!modelsR.ok) throw new Error(modelsR.status);
        const models = await modelsR.json();

        // ★ 同时获取自动发现的本地模型（如华佗）
        let discoveredModels = [];
        try {
            const dr = await fetch('/api/system/scan');
            if (dr.ok) {
                const ddata = await dr.json();
                const knowledge = ddata.knowledge || {};
                // ModelScope + HuggingFace 模型
                discoveredModels = [
                    ...(knowledge.modelscope_models || []).map(m => ({
                        name: m.name,
                        label: `🧠 ${m.name} (${m.source})`,
                        type: m.model_type || 'unknown',
                        path: m.path
                    })),
                    ...(knowledge.huggingface_models || []).map(m => ({
                        name: m.name,
                        label: `🧠 ${m.name} (${m.source})`,
                        type: 'unknown',
                        path: m.path
                    }))
                ];
            }
        } catch(e) { console.warn('[populateModelSelect] 获取本地模型失败:', e); }

        if (models && models.length) {
            // 过滤掉嵌入模型（不用于对话）
            const chatModels = models.filter(m => m !== 'nomic-embed-text:latest');

            const current = activeModel || chatModels[0];
            sel.innerHTML = '';

            // Ollama模型
            const ollamaGroup = document.createElement('optgroup');
            ollamaGroup.label = '🦙 Ollama模型';
            chatModels.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                if (m === current) opt.selected = true;
                ollamaGroup.appendChild(opt);
            });
            sel.appendChild(ollamaGroup);

            // 本地下载的模型（如华佗）
            if (discoveredModels.length > 0) {
                const localGroup = document.createElement('optgroup');
                localGroup.label = '📦 本地下载的模型';
                discoveredModels.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.path || m.name;
                    opt.textContent = m.label;
                    opt.dataset.loader = 'transformers';
                    opt.dataset.path = m.path;
                    localGroup.appendChild(opt);
                });
                sel.appendChild(localGroup);
            }

            // 同步 modelSelect（如有）
            const ms = document.getElementById('modelSelect');
            if (ms) {
                const cur = activeModel || ms.value;
                ms.innerHTML = '';
                chatModels.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m;
                    opt.textContent = m;
                    if (m === cur) opt.selected = true;
                    ms.appendChild(opt);
                });
                discoveredModels.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.path || m.name;
                    opt.textContent = m.label;
                    ms.appendChild(opt);
                });
            }
            // 更新描述
            previewModel(sel.value);

            // 后台异步更新 VRAM 警告（不阻塞）
            getDetectedVram().then(vram => {
                if (vram > 0) {
                    const opts = sel.querySelectorAll('option');
                    opts.forEach(opt => {
                        const req = MODEL_VRAM_REQ[opt.value];
                        if (req && req > vram) {
                            opt.textContent += ' ⚠️ 显存不足';
                        } else if (req && req > vram * 0.9) {
                            opt.textContent += ' ⚡ 显存紧张';
                        }
                    });
                }
            }).catch(() => {});
        }
    } catch (e) {
        console.warn('populateModelSelect failed:', e);
    }
}

// switchSettingsTab 定义在文件末尾增强版中

const modelDescMap = {
    'qwen2.5:3b':      '轻量通用，4GB显存可跑 — 入门首选',
    'qwen2.5:7b':      '日常对话、快速响应 — 适合简单问答',
    'qwen2.5:14b':     '深度思考、复杂推理 — 12GB显存推荐',
    'deepseek-r1:8b':  '轻量推理模型，速度快',
    'deepseek-r1:14b': '深度推理、技术分析 — 需12GB显存',
    'deepseek-r1:32b': '大规模推理 — 需24GB显存',
    'minicpm-v:8b':    '视觉多模态，可识别图片内容',
    'qwen3:14b':       '通用强，支持思考模式开关 — 需12GB显存',
    'qwen3.5:14b':     '多模态，支持文字+图片+音频 — 需12GB显存',
    'qwen3.6:35b-a3b': 'MoE架构35B — 需24GB显存，12GB显卡无法运行',
};

function previewModel(modelId) {
    const desc = modelDescMap[modelId] || (modelId ? `已选择: ${modelId}` : '请选择模型');
    const el = document.getElementById('modelDesc');
    if (el) el.textContent = desc;
}

// Interest tags
function addInterestTag() {
    const input = document.getElementById('setInterests');
    const val = input.value.trim();
    if (val && !userInterests.includes(val)) {
        userInterests.push(val);
        renderInterestTags();
    }
    input.value = '';
}

function removeInterestTag(idx) {
    userInterests.splice(idx, 1);
    renderInterestTags();
}

function renderInterestTags() {
    const el = document.getElementById('interestTagsList');
    if (!userInterests.length) { el.innerHTML = ''; return; }
    el.innerHTML = userInterests.map((t, i) =>
        `<span class="tag-item">${escHtml(t)}<span class="tag-remove" onclick="removeInterestTag(${i})">×</span></span>`
    ).join('');
}

async function saveAllSettings() {
    // 辅助：安全读取DOM元素值（元素不存在时返回默认值）
    var _g = function(id, prop, def){
        var el = document.getElementById(id);
        if (!el) return def;
        return prop === 'checked' ? el.checked : el.value;
    };

    // 先保存品牌定制数据（外观Tab中的名称/Emoji/头像）
    const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
    if (brand.name) _applyBrandName(brand.name);
    if (brand.emoji) _applyBrandEmoji(brand.emoji);

    // ★ 2026-08-16: setUiLang 统一控制界面+AI回答语言 — 解析后同步到 app.language
    var _ui_lang_raw = _g('setUiLang','value','follow');
    var _resolved_ui = _ui_lang_raw === 'follow'
        ? ((navigator.language || 'zh-CN').toLowerCase().indexOf('zh') === 0 ? 'zh_CN' : 'en_US')
        : _ui_lang_raw;
    var _app_lang_map = { 'zh_CN': 'zh-CN', 'en_US': 'en' };
    var _app_lang_sync = _app_lang_map[_resolved_ui] || 'zh-CN';

    const data = {
        app: {
            name: _g('setAiName','value','Harven AI'),
            language: _app_lang_sync,  // ★ 同步：界面语言=AI回答语言
        },
        ollama: {
            default_model: _g('setModel','value',''),
            reasoning_mode: _g('setReasoning','value','standard'),
            model_scheduling: _g('setScheduling','value','round_robin'),
            base_url: _g('setOllamaUrl','value','http://localhost:11434'),
        },
        ai: {
            temperature: parseFloat(_g('setTemperature','value','0.7')),
        },
        memory: {
            working_context_turns: parseInt(_g('setContextTurns','value','20')) || 20,
        },
        user: {
            nickname: _g('setNickname','value',''),
            profession: _g('setProfession','value',''),
            answer_preference: _g('setAnswerPref','value','direct'),
            interests: userInterests,
        },
        ui: {
            theme: (document.querySelector('.theme-btn.active') ? document.querySelector('.theme-btn.active').dataset.theme : null) || 'dark',
            font_size: _g('setFontSize','value','medium'),
            bubble_style: _g('setBubbleStyle','value','rounded'),
        },
        // ★ 2026-08-16: 语言设置 — setUiLang 统一控制界面+AI回答语言
        language: {
            main: _app_lang_sync,
            mode: _g('setLangMode','value','translate'),
            ui_lang: _ui_lang_raw,
        },
    };
    try {
        const r = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const res = await r.json();
        if (res.error) { toast('❌ 保存失败: ' + res.error); return; }
        // Also switch model if changed
        const modelId = document.getElementById('setModel').value;
        try {
            await fetch('/api/ollama/switch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: modelId })
            });
        } catch {}
        // ★ 刷新主界面模型状态栏
        if (typeof checkOllamaStatus === 'function') {
            setTimeout(checkOllamaStatus, 500);
        }
        // Apply theme & cache for next page load (prevents flash)
        const theme = data.ui.theme || 'dark';
        document.documentElement.setAttribute('data-theme', theme === 'system' ? 
            (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme);
        localStorage.setItem('harven_theme', JSON.stringify(theme));
        // Apply font size
        document.documentElement.style.fontSize =
            data.ui.font_size === 'small' ? '14px' : data.ui.font_size === 'large' ? '17px' : '';
        // ★ 2026-08-16: 界面语言立即生效（follow 时按主语言解析）
        if (window.HarvenI18n) {
            var _uiLang = data.language.ui_lang;
            if (_uiLang === 'follow') {
                _uiLang = (data.language.main || 'zh-CN').indexOf('zh') === 0 ? 'zh_CN' : 'en_US';
            }
            if (_uiLang !== HarvenI18n.getLang()) HarvenI18n.setLang(_uiLang, false);
        }
        toast(typeof HarvenI18n !== 'undefined' && HarvenI18n.getLang() === 'en_US' ? '✅ Settings saved' : '✅ 设置已保存');
        // 清除脏状态
        window._settingsDirty = false;
        _updateSaveBtnState();
    } catch (e) { toast('❌ 保存失败: ' + e.message); }
}

// ===== 脏状态跟踪（P1 增强）=====
(function setupDirtyTracking() {
    window._settingsDirty = false;
    const saveBtn = document.querySelector('#settingsModal .btn-primary');
    const resetBtn = document.querySelector('#settingsModal .btn-secondary');

    function markDirty() {
        if (window._settingsDirty) return;
        window._settingsDirty = true;
        _updateSaveBtnState();
    }

    function _updateSaveBtnState() {
        // 同时更新顶部和底部保存按钮
        const btns = document.querySelectorAll('#settingsSaveTopBtn, #settingsModal .modal-footer .btn-primary');
        btns.forEach(function(btn) {
            if (!btn) return;
            if (window._settingsDirty) {
                btn.innerHTML = '⚠️ 保存设置（有修改）';
                btn.style.background = 'linear-gradient(135deg, #f59e0b, #d97706)';
                btn.style.boxShadow = '0 2px 8px rgba(245,158,11,0.4)';
            } else {
                btn.innerHTML = '💾 保存设置';
                btn.style.background = '';
                btn.style.boxShadow = '';
            }
        });
    }
    window._updateSaveBtnState = _updateSaveBtnState;

    // 监听所有设置输入变化
    const panel = document.getElementById('settingsModal');
    if (panel) {
        panel.addEventListener('input', function(e) {
            const tag = (e.target.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select') {
                markDirty();
            }
        });
        panel.addEventListener('change', function(e) {
            const tag = (e.target.tagName || '').toLowerCase();
            if (tag === 'select' || (tag === 'input' && (e.target.type === 'checkbox' || e.target.type === 'radio'))) {
                markDirty();
            }
        });
        panel.addEventListener('click', function(e) {
            if (e.target.matches('.theme-btn') || e.target.closest('.theme-btn')) {
                markDirty();
            }
        });
    }
})();

async function resetSettings() {
    if (!confirm('确定要恢复所有设置为默认值吗？')) return;
    var _r = function(id, prop, val){
        var el = document.getElementById(id);
        if (!el) return;
        if (prop === 'checked') el.checked = val; else el.value = val;
    };
    _r('setNickname','value','');
    _r('setAiName','value','Harven AI');
    _r('setMainLang','value','zh-CN');
    _r('setModel','value',document.getElementById('setModel')?.options[0]?.value||'');
    _r('setReasoning','value','standard');
    _r('setScheduling','value','auto');
    _r('setTemperature','value','0.7');
    var tEl = document.getElementById('tempValue'); if (tEl) tEl.textContent = '0.7';
    _r('setContextTurns','value','20');
    _r('setProfession','value','');
    _r('setAnswerPref','value','detailed');
    userInterests = [];
    renderInterestTags();
    _r('setFontSize','value','medium');
    _r('setBubbleStyle','value','default');
    document.querySelectorAll('.theme-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.theme === 'dark');
    });
    toast('已重置为默认值，点击保存生效');
}

async function savePrivacyPassword() {
    const oldPwd = document.getElementById('setPrivacyPwd').value;
    const newPwd = oldPwd; // For first set, old = new
    try {
        const r = await fetch('/api/privacy/set-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ old_password: '', new_password: newPwd })
        });
        const d = await r.json();
        toast(d.error ? '❌ ' + d.error : '✅ 密码已设置');
    } catch (e) { toast('操作失败'); }
}

async function testAntivirus() {
    const path = document.getElementById('setAntivirusPath').value.trim();
    if (!path) { toast('请先输入杀毒软件路径'); return; }
    try {
        const r = await fetch('/api/antivirus/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path })
        });
        const d = await r.json();
        toast(d.success ? '✅ 连接成功: ' + d.version : '❌ ' + (d.error || '连接失败'));
    } catch (e) { toast('测试失败: ' + e.message); }
}

async function loadSystemInfo() {
    try {
        const r = await fetch('/api/system/info');
        const d = await r.json();
        if (d.error) { return; }
        const sc=document.getElementById('sysCpu'), sm=document.getElementById('sysMemory');
        const sd=document.getElementById('sysDisk'), sg=document.getElementById('sysGpu');
        if (sc) sc.textContent = d.cpu_percent + '%';
        if (sm) sm.textContent = `${d.memory.used}GB / ${d.memory.total}GB (${d.memory.percent}%)`;
        if (sd) sd.textContent = `可用 ${d.disk.free}GB / 总计 ${d.disk.total}GB`;
        if (sg) {
            if (d.gpu && d.gpu.name !== 'Unknown') {
                sg.textContent = `${d.gpu.name} — ${d.gpu.memory_allocated}GB / ${d.gpu.memory_total}GB`;
            } else {
                sg.textContent = '未检测到独立显卡';
            }
        }
    } catch {
        ['sysCpu','sysMemory','sysDisk','sysGpu'].forEach(id => {
            const e = document.getElementById(id); if (e) e.textContent = '检测失败';
        });
    }
}

async function exportData() {
    if (!confirm('确定导出所有数据？将下载包含对话记录、知识库、设置的JSON文件。')) return;
    toast('⏳ 正在导出...');
    try {
        const [convRes, kbRes, prefRes] = await Promise.all([
            fetch('/api/conversations').then(r => r.json()).catch(() => []),
            fetch('/api/kb/list').then(r => r.json()).catch(() => []),
            fetch('/api/preferences').then(r => r.json()).catch(() => ({})),
        ]);
        const exportData = {
            export_time: new Date().toISOString(),
            version: '1.0',
            conversations: convRes,
            knowledge_base: kbRes,
            preferences: prefRes,
        };
        const blob = new Blob([JSON.stringify(exportData, null, 2)], {type: 'application/json'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'harven_export_' + new Date().toISOString().slice(0, 10) + '.json';
        a.click();
        URL.revokeObjectURL(url);
        toast('✅ 导出完成');
    } catch (e) {
        toast('❌ 导出失败: ' + e.message);
    }
}

async function clearAllData() {
    if (!confirm('⚠️ 确定清除所有数据？包括所有对话记录、知识库、设置。此操作不可撤销！')) return;
    if (!confirm('再次确认：真的要清除所有数据吗？')) return;
    const allowed = await requirePasswordIfNeeded('clearAllData');
    if (!allowed) return;
    toast('清除功能开发中...');
}

// ===== Local Engine Status Detail =====
async function checkOllamaStatusDetail() {
    const el = document.getElementById('modelStatusDetail');
    try {
        const r = await fetch('/api/ollama/status');
        const d = await r.json();
        el.textContent = d.connected ? '🟢 已连接 - ' + (d.message || '本地引擎运行中') : '🔴 ' + (d.message || '未连接');
        el.style.color = d.connected ? '#22c55e' : '#ef4444';
    } catch {
        el.textContent = '🔴 无法连接';
        el.style.color = '#ef4444';
    }
}

function setTheme(theme) {
    document.querySelectorAll('.theme-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.theme === theme);
    });
}

function closeModal(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('active');
    el.style.display = '';
    el.style.visibility = '';
    el.style.opacity = '';
    el.style.zIndex = '';
    el.style.pointerEvents = '';
}

// ★ 2026-07-14: loadKbList / deleteKbItem / viewKbContent / pasteToKnowledge 统一到 app-knowledge.js
//   原此处重复定义导致覆盖冲突：app-settings.js 后加载覆盖了 app-knowledge.js 的正确版本。
//   现已全部删除，由 app-knowledge.js 提供。

// Close modal on overlay click（防抖保护：300ms内刚打开的modal不被误关）
document.addEventListener('click', e => {
    if (e.target.classList.contains('modal-overlay')) {
        if (Date.now() - _lastModalOpenTime < 300) return; // 防抖：忽略刚打开后的点击
        closeModal(e.target.id);
    }
});

// Keyboard shortcuts: Escape closes active modal
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        const activeModal = document.querySelector('.modal-overlay.active');
        if (activeModal) closeModal(activeModal.id);
    }
});

// ===== Auto Classify =====
var _lastClassifyCount = 0;
var _classifyTimer = null;
var _classifyPending = false;

async function autoClassify(silent = false) {
    // 防止短时间内重复调用
    if (_classifyPending) return;
    _classifyPending = true;
    try {
        const r = await fetch('/api/conversations/auto-classify', {method: 'POST'});
        const data = await r.json();
        if (data.classified > 0 && data.classified !== _lastClassifyCount) {
            _lastClassifyCount = data.classified;
            if (!silent) {
                let detail = `已智能分类 ${data.classified} 个对话`;
                if (data.results && data.results.length > 0) {
                    const catSummary = {};
                    data.results.forEach(r => {
                        catSummary[r.category] = (catSummary[r.category] || 0) + 1;
                    });
                    detail += '：' + Object.entries(catSummary).map(([k,v]) => `${k}×${v}`).join('，');
                }
                toast(detail);
            }
            loadConversations();
        } else if (data.classified > 0) {
            loadConversations();
        }
    } catch {} finally {
        _classifyPending = false;
    }
}

// 延迟触发自动分类（防抖，避免频繁调用）
function scheduleAutoClassify(delayMs = 1500) {
    if (_classifyTimer) clearTimeout(_classifyTimer);
    _classifyTimer = setTimeout(() => autoClassify(true), delayMs);
}

// 页面加载后启动自动分类（从 app-core.js 移入，确保 autoClassify 已定义）
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => autoClassify(true), 3000);
    setInterval(autoClassify, 60000);
    setTimeout(autoClassify, 3000);
});

// ===== Helpers =====
function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function renderMd(text) {
    if (!text) return '';
    try {
        marked.setOptions({
            breaks: true,
            gfm: true,
            highlight: function(code, lang) {
                if (lang && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                return hljs.highlightAuto(code).value;
            }
        });
        return marked.parse(text);
    } catch {
        return escHtml(text).replace(/\n/g, '<br>');
    }
}

function scrollBottom() {
    const el = document.getElementById('chatArea');
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
}

function toast(msg, type) {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'toast';  // reset
    if (type === 'error') el.classList.add('toast-error');
    if (type === 'success') el.classList.add('toast-success');
    el.classList.add('show');
    setTimeout(function() { el.classList.remove('show'); }, 3000);
}

// ===== Plugin Management =====
let currentPluginRepo = null;

// ===== Brand Customization =====
const BRAND_KEY = 'harven_brand';

function loadBrandSettings() {
    const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
    // 名称
    const nameEl = document.getElementById('brandCustomName');
    if (nameEl) nameEl.value = brand.name || '';
    // Emoji
    const emoji = brand.emoji || '🤖';
    _applyBrandEmoji(emoji);
    // 图片头像
    if (brand.avatar) {
        const img = document.getElementById('brandAvatarPreviewImg');
        if (img) { img.src = brand.avatar; img.style.display = 'block'; }
    }
    // 侧边栏名称
    _applyBrandName(brand.name);
}

function saveBrandName() {
    const nameEl = document.getElementById('brandCustomName');
    const name = nameEl ? nameEl.value.trim() : '';
    const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
    brand.name = name;
    localStorage.setItem(BRAND_KEY, JSON.stringify(brand));
    _applyBrandName(name);
    toast('✅ 名称已保存');
}

function resetBrandName() {
    const nameEl = document.getElementById('brandCustomName');
    if (nameEl) nameEl.value = '';
    const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
    delete brand.name;
    localStorage.setItem(BRAND_KEY, JSON.stringify(brand));
    _applyBrandName('');
    toast('已重置为默认名称');
}

function setBrandEmoji(emoji) {
    if (!emoji || !emoji.trim()) return;
    const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
    brand.emoji = emoji.trim();
    localStorage.setItem(BRAND_KEY, JSON.stringify(brand));
    _applyBrandEmoji(emoji.trim());
    // 更新输入框
    const input = document.getElementById('brandCustomEmoji');
    if (input) input.value = emoji.trim();
}

function _applyBrandEmoji(emoji) {
    const previewEl = document.getElementById('brandIconPreview');
    if (previewEl) previewEl.textContent = emoji;
    // 高亮当前选中
    document.querySelectorAll('.brand-emoji-btn').forEach(btn => {
        btn.classList.toggle('active', btn.textContent.trim() === emoji);
    });
    // 侧边栏图标（如果有）
    const sideIcon = document.getElementById('sidebarBrandIcon');
    if (sideIcon) sideIcon.textContent = emoji;
}

function _applyBrandName(name) {
    const displayName = name || 'Harven AI';
    // 侧边栏标题
    const titleEl = document.getElementById('sidebarBrandName') || document.querySelector('.sidebar-title');
    if (titleEl) titleEl.textContent = displayName;
    // 页面标题
    document.title = displayName;
}

function previewBrandAvatar(input) {
    const file = input.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { toast('❌ 图片不超过 2MB'); return; }
    const reader = new FileReader();
    reader.onload = (e) => {
        const img = document.getElementById('brandAvatarPreviewImg');
        if (img) { img.src = e.target.result; img.style.display = 'block'; }
        const preview = document.getElementById('brandIconPreview');
        if (preview) {
            preview.innerHTML = `<img src="${e.target.result}" style="width:44px;height:44px;border-radius:10px;object-fit:cover;">`;
        }
        // 保存到 localStorage
        const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
        brand.avatar = e.target.result;
        localStorage.setItem(BRAND_KEY, JSON.stringify(brand));
        toast('✅ 头像已设置');
    };
    reader.readAsDataURL(file);
}

function clearBrandAvatar() {
    const img = document.getElementById('brandAvatarPreviewImg');
    if (img) { img.src = ''; img.style.display = 'none'; }
    const fileInput = document.getElementById('brandAvatarFile');
    if (fileInput) fileInput.value = '';
    const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
    delete brand.avatar;
    localStorage.setItem(BRAND_KEY, JSON.stringify(brand));
    // 恢复 emoji
    _applyBrandEmoji(brand.emoji || '🤖');
    toast('已清除自定义头像');
}

// 页面初始化时应用品牌设置
(function() {
    const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
    if (brand.name) _applyBrandName(brand.name);
    if (brand.emoji) _applyBrandEmoji(brand.emoji);
    if (brand.avatar) {
        const img = document.getElementById('brandAvatarPreviewImg');
        if (img) { img.src = brand.avatar; }
        const sideIcon = document.getElementById('sidebarBrandIcon');
        if (sideIcon) sideIcon.innerHTML = `<img src="${brand.avatar}" style="width:24px;height:24px;border-radius:6px;object-fit:cover;">`;
    }
})();

// 打开外观设置时加载品牌设置
document.addEventListener('DOMContentLoaded', () => {
    const stb = document.querySelector('.stb[onclick*="appearance"]');
    if (stb) {
        stb.addEventListener('click', () => {
            loadBrandSettings();
        });
    }
    // ★ 2026-07-05: initKnowledgeUpload 已移除（与 app-core.js setupDragDrop 重复且字段名错误）
    // 知识库上传统一由 app-core.js 的 setupDragDrop 处理
});

window.loadBrandSettings = loadBrandSettings;
window.saveBrandName = saveBrandName;
window.resetBrandName = resetBrandName;
window.setBrandEmoji = setBrandEmoji;
window.previewBrandAvatar = previewBrandAvatar;
window.clearBrandAvatar = clearBrandAvatar;


// ===== Security Settings =====

/**
 * 保存单个安全配置项到后端（via evolution proposal 或直接 API）
 */
async function saveSecuritySetting(key, value) {
    try {
        const r = await fetch('/api/security/setting', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value }),
        });
        const d = await r.json();
        if (!d.success) {
            console.warn('[Security] 保存失败:', key, d.error);
        }
    } catch (e) {
        console.warn('[Security] 保存请求失败:', e.message);
    }
}

/**
 * 加载安全设置到 UI
 */
async function loadSecuritySettings() {
    try {
        const r = await fetch('/api/security/settings');
        const d = await r.json();
        if (!d.success) return;
        const s = d.settings || {};
        _setBool('setSsrfEnabled',          s.ssrf_protection       !== false);
        _setBool('setPluginNetWhitelist',    s.plugin_network_whitelist === true);
        _setBool('setRateLimitEnabled',      s.rate_limit_enabled    !== false);
        _setBool('setSandboxEnabled',        s.sandbox_enabled       === true);
        _setBool('setSandboxNetIsolate',     s.sandbox_network_isolate !== false);
        _setBool('setInjectionDetect',       s.injection_detection   !== false);
        _setBool('setFileScanEnabled',       s.file_scan_enabled     === true);
        _setVal('setRateLimitChat',    s.rate_limit_chat   || 30);
        _setVal('setRateLimitGlobal',  s.rate_limit_global || 120);
        _setVal('setSandboxTimeout',   s.sandbox_timeout   || 30);
        _setVal('setDeviceBindPolicy', s.device_bind_policy || 'log_only');
        _setVal('setPluginAllowedDomains', (s.plugin_allowed_domains || []).join('\n'));
        _setVal('setAntivirusPath2',   s.antivirus_path || '');
    } catch (e) {
        // 接口不可用时静默
    }
    // 加载密码保护的操作设置
    await loadPwdProtectActions();
}

// ===== 密码保护的操作 =====
let _pwdProtectedActions = [];

/**
 * 加载密码保护的操作设置，并更新 UI 复选框
 */
async function loadPwdProtectActions() {
    try {
        const r = await fetch('/api/security/settings');
        const d = await r.json();
        if (!d.success) return;
        _pwdProtectedActions = d.settings?.password_protected_actions || [];
        // 更新所有 pwdProtect- 复选框
        document.querySelectorAll('[id^="pwdProtect-"]').forEach(el => {
            const actionId = el.id.replace('pwdProtect-', '');
            el.checked = _pwdProtectedActions.includes(actionId);
        });
    } catch (e) { /* 静默 */ }
}

/**
 * 保存单个操作的密码保护设置（HTML onchange 调用）
 */
async function savePwdProtectAction(actionId, enabled) {
    if (enabled) {
        if (!_pwdProtectedActions.includes(actionId)) {
            _pwdProtectedActions.push(actionId);
        }
    } else {
        _pwdProtectedActions = _pwdProtectedActions.filter(a => a !== actionId);
    }
    try {
        const r = await fetch('/api/security/setting', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'password_protected_actions', value: _pwdProtectedActions }),
        });
        const d = await r.json();
        if (!d.success) console.warn('[PwdProtect] 保存失败:', actionId, d.error);
    } catch (e) {
        console.warn('[PwdProtect] 保存请求失败:', e.message);
    }
}
window.savePwdProtectAction = savePwdProtectAction;

/**
 * 检查操作是否需要密码保护，如果需要则弹出验证框
 * @returns {Promise<boolean>} true = 允许执行, false = 取消
 */
async function requirePasswordIfNeeded(actionId) {
    if (!_pwdProtectedActions.includes(actionId)) return true;
    return await showPasswordVerifyModal();
}

/**
 * 显示密码验证模态框
 * @returns {Promise<boolean>} true = 验证通过, false = 取消
 */
function showPasswordVerifyModal() {
    return new Promise((resolve) => {
        const oldModal = document.getElementById('pwdVerifyModal');
        if (oldModal) oldModal.remove();

        const modal = document.createElement('div');
        modal.id = 'pwdVerifyModal';
        modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;z-index:9999;';
        modal.innerHTML = `
            <div style="background:var(--bg-primary);padding:28px 24px;border-radius:14px;min-width:320px;max-width:400px;box-shadow:0 8px 32px rgba(0,0,0,0.35);">
                <div style="font-size:16px;font-weight:600;margin-bottom:6px;">🔐 需要验证密码</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:16px;">此操作已启用密码保护，请输入管理员密码继续</div>
                <input type="password" id="pwdVerifyInput" placeholder="输入管理员密码" style="width:100%;padding:9px 12px;box-sizing:border-box;border:1px solid var(--border);border-radius:8px;background:var(--bg-secondary);color:var(--text-primary);font-size:14px;margin-bottom:14px;" onkeydown="if(event.key==='Enter')submitPasswordVerify();">
                <div id="pwdVerifyError" style="font-size:12px;color:#ef4444;margin-bottom:10px;display:none;"></div>
                <div style="display:flex;gap:8px;justify-content:flex-end;">
                    <button class="btn-secondary btn-sm" onclick="closePasswordVerifyModal(false)">取消</button>
                    <button class="btn-primary btn-sm" id="pwdVerifySubmit" onclick="submitPasswordVerify()">确认</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
        setTimeout(() => document.getElementById('pwdVerifyInput')?.focus(), 80);
        window._pwdVerifyResolve = resolve;
    });
}

/**
 * 提交密码验证
 */
async function submitPasswordVerify() {
    const pwd = document.getElementById('pwdVerifyInput')?.value || '';
    const errEl = document.getElementById('pwdVerifyError');
    if (!pwd) {
        if (errEl) { errEl.textContent = '请输入密码'; errEl.style.display = 'block'; }
        return;
    }
    try {
        const r = await fetch('/api/security/verify-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd }),
        });
        const d = await r.json();
        if (d.success) {
            closePasswordVerifyModal(true);
        } else {
            if (errEl) { errEl.textContent = '密码错误：' + (d.error || '请重试'); errEl.style.display = 'block'; }
            const input = document.getElementById('pwdVerifyInput');
            if (input) { input.value = ''; input.focus(); }
        }
    } catch (e) {
        if (errEl) { errEl.textContent = '验证请求失败：' + e.message; errEl.style.display = 'block'; }
    }
}

/**
 * 关闭密码验证模态框
 */
function closePasswordVerifyModal(confirmed) {
    const modal = document.getElementById('pwdVerifyModal');
    if (modal) modal.remove();
    if (window._pwdVerifyResolve) {
        window._pwdVerifyResolve(!!confirmed);
        delete window._pwdVerifyResolve;
    }
}

window.closePasswordVerifyModal = closePasswordVerifyModal;
window.submitPasswordVerify = submitPasswordVerify;
window.requirePasswordIfNeeded = requirePasswordIfNeeded;

// ===== 原有辅助函数 =====
function _setBool(id, val) {
    const el = document.getElementById(id);
    if (el) el.checked = !!val;
}
function _setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val;
}

/**
 * 加载设备指纹信息
 */
async function loadDeviceFingerprint() {
    const el = document.getElementById('deviceFingerprintInfo');
    if (!el) return;
    el.innerHTML = '<span style="color:var(--text-secondary);font-size:12px;">加载中...</span>';
    try {
        const r = await fetch('/api/security/device-fingerprint');
        const d = await r.json();
        if (d.success && d.fingerprint) {
            const f = d.fingerprint;
            el.innerHTML = `
                <div style="font-size:11px;line-height:1.8;color:var(--text-secondary);">
                    <div>CPU：${f.cpu || '—'}</div>
                    <div>主板：${f.motherboard || '—'}</div>
                    <div>MAC：${f.mac || '—'}</div>
                    <div>指纹哈希：<code style="font-size:10px;background:var(--bg-primary);padding:1px 4px;border-radius:3px;">${(f.hash || '').slice(0,16)}…</code></div>
                    <div>绑定状态：${f.bound ? '<span style="color:#34d399">✅ 已绑定</span>' : '<span style="color:#fbbf24">⚠️ 未绑定</span>'}</div>
                </div>`;
        } else {
            el.innerHTML = '<span style="color:#f87171;font-size:12px;">获取失败（hw_security 未启动或不支持）</span>';
        }
    } catch (e) {
        el.innerHTML = '<span style="color:#f87171;font-size:12px;">接口错误：' + e.message + '</span>';
    }
}

/**
 * 测试杀毒软件连接（第二个输入框）
 */
async function testAntivirus2() {
    const path = document.getElementById('setAntivirusPath2')?.value?.trim() || '';
    if (!path) { alert('请先填写杀毒软件路径'); return; }
    toast('正在测试连接...');
    try {
        const r = await fetch('/api/security/test-antivirus', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        const d = await r.json();
        toast(d.success ? ('✅ 连通：' + (d.version || 'OK')) : ('❌ 失败：' + (d.error || '无法调用')));
    } catch(e) { toast('❌ 网络错误'); }
}

// Tab 切换时加载安全设置
document.addEventListener('DOMContentLoaded', () => {
    const stb = document.querySelector('.stb[onclick*="security"]');
    if (stb) {
        stb.addEventListener('click', () => {
            loadSecuritySettings();
        });
    }
});

window.saveSecuritySetting = saveSecuritySetting;
window.loadSecuritySettings = loadSecuritySettings;
window.loadDeviceFingerprint = loadDeviceFingerprint;
window.testAntivirus2 = testAntivirus2;


// ===== Style Pack (风格包) =====

/**
 * 导出风格包为 JSON 文件下载
 */
function exportStylePack() {
    const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
    const includeAvatar = document.getElementById('exportIncludeAvatar')?.checked !== false;
    const includeEmoji  = document.getElementById('exportIncludeEmoji')?.checked  !== false;
    const includeName   = document.getElementById('exportIncludeName')?.checked   !== false;

    const pack = {
        _harven_style_pack: true,
        _version: '1.0',
        _exported_at: new Date().toISOString(),
        _privacy_note: '此风格包由本机 Harven AI 生成，仅包含外观数据，不含任何对话或个人数据。',
    };
    if (includeName  && brand.name)   pack.name   = brand.name;
    if (includeEmoji && brand.emoji)  pack.emoji  = brand.emoji;
    if (includeAvatar && brand.avatar) pack.avatar = brand.avatar;

    const blob = new Blob([JSON.stringify(pack, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `harven-style-${new Date().toISOString().slice(0,10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast('✅ 风格包已导出');
}

/**
 * 导入风格包 JSON 文件
 */
function importStylePack(input) {
    const file = input?.files?.[0];
    if (!file) return;
    const statusEl = document.getElementById('stylePackImportStatus');

    const reader = new FileReader();
    reader.onload = (e) => {
        try {
            const pack = JSON.parse(e.target.result);
            if (!pack._harven_style_pack) {
                if (statusEl) statusEl.textContent = '❌ 不是有效的 Harven 风格包文件';
                return;
            }

            const brand = JSON.parse(localStorage.getItem(BRAND_KEY) || '{}');
            let changed = [];

            if (pack.name)   { brand.name = pack.name;   changed.push('名称'); }
            if (pack.emoji)  { brand.emoji = pack.emoji;  changed.push('图标'); }
            if (pack.avatar) { brand.avatar = pack.avatar; changed.push('头像'); }

            localStorage.setItem(BRAND_KEY, JSON.stringify(brand));
            loadBrandSettings();  // 重新应用

            const msg = changed.length > 0
                ? `✅ 已导入：${changed.join('、')}（来自 ${pack._exported_at ? new Date(pack._exported_at).toLocaleDateString('zh-CN') : '未知日期'}）`
                : '⚠️ 风格包中无有效数据';
            if (statusEl) statusEl.textContent = msg;
            toast('风格包导入成功');
        } catch (err) {
            if (statusEl) statusEl.textContent = '❌ 解析失败：' + err.message;
        }
        // 重置 input 以允许重复导入同名文件
        if (input) input.value = '';
    };
    reader.readAsText(file);
}

/**
 * 重置所有品牌定制数据
 */
async function resetStylePack() {
    if (!confirm('确定要重置所有品牌定制（名称/图标/头像）？此操作不可恢复。')) return;
    const allowed = await requirePasswordIfNeeded('resetStylePack');
    if (!allowed) return;
    localStorage.removeItem(BRAND_KEY);
    // 恢复默认
    _applyBrandName('Harven AI');
    _applyBrandEmoji('🤖');
    const img = document.getElementById('brandAvatarPreviewImg');
    if (img) { img.src = ''; img.style.display = 'none'; }
    const sideIcon = document.getElementById('sidebarBrandIcon');
    if (sideIcon) sideIcon.innerHTML = '🤖';
    // 清空输入框
    const nameInput = document.getElementById('brandCustomName');
    if (nameInput) nameInput.value = '';
    toast('✅ 品牌定制已重置为默认');
}

window.exportStylePack = exportStylePack;
window.importStylePack = importStylePack;
window.resetStylePack  = resetStylePack;

// ===== Settings Search =====
function filterSettingsTabs(query) {
    const q = (query || '').toLowerCase().trim();
    const tabs = document.querySelectorAll('#settingsTabs .stb');
    if (!q) {
        tabs.forEach(t => t.classList.remove('hidden-by-search'));
        return;
    }
    tabs.forEach(t => {
        const text = (t.textContent || '').toLowerCase();
        t.classList.toggle('hidden-by-search', !text.includes(q));
    });
}
window.filterSettingsTabs = filterSettingsTabs;

// ===== Settings Tab Router =====
// switchSettingsTab 增强版：按需加载对应Tab数据，支持合并面板
// ════════════════════════════════════════
// 设置面板 — 8主 Tab × 子面板映射表
// ════════════════════════════════════════

const SETTINGS_TAB_MAP = {
    basic:      ['tab-basic'],
    model:      ['tab-model', 'tab-multimodel', 'modelTabContentInstalled', 'modelTabContentMarket', 'modelTabContentPull', 'tab-local-services', 'tab-local-models'],
    profile:    ['tab-profile'],
    appearance: ['tab-appearance'],
    security:   ['tab-security', 'tab-sandbox', 'tab-agent'],
    vault:      ['tab-vault'],
    plugins:    ['tab-skills', 'tab-skill-builder', 'tab-agents', 'tab-agent-workshop', 'tab-external-models'],
    about:      ['tab-about', 'tab-hardware', 'tab-share', 'tab-channels'],
};

// 所有子面板 id（扁平列表，切换时全部隐藏再按 Map 显示）
const ALL_SUB_PANELS = Object.values(SETTINGS_TAB_MAP).flat();

// 每个主 Tab 默认显示第一个子面板
const TAB_DEFAULT_SUB = {
    basic:      'tab-basic',
    model:      'tab-model',
    profile:    'tab-profile',
    appearance: 'tab-appearance',
    security:   'tab-security',
    vault:      'tab-vault',
    plugins:    'tab-skills',
    about:      'tab-about',
};

// 🔑 初始化设置面板显隐：打开设置时调用，确保默认 basic 可见，其他隐藏
function initSettingsPanelVisibility() {
    // 纯 CSS class 控制：隐藏所有，显示 basic
    ALL_SUB_PANELS.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('active');
    });
    const basicPanel = document.getElementById('tab-basic');
    if (basicPanel) basicPanel.classList.add('active');
    // 高亮 basic 按钮
    document.querySelectorAll('.stb').forEach(b => b.classList.remove('active'));
    const basicBtn = document.querySelector('.stb[onclick*="basic"]');
    if (basicBtn) basicBtn.classList.add('active');
    console.log('[initPanels] ✅ basic active');
}

function switchSettingsTab(tab, btn) {
    console.log('[switchSettingsTab] →', tab, btn ? btn.textContent.trim() : '');
    
    // ① 隐藏所有面板（纯 class，无 inline style）
    ALL_SUB_PANELS.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('active');
    });
    
    // ② 隐藏子导航
    const aboutSubNav = document.getElementById('aboutSubNavWrapper');
    if (aboutSubNav) aboutSubNav.style.display = 'none';
    const pluginsSubNav = document.getElementById('pluginsSubNav');
    if (pluginsSubNav) pluginsSubNav.style.display = 'none';

    // ③ 高亮主 Tab 按钮
    document.querySelectorAll('.stb').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    // ④ 显示默认子面板
    const defaultSub = TAB_DEFAULT_SUB[tab];
    if (defaultSub) {
        const el = document.getElementById(defaultSub);
        if (el) {
            el.classList.add('active');
            console.log('[switchSettingsTab] ✅', defaultSub, 'computed:', window.getComputedStyle(el).display);
        } else {
            console.error('[switchSettingsTab] ❌ 面板不存在:', defaultSub);
        }
    } else {
        console.error('[switchSettingsTab] ❌ TAB_DEFAULT_SUB 无条目:', tab);
    }

    // ⑤ 模型管理：同时显示本地模型面板
    if (tab === 'model') {
        ['tab-local-services', 'tab-local-models'].forEach(subId => {
            const el = document.getElementById(subId);
            if (el) el.classList.add('active');
        });
    }

    // ⑥ 显示 about 子导航
    if (tab === 'about' && aboutSubNav) aboutSubNav.style.display = 'flex';

    // ⑦ 重置子 Tab 按钮高亮
    const subBtnSelectors = {
        model: '.model-sub-btn', profile: '.profile-sub-btn',
        security: '.sec-sub-btn', plugins: '.plugins-sub-btn', about: '.about-sub-btn',
    };
    if (subBtnSelectors[tab]) {
        document.querySelectorAll(subBtnSelectors[tab]).forEach((b, i) => b.classList.toggle('active', i === 0));
    }

    // ⑧ 懒加载
    if (tab === 'model') {
        if (typeof loadInstalledModelsEnhanced === 'function') loadInstalledModelsEnhanced();
        if (typeof checkOllamaStatusDetail === 'function') checkOllamaStatusDetail();
    } else if (tab === 'plugins') {
        if (typeof loadRHChannels === 'function') loadRHChannels();
        if (typeof loadInstalledPlugins === 'function') loadInstalledPlugins();
        if (typeof loadSkills === 'function') loadSkills();
        if (typeof loadRecommendedTools === 'function') loadRecommendedTools();
        ensurePluginsSubNav();
        if (pluginsSubNav) pluginsSubNav.style.display = 'flex';
    } else if (tab === 'about') {
        if (typeof checkVersionManual === 'function') checkVersionManual();
    } else if (tab === 'security') {
        if (typeof loadLoginStatus === 'function') loadLoginStatus();
    }
}

window.switchSettingsTab = switchSettingsTab;
window.initSettingsPanelVisibility = initSettingsPanelVisibility;

// ════════════════════════════════════════
// 主 Tab 栏左右滚动箭头
// ════════════════════════════════════════
function scrollSettingsTabs(delta) {
    const el = document.getElementById('settingsTabs');
    if (!el) return;
    el.scrollBy({ left: delta, behavior: 'smooth' });
    setTimeout(_updateStbArrows, 200);
}

function _updateStbArrows() {
    const el = document.getElementById('settingsTabs');
    const lBtn = document.getElementById('stbArrowLeft');
    const rBtn = document.getElementById('stbArrowRight');
    if (!el || !lBtn || !rBtn) return;
    const atLeft  = el.scrollLeft <= 2;
    const atRight = el.scrollLeft + el.clientWidth >= el.scrollWidth - 2;
    lBtn.classList.toggle('hidden', atLeft);
    rBtn.classList.toggle('hidden', atRight);
}

window.scrollSettingsTabs = scrollSettingsTabs;

// 绑定 Tab 栏滚动事件（DOM 加载后）
document.addEventListener('DOMContentLoaded', function() {
    const el = document.getElementById('settingsTabs');
    if (el) {
        el.addEventListener('scroll', _updateStbArrows, { passive: true });
        // 初始检测
        _updateStbArrows();
        // ResizeObserver 监听宽度变化
        const ro = new ResizeObserver(_updateStbArrows);
        ro.observe(el);
    }
});
window._updateStbArrows = _updateStbArrows;


function _switchSubTab(parentPanels, prefix, subTab, btn, btnSelector, lazyLoaders) {
    parentPanels.forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.classList.remove('active'); el.style.display = 'none'; }
    });
    document.querySelectorAll(btnSelector).forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    const targetId = prefix + subTab;
    const target = document.getElementById(targetId);
    if (target) {
        target.classList.add('active');
        target.style.display = 'block';
        target.style.opacity = '1';
        target.style.animation = 'none';
    }

    // 执行懒加载
    if (lazyLoaders && lazyLoaders[subTab]) {
        lazyLoaders[subTab].forEach(fn => {
            if (typeof fn === 'function') fn();
        });
    }
}

/**
 * ② 模型管理子 Tab：model / multimodel / installed(modelTabContentInstalled) / market(modelTabContentMarket) / pull
 */
function switchModelSubTab(subTab, btn) {
    try {
    // ★ 2026-07-05: 用 modelMainContent 代替 tab-model，保持子导航始终可见
    const panels = ['modelMainContent', 'tab-multimodel', 'modelTabContentInstalled', 'modelTabContentMarket', 'modelTabContentPull', 'tab-hardware', 'tab-local-models', 'modelTabContentDualConfig'];
    // 特殊 id 映射
    const idMap = {
        main:      'modelMainContent',
        multi:     'tab-multimodel',
        hardware:  'tab-hardware',
        installed: 'modelTabContentInstalled',
        market:    'modelTabContentMarket',
        pull:      'modelTabContentPull',
        local:     'tab-local-models',
        dual:      'modelTabContentDualConfig',
    };

    panels.forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.classList.remove('active'); el.style.display = 'none'; }
    });
    document.querySelectorAll('.model-sub-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    const targetId = idMap[subTab];
    if (targetId) {
        const el = document.getElementById(targetId);
        if (el) { el.classList.add('active'); el.style.display = 'block'; }
        else console.error('[switchModelSubTab] panel not found:', targetId);
    }

    if (subTab === 'main') {
        // 如果下拉框还卡在"加载中..."，强制重新加载
        const sel = document.getElementById('setModel');
        if (sel && sel.value === '') {
            try { populateModelSelect('qwen2.5:14b'); } catch(e) {}
        }
    }
    if (subTab === 'installed' && typeof loadInstalledModelsEnhanced === 'function') { try { loadInstalledModelsEnhanced(); } catch(e) {} }
    if (subTab === 'market') {
        if (typeof loadModelMarket === 'function') { try { loadModelMarket(); } catch(e) {} }
        if (typeof loadModelMarketRecommend === 'function') { try { loadModelMarketRecommend(); } catch(e) {} }
    }
    if (subTab === 'multi' && typeof loadMultiModels === 'function') { try { loadMultiModels(); } catch(e) {} }
    if (subTab === 'hardware' && typeof loadHardwareReport === 'function') { try { loadHardwareReport(); } catch(e) {} }
    if (subTab === 'local' && typeof loadLocalModels === 'function') { try { loadLocalModels(); } catch(e) {} }
    if (subTab === 'dual' && typeof loadDualModelConfig === 'function') { try { loadDualModelConfig(); } catch(e) {} }
    } catch(e) { console.error('[switchModelSubTab] error:', e); alert('子导航错误: ' + e.message); }
}
window.switchModelSubTab = switchModelSubTab;


/**
 * ③ 个人画像子 Tab：profile
 */
function switchProfileSubTab(subTab, btn) {
    _switchSubTab(
        ['tab-profile'],
        'tab-',
        subTab,
        btn,
        '.profile-sub-btn',
        {
            profile: [typeof profileLoadAutoState === 'function' ? profileLoadAutoState : null],
        }
    );
}
window.switchProfileSubTab = switchProfileSubTab;


/**
 * ⑤ 安全隐私子 Tab：security / sandbox
 */
function switchSecSubTab(subTab, btn) {
    _switchSubTab(
        ['tab-security', 'tab-sandbox'],
        'tab-',
        subTab,
        btn,
        '.sec-sub-btn',
        {}
    );
}
window.switchSecSubTab = switchSecSubTab;


/**
 * ⑥ 关于 & 连接子 Tab：about / hardware / mobile(share) / channels
 */
function switchAboutSubTab(subTab, btn) {
    const idMap = {
        about:    'tab-about',
        hardware: 'tab-hardware',
        mobile:   'tab-share',
        channels: 'tab-channels',
    };
    const panels = Object.values(idMap);

    panels.forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.classList.remove('active'); el.style.display = 'none'; }
    });
    document.querySelectorAll('.about-sub-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    const targetId = idMap[subTab];
    if (targetId) {
        const el = document.getElementById(targetId);
        if (el) { el.classList.add('active'); el.style.display = 'block'; }
    }

    if (subTab === 'hardware' && typeof loadHardwareReport === 'function') loadHardwareReport();
    if (subTab === 'mobile' && typeof loadServerInfo === 'function') loadServerInfo();
    if (subTab === 'mobile' && typeof detectLocalAddr === 'function') detectLocalAddr();
    if (subTab === 'channels') {
        if (typeof loadChannels === 'function') loadChannels();
        if (typeof updateChannelConfigHint === 'function') updateChannelConfigHint();
    }
}
window.switchAboutSubTab = switchAboutSubTab;


// ═══════════════════════════════════════════
// 用户画像自动分析
// ═══════════════════════════════════════════

async function profileLoadAutoState() {
    const resultEl = document.getElementById('profileAutoResult');
    const statsEl = document.getElementById('profileAutoStats');
    const suggDiv = document.getElementById('profileSuggestions');
    const suggList = document.getElementById('profileSuggestionsList');

    try {
        const resp = await fetch('/api/profile/auto/state');
        const data = await resp.json();

        // 统计信息
        const profile = data.profile || {};
        const stats = profile.stats || {};
        if (stats.total_messages > 0) {
            statsEl.textContent = `已分析 ${stats.total_messages} 条消息 · ${stats.total_conversations || 0} 个对话 · 版本 ${stats.version || 1}`;
        } else {
            statsEl.textContent = '尚未分析';
        }

        // 维度展示
        const dims = profile.dimensions || {};
        const dimKeys = Object.keys(dims);
        if (dimKeys.length === 0) {
            resultEl.innerHTML = '<span style="color:#888;">📭 暂无自动分析数据，点击"立即分析"开始</span>';
        } else {
            const dimLabels = {
                interests: '兴趣', expertise: '专业', communication_style: '交流风格',
                values: '价值观', habits: '习惯', emotional_tone: '情绪',
                skill_level: '技能', tech_stack: '技术栈', goals: '目标', language_pref: '语言',
            };
            const confColors = { high: '#10b981', mid: '#f59e0b', low: '#888' };
            let html = '<div style="display:flex;flex-direction:column;gap:8px;">';
            for (const [dim, info] of Object.entries(dims)) {
                const traits = (info.traits || []).slice(0, 5);
                if (traits.length === 0) continue;
                html += `<div style="display:flex;flex-wrap:wrap;gap:4px;align-items:center;">`;
                html += `<span style="font-size:11px;color:#888;min-width:60px;">${dimLabels[dim] || dim}:</span>`;
                for (const t of traits) {
                    const c = t.confidence >= 0.7 ? confColors.high : (t.confidence >= 0.4 ? confColors.mid : confColors.low);
                    const icon = t.source === 'manual' ? '👤' : '🤖';
                    html += `<span style="font-size:11px;background:var(--bg-hover);padding:2px 8px;border-radius:4px;border-left:3px solid ${c};" title="置信度: ${(t.confidence*100).toFixed(0)}% · 来源: ${t.source}">${icon} ${escapeHtml(t.label)}</span>`;
                }
                html += '</div>';
            }
            if (stats.last_analysis) {
                html += `<div style="font-size:10px;color:#888;margin-top:4px;">📅 上次分析: ${stats.last_analysis.slice(0, 16)}</div>`;
            }
            html += '</div>';
            resultEl.innerHTML = html;
        }

        // 建议列表
        const suggestions = data.suggestions || [];
        if (suggestions.length > 0) {
            suggDiv.style.display = 'block';
            suggList.innerHTML = suggestions.map(s => `
                <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 10px;background:var(--bg-card);border:1px solid var(--border);border-radius:6px;margin-bottom:4px;font-size:12px;">
                    <div style="display:flex;align-items:center;gap:6px;">
                        <span style="color:#888;">${s.dimension_label || s.dimension}:</span>
                        <span style="font-weight:500;">${escapeHtml(s.label)}</span>
                        <span style="font-size:10px;color:${s.confidence >= 0.7 ? '#10b981' : '#f59e0b'};">${(s.confidence * 100).toFixed(0)}%</span>
                    </div>
                    <div style="display:flex;gap:4px;">
                        <button class="btn-primary btn-sm" style="font-size:10px;padding:2px 8px;" onclick="profileApproveSuggestion('${s.dimension}','${escapeHtml(s.label)}')">✅</button>
                        <button class="btn-secondary btn-sm" style="font-size:10px;padding:2px 8px;" onclick="profileDismissSuggestion('${s.dimension}','${escapeHtml(s.label)}')">✕</button>
                    </div>
                </div>
            `).join('');
        } else {
            suggDiv.style.display = 'none';
        }
    } catch (e) {
        resultEl.innerHTML = `<span style="color:#f87171;">加载失败: ${e.message}</span>`;
    }
}

async function profileTriggerAnalyze() {
    const resultEl = document.getElementById('profileAutoResult');
    resultEl.innerHTML = '<span style="color:#f59e0b;">⏳ 分析中...</span>';
    try {
        const resp = await fetch('/api/profile/auto/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ limit: 50, since_hours: 72 })
        });
        const data = await resp.json();
        if (data.success) {
            await profileLoadAutoState();
        } else {
            resultEl.innerHTML = `<span style="color:#f87171;">❌ ${data.error || '分析失败'}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<span style="color:#f87171;">❌ 请求失败: ${e.message}</span>`;
    }
}

async function profileForceAnalyze() {
    if (!confirm('深度分析将扫描最近30天对话，可能需要更长时间。继续？')) return;
    const resultEl = document.getElementById('profileAutoResult');
    resultEl.innerHTML = '<span style="color:#f59e0b;">⏳ 深度分析中...</span>';
    try {
        const resp = await fetch('/api/profile/auto/force', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            await profileLoadAutoState();
        } else {
            resultEl.innerHTML = `<span style="color:#f87171;">❌ ${data.error || '分析失败'}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<span style="color:#f87171;">❌ 请求失败: ${e.message}</span>`;
    }
}

async function profileApproveSuggestion(dim, label) {
    try {
        const resp = await fetch('/api/profile/auto/suggestions/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dimension: dim, label: label })
        });
        const data = await resp.json();
        if (data.success) {
            await profileLoadAutoState();
        }
    } catch (e) { /* ignore */ }
}

async function profileDismissSuggestion(dim, label) {
    try {
        const resp = await fetch('/api/profile/auto/suggestions/dismiss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dimension: dim, label: label })
        });
        await profileLoadAutoState();
    } catch (e) { /* ignore */ }
}

async function profileClearSuggestions() {
    try {
        await fetch('/api/profile/auto/suggestions/clear', { method: 'POST' });
        await profileLoadAutoState();
    } catch (e) { /* ignore */ }
}

async function profileResetAuto() {
    if (!confirm('⚠️ 确认重置所有AI自动分析的画像数据？此操作不可撤销！')) return;
    try {
        await fetch('/api/profile/auto/reset', { method: 'POST' });
        await profileLoadAutoState();
    } catch (e) { /* ignore */ }
}

// 在切换到个人画像 Tab 时加载
window.profileLoadAutoState = profileLoadAutoState;
window.profileTriggerAnalyze = profileTriggerAnalyze;
window.profileForceAnalyze = profileForceAnalyze;
window.profileApproveSuggestion = profileApproveSuggestion;
window.profileDismissSuggestion = profileDismissSuggestion;
window.profileClearSuggestions = profileClearSuggestions;
window.profileResetAuto = profileResetAuto;


/**
 * ⑦ 插件管理子 Tab：discover(资源搜索) / skills(技能管理) / builder(制作向导)
 */
function switchPluginSubTab(subTab, btn) {
    const panelIds = [
        'tab-plugins', 'tab-skills', 'tab-skill-builder',
        'tab-agents', 'tab-agent-workshop', 'tab-external-models'
    ];
    
    panelIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.classList.remove('active'); el.style.display = 'none'; }
    });
    document.querySelectorAll('.plugins-sub-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    const el = document.getElementById(subTab);
    if (el) { el.classList.add('active'); el.style.display = 'block'; }

    // 懒加载
    if (subTab === 'tab-plugins' && typeof loadRHChannels === 'function') loadRHChannels();
    if (subTab === 'tab-skills' && typeof loadSkills === 'function') { loadSkills(); loadRecommendedTools(); }
    if (subTab === 'tab-skill-builder' && typeof loadSkillCategories === 'function') loadSkillCategories();
    if (subTab === 'tab-agents' && typeof loadAgentList === 'function') loadAgentList();
}
window.switchPluginSubTab = switchPluginSubTab;


// ★ 自动生成插件子导航
function ensurePluginsSubNav() {
    var c = document.getElementById("pluginsSubNav");
    if (!c) {
        var p = document.getElementById("tab-skills");
        if (!p || !p.parentElement) return;
        c = document.createElement("div"); c.id = "pluginsSubNav";
        c.style.cssText = "display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;";
        p.parentElement.insertBefore(c, p);
    }
    if (c.children.length > 0) return;
    [["tab-skills","🛠️ 技能"],
     ["tab-skill-builder","🧱 构建"],
     ["tab-agents","🧩 Agent"],
     ["tab-agent-workshop","🏭 工坊"],
     ["tab-external-models","📦 外部模型"]].forEach(function(t){
        var b = document.createElement("button");
        b.className = "btn-sm btn-secondary plugins-sub-btn";
        b.textContent = t[1]; b.style.cssText = "font-size:11px;padding:4px 10px;border-radius:6px;";
        b.onclick = function(){ switchPluginSubTab(t[0], b); };
        c.appendChild(b);
    });
    switchPluginSubTab("tab-skills", c.children[0]);
}


// ===== 专项培养 (Specializations) =====
async function loadSpecializations() {
    try {
        const r = await fetch('/api/specializations');
        const data = await r.json();
        const specs = data.specializations || [];
        const el = document.getElementById('specList');
        if (!specs.length) {
            el.innerHTML = '<p style="color:var(--text-secondary);font-size:13px;">暂无专项，请添加</p>';
            return;
        }
        el.innerHTML = specs.map(s => `
            <div class="kb-item" style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <strong>${escHtml(s.name)}</strong>
                    <p style="font-size:12px;color:var(--text-secondary);margin:4px 0 0;">${(s.prompt || '').substring(0, 60)}...</p>
                </div>
                <button class="btn-batch btn-batch-danger" onclick="deleteSpec(${s.id}, '${escHtml(s.name)}')">删除</button>
            </div>
        `).join('');
    } catch (e) {
        console.error('[loadSpecializations]', e);
    }
}

async function saveSpec() {
    const name = document.getElementById('specName').value.trim();
    const prompt = document.getElementById('specPrompt').value.trim();
    if (!name || !prompt) {
        toast('请填写名称和提示词');
        return;
    }
    try {
        const r = await fetch('/api/specializations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, prompt: prompt })
        });
        const data = await r.json();
        if (data.success) {
            document.getElementById('specName').value = '';
            document.getElementById('specPrompt').value = '';
            toast('专项保存成功');
            loadSpecializations();
        } else {
            toast('保存失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('保存失败: ' + e.message);
    }
}

async function deleteSpec(id, name) {
    if (!confirm(`确定删除专项「${name}」？`)) return;
    try {
        const r = await fetch(`/api/specializations/${id}`, { method: 'DELETE' });
        const data = await r.json();
        if (data.success) {
            toast('已删除');
            loadSpecializations();
        } else {
            toast('删除失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('删除失败: ' + e.message);
    }
}









// ===== 通用子 Tab 切换（新架构）=====
// 参数：navContainerId（子导航容器ID）, targetPanelId（目标子面板ID）, btn（被点击按钮）
function switchSettingsSubTab(navContainerId, targetPanelId, btn) {
    // 1. 更新子导航按钮高亮
    const nav = document.getElementById(navContainerId);
    if (nav) {
        nav.querySelectorAll('.sub-nav-btn').forEach(b => b.classList.remove('active'));
    }
    if (btn) btn.classList.add('active');

    // 2. 找到同层级 .settings-sub-body，隐藏所有子面板
    let body = null;
    if (nav && nav.nextElementSibling && nav.nextElementSibling.classList.contains('settings-sub-body')) {
        body = nav.nextElementSibling;
    } else if (nav) {
        const panel = nav.closest('.settings-panel');
        if (panel) body = panel.querySelector('.settings-sub-body');
    }

    if (body) {
        body.querySelectorAll(':scope > div').forEach(d => d.style.display = 'none');
    }

    // 3. 显示目标子面板
    const target = document.getElementById(targetPanelId);
    if (target) { target.classList.add('active'); target.style.display = 'block'; }
}

window.switchSettingsSubTab = switchSettingsSubTab;

// ===== 协作模式设置 =====

let _collab_foreign_models = [];

async function loadCollabSettings() {
    try {
        const resp = await fetch('/api/collab/status');
        const data = await resp.json();
        if (data.success && data.data) {
            const d = data.data;
            const enabledEl = document.getElementById('setCollabEnabled');
            const modelGroup = document.getElementById('collabModelGroup');
            const privacyGroup = document.getElementById('collabPrivacyGroup');
            if (enabledEl) enabledEl.checked = d.enabled || false;
            if (modelGroup) modelGroup.style.display = d.enabled ? '' : 'none';
            if (privacyGroup) privacyGroup.style.display = d.enabled ? '' : 'none';
            // 设置外脑选择
            const modelSel = document.getElementById('setCollabForeignModel');
            if (modelSel) {
                await loadCollabForeignModels();
                if (d.foreign_model) modelSel.value = d.foreign_model;
            }
            // 设置隐私级别
            if (d.privacy_level) {
                document.querySelectorAll('input[name="collabPrivacy"]').forEach(r => {
                    r.checked = r.value === d.privacy_level;
                });
                updatePrivacyLabelUI(d.privacy_level);
            }
        }
    } catch (e) {
        console.debug('[Collab] 加载协作设置失败:', e);
    }
}

async function loadCollabForeignModels() {
    try {
        const resp = await fetch('/api/collab/foreign-models');
        const data = await resp.json();
        if (data.success && data.data) {
            _collab_foreign_models = data.data;
            const sel = document.getElementById('setCollabForeignModel');
            if (!sel) return;
            sel.innerHTML = '<option value="">-- 请选择外脑 --</option>';
            data.data.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                opt.textContent = `${m.name}${m.has_key ? '' : ' (需要 API Key)'}`;
                if (!m.has_key) opt.disabled = true;
                sel.appendChild(opt);
            });
        }
    } catch (e) {
        console.debug('[Collab] 加载外脑列表失败:', e);
    }
}

async function saveCollabSettings() {
    const enabled = document.getElementById('setCollabEnabled');
    const modelSel = document.getElementById('setCollabForeignModel');
    const modelGroup = document.getElementById('collabModelGroup');
    const privacyGroup = document.getElementById('collabPrivacyGroup');
    const isEnabled = enabled ? enabled.checked : false;

    // 切换 UI 显示
    if (modelGroup) modelGroup.style.display = isEnabled ? '' : 'none';
    if (privacyGroup) privacyGroup.style.display = isEnabled ? '' : 'none';

    // 获取隐私级别
    const privacyRadio = document.querySelector('input[name="collabPrivacy"]:checked');
    const privacy_level = privacyRadio ? privacyRadio.value : 'balanced';

    try {
        const resp = await fetch('/api/collab/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                enabled: isEnabled,
                foreign_model: modelSel ? modelSel.value : '',
                privacy_level: privacy_level,
            }),
        });
        const data = await resp.json();
        if (data.success) {
            console.log('[Collab] 设置已保存:', data.data);
            // 如果有 toast 函数，显示提示
            if (typeof toast === 'function') {
                toast(isEnabled ? '🤝 协作模式已启用' : '协作模式已关闭', 'success');
            }
        }
    } catch (e) {
        console.error('[Collab] 保存协作设置失败:', e);
    }
}

function setCollabPrivacy(level) {
    // 更新 radio
    document.querySelectorAll('input[name="collabPrivacy"]').forEach(r => {
        r.checked = r.value === level;
    });
    updatePrivacyLabelUI(level);
    saveCollabSettings();
}

function updatePrivacyLabelUI(level) {
    const descMap = {
        'strict': '🔒 严格：不传用户数据，仅传去敏的问题摘要',
        'balanced': '⚖️ 平衡：传去敏数据（移除姓名/地点/数字等敏感信息）',
        'full': '📤 全量：传全部数据（仅限完全信任的外脑）',
    };
    const descEl = document.getElementById('privacyDesc');
    if (descEl) descEl.textContent = descMap[level] || '';

    // 高亮选中
    ['strict', 'balanced', 'full'].forEach(l => {
        const label = document.getElementById('privacyLabel' + l.charAt(0).toUpperCase() + l.slice(1));
        if (label) {
            if (l === level) {
                label.style.borderColor = 'var(--primary)';
                label.style.background = 'var(--primary-light, rgba(var(--primary-rgb, 59,130,246), 0.1))';
                label.style.borderWidth = '2px';
            } else {
                label.style.borderColor = 'var(--border)';
                label.style.background = '';
                label.style.borderWidth = '1px';
            }
        }
    });
}

// ===== Window 全局导出（由自检脚本补全）=====
window.loadSpecializations = loadSpecializations;
window.saveSpec = saveSpec;
window.deleteSpec = deleteSpec;
window.loadTasks = loadTasks;
window.filterTasks = filterTasks;
window.addTask = addTask;
window.deleteTask = deleteTask;
window.previewModel = previewModel;
window.loadPwdProtectActions = loadPwdProtectActions;
window.clearAllData = clearAllData;
window.resetSettings = resetSettings;
window.saveAllSettings = saveAllSettings;
window.loadSettings = loadSettings;
window.addInterestTag = addInterestTag;
window.removeInterestTag = removeInterestTag;
window.exportData = exportData;
// ===== 协作模式设置 =====
window.saveCollabSettings = saveCollabSettings;
window.setCollabPrivacy = setCollabPrivacy;
window.loadCollabSettings = loadCollabSettings;
console.log('[app-settings] ✅ 全部加载完成（2131行）');
