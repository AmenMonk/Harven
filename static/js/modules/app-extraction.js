/**
 * 知识萃取模块
 * 功能：从文档/对话中萃取核心要点、查看已萃取的知识、删除已萃取的知识
 */

let _extractionCache = null;

/**
 * 加载知识萃取面板
 */
async function loadExtractionPanel() {
    const panel = document.getElementById('extraction-panel');
    if (!panel) {
        console.error('[Extraction] 知识萃取面板不存在');
        return;
    }

    panel.innerHTML = `
        <div class="extraction-container">
            <div class="extraction-header">
                <h3>📚 知识萃取</h3>
                <button class="btn btn-primary btn-sm" onclick="refreshExtraction()">
                    🔄 刷新
                </button>
            </div>
            <div class="extraction-actions">
                <button class="btn btn-success btn-sm" onclick="showExtractDocumentDialog()">
                    📄 从文档萃取
                </button>
                <button class="btn btn-info btn-sm" onclick="extractFromConversation()">
                    💬 从对话萃取
                </button>
            </div>
            <div class="extraction-knowledge" id="extraction-knowledge">
                <div class="loading">加载中...</div>
            </div>
            <div class="extraction-log" id="extraction-log"></div>
        </div>
    `;

    await refreshExtraction();
}

/**
 * 刷新知识萃取
 */
async function refreshExtraction() {
    await Promise.all([
        loadExtractedKnowledge(),
        loadExtractionLog(),
    ]);
}

/**
 * 加载已萃取的知识
 */
async function loadExtractedKnowledge() {
    const knowledgeEl = document.getElementById('extraction-knowledge');
    if (!knowledgeEl) return;

    knowledgeEl.innerHTML = '<div class="loading">加载中...</div>';

    try {
        const response = await fetch('/api/extraction/knowledge');
        const data = await response.json();

        if (data.success) {
            renderExtractedKnowledge(data.knowledge);
        } else {
            knowledgeEl.innerHTML = `<div class="error">加载失败: ${data.error || '未知错误'}</div>`;
        }
    } catch (error) {
        knowledgeEl.innerHTML = `<div class="error">网络错误: ${error.message}</div>`;
    }
}

/**
 * 渲染已萃取的知识
 */
function renderExtractedKnowledge(knowledge) {
    const knowledgeEl = document.getElementById('extraction-knowledge');
    if (!knowledgeEl) return;

    if (!knowledge || knowledge.length === 0) {
        knowledgeEl.innerHTML = '<div class="empty">暂无萃取的知识</div>';
        return;
    }

    let html = '<h4>已萃取的知识</h4>';
    html += '<div class="knowledge-list">';

    for (const item of knowledge) {
        html += `
            <div class="knowledge-item" data-id="${item.id}">
                <div class="knowledge-content">${item.content}</div>
                <div class="knowledge-meta">
                    <span class="badge badge-info">${item.source_type || 'unknown'}</span>
                    <span class="knowledge-time">${formatDateTime(item.extracted_at)}</span>
                </div>
                <div class="knowledge-actions">
                    <button class="btn btn-danger btn-xs" onclick="deleteExtractedKnowledge(${item.id})">删除</button>
                </div>
            </div>
        `;
    }

    html += '</div>';
    knowledgeEl.innerHTML = html;
}

/**
 * 加载萃取日志
 */
async function loadExtractionLog() {
    const logEl = document.getElementById('extraction-log');
    if (!logEl) return;

    logEl.innerHTML = '<div class="loading">加载中...</div>';

    try {
        const response = await fetch('/api/extraction/log');
        const data = await response.json();

        if (data.success) {
            renderExtractionLog(data.log);
        } else {
            logEl.innerHTML = `<div class="error">加载失败: ${data.error || '未知错误'}</div>`;
        }
    } catch (error) {
        logEl.innerHTML = `<div class="error">网络错误: ${error.message}</div>`;
    }
}

/**
 * 渲染萃取日志
 */
function renderExtractionLog(log) {
    const logEl = document.getElementById('extraction-log');
    if (!logEl) return;

    if (!log || log.length === 0) {
        logEl.innerHTML = '<div class="empty">暂无萃取日志</div>';
        return;
    }

    let html = '<h4>萃取日志</h4>';
    html += '<div class="log-list"><table class="table table-sm">';
    html += '<thead><tr><th>时间</th><th>来源类型</th><th>来源ID</th><th>条目数</th></tr></thead><tbody>';

    // 只显示最近10条
    const recentLog = log.slice(-10).reverse();

    for (const entry of recentLog) {
        html += `
            <tr>
                <td>${formatDateTime(entry.timestamp)}</td>
                <td>${entry.source_type || '-'}</td>
                <td>${entry.source_id || '-'}</td>
                <td>${entry.item_count || 0}</td>
            </tr>
        `;
    }

    html += '</tbody></table></div>';
    logEl.innerHTML = html;
}

/**
 * 显示从文档萃取对话框
 */
function showExtractDocumentDialog() {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <div class="modal-header">
                <h4>从文档萃取核心要点</h4>
                <button class="modal-close" onclick="this.closest('.modal').remove()">×</button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label>文档路径</label>
                    <input type="text" id="extract-doc-path" placeholder="输入文档路径...">
                </div>
                <div class="form-group">
                    <label>或选择文件</label>
                    <input type="file" id="extract-doc-file" accept=".txt,.md,.pdf,.docx,.doc">
                </div>
                <button class="btn btn-primary btn-sm" onclick="extractFromDocument()">开始萃取</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    // 文件选择事件
    const fileInput = document.getElementById('extract-doc-file');
    if (fileInput) {
        fileInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            // 读取文件内容
            const reader = new FileReader();
            reader.onload = (event) => {
                document.getElementById('extract-doc-path').value = file.name;
                // 保存文件内容到临时变量
                window._tempFileContent = event.target.result;
            };
            reader.readAsText(file);
        });
    }
}

/**
 * 从文档萃取
 */
async function extractFromDocument() {
    const pathInput = document.getElementById('extract-doc-path');
    const filePath = pathInput ? pathInput.value : '';

    if (!filePath && !window._tempFileContent) {
        alert('请输入文档路径或选择文件');
        return;
    }

    let documentContent = window._tempFileContent || '';
    let documentId = filePath || 'temp';

    // 如果输入的是路径，尝试读取文件
    if (filePath && !window._tempFileContent) {
        alert('暂不支持直接读取服务器文件，请选择本地文件上传');
        return;
    }

    try {
        const response = await fetch('/api/extraction/extract-document', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: documentId,
                content: documentContent,
            }),
        });

        const data = await response.json();
        if (data.success) {
            alert(`萃取成功，共萃取 ${data.extracted_count} 条核心要点`);
            document.querySelector('.modal')?.remove();
            window._tempFileContent = null;
            await refreshExtraction();
        } else {
            alert(`萃取失败: ${data.error || '未知错误'}`);
        }
    } catch (error) {
        alert(`网络错误: ${error.message}`);
    }
}

/**
 * 从对话萃取
 */
async function extractFromConversation() {
    if (!confirm('确定要从对话历史中萃取有价值的经验吗？')) {
        return;
    }

    try {
        const response = await fetch('/api/extraction/extract-conversation', {
            method: 'POST',
        });

        const data = await response.json();
        if (data.success) {
            alert(`萃取成功，共萃取 ${data.extracted_count} 条经验`);
            await refreshExtraction();
        } else {
            alert(`萃取失败: ${data.error || '未知错误'}`);
        }
    } catch (error) {
        alert(`网络错误: ${error.message}`);
    }
}

/**
 * 删除已萃取的知识
 */
async function deleteExtractedKnowledge(knowledgeId) {
    if (!confirm('确定要删除这条已萃取的知识吗？')) {
        return;
    }
    const allowed = await requirePasswordIfNeeded('deleteExtractedKnowledge');
    if (!allowed) return;

    try {
        const response = await fetch(`/api/extraction/knowledge/${knowledgeId}`, {
            method: 'DELETE',
        });

        const data = await response.json();
        if (data.success) {
            await refreshExtraction();
        } else {
            alert(`删除失败: ${data.error || '未知错误'}`);
        }
    } catch (error) {
        alert(`网络错误: ${error.message}`);
    }
}

/**
 * 格式化日期时间
 */
function formatDateTime(isoString) {
    if (!isoString) return '-';
    try {
        const date = new Date(isoString);
        return date.toLocaleString('zh-CN');
    } catch (e) {
        return isoString;
    }
}

// 导出函数
window.loadExtractionPanel = loadExtractionPanel;
window.refreshExtraction = refreshExtraction;
window.loadExtractedKnowledge = loadExtractedKnowledge;
window.showExtractDocumentDialog = showExtractDocumentDialog;
window.extractFromDocument = extractFromDocument;
window.extractFromConversation = extractFromConversation;
window.deleteExtractedKnowledge = deleteExtractedKnowledge;

// ——— HarvenExtraction: 与 index.html tab-extraction 面板对接 ———
window.HarvenExtraction = {
    async loadKnowledge() {
        const listEl = document.getElementById('extractionKnowledgeList');
        const countEl = document.getElementById('extractionCount');
        const query = document.getElementById('extractionSearchQ')?.value || '';
        if (listEl) listEl.innerHTML = '<div class="plugin-empty">加载中...</div>';
        try {
            const url = '/api/extraction/knowledge' + (query ? '?query=' + encodeURIComponent(query) : '');
            const r = await fetch(url);
            const d = await r.json();
            const knowledge = d.knowledge || [];
            if (countEl) countEl.textContent = `(${knowledge.length})`;
            if (!knowledge.length) {
                if (listEl) listEl.innerHTML = '<div class="plugin-empty">暂无萃取知识</div>';
                return;
            }
            if (listEl) listEl.innerHTML = knowledge.map(item => `
                <div class="plugin-item" style="padding:8px 12px;">
                    <div style="font-size:13px;margin-bottom:4px;">${item.content}</div>
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span style="font-size:11px;color:var(--text-secondary);">${item.source_type || ''} · ${item.extracted_at ? new Date(item.extracted_at).toLocaleString('zh-CN') : ''}</span>
                        <button class="btn-secondary btn-sm" style="font-size:11px;padding:2px 8px;" onclick="deleteExtractedKnowledge(${item.id}).then(()=>window.HarvenExtraction.loadKnowledge())">删除</button>
                    </div>
                </div>`).join('');
        } catch(e) {
            if (listEl) listEl.innerHTML = '<div class="plugin-empty">加载失败</div>';
        }
    },

    async loadLog() {
        const logEl = document.getElementById('extractionLogList');
        try {
            const r = await fetch('/api/extraction/log');
            const d = await r.json();
            const log = d.log || [];
            if (!logEl) return;
            if (!log.length) { logEl.textContent = '暂无日志'; return; }
            logEl.innerHTML = log.slice(-20).reverse().map(e =>
                `<div style="padding:2px 0;border-bottom:1px solid var(--border);">${new Date(e.timestamp).toLocaleString('zh-CN')} · ${e.source_type} · ${e.item_count} 条</div>`
            ).join('');
        } catch(e) {}
    },

    showDocumentExtractor() {
        const area = document.getElementById('docExtractorArea');
        if (area) area.style.display = area.style.display === 'none' ? 'block' : 'none';
    },

    async doExtractDocument() {
        const content = document.getElementById('docExtractContent')?.value?.trim();
        const statusEl = document.getElementById('extractionStatus');
        if (!content) { alert('请粘贴文档内容'); return; }
        if (statusEl) statusEl.textContent = '萃取中...';
        try {
            const r = await fetch('/api/extraction/extract-document', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ document_id: 'manual_' + Date.now(), content }),
            });
            const d = await r.json();
            if (statusEl) statusEl.textContent = d.success ? `✅ 萃取完成，共 ${d.extracted_count} 条` : `❌ 失败: ${d.error}`;
            if (d.success) {
                document.getElementById('docExtractContent').value = '';
                document.getElementById('docExtractorArea').style.display = 'none';
                await this.loadKnowledge();
            }
        } catch(e) {
            if (statusEl) statusEl.textContent = '❌ 网络错误';
        }
    },

    async extractConversation() {
        const statusEl = document.getElementById('extractionStatus');
        if (!confirm('从当前对话历史中萃取经验？')) return;
        if (statusEl) statusEl.textContent = '萃取中...';
        try {
            const r = await fetch('/api/extraction/extract-conversation', { method: 'POST' });
            const d = await r.json();
            if (statusEl) statusEl.textContent = d.success ? `✅ 萃取完成，共 ${d.extracted_count} 条` : `❌ 失败: ${d.error}`;
            if (d.success) await this.loadKnowledge();
        } catch(e) {
            if (statusEl) statusEl.textContent = '❌ 网络错误';
        }
    },

    init() { this.loadKnowledge(); this.loadLog(); }
};

// ===== Window 全局导出（由自检补全）=====
window.renderExtractedKnowledge = renderExtractedKnowledge;
window.loadExtractionLog = loadExtractionLog;
window.renderExtractionLog = renderExtractionLog;
window.formatDateTime = formatDateTime;
