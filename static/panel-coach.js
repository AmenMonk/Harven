/**
 * 哈文·面板教练横幅（2026-10-22 教练层通用组件）
 * 用法：页面放 <div id="hvCoach"></div>，然后 HvCoach.mount({ panelId: 'fitness_log' })
 * 效果：顶部横幅「🧑‍🏫 哈文的观察：…」+ 1~3 个行动按钮
 *   navigate → 穿透父页 SPA 路由跳转 ｜ prefill → 跳聊天页自动发送
 */
(function () {
  const css = `
  .hvc-banner{display:flex;align-items:flex-start;gap:10px;border:1px solid #E6EAF2;border-left:4px solid #22A06B;
    border-radius:12px;padding:10px 12px;margin:8px 0 12px;background:#F7FBF8}
  .hvc-banner.lv-urge{border-left-color:#E8A23D;background:#FDFAF3}
  .hvc-av{font-size:20px;line-height:1.2}
  .hvc-body{flex:1}
  .hvc-t{font-size:13px;color:#3A4453;line-height:1.7}
  .hvc-t b{color:#1F9A5A}
  .hvc-btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
  .hvc-btn{font-size:12px;padding:4px 12px;border-radius:14px;border:1px solid #4A7DFF;background:#fff;color:#4A7DFF;cursor:pointer}
  .hvc-btn.pri{background:#4A7DFF;color:#fff}
  .hvc-btn:hover{filter:brightness(1.05)}
  .hvc-brief{font-size:12px;color:#5A6474;margin-top:6px;padding:6px 9px;background:#fff;border-radius:8px;border:1px solid #E6EAF2}
  .hvc-brief.ok{color:#1F9A5A;background:#F2FAF5}
  .hvc-stats{display:flex;gap:8px;flex-wrap:wrap;margin-top:7px}
  .hvc-st{display:inline-flex;flex-direction:column;align-items:center;background:#fff;border:1px solid #E6EAF2;border-radius:10px;padding:5px 12px;min-width:72px}
  .hvc-st i{font-style:normal;font-size:10.5px;color:#8A94A6}
  .hvc-st b{font-size:14px;color:#2B3446}
  .hvc-st b u{text-decoration:none;font-size:10px;color:#8A94A6;margin-left:1px}
  `;
  function injectCss() {
    if (document.getElementById('hvc-style')) return;
    const s = document.createElement('style'); s.id = 'hvc-style'; s.textContent = css;
    document.head.appendChild(s);
  }
  function esc(x) { return String(x || '').replace(/</g, '&lt;'); }

  function runAction(a) {
    if (!a) return;
    /* ★ 2026-10-22 form 动作：本面板内直接开录入表单（跳路由对本面板无效=点不动的根因） */
    if (a.action === 'form') {
      if (window.PanelApp && PanelApp.showForm) { PanelApp.showForm(); }
      else if (a.path) { try { window.parent.location.hash = '#' + a.path; } catch (e) { location.hash = '#' + a.path; } }
      return;
    }
    if (a.action === 'navigate' && a.path) {
      try { window.parent.location.hash = '#' + a.path; } catch (e) { location.hash = '#' + a.path; }
    } else if (a.action === 'prefill' && a.q) {
      const u = '#/?prefill=' + encodeURIComponent(a.q) + '&autosend=1';
      try { window.parent.location.hash = u; } catch (e) { location.hash = u; }
    } else if (a.action === 'api' && a.url) {
      fetch(a.url, { method: a.method || 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(a.body || {}) });
    }
  }

  window.HvCoach = {
    async mount(opts) {
      injectCss();
      const el = document.querySelector((opts && opts.container) || '#hvCoach');
      if (!el) return;
      const pid = (opts && opts.panelId) || '';
      try {
        /* ★ 2026-10-22 每日早报段：有事项列出来，没事项也显示「今日无待办」——早报必须可见 */
        let briefHtml = ''
        try {
          const b = await fetch('/api/coach/brief').then(x => x.json())
          const items = b.items || []
          briefHtml = items.length
            ? '<div class="hvc-brief">📋 今日：' + items.map(i => esc(i.text)).join('　') + '</div>'
            : '<div class="hvc-brief ok">✅ 今日无待办（生日/临期/目标节点都正常）</div>'
        } catch (_) {}
        const r = await fetch('/api/coach/observe?panel_id=' + encodeURIComponent(pid)).then(x => x.json());
        /* ★ 2026-10-22 数据指标 chips：即使没有数据也显示 0/—（开发者要求） */
        let statsHtml = ''
        const stats = r.stats || []
        if (stats.length) {
          statsHtml = '<div class="hvc-stats">' + stats.map(st =>
            `<span class="hvc-st"><i>${esc(st.label)}</i><b>${esc(st.value)}<u>${esc(st.unit || '')}</u></b></span>`).join('') + '</div>'
        }
        if (!r.success || !r.text) { el.innerHTML = `<div class="hvc-banner lv-info"><span class="hvc-av">🧑‍🏫</span><div class="hvc-body">${briefHtml}${statsHtml}</div></div>`; return; }
        /* ★ 2026-10-22 去重：生成品面板自带「＋添加」按钮，教练横幅的 form 类「录一条」与之重复→跳过
           （自定义面板无 ＋添加 时仍显示，保底入口不丢） */
        const hasAdd = !!(window.PanelApp && PanelApp.showForm)
        const acts = (r.actions || []).filter(a => !(a.action === 'form' && hasAdd))
        const btns = acts.map((a, i) =>
          `<button class="hvc-btn${i === 0 ? ' pri' : ''}" data-i="${i}">${esc(a.label)}</button>`).join('');
        el.innerHTML = `<div class="hvc-banner lv-${r.level || 'info'}">
          <span class="hvc-av">🧑‍🏫</span>
          <div class="hvc-body"><div class="hvc-t"><b>哈文的观察：</b>${esc(r.text)}</div>
            ${briefHtml}${statsHtml}
            ${btns ? '<div class="hvc-btns">' + btns + '</div>' : ''}</div></div>`;
        el.querySelectorAll('.hvc-btn').forEach(b => {
          b.onclick = () => runAction(acts[parseInt(b.dataset.i)]);
        });
      } catch (e) { el.innerHTML = ''; }
    },
  };
})();
