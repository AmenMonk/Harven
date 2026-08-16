/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — MEDIA module ===== */
// ===== Attach Menu =====
function toggleAttachMenu() {
    const menu = document.getElementById('attachMenu');
    menu.classList.toggle('show');
}

function triggerFileInput(inputId) {
    document.getElementById(inputId).click();
    // 选择后关闭菜单
    document.getElementById('attachMenu').classList.remove('show');
}

// 点击页面其他地方时关闭附件菜单
document.addEventListener('click', e => {
    const wrapper = document.querySelector('.attach-wrapper');
    if (wrapper && !wrapper.contains(e.target)) {
        document.getElementById('attachMenu').classList.remove('show');
    }
});

// ===== Camera =====
let cameraStream = null;
let cameraBlob = null;

function openCamera() {
    // 关闭附件菜单
    document.getElementById('attachMenu').classList.remove('show');
    // 显示弹窗
    const modal = document.getElementById('cameraModal');
    modal.style.display = 'flex';
    document.getElementById('cameraCapturedImg').style.display = 'none';
    document.getElementById('cameraVideo').style.display = 'block';
    document.getElementById('btnCapture').style.display = '';
    document.getElementById('btnRetake').style.display = 'none';
    document.getElementById('btnUsePhoto').style.display = 'none';
    cameraBlob = null;
    // 开启摄像头
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false })
        .then(stream => {
            cameraStream = stream;
            document.getElementById('cameraVideo').srcObject = stream;
        })
        .catch(err => {
            toast('⚠️ 无法访问摄像头: ' + err.message);
            closeCamera();
        });
}

function closeCamera() {
    // 关闭摄像头流
    if (cameraStream) {
        cameraStream.getTracks().forEach(t => t.stop());
        cameraStream = null;
    }
    document.getElementById('cameraVideo').srcObject = null;
    document.getElementById('cameraModal').style.display = 'none';
    cameraBlob = null;
}

function capturePhoto() {
    const video = document.getElementById('cameraVideo');
    const canvas = document.getElementById('cameraCanvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    // 转 blob
    canvas.toBlob(blob => {
        cameraBlob = blob;
        document.getElementById('cameraPhoto').src = URL.createObjectURL(blob);
        document.getElementById('cameraCapturedImg').style.display = 'block';
        document.getElementById('cameraVideo').style.display = 'none';
        document.getElementById('btnCapture').style.display = 'none';
        document.getElementById('btnRetake').style.display = '';
        document.getElementById('btnUsePhoto').style.display = '';
    }, 'image/jpeg', 0.92);
}

function retakePhoto() {
    document.getElementById('cameraCapturedImg').style.display = 'none';
    document.getElementById('cameraVideo').style.display = 'block';
    document.getElementById('btnCapture').style.display = '';
    document.getElementById('btnRetake').style.display = 'none';
    document.getElementById('btnUsePhoto').style.display = 'none';
    cameraBlob = null;
}

function useCameraPhoto() {
    if (!cameraBlob) return;
    // 关闭摄像头
    if (cameraStream) {
        cameraStream.getTracks().forEach(t => t.stop());
        cameraStream = null;
    }
    document.getElementById('cameraModal').style.display = 'none';
    // 把拍照结果当作图片发送
    selectedImageFiles = [new File([cameraBlob], 'camera_' + Date.now() + '.jpg', { type: 'image/jpeg' })];
    renderImageThumbs();
    document.getElementById('imagePreview').style.display = 'flex';
    cameraBlob = null;
}

// ===== F1: 统一附件变量（2026-07-05）=====
// 所有 selected* 变量集中定义在 app-media.js，其他文件通过 window 访问
let selectedFiles = [];       // 通用文件（txt/pdf/docx等）
let selectedImageFiles = [];  // 图片
let selectedAudioFile = null; // 音频（单文件）
let selectedVideoFile = null; // 视频（单文件）

// ★ F6: 删除 selectedFile 单文件变量，统一用 selectedFiles 数组

function handleFileSelect(input) {
    var files = Array.from(input.files);
    if (files.length === 0) return;
    selectedFiles = [...selectedFiles, ...files].slice(0, 10);  // 最多10个

    var names = selectedFiles.map(function(f) {
        return '📄 ' + f.name + ' (' + (f.size / 1024 / 1024).toFixed(1) + 'MB)';
    }).join(', ');
    document.getElementById('fileName').textContent = names;
    document.getElementById('filePreview').style.display = 'flex';
    input.value = '';
}

function removeFile() {
    selectedFiles = [];
    document.getElementById('filePreview').style.display = 'none';
}

async function sendAudioFileMessage(text) {
    if (!selectedAudioFile) return;

    document.getElementById('welcomeScreen').style.display = 'none';
    appendUserMsg('[音频] ' + selectedAudioFile.name);

    const formData = new FormData();
    formData.append('audio', selectedAudioFile);
    if (text) formData.append('message', text);
    if (currentConvId) formData.append('conversation_id', currentConvId);

    selectedAudioFile = null;
    document.getElementById('audioPreview').style.display = 'none';

    // 设置处理中状态
    isProcessing = true;
    updateQueueUI();

    const thinkingDiv = startAiStream();
    document.getElementById('thinkingText').textContent = '🔄 正在转写音频...';

    try {
        const resp = await fetch('/api/chat/audio', { method: 'POST', body: formData });
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let aiText = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const payload = line.slice(6).trim();
                if (payload === '[DONE]') break;
                try {
                    const data = JSON.parse(payload);
                    if (data.type === 'transcript') {
                        document.getElementById('thinkingText').textContent = '📝 ' + data.content;
                    } else if (data.type === 'text') {
                        appendStreamChunk(data.content);
                        aiText += data.content;
                    } else if (data.type === 'thinking') {
                        const el = document.getElementById('thinkingText');
                        if (el) el.textContent = data.content;
                    } else if (data.type === 'error') {
                        removeStreamMsg();
                        appendError(data.content);
                        aiText = '';
                    }
                } catch {}
            }
        }
        if (aiText) finalizeStream();
        else removeStreamMsg();
        loadConversations();
    } catch (e) {
        removeStreamMsg();
        appendError('音频处理失败: ' + e.message);
    } finally {
        isProcessing = false;
        updateQueueUI();
        processNextInQueue();
    }
}

// ===== Generic File Handling (v2: 多文件支持) =====
async function sendFileMessage(text) {
    // ★ F6: 统一用 selectedFiles，删除 selectedFile
    if (!selectedFiles || selectedFiles.length === 0) return;

    document.getElementById('welcomeScreen').style.display = 'none';

    var files = selectedFiles.slice();

    if (files.length === 0) return;

    var fileNames = files.map(function(f) { return f.name; }).join(', ');
    appendUserMsg((text ? text + '\n' : '') + '📄 文件(' + files.length + '): ' + fileNames);

    selectedFiles = [];
    document.getElementById('filePreview').style.display = 'none';

    // 设置处理中状态
    isProcessing = true;
    if (typeof updateQueueUI === 'function') updateQueueUI();

    const thinkingDiv = typeof startAiStream === 'function' ? startAiStream() : null;
    const thinkingEl = document.getElementById('thinkingText');
    if (thinkingEl) thinkingEl.textContent = '📤 正在处理文件...';

    try {
        const formData = new FormData();
        files.forEach(f => formData.append('files', f));
        if (text) formData.append('message', text);
        if (typeof currentConvId !== 'undefined' && currentConvId) formData.append('conversation_id', currentConvId);

        // ★ 2026-07-05: 后端返回SSE流，不是JSON，需要用流式读取
        const r = await fetch('/api/chat/file', { method: 'POST', body: formData });
        const contentType = r.headers.get('content-type') || '';

        if (contentType.indexOf('text/event-stream') >= 0 || contentType.indexOf('text/plain') >= 0) {
            // SSE流式处理
            const reader = r.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let aiText = '';
            let hasError = false;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const payload = line.slice(6).trim();
                    if (payload === '[DONE]') break;
                    try {
                        const data = JSON.parse(payload);
                        if (data.type === 'text') {
                            if (typeof appendStreamChunk === 'function') {
                                appendStreamChunk(data.content);
                            } else {
                                const sc = document.getElementById('streamContent');
                                if (sc) sc.textContent += data.content;
                            }
                            aiText += data.content;
                        } else if (data.type === 'thinking') {
                            const el = document.getElementById('thinkingText');
                            if (el) el.textContent = data.content;
                        } else if (data.type === 'source') {
                            // 来源卡片
                            if (typeof appendSystemMsg === 'function') {
                                appendSystemMsg(data.content);
                            }
                        } else if (data.type === 'error') {
                            if (typeof removeStreamMsg === 'function') removeStreamMsg();
                            if (typeof appendError === 'function') appendError(data.content);
                            hasError = true;
                            aiText = '';
                        } else if (data.type === 'done') {
                            // 结束
                        }
                    } catch (e) {}
                }
            }
            if (aiText && !hasError && typeof finalizeStream === 'function') finalizeStream();
            else if (!aiText && !hasError) {
                if (typeof removeStreamMsg === 'function') removeStreamMsg();
            }
            if (typeof currentConvId !== 'undefined' && !currentConvId) {
                // 尝试从响应中获取conversation_id（如果有）
            }
            if (typeof loadConversations === 'function') loadConversations();
            if (typeof scheduleAutoClassify === 'function') scheduleAutoClassify();
        } else {
            // JSON响应（兼容旧逻辑）
            const data = await r.json();
            if (data.error) {
                if (thinkingDiv && typeof removeStreamMsg === 'function') removeStreamMsg();
                if (typeof appendError === 'function') appendError(data.error);
            } else {
                if (thinkingDiv) {
                    const streamEl = document.getElementById('streamContent');
                    if (streamEl) streamEl.textContent = data.response;
                    if (typeof finalizeStream === 'function') finalizeStream();
                }
                if (typeof currentConvId !== 'undefined' && !currentConvId) currentConvId = data.conversation_id;
                if (typeof loadConversations === 'function') loadConversations();
                if (typeof scheduleAutoClassify === 'function') scheduleAutoClassify();
            }
        }
    } catch (e) {
        if (thinkingDiv && typeof removeStreamMsg === 'function') removeStreamMsg();
        if (typeof appendError === 'function') appendError('文件处理失败: ' + e.message);
    } finally {
        isProcessing = false;
        if (typeof updateQueueUI === 'function') updateQueueUI();
        if (typeof processNextInQueue === 'function') processNextInQueue();
    }
}

// ===== F5: 音视频处理函数统一到 app-media.js（2026-07-05）=====

function handleVideoSelect(input) {
    if (input.files[0]) {
        selectedVideoFile = input.files[0];
        if (selectedVideoFile.size > 500 * 1024 * 1024) {
            toast('⚠️ 视频文件不能超过 500MB');
            selectedVideoFile = null;
            return;
        }
        document.getElementById('videoName').textContent = '🎬 ' + selectedVideoFile.name;
        document.getElementById('videoPreview').style.display = 'flex';
    }
    input.value = '';
}

function removeVideo() {
    selectedVideoFile = null;
    document.getElementById('videoPreview').style.display = 'none';
}

function handleAudioSelect(input) {
    if (input.files[0]) {
        selectedAudioFile = input.files[0];
        document.getElementById('audioName').textContent = '🎵 ' + selectedAudioFile.name;
        document.getElementById('audioPreview').style.display = 'flex';
    }
    input.value = '';
}

function removeAudio() {
    selectedAudioFile = null;
    document.getElementById('audioPreview').style.display = 'none';
}

// ===== G2: 多模态模型检测（2026-07-05）=====
// 不支持视频/音频的纯文本模型列表
const _TEXT_ONLY_MODELS = ['deepseek-r1', 'deepseek-v3', 'qwen2.5', 'qwen2', 'llama3', 'mistral', 'glm4', 'yi-'];

function isMultimodalModel() {
    var model = '';
    try { model = (window._lastModelSource && window._lastModelSource.model) || ''; } catch(e) {}
    if (!model) model = '';
    var modelLower = model.toLowerCase();
    // 支持多模态的模型
    var multimodalKeywords = ['gpt-4', 'gpt-4o', 'claude-3', 'claude-4', 'qwen-vl', 'qwen2-vl', 'glm-4v', 'glm4v', 'yi-vl', 'gemini', 'doubao-vision', 'step-1v', 'minicpm'];
    for (var i = 0; i < multimodalKeywords.length; i++) {
        if (modelLower.indexOf(multimodalKeywords[i]) >= 0) return true;
    }
    // 检查纯文本模型
    for (var j = 0; j < _TEXT_ONLY_MODELS.length; j++) {
        if (modelLower.indexOf(_TEXT_ONLY_MODELS[j]) >= 0) return false;
    }
    // 未知模型，默认不支持（保守策略）
    return false;
}

function getCurrentModelName() {
    try {
        if (window._lastModelSource && window._lastModelSource.label) return window._lastModelSource.label;
        if (window._lastModelSource && window._lastModelSource.model) return window._lastModelSource.model;
    } catch(e) {}
    return '当前模型';
}

function checkAndSendVideo() {
    if (!isMultimodalModel()) {
        var modelName = getCurrentModelName();
        alert(
            '⚠️ 当前模型不支持视频识别\n\n' +
            '您的当前模型：' + modelName + '\n\n' +
            '视频识别需要多模态模型（如 GPT-4o/Claude-3/Qwen-VL）。\n\n' +
            '解决方案：\n' +
            '1. 在「设置 → 模型管理 → 多模型」配置支持视觉的外脑模型\n' +
            '2. 切换到 🤖外脑模式\n' +
            '3. 如果只是想提取视频中的音频转文字，可以用「音频」功能上传MP3'
        );
        return;
    }
    triggerFileInput('videoInput');
}

function checkAndSendAudio() {
    if (!isMultimodalModel()) {
        var modelName = getCurrentModelName();
        alert(
            '⚠️ 当前模型不支持音频识别\n\n' +
            '您的当前模型：' + modelName + '\n\n' +
            '音频识别需要多模态模型或语音识别功能。\n\n' +
            '解决方案：\n' +
            '1. 在「设置 → 模型管理 → 多模型」配置支持音频的外脑模型\n' +
            '2. 切换到 🤖外脑模式\n' +
            '3. 如果想语音转文字，可以用 🎤录音功能（需要安装Whisper模型）'
        );
        return;
    }
    triggerFileInput('audioInput');
}

// ===== Image Handling =====
function handleImageSelect(input) {
    const files = Array.from(input.files);
    if (files.length === 0) return;
    
    // 合并到现有队列（最多10张）
    selectedImageFiles = [...selectedImageFiles, ...files].slice(0, 10);
    renderImageThumbs();
    input.value = '';
}

function renderImageThumbs() {
    const container = document.getElementById('imageThumbs');
    if (!container || selectedImageFiles.length === 0) {
        if (container) container.innerHTML = '';
        const preview = document.getElementById('imagePreview');
        if (preview) preview.style.display = 'none';
        return;
    }
    container.innerHTML = '';
    selectedImageFiles.forEach((f, i) => {
        const wrapper = document.createElement('div');
        wrapper.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:4px;position:relative;';
        const img = document.createElement('img');
        img.src = URL.createObjectURL(f);
        img.style.cssText = 'width:72px;height:72px;object-fit:cover;border-radius:6px;border:1px solid var(--border);';
        img.title = f.name;
        // ★ 2026-07-06: 修复删除按钮不好用——改用 data-index + 全局函数，避免闭包问题
        const rm = document.createElement('button');
        rm.textContent = '✕';
        rm.setAttribute('data-index', i);
        rm.style.cssText = 'font-size:12px;padding:2px 8px;background:#e53e3e;color:#fff;border:none;border-radius:4px;cursor:pointer;line-height:1.4;min-width:24px;min-height:20px;';
        rm.onclick = function(e) {
            e.preventDefault();
            e.stopPropagation();
            var idx = parseInt(this.getAttribute('data-index'));
            if (!isNaN(idx) && idx >= 0 && idx < selectedImageFiles.length) {
                selectedImageFiles.splice(idx, 1);
                renderImageThumbs();
            }
        };
        wrapper.appendChild(img);
        wrapper.appendChild(rm);
        container.appendChild(wrapper);
    });
    const preview = document.getElementById('imagePreview');
    if (preview) preview.style.display = selectedImageFiles.length > 0 ? 'flex' : 'none';
}

function removeAllImages() {
    selectedImageFiles = [];
    const thumbs = document.getElementById('imageThumbs');
    if (thumbs) thumbs.innerHTML = '';
    const preview = document.getElementById('imagePreview');
    if (preview) preview.style.display = 'none';
}
window.removeAllImages = removeAllImages;

function removeImage() { removeAllImages(); }

// ===== Specializations =====
async function loadSpecs() {
    try {
        const r = await fetch('/api/specializations');
        const specs = await r.json();
        const sel = document.getElementById('specSelect');
        sel.innerHTML = '<option value="">🎯 通用模式</option>';
        specs.forEach(s => {
            sel.innerHTML += `<option value="${s.id}">${_safeEscHtml(s.name)}</option>`;
        });
        renderSpecList(specs);
        if (currentSpecId) sel.value = currentSpecId;
    } catch {}
}

// ★ 2026-07-06: 局部 escHtml fallback，避免跨文件依赖
var _escHtmlDiv = document.createElement('div');
function _safeEscHtml(s) {
    if (typeof escHtml === 'function') return escHtml(s);
    if (!s) return '';
    _escHtmlDiv.textContent = String(s);
    return _escHtmlDiv.innerHTML;
}

function renderSpecList(specs) {
    const el = document.getElementById('specList');
    if (!el) return;
    if (!specs.length) { el.innerHTML = '<p style="color:var(--text-secondary);font-size:13px;">暂无专项培养</p>'; return; }
    el.innerHTML = '';
    specs.forEach(function(s) {
        var item = document.createElement('div');
        item.className = 'spec-item';

        var info = document.createElement('div');
        var nameEl = document.createElement('div');
        nameEl.className = 'spec-name';
        nameEl.textContent = s.name || '';
        var descEl = document.createElement('div');
        descEl.className = 'spec-desc';
        descEl.textContent = (s.system_prompt || '').substring(0, 60) + '...';
        info.appendChild(nameEl);
        info.appendChild(descEl);

        // ★ 2026-07-06: 删除按钮改用DOM创建+addEventListener，避免字符串拼接XSS风险
        var btn = document.createElement('button');
        btn.className = 'btn-del';
        btn.textContent = '删除';
        var specId = s.id;
        btn.addEventListener('click', function() {
            if (typeof delSpec === 'function') delSpec(specId);
        });

        item.appendChild(info);
        item.appendChild(btn);
        el.appendChild(item);
    });
}

function switchSpec(id) {
    currentSpecId = id || '';
    newChat();
    loadConversations();
    const indicator = document.getElementById('channelName');
    if (indicator) {
        const sel = document.getElementById('specSelect');
        const opt = sel.querySelector('option[value="' + id + '"]');
        indicator.textContent = opt ? opt.textContent : '💬 通用模式';
    }
}

async function saveSpec() {
    const name = document.getElementById('specName').value.trim();
    const prompt = document.getElementById('specPrompt').value.trim();
    if (!name || !prompt) { toast('请填写名称和提示词'); return; }
    try {
        await fetch('/api/specializations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, system_prompt: prompt, description: '' })
        });
        document.getElementById('specName').value = '';
        document.getElementById('specPrompt').value = '';
        loadSpecs();
        toast('保存成功');
    } catch { toast('保存失败'); }
}

async function delSpec(id) {
    if (!confirm('确定删除？')) return;
    try {
        await fetch(`/api/specializations/${id}`, { method: 'DELETE' });
        loadSpecs();
        toast('已删除');
    } catch { toast('删除失败'); }
}

// ===== Knowledge Base Auto-Import =====

// ===== Window 全局导出（F1: 统一变量+函数导出）=====
window.toggleAttachMenu = toggleAttachMenu;
window.triggerFileInput = triggerFileInput;
window.openCamera = openCamera;
window.closeCamera = closeCamera;
window.capturePhoto = capturePhoto;
window.retakePhoto = retakePhoto;
window.useCameraPhoto = useCameraPhoto;
window.handleFileSelect = handleFileSelect;
window.removeFile = removeFile;
window.sendAudioFileMessage = sendAudioFileMessage;
window.sendFileMessage = sendFileMessage;
window.handleImageSelect = handleImageSelect;
window.removeImage = removeImage;
window.checkAndSendVideo = checkAndSendVideo;
window.checkAndSendAudio = checkAndSendAudio;
window.isMultimodalModel = isMultimodalModel;
window.handleVideoSelect = handleVideoSelect;
window.removeVideo = removeVideo;
window.handleAudioSelect = handleAudioSelect;
window.removeAudio = removeAudio;
window.loadSpecs = loadSpecs;
window.renderSpecList = renderSpecList;
// F1: 变量全局访问
window.selectedFiles = selectedFiles;
window.selectedImageFiles = selectedImageFiles;
window.selectedAudioFile = selectedAudioFile;
window.selectedVideoFile = selectedVideoFile;
window.switchSpec = switchSpec;
window.saveSpec = saveSpec;
window.delSpec = delSpec;
