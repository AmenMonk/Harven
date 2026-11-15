/* ★ 2026-09-19 面板浮动 AI 助手（定制页共用）
   背景：记账2.0/成长档案/宠物中心是定制页，生成器的「AI 分析」按钮不会出现在这些页上。
   本脚本给所有定制面板统一加一个右下角 ✨AI 浮钮，点击弹出分析面板：
   - 自动回显上次分析（GET /api/assist/panel-ai/last）
   - 可选填「想让 AI 重点看什么」后开始分析（POST /api/assist/panel-ai）
   面板 id 从 location.pathname 取（/panel/<id>/...），零配置接入。
   生成器面板已有自己的按钮，不引此脚本。 */
(function () {
  'use strict';
  var m = location.pathname.match(/\/panel\/([^\/]+)/);
  if (!m) return;
  var PID = m[1];

  var btn = document.createElement('button');
  btn.id = 'paf-fab';
  btn.title = 'AI 分析（本模块专职角色）';
  btn.textContent = '✨ AI';
  btn.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:99998;' +
    'padding:8px 14px;border:none;border-radius:20px;cursor:pointer;' +
    'background:#5B87D8;color:#fff;font-size:13px;font-weight:600;' +
    'box-shadow:0 4px 12px rgba(91,135,216,0.35);';

  var box = document.createElement('div');
  box.id = 'paf-box';
  box.style.cssText = 'display:none;position:fixed;right:16px;bottom:60px;z-index:99999;' +
    'width:min(420px,calc(100vw - 32px));max-height:70vh;overflow:auto;' +
    'background:#fff;border:1px solid #E5EAF2;border-radius:12px;padding:14px;' +
    'box-shadow:0 8px 28px rgba(40,60,100,0.18);font-family:inherit;';
  box.innerHTML =
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
    '  <b style="font-size:14px;color:#3A4453">✨ AI 分析</b>' +
    '  <span style="flex:1"></span>' +
    '  <button id="paf-close" style="border:none;background:none;cursor:pointer;font-size:16px;color:#8A93A3">✕</button>' +
    '</div>' +
    '<div style="display:flex;gap:6px;margin-bottom:8px">' +
    '  <input id="paf-q" placeholder="想让 AI 重点看什么？（可空）" style="flex:1;padding:7px 10px;' +
    '    border:1px solid #E5EAF2;border-radius:8px;font-size:12px;outline:none">' +
    '  <button id="paf-go" style="padding:7px 12px;border:none;border-radius:8px;cursor:pointer;' +
    '    background:#5B87D8;color:#fff;font-size:12px;font-weight:600">分析</button>' +
    '</div>' +
    '<div id="paf-out" style="white-space:pre-wrap;font-size:12px;line-height:1.7;color:#3A4453;' +
    '  background:#F7F9FC;border-radius:8px;padding:10px;min-height:40px"></div>';

  function esc(s) { var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

  function loadLast() {
    var out = document.getElementById('paf-out');
    out.textContent = '…';
    fetch('/api/assist/panel-ai/last?panel_id=' + encodeURIComponent(PID))
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var r = j && j.result;
        if (r && r.text) {
          var when = (r.created_at || '').replace('T', ' ').slice(0, 16);
          out.innerHTML = '<div style="color:#8A93A3;margin-bottom:6px">🕘 上次分析' +
            (when ? ' · ' + when : '') + (r.model ? ' · ' + esc(r.model) : '') + '</div>' + esc(r.text);
        } else {
          out.textContent = '还没有分析记录，点上面「分析」开始第一次 AI 分析。';
        }
      })
      .catch(function () { out.textContent = '⚠️ 读取上次分析失败'; });
  }

  function run() {
    var out = document.getElementById('paf-out');
    var q = document.getElementById('paf-q').value.trim();
    out.textContent = '⏳ 本地模型分析中，几秒钟到一分钟…';
    fetch('/api/assist/panel-ai', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ panel_id: PID, question: q })
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.success) {
        var when = (j.created_at || '').replace('T', ' ').slice(0, 16);
        out.innerHTML = (when ? '<div style="color:#8A93A3;margin-bottom:6px">✅ ' + when +
          (j.model ? ' · ' + esc(j.model) : '') + '</div>' : '') + esc(j.text);
      } else {
        out.textContent = '⚠️ ' + ((j && j.error) || '分析失败');
      }
    }).catch(function (e) { out.textContent = '⚠️ 请求失败: ' + e.message; });
  }

  btn.addEventListener('click', function () {
    var show = box.style.display === 'none';
    box.style.display = show ? 'block' : 'none';
    if (show) loadLast();
  });
  box.addEventListener('click', function (e) {
    if (e.target.id === 'paf-close') box.style.display = 'none';
    if (e.target.id === 'paf-go') run();
  });
  box.addEventListener('keydown', function (e) {
    if (e.target.id === 'paf-q' && e.key === 'Enter') run();
  });

  document.body.appendChild(btn);
  document.body.appendChild(box);
})();
