/* 哈文助手 · 待确认收纳盒 3.0 ★ 2026-10-11（照片链 B4，定版拍板「电脑对齐手机」）
 * 形态：右下角悬浮圆钮（绿鸭头+红点，可拖动记位置）→ 点开「收纳盒」卡片弹层
 *   （来自聊天的待确认记录 + 到点提醒，一屏处理完）
 * ★ 对话窗已退役（与手机端 09-30 #931 同一拍板：助手=聊天）——想说话去首页聊天框；
 *   面板页「📷 批量照片录入」等按钮通过 api.showConfirm / api.showConfirmHome 弹确认卡
 * 用法: PanelAssist.mount({ host, panelId, fields, getDomain, save, onSaved, theme,
 *                           mode:'home', hintText, getContext, pageContext })
 *       PanelAssist.open()  → 打开收纳盒
 *       实例 api.showConfirm(records, domain, sourceText, pendingImg, alerts, quality)
 *       实例 api.showConfirmHome(groups, pendingImg)
 */
window.PanelAssist = (function () {
  'use strict';
  let _curLight = true;  /* ★ #923 当前实例是否浅色（toast 在 mount 外用） */

  /* ★ 单实例语义：新 mount 先清掉旧实例（DOM + 定时器），切页签/切页面不叠罗汉 */
  let prevCleanup = null;
  let lastApi = null; /* 最近一次 mount 的实例 API（PanelAssist.open() 全局入口用） */
  function destroyStray() {
    if (prevCleanup) { try { prevCleanup(); } catch (e) { /* 静默 */ } prevCleanup = null; }
    document.querySelectorAll('body > .pa-fab, body > .pa-chat, body > .pa-toast, body > .pa-overlay').forEach(el => el.remove());
  }

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  /* ★ 2026-09-19 修C：技术报错 → 人话 + 下一步指引（用户是小白，术语甩脸上等于没说）
     ★ 2026-09-27 #892 报错诚实化：后端 #891 已返回阵营精确报错，这里透传 */
  function humanErr(msg) {
    const m = String(msg == null ? '' : msg);
    if (/panel_id|缺少.*标识/.test(m)) return '⚠️ 出了个小故障（程序内部标识没传到）。重启一下哈文通常就能修好：关掉哈文窗口，再双击桌面「Harven AI」打开。';
    if (/超时|timeout|timed? ?out/i.test(m)) return '⚠️ 这回等太久没回话（模型可能在忙）。等几秒再发一次试试。';
    if (/locked|database/i.test(m)) return '⚠️ 数据库这会儿被占用（可能别的窗口在读写）。等几秒再试一次。';
    return '⚠️ ' + m + '\n再试一次还不行的话，把这句话截图，到「设置 → 关于」里反馈。';
  }

  function normFields(fields) {
    return (fields || []).map(f => ({
      key: f.key || f.name || '',
      label: f.label || f.name || f.key || '',
      type: f.type || 'text',
      options: f.options || []
    })).filter(f => f.key && f.key !== 'photo' && f.key !== 'member');
  }

  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'pa-toast' + (_curLight ? ' pa-light' : '');
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2600);
  }

  /* ═══ 导航拦截（保留：__tryNav 暴露给 SPA 各页用；收纳盒「去处理」跳转也用） ═══ */
  const NAV_BUILTINS = [
    { k: ['面板中心', '面板管理', '面板页', '面板总页', '我的面板', '面板', '模版库', '模板库'], p: '/panels', n: '面板中心' },
    { k: ['知识库'], p: '/knowledge', n: '知识库' },
    { k: ['技能库', '技能'], p: '/skills', n: '技能库' },
    { k: ['任务'], p: '/tasks', n: '任务' },
    { k: ['邮箱', '邮件'], p: '/mail', n: '邮箱' },
    { k: ['harvenhub', '哈文hub', 'hub市场', '市场'], p: '/hub', n: 'HarvenHub' },
    { k: ['模型中心', '模型页', '模型'], p: '/models', n: '模型中心' },
    { k: ['设置', '设置页'], p: '/settings', n: '设置' },
    { k: ['运行状态', '状态页', '监控中心'], p: '/status', n: '运行状态' },
    { k: ['时间轴', '会话记录'], p: '/timeline', n: '时间轴' },
    { k: ['创作工坊', '工坊'], p: '/studio', n: '创作工坊' },
    { k: ['更多'], p: '/more', n: '更多' },
    { k: ['家庭模板', '家庭生活', '家庭'], p: '/space/family', n: '家庭模板' },
    { k: ['公司模板', '公司'], p: '/space/company', n: '公司模板' },
    { k: ['创作模板', '创作娱乐'], p: '/space/create', n: '创作模板' }
  ];
  /* 常用别名 → 面板ID（与 navCommands.js 同步维护） */
  const NAV_ALIAS = [
    ['孩子成长档案', 'kid_archive'], ['成长档案', 'kid_archive'], ['成长', 'kid_archive'], ['孩子档案', 'kid_archive'],
    ['食物库存', 'fridge_stock_1'], ['冰箱', 'fridge_stock_1'],
    ['记账账本', 'accounting_daily_1'], ['记账', 'accounting_daily_1'], ['账本', 'accounting_daily_1'],
    ['车辆保养', 'car_maintenance_1'], ['车辆', 'car_maintenance_1'], ['汽车', 'car_maintenance_1'],
    ['宠物', 'pet_care_1'],
    ['绿植', 'plants_1'], ['花草', 'plants_1'], ['浇花', 'plants_1'],
    ['健康档案', 'health_file'], ['体检', 'health_file'], ['体检报告', 'health_file'], ['复查', 'health_file'],
    ['健身记录', 'fitness_log'], ['健身', 'fitness_log'], ['运动记录', 'fitness_log'],
    ['孩子成长', 'child_growth'], ['接种', 'child_growth'], ['视力', 'child_growth'],
    ['家庭日历', 'home_calendar'], ['日历', 'home_calendar'], ['日程', 'home_calendar'],
    ['物品存放', 'home_items'], ['物品', 'home_items'], ['东西在哪', 'home_items'],
    ['老人关怀', 'elder_care'], ['老人', 'elder_care'], ['用药', 'elder_care'],
    ['家庭药箱', 'med_cabinet'], ['药箱', 'med_cabinet'], ['吃药', 'med_cabinet'],
    ['证件保单', 'home_docs'], ['证件', 'home_docs'], ['保单', 'home_docs'],
    ['水电气', 'home_utilities'], ['缴费', 'home_utilities'],
    ['全家待办', 'family_todo'],
    ['人情往来', 'home_favors'], ['人情', 'home_favors'], ['随礼', 'home_favors'],
    ['家庭维修', 'home_repair'], ['维修', 'home_repair']
  ];
  const NAV_IMPERATIVE = /^(请|麻烦|帮我|劳驾)?(帮我)?(打开|进入|切换到|切换|带我去|跳转到|跳转|去(看看|一下|下)?|查看|看看)/;
  let _navPanels = null, _navPanelsAt = 0;
  function navGetPanels() {
    if (_navPanels && Date.now() - _navPanelsAt < 5 * 60 * 1000) return Promise.resolve(_navPanels);
    return fetch('/api/panels').then(r => r.json()).then(j => {
      _navPanels = (j.panels || []).filter(p => p && p.id).map(p => ({ id: p.id, name: p.name || '' }));
      _navPanelsAt = Date.now();
      return _navPanels;
    }).catch(() => { _navPanels = _navPanels || []; return _navPanels; });
  }
  function navCats() {
    let custom = [], hidden = [];
    try {
      custom = JSON.parse(localStorage.getItem('harven_spa_panel_cats') || '[]');
      hidden = JSON.parse(localStorage.getItem('harven_spa_panel_cats_hidden') || '[]');
    } catch (e) { /* 老包兼容 */ }
    return custom.filter(c => c && c.id && c.name && hidden.indexOf(c.id) < 0)
      .map(c => ({ p: '/space/' + c.id, n: c.name }));
  }
  function navCandidates(kw) {
    const list = [kw];
    let cur = kw;
    while (list.length < 4) {
      const next = cur.replace(/(面板|页面|空间|中心|界面|工具|记录页|的页|页|库)$/g, '').trim();
      if (!next || next === cur) break;
      list.push(next);
      cur = next;
    }
    return list;
  }
  function navTo(path) {
    /* SPA 侧 main.js 监听 harven:navigate 事件做 router.push */
    try { window.dispatchEvent(new CustomEvent('harven:navigate', { detail: { path: path } })); } catch (e) { /* 静默 */ }
  }
  /* 尝试把一句话解析成页面跳转；命中返回 {path,name,note}，未命中返回 null（走聊天管线） */
  async function tryAssistNav(text) {
    const t = String(text || '').trim();
    if (!t || t.length > 24) return null;
    if (/[？?]/.test(t)) return null;
    if (!NAV_IMPERATIVE.test(t)) return null;
    let kw0 = t.replace(NAV_IMPERATIVE, '').replace(/^(一下|一下下|下)/, '').replace(/(吧|呗|哈|哦|啦)$/g, '').trim();
    if (!kw0) return null;
    const kws = navCandidates(kw0);
    /* 1. 内置页面+出厂分类 */
    for (const cand of kws) {
      for (const b of NAV_BUILTINS) {
        if (b.k.indexOf(cand) >= 0) return { path: b.p, name: b.n, note: '🧭 已打开「' + b.n + '」～' };
      }
      for (const c of navCats()) {
        if (cand === c.n) return { path: c.p, name: c.n, note: '🧭 已打开「' + c.n + '」～' };
      }
    }
    /* 2. 面板别名（精确） */
    for (const cand of kws) {
      for (const pair of NAV_ALIAS) {
        if (cand !== pair[0]) continue;
        const panels = await navGetPanels();
        const hit = panels.find(p => p.id === pair[1]);
        if (hit) return { path: '/panel/' + hit.id, name: hit.name, note: '🧭 已打开「' + hit.name + '」～' };
        return { path: '/panels', name: '面板管理', note: '没有找到「' + pair[0] + '」对应的已装面板，先帮你打开面板页，在里面看看～' };
      }
    }
    /* 3. 动态面板名（互含且相差≤2字，防粘连误配） */
    const panels = await navGetPanels();
    for (const cand of kws) {
      const hits = panels.filter(p => {
        const nm = p.name;
        if (!nm) return false;
        if (cand === nm) return true;
        if (nm.indexOf(cand) >= 0 && nm.length - cand.length <= 2) return true;
        if (cand.indexOf(nm) >= 0 && cand.length - nm.length <= 2) return true;
        return false;
      });
      if (hits.length === 1) return { path: '/panel/' + hits[0].id, name: hits[0].name, note: '🧭 已打开「' + hits[0].name + '」～' };
      if (hits.length > 1) return { path: '/panels', name: '面板管理', note: '「' + kw0 + '」匹配到多个面板：' + hits.map(h => h.name).join('、') + '——先帮你打开面板页，你在里面点想看的那个～' };
    }
    return null;
  }

  function mount(opts) {
    destroyStray();
    const fields = normFields(opts.fields);
    const panelId = opts.panelId;
    const getDomain = opts.getDomain || (() => '');
    /* ★ 2026-09-27 实测实锤（物品存放台账：助手黑窗灰字看不清）：哈文全 UI 已统一浅色
       （09-18 面板跟随 SPA 浅色令牌），助手却默认暗色——只有 kid.js 传过 light。
       改为默认浅色，除非明确 theme:'dark'；所有面板/SPA 页一处生效，不再逐页补参数 */
    const light = opts.theme !== 'dark';
    _curLight = light;
    const homeMode = opts.mode === 'home';
    /* ★ B4（2026-10-11）：聊天窗退役，state 只留收纳盒开关与提醒缓存 */
    const state = { open: false, lastAlerts: null };
    let inboxEl = null;   /* 收纳盒弹层（开着才有） */

    /* ── 悬浮圆钮（绿色+鸭头+58px+默认底部居中+可拖动记忆位置）──
       ★ B4 语义变更：点鸭头 = 开「待确认收纳盒」（不再是聊天窗） */
    const FAB_POS_KEY = 'harven_assist_fab_pos';
    const fab = document.createElement('div');
    fab.className = 'pa-fab' + (light ? ' pa-light' : '');
    fab.title = '待确认：哈文识别出来等你把关的记录、到点提醒都在这';
    fab.innerHTML = '<img class="pa-fab-ico" src="/static/icons/duck_64.png" alt="哈文" draggable="false">' +
      '<span class="pa-fab-dot" style="display:none"></span>';
    document.body.appendChild(fab);

    function applyFabPos(l, t) {
      const size = 58, vw = window.innerWidth, vh = window.innerHeight;
      l = Math.max(8, Math.min(l, vw - size - 8));
      t = Math.max(8, Math.min(t, vh - size - 8));
      fab.style.left = l + 'px'; fab.style.top = t + 'px';
      fab.style.right = 'auto'; fab.style.bottom = 'auto';
      fab.classList.add('pa-fab-dragged');
    }
    try {
      const saved = JSON.parse(localStorage.getItem(FAB_POS_KEY) || 'null');
      if (saved && typeof saved.left === 'number') applyFabPos(saved.left, saved.top);
    } catch (e) { /* 默认位置 */ }

    let dragState = null;
    fab.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      const r = fab.getBoundingClientRect();
      dragState = { sx: e.clientX, sy: e.clientY, l: r.left, t: r.top, moved: false };
      try { fab.setPointerCapture(e.pointerId); } catch (err) { /* 静默 */ }
    });
    fab.addEventListener('pointermove', function (e) {
      if (!dragState) return;
      const dx = e.clientX - dragState.sx, dy = e.clientY - dragState.sy;
      if (!dragState.moved && Math.abs(dx) < 5 && Math.abs(dy) < 5) return;
      dragState.moved = true;
      applyFabPos(dragState.l + dx, dragState.t + dy);
    });
    fab.addEventListener('pointerup', function () {
      if (!dragState) return;
      if (dragState.moved) {
        const r = fab.getBoundingClientRect();
        try { localStorage.setItem(FAB_POS_KEY, JSON.stringify({ left: r.left, top: r.top })); } catch (err) { /* 静默 */ }
      } else {
        /* ★ 2026-10-25 #1184 入口收纳（定版拍板）：浮球点击 = 管家专项入口 → 管家中心（SPA /butler）；
           待确认收纳盒内容已并入管家中心，程序化 open（api.open）仍可唤起原收纳盒 */
        openButler();
      }
      dragState = null;
    });

    /* ★ #1184 浮球直达管家中心：SPA 内改 hash 路由；面板 iframe 内改父窗口 hash；
       独立打开时跳 SPA 正门 /spa/（★ #1189 实锤：/static/spa/ 是静态目录无索引会 404） */
    function openButler() {
      var go = function () {
        try { window.location.href = '/spa/#/butler'; } catch (e) { /* 静默 */ }
      };
      try {
        if (window.parent && window.parent !== window && /\/spa[\/?#]/.test(String(window.parent.location.href))) {
          window.parent.location.hash = '#/butler'; return;
        }
      } catch (e) { /* 跨域等异常走兜底 */ }
      go();
    }

    /* ── 确认卡（单面板）── ★ B4 保留：批量照片录入（B1）与后续链路共用 */
    /* ── ★ #1407（2026-11-03）一物多照工具条：确认卡共用 ──
       实测实锤场景：重要物品一张说不清（柜子 1 张 + 抽屉 1 张 + 物品多视角）。
       缩略图 + 角色下拉（写进该图 note）+ 📷 补拍按钮（file input，上限 9 张） */
    const PA_SHOT_ROLES = ['柜子/位置', '抽屉/内部', '物品特写', '包装/铭牌', '其他'];
    function paNormImgs(pendingImg) {
      const arr = Array.isArray(pendingImg) ? pendingImg : (pendingImg ? [pendingImg] : []);
      return arr.filter(x => typeof x === 'string' && x.length > 100).slice(0, 9);
    }
    function paImgStripHtml(imgs, notes) {
      let h = '<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;margin:6px 2px">';
      imgs.forEach((im, i) => {
        h += '<div style="position:relative;text-align:center">' +
          '<img src="' + im + '" style="width:54px;height:54px;object-fit:cover;border-radius:6px;border:1px solid #E5EAF2">' +
          '<span class="pa-link" data-imdel="' + i + '" style="position:absolute;top:-6px;right:-6px;background:#fff;border-radius:50%;font-size:10px;cursor:pointer;padding:0 4px;box-shadow:0 1px 3px rgba(0,0,0,.25)">✕</span>' +
          '<select data-imnote="' + i + '" style="display:block;width:62px;font-size:10px;margin-top:2px;border:1px solid #E5EAF2;border-radius:4px;padding:1px;background:#fff">' +
          '<option value="">角色…</option>' + PA_SHOT_ROLES.map(r => '<option ' + (notes[i] === r ? 'selected' : '') + '>' + esc(r) + '</option>').join('') +
          '</select></div>';
      });
      if (imgs.length < 9) {
        h += '<label class="pa-link pa-addshot" style="cursor:pointer;display:inline-flex;flex-direction:column;align-items:center;justify-content:center;width:54px;height:54px;border:1px dashed #C9D2E0;border-radius:6px;color:#5b6472;user-select:none">📷<span style="font-size:10px">补拍 ' + imgs.length + '/9</span>' +
          '<input type="file" accept="image/*" multiple style="display:none"></label>';
      }
      h += '</div>';
      return h;
    }
    function paBindImgStrip(dialog, imgs, notes, rerender) {
      const add = dialog.querySelector('.pa-addshot');
      if (add) add.querySelector('input').addEventListener('change', (ev) => {
        const files = Array.from(ev.target.files || []);
        if (!files.length) return;
        const before = imgs.length;
        const quota = Math.min(files.length, 9 - before);
        const got = [];
        let done = 0;
        files.slice(0, quota).forEach(f => {
          const rd = new FileReader();
          rd.onload = () => {
            got.push(String(rd.result));
            if (++done === quota) {
              imgs.push(...got);
              for (let k = 0; k < got.length; k++) notes.push('');
              rerender();
            }
          };
          rd.readAsDataURL(f);
        });
      });
      dialog.querySelectorAll('[data-imnote]').forEach(sel =>
        sel.addEventListener('change', () => { notes[+sel.dataset.imnote] = sel.value; }));
      dialog.querySelectorAll('[data-imdel]').forEach(el => el.addEventListener('click', () => {
        const i = +el.dataset.imdel;
        imgs.splice(i, 1); notes.splice(i, 1); rerender();
      }));
    }
    function _paImgNotes(notes) {
      return (notes || []).map(n => n ? '哈文助手确认入库配图 · ' + n : '');
    }

    function showConfirm(records, domain, sourceText, pendingImg, alerts, quality) {
      const overlay = document.createElement('div');
      overlay.className = 'pa-overlay';
      const fs = normFields(opts.fields);
      let recs = records.map(r => Object.assign({}, r));
      /* ★ #1407-A/C：首拍图 + 补拍图统一进 imgs（≤9），角色标注随图提交 */
      const imgs = paNormImgs(pendingImg);
      const imgNotes = imgs.map(() => '');
      alerts = alerts || [];  /* ★ 2026-10-10 #1062 C2 过敏预警 */
      /* ★ 2026-09-18 空字段兜底：总览模式挂载 fields=[]，按解析出的域拉 schema 补字段行 */
      async function ensureFields() {
        if (fs.length || !records.length) return;
        try {
          const r = await fetch('/api/assist/schema?panel_id=' + encodeURIComponent(panelId) +
            '&domain=' + encodeURIComponent(domain || ''));
          const j = await r.json();
          if (j.success && j.schema && j.schema.fields) {
            normFields(j.schema.fields).forEach(f => { if (!fs.some(x => x.key === f.key)) fs.push(f); });
          }
        } catch (e) { /* 静默 */ }
      }
      const dialog = document.createElement('div');
      dialog.className = 'pa-card' + (light ? ' pa-light' : '');
      overlay.appendChild(dialog);
      document.body.appendChild(overlay);

      async function render() {
        await ensureFields();
        const dl = (opts.domainLabels && domain && opts.domainLabels[domain]) || '';
        let html = '<h3>📋 确认入库</h3><div class="pa-sub">' +
          (dl ? '哈文判断记入【' + esc(dl) + '】· ' : '') +
          '改一改或留空都行，留空的以后在表单里补</div>';
        /* ★ 2026-10-11 #1073 健康预警分级：过敏/药食冲突=红条，忌口/易上火=黄条
           （兼容 #1062 旧 {allergy} 形状；新形状 {level,kind,member,term,hit_in,msg}） */
        (alerts || []).forEach(a => {
          const danger = a.level === 'danger' || !!a.allergy;
          const kind = a.kind || (a.allergy ? '过敏' : '提醒');
          const term = a.term || a.allergy || '';
          const box = danger
            ? 'background:rgba(248,81,73,.12);border:1px solid #f85149;color:#c0392b'
            : 'background:rgba(210,153,34,.1);border:1px solid #d29922;color:#8a6d1a';
          const icon = danger ? '⚠️' : '💡';
          const body = a.msg
            ? esc(a.msg)
            : esc(a.member || '家人') + '「<b>' + esc(term) + '</b>」这单里有：' + esc(a.hit_in || '');
          html += '<div style="' + box + ';border-radius:8px;padding:8px 10px;margin:6px 0;font-size:12px">' +
            icon + ' <b>' + esc(kind) + '提醒：</b>' + body + '</div>';
        });
        recs.forEach((rec, ri) => {
          html += '<div class="pa-rec"><div class="pa-rec-head"><span>第 ' + (ri + 1) + ' 条</span>' +
            '<span class="pa-link" data-del="' + ri + '" style="cursor:pointer">✕ 移除</span></div>';
          fs.forEach(f => {
            const v = rec[f.key] == null ? '' : rec[f.key];
            const empty = String(v).trim() === '';
            if (f.type === 'select' && f.options.length) {
              html += '<div class="pa-frow"><label>' + esc(f.label) + '</label><select data-ri="' + ri + '" data-k="' + esc(f.key) + '">' +
                '<option value=""></option>' + f.options.map(o => '<option ' + (o === v ? 'selected' : '') + '>' + esc(o) + '</option>').join('') + '</select></div>';
            } else {
              html += '<div class="pa-frow"><label>' + esc(f.label) + '</label>' +
                '<input data-ri="' + ri + '" data-k="' + esc(f.key) + '" value="' + esc(v) + '" placeholder="' + (empty ? '留空可补' : '') + '"></div>';
            }
          });
          html += '</div>';
        });
        /* ★ #1407-A 一物多照：缩略图条 + 角色下拉 + 补拍按钮（说明框下方） */
        html += paImgStripHtml(imgs, imgNotes);
        html += '<div style="text-align:right;margin:2px 4px 0"><span class="pa-link pa-remember" style="cursor:pointer;font-size:11px">📌 以后都这样记</span></div>' +
          '<div class="pa-foot"><button type="button" class="pa-btn pa-cancel">取消</button>' +
          '<button type="button" class="pa-btn pa-btn-primary pa-ok">✅ 确认入库（' + recs.length + ' 条）</button></div>';
        dialog.innerHTML = html;
        dialog.querySelectorAll('[data-k]').forEach(el => {
          el.addEventListener('input', () => { recs[+el.dataset.ri][el.dataset.k] = el.value; });
          el.addEventListener('change', () => { recs[+el.dataset.ri][el.dataset.k] = el.value; });
        });
        dialog.querySelectorAll('[data-del]').forEach(el => el.addEventListener('click', () => {
          recs.splice(+el.dataset.del, 1);
          if (!recs.length) { overlay.remove(); return; }
          render();
        }));
        dialog.querySelector('.pa-cancel').addEventListener('click', () => overlay.remove());
        dialog.querySelector('.pa-ok').addEventListener('click', doSave);
        /* ★ #1407-A 图片条事件：补拍/换角色/删图 */
        paBindImgStrip(dialog, imgs, imgNotes, render);
        /* ★ 2026-09-18 个人词典沉淀：以后类似的话快车道直接命中，0 token */
        const rem = dialog.querySelector('.pa-remember');
        if (rem) rem.addEventListener('click', async () => {
          try {
            const r = await fetch('/api/assist/lexicon', { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ panel_id: panelId, domain: domain || '', records: recs, source_text: sourceText || '' }) });
            const j = await r.json();
            toast(j.success ? '📌 记住了，以后类似的话直接分对' : (j.error || '没记上'));
          } catch (e) { toast('没记上'); }
        });
      }

      async function doSave() {
        const okBtn = dialog.querySelector('.pa-ok');
        okBtn.disabled = true; okBtn.textContent = '入库中…';
        const notes = [];  /* ★ 2026-10-10 #1061 外卖记账联动等后端提示 */
        try {
          if (opts.save) {
            await opts.save(recs, domain);
          } else {
            const r = await fetch('/api/assist/confirm', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              /* ★ 照片链 A5：画质档随图入库；电脑恒原图标 hd（2026-10-11）
                 ★ #1407-A：imgs 数组（首拍+补拍 ≤9）+ image_notes 逐图角色标注 */
              body: JSON.stringify({ panel_id: panelId, domain: domain || '', records: recs,
                image_b64: imgs.length ? imgs : '', image_quality: quality || 'hd',
                image_notes: _paImgNotes(imgNotes) })
            });
            const jj = await r.json();
            if (!jj.success) throw new Error(jj.error || '入库失败');
            if (jj.note) notes.push(jj.note);
            if ((jj.inserted || 0) < recs.length) throw new Error('只入库 ' + (jj.inserted || 0) + '/' + recs.length + ' 条，请重试');
          }
          overlay.remove();
          const noteTxt = notes.length ? '（' + notes.join('；') + '）' : '';
          toast('✅ 已入库 ' + recs.length + ' 条' + noteTxt);
          if (opts.onSaved) opts.onSaved();
          loadFabBadge();
        } catch (e) {
          okBtn.disabled = false; okBtn.textContent = '✅ 确认入库（' + recs.length + ' 条）';
          toast(e.message || '入库失败');
        }
      }
      render();
    }

    /* ── 家庭空间分组确认卡（一句话/批量照片跨多个档案）── ★ B4 保留
       ★ B1/B2（2026-10-11 照片链）：每组可带 _img（组级配图随组入库）+ 面板下拉可切换；
       电脑恒原图（quality 固定 hd 随图入库，不出画质选项） */
    function showConfirmHome(groups, pendingImg) {
      const overlay = document.createElement('div');
      overlay.className = 'pa-overlay';
      const dialog = document.createElement('div');
      dialog.className = 'pa-card' + (light ? ' pa-light' : '');
      overlay.appendChild(dialog);
      document.body.appendChild(overlay);
      let _panelList = null;
      /* ★ #1407-A：每组 _img 兼容化成 _imgs 数组（≤9）+ 角色标注；首拍单图并入第一组 */
      groups.forEach((g, gi) => {
        g._imgs = paNormImgs(g._img || (gi === 0 ? pendingImg : ''));
        g._notes = g._imgs.map(() => '');
      });
      function getPanelList() {
        if (_panelList) return Promise.resolve(_panelList);
        return navGetPanels().then(list => { _panelList = list; return list; });
      }

      function render() {
        let html = '<h3>📋 确认入库</h3><div class="pa-sub">哈文自动分好类了，改一改或换面板都行，留空的以后在表单里补</div>';
        groups.forEach((g, gi) => {
          html += '<div class="pa-rec"><div class="pa-rec-head"><span>' + (g._img ? '📷 ' : '📁 ') +
            '<select data-gpanel="' + gi + '" style="border:1px solid #E5EAF2;border-radius:6px;padding:2px 6px;font-size:12px;max-width:180px"></select>' +
            (g._img ? ' <span style="font-size:11px;color:#8a93a6">照片随这条入库</span>' : '') + '</span>' +
            '<span class="pa-link" data-gdel="' + gi + '" style="cursor:pointer">✕ 整组移除</span></div>';
          /* ★ #1407-A 组级一物多照：图片条（缩略+角色+补拍） */
          html += paImgStripHtml(g._imgs, g._notes);
          const fs = normFields(g.fields);
          g.records.forEach((rec, ri) => {
            html += '<div class="pa-rec-item">';
            fs.forEach(f => {
              const v = rec[f.key] == null ? '' : rec[f.key];
              const empty = String(v).trim() === '';
              if (f.type === 'select' && f.options.length) {
                html += '<div class="pa-frow"><label>' + esc(f.label) + '</label><select data-g="' + gi + '" data-r="' + ri + '" data-k="' + esc(f.key) + '">' +
                  '<option value=""></option>' + f.options.map(o => '<option ' + (o === v ? 'selected' : '') + '>' + esc(o) + '</option>').join('') + '</select></div>';
              } else {
                html += '<div class="pa-frow"><label>' + esc(f.label) + '</label>' +
                  '<input data-g="' + gi + '" data-r="' + ri + '" data-k="' + esc(f.key) + '" value="' + esc(v) + '" placeholder="' + (empty ? '留空可补' : '') + '"></div>';
              }
            });
            html += '</div>';
          });
          html += '</div>';
        });
        if (!groups.length) html += '<div class="pa-loading">没有可入库的内容</div>';
        const total = groups.reduce((s, g) => s + g.records.length, 0);
        html += '<div class="pa-foot"><button type="button" class="pa-btn pa-cancel">取消</button>' +
          '<button type="button" class="pa-btn pa-btn-primary pa-ok"' + (total ? '' : ' disabled') + '>✅ 确认入库（' + total + ' 条）</button></div>';
        dialog.innerHTML = html;
        /* 面板下拉：切换时拉新 schema 重画该组（B2 面板可改） */
        getPanelList().then(list => {
          dialog.querySelectorAll('[data-gpanel]').forEach(sel => {
            const gi = +sel.dataset.gpanel;
            if (!groups[gi]) return;
            sel.innerHTML = '<option value="">选择面板…</option>' + list.map(p =>
              '<option value="' + esc(p.id) + '"' + (groups[gi].panel_id === p.id ? ' selected' : '') + '>' + esc(p.name || p.id) + '</option>').join('');
            sel.addEventListener('change', async () => {
              const pid = sel.value;
              if (!pid || !groups[gi]) return;
              try {
                const r = await fetch('/api/assist/schema?panel_id=' + encodeURIComponent(pid));
                const j = await r.json();
                if (j.success && j.schema && j.schema.fields) {
                  groups[gi].panel_id = pid;
                  groups[gi].domain = '';
                  groups[gi].label = j.schema.label || pid;
                  groups[gi].fields = j.schema.fields;
                  groups[gi].records = [{}];  /* 换面板后原字段对不上，给空行重填 */
                  render();
                } else { toast('这个面板拉不到字段，换一个试试'); }
              } catch (e) { toast('换面板失败：' + e.message); }
            });
          });
        });
        dialog.querySelectorAll('[data-k]').forEach(el => {
          const fn = () => { groups[+el.dataset.g].records[+el.dataset.r][el.dataset.k] = el.value; };
          el.addEventListener('input', fn);
          el.addEventListener('change', fn);
        });
        dialog.querySelectorAll('[data-gdel]').forEach(el => el.addEventListener('click', () => {
          groups.splice(+el.dataset.gdel, 1);
          if (!groups.length) { overlay.remove(); return; }
          render();
        }));
        dialog.querySelector('.pa-cancel').addEventListener('click', () => overlay.remove());
        dialog.querySelector('.pa-ok').addEventListener('click', doSaveHome);
        /* ★ #1407-A 各组图片条事件 */
        groups.forEach(g => paBindImgStrip(dialog, g._imgs, g._notes, render));
      }

      async function doSaveHome() {
        const okBtn = dialog.querySelector('.pa-ok');
        okBtn.disabled = true; okBtn.textContent = '入库中…';
        let saved = 0; const errs = []; const notes = [];
        try {
          for (let gi = 0; gi < groups.length; gi++) {
            const g = groups[gi];
            const r = await fetch('/api/assist/confirm', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              /* ★ 组级配图：批量照片每组带自己的图（#1407：多图数组+角色标注）；老调用（单图跟第一组）照旧；恒 hd（电脑恒原图） */
              body: JSON.stringify({ panel_id: g.panel_id, domain: g.domain || '', records: g.records,
                image_b64: (g._imgs && g._imgs.length) ? g._imgs : (gi === 0 && pendingImg ? pendingImg : ''),
                image_quality: 'hd', image_notes: _paImgNotes(g._notes) })
            });
            const jj = await r.json();
            if (jj.note) notes.push(jj.note);
            /* ★ 2026-09-16 假成功修复：只按后端真实 inserted 计数，不许拿条数顶数 */
            if (jj.success && (jj.inserted || 0) >= g.records.length) saved += jj.inserted;
            else errs.push((g.label || g.panel_id) + '：' + (jj.error || ('只入库 ' + (jj.inserted || 0) + '/' + g.records.length + ' 条，请重试')));
          }
          if (!saved && errs.length) throw new Error(errs.join('；'));
          overlay.remove();
          const noteTxt = notes.length ? '（' + notes.join('；') + '）' : '';
          toast('✅ 已入库 ' + saved + ' 条' + noteTxt + (errs.length ? '（部分失败：' + errs.join('；') + '）' : ''));
          if (opts.onSaved) opts.onSaved();
          loadFabBadge();
        } catch (e) {
          okBtn.disabled = false; okBtn.textContent = '✅ 确认入库';
          toast(e.message || '入库失败');
        }
      }
      render();
    }

    /* ── 待确认收纳盒（★ B4 2026-10-11：替代退役的聊天窗）──
       一个卡片弹层装两块：⏰ 到点提醒卡 + 📥 来自聊天的待确认记录；底部一行引导去首页聊天 */
    function openInbox() {
      closeInbox();
      state.open = true;
      const overlay = document.createElement('div');
      overlay.className = 'pa-overlay';
      const card = document.createElement('div');
      card.className = 'pa-card pa-inbox' + (light ? ' pa-light' : '');
      card.innerHTML = '<h3>📥 待确认收纳盒</h3>' +
        '<div class="pa-sub">哈文识别出来等你把关的记录、到点的提醒，处理完就消掉</div>' +
        '<div class="pa-inbox-alerts"></div>' +
        '<div class="pa-inbox-pending"><div class="pa-loading">正在取列表…</div></div>' +
        '<div style="margin-top:10px;padding-top:8px;border-top:1px dashed #E5EAF2;font-size:11.5px;color:#8a93a6">' +
        '随手记碎片和临时提醒在<b>管家中心</b>（左栏「管家」或首页「+」菜单）</div>';
      overlay.appendChild(card);
      document.body.appendChild(overlay);
      inboxEl = overlay;
      overlay.addEventListener('click', e => { if (e.target === overlay) closeInbox(); });
      renderAlertCards();
      renderPendingList();
      loadFabBadge();
    }
    function closeInbox() {
      state.open = false;
      try { if (inboxEl) inboxEl.remove(); } catch (e) { /* 静默 */ }
      inboxEl = null;
    }

    /* ── 圆钮红点 + 提醒卡（★ 2026-09-18 处理闭环；渲染目标改为收纳盒） ──
       ★ 2026-09-26 全量化（已定案A）：首页聊天产生的待确认在任何页面都能看到并处理 */
    async function loadFabBadge() {
      try {
        let n = 0;
        const r1 = await fetch('/api/assist/pending/count'); /* ★ 全量，不分面板 */
        const j1 = await r1.json();
        n += (j1.success && j1.count) || 0;
        /* ★ 2026-09-18 统一待办：到期提醒也进角标，点开收纳盒直接处理（去处理/知道了/删除） */
        let alerts = null;
        try {
          const r2 = await fetch('/api/assist/alerts?panel_id=' + encodeURIComponent(panelId));
          alerts = await r2.json();
        } catch (e2) {
          try {
            const r3 = await fetch('/api/home/due');
            alerts = await r3.json();
          } catch (e3) { /* 静默 */ }
        }
        if (alerts && alerts.success) {
          state.lastAlerts = alerts;
          const rems = alerts.reminders || alerts.items || [];
          n = (alerts.pending || 0) + rems.length;
          if (rems.length && !alerts.reminders) alerts.reminders = rems;
        }
        const dot = fab.querySelector('.pa-fab-dot');
        if (n > 0 && !state.open) { dot.style.display = 'block'; dot.textContent = n > 9 ? '9+' : n; }
        else { dot.style.display = 'none'; }
        if (state.open) renderAlertCards();
      } catch (e) { /* 静默 */ }
    }
    function renderAlertCards() {
      if (!inboxEl) return;
      const box = inboxEl.querySelector('.pa-inbox-alerts');
      if (!box) return;
      box.innerHTML = '';
      const j = state.lastAlerts;
      if (!j || !j.success) return;
      const items = (j.reminders || j.items || []);
      items.slice(0, 5).forEach(it => {
        /* ★ 2026-10-11 #1073 健康待办（it.health）：没有后端 id，知道了/删除 = 当天不再弹（localStorage） */
        if (it.health) {
          try { if (localStorage.getItem('pa_health_ack_' + it.id)) return; } catch (e) { /* 静默 */ }
        }
        const b = document.createElement('div');
        b.className = 'pa-rec pa-alert';
        const rep = { daily: '每天', weekly: '每周', monthly: '每月', once: '一次性' }[it.repeat] || it.repeat || '';
        b.innerHTML = '<div class="pa-src">⏰ <b>' + (it.health ? '健康待办' : '提醒到点') + '</b>：' + esc(it.title || '') + (rep ? '（' + esc(rep) + '）' : '') + '</div>' +
          '<div style="margin-top:6px;display:flex;gap:6px;justify-content:flex-end">' +
          (it.link ? '<button type="button" class="pa-btn pa-btn-primary pa-al-go">去处理</button>' : '') +
          '<button type="button" class="pa-btn pa-al-ack">知道了</button>' +
          '<button type="button" class="pa-btn pa-al-del">删除</button></div>';
        box.appendChild(b);
        const go = b.querySelector('.pa-al-go');
        if (go) go.addEventListener('click', () => { navTo(it.link); closeInbox(); });
        const healthAck = () => { try { localStorage.setItem('pa_health_ack_' + it.id, String(Date.now())); } catch (e) { /* 静默 */ } };
        b.querySelector('.pa-al-ack').addEventListener('click', async () => {
          if (it.health) { healthAck(); b.remove(); loadFabBadge(); return; }
          try { await fetch('/api/assist/alerts/reminder/' + it.id + '/ack', { method: 'POST' }); } catch (e) { /* 静默 */ }
          b.remove(); loadFabBadge();
        });
        b.querySelector('.pa-al-del').addEventListener('click', async () => {
          if (it.health) { healthAck(); b.remove(); loadFabBadge(); return; }
          if (!confirm('删除这条提醒？')) return;
          try { await fetch('/api/assist/alerts/reminder/' + it.id, { method: 'DELETE' }); } catch (e) { /* 静默 */ }
          b.remove(); loadFabBadge(); toast('提醒已删除');
        });
      });
    }

    /* 待确认列表（原 showPending 并入收纳盒） */
    function renderPendingList() {
      if (!inboxEl) return;
      const box = inboxEl.querySelector('.pa-inbox-pending');
      if (!box) return;
      const nameP = navGetPanels().then(list => { const m = {}; (list || []).forEach(p => { m[p.id] = p.name; }); return m; }).catch(() => ({}));
      Promise.all([nameP, fetch('/api/assist/pending').then(r => r.json())])
        .then(([nameMap, j]) => {
          if (!inboxEl) return;  /* 弹层可能已被关掉 */
          const items = j.items || [];
          box.innerHTML = items.length ? items.map(it =>
            '<div class="pa-rec" data-id="' + it.id + '"><div class="pa-src">🗣 ' + esc(it.source_text) + ' · ' + esc(it.created_at) +
            (nameMap[it.panel_id] ? ' · <b>→ ' + esc(nameMap[it.panel_id]) + '</b>' : '') + '</div>' +
            '<div style="font-size:12px">' + (it.records || []).map(r =>
              Object.entries(r).filter(([, v]) => String(v).trim()).map(([k, v]) => esc(k) + ':' + esc(v)).join(' · ')).join('<br>') + '</div>' +
            '<div class="pa-foot"><button type="button" class="pa-btn pa-ign">忽略</button><button type="button" class="pa-btn pa-btn-primary pa-save">入库</button></div></div>'
          ).join('') : '<div class="pa-loading">没有待确认的记录</div>';
          box.querySelectorAll('.pa-rec').forEach(el => {
            const id = el.dataset.id;
            el.querySelector('.pa-ign').addEventListener('click', async () => {
              await fetch('/api/assist/pending/' + id, { method: 'DELETE' }); el.remove(); loadFabBadge();
              if (!box.querySelector('.pa-rec')) closeInbox();
            });
            el.querySelector('.pa-save').addEventListener('click', async () => {
              const r = await fetch('/api/assist/pending/' + id + '/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
              const jj = await r.json();
              toast(jj.message || (jj.success ? '已入库' : '失败'));
              if (jj.success) { el.remove(); loadFabBadge(); if (opts.onSaved) opts.onSaved(); if (!box.querySelector('.pa-rec')) closeInbox(); }
            });
          });
        })
        .catch(() => { if (box) box.innerHTML = '<div class="pa-loading">取列表失败，稍后再开一次</div>'; });
    }

    /* ★ 单实例清理（B4：无聊天窗可清） */
    prevCleanup = function () {
      clearInterval(pollTimer);
      closeInbox();
      fab.remove();
    };

    const pollTimer = setInterval(loadFabBadge, 60000);
    loadFabBadge();

    /* ★ B4 api：open=收纳盒；showConfirm/showConfirmHome=确认卡（面板页「📷 批量照片录入」等按钮调用） */
    const api = { refreshPending: loadFabBadge, loadFabBadge: loadFabBadge, destroy: prevCleanup,
                  open: openInbox, showConfirm: showConfirm, showConfirmHome: showConfirmHome };
    lastApi = api; /* ★ 供 PanelAssist.open() 全局唤起 */
    return api;
  }

  return { mount: mount, __tryNav: tryAssistNav, open: function () { if (lastApi && lastApi.open) { try { lastApi.open(); } catch (e) { /* 静默 */ } } } };
})();
