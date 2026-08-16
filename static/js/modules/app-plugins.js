/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — PLUGINS + RESOURCE HUB module ===== */
// ===== 全网资源发现：多渠道搜索 + AI生成 + 一键安装 =====

// 搜索状态
window._rhState = {
    selectedChannels: [],   // 空=全部
    selectedType: 'all',
    currentResults: null,
};

// 渠道定义（按优先级排列）
const RH_CHANNELS = {
    harvenhub:   { id: 'harvenhub',   label: 'HarvenHub',   icon: '🏠', color: '#8b5cf6', url: 'https://harvenhub.com', official: true, priority: 0 },
    skillhub:    { id: 'skillhub',     label: 'SkillHub',     icon: '🛒', color: '#f59e0b', url: 'https://skillhub.tencent.com', official: false, priority: 3 },
    clawhub:     { id: 'clawhub',      label: 'ClawHub',      icon: '🦞', color: '#ef4444', url: 'https://clawhub.com', official: false, priority: 3 },
    github:      { id: 'github',        label: 'GitHub',        icon: '🐙', color: '#333', url: 'https://github.com', official: false, priority: 5 },
    gitee:       { id: 'gitee',        label: 'Gitee',         icon: '🏮', color: '#c71d23', url: 'https://gitee.com', official: false, priority: 5 },
    pypi:        { id: 'pypi',         label: 'PyPI',          icon: '🐍', color: '#3776ab', url: 'https://pypi.org', official: false, priority: 5 },
    huggingface: { id: 'huggingface',  label: 'HuggingFace',  icon: '🤗', color: '#ff9d00', url: 'https://huggingface.co', official: false, priority: 5 },
    winget:      { id: 'winget',       label: 'Winget',       icon: '💿', color: '#0078d4', url: 'https://winstall.app', official: false, priority: 5 },
};

async function loadRHChannels() {
    try {
        const r = await fetch('/api/resource-hub/channels');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        if (d.channels && d.channels.length > 0) {
            _renderChannelTabs(d.channels);
            return;
        }
    } catch (e) {
        console.warn('[ResourceHub] channels API 不可达，使用本地 RH_CHANNELS 渲染:', e.message);
    }
    // Fallback：直接用本地 RH_CHANNELS 常量渲染，确保新渠道显示
    const fallbackChannels = Object.values(RH_CHANNELS).map(ch => ({
        id: ch.id,
        label: ch.label,
        icon: ch.icon,
        priority: ch.priority,
        is_official: ch.official,
        enabled: true,
    }));
    _renderChannelTabs(fallbackChannels);
}

function _renderChannelTabs(channels) {
    const el = document.getElementById('rhChannelTabs');
    if (!el) return;
    // 按优先级排序：HarvenHub 官方第一
    channels.sort((a, b) => (a.priority || 99) - (b.priority || 99));
    let html = '<button class="btn-tag active" onclick="toggleRHChannel(this)" data-channel="" title="全部渠道">🌐 全部</button>';
    for (const ch of channels) {
        const def = RH_CHANNELS[ch.id] || { icon: '📦', label: ch.label, color: '#666', official: false };
        const officialBadge = ch.is_official || def.official ? ' <span style="font-size:9px;background:#8b5cf6;color:#fff;padding:0 3px;border-radius:3px;">官方</span>' : '';
        const chUrl = (RH_CHANNELS[ch.id] || {}).url || '#';
        html += `<button class="btn-tag" onclick="toggleRHChannel(this)" data-channel="${ch.id}" title="${ch.label}" style="--ch-color:${def.color}">
            <a href="${chUrl}" target="_blank" style="text-decoration:none;color:inherit;" onclick="event.stopPropagation()">${def.icon} ${def.label}</a>${officialBadge}
        </button>`;
    }
    el.innerHTML = html;
}

function toggleRHChannel(btn) {
    btn.classList.toggle('active');
    const ch = btn.dataset.channel;
    if (ch) {
        const idx = window._rhState.selectedChannels.indexOf(ch);
        if (idx >= 0) window._rhState.selectedChannels.splice(idx, 1);
        else window._rhState.selectedChannels.push(ch);
    }

    // 如果搜索框为空且选中了渠道 → 自动触发浏览模式
    const input = document.getElementById('pluginMarketSearch');
    if (!input || !input.value.trim()) {
        const active = window._rhState.selectedChannels;
        if (active.length === 1) {
            browseRHChannel(active[0]);
        } else if (active.length === 0 && ch === '') {
            // 点击"全部" → 浏览所有支持浏览的渠道
            browseAllRHChannels();
        }
    }
}

function setRHType(type, btn) {
    window._rhState.selectedType = type;
    document.querySelectorAll('.rh-type-tag').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    // 重新搜索
    const input = document.getElementById('pluginMarketSearch');
    if (input && input.value.trim()) {
        searchResourceHub();
    }
}

async function searchResourceHub() {
    const input = document.getElementById('pluginMarketSearch');
    const query = input ? input.value.trim() : '';
    if (!query) { toast('请输入搜索关键词'); return; }

    const resultsEl = document.getElementById('pluginMarketResults');
    const statusEl = document.getElementById('pluginMarketStatus');
    const statsEl = document.getElementById('rhSearchStats');

    resultsEl.innerHTML = '<div class="rh-loading">🏠 正在搜索 HarvenHub 官方市场 + 全网资源...</div>';
    if (statusEl) statusEl.textContent = '';
    if (statsEl) statsEl.innerHTML = '';

    try {
        const params = new URLSearchParams({
            q: query,
            type: window._rhState.selectedType || 'all',
            limit: '20',
        });
        if (window._rhState.selectedChannels.length) {
            params.set('channels', window._rhState.selectedChannels.join(','));
        }

        // 使用 HarvenHub 优先搜索端点
        const r = await fetch('/api/resource-hub/search-official-first?' + params.toString());
        const d = await r.json();

        if (!d.success) {
            resultsEl.innerHTML = `<div class="rh-error">❌ ${escHtml(d.error || '搜索失败')}</div>`;
            return;
        }

        window._rhState.currentResults = d;
        window._rhState.harvenhubStatus = d.harvenhub_status || null;

        // === HarvenHub 状态横幅 ===
        let bannerHtml = '';
        if (d.harvenhub_status) {
            const hs = d.harvenhub_status;
            if (hs.available) {
                bannerHtml = `<div class="rh-harvenhub-banner official">
                    🏠 <b>HarvenHub 官方精选</b> — ${escHtml(hs.message)}
                    <span style="font-size:11px;color:#666;">（官方认证，安全可靠）</span>
                </div>`;
            } else {
                bannerHtml = `<div class="rh-harvenhub-banner fallback">
                    🏠 <b>HarvenHub</b> — ${escHtml(hs.message)}
                    ${hs.hint ? `<span style="font-size:11px;color:#888;">（${escHtml(hs.hint)}）</span>` : ''}
                </div>`;
            }
        }

        // 统计栏
        if (statsEl && d.channels) {
            statsEl.innerHTML = d.channels.map(ch => {
                const def = RH_CHANNELS[ch.id] || { icon: '📦', label: ch.label };
                const officialTag = ch.is_official ? ' <span style="font-size:9px;color:#8b5cf6;">官方</span>' : '';
                return `<span style="margin-right:12px;font-size:12px;color:#888;">${def.icon} ${def.label}${officialTag}: <b style="color:var(--primary)">${ch.count}</b></span>`;
            }).join('') + `<span style="font-size:12px;color:#888;">| 共 <b>${d.total}</b> 个结果</span>`;
        }

        if (!d.total) {
            resultsEl.innerHTML = bannerHtml + `
                <div class="rh-empty">
                    <div style="font-size:40px;margin-bottom:10px;">🔍</div>
                    <div>未找到相关资源</div>
                    <div style="font-size:12px;color:#888;margin-top:6px;">试试其他关键词，或者让 AI 帮你生成一个</div>
                    <button class="btn-primary btn-sm" style="margin-top:12px;" onclick="showAISkillGenerator()">🤖 AI 生成技能</button>
                </div>`;
            return;
        }

        // 渲染结果（按类型分组）
        let html = bannerHtml;
        if (d.by_type) {
            const typeOrder = ['plugin', 'skill', 'knowledge', 'tool', 'other'];
            for (const t of typeOrder) {
                const items = d.by_type[t];
                if (!items || !items.length) continue;
                const typeInfo = { plugin: '🔌', skill: '🎯', knowledge: '📚', tool: '🔧', other: '📦' };
                html += `<div class="rh-type-section">
                    <div class="rh-type-title">${typeInfo[t] || ''} ${items[0].resource_type_label || t} (${items.length})</div>`;
                html += items.map((item, i) => _renderRHResult(item, i, t)).join('');
                html += '</div>';
            }
        }

        resultsEl.innerHTML = html || bannerHtml + '<div class="rh-empty">暂无结果</div>';
    } catch (e) {
        resultsEl.innerHTML = `<div class="rh-error">❌ 搜索失败: ${escHtml(e.message)}</div>`;
    }
}

function _renderRHResult(item, index, rtype) {
    const src = RH_CHANNELS[item.source] || { icon: '📦', label: item.source_label || item.source, color: '#666', official: false };
    const qualityStars = '⭐'.repeat(Math.round(item.quality / 2));
    const licenseLabel = item.license
        ? (typeof item.license === 'string' ? item.license : (item.license.spdx_id || item.license.key || ''))
        : '';
    const licenseColor = licenseLabel.match(/mit|apache|bsd|unlicense|cc0/i) ? '#22c55e'
        : licenseLabel.match(/gpl|agpl|lgpl/i) ? '#eab308' : '#ef4444';
    const licenseCategory = licenseLabel.match(/mit|apache|bsd|unlicense|cc0/i) ? 'safe'
        : licenseLabel.match(/gpl|agpl|lgpl/i) ? 'caution' : 'danger';

    const uid = `${item.source}_${index}`;

    // HarvenHub 官方认证标记
    const officialBadge = (item.source === 'harvenhub' || src.official)
        ? '<span style="font-size:9px;background:#8b5cf610;color:#8b5cf6;padding:1px 5px;border-radius:3px;margin-left:4px;" title="HarvenHub 官方认证">🏠 官方认证</span>' : '';
    // 许可证风险图标
    const licenseIcon = licenseCategory === 'safe' ? '🟢' : licenseCategory === 'caution' ? '🟡' : '🔴';

    return `
    <div class="plugin-result-card rh-card" id="rh-card-${uid}">
        <div class="plugin-result-header">
            <div style="flex:1;min-width:0;">
                <div class="plugin-result-title">
                    <span style="font-size:10px;background:${src.color}15;color:${src.color};padding:1px 5px;border-radius:3px;margin-right:4px;">${src.icon} ${src.label}</span>
                    ${escHtml(item.name || item.full_name)}${officialBadge}
                    ${item.tags && item.tags.length ? item.tags.slice(0,2).map(t => `<span style="font-size:9px;background:var(--bg-tertiary);padding:1px 4px;border-radius:3px;color:#888;">${escHtml(t)}</span>`).join(' ') : ''}
                </div>
                <div class="plugin-result-desc">${escHtml(item.description || '无描述')}</div>
            </div>
            <button class="btn-primary btn-sm" onclick="installRHItem('${uid}', this)" title="一键安装">
                ⬇️ 安装
            </button>
        </div>
        <div class="plugin-result-meta" style="flex-wrap:wrap;gap:4px 12px;">
            <span title="质量评分">${qualityStars} ${item.quality.toFixed(1)}</span>
            <span>⭐ ${item.stars || 0}</span>
            ${item.updated_at ? `<span>📅 ${item.updated_at.substring(0, 10)}</span>` : ''}
            ${licenseLabel ? `<span style="color:${licenseColor};" title="许可证">${licenseIcon} ${licenseLabel}</span>` : '<span style="color:#ef4444;">🔴 无许可证</span>'}
            <a href="${escHtml(item.url || '#')}" target="_blank" style="font-size:11px;" onclick="event.stopPropagation()">查看 ↗</a>
            <a href="#" style="font-size:11px;color:var(--primary);" onclick="event.stopPropagation();scanRHItem('${uid}')">🔍 扫描</a>
        </div>
        <div id="rh-install-status-${uid}" style="margin-top:6px;font-size:12px;"></div>
    </div>`;
}

// 外部链接预览（避免直接跳转离开应用）
function showExternalPreview(url, title) {
    // 创建临时遮罩和弹窗
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.innerHTML = `
        <div style="background:var(--bg-primary);border-radius:12px;padding:24px;max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.3);">
            <div style="font-size:16px;font-weight:600;margin-bottom:12px;">🔍 外部资源预览</div>
            <div style="font-size:13px;color:var(--text-secondary);margin-bottom:8px;">即将访问以下外部网站：</div>
            <div style="background:var(--bg-tertiary);padding:10px 12px;border-radius:8px;font-size:13px;word-break:break-all;margin-bottom:16px;">
                <b>${escHtml(title || '未知')}</b><br>
                <span style="color:#888;font-size:12px;">${escHtml(url)}</span>
            </div>
            <div style="font-size:12px;color:#888;margin-bottom:16px;">
                ⚠️ 外部网站内容不受 Harven AI 控制，请注意数据安全。
            </div>
            <div style="display:flex;gap:10px;justify-content:flex-end;">
                <button onclick="this.closest('.rh-ext-overlay').remove()" style="padding:8px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg-secondary);cursor:pointer;font-size:13px;">取消</button>
                <button onclick="window.open('${escHtml(url)}', '_blank');this.closest('.rh-ext-overlay').remove();" style="padding:8px 16px;border-radius:6px;border:none;background:var(--primary);color:#fff;cursor:pointer;font-size:13px;">继续访问 ↗</button>
            </div>
        </div>
    `;
    overlay.className = 'rh-ext-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
}

// 安装资源（含许可证合规检查）
async function installRHItem(uid, btn) {
    const results = window._rhState.currentResults;
    if (!results) return;

    // 从 UID 解析 source 和 index
    const parts = uid.replace('rh-card-', '').split('_');
    const source = parts[0];
    const rtype = parts[1] || 'other';

    // 找到对应的 item
    let item = null;
    for (const ch in results.by_channel || {}) {
        const items = results.by_channel[ch];
        for (const it of items) {
            if ((it.source + '_' + it.resource_type) === (source + '_' + rtype)) {
                item = it;
                break;
            }
        }
        if (item) break;
    }
    if (!item) item = (results.by_channel?.[source] || [])[parseInt(rtype)] || results.top?.[0];
    if (!item) return;

    // 提取许可证文本（用于后端检测）
    let licenseText = '';
    if (item.license) {
        if (typeof item.license === 'string') {
            licenseText = item.license;
        } else if (item.license.spdx_id) {
            licenseText = item.license.spdx_id;
        } else if (item.license.key) {
            licenseText = item.license.key;
        }
    }

    const statusEl = document.getElementById('rh-install-status-' + uid);
    const originalText = btn.textContent;
    const originalClass = btn.className;

    btn.disabled = true;
    btn.textContent = '⏳ 安装中...';
    if (statusEl) statusEl.innerHTML = '<span style="color:#3b82f6;">正在下载并安全扫描...</span>';

    try {
        const installData = {
            source: item.source,
            resource_id: item.full_name || item.id,
            url: item.url,
            repo_url: item.url,
            resource_type: item.resource_type,
            name: item.name,
            license_text: licenseText,
        };

        const r = await fetch('/api/resource-hub/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(installData)
        });
        const d = await r.json();

        // === 许可证拦截处理 ===
        if (d.blocked) {
            btn.textContent = originalText;
            btn.className = originalClass;
            btn.disabled = false;

            const blockReason = d.block_reason || 'license_policy';
            const reason = d.reason || '许可证不合规';

            if (statusEl) {
                let actionHtml = '';
                if (blockReason === 'no_license') {
                    actionHtml = `<div style="margin-top:6px;">
                        <button class="btn-danger btn-sm" onclick="installRHItemForce('${uid}')" 
                                style="margin-right:6px;">⚠️ 我了解风险，继续安装</button>
                        <button class="btn-secondary btn-sm" onclick="document.getElementById('rh-install-status-${uid}').innerHTML=''">取消</button>
                    </div>`;
                } else if (blockReason === 'viral_license') {
                    actionHtml = `<div style="margin-top:6px;">
                        <button class="btn-warning btn-sm" onclick="installRHItemForce('${uid}')"
                                style="margin-right:6px;">⚠️ 我了解传染性风险，继续安装</button>
                        <button class="btn-secondary btn-sm" onclick="document.getElementById('rh-install-status-${uid}').innerHTML='';
                            searchAlternative('${(item.name || '').replace(/'/g, "\\'")}')">🔄 搜索替代品</button>
                    </div>`;
                } else if (blockReason === 'pypi_confirm' || blockReason === 'winget_confirm') {
                    actionHtml = `<div style="margin-top:6px;">
                        <button class="btn-primary btn-sm" onclick="installRHItemForce('${uid}')"
                                style="margin-right:6px;">✅ 确认安装</button>
                        <button class="btn-secondary btn-sm" onclick="document.getElementById('rh-install-status-${uid}').innerHTML=''">取消</button>
                    </div>`;
                } else if (blockReason === 'dangerous_license') {
                    actionHtml = `<div style="margin-top:6px;">
                        <strong style="color:#ef4444;">🔴 强烈不建议安装此资源</strong>
                        <button class="btn-secondary btn-sm" onclick="document.getElementById('rh-install-status-${uid}').innerHTML=''" style="margin-left:8px;">取消</button>
                    </div>`;
                }

                statusEl.innerHTML = `<div style="padding:8px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:12px;white-space:pre-line;line-height:1.6;">${escHtml(reason)}${actionHtml}</div>`;
            }
            return;
        }

        // === 安装成功 ===
        if (d.success) {
            btn.textContent = '✅ 已安装';
            btn.className = 'btn-secondary btn-sm';
            let statusMsg = `✅ 安装成功！「${escHtml(d.name || item.name)}」已就绪`;
            if (d.hint) statusMsg += `\n<span style="font-size:10px;color:#888;">${escHtml(d.hint)}</span>`;
            if (statusEl) statusEl.innerHTML = `<span style="color:#22c55e;">${statusMsg}</span>`;
            toast('✅ 安装成功！');
            loadInstalledPlugins();
            if (d.type === 'skill') loadSkills();
        } else {
            btn.textContent = '❌ 失败';
            btn.className = 'btn-danger btn-sm';
            if (statusEl) statusEl.innerHTML = `<span style="color:#ef4444;">❌ ${escHtml(d.error || d.message || '安装失败')}</span>`;
            setTimeout(() => { btn.textContent = originalText; btn.className = originalClass; btn.disabled = false; }, 2000);
        }
    } catch (e) {
        btn.textContent = '❌ 错误';
        btn.className = 'btn-danger btn-sm';
        if (statusEl) statusEl.innerHTML = `<span style="color:#ef4444;">❌ ${escHtml(e.message)}</span>`;
        setTimeout(() => { btn.textContent = originalText; btn.className = originalClass; btn.disabled = false; }, 2000);
    }
}

// 强制安装（跳过许可证检查）
async function installRHItemForce(uid) {
    const results = window._rhState.currentResults;
    if (!results) return;

    const parts = uid.replace('rh-card-', '').split('_');
    const source = parts[0];
    const rtype = parts[1] || 'other';

    let item = null;
    for (const ch in results.by_channel || {}) {
        for (const it of results.by_channel[ch]) {
            if ((it.source + '_' + it.resource_type) === (source + '_' + rtype)) {
                item = it; break;
            }
        }
        if (item) break;
    }
    if (!item) return;

    const statusEl = document.getElementById('rh-install-status-' + uid);
    if (statusEl) statusEl.innerHTML = '<span style="color:#3b82f6;">⏳ 正在强制安装（已跳过许可证检查）...</span>';

    try {
        const r = await fetch('/api/resource-hub/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source: item.source,
                resource_id: item.full_name || item.id,
                url: item.url,
                repo_url: item.url,
                resource_type: item.resource_type,
                name: item.name,
                skip_license_check: true,
            })
        });
        const d = await r.json();

        if (d.success) {
            if (statusEl) statusEl.innerHTML = `<span style="color:#22c55e;">✅ 已强制安装「${escHtml(d.name || item.name)}」</span>`;
            toast('✅ 安装成功（已跳过许可证检查）');
            loadInstalledPlugins();
            if (d.type === 'skill') loadSkills();
        } else {
            if (statusEl) statusEl.innerHTML = `<span style="color:#ef4444;">❌ ${escHtml(d.error || '安装失败')}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = `<span style="color:#ef4444;">❌ ${escHtml(e.message)}</span>`;
    }
}

// 搜索替代品（当原资源许可证不合规时）
function searchAlternative(name) {
    const input = document.getElementById('pluginMarketSearch');
    if (input) {
        input.value = name + ' alternative mit license';
        searchResourceHub();
    }
}

// 安全扫描
async function scanRHItem(uid) {
    const results = window._rhState.currentResults;
    if (!results) return;
    const parts = uid.replace('rh-card-', '').split('_');
    let item = null;
    for (const ch in results.by_channel || {}) {
        for (const it of results.by_channel[ch]) {
            if ((it.source + '_' + it.resource_type) === (parts[0] + '_' + parts[1])) {
                item = it; break;
            }
        }
        if (item) break;
    }
    if (!item) return;

    const statusEl = document.getElementById('rh-install-status-' + uid);
    if (statusEl) statusEl.innerHTML = '<span style="color:#3b82f6;">🔍 正在安全扫描...</span>';

    try {
        // 仅 GitHub/Gitee 支持 dry_run 扫描
        if (item.source === 'github' || item.source === 'gitee') {
            const r = await fetch('/api/plugins/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_url: item.url, dry_run: true })
            });
            const d = await r.json();
            let html = '';
            if (d.scan_result) {
                html += d.scan_result.safe
                    ? '<span style="color:#22c55e;">✅ 安全扫描通过</span> '
                    : '<span style="color:#ef4444;">❌ 存在风险</span> ';
                if (d.scan_result.warnings?.length) html += `<span style="color:#eab308;">⚠️${d.scan_result.warnings.length}个警告</span> `;
                if (d.scan_result.errors?.length) html += `<span style="color:#ef4444;">🚨${d.scan_result.errors.length}个危险</span> `;
                if (d.scan_result.scanned_files) html += `<span style="color:#888;">扫描${d.scan_result.scanned_files.length}个文件</span>`;
            }
            if (statusEl) statusEl.innerHTML = html;
        } else {
            if (statusEl) statusEl.innerHTML = `<span style="color:#22c55e;">✅ 来源 ${item.source_label || item.source} 为官方渠道，风险较低</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = `<span style="color:#ef4444;">扫描失败: ${escHtml(e.message)}</span>`;
    }
}

// AI 生成技能弹窗
function showAISkillGenerator() {
    const desc = prompt('🤖 AI 技能生成器\n\n描述你需要的技能，例如：\n"帮我分析Excel表格，生成图表和数据报告"\n"帮我润色英文邮件，提供多个版本"\n"帮我根据股票代码分析财报并给出投资建议"\n\n输入描述：');
    if (!desc) return;
    generateAISkill(desc);
}

async function generateAISkill(description) {
    const resultsEl = document.getElementById('pluginMarketResults');
    const statusEl = document.getElementById('pluginMarketStatus');
    if (statusEl) statusEl.textContent = '🤖 AI 正在生成技能...';

    try {
        const r = await fetch('/api/resource-hub/generate-skill', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ description })
        });
        const d = await r.json();
        if (d.success) {
            if (statusEl) statusEl.textContent = `✅ AI 技能「${d.skill?.name || ''}」已生成！已添加到技能列表。`;
            toast('✅ AI 技能生成成功！');
            loadSkills();
        } else {
            if (statusEl) statusEl.textContent = '❌ 生成失败: ' + (d.error || '');
        }
    } catch (e) {
        if (statusEl) statusEl.textContent = '❌ 生成失败: ' + e.message;
    }
}

// 兼容旧版 searchMarketPlugins（保持向后兼容）
async function searchMarketPlugins() {
    return searchResourceHub();
}

// 兼容旧版 installMarketPlugin
async function installMarketPlugin(index, btn) {
    const results = window._rhState.currentResults;
    if (!results || !results.top || !results.top[index]) return;
    const item = results.top[index];
    const uid = `${item.source}_${item.resource_type}_${index}`;
    return installRHItem(uid, btn);
}

// 兼容旧版 scanMarketPlugin
async function scanMarketPlugin(index) {
    const results = window._rhState.currentResults;
    if (!results || !results.top || !results.top[index]) return;
    const item = results.top[index];
    const uid = `${item.source}_${item.resource_type}_${index}`;
    return scanRHItem(uid);
}

// ===== 旧版弹窗兼容 =====

// Search plugins on GitHub (保留给独立弹窗)
async function searchPlugins() {
    const input = document.getElementById('pluginSearchInput');
    const query = input.value.trim();
    if (!query) { toast('请输入搜索关键词或 GitHub 地址'); return; }

    const resultsEl = document.getElementById('pluginSearchResults');
    resultsEl.innerHTML = '<div style="color:#aaa;padding:10px;">搜索中...</div>';

    try {
        let searchQuery = query;
        const ghMatch = query.match(/github\.com[:/]([^/]+)\/([^/]+)/);
        if (ghMatch) {
            searchQuery = ghMatch[2].replace('.git', '');
        }

        const r = await fetch('/api/plugins/search?q=' + encodeURIComponent(searchQuery));
        const d = await r.json();

        if (d.error) {
            resultsEl.innerHTML = `<div style="color:#ef4444;padding:10px;">搜索失败: ${escHtml(d.error)}</div>`;
            return;
        }
        if (!d.results || !d.results.length) {
            resultsEl.innerHTML = '<div style="color:#aaa;padding:10px;">未找到相关插件，试试其他关键词</div>';
            return;
        }

        resultsEl.innerHTML = d.results.map((repo, i) => `
            <div class="plugin-result-card" onclick="showPluginDetail(${i})">
                <div class="plugin-result-title">📦 ${escHtml(repo.full_name)}</div>
                <div class="plugin-result-desc">${escHtml(repo.description || '无描述')}</div>
                <div class="plugin-result-meta">
                    <span class="plugin-result-stars">⭐ ${repo.stargazers_count || 0}</span>
                    <span>更新: ${repo.updated_at ? repo.updated_at.substring(0, 10) : '未知'}</span>
                </div>
            </div>
        `).join('');

        window._pluginSearchResults = d.results;
    } catch (e) {
        resultsEl.innerHTML = `<div style="color:#ef4444;padding:10px;">搜索失败: ${escHtml(e.message)}</div>`;
    }
}

// Show plugin detail and scan
async function showPluginDetail(index) {
    const results = window._pluginSearchResults;
    if (!results || !results[index]) return;
    const repo = results[index];
    currentPluginRepo = repo;

    const titleEl = document.getElementById('pluginDetailTitle');
    const contentEl = document.getElementById('pluginDetailContent');

    titleEl.textContent = '插件详情 — ' + repo.full_name;
    contentEl.innerHTML = `
        <div style="margin-bottom:12px;">
            <strong>${escHtml(repo.full_name)}</strong>
            <span style="color:#eab308;margin-left:8px;">⭐ ${repo.stargazers_count || 0}</span>
        </div>
        <div style="color:#888;font-size:13px;margin-bottom:12px;">${escHtml(repo.description || '无描述')}</div>
        <div style="font-size:12px;color:#aaa;margin-bottom:16px;">
            仓库: <a href="${escHtml(repo.html_url || '#')}" target="_blank">${repo.html_url}</a><br>
            更新时间: ${repo.updated_at || '未知'}
        </div>
        <button class="btn-secondary btn-sm" onclick="scanPluginBeforeInstall()">🔍 安全扫描并预览代码</button>
        <div id="pluginScanResult" style="margin-top:12px;"></div>
    `;

    openModal('pluginDetailModal');
}

// Scan plugin before install
async function scanPluginBeforeInstall() {
    if (!currentPluginRepo) return;
    const scanEl = document.getElementById('pluginScanResult');
    scanEl.innerHTML = '<div style="color:#aaa;">正在从 GitHub 下载并扫描...</div>';

    try {
        const r = await fetch('/api/plugins/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo_url: currentPluginRepo.html_url, dry_run: true })
        });
        const d = await r.json();

        if (d.error) {
            scanEl.innerHTML = `<div class="scan-error">扫描失败: ${escHtml(d.error)}</div>`;
            return;
        }

        let html = '';
        if (d.scan_result) {
            if (d.scan_result.safe) {
                html += '<div class="scan-ok">✅ 静态扫描通过，未检测到危险操作</div>';
            }
            if (d.scan_result.warnings && d.scan_result.warnings.length) {
                html += '<div class="scan-warn"><strong>⚠️ 警告：</strong><ul>' +
                    d.scan_result.warnings.map(w => `<li>${escHtml(w)}</li>`).join('') +
                    '</ul></div>';
            }
            if (d.scan_result.errors && d.scan_result.errors.length) {
                html += '<div class="scan-error"><strong>❌ 错误：</strong><ul>' +
                    d.scan_result.errors.map(e => `<li>${escHtml(e)}</li>`).join('') +
                    '</ul></div>';
            }
        }

        // Show file list
        if (d.scan_result && d.scan_result.scanned_files) {
            html += '<div style="margin-top:8px;font-size:12px;color:#888;">扫描文件: ' +
                d.scan_result.scanned_files.join(', ') + '</div>';
        }

        // Show hashes
        if (d.hashes) {
            html += '<div class="hash-list">文件哈希 (SHA-256):<br>' +
                Object.entries(d.hashes).map(([f, h]) =>
                    `<span style="font-family:monospace;font-size:11px;">${escHtml(f)}: ${h.substring(0, 16)}...</span>`
                ).join('<br>') + '</div>';
        }

        scanEl.innerHTML = html || '<div class="scan-ok">✅ 扫描完成</div>';
    } catch (e) {
        scanEl.innerHTML = `<div class="scan-error">扫描失败: ${escHtml(e.message)}</div>`;
    }
}

// Confirm and install plugin
async function confirmInstallPlugin() {
    if (!currentPluginRepo) return;
    const btn = document.getElementById('pluginInstallBtn');
    btn.disabled = true;
    btn.textContent = '安装中...';

    try {
        const r = await fetch('/api/plugins/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ repo_url: currentPluginRepo.html_url })
        });
        const d = await r.json();

        if (d.success) {
            toast('✅ 插件安装成功！重启后生效');
            closeModal('pluginDetailModal');
            closeModal('pluginInstallModal');
            loadInstalledPlugins();
        } else {
            toast('❌ 安装失败: ' + (d.message || d.error));
        }
    } catch (e) {
        toast('安装失败: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '安装插件';
    }
}

// ===== End Plugin Management =====


// ===== Ollama Model Switch (Multi-model Tab) =====

async function loadOllamaModelSelect() {
    try {
        const r = await fetch('/api/ollama/models');
        const models = await r.json();
        const sel = document.getElementById('ollamaModelSelect');
        if (!sel) return;
        if (models.length) {
            sel.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join('');
        } else {
            sel.innerHTML = '<option value="">未检测到已安装模型</option>';
        }
        // 显示当前模型
        const sr = await fetch('/api/ollama/status');
        const sd = await sr.json();
        const desc = document.getElementById('ollamaCurrentModel');
        if (desc) desc.textContent = `当前模型: ${sd.model || '未知'}`;
        // 预选当前
        if (sd.model && sel.querySelector(`option[value="${sd.model}"]`)) {
            sel.value = sd.model;
        }
    } catch (e) {
        const sel = document.getElementById('ollamaModelSelect');
        if (sel) sel.innerHTML = '<option value="">本地引擎未连接</option>';
    }
}

async function switchOllamaModel() {
    const sel = document.getElementById('ollamaModelSelect');
    const model = sel ? sel.value : '';
    if (!model) { toast('请先选择一个模型'); return; }
    try {
        const r = await fetch('/api/ollama/switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        const d = await r.json();
        if (d.ok || d.success) {
            toast(`✅ 已切换到 ${model}`);
            document.getElementById('ollamaCurrentModel').textContent = `当前模型: ${model}`;
            checkOllamaStatus();
        } else {
            toast('切换失败: ' + (d.error || '未知错误'));
        }
    } catch (e) {
        toast('切换失败: ' + e.message);
    }
}


// ===== Skills System =====

let _skillsList = [];

async function loadSkills() {
    try {
        const r = await fetch('/api/skills');
        const d = await r.json();
        _skillsList = d.skills || [];
        renderSkillsList();
    } catch (e) {
        const el = document.getElementById('skillsList');
        if (el) el.innerHTML = '<div class="plugin-empty">加载失败: ' + e.message + '</div>';
    }
}

function renderSkillsList() {
    const el = document.getElementById('skillsList');
    if (!el) return;
    if (!_skillsList.length) {
        el.innerHTML = '<div class="plugin-empty">暂无技能</div>';
        return;
    }
    el.innerHTML = _skillsList.map(s => `
        <div class="plugin-item" id="skill-item-${s.id}">
            <div class="plugin-info">
                <div class="plugin-name">${escHtml(s.name)} ${s.builtin ? '<span style="font-size:10px;color:#888;background:#eee;padding:1px 5px;border-radius:3px;">内置</span>' : ''}</div>
                <div class="plugin-desc">${escHtml(s.description || '')}</div>
                <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">
                    触发词：${s.triggers && s.triggers.length ? s.triggers.map(t => `<code style="background:var(--bg-tertiary);padding:1px 4px;border-radius:3px;">${escHtml(t)}</code>`).join(' ') : '（无）'}
                </div>
            </div>
            <div class="plugin-actions">
                <label class="toggle-small" title="${s.enabled ? '点击禁用' : '点击启用'}">
                    <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleSkill('${s.id}', this.checked)">
                    <span class="ts-slider"></span>
                </label>
                ${!s.builtin ? `<button class="btn-secondary btn-xs" onclick="showSkillEditor('${s.id}')">✏️</button>` : ''}
                ${!s.builtin ? `<button class="btn-danger btn-xs" onclick="deleteSkill('${s.id}')">🗑️</button>` : ''}
            </div>
        </div>
    `).join('');
}

function showSkillEditor(skillId) {
    // 2026-07-14: 改为动态 modal，不再依赖 tab-skills.html 内联 #skillEditorArea
    const isEdit = !!skillId;
    let skillData = null;
    if (isEdit) {
        skillData = _skillsList.find(x => x.id === skillId);
    }

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    overlay.id = 'modal-skill-editor';
    overlay.innerHTML = `
        <div class="modal" style="max-width:560px">
            <div class="modal-header">
                <h4>${isEdit ? '✏️ 编辑技能' : '✏️ 新建技能'}</h4>
                <button class="modal-close" onclick="hideSkillEditor()">×</button>
            </div>
            <div class="modal-body">
                <input type="hidden" id="modal-editSkillId" value="${skillId || ''}">
                <div class="form-group">
                    <label>技能名称</label>
                    <input type="text" id="modal-editSkillName" placeholder="例如：✍️ 写作助手" value="${skillData ? skillData.name || '' : ''}">
                </div>
                <div class="form-group">
                    <label>描述</label>
                    <input type="text" id="modal-editSkillDesc" placeholder="简短描述这个技能的用途" value="${skillData ? skillData.description || '' : ''}">
                </div>
                <div class="form-group">
                    <label>触发词 <span style="font-size:11px;color:var(--text-secondary);">（逗号分隔）</span></label>
                    <input type="text" id="modal-editSkillTriggers" placeholder="例如：帮我写,写一篇,写作,润色" value="${skillData && skillData.triggers ? skillData.triggers.join(', ') : ''}">
                </div>
                <div class="form-group">
                    <label>系统提示词</label>
                    <textarea id="modal-editSkillPrompt" rows="6" placeholder="你是一个专业的写作助手..." style="width:100%;box-sizing:border-box;font-size:13px;">${skillData ? skillData.prompt || '' : ''}</textarea>
                </div>
                <div style="display:flex;gap:8px;">
                    <button class="btn-primary btn-sm" onclick="saveSkill()">💾 保存</button>
                    <button class="btn-secondary btn-sm" onclick="hideSkillEditor()">取消</button>
                    <button class="btn-secondary btn-sm" onclick="testSkillMatch()">🔍 测试匹配</button>
                </div>
                <div id="modal-skillTestResult" style="margin-top:8px;font-size:12px;color:var(--text-secondary);"></div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
}

function hideSkillEditor() {
    const modal = document.getElementById('modal-skill-editor');
    if (modal) modal.remove();
    // 兼容旧的内联编辑器残留
    const area = document.getElementById('skillEditorArea');
    if (area) area.style.display = 'none';
}

async function saveSkill() {
    const skillId = document.getElementById('modal-editSkillId').value;
    const name = document.getElementById('modal-editSkillName').value.trim();
    const description = document.getElementById('modal-editSkillDesc').value.trim();
    const triggersRaw = document.getElementById('modal-editSkillTriggers').value;
    const triggers = triggersRaw.split(',').map(t => t.trim()).filter(Boolean);
    const prompt = document.getElementById('modal-editSkillPrompt').value.trim();

    if (!name) { toast('技能名称不能为空'); return; }
    if (!prompt) { toast('系统提示词不能为空'); return; }

    try {
        let r;
        if (skillId) {
            r = await fetch(`/api/skills/${skillId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description, triggers, prompt })
            });
        } else {
            r = await fetch('/api/skills', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description, triggers, prompt })
            });
        }
        const d = await r.json();
        if (d.success) {
            toast('✅ 技能保存成功');
            hideSkillEditor();
            loadSkills();
        } else {
            toast('保存失败: ' + (d.error || ''));
        }
    } catch (e) {
        toast('保存失败: ' + e.message);
    }
}

async function toggleSkill(skillId, enabled) {
    try {
        const r = await fetch(`/api/skills/${skillId}/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        const d = await r.json();
        if (d.success) {
            toast(enabled ? '✅ 技能已启用' : '⏸️ 技能已禁用');
        } else {
            toast('操作失败: ' + (d.error || ''));
            loadSkills();
        }
    } catch (e) {
        toast('操作失败: ' + e.message);
    }
}

async function deleteSkill(skillId) {
    if (!confirm('确认删除这个技能？')) return;
    const allowed = await requirePasswordIfNeeded('deleteSkill');
    if (!allowed) return;
    try {
        const r = await fetch(`/api/skills/${skillId}`, { method: 'DELETE' });
        const d = await r.json();
        if (d.success) {
            toast('🗑️ 技能已删除');
            loadSkills();
        } else {
            toast('删除失败: ' + (d.error || ''));
        }
    } catch (e) {
        toast('删除失败: ' + e.message);
    }
}

async function testSkillMatch() {
    const msg = prompt('输入一条消息测试是否匹配当前编辑的技能（触发词）：');
    if (!msg) return;
    try {
        const r = await fetch('/api/skills/match', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg })
        });
        const d = await r.json();
        const el = document.getElementById('modal-skillTestResult');
        if (d.matched) {
            if (el) el.textContent = `✅ 匹配到技能「${d.skill.name}」`;
            if (el) el.style.color = '#22c55e';
        } else {
            if (el) el.textContent = '❌ 没有匹配到任何技能';
            if (el) el.style.color = '#ef4444';
        }
    } catch (e) {
        toast('测试失败: ' + e.message);
    }
}

// ===== Plugin Management (完善版) =====

async function loadInstalledPlugins() {
    try {
        const r = await fetch('/api/plugins');
        const d = await r.json();
        const plugins = d.plugins || [];
        const el = document.getElementById('installedPluginsList');
        if (!el) return;
        if (!plugins.length) {
            el.innerHTML = '<div class="plugin-empty">暂无已安装插件</div>';
            return;
        }
        el.innerHTML = plugins.map(p => `
            <div class="plugin-item">
                <div class="plugin-info">
                    <div class="plugin-name">${escHtml(p.name)} <span style="font-size:10px;color:#888;">v${escHtml(p.version || '?')}</span></div>
                    <div class="plugin-desc">${escHtml(p.description || '无描述')}</div>
                </div>
                <div class="plugin-actions">
                    <label class="toggle-small">
                        <input type="checkbox" ${p.enabled ? 'checked' : ''} onchange="togglePlugin('${escHtml(p.name)}', this.checked)">
                        <span class="ts-slider"></span>
                    </label>
                    <button class="btn-danger btn-xs" onclick="uninstallPlugin('${escHtml(p.name)}')">卸载</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        const el = document.getElementById('installedPluginsList');
        if (el) el.innerHTML = '<div class="plugin-empty">加载失败</div>';
    }
}

async function togglePlugin(name, enabled) {
    try {
        const url = enabled ? `/api/plugins/${name}/enable` : `/api/plugins/${name}/disable`;
        const r = await fetch(url, { method: 'POST' });
        const d = await r.json();
        toast(d.success ? (enabled ? '✅ 插件已启用' : '⏸️ 插件已禁用') : ('失败: ' + d.error));
    } catch (e) {
        toast('操作失败: ' + e.message);
    }
}

async function uninstallPlugin(name) {
    if (!confirm(`确认卸载插件 ${name}？`)) return;
    try {
        const r = await fetch(`/api/plugins/${name}/uninstall`, { method: 'DELETE' });
        const d = await r.json();
        if (d.success) { toast('🗑️ 插件已卸载'); loadInstalledPlugins(); }
        else toast('卸载失败: ' + d.error);
    } catch (e) {
        toast('卸载失败: ' + e.message);
    }
}

async function reloadPlugins() {
    try {
        const r = await fetch('/api/plugins/reload', { method: 'POST' });
        const d = await r.json();
        toast(d.success ? `✅ 已重载 ${d.count} 个插件` : '重载失败: ' + d.error);
        loadInstalledPlugins();
    } catch (e) {
        toast('重载失败: ' + e.message);
    }
}

async function installPluginFromCode() {
    const name = document.getElementById('pastePluginName').value.trim();
    const code = document.getElementById('pastePluginCode').value.trim();
    const result = document.getElementById('pluginInstallResult');
    if (!name) { toast('请输入插件名称'); return; }
    if (!code) { toast('请粘贴插件代码'); return; }
    if (result) result.textContent = '安装中...';
    try {
        const r = await fetch('/api/plugins/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, code })
        });
        const d = await r.json();
        if (d.success) {
            if (result) { result.textContent = '✅ 安装成功'; result.style.color = '#22c55e'; }
            toast('✅ 插件安装成功');
            document.getElementById('pastePluginCode').value = '';
            document.getElementById('pastePluginName').value = '';
            loadInstalledPlugins();
        } else {
            if (result) { result.textContent = '❌ ' + (d.error || '安装失败'); result.style.color = '#ef4444'; }
        }
    } catch (e) {
        if (result) { result.textContent = '❌ ' + e.message; result.style.color = '#ef4444'; }
    }
}

async function showPluginDemo() {
    try {
        const r = await fetch('/api/plugins/demo');
        const d = await r.json();
        if (d.success) {
            document.getElementById('pastePluginCode').value = d.demo_code;
            document.getElementById('pastePluginName').value = 'my_weather';
            toast('📋 示例代码已填入');
        }
    } catch (e) {
        toast('获取示例失败');
    }
}

function openPluginInstall() {
    openModal('pluginInstallModal');
}


// switchSettingsTab 已移至 app-settings.js，此处不再重复定义




// ============================================================
// 浏览模式：无需搜索词，像逛商店一样浏览技能列表
// ============================================================

/**
 * 浏览单个渠道：获取该渠道的热门技能列表
 * 当用户点击渠道 Tab 且搜索框为空时自动触发
 */
async function browseRHChannel(channel, category = '') {
    const resultsEl = document.getElementById('pluginMarketResults');
    const statusEl = document.getElementById('pluginMarketStatus');
    const statsEl = document.getElementById('rhSearchStats');

    const chInfo = RH_CHANNELS[channel] || { icon: '📦', label: channel };
    resultsEl.innerHTML = `<div class="rh-loading">${chInfo.icon} 正在浏览 ${chInfo.label} 热门技能...</div>`;
    if (statusEl) statusEl.textContent = '';
    if (statsEl) statsEl.innerHTML = '';

    try {
        const params = new URLSearchParams({ limit: '30' });
        if (category) params.set('category', category);

        const r = await fetch(`/api/resource-hub/browse/${channel}?${params.toString()}`);
        const d = await r.json();

        if (!d.success) {
            resultsEl.innerHTML = `<div class="rh-error">❌ ${escHtml(d.error || '浏览失败')}</div>`;
            return;
        }

        // 存储结果（兼容 search 的结果结构）
        window._rhState.currentResults = {
            success: true,
            total: d.count,
            by_channel: { [channel]: d.results },
            by_type: _groupByType(d.results),
            channels: [{ id: channel, label: d.channel_label, icon: d.channel_icon, count: d.count }],
            harvenhub_status: null,
        };

        // 显示统计
        if (statsEl) {
            statsEl.innerHTML = `${d.channel_icon} ${d.channel_label}: <b style="color:var(--primary)">${d.count}</b> 个技能`;
            if (category) {
                statsEl.innerHTML += ` · 分类: ${category}`;
            }
            statsEl.innerHTML += ` | <a href="#" onclick="event.preventDefault();resetRHBrowse()" style="font-size:11px;color:#888;">↩ 返回搜索</a>`;
        }

        // 分类标签（如果渠道支持）
        let categoryHtml = '';
        if (d.categories && d.categories.length) {
            categoryHtml = '<div style="margin-bottom:12px;display:flex;gap:6px;flex-wrap:wrap;">';
            categoryHtml += d.categories.map(c =>
                `<button class="btn-tag" onclick="browseRHChannel('${channel}','${c.key}')" 
                    style="--ch-color:#666;font-size:11px;">${c.name || c.nameEn}</button>`
            ).join('');
            categoryHtml += '</div>';
        }

        if (!d.count) {
            resultsEl.innerHTML = `
                <div class="rh-empty">
                    <div style="font-size:40px;margin-bottom:10px;">📭</div>
                    <div>${chInfo.label} 暂无技能</div>
                    <div style="font-size:12px;color:#888;margin-top:6px;">该渠道可能暂时没有可用技能</div>
                </div>`;
            return;
        }

        // 渲染结果
        let html = `<div class="rh-browse-header" style="margin-bottom:12px;font-size:13px;color:#888;">
            ${chInfo.icon} <b>${chInfo.label}</b> 热门技能 · <a href="${(RH_CHANNELS[channel] || {}).url || '#'}" target="_blank" style="color:var(--primary);">去官网了解更多 ↗</a>
        </div>`;
        html += categoryHtml;

        const byType = _groupByType(d.results);
        const typeOrder = ['plugin', 'skill', 'knowledge', 'tool', 'other'];
        const typeIcons = { plugin: '🔌', skill: '🎯', knowledge: '📚', tool: '🔧', other: '📦' };
        for (const t of typeOrder) {
            const items = byType[t];
            if (!items || !items.length) continue;
            html += `<div class="rh-type-section">
                <div class="rh-type-title">${typeIcons[t] || ''} ${items[0].resource_type_label || t} (${items.length})</div>`;
            html += items.map((item, i) => _renderRHResult(item, i, t)).join('');
            html += '</div>';
        }

        resultsEl.innerHTML = html;
    } catch (e) {
        resultsEl.innerHTML = `<div class="rh-error">❌ 浏览失败: ${escHtml(e.message)}</div>`;
    }
}

/**
 * 浏览所有支持浏览的渠道
 */
async function browseAllRHChannels() {
    const resultsEl = document.getElementById('pluginMarketResults');
    const statsEl = document.getElementById('rhSearchStats');

    resultsEl.innerHTML = '<div class="rh-loading">🌐 正在浏览全部渠道热门技能...</div>';
    if (statsEl) statsEl.innerHTML = '';

    try {
        const r = await fetch('/api/resource-hub/browse?limit=30');
        const d = await r.json();

        if (!d.success) {
            resultsEl.innerHTML = `<div class="rh-error">❌ ${escHtml(d.error || '浏览失败')}</div>`;
            return;
        }

        window._rhState.currentResults = {
            success: true,
            total: d.total,
            by_channel: d.by_channel,
            channels: d.channels,
            harvenhub_status: null,
        };

        // 统计栏
        if (statsEl) {
            statsEl.innerHTML = d.channels.map(ch => {
                const def = RH_CHANNELS[ch.id] || { icon: '📦', label: ch.label };
                return `<span style="margin-right:12px;font-size:12px;color:#888;">${def.icon} ${def.label}: <b style="color:var(--primary)">${ch.count}</b></span>`;
            }).join('') + `<span style="font-size:12px;color:#888;">| 共 <b>${d.total}</b> 个技能</span>`;
        }

        if (!d.total) {
            resultsEl.innerHTML = '<div class="rh-empty">📭 暂无可用技能</div>';
            return;
        }

        // 按渠道分组渲染
        let html = '';
        for (const ch of (d.channels || [])) {
            const items = d.by_channel?.[ch.id];
            if (!items || !items.length) continue;
            const def = RH_CHANNELS[ch.id] || { icon: '📦', label: ch.label };
            html += `<div class="rh-channel-section" style="margin-bottom:16px;">
                <div class="rh-channel-title" style="font-size:14px;font-weight:600;padding:8px 0;border-bottom:1px solid var(--border);margin-bottom:8px;">
                    ${def.icon} ${def.label} <span style="font-size:12px;color:#888;">(${items.length})</span>
                    <a href="${ch.id === 'skillhub' ? 'https://lightmake.site' : ch.id === 'clawhub' ? 'https://clawhub.com' : '#'}" target="_blank" style="font-size:11px;color:var(--primary);float:right;">去官网 ↗</a>
                </div>`;
            const byType = _groupByType(items);
            const typeOrder = ['plugin', 'skill', 'knowledge', 'tool', 'other'];
            const typeIcons = { plugin: '🔌', skill: '🎯', knowledge: '📚', tool: '🔧', other: '📦' };
            for (const t of typeOrder) {
                const typed = byType[t];
                if (!typed || !typed.length) continue;
                html += typed.map((item, i) => _renderRHResult(item, i, t)).join('');
            }
            html += '</div>';
        }

        resultsEl.innerHTML = html;
    } catch (e) {
        resultsEl.innerHTML = `<div class="rh-error">❌ 浏览失败: ${escHtml(e.message)}</div>`;
    }
}

/**
 * 重置浏览状态，回到搜索模式
 */
function resetRHBrowse() {
    window._rhState.currentResults = null;
    document.getElementById('pluginMarketSearch').value = '';
    document.getElementById('pluginMarketResults').innerHTML =
        '<div class="rh-placeholder">🔍 输入关键词搜索技能，或点击上方渠道 Tab 浏览热门技能</div>';
    const statsEl = document.getElementById('rhSearchStats');
    if (statsEl) statsEl.innerHTML = '';
}

/**
 * 将结果按 resource_type 分组
 */
function _groupByType(items) {
    const groups = {};
    for (const item of items) {
        const t = item.resource_type || 'other';
        if (!groups[t]) groups[t] = [];
        groups[t].push(item);
    }
    return groups;
}

// ===== End New Features =====





// ===== 推荐工具 =====
/**
 * 加载推荐工具列表（CLI-Anything Hub 等）
 * 显示在 tab-skills 顶部的 #recommendedTools 区域
 */
async function loadRecommendedTools() {
    const container = document.getElementById('recommendedTools');
    if (!container) return;

    let data = { tools: [] };
    try {
        // 检测 CLI-Anything 是否已安装
        const r = await fetch('/api/plugins/recommended');
        if (r.ok) {
            data = await r.json();
        }
    } catch (e) {
        // API 不可用时使用默认数据
        console.log('[RecommendedTools] API 不可达，使用默认列表:', e.message);
    }

    // 构建推荐工具列表（API 数据优先，否则用默认）
    const tools = (data && data.tools && data.tools.length > 0)
        ? data.tools
        : [{
            id: 'cli-anything-hub',
            name: 'CLI-Anything Hub',
            icon: '🎨',
            desc: '79个创意软件CLI封装，Apache 2.0协议',
            size: '基础包 ~5MB（按需扩展）',
            tip: '安装后可通过对话直接控制 Blender / GIMP / Inkscape 等软件。目标软件需自行安装。',
            pkg: 'cli-anything-hub',
            installed: false,
        }];

    let html = '<div class="setting-section-title">⭐ 推荐工具</div>';
    html += '<div class="setting-desc" style="margin-bottom:8px;font-size:13px;color:#bbb;">以下工具可扩展哈文的能力，一键安装即可在对话中使用</div>';

    for (const tool of tools) {
        const statusBadge = tool.installed
            ? '<span style="background:#22c55e;color:#000;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:500;">✅ 已安装</span>'
            : '<span style="background:#f59e0b;color:#000;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:500;">📦 未安装</span>';

        const btnHtml = tool.installed
            ? `<button class="btn-sm btn-secondary" disabled style="opacity:0.6;">已安装</button>`
            : `<button class="btn-sm btn-primary" onclick="installRecommendedTool('${tool.id}', '${tool.pkg}')">📥 安装</button>`;

        html += `
        <div class="plugin-card" style="border:1px solid #2a2a4a;border-radius:10px;padding:14px;margin-bottom:10px;background:linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);">
            <div style="display:flex;align-items:flex-start;gap:12px;">
                <div style="font-size:32px;flex-shrink:0;">${tool.icon}</div>
                <div style="flex:1;">
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                        <strong style="font-size:15px;color:#e0e0e0;">${tool.name}</strong>
                        ${statusBadge}
                    </div>
                    <p style="font-size:13px;color:#ccc;margin:4px 0;">${tool.desc}</p>
                    <p style="font-size:12px;color:#aaa;margin:4px 0;">📦 ${tool.size}</p>
                    <p style="font-size:12px;color:#fbbf24;margin:6px 0;font-weight:500;">⚠️ ${tool.tip}</p>
                </div>
                <div style="flex-shrink:0;">${btnHtml}</div>
            </div>
        </div>`;
    }

    container.innerHTML = html;
}

/**
 * 安装推荐工具
 */
async function installRecommendedTool(toolId, pkgName) {
    const btn = event.target;
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ 安装中...';

    try {
        const r = await fetch('/api/plugins/recommended/install', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tool_id: toolId, pkg: pkgName }),
        });
        const data = await r.json();

        if (data.success) {
            showToast(`✅ ${data.name || toolId} 安装成功！现在可以在对话中使用了`, 'success');
            // 刷新推荐工具列表
            setTimeout(() => loadRecommendedTools(), 1000);
        } else {
            showToast(`❌ 安装失败: ${data.error || '未知错误'}`, 'error');
            btn.disabled = false;
            btn.textContent = originalText;
        }
    } catch (e) {
        showToast(`❌ 安装出错: ${e.message}`, 'error');
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// ===== Window 全局导出（由自检补全）=====
window.loadRHChannels = loadRHChannels;
window.toggleRHChannel = toggleRHChannel;
window.setRHType = setRHType;
window.searchResourceHub = searchResourceHub;
window.showExternalPreview = showExternalPreview;
window.installRHItem = installRHItem;
window.installRHItemForce = installRHItemForce;
window.searchAlternative = searchAlternative;
window.scanRHItem = scanRHItem;
window.showAISkillGenerator = showAISkillGenerator;
window.generateAISkill = generateAISkill;
window.searchMarketPlugins = searchMarketPlugins;
window.installMarketPlugin = installMarketPlugin;
window.scanMarketPlugin = scanMarketPlugin;
window.searchPlugins = searchPlugins;
window.showPluginDetail = showPluginDetail;
window.scanPluginBeforeInstall = scanPluginBeforeInstall;
window.confirmInstallPlugin = confirmInstallPlugin;
window.loadOllamaModelSelect = loadOllamaModelSelect;
window.switchOllamaModel = switchOllamaModel;
window.loadRecommendedTools = loadRecommendedTools;
window.installRecommendedTool = installRecommendedTool;
window.loadSkills = loadSkills;
window.renderSkillsList = renderSkillsList;
window.showSkillEditor = showSkillEditor;
window.hideSkillEditor = hideSkillEditor;
window.saveSkill = saveSkill;
window.toggleSkill = toggleSkill;
window.deleteSkill = deleteSkill;
window.testSkillMatch = testSkillMatch;
window.loadInstalledPlugins = loadInstalledPlugins;
window.togglePlugin = togglePlugin;
window.uninstallPlugin = uninstallPlugin;
window.reloadPlugins = reloadPlugins;
window.installPluginFromCode = installPluginFromCode;
window.showPluginDemo = showPluginDemo;
window.openPluginInstall = openPluginInstall;
window.browseRHChannel = browseRHChannel;
window.browseAllRHChannels = browseAllRHChannels;
window.resetRHBrowse = resetRHBrowse;
