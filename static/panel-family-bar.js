/**
 * 哈文面板·家人条 v2（通用组件，2026-10-21 家人健康总纲）
 * 用法：页面放 <div id="hvFamilyBar"></div>，然后
 *   HvFamilyBar.mount({ container: '#hvFamilyBar', onPick: (name)=>{...} })
 * v2 变更：
 *  - 32 字段六层分组资料弹窗（基础/健康/饮食/成长/社会/生活 + 自定义10）
 *  - 星座自动显示（后端算好 zodiac）
 *  - 按关系智能显隐：孩子默认展开成长层，大人默认展开社会层
 *  - ＋家人 弃 prompt()（iframe 内被浏览器拦，实锤「添加不好用」）→ 自绘弹窗
 * 数据真家：panel_members（GET/POST/PUT /api/panels/members）
 */
(function () {
  const COLOR = 'var(--hv-blue,#4A7DFF)';
  const css = `
  .hvfb{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 0 10px;border-bottom:1px dashed #E6EAF2;margin-bottom:10px}
  .hvfb-t{font-size:12.5px;font-weight:600;color:#3A4453}
  .hvfb-chip{display:inline-flex;align-items:center;gap:5px;border:1px solid #E6EAF2;border-radius:16px;padding:3px 11px;font-size:12.5px;cursor:pointer;background:#fff;color:#3A4453}
  .hvfb-chip.on{border-color:${COLOR};color:${COLOR};font-weight:600;background:#F3F7FE}
  .hvfb-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
  .hvfb-add{border:1px solid ${COLOR};background:${COLOR};color:#fff;border-radius:16px;padding:4px 12px;font-size:12.5px;cursor:pointer;font-weight:600}
  .hvfb-prof{border:1px solid #E6EAF2;background:#fff;border-radius:16px;padding:4px 12px;font-size:12.5px;cursor:pointer;color:#5A6474}
  .hvcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;padding:10px 0}
  .hvcard{border:1px solid #E6EAF2;border-radius:14px;padding:12px 14px;background:#fff;cursor:pointer}
  .hvcard:hover{border-color:#4A7DFF}
  .hvcard-top{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  .hvcard-top b{font-size:14.5px}
  .hvcard-rel{font-size:11px;color:#4A7DFF;background:#F3F7FE;border-radius:9px;padding:1px 8px}
  .hvcard-meta{display:flex;flex-direction:column;gap:3px;font-size:12px;color:#5A6474;line-height:1.6}
  .hvcard-ops{margin-top:8px}
  .hvcard-add{border:1px solid #4A7DFF;background:#4A7DFF;color:#fff;border-radius:14px;padding:12px 8px;font-size:13px;font-weight:600;cursor:pointer;min-height:100px}
  .hvfb-mask{position:fixed;inset:0;background:rgba(20,26,38,.42);z-index:9999;display:flex;align-items:center;justify-content:center}
  .hvfb-box{background:#fff;border-radius:14px;width:min(600px,94vw);max-height:88vh;overflow:auto;padding:16px}
  .hvfb-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:14px;font-weight:600}
  .hvfb-x{border:none;background:transparent;font-size:16px;cursor:pointer;color:#8A94A6}
  .hvfb-zd{font-size:12px;color:#8A94A6;font-weight:400;margin-left:8px}
  .hvfb-layer{margin:10px 0 4px;font-size:12px;font-weight:700;color:#4A7DFF;display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
  .hvfb-layer .ly-arrow{font-size:10px;color:#8A94A6}
  .hvfb-g{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px}
  .hvfb-g.hide{display:none}
  .hvfb-g label{display:flex;flex-direction:column;gap:3px;font-size:11.5px;color:#8A94A6;position:relative}
  .hvfb-g input{border:1px solid #E6EAF2;border-radius:8px;padding:6px 9px;font-size:13px;font-family:inherit}
  .hvfb-row{grid-column:1/-1;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  .hvfb-row input{flex:1;min-width:90px}
  .hvfb-ops{grid-column:1/-1;display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:4px}
  .hvfb-mini{border:1px solid #E6EAF2;border-radius:8px;padding:5px 12px;font-size:12.5px;cursor:pointer;background:#fff;color:#3A4453}
  .hvfb-mini.pri{border-color:#4A7DFF;background:#4A7DFF;color:#fff;font-weight:600}
  .hvfb-msg{font-size:12px;color:#1F9A5A}
  .hvfb-del{color:#B4443C;cursor:pointer;font-size:10px;margin-left:4px}
  .hvfb-warn{color:#B4443C}
  `;
  // 六层字段定义
  const LAYERS = [
    { key: 'base', name: '📌 基础', fields: [
      ['relation', '关系', '如：爸爸/妈妈/儿子'], ['birth_time', '出生时间', '如：午时（选填）'],
      ['phone', '手机', '选填'], ['wechat', '微信', '选填']] },
    { key: 'health', name: '🩺 健康', fields: [
      ['blood_type', '血型', 'A/B/O/AB'], ['height_cm', '身高cm', ''], ['weight_kg', '体重kg', ''],
      ['chronic', '慢性病', '如：血压偏高'], ['medication', '长期用药', ''], ['drug_allergy', '药物过敏', '⚠️与食物忌口分开'],
      ['vaccine_note', '疫苗情况', ''], ['exercise', '运动习惯', '如：每周跑步3次'], ['sleep_note', '睡眠情况', ''],
      ['health_note', '健康备注', '']] },
    { key: 'diet', name: '🍚 饮食', fields: [
      ['preference', '口味偏好', '如：爱吃辣/清淡'], ['allergy', '食物忌口/过敏', '⚠️做饭采购会避开'],
      ['like_food', '喜欢的食物', ''], ['hate_food', '讨厌的食物', '']] },
    { key: 'grow', name: '📚 成长（孩子）', fields: [
      ['school', '学校', ''], ['grade', '年级', ''], ['interests', '兴趣特长', '']] },
    { key: 'social', name: '💼 社会（大人）', fields: [
      ['job', '职业', ''], ['company', '公司/单位', ''], ['license_type', '驾照准驾车型', '如：C1'],
      ['license_expiry', '驾照有效期', ''], ['id_expiry', '身份证有效期', '临期自动提醒']] },
    { key: 'life', name: '🛍 生活', fields: [
      ['emergency_contact', '紧急联系人', ''], ['emergency_phone', '紧急联系电话', ''],
      ['top_size', '上衣尺码', ''], ['shoe_size', '鞋码', ''],
      ['budget_monthly', '月预算/零花钱', ''], ['comm_style', '沟通偏好', '如：对孩子用简单话']] },
  ];
  function isChild(m) {
    const rel = String(m.relation || '');
    const hasKidWord = /(子|女|娃|宝)/.test(rel);
    const school = String(m.school || '').trim();
    return hasKidWord || (!String(m.job || '').trim() && !!school);
  }

  function injectCss() {
    if (document.getElementById('hvfb-style')) return;
    const s = document.createElement('style'); s.id = 'hvfb-style'; s.textContent = css;
    document.head.appendChild(s);
  }

  const State = { members: [], picked: '', onPick: null, container: '#hvFamilyBar', box: null, mode: 'bar' };

  async function load() {
    try {
      const r = await fetch('/api/panels/members').then(x => x.json());
      State.members = r.members || [];
    } catch (_) { State.members = []; }
  }

  function birthdayIn(m) {
    const b = String(m.birthday || '');
    if (b.length < 10) return 0;
    const t = new Date(); t.setHours(0, 0, 0, 0);
    const d = new Date(t.getFullYear() + b.slice(4, 10) + 'T00:00:00');
    if (isNaN(d)) return 0;
    if (d < t) d.setFullYear(t.getFullYear() + 1);
    const n = Math.round((d - t) / 86400000);
    return n <= 60 ? n : 0;
  }

  function esc(s) { return String(s || '').replace(/"/g, '&quot;'); }

  function render() {
    const el = document.querySelector(State.container);
    if (!el) return;
    /* ★ 2026-10-22 卡片模式（家人健康面板正主界面）：家人大卡 + 健康事件流在下方 */
    if (State.mode === 'cards') { renderCards(el); return }
    const chips = State.members.map(m => {
      const bd = birthdayIn(m);
      const rel = m.relation ? '·' + m.relation : '';
      const bdTxt = bd > 0 ? ` 🎂${bd}天` : '';
      const warn = m.allergy ? ' ⚠️' : '';
      return `<span class="hvfb-chip${State.picked === m.name ? ' on' : ''}" data-n="${encodeURIComponent(m.name)}" title="${esc(m.allergy ? '忌口：' + m.allergy : '')} ${esc(m.health_note || '')}">
        <span class="hvfb-dot" style="background:${m.color || '#5B8DEF'}"></span>${m.name}${rel}${bdTxt}${warn}${m.zodiac ? ' ' + m.zodiac : ''}</span>`;
    }).join('');
    el.innerHTML = `<div class="hvfb">
      <span class="hvfb-t">👨‍👩‍👧 家人</span>
      <span class="hvfb-chip${State.picked === '' ? ' on' : ''}" data-n="">全部</span>
      ${chips || '<span style="font-size:12px;color:#8A94A6">还没有家人，点右边加一个</span>'}
      <button class="hvfb-add" id="hvfbAdd">＋ 家人</button>
      <button class="hvfb-prof" id="hvfbProf">资料</button>
    </div>`;
    el.querySelectorAll('.hvfb-chip').forEach(c => {
      c.onclick = () => {
        State.picked = decodeURIComponent(c.dataset.n || '');
        render();
        if (typeof State.onPick === 'function') State.onPick(State.picked);
      };
    });
    const addBtn = el.querySelector('#hvfbAdd');
    if (addBtn) addBtn.onclick = () => openAdd();
    const profBtn = el.querySelector('#hvfbProf');
    if (profBtn) profBtn.onclick = () => openProfile(State.members.find(x => x.name === State.picked) || State.members[0] || null);
  }

  /* ★ 2026-10-22 家人卡区（cards 模式）：每家人一张卡，点开六层资料弹窗 */
  function renderCards(el) {
    const cards = State.members.map(m => {
      const bd = birthdayIn(m);
      const rows = [];
      if (m.relation) rows.push('关系：' + m.relation);
      if (m.birthday) rows.push('🎂 ' + String(m.birthday).slice(0, 10) + (m.zodiac ? '（' + m.zodiac + '座）' : ''));
      if (m.birthday_lunar) rows.push('🌙 农历 ' + m.birthday_lunar);
      if (m.blood_type) rows.push('🩸 ' + m.blood_type + '型');
      if (m.allergy) rows.push('<span class="hvfb-warn">⚠️ 忌口：' + esc(m.allergy) + '</span>');
      if (m.health_note) rows.push(esc(m.health_note));
      const exN = Object.keys(m.extra || {}).length;
      if (exN) rows.push('＋' + exN + ' 项自定义');
      return `<div class="hvcard" data-n="${encodeURIComponent(m.name)}">
        <div class="hvcard-top"><span class="hvfb-dot" style="background:${m.color || '#5B8DEF'};width:12px;height:12px"></span>
          <b>${m.name}</b>${m.relation ? '<span class="hvcard-rel">' + esc(m.relation) + '</span>' : ''}</div>
        <div class="hvcard-meta">${rows.map(r => '<div>' + r + '</div>').join('') || '<div style="color:#B9C4D6">点「资料」开始录入</div>'}</div>
        <div class="hvcard-ops"><button class="hvfb-mini pri" data-prof="${encodeURIComponent(m.name)}">📋 资料（32项）</button></div>
      </div>`;
    }).join('');
    el.innerHTML = `<div class="hvcards">
      ${cards || '<div style="color:#8A94A6;font-size:13px;padding:8px 0">还没有家人档案</div>'}
      <button class="hvcard-add" id="hvfbAdd2">＋ 添加家人</button>
    </div>`;
    el.querySelectorAll('.hvcard').forEach(c => {
      c.onclick = e => {
        if (e.target.closest('button')) return;
        const m = State.members.find(x => x.name === decodeURIComponent(c.dataset.n || ''));
        if (m) openProfile(m);
      };
    });
    el.querySelectorAll('[data-prof]').forEach(b => {
      b.onclick = () => openProfile(State.members.find(x => x.name === decodeURIComponent(b.dataset.prof || '')));
    });
    const add = el.querySelector('#hvfbAdd2');
    if (add) add.onclick = () => openAdd();
  }

  /* ── 添加家人：自绘弹窗（弃 prompt——iframe 内被浏览器拦实锤） ── */
  function openAdd() {
    injectCss();
    const mask = document.createElement('div');
    mask.className = 'hvfb-mask';
    mask.innerHTML = `<div class="hvfb-box" style="width:min(380px,92vw)">
      <div class="hvfb-h"><span>＋ 添加家人</span><button class="hvfb-x">✕</button></div>
      <div class="hvfb-g" style="grid-template-columns:1fr">
        <label>称呼 *<input id="hvfbAddName" placeholder="如：爸爸 / 妈妈 / 小明" maxlength="20" /></label>
        <label>关系<input id="hvfbAddRel" placeholder="如：儿子 / 女儿 / 爸爸" maxlength="10" /></label>
        <div class="hvfb-ops">
          <button class="hvfb-mini pri" id="hvfbAddGo">添加并录资料</button>
          <span class="hvfb-msg" id="hvfbAddMsg"></span>
        </div>
      </div>
    </div>`;
    document.body.appendChild(mask);
    const close = () => mask.remove();
    mask.querySelector('.hvfb-x').onclick = close;
    mask.onclick = e => { if (e.target === mask) close(); };
    const nameEl = mask.querySelector('#hvfbAddName');
    nameEl.focus();
    mask.querySelector('#hvfbAddGo').onclick = async () => {
      const name = nameEl.value.trim();
      if (!name) { mask.querySelector('#hvfbAddMsg').textContent = '称呼必填'; return; }
      const rel = mask.querySelector('#hvfbAddRel').value.trim();
      try {
        const r = await fetch('/api/panels/members', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, relation: rel, color: '#5B8DEF', role: '员工' }),
        }).then(x => x.json());
        if (!r.success) { mask.querySelector('#hvfbAddMsg').textContent = r.message || '添加失败'; return; }
        await load(); render();
        close();
        openProfile(State.members.find(x => x.name === name));
      } catch (e) { mask.querySelector('#hvfbAddMsg').textContent = '网络错误'; }
    };
    nameEl.onkeydown = e => { if (e.key === 'Enter') mask.querySelector('#hvfbAddGo').click(); };
  }

  /* ── 资料弹窗：六层分组 + 星座自动 + 按关系显隐 + 自定义10 ── */
  function openProfile(m) {
    if (!m) { return; }
    injectCss();
    const extra = m.extra || {};
    const child = isChild(m);
    const exRows = Object.keys(extra).map(k => `
      <label>${esc(k)}<span class="hvfb-del" data-k="${encodeURIComponent(k)}">✕</span>
        <input data-ex="${encodeURIComponent(k)}" value="${esc(extra[k])}" placeholder="值" /></label>`).join('');
    const layerHtml = LAYERS.map(L => {
      const autoShow = L.key === 'grow' ? child : (L.key === 'social' ? !child : true);
      return `<div class="hvfb-layer" data-ly="${L.key}"><span class="ly-arrow">${autoShow ? '▼' : '▶'}</span>${L.name}</div>
      <div class="hvfb-g${autoShow ? '' : ' hide'}" data-lyg="${L.key}">
        ${L.fields.map(function (f) { return '<label>' + f[1] + '<input id="hvfb_' + f[0] + '" value="' + esc(m[f[0]]) + '" placeholder="' + f[2] + '" /></label>'; }).join('')}
      </div>`;
    }).join('');
    const mask = document.createElement('div');
    mask.className = 'hvfb-mask';
    mask.innerHTML = `<div class="hvfb-box">
      <div class="hvfb-h"><span>👤 ${m.name}${m.relation ? ' · ' + m.relation : ''}
        ${m.zodiac ? '<span class="hvfb-zd">' + m.zodiac + '座（按生日自动）</span>' : ''}
        <span class="hvfb-zd hvfb-warn">${m.allergy ? '⚠️ 忌口：' + esc(m.allergy) : ''}</span></span>
        <button class="hvfb-x">✕</button></div>
      ${layerHtml}
      <div class="hvfb-layer" data-ly="ex"><span class="ly-arrow">▼</span>➕ 自定义录入项</div>
      <div class="hvfb-g" data-lyg="ex">
        <div class="hvfb-row" id="hvfbExtraRows" style="display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;width:100%">${exRows}</div>
        <div class="hvfb-row">
          <input id="hvfbNewK" placeholder="新录入项名（如：过敏药物）" maxlength="20" />
          <input id="hvfbNewV" placeholder="值" />
          <button class="hvfb-mini" id="hvfbAddEx">＋ 添加</button>
          <span style="font-size:11px;color:#8A94A6" id="hvfbExN">${Object.keys(extra).length}/10</span>
        </div>
      </div>
      <div class="hvfb-ops">
        <button class="hvfb-mini pri" id="hvfbSave">💾 保存</button>
        <button class="hvfb-mini" id="hvfbAsk">💬 和哈文讨论加什么项</button>
        <span class="hvfb-msg" id="hvfbMsg"></span>
      </div>
    </div>`;
    document.body.appendChild(mask);
    const close = () => { mask.remove(); State.box = null; };
    State.box = mask;
    mask.querySelector('.hvfb-x').onclick = close;
    mask.onclick = e => { if (e.target === mask) close(); };
    mask.querySelectorAll('.hvfb-layer').forEach(h => {
      h.onclick = () => {
        const g = mask.querySelector('[data-lyg="' + h.dataset.ly + '"]');
        if (!g) return;
        g.classList.toggle('hide');
        h.querySelector('.ly-arrow').textContent = g.classList.contains('hide') ? '▶' : '▼';
      };
    });
    mask.querySelectorAll('.hvfb-del').forEach(d => { d.onclick = function () { this.parentElement.remove(); }; });
    mask.querySelector('#hvfbAddEx').onclick = () => {
      const k = mask.querySelector('#hvfbNewK').value.trim();
      const v = mask.querySelector('#hvfbNewV').value.trim();
      if (!k) return;
      const rows = mask.querySelector('#hvfbExtraRows');
      if (rows.querySelectorAll('label').length >= 10) { mask.querySelector('#hvfbMsg').textContent = '最多10项'; return; }
      const lb = document.createElement('label');
      lb.innerHTML = esc(k) + '<span class="hvfb-del">✕</span><input data-ex="' + encodeURIComponent(k) + '" value="' + esc(v) + '" placeholder="值" />';
      lb.querySelector('.hvfb-del').onclick = function () { this.parentElement.remove(); };
      rows.appendChild(lb);
      mask.querySelector('#hvfbNewK').value = '';
      mask.querySelector('#hvfbNewV').value = '';
      mask.querySelector('#hvfbExN').textContent = rows.querySelectorAll('label').length + '/10';
    };
    mask.querySelector('#hvfbSave').onclick = async function () {
      const extraOut = {};
      mask.querySelectorAll('#hvfbExtraRows label').forEach(function (lb) {
        const inp = lb.querySelector('input');
        if (inp) extraOut[decodeURIComponent(inp.dataset.ex || '')] = inp.value.trim();
      });
      const payload = { extra: extraOut };
      LAYERS.forEach(function (L) { L.fields.forEach(function (f) {
        const el = mask.querySelector('#hvfb_' + f[0]);
        if (el) payload[f[0]] = el.value.trim();
      }); });
      try {
        const r = await fetch('/api/panels/members/' + m.id, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }).then(function (x) { return x.json(); });
        if (r.success) {
          mask.querySelector('#hvfbMsg').textContent = '✅ 已保存';
          await load(); render();
          setTimeout(close, 800);
        } else mask.querySelector('#hvfbMsg').textContent = r.message || '保存失败';
      } catch (e) { mask.querySelector('#hvfbMsg').textContent = '网络错误'; }
    };
    mask.querySelector('#hvfbAsk').onclick = () => {
      const q = '我在录「' + m.name + '」的家人健康档案（关系：' + (m.relation || '未填') + '，忌口：' + (m.allergy || '无') + '，健康备注：' + (m.health_note || '无') + '，血型：' + (m.blood_type || '未填') + '）。请建议还应该补录哪些重要信息（最多到10项自定义），每项给出名称和为什么值得记。';
      location.hash = '#/?prefill=' + encodeURIComponent(q);
    };
  }

  window.HvFamilyBar = {
    async mount(opts) {
      injectCss();
      State.container = (opts && opts.container) || '#hvFamilyBar';
      State.onPick = (opts && opts.onPick) || null;
      State.mode = (opts && opts.mode) || 'bar';
      try {
        const u = new URL(location.href);
        const mp = u.searchParams ? u.searchParams.get('member') : null;
        if (mp) State.picked = mp;
      } catch (_) {}
      await load();
      render();
    },
    get picked() { return State.picked; },
    reload: async function () { await load(); render(); },
  };
})();
