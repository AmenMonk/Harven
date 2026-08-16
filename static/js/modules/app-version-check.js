// ===== Harven AI - 版本检测 & 在线更新 =====

let _updateCheckResult = null;

// 启动时自动检查更新（3秒后静默执行）
function initVersionCheck() {
    setTimeout(checkVersionSilent, 3000);
}

// 静默版本检查（启动时）
async function checkVersionSilent() {
    try {
        const r = await fetch('/api/version/check', { signal: AbortSignal.timeout(6000) });
        const data = await r.json();
        _updateCheckResult = data;

        if (data.has_update) {
            // 在设置按钮上显示红点
            const settingsBtn = document.getElementById('settingsBtn');
            if (settingsBtn) {
                let badge = settingsBtn.querySelector('.update-badge');
                if (!badge) {
                    badge = document.createElement('span');
                    badge.className = 'update-badge';
                    badge.textContent = '1';
                    settingsBtn.appendChild(badge);
                }
                settingsBtn.title = `有新版本: ${data.latest}`;
            }
        }

        // 更新关于页面的版本信息
        updateAboutVersion(data);
    } catch (e) {
        // 静默失败
    }
}

// 手动检查更新（设置页按钮触发）
async function checkVersionManual() {
    const btn = document.getElementById('checkUpdateBtn');
    const status = document.getElementById('updateStatus');
    if (btn) { btn.disabled = true; btn.textContent = '检测中...'; }
    if (status) { status.innerHTML = '<span style="color:#888">⏳ 正在检测...</span>'; }

    try {
        const r = await fetch('/api/version/check', { signal: AbortSignal.timeout(10000) });
        const data = await r.json();
        _updateCheckResult = data;

        if (btn) { btn.disabled = false; btn.textContent = '🔄 检查更新'; }

        renderVersionResult(data);
        updateAboutVersion(data);
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = '🔄 检查更新'; }
        if (status) { status.innerHTML = '<span style="color:#ef4444">❌ 检查失败: ' + e.message + '</span>'; }
    }
}

// 渲染版本检查结果
function renderVersionResult(data) {
    const status = document.getElementById('updateStatus');
    if (!status) return;

    if (data.has_update) {
        status.innerHTML = `
            <div class="update-available">
                <div class="update-header">🎉 发现新版本</div>
                <div class="update-versions">
                    <span class="ver-old">${data.current}</span> → <span class="ver-new">${data.latest}</span>
                </div>
                ${data.changelog ? `<div class="update-changelog"><pre>${data.changelog}</pre></div>` : ''}
                <button class="btn-primary" onclick="startUpdate('${data.url || ''}')" id="startUpdateBtn">
                    ⬇️ 立即更新
                </button>
            </div>
        `;
    } else if (data.error) {
        status.innerHTML = `<span style="color:#f59e0b">⚠️ ${data.error}</span>`;
    } else {
        status.innerHTML = '<span style="color:#22c55e">✅ 已是最新版本 (v' + data.current + ')</span>';
    }
}

// 开始更新下载
async function startUpdate(url) {
    if (!url) {
        toast('更新地址为空，请稍后重试');
        return;
    }

    const status = document.getElementById('updateStatus');
    const btn = document.getElementById('startUpdateBtn');
    if (btn) { btn.disabled = true; btn.textContent = '下载中...'; }

    if (status) {
        status.innerHTML = `
            <div class="update-progress-container">
                <div class="progress-bar"><div class="progress-fill" id="updateProgressFill" style="width:0%"></div></div>
                <div class="progress-text" id="updateProgressText">开始下载...</div>
            </div>
        `;
    }

    try {
        const r = await fetch('/api/version/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        const data = await r.json();
        if (!data.ok) {
            if (status) status.innerHTML = '<span style="color:#ef4444">❌ 下载启动失败</span>';
            return;
        }

        // 轮询进度
        const fill = document.getElementById('updateProgressFill');
        const text = document.getElementById('updateProgressText');
        const checkInterval = setInterval(async () => {
            try {
                const sr = await fetch('/api/version/update-progress');
                const progress = await sr.json();

                if (fill) fill.style.width = (progress.progress || 0) + '%';
                if (text) {
                    if (progress.status === 'done') {
                        text.textContent = '✅ 更新完成！请重启 Harven AI';
                        clearInterval(checkInterval);
                    } else if (progress.status === 'error') {
                        text.textContent = '❌ 更新失败: ' + (progress.error || '未知错误');
                        clearInterval(checkInterval);
                    } else if (progress.status === 'extracting') {
                        text.textContent = '📦 正在解压安装... ' + (progress.progress || 0) + '%';
                    } else {
                        text.textContent = '📥 下载中... ' + (progress.progress || 0) + '%';
                    }
                }

                if (progress.status === 'done' || progress.status === 'error') {
                    clearInterval(checkInterval);
                    if (btn) { btn.disabled = false; btn.textContent = '🔄 重新检查'; }
                }
            } catch (e) {
                clearInterval(checkInterval);
            }
        }, 1500);
    } catch (e) {
        if (status) status.innerHTML = '<span style="color:#ef4444">❌ 下载失败: ' + e.message + '</span>';
        if (btn) { btn.disabled = false; btn.textContent = '⬇️ 立即更新'; }
    }
}

// 更新关于页面的版本信息
function updateAboutVersion(data) {
    const el = document.getElementById('aboutVersion');
    if (!el) return;
    el.textContent = 'v' + (data.current || '0.1.0');
    if (data.has_update) {
        el.innerHTML += ` <span style="color:#f59e0b;font-size:12px">(有新版本 v${data.latest})</span>`;
    }
}

// ===== Window 全局导出（由自检脚本补全）=====
window.initVersionCheck = initVersionCheck;
window.checkVersionSilent = checkVersionSilent;
window.checkVersionManual = checkVersionManual;
window.renderVersionResult = renderVersionResult;
window.startUpdate = startUpdate;
window.updateAboutVersion = updateAboutVersion;
