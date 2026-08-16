/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — NETWORK module ===== */
// ===== Cloudflare Tunnel (公网分享) =====
let tunnelPolling = null;

function loadServerInfo() {
    fetch('/api/server/info').then(r => r.json()).then(data => {
        if (data.local_url) {
            document.getElementById('shareLocalUrl').textContent = data.local_url;
        }
        if (data.tunnel_url) {
            document.getElementById('sharePublicUrl').innerHTML =
                `<a href="${data.tunnel_url}" target="_blank" style="color:#2563eb;text-decoration:none">${data.tunnel_url}</a>`;
            document.getElementById('btnStartTunnel').style.display = 'none';
            document.getElementById('btnStopTunnel').style.display = '';
            document.getElementById('btnCopyUrl').style.display = '';
        }
    }).catch(() => {});
    // 同时加载访问码状态和访客统计
    loadAccessCodeStatus();
    refreshVisitorStats();
}

function startTunnel() {
    const btn = document.getElementById('btnStartTunnel');
    btn.textContent = '⏳ 启动中...';
    btn.disabled = true;
    document.getElementById('sharePublicUrl').textContent = '正在连接 Cloudflare...';

    fetch('/api/tunnel/start', { method: 'POST' }).then(r => r.json()).then(data => {
        btn.disabled = false;
        if (data.success) {
            toast('✅ 公网穿透已启动');
            // 轮询获取 URL
            if (tunnelPolling) clearInterval(tunnelPolling);
            tunnelPolling = setInterval(loadServerInfo, 2000);
            // 30秒后停止轮询
            setTimeout(() => { if (tunnelPolling) clearInterval(tunnelPolling); }, 30000);
        } else {
            toast('❌ ' + (data.error || '启动失败'));
            btn.textContent = '🚀 开启公网穿透';
            document.getElementById('sharePublicUrl').textContent = '启动失败';
        }
    }).catch(e => {
        toast('❌ 请求失败');
        btn.textContent = '🚀 开启公网穿透';
        btn.disabled = false;
    });
}

function stopTunnel() {
    fetch('/api/tunnel/stop', { method: 'POST' }).then(() => {
        document.getElementById('sharePublicUrl').textContent = '未启动';
        document.getElementById('btnStartTunnel').style.display = '';
        document.getElementById('btnStartTunnel').textContent = '🚀 开启公网穿透';
        document.getElementById('btnStopTunnel').style.display = 'none';
        document.getElementById('btnCopyUrl').style.display = 'none';
        if (tunnelPolling) clearInterval(tunnelPolling);
        toast('公网穿透已关闭');
    }).catch(() => { toast('❌ 停止穿透失败，请重试'); });
}

function copyTunnelUrl() {
    const urlEl = document.getElementById('sharePublicUrl').querySelector('a');
    if (urlEl) {
        navigator.clipboard.writeText(urlEl.href).then(() => {
            toast('📋 链接已复制');
        }).catch(() => {
            // fallback
            const ta = document.createElement('textarea');
            ta.value = urlEl.href;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            toast('📋 链接已复制');
        });
    }
}

// loadAccessCodeStatus 已统一移至 app-devices.js

// setAccessCode / clearAccessCode / loadAccessCodeStatus 已统一移至 app-devices.js，此处不重复定义

// ===== 访客统计 =====
function refreshVisitorStats() {
    fetch('/api/visitor/stats').then(r => r.json()).then(data => {
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        set('statOnline',   data.online_now    !== null && data.online_now    !== undefined ? data.online_now    : '—');
        set('statToday',    data.today_visitors !== null && data.today_visitors !== undefined ? data.today_visitors : '—');
        set('statTotal',    data.total_visitors !== null && data.total_visitors !== undefined ? data.total_visitors : '—');
        set('statTodayMsg', data.today_messages !== null && data.today_messages !== undefined ? data.today_messages : '—');
        set('statTotalMsg', data.total_messages !== null && data.total_messages !== undefined ? data.total_messages : '—');
    }).catch(() => {});
}

let _visitorListVisible = false;
function toggleVisitorList() {
    _visitorListVisible = !_visitorListVisible;
    const panel = document.getElementById('visitorListPanel');
    if (!panel) return;
    if (_visitorListVisible) {
        panel.style.display = '';
        loadVisitorList();
    } else {
        panel.style.display = 'none';
    }
}

function loadVisitorList() {
    fetch('/api/visitor/list?limit=50').then(r => r.json()).then(list => {
        const el = document.getElementById('visitorListTable');
        if (!el) return;
        if (!list.length) { el.innerHTML = '<div style="padding:12px;color:#888">暂无访客记录</div>'; return; }
        let html = `<table style="width:100%;border-collapse:collapse">
            <thead><tr style="background:var(--bg-side);position:sticky;top:0">
                <th style="padding:7px 10px;text-align:left;font-size:11px;color:#888;font-weight:500">IP</th>
                <th style="padding:7px 10px;text-align:left;font-size:11px;color:#888;font-weight:500">浏览器</th>
                <th style="padding:7px 10px;text-align:center;font-size:11px;color:#888;font-weight:500">对话</th>
                <th style="padding:7px 10px;text-align:left;font-size:11px;color:#888;font-weight:500">最后访问</th>
                <th style="padding:7px 10px;text-align:center;font-size:11px;color:#888;font-weight:500">操作</th>
            </tr></thead><tbody>`;
        list.forEach(v => {
            const ua = v.user_agent ? v.user_agent.slice(0, 40) + (v.user_agent.length > 40 ? '…' : '') : '-';
            const lastSeen = v.last_seen ? v.last_seen.slice(0, 16) : '-';
            const blocked = v.is_blocked;
            const rowStyle = blocked ? 'opacity:0.5;' : '';
            html += `<tr style="border-bottom:1px solid var(--border);${rowStyle}">
                <td style="padding:6px 10px;font-size:12px">${v.ip_address || '-'}</td>
                <td style="padding:6px 10px;font-size:11px;color:#aaa">${ua}</td>
                <td style="padding:6px 10px;font-size:12px;text-align:center">${v.msg_count}</td>
                <td style="padding:6px 10px;font-size:11px;color:#aaa">${lastSeen}</td>
                <td style="padding:6px 10px;text-align:center">
                  ${blocked
                    ? `<button onclick="visitorUnblock('${v.session_id}')" style="font-size:11px;padding:2px 8px;border:1px solid #34d399;border-radius:4px;background:transparent;color:#34d399;cursor:pointer">解封</button>`
                    : `<button onclick="visitorBlock('${v.session_id}')" style="font-size:11px;padding:2px 8px;border:1px solid #f87171;border-radius:4px;background:transparent;color:#f87171;cursor:pointer">封禁</button>`
                  }
                </td>
            </tr>`;
        });
        html += '</tbody></table>';
        el.innerHTML = html;
    }).catch(() => {});
}

function visitorBlock(sid) {
    if (!confirm('确认封禁此访客？')) return;
    fetch('/api/visitor/block', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid })
    }).then(() => { toast('已封禁'); loadVisitorList(); });
}

function visitorUnblock(sid) {
    fetch('/api/visitor/unblock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid })
    }).then(() => { toast('已解封'); loadVisitorList(); });
}

// ============================================================
//  ⏰ 定时任务
// ============================================================
async function loadSchedulerTasks() {
    const el = document.getElementById('schedulerTaskList');
    el.innerHTML = '<div class="plugin-empty">加载中...</div>';
    try {
        const res = await fetch('/api/scheduler/tasks');
        const data = await res.json();
        if (!data.tasks || data.tasks.length === 0) {
            el.innerHTML = '<div class="plugin-empty">暂无定时任务</div>';
            return;
        }
        el.innerHTML = data.tasks.map(t => {
            const tid = t.task_id || t.id || '';
            const trigger = t.trigger || t.schedule_type || '';
            return `
            <div class="plugin-item" style="padding:12px 14px;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div style="flex:1;">
                        <div style="font-weight:600;font-size:14px;">${escHtml(t.name)}</div>
                        <div style="font-size:12px;color:var(--text-secondary);margin-top:3px;">${escHtml(trigger)} · ${escHtml(t.status || 'running')}</div>
                        <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">${escHtml((t.content || t.prompt || '').substring(0, 60))}</div>
                    </div>
                    <div style="display:flex;gap:6px;flex-shrink:0;margin-left:10px;">
                        <button class="btn-secondary btn-sm" onclick="pauseSchedulerTask('${tid}')">⏸</button>
                        <button class="btn-secondary btn-sm" style="color:#e74c3c;" onclick="deleteSchedulerTask('${tid}')">🗑</button>
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch(e) {
        el.innerHTML = '<div class="plugin-empty" style="color:#e74c3c;">加载失败</div>';
    }
}

async function createSchedulerTask() {
    const name = document.getElementById('newTaskName').value.trim();
    const schedule = document.getElementById('newTaskSchedule').value.trim();
    const content = document.getElementById('newTaskContent').value.trim();
    const taskType = document.getElementById('newTaskType').value;
    if (!name || !schedule || !content) { toast('请填写任务名称、时间和内容'); return; }
    try {
        const res = await fetch('/api/scheduler/tasks', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name, schedule_str: schedule, content, task_type: taskType })
        });
        const data = await res.json();
        if (data.success) {
            toast('✅ 定时任务已创建');
            document.getElementById('newTaskName').value = '';
            document.getElementById('newTaskSchedule').value = '';
            document.getElementById('newTaskContent').value = '';
            loadSchedulerTasks();
        } else {
            toast('❌ ' + (data.error || '创建失败'));
        }
    } catch(e) { toast('❌ 网络错误'); }
}

async function deleteSchedulerTask(taskId) {
    if (!confirm('确认删除该定时任务？')) return;
    const allowed = await requirePasswordIfNeeded('deleteSchedulerTask');
    if (!allowed) return;
    try {
        const res = await fetch(`/api/scheduler/tasks/${taskId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) { toast('已删除'); loadSchedulerTasks(); }
        else toast('删除失败');
    } catch(e) { toast('网络错误'); }
}

async function pauseSchedulerTask(taskId) {
    try {
        const res = await fetch(`/api/scheduler/tasks/${taskId}/pause`, { method: 'POST' });
        const data = await res.json();
        toast(data.success ? '⏸ 已暂停' : '操作失败');
        loadSchedulerTasks();
    } catch(e) { toast('网络错误'); }
}

function fillSchedulerTemplate(type) {
    const templates = {
        morning: { name: '每日早安', schedule: '每天早上8点', content: '查询今日天气，生成一条温馨的早安提醒，包含穿衣建议和今日重要节点' },
        summary: { name: '每日总结', schedule: '每天晚上9点', content: '总结今天的待办任务完成情况，列出明天需要关注的事项' },
        news:    { name: '新闻摘要', schedule: '每天早上7点', content: '搜索今日科技新闻和行业动态，生成5条摘要' },
        remind:  { name: '待办提醒', schedule: '每天上午10点', content: '查看待办任务列表，提醒今天需要完成的重要事项' }
    };
    const t = templates[type];
    if (t) {
        document.getElementById('newTaskName').value = t.name;
        document.getElementById('newTaskSchedule').value = t.schedule;
        document.getElementById('newTaskContent').value = t.content;
    }
}

// ========== 定时任务执行历史 ==========

async function loadSchedulerRunHistory() {
    const el = document.getElementById('schedulerRunHistory');
    if (!el) return;

    el.innerHTML = '<div class="plugin-empty">⏳ 加载中...</div>';

    try {
        const resp = await fetch('/api/scheduler/runs?limit=30');
        const data = await resp.json();
        const runs = data.runs || [];

        if (runs.length === 0) {
            el.innerHTML = '<div class="plugin-empty">📭 暂无执行记录</div>';
            return;
        }

        const statusIcons = { running: '⏳', completed: '✅', failed: '❌' };
        const statusColors = { running: '#f59e0b', completed: '#10b981', failed: '#f87171' };

        el.innerHTML = runs.map(r => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 10px;border-bottom:1px solid var(--border);font-size:12px;">
                <div style="flex:1;min-width:0;">
                    <span style="font-weight:500;">${escapeHtml(r.name || r.task_id)}</span>
                    <span style="color:#888;font-size:10px;margin-left:6px;">${(r.prompt || '').substring(0, 40)}</span>
                </div>
                <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
                    <span style="color:${statusColors[r.status] || '#888'};">${statusIcons[r.status] || '❓'} ${r.status}</span>
                    <span style="color:#888;font-size:10px;">${(r.started_at || '').slice(0, 16)}</span>
                    ${r.error ? `<span style="color:#f87171;font-size:10px;" title="${escapeHtml(r.error)}">⚠️</span>` : ''}
                </div>
            </div>
        `).join('');

    } catch (e) {
        el.innerHTML = `<div class="plugin-empty" style="color:#f87171;">❌ ${e.message}</div>`;
    }
}

window.loadSchedulerRunHistory = loadSchedulerRunHistory;

// ============================================================
//  🔗 工作流
// ============================================================
function openCustomWorkflow() {
    document.getElementById('customWorkflowEditor').style.display = 'block';
}

async function loadWorkflows() {
    const el = document.getElementById('workflowList');
    el.innerHTML = '<div class="plugin-empty">加载中...</div>';
    try {
        const res = await fetch('/api/workflows');
        const data = await res.json();
        // 兼容两种格式：{workflows:[]} 或 {builtin:[], custom:[]}
        const all = data.workflows || [...(data.builtin || []), ...(data.custom || [])];
        if (all.length === 0) { el.innerHTML = '<div class="plugin-empty">暂无工作流</div>'; return; }
        el.innerHTML = all.map(w => {
            const wid = w.workflow_id || w.id || '';
            return `
            <div class="plugin-item" style="padding:12px 14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-weight:600;font-size:14px;">${escHtml(w.name)}</div>
                        <div style="font-size:12px;color:var(--text-secondary);">${(w.steps||[]).length} 个步骤 · ${w.builtin?'内置':'自定义'}</div>
                    </div>
                    <div style="display:flex;gap:6px;">
                        <button class="btn-primary btn-sm" onclick="executeWorkflow('${wid}')">▶ 运行</button>
                        ${!w.builtin ? `<button class="btn-secondary btn-sm" style="color:#e74c3c;" onclick="deleteWorkflow('${wid}')">🗑</button>` : ''}
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch(e) { el.innerHTML = '<div class="plugin-empty" style="color:#e74c3c;">加载失败</div>'; }
}

async function executeWorkflow(wfId) {
    const input = prompt('请输入工作流的输入参数（如搜索关键词）：');
    if (input === null) return;
    toast('🔗 工作流执行中...');
    const area = document.getElementById('workflowResultArea');
    const resultEl = document.getElementById('workflowResult');
    area.style.display = 'block';
    resultEl.textContent = '执行中，请稍候...';
    try {
        const res = await fetch(`/api/workflows/${wfId}/run`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ input })
        });
        const data = await res.json();
        if (data.success) {
            resultEl.textContent = JSON.stringify(data.result, null, 2);
            toast('✅ 工作流执行完成');
        } else {
            resultEl.textContent = '执行失败：' + (data.error || '未知错误');
        }
    } catch(e) { resultEl.textContent = '网络错误：' + e.message; }
}

async function runWorkflowTemplate(templateId) {
    const input = prompt('请输入工作流输入（如：研究主题/代码内容/网页URL）：');
    if (input === null) return;
    toast('🔗 正在执行...');
    const area = document.getElementById('workflowResultArea');
    const resultEl = document.getElementById('workflowResult');
    area.style.display = 'block';
    resultEl.textContent = '执行中...';
    try {
        const res = await fetch(`/api/workflows/${templateId}/run`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ input })
        });
        const data = await res.json();
        resultEl.textContent = data.success ? JSON.stringify(data.result, null, 2) : ('失败：' + (data.error||''));
        if (data.success) toast('✅ 执行完成');
    } catch(e) { resultEl.textContent = '网络错误'; }
}

async function saveCustomWorkflow() {
    const name = document.getElementById('wfName').value.trim();
    const stepsRaw = document.getElementById('wfSteps').value.trim();
    if (!name || !stepsRaw) { toast('请填写名称和步骤'); return; }
    let steps;
    try { steps = JSON.parse(stepsRaw); } catch(e) { toast('步骤 JSON 格式错误'); return; }
    try {
        const res = await fetch('/api/workflows', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name, steps })
        });
        const data = await res.json();
        if (data.success) { toast('✅ 工作流已保存'); document.getElementById('customWorkflowEditor').style.display='none'; loadWorkflows(); }
        else toast('保存失败：' + (data.error||''));
    } catch(e) { toast('网络错误'); }
}

async function deleteWorkflow(wfId) {
    if (!confirm('确认删除？')) return;
    const allowed = await requirePasswordIfNeeded('deleteWorkflow');
    if (!allowed) return;
    try {
        const res = await fetch(`/api/workflows/${wfId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) { toast('已删除'); loadWorkflows(); }
    } catch(e) { toast('网络错误'); }
}

// ============================================================
//  🐳 代码沙箱
// ============================================================
async function runSandboxCode() {
    const code = document.getElementById('sandboxCode').value;
    const lang = document.getElementById('sandboxLang').value;
    const status = document.getElementById('sandboxStatus');
    const output = document.getElementById('sandboxOutput');
    if (!code.trim()) { toast('请输入代码'); return; }
    status.textContent = '⏳ 执行中...';
    output.textContent = '执行中...';
    output.style.color = '#cdd6f4';
    try {
        const res = await fetch('/api/sandbox/execute', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ code, language: lang })
        });
        const data = await res.json();
        if (data.success) {
            status.textContent = `✅ 完成 (${data.execution_time ? data.execution_time.toFixed(2) + 's' : ''})`;
            output.textContent = data.output || '(无输出)';
            output.style.color = '#a6e3a1';
        } else {
            status.textContent = '❌ 执行失败';
            output.textContent = data.output || data.error || '执行失败';
            output.style.color = '#f38ba8';
        }
    } catch(e) {
        status.textContent = '❌ 网络错误';
        output.textContent = '网络错误：' + e.message;
        output.style.color = '#f38ba8';
    }
}

function fillSandboxExample() {
    const lang = document.getElementById('sandboxLang').value;
    const examples = {
        python: `# Python 示例\nimport sys\nimport datetime\n\nprint(f"Python {sys.version}")\nprint(f"当前时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")\n\n# 简单计算\nresult = sum(range(1, 101))\nprint(f"1+2+...+100 = {result}")`,
        javascript: `// JavaScript 示例\nconsole.log("Node.js version:", process.version);\n\nconst arr = [1, 2, 3, 4, 5];\nconst doubled = arr.map(x => x * 2);\nconsole.log("doubled:", doubled);\n\nconst sum = arr.reduce((a, b) => a + b, 0);\nconsole.log("sum:", sum);`,
        shell: `#!/bin/bash\necho "Shell 示例"\necho "当前时间: $(date)"\necho "当前目录: $(pwd)"\nls -la | head -5`
    };
    document.getElementById('sandboxCode').value = examples[lang] || examples.python;
}

// ============================================================
//  🌐 浏览器代理
// ============================================================
async function checkBrowserStatus() {
    const badge = document.getElementById('browserStatusBadge');
    const text = document.getElementById('browserStatusText');
    const installBtn = document.getElementById('btnInstallPlaywright');
    badge.textContent = '检测中...';
    badge.style.background = '#fff3cd';
    badge.style.color = '#856404';
    try {
        const res = await fetch('/api/browser/status');
        const data = await res.json();
        if (data.playwright_installed && data.chromium_available) {
            badge.textContent = '✅ 就绪';
            badge.style.background = '#d4edda';
            badge.style.color = '#155724';
            text.textContent = 'Playwright 和 Chromium 均已安装，可以使用浏览器自动化';
            installBtn.style.display = 'none';
        } else if (data.playwright_installed) {
            badge.textContent = '⚠️ 缺少Chromium';
            badge.style.background = '#fff3cd';
            badge.style.color = '#856404';
            text.textContent = 'Playwright 已安装但 Chromium 未安装，点击按钮安装';
            installBtn.style.display = 'inline-block';
        } else {
            badge.textContent = '❌ 未安装';
            badge.style.background = '#f8d7da';
            badge.style.color = '#721c24';
            text.textContent = 'Playwright 未安装，点击按钮一键安装';
            installBtn.style.display = 'inline-block';
        }
    } catch(e) {
        badge.textContent = '❓ 无法连接';
        text.textContent = '服务未运行或检测失败';
    }
}

async function installPlaywright() {
    const btn = document.getElementById('btnInstallPlaywright');
    const text = document.getElementById('browserStatusText');
    btn.disabled = true;
    btn.textContent = '⏳ 安装中...';
    text.textContent = '正在安装 Chromium，这可能需要几分钟...';
    try {
        const res = await fetch('/api/browser/install', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            toast('✅ Chromium 安装成功！');
            checkBrowserStatus();
        } else {
            toast('❌ 安装失败：' + (data.error || ''));
            text.textContent = '安装失败：' + (data.error || '请手动运行安装命令');
        }
    } catch(e) { toast('网络错误'); }
    btn.disabled = false;
    btn.textContent = '⬇️ 安装 Chromium';
}

async function browserNavigate() {
    const url = document.getElementById('browserUrlInput').value.trim();
    if (!url) { toast('请输入 URL'); return; }
    showBrowserResult('正在打开 ' + url + '...');
    try {
        const res = await fetch('/api/browser/navigate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ url })
        });
        const data = await res.json();
        showBrowserResult(data.success ? ('✅ 已打开：' + url + '\n' + (data.title ? '页面标题：' + data.title : '')) : ('❌ ' + (data.error||'失败')));
    } catch(e) { showBrowserResult('网络错误：' + e.message); }
}

async function browserScreenshot() {
    showBrowserResult('正在截图...');
    try {
        const res = await fetch('/api/browser/screenshot', { method: 'POST' });
        const data = await res.json();
        if (data.success && data.screenshot) {
            showBrowserResult('✅ 截图成功');
            const imgArea = document.getElementById('browserScreenshotArea');
            const img = document.getElementById('browserScreenshotImg');
            img.src = 'data:image/png;base64,' + data.screenshot;
            imgArea.style.display = 'block';
        } else {
            showBrowserResult('❌ 截图失败：' + (data.error||''));
        }
    } catch(e) { showBrowserResult('网络错误'); }
}

async function browserGetContent() {
    showBrowserResult('正在提取内容...');
    try {
        const res = await fetch('/api/browser/content', { method: 'POST' });
        const data = await res.json();
        showBrowserResult(data.success ? ('📄 页面内容：\n' + (data.content||'').substring(0, 1000)) : ('❌ ' + (data.error||'')));
    } catch(e) { showBrowserResult('网络错误'); }
}

async function browserClose() {
    try {
        await fetch('/api/browser/close', { method: 'POST' });
        toast('浏览器已关闭');
        showBrowserResult('浏览器已关闭');
    } catch(e) { toast('网络错误'); }
}

async function browserQuick(action) {
    const urls = {
        zhihu: 'https://www.zhihu.com/hot',
        weibo: 'https://s.weibo.com/top/summary',
        github_trending: 'https://github.com/trending'
    };
    const url = urls[action];
    if (!url) return;
    document.getElementById('browserUrlInput').value = url;
    showBrowserResult('正在打开 ' + url + '...');
    try {
        await fetch('/api/browser/navigate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ url })
        });
        const res2 = await fetch('/api/browser/content', { method: 'POST' });
        const data = await res2.json();
        showBrowserResult(data.success ? ('📄 内容摘要：\n' + (data.content||'').substring(0, 800)) : '提取内容失败');
    } catch(e) { showBrowserResult('网络错误'); }
}

function showBrowserResult(text) {
    const area = document.getElementById('browserResultArea');
    const el = document.getElementById('browserResult');
    area.style.display = 'block';
    el.textContent = text;
    document.getElementById('browserScreenshotArea').style.display = 'none';
}

// Helper: HTML 转义
function escHtml(str) {
    return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ============================================================
//  📡 多渠道接入
// ============================================================
const CHANNEL_CONFIGS = {
    webhook: {
        hint: '通用 Webhook：配置接收端后，外部系统 POST JSON 到 Webhook 地址即可触发 AI。可选填 secret 用于签名验证。',
        fields: [
            {id: 'webhook_secret', label: '签名密钥（可选）', placeholder: '留空则不做签名验证'},
            {id: 'webhook_callback', label: '回调地址（可选）', placeholder: 'https://your-server.com/callback'}
        ]
    },
    feishu: {
        hint: '飞书机器人：群机器人直接填 Webhook URL，企业应用填 App ID/Secret。',
        fields: [
            {id: 'feishu_webhook', label: '群机器人 Webhook URL（推荐）', placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/xxx'},
            {id: 'feishu_app_id', label: 'App ID（企业应用）', placeholder: 'cli_xxxxxxxx'},
            {id: 'feishu_app_secret', label: 'App Secret（企业应用）', placeholder: 'xxxxxxxx'},
        ]
    },
    dingtalk: {
        hint: '钉钉群机器人：在群设置→机器人中添加自定义机器人，复制 Webhook URL 填入下方。',
        fields: [
            {id: 'dingtalk_webhook', label: '群机器人 Webhook URL', placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=...'},
        ]
    },
    telegram: {
        hint: 'Telegram Bot：通过 @BotFather 创建 Bot 获取 Token，然后调用 setWebhook 将 Bot 的更新推送到本系统。',
        fields: [
            {id: 'telegram_token', label: 'Bot Token', placeholder: '123456789:AAxxxxxxxx'},
        ]
    }
};

function updateChannelConfigHint() {
    const type = document.getElementById('newChannelType').value;
    const cfg = CHANNEL_CONFIGS[type] || {};
    const hint = document.getElementById('channelHintArea');
    const area = document.getElementById('channelConfigArea');
    hint.textContent = cfg.hint || '';
    area.innerHTML = (cfg.fields || []).map(f => `
        <div style="margin-bottom:8px;">
            <label style="font-size:12px;color:var(--text-secondary);">${f.label}</label>
            <input type="text" id="chfield_${f.id}" placeholder="${f.placeholder||''}" style="width:100%;box-sizing:border-box;">
        </div>`).join('');
}

async function addChannel() {
    const type = document.getElementById('newChannelType').value;
    const name = document.getElementById('newChannelName').value.trim();
    if (!name) { toast('请填写渠道名称'); return; }
    const cfg = CHANNEL_CONFIGS[type] || {};
    const config = { type, name };
    (cfg.fields || []).forEach(f => {
        const val = (document.getElementById('chfield_' + f.id) || {}).value || '';
        if (val) {
            const key = f.id.replace(type + '_', '');
            config[key] = val;
        }
    });
    try {
        const res = await fetch('/api/channels', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config)
        });
        const data = await res.json();
        if (data.success) {
            toast(`✅ 渠道已添加，ID: ${data.channel_id}`);
            document.getElementById('newChannelName').value = '';
            loadChannels();
        } else toast('❌ ' + (data.error||'添加失败'));
    } catch(e) { toast('网络错误'); }
}

async function loadChannels() {
    const el = document.getElementById('channelList');
    el.innerHTML = '<div class="plugin-empty">加载中...</div>';

    // 更新 Webhook 基础 URL
    const base = document.getElementById('webhookBaseUrl');
    if (base) base.textContent = location.origin;

    try {
        const res = await fetch('/api/channels');
        const data = await res.json();
        if (!data.channels || data.channels.length === 0) {
            el.innerHTML = '<div class="plugin-empty">暂无渠道，点击上方添加</div>';
            return;
        }
        el.innerHTML = data.channels.map(c => `
            <div class="plugin-item" style="padding:12px 14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-weight:600;font-size:14px;">${escHtml(c.name)}</div>
                        <div style="font-size:12px;color:var(--text-secondary);">${escHtml(c.type)} · ID: ${escHtml(c.id)}</div>
                        <div style="font-size:11px;color:#4a9eff;margin-top:2px;cursor:pointer;" onclick="navigator.clipboard.writeText(location.origin+'/api/webhook/${c.id}').then(()=>toast('已复制'))">
                            📋 ${location.origin}/api/webhook/${c.id}
                        </div>
                    </div>
                    <div style="display:flex;gap:6px;">
                        <button class="btn-secondary btn-sm" onclick="testChannel('${c.id}')">🧪 测试</button>
                        <button class="btn-secondary btn-sm" style="color:#e74c3c;" onclick="deleteChannel('${c.id}')">🗑</button>
                    </div>
                </div>
            </div>`).join('');
    } catch(e) { el.innerHTML = '<div class="plugin-empty" style="color:#e74c3c;">加载失败</div>'; }
}

async function deleteChannel(channelId) {
    if (!confirm('确认删除此渠道？')) return;
    const allowed = await requirePasswordIfNeeded('deleteChannel');
    if (!allowed) return;
    try {
        const res = await fetch(`/api/channels/${channelId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) { toast('已删除'); loadChannels(); }
        else toast('删除失败');
    } catch(e) { toast('网络错误'); }
}

async function testChannel(channelId) {
    toast('正在发送测试消息...');
    try {
        const res = await fetch('/api/channels/test', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ channel_id: channelId })
        });
        const data = await res.json();
        toast(data.success ? '✅ 测试消息已发送' : '❌ ' + (data.error||'发送失败'));
    } catch(e) { toast('网络错误'); }
}



// ===== Window 全局导出（由自检补全）=====
window.loadServerInfo = loadServerInfo;
window.startTunnel = startTunnel;
window.stopTunnel = stopTunnel;
window.copyTunnelUrl = copyTunnelUrl;
window.refreshVisitorStats = refreshVisitorStats;
window.toggleVisitorList = toggleVisitorList;
window.loadVisitorList = loadVisitorList;
window.visitorBlock = visitorBlock;
window.visitorUnblock = visitorUnblock;
window.loadSchedulerTasks = loadSchedulerTasks;
window.createSchedulerTask = createSchedulerTask;
window.deleteSchedulerTask = deleteSchedulerTask;
window.pauseSchedulerTask = pauseSchedulerTask;
window.fillSchedulerTemplate = fillSchedulerTemplate;
window.openCustomWorkflow = openCustomWorkflow;
window.loadWorkflows = loadWorkflows;
window.executeWorkflow = executeWorkflow;
window.runWorkflowTemplate = runWorkflowTemplate;
window.saveCustomWorkflow = saveCustomWorkflow;
window.deleteWorkflow = deleteWorkflow;
window.runSandboxCode = runSandboxCode;
window.fillSandboxExample = fillSandboxExample;
window.checkBrowserStatus = checkBrowserStatus;
window.installPlaywright = installPlaywright;
window.browserNavigate = browserNavigate;
window.browserScreenshot = browserScreenshot;
window.browserGetContent = browserGetContent;
window.browserClose = browserClose;
window.browserQuick = browserQuick;
window.showBrowserResult = showBrowserResult;
window.escHtml = escHtml;
window.updateChannelConfigHint = updateChannelConfigHint;
window.addChannel = addChannel;
window.loadChannels = loadChannels;
window.deleteChannel = deleteChannel;
window.testChannel = testChannel;
