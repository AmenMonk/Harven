// ===== Harven AI - 模型管理（拉取/删除/进度） =====

// 增强版：加载已安装模型列表（带删除按钮）
async function loadInstalledModelsEnhanced() {
    const cont = document.getElementById('installedModels');
    if (!cont) return;
    cont.innerHTML = '<div class="pull-loading">加载中...</div>';
    try {
        // ★ 2026-07-12: 用local-all API（包含Ollama+HF缓存+data/models）
        const r = await fetch('/api/models/local-all');
        const d = await r.json();
        const models = d.models || [];
        if (models.length) {
            cont.innerHTML = models.map(m => {
                const icon = m.icon || '📦';
                const source = m.source || '';
                const size = m.size_gb ? m.size_gb + 'GB' : '';
                const isOllama = m.id && m.id.startsWith('ollama:');
                const delBtn = isOllama ? `<button class="btn-sm btn-sm-danger" onclick="deleteModel('${m.name}')" title="删除模型">🗑️</button>` : '';
                return `<div class="model-item">
                    <span class="model-name" title="${m.id || m.name}">${icon} ${m.name} <span style="color:#888;font-size:11px;">${source} ${size}</span></span>
                    ${delBtn}
                </div>`;
            }).join('');
            // 兼容旧的 modelSelect
            const ms = document.getElementById('modelSelect');
            if (ms) {
                const cur = ms.value;
                ms.innerHTML = '';
                models.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.id || m.name;
                    opt.textContent = m.name;
                    if (m.name === cur) opt.selected = true;
                    ms.appendChild(opt);
                });
            }
        } else {
            cont.innerHTML = '<div class="pull-empty">未安装任何模型</div>';
        }
    } catch {
        cont.innerHTML = '<div class="pull-empty">本地引擎未连接</div>';
    }
}

// 拉取新模型
async function pullNewModel() {
    const input = document.getElementById('pullModelInput');
    const btn = document.getElementById('pullModelBtn');
    const progress = document.getElementById('pullProgress');
    const model = input ? input.value.trim() : '';

    if (!model) { toast('请输入模型名称，如 qwen3:14b'); return; }

    // 显示进度条
    if (progress) {
        progress.style.display = 'block';
        progress.innerHTML = `<div class="progress-bar"><div class="progress-fill" style="width:0%"></div></div><div class="progress-text">正在拉取 ${model}...</div>`;
    }
    btn.disabled = true;
    btn.textContent = '拉取中...';
    input.disabled = true;

    try {
        const r = await fetch('/api/ollama/pull', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        const data = await r.json();
        if (!data.ok) {
            if (progress) progress.style.display = 'none';
            btn.disabled = false;
            btn.textContent = '拉取';
            input.disabled = false;
            toast(data.error || '拉取失败');
            return;
        }

        // 轮询进度
        const checkInterval = setInterval(async () => {
            try {
                const sr = await fetch(`/api/ollama/pull-status?model=${encodeURIComponent(model)}`);
                const status = await sr.json();

                if (progress) {
                    const pct = status.progress || 0;
                    progress.innerHTML = `
                        <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
                        <div class="progress-text">${status.status === 'done' ? '✅ 完成' : status.status === 'error' ? '❌ 失败' : `拉取中 ${pct}%`} – ${model}</div>
                    `;
                }

                if (status.status === 'done') {
                    clearInterval(checkInterval);
                    btn.disabled = false;
                    btn.textContent = '拉取';
                    input.disabled = false;
                    input.value = '';
                    toast(`✅ ${model} 拉取完成！`);
                    // 刷新模型列表
                    setTimeout(loadInstalledModelsEnhanced, 1000);
                    if (progress) setTimeout(() => { progress.style.display = 'none'; }, 3000);
                } else if (status.status === 'error') {
                    clearInterval(checkInterval);
                    btn.disabled = false;
                    btn.textContent = '拉取';
                    input.disabled = false;
                    toast('❌ 拉取失败: ' + (status.error || '未知错误'));
                }
            } catch (e) {
                clearInterval(checkInterval);
                btn.disabled = false;
                btn.textContent = '拉取';
                input.disabled = false;
            }
        }, 2000);
    } catch (e) {
        if (progress) progress.style.display = 'none';
        btn.disabled = false;
        btn.textContent = '拉取';
        input.disabled = false;
        toast('拉取请求失败: ' + e.message);
    }
}

// 删除模型
async function deleteModel(modelName) {
    if (!confirm(`确认删除模型 ${modelName}？\n此操作不可恢复，需要重新拉取。`)) return;
    const allowed = await requirePasswordIfNeeded('deleteModel');
    if (!allowed) return;
    try {
        const r = await fetch(`/api/ollama/model/${encodeURIComponent(modelName)}`, { method: 'DELETE' });
        const data = await r.json();
        if (data.ok) {
            toast(`已删除: ${modelName}`);
            loadInstalledModelsEnhanced();
        } else {
            toast('删除失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('删除请求失败: ' + e.message);
    }
}

// ============================================================
// 模型市场 & 推荐（L1）
// ============================================================

let _marketModels = [];
let _marketFilter = { family: '', tag: '' };

/** 加载硬件信息并显示推荐 */
async function loadModelMarketRecommend() {
    const recDiv = document.getElementById('modelRecommend');
    if (!recDiv) return;
    recDiv.innerHTML = '<div class="pull-loading">检测硬件中...</div>';

    try {
        const r = await fetch('/api/hardware/info');
        const hw = await r.json();
        if (!hw.ok) throw new Error('检测失败');

        const gpu = hw.gpu || {};
        const vram = gpu.vram_gb || 0;
        const ram  = hw.ram_gb || 0;

        let html = `<div class="hw-info">`;
        html += `<span>🖥️ ${gpu.name || '未知GPU'}</span>`;
        html += `<span>⚡ ${vram}GB 显存</span>`;
        html += `<span>💾 ${ram}GB 内存</span>`;
        html += `</div>`;

        // 获取推荐
        const rr = await fetch('/api/models/recommend');
        const rec = await rr.json();
        if (rec.ok && rec.recommended?.length) {
            html += '<div class="rec-title">✅ 推荐安装</div>';
            html += '<div class="rec-list">';
            for (const m of rec.recommended) {
                const tags = (m.tags || []).map(t => `<span class="tag">${t}</span>`).join('');
                html += `<div class="rec-card">
                    <div class="rec-name">${m.name}</div>
                    <div class="rec-desc">${m.description || ''}</div>
                    <div class="rec-meta">${m.size} · 需 ${m.vram_gb}GB显存 ${tags}</div>
                    <button class="btn-primary btn-sm" onclick="marketPullModel('${m.name.replace(/'/g, "\\'")}')">安装</button>
                </div>`;
            }
            html += '</div>';
        }

        // 可安装列表
        if (rec.ok && rec.installable?.length) {
            html += '<div class="rec-title">📦 可安装</div>';
            html += '<div class="rec-list">';
            for (const m of rec.installable) {
                if (m.installed) continue;
                const statusIcon = m.status === 'tight' ? '⚠️ 显存紧张' : m.status === 'cpu_only' ? '🐢 CPU模式' : '✅';
                const tags = (m.tags || []).map(t => `<span class="tag">${t}</span>`).join('');
                html += `<div class="rec-card rec-${m.status}">
                    <div class="rec-name">${m.name} <span class="rec-status">${statusIcon}</span></div>
                    <div class="rec-desc">${m.description || ''}</div>
                    <div class="rec-meta">${m.size} · 需 ${m.vram_gb}GB显存 ${tags}</div>
                    <button class="btn-primary btn-sm" onclick="marketPullModel('${m.name.replace(/'/g, "\\'")}')">安装</button>
                </div>`;
            }
            html += '</div>';
        }

        recDiv.innerHTML = html;
    } catch (e) {
        recDiv.innerHTML = `<div class="pull-empty">硬件检测失败: ${e.message}</div>`;
    }
}

/** 加载模型市场列表（可用模型库） */
async function loadModelMarket(family = '', tag = '') {
    const marketDiv = document.getElementById('modelMarketList');
    if (!marketDiv) return;
    marketDiv.innerHTML = '<div class="pull-loading">加载模型列表中...</div>';

    try {
        let url = '/api/models/available';
        const params = [];
        if (family) params.push(`family=${encodeURIComponent(family)}`);
        if (tag)    params.push(`tag=${encodeURIComponent(tag)}`);
        if (params.length) url += '?' + params.join('&');

        const r  = await fetch(url);
        const js = await r.json();
        if (!js.ok) throw new Error(js.error || '请求失败');

        _marketModels = js.models || [];
        renderModelMarket();
    } catch (e) {
        marketDiv.innerHTML = `<div class="pull-empty">加载失败: ${e.message}</div>`;
    }
}

/** 渲染模型市场列表 */
function renderModelMarket() {
    const marketDiv = document.getElementById('modelMarketList');
    if (!marketDiv) return;

    let models = _marketModels;
    // 前端二次过滤（备用）
    if (_marketFilter.family) {
        models = models.filter(m => m.family === _marketFilter.family);
    }
    if (_marketFilter.tag) {
        models = models.filter(m => (m.tags || []).includes(_marketFilter.tag));
    }

    if (!models.length) {
        marketDiv.innerHTML = '<div class="pull-empty">暂无匹配的模型</div>';
        return;
    }

    // 家族筛选标签
    const families = [...new Set(_marketModels.map(m => m.family))];
    let html = '<div class="market-filters">';
    html += `<button class="filter-btn ${_marketFilter.family === '' ? 'active' : ''}" onclick="marketFilterFamily('')">全部</button>`;
    for (const f of families) {
        html += `<button class="filter-btn ${_marketFilter.family === f ? 'active' : ''}" onclick="marketFilterFamily('${f}')">${f}</button>`;
    }
    html += '</div>';

    html += '<div class="market-list">';
    for (const m of models) {
        const installed = m.installed;
        const tags = (m.tags || []).map(t => `<span class="tag">${t}</span>`).join('');
        html += `<div class="market-card ${installed ? 'installed' : ''}">
            <div class="market-card-top">
                <span class="market-name">${m.name}</span>
                ${installed ? '<span class="badge-installed">已安装</span>' : ''}
            </div>
            <div class="market-desc">${m.description || ''}</div>
            <div class="market-meta">${m.size} · 需 ${m.vram_gb}GB显存 ${tags}</div>
            <div class="market-actions">
                ${installed
                    ? '<span class="text-green">✅ 已安装</span>'
                    : `<button class="btn-primary btn-sm" onclick="marketPullModel('${m.name.replace(/'/g, "\\'")}')">安装</button>`
                }
            </div>
        </div>`;
    }
    html += '</div>';
    marketDiv.innerHTML = html;
}

function marketFilterFamily(family) {
    _marketFilter.family = family;
    renderModelMarket();
}

/** 从市场一键拉取模型（复用 pullNewModel 的进度逻辑） */
async function marketPullModel(modelName) {
    if (!modelName) return;
    if (!confirm(`确认安装模型 ${modelName}？\n将从 Ollama 仓库下载，可能需要较长时间。`)) return;

    const progressDiv = document.getElementById('pullProgress');
    // 填充 pullModelInput 并复用现有逻辑
    const input = document.getElementById('pullModelInput');
    if (input) input.value = modelName;

    // 直接调用后端拉取 API（复用 pullNewModel 的逻辑）
    if (typeof pullNewModel === 'function') {
        // 设置 input 值后调用
        pullNewModel();
        return;
    }

    // fallback: 直接实现
    await pullModelDirect(modelName, progressDiv);
}

/** 直接拉取（fallback） */
async function pullModelDirect(model, progressDiv) {
    if (progressDiv) {
        progressDiv.style.display = 'block';
        progressDiv.innerHTML = `<div class="progress-bar"><div class="progress-fill" style="width:0%"></div></div>
            <div class="progress-text">正在拉取 ${model}...</div>`;
    }

    try {
        const r = await fetch('/api/ollama/pull', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        const data = await r.json();
        if (!data.ok && data.error) {
            if (progressDiv) progressDiv.style.display = 'none';
            toast(data.error);
            return;
        }

        // 轮询进度
        const check = setInterval(async () => {
            try {
                const sr = await fetch(`/api/ollama/pull-status?model=${encodeURIComponent(model)}`);
                const status = await sr.json();
                const pct = status.progress || 0;
                if (progressDiv) {
                    progressDiv.innerHTML = `
                        <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
                        <div class="progress-text">${status.status === 'done' ? '✅ 完成' : status.status === 'error' ? '❌ 失败' : `拉取中 ${pct}% – ${model}`}</div>
                    `;
                }
                if (status.status === 'done') {
                    clearInterval(check);
                    toast(`✅ ${model} 安装完成！`);
                    setTimeout(() => { if (progressDiv) progressDiv.style.display = 'none'; }, 3000);
                    loadInstalledModelsEnhanced();
                    loadModelMarket();
                } else if (status.status === 'error') {
                    clearInterval(check);
                    toast('❌ 拉取失败: ' + (status.error || '未知错误'));
                    if (progressDiv) progressDiv.style.display = 'none';
                }
            } catch (e) {
                clearInterval(check);
            }
        }, 2000);
    } catch (e) {
        if (progressDiv) progressDiv.style.display = 'none';
        toast('拉取请求失败: ' + e.message);
    }
}

// 初始化：设置页面加载时自动触发
document.addEventListener('DOMContentLoaded', () => {
    // 如果模型市场 Tab 存在，延迟加载（等设置页渲染完）
    setTimeout(() => {
        const recDiv = document.getElementById('modelRecommend');
        if (recDiv) loadModelMarketRecommend();
        const marketDiv = document.getElementById('modelMarketList');
        if (marketDiv) loadModelMarket();
    }, 500);
});


// ===== Window 全局导出（由自检补全）=====
window.loadInstalledModelsEnhanced = loadInstalledModelsEnhanced;
window.pullNewModel = pullNewModel;
window.deleteModel = deleteModel;
window.loadModelMarketRecommend = loadModelMarketRecommend;
window.loadModelMarket = loadModelMarket;
window.renderModelMarket = renderModelMarket;
window.marketFilterFamily = marketFilterFamily;
window.marketPullModel = marketPullModel;
window.pullModelDirect = pullModelDirect;
