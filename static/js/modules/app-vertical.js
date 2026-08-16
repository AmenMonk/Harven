/**
 * Harven AI - 垂直模型画板管理器
 * Phase 1: 统一界面路由 + 模式切换器
 * Phase 2: 模型列表 + 适配器
 * Phase 3: 元数据驱动参数面板
 * Phase 4: 6个画板组件
 * Phase 5: UI 布局 JSON 驱动
 * Phase 6: 对话-画板联动
 */

// ═══════════════════════════════════════════════════════════════
// Phase 1: 模式切换 — 打开/关闭垂直画板
// ═══════════════════════════════════════════════════════════════

let _verticalPanelOpen = false;
let _currentVerticalModel = null;
let _verticalModels = [];

function toggleVerticalPanel() {
    if (_verticalPanelOpen) {
        closeVerticalPanel();
    } else {
        openVerticalPanel();
    }
}
window.toggleVerticalPanel = toggleVerticalPanel;

function openVerticalPanel() {
    const panel = document.getElementById('verticalPanel');
    const mainEl = document.getElementById('main');
    const chatWrapper = document.getElementById('chatWrapper');
    if (!panel || !mainEl) return;

    // ★ 问题1修复：打开垂直面板时，自动关闭外脑模式（互斥）
    // 外脑按钮取消高亮
    document.querySelectorAll('.qm-btn[data-mode="direct"]').forEach(function(btn) {
        btn.classList.remove('active');
    });
    // 如果当前是外脑模式，切回本地
    if (typeof currentQueryMode !== 'undefined' && currentQueryMode === 'direct') {
        // 不调用 setQueryMode（会切换对话），只改状态和UI
        currentQueryMode = 'kb_only';
        try { localStorage.setItem('harven_query_mode', 'kb_only'); } catch(e) {}
        // 本地按钮高亮
        document.querySelectorAll('.qm-btn[data-mode="kb_only"]').forEach(function(btn) {
            btn.classList.add('active');
        });
        const debug = document.getElementById('queryModeDebug');
        if (debug) { debug.textContent = '当前: 🏠本地'; debug.style.color = 'var(--text-secondary)'; }
        const input = document.getElementById('msgInput');
        if (input) input.placeholder = '输入消息... (Shift+Enter换行)';
    }

    // ★ 核心布局：切换为纵向 — 画板在上，对话框在下
    mainEl.style.flexDirection = 'column';

    panel.style.display = 'flex';
    panel.style.width = '100%';
    panel.style.height = '45%';
    panel.style.minHeight = '280px';
    panel.style.maxHeight = '55vh';
    panel.style.flexShrink = '0';
    panel.style.borderLeft = 'none';
    panel.style.borderBottom = '1px solid var(--border)';

    if (chatWrapper) {
        chatWrapper.style.flex = '1';
        chatWrapper.style.minHeight = '200px';
    }

    _verticalPanelOpen = true;

    const btn = document.getElementById('verticalModeBtn');
    if (btn) btn.style.background = 'var(--accent)';

    if (_verticalModels.length === 0) {
        loadVerticalModels();
    }

    // ★ H1+H2: 如果之前选过模型，自动恢复模型选择和面板
    setTimeout(function() {
        if (_currentVerticalModel) {
            var select = document.getElementById('verticalModelSelect');
            if (select) {
                select.value = _currentVerticalModel;
                // 恢复模型面板（包括历史区）
                onVerticalModelChange(_currentVerticalModel);
            }
        }
    }, 200);
}
window.openVerticalPanel = openVerticalPanel;

function closeVerticalPanel() {
    const panel = document.getElementById('verticalPanel');
    const mainEl = document.getElementById('main');
    const chatWrapper = document.getElementById('chatWrapper');
    if (!panel || !mainEl) return;

    mainEl.style.flexDirection = 'row';

    panel.style.display = 'none';
    panel.style.width = '';
    panel.style.height = '';
    panel.style.minHeight = '';
    panel.style.maxHeight = '';
    panel.style.borderLeft = '';

    if (chatWrapper) {
        chatWrapper.style.flex = '1';
        chatWrapper.style.minHeight = '';
    }

    // ★ H2: 不清除 _currentVerticalModel，重新打开时自动恢复
    _verticalPanelOpen = false;

    const btn = document.getElementById('verticalModeBtn');
    if (btn) btn.style.background = '';
}
window.closeVerticalPanel = closeVerticalPanel;

// ═══════════════════════════════════════════════════════════════
// Phase 2: 加载垂直模型列表
// ═══════════════════════════════════════════════════════════════

async function loadVerticalModels() {
    const select = document.getElementById('verticalModelSelect');
    if (!select) return;
    select.innerHTML = '<option value="">加载中...</option>';

    try {
        const r = await fetch('/api/vertical/models?' + Date.now(), { cache: 'no-store' });
        const d = await r.json();

        // 调试：打印原始响应
        console.log('[Vertical] API response:', JSON.stringify(d).substring(0, 300));
        console.log('[Vertical] response keys:', Object.keys(d));

        _verticalModels = d.models || [];
        select.innerHTML = '<option value="">选择垂直模型...</option>';

        if (_verticalModels.length === 0) {
            // 没有模型 — 显示详细诊断信息
            var diag = '⚠ 未找到垂直模型';
            if (!d.models && d.data) {
                diag += '（后端返回旧格式 data 字段，需重启Harven）';
            } else if (d.error) {
                diag += '（错误: ' + d.error + '）';
            } else if (r.status !== 200) {
                diag += '（HTTP ' + r.status + '，需重启Harven）';
            }
            select.innerHTML = '<option value="">' + diag + '</option>';
            // 在画板区域也显示提示
            var canvas = document.getElementById('verticalCanvas');
            if (canvas) {
                canvas.innerHTML =
                    '<div style="text-align:center;padding:30px;color:#e74c3c;font-size:13px;">' +
                    '<div style="font-size:24px;margin-bottom:8px;">⚠️</div>' +
                    '<div>垂直模型列表为空</div>' +
                    '<div style="color:var(--text-secondary);margin-top:8px;font-size:11px;">' +
                    '请确认：1) 已重启Harven 2) 浏览器已刷新(F5) 3) 控制台无红色错误</div>' +
                    '</div>';
            }
            return;
        }

        _verticalModels.forEach(function(m) {
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = (m.icon || '') + ' ' + (m.name || m.id);
            select.appendChild(opt);
        });

        console.log('[Vertical] 成功加载', _verticalModels.length, '个模型:', _verticalModels.map(function(m){return m.id}).join(', '));
    } catch(e) {
        select.innerHTML = '<option value="">加载失败</option>';
        console.error('[Vertical] 加载模型列表失败:', e);
        // 在画板上显示错误
        var canvas = document.getElementById('verticalCanvas');
        if (canvas) {
            canvas.innerHTML =
                '<div style="text-align:center;padding:30px;color:#e74c3c;font-size:12px;">' +
                '❌ 网络错误: ' + e.message + '<br><br>' +
                '<span style="color:var(--text-secondary)">检查: Harven是否运行? 端口7000?</span></div>';
        }
    }
}
window.loadVerticalModels = loadVerticalModels;

// ═══════════════════════════════════════════════════════════════
// Phase 3: 元数据驱动参数面板 — 选模型后渲染表单
// ═══════════════════════════════════════════════════════════════

function onVerticalModelChange(modelId) {
    _currentVerticalModel = modelId;
    if (!modelId) {
        document.getElementById('verticalCanvas').innerHTML =
            '<div style="text-align:center;color:var(--text-secondary);font-size:13px;padding:40px 0;">请选择一个垂直模型</div>';
        return;
    }

    const model = _verticalModels.find(function(m) { return m.id === modelId; });
    if (!model) return;

    renderVerticalCanvas(model);
}
window.onVerticalModelChange = onVerticalModelChange;

function renderVerticalCanvas(model) {
    const canvas = document.getElementById('verticalCanvas');
    const status = document.getElementById('verticalStatus');
    if (!canvas) return;

    // 根据 model.type 选择画板组件
    const canvasType = model.canvas_type || model.type || 'generic';
    const params = model.params || {};

    // ── 新布局：结果区在上（flex:1 占满），参数表单在下（贴底）──
    let formHtml = '';

    // 1) 顶部标题栏（紧凑）
    formHtml += '<div style="margin-bottom:8px;flex-shrink:0;">';
    formHtml += '<div style="font-size:14px;font-weight:600;">' +
                (model.icon || '') + ' ' + (model.name || model.id) + '</div>';
    formHtml += '<div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">' +
                (model.description || '') + '</div>';
    // ★ 2026-07-18: 许可证警告
    if (model.license_warning) {
        formHtml += '<div style="font-size:11px;color:#e74c3c;background:#fdf2f2;border:1px solid #f5c6cb;border-radius:6px;padding:4px 8px;margin-top:4px;">' +
                    model.license_warning + '</div>';
    }
    if (model.license && model.commercial_use) {
        formHtml += '<div style="font-size:10px;color:#27ae60;margin-top:2px;">许可: ' + model.license + ' ✅可商用</div>';
    }
    formHtml += '</div>';

    // 2) 结果展示区（占满中间区域）
    formHtml += '<div id="verticalPreview" style="flex:1;min-height:120px;border:1px dashed var(--border);border-radius:8px;padding:12px;text-align:center;color:var(--text-secondary);font-size:12px;overflow-y:auto;margin-bottom:8px;">执行后在此显示结果</div>';

    // 3) 底部参数表单 + 执行按钮（贴底，可滚动）
    formHtml += '<div id="verticalForm" style="flex-shrink:0;max-height:240px;overflow-y:auto;padding-top:8px;border-top:1px solid var(--border);">';
    for (const key in params) {
        const p = params[key];
        const label = p.label || key;
        const val = p.default !== undefined ? p.default : '';
        const ph = p.placeholder || '';

        if (p.type === 'select' && p.options) {
            formHtml += '<div style="margin-bottom:8px;">';
            formHtml += '<label style="font-size:11px;color:var(--text-secondary);display:block;margin-bottom:3px;">' + label + '</label>';
            formHtml += '<select id="vparam_' + key + '" style="width:100%;box-sizing:border-box;padding:5px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);font-size:12px;">';
            p.options.forEach(function(opt) {
                formHtml += '<option value="' + opt + '"' + (opt === val ? ' selected' : '') + '>' + opt + '</option>';
            });
            formHtml += '</select></div>';
        } else if (p.type === 'range') {
            formHtml += '<div style="margin-bottom:8px;">';
            formHtml += '<label style="font-size:11px;color:var(--text-secondary);display:block;margin-bottom:3px;">' + label + ': <span id="vrange_' + key + '">' + val + '</span></label>';
            formHtml += '<input type="range" id="vparam_' + key + '" min="' + (p.min || 0) + '" max="' + (p.max || 100) + '" value="' + val + '" oninput="document.getElementById(\'vrange_' + key + '\').textContent=this.value" style="width:100%;">';
            formHtml += '</div>';
        } else if (p.type === 'textarea') {
            formHtml += '<div style="margin-bottom:8px;">';
            formHtml += '<label style="font-size:11px;color:var(--text-secondary);display:block;margin-bottom:3px;">' + label + '</label>';
            formHtml += '<textarea id="vparam_' + key + '" placeholder="' + ph + '" style="width:100%;box-sizing:border-box;padding:5px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);font-size:12px;min-height:50px;resize:vertical;">' + val + '</textarea>';
            formHtml += '</div>';
        } else {
            formHtml += '<div style="margin-bottom:8px;">';
            formHtml += '<label style="font-size:11px;color:var(--text-secondary);display:block;margin-bottom:3px;">' + label + '</label>';
            formHtml += '<input type="text" id="vparam_' + key + '" value="' + val + '" placeholder="' + ph + '" style="width:100%;box-sizing:border-box;padding:5px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-primary);color:var(--text-primary);font-size:12px;">';
            formHtml += '</div>';
        }
    }
    formHtml += '</div>';

    // 4) 执行按钮（最底部）—— 2026-07-04 移除批量按钮（伪需求，12GB显存无法真并行）
    formHtml += '<div style="margin-top:4px;">';
    formHtml += '<button id="verticalExecBtn" onclick="executeVerticalModel()" style="width:100%;padding:10px;border:none;border-radius:8px;background:var(--accent);color:#fff;font-size:13px;cursor:pointer;font-weight:600;">▶ 执行</button>';
    formHtml += '</div>';

    // 5) 历史记录折叠区（2026-07-04 新增）
    formHtml += '<div id="verticalHistorySection" style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px;">';
    formHtml += '<div onclick="toggleVerticalHistory()" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:4px 0;">';
    formHtml += '<span style="font-size:12px;font-weight:600;color:var(--text-secondary);">📜 历史 <span id="verticalHistoryCount" style="color:var(--text-tertiary);">(0)</span></span>';
    formHtml += '<span id="verticalHistoryToggle" style="font-size:12px;color:var(--text-tertiary);">展开 ▼</span>';
    formHtml += '</div>';
    formHtml += '<div id="verticalHistoryList" style="display:none;max-height:200px;overflow-y:auto;margin-top:6px;"></div>';
    formHtml += '</div>';

    // ── 设置容器为 flex column 布局 ──
    canvas.style.display = 'flex';
    canvas.style.flexDirection = 'column';
    canvas.innerHTML = formHtml;
    if (status) status.textContent = '已选择: ' + (model.name || model.id);

    // 加载历史记录
    loadVerticalHistory(model.id);
}

// ═══════════════════════════════════════════════════════════════
// ★ 2026-07-03: 智能预填 — 对话框LLM提取参数后自动填入表单
// ═══════════════════════════════════════════════════════════════
function handleVerticalPrefill(data) {
    console.log('[Vertical] 收到预填指令:', data);
    if (!data || !data.model_id) return;

    // 1) 打开垂直面板（如果未开）
    if (!_verticalPanelOpen) {
        openVerticalPanel();
    }

    // 2) 切换到对应模型
    const select = document.getElementById('verticalModelSelect');
    if (select) {
        select.value = data.model_id;
        // 触发 change 事件渲染表单
        if (typeof onVerticalModelChange === 'function') {
            onVerticalModelChange(data.model_id);
        }
    }

    // 3) 等待表单渲染完成后填入参数
    setTimeout(function() {
        const params = data.params || {};
        let filledCount = 0;
        for (const key in params) {
            const input = document.getElementById('vparam_' + key);
            if (!input) continue;
            const val = params[key];
            if (input.tagName === 'SELECT') {
                // 匹配最接近的option
                const opts = Array.from(input.options);
                const exact = opts.find(o => o.value === val || o.textContent === val);
                if (exact) {
                    input.value = exact.value;
                } else {
                    // 模糊匹配：包含关系
                    const fuzzy = opts.find(o => o.value.indexOf(val) > -1 || val.indexOf(o.value) > -1);
                    if (fuzzy) input.value = fuzzy.value;
                }
                filledCount++;
            } else if (input.type === 'range') {
                const numVal = parseFloat(val);
                if (!isNaN(numVal)) {
                    input.value = numVal;
                    const rangeLabel = document.getElementById('vrange_' + key);
                    if (rangeLabel) rangeLabel.textContent = numVal;
                }
                filledCount++;
            } else if (input.tagName === 'TEXTAREA') {
                input.value = val;
                filledCount++;
            } else {
                input.value = val;
                filledCount++;
            }
        }
        console.log('[Vertical] 预填完成:', filledCount, '个参数');

        // 4) 闪烁执行按钮提示用户
        const execBtn = document.getElementById('verticalExecBtn');
        if (execBtn) {
            execBtn.style.animation = 'pulse 1s ease-in-out 3';
            setTimeout(function() { execBtn.style.animation = ''; }, 3000);
        }
    }, 200);

    // 5) toast 提示
    if (typeof toast === 'function') {
        toast((data.model_icon || '🎯') + ' 参数已填好，点上方【执行】生成', 5000);
    }
}
window.handleVerticalPrefill = handleVerticalPrefill;

// ═══════════════════════════════════════════════════════════════
// Phase 2: 执行垂直模型
// ═══════════════════════════════════════════════════════════════

async function executeVerticalModel() {
    if (!_currentVerticalModel) return;

    const btn = document.getElementById('verticalExecBtn');
    const preview = document.getElementById('verticalPreview');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 执行中...'; }

    // 先获取模型信息
    const model = _verticalModels.find(function(m) { return m.id === _currentVerticalModel; });

    // ★ 2026-07-12: 根据模型类型显示不同提示
    var loadingMsg = '⏳ 正在执行...';
    if (model) {
        var mtype = model.type || model.canvas_type || '';
        if (mtype.indexOf('medical') > -1 || mtype === 'chat') loadingMsg = '🏥 正在回答...';
        else if (mtype.indexOf('music') > -1) loadingMsg = '🎵 正在生成音乐...（可能需要1-3分钟）';
        else if (mtype.indexOf('image') > -1) loadingMsg = '🎨 正在生成图片...';
        else if (mtype.indexOf('speech') > -1 || mtype.indexOf('tts') > -1) loadingMsg = '🗣️ 正在生成语音...';
        else if (mtype.indexOf('code') > -1) loadingMsg = '💻 正在生成代码...';
        else if (mtype.indexOf('data') > -1) loadingMsg = '📊 正在分析数据...';
        else if (mtype.indexOf('translation') > -1) loadingMsg = '🌐 正在翻译...';
    }
    if (preview) preview.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);font-size:14px;">' + loadingMsg + '</div>';

    // 收集参数
    const params = {};
    if (model && model.params) {
        for (const key in model.params) {
            const el = document.getElementById('vparam_' + key);
            if (el) {
                params[key] = el.type === 'number' || el.type === 'range' ? parseFloat(el.value) : el.value;
            }
        }
    }

    try {
        const r = await fetch('/api/vertical/execute', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                model_id: _currentVerticalModel,
                params: params,
                action: 'generate'
            })
        });
        const d = await r.json();

        if (d.success) {
            renderVerticalResult(d, model);
            // ★ 2026-07-04: 执行成功后刷新历史记录
            setTimeout(function() {
                if (_currentVerticalModel) loadVerticalHistory(_currentVerticalModel);
            }, 500);
        } else {
            if (preview) preview.innerHTML = '<div style="color:#ef4444;">❌ ' + (d.error || '执行失败') + '</div>';
        }
    } catch(e) {
        if (preview) preview.innerHTML = '<div style="color:#ef4444;">❌ 网络错误: ' + e.message + '</div>';
    } finally {
        // ★ 2026-07-04: 用 finally 确保按钮一定恢复（无论成功/失败/超时）
        if (btn) { btn.disabled = false; btn.textContent = '▶ 执行'; }
    }
}
window.executeVerticalModel = executeVerticalModel;

// ═══════════════════════════════════════════════════════════════
// Phase 3.5: 批量执行 — 2026-07-04 已移除（伪需求，12GB显存无法真并行）
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
// Phase 3.6: 历史记录（2026-07-04 新增）
// ═══════════════════════════════════════════════════════════════

let _verticalHistoryLoaded = false;

async function loadVerticalHistory(modelId) {
    try {
        const resp = await fetch('/api/vertical/history?limit=20' + (modelId ? '&model_id=' + encodeURIComponent(modelId) : ''));
        const data = await resp.json();
        if (!data.success) return;

        const list = document.getElementById('verticalHistoryList');
        const countEl = document.getElementById('verticalHistoryCount');
        if (!list) return;

        const history = data.history || [];
        if (countEl) countEl.textContent = '(' + history.length + ')';

        if (history.length === 0) {
            list.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-tertiary);font-size:11px;">暂无历史记录</div>';
            return;
        }

        let html = '';
        history.forEach(function(item) {
            const params = item.params || {};
            // 参数摘要
            const paramSummary = Object.keys(params).map(function(k) {
                return k + '=' + params[k];
            }).join(' · ');

            // 文件类型渲染
            // ★ 2026-07-12: 对话型模型（华佗/翻译/代码/数据分析）不显示"查看文件"
            let mediaHtml = '';
            var _modelId = (item.model_id || '').toLowerCase();
            var _isChatModel = ['huatuo', 'translation', 'code-generator', 'data-analysis'].some(function(k) { return _modelId.indexOf(k) > -1; });
            if (item.file_path && !_isChatModel) {
                if (item.file_type === 'audio') {
                    mediaHtml = '<audio controls src="' + item.file_path + '" style="width:100%;height:28px;margin-top:4px;"></audio>';
                } else if (item.file_type === 'image') {
                    mediaHtml = '<img src="' + item.file_path + '" style="max-width:100%;max-height:80px;border-radius:4px;margin-top:4px;" />';
                } else {
                    mediaHtml = '<a href="' + item.file_path + '" target="_blank" style="font-size:11px;color:var(--accent);">查看文件</a>';
                }
            }

            const time = item.created_at ? String(item.created_at).substring(5, 16) : '';

            html += '<div style="padding:8px;border-bottom:1px solid var(--border);" data-history-id="' + item.id + '">' +
                '<div style="display:flex;justify-content:space-between;align-items:start;">' +
                '<div style="flex:1;min-width:0;">' +
                '<div style="font-size:11px;color:var(--text-secondary);">' + (item.model_name || item.model_id) + '</div>' +
                '<div style="font-size:10px;color:var(--text-tertiary);margin:2px 0;word-break:break-all;">' + paramSummary + '</div>' +
                '<div style="font-size:10px;color:var(--text-tertiary);">' + time + '</div>' +
                mediaHtml +
                '</div>' +
                '<div style="display:flex;flex-direction:column;gap:4px;margin-left:8px;">' +
                '<button onclick="reuseVerticalHistory(' + item.id + ')" title="重用参数" style="padding:3px 6px;font-size:10px;border:1px solid var(--border);border-radius:4px;background:var(--bg-primary);color:var(--text-primary);cursor:pointer;">🔁</button>' +
                '<button onclick="deleteVerticalHistory(' + item.id + ')" title="删除" style="padding:3px 6px;font-size:10px;border:1px solid var(--border);border-radius:4px;background:var(--bg-primary);color:#ef4444;cursor:pointer;">🗑</button>' +
                '</div>' +
                '</div>' +
                '</div>';
        });
        list.innerHTML = html;
        _verticalHistoryLoaded = true;
    } catch (e) {
        console.error('[Vertical] 加载历史失败:', e);
    }
}
window.loadVerticalHistory = loadVerticalHistory;

function toggleVerticalHistory() {
    const list = document.getElementById('verticalHistoryList');
    const toggle = document.getElementById('verticalHistoryToggle');
    if (!list) {
        // ★ H3: 历史区不存在，可能没选模型
        toast('请先选择一个垂直模型');
        return;
    }
    if (list.style.display === 'none' || list.style.display === '') {
        list.style.display = 'block';
        if (toggle) toggle.textContent = '收起 ▲';
        // ★ H3: 强制重新加载
        if (_currentVerticalModel) {
            loadVerticalHistory(_currentVerticalModel);
        }
    } else {
        list.style.display = 'none';
        if (toggle) toggle.textContent = '展开 ▼';
    }
}
window.toggleVerticalHistory = toggleVerticalHistory;

function reuseVerticalHistory(historyId) {
    const list = document.getElementById('verticalHistoryList');
    if (!list) return;
    const item = list.querySelector('[data-history-id="' + historyId + '"]');
    if (!item) return;

    // 从历史记录div的数据属性找参数（重新fetch确保准确）
    fetch('/api/vertical/history?limit=50' + (_currentVerticalModel ? '&model_id=' + encodeURIComponent(_currentVerticalModel) : ''))
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.success) return;
            const record = (data.history || []).find(function(h) { return h.id === historyId; });
            if (!record || !record.params) return;

            // 把参数填回表单
            Object.keys(record.params).forEach(function(key) {
                const el = document.getElementById('vparam_' + key);
                if (el) {
                    el.value = record.params[key];
                    // 触发 range 的显示更新
                    if (el.type === 'range') {
                        const display = document.getElementById('vrange_' + key);
                        if (display) display.textContent = record.params[key];
                    }
                }
            });

            if (typeof toast === 'function') toast('参数已填入，可调整后执行');
            else console.log('[Vertical] 参数已填入');
        })
        .catch(function(e) { console.error('[Vertical] 重用参数失败:', e); });
}
window.reuseVerticalHistory = reuseVerticalHistory;

async function deleteVerticalHistory(historyId) {
    if (!confirm('确认删除这条历史记录？')) return;
    try {
        const resp = await fetch('/api/vertical/history/' + historyId, { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) {
            if (_currentVerticalModel) loadVerticalHistory(_currentVerticalModel);
        } else {
            alert('删除失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        console.error('[Vertical] 删除历史失败:', e);
    }
}
window.deleteVerticalHistory = deleteVerticalHistory;

// ═══════════════════════════════════════════════════════════════
// Phase 4: 画板组件 — 根据类型渲染结果
// ═══════════════════════════════════════════════════════════════

function renderVerticalResult(result, model) {
    const preview = document.getElementById('verticalPreview');
    if (!preview) return;

    const canvasType = model.canvas_type || model.type || 'generic';
    const data = result.data || result.output || {};
    const file = data.file || data.url || '';

    // 清空预览区的初始提示
    preview.innerHTML = '';
    preview.style.textAlign = 'left';

    // 先尝试内置渲染器
    switch(canvasType) {
        case 'image_generation':
            renderImageCanvas(preview, file, data);
            break;
        case 'music_generation':
            renderMusicCanvas(preview, file, data);
            break;
        case 'speech_synthesis':
            renderAudioCanvas(preview, file, data);
            break;
        case 'code_generation':
            renderCodeCanvas(preview, data);
            break;
        case 'data_analysis':
            renderDataCanvas(preview, data);
            break;
        case 'translation':
            renderTranslationCanvas(preview, data);
            break;
        case 'chat':
        case 'medical_qa':
            renderChatCanvas(preview, data, model);
            break;
        default:
            // 尝试动态渲染器
            var dynRendererName = 'renderDynamic_' + canvasType.replace(/-/g, '_');
            if (typeof window[dynRendererName] === 'function') {
                window[dynRendererName](preview, data);
            } else {
                renderGenericCanvas(preview, data);
            }
    }
}

// --- 图片画板 ---
function renderImageCanvas(container, file, data) {
    if (file) {
        container.innerHTML = '<img src="' + file + '" style="width:100%;border-radius:8px;" />';
    } else {
        container.innerHTML = '<div>图片生成完成，但未返回文件路径</div>';
    }
}

// --- 音乐画板 ---
function renderMusicCanvas(container, file, data) {
    if (file) {
        container.innerHTML =
            '<div style="margin-bottom:8px;font-size:12px;color:var(--text-secondary);">🎵 生成完毕 ' + (data.duration || '') + '秒</div>' +
            '<audio controls src="' + file + '" style="width:100%;"></audio>';
    } else {
        container.innerHTML = '<div>音乐生成完成，但未返回文件路径</div>';
    }
}

// --- 语音画板 ---
function renderAudioCanvas(container, file, data) {
    if (file) {
        container.innerHTML =
            '<div style="margin-bottom:8px;font-size:12px;color:var(--text-secondary);">🗣️ 语音合成完毕</div>' +
            '<audio controls src="' + file + '" style="width:100%;"></audio>';
    } else {
        container.innerHTML = '<div>语音合成完成，但未返回文件路径</div>';
    }
}

// --- 代码画板 ---
function renderCodeCanvas(container, data) {
    const code = data.code || data.output || '';
    const lang = data.language || 'python';
    container.innerHTML =
        '<div style="margin-bottom:8px;font-size:12px;color:var(--text-secondary);">💻 代码生成完毕</div>' +
        '<pre style="background:var(--bg-tertiary);padding:12px;border-radius:8px;overflow-x:auto;font-size:11px;max-height:300px;"><code>' +
        escapeHtml(code) + '</code></pre>';
}

// --- 数据分析画板 ---
function renderDataCanvas(container, data) {
    const chart = data.chart || '';
    const table = data.table || data.output || '';
    let html = '<div style="margin-bottom:8px;font-size:12px;color:var(--text-secondary);">📊 数据分析完毕</div>';
    if (chart) {
        html += '<div style="margin-bottom:8px;"><img src="' + chart + '" style="width:100%;border-radius:8px;" /></div>';
    }
    if (table) {
        html += '<pre style="background:var(--bg-tertiary);padding:8px;border-radius:6px;font-size:11px;overflow-x:auto;max-height:200px;">' + escapeHtml(table) + '</pre>';
    }
    container.innerHTML = html;
}

// --- 翻译画板 ---
function renderTranslationCanvas(container, data) {
    const original = data.original || '';
    const translated = data.translated || data.output || '';
    container.innerHTML =
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">' +
        '<div><div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">原文</div>' +
        '<div style="background:var(--bg-tertiary);padding:8px;border-radius:6px;font-size:12px;min-height:60px;">' + escapeHtml(original) + '</div></div>' +
        '<div><div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">译文</div>' +
        '<div style="background:var(--bg-tertiary);padding:8px;border-radius:6px;font-size:12px;min-height:60px;">' + escapeHtml(translated) + '</div></div>' +
        '</div>';
}

// --- 通用画板 ---
function renderGenericCanvas(container, data) {
    const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    container.innerHTML = '<pre style="background:var(--bg-tertiary);padding:12px;border-radius:8px;font-size:11px;overflow-x:auto;max-height:300px;">' + escapeHtml(text) + '</pre>';
}

// ★ 2026-07-12: 对话/医疗问答画板
function renderChatCanvas(container, data, model) {
    var output = data.output || data.content || data.text || '';
    if (!output) {
        if (data.error) {
            container.innerHTML = '<div style="color:#ef4444;padding:16px;">❌ ' + escapeHtml(data.error) + '</div>';
        } else {
            container.innerHTML = '<div style="color:var(--text-secondary);padding:16px;">未返回内容</div>';
        }
        return;
    }

    // 去掉 ## Thinking 部分（只保留最终回答）
    var finalResponse = output;
    var finalIdx = output.indexOf('## Final Response');
    if (finalIdx > -1) {
        finalResponse = output.substring(finalIdx + '## Final Response'.length).trim();
    } else {
        // 也尝试去掉纯 Thinking 标记
        var thinkingEnd = output.indexOf('\n\n', output.indexOf('Thinking'));
        if (thinkingEnd > -1 && output.indexOf('Thinking') < 50) {
            // 如果开头有Thinking标记 → 去掉
        }
    }

    // 简单markdown转HTML
    var html = escapeHtml(finalResponse);
    // 标题
    html = html.replace(/^### (.+)$/gm, '<h4 style="margin:12px 0 6px;color:var(--text-primary);">$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3 style="margin:14px 0 8px;color:var(--text-primary);">$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2 style="margin:16px 0 10px;color:var(--text-primary);">$1</h2>');
    // 加粗
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text-primary);">$1</strong>');
    // 列表
    html = html.replace(/^(\d+)\. (.+)$/gm, '<div style="margin:4px 0 4px 20px;"><span style="color:#22c55e;font-weight:600;">$1.</span> $2</div>');
    html = html.replace(/^[-*] (.+)$/gm, '<div style="margin:4px 0 4px 20px;">• $1</div>');
    // 段落
    html = html.replace(/\n\n/g, '</p><p style="margin:8px 0;">');
    html = '<p style="margin:8px 0;">' + html + '</p>';

    var modelName = (model && model.name) ? model.name : 'AI助手';
    var modelIcon = (model && model.icon) ? model.icon : '🤖';

    container.innerHTML =
        '<div style="background:rgba(100,116,139,0.06);border-radius:12px;padding:20px;margin-bottom:12px;">' +
            '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid rgba(100,116,139,0.15);">' +
                modelIcon + ' <strong>' + escapeHtml(modelName) + '</strong> 回答：' +
            '</div>' +
            '<div style="font-size:16px;line-height:1.8;color:var(--text-primary);">' + html + '</div>' +
        '</div>';
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ═══════════════════════════════════════════════════════════════
// Phase 6: 对话-画板联动 — 从对话消息中提取参数填充画板
// ═══════════════════════════════════════════════════════════════

function syncFromConversation(message, modelId) {
    if (!_verticalPanelOpen || !modelId) return;
    const model = _verticalModels.find(function(m) { return m.id === modelId; });
    if (!model || !model.params) return;

    // 智能参数提取：从用户消息中提取参数
    // 1. 先检查模型定义中的 extract_regex
    // 2. 再用通用关键词匹配
    const msg = message.toLowerCase();
    let hasMatch = false;

    for (const key in model.params) {
        const p = model.params[key];
        if (p.extract_regex) {
            try {
                const re = new RegExp(p.extract_regex, 'i');
                const m = msg.match(re);
                if (m && m[1]) {
                    const el = document.getElementById('vparam_' + key);
                    if (el) { el.value = m[1]; hasMatch = true; }
                }
            } catch(e) {}
        }
    }

    // 通用智能提取
    if (!hasMatch && model.params) {
        // 数字提取：如 "15秒" "20步" "512宽"
        for (const key in model.params) {
            const p = model.params[key];
            if (p.type === 'range') {
                const patterns = [
                    new RegExp(key + '[^0-9]*(\\d+)', 'i'),
                    new RegExp('(\\d+)\\s*' + (p.label || key), 'i'),
                ];
                for (const re of patterns) {
                    const m = msg.match(re);
                    if (m && m[1]) {
                        const val = parseFloat(m[1]);
                        if (val >= (p.min || 0) && val <= (p.max || 9999)) {
                            const el = document.getElementById('vparam_' + key);
                            if (el) {
                                el.value = val;
                                const rangeLabel = document.getElementById('vrange_' + key);
                                if (rangeLabel) rangeLabel.textContent = val;
                                hasMatch = true;
                            }
                            break;
                        }
                    }
                }
            }
            // select 类型：关键词匹配
            if (p.type === 'select' && p.options) {
                for (const opt of p.options) {
                    if (opt !== 'auto' && msg.includes(opt.toLowerCase())) {
                        const el = document.getElementById('vparam_' + key);
                        if (el) { el.value = opt; hasMatch = true; }
                        break;
                    }
                }
            }
            // textarea/text：直接用消息内容作为 prompt
            if (p.type === 'textarea' || p.type === 'text') {
                if (key === 'prompt' || key === 'text' || key === 'description' || key === 'input') {
                    const el = document.getElementById('vparam_' + key);
                    if (el && !el.value) { el.value = message; hasMatch = true; }
                }
            }
        }
    }

    // 如果有匹配，闪烁提示
    if (hasMatch) {
        const form = document.getElementById('verticalForm');
        if (form) {
            form.style.transition = 'background 0.3s';
            form.style.background = 'var(--accent)';
            setTimeout(function() { form.style.background = ''; }, 600);
        }
    }
}
window.syncFromConversation = syncFromConversation;

// ═══════════════════════════════════════════════════════════════
// Phase 5: 动态画板渲染器加载
// ═══════════════════════════════════════════════════════════════

async function loadDynamicCanvasRenderers() {
    try {
        const r = await fetch('/api/vertical/canvas-renderers');
        const d = await r.json();
        if (d.renderers) {
            for (const canvasType in d.renderers) {
                const jsCode = d.renderers[canvasType];
                try {
                    // 安全注入渲染器代码
                    eval(jsCode);
                } catch(e) {
                    console.error('[Vertical] 加载动态画板渲染器失败 ' + canvasType + ':', e);
                }
            }
            if (d.count > 0) {
                console.log('[Vertical] 已加载 ' + d.count + ' 个动态画板渲染器');
            }
        }
    } catch(e) {
        console.error('[Vertical] 获取动态画板渲染器列表失败:', e);
    }
}
window.loadDynamicCanvasRenderers = loadDynamicCanvasRenderers;

// ═══════════════════════════════════════════════════════════════
// Phase 6: 适配器管理 UI
// ═══════════════════════════════════════════════════════════════

async function autoGenerateAdapter(modelId, modelName, docText, docPath) {
    try {
        const r = await fetch('/api/vertical/auto-generate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                model_id: modelId,
                model_name: modelName || modelId,
                doc_text: docText || '',
                doc_path: docPath || '',
                auto_register: true
            })
        });
        const d = await r.json();
        if (d.success) {
            // 刷新模型列表
            await loadVerticalModels();
            console.log('[Vertical] 适配器自动生成成功:', d.message);
            return d;
        } else {
            console.error('[Vertical] 适配器自动生成失败:', d.error);
            return d;
        }
    } catch(e) {
        console.error('[Vertical] 自动生成适配器网络错误:', e);
        return {success: false, error: e.message};
    }
}
window.autoGenerateAdapter = autoGenerateAdapter;

async function deleteVerticalAdapter(adapterId) {
    try {
        const r = await fetch('/api/vertical/adapters/' + adapterId, {method: 'DELETE'});
        const d = await r.json();
        if (d.success) {
            await loadVerticalModels();
            console.log('[Vertical] 适配器已删除:', adapterId);
        }
        return d;
    } catch(e) {
        console.error('[Vertical] 删除适配器失败:', e);
        return {success: false, error: e.message};
    }
}
window.deleteVerticalAdapter = deleteVerticalAdapter;

async function reloadVerticalAdapter(adapterId) {
    try {
        const r = await fetch('/api/vertical/adapters/' + adapterId + '/reload', {method: 'POST'});
        const d = await r.json();
        if (d.success) {
            await loadVerticalModels();
            console.log('[Vertical] 适配器已热重载:', adapterId);
        }
        return d;
    } catch(e) {
        console.error('[Vertical] 热重载适配器失败:', e);
        return {success: false, error: e.message};
    }
}
window.reloadVerticalAdapter = reloadVerticalAdapter;

// 页面加载时初始化动态画板渲染器
document.addEventListener('DOMContentLoaded', function() {
    loadDynamicCanvasRenderers();
});
