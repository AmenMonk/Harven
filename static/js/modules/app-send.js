/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — SEND module ===== */
// ===== 队列面板渲染（WorkBuddy风格） =====
function updateQueueUI() {
    const panel  = document.getElementById('msgQueuePanel');
    const body   = document.getElementById('mqBody');
    const total  = document.getElementById('mqTotal');
    if (!panel || !body || !total) return;

    total.textContent = msgQueue.length;

    if (msgQueue.length === 0) {
        panel.style.display = 'none';
        // 恢复状态栏文字
        const status = document.getElementById('modelStatus');
        if (status && isProcessing) status.textContent = '🟡 生成中...';
        return;
    }

    panel.style.display = 'block';

    // 渲染队列条目
    body.innerHTML = '';
    msgQueue.forEach((item, idx) => {
        const div = document.createElement('div');
        div.className = 'mq-item';
        div.dataset.idx = idx;
        div.draggable = true;

        // 拖拽事件
        div.addEventListener('dragstart', (e) => { handleDragStart(e, idx); });
        div.addEventListener('dragover',  (e) => { e.preventDefault(); handleDragOver(e, idx); });
        div.addEventListener('dragend',   ()   => { handleDragEnd(); });
        // 移动端 touch 拖拽（简化：用上移/下移按钮代替）
        // 桌面端用 HTML5 drag & drop

        // 内容
        const preview = (item.text || '').substring(0, 60) || '[图片]';
        const typeLabel = item.type === 'image' ? '🖼️ 图片' : '💬 文字';

        div.innerHTML =
            '<span class="mq-drag-handle">⠿</span>' +
            '<span class="mq-item-type">' + typeLabel + '</span>' +
            '<span class="mq-item-preview" title="' + escHtml(item.text||'[图片]') + '">' + escHtml(preview) + '</span>' +
            '<span class="mq-item-actions">' +
                (idx > 0
                    ? '<button class="mq-action-btn" title="上移" onclick="moveQueueItem(' + idx + ',-1)">↑</button>'
                    : '') +
                (idx < msgQueue.length - 1
                    ? '<button class="mq-action-btn" title="下移" onclick="moveQueueItem(' + idx + ',1)">↓</button>'
                    : '') +
                '<button class="mq-action-btn delete" title="删除" onclick="removeQueueItem(' + idx + ')">✕</button>' +
            '</span>';

        body.appendChild(div);
    });

    // 状态栏也要显示排队信息
    const status = document.getElementById('modelStatus');
    if (status && isProcessing) {
        status.textContent = '🟡 生成中...（排队 ' + msgQueue.length + ' 条）';
    }
}

// 拖拽排序
function handleDragStart(e, idx) {
    dragIndex = idx;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', idx);
    setTimeout(() => { if (e.target) e.target.classList.add('dragging'); }, 0);
}
function handleDragOver(e, idx) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    dropIndex = idx;
    // 视觉反馈：给目标位置加高亮
    const items = document.querySelectorAll('.mq-item');
    items.forEach(el => el.classList.remove('drag-over'));
    if (items[idx]) items[idx].classList.add('drag-over');
}
function handleDragEnd() {
    const items = document.querySelectorAll('.mq-item');
    items.forEach(el => { el.classList.remove('dragging','drag-over'); });
    // 执行拖拽排序
    if (dragIndex >= 0 && dropIndex >= 0 && dragIndex !== dropIndex && dropIndex < msgQueue.length) {
        const [moved] = msgQueue.splice(dragIndex, 1);
        msgQueue.splice(dropIndex, 0, moved);
    }
    dragIndex = -1;
    dropIndex = -1;
    updateQueueUI();
}

// 队列操作
function moveQueueItem(idx, dir) {
    const newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= msgQueue.length) return;
    [msgQueue[idx], msgQueue[newIdx]] = [msgQueue[newIdx], msgQueue[idx]];
    updateQueueUI();
}

function removeQueueItem(idx) {
    msgQueue.splice(idx, 1);
    updateQueueUI();
}

function clearQueue() {
    if (msgQueue.length === 0) return;
    if (!confirm('确定清空所有排队消息？')) return;
    msgQueue = [];
    updateQueueUI();
    toast('已清空消息队列');
}

// 折叠/展开队列面板
document.addEventListener('DOMContentLoaded', () => {
    // 初始化队列面板状态（防止页面加载时面板状态与队列数据不一致）
    updateQueueUI();

    const header = document.getElementById('mqHeader');
    if (header) {
        header.addEventListener('click', (e) => {
            if (e.target.closest('.mq-actions')) return; // 点击按钮不触发展开/折叠
            const panel = document.getElementById('msgQueuePanel');
            if (panel) panel.classList.toggle('collapsed');
        });
    }
});

function stopStream() {
    if (abortController) abortController.abort();
    // 用户手动停止 → 清空排队消息
    msgQueue = [];
    updateQueueUI();
}

async function sendImageMessage(text) {
    const formData = new FormData();
    if (selectedImageFiles && selectedImageFiles.length > 0) {
        selectedImageFiles.forEach(f => formData.append('images', f));
    }
    if (text) formData.append('message', text);
    if (currentConvId) formData.append('conversation_id', currentConvId);

    document.getElementById('welcomeScreen').style.display = 'none';
    appendUserMsg('[图片]' + (text ? ' ' + text : ''));
    removeImage();

    // 设置处理中状态
    isProcessing = true;
    updateQueueUI();

    const thinkingDiv = startAiStream();
    document.getElementById('thinkingText').textContent = '正在分析图片...';

    try {
        const r = await fetch('/api/chat/image', { method: 'POST', body: formData });
        const data = await r.json();
        if (data.error) {
            removeStreamMsg();
            appendError(data.error);
        } else {
            const streamEl = document.getElementById('streamContent');
            streamEl.textContent = data.response;
            finalizeStream();
            if (!currentConvId) currentConvId = data.conversation_id;
            loadConversations();
            scheduleAutoClassify();
        }
    } catch (e) {
        removeStreamMsg();
        appendError('图片分析失败: ' + e.message);
    } finally {
        isProcessing = false;
        updateQueueUI();
        processNextInQueue();
    }
}

function appendError(text) {
    const el = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'msg assistant';
    div.innerHTML = `<div class="msg-avatar">⚡</div><div class="msg-body" style="color:var(--danger)">⚠️ ${escHtml(text)}</div>`;
    el.appendChild(div);
    scrollBottom();
}

function appendSystemMsg(text) {
    // 系统消息（GitHub下载/知识库/搜索结果等）
    const el = document.getElementById('messages');
    const div = document.createElement('div');
    div.className = 'msg system-msg';
    div.innerHTML = `<div class="msg-body" style="font-size:12px;color:var(--text-secondary);padding:4px 8px;margin:2px 0;border-left:3px solid var(--accent);opacity:0.85">${escHtml(text)}</div>`;
    el.appendChild(div);
    scrollBottom();
}

function quickSend(text) {
    document.getElementById('msgInput').value = text;
    sendMessage();
}

// ===== Voice Recording (PCM → WAV, no ffmpeg needed) =====
let voiceCtx = null;
let voiceProcessor = null;
let voiceStream = null;
let voiceChunks = [];  // Float32Array chunks
let voiceBlob = null;

function startVoiceRecord() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        toast('⚠️ 浏览器不支持录音，请使用 Chrome/Edge');
        return;
    }
    navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true } })
    .then(stream => {
        voiceStream = stream;
        voiceChunks = [];
        voiceCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const source = voiceCtx.createMediaStreamSource(stream);
        voiceProcessor = voiceCtx.createScriptProcessor(4096, 1, 1);
        voiceProcessor.onaudioprocess = e => {
            voiceChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
        };
        source.connect(voiceProcessor);
        voiceProcessor.connect(voiceCtx.destination);

        document.getElementById('voiceIndicator').style.display = 'block';
        document.getElementById('voiceBtn').classList.add('recording');
        document.getElementById('voiceBtn').innerHTML = '⏹ 停止';
    }).catch(err => {
        toast('⚠️ 麦克风权限被拒绝: ' + err.message);
    });
}

function stopVoiceRecord() {
    if (voiceProcessor) { voiceProcessor.disconnect(); voiceProcessor = null; }
    if (voiceCtx) { voiceCtx.close(); voiceCtx = null; }
    if (voiceStream) { voiceStream.getTracks().forEach(t => t.stop()); voiceStream = null; }
    document.getElementById('voiceIndicator').style.display = 'none';
    document.getElementById('voiceBtn').classList.remove('recording');
    document.getElementById('voiceBtn').innerHTML = '🎤 录音';

    // Encode PCM chunks to WAV
    if (voiceChunks.length === 0) {
        toast('录音太短，请重试');
        return;
    }
    const wavBlob = encodePCMtoWAV(voiceChunks, 16000);
    voiceChunks = [];
    if (wavBlob.size < 2000) {
        toast('录音太短（至少说1秒），请重试');
        return;
    }
    voiceBlob = wavBlob;
    sendVoiceMessage();
}

function encodePCMtoWAV(chunks, sampleRate) {
    // Concatenate all Float32 chunks
    let totalLen = 0;
    for (const c of chunks) totalLen += c.length;
    const pcm = new Float32Array(totalLen);
    let offset = 0;
    for (const c of chunks) { pcm.set(c, offset); offset += c.length; }

    // Convert Float32 [-1,1] to Int16
    const int16 = new Int16Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) {
        let s = Math.max(-1, Math.min(1, pcm[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }

    // Build WAV header + data
    const dataLen = int16.length * 2;
    const buffer = new ArrayBuffer(44 + dataLen);
    const view = new DataView(buffer);

    // RIFF header
    writeString(view, 0, 'RIFF');
    view.setUint32(4, 36 + dataLen, true);
    writeString(view, 8, 'WAVE');

    // fmt chunk
    writeString(view, 12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);     // PCM
    view.setUint16(22, 1, true);     // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);

    // data chunk
    writeString(view, 36, 'data');
    view.setUint32(40, dataLen, true);

    // PCM data
    const raw = new Uint8Array(buffer, 44);
    const raw16 = new Uint8Array(int16.buffer);
    raw.set(raw16);

    return new Blob([buffer], { type: 'audio/wav' });
}

function writeString(view, offset, str) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
}

function toggleVoiceRecord() {
    if (voiceProcessor || (voiceCtx && voiceCtx.state === 'running')) {
        stopVoiceRecord();
    } else {
        startVoiceRecord();
    }
}

function getSupportedMimeType() {
    return 'audio/wav';
}

async function sendVoiceMessage() {
    if (!voiceBlob) return;
    const input = document.getElementById('msgInput');

    // 显示转写中状态（不发送AI消息）
    const statusEl = document.getElementById('voiceIndicator');
    if (statusEl) statusEl.textContent = '🔄 转写中...';
    document.getElementById('voiceBtn').disabled = true;

    const formData = new FormData();
    formData.append('audio', voiceBlob, 'voice.wav');
    voiceBlob = null;

    try {
        // 调用纯转写接口，不走AI
        const resp = await fetch('/api/audio/transcribe', { method: 'POST', body: formData });
        const result = await resp.json();

        if (result.error) {
            toast('⚠️ ' + result.error);
        } else if (result.text && result.text.trim()) {
            // 将转写文字放入输入框，让用户自己决定何时发送
            input.value = result.text.trim();
            input.focus();
            toast('✅ 转写完成，按下发送或回车键发送');
        } else {
            toast('⚠️ 未识别到语音内容，请重试');
        }
    } catch (e) {
        toast('⚠️ 语音转写失败: ' + e.message);
    } finally {
        if (statusEl) statusEl.textContent = '';
        document.getElementById('voiceBtn').disabled = false;
        // 清除残留的转写中提示
        const thinkEl = document.getElementById('thinkingText');
        if (thinkEl) thinkEl.textContent = '';
    }
}

// ===== Video Handling =====
// ★ F5: handleVideoSelect/removeVideo/selectedVideoFile 已移到 app-media.js 统一管理
// sendVideoMessage 保留在这里（发送逻辑）

async function sendVideoMessage(text) {
    if (!selectedVideoFile) return;

    document.getElementById('welcomeScreen').style.display = 'none';
    appendUserMsg('[视频]' + (text ? ' ' + text : ''));

    const formData = new FormData();
    formData.append('video', selectedVideoFile);
    formData.append('message', text || '请分析这个视频');
    if (currentConvId) formData.append('conversation_id', currentConvId);

    selectedVideoFile = null;
    document.getElementById('videoPreview').style.display = 'none';

    // 设置处理中状态，防止队列消息同时发送
    isProcessing = true;
    updateQueueUI();

    const thinkingDiv = startAiStream();
    document.getElementById('thinkingText').textContent = '🎬 正在分析视频...';

    try {
        const resp = await fetch('/api/chat/video', { method: 'POST', body: formData });
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
                    if (data.type === 'text') {
                        appendStreamChunk(data.content);
                        aiText += data.content;
                    } else if (data.type === 'thinking') {
                        const el = document.getElementById('thinkingText');
                        if (el) el.textContent = data.content;
                    } else if (data.type === 'error') {
                        removeStreamMsg();
                        appendError(data.content);
                        aiText = ''; // 标记失败
                    }
                } catch {}
            }
        }
        if (aiText) finalizeStream();
        else removeStreamMsg();
        loadConversations();
        scheduleAutoClassify();
    } catch (e) {
        removeStreamMsg();
        appendError('视频分析失败: ' + e.message);
    } finally {
        isProcessing = false;
        updateQueueUI();
        processNextInQueue();
    }
}

// ===== Audio File Handling =====
// ★ F5: handleAudioSelect/removeAudio/selectedAudioFile 已移到 app-media.js 统一管理
// sendAudioFileMessage 保留在这里（发送逻辑在 app-media.js）



// ===== Window 全局导出（由自检脚本补全）=====
window.updateQueueUI = updateQueueUI;
window.handleDragStart = handleDragStart;
window.handleDragOver = handleDragOver;
window.handleDragEnd = handleDragEnd;
window.moveQueueItem = moveQueueItem;
window.removeQueueItem = removeQueueItem;
window.clearQueue = clearQueue;
window.stopStream = stopStream;
window.sendImageMessage = sendImageMessage;
window.appendError = appendError;
window.appendSystemMsg = appendSystemMsg;
window.quickSend = quickSend;
window.startVoiceRecord = startVoiceRecord;
window.stopVoiceRecord = stopVoiceRecord;
window.encodePCMtoWAV = encodePCMtoWAV;
window.writeString = writeString;
window.toggleVoiceRecord = toggleVoiceRecord;
window.getSupportedMimeType = getSupportedMimeType;
window.sendVoiceMessage = sendVoiceMessage;
// ★ F5: handleVideoSelect/removeVideo/handleAudioSelect/removeAudio 已移到 app-media.js
window.sendVideoMessage = sendVideoMessage;
