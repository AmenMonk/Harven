/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — Visitor & Share module ===== */
/* ★ 2026-07-14: IoT device management functions removed (no backend blueprint). */
/*    Only visitor/share (login status, access code, admin password) remains. */

// ===== 管理员密码 =====
async function changeAdminPassword() {
    const curPwd = (document.getElementById('adminCurrentPwd') || {}).value || '';
    const newPwd = (document.getElementById('adminNewPwd') || {}).value || '';
    const confirmPwd = (document.getElementById('adminConfirmPwd') || {}).value || '';
    const resultEl = document.getElementById('adminPwdResult');
    if (!curPwd) { if (resultEl) resultEl.innerHTML = '<span style="color:#f87171;">请输入当前密码</span>'; return; }
    if (!newPwd) { if (resultEl) resultEl.innerHTML = '<span style="color:#f87171;">请输入新密码</span>'; return; }
    if (newPwd.length < 6) { if (resultEl) resultEl.innerHTML = '<span style="color:#f87171;">新密码不能少于6位</span>'; return; }
    if (newPwd !== confirmPwd) { if (resultEl) resultEl.innerHTML = '<span style="color:#f87171;">两次密码不一致</span>'; return; }
    try {
        const res = await fetch('/api/admin/change-password', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ current_password: curPwd, value: newPwd, key: 'admin_password' })
        });
        const data = await res.json();
        if (data.success) {
            if (resultEl) resultEl.innerHTML = '<span style="color:#34d399;">✅ 密码已修改</span>';
            toast('✅ 管理员密码已更新');
            document.getElementById('adminCurrentPwd').value = '';
            document.getElementById('adminNewPwd').value = '';
            document.getElementById('adminConfirmPwd').value = '';
        } else {
            if (resultEl) resultEl.innerHTML = '<span style="color:#f87171;">❌ ' + (data.error||'修改失败') + '</span>';
        }
    } catch(e) { if (resultEl) resultEl.innerHTML = '<span style="color:#f87171;">网络错误</span>'; }
}

// ===== 登录状态 =====
async function loadLoginStatus() {
    try {
        const res = await fetch('/api/visitor/status');
        const data = await res.json();
        const statusEl = document.getElementById('loginStatus');
        const sourceEl = document.getElementById('loginSource');
        if (statusEl) {
            statusEl.innerHTML = data.is_local
                ? '<span style="color:#34d399;">✅ 已登录（本机/局域网免认证）</span>'
                : (data.authenticated
                    ? '<span style="color:#60a5fa;">✅ 已登录（公网访客）</span>'
                    : '<span style="color:#f87171;">⚠️ 未登录</span>');
        }
        if (sourceEl) sourceEl.textContent = data.source || (data.is_local ? '本地访问' : '公网访问');
    } catch(e) {}
}

// ===== 访问码管理 =====
async function setAccessCode() {
    const code = (document.getElementById('accessCodeInput') || {}).value || '';
    if (!code) { toast('请输入访问码'); return; }
    try {
        const res = await fetch('/api/visitor/set-access-code', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ access_code: code })
        });
        const data = await res.json();
        if (data.success) {
            toast('✅ 访问码已设置');
            loadAccessCodeStatus();
        } else {
            toast('❌ ' + (data.error||'设置失败'));
        }
    } catch(e) { toast('网络错误'); }
}

async function clearAccessCode() {
    if (!confirm('确认清除访问码？清除后公网访问无需密码。')) return;
    const allowed = await requirePasswordIfNeeded('clearAccessCode');
    if (!allowed) return;
    try {
        const res = await fetch('/api/visitor/set-access-code', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ access_code: '' })
        });
        const data = await res.json();
        if (data.success) {
            toast('✅ 访问码已清除');
            const el = document.getElementById('accessCodeInput');
            if (el) el.value = '';
            loadAccessCodeStatus();
        } else {
            toast('❌ ' + (data.error||'清除失败'));
        }
    } catch(e) { toast('网络错误'); }
}

async function loadAccessCodeStatus() {
    try {
        const res = await fetch('/api/visitor/status');
        const data = await res.json();
        const el1 = document.getElementById('accessCodeStatus');
        const el2 = document.getElementById('accessCodeStatus2');
        const msg = data.protected
            ? '<span style="color:#34d399;">🔒 已启用保护</span>'
            : '<span style="color:#f87171;">⚠️ 未启用保护</span>';
        if (el1) el1.innerHTML = msg;
        if (el2) el2.innerHTML = msg + (data.protected ? ' — 访客需输入密码' : ' — 任何人可直接访问');
    } catch(e) {}
}

// ===== Window 全局导出 =====
window.changeAdminPassword = changeAdminPassword;
window.loadLoginStatus = loadLoginStatus;
window.setAccessCode = setAccessCode;
window.clearAccessCode = clearAccessCode;
window.loadAccessCodeStatus = loadAccessCodeStatus;
