/**
 * Harven AI - 多Agent协作 JS 模块
 * 协作模式切换、角色配置、会话管理、消息发送/轮询
 */

// ============ 全局状态 ============
const MultiAgent = {
    mode: 'sequential',
    agents: [],
    templates: [],
    currentSession: null,
    sessions: [],
    isRunning: false,
    messages: []
};

// ============ 初始化 ============
function openMultiAgentModal() {
    openModal('multiAgentModal');
    // 延迟初始化，等待 DOM 渲染
    setTimeout(() => initMultiAgentTab(), 100);
}

function initMultiAgentTab() {
    loadMultiAgentRoles();
    loadMultiAgentTemplates();
    loadMultiAgentSessions();
    bindMultiAgentEvents();
}

// ============ 加载数据 ============
async function loadMultiAgentRoles() {
    try {
        const res = await fetch('/api/multi-agent/roles');
        const data = await res.json();
        if (data.success) {
            renderAgentRoles(data.roles);
        }
    } catch (e) {
        console.error('加载角色失败:', e);
    }
}

async function loadMultiAgentTemplates() {
    try {
        const res = await fetch('/api/multi-agent/templates');
        const data = await res.json();
        if (data.success) {
            MultiAgent.templates = data.templates;
            renderTemplates(data.templates);
        }
    } catch (e) {
        console.error('加载模板失败:', e);
    }
}

async function loadMultiAgentSessions() {
    try {
        const res = await fetch('/api/multi-agent/sessions');
        const data = await res.json();
        if (data.success) {
            MultiAgent.sessions = data.sessions;
            renderSessionList(data.sessions);
        }
    } catch (e) {
        console.error('加载会话失败:', e);
    }
}

// ============ 渲染 ============
function renderAgentRoles(roles) {
    const container = document.getElementById('agentRolesGrid');
    if (!container) return;

    container.innerHTML = roles.map(r => `
        <div class="agent-role-card ${MultiAgent.agents.includes(r.id) ? 'selected' : ''}"
             onclick="toggleAgent('${r.id}')" data-role="${r.id}">
            <div class="agent-role-emoji">${r.emoji}</div>
            <div class="agent-role-name">${r.name}</div>
            <div class="agent-role-desc">${r.description}</div>
        </div>
    `).join('');
}

function renderTemplates(templates) {
    const container = document.getElementById('templateList');
    if (!container) return;

    container.innerHTML = templates.map(t => `
        <div class="template-card" onclick="selectTemplate('${t.id}')" data-tid="${t.id}">
            <div class="template-card-emoji">${t.emoji}</div>
            <div class="template-card-name">${t.name}</div>
            <div class="template-card-desc">${t.description}</div>
            <span class="badge badge-primary mt-2">${getModeLabel(t.mode)}</span>
        </div>
    `).join('');
}

function renderSessionList(sessions) {
    const container = document.getElementById('sessionList');
    if (!container) return;

    if (!sessions.length) {
        container.innerHTML = `<div class="empty-state">
            <div class="empty-state-icon">📭</div>
            <div class="empty-state-text">暂无协作会话</div>
        </div>`;
        return;
    }

    container.innerHTML = sessions.map(s => `
        <div class="sidebar-item ${MultiAgent.currentSession === s.id ? 'active' : ''}"
             onclick="loadSession('${s.id}')">
            <span class="status-dot ${s.status}"></span>
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(s.topic)}</span>
            <span class="badge badge-${s.status === 'completed' ? 'success' : s.status === 'running' ? 'warning' : 'primary'}">${s.status}</span>
        </div>
    `).join('');
}

function renderMessages(messages) {
    const container = document.getElementById('messageList');
    if (!container) return;

    if (!messages.length) {
        container.innerHTML = `<div class="empty-state">
            <div class="empty-state-icon">💬</div>
            <div class="empty-state-text">协作尚未开始，点击"开始协作"</div>
        </div>`;
        return;
    }

    container.innerHTML = messages.map(m => `
        <div class="message-item">
            <div class="message-header">
                <span>${m.agent_emoji || '🤖'}</span>
                <span class="message-role">${m.agent_name || m.agent_role || 'Agent'}</span>
                <span class="message-time">${formatTime(m.timestamp)}</span>
            </div>
            <div class="message-body">${escapeHtml(m.content || '')}</div>
        </div>
    `).join('');

    // 滚动到底部
    container.scrollTop = container.scrollHeight;
}

function updateCollabStatus(status) {
    const btn = document.getElementById('btnStartCollab');
    const statusEl = document.getElementById('collabStatus');

    if (status === 'running') {
        MultiAgent.isRunning = true;
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 协作中...'; }
        if (statusEl) statusEl.innerHTML = '<span class="badge badge-warning">运行中</span>';
    } else {
        MultiAgent.isRunning = false;
        if (btn) { btn.disabled = false; btn.innerHTML = '🚀 开始协作'; }
        if (statusEl) {
            const label = status === 'completed' ? '<span class="badge badge-success">已完成</span>' :
                         status === 'error' ? '<span class="badge badge-danger">出错</span>' :
                         '<span class="badge badge-primary">就绪</span>';
            statusEl.innerHTML = label;
        }
    }
}

// ============ 交互 ============
function toggleAgent(roleId) {
    const idx = MultiAgent.agents.indexOf(roleId);
    if (idx >= 0) {
        MultiAgent.agents.splice(idx, 1);
    } else {
        MultiAgent.agents.push(roleId);
    }
    // 更新UI
    const card = document.querySelector(`.agent-role-card[data-role="${roleId}"]`);
    if (card) card.classList.toggle('selected');
    updateSelectedAgentsDisplay();
}

function updateSelectedAgentsDisplay() {
    const el = document.getElementById('selectedAgents');
    if (!el) return;
    el.textContent = MultiAgent.agents.length
        ? `已选: ${MultiAgent.agents.join(', ')}`
        : '未选择Agent';
}

function switchMultiAgentMode(mode) {
    MultiAgent.mode = mode;
    document.querySelectorAll('.ma-mode-tab').forEach(t => t.classList.remove('active'));
    const activeTab = document.querySelector(`.ma-mode-tab[data-mode="${mode}"]`);
    if (activeTab) activeTab.classList.add('active');
}

function selectTemplate(templateId) {
    const template = MultiAgent.templates.find(t => t.id === templateId);
    if (!template) return;

    // 高亮模板
    document.querySelectorAll('.template-card').forEach(c => c.classList.remove('selected'));
    const card = document.querySelector(`.template-card[data-tid="${templateId}"]`);
    if (card) card.classList.add('selected');

    // 应用模板设置
    switchMultiAgentMode(template.mode);
    MultiAgent.agents = template.agents.map(a => a.role);

    // 更新角色选择UI
    document.querySelectorAll('.agent-role-card').forEach(card => {
        const role = card.getAttribute('data-role');
        if (MultiAgent.agents.includes(role)) {
            card.classList.add('selected');
        } else {
            card.classList.remove('selected');
        }
    });
    updateSelectedAgentsDisplay();

    // 可选填入话题
    const topicInput = document.getElementById('collabTopic');
    if (topicInput && !topicInput.value) {
        topicInput.placeholder = `示例: ${template.description}`;
    }
}

async function loadSession(sessionId) {
    MultiAgent.currentSession = sessionId;

    // 高亮侧边栏
    document.querySelectorAll('#sessionList .sidebar-item').forEach(i => i.classList.remove('active'));
    const item = document.querySelector(`#sessionList .sidebar-item[onclick*="${sessionId}"]`);
    if (item) item.classList.add('active');

    // 加载详情
    try {
        const res = await fetch(`/api/multi-agent/sessions/${sessionId}`);
        const data = await res.json();
        if (data.success) {
            const s = data.session;
            setCollabMode(s.mode);
            document.getElementById('collabTopic').value = s.topic || '';
            updateCollabStatus(s.status);

            // 加载消息
            await loadSessionHistory(sessionId);
        }
    } catch (e) {
        console.error('加载会话失败:', e);
    }
}

async function loadSessionHistory(sessionId) {
    try {
        const res = await fetch(`/api/multi-agent/sessions/${sessionId}/history`);
        const data = await res.json();
        if (data.success) {
            MultiAgent.messages = data.messages;
            renderMessages(data.messages);
        }
    } catch (e) {
        console.error('加载消息历史失败:', e);
    }
}

// ============ 执行协作 ============
async function startCollaboration() {
    const topic = document.getElementById('collabTopic').value.trim();
    if (!topic) {
        alert('请输入协作话题');
        return;
    }
    if (!MultiAgent.agents.length) {
        alert('请至少选择一个Agent角色');
        return;
    }

    updateCollabStatus('running');

    try {
        // 1. 创建会话
        const agentsConfig = MultiAgent.agents.map(role => ({
            role: role,
            instruction: `作为${role}专家参与话题: ${topic}`
        }));

        const createRes = await fetch('/api/multi-agent/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                topic: topic,
                mode: MultiAgent.mode,
                agents: agentsConfig
            })
        });
        const createData = await createRes.json();
        if (!createData.success) {
            alert('创建会话失败: ' + createData.error);
            updateCollabStatus('error');
            return;
        }

        MultiAgent.currentSession = createData.session_id;

        // 2. 执行协作
        const collabRes = await fetch('/api/multi-agent/collaborate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: createData.session_id })
        });
        const collabData = await collabRes.json();

        if (collabData.success) {
            updateCollabStatus('completed');

            // 渲染结果
            const resultEl = document.getElementById('collabResult');
            if (resultEl) {
                resultEl.innerHTML = renderCollabResult(collabData);
            }

            // 重新加载消息
            await loadSessionHistory(createData.session_id);
        } else {
            updateCollabStatus('error');
            alert('协作失败: ' + (collabData.error || '未知错误'));
        }

    } catch (e) {
        console.error('协作执行失败:', e);
        updateCollabStatus('error');
        alert('执行出错: ' + e.message);
    }

    // 刷新会话列表
    await loadMultiAgentSessions();
}

function renderCollabResult(data) {
    let html = '<div class="card"><div class="card-title mb-3">📊 协作结果</div>';

    if (data.mode === 'sequential') {
        html += `<p class="mb-2">模式：串行 | 步骤数：${data.total_steps}</p>`;
        data.steps.forEach(s => {
            html += `<div class="message-item"><div class="message-header">
                <span class="message-role">第${s.step}步 - ${s.name}</span></div>
                <div class="message-body">${escapeHtml(s.content?.substring(0, 500) || '')}${(s.content?.length > 500 ? '...' : '')}</div>
            </div>`;
        });
    } else if (data.mode === 'parallel') {
        html += `<p class="mb-2">模式：并行 | Agent数：${data.total_agents}</p>`;
        data.results.forEach(r => {
            html += `<div class="message-item"><div class="message-header">
                <span class="message-role">${r.name}</span></div>
                <div class="message-body">${escapeHtml(r.content?.substring(0, 500) || '')}${(r.content?.length > 500 ? '...' : '')}</div>
            </div>`;
        });
    } else if (data.mode === 'debate') {
        html += '<p class="mb-2">模式：辩论</p>';
        html += `<div class="message-item" style="border-left-color: #22c55e;"><div class="message-header"><span class="message-role">✅ 正方 - ${data.pro.name}</span></div><div class="message-body">${escapeHtml(data.pro.content?.substring(0, 300) || '')}</div></div>`;
        html += `<div class="message-item" style="border-left-color: #ef4444;"><div class="message-header"><span class="message-role">❌ 反方 - ${data.con.name}</span></div><div class="message-body">${escapeHtml(data.con.content?.substring(0, 300) || '')}</div></div>`;
        html += `<div class="message-item" style="border-left-color: #6366f1;"><div class="message-header"><span class="message-role">⚖️ 裁判 - ${data.judge.name}</span></div><div class="message-body">${escapeHtml(data.judge.content?.substring(0, 500) || '')}</div></div>`;
    } else if (data.mode === 'roleplay') {
        html += `<p class="mb-2">模式：角色扮演 | 轮次：${data.dialogue?.length || 0}</p>`;
        (data.dialogue || []).forEach(d => {
            html += `<div class="message-item"><div class="message-header"><span class="message-role">${d.turn}. ${d.name}</span></div><div class="message-body">${escapeHtml(d.content?.substring(0, 500) || '')}</div></div>`;
        });
    } else if (data.mode === 'hierarchy') {
        html += `<p class="mb-2">模式：层级委派</p>`;
        (data.results || []).forEach(r => {
            html += `<div class="message-item" style="border-left-color: ${r.type === 'plan' ? '#f59e0b' : '#6366f1'};"><div class="message-header"><span class="message-role">${r.type === 'plan' ? '📋 主计划' : '🔧 执行'} - ${r.name}</span></div><div class="message-body">${escapeHtml(r.content?.substring(0, 500) || '')}</div></div>`;
        });
    }

    html += '</div>';
    return html;
}

// ============ 事件绑定 ============
function bindMultiAgentEvents() {
    // 模式切换
    document.querySelectorAll('.mode-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            setCollabMode(tab.dataset.mode);
        });
    });

    // 开始协作按钮
    const btnStart = document.getElementById('btnStartCollab');
    if (btnStart) {
        btnStart.addEventListener('click', startCollaboration);
    }

    // 刷新会话列表
    const btnRefresh = document.getElementById('btnRefreshSessions');
    if (btnRefresh) {
        btnRefresh.addEventListener('click', loadMultiAgentSessions);
    }

    // 创建新会话（清空）
    const btnNew = document.getElementById('btnNewSession');
    if (btnNew) {
        btnNew.addEventListener('click', () => {
            MultiAgent.currentSession = null;
            MultiAgent.messages = [];
            MultiAgent.agents = [];
            document.getElementById('collabTopic').value = '';
            document.getElementById('collabResult').innerHTML = '';
            updateCollabStatus('idle');
            renderMessages([]);
            updateSelectedAgentsDisplay();
            document.querySelectorAll('.agent-role-card').forEach(c => c.classList.remove('selected'));
            document.querySelectorAll('.template-card').forEach(c => c.classList.remove('selected'));
            document.querySelectorAll('#sessionList .sidebar-item').forEach(i => i.classList.remove('active'));
        });
    }
}

// ============ 工具函数 ============
function getModeLabel(mode) {
    const map = {
        'sequential': '串行', 'parallel': '并行', 'debate': '辩论',
        'roleplay': '角色扮演', 'hierarchy': '层级委派'
    };
    return map[mode] || mode;
}

function formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ===== Window 全局导出（由自检脚本补全）=====
window.openMultiAgentModal = openMultiAgentModal;
window.initMultiAgentTab = initMultiAgentTab;
window.loadMultiAgentRoles = loadMultiAgentRoles;
window.loadMultiAgentTemplates = loadMultiAgentTemplates;
window.loadMultiAgentSessions = loadMultiAgentSessions;
window.renderAgentRoles = renderAgentRoles;
window.renderTemplates = renderTemplates;
window.renderSessionList = renderSessionList;
window.renderMessages = renderMessages;
window.updateCollabStatus = updateCollabStatus;
window.toggleAgent = toggleAgent;
window.updateSelectedAgentsDisplay = updateSelectedAgentsDisplay;
window.switchMultiAgentMode = switchMultiAgentMode;
window.selectTemplate = selectTemplate;
window.loadSession = loadSession;
window.loadSessionHistory = loadSessionHistory;
window.startCollaboration = startCollaboration;
window.renderCollabResult = renderCollabResult;
window.bindMultiAgentEvents = bindMultiAgentEvents;
window.getModeLabel = getModeLabel;
window.formatTime = formatTime;
window.escapeHtml = escapeHtml;
