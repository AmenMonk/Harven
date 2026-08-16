/**
 * app-im.js — IM 渠道接入管理模块
 * P0-3 渠道接入后端联通 · 前端交互
 */
let _imCurrentPlatform = 'wecom';
let _imEditingId      = null;

// ── 平台配置字段定义 ─────────────────
const IM_PLATFORM_FIELDS = {
  wecom: [
    {key:'corpid',     label:'CorpID',     placeholder:'企业ID'},
    {key:'agentid',    label:'AgentID',    placeholder:'应用ID'},
    {key:'secret',     label:'Secret',      placeholder:'应用Secret', type:'password'},
    {key:'token',      label:'Webhook Token', placeholder:'群机器人Webhook Key', required:false},
  ],
  dingtalk: [
    {key:'appkey',     label:'AppKey',     placeholder:'钉钉应用AppKey'},
    {key:'appsecret',  label:'AppSecret',  placeholder:'钉钉应用AppSecret', type:'password'},
    {key:'token',      label:'Webhook Token', placeholder:'群机器人Webhook Token', required:false},
  ],
  feishu: [
    {key:'app_id',       label:'App ID',        placeholder:'飞书应用App ID'},
    {key:'app_secret',   label:'App Secret',    placeholder:'飞书应用App Secret', type:'password'},
    {key:'verification_token', label:'Verification Token', placeholder:'事件订阅Verification Token', required:false},
  ],
  qqbot: [
    {key:'appid',     label:'AppID',     placeholder:'QQ机器人AppID'},
    {key:'token',     label:'Bot Token', placeholder:'QQBot xxxx格式', type:'password'},
    {key:'secret',    label:'Client Secret', placeholder:'可选', required:false, type:'password'},
  ],
};

// ── 渲染配置字段 ─────────────────
function imRenderConfigFields() {
  const fields = IM_PLATFORM_FIELDS[_imCurrentPlatform] || [];
  let html = '';
  fields.forEach(f => {
    const req = f.required ? '' : '（可选）';
    const type = f.type || 'text';
    html += `
    <div class="im-form-group">
      <label>${f.label} ${req}</label>
      <input type="${type}" id="im-cfg-${f.key}" class="im-input" placeholder="${f.placeholder}">
    </div>`;
  });
  document.getElementById('im-config-fields').innerHTML = html;
}

// ── 平台选择 ────────────────────
function imSelectPlatform(btn) {
  document.querySelectorAll('.im-platform-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  _imCurrentPlatform = btn.dataset.platform;
  imRenderConfigFields();
  const el = document.getElementById('im-test-result');
  if (el) el.innerHTML = '';
}

// ── 弹窗控制 ────────────────────
function imShowAddModal() {
  _imEditingId = null;
  const titleEl = document.getElementById('im-modal-title');
  if (titleEl) titleEl.textContent = '添加渠道';
  const nameEl = document.getElementById('im-channel-name');
  if (nameEl) nameEl.value = '';
  _imCurrentPlatform = 'wecom';
  document.querySelectorAll('.im-platform-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.platform === 'wecom');
  });
  imRenderConfigFields();
  const resultEl = document.getElementById('im-test-result');
  if (resultEl) resultEl.innerHTML = '';
  const overlay = document.getElementById('im-modal-overlay');
  if (overlay) overlay.style.display = 'flex';
}

function imHideModal() {
  const overlay = document.getElementById('im-modal-overlay');
  if (overlay) overlay.style.display = 'none';
}

// ── 读取当前表单配置 ─────────────────
function imGetFormConfig() {
  const fields = IM_PLATFORM_FIELDS[_imCurrentPlatform] || [];
  const config = {};
  fields.forEach(f => {
    const el = document.getElementById(`im-cfg-${f.key}`);
    if (el && el.value.trim()) config[f.key] = el.value.trim();
  });
  return config;
}

// ── 测试连接 ────────────────────
function imTestConnection() {
  const name   = document.getElementById('im-channel-name');
  const nameVal = name ? name.value.trim() : '';
  const config  = imGetFormConfig();
  const resultEl = document.getElementById('im-test-result');
  if (resultEl) resultEl.innerHTML = '<span class="im-testing">⏳ 测试中…</span>';

  fetch('/api/im/test', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({platform:_imCurrentPlatform, config})
  }).then(r=>r.json()).then(d=>{
    if (resultEl) {
      if (d.success) {
        resultEl.innerHTML = `<span class="im-test-ok">✅ ${d.message}</span>`;
      } else {
        resultEl.innerHTML = `<span class="im-test-fail">❌ 连接失败：${d.error||'未知错误'}</span>`;
      }
    }
  }).catch(e=>{
    if (resultEl) resultEl.innerHTML = `<span class="im-test-fail">❌ 请求失败：${e}</span>`;
  });
}

// ── 保存渠道 ────────────────────
function imSaveChannel() {
  const nameEl = document.getElementById('im-channel-name');
  const nameVal = nameEl ? nameEl.value.trim() : '';
  if (!nameVal) { alert('请输入渠道名称'); return; }
  const config  = imGetFormConfig();
  const body = {platform:_imCurrentPlatform, name:nameVal, config};

  fetch('/api/im/channels', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)
  }).then(r=>r.json()).then(d=>{
    if (d.success) {
      imHideModal();
      imRefreshList();
    } else {
      alert('保存失败：' + (d.error||'未知错误'));
    }
  }).catch(e=>alert('请求失败：'+e));
}

// ── 刷新渠道列表 ─────────────────
function imRefreshList() {
  fetch('/api/im/channels').then(r=>r.json()).then(d=>{
    if (!d.success) { console.error('加载渠道失败', d.error); return; }
    imRenderChannelList(d.channels);
  }).catch(e=>console.error('加载渠道失败', e));
}

// ── 渲染渠道列表 ─────────────────
function imRenderChannelList(channels) {
  const el = document.getElementById('im-channel-list');
  if (!el) return;
  if (!channels || channels.length === 0) {
    el.innerHTML = '<div class="im-empty">暂无渠道，点击"添加渠道"开始配置</div>';
    return;
  }
    const ICONS = {wecom:'ⓦ', dingtalk:'②', feishu:'📖', qqbot:'🐧', qq:'🐧'};
  let html = '<div class="im-channel-cards">';
  channels.forEach(ch => {
    const icon = ICONS[ch.platform] || '❓';
    const cls  = ch.enabled ? 'im-card-enabled' : 'im-card-disabled';
    // 安全序列化 ID 和名称
    const safeId = String(ch.id).replace(/'/g, "\\'").replace(/"/g, '&quot;');
    const safeName = (ch.name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    html += `
    <div class="im-channel-card ${cls}" data-id="${safeId}">
      <div class="im-card-top">
        <span class="im-card-icon">${icon}</span>
        <span class="im-card-name">${safeName}</span>
        <span class="im-card-platform">${ch.platform}</span>
      </div>
      <div class="im-card-actions">
        <label class="im-toggle">
          <input type="checkbox" ${ch.enabled?'checked':''} onchange="imToggleChannel('${safeId}', this.checked)">
          <span class="im-toggle-slider"></span>
          <span class="im-toggle-label">${ch.enabled?'已启用':'已禁用'}</span>
        </label>
        <button class="btn btn-xs btn-outline" onclick="imDeleteChannel('${safeId}', '${safeName}')">删除</button>
      </div>
    </div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ── 启用/禁用渠道 ─────────────────
function imToggleChannel(channelId, enabled) {
  fetch(`/api/im/channels/${channelId}/toggle`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({enabled})
  }).then(r=>r.json()).then(d=>{
    if (!d.success) alert('操作失败：' + (d.error||''));
    else imRefreshList();
  }).catch(e=>alert('请求失败：'+e));
}

// ── 删除渠道 ────────────────────
function imDeleteChannel(channelId, name) {
  if (!confirm(`确定删除渠道「${name}」？此操作不可撤销。`)) return;
  fetch(`/api/im/channels/${channelId}`, {
    method: 'DELETE', headers: {'Content-Type': 'application/json'}
  }).then(r => r.json()).then(d => {
    if (d.success) {
      imRefreshList();
    } else {
      alert('删除失败：' + (d.error || '未知错误'));
    }
  }).catch(e => alert('请求失败：' + e));
}

// ── 初始化 ──────────────────────
// 由 index.html 的 Tab 切换懒加载调用
// 当切换到 im 标签页时调用 imRefreshList()

// ===== Window 全局导出（由自检脚本补全）=====
window.imRenderConfigFields = imRenderConfigFields;
window.imSelectPlatform = imSelectPlatform;
window.imShowAddModal = imShowAddModal;
window.imHideModal = imHideModal;
window.imGetFormConfig = imGetFormConfig;
window.imTestConnection = imTestConnection;
window.imSaveChannel = imSaveChannel;
window.imRefreshList = imRefreshList;
window.imRenderChannelList = imRenderChannelList;
window.imToggleChannel = imToggleChannel;
window.imDeleteChannel = imDeleteChannel;
