/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — CONVERSATION module ===== */
// ===== Query Mode =====
// 两种查询模式（2026-07-06 更新，auto已取消）：
//   kb_only  — 🏠本地：本地通用模型 + 知识库 + Skills + 画像 + 习惯，纯本地不外出
//   direct   — 🤖外脑：外部模型 + 知识库 + Skills + 画像 + 习惯，外脑回答
//   协作模式是独立开关（off/cloud_local/local_dual），与查询模式配合
const QUERY_MODES = {
    kb_only: { label: '🏠本地', desc: '本地通用模型+知识库+Skills+画像+习惯，纯本地不外出', group: 'analysis' },
    direct: { label: '🤖外脑', desc: '外部模型+知识库+Skills+画像+习惯，外脑回答', group: 'direct' },
};

// ★ 2026-08-03: 查看旧对话标记 — 查看旧对话后发消息自动开新对话
var _viewingOldConv = false;

// ★ 2026-07-06: 每次刷新强制设为外脑模式（不管localStorage存了什么）
// 必须在 currentQueryMode 赋值之前执行，否则变量已经是旧值
(function(){
    try {
        localStorage.setItem('harven_query_mode', 'direct');
    } catch(e) {}
})();

// ★ 强制外脑模式：不读旧localStorage，每次刷新都是direct（auto已取消）
let currentQueryMode = 'direct';

// ★ 多模式对话隔离：每个模式独立保存最后一次对话ID
function _saveCurrentConvByMode() {
    if (currentConvId && currentQueryMode) {
        try { localStorage.setItem('conv_' + currentQueryMode, currentConvId); } catch(e) {}
    }
}
async function _switchConvByMode(mode) {
    if (!mode) return;
    // 保存当前对话到旧模式
    if (currentConvId && currentQueryMode && currentQueryMode !== mode) {
        _saveCurrentConvByMode();
    }
    try {
        var saved = localStorage.getItem('conv_' + mode);
        if (saved) {
            await loadConv(saved);
        } else {
            newChat();
        }
    } catch(e) {
        newChat();
    }
}

async function setQueryMode(mode) {
    if (!QUERY_MODES[mode]) return;

    // ★ 2026-07-05: 切换到任何模式时都自动关闭垂直面板（不只是外脑模式）
    if (typeof _verticalPanelOpen !== 'undefined' && _verticalPanelOpen) {
        if (typeof closeVerticalPanel === 'function') {
            closeVerticalPanel();
        }
    }

    // ★ 2026-07-13: 切换查询模式时 → 总是关闭协作模式（避免按钮不同步）
    if (collabMode && collabMode !== 'off') {
        if (typeof setCollabMode === 'function') setCollabMode('off');
    }

    currentQueryMode = mode;
    try { localStorage.setItem('harven_query_mode', mode); } catch(e) {}
    console.log('[setQueryMode] 切换至:', mode, 'currentQueryMode=', currentQueryMode);
    
    // ★ 切换模式时，切换到该模式的上一次对话
    await _switchConvByMode(mode);
    
    // 更新按钮状态
    document.querySelectorAll('.qm-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    // 更新调试文字
    const debug = document.getElementById('queryModeDebug');
    if (debug) {
        debug.textContent = '当前: ' + QUERY_MODES[mode].label;
        debug.style.color = mode === 'direct' ? '#c4a1ff' : 'var(--text-secondary)';
    }
    // 更新输入框 placeholder
    const input = document.getElementById('msgInput');
    const m = QUERY_MODES[mode];
    if (m.group === 'direct') {
        input.placeholder = '🤖 外脑模式 · 外部模型+Skills直接回答';
    } else {
        input.placeholder = `输入消息... (${m.desc})`;
    }
    // ★ 更新状态栏以反映当前模式
    updateModelStatus(mode);
    toast('已切换至：' + m.label + ' · ' + m.desc);
}

// ★ 根据当前模式更新状态栏（解决截图中的显示不一致问题）
function updateModelStatus(mode) {
    const el = document.getElementById('modelStatus');
    const detailEl = document.getElementById('modelStatusDetail');
    if (!el) return;

    if (mode === 'direct') {
        // 外脑模式 → 显示外脑信息
        const foreignModel = localStorage.getItem('harven_foreign_model') || '外部模型';
        el.textContent = '🔵 外脑 · ' + foreignModel;
        el.style.color = '#8b5cf6';
        if (detailEl) { detailEl.textContent = '远程推理 · 数据加密传输'; detailEl.style.color = '#8b5cf6'; }
    } else {
        // 本地模式 → 稍后由 checkOllamaStatus 异步更新
        el.textContent = '🔄 检测本地运行时...';
        el.style.color = '#94a3b8';
        checkOllamaStatus();
    }
}

// ═══ 联网搜索 ═══
// ★ 2026-07-06: 互联网开关（SearXNG优先+Bing回退，按钮控制）
let webSearchEnabled = false;

function toggleWebSearch() {
    const btn = document.getElementById('webSearchBtn');
    if (!btn) return;
    
    webSearchEnabled = !webSearchEnabled;
    if (webSearchEnabled) {
        btn.innerHTML = '🌐 互联网开';
        btn.className = 'btn-web-search-on';
        btn.title = '互联网搜索已开启 · 本次查询会慢10-20秒 · 点此关闭';
        toast('🌐 互联网搜索已开启（本次查询约慢10-20秒）');
    } else {
        btn.innerHTML = '🌐 互联网关';
        btn.className = 'btn-web-search-off';
        btn.title = '互联网搜索关闭 · 点击开启（会变慢10-20秒）';
    }
}
window.toggleWebSearch = toggleWebSearch;

// ★ 2026-07-06: 初始化互联网按钮（SearXNG优先+Bing回退）
function initWebSearchButton() {
    const btn = document.getElementById('webSearchBtn');
    if (!btn) return;
    webSearchEnabled = false;
    btn.innerHTML = '🌐 互联网关';
    btn.className = 'btn-web-search-off';
    btn.title = '互联网搜索关闭 · 点击开启（会变慢10-20秒）';
    btn.disabled = false;
}
setTimeout(initWebSearchButton, 500);

// ===== 协作模式 =====
let collabMode = 'off';  // 'off' | 'cloud_local' | 'local_dual'

// 下拉菜单开关
function toggleCollabDropdown() {
    const menu = document.getElementById('collabMenu');
    const dropdown = document.getElementById('collabDropdown');
    if (!menu || !dropdown) return;
    const isOpen = menu.style.display !== 'none';
    menu.style.display = isOpen ? 'none' : 'block';
    dropdown.classList.toggle('open', !isOpen);
}

// 设置协作模式
async function setCollabMode(mode) {
    const btn = document.getElementById('collabModeBtn');
    const btnIcon = document.getElementById('collabBtnIcon');
    const btnText = document.getElementById('collabBtnText');
    const menu = document.getElementById('collabMenu');
    const dropdown = document.getElementById('collabDropdown');

    if (!btn) { console.warn('[Collab] collabModeBtn 不存在'); return; }

    const modeConfig = {
        'off':          { icon: '🚫', text: '关闭', desc: '未开启协作' },
        'cloud_local':  { icon: '☁️', text: '云地协作', desc: '云端大模型 → 本地垂直模型' },
        'local_dual':   { icon: '🏠', text: '本地双模', desc: '本地通用模型 → 垂直模型' },
    };
    const cfg = modeConfig[mode] || modeConfig['off'];

    console.log('[Collab] setCollabMode:', mode, cfg);

    // ★ 先本地更新 UI（即时响应，不等待 API）
    collabMode = mode;
    if (btnIcon) btnIcon.textContent = cfg.icon;
    if (btnText) btnText.textContent = cfg.text;
    if (btn) {
        btn.title = cfg.desc;
        btn.className = 'qm-btn collab-dropdown-btn' + (mode !== 'off' ? ' qm-btn-collab-active' : '');
    }
    if (dropdown) dropdown.classList.remove('open');
    if (menu) menu.style.display = 'none';
    // 更新下拉菜单选中态
    document.querySelectorAll('.collab-menu-item[data-mode]').forEach(item => {
        const isActive = item.dataset.mode === mode;
        item.classList.toggle('active', isActive);
        const check = item.querySelector('.collab-item-check');
        if (check) check.textContent = isActive ? '●' : '○';
    });
    toast('协作模式: ' + cfg.text);

    // ★ 问题修复：协作模式开启时自动打开垂直面板，关闭时自动收起
    if (mode !== 'off') {
        // 协作模式需要垂直画板界面
        if (typeof openVerticalPanel === 'function' && !_verticalPanelOpen) {
            openVerticalPanel();
        }
    } else {
        // 关闭协作时，如果之前是协作打开的垂直面板，一起关掉
        if (typeof closeVerticalPanel === 'function') {
            closeVerticalPanel();
        }
    }

    // ★ 刷新底部状态栏以显示协作模式
    setTimeout(checkOllamaStatus, 100);

    // 检查内存（开启协作时）— 异步不阻塞
    if (mode !== 'off') { setTimeout(function() { _checkCollabMemory(); }, 50); }

    // ★ 后端同步改为 fire-and-forget（不再 await，解决切换卡顿）
    fetch('/api/collab/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode }),
    }).then(function(resp) {
        return resp.json();
    }).then(function(data) {
        if (!data.success) {
            console.warn('[Collab] 后端同步失败:', data.error);
            toast('⚠ 协作同步后端失败', 'error');
        }
    }).catch(function(e) {
        console.warn('[Collab] 后端不可达（离线模式）:', e.message);
        localStorage.setItem('harven_collab_mode', mode);
    });
}

// 显示协作帮助
function showCollabHelp() {
    const menu = document.getElementById('collabMenu');
    const dropdown = document.getElementById('collabDropdown');
    if (menu) menu.style.display = 'none';
    if (dropdown) dropdown.classList.remove('open');
    alert(
        '🤝 协作模式说明\n\n' +
        '协作模式 = 多模型协作的安全开关\n\n' +
        '🚫 关闭：纯对话，不启用垂直模型\n' +
        '☁️ 智能协作：Skill脚本编排 + 外脑分析 + 垂直模型执行\n' +
        '🏠 本地协作：Skill脚本编排 + 本地模型 + 垂直模型（无需网络）\n\n' +
        '🔑 工作流程：\n' +
        '1. 引擎检测触发词 → 匹配Skill脚本\n' +
        '2. Skill脚本编排多个模型：\n' +
        '   通用模型（带画像+习惯）思考分析\n' +
        '   → 垂直模型执行生成\n' +
        '   → 通用模型总结\n' +
        '3. 支持链式调用多个模型完成长效任务\n\n' +
        '✅ 能力：\n' +
        '• 多模型协作（通用→音乐→语音→视频）\n' +
        '• 长效任务（财务报告/代码审计/家庭管理）\n' +
        '• 跨模态（文字→音乐→图片→视频）\n' +
        '• 通用模型环节带画像+习惯\n' +
        '• 垂直模型环节只接收参数\n\n' +
        '📌 举例：\n' +
        '"写一首歌" → 通用模型写词 → MusicGen作曲 → Bark配声 → FFmpeg合成'
    );
}

// 协作模式内存检查
async function _checkCollabMemory() {
    try {
        const resp = await fetch('/api/collab/memory-check');
        const data = await resp.json();
        if (data.success && data.data && data.data.memory) {
            const mem = data.data.memory;
            if (!mem.ok && mem.warnings.length > 0) {
                setTimeout(() => {
                    toast('⚠️ 内存警告：' + mem.warnings[0], 6000);
                }, 1500);
            }
        }
    } catch (e) {
        // 静默，内存检查失败不影响功能
    }
}

// 点击外部关闭下拉 + ★ 菜单项点击委托
document.addEventListener('click', function(e) {
    const dropdown = document.getElementById('collabDropdown');
    const menu = document.getElementById('collabMenu');
    if (!dropdown || !menu) return;

    // ★ 处理菜单项点击（事件委托）
    const menuItem = e.target.closest('.collab-menu-item[data-mode]');
    if (menuItem) {
        e.preventDefault();
        e.stopPropagation();
        const mode = menuItem.dataset.mode;
        if (mode) setCollabMode(mode);
        return;
    }

    // 点击外部关闭
    if (!dropdown.contains(e.target) && menu.style.display !== 'none') {
        menu.style.display = 'none';
        dropdown.classList.remove('open');
    }
});

// ===== 垂直模型确认卡片 — ★ 2026-07-03 已删除 =====
// 旧架构用确认卡片（vertical_confirm 事件），新架构改用垂直面板预填（vertical_prefill 事件）
// renderVerticalConfirmCard / confirmVerticalCard / switchToGeneralModel 已删除
// 保留 dismissVerticalCard（搜索引导卡仍在用）+ escapeHtml（通用工具函数）

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ===== 垂直模型引导卡片（2026-07-03 新增）=====
// vertical_action 事件渲染：含【直接生成】+【我先调整】按钮
function renderVerticalActionCard(data) {
    if (!data || !data.model_id) return;
    const container = document.getElementById('chatMessages') || document.getElementById('messages');
    if (!container) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'vertical-action-card';
    wrapper.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--accent);border-radius:12px;padding:16px 20px;margin:12px 0;animation:fadeIn 0.3s;';

    const icon = data.model_icon || '🎯';
    const modelName = data.model_name || data.model_id;
    const summary = data.params_summary || '';
    const message = data.message || '参数已填好，要我直接生成吗？';

    wrapper.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
            <span style="font-size:24px;">${icon}</span>
            <div>
                <div style="font-weight:600;font-size:15px;color:var(--text-primary);">${escapeHtml(modelName)}</div>
                <div style="font-size:12px;color:var(--text-secondary);">${escapeHtml(message)}</div>
            </div>
        </div>
        ${summary ? `<div style="font-size:13px;color:var(--text-secondary);background:var(--bg-tertiary);padding:8px 12px;border-radius:8px;margin-bottom:12px;line-height:1.6;">${escapeHtml(summary)}</div>` : ''}
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <button class="btn-primary" style="padding:8px 18px;border:none;border-radius:8px;background:var(--accent);color:#fff;font-size:13px;cursor:pointer;font-weight:600;" onclick="execVerticalFromCard(this, '${escapeHtml(data.model_id)}')">▶ 直接生成</button>
            <button class="btn-secondary" style="padding:8px 18px;border:1px solid var(--border);border-radius:8px;background:var(--bg-primary);color:var(--text-primary);font-size:13px;cursor:pointer;" onclick="dismissVerticalActionCard(this)">✏️ 我先调整</button>
        </div>
    `;
    container.appendChild(wrapper);
    wrapper.scrollIntoView({ behavior: 'smooth', block: 'end' });
}
window.renderVerticalActionCard = renderVerticalActionCard;

// ★ 点击【直接生成】：调用垂直面板的执行函数
async function execVerticalFromCard(btn, modelId) {
    btn.disabled = true;
    btn.textContent = '⏳ 生成中...';
    try {
        // 确保垂直面板已切换到目标模型
        const select = document.getElementById('verticalModelSelect');
        if (select && select.value !== modelId) {
            select.value = modelId;
            if (typeof onVerticalModelChange === 'function') {
                onVerticalModelChange(modelId);
            }
        }
        // 调用垂直面板的执行函数
        if (typeof executeVerticalModel === 'function') {
            await executeVerticalModel();
        }
        // 关闭卡片
        const card = btn.closest('.vertical-action-card');
        if (card) card.remove();
    } catch(e) {
        console.error('[VerticalAction] 执行失败:', e);
        btn.textContent = '❌ 失败';
        btn.disabled = false;
        setTimeout(() => { btn.textContent = '▶ 直接生成'; }, 2000);
    }
}
window.execVerticalFromCard = execVerticalFromCard;

// ★ 点击【我先调整】：关闭卡片，闪烁执行按钮
function dismissVerticalActionCard(btn) {
    const card = btn.closest('.vertical-action-card');
    if (card) {
        card.style.opacity = '0';
        card.style.transform = 'scale(0.95)';
        card.style.transition = 'all 0.2s';
        setTimeout(() => card.remove(), 200);
    }
    // ★ 2026-07-04 改进: 滚动到垂直面板 + 高亮边框 2 秒
    const panel = document.getElementById('verticalPanel') || document.querySelector('.vertical-panel');
    if (panel) {
        // 滚动到垂直面板
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        // 高亮边框
        const oldBorder = panel.style.border;
        const oldTransition = panel.style.transition;
        panel.style.transition = 'border 0.3s ease';
        panel.style.border = '2px solid var(--accent)';
        setTimeout(function() {
            panel.style.border = oldBorder;
            setTimeout(function() { panel.style.transition = oldTransition; }, 300);
        }, 2000);
    }
    // 闪烁执行按钮
    const execBtn = document.getElementById('verticalExecBtn');
    if (execBtn) {
        execBtn.style.animation = 'pulse 1s ease-in-out 3';
        setTimeout(function() { execBtn.style.animation = ''; }, 3000);
    }
}
window.dismissVerticalActionCard = dismissVerticalActionCard;

// ===== AI 主动反问卡片（2026-07-04 新增）=====
// vertical_inquiry 事件渲染：关键参数缺失时 AI 主动反问
function renderVerticalInquiryCard(data) {
    if (!data || !data.model_id) return;
    const container = document.getElementById('chatMessages') || document.getElementById('messages');
    if (!container) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'vertical-inquiry-card';
    wrapper.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--accent);border-radius:12px;padding:16px 20px;margin:12px 0;animation:fadeIn 0.3s;';

    const icon = data.model_icon || '🎯';
    const modelName = data.model_name || data.model_id;
    const message = data.message || '我需要了解更多信息：';
    const questions = data.questions || [];

    // ★ 2026-07-04: 支持 \n 换行，不加序号前缀（LLM 的 ADVISE 文本自带序号）
    let questionsHtml = '';
    questions.forEach(function(q) {
        // 把 \n 转成 <br>，让换行正常显示
        const formattedQ = escapeHtml(q).replace(/\\n/g, '<br>').replace(/\n/g, '<br>');
        questionsHtml += '<div style="font-size:13px;color:var(--text-primary);padding:8px 0;line-height:1.8;white-space:pre-wrap;">' + formattedQ + '</div>';
    });

    wrapper.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
            <span style="font-size:24px;">${icon}</span>
            <div>
                <div style="font-weight:600;font-size:15px;color:var(--text-primary);">${escapeHtml(modelName)}</div>
                <div style="font-size:12px;color:var(--text-secondary);">${escapeHtml(message)}</div>
            </div>
        </div>
        <div style="margin-bottom:12px;">${questionsHtml}</div>
        <div style="font-size:12px;color:var(--text-tertiary);padding:8px 12px;background:var(--bg-tertiary);border-radius:8px;">
            💡 请在下方对话框输入您的回答，我会重新提取参数
        </div>
    `;
    container.appendChild(wrapper);
    wrapper.scrollIntoView({ behavior: 'smooth', block: 'end' });

    // 聚焦输入框
    const input = document.getElementById('msgInput');
    if (input) {
        setTimeout(function() { input.focus(); }, 300);
    }
}
window.renderVerticalInquiryCard = renderVerticalInquiryCard;

function dismissVerticalCard(btn) {
    const card = btn.closest('.vertical-confirm-card');
    if (card) {
        card.style.opacity = '0';
        card.style.transform = 'scale(0.95)';
        card.style.transition = 'all 0.2s';
        setTimeout(() => card.remove(), 200);
    }
}

// ★ 搜索技能引导卡（2026-07-06 SearXNG已恢复双路）
// ★ 2026-07-06: renderSearchGuideCard + installSearchSkillFromChat 已移除（SearXNG双路+Bing内置）

// ★ 2026-07-06: 搜索结果卡片渲染（SearXNG双路+Bing内置）
function renderSearchResultsCard(content) {
    const chatMessages = document.getElementById('messages') || document.getElementById('chatMessages');
    if (!chatMessages) return;

    const data = content || {};
    const query = data.query || '';
    const sources = data.sources || [];
    const engine = data.engine || 'Bing';

    const card = document.createElement('div');
    card.className = 'search-results-card';
    card.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;padding:16px;margin:8px 0;animation:fadeIn 0.3s;';

    let html = '<div style="display:flex;align-items:center;gap:6px;margin-bottom:10px;">';
    html += '<span style="font-size:14px;">🌐</span>';
    html += '<span style="font-size:13px;font-weight:600;">联网搜索结果</span>';
    html += '<span style="font-size:11px;color:var(--text-secondary);margin-left:auto;">引擎: ' + engine + '</span>';
    html += '</div>';

    html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:10px;">搜索词: ' + escapeHtml(query) + '</div>';

    if (sources.length === 0) {
        html += '<div style="font-size:12px;color:var(--text-secondary);padding:8px 0;">未找到相关结果</div>';
    } else {
        html += '<div style="display:flex;flex-direction:column;gap:8px;">';
        sources.forEach(function(s, i) {
            var title = s.title || s.url || '';
            var url = s.url || '';
            var domain = '';
            try {
                domain = new URL(url).hostname;
            } catch(e) { domain = url.substring(0, 40); }

            html += '<div style="padding:8px;background:var(--bg-primary);border-radius:6px;border:1px solid var(--border);">';
            html += '<div style="font-size:12px;font-weight:500;margin-bottom:3px;">' + (i+1) + '. ' + escapeHtml(title) + '</div>';
            html += '<div style="font-size:11px;color:var(--text-secondary);">';
            html += '<a href="' + escapeHtml(url) + '" target="_blank" style="color:var(--accent);text-decoration:none;">' + escapeHtml(domain) + '</a>';
            html += '</div>';
            html += '</div>';
        });
        html += '</div>';
    }

    card.innerHTML = html;
    chatMessages.appendChild(card);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
window.renderSearchResultsCard = renderSearchResultsCard;

// ★ 2026-07-05: 事实查询直接展示搜索结果（不经过模型整合）
function renderFactSearchResults(content) {
    const chatMessages = document.getElementById('messages') || document.getElementById('chatMessages');
    if (!chatMessages) return;

    const data = content || {};
    const query = data.query || '';
    const results = data.results || [];

    const card = document.createElement('div');
    card.className = 'fact-search-card';
    card.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;padding:16px;margin:8px 0;animation:fadeIn 0.3s;';

    let html = '<div style="display:flex;align-items:center;gap:6px;margin-bottom:10px;">';
    html += '<span style="font-size:14px;">🔍</span>';
    html += '<span style="font-size:13px;font-weight:600;">搜索结果</span>';
    html += '<span style="font-size:11px;color:var(--text-secondary);margin-left:auto;">Bing</span>';
    html += '</div>';

    html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:10px;">搜索词: ' + escapeHtml(query) + '</div>';

    if (results.length === 0) {
        html += '<div style="font-size:12px;color:var(--text-secondary);padding:8px 0;">未找到相关结果</div>';
    } else {
        html += '<div style="display:flex;flex-direction:column;gap:10px;">';
        results.forEach(function(r, i) {
            var title = r.title || r.url || '';
            var url = r.url || '';
            var snippet = r.snippet || '';
            var domain = '';
            try { domain = new URL(url).hostname; } catch(e) { domain = url.substring(0, 40); }

            html += '<div style="padding:10px;background:var(--bg-primary);border-radius:8px;border:1px solid var(--border);">';
            html += '<div style="font-size:13px;font-weight:500;margin-bottom:4px;">' + (i+1) + '. ' + escapeHtml(title) + '</div>';
            if (snippet) {
                html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;line-height:1.5;">' + escapeHtml(snippet.substring(0, 200)) + '</div>';
            }
            html += '<div style="font-size:11px;">';
            html += '<a href="' + escapeHtml(url) + '" target="_blank" style="color:var(--accent);text-decoration:none;">' + escapeHtml(domain) + ' ↗</a>';
            html += '</div>';
            html += '</div>';
        });
        html += '</div>';
        html += '<div style="font-size:11px;color:var(--text-tertiary);margin-top:10px;padding-top:8px;border-top:1px solid var(--border);">💡 点击链接查看原文</div>';
    }

    card.innerHTML = html;
    chatMessages.appendChild(card);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
window.renderFactSearchResults = renderFactSearchResults;

// ★ 2026-07-05: 模型安装确认卡片
function renderInstallConfirmCard(data) {
    const chatMessages = document.getElementById('messages') || document.getElementById('chatMessages');
    if (!chatMessages || !data) return;

    const card = document.createElement('div');
    card.className = 'install-confirm-card';
    card.id = 'installConfirmCard_' + (data.model_id || '').replace(/[^a-zA-Z0-9]/g, '_');
    card.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;padding:20px;margin:12px 0;animation:fadeIn 0.3s;';

    // 已安装的情况
    if (data.already_installed) {
        card.innerHTML = '<div style="font-size:14px;line-height:1.8;">' +
            '<div style="font-size:16px;font-weight:600;margin-bottom:12px;">📦 ' + escapeHtml(data.model_name || data.model_id) + '</div>' +
            '<div style="color:var(--text-secondary);">' + escapeHtml(data.message || '已安装') + '</div>' +
            '</div>';
        chatMessages.appendChild(card);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return;
    }

    // GGUF引导的情况
    if (data.is_gguf) {
        card.innerHTML = '<div style="font-size:14px;line-height:1.8;">' +
            '<div style="font-size:16px;font-weight:600;margin-bottom:12px;">📦 ' + escapeHtml(data.model_name || '') + '</div>' +
            '<div style="color:var(--text-secondary);margin-bottom:12px;">' + escapeHtml(data.message || '') + '</div>' +
            '<button class="btn-primary" style="padding:8px 16px;border-radius:8px;cursor:pointer;border:none;background:var(--accent);color:#fff;" onclick="window.location.href=\'/settings\'">前往设置页下载</button>' +
            '</div>';
        chatMessages.appendChild(card);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return;
    }

    // 正常安装确认
    var req = data.requirements || {};
    var reqHtml = '';
    if (req.ram) reqHtml += '<div>内存：' + escapeHtml(req.ram) + '</div>';
    if (req.gpu) reqHtml += '<div>显卡：' + escapeHtml(req.gpu) + '</div>';
    if (req.disk) reqHtml += '<div>硬盘：' + escapeHtml(req.disk) + '</div>';

    card.innerHTML = '<div style="font-size:14px;line-height:1.8;">' +
        '<div style="font-size:16px;font-weight:600;margin-bottom:8px;">📦 安装 ' + escapeHtml(data.model_name || data.model_id) + '</div>' +
        '<div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">' + escapeHtml(data.description || '') + '</div>' +
        '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">' +
        '<div>大小：' + escapeHtml(data.size || '未知') + '</div>' +
        reqHtml +
        '<div>安装方式：' + escapeHtml(data.install_type || '') + '</div>' +
        '</div>' +
        '<div style="font-size:11px;color:var(--text-tertiary);margin-bottom:16px;">预计时间：3-10分钟（取决于网络）</div>' +
        '<div style="display:flex;gap:8px;">' +
        '<button class="btn-primary" style="flex:1;padding:10px;border-radius:8px;cursor:pointer;border:none;background:var(--accent);color:#fff;font-weight:600;" onclick="confirmInstallModel(\'' + escapeHtml(data.model_id) + '\', this)">🚀 一键安装</button>' +
        '<button class="btn-secondary" style="padding:10px 16px;border-radius:8px;cursor:pointer;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);" onclick="this.closest(\'.install-confirm-card\').remove()">取消</button>' +
        '</div>' +
        '</div>';
    chatMessages.appendChild(card);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
window.renderInstallConfirmCard = renderInstallConfirmCard;

// ★ 2026-07-05: 确认安装模型
async function confirmInstallModel(modelId, btn) {
    if (!modelId) return;
    btn.disabled = true;
    btn.textContent = '⏳ 安装中...';

    // 找到卡片，添加进度区
    var card = btn.closest('.install-confirm-card');
    var progressDiv = document.createElement('div');
    progressDiv.id = 'installProgress_' + modelId.replace(/[^a-zA-Z0-9]/g, '_');
    progressDiv.style.cssText = 'padding:12px;background:var(--bg-primary);border-radius:8px;margin-top:12px;font-size:13px;color:var(--text-secondary);';
    progressDiv.innerHTML = '⏳ 正在安装 ' + modelId + ' ...<br>请耐心等待，不要关闭页面。';
    if (card) card.appendChild(progressDiv);

    try {
        var resp = await fetch('/api/models/installer/install/' + encodeURIComponent(modelId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        var data = await resp.json();

        if (data.success) {
            if (progressDiv) {
                progressDiv.innerHTML = '<div style="color:#10b981;font-weight:600;">✅ ' + escapeHtml(data.message || '安装完成') + '</div>' +
                    '<div style="margin-top:8px;font-size:12px;color:var(--text-tertiary);">' + escapeHtml(data.next_step || '现在可以使用了') + '</div>';
            }
            btn.textContent = '✅ 已安装';
            btn.style.background = '#10b981';
            toast('✅ ' + (data.message || '安装完成'));
        } else {
            if (progressDiv) {
                progressDiv.innerHTML = '<div style="color:#ef4444;">❌ ' + escapeHtml(data.message || data.error || '安装失败') + '</div>';
            }
            btn.disabled = false;
            btn.textContent = '🚀 重试安装';
            toast('❌ 安装失败：' + (data.message || data.error || '未知错误'));
        }
    } catch(e) {
        if (progressDiv) {
            progressDiv.innerHTML = '<div style="color:#ef4444;">❌ 网络错误: ' + escapeHtml(e.message) + '</div>';
        }
        btn.disabled = false;
        btn.textContent = '🚀 重试安装';
    }
}
window.confirmInstallModel = confirmInstallModel;

// ★ 2026-08-11: 脚本Agent操作确认卡片
function renderConfirmActionCard(data) {
    var messagesDiv = document.getElementById('messages');
    var card = document.createElement('div');
    card.className = 'install-confirm-card';
    card.style.cssText = 'background:rgba(245,158,11,0.15);border:2px solid rgba(245,158,11,0.4);border-radius:12px;padding:16px;margin:8px 0;text-align:center';
    card.innerHTML =
        '<div style="font-size:20px;margin-bottom:8px;">⚠️</div>' +
        '<div style="color:var(--text-primary);font-size:14px;margin-bottom:4px;"><b>' + escapeHtml(data.agent_name || '系统操作') + '</b></div>' +
        '<div style="color:var(--text-secondary);font-size:12px;margin-bottom:12px;">' + escapeHtml(data.description || '是否执行此操作？') + '</div>' +
        '<div style="display:flex;gap:8px;justify-content:center;">' +
        '<button class="btn-primary" style="padding:8px 24px;border-radius:8px;cursor:pointer;border:none;background:var(--primary);color:#fff;" onclick="confirmScriptAction(\'' + escapeHtml(data.agent_id) + '\',\'' + escapeHtml(data.message || '') + '\',this)">✅ 确认执行</button>' +
        '<button class="btn-secondary" style="padding:8px 24px;border-radius:8px;cursor:pointer;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);" onclick="this.closest(\'.install-confirm-card\').remove()">取消</button>' +
        '</div>';
    messagesDiv.appendChild(card);
    card.scrollIntoView({behavior:'smooth',block:'end'});
}
window.renderConfirmActionCard = renderConfirmActionCard;

function confirmScriptAction(agentId, message, btn) {
    var card = btn.closest('.install-confirm-card');
    card.innerHTML = '<div style="color:var(--warn);text-align:center;padding:12px;">⏳ 执行中...</div>';
    // 发送确认消息，触发引擎执行
    sendMessage('[CONFIRM_AGENT:' + agentId + '] ' + (message || ''));
    setTimeout(function(){ card.remove(); }, 500);
}
window.confirmScriptAction = confirmScriptAction;

// ★ 2026-07-05: 安装进度更新（预留，当前用同步方式）
function renderInstallProgress(data) {
    var progressDiv = document.getElementById('installProgress_' + (data.model_id || '').replace(/[^a-zA-Z0-9]/g, '_'));
    if (!progressDiv) return;
    var stage = data.stage || '';
    var message = data.message || '';
    var icon = stage === 'done' ? '✅' : stage === 'error' ? '❌' : '⏳';
    progressDiv.innerHTML = icon + ' ' + escapeHtml(message);
}
window.renderInstallProgress = renderInstallProgress;

// ★ 2026-07-05: 专项培养创建确认卡片
function renderSpecCreateCard(data) {
    const chatMessages = document.getElementById('messages') || document.getElementById('chatMessages');
    if (!chatMessages || !data) return;

    const card = document.createElement('div');
    card.className = 'spec-create-card';
    card.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;padding:20px;margin:12px 0;animation:fadeIn 0.3s;';

    // 自定义模式（没匹配到预设）
    if (data.action === 'create_custom' && !data.preset) {
        card.innerHTML = '<div style="font-size:14px;line-height:1.8;">' +
            '<div style="font-size:16px;font-weight:600;margin-bottom:12px;">🤖 创建专项培养</div>' +
            '<div style="color:var(--text-secondary);margin-bottom:16px;">' + escapeHtml(data.message || '请填写角色信息') + '</div>' +
            '<div style="margin-bottom:12px;"><label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:4px;">角色名称</label>' +
            '<input type="text" id="specCustomName" placeholder="如：翻译助手" style="width:100%;box-sizing:border-box;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);font-size:13px;"></div>' +
            '<div style="margin-bottom:12px;"><label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:4px;">角色规则（系统提示词）</label>' +
            '<textarea id="specCustomPrompt" placeholder="如：你是专业翻译，用户发中文翻译成英文..." style="width:100%;box-sizing:border-box;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);font-size:13px;min-height:80px;resize:vertical;"></textarea></div>' +
            '<div style="display:flex;gap:8px;">' +
            '<button class="btn-primary" style="flex:1;padding:10px;border-radius:8px;cursor:pointer;border:none;background:var(--accent);color:#fff;font-weight:600;" onclick="confirmCreateSpecCustom(this)">🚀 创建</button>' +
            '<button class="btn-secondary" style="padding:10px 16px;border-radius:8px;cursor:pointer;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);" onclick="this.closest(\'.spec-create-card\').remove()">取消</button>' +
            '</div></div>';
        chatMessages.appendChild(card);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return;
    }

    // 预设模板模式
    var name = data.name || '';
    var icon = data.icon || '🤖';
    var desc = data.description || '';
    var prompt = data.system_prompt || '';

    card.innerHTML = '<div style="font-size:14px;line-height:1.8;">' +
        '<div style="font-size:16px;font-weight:600;margin-bottom:8px;">' + icon + ' 创建专项培养：' + escapeHtml(name) + '</div>' +
        '<div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">' + escapeHtml(desc) + '</div>' +
        '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;background:var(--bg-primary);padding:10px;border-radius:6px;border:1px solid var(--border);">' +
        '<div style="font-weight:600;margin-bottom:4px;">📋 角色规则：</div>' +
        '<div style="white-space:pre-wrap;">' + escapeHtml(prompt) + '</div></div>' +
        '<div style="display:flex;gap:8px;margin-top:16px;">' +
        '<button class="btn-primary" style="flex:1;padding:10px;border-radius:8px;cursor:pointer;border:none;background:var(--accent);color:#fff;font-weight:600;" onclick="confirmCreateSpec(\'' + escapeHtml(name) + '\',\'' + escapeHtml(icon) + '\',\'' + escapeHtml(desc) + '\',this)">🚀 创建</button>' +
        '<button class="btn-secondary" style="padding:10px 16px;border-radius:8px;cursor:pointer;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);" onclick="this.closest(\'.spec-create-card\').remove()">取消</button>' +
        '</div></div>';
    chatMessages.appendChild(card);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
window.renderSpecCreateCard = renderSpecCreateCard;

// ★ 2026-07-05: 确认创建专项培养（预设模板）
async function confirmCreateSpec(name, icon, desc, btn) {
    var card = btn.closest('.spec-create-card');
    var promptEl = card.querySelector('[style*="white-space:pre-wrap"]');
    var prompt = promptEl ? promptEl.textContent : '';

    btn.disabled = true;
    btn.textContent = '⏳ 创建中...';

    try {
        var resp = await fetch('/api/specializations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                description: desc,
                system_prompt: prompt
            })
        });
        var data = await resp.json();
        if (data.id) {
            card.innerHTML = '<div style="color:#10b981;font-weight:600;padding:12px;">✅ 专项培养"' + name + '"创建成功！</div>' +
                '<div style="font-size:12px;color:var(--text-tertiary);margin-top:8px;">💡 创建新对话时选择这个专项培养即可使用</div>';
            toast('✅ 专项培养"' + name + '"创建成功');
        } else {
            btn.disabled = false;
            btn.textContent = '🚀 创建';
            toast('❌ 创建失败：' + (data.error || '未知错误'));
        }
    } catch(e) {
        btn.disabled = false;
        btn.textContent = '🚀 创建';
        toast('❌ 网络错误：' + e.message);
    }
}
window.confirmCreateSpec = confirmCreateSpec;

// ★ 2026-07-05: 确认创建自定义专项培养
async function confirmCreateSpecCustom(btn) {
    var card = btn.closest('.spec-create-card');
    var name = document.getElementById('specCustomName').value.trim();
    var prompt = document.getElementById('specCustomPrompt').value.trim();

    if (!name) { toast('请输入角色名称'); return; }
    if (!prompt) { toast('请输入角色规则'); return; }

    btn.disabled = true;
    btn.textContent = '⏳ 创建中...';

    try {
        var resp = await fetch('/api/specializations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                description: '用户自定义角色',
                system_prompt: prompt
            })
        });
        var data = await resp.json();
        if (data.id) {
            card.innerHTML = '<div style="color:#10b981;font-weight:600;padding:12px;">✅ 专项培养"' + name + '"创建成功！</div>' +
                '<div style="font-size:12px;color:var(--text-tertiary);margin-top:8px;">💡 创建新对话时选择这个专项培养即可使用</div>';
            toast('✅ 专项培养"' + name + '"创建成功');
        } else {
            btn.disabled = false;
            btn.textContent = '🚀 创建';
            toast('❌ 创建失败：' + (data.error || '未知错误'));
        }
    } catch(e) {
        btn.disabled = false;
        btn.textContent = '🚀 创建';
        toast('❌ 网络错误：' + e.message);
    }
}
window.confirmCreateSpecCustom = confirmCreateSpecCustom;

// ★ 2026-07-05: 知识库上传引导卡片
function renderKbUploadGuideCard(data) {
    const chatMessages = document.getElementById('messages') || document.getElementById('chatMessages');
    if (!chatMessages || !data) return;

    const card = document.createElement('div');
    card.className = 'kb-upload-guide-card';
    card.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;padding:20px;margin:12px 0;animation:fadeIn 0.3s;';

    var kbCount = data.kb_count || 0;
    var formats = data.formats || 'txt/md/csv/pdf/docx/xlsx/pptx';
    var msg = data.message || '知识库可以存储文档，对话时自动搜索相关内容。';

    var countHtml = kbCount > 0
        ? '<div style="font-size:12px;color:#10b981;margin-bottom:8px;">📚 当前知识库已有 ' + kbCount + ' 个文档</div>'
        : '<div style="font-size:12px;color:var(--text-tertiary);margin-bottom:8px;">📚 当前知识库为空</div>';

    card.innerHTML = '<div style="font-size:14px;line-height:1.8;">' +
        '<div style="font-size:16px;font-weight:600;margin-bottom:8px;">📚 知识库上传</div>' +
        '<div style="color:var(--text-secondary);margin-bottom:12px;">' + escapeHtml(msg) + '</div>' +
        countHtml +
        '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;background:var(--bg-primary);padding:10px;border-radius:6px;border:1px solid var(--border);">' +
        '<div style="font-weight:600;margin-bottom:4px;">📋 支持格式：</div>' +
        '<div>' + formats + '</div></div>' +
        '<div style="font-size:12px;color:var(--text-tertiary);margin-bottom:16px;">' +
        '💡 上传方式：点击下方按钮打开知识库面板，拖拽文件或粘贴文本即可</div>' +
        '<div style="display:flex;gap:8px;">' +
        '<button class="btn-primary" style="flex:1;padding:10px;border-radius:8px;cursor:pointer;border:none;background:var(--accent);color:#fff;font-weight:600;" onclick="openKbModalFromChat()">📚 打开知识库</button>' +
        '<button class="btn-secondary" style="padding:10px 16px;border-radius:8px;cursor:pointer;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);" onclick="this.closest(\'.kb-upload-guide-card\').remove()">知道了</button>' +
        '</div></div>';
    chatMessages.appendChild(card);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
window.renderKbUploadGuideCard = renderKbUploadGuideCard;

// ★ 2026-07-05: 从对话打开知识库面板
function openKbModalFromChat() {
    if (typeof window.openModal === 'function') {
        window.openModal('kbModal');
    } else if (typeof window.openKbModal === 'function') {
        window.openKbModal();
    }
}
window.openKbModalFromChat = openKbModalFromChat;

// ★ 2026-07-06: 受控进化提案卡片（R4 + E1人话描述 + E2风险反问）
function renderEvolutionProposalCard(data) {
    const chatMessages = document.getElementById('messages') || document.getElementById('chatMessages');
    if (!chatMessages || !data) return;

    const card = document.createElement('div');
    card.className = 'evolution-proposal-card';
    card.style.cssText = 'background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;padding:20px;margin:12px 0;animation:fadeIn 0.3s;';

    var riskLevel = data.risk_level || 'low';
    var riskDesc = data.risk_desc || '低风险';
    var riskIcon = riskLevel === 'high' ? '🔴' : riskLevel === 'medium' ? '⚠️' : '✅';
    var riskColor = riskLevel === 'high' ? '#ef4444' : riskLevel === 'medium' ? '#f59e0b' : '#10b981';
    var needsConfirm = data.needs_confirm || false;
    var warning = data.warning || '';
    var changes = data.changes || [];

    // E1: 人话描述
    var descHtml = '<div style="font-size:14px;line-height:1.8;margin-bottom:16px;">' +
        '<div style="font-size:16px;font-weight:600;margin-bottom:8px;">🧬 受控进化提案</div>' +
        '<div style="color:var(--text-secondary);margin-bottom:12px;white-space:pre-wrap;">' + escapeHtml(data.description || '') + '</div>';

    // 修改文件列表
    if (changes.length > 0) {
        descHtml += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;background:var(--bg-primary);padding:10px;border-radius:6px;border:1px solid var(--border);">' +
            '<div style="font-weight:600;margin-bottom:4px;">📁 修改文件：</div>';
        changes.forEach(function(c) {
            descHtml += '<div style="margin:4px 0;">• ' + escapeHtml(c.file || '') + ' — ' + escapeHtml(c.desc || '') + '</div>';
        });
        descHtml += '</div>';
    }

    // E2: 风险标注
    descHtml += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:12px;padding:8px 12px;background:' + riskColor + '20;border-radius:6px;border:1px solid ' + riskColor + '40;">' +
        '<span style="font-size:16px;">' + riskIcon + '</span>' +
        '<span style="font-size:13px;font-weight:600;color:' + riskColor + ';">风险等级：' + escapeHtml(riskDesc) + '</span>' +
        '</div>';

    // E2: 风险反问
    if (warning) {
        descHtml += '<div style="font-size:13px;color:#f59e0b;margin-bottom:16px;padding:12px;background:#f59e0b10;border-radius:8px;border:1px solid #f59e0b40;line-height:1.6;">' +
            escapeHtml(warning) + '</div>';
    }

    // 按钮
    var approveText = riskLevel === 'high' ? '🔴 我确认修改' : riskLevel === 'medium' ? '⚠️ 批准修改' : '✅ 批准';
    descHtml += '<div style="display:flex;gap:8px;">' +
        '<button class="btn-primary" style="flex:1;padding:10px;border-radius:8px;cursor:pointer;border:none;background:' + riskColor + ';color:#fff;font-weight:600;" onclick="approveEvolution(this, ' + JSON.stringify(data).replace(/"/g, '&quot;') + ')">' + approveText + '</button>' +
        '<button class="btn-secondary" style="padding:10px 16px;border-radius:8px;cursor:pointer;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);" onclick="rejectEvolution(this)">❌ 不改了</button>' +
        '</div>';

    descHtml += '<div style="font-size:11px;color:var(--text-tertiary);margin-top:12px;padding-top:8px;border-top:1px solid var(--border);">💡 批准后会自动备份原文件，如果出问题可以说"回滚"恢复</div>';

    descHtml += '</div>';
    card.innerHTML = descHtml;
    chatMessages.appendChild(card);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
window.renderEvolutionProposalCard = renderEvolutionProposalCard;

// ★ 2026-07-06: 批准进化提案
async function approveEvolution(btn, data) {
    if (!data || !data.changes || data.changes.length === 0) {
        toast('提案数据异常');
        return;
    }

    // 高风险需要二次确认
    if (data.risk_level === 'high') {
        if (!confirm('这是高风险修改！确定要继续吗？\n\n已自动备份原文件，出问题可以回滚。')) {
            return;
        }
    }

    var card = btn.closest('.evolution-proposal-card');
    btn.disabled = true;
    btn.textContent = '⏳ 正在应用修改...';

    try {
        var resp = await fetch('/api/evolution/code-proposal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: data.title || '受控进化提案',
                description: data.description || '',
                changes: data.changes.map(function(c) {
                    return {
                        file: c.file || '',
                        action: c.action || 'write',
                        content: c.content || '',
                        desc: c.desc || ''
                    };
                }),
                proposed_by: 'user'
            })
        });
        var result = await resp.json();

        if (result.success) {
            // 应用提案
            if (result.proposal_id) {
                var applyResp = await fetch('/api/evolution/code-proposal/' + result.proposal_id + '/apply', {
                    method: 'POST'
                });
                var applyResult = await applyResp.json();

                if (applyResult.success) {
                    if (card) {
                        card.innerHTML = '<div style="color:#10b981;font-weight:600;padding:16px;">✅ 修改已应用成功！</div>' +
                            '<div style="font-size:12px;color:var(--text-tertiary);margin-top:8px;">已自动备份原文件。如出问题说"回滚"恢复。</div>';
                    }
                    toast('✅ 修改已应用');
                } else {
                    btn.disabled = false;
                    btn.textContent = '✅ 批准';
                    toast('❌ 应用失败：' + (applyResult.error || '未知错误'));
                }
            } else {
                if (card) {
                    card.innerHTML = '<div style="color:#10b981;font-weight:600;padding:16px;">✅ 提案已保存！</div>' +
                        '<div style="font-size:12px;color:var(--text-tertiary);margin-top:8px;">提案ID: ' + (result.proposal_id || '') + '</div>';
                }
                toast('✅ 提案已保存');
            }
        } else {
            btn.disabled = false;
            btn.textContent = '✅ 批准';
            toast('❌ 创建提案失败：' + (result.error || '未知错误'));
        }
    } catch(e) {
        btn.disabled = false;
        btn.textContent = '✅ 批准';
        toast('❌ 网络错误：' + e.message);
    }
}
window.approveEvolution = approveEvolution;

// ★ 2026-07-06: 拒绝进化提案
function rejectEvolution(btn) {
    var card = btn.closest('.evolution-proposal-card');
    if (card) {
        card.innerHTML = '<div style="color:var(--text-secondary);padding:16px;text-align:center;">❌ 已取消修改</div>';
    }
}
window.rejectEvolution = rejectEvolution;

// ★ 2026-07-06: installSearchSkillFromChat 已移除（SearXNG双路+Bing内置）

// ===== 长任务进度条 =====
function renderTaskProgress(data) {
    if (!data || !data.task_id) return;
    const container = document.getElementById('chatMessages');
    if (!container) return;
    let bar = document.getElementById('task-progress-' + data.task_id);
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'task-progress-' + data.task_id;
        bar.className = 'task-progress-card';
        bar.innerHTML = '<div class="tp-header"><span class="tp-icon">⏳</span><span class="tp-title">长任务</span><span class="tp-status">' + escapeHtml(data.status || '') + '</span></div><div class="tp-bar-track"><div class="tp-bar-fill" style="width:' + (data.pct || 0) + '%"></div></div><div class="tp-info">' + (data.step || 0) + ' / ' + (data.total || '?') + '</div>';
        container.appendChild(bar);
    } else {
        bar.querySelector('.tp-status').textContent = escapeHtml(data.status || '');
        bar.querySelector('.tp-bar-fill').style.width = (data.pct || 0) + '%';
        bar.querySelector('.tp-info').textContent = (data.step || 0) + ' / ' + (data.total || '?');
    }
    if (data.type === 'done') { bar.querySelector('.tp-icon').textContent = '✅'; }
    if (data.type === 'error') { bar.querySelector('.tp-icon').textContent = '❌'; }
    bar.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

// ===== 模型状态检查（智能：区分通用/垂直模型） =====
const _NON_CHAT_KW = ['musicgen','bark','riffusion','audiocraft','whisper','stable-diffusion','sdxl','cogvideo','animatediff','embed','bge-','e5-','nomic'];

function _isChatModel(name) {
    const lower = (name || '').toLowerCase();
    return !_NON_CHAT_KW.some(kw => lower.includes(kw));
}

async function checkOllamaStatus() {
    const el = document.getElementById('modelStatus');
    const detailEl = document.getElementById('modelStatusDetail');
    const downloadHint = document.getElementById('ollamaDownloadHint');
    
    // ★ 如果当前是外脑模式，不检查本地引擎，直接显示外脑状态
    if (currentQueryMode === 'direct') {
        updateModelStatus('direct');
        return;
    }
    
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);  // 10秒超时（Ollama 响应慢时需要更长时间）
        const r = await fetch('/api/ollama/status', { signal: controller.signal });
        clearTimeout(timer);
        const d = await r.json();
        if (d.connected) {
            // 获取已安装模型列表并分类
            let genericModels = [], verticalModels = [], currentModel = (d.model || d.current_model || '').replace('local:', '');
            try {
                const mr = await fetch('/api/ollama/models');
                const models = await mr.json();
                if (Array.isArray(models)) {
                    models.forEach(m => {
                        const name = typeof m === 'string' ? m : (m.name || m.model || '');
                        if (_isChatModel(name)) genericModels.push(name);
                        else verticalModels.push(name);
                    });
                }
            } catch(e) { /* 静默，模型列表不影响基础状态 */ }

            const hasGeneric = genericModels.length > 0;
            const hasVertical = verticalModels.length > 0;
            const curIsChat = _isChatModel(currentModel);

            if (hasGeneric) {
                // 有通用模型 → 正常
                const displayName = currentModel || genericModels[0];
                // ★ 协作模式感知：根据 collabMode 调整显示文本
                const collabPrefix = (collabMode === 'local_dual') ? '🏠 本地双模 · ' :
                                     (collabMode === 'cloud_local') ? '☁️ 云地协作 · ' : '';
                if (el) { 
                    const prefix = (collabMode === 'local_dual') ? '🏠 本地双模' : (collabMode === 'cloud_local') ? '☁️ 云地协作' : '🟢';
                    el.textContent = prefix; 
                    el.style.color = '#22c55e'; 
                }
                if (detailEl) { 
                    let detail = hasVertical ? `+ ${verticalModels.length} 个垂直模型可用` : '本地运行时正常';
                    if (collabMode === 'local_dual') detail = '通用模型调度中 · ' + detail;
                    else if (collabMode === 'cloud_local') detail = '云端大模型思考中 · ' + detail;
                    detailEl.textContent = detail; 
                    detailEl.style.color = '#22c55e'; 
                }
            } else if (!curIsChat && currentModel) {
                // 只有垂直模型，没有通用模型
                if (el) { el.textContent = '⚠️ 缺少通用模型，无法对话'; el.style.color = '#f59e0b'; }
                if (detailEl) { detailEl.textContent = '只能生成音乐/图片/语音，请下载通用模型（如 qwen2.5:7b）或切 🤖外脑'; detailEl.style.color = '#f59e0b'; }
            } else {
                // 没有任何模型
                if (el) { el.textContent = '🟡 本地运行时已连接，暂无模型'; el.style.color = '#f59e0b'; }
                if (detailEl) { detailEl.textContent = '请下载模型或切 🤖外脑'; detailEl.style.color = '#f59e0b'; }
            }
            if (downloadHint) downloadHint.style.display = 'none';
        } else {
            if (el) { el.textContent = '🔴 ' + (d.message || '本地运行时未连接'); el.style.color = '#ef4444'; }
            if (detailEl) { detailEl.textContent = '请启动本地引擎'; detailEl.style.color = '#ef4444'; }
            if (downloadHint) downloadHint.style.display = 'block';
        }
    } catch (e) {
        const msg = (e.name === 'AbortError') ? '🔴 检测超时' : '🔴 连接失败';
        if (el) { el.textContent = msg; el.style.color = '#ef4444'; }
        if (detailEl) { detailEl.textContent = msg; detailEl.style.color = '#ef4444'; }
        if (downloadHint) downloadHint.style.display = 'block';
    }
}

async function loadInstalledModels() {
    try {
        const r = await fetch('/api/ollama/models');
        const models = await r.json();
        const sel = document.getElementById('setModelHidden') || document.getElementById('setModel');
        const cont = document.getElementById('installedModels');
        if (models.length) {
            cont.innerHTML = models.map(m => `<span>${m}</span>`).join('');
            // Update select options
            sel.innerHTML = models.map(m =>
                `<option value="${m}">${m}</option>`
            ).join('');
        } else {
            cont.textContent = '未检测到已安装模型';
        }
    } catch {
        document.getElementById('installedModels').textContent = '加载失败';
    }
}

// ===== Conversations =====
async function loadConversations() {
    try {
        const url = '/api/conversations' + (currentSpecId ? '?spec_id=' + currentSpecId : '');
        const r = await fetch(url);
        const convs = await r.json();
        // ★ 2026-07-26: 过滤掉定时任务等非对话项目（sched_开头）
        const realConvs = convs.filter(c => c && c.id && !String(c.id).startsWith('sched_'));
        renderConvList(realConvs);
    } catch { /* ignore */ }
}

function renderConvList(convs) {
    const el = document.getElementById('convList');
    if (!convs.length) {
        el.innerHTML = '<div style="padding:16px;color:var(--sidebar-text);font-size:13px;text-align:center;">暂无对话</div>';
        return;
    }
    
    // 按分类分组
    const grouped = {};
    const uncategorized = [];
    
    convs.forEach(c => {
        if (c.category) {
            if (!grouped[c.category]) grouped[c.category] = [];
            grouped[c.category].push(c);
        } else {
            uncategorized.push(c);
        }
    });
    
    // 分类顺序（固定的顺序）
    const catOrder = ['🤖 AI人工智能', '💻 编程开发', '🔧 工具软件', '🚗 汽车', '📚 教育学习', '💪 健康运动', '💰 财务投资', '🎮 娱乐游戏', '🍜 美食', '🏠 生活家居', '💼 工作职场', '❤️ 情感社交', '📰 新闻资讯'];
    
    let html = '';
    
    // 按固定顺序显示有分类的对话
    catOrder.forEach(cat => {
        if (grouped[cat]) {
            html += `<div class="conv-group-header">${escHtml(cat)} (${grouped[cat].length})</div>`;
            grouped[cat].forEach(c => {
                html += renderOneConv(c);
            });
            delete grouped[cat];
        }
    });
    
    // 显示剩余未预定义分类的对话
    Object.keys(grouped).forEach(cat => {
        html += `<div class="conv-group-header">${escHtml(cat)} (${grouped[cat].length})</div>`;
        grouped[cat].forEach(c => {
            html += renderOneConv(c);
        });
    });
    
    // 未分类的对话
    if (uncategorized.length > 0) {
        html += `<div class="conv-group-header">📝 未分类 (${uncategorized.length})</div>`;
        uncategorized.forEach(c => {
            html += renderOneConv(c);
        });
    }
    
    el.innerHTML = html;
}

function renderOneConv(c) {
    const time = formatTime(c.updated_at || c.created_at);
    const checked = selectedConvs.has(c.id) ? 'checked' : '';
    return `
    <div class="conv-item ${c.id === currentConvId ? 'active' : ''}" onclick="loadConv('${c.id}')">
        ${multiSelectMode ? `<input type="checkbox" class="conv-check" ${checked} onclick="event.stopPropagation();toggleConvSelect('${c.id}',this)">` : ''}
        <div class="conv-info">
            <span class="conv-title">${escHtml(c.title || '未命名对话')}</span>
            <div class="conv-meta"><span class="conv-time">${time}</span></div>
        </div>
        ${!multiSelectMode ? `<button class="conv-delete" onclick="event.stopPropagation();delConv('${c.id}')" title="删除">🗑</button>` : ''}
    </div>`;
}

function formatTime(dateStr) {
    if (!dateStr) return '';
    // ★ 2026-07-26: 手动解析日期，不依赖 new Date() 字符串解析（pywebview兼容）
    var parts = String(dateStr).match(/(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{1,2}):?(\d{1,2})?/);
    if (parts) {
        var year = parseInt(parts[1]);
        var month = parseInt(parts[2]) - 1;
        var day = parseInt(parts[3]);
        var hour = parseInt(parts[4]);
        var min = parseInt(parts[5]);
        var now = new Date();
        var isToday = year === now.getFullYear() && month === now.getMonth() && day === now.getDate();
        var hh = String(hour).padStart(2,'0');
        var mm = String(min).padStart(2,'0');
        if (isToday) return hh + ':' + mm;
        return (month+1) + '/' + day + ' ' + hh + ':' + mm;
    }
    return String(dateStr);
}

// ===== Manual Classify =====
async function manualClassify() {
    try {
        toast('正在分类对话...');
        const r = await fetch('/api/conversations/auto-classify', {method: 'POST'});
        const data = await r.json();
        if (data.classified > 0) {
            toast(`已分类 ${data.classified} 个对话`);
            loadConversations();
        } else if (data.total_unclassified === 0) {
            toast('所有对话已分类');
        } else {
            toast('未找到可分类的对话');
        }
    } catch (e) {
        toast('分类失败: ' + e.message);
    }
}

// ===== Multi Select =====
function toggleMultiSelect() {
    multiSelectMode = !multiSelectMode;
    selectedConvs.clear();
    const btn = document.getElementById('btnMultiSelect');
    const batchBar = document.getElementById('batchBar');
    if (multiSelectMode) {
        btn.textContent = '✅ 完成';
        batchBar.style.display = 'flex';
    } else {
        btn.textContent = '☑ 多选';
        batchBar.style.display = 'none';
    }
    loadConversations();
}

function toggleConvSelect(id, checkbox) {
    if (checkbox.checked) selectedConvs.add(id);
    else selectedConvs.delete(id);
    updateBatchCount();
}

function selectAllConvs() {
    const checks = document.querySelectorAll('.conv-check');
    const allChecked = Array.prototype.slice.call(checks).every(c => c.checked);
    checks.forEach(c => {
        c.checked = !allChecked;
        const _attr = c.closest('.conv-item') ? c.closest('.conv-item').getAttribute('onclick') : null;
        const _m = _attr ? _attr.match(/'([^']+)'/) : null;
        const id = _m ? _m[1] : null;
        if (id) {
            if (!allChecked) selectedConvs.add(id);
            else selectedConvs.delete(id);
        }
    });
    updateBatchCount();
}

function updateBatchCount() {
    const el = document.getElementById('batchCount');
    el.textContent = `已选 ${selectedConvs.size} 个`;
}

function batchDeleteConvs() {
    if (!selectedConvs.size) { toast('请先选择对话'); return; }
    if (!confirm(`确定删除选中的 ${selectedConvs.size} 个对话？`)) return;
    const ids = Array.from(selectedConvs);
    fetch('/api/conversations/batch-delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ids})
    }).then(() => {
        if (ids.includes(currentConvId)) newChat();
        selectedConvs.clear();
        loadConversations();
        updateBatchCount();
        toast(`已删除 ${ids.length} 个对话`);
    });
}

async function newChat() {
    currentConvId = null;
    _viewingOldConv = false;
    // 显示欢迎页（覆盖消息区，不清空）
    const welcomeEl = document.getElementById('welcomeScreen');
    if (welcomeEl) welcomeEl.style.display = 'flex';
    const titleEl = document.getElementById('convTitle');
    if (titleEl) titleEl.textContent = '';
    // 取消列表高亮
    document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
    // 刷新侧边栏列表
    loadConversations();
}

async function loadConv(id) {
    // ★ 2026-07-26: 加调试标记，确认点击是否生效
    currentConvId = id;
    _viewingOldConv = true;  // ★ 标记：正在查看旧对话
    var ws = document.getElementById('welcomeScreen');
    if (ws) ws.style.display = 'none';
    try {
        const r = await fetch('/api/conversations/' + id + '/messages');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const msgs = await r.json();
        renderMessages(msgs);
        // ★ 2026-08-06: 空对话重新显示欢迎页
        if (!msgs || msgs.length === 0) {
            if (ws) ws.style.display = 'flex';
        }
        loadConversations(); // refresh active state
    } catch(e) {
        // ★ 2026-07-26: pywebview兼容（不用toast，直接显示在消息区）
        var el = document.getElementById('messages');
        if (el) el.innerHTML = '<div style="padding:20px;color:#999;text-align:center;">对话加载失败，请重试</div>';
    }
}

async function delConv(id) {
    // ★ 2026-07-26: pywebview兼容（不用confirm，直接删除+可撤销提示）
    try {
        await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
        if (currentConvId === id) newChat();
        loadConversations();
        // 显示删除提示（不用toast）
        var tip = document.createElement('div');
        tip.style.cssText = 'position:fixed;top:60px;right:20px;background:#ef4444;color:white;padding:10px 16px;border-radius:8px;z-index:99999;font-size:13px;';
        tip.textContent = '对话已删除';
        document.body.appendChild(tip);
        setTimeout(function() { tip.remove(); }, 2500);
    } catch(e) {
        var tip2 = document.createElement('div');
        tip2.style.cssText = 'position:fixed;top:60px;right:20px;background:#ef4444;color:white;padding:10px 16px;border-radius:8px;z-index:99999;font-size:13px;';
        tip2.textContent = '删除失败';
        document.body.appendChild(tip2);
        setTimeout(function() { tip2.remove(); }, 2500);
    }
}

// ===== Messages =====
function renderMessages(msgs) {
    const el = document.getElementById('messages');
    if (!el) return;
    el.innerHTML = '';
    if (!msgs || !Array.isArray(msgs) || msgs.length === 0) {
        return;
    }
    msgs.forEach(m => {
        // ★ 2026-08-06: 增强渲染器 — 处理空内容/非字符串/缺失字段
        var content = m.content || m.text || m.message || '';
        if (typeof content !== 'string') {
            try { content = JSON.stringify(content, null, 2); } catch(e) { content = '[内容无法显示]'; }
        }
        if (!content.trim()) return;  // 跳过空消息
        if (m.role === 'user') appendUserMsg(content);
        else if (m.role === 'assistant' || m.role === 'ai') appendAiMsg(content);
        // 其他role（system/tool/function）也尝试显示
        else if (content) {
            appendAiMsg('[' + (m.role || 'unknown') + ']\n' + content);
        }
    });
    scrollBottom();
}

function appendUserMsg(text) {
    const el = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'msg user';
    const content = text.startsWith('[图片]') ?
        `<div style="color:var(--text-secondary);font-size:13px;margin-bottom:4px;">🖼️ 图片</div>${escHtml(text.replace('[图片]', '').trim())}` :
        escHtml(text).replace(/\n/g, '<br>');
    div.innerHTML = `<div class="msg-avatar">😊</div><div class="msg-body">${content}</div>`;
    el.appendChild(div);
    scrollBottom();
}

function appendAiMsg(text) {
    const el = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'msg assistant';
    // 检测是否有音乐文件
    var audioMatch = text.match(/文件:\s*(data\/media\/[^\s]+\.wav)/i);
    var bodyHtml = renderMd(text);
    if (audioMatch) {
        var audioPath = '/' + audioMatch[1];
        bodyHtml += '<div style="margin-top:8px;padding:8px;background:#1e1e3a;border-radius:8px;border:1px solid #333;">' +
            '<div style="font-size:11px;color:#4ade80;margin-bottom:4px;">🎵 音频播放器</div>' +
            '<audio controls style="width:100%;max-width:400px;" preload="auto">' +
            '<source src="' + audioPath + '" type="audio/wav">' +
            '您的浏览器不支持音频播放。</audio>' +
            '</div>';
    }
    div.innerHTML = '<div class="msg-avatar">⚡</div><div class="msg-body">' + bodyHtml + '</div>';
    el.appendChild(div);
    div.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
    scrollBottom();
}

function startAiStream() {
    // Remove any leftover stream msg from previous session
    removeStreamMsg();
    const el = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'msg assistant';
    div.id = 'streamMsg';
    div.innerHTML = `
        <div class="msg-avatar">⚡</div>
        <div class="msg-body">
            <div class="thinking-indicator">
                <div class="thinking-dots"><span></span><span></span><span></span></div>
                <span id="thinkingText">思考中...</span>
            </div>
            <div id="streamContent"></div>
        </div>
    `;
    el.appendChild(div);
    scrollBottom();
    return document.getElementById('streamContent');
}

function appendStreamChunk(text) {
    const el = document.getElementById('streamContent');
    if (!el) return;
    el.textContent += text;
    scrollBottom();
}

function finalizeStream() {
    const el = document.getElementById('streamContent');
    if (!el) return;
    const parent = document.getElementById('streamMsg');
    // Remove ids to prevent conflict with next stream
    if (parent) parent.removeAttribute('id');
    el.removeAttribute('id');
    el.innerHTML = renderMd(el.textContent);
    el.parentElement.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
    // Remove thinking indicator
    const think = el.parentElement.querySelector('.thinking-indicator');
    if (think) think.style.display = 'none';

    // ★ 注入模型来源标签
    if (window._lastModelSource) {
        const src = window._lastModelSource;
        const tag = document.createElement('div');
        tag.className = 'model-source-tag';
        if (src.is_external) {
            tag.innerHTML = `<span style="background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:2px 10px;border-radius:12px;font-size:11px;display:inline-block;margin-top:6px;opacity:0.85;">🤖 ${src.label} · 外脑模式</span>`;
        } else if (src.mode === 'collab') {
            tag.innerHTML = `<span style="background:linear-gradient(135deg,#6c5ce7,#a29bfe);color:#fff;padding:2px 10px;border-radius:12px;font-size:11px;display:inline-block;margin-top:6px;opacity:0.85;">${src.label}</span>`;
        } else {
            tag.innerHTML = `<span style="background:#555;color:#ccc;padding:2px 10px;border-radius:12px;font-size:11px;display:inline-block;margin-top:6px;opacity:0.7;">🏠 ${src.label}</span>`;
        }
        el.parentElement.appendChild(tag);
        window._lastModelSource = null; // 消费后清除
    }

    // ★ 添加"入库"按钮（外脑模式的好回答→保存到知识库）
    _addSaveKbButton(el.parentElement);

    scrollBottom();
}

// ★ 保存回答到知识库
function _addSaveKbButton(container) {
    const saveBtn = document.createElement('span');
    saveBtn.className = 'kb-save-btn';
    saveBtn.textContent = '📥 入库';
    saveBtn.title = '将这条回答保存到知识库';
    saveBtn.style.cssText = 'cursor:pointer;font-size:11px;color:#888;margin-left:8px;opacity:0.6;display:inline-block;user-select:none;';
    saveBtn.onmouseenter = function(){ this.style.opacity = '1'; };
    saveBtn.onmouseleave = function(){ this.style.opacity = '0.6'; };
    saveBtn.onclick = async function() {
        // 获取用户最近的问题（文档中最后一个.user消息）
        const msgs = document.getElementById('messages');
        const userMsgs = msgs.querySelectorAll('.msg.user');
        const lastUserMsg = userMsgs[userMsgs.length - 1];
        const question = lastUserMsg ? lastUserMsg.textContent.trim() : '';
        // 获取AI回答内容
        const answer = container.querySelector('.msg-body')?.textContent.trim() || container.textContent.trim();
        if (!question || !answer) { toast('无法获取问答内容', 'error'); return; }
        
        this.textContent = '⏳ 入库中...';
        this.style.cursor = 'default';
        this.onclick = null;
        
        try {
            const resp = await fetch('/api/knowledge/add_text', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: question.substring(0, 80),
                    content: '问：' + question + '\n答：' + answer
                })
            });
            const data = await resp.json();
            if (resp.ok) {
                this.textContent = '✅ 已入库';
                toast('✅ 已保存到知识库');
            } else {
                this.textContent = '❌ 入库失败';
                toast('入库失败: ' + (data.error || data.message || '未知错误'), 'error');
            }
        } catch(e) {
            this.textContent = '❌ 网络错误';
            toast('入库失败: 网络错误', 'error');
        }
        setTimeout(() => {
            this.textContent = '📥 入库';
            this.style.cursor = 'pointer';
            this.onclick = arguments.callee;
        }, 3000);
    };
    // 追加到模型标签后面
    const tag = container.querySelector('.model-source-tag') || container.querySelector('.msg-body');
    if (tag) tag.parentElement.appendChild(saveBtn);
}

function removeStreamMsg() {
    const el = document.getElementById('streamMsg');
    if (el) el.remove();
}

// ===== Send Message (消息队列版 · WorkBuddy风格) =====
async function sendMessage() {
    const input = document.getElementById('msgInput');
    const text = input.value.trim();

    // ★ 2026-08-03: 默认新对话 — 查看旧对话后发消息，自动开新对话
    if (currentConvId && typeof _viewingOldConv !== 'undefined' && _viewingOldConv) {
        currentConvId = null;
        _viewingOldConv = false;
        // 清空聊天区（ID是messages不是chatMessages）
        var chatArea = document.getElementById('messages');
        if (chatArea) chatArea.innerHTML = '';
        // 取消侧边栏高亮
        document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
    }

    // 视频消息：直接发送不走队列
    if (typeof selectedVideoFile !== 'undefined' && selectedVideoFile) {
        input.value = '';
        input.style.height = 'auto';
        await sendVideoMessage(text);
        return;
    }

    // 音频文件消息：直接发送不走队列
    if (typeof selectedAudioFile !== 'undefined' && selectedAudioFile) {
        input.value = '';
        input.style.height = 'auto';
        await sendAudioFileMessage(text);
        return;
    }

    // 通用文件消息：直接发送不走队列
    // ★ F6: 统一用 selectedFiles，删除 selectedFile
    if (typeof selectedFiles !== 'undefined' && selectedFiles && selectedFiles.length > 0) {
        input.value = '';
        input.style.height = 'auto';
        await sendFileMessage(text);
        return;
    }

    if (!text && (typeof selectedImageFiles === 'undefined' || selectedImageFiles.length === 0)) return;

    // Handle image（图片不走队列，直接发）
    if (typeof selectedImageFiles !== 'undefined' && selectedImageFiles.length > 0) {
        if (isProcessing) {
            const id = Date.now() + Math.random();
            msgQueue.push({ text, type: 'image', files: [...selectedImageFiles], id });
            input.value = '';
            input.style.height = 'auto';
            removeImage();
            updateQueueUI();
            toast('图片消息已排队，AI回复后自动发送');
            return;
        }
        await sendImageMessage(text);
        return;
    }

    input.value = '';
    input.style.height = 'auto';

    // 如果AI正在回复，入队等待 → 不显示在聊天区，进入队列面板
    if (isProcessing) {
        const id = Date.now() + Math.random();
        msgQueue.push({ text, type: 'text', id });
        document.getElementById('welcomeScreen').style.display = 'none';
        updateQueueUI();
        toast('消息已排队（' + msgQueue.length + '条），AI回复后自动发送');
        return;
    }

    // 立即在UI显示用户消息
    document.getElementById('welcomeScreen').style.display = 'none';
    appendUserMsg(text);

    // 直接发送
    await _sendMessageNow(text);
}

// 实际发送消息到后端（核心逻辑）
async function _sendMessageNow(text, skipVertical) {
    isProcessing = true;
    isStreaming = true;
    const sendBtn = document.getElementById('sendBtn');
    sendBtn.classList.add('stop');
    sendBtn.innerHTML = '⏹';
    sendBtn.onclick = function() { stopStream(); };  // 生成中点击 = 停止
    document.getElementById('modelStatus').textContent = '🟡 生成中...';
    // ★ 显示停止按钮（必须在可能抛异常的逻辑之前）
    document.getElementById('sendBtn').style.display = 'none';
    document.getElementById('stopBtn').style.display = 'flex';
    updateQueueUI();


    abortController = new AbortController();
    const streamEl = startAiStream();
    let fullText = '';
    let _verticalCardShown = false;  // ★ 2026-07-04: 标记是否显示了垂直卡片（action/inquiry）
    let _pendingSuggestions = null;  // 技能自动发现结果

    // 直接从 DOM 读取当前激活的查询模式（避免变量不同步）
    const activeBtn = document.querySelector('.qm-btn.active');
    const domQueryMode = activeBtn ? activeBtn.dataset.mode : currentQueryMode;
    try {
        console.log('[Send] query_mode=', domQueryMode, '(DOM)', '| var=', currentQueryMode, '| msg=', text.substring(0, 50));
        const r = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: text,
                conversation_id: currentConvId,
                specialization_id: currentSpecId || null,
                query_mode: domQueryMode,
                collab_mode: collabMode || null,
                web_search: webSearchEnabled || false,
                skip_vertical: skipVertical || false
            }),
            signal: abortController.signal
        });

        // ★ 检查HTTP状态码
        if (!r.ok) {
            const errText = await r.text().catch(() => '');
            let errMsg = `服务器错误 (${r.status})`;
            if (r.status === 429) errMsg = '请求过于频繁，请稍后再试';
            else if (r.status === 500) errMsg = '服务器内部错误，请查看终端日志';
            else if (r.status === 503) errMsg = '服务繁忙，请稍后再试';
            console.error('[Harven] API错误:', r.status, errText.substring(0, 200));
            removeStreamMsg();
            appendError('❌ ' + errMsg + '\n💡 提示：如持续报错，请重启 Harven AI');
            throw new Error(errMsg);
        }

        const reader = r.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep incomplete line

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const data = line.slice(6).trim();
                if (data === '[DONE]') continue;

                try {
                    const parsed = JSON.parse(data);
                    if (parsed.type === 'meta' && parsed.conversation_id) {
                        currentConvId = parsed.conversation_id;
                        _viewingOldConv = false;  // ★ 新消息已发送，不再是"查看旧对话"
                        // 自动保存该对话到当前模式
                        _saveCurrentConvByMode();
                    } else if (parsed.type === 'text') {
                        // 过滤模型可能输出的后台任务 JSON 块，不展示给用户
                        let _chunk = parsed.content;
                        // 简化过滤：实时检测并跳过纯 JSON 行
                        if (!window._jsonBlockActive) window._jsonBlockActive = false;
                        if (!window._jsonBlockBuf) window._jsonBlockBuf = '';

                        // 检测 JSON 块开始
                        const _hasJsonStart = _chunk.includes('```json') ||
                            _chunk.includes('"tool_calls"') ||
                            (_chunk.trim().startsWith('[') && _chunk.includes('"action"'));
                        
                        if (_hasJsonStart && !window._jsonBlockActive) {
                            window._jsonBlockActive = true;
                            window._jsonBlockBuf = _chunk;
                            _chunk = '';
                        } else if (window._jsonBlockActive) {
                            window._jsonBlockBuf += _chunk;
                            // 检测 JSON 块结束
                            const _buf = window._jsonBlockBuf;
                            const _hasEnd = _buf.includes('```', _buf.indexOf('```json') >= 0 ? _buf.indexOf('```json') + 7 : 0) ||
                                (_buf.includes(']') && _buf.includes('"action"')) ||
                                (_buf.includes('}') && _buf.includes('"tool_calls"'));
                            if (_hasEnd) {
                                // 剔除 JSON，保留纯文本
                                let _cleaned = _buf
                                    .replace(/```json[\s\S]*?```/g, '')
                                    .replace(/\{\s*"tool_calls"\s*:\s*\[[\s\S]*?\]\s*\}/g, '')
                                    .replace(/\[\s*\{[^}]*"action"\s*:[\s\S]*?\}\s*\]/g, '')
                                    .replace(/^\s*[\{\[][\s\S]*?[\}\]]\s*$/gm, '')
                                    .trim();
                                if (_cleaned) { appendStreamChunk(_cleaned); fullText += _cleaned; }
                                window._jsonBlockActive = false;
                                window._jsonBlockBuf = '';
                            }
                            _chunk = '';
                        }

                        if (_chunk && !window._jsonBlockActive) {
                            appendStreamChunk(_chunk);
                            fullText += _chunk;
                        }
                    } else if (parsed.type === 'thinking') {
                        const tt = document.getElementById('thinkingText');
                        if (tt) tt.textContent = parsed.content;
                    } else if (parsed.type === 'source') {
                        // GitHub下载/知识库/联网搜索结果提示
                        const tt = document.getElementById('thinkingText');
                        if (tt) tt.textContent = parsed.content;
                        // 也追加到聊天区作为系统消息
                        appendSystemMsg(parsed.content);
                    } else if (parsed.type === 'warning') {
                        appendSystemMsg('⚠️ ' + parsed.content);
                    } else if (parsed.type === 'notification') {
                        // 后端推送通知（如外脑回退、模式切换等）
                        if (parsed.content && typeof parsed.content === 'object') {
                            if (parsed.content.message) {
                                appendSystemMsg(parsed.content.message);
                            }
                            // 根据 type 做特定处理
                            if (parsed.content.type === 'foreign_model_fallback') {
                                if (typeof toast === 'function') {
                                    toast(parsed.content.message);
                                }
                            }
                            // 各种通知类型
                            const notifyTypes = [
                                'collab_simple_command',
                                'collab_specialized_model',
                                'collab_local_dual',
                                'nonchat_model',
                                'music_suggest',
                            ];
                            if (notifyTypes.includes(parsed.content.type)) {
                                if (typeof toast === 'function') {
                                    toast(parsed.content.message, 8000);
                                }
                            }
                        } else if (parsed.content) {
                            appendSystemMsg(String(parsed.content));
                        }
                    } else if (parsed.type === 'suggestion') {
                        // 对话式技能自动发现结果（渲染为安装卡片）
                        _pendingSuggestions = parsed.content || [];
                        window._lastSuggestions = _pendingSuggestions;
                    } else if (parsed.type === 'vertical_prefill') {
                        // ★ 2026-07-03: 垂直面板参数预填模式
                        // ★ 2026-07-03 修复: 不调用 removeStreamMsg()，否则后续 text 事件无法累积 fullText
                        //   → done 时 fullText 为空 → 触发"模型未返回内容"误报
                        //   handleVerticalPrefill() 已有 toast 通知，流式消息由后续 text 事件正常替换
                        handleVerticalPrefill(parsed.content);
                    } else if (parsed.type === 'vertical_action') {
                        // ★ 2026-07-03: 垂直模型引导卡片（含直接生成按钮）
                        // ★ 2026-07-04 修复: 不调用 removeStreamMsg()，否则 done 时 fullText 为空 → "模型未返回内容"
                        _verticalCardShown = true;  // ★ 标记：有卡片，done 时不报错
                        renderVerticalActionCard(parsed.content);
                    } else if (parsed.type === 'vertical_inquiry') {
                        // ★ 2026-07-04: AI 主动反问卡片（关键参数缺失时）
                        _verticalCardShown = true;  // ★ 标记：有卡片，done 时不报错
                        renderVerticalInquiryCard(parsed.content);
                    } else if (parsed.type === 'search_results') {
                        // ★ 2026-07-06: 搜索结果卡片（SearXNG双路+Bing内置）
                        renderSearchResultsCard(parsed.content);
                    } else if (parsed.type === 'fact_search_results') {
                        // ★ 2026-07-05: 事实查询直接展示搜索结果（不经过模型整合）
                        removeStreamMsg();
                        renderFactSearchResults(parsed.content);
                    } else if (parsed.type === 'install_confirm') {
                        // ★ 2026-07-05: 模型安装确认卡片
                        removeStreamMsg();
                        _verticalCardShown = true;
                        renderInstallConfirmCard(parsed.content);
                    } else if (parsed.type === 'confirm_action') {
                        // ★ 2026-08-11: 脚本Agent操作确认卡片
                        removeStreamMsg();
                        _verticalCardShown = true;
                        renderConfirmActionCard(parsed.content);
                    } else if (parsed.type === 'spec_create_confirm') {
                        // ★ 2026-07-05: 专项培养创建确认卡片
                        removeStreamMsg();
                        _verticalCardShown = true;
                        renderSpecCreateCard(parsed.content);
                    } else if (parsed.type === 'kb_upload_guide') {
                        // ★ 2026-07-05: 知识库上传引导卡片
                        removeStreamMsg();
                        _verticalCardShown = true;
                        renderKbUploadGuideCard(parsed.content);
                    } else if (parsed.type === 'evolution_proposal') {
                        // ★ 2026-07-06: 受控进化提案卡片
                        removeStreamMsg();
                        _verticalCardShown = true;
                        renderEvolutionProposalCard(parsed.content);
                    } else if (parsed.type === 'install_progress') {
                        // ★ 2026-07-05: 安装进度更新
                        renderInstallProgress(parsed.content);
                    } else if (parsed.type === 'task_progress') {
                        // 长任务进度条
                        renderTaskProgress(parsed.content);
                    } else if (parsed.type === 'error') {
                        removeStreamMsg();
                        appendError(parsed.content);
                        toast(parsed.content);
                } else if (parsed.type === 'model_source') {
                    // ★ 模型来源标识：记录本次回复的模型信息
                    window._lastModelSource = parsed.content;
                    console.log('[模型来源]', JSON.stringify(parsed.content));
                } else if (parsed.type === 'tool_result') {
                    // ★ 工具执行结果：模型生成的代码被执行了
                    const tr = parsed.content || {};
                    const ok = tr.success ? '✅' : '❌';
                    const out = (tr.output || '').trim();
                    const err = (tr.error || '').trim();
                    let msg = `${ok} 工具执行 #${tr.index || ''}`;
                    if (out) msg += `\n${out.substring(0, 500)}`;
                    if (err) msg += `\n错误: ${err.substring(0, 300)}`;
                    if (tr.success && !out) msg = `✅ 操作已完成`;
                    appendSystemMsg(msg);
                }
                } catch { /* skip malformed */ }
            }
        }

        if (fullText) {
            // ★ 最终清洗：移除流式过滤遗漏的 JSON 块
            fullText = fullText
                .replace(/```json[\s\S]*?```/g, '')
                .replace(/\{\s*"tool_calls"\s*:\s*\[[\s\S]*?\]\s*\}/g, '')
                .replace(/\[\s*\{[^}]*"action"\s*:[\s\S]*?\}\s*\]/g, '')
                .replace(/\n{3,}/g, '\n\n')
                .trim();
            
            // 如果清洗后仍有内容，更新 DOM 中的流式消息
            if (fullText) {
                const streamContent = document.getElementById('streamContent');
                if (streamContent) {
                    streamContent.textContent = fullText;
                }
            }
            finalizeStream();
        } else if (_verticalCardShown) {
            // ★ 2026-07-04: 有垂直卡片（action/inquiry）但无文字 → 正常结束，不报错
            removeStreamMsg();
        } else {
            // 流结束但没有收到任何文本 — 可能是后端超时或空响应
            removeStreamMsg();
            appendError('⚠️ 模型未返回内容。可能原因：\n1. 模型加载失败 → 重启本地引擎\n2. 提示词超长 → 切换到 🤖外脑 模式\n3. 模型版本兼容问题 → 更换模型\n4. 协作模式开启但无匹配Skill → 尝试关闭协作模式后重试');
        }

        // 渲染技能自动发现建议卡片
        if (_pendingSuggestions && _pendingSuggestions.length) {
            renderSuggestionCards(_pendingSuggestions);
        }

        // Refresh conv list
        loadConversations();
        scheduleAutoClassify();

    } catch (e) {
        if (e.name === 'AbortError') {
            if (!fullText && document.getElementById('streamMsg')) removeStreamMsg();
            else if (fullText) finalizeStream();
            toast('已停止生成');
        } else {
            removeStreamMsg();
            appendError('网络错误: ' + e.message);
            toast('发送失败');
        }
    } finally {
        isStreaming = false;
        isProcessing = false;
        restoreSendButton();
        sendBtn.classList.remove('stop');
        sendBtn.innerHTML = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>`;
        sendBtn.onclick = function() { sendMessage(); };  // 恢复为发送操作
        checkOllamaStatus();
        updateQueueUI();

        // 处理队列中的下一条消息
        processNextInQueue();
    }
}

// 处理队列中的下一条消息
function processNextInQueue() {
    if (msgQueue.length === 0) return;
    const item = msgQueue.shift();
    updateQueueUI();
    // 先显示用户消息到聊天区，再发送
    if (item.type === 'image' && item.files && item.files.length > 0) {
        appendUserMsg(item.text || '[图片]');
        selectedImageFiles = item.files;
        sendImageMessage(item.text);
    } else {
        appendUserMsg(item.text);
        setTimeout(() => _sendMessageNow(item.text), 150);
    }
}

// ===== 对话式技能自动发现 — 建议卡片渲染 =====

function renderSuggestionCards(suggestions) {
    const el = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'msg suggestion-cards';

    const sourceColors = {
        harvenhub: '#8b5cf6', github: '#333', gitee: '#c71d23',
        pypi: '#3776ab', huggingface: '#ff9d00', winget: '#0078d4',
    };

    const cards = suggestions.map((s, i) => {
        const srcLabel = s.source_label || s.source || '未知';
        const srcIcon = s.source_icon || '📦';
        const srcColor = sourceColors[s.source] || '#666';
        const stars = '⭐'.repeat(Math.round((s.quality || 5) / 2));
        const licenseBadge = (s.license || '').match(/mit|apache|bsd|cc0|unlicense|isc/i)
            ? '<span style="color:#22c55e;font-size:11px;">🟢 ' + escHtml(s.license) + '</span>'
            : '<span style="color:#888;font-size:11px;">📄 ' + escHtml(s.license || '?') + '</span>';

        // 是否有URL可以查看
        const viewLink = s.url
            ? `<a href="${escHtml(s.url)}" target="_blank" style="font-size:10px;color:#888;margin-left:6px;" onclick="event.stopPropagation()">查看 ↗</a>`
            : '';

        return `
        <div class="suggestion-card" style="position:relative;">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
                <span style="font-size:10px;background:${srcColor}15;color:${srcColor};padding:1px 5px;border-radius:3px;">${srcIcon} ${escHtml(srcLabel)}</span>
                ${licenseBadge}
                <span style="color:#eab308;font-size:11px;">${stars}</span>
                ${viewLink}
            </div>
            <div style="font-weight:600;font-size:14px;margin-bottom:3px;">🎯 ${escHtml(s.name)}</div>
            <div style="font-size:12px;color:#888;margin-bottom:8px;line-height:1.5;">${escHtml(s.description || '无描述')}</div>
            <button class="btn-primary btn-sm"
                    onclick="installSuggestion(${i}, this)"
                    style="width:100%;">
                ⬇️ 一键安装
            </button>
        </div>`;
    }).join('');

    div.innerHTML = `
        <div style="margin:8px 0;padding:10px;background:var(--bg-secondary);border-radius:10px;border:1px solid var(--border-color);">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:10px;">
                <span style="font-size:18px;">🔍</span>
                <span style="font-size:13px;font-weight:600;color:var(--text-primary);">为你找到 ${suggestions.length} 个相关技能</span>
                <span style="font-size:11px;color:#888;">（对话自动发现）</span>
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;">
                ${cards}
            </div>
            <div style="font-size:10px;color:#666;margin-top:8px;text-align:center;">
                仅推荐 MIT/Apache/BSD 等安全许可资源 · 安装前会自动扫描
            </div>
        </div>`;
    el.appendChild(div);
    scrollBottom();
}

async function installSuggestion(index, btn) {
    const suggestions = window._lastSuggestions || [];
    if (!suggestions[index]) return;

    const s = suggestions[index];
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ 安装中...';

    try {
        const r = await fetch('/api/resource-hub/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source: s.source,
                resource_id: s.resource_id || s.name,
                url: s.url,
                repo_url: s.url,
                resource_type: s.resource_type || 'skill',
                name: s.name,
                license_text: s.license || '',
            })
        });
        const d = await r.json();

        if (d.blocked) {
            btn.textContent = '⚠️ 需确认';
            btn.className = 'btn-warning btn-sm';
            btn.disabled = false;
            btn.onclick = function() {
                installSuggestionForce(index, btn);
            };
            toast('⚠️ 许可证不合规，点击再次确认安装');
            return;
        }

        if (d.success) {
            btn.textContent = '✅ 已安装';
            btn.className = 'btn-secondary btn-sm';
            toast('✅ 「' + s.name + '」安装成功！');
            // 刷新技能列表
            if (typeof loadSkills === 'function') loadSkills();
        } else {
            btn.textContent = '❌ 失败';
            btn.className = 'btn-danger btn-sm';
            setTimeout(() => { btn.textContent = originalText; btn.className = 'btn-primary btn-sm'; btn.disabled = false; }, 2000);
        }
    } catch (e) {
        btn.textContent = '❌ 错误';
        btn.className = 'btn-danger btn-sm';
        setTimeout(() => { btn.textContent = originalText; btn.className = 'btn-primary btn-sm'; btn.disabled = false; }, 2000);
    }
}

async function installSuggestionForce(index, btn) {
    const suggestions = window._lastSuggestions || [];
    if (!suggestions[index]) return;

    const s = suggestions[index];
    btn.disabled = true;
    btn.textContent = '⏳ 强制安装...';
    btn.className = 'btn-primary btn-sm';

    try {
        const r = await fetch('/api/resource-hub/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source: s.source,
                resource_id: s.resource_id || s.name,
                url: s.url,
                repo_url: s.url,
                resource_type: s.resource_type || 'skill',
                name: s.name,
                skip_license_check: true,
            })
        });
        const d = await r.json();
        if (d.success) {
            btn.textContent = '✅ 已安装';
            btn.className = 'btn-secondary btn-sm';
            toast('✅ 「' + s.name + '」已强制安装');
            if (typeof loadSkills === 'function') loadSkills();
        } else {
            btn.textContent = '❌ 失败';
            btn.className = 'btn-danger btn-sm';
        }
    } catch (e) {
        btn.textContent = '❌ 错误';
        btn.className = 'btn-danger btn-sm';
    }
}

// ===== 初始化：同步 localStorage 的查询模式到 DOM =====
document.addEventListener('DOMContentLoaded', () => {
    setQueryMode(currentQueryMode);
    // ★ 2026-07-05: 刷新后协作模式重置为关闭（用户需要时手动开）
    _syncCollabBtnState();
});

async function _syncCollabBtnState() {
    // ★ 2026-07-05: 刷新后协作模式强制重置为 off
    //   用户需要协作时手动选择，刷新不自动恢复
    //   后端也同步重置
    try {
        collabMode = 'off';
        const btn = document.getElementById('collabModeBtn');
        const btnIcon = document.getElementById('collabBtnIcon');
        const btnText = document.getElementById('collabBtnText');
        if (btnIcon) btnIcon.textContent = '🚫';
        if (btnText) btnText.textContent = '关闭';
        if (btn) btn.classList.remove('qm-btn-collab-active');
        document.querySelectorAll('.collab-menu-item[data-mode]').forEach(item => {
            const isActive = item.dataset.mode === 'off';
            item.classList.toggle('active', isActive);
            const check = item.querySelector('.collab-item-check');
            if (check) check.textContent = isActive ? '●' : '○';
        });

        // 后端也重置为 off（fire-and-forget）
        fetch('/api/collab/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'off' })
        }).catch(function() {});
    } catch (e) {
        // 静默失败
    }
}

// ===== 图片生成 =====

// 双重保险：脚本在body末尾执行，DOM已就绪
(function bindImageGen() {
    const imgGenBtn = document.getElementById('imgGenBtn');
    if (imgGenBtn) {
        imgGenBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            openImageGenDialog();
        });
    }
})();

function openImageGenDialog() {
    const overlay = document.getElementById('imgGenOverlay');
    if (!overlay) {
        console.error('[Harven] imgGenOverlay 元素未找到');
        return;
    }
    // 直接设置 style.display，绕过 CSS 优先级问题
    overlay.style.display = 'flex';
    overlay.classList.add('active');
    // 确保关键元素存在再操作
    const promptEl = document.getElementById('imgGenPrompt');
    const resultEl = document.getElementById('imgGenResult');
    const loadingEl = document.getElementById('imgGenLoading');
    const submitBtn = document.getElementById('imgGenSubmitBtn');
    if (promptEl) promptEl.value = '';
    if (resultEl) resultEl.classList.remove('active');
    if (loadingEl) loadingEl.classList.remove('active');
    if (submitBtn) submitBtn.disabled = false;
    // 自动获取输入框内容作为默认 prompt
    const msgInput = document.getElementById('msgInput');
    if (msgInput && msgInput.value && msgInput.value.trim() && promptEl) {
        promptEl.value = msgInput.value.trim();
    }
    setTimeout(() => { if (promptEl) promptEl.focus(); }, 100);
}

function closeImageGenDialog() {
    const overlay = document.getElementById('imgGenOverlay');
    if (overlay) {
        overlay.classList.remove('active');
        overlay.style.display = 'none';
    }
}

function submitImageGen() {
    const prompt = document.getElementById('imgGenPrompt').value.trim();
    if (!prompt) {
        toast('请输入图片描述');
        return;
    }

    const loading = document.getElementById('imgGenLoading');
    const result = document.getElementById('imgGenResult');
    const submitBtn = document.getElementById('imgGenSubmitBtn');
    const imgBtn = document.getElementById('imgGenBtn');

    loading.classList.add('active');
    result.classList.remove('active');
    submitBtn.disabled = true;
    submitBtn.textContent = '生成中...';
    if (imgBtn) imgBtn.classList.add('loading');

    fetch('/api/image/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt })
    })
    .then(r => r.json())
    .then(data => {
        loading.classList.remove('active');
        if (imgBtn) imgBtn.classList.remove('loading');
        submitBtn.textContent = '生成图片';
        submitBtn.disabled = false;

        if (data.success && data.url) {
            const img = document.getElementById('imgGenImage');
            img.src = data.url;
            img.onload = () => result.classList.add('active');
            img.onerror = () => {
                result.classList.add('active');
                img.alt = '图片加载失败';
            };
            // 同时在聊天区显示
            showImageInChat(data.url, prompt);
        } else {
            toast(data.error || '图片生成失败，请确认 Stable Diffusion 已启动');
        }
    })
    .catch(err => {
        loading.classList.remove('active');
        if (imgBtn) imgBtn.classList.remove('loading');
        submitBtn.textContent = '生成图片';
        submitBtn.disabled = false;
        toast('生成失败: ' + err.message);
    });
}

function showImageInChat(imageUrl, prompt) {
    // 添加 AI 消息到聊天区
    const el = document.getElementById('messages');
    if (!el) return;
    const div = document.createElement('div');
    div.className = 'msg assistant';
    div.innerHTML = '<div class="msg-avatar">⚡</div><div class="msg-body">' +
        '<p>🎨 已生成图片：' + escapeHtml(prompt) + '</p>' +
        '<div style="margin-top:8px;">' +
        '<img src="' + imageUrl + '" style="max-width:100%;max-height:400px;border-radius:12px;cursor:pointer;" ' +
        'onclick="window.open(this.src)" title="' + escapeHtml(prompt) + '" alt="' + escapeHtml(prompt) + '">' +
        '</div></div>';
    el.appendChild(div);
    scrollBottom();
}

// 点击弹窗空白处关闭
document.addEventListener('click', function(e) {
    const overlay = document.getElementById('imgGenOverlay');
    if (overlay && e.target === overlay) {
        closeImageGenDialog();
    }
});// ===== Window 全局导出（由自检脚本补全）=====
window.setQueryMode = setQueryMode;
window.checkOllamaStatus = checkOllamaStatus;
window.loadInstalledModels = loadInstalledModels;
window.loadConversations = loadConversations;
window.renderConvList = renderConvList;
window.renderOneConv = renderOneConv;
window.formatTime = formatTime;
window.manualClassify = manualClassify;
window.toggleMultiSelect = toggleMultiSelect;
window.toggleConvSelect = toggleConvSelect;

// ===== 事件监听（替代 onclick，避免加载时序问题）=====
// 直接绑定（脚本在 body 底部，DOM 已就绪）
const _btnMulti = document.getElementById('btnMultiSelect');
if (_btnMulti) _btnMulti.addEventListener('click', toggleMultiSelect);
// 双重保险：DOMContentLoaded 也绑（防止脚本被提前加载的情况）
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('btnMultiSelect');
    if (btn && !btn._multiBound) {
        btn._multiBound = true;
        btn.addEventListener('click', toggleMultiSelect);
    }
});
window.selectAllConvs = selectAllConvs;
window.setCollabRecipe = function(v) { console.log('[Collab] 配方:', v); };
window.updateBatchCount = updateBatchCount;
window.batchDeleteConvs = batchDeleteConvs;
window.newChat = newChat;
window.loadConv = loadConv;
window.delConv = delConv;
window.renderMessages = renderMessages;
window.appendUserMsg = appendUserMsg;
window.appendAiMsg = appendAiMsg;
window.startAiStream = startAiStream;
window.appendStreamChunk = appendStreamChunk;
window.finalizeStream = finalizeStream;
window.removeStreamMsg = removeStreamMsg;
window.sendMessage = sendMessage;

function stopGeneration() {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
    // 清除消息队列
    if (typeof msgQueue !== 'undefined') msgQueue = [];
    if (typeof updateQueueUI === 'function') updateQueueUI();
    restoreSendButton();
    document.getElementById('modelStatus').textContent = '⏹ 已停止';
    // 不追加错误消息，由catch块中的AbortError处理
}
window.stopGeneration = stopGeneration;

// 生成完成后恢复按钮状态（在所有完成路径中调用）
function restoreSendButton() {
    document.getElementById('sendBtn').style.display = '';
    document.getElementById('stopBtn').style.display = 'none';
}

// ★ 2026-08-08: ask_user 前端轮询 — 检测Skill脚本问用户问题
var _askUserPolling = null;
function startAskUserPolling() {
    if (_askUserPolling) clearInterval(_askUserPolling);
    _askUserPolling = setInterval(async function() {
        try {
            const r = await fetch('/api/ask_user/pending').then(r=>r.json());
            if (r.has_question) {
                // 显示问题给用户
                var existing = document.getElementById('_askUserModal');
                if (existing) return; // 已显示
                var modal = document.createElement('div');
                modal.id = '_askUserModal';
                modal.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.7);z-index:99999;display:flex;align-items:center;justify-content:center;';
                var optionsHtml = '';
                if (r.options && r.options.length) {
                    r.options.forEach(function(opt) {
                        optionsHtml += '<button onclick="window._answerAskUser(\'' + opt + '\')" style="margin:4px;padding:8px 20px;border-radius:6px;border:1px solid #4f46e5;background:transparent;color:#4f46e5;cursor:pointer;font-size:13px;">' + opt + '</button>';
                    });
                }
                modal.innerHTML = '<div style="background:#1a1a2e;border-radius:12px;padding:24px;max-width:400px;text-align:center;">' +
                    '<div style="font-size:14px;color:#c9d1d9;margin-bottom:16px;">' + r.question + '</div>' +
                    (optionsHtml || '<input type="text" id="_askUserInput" placeholder="输入回答..." style="width:100%;padding:8px;border-radius:6px;border:1px solid #333;background:#0d1117;color:#c9d1d9;font-size:13px;margin-bottom:12px;">' +
                    '<button onclick="window._answerAskUser()" style="padding:8px 24px;border-radius:6px;border:none;background:#4f46e5;color:#fff;cursor:pointer;font-size:13px;">提交</button>') +
                    '</div>';
                document.body.appendChild(modal);
                window._askUserQuestionId = r.id;
                if (!r.options || !r.options.length) {
                    var input = document.getElementById('_askUserInput');
                    if (input) input.focus();
                    input.onkeydown = function(e) { if (e.key === 'Enter') window._answerAskUser(); };
                }
            }
        } catch(e) {}
    }, 2000);
}

window._answerAskUser = function(answer) {
    if (!answer) {
        var input = document.getElementById('_askUserInput');
        if (input) answer = input.value;
    }
    if (!answer) return;
    fetch('/api/ask_user/answer', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: window._askUserQuestionId, answer: answer})
    });
    var modal = document.getElementById('_askUserModal');
    if (modal) modal.remove();
};

// 启动轮询
startAskUserPolling();

// ★ 2026-08-08: 多外脑快速切换
window.quickSwitchForeignModel = function(modelKey) {
    if (!modelKey) return;
    fetch('/api/multi-model/switch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({provider: modelKey})
    }).then(r=>r.json()).then(data=>{
        if (data.success) {
            toast('已切换到 ' + (data.name || modelKey), 3000);
        } else {
            toast('切换失败: ' + (data.error || '未知'), 3000);
        }
    }).catch(e=>toast('网络错误', 2000));
};

// 加载外脑列表到下拉框
async function loadQuickForeignModels() {
    try {
        const r = await fetch('/api/multi-model/list').then(r=>r.json());
        var sel = document.getElementById('quickForeignModelSelect');
        if (!sel) return;
        var models = r.models || r.providers || [];
        var html = '<option value="">当前外脑</option>';
        models.forEach(function(m) {
            if (m.configured) {
                var icon = m.icon || '🤖';  // ★ 2026-08-10: 兼容没有icon字段的响应
                html += '<option value="' + m.key + '">' + icon + ' ' + m.name + '</option>';
            }
        });
        sel.innerHTML = html;
    } catch(e) {}
}
setTimeout(loadQuickForeignModels, 2000);

window.processNextInQueue = processNextInQueue;
window.renderSuggestionCards = renderSuggestionCards;
window.installSuggestion = installSuggestion;
window.installSuggestionForce = installSuggestionForce;
window.openImageGenDialog = openImageGenDialog;
window.closeImageGenDialog = closeImageGenDialog;
window.submitImageGen = submitImageGen;
window.showImageInChat = showImageInChat;
window.toggleCollabDropdown = toggleCollabDropdown;
window.setCollabMode = setCollabMode;
window.showCollabHelp = showCollabHelp;



