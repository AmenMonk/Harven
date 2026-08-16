/* ===== Harven AI — CORE module =====
 * Copyright 2026 AmenMonk <38552540@qq.com>
 * Licensed under Apache License 2.0
 */

let currentConvId = null;
let currentSpecId = '';
let isStreaming = false;
// ★ F1: selectedImageFiles 移到 app-media.js 统一管理，这里删除
let abortController = null;
let multiSelectMode = false;
let selectedConvs = new Set();

// ===== 消息队列（WorkBuddy风格：独立面板 + 手动排序） =====
let msgQueue = [];         // 排队等待发送的消息 [{text, type, file, id}]
let isProcessing = false;  // 是否正在处理（含等待AI回复）
let dragIndex = -1;       // 拖拽源索引
let dropIndex = -1;       // 拖拽目标索引

// ===== Init =====
document.addEventListener('DOMContentLoaded', () => {
    loadConversations();
    loadSpecs();
    checkOllamaStatus();
    if (typeof loadInstalledModelsEnhanced === 'function') loadInstalledModelsEnhanced();
    initVersionCheck();
    setupInput();
    setupDragDrop();
    // 检查知识库自动导入状态
    if (typeof checkKbAutoImport === 'function') checkKbAutoImport();
});

// ===== Input =====
function setupInput() {
    const input = document.getElementById('msgInput');
    input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    // Auto resize
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 150) + 'px';
    });

    // ===== Paste: support image + file from clipboard =====
    input.addEventListener('paste', e => {
        const items = (e.clipboardData || {}).items;
        if (!items) return;

        for (const item of items) {
            // 1) Image paste (screenshot / copied image)
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const blob = item.getAsFile();
                if (!blob) continue;
                // Convert to File with proper name
                const file = new File([blob], 'paste_' + Date.now() + '.png', { type: blob.type || 'image/png' });
                selectedImageFiles = [file];
                renderImageThumbs();
                toast('📷 粘贴了图片');
                return;
            }
            // 2) File paste (e.g. copied file from Explorer)
            if (item.kind === 'file' && !item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (!file) return;
                // ★ F4: 粘贴文件也存入 selectedFiles 数组（不再用 selectedFile 单文件变量）
                if (typeof selectedFiles !== 'undefined') {
                    selectedFiles = [file];  // 粘贴替换，不用追加
                }
                const name = file.name || 'pasted_file';
                const sizeMB = (file.size / 1024 / 1024).toFixed(1);
                document.getElementById('fileName').textContent = '📄 ' + name + ' (' + sizeMB + 'MB)';
                document.getElementById('filePreview').style.display = 'flex';
                toast('📎 粘贴了文件: ' + name);
                return;
            }
        }
        // Text paste → native behavior, no need to handle
    });

    // Global keyboard shortcut: F9 to toggle voice recording
    document.addEventListener('keydown', e => {
        if (e.key === 'F9') {
            e.preventDefault();
            toggleVoiceRecord();
        }
    });
}

function setupDragDrop() {
    const upload = document.getElementById('kbUpload');
    if (!upload) return;
    upload.addEventListener('dragover', e => { e.preventDefault(); upload.style.borderColor = 'var(--primary)'; });
    upload.addEventListener('dragleave', () => { upload.style.borderColor = ''; });
    upload.addEventListener('drop', e => {
        e.preventDefault();
        upload.style.borderColor = '';
        const files = e.dataTransfer.files;
        if (files.length) uploadKbFiles(files);
    });
    document.getElementById('kbFiles').addEventListener('change', function() {
        if (this.files.length) uploadKbFiles(this.files);
        this.value = '';
    });
}



// ===== Window 全局导出（由自检脚本补全）=====
window.setupInput = setupInput;
window.setupDragDrop = setupDragDrop;
