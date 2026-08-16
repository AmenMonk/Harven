/**
 * Harven AI — B站视频发布模块
 * app-bilibili.js
 */

// ========== 页面初始化 ==========

function initBilibiliTab() {
    biliLoadAuth();
    biliRefreshTasks();
    biliCheckCli();
}

// ========== 认证管理 ==========

async function biliSaveAuth() {
    const sessdata = document.getElementById('biliSESSDATA')?.value?.trim();
    const bili_jct = document.getElementById('biliJct')?.value?.trim();
    const dedeuserid = document.getElementById('biliUid')?.value?.trim();
    const buvid3 = document.getElementById('biliBuvid')?.value?.trim();
    const resultEl = document.getElementById('biliAuthResult');

    if (!sessdata || !bili_jct) {
        resultEl.innerHTML = '<span style="color:#f87171;">❌ SESSDATA 和 bili_jct 必填</span>';
        return;
    }

    resultEl.innerHTML = '<span style="color:#f59e0b;">⏳ 验证中...</span>';

    try {
        const resp = await fetch('/api/bilibili/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sessdata, bili_jct, dedeuserid, buvid3 })
        });
        const data = await resp.json();

        if (data.success) {
            resultEl.innerHTML = `<span style="color:#10b981;">✅ 验证成功！已登录: ${data.uname} (UID: ${data.uid})</span>`;
            biliLoadAuth();
        } else {
            resultEl.innerHTML = `<span style="color:#f87171;">❌ ${data.error}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<span style="color:#f87171;">❌ 请求失败: ${e.message}</span>`;
    }
}

async function biliLoadAuth() {
    const statusEl = document.getElementById('biliAuthStatus');
    try {
        const resp = await fetch('/api/bilibili/auth');
        const data = await resp.json();

        if (data.configured) {
            statusEl.innerHTML = `
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-size:24px;">✅</span>
                    <div>
                        <span style="color:#10b981;font-weight:600;">已认证</span>
                        <span style="color:#888;font-size:12px;margin-left:8px;">${data.uname || '未知用户'} · UID: ${data.uid || '-'}</span>
                        ${data.updated_at ? `<span style="color:#888;font-size:11px;margin-left:8px;">更新于: ${data.updated_at.slice(0,16)}</span>` : ''}
                    </div>
                </div>`;
        } else {
            statusEl.innerHTML = `
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="font-size:24px;">⚠️</span>
                    <div>
                        <span style="color:#f59e0b;font-weight:600;">未配置</span>
                        <span style="color:#888;font-size:12px;margin-left:8px;">请填写 Cookie 凭证并点击"保存并验证"</span>
                    </div>
                </div>`;
        }
    } catch (e) {
        statusEl.innerHTML = `<span style="color:#f87171;font-size:13px;">❌ 获取认证状态失败</span>`;
    }
}

// ========== 文件选择 ==========

function biliBrowseVideo() {
    document.getElementById('biliFileInput')?.click();
}

function biliFileSelected(event) {
    const file = event.target.files[0];
    if (file) {
        document.getElementById('biliFilePath').value = file.path || file.name;
        biliValidateVideo();
    }
}

function biliBrowseCover() {
    document.getElementById('biliCoverInput')?.click();
}

function biliCoverSelected(event) {
    const file = event.target.files[0];
    if (file) {
        document.getElementById('biliCoverPath').value = file.path || file.name;
    }
}

async function biliValidateVideo() {
    const filePath = document.getElementById('biliFilePath')?.value?.trim();
    const infoEl = document.getElementById('biliVideoInfo');
    if (!filePath) {
        infoEl.innerHTML = '<span style="color:#f87171;">请先选择视频文件</span>';
        return;
    }

    infoEl.innerHTML = '<span style="color:#f59e0b;">⏳ 检查中...</span>';

    try {
        const resp = await fetch('/api/bilibili/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_path: filePath })
        });
        const data = await resp.json();

        if (data.valid) {
            infoEl.innerHTML = `
                <div style="display:flex;gap:12px;flex-wrap:wrap;color:#10b981;">
                    <span>✅ 格式: ${data.format || '-'}</span>
                    <span>📏 ${data.width || '?'}×${data.height || '?'}</span>
                    <span>⏱️ ${data.duration || 0}秒</span>
                    <span>💾 ${data.file_size_mb || 0}MB</span>
                </div>`;
        } else {
            infoEl.innerHTML = `<span style="color:#f87171;">❌ ${data.error}</span>`;
        }
    } catch (e) {
        infoEl.innerHTML = `<span style="color:#f87171;">❌ 检查失败: ${e.message}</span>`;
    }
}

// ========== 元数据 ==========

function biliCopyrightChange() {
    const copyright = document.getElementById('biliCopyright')?.value;
    const sourceGroup = document.getElementById('biliSourceGroup');
    if (sourceGroup) {
        sourceGroup.style.display = copyright === '2' ? 'block' : 'none';
    }
}

// 标题字数计数
document.addEventListener('DOMContentLoaded', () => {
    const titleEl = document.getElementById('biliTitle');
    const descEl = document.getElementById('biliDesc');
    if (titleEl) {
        titleEl.addEventListener('input', () => {
            const count = titleEl.value.length;
            document.getElementById('biliTitleCount').textContent = `${count}/80`;
            document.getElementById('biliTitleCount').style.color = count > 80 ? '#f87171' : '#888';
        });
    }
    if (descEl) {
        descEl.addEventListener('input', () => {
            const count = descEl.value.length;
            document.getElementById('biliDescCount').textContent = `${count}/2000`;
        });
    }
});

// ========== 上传流程 ==========

async function biliStartUpload() {
    const filePath = document.getElementById('biliFilePath')?.value?.trim();
    const title = document.getElementById('biliTitle')?.value?.trim();
    const description = document.getElementById('biliDesc')?.value?.trim();
    const tid = parseInt(document.getElementById('biliCategory')?.value || 188);
    const copyright = parseInt(document.getElementById('biliCopyright')?.value || 2);
    const source = document.getElementById('biliSource')?.value?.trim() || '';
    const tagsStr = document.getElementById('biliTags')?.value?.trim() || '';
    const coverPath = document.getElementById('biliCoverPath')?.value?.trim() || '';

    if (!filePath) { alert('请选择视频文件'); return; }
    if (!title) { alert('请输入标题'); return; }
    if (title.length > 80) { alert('标题不能超过80字'); return; }

    const tags = tagsStr ? tagsStr.split(/[,，]/).map(t => t.trim()).filter(Boolean) : [];

    // 创建任务
    try {
        const resp = await fetch('/api/bilibili/upload', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                file_path: filePath,
                title, description, tags, tid, copyright, source, cover_path: coverPath,
            })
        });
        const data = await resp.json();
        if (!data.success) { alert('创建失败: ' + (data.error || '未知错误')); return; }

        const taskId = data.task_id;

        // 显示进度区域
        document.getElementById('biliProgressArea').style.display = 'block';
        document.getElementById('biliProgressText').textContent = '0%';
        document.getElementById('biliProgressStatus').textContent = '创建任务成功，开始上传...';
        document.getElementById('biliProgressFill').style.width = '0%';
        document.getElementById('biliResult').innerHTML = '';

        // 启动上传
        const startResp = await fetch(`/api/bilibili/upload/${taskId}/start`, { method: 'POST' });
        const startData = await startResp.json();
        if (!startData.success) {
            document.getElementById('biliResult').innerHTML = `<span style="color:#f87171;">❌ ${startData.error}</span>`;
            return;
        }

        // 轮询进度
        biliPollProgress(taskId);
    } catch (e) {
        document.getElementById('biliResult').innerHTML = `<span style="color:#f87171;">❌ 上传失败: ${e.message}</span>`;
    }
}

async function biliPollProgress(taskId) {
    const maxPolls = 300;  // 最多轮询5分钟
    let polls = 0;

    const interval = setInterval(async () => {
        polls++;
        try {
            const resp = await fetch(`/api/bilibili/upload/${taskId}`);
            const data = await resp.json();

            document.getElementById('biliProgressFill').style.width = `${data.progress || 0}%`;
            document.getElementById('biliProgressText').textContent = `${Math.round(data.progress || 0)}%`;
            document.getElementById('biliProgressStatus').textContent = data.error_msg || data.status || '';

            if (data.status === 'completed') {
                clearInterval(interval);
                document.getElementById('biliResult').innerHTML = `
                    <div style="color:#10b981;font-weight:600;">✅ 上传完成！</div>
                    <div style="font-size:12px;color:#888;margin-top:4px;">
                        BV号: <a href="https://www.bilibili.com/video/${data.bvid}" target="_blank" style="color:#6366f1;">${data.bvid}</a>
                        ${data.aid ? ` · AID: ${data.aid}` : ''}
                    </div>`;
                biliRefreshTasks();
            } else if (data.status === 'failed') {
                clearInterval(interval);
                document.getElementById('biliResult').innerHTML = `<span style="color:#f87171;">❌ 上传失败: ${data.error_msg}</span>`;
            } else if (data.status === 'cancelled') {
                clearInterval(interval);
                document.getElementById('biliResult').innerHTML = `<span style="color:#f59e0b;">⏹️ 已取消</span>`;
            }

            if (polls >= maxPolls) {
                clearInterval(interval);
                document.getElementById('biliResult').innerHTML = `<span style="color:#f59e0b;">⚠️ 轮询超时，请手动刷新查看状态</span>`;
            }
        } catch (e) {
            if (polls >= 10) {
                clearInterval(interval);
                document.getElementById('biliResult').innerHTML = `<span style="color:#f87171;">❌ 轮询失败: ${e.message}</span>`;
            }
        }
    }, 1000);
}

// ========== 任务列表与历史 ==========

async function biliRefreshTasks() {
    const listEl = document.getElementById('biliTaskList');
    try {
        const resp = await fetch('/api/bilibili/tasks');
        const data = await resp.json();
        const tasks = data.tasks || [];

        if (tasks.length === 0) {
            listEl.innerHTML = '<div style="text-align:center;color:#888;font-size:13px;padding:15px;">📭 暂无上传任务</div>';
            return;
        }

        const statusIcons = {
            pending: '⏳', preuploading: '📤', uploading: '📤', submitting: '📝',
            completed: '✅', failed: '❌', cancelled: '⏹️'
        };
        const statusColors = {
            pending: '#888', preuploading: '#f59e0b', uploading: '#6366f1', submitting: '#8b5cf6',
            completed: '#10b981', failed: '#f87171', cancelled: '#888'
        };

        let html = '';
        for (const t of tasks) {
            const icon = statusIcons[t.status] || '❓';
            const color = statusColors[t.status] || '#888';
            html += `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--border);font-size:12px;">
                <div style="flex:1;min-width:0;">
                    <span style="font-weight:500;">${escapeHtml(t.title || '未命名')}</span>
                    <span style="color:#888;margin-left:6px;">${t.file_name || ''} (${t.file_size_mb || 0}MB)</span>
                </div>
                <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
                    ${t.bvid ? `<a href="https://www.bilibili.com/video/${t.bvid}" target="_blank" style="color:#6366f1;font-size:11px;">${t.bvid}</a>` : ''}
                    <span style="color:${color};">${icon} ${t.status}</span>
                    <span style="color:#888;">${Math.round(t.progress || 0)}%</span>
                    ${t.status === 'pending' || t.status === 'uploading' ? `<button class="btn-secondary btn-sm" style="font-size:10px;padding:2px 6px;" onclick="biliCancelTask('${t.task_id}')">取消</button>` : ''}
                </div>
            </div>`;
        }
        listEl.innerHTML = html;
    } catch (e) {
        listEl.innerHTML = `<div style="color:#f87171;font-size:13px;">❌ 加载失败: ${e.message}</div>`;
    }
}

async function biliCancelTask(taskId) {
    if (!confirm('确认取消上传？')) return;
    try {
        await fetch(`/api/bilibili/upload/${taskId}`, { method: 'DELETE' });
        biliRefreshTasks();
    } catch (e) {
        alert('取消失败: ' + e.message);
    }
}

async function biliLoadHistory() {
    const listEl = document.getElementById('biliTaskList');
    try {
        const resp = await fetch('/api/bilibili/history');
        const data = await resp.json();
        const history = data.history || [];

        if (history.length === 0) {
            listEl.innerHTML = '<div style="text-align:center;color:#888;font-size:13px;padding:15px;">📭 暂无上传历史</div>';
            return;
        }

        let html = '<div style="font-weight:600;font-size:12px;margin-bottom:8px;color:#888;">📂 上传历史</div>';
        for (const h of history) {
            html += `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--border);font-size:12px;">
                <div style="flex:1;min-width:0;">
                    <span style="font-weight:500;">${escapeHtml(h.title || '未命名')}</span>
                    <span style="color:#888;margin-left:6px;">${h.file_size_mb || 0}MB</span>
                </div>
                <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
                    <a href="https://www.bilibili.com/video/${h.bvid}" target="_blank" style="color:#6366f1;">${h.bvid}</a>
                    <span style="color:#888;">${(h.completed_at || '').slice(0,16)}</span>
                </div>
            </div>`;
        }
        listEl.innerHTML = html;
    } catch (e) {
        listEl.innerHTML = `<div style="color:#f87171;font-size:13px;">❌ 加载失败: ${e.message}</div>`;
    }
}

// ========== biliup CLI 检查 ==========

async function biliCheckCli() {
    const el = document.getElementById('biliupCliStatus');
    try {
        const resp = await fetch('/api/bilibili/biliup-check');
        const data = await resp.json();
        if (data.installed) {
            el.innerHTML = `<span style="color:#10b981;">✅ biliup 已安装</span> · ${data.path || ''} · ${data.version || ''}`;
        } else {
            el.innerHTML = `<span style="color:#888;">⚠️ biliup 未安装 · <code style="font-size:11px;">pip install biliup</code> · 当前使用 HTTP API 模式</span>`;
        }
    } catch (e) {
        el.innerHTML = `<span style="color:#f87171;">检查失败</span>`;
    }
}

// ===== Window 全局导出（由自检脚本补全）=====
window.initBilibiliTab = initBilibiliTab;
window.biliSaveAuth = biliSaveAuth;
window.biliLoadAuth = biliLoadAuth;
window.biliBrowseVideo = biliBrowseVideo;
window.biliFileSelected = biliFileSelected;
window.biliBrowseCover = biliBrowseCover;
window.biliCoverSelected = biliCoverSelected;
window.biliValidateVideo = biliValidateVideo;
window.biliCopyrightChange = biliCopyrightChange;
window.biliStartUpload = biliStartUpload;
window.biliPollProgress = biliPollProgress;
window.biliRefreshTasks = biliRefreshTasks;
window.biliCancelTask = biliCancelTask;
window.biliLoadHistory = biliLoadHistory;
window.biliCheckCli = biliCheckCli;
