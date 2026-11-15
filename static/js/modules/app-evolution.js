/**
 * 受控进化模块
 * 功能：检查更新、请求进化、批准/拒绝进化、回滚
 */

let _evolutionCache = null;

/**
 * 加载受控进化面板
 */
async function loadEvolutionPanel() {
    const panel = document.getElementById('evolution-panel');
    if (!panel) {
        console.error('[Evolution] 受控进化面板不存在');
        return;
    }

    panel.innerHTML = `
        <div class="evolution-container">
            <div class="evolution-header">
                <h3>🚀 受控进化</h3>
                <button class="btn btn-primary btn-sm" onclick="checkEvolutionUpdates()">
                    🔄 检查更新
                </button>
            </div>
            <div class="evolution-current">
                <h4>当前版本</h4>
                <div id="current-version-info">加载中...</div>
            </div>
            <div class="evolution-actions">
                <button class="btn btn-success btn-sm" onclick="loadEvolutionHistory()">
                    查看进化历史
                </button>
            </div>
            <div class="evolution-updates" id="evolution-updates"></div>
            <div class="evolution-history" id="evolution-history"></div>
        </div>
    `;

    await loadCurrentVersion();
}

/**
 * 加载当前版本信息
 */
async function loadCurrentVersion() {
    const infoEl = document.getElementById('current-version-info');
    if (!infoEl) return;

    try {
        const response = await fetch('/api/evolution/version');
        const data = await response.json();

        if (data.success) {
            const version = data.version;
            infoEl.innerHTML = `
                <div class="version-info">
                    <div><strong>版本：</strong>v${version.version || '0.2.0'}</div>
                    <div><strong>构建ID：</strong>${version.build_id || '-'}</div>
                    <div><strong>发布日期：</strong>${version.release_date || '-'}</div>
                </div>
            `;
        } else {
            infoEl.innerHTML = `<div class="error">加载失败: ${data.error || '未知错误'}</div>`;
        }
    } catch (error) {
        infoEl.innerHTML = `<div class="error">网络错误: ${error.message}</div>`;
    }
}

/**
 * 检查进化更新
 */
async function checkEvolutionUpdates() {
    const updatesEl = document.getElementById('evolution-updates');
    if (!updatesEl) return;

    updatesEl.innerHTML = '<div class="loading">正在检查更新...</div>';

    try {
        const response = await fetch('/api/evolution/check');
        const data = await response.json();

        if (data.success) {
            if (data.has_update) {
                updatesEl.innerHTML = `
                    <div class="update-available">
                        <h4>🎉 发现新版本</h4>
                        <div><strong>新版本：</strong>v${data.latest_version}</div>
                        <div><strong>当前版本：</strong>v${data.current_version}</div>
                        <div class="release-notes">
                            <strong>更新内容：</strong>
                            <pre>${data.release_notes || '暂无更新说明'}</pre>
                        </div>
                        <button class="btn btn-primary btn-sm" onclick="requestEvolution('${data.latest_version}', '${data.download_url}')">
                            请求更新
                        </button>
                    </div>
                `;
            } else {
                updatesEl.innerHTML = '<div class="info">✅ 当前已是最新版本</div>';
            }
        } else {
            updatesEl.innerHTML = `<div class="error">检查失败: ${data.error || '未知错误'}</div>`;
        }
    } catch (error) {
        updatesEl.innerHTML = `<div class="error">网络错误: ${error.message}</div>`;
    }
}

/**
 * 请求进化
 */
async function requestEvolution(version, downloadUrl) {
    try {
        const { data } = await evoPost('/api/evolution/request', {
            version: version,
            download_url: downloadUrl,
        });
        if (!data) return;
        if (data.success) {
            alert(`进化请求已提交，ID: ${data.log_id}，请等待批准`);
            await loadEvolutionHistory();
        } else {
            alert(`请求失败: ${data.error || '未知错误'}`);
        }
    } catch (error) {
        alert(`网络错误: ${error.message}`);
    }
}

/**
 * 加载进化历史
 */
async function loadEvolutionHistory() {
    const historyEl = document.getElementById('evolution-history');
    if (!historyEl) return;

    historyEl.innerHTML = '<div class="loading">加载中...</div>';

    try {
        const response = await fetch('/api/evolution/history');
        const data = await response.json();

        if (data.success) {
            renderEvolutionHistory(data.history);
        } else {
            historyEl.innerHTML = `<div class="error">加载失败: ${data.error || '未知错误'}</div>`;
        }
    } catch (error) {
        historyEl.innerHTML = `<div class="error">网络错误: ${error.message}</div>`;
    }
}

/**
 * 渲染进化历史
 */
function renderEvolutionHistory(history) {
    const historyEl = document.getElementById('evolution-history');
    if (!historyEl) return;

    if (!history || history.length === 0) {
        historyEl.innerHTML = '<div class="empty">暂无进化历史</div>';
        return;
    }

    let html = '<h4>进化历史</h4>';
    html += '<div class="evolution-list"><table class="table table-sm">';
    html += '<thead><tr><th>ID</th><th>类型</th><th>状态</th><th>请求时间</th><th>操作</th></tr></thead><tbody>';

    for (const log of history.reverse()) {
        const statusBadge = getEvolutionStatusBadge(log.status);
        const actions = getEvolutionActions(log);

        html += `
            <tr>
                <td>${log.id}</td>
                <td>${log.type || 'update'}</td>
                <td>${statusBadge}</td>
                <td>${formatDateTime(log.requested_at)}</td>
                <td>${actions}</td>
            </tr>
        `;
    }

    html += '</tbody></table></div>';
    historyEl.innerHTML = html;
}

/**
 * 获取进化状态徽章
 */
function getEvolutionStatusBadge(status) {
    const badgeMap = {
        'pending': '<span class="badge badge-warning">待批准</span>',
        'approved': '<span class="badge badge-success">已批准</span>',
        'rejected': '<span class="badge badge-danger">已拒绝</span>',
        'applied': '<span class="badge badge-success">已应用</span>',
        'failed': '<span class="badge badge-danger">失败</span>',
        'rolled_back': '<span class="badge badge-warning">已回滚</span>',
    };
    return badgeMap[status] || `<span class="badge badge-secondary">${status}</span>`;
}

/**
 * 获取进化操作按钮
 */
function getEvolutionActions(log) {
    let actions = '';

    if (log.status === 'pending') {
        actions += `<button class="btn btn-success btn-xs" onclick="approveEvolution(${log.id})">批准</button> `;
        actions += `<button class="btn btn-danger btn-xs" onclick="rejectEvolution(${log.id})">拒绝</button>`;
    } else if (log.status === 'applied') {
        actions += `<button class="btn btn-warning btn-xs" onclick="rollbackEvolution(${log.id})">回滚</button>`;
    }

    return actions;
}

/**
 * ★ 2026-11-15: 进化写操作统一入口（密码门）——先发请求；若后端返回 401 password_required（受保护动作），
 * 再弹窗收集管理员密码重试一次。密码门关闭（password_protected_actions 未含 evolution）时零打扰。
 */
async function evoPost(url, payload = {}) {
    const doPost = (body) => fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    let resp = await doPost(payload);
    let data = await resp.json().catch(() => ({}));
    if (resp.status === 401 && data.code === 'password_required') {
        const pwd = prompt('🔒 该操作需要管理员密码（受保护的进化操作）：', '');
        if (!pwd) {
            alert('已取消：未输入管理员密码');
            return null;
        }
        resp = await doPost({ ...payload, password: pwd });
        data = await resp.json().catch(() => ({}));
    }
    return { resp, data };
}

/**
 * 批准进化
 */
async function approveEvolution(logId) {
    if (!confirm(`确定要批准进化 #${logId} 吗？`)) {
        return;
    }

    try {
        const { resp, data } = await evoPost(`/api/evolution/${logId}/approve`);
        if (!data) return;
        if (data.success) {
            alert('批准成功');
            await loadEvolutionHistory();
        } else if (resp.status === 403 && data.code === 'password_wrong') {
            alert('批准失败: 管理员密码错误');
        } else {
            alert(`批准失败: ${data.error || '未知错误'}`);
        }
    } catch (error) {
        alert(`网络错误: ${error.message}`);
    }
}

/**
 * 拒绝进化
 */
async function rejectEvolution(logId) {
    if (!confirm(`确定要拒绝进化 #${logId} 吗？`)) {
        return;
    }

    try {
        const { resp, data } = await evoPost(`/api/evolution/${logId}/reject`);
        if (!data) return;
        if (data.success) {
            alert('拒绝成功');
            await loadEvolutionHistory();
        } else if (resp.status === 403 && data.code === 'password_wrong') {
            alert(`拒绝失败: 管理员密码错误`);
        } else {
            alert(`拒绝失败: ${data.error || '未知错误'}`);
        }
    } catch (error) {
        alert(`网络错误: ${error.message}`);
    }
}

/**
 * 回滚进化
 */
async function rollbackEvolution(logId) {
    if (!confirm(`确定要回滚进化 #${logId} 吗？此操作将恢复到之前的版本。`)) {
        return;
    }

    try {
        const { resp, data } = await evoPost(`/api/evolution/${logId}/rollback`);
        if (!data) return;
        if (data.success) {
            alert('回滚成功');
            await loadCurrentVersion();
            await loadEvolutionHistory();
        } else if (resp.status === 403 && data.code === 'password_wrong') {
            alert(`回滚失败: 管理员密码错误`);
        } else {
            alert(`回滚失败: ${data.error || '未知错误'}`);
        }
    } catch (error) {
        alert(`网络错误: ${error.message}`);
    }
}

/**
 * 格式化日期时间
 */
function formatDateTime(isoString) {
    if (!isoString) return '-';
    try {
        const date = new Date(isoString);
        return date.toLocaleString('zh-CN');
    } catch (e) {
        return isoString;
    }
}

// 导出函数
window.loadEvolutionPanel = loadEvolutionPanel;
window.loadCurrentVersion = loadCurrentVersion;
window.checkEvolutionUpdates = checkEvolutionUpdates;
window.requestEvolution = requestEvolution;
window.loadEvolutionHistory = loadEvolutionHistory;
window.approveEvolution = approveEvolution;
window.rejectEvolution = rejectEvolution;
window.rollbackEvolution = rollbackEvolution;

// ——— HarvenEvolution: 与 index.html tab-evolution 面板对接 ———
window.HarvenEvolution = {
    async checkUpdates() {
        const verEl = document.getElementById('evolutionCurrentVersion');
        const infoEl = document.getElementById('evolutionUpdateInfo');
        const badgeEl = document.getElementById('evolutionUpdateBadge');
        if (verEl) verEl.textContent = '检测中...';
        try {
            const r = await fetch('/api/evolution/check');
            const d = await r.json();
            if (verEl) verEl.textContent = 'v' + (d.current_version || '0.2.0');
            if (infoEl) infoEl.style.display = 'block';
            if (badgeEl) {
                if (d.has_update) {
                    badgeEl.style.background = 'rgba(74,222,128,0.15)';
                    badgeEl.style.color = '#4ade80';
                    badgeEl.textContent = `🎉 发现新版本 v${d.latest_version}，点击「批准进化」可更新`;
                } else {
                    badgeEl.style.background = 'rgba(148,163,184,0.1)';
                    badgeEl.style.color = 'var(--text-secondary)';
                    badgeEl.textContent = d.error || '✅ 当前已是最新版本';
                }
            }
        } catch(e) {
            if (verEl) verEl.textContent = '检测失败';
        }
        await this.loadHistory();
    },

    async loadHistory() {
        const listEl = document.getElementById('evolutionHistoryList');
        if (!listEl) return;
        try {
            const r = await fetch('/api/evolution/history');
            const d = await r.json();
            const history = d.history || [];
            if (!history.length) {
                listEl.innerHTML = '<div class="plugin-empty">暂无进化记录</div>';
                return;
            }
            listEl.innerHTML = history.slice().reverse().map(log => {
                const statusMap = {pending:'⏳待批准',approved:'✅已批准',rejected:'❌已拒绝',applied:'🚀已应用',failed:'⚠️失败',rolled_back:'↩已回滚'};
                const s = statusMap[log.status] || log.status;
                const actions = log.status === 'pending'
                    ? `<button class="btn-secondary btn-sm" style="margin-left:6px;" onclick="approveEvolution(${log.id})">批准</button><button class="btn-secondary btn-sm" style="margin-left:4px;" onclick="rejectEvolution(${log.id})">拒绝</button>`
                    : log.status === 'applied'
                    ? `<button class="btn-secondary btn-sm" style="margin-left:6px;" onclick="rollbackEvolution(${log.id})">回滚</button>`
                    : '';
                return `<div class="plugin-item" style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;">
                    <div><span style="font-size:12px;color:var(--text-secondary);">#${log.id}</span> ${s}</div>
                    <div style="display:flex;align-items:center;gap:4px;">
                        <span style="font-size:11px;color:var(--text-secondary);">${log.requested_at ? new Date(log.requested_at).toLocaleString('zh-CN') : ''}</span>
                        ${actions}
                    </div>
                </div>`;
            }).join('');
        } catch(e) {
            if (listEl) listEl.innerHTML = '<div class="plugin-empty">加载失败</div>';
        }
    },

    toggleEnabled(checked) {
        // 目前仅前端记录状态，后端无需接口
        console.log('[Evolution] 受控进化总开关:', checked);
    },

    // ── 进化提案系统 ──────────────────────────────────────────────

    async loadProposals(status) {
        const listEl = document.getElementById('evolutionProposalList');
        if (!listEl) return;
        listEl.innerHTML = '<div class="plugin-empty" style="padding:8px;">加载中...</div>';
        try {
            const url = status ? `/api/evolution/proposals?status=${status}` : '/api/evolution/proposals';
            const r = await fetch(url);
            const d = await r.json();
            this.renderProposals(d.proposals || []);
        } catch(e) {
            listEl.innerHTML = '<div class="plugin-empty">加载失败</div>';
        }
    },

    renderProposals(proposals) {
        const listEl = document.getElementById('evolutionProposalList');
        if (!listEl) return;
        if (!proposals.length) {
            listEl.innerHTML = '<div class="plugin-empty">暂无进化提案</div>';
            return;
        }
        const statusMap = {
            pending:  '⏳ 待审批',
            approved: '✅ 已批准',
            rejected: '❌ 已拒绝',
            applied:  '🚀 已应用',
            failed:   '⚠️ 失败',
        };
        const typeMap = {
            config_change:    '⚙️ 配置变更',
            skill_update:     '🔧 技能更新',
            knowledge_update: '📚 知识更新',
            other:            '📝 其他',
        };
        listEl.innerHTML = [...proposals].reverse().map(p => {
            const s = statusMap[p.status] || p.status;
            const t = typeMap[p.type] || p.type;
            const time = p.created_at ? new Date(p.created_at).toLocaleString('zh-CN') : '';
            const actions = p.status === 'pending'
                ? `<button class="btn-secondary btn-sm" style="margin-left:6px;color:#4ade80;border-color:#4ade80;" onclick="HarvenEvolution.approveProposal(${p.id})">批准</button>
                   <button class="btn-secondary btn-sm" style="margin-left:4px;color:#f87171;border-color:#f87171;" onclick="HarvenEvolution.rejectProposal(${p.id})">拒绝</button>`
                : '';
            const comment = p.review_comment ? `<div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">评语：${p.review_comment}</div>` : '';
            return `<div class="plugin-item" style="padding:10px 12px;border-bottom:1px solid var(--border);">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div style="flex:1;">
                        <div style="font-weight:500;font-size:13px;">${p.title || '无标题'}</div>
                        <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">${t} · ${s} · ${time}</div>
                        ${p.description ? `<div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">${p.description.slice(0,100)}${p.description.length>100?'...':''}</div>` : ''}
                        ${comment}
                    </div>
                    <div style="display:flex;align-items:center;flex-shrink:0;margin-left:8px;">${actions}</div>
                </div>
            </div>`;
        }).join('');
    },

    async approveProposal(id) {
        const comment = prompt(`批准提案 #${id}，可输入批注（可选）：`, '');
        if (comment === null) return; // 取消
        try {
            const { resp, data } = await evoPost(`/api/evolution/proposals/${id}/approve`, { comment: comment || '' });
            if (!data) return;
            if (data.success) { toast && toast('✅ 提案已批准'); this.loadProposals(); }
            else if (resp.status === 403 && data.code === 'password_wrong') { toast && toast('❌ 管理员密码错误'); }
            else { toast && toast(`批准失败: ${data.error || '未知错误'}`); }
        } catch(e) { toast && toast('请求失败: ' + e.message); }
    },

    async rejectProposal(id) {
        const comment = prompt(`拒绝提案 #${id}，请说明原因：`, '');
        if (comment === null) return;
        try {
            const { resp, data } = await evoPost(`/api/evolution/proposals/${id}/reject`, { comment: comment || '' });
            if (!data) return;
            if (data.success) { toast && toast('提案已拒绝'); this.loadProposals(); }
            else { toast && toast(`拒绝失败: ${data.error || '未知错误'}`); }
        } catch(e) { toast && toast('请求失败: ' + e.message); }
    },

    async createProposal() {
        // 简单弹窗收集提案信息
        const title = prompt('提案标题：', '');
        if (!title) return;
        const description = prompt('详细描述：', '');
        const typeChoice = prompt('提案类型（1=配置变更 2=技能更新 3=知识更新 4=其他）：', '4');
        const typeMap = { '1': 'config_change', '2': 'skill_update', '3': 'knowledge_update', '4': 'other' };
        const type = typeMap[typeChoice] || 'other';
        let target = '', newValue = '';
        if (type === 'config_change') {
            target = prompt('配置键（如 ollama.default_model）：', '') || '';
            newValue = prompt('新值：', '') || '';
        }
        try {
            const r = await fetch('/api/evolution/proposals', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, description, type, target, new_value: newValue, submitted_by: 'user' }),
            });
            const d = await r.json();
            if (d.success) { toast && toast('✅ 提案已提交，等待审批'); this.loadProposals(); }
            else { toast && toast('提交失败: ' + (d.error || '未知错误')); }
        } catch(e) { toast && toast('请求失败: ' + e.message); }
    },

    init() {
        // 加载当前版本
        fetch('/api/evolution/version').then(r => r.json()).then(d => {
            const verEl = document.getElementById('evolutionCurrentVersion');
            if (verEl && d.version) verEl.textContent = 'v' + d.version.version;
        }).catch(() => {});
        // 加载待审批提案数量（显示角标）
        this._loadPendingBadge();
    },

    async _loadPendingBadge() {
        try {
            const r = await fetch('/api/evolution/proposals?status=pending');
            const d = await r.json();
            const count = (d.proposals || []).length;
            const badgeEl = document.getElementById('evolutionPendingBadge');
            if (badgeEl) {
                badgeEl.textContent = count > 0 ? count : '';
                badgeEl.style.display = count > 0 ? 'inline-block' : 'none';
            }
        } catch(e) {}
    },

};  // HarvenEvolution 对象结束

// ===== Window 全局导出（由自检补全）=====
window.renderEvolutionHistory = renderEvolutionHistory;
window.getEvolutionStatusBadge = getEvolutionStatusBadge;
window.getEvolutionActions = getEvolutionActions;
window.formatDateTime = formatDateTime;
