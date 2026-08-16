/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — HARDWARE module ===== */

// ── 硬件自检状态 ──
let hardwareSpec = null;
let hardwareRecommended = null;
let hardwareBestMatch = null;

// ── 加载硬件报告 ──
async function loadHardwareReport() {
    const el = document.getElementById('hardwareReport');
    const btn = document.getElementById('btnHardwareDetect');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '检测中...';
    }
    if (el) el.innerHTML = '<p style="color:var(--text-secondary); font-size:13px;">正在检测硬件配置，请稍候...</p>';

    try {
        const r = await fetch('/api/hardware/detect');
        if (!r.ok) throw new Error(await r.text());
        hardwareSpec = await r.json();

        const r2 = await fetch('/api/hardware/recommend');
        if (!r2.ok) throw new Error(await r2.text());
        const rec = await r2.json();
        hardwareRecommended = rec.recommended;
        hardwareBestMatch = rec.best_match;
        hardwareCompatible = rec.compatible;

        renderHardwareReport();
    } catch (e) {
        console.error('[Hardware] 检测失败:', e);
        if (el) el.innerHTML = `<p style="color:#ff6b6b; font-size:13px;">检测失败：${escHtml(e.message)}</p>`;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '重新检测';
        }
    }
}

// ── 渲染硬件报告 ──
function renderHardwareReport() {
    const el = document.getElementById('hardwareReport');
    if (!el || !hardwareSpec) return;

    const s = hardwareSpec;
    let html = '';

    // ── 本机配置 ──
    html += `<div style="margin-bottom:16px;">
        <div style="font-weight:600; color:var(--text-primary); margin-bottom:8px; font-size:14px;">🖥️ 本机配置</div>
        <div style="font-size:13px; color:var(--text-secondary); line-height:1.8;">
            <div>CPU：${escHtml(s.cpu_name || 'Unknown')}（${s.cpu_cores}C/${s.cpu_threads}T）</div>
            <div>内存：${s.ram_total_gb} GB 总计 / ${s.ram_available_gb} GB 可用</div>`;

    if (s.has_dedicated_gpu) {
        html += `        <div>GPU：${escHtml(s.gpu_name)}（${s.gpu_vram_gb} GB VRAM）</div>`;
    } else {
        html += `        <div>GPU：无独立显卡（将使用 CPU 推理，速度较慢）</div>`;
    }

    html += `        <div>系统：${escHtml(s.os_name)}（${escHtml(s.os_arch)}）</div>
        <div>本地引擎：${s.ollama_installed ? '✅ 已安装（' + (s.ollama_models || []).join('、') + '）' : '❌ 未安装（请先安装本地引擎）'}</div>`;
    html += `    </div>\n</div>`;

    // ── 推荐模型 ──
    if (hardwareRecommended && hardwareRecommended.length) {
        html += `<div style="margin-bottom:16px;">
            <div style="font-weight:600; color:var(--text-primary); margin-bottom:8px; font-size:14px;">✅ 推荐模型（最适合你的配置）</div>`;
        hardwareRecommended.forEach((m, i) => {
            const isBest = hardwareBestMatch && m.model_id === hardwareBestMatch.model_id;
            html += `<div style="background:var(--bg-secondary); border-radius:8px; padding:12px; margin-bottom:8px; border: ${isBest ? '2px solid #4a9eff' : '1px solid var(--border-color)'};">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <span style="font-weight:600; color:var(--text-primary);">${escHtml(m.name)}</span>
                    <span style="font-size:12px; color:var(--accent-color);">${m.size_mb} MB</span>
                </div>
                <div style="font-size:12px; color:var(--text-secondary); line-height:1.6;">
                    <div>用途：${escHtml(m.use_case)}</div>
                    <div>质量：${m.quality}　速度（GPU）：${m.speed_gpu}　（CPU）：${m.speed_cpu}</div>
                    <div>最低要求：RAM ${m.min_ram_gb} GB / 显存 ${m.min_vram_gb} GB</div>
                </div>
                <div style="margin-top:8px; display:flex; gap:8px;">
                    <button class="btn btn-sm" onclick="downloadModel('${m.model_id}')">下载此模型</button>
                    ${isBest ? '<button class="btn btn-sm btn-primary" onclick="setDefaultModel(\'' + m.model_id + '\')">设为默认</button>' : ''}
                </div>
            </div>`;
        });
        html += `</div>`;
    }

    // ── 所有兼容模型 ──
    if (hardwareCompatible && hardwareCompatible.length > 1) {
        html += `<div style="margin-bottom:16px;">
            <div style="font-weight:600; color:var(--text-primary); margin-bottom:8px; font-size:14px;">📋 所有兼容模型</div>
            <div style="font-size:12px; color:var(--text-secondary); line-height:2;">`;
        hardwareCompatible.forEach(m => {
            const isInstalled = s.ollama_models && s.ollama_models.includes(m.model_id);
            const isBest = hardwareBestMatch && m.model_id === hardwareBestMatch.model_id;
            html += `<div style="display:flex; justify-content:space-between; align-items:center; padding:4px 0; border-bottom:1px solid var(--border-color);">
                <span>${isBest ? '✅ ' : ''}${escHtml(m.name)}（${m.size_mb} MB）</span>
                <span>${isInstalled ? '<span style="color:#4caf50;">已安装</span>' : '<span style="color:var(--text-secondary);">未安装</span>'}</span>
            </div>`;
        });
        html += `    </div>\n</div>`;
    }

    // ── 无兼容模型提示 ──
    if (!hardwareRecommended || !hardwareRecommended.length) {
        html += `<div style="background:#2a2a3e; border-radius:8px; padding:12px; margin-bottom:12px;">
            <div style="color:#ff9800; font-weight:600; margin-bottom:6px;">⚠️ 未找到兼容的本地模型</div>
            <div style="font-size:12px; color:var(--text-secondary); line-height:1.6;">
                你的电脑配置较低，建议使用云端 API 模式（DeepSeek / Claude 等）。<br>
                或者尝试安装 <b>qwen2.5:3b</b>（最低配置，2GB 下载）。
            </div>
            <button class="btn btn-sm" style="margin-top:8px;" onclick="downloadModel('qwen2.5:3b')">下载 qwen2.5:3b（保底模型）</button>
        </div>`;
    }

    el.innerHTML = html;
}

// ── 下载模型 ──
let hardwarePullAbort = null;

async function downloadModel(modelId) {
    if (hardwarePullAbort) {
        toast('已有模型正在下载，请等待完成');
        return;
    }

    const el = document.getElementById('hardwareReport');
    const progressEl = document.getElementById('hardwareProgress');
    if (progressEl) {
        progressEl.style.display = 'block';
        progressEl.innerHTML = `<p style="color:var(--accent-color); font-size:13px;">开始下载 ${escHtml(modelId)} ...</p>`;
    }

    try {
        const r = await fetch('/api/hardware/pull-model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_id: modelId }),
        });

        if (!r.ok) throw new Error(await r.text());

        const reader = r.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    if (data.progress) {
                        if (progressEl) {
                            progressEl.innerHTML = `<p style="color:var(--accent-color); font-size:13px;">${escHtml(data.progress)}</p>`;
                        }
                    }
                    if (data.done) {
                        if (data.success) {
                            toast(`✅ ${modelId} 下载完成！`);
                            if (progressEl) progressEl.style.display = 'none';
                            loadHardwareReport();  // 刷新
                        } else {
                            toast(`❌ 下载失败（退出码 ${data.code}）`);
                        }
                        hardwarePullAbort = null;
                        return;
                    }
                    if (data.error) {
                        toast(`❌ ${data.error}`);
                        hardwarePullAbort = null;
                        return;
                    }
                } catch (e) { /* ignore JSON parse errors */ }
            }
        }
    } catch (e) {
        console.error('[Hardware] 下载失败:', e);
        toast('下载失败：' + e.message);
        if (progressEl) progressEl.style.display = 'none';
        hardwarePullAbort = null;
    }
}

// ── 设为默认模型 ──
async function setDefaultModel(modelId) {
    try {
        const r = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ default_model: modelId })
        });
        const data = await r.json();
        if (data.success) {
            toast(`✅ 已设为默认模型：${modelId}`);
        } else {
            toast('设置失败：' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('设置失败：' + e.message);
    }
}

// ── 启动时的硬件自检（首次使用时显示） ──
async function autoHardwareCheck() {
    // 检查是否已经做过自检（存在 localStorage 标记）
    const checked = localStorage.getItem('harven_hardware_checked');
    if (checked === 'true') return;

    try {
        const r = await fetch('/api/hardware/detect');
        if (!r.ok) return;
        const spec = await r.json();

        // 如果没有兼容模型，或者 本地引擎未安装，弹出提示
        const r2 = await fetch('/api/hardware/recommend');
        if (!r2.ok) return;
        const rec = await r2.json();

        if (!spec.ollama_installed || !rec.best_match) {
            // 显示硬件报告抽屉
            const drawer = document.getElementById('settingsDrawer');
            if (drawer) {
                drawer.classList.add('open');
                // 切换到硬件标签页
                setTimeout(() => {
                    const tab = document.querySelector('[data-tab="hardware"]');
                    if (tab) tab.click();
                    else loadHardwareReport();
                }, 300);
            }
            localStorage.setItem('harven_hardware_checked', 'true');
        } else {
            localStorage.setItem('harven_hardware_checked', 'true');
        }
    } catch (e) {
        console.error('[Hardware] 自动检查失败:', e);
    }
}

// ===== Window 全局导出（由自检脚本补全）=====
window.loadHardwareReport = loadHardwareReport;
window.renderHardwareReport = renderHardwareReport;
window.downloadModel = downloadModel;
window.setDefaultModel = setDefaultModel;
window.autoHardwareCheck = autoHardwareCheck;
