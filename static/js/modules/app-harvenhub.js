/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/**
 * HarvenHub — 知识打包与发布模块
 */

// ========== 全局状态 ==========
let hhDataSources = null;
let hhSelectedItems = new Map(); // key: "type:id" -> true
let hhAnalysisResults = {};
let hhIsAnalyzing = false;

// ========== Modal 控制 ==========

async function openHarvenHub() {
    const modal = document.getElementById('harvenhubModal');
    modal.style.display = 'flex';
    modal.classList.add('modal--open');

    // 默认打开社区市场 Tab，自动加载数据
    hhSwitchTab('market');
}

function closeHarvenHub() {
    const modal = document.getElementById('harvenhubModal');
    modal.classList.remove('modal--open');
    setTimeout(() => { modal.style.display = 'none'; }, 200);
}

// ========== 标签切换 ==========

function hhSwitchTab(tabId) {
    document.querySelectorAll('.hh-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.hh-tab-panel').forEach(p => p.classList.remove('active'));

    document.querySelector(`.hh-tab[data-tab="${tabId}"]`)?.classList.add('active');
    document.getElementById(`hhTab_${tabId}`)?.classList.add('active');

    if (tabId === 'market') {
        loadHarvenHubMarket();
    } else if (tabId === 'cloudhub') {
        hubCloudLoad();
    } else if (tabId === 'history') {
        loadHarvenHubHistory();
    }
}

// ========== 加载数据源 ==========

async function loadHarvenHubSources() {
    const body = document.getElementById('hhSourceBody');
    body.innerHTML = '<div class="hh-loading">⏳ 正在扫描数据源...</div>';

    try {
        const resp = await fetch('/api/harvenhub/sources');
        hhDataSources = await resp.json();
        hhSelectedItems.clear();
        hhAnalysisResults = {};

        renderSources();
    } catch (e) {
        body.innerHTML = `<div class="hh-error">❌ 加载失败: ${e.message}</div>`;
    }
}

function renderSources() {
    const body = document.getElementById('hhSourceBody');
    if (!hhDataSources) return;

    let html = '';

    const sections = [
        { key: 'conversations', icon: '💬', label: '对话记录' },
        { key: 'knowledge_base', icon: '📚', label: '知识库文件' },
        { key: 'memories', icon: '🧠', label: '认知记忆' },
        { key: 'specializations', icon: '🎯', label: '专项培养' },
    ];

    for (const sec of sections) {
        const items = hhDataSources[sec.key] || [];
        if (items.length === 0) continue;

        html += `<div class="hh-section-title">${sec.icon} ${sec.label} <span class="hh-count">(${items.length})</span></div>`;

        for (const item of items) {
            const key = `${sec.key}:${item.id}`;
            const checked = hhSelectedItems.has(key) ? 'checked' : '';
            const riskBadge = getRiskBadge(item.privacy_risk);

            let info = '';
            if (sec.key === 'conversations') {
                info = `<span class="hh-item-meta">${item.msg_count || 0} 条消息 · ${item.category || '未分类'}</span>`;
            } else if (sec.key === 'knowledge_base') {
                info = `<span class="hh-item-meta">${item.size_kb} KB · ${item.ext}</span>`;
            } else if (sec.key === 'memories') {
                info = `<span class="hh-item-meta">${item.source} · ${item.value_preview.substring(0, 60)}</span>`;
            } else if (sec.key === 'specializations') {
                info = `<span class="hh-item-meta">${item.description || '无描述'}</span>`;
            }

            let title = item.title || item.name || item.key || item.id;
            if (title.length > 60) title = title.substring(0, 57) + '...';

            html += `
            <label class="hh-item">
                <input type="checkbox" class="hh-checkbox" data-key="${key}"
                       ${checked} onchange="hhToggleItem('${key}', this.checked)">
                <div class="hh-item-info">
                    <div class="hh-item-title">${escapeHtml(title)}</div>
                    ${info}
                </div>
                ${riskBadge}
            </label>`;
        }
    }

    if (!html) {
        html = '<div class="hh-empty">📭 暂无可打包的数据</div>';
    }

    body.innerHTML = html;
}

function getRiskBadge(risk) {
    if (risk === 'high') return '<span class="hh-risk hh-risk-high">🔴 高风险</span>';
    if (risk === 'medium') return '<span class="hh-risk hh-risk-medium">🟡 中风险</span>';
    if (risk === 'low') return '<span class="hh-risk hh-risk-low">🟢 低风险</span>';
    if (risk === 'safe') return '<span class="hh-risk hh-risk-safe">✅ 安全</span>';
    return '<span class="hh-risk hh-risk-unknown">❓ 待检测</span>';
}

function hhToggleItem(key, checked) {
    if (checked) {
        hhSelectedItems.set(key, true);
    } else {
        hhSelectedItems.delete(key);
    }
    updateSelectedCount();
}

function hhSelectAll(sourceType) {
    if (!hhDataSources) return;
    const items = hhDataSources[sourceType] || [];
    const allSelected = items.every(item => hhSelectedItems.has(`${sourceType}:${item.id}`));

    items.forEach(item => {
        const key = `${sourceType}:${item.id}`;
        if (allSelected) {
            hhSelectedItems.delete(key);
        } else {
            hhSelectedItems.set(key, true);
        }
    });

    // 更新 checkbox UI
    document.querySelectorAll(`.hh-checkbox[data-key^="${sourceType}:"]`).forEach(cb => {
        cb.checked = !allSelected;
    });
    updateSelectedCount();
}

function updateSelectedCount() {
    const count = hhSelectedItems.size;
    const el = document.getElementById('hhSelectedCount');
    if (el) el.textContent = `已选 ${count} 项`;
}

function hhClearSelection() {
    if (hhSelectedItems.size === 0) return;
    hhSelectedItems.clear();
    // 取消所有 checkbox
    document.querySelectorAll('.hh-checkbox').forEach(cb => { cb.checked = false; });
    updateSelectedCount();
}

// ========== AI 隐私分析 ==========

async function hhRunAnalysis() {
    if (hhSelectedItems.size === 0) {
        alert('请先选择要分析的数据项');
        return;
    }
    if (hhIsAnalyzing) return;

    hhIsAnalyzing = true;
    const btn = document.getElementById('hhAnalyzeBtn');
    const status = document.getElementById('hhAnalyzeStatus');

    btn.textContent = '⏳ 分析中...';
    btn.disabled = true;
    status.innerHTML = '<span class="hh-status-text">正在逐个分析选中项...</span>';
    status.style.display = 'block';

    const items = [];
    for (const [key] of hhSelectedItems) {
        const [type, id] = key.split(':', 2);
        items.push({ type, id });
    }

    try {
        const resp = await fetch('/api/harvenhub/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items }),
        });

        const results = await resp.json();
        hhAnalysisResults = {};

        let highCount = 0, mediumCount = 0, lowCount = 0, safeCount = 0;

        for (const r of results) {
            const key = `${r.type}:${r.id}`;
            hhAnalysisResults[key] = r;

            if (r.risk_level === 'high') highCount++;
            else if (r.risk_level === 'medium') mediumCount++;
            else if (r.risk_level === 'low') lowCount++;
            else safeCount++;

            // 更新数据源中的风险标记
            const items = hhDataSources[r.type] || [];
            const found = items.find(i => i.id === r.id);
            if (found) found.privacy_risk = r.risk_level;
        }

        // 重新渲染以显示风险徽章
        renderSources();

        status.innerHTML = `
            <div class="hh-analysis-summary">
                🔍 分析完成：
                <span class="hh-risk hh-risk-high" style="margin-left:4px">🔴 ${highCount} 高风险</span>
                <span class="hh-risk hh-risk-medium">🟡 ${mediumCount} 中风险</span>
                <span class="hh-risk hh-risk-low">🟢 ${lowCount} 低风险</span>
                <span class="hh-risk hh-risk-safe">✅ ${safeCount} 安全</span>
            </div>
            <div style="font-size:11px;color:#888;margin-top:4px">
                💡 高风险项将在导出时自动过滤（可关闭"排除隐私"选项）
            </div>`;

        // 切换到导出标签
        hhSwitchTab('export');

    } catch (e) {
        status.innerHTML = `<span style="color:#f87171">❌ 分析失败: ${e.message}</span>`;
    } finally {
        hhIsAnalyzing = false;
        btn.textContent = '🔍 AI 隐私分析';
        btn.disabled = false;
    }
}

// ========== 导出打包 ==========

async function hhDoExport() {
    if (hhSelectedItems.size === 0) {
        alert('请先在"选择数据"标签页中选择要打包的数据项');
        return;
    }

    const name = document.getElementById('hhPackageName').value.trim();
    if (!name) {
        alert('请输入知识包名称');
        return;
    }

    const description = document.getElementById('hhPackageDesc').value.trim();
    const excludePrivacy = document.getElementById('hhExcludePrivacy').checked;

    const items = [];
    for (const [key] of hhSelectedItems) {
        const [type, id] = key.split(':', 2);
        items.push({ type, id });
    }

    const btn = document.getElementById('hhExportBtn');
    const status = document.getElementById('hhExportStatus');
    btn.textContent = '⏳ 打包中...';
    btn.disabled = true;
    status.innerHTML = '';
    status.style.display = 'block';

    try {
        const resp = await fetch('/api/harvenhub/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description, items, exclude_privacy: excludePrivacy }),
        });

        const result = await resp.json();

        if (result.success) {
            status.innerHTML = `
                <div class="hh-export-success">
                    <div class="hh-success-icon">✅</div>
                    <div class="hh-success-info">
                        <strong>导出成功！</strong>
                        <div>文件: <code>${result.file}</code> (${result.size_kb} KB)</div>
                        <div class="hh-stats">
                            已包含 ${result.stats.included} 项
                            ${result.stats.skipped > 0 ? ` · 已过滤 ${result.stats.skipped} 项` : ''}
                            ${result.stats.empty > 0 ? ` · ${result.stats.empty} 项为空` : ''}
                        </div>
                        <div style="margin-top:8px;font-size:12px;color:#888">
                            💡 知识包已保存到: <br><code>${result.path}</code>
                        </div>
                        <button class="btn btn-primary" style="margin-top:10px" 
                                onclick="window.open('${result.path}', '_blank')">
                            📂 打开文件位置
                        </button>
                    </div>
                </div>`;
        } else {
            status.innerHTML = `<span style="color:#f87171">❌ 导出失败: ${result.error || '未知错误'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span style="color:#f87171">❌ 导出失败: ${e.message}</span>`;
    } finally {
        btn.textContent = '📦 开始打包';
        btn.disabled = false;
    }
}

// ========== 导出历史 ==========

async function loadHarvenHubHistory() {
    const body = document.getElementById('hhHistoryBody');
    body.innerHTML = '<div class="hh-loading">⏳ 加载中...</div>';

    try {
        const resp = await fetch('/api/harvenhub/history');
        const history = await resp.json();

        if (!history || history.length === 0) {
            body.innerHTML = '<div class="hh-empty">📭 暂无导出记录</div>';
            return;
        }

        body.innerHTML = history.map(h => `
            <div class="hh-history-item">
                <div class="hh-history-name">📦 ${escapeHtml(h.name)}</div>
                <div class="hh-history-meta">${h.size_kb} KB · ${h.exported_at}</div>
            </div>
        `).join('');

    } catch (e) {
        body.innerHTML = `<div class="hh-error">❌ 加载失败: ${e.message}</div>`;
    }
}

// ========== 工具函数 ==========

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ========== 社区市场 ==========

let hhMarketData = { items: [], total: 0 };
let hhMarketCategory = '';
let hhMarketShowPublish = false;

async function loadHarvenHubMarket() {
    const statsEl = document.getElementById('hhMarketStats');
    const catEl = document.getElementById('hhMarketCategories');

    try {
        const resp = await fetch('/api/harvenhub-market/stats');
        const stats = await resp.json();

        // 统计条
        const typeLabels = { skill: '⚡ Skill', agent: '🤖 Agent', knowledge: '📚 知识包', bundle: '📦 Bundle', template: '📋 模板', model_link: '🧠 模型/工具', tool: '🔧 工具' };
        let typeStr = '';
        if (stats.by_type) {
            typeStr = Object.entries(stats.by_type)
                .map(([k, v]) => `<span style="margin:0 4px">${typeLabels[k] || k}: <b>${v}</b></span>`)
                .join('');
        }

        statsEl.innerHTML = `
            <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:10px 14px;flex:1;min-width:120px;text-align:center;">
                <div style="font-size:20px;font-weight:700;color:#6366f1;">${stats.total_items || 0}</div>
                <div style="font-size:11px;color:#888;">已发布项目</div>
            </div>
            <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:10px 14px;flex:1;min-width:120px;text-align:center;">
                <div style="font-size:20px;font-weight:700;color:#f59e0b;">${stats.total_downloads || 0}</div>
                <div style="font-size:11px;color:#888;">总下载</div>
            </div>
            <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:10px 14px;flex:2;min-width:200px;text-align:center;display:flex;align-items:center;justify-content:center;gap:4px;flex-wrap:wrap;">
                <div style="font-size:12px;color:#888;white-space:nowrap;">分类:</div>
                <div style="font-size:12px;display:flex;flex-wrap:wrap;gap:4px;justify-content:center;">${typeStr}</div>
            </div>`;

        // 分类筛选
        if (stats.categories && stats.categories.length > 0) {
            let catHtml = '<button class="btn-tag active" onclick="hhMarketSetCategory(\'\')" data-cat="" style="--ch-color:#6366f1">🌐 全部</button>';
            for (const c of stats.categories) {
                if (c.item_count > 0) {
                    catHtml += `<button class="btn-tag" onclick="hhMarketSetCategory('${c.id}')" data-cat="${c.id}" style="--ch-color:#8b5cf6">${c.name} <span style="font-size:10px;opacity:0.7">${c.item_count}</span></button>`;
                }
            }
            catEl.innerHTML = catHtml;
        }

        // 加载列表
        await hhMarketLoad();
    } catch (e) {
        statsEl.innerHTML = `<div style="color:#f87171;font-size:13px;">❌ 加载失败: ${e.message}</div>`;
    }
}

function hhMarketSetCategory(catId) {
    hhMarketCategory = catId;
    document.querySelectorAll('#hhMarketCategories .btn-tag').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`#hhMarketCategories [data-cat="${catId}"]`);
    if (btn) btn.classList.add('active');
    hhMarketLoad();
}

async function hhMarketLoad() {
    const resultsEl = document.getElementById('hhMarketResults');
    resultsEl.innerHTML = '<div style="text-align:center;color:#888;padding:20px;">⏳ 加载中...</div>';

    try {
        const sort = document.getElementById('hhMarketSort')?.value || 'newest';
        const type = document.getElementById('hhMarketType')?.value || '';
        // ★ 2026-08-02: 改用云端API（之前用本地market/browse几乎没内容）
        let url = `/api/harvenhub-cloud/browse?sort=${sort}&limit=50`;
        if (hhMarketCategory) url += `&category=${hhMarketCategory}`;
        if (type) url += `&type=${type}`;

        const resp = await fetch(url);
        const data = await resp.json();
        hhMarketData = data;

        if (!data.items || data.items.length === 0) {
            resultsEl.innerHTML = `<div style="text-align:center;color:#888;padding:30px;font-size:14px;">
                📭 市场暂无内容<br>
                <span style="font-size:12px;">成为第一个发布者！点击"📤 发布"按钮分享你的技能</span>
            </div>`;
            return;
        }

        const typeLabels = { skill: '⚡ Skill', agent: '🤖 Agent', knowledge: '📚 知识包', bundle: '📦 Bundle', template: '📋 模板', model_link: '🧠 模型/工具', tool: '🔧 工具' };
        const typeColors = { skill: '#6366f1', plugin: '#f59e0b', style: '#ec4899', knowledge: '#10b981' };

        let html = '';
        for (const item of data.items) {
            const stars = item.rating_avg ? '⭐'.repeat(Math.round(item.rating_avg)) + ` ${item.rating_avg.toFixed(1)}` : '暂无评分';
            const tags = (item.tags || []).slice(0, 3).map(t => `<span style="font-size:10px;background:var(--bg-hover);padding:1px 6px;border-radius:3px;margin-right:2px;">${escapeHtml(t)}</span>`).join('');
            const typeLabel = typeLabels[item.item_type] || '📦 其他';
            const typeColor = typeColors[item.item_type] || '#888';

            html += `
            <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:8px;display:flex;gap:12px;align-items:flex-start;transition:all 0.2s;"
                 onmouseover="this.style.borderColor='#6366f1';this.style.boxShadow='0 2px 8px rgba(99,102,241,0.1)'" 
                 onmouseout="this.style.borderColor='';this.style.boxShadow=''">
                <div style="font-size:24px;flex-shrink:0;width:40px;text-align:center;">${escapeHtml(item.icon || '📦')}</div>
                <div style="flex:1;min-width:0;">
                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                        <span style="font-weight:600;font-size:14px;">${escapeHtml(item.name)}</span>
                        <span style="font-size:10px;background:${typeColor};color:#fff;padding:1px 6px;border-radius:3px;">${typeLabel}</span>
                        <span style="font-size:10px;color:#888;">v${escapeHtml(item.version || '1.0.0')}</span>
                    </div>
                    <div style="font-size:12px;color:#888;margin:4px 0;">${escapeHtml(item.description || '暂无描述')}</div>
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px;color:#888;">
                        <span>👤 ${escapeHtml(item.author || '匿名')}</span>
                        <span>${stars}</span>
                        <span>📥 ${item.download_count || 0}</span>
                        ${tags}
                    </div>
                </div>
                <div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0;">
                    <button class="btn-primary btn-sm" onclick="hhMarketInstall('${item.id}')" style="white-space:nowrap;">📥 安装</button>
                    <button class="btn-secondary btn-sm" onclick="hhMarketRate('${item.id}','${escapeHtml(item.name)}')" style="white-space:nowrap;">⭐ 评分</button>
                </div>
            </div>`;
        }

        if (data.has_more) {
            html += `<div style="text-align:center;padding:10px;font-size:12px;color:#888;">显示 ${data.items.length} / ${data.total} 项</div>`;
        }

        resultsEl.innerHTML = html;
    } catch (e) {
        resultsEl.innerHTML = `<div style="color:#f87171;text-align:center;padding:20px;">❌ 加载失败: ${e.message}</div>`;
    }
}

async function hhMarketSearch() {
    const query = document.getElementById('hhMarketSearch')?.value?.trim();
    if (!query) { hhMarketLoad(); return; }
    const resultsEl = document.getElementById('hhMarketResults');
    resultsEl.innerHTML = '<div style="text-align:center;color:#888;padding:20px;">🔍 搜索中...</div>';

    try {
        const type = document.getElementById('hhMarketType')?.value || '';
        let url = `/api/harvenhub-market/search?q=${encodeURIComponent(query)}&limit=30`;
        if (type) url += `&type=${type}`;
        if (hhMarketCategory) url += `&category=${hhMarketCategory}`;

        const resp = await fetch(url);
        const data = await resp.json();

        if (!data.results || data.results.length === 0) {
            resultsEl.innerHTML = `<div style="text-align:center;color:#888;padding:30px;">📭 未找到 "${escapeHtml(query)}" 相关结果</div>`;
            return;
        }

        // Reuse the same render logic - set hhMarketData and call same render
        hhMarketData = { items: data.results, total: data.total };
        // Simple re-render using a helper
        renderMarketItems(data.results);
    } catch (e) {
        resultsEl.innerHTML = `<div style="color:#f87171;text-align:center;padding:20px;">❌ 搜索失败: ${e.message}</div>`;
    }
}

function renderMarketItems(items) {
    const resultsEl = document.getElementById('hhMarketResults');
    const typeLabels = { skill: '🎯 技能', plugin: '🔌 插件', style: '🎨 风格包', knowledge: '📚 知识' };
    const typeColors = { skill: '#6366f1', plugin: '#f59e0b', style: '#ec4899', knowledge: '#10b981' };

    let html = '';
    for (const item of items) {
        const stars = item.rating_avg ? '⭐'.repeat(Math.round(item.rating_avg)) + ` ${item.rating_avg.toFixed(1)}` : '暂无评分';
        const tags = (item.tags || []).slice(0, 3).map(t => `<span style="font-size:10px;background:var(--bg-hover);padding:1px 6px;border-radius:3px;margin-right:2px;">${escapeHtml(t)}</span>`).join('');
        const typeLabel = typeLabels[item.item_type] || '📦 其他';
        const typeColor = typeColors[item.item_type] || '#888';

        html += `
        <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:8px;display:flex;gap:12px;align-items:flex-start;transition:all 0.2s;"
             onmouseover="this.style.borderColor='#6366f1';this.style.boxShadow='0 2px 8px rgba(99,102,241,0.1)'" 
             onmouseout="this.style.borderColor='';this.style.boxShadow=''">
            <div style="font-size:24px;flex-shrink:0;width:40px;text-align:center;">${escapeHtml(item.icon || '📦')}</div>
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                    <span style="font-weight:600;font-size:14px;">${escapeHtml(item.name)}</span>
                    <span style="font-size:10px;background:${typeColor};color:#fff;padding:1px 6px;border-radius:3px;">${typeLabel}</span>
                </div>
                <div style="font-size:12px;color:#888;margin:4px 0;">${escapeHtml(item.description || '暂无描述')}</div>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px;color:#888;">
                    <span>👤 ${escapeHtml(item.author || '匿名')}</span>
                    <span>${stars}</span>
                    <span>📥 ${item.download_count || 0}</span>
                    ${tags}
                </div>
            </div>
            <div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0;">
                <button class="btn-primary btn-sm" onclick="hhMarketInstall('${item.id}')" style="white-space:nowrap;">📥 安装</button>
                <button class="btn-secondary btn-sm" onclick="hhMarketRate('${item.id}','${escapeHtml(item.name)}')" style="white-space:nowrap;">⭐ 评分</button>
            </div>
        </div>`;
    }

    resultsEl.innerHTML = html;
}

async function hhMarketInstall(itemId) {
    try {
        // ★ 2026-08-02: 改用云端下载API
        const resp = await fetch(`/api/harvenhub-cloud/download/${itemId}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            showToast(`✅ ${data.message || '安装成功'}`);
        } else {
            showToast(`❌ ${data.error || '安装失败'}`);
        }
    } catch (e) {
        showToast(`❌ 安装失败: ${e.message}`);
    }
}

async function hhMarketRate(itemId, itemName) {
    const score = prompt(`为 "${itemName}" 评分 (1-5星):`, '5');
    if (!score) return;
    const s = parseInt(score);
    if (isNaN(s) || s < 1 || s > 5) { showToast('❌ 评分范围1-5'); return; }

    const comment = prompt('写下评论（可选）:', '') || '';

    try {
        const resp = await fetch('/api/harvenhub-market/rate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                item_id: itemId,
                user_name: 'Harven用户',
                score: s,
                comment: comment
            })
        });
        const data = await resp.json();
        if (data.success) {
            showToast(`✅ 评分成功！新均分: ${data.new_avg} (${data.new_count}人评分)`);
            hhMarketLoad();
        } else {
            showToast(`❌ ${data.error}`);
        }
    } catch (e) {
        showToast(`❌ 评分失败: ${e.message}`);
    }
}

function hhMarketTogglePublish() {
    const form = document.getElementById('hhMarketPublishForm');
    hhMarketShowPublish = !hhMarketShowPublish;
    form.style.display = hhMarketShowPublish ? 'block' : 'none';
}

async function hhMarketPublish() {
    const name = document.getElementById('hhPublishName')?.value?.trim();
    const desc = document.getElementById('hhPublishDesc')?.value?.trim();
    const author = document.getElementById('hhPublishAuthor')?.value?.trim() || 'Harven用户';
    const category = document.getElementById('hhPublishCategory')?.value || 'other';
    const type = document.getElementById('hhPublishType')?.value || 'skill';
    const tagsStr = document.getElementById('hhPublishTags')?.value?.trim() || '';
    const version = document.getElementById('hhPublishVersion')?.value?.trim() || '1.0.0';
    const resultEl = document.getElementById('hhPublishResult');

    if (!name) { resultEl.innerHTML = '<span style="color:#f87171;">❌ 请输入名称</span>'; return; }
    if (!desc) { resultEl.innerHTML = '<span style="color:#f87171;">❌ 请输入描述</span>'; return; }

    const tags = tagsStr ? tagsStr.split(/[,，]/).map(t => t.trim()).filter(Boolean) : [];

    resultEl.innerHTML = '<span style="color:#f59e0b;">⏳ 发布中...</span>';

    try {
        const resp = await fetch('/api/harvenhub-market/publish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name, description: desc, author, category_id: category,
                item_type: type, tags, version, icon: '',
            })
        });
        const data = await resp.json();
        if (data.success) {
            resultEl.innerHTML = `<span style="color:#10b981;">${data.message}</span>`;
            // Clear form
            document.getElementById('hhPublishName').value = '';
            document.getElementById('hhPublishDesc').value = '';
            document.getElementById('hhPublishTags').value = '';
            // Refresh market
            setTimeout(() => {
                hhMarketTogglePublish();
                resultEl.innerHTML = '';
                hhMarketLoad();
                loadHarvenHubMarket(); // refresh stats too
            }, 1500);
        } else {
            resultEl.innerHTML = `<span style="color:#f87171;">❌ ${data.error}</span>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<span style="color:#f87171;">❌ 发布失败: ${e.message}</span>`;
    }
}

function showToast(msg) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.style.display = 'block';
    toast.style.opacity = '1';
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => { toast.style.display = 'none'; }, 300);
    }, 2500);
}

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
    // 侧边栏 HarvenHub 按钮事件
    const hhBtn = document.getElementById('btnHarvenHub');
    if (hhBtn) {
        hhBtn.addEventListener('click', openHarvenHub);
    }
});

// ===== Window 全局导出（由自检脚本补全）=====
window.openHarvenHub = openHarvenHub;
window.closeHarvenHub = closeHarvenHub;
window.hhSwitchTab = hhSwitchTab;
window.loadHarvenHubSources = loadHarvenHubSources;
window.renderSources = renderSources;
window.getRiskBadge = getRiskBadge;
window.hhToggleItem = hhToggleItem;
window.hhSelectAll = hhSelectAll;
window.updateSelectedCount = updateSelectedCount;
window.hhClearSelection = hhClearSelection;
window.hhRunAnalysis = hhRunAnalysis;
window.hhDoExport = hhDoExport;
window.loadHarvenHubHistory = loadHarvenHubHistory;
window.escapeHtml = escapeHtml;
window.loadHarvenHubMarket = loadHarvenHubMarket;
window.hhMarketSetCategory = hhMarketSetCategory;
window.hhMarketLoad = hhMarketLoad;
window.hhMarketSearch = hhMarketSearch;
window.renderMarketItems = renderMarketItems;
window.hhMarketInstall = hhMarketInstall;
window.hhMarketRate = hhMarketRate;

// ========== ★ 2026-07-24: Hub云端市场改为走后端API（不直连Hub服务器）==========
// ★ 旧代码直连 http://127.0.0.1:5001，现在走后端 /api/harvenhub-cloud/*

async function hubCloudLoad() {
    const listEl = document.getElementById('hubCloudList');
    const statsEl = document.getElementById('hubCloudStats');
    if (!listEl) return;

    listEl.innerHTML = '<div style="grid-column:1;text-align:center;padding:40px;color:#999;">⏳ 加载中...</div>';

    // 先检查连接
    try {
        const checkResp = await fetch('/api/harvenhub-cloud/check');
        const checkData = await checkResp.json();
        if (!checkData.success) {
            listEl.innerHTML = `<div style="grid-column:1;text-align:center;padding:40px;color:#999;">☁️ 云端未连接<br><small>${checkData.error || '无法连接到HarvenHub'}</small></div>`;
            if (statsEl) statsEl.innerHTML = '<span style="color:#999;">☁️ 未连接</span>';
            return;
        }
    } catch(e) {
        listEl.innerHTML = `<div style="grid-column:1;text-align:center;padding:40px;color:red;">❌ 连接失败: ${e.message}</div>`;
        return;
    }

    // 加载统计
    try {
        const sResp = await fetch('/api/harvenhub-cloud/check');
        if (statsEl) statsEl.innerHTML = '<span>☁️ 已连接</span>';
    } catch(e) { if(statsEl) statsEl.innerHTML = '<span style="color:red;">❌</span>'; }

    // 加载列表（走后端API，后端转发到云端）
    try {
        const type = document.getElementById('hubCloudType')?.value || '';
        const params = new URLSearchParams();
        if (type) params.set('type', type);
        params.set('sort', 'latest');
        params.set('limit', '100');

        const resp = await fetch(`/api/harvenhub-cloud/browse?${params}`);
        const data = await resp.json();

        const items = data.items || [];
        if (items.length > 0) {
            listEl.innerHTML = items.map(item => {
                const icon = item.type === 'model_link' ? '🧠' : item.type === 'skill' ? '⚡' : item.type === 'agent' ? '🤖' : item.type === 'knowledge' ? '📚' : item.type === 'bundle' ? '📦' : '📋';
                const catLabel = {general:'通用', reasoning:'推理', vision:'视觉', code:'代码', vertical:'垂直', tool:'工具', family:'家庭', enterprise:'企业', factory:'工厂'}[item.category] || item.category || '通用';
                return `
                <div style="border:1px solid var(--border);border-radius:8px;padding:12px;cursor:pointer;" onclick="hubCloudDetail('${item.id}')">
                    <div style="font-weight:bold;margin-bottom:6px;">${icon} ${item.name}</div>
                    <div style="font-size:12px;color:#888;margin-bottom:6px;">${(item.description||'').substring(0,80)}</div>
                    <div style="font-size:11px;color:#999;display:flex;justify-content:space-between;">
                        <span>📁 ${catLabel}</span>
                        <span>⬇️${item.download_count || 0}</span>
                        <span>⭐${item.rating || 0}</span>
                    </div>
                </div>`;
            }).join('');
        } else {
            listEl.innerHTML = '<div style="grid-column:1;text-align:center;padding:40px;color:#999;">📭 暂无内容<br><small>成为第一个上传者！</small></div>';
        }
    } catch (e) {
        listEl.innerHTML = `<div style="grid-column:1;text-align:center;padding:40px;color:red;">❌ 加载失败: ${e.message}</div>`;
    }
}

async function hubCloudSearch() {
    const query = document.getElementById('hubCloudSearch')?.value || '';
    const listEl = document.getElementById('hubCloudList');
    if (!listEl) return;
    if (!query.trim()) { hubCloudLoad(); return; }

    listEl.innerHTML = '<div style="grid-column:1;text-align:center;padding:40px;color:#999;">⏳ 搜索中...</div>';
    try {
        // ★ 2026-07-24: 走后端API（后端转发到云端）
        const resp = await fetch(`/api/harvenhub-market/search?q=${encodeURIComponent(query)}&limit=100`);
        const data = await resp.json();
        const items = data.results || data.items || [];
        if (items.length > 0) {
            listEl.innerHTML = items.map(item => `
                <div style="border:1px solid var(--border);border-radius:8px;padding:12px;cursor:pointer;" onclick="hubCloudDetail('${item.id}')">
                    <div style="font-weight:bold;">${item.name}</div>
                    <div style="font-size:12px;color:#888;">${(item.description||'').substring(0,60)}</div>
                    <div style="font-size:11px;color:#999;">⬇️${item.download_count||0} ⭐${item.rating||0}</div>
                </div>
            `).join('');
        } else {
            listEl.innerHTML = '<div style="grid-column:1;text-align:center;padding:40px;color:#999;">📭 未找到相关内容</div>';
        }
    } catch(e) {
        listEl.innerHTML = `<div style="grid-column:1;text-align:center;padding:40px;color:red;">❌ 搜索失败: ${e.message}</div>`;
    }
}

async function hubCloudDetail(itemId) {
    try {
        // ★ 2026-07-24: 走后端API
        const resp = await fetch(`/api/harvenhub-market/browse?limit=100`);
        const data = await resp.json();
        const items = data.items || [];
        const item = items.find(i => i.id === itemId) || (await fetch(`/api/harvenhub-cloud/check`).then(r=>r.json()));
        if (!item || !item.id) {
            alert('未找到内容');
            return;
        }

            // ★ 模型链接类型：执行命令/打开URL，不下载文件
            if (item.type === 'model_link') {
                const command = item.file_path || ''; // file_path存的是命令/URL
                const source = (item.tags || []).find(t => ['ollama','modelscope','huggingface','pip','github','url'].includes(t)) || '';

                // 从command推断source
                let installMethod = '';
                if (command.startsWith('ollama pull')) installMethod = 'ollama';
                else if (command.startsWith('pip install')) installMethod = 'pip';
                else if (command.startsWith('git clone')) installMethod = 'git';
                else if (command.startsWith('from modelscope')) installMethod = 'modelscope';
                else if (command.startsWith('from huggingface')) installMethod = 'huggingface';
                else if (command.startsWith('http')) installMethod = 'url';
                else if (command.startsWith('winget')) installMethod = 'winget';

                let msg = `${item.name}\n\n${item.description||'暂无描述'}\n\n`;
                msg += `来源: ${installMethod}\n`;
                msg += `命令: ${command}\n\n`;

                if (installMethod === 'url') {
                    msg += '点击确定打开下载页面';
                    if (confirm(msg)) {
                        window.open(command, '_blank');
                    }
                } else if (installMethod === 'ollama' || installMethod === 'pip' || installMethod === 'git' || installMethod === 'modelscope' || installMethod === 'huggingface' || installMethod === 'winget') {
                    msg += '点击确定让哈文自动安装';
                    if (confirm(msg)) {
                        // ★ 通过哈文后端API执行安装命令
                        hubCloudExecuteInstall(item.name, command, installMethod);
                    }
                } else {
                    msg += '点击确定打开链接';
                    if (confirm(msg)) {
                        window.open(command, '_blank');
                    }
                }
                return;
            }

            // ★ 其他类型：下载文件
            alert(`📦 ${item.name}\n\n描述: ${item.description||'暂无'}\n类型: ${item.type}\n分类: ${item.category||'通用'}\n下载: ${item.download_count||0}\n评分: ⭐${item.rating||0}\n\n点击确定下载`);
            hubCloudDownload(itemId, item.name);
    } catch(e) {
        alert('加载失败: ' + e.message);
    }
}

// ★ 通过哈文后端API执行安装命令
async function hubCloudExecuteInstall(modelName, command, method) {
    try {
        const resp = await fetch('/api/hub/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command, method })
        });
        const data = await resp.json();

        if (data.success) {
            alert(`✅ ${modelName} 安装成功！\n\n输出:\n${data.output || '完成'}`);
        } else {
            alert(`❌ ${modelName} 安装失败\n\n错误:\n${data.error || '未知错误'}`);
        }
    } catch(e) {
        // 降级：通过哈文对话执行
        hubCloudInstall(modelName, command, method);
    }
}

// ★ 通过哈文对话执行安装（降级方案）
async function hubCloudInstall(modelName, command, method) {
    const msg = `请帮我安装模型：${modelName}\n安装命令：${command}\n安装方式：${method}`;
    const input = document.getElementById('messageInput') || document.querySelector('#userInput') || document.querySelector('textarea[data-role="input"]');
    const sendBtn = document.getElementById('sendBtn') || document.querySelector('button[data-role="send"]');
    if (input && sendBtn) {
        input.value = msg;
        input.dispatchEvent(new Event('input', {bubbles: true}));
        setTimeout(() => sendBtn.click(), 100);
    } else {
        navigator.clipboard.writeText(msg).then(() => {
            alert(`安装命令已复制到剪贴板！\n\n请粘贴到哈文对话框发送：\n${msg}`);
        }).catch(() => {
            alert(`请手动复制以下内容到哈文对话框：\n\n${msg}`);
        });
    }
}

// ★ 上传Skill到Hub
async function hubUploadSkill(skillName, skillFile) {
    const formData = new FormData();
    formData.append('name', skillName);
    formData.append('type', 'skill');
    formData.append('category', 'tool');
    formData.append('description', skillName + ' Skill');
    formData.append('file', skillFile);

    try {
        const resp = await fetch(`/api/harvenhub-cloud/upload`, {
            method: 'POST', body: formData
        });
        const data = await resp.json();
        if (data.success) {
            alert(`✅ Skill上传成功！状态: ${data.status}`);
        } else {
            alert(`❌ 上传失败: ${data.error}`);
        }
    } catch(e) {
        alert(`❌ 上传失败: ${e.message}`);
    }
}

// ★ 上传Agent到Hub
async function hubUploadAgent(agentName, agentFile) {
    const formData = new FormData();
    formData.append('name', agentName);
    formData.append('type', 'agent');
    formData.append('category', 'tool');
    formData.append('description', agentName + ' Agent');
    formData.append('file', agentFile);

    try {
        const resp = await fetch(`/api/harvenhub-cloud/upload`, {
            method: 'POST', body: formData
        });
        const data = await resp.json();
        if (data.success) {
            alert(`✅ Agent上传成功！状态: ${data.status}`);
        } else {
            alert(`❌ 上传失败: ${data.error}`);
        }
    } catch(e) {
        alert(`❌ 上传失败: ${e.message}`);
    }
}

// ★ 上传知识包到Hub
async function hubUploadKnowledge(packName, packFile, description) {
    const formData = new FormData();
    formData.append('name', packName);
    formData.append('type', 'knowledge');
    formData.append('category', 'tool');
    formData.append('description', description || packName + ' 知识包');
    formData.append('file', packFile);

    try {
        const resp = await fetch(`/api/harvenhub-cloud/upload`, {
            method: 'POST', body: formData
        });
        const data = await resp.json();
        if (data.success) {
            alert(`✅ 知识包上传成功！状态: ${data.status}`);
        } else {
            alert(`❌ 上传失败: ${data.error}`);
        }
    } catch(e) {
        alert(`❌ 上传失败: ${e.message}`);
    }
}

// ★ 自动安装（从Hub下载并安装到哈文目录）
async function hubCloudAutoInstall(itemId, name, type) {
    if (!confirm(`确定下载并安装 "${name}" 吗？`)) return;

    // ★ 2026-08-10: 安装前检查pip依赖
    try {
        const depsResp = await fetch('/api/hub/check-deps', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: itemId })
        });
        const depsData = await depsResp.json();
        if (depsData.success && depsData.deps && depsData.deps.length > 0) {
            var depsList = depsData.deps.map(function(d) {
                return d.status + ' ' + d.name;
            }).join('\n');
            var hasMissing = depsData.deps.some(function(d) { return !d.installed; });
            var depsMsg = '📦 此 Skill 需要以下依赖：\n\n' + depsList;
            if (hasMissing) {
                depsMsg += '\n\n⚠️ 缺失的依赖会自动安装（pip install）';
            }
            depsMsg += '\n\n继续安装？';
            if (!confirm(depsMsg)) return;
        } else if (depsData.note) {
            // 无依赖声明，直接装
        }
    } catch(e) {
        // 检查失败不阻止安装
    }

    try {
        const resp = await fetch('/api/hub/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                item_id: itemId,
                name: name,
                type: type,
                // hub_url 不再需要，后端自动从config.yaml读取
            })
        });
        const data = await resp.json();

        if (data.success) {
            alert(`✅ ${name} 安装成功！\n\n已安装:\n${data.installed.join('\n')}`);
        } else {
            alert(`❌ 安装失败: ${data.error}`);
        }
    } catch(e) {
        alert(`❌ 安装失败: ${e.message}`);
    }
}

window.hubCloudExecuteInstall = hubCloudExecuteInstall;
window.hubUploadSkill = hubUploadSkill;
window.hubUploadAgent = hubUploadAgent;
window.hubUploadKnowledge = hubUploadKnowledge;
window.hubCloudAutoInstall = hubCloudAutoInstall;

async function hubCloudDownload(itemId, name) {
    try {
        // ★ 2026-07-24: 走后端API下载（后端转发到云端）
        const resp = await fetch('/api/harvenhub-cloud/download/' + itemId, { method: 'POST' });
        if (!resp.ok) throw new Error('下载失败');
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name + '.zip';
        a.click();
        URL.revokeObjectURL(url);
        alert(`✅ ${name} 已下载！`);
    } catch(e) {
        alert(`❌ 下载失败: ${e.message}`);
    }
}

function hubCloudShowUpload() {
    const el = document.getElementById('hubCloudUploadArea');
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function hubCloudDoUpload() {
    const name = document.getElementById('hubCloudUpName')?.value.trim();
    const type = document.getElementById('hubCloudUpType')?.value;
    const desc = document.getElementById('hubCloudUpDesc')?.value.trim();
    const fileInput = document.getElementById('hubCloudUpFile');
    const statusEl = document.getElementById('hubCloudUpStatus');

    if (!name || !fileInput?.files[0]) {
        if (statusEl) statusEl.innerHTML = '❌ 请填写名称并选择文件';
        return;
    }

    const formData = new FormData();
    formData.append('name', name);
    formData.append('type', type);
    formData.append('description', desc);
    formData.append('file', fileInput.files[0]);

    if (statusEl) statusEl.innerHTML = '⏳ 上传中...';

    try {
        const resp = await fetch(`/api/harvenhub-cloud/upload`, {
            method: 'POST',
            body: formData
        });
        const data = await resp.json();
        if (data.success) {
            if (statusEl) statusEl.innerHTML = `✅ 上传成功！状态: ${data.status}`;
            hubCloudLoad();
        } else {
            if (statusEl) statusEl.innerHTML = `❌ 失败: ${data.error}`;
        }
    } catch(e) {
        if (statusEl) statusEl.innerHTML = `❌ 上传失败: ${e.message}`;
    }
}

window.hubCloudLoad = hubCloudLoad;
window.hubCloudSearch = hubCloudSearch;
window.hubCloudDetail = hubCloudDetail;
window.hubCloudDownload = hubCloudDownload;
window.hubCloudShowUpload = hubCloudShowUpload;
window.hubCloudDoUpload = hubCloudDoUpload;

window.hhMarketTogglePublish = hhMarketTogglePublish;
window.hhMarketPublish = hhMarketPublish;
window.showToast = showToast;
