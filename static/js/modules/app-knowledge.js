/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — KNOWLEDGE module ===== */
function checkKbAutoImport() {
    fetch('/api/kb/auto-import/status')
        .then(r => r.json())
        .then(status => {
            if (status.running) {
                // total=0 说明后端线程刚启动还没扫完目录，或状态回退，清理可能残留的僵尸进度条
                if (!status.total || status.total === 0) {
                    var zombieBar = document.getElementById('kbImportBar');
                    if (zombieBar) zombieBar.remove();
                    setTimeout(checkKbAutoImport, 1000);
                    return;
                }
                showKbImportProgress(status);
                // 导入中，每3秒刷新
                const timer = setInterval(() => {
                    fetch('/api/kb/auto-import/status').then(r => r.json()).then(s => {
                        if (!s.running) {
                            clearInterval(timer);
                            if (s.done > 0) {
                                const okList = s.results.filter(r => r.status === 'ok');
                                const errList = s.results.filter(r => r.status === 'error' || r.status === 'unsupported' || r.status === 'skipped');
                                let msg = `📚 知识库导入完成: ${okList.length} 成功`;
                                if (errList.length > 0) msg += `, ${errList.length} 失败`;
                                toast(msg);
                                // 有失败的，3秒后展示详情
                                if (errList.length > 0) {
                                    const allDetails = s.results.map(r => {
                                        if (r.status === 'ok') return `✅ ${r.filename}: ${r.chunks}段`;
                                        const reason = r.error || r.message || '未知原因';
                                        return `❌ ${r.filename}: ${reason.length > 60 ? reason.substring(0,60) + '...' : reason}`;
                                    });
                                    setTimeout(() => showKbImportDetails(allDetails), 3000);
                                }
                                loadKbList();
                            }
                        } else {
                            showKbImportProgress(s);
                        }
                    });
                }, 3000);
            } else if (status.done > 0 && status.finished_at) {
                // 上次导入已完成，显示结果摘要
                const ok = status.results.filter(r => r.status === 'ok');
                const fail = status.results.filter(r => r.status === 'error');
                if (ok.length > 0) {
                    const totalChunks = ok.reduce((sum, r) => sum + (r.chunks || 0), 0);
                    toast(`📚 知识库: ${ok.length} 个文件, ${totalChunks} 段${fail.length ? `, ${fail.length} 个失败` : ''}`);
                }
            }
        })
        .catch(() => {});
}

function showKbImportProgress(status) {
    // total=0 说明还没有文件，不展示
    if (!status.total || status.total === 0) return;
    // done=0 说明还没开始导入，等有进展后再显示，避免长时间卡在 "0/N"
    if (!status.done || status.done === 0) {
        setTimeout(function() { checkKbAutoImport(); }, 2000);
        return;
    }
    // 更新已有进度条
    const existing = document.getElementById('kbImportBar');
    if (existing) {
        existing.textContent = '📚 知识库导入中: ' + status.done + '/' + status.total + (status.current_file ? ' (' + status.current_file + ')' : '');
        return;
    }
    const bar = document.createElement('div');
    bar.id = 'kbImportBar';
    // 位置改到右上角，避免遮挡底部输入框
    bar.style.cssText = 'position:fixed;top:16px;right:20px;transform:none;background:#1a1a2e;color:#fff;padding:6px 14px;border-radius:20px;font-size:12px;z-index:9999;box-shadow:0 2px 12px rgba(0,0,0,.3);max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
    bar.textContent = '📚 知识库导入中: ' + status.done + '/' + status.total + (status.current_file ? ' (' + status.current_file + ')' : '');
    document.body.appendChild(bar);
    // 完成后移除，total=0 也移除（后端可能状态回退）
    const timer = setInterval(() => {
        fetch('/api/kb/auto-import/status').then(r => r.json()).then(s => {
            if (!s.running || !s.total || s.total === 0) {
                clearInterval(timer);
                bar.remove();
            } else {
                bar.textContent = '📚 知识库导入中: ' + s.done + '/' + s.total + (s.current_file ? ' (' + s.current_file + ')' : '');
            }
        }).catch(() => {
            // 网络异常也清理掉，避免僵尸进度条
            clearInterval(timer);
            bar.remove();
        });
    }, 3000);
}

// ===== Knowledge Base =====
async function uploadKbFiles(files) {
    // ★ 2026-07-05: 添加加载提示，让用户看到正在处理
    const fileCount = files.length;
    const fileNames = Array.from(files).map(f => f.name).join(', ');
    toast(`📚 正在导入 ${fileCount} 个文件到知识库...`);

    // 显示处理中状态
    const uploadArea = document.getElementById('kbUpload');
    let originalHtml = '';
    if (uploadArea) {
        originalHtml = uploadArea.innerHTML;
        uploadArea.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);">⏳ 正在导入到知识库，请稍候...<br><small>' + fileNames + '</small></div>';
    }

    const formData = new FormData();
    Array.from(files).forEach(f => formData.append('files', f));
    try {
        const r = await fetch('/api/knowledge/upload', { method: 'POST', body: formData });
        const data = await r.json();
        if (Array.isArray(data)) {
            // 详细结果：成功/失败/原因
            let okCount = 0, failCount = 0;
            const details = [];
            for (const d of data) {
                if (d.success) {
                    okCount++;
                    details.push(`✅ ${d.name}: ${d.chunks}段`);
                } else {
                    failCount++;
                    const reason = d.error || d.message || '未知原因';
                    const shortReason = reason.length > 50 ? reason.substring(0, 50) + '...' : reason;
                    details.push(`❌ ${d.name}: ${shortReason}`);
                }
            }
            let summary = '';
            if (okCount > 0) summary += `✅ 成功 ${okCount} 个`;
            if (failCount > 0) summary += (summary ? ', ' : '') + `❌ 失败 ${failCount} 个`;
            toast(`📚 ${summary}`);

            if (failCount > 0) {
                console.warn('[知识库导入]', details.filter(d => d.startsWith('❌')));
                showKbImportDetails(details);
            }
        } else if (data.error) {
            toast('❌ ' + data.error);
        } else {
            toast(`📚 已上传 ${fileCount} 个文件`);
        }
        loadKbList();
    } catch (e) {
        toast('❌ 上传失败: ' + e.message);
        console.error('[知识库上传错误]', e);
    } finally {
        // 恢复上传区域
        if (uploadArea && originalHtml) {
            uploadArea.innerHTML = originalHtml;
        }
    }
}

// 显示知识库导入详细结果弹窗
function showKbImportDetails(details) {
    // 移除已有弹窗
    const old = document.getElementById('kbImportDetailPanel');
    if (old) old.remove();

    const panel = document.createElement('div');
    panel.id = 'kbImportDetailPanel';
    panel.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#fff;border-radius:12px;padding:24px;max-width:500px;width:90%;max-height:70vh;overflow-y:auto;z-index:10000;box-shadow:0 8px 32px rgba(0,0,0,.3)';

    let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">';
    html += '<h3 style="margin:0;font-size:16px;color:#333">📚 导入结果详情</h3>';
    html += '<button onclick="this.closest(\'#kbImportDetailPanel\').remove()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#999">&times;</button>';
    html += '</div>';
    html += '<div style="font-size:13px;line-height:1.8;color:#444">';
    for (const d of details) {
        const isOk = d.startsWith('✅');
        html += `<div style="padding:4px 0;border-bottom:1px solid #f0f0f0;color:${isOk ? '#16a34a' : '#dc2626'}">${d}</div>`;
    }
    html += '</div>';
    html += '<div style="margin-top:12px;text-align:center"><button onclick="this.closest(\'#kbImportDetailPanel\').remove()" style="background:#1a1a2e;color:#fff;border:none;padding:6px 24px;border-radius:6px;cursor:pointer;font-size:13px">知道了</button></div>';

    panel.innerHTML = html;

    // 遮罩层
    const overlay = document.createElement('div');
    overlay.id = 'kbImportDetailOverlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:9999';
    overlay.onclick = () => { overlay.remove(); panel.remove(); };

    document.body.appendChild(overlay);
    document.body.appendChild(panel);
}

// ===== Paste Text to Knowledge Base =====
async function pasteToKnowledge() {
    const titleEl = document.getElementById('kbTextTitle');
    const contentEl = document.getElementById('kbTextContent');
    const title = titleEl.value.trim();
    const content = contentEl.value.trim();

    if (!title) {
        toast('请输入标题');
        titleEl.focus();
        return;
    }
    if (!content) {
        toast('请输入或粘贴内容');
        contentEl.focus();
        return;
    }

    try {
        const r = await fetch('/api/knowledge/add_text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: title, content: content })
        });
        const data = await r.json();

        if (data.success) {
            toast('已添加到知识库');
            titleEl.value = '';
            contentEl.value = '';
            loadKbList();  // 刷新列表
        } else {
            toast('添加失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('添加失败: ' + e.message);
    }
}

// ===== Multi-Model =====
let currentMultiModel = '';

// 云端模型描述与申请地址（25+ 厂商）
const MODEL_DESCS = {
    // ── 国内厂商 ──
    'deepseek':   { desc: 'platform.deepseek.com — 免费额度，推荐首选', url: 'https://platform.deepseek.com' },
    'qwen':       { desc: 'dashscope.aliyun.com — 通义千问，有免费额度', url: 'https://dashscope.aliyun.com' },
    'kimi':       { desc: 'platform.moonshot.cn — Kimi，有免费额度', url: 'https://platform.moonshot.cn' },
    'doubao':     { desc: 'console.volcengine.com — 豆包（字节），按量付费', url: 'https://console.volcengine.com/ark' },
    'hunyuan':    { desc: 'hunyuan.tencent.com — 腾讯元宝/混元，有免费额度', url: 'https://hunyuan.tencent.com' },
    'zhipu':      { desc: 'open.bigmodel.cn — 智谱AI GLM，有免费额度', url: 'https://open.bigmodel.cn' },
    'yi':         { desc: 'platform.lingyiwanwu.com — 零一万物，有免费额度', url: 'https://platform.lingyiwanwu.com' },
    'stepfun':    { desc: 'platform.stepfun.com — 阶跃星辰，有免费额度', url: 'https://platform.stepfun.com' },
    'minimax':    { desc: 'platform.minimaxi.chat — MiniMax海螺，有免费额度', url: 'https://platform.minimaxi.chat' },
    'ernie':      { desc: 'qianfan.cloud.baidu.com — 百度文心千帆，需先换取 access_token', url: 'https://qianfan.cloud.baidu.com' },
    'spark':      { desc: 'xinghuo.xfyun.cn — 讯飞星火，API key 格式为 APIKey:APISecret', url: 'https://xinghuo.xfyun.cn' },
    'baichuan':   { desc: 'platform.baichuan-ai.com — 百川智能，有免费额度', url: 'https://platform.baichuan-ai.com' },
    'siliconflow':{ desc: 'cloud.siliconflow.cn — 硅基流动，有免费额度，多模型聚合', url: 'https://cloud.siliconflow.cn' },
    // ── 国际厂商 ──
    'openai':     { desc: 'platform.openai.com — GPT-4o/o4-mini，收费', url: 'https://platform.openai.com' },
    'claude':     { desc: 'console.anthropic.com — Claude，收费', url: 'https://console.anthropic.com' },
    'gemini':     { desc: 'aistudio.google.com — Gemini，有免费额度', url: 'https://aistudio.google.com' },
    'xai':        { desc: 'console.x.ai — Grok（xAI），有免费额度', url: 'https://console.x.ai' },
    'groq':       { desc: 'console.groq.com — 极速推理，有免费额度', url: 'https://console.groq.com' },
    'perplexity': { desc: 'www.perplexity.ai/api — 搜索增强型AI，收费', url: 'https://www.perplexity.ai/api' },
    'mistral':    { desc: 'console.mistral.ai — 欧洲大模型，有免费额度', url: 'https://console.mistral.ai' },
    'together':   { desc: 'api.together.xyz — 多模型聚合，有免费额度', url: 'https://api.together.xyz' },
    'cohere':     { desc: 'dashboard.cohere.com — Command R+，有免费额度', url: 'https://dashboard.cohere.com' },
    'openrouter': { desc: 'openrouter.ai — 聚合路由，按用量计费', url: 'https://openrouter.ai' },
    'huggingface':{ desc: 'huggingface.co/settings/tokens — 无服务器推理，有免费额度', url: 'https://huggingface.co/settings/tokens' },
};

async function loadMultiModels() {
    try {
        const r = await fetch('/api/models/local-all');
        const data = await r.json();
        const models = data.models || [];
        const serverCurrent = data.current || '';

        // Update selector in settings（切换当前模型 下拉框）
        const sel = document.getElementById('setCurrentMultiModel');
        if (sel) {
            sel.innerHTML = '';
            // 只放 Ollama 模型进下拉框
            const ollamaModels = models.filter(m => m.source === 'Ollama');
            ollamaModels.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                opt.textContent = m.name;
                if (m.id === serverCurrent) {
                    opt.selected = true;
                    currentMultiModel = m.id;
                }
                sel.appendChild(opt);
            });
            if (!ollamaModels.length) {
                sel.innerHTML = '<option value="">暂无本地模型</option>';
            }
        }

        // Update model list
        const listEl = document.getElementById('multiModelList');
        if (!listEl) return;

        if (!models.length) {
            listEl.innerHTML = '<div class="plugin-empty">模型列表为空</div>';
            return;
        }

        // 多模型列表只显示云端模型，Ollama 在 AI模型 Tab 管理
        const cloudModels = models.filter(m => m.id !== 'ollama');
        listEl.innerHTML = cloudModels.map(m => {
            const statusColor = m.enabled ? '#22c55e' : '#9ca3af';
            const statusText = m.enabled ? '● 已启用' : '○ 未启用';
            const currentBadge = m.is_current
                ? '<span style="font-size:11px;background:var(--primary);color:#fff;border-radius:4px;padding:1px 6px;margin-left:4px;">当前使用</span>'
                : '';

            // 按钮区域
            let btns = '';
            if (!m.enabled) {
                btns = `<button class="btn-primary btn-sm" onclick="toggleModelConfig('${m.id}',this)">配置启用</button>`;
            } else {
                btns = '';
                if (!m.is_current) {
                    btns += `<button class="btn-secondary btn-sm" style="margin-right:4px;" onclick="setCurrentModel('${m.id}')">设为当前</button>`;
                }
                btns += `<button class="btn-danger btn-sm" onclick="disableModel('${m.id}')">禁用</button>`;
            }
            // 自定义模型额外加「删除」按钮
            if (m.is_custom) {
                btns += ` <button class="btn-danger btn-sm" style="margin-left:4px;" onclick="deleteCustomModel('${m.id}','${m.name}')">🗑️</button>`;
            }

            return `
                <div class="plugin-item" style="display:flex;justify-content:space-between;align-items:center;padding:10px 8px;border-bottom:1px solid var(--border);">
                    <div>
                        <strong style="font-size:13px;">${m.name}</strong>
                        ${currentBadge}
                        <br>
                        <span style="font-size:11px;color:${statusColor};">${statusText}</span>
                        ${m.free ? '<span style="font-size:11px;color:#f59e0b;margin-left:6px;">🆓免费额度</span>' : ''}
                    </div>
                    <div style="flex-shrink:0;">${btns}</div>
                </div>
                <div id="modelConfig_${m.id}" style="display:none;padding:8px 12px 12px;background:var(--bg-hover);border-bottom:1px solid var(--border);">
                </div>
            `;
        }).join('');

    } catch (e) {
        console.error('加载多模型列表失败:', e);
        const listEl = document.getElementById('multiModelList');
        if (listEl) listEl.innerHTML = '<div class="plugin-empty" style="color:red;">加载失败，请刷新重试</div>';
    }
}

// 展开/收起模型配置面板（内嵌，不用 prompt）
function toggleModelConfig(modelId, btn) {
    const configEl = document.getElementById('modelConfig_' + modelId);
    if (!configEl) return;
    // 如果已展开则收起
    if (configEl.style.display !== 'none') {
        configEl.style.display = 'none';
        return;
    }
    // 收起其他已展开的配置
    document.querySelectorAll('[id^="modelConfig_"]').forEach(el => {
        if (el.id !== 'modelConfig_' + modelId) el.style.display = 'none';
    });
    const info = MODEL_DESCS[modelId] || { desc: '需要对应官网的API密钥', url: '' };
    // 讯飞星火特殊提示
    const isSparkModel = (modelId === 'spark');
    const keyPlaceholder = isSparkModel ? '格式：APIKey:APISecret（用冒号连接）' : '请粘贴 API 密钥 (sk-xxxx)';
    const keyHint = isSparkModel ? '<div style="font-size:11px;color:#f59e0b;margin-bottom:6px;">⚠️ 讯飞星火 key 格式：<code>APIKey:APISecret</code>，不是普通 Bearer Token</div>' : '';
    configEl.innerHTML = `
        <div style="margin-bottom:8px;font-size:12px;color:var(--text-secondary);">
            ${info.desc} &nbsp;<a href="${info.url}" target="_blank" style="color:var(--primary);">👉 去申请</a>
        </div>
        ${keyHint}
        <label style="font-size:12px;display:block;margin-bottom:4px;">API 密钥</label>
        <input type="password" id="cfgKey_${modelId}" placeholder="${keyPlaceholder}" style="width:100%;padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg-main);color:var(--text-primary);font-size:13px;margin-bottom:8px;">
        <label style="font-size:12px;display:block;margin-bottom:4px;">Base URL（可选，国内中转/自定义端点可填）</label>
        <input type="text" id="cfgUrl_${modelId}" placeholder="${info.url || ''}" style="width:100%;padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--bg-main);color:var(--text-primary);font-size:13px;margin-bottom:8px;">
        <div style="display:flex;gap:8px;">
            <button class="btn-primary btn-sm" onclick="doInlineEnable('${modelId}')">✅ 确认启用</button>
            <button class="btn-secondary btn-sm" onclick="doInlineTest('${modelId}')">🔍 测试连接</button>
        </div>
        <span id="cfgResult_${modelId}" style="margin-left:8px;font-size:12px;"></span>
    `;
    configEl.style.display = 'block';
}

// 内嵌面板：确认启用
async function doInlineEnable(modelId) {
    const keyEl = document.getElementById('cfgKey_' + modelId);
    const urlEl = document.getElementById('cfgUrl_' + modelId);
    const apiKey = keyEl ? keyEl.value.trim() : '';
    const baseUrl = urlEl ? urlEl.value.trim() : '';
    if (!apiKey) {
        const r = document.getElementById('cfgResult_' + modelId);
        if (r) { r.style.color = '#ef4444'; r.textContent = '❌ 请输入 API 密钥'; }
        return;
    }
    await doEnableModel(modelId, apiKey, baseUrl);
}

// 内嵌面板：测试连接
async function doInlineTest(modelId) {
    const keyEl = document.getElementById('cfgKey_' + modelId);
    const urlEl = document.getElementById('cfgUrl_' + modelId);
    const apiKey = keyEl ? keyEl.value.trim() : '';
    const baseUrl = urlEl ? urlEl.value.trim() : '';
    const rEl = document.getElementById('cfgResult_' + modelId);
    if (!apiKey) {
        if (rEl) { rEl.style.color = '#ef4444'; rEl.textContent = '❌ 请先输入 API 密钥'; }
        return;
    }
    if (rEl) rEl.textContent = '⏳ 测试中...';
    try {
        const body = { model_id: modelId, api_key: apiKey };
        if (baseUrl) body.base_url = baseUrl;
        const resp = await fetch('/api/models/test-connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await resp.json();
        if (rEl) {
            rEl.style.color = data.success ? '#22c55e' : '#ef4444';
            rEl.textContent = data.success ? '✅ 连接成功！' : '❌ ' + (data.error || '连接失败');
        }
    } catch (e) {
        if (rEl) { rEl.style.color = '#ef4444'; rEl.textContent = '❌ ' + e.message; }
    }
}

async function doEnableModel(modelId, apiKey, baseUrl) {
    try {
        const body = { model_id: modelId };
        if (apiKey) body.api_key = apiKey;
        if (baseUrl) body.base_url = baseUrl;

        const r = await fetch('/api/models/set-current', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await r.json();
        if (data.success) {
            toast(`✅ ${modelId} 已启用，可设为当前使用`);
            loadMultiModels();
        } else {
            toast('启用失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('启用失败: ' + e.message);
    }
}

// ===== 自定义模型增删 =====

function quickFillModel(id, name, baseUrl) {
    // 先展开面板
    const panel = document.getElementById('addModelPanel');
    if (panel) panel.style.display = 'block';
    // 填入预设值
    const idEl = document.getElementById('newModelId');
    const nameEl = document.getElementById('newModelName');
    const urlEl = document.getElementById('newModelBaseUrl');
    if (idEl) idEl.value = id;
    if (nameEl) nameEl.value = name;
    if (urlEl) urlEl.value = baseUrl;
    // 清空 API key 和 model name（留给用户填）
    const keyEl = document.getElementById('newModelKey');
    const modelEl = document.getElementById('newModelModelName');
    if (keyEl) keyEl.value = '';
    if (modelEl) modelEl.value = '';
    const r = document.getElementById('addModelResult');
    if (r) r.textContent = '';
    toast('已填入 ' + name + '，请填写 API Key 后保存');
}

function showAddModelPanel() {
    const panel = document.getElementById('addModelPanel');
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    // 清空表单
    ['newModelId','newModelName','newModelBaseUrl','newModelKey','newModelModelName'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    const r = document.getElementById('addModelResult');
    if (r) r.textContent = '';
}

async function saveCustomModel() {
    const providerId  = ((document.getElementById('newModelId') || {}).value || '').trim();
    const name        = ((document.getElementById('newModelName') || {}).value || '').trim();
    const baseUrl     = ((document.getElementById('newModelBaseUrl') || {}).value || '').trim();
    const apiKey      = ((document.getElementById('newModelKey') || {}).value || '').trim();
    const modelName   = ((document.getElementById('newModelModelName') || {}).value || '').trim();
    const rEl         = document.getElementById('addModelResult');

    if (!providerId || !name || !baseUrl) {
        if (rEl) { rEl.style.color='#ef4444'; rEl.textContent='❌ ID / 名称 / Base URL 必填'; }
        return;
    }
    if (rEl) { rEl.style.color='var(--text-secondary)'; rEl.textContent='保存中...'; }

    try {
        const resp = await fetch('/api/models/custom-provider', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider_id: providerId, name, base_url: baseUrl, api_key: apiKey, model_name: modelName })
        });
        const data = await resp.json();
        if (data.success) {
            if (rEl) { rEl.style.color='#22c55e'; rEl.textContent='✅ 已添加并启用！'; }
            toast(`✅ 自定义模型「${name}」已添加`);
            setTimeout(() => { document.getElementById('addModelPanel').style.display='none'; }, 1000);
            loadMultiModels();
        } else {
            if (rEl) { rEl.style.color='#ef4444'; rEl.textContent='❌ ' + (data.error || '保存失败'); }
        }
    } catch (e) {
        if (rEl) { rEl.style.color='#ef4444'; rEl.textContent='❌ ' + e.message; }
    }
}

async function deleteCustomModel(providerId, name) {
    if (!confirm(`确定删除自定义模型「${name}」？此操作不可恢复。`)) return;
    try {
        const resp = await fetch(`/api/models/custom-provider/${providerId}`, { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) {
            toast(`已删除「${name}」`);
            loadMultiModels();
        } else {
            toast('删除失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('删除功能暂不可用（后端路由未注册）');
    }
}

async function changeMultiModel(modelId) {
    if (!modelId) return;
    try {
        const r = await fetch('/api/models/set-current', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_id: modelId })
        });
        const data = await r.json();
        if (data.success) {
            currentMultiModel = modelId;
            toast('已切换模型');
            loadMultiModels();
        } else {
            toast('切换失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('切换失败: ' + e.message);
    }
}

async function disableModel(modelId) {
    if (!confirm('确定禁用此模型？')) return;
    toast('禁用功能暂不可用（后端路由未注册）');
}

async function setCurrentModel(modelId) {
    try {
        const r = await fetch('/api/models/set-current', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_id: modelId })
        });
        const data = await r.json();
        if (data.success) {
            currentMultiModel = modelId;
            toast('已设为当前模型');
            loadMultiModels();
        } else {
            toast('设置失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('设置失败: ' + e.message);
    }
}

// ===== Tasks =====
taskFilter = 'all';




// ===== Window 全局导出（由自检补全）=====
window.checkKbAutoImport = checkKbAutoImport;
window.uploadKbFiles = uploadKbFiles;
window.pasteToKnowledge = pasteToKnowledge;
window.loadKbList = loadKbList;
window.deleteKbItem = deleteKbItem;
window.loadMultiModels = loadMultiModels;
window.toggleModelConfig = toggleModelConfig;
window.doEnableModel = doEnableModel;
window.quickFillModel = quickFillModel;

// ===== 2026-07-13: 知识库文件列表+删除 =====
async function loadKbList() {
    var listEl = document.getElementById('kbList');
    if (!listEl) return;
    listEl.innerHTML = '<div style="text-align:center;color:var(--text-secondary);padding:20px;">加载中...</div>';
    try {
        var r = await fetch('/api/skills/templates'); // 确认API可用
        // 用list_knowledge
        var r2 = await fetch('/api/kb/list');
        if (!r2.ok) {
            // fallback: 直接使用空结果
            r2 = { json: () => ({ items: [] }) };
        }
        var d = await r2.json();
        var items = d.items || d.data?.items || [];
        if (!items.length) {
            listEl.innerHTML = '<div style="text-align:center;color:var(--text-secondary);padding:20px;">📭 知识库为空<br><small>上传文件或粘贴文本即可添加</small></div>';
            return;
        }
        var html = '<div style="margin-top:12px;"><div style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text-primary);">📋 已导入内容（' + items.length + '项）</div>';
        items.forEach(function(item) {
            var icon = '📄';
            var fmt = item.format || '';
            if (fmt.indexOf('png') > -1 || fmt.indexOf('jpg') > -1 || fmt.indexOf('image') > -1) icon = '🖼️';
            else if (fmt.indexOf('pdf') > -1) icon = '📕';
            else if (fmt.indexOf('docx') > -1 || fmt.indexOf('word') > -1) icon = '📘';
            else if (fmt.indexOf('xlsx') > -1 || fmt.indexOf('excel') > -1) icon = '📗';
            else if (fmt.indexOf('pptx') > -1 || fmt.indexOf('ppt') > -1) icon = '📙';
            else if (fmt.indexOf('mp3') > -1 || fmt.indexOf('wav') > -1 || fmt.indexOf('audio') > -1) icon = '🎵';
            else if (item.filename.indexOf('auto_learn') > -1) icon = '💬';

            var name = item.filename || '未知';
            var chunks = item.chunks || 0;
            var preview = item.preview || '';

            html += '<div style="display:flex;align-items:center;gap:8px;padding:10px 12px;margin-bottom:6px;background:var(--bg-tertiary);border-radius:8px;">';
            html += '<span style="font-size:20px;">' + icon + '</span>';
            html += '<div style="flex:1;min-width:0;">';
            html += '<div style="font-size:13px;font-weight:500;color:var(--text-primary);word-break:break-all;">' + name + '</div>';
            html += '<div style="font-size:11px;color:var(--text-secondary);">' + chunks + ' 段';
            if (preview) html += ' · ' + preview.substring(0, 60);
            html += '</div>';
            html += '</div>';
            html += '<div style="display:flex;gap:4px;flex-shrink:0;">';
            html += '<button onclick="viewKbContent(\'' + name.replace(/'/g, "\\'") + '\')" title="查看内容" style="padding:4px 8px;font-size:12px;border:1px solid var(--border);border-radius:6px;background:var(--bg-primary);color:var(--primary);cursor:pointer;">👁️</button>';
            html += '<button onclick="deleteKbItem(\'' + name.replace(/'/g, "\\'") + '\')" title="删除" style="padding:4px 8px;font-size:12px;border:1px solid var(--border);border-radius:6px;background:var(--bg-primary);color:#ef4444;cursor:pointer;">🗑️</button>';
            html += '</div>';
            html += '</div>';
        });
        html += '</div>';
        listEl.innerHTML = html;
    } catch(e) {
        listEl.innerHTML = '<div style="color:var(--text-secondary);padding:12px;font-size:12px;">加载失败: ' + e.message + '</div>';
    }
}

async function deleteKbItem(filename) {
    if (!confirm('确认删除「' + filename + '」？\n\n这会从知识库中移除该文件的所有内容。')) return;
    try {
        var r = await fetch('/api/kb/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({filename: filename})
        });
        var d = await r.json();
        if (d.success) {
            loadKbList();
            toast('✅ 已删除「' + filename + '」');
        } else {
            toast('❌ 删除失败: ' + (d.error || ''));
        }
    } catch(e) {
        toast('❌ 网络错误');
    }
}

// ★ 2026-07-14: 查看知识库内容（从向量库取，非磁盘文件）
async function viewKbContent(filename) {
    try {
        toast('加载中...');
        var r = await fetch('/api/kb/content?source=' + encodeURIComponent(filename));
        var d = await r.json();
        if (!d.success && d.error) {
            toast('❌ ' + d.error);
            return;
        }
        var content = d.content || '(无内容)';
        var chunks = d.chunks || 0;
        var overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:99998;';
        var div = document.createElement('div');
        div.style.cssText = 'position:fixed;top:8%;left:10%;width:80%;height:80%;background:var(--bg-main);z-index:99999;border-radius:12px;padding:20px;overflow:auto;box-shadow:0 10px 40px rgba(0,0,0,0.5);';
        var escFn = (typeof escHtml === 'function') ? escHtml : function(s){return String(s).replace(/</g,'&lt;').replace(/>/g,'&gt;');};
        div.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">' +
            '<b style="color:var(--text-primary);font-size:15px;">📄 ' + escFn(filename) + ' <small style="color:var(--text-secondary);font-weight:normal;">(' + chunks + '段)</small></b>' +
            '<button style="background:#c0392b;color:#fff;border:none;padding:6px 16px;border-radius:6px;cursor:pointer;font-size:13px;" id="kbViewCloseBtn">关闭</button>' +
            '</div><pre style="color:var(--text-secondary);white-space:pre-wrap;font-size:13px;line-height:1.6;max-height:calc(100% - 60px);overflow:auto;">' + escFn(content) + '</pre>';
        overlay.onclick = function() { overlay.remove(); div.remove(); };
        document.body.appendChild(overlay);
        document.body.appendChild(div);
        var closeBtn = document.getElementById('kbViewCloseBtn');
        if (closeBtn) closeBtn.onclick = function() { overlay.remove(); div.remove(); };
    } catch(e) {
        toast('❌ 查看失败: ' + e.message);
    }
}
window.viewKbContent = viewKbContent;
window.saveCustomModel = saveCustomModel;
window.deleteCustomModel = deleteCustomModel;
window.changeMultiModel = changeMultiModel;
window.disableModel = disableModel;
window.setCurrentModel = setCurrentModel;
window.doInlineEnable = doInlineEnable;
window.doInlineTest = doInlineTest;
