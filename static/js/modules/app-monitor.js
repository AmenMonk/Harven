/* Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0. */
/* ===== Harven AI — MONITOR module ===== */
// ===== Monitor =====
let currentMonitorTopicId = null;

function showAddTopic() {
    document.getElementById('topicName').value = '';
    document.getElementById('topicKeywords').value = '';
    document.getElementById('srcRss').checked = true;
    document.getElementById('srcWeb').checked = true;
    document.getElementById('srcGithub').checked = false;
    openModal('addTopicModal');
}

async function addTopic() {
    const topicName = document.getElementById('topicName').value.trim();
    const keywordsStr = document.getElementById('topicKeywords').value.trim();
    
    if (!topicName) {
        toast('请输入主题名称');
        return;
    }
    if (!keywordsStr) {
        toast('请输入关键词');
        return;
    }
    
    const keywords = keywordsStr.split(/[,，]/).map(k => k.trim()).filter(k => k);
    const sources = [];
    if (document.getElementById('srcRss').checked) sources.push('rss');
    if (document.getElementById('srcWeb').checked) sources.push('web');
    if (document.getElementById('srcGithub').checked) sources.push('github');
    
    if (!sources.length) {
        toast('请至少选择一个数据源');
        return;
    }
    
    // 禁用按钮防重复点击
    const btn = document.querySelector('#addTopicModal button.btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 添加中...'; }
    
    try {
        const r = await fetch('/api/monitor/topics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic_name: topicName, keywords: keywords, sources: sources })
        });
        const data = await r.json();
        
        if (data.success) {
            closeModal('addTopicModal');
            
            // 立即刷新主题列表
            await loadMonitorTopics();
            
            // 选中新主题
            if (data.id) {
                selectTopic(data.id, topicName, keywords.join(', '));
            }
            
            // 提示：后台检查中
            toast(`✅ 已添加「${topicName}」，后台检查中（约30-60秒）...`);
            
            // 3秒后自动查询结果
            if (data.id) {
                setTimeout(async () => {
                    try {
                        await loadMonitorItems(data.id);
                        await updateTopicUnread(data.id);
                        await updateMonitorBadge();
                        // 检查是否有条目了
                        const itemsEl = document.getElementById('monitorItemsList');
                        const count = itemsEl ? itemsEl.children.length : 0;
                        if (count > 0) {
                            toast(`🔔 「${topicName}」检查完成，发现 ${count} 条内容`);
                        } else {
                            toast(`⚠️ 「${topicName}」检查完成，未发现新内容 | 可尝试更宽泛的关键词`);
                        }
                    } catch(e) { /* 静默 */ }
                }, 3000);
            }
        } else {
            toast('❌ 添加失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('❌ 添加失败: ' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '添加'; }
    }
}

async function loadMonitorTopics() {
    try {
        const r = await fetch('/api/monitor/topics');
        const data = await r.json();
        const topics = data.topics || [];
        
        const el = document.getElementById('monitorTopicsList');
        if (!topics.length) {
            el.innerHTML = '<p style="color:var(--text-secondary); font-size:13px;">暂无监控主题</p>';
            document.getElementById('monitorDetail').style.display = 'none';
            document.getElementById('monitorEmpty').style.display = 'block';
            return;
        }
        
        el.innerHTML = topics.map(t => `
            <div class="kb-item" style="cursor:pointer; margin-bottom:8px;" onclick="selectTopic(${t.id}, '${escHtml(t.topic_name)}', '${escHtml(t.keywords.join(', '))}')">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span>${escHtml(t.topic_name)}</span>
                    ${t.last_check_at ? '<span style="font-size:11px; color:var(--text-secondary);">已检查</span>' : '<span style="font-size:11px; color:var(--text-secondary);">未检查</span>'}
                </div>
            </div>
        `).join('');
        
        // 更新未读计数
        updateMonitorBadge();
    } catch (e) {
        console.error('加载监控主题失败:', e);
    }
}

async function selectTopic(topicId, topicName, keywords) {
    currentMonitorTopicId = topicId;
    document.getElementById('monitorTopicTitle').textContent = topicName;
    document.getElementById('monitorKeywords').textContent = keywords;
    document.getElementById('monitorDetail').style.display = 'block';
    document.getElementById('monitorEmpty').style.display = 'none';
    
    // 高亮选中的主题
    document.querySelectorAll('#monitorTopicsList .kb-item').forEach(el => {
        el.style.background = '';
    });
    event.currentTarget.style.background = 'var(--hover)';
    
    // 加载未读计数
    await updateTopicUnread(topicId);
    
    // 加载条目
    await loadMonitorItems(topicId);
}

async function updateTopicUnread(topicId) {
    try {
        const r = await fetch(`/api/monitor/items?topic_id=${topicId}&unread=true&limit=1`);
        const data = await r.json();
        document.getElementById('monitorUnread').textContent = data.items ? data.items.length : 0;
    } catch {}
}

async function checkTopicNow() {
    if (!currentMonitorTopicId) {
        toast('请先选择一个主题');
        return;
    }
    
    const btn = document.querySelector('#monitorDetail button[onclick="checkTopicNow()"]');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 检查中...'; }
    
    try {
        const r = await fetch(`/api/monitor/check/${currentMonitorTopicId}`, { method: 'POST' });
        const data = await r.json();
        
        if (data.success) {
            if (data.count > 0) {
                toast(`✅ 检查完成，发现 ${data.count} 条新内容`);
            } else {
                toast('⚠️ 检查完成，未发现新内容 | 可尝试更宽泛的关键词');
            }
            await loadMonitorItems(currentMonitorTopicId);
            await updateTopicUnread(currentMonitorTopicId);
            await updateMonitorBadge();
        } else {
            toast('❌ 检查失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        toast('❌ 检查失败: ' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🔄 立即检查'; }
    }
}

async function deleteTopic() {
    if (!currentMonitorTopicId) return;
    if (!confirm('确定删除此监控主题？')) return;
    
    try {
        const r = await fetch(`/api/monitor/topics/${currentMonitorTopicId}`, { method: 'DELETE' });
        const data = await r.json();
        
        if (data.success) {
            toast('已删除');
            currentMonitorTopicId = null;
            document.getElementById('monitorDetail').style.display = 'none';
            document.getElementById('monitorEmpty').style.display = 'block';
            loadMonitorTopics();
        }
    } catch (e) {
        toast('删除失败: ' + e.message);
    }
}

async function filterMonitorItems(filter) {
    if (!currentMonitorTopicId) return;
    
    document.getElementById('btnMonitorAll').style.background = filter === 'all' ? 'var(--primary)' : '';
    document.getElementById('btnMonitorAll').style.color = filter === 'all' ? 'white' : '';
    document.getElementById('btnMonitorUnread').style.background = filter === 'unread' ? 'var(--primary)' : '';
    document.getElementById('btnMonitorUnread').style.color = filter === 'unread' ? 'white' : '';
    
    await loadMonitorItems(currentMonitorTopicId, filter === 'unread');
}

async function loadMonitorItems(topicId, unreadOnly = false) {
    try {
        const url = `/api/monitor/items?topic_id=${topicId}&unread=${unreadOnly}&limit=50`;
        const r = await fetch(url);
        const data = await r.json();
        const items = data.items || [];
        
        const el = document.getElementById('monitorItemsList');
        if (!items.length) {
            el.innerHTML = '<p style="color:var(--text-secondary); font-size:13px;">暂无内容</p>';
            return;
        }
        
        el.innerHTML = items.map(item => `
            <div class="kb-item ${item.read_status ? '' : 'unread'}" style="margin-bottom:10px; ${item.read_status ? '' : 'border-left: 3px solid var(--primary);'}">
                <div style="font-weight:600; margin-bottom:5px;">
                    <a href="${item.url || '#'}" target="_blank" style="color:var(--text); text-decoration:none;">${escHtml(item.title)}</a>
                </div>
                <div style="font-size:12px; color:var(--text-secondary); margin-bottom:8px;">${escHtml(item.content || '').substring(0, 200)}</div>
                <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px;">
                    <span style="color:var(--text-secondary);">${item.source_type} | ${item.published_at ? new Date(item.published_at).toLocaleString() : ''}</span>
                    ${item.read_status ? '' : `<button class="btn-secondary btn-sm" onclick="markRead(${item.id})">标为已读</button>`}
                </div>
            </div>
        `).join('');
    } catch (e) {
        console.error('加载监控条目失败:', e);
    }
}

async function markRead(itemId) {
    try {
        await fetch(`/api/monitor/read/${itemId}`, { method: 'POST' });
        if (currentMonitorTopicId) {
            await loadMonitorItems(currentMonitorTopicId);
            await updateTopicUnread(currentMonitorTopicId);
            await updateMonitorBadge();
        }
    } catch (e) {
        console.error('标记已读失败:', e);
    }
}

async function updateMonitorBadge() {
    try {
        const r = await fetch('/api/monitor/unread_count');
        const data = await r.json();
        const badge = document.getElementById('monitorBadge');
        if (data.count > 0) {
            badge.textContent = data.count > 99 ? '99+' : data.count;
            badge.style.display = 'block';
        } else {
            badge.style.display = 'none';
        }
    } catch {}
}

// ⚠️ 以下函数均定义在 app-settings.js 中：
//    openModal, closeModal, loadTasks, renderTasks, filterTasks, addTask,
//    toggleTask, deleteTask, loadKbList, loadSpecializations, escHtml,
//    renderMd, scrollBottom, toast, autoClassify, scheduleAutoClassify,
//    loadSettings, previewModel, saveAllSettings, resetSettings 等

// ===== Window 全局导出（仅 monitor 专有函数）=====
window.showAddTopic = showAddTopic;
window.addTopic = addTopic;
window.loadMonitorTopics = loadMonitorTopics;
window.selectTopic = selectTopic;
window.updateTopicUnread = updateTopicUnread;
window.checkTopicNow = checkTopicNow;
window.deleteTopic = deleteTopic;
window.filterMonitorItems = filterMonitorItems;
window.loadMonitorItems = loadMonitorItems;
window.markRead = markRead;
window.updateMonitorBadge = updateMonitorBadge;
