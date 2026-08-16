/**
 * Harven AI - Panel System (面板系统 JS)
 * 管理面板的安装/卸载/创建/打开/关闭
 */

var PANELS_MODULE = (function() {
  'use strict';

  var panels = [];
  var currentPanelId = null;

  // ═══════════════ 左侧面板列表 ═══════════════

  function loadPanelList() {
    fetch('/api/panels')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        panels = data.panels || [];
        renderSidebarList();
        renderManageList();
      })
      .catch(function(e) {
        console.error('[Panels] 加载面板列表失败:', e);
        var list = document.getElementById('panelList');
        if (list) list.innerHTML = '<div style="text-align:center;color:var(--text-tertiary);font-size:11px;padding:8px 0;">加载失败</div>';
      });
  }

  function renderSidebarList() {
    var list = document.getElementById('panelList');
    if (!list) return;
    if (!panels.length) {
      list.innerHTML = '<div style="text-align:center;color:var(--text-tertiary);font-size:11px;padding:8px 0;">暂无面板</div>';
      return;
    }
    var icons = { wallet: '💰', table: '📋', kanban: '📌', dashboard: '📊', calendar: '📅', form: '📝', file: '📄' };
    list.innerHTML = panels.map(function(p) {
      var icon = icons[p.icon] || icons.file;
      return '<div class="panel-sidebar-item" onclick="window.openPanel(\'' + p.id + '\')" ' +
        'style="display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:6px;cursor:pointer;' +
        'color:var(--text-secondary);font-size:12px;transition:background 0.15s;" ' +
        'onmouseenter="this.style.background=\'var(--bg-hover)\'" ' +
        'onmouseleave="if(window._currentPanelId!==\'' + p.id + '\')this.style.background=\'transparent\'">' +
        '<span style="font-size:14px;">' + icon + '</span>' +
        '<span>' + escapeHtml(p.name) + '</span>' +
      '</div>';
    }).join('');
  }

  // ═══════════════ 面板管理模态框 ═══════════════

  function openPanelManager() {
    // 先关闭面板查看器
    var viewer = document.getElementById('panelViewer');
    if (viewer) viewer.style.display = 'none';
    var cw = document.getElementById('chatWrapper');
    if (cw) cw.style.display = 'flex';

    // 移除旧弹窗
    var old = document.getElementById('_panelMgrDyn');
    if (old) old.remove();

    var overlay = document.createElement('div');
    overlay.id = '_panelMgrDyn';
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.85);z-index:99999;display:flex;align-items:center;justify-content:center;';

    var box = document.createElement('div');
    box.style.cssText = 'background:#fff;border-radius:12px;padding:0;width:520px;max-height:85vh;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.3);';

    // 头部
    var header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:14px 20px;border-bottom:1px solid #eee;flex-shrink:0;';
    header.innerHTML = '<h3 style="margin:0;font-size:16px;color:#333;">面板管理</h3>';
    var closeBtn = document.createElement('button');
    closeBtn.textContent = '✕';
    closeBtn.style.cssText = 'background:none;border:none;font-size:20px;cursor:pointer;color:#999;padding:4px;';
    closeBtn.onclick = function() { overlay.remove(); };
    header.appendChild(closeBtn);
    box.appendChild(header);

    // Tab按钮
    var tabBar = document.createElement('div');
    tabBar.style.cssText = 'display:flex;border-bottom:1px solid #eee;flex-shrink:0;';
    var tabs = [
      {id:'installed', label:'📋 已安装'},
      {id:'install', label:'📦 安装ZIP'},
      {id:'create', label:'➕ 创建面板'}
    ];
    tabs.forEach(function(t, i) {
      var btn = document.createElement('button');
      btn.textContent = t.label;
      btn.dataset.tab = t.id;
      btn.style.cssText = 'flex:1;padding:10px;border:none;background:transparent;cursor:pointer;font-size:13px;color:' + (i===0?'#4f46e5':'#999') + ';border-bottom:2px solid ' + (i===0?'#4f46e5':'transparent') + ';';
      btn.onclick = function() {
        box.querySelectorAll('[data-tabbtn]').forEach(function(b) {
          b.style.color = '#999'; b.style.borderBottomColor = 'transparent';
        });
        btn.style.color = '#4f46e5'; btn.style.borderBottomColor = '#4f46e5';
        box.querySelector('[data-tabcontent="installed"]').style.display = (t.id==='installed') ? 'block' : 'none';
        box.querySelector('[data-tabcontent="install"]').style.display = (t.id==='install') ? 'block' : 'none';
        box.querySelector('[data-tabcontent="create"]').style.display = (t.id==='create') ? 'block' : 'none';
      };
      btn.setAttribute('data-tabbtn', t.id);
      tabBar.appendChild(btn);
    });
    box.appendChild(tabBar);

    // 内容区
    var body = document.createElement('div');
    body.style.cssText = 'overflow-y:auto;flex:1;padding:16px;';

    // Tab1: 已安装
    var tabInstalled = document.createElement('div');
    tabInstalled.setAttribute('data-tabcontent', 'installed');
    tabInstalled.innerHTML = '<p style="color:#999;text-align:center;padding:20px;">加载中...</p>';
    body.appendChild(tabInstalled);

    // Tab2: 安装ZIP
    var tabInstall = document.createElement('div');
    tabInstall.setAttribute('data-tabcontent', 'install');
    tabInstall.style.display = 'none';
    tabInstall.innerHTML =
      '<div style="border:2px dashed #ddd;border-radius:8px;padding:30px;text-align:center;">' +
        '<p style="color:#666;margin:0 0 12px 0;">选择面板ZIP文件安装</p>' +
        '<input type="file" id="_panelZipInput" accept=".zip" style="display:none;">' +
        '<button onclick="document.getElementById(\'_panelZipInput\').click()" style="background:#4f46e5;border:none;border-radius:6px;padding:8px 24px;color:#fff;cursor:pointer;font-size:13px;">选择ZIP文件</button>' +
        '<div id="_panelInstallFb" style="margin-top:12px;font-size:12px;"></div>' +
      '</div>';
    body.appendChild(tabInstall);

    // Tab3: 创建面板
    var tabCreate = document.createElement('div');
    tabCreate.setAttribute('data-tabcontent', 'create');
    tabCreate.style.display = 'none';
    tabCreate.innerHTML =
      // ★ 2026-08-08: 模板选择
      '<div style="margin-bottom:10px;"><label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">📋 选择模板</label>' +
        '<select id="_cpTemplate" onchange="window._applyPanelTemplate&&window._applyPanelTemplate(this.value)" style="width:100%;padding:8px;border-radius:6px;border:1px solid #ddd;font-size:13px;">' +
          '<option value="blank">空白面板（自定义）</option>' +
          '<option value="accounting">💰 记账面板（带统计图表）</option>' +
          '<option value="fitness">💪 健身面板（运动记录+趋势）</option>' +
          '<option value="todo">✅ 待办面板（任务管理）</option>' +
          '<option value="inventory">📦 库存面板（物品管理）</option>' +
        '</select></div>' +
      '<div style="margin-bottom:10px;"><label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">面板名称 *</label>' +
        '<input type="text" id="_cpName" placeholder="例如：家庭记账" style="width:100%;padding:8px;border-radius:6px;border:1px solid #ddd;font-size:13px;"></div>' +
      '<div style="margin-bottom:10px;"><label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">面板ID *（英文）</label>' +
        '<input type="text" id="_cpId" placeholder="如 accounting" style="width:100%;padding:8px;border-radius:6px;border:1px solid #ddd;font-size:13px;"></div>' +
      '<div style="margin-bottom:10px;"><label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">描述</label>' +
        '<input type="text" id="_cpDesc" placeholder="面板用途" style="width:100%;padding:8px;border-radius:6px;border:1px solid #ddd;font-size:13px;"></div>' +
      // ★ 2026-08-08: 图标选择
      '<div style="margin-bottom:10px;"><label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">图标</label>' +
        '<div id="_cpIconPicker" style="display:flex;gap:6px;flex-wrap:wrap;">' +
          '<span onclick="window._selectIcon&&window._selectIcon(this,\'💰\')" style="cursor:pointer;font-size:20px;padding:4px 8px;border-radius:6px;border:1px solid #ddd;">💰</span>' +
          '<span onclick="window._selectIcon&&window._selectIcon(this,\'💪\')" style="cursor:pointer;font-size:20px;padding:4px 8px;border-radius:6px;border:1px solid #ddd;">💪</span>' +
          '<span onclick="window._selectIcon&&window._selectIcon(this,\'✅\')" style="cursor:pointer;font-size:20px;padding:4px 8px;border-radius:6px;border:1px solid #ddd;">✅</span>' +
          '<span onclick="window._selectIcon&&window._selectIcon(this,\'📦\')" style="cursor:pointer;font-size:20px;padding:4px 8px;border-radius:6px;border:1px solid #ddd;">📦</span>' +
          '<span onclick="window._selectIcon&&window._selectIcon(this,\'📅\')" style="cursor:pointer;font-size:20px;padding:4px 8px;border-radius:6px;border:1px solid #ddd;">📅</span>' +
          '<span onclick="window._selectIcon&&window._selectIcon(this,\'📊\')" style="cursor:pointer;font-size:20px;padding:4px 8px;border-radius:6px;border:1px solid #ddd;">📊</span>' +
          '<span onclick="window._selectIcon&&window._selectIcon(this,\'🏠\')" style="cursor:pointer;font-size:20px;padding:4px 8px;border-radius:6px;border:1px solid #ddd;">🏠</span>' +
          '<span onclick="window._selectIcon&&window._selectIcon(this,\'🎯\')" style="cursor:pointer;font-size:20px;padding:4px 8px;border-radius:6px;border:1px solid #ddd;">🎯</span>' +
        '</div>' +
        '<input type="hidden" id="_cpIcon" value="💰">' +
      '</div>' +
      // ★ 2026-08-08: 统计选项
      '<div style="margin-bottom:10px;">' +
        '<label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#666;cursor:pointer;">' +
          '<input type="checkbox" id="_cpChart" checked style="cursor:pointer;"><span>📊 生成统计图表（汇总卡片+分类柱状图）</span>' +
        '</label>' +
        '<label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#666;cursor:pointer;margin-top:4px;">' +
          '<input type="checkbox" id="_cpSearch" checked style="cursor:pointer;"><span>🔍 生成搜索筛选功能</span>' +
        '</label>' +
      '</div>' +
      '<div style="margin-bottom:10px;"><div style="display:flex;justify-content:space-between;align-items:center;">' +
        '<span style="font-size:12px;color:#666;font-weight:500;">字段定义</span>' +
        '<button id="_cpAddField" style="background:#f0f0f5;border:1px solid #ddd;border-radius:4px;color:#666;cursor:pointer;font-size:11px;padding:2px 10px;">+ 添加字段</button>' +
      '</div><div id="_cpFields" style="margin-top:8px;"></div></div>' +
      // ★ 2026-08-08: 按钮组（创建+预览）
      '<div style="display:flex;gap:8px;margin-top:8px;">' +
        '<button id="_cpCreate" style="flex:1;background:#4f46e5;border:none;border-radius:6px;padding:10px;color:#fff;cursor:pointer;font-size:13px;">创建面板</button>' +
        '<button id="_cpPreview" style="background:#f0f0f5;border:1px solid #ddd;border-radius:6px;padding:10px 16px;color:#666;cursor:pointer;font-size:13px;">👁 预览</button>' +
      '</div>' +
      '<div id="_cpFeedback" style="margin-top:10px;text-align:center;font-size:12px;"></div>' +
      '<div id="_cpPreviewArea" style="display:none;margin-top:12px;border:1px solid #ddd;border-radius:8px;padding:12px;background:#fafafa;"></div>';
    body.appendChild(tabCreate);

    box.appendChild(body);
    overlay.appendChild(box);
    overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);

    // 加载已安装面板列表
    _loadInstalledList(tabInstalled);

    // ZIP安装
    var zipInput = tabInstall.querySelector('#_panelZipInput');
    if (zipInput) {
      zipInput.onchange = function() {
        var file = zipInput.files[0];
        if (!file) return;
        var fb = tabInstall.querySelector('#_panelInstallFb');
        if (fb) fb.innerHTML = '<span style="color:#4f46e5;">安装中...</span>';
        var formData = new FormData();
        formData.append('file', file);
        fetch('/api/panels/install', { method: 'POST', body: formData })
          .then(function(r) { return r.json(); })
          .then(function(data) {
            if (fb) fb.innerHTML = data.success ? '<span style="color:#22c55e;">' + data.message + '</span>' : '<span style="color:#e55;">' + (data.message||'失败') + '</span>';
            if (data.success) { _loadInstalledList(tabInstalled); loadPanelList(); }
          })
          .catch(function(e) { if (fb) fb.innerHTML = '<span style="color:#e55;">' + e.message + '</span>'; });
      };
    }

    // 创建面板
    var createFields = [];
    var fieldsContainer = tabCreate.querySelector('#_cpFields');
    var addFieldBtn = tabCreate.querySelector('#_cpAddField');
    if (addFieldBtn) {
      addFieldBtn.onclick = function() {
        var idx = createFields.length;
        createFields.push({name:'',label:'',type:'text'});
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:4px;margin-bottom:4px;';
        row.innerHTML =
          '<input placeholder="字段名(en)" style="flex:1;padding:5px;border-radius:4px;border:1px solid #ddd;font-size:12px;" oninput="window._cpFields['+idx+'].name=this.value">' +
          '<input placeholder="显示名" style="flex:1;padding:5px;border-radius:4px;border:1px solid #ddd;font-size:12px;" oninput="window._cpFields['+idx+'].label=this.value">' +
          '<select style="width:80px;padding:5px;border-radius:4px;border:1px solid #ddd;font-size:12px;" onchange="window._cpFields['+idx+'].type=this.value">' +
            '<option value="text">文本</option><option value="number">数字</option><option value="date">日期</option><option value="select">下拉</option>' +
            '<option value="textarea">多行文本</option><option value="time">时间</option><option value="image">图片</option></select>';
        fieldsContainer.appendChild(row);
      };
    }
    window._cpFields = createFields;
    // ★ 2026-08-08: 绑定预览按钮
    var previewBtn = tabCreate.querySelector('#_cpPreview');
    if (previewBtn) previewBtn.onclick = window._cpPreview;
    var createBtn = tabCreate.querySelector('#_cpCreate');
    if (createBtn) {
      createBtn.onclick = function() {
        var name = tabCreate.querySelector('#_cpName').value.trim();
        var pid = tabCreate.querySelector('#_cpId').value.trim();
        var desc = tabCreate.querySelector('#_cpDesc').value.trim();
        var icon = tabCreate.querySelector('#_cpIcon') ? tabCreate.querySelector('#_cpIcon').value : '💰';
        var withChart = tabCreate.querySelector('#_cpChart') ? tabCreate.querySelector('#_cpChart').checked : true;
        var withSearch = tabCreate.querySelector('#_cpSearch') ? tabCreate.querySelector('#_cpSearch').checked : true;
        var fb = tabCreate.querySelector('#_cpFeedback');
        var fields = createFields.filter(function(f){return f&&f.name&&f.label;});
        if (!name) { if(fb)fb.innerHTML='<span style="color:#e55;">请输入名称</span>'; return; }
        if (!pid) { if(fb)fb.innerHTML='<span style="color:#e55;">请输入ID</span>'; return; }
        if (!fields.length) { if(fb)fb.innerHTML='<span style="color:#e55;">请添加字段</span>'; return; }
        if(fb)fb.innerHTML='<span style="color:#4f46e5;">创建中...</span>';
        fetch('/api/panels/create', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name,id:pid,fields:fields,description:desc,icon:icon,with_chart:withChart,with_search:withSearch}) })
          .then(function(r){return r.json();})
          .then(function(data){
            if(fb)fb.innerHTML = data.success ? '<span style="color:#22c55e;">'+data.message+'</span>' : '<span style="color:#e55;">'+(data.message||'失败')+'</span>';
            if(data.success){ _loadInstalledList(tabInstalled); loadPanelList(); }
          })
          .catch(function(e){ if(fb)fb.innerHTML='<span style="color:#e55;">'+e.message+'</span>'; });
      };
    }
  }

  function _loadInstalledList(container) {
    if (!container) return;
    container.innerHTML = '<p style="color:#999;text-align:center;padding:20px;">加载中...</p>';
    fetch('/api/panels').then(function(r) { return r.json(); }).then(function(data) {
      var panels = data.panels || [];
      var html = '<div style="margin-bottom:12px;"><strong style="color:#333;">已安装面板 (' + panels.length + ')</strong></div>';
      if (panels.length === 0) {
        html += '<p style="color:#999;text-align:center;padding:20px;">暂无已安装面板</p>';
      } else {
        panels.forEach(function(p) {
          html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #eee;">' +
            '<div><strong style="color:#333;">' + p.name + '</strong>' +
            '<span style="color:#999;font-size:11px;margin-left:8px;">v' + p.version + '</span></div>' +
            '<button onclick="window.uninstallPanel(\'' + p.id + '\')" style="background:none;border:1px solid #e55;border-radius:4px;color:#e55;cursor:pointer;font-size:11px;padding:3px 10px;">卸载</button>' +
            '</div>';
        });
      }
      container.innerHTML = html;
    }).catch(function(e) {
      container.innerHTML = '<p style="color:#e55;">加载失败: ' + e.message + '</p>';
    });
  }

  function closePanelManager() {
    var old = document.getElementById('_panelMgrDyn');
    if (old) old.remove();
  }

  // ★ 2026-08-08: 模板选择功能
  window._applyPanelTemplate = function(template) {
    var templates = {
      'accounting': {
        name: '家庭记账', id: 'my-accounting', desc: '管理家庭收入与支出',
        icon: '💰', chart: true, search: true,
        fields: [
          {name:'amount',label:'金额',type:'number'},
          {name:'category',label:'分类',type:'select',options:'餐饮,交通,购物,住房,娱乐,医疗,教育,其他'},
          {name:'type',label:'类型',type:'select',options:'支出,收入'},
          {name:'date',label:'日期',type:'date'},
          {name:'note',label:'备注',type:'text'}
        ]
      },
      'fitness': {
        name: '健身记录', id: 'my-fitness', desc: '运动数据追踪',
        icon: '💪', chart: true, search: true,
        fields: [
          {name:'date',label:'日期',type:'date'},
          {name:'exercise',label:'运动项目',type:'select',options:'跑步,游泳,健身,瑜伽,骑行,其他'},
          {name:'duration',label:'时长(分钟)',type:'number'},
          {name:'calories',label:'消耗(卡)',type:'number'},
          {name:'note',label:'备注',type:'text'}
        ]
      },
      'todo': {
        name: '待办事项', id: 'my-todo', desc: '任务管理',
        icon: '✅', chart: false, search: true,
        fields: [
          {name:'task',label:'任务',type:'text'},
          {name:'priority',label:'优先级',type:'select',options:'高,中,低'},
          {name:'status',label:'状态',type:'select',options:'待办,进行中,完成'},
          {name:'deadline',label:'截止日期',type:'date'}
        ]
      },
      'inventory': {
        name: '物品库存', id: 'my-inventory', desc: '物品管理',
        icon: '📦', chart: false, search: true,
        fields: [
          {name:'name',label:'物品名称',type:'text'},
          {name:'quantity',label:'数量',type:'number'},
          {name:'location',label:'存放位置',type:'text'},
          {name:'category',label:'分类',type:'select',options:'食品,日用,电子,工具,其他'},
          {name:'expire',label:'过期日期',type:'date'}
        ]
      }
    };

    if (template === 'blank' || !templates[template]) {
      // 清空表单
      var n = document.getElementById('_cpName'); if (n) n.value = '';
      var i = document.getElementById('_cpId'); if (i) i.value = '';
      var d = document.getElementById('_cpDesc'); if (d) d.value = '';
      window._cpFields = [];
      var fc = document.getElementById('_cpFields'); if (fc) fc.innerHTML = '';
      var c = document.getElementById('_cpChart'); if (c) c.checked = true;
      var s = document.getElementById('_cpSearch'); if (s) s.checked = true;
      return;
    }

    var t = templates[template];
    var n = document.getElementById('_cpName'); if (n) n.value = t.name;
    var i = document.getElementById('_cpId'); if (i) i.value = t.id;
    var d = document.getElementById('_cpDesc'); if (d) d.value = t.desc;
    var icon = document.getElementById('_cpIcon'); if (icon) icon.value = t.icon;
    var c = document.getElementById('_cpChart'); if (c) c.checked = t.chart;
    var s = document.getElementById('_cpSearch'); if (s) s.checked = t.search;
    // 设置图标选中
    var picker = document.getElementById('_cpIconPicker');
    if (picker) {
      picker.querySelectorAll('span').forEach(function(el) {
        el.style.borderColor = el.textContent === t.icon ? '#4f46e5' : '#ddd';
      });
    }
    // 填充字段
    window._cpFields = JSON.parse(JSON.stringify(t.fields));
    var fc = document.getElementById('_cpFields');
    if (fc) {
      fc.innerHTML = '';
      window._cpFields.forEach(function(f, idx) {
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:6px;margin-bottom:6px;';
        row.innerHTML =
          '<input value="' + (f.name||'') + '" placeholder="字段名" style="flex:1;padding:5px;border-radius:4px;border:1px solid #ddd;font-size:12px;" oninput="window._cpFields[' + idx + '].name=this.value">' +
          '<input value="' + (f.label||'') + '" placeholder="显示名" style="flex:1;padding:5px;border-radius:4px;border:1px solid #ddd;font-size:12px;" oninput="window._cpFields[' + idx + '].label=this.value">' +
          '<select style="width:80px;padding:5px;border-radius:4px;border:1px solid #ddd;font-size:12px;" onchange="window._cpFields[' + idx + '].type=this.value;window._cpFields[' + idx + '].options=this.value==\'select\'?\'选项1,选项2,选项3\':\'\'">' +
            '<option value="text"' + (f.type==='text'?' selected':'') + '>文本</option>' +
            '<option value="number"' + (f.type==='number'?' selected':'') + '>数字</option>' +
            '<option value="date"' + (f.type==='date'?' selected':'') + '>日期</option>' +
            '<option value="select"' + (f.type==='select'?' selected':'') + '>下拉</option>' +
          '</select>';
        fc.appendChild(row);
      });
    }
  };

  // ★ 2026-08-08: 图标选择
  window._selectIcon = function(el, icon) {
    var picker = document.getElementById('_cpIconPicker');
    if (picker) {
      picker.querySelectorAll('span').forEach(function(e) { e.style.borderColor = '#ddd'; });
    }
    el.style.borderColor = '#4f46e5';
    var hidden = document.getElementById('_cpIcon');
    if (hidden) hidden.value = icon;
  };

  // ★ 2026-08-08: 预览
  window._cpPreview = function() {
    var name = (document.getElementById('_cpName') || {}).value || '面板预览';
    var icon = (document.getElementById('_cpIcon') || {}).value || '📋';
    var withChart = (document.getElementById('_cpChart') || {}).checked;
    var fields = window._cpFields || [];
    var area = document.getElementById('_cpPreviewArea');
    if (!area) return;
    var html = '<div style="font-size:14px;font-weight:bold;margin-bottom:8px">' + icon + ' ' + name + ' <span style="font-size:11px;color:#999">（预览）</span></div>';
    if (withChart) {
      html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px">';
      html += '<div style="background:#f5f5f5;padding:8px;border-radius:6px;text-align:center"><div style="font-size:10px;color:#666">总记录</div><div style="font-size:16px;color:#4f46e5">0</div></div>';
      html += '<div style="background:#f5f5f5;padding:8px;border-radius:6px;text-align:center"><div style="font-size:10px;color:#666">分类</div><div style="font-size:16px;color:#22c55e">0</div></div>';
      html += '<div style="background:#f5f5f5;padding:8px;border-radius:6px;text-align:center"><div style="font-size:10px;color:#666">数值合计</div><div style="font-size:16px;color:#e55">0</div></div>';
      html += '</div>';
    }
    html += '<table style="width:100%;font-size:11px;border-collapse:collapse"><tr>';
    fields.forEach(function(f) { html += '<th style="text-align:left;padding:4px;border-bottom:1px solid #ddd;color:#666">' + (f.label||f.name) + '</th>'; });
    html += '<th></th></tr><tr><td colspan="' + (fields.length+1) + '" style="padding:12px;text-align:center;color:#999">暂无数据</td></tr></table>';
    area.innerHTML = html;
    area.style.display = 'block';
  };

  function switchPanelTab(tab) {
    var tabs = { installed: document.getElementById('tabInstalled'), install: document.getElementById('tabInstall'), create: document.getElementById('tabCreate') };
    var btns = { installed: document.getElementById('tabManageInstalled'), install: document.getElementById('tabManageInstall'), create: document.getElementById('tabManageCreate') };
    Object.keys(tabs).forEach(function(k) {
      if (tabs[k]) tabs[k].style.display = (k === tab) ? 'block' : 'none';
      if (btns[k]) { btns[k].classList.toggle('active', k === tab); }
    });
    if (tab === 'installed') renderManageList();
  }

  function renderManageList() {
    var container = document.getElementById('panelManageList');
    if (!container) return;
    if (!panels.length) {
      container.innerHTML = '<div style="text-align:center;color:var(--text-tertiary);font-size:12px;">暂无已安装面板</div>';
      return;
    }
    container.innerHTML = panels.map(function(p) {
      return '<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:0.5px solid var(--border);">' +
        '<div><strong style="color:var(--text-primary);">' + escapeHtml(p.name) + '</strong>' +
        '<span style="color:var(--text-tertiary);font-size:11px;margin-left:8px;">v' + escapeHtml(p.version) + '</span></div>' +
        '<button onclick="window.uninstallPanel(\'' + p.id + '\')" style="background:none;border:0.5px solid #804040;border-radius:4px;color:#e05050;cursor:pointer;font-size:11px;padding:3px 10px;">卸载</button>' +
      '</div>';
    }).join('');
  }

  function installPanelFromFile(input) {
    var file = input.files[0];
    if (!file) return;
    var feedback = document.getElementById('installFeedback');
    if (feedback) feedback.innerHTML = '<span style="color:var(--accent);">安装中...</span>';

    var formData = new FormData();
    formData.append('file', file);

    fetch('/api/panels/install', { method: 'POST', body: formData })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.success) {
          if (feedback) feedback.innerHTML = '<span style="color:var(--text-success);">' + data.message + '</span>';
          input.value = '';
          loadPanelList();
        } else {
          if (feedback) feedback.innerHTML = '<span style="color:var(--text-danger);">' + (data.message || '安装失败') + '</span>';
        }
      })
      .catch(function(e) {
        if (feedback) feedback.innerHTML = '<span style="color:var(--text-danger);">网络错误: ' + e.message + '</span>';
      });
  }

  function uninstallPanel(panelId) {
    if (!confirm('确定要卸载此面板吗？所有数据将被删除。')) return;
    fetch('/api/panels/' + panelId, { method: 'DELETE' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.success) {
          if (currentPanelId === panelId) closePanel();
          loadPanelList();
        } else {
          alert(data.message || '卸载失败');
        }
      });
  }

  // ═══════════════ 面板创建（模板） ═══════════════

  var createFields = [];

  function addCreateField() {
    var list = document.getElementById('createFieldsList');
    if (!list) return;
    var idx = createFields.length;
    createFields.push({ name: '', label: '', type: 'text', options: '' });
    var div = document.createElement('div');
    div.className = 'create-field-row';
    div.id = 'createFieldRow' + idx;
    div.style.cssText = 'margin-bottom:8px;';
    div.innerHTML =
      '<div style="display:flex;gap:6px;align-items:center;">' +
      '<input type="text" placeholder="字段名(en)" onchange="window._updateCreateField(' + idx + ', \'name\', this.value)" style="flex:1;padding:6px;border-radius:4px;border:0.5px solid var(--border);background:var(--bg-secondary);color:var(--text-primary);font-size:12px;">' +
      '<input type="text" placeholder="显示名" onchange="window._updateCreateField(' + idx + ', \'label\', this.value)" style="flex:1;padding:6px;border-radius:4px;border:0.5px solid var(--border);background:var(--bg-secondary);color:var(--text-primary);font-size:12px;">' +
      '<select onchange="window._updateCreateField(' + idx + ', \'type\', this.value);window._toggleCreateOptions(' + idx + ')" style="width:80px;padding:6px;border-radius:4px;border:0.5px solid var(--border);background:var(--bg-secondary);color:var(--text-primary);font-size:12px;">' +
        '<option value="text">文本</option><option value="number">数字</option><option value="date">日期</option><option value="select">下拉</option><option value="textarea">多行</option></select>' +
      '<button onclick="if(window.removeCreateField)removeCreateField(' + idx + ')" style="background:none;border:none;color:#e05050;cursor:pointer;font-size:16px;flex-shrink:0;">×</button>' +
      '</div>' +
      '<div id="createFieldOptions' + idx + '" style="display:none;margin-top:4px;">' +
        '<input type="text" placeholder="选项（逗号分隔，如：餐饮,交通,购物）" onchange="window._updateCreateField(' + idx + ', \'options\', this.value)" style="width:100%;padding:6px;border-radius:4px;border:0.5px solid var(--border);background:var(--bg-secondary);color:var(--text-primary);font-size:12px;">' +
      '</div>';
    list.appendChild(div);
  }

  function removeCreateField(idx) {
    var row = document.getElementById('createFieldRow' + idx);
    if (row) row.remove();
    createFields[idx] = null;
  }

  function _updateCreateField(idx, key, value) {
    if (createFields[idx]) createFields[idx][key] = value;
  }

  function _toggleCreateOptions(idx) {
    var optsDiv = document.getElementById('createFieldOptions' + idx);
    var field = createFields[idx];
    if (optsDiv && field) {
      optsDiv.style.display = (field.type === 'select') ? 'block' : 'none';
    }
  }

  function doCreatePanel() {
    var name = document.getElementById('createPanelName').value.trim();
    var panelId = document.getElementById('createPanelId').value.trim();
    var desc = document.getElementById('createPanelDesc').value.trim();
    var feedback = document.getElementById('createFeedback');
    var icon = document.getElementById('createPanelIcon') ? document.getElementById('createPanelIcon').value : '💰';
    var withChart = document.getElementById('createPanelChart') ? document.getElementById('createPanelChart').checked : true;
    var withSearch = document.getElementById('createPanelSearch') ? document.getElementById('createPanelSearch').checked : true;
    var fields = createFields.filter(function(f) { return f && f.name && f.label; });
    // 解析select类型字段的options
    fields.forEach(function(f) {
      if (f.type === 'select' && f.options) {
        f.options = f.options.split(',').map(function(s) { return s.trim(); }).filter(function(s) { return s; });
      } else {
        delete f.options;
      }
    });

    if (!name) { if (feedback) feedback.innerHTML = '<span style="color:var(--text-danger);">请输入面板名称</span>'; return; }
    if (!panelId) { if (feedback) feedback.innerHTML = '<span style="color:var(--text-danger);">请输入面板ID</span>'; return; }
    if (!fields.length) { if (feedback) feedback.innerHTML = '<span style="color:var(--text-danger);">请至少添加一个字段</span>'; return; }

    if (feedback) feedback.innerHTML = '<span style="color:var(--accent);">创建中...</span>';

    fetch('/api/panels/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, id: panelId, fields: fields, description: desc, icon: icon, with_chart: withChart, with_search: withSearch })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.success) {
        if (feedback) feedback.innerHTML = '<span style="color:var(--text-success);">' + data.message + '</span>';
        createFields = [];
        document.getElementById('createFieldsList').innerHTML = '';
        document.getElementById('createPanelName').value = '';
        document.getElementById('createPanelId').value = '';
        document.getElementById('createPanelDesc').value = '';
        loadPanelList();
      } else {
        if (feedback) feedback.innerHTML = '<span style="color:var(--text-danger);">' + (data.message || '创建失败') + '</span>';
      }
    })
    .catch(function(e) {
      if (feedback) feedback.innerHTML = '<span style="color:var(--text-danger);">网络错误</span>';
    });
  }

  // ★ 2026-08-08: 模板选择
  function applyPanelTemplate(template) {
    var templates = {
      'accounting': {
        name: '家庭记账', id: 'my-accounting', desc: '管理家庭收入与支出',
        icon: '💰', chart: true, search: true,
        fields: [
          {name:'amount',label:'金额',type:'number'},
          {name:'category',label:'分类',type:'select',options:'餐饮,交通,购物,住房,娱乐,医疗,教育,其他'},
          {name:'type',label:'类型',type:'select',options:'支出,收入'},
          {name:'date',label:'日期',type:'date'},
          {name:'note',label:'备注',type:'text'}
        ]
      },
      'fitness': {
        name: '健身记录', id: 'my-fitness', desc: '运动数据追踪',
        icon: '💪', chart: true, search: true,
        fields: [
          {name:'date',label:'日期',type:'date'},
          {name:'exercise',label:'运动项目',type:'select',options:'跑步,游泳,健身,瑜伽,骑行,其他'},
          {name:'duration',label:'时长(分钟)',type:'number'},
          {name:'calories',label:'消耗(卡)',type:'number'},
          {name:'note',label:'备注',type:'text'}
        ]
      },
      'todo': {
        name: '待办事项', id: 'my-todo', desc: '任务管理',
        icon: '✅', chart: false, search: true,
        fields: [
          {name:'task',label:'任务',type:'text'},
          {name:'priority',label:'优先级',type:'select',options:'高,中,低'},
          {name:'status',label:'状态',type:'select',options:'待办,进行中,完成'},
          {name:'deadline',label:'截止日期',type:'date'}
        ]
      },
      'inventory': {
        name: '物品库存', id: 'my-inventory', desc: '物品管理',
        icon: '📦', chart: false, search: true,
        fields: [
          {name:'name',label:'物品名称',type:'text'},
          {name:'quantity',label:'数量',type:'number'},
          {name:'location',label:'存放位置',type:'text'},
          {name:'category',label:'分类',type:'select',options:'食品,日用,电子,工具,其他'},
          {name:'expire',label:'过期日期',type:'date'}
        ]
      }
    };

    if (template === 'blank' || !templates[template]) {
      document.getElementById('createPanelName').value = '';
      document.getElementById('createPanelId').value = '';
      document.getElementById('createPanelDesc').value = '';
      createFields = [];
      document.getElementById('createFieldsList').innerHTML = '';
      if (document.getElementById('createPanelChart')) document.getElementById('createPanelChart').checked = true;
      if (document.getElementById('createPanelSearch')) document.getElementById('createPanelSearch').checked = true;
      return;
    }

    var t = templates[template];
    document.getElementById('createPanelName').value = t.name;
    document.getElementById('createPanelId').value = t.id;
    document.getElementById('createPanelDesc').value = t.desc;
    if (document.getElementById('createPanelIcon')) document.getElementById('createPanelIcon').value = t.icon;
    if (document.getElementById('createPanelChart')) document.getElementById('createPanelChart').checked = t.chart;
    if (document.getElementById('createPanelSearch')) document.getElementById('createPanelSearch').checked = t.search;

    // 设置图标选中
    document.querySelectorAll('.icon-option').forEach(function(el) {
      el.style.borderColor = el.textContent === t.icon ? 'var(--accent)' : 'var(--border)';
    });

    // 填充字段
    createFields = JSON.parse(JSON.stringify(t.fields));
    renderCreateFields();
  }

  // ★ 2026-08-08: 图标选择
  function selectIcon(el, icon) {
    document.querySelectorAll('.icon-option').forEach(function(e) { e.style.borderColor = 'var(--border)'; });
    el.style.borderColor = 'var(--accent)';
    document.getElementById('createPanelIcon').value = icon;
  }

  // ★ 2026-08-08: 预览
  function previewPanel() {
    var name = document.getElementById('createPanelName').value.trim() || '面板预览';
    var icon = document.getElementById('createPanelIcon') ? document.getElementById('createPanelIcon').value : '📋';
    var withChart = document.getElementById('createPanelChart') ? document.getElementById('createPanelChart').checked : false;
    var fields = createFields.filter(function(f) { return f && f.name && f.label; });
    var previewArea = document.getElementById('previewArea');
    if (!previewArea) return;

    var html = '<div style="font-size:14px;font-weight:bold;margin-bottom:8px">' + icon + ' ' + name + ' <span style="font-size:11px;color:var(--text-tertiary)">（预览）</span></div>';
    
    if (withChart) {
      html += '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px">';
      html += '<div style="background:rgba(255,255,255,0.03);padding:8px;border-radius:6px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">总计</div><div style="font-size:16px;color:#3fb950">¥0</div></div>';
      html += '<div style="background:rgba(255,255,255,0.03);padding:8px;border-radius:6px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">分类</div><div style="font-size:16px;color:#58a6ff">0</div></div>';
      html += '<div style="background:rgba(255,255,255,0.03);padding:8px;border-radius:6px;text-align:center"><div style="font-size:10px;color:var(--text-tertiary)">记录数</div><div style="font-size:16px;color:#f85149">0</div></div>';
      html += '</div>';
    }

    html += '<table style="width:100%;font-size:11px;border-collapse:collapse">';
    html += '<tr>';
    fields.forEach(function(f) { html += '<th style="text-align:left;padding:4px;border-bottom:1px solid var(--border);color:var(--text-tertiary)">' + f.label + '</th>'; });
    html += '<th style="width:60px"></th></tr>';
    html += '<tr><td colspan="' + (fields.length+1) + '" style="padding:12px;text-align:center;color:var(--text-tertiary)">暂无数据</td></tr>';
    html += '</table>';

    previewArea.innerHTML = html;
    previewArea.style.display = 'block';
  }

  // ═══════════════ 面板查看器 ═══════════════

  function openPanel(panelId) {
    var viewer = document.getElementById('panelViewer');
    var chatWrapper = document.getElementById('chatWrapper');
    var frame = document.getElementById('panelFrame');
    var title = document.getElementById('panelViewerTitle');
    if (!viewer || !frame) return;

    currentPanelId = panelId;
    window._currentPanelId = panelId;

    // 高亮侧边栏
    var items = document.querySelectorAll('.panel-sidebar-item');
    items.forEach(function(item) { item.style.background = 'transparent'; });
    // 简单匹配
    items.forEach(function(item) {
      if (item.getAttribute('onclick') && item.getAttribute('onclick').indexOf("'" + panelId + "'") >= 0) {
        item.style.background = 'var(--bg-hover)';
      }
    });

    // 找面板名
    var panel = panels.find(function(p) { return p.id === panelId; });
    if (title) title.textContent = panel ? panel.name : panelId;

    frame.src = '/panel/' + panelId;
    viewer.style.display = 'flex';
    if (chatWrapper) chatWrapper.style.display = 'none';
  }

  function closePanel() {
    var viewer = document.getElementById('panelViewer');
    var chatWrapper = document.getElementById('chatWrapper');
    var frame = document.getElementById('panelFrame');
    if (viewer) viewer.style.display = 'none';
    if (chatWrapper) chatWrapper.style.display = 'flex';
    if (frame) frame.src = '';
    currentPanelId = null;
    window._currentPanelId = null;
    loadPanelList(); // 刷新侧边栏高亮
  }

  function reloadPanel() {
    var frame = document.getElementById('panelFrame');
    if (frame && currentPanelId) frame.src = '/panel/' + currentPanelId;
  }

  // ═══════════════ postMessage 处理 ═══════════════

  window.addEventListener('message', function(event) {
    var data = event.data;
    if (!data || data.type !== 'panel-api-request') return;

    var panelId = data.panelId;
    var action = data.action;
    var params = data.params || {};
    var requestId = data.requestId;

    var url = '';
    var method = 'GET';
    var body = null;

    switch (action) {
      case 'query':
        url = '/api/panels/' + panelId + '/data';
        if (params.filter) {
          var qs = Object.keys(params.filter).map(function(k) {
            return encodeURIComponent(k) + '=' + encodeURIComponent(params.filter[k]);
          }).join('&');
          if (qs) url += '?' + qs;
        }
        method = 'GET';
        break;
      case 'save':
        url = '/api/panels/' + panelId + '/data';
        method = 'POST';
        body = JSON.stringify(params.data || {});
        break;
      case 'update':
        url = '/api/panels/' + panelId + '/data/' + params.rowId;
        method = 'PUT';
        body = JSON.stringify(params.data || {});
        break;
      case 'remove':
        url = '/api/panels/' + panelId + '/data/' + params.rowId;
        method = 'DELETE';
        break;
      case 'info':
        url = '/api/panels/' + panelId;
        method = 'GET';
        break;
      default:
        sendResponse(requestId, false, '未知操作: ' + action, null);
        return;
    }

    var fetchOpts = { method: method, headers: {} };
    if (body) { fetchOpts.headers['Content-Type'] = 'application/json'; fetchOpts.body = body; }

    fetch(url, fetchOpts)
      .then(function(r) { return r.json(); })
      .then(function(result) {
        if (action === 'query') {
          sendResponse(requestId, true, '', result.records || []);
        } else if (action === 'info') {
          sendResponse(requestId, true, '', result.panel || {});
        } else {
          sendResponse(requestId, result.success, result.message || '', result.record || null);
        }
      })
      .catch(function(e) {
        sendResponse(requestId, false, e.message, null);
      });
  });

  function sendResponse(requestId, success, message, data) {
    var frame = document.getElementById('panelFrame');
    if (frame && frame.contentWindow) {
      frame.contentWindow.postMessage({
        type: 'panel-api-response',
        requestId: requestId,
        success: success,
        message: message,
        data: data
      }, '*');
    }
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ═══════════════ 导出到 window ═══════════════

  window.loadPanelList = loadPanelList;
  window.openPanel = openPanel;
  window.closePanel = closePanel;
  window.reloadPanel = reloadPanel;
  window.openPanelManager = openPanelManager;
  window.closePanelManager = closePanelManager;
  window.switchPanelTab = switchPanelTab;
  window.installPanelFromFile = installPanelFromFile;
  window.uninstallPanel = uninstallPanel;
  window.addCreateField = addCreateField;
  window.removeCreateField = removeCreateField;
  window._updateCreateField = _updateCreateField;
  window._toggleCreateOptions = _toggleCreateOptions;
  window.doCreatePanel = doCreatePanel;

  // ═══════════════ 初始化 ═══════════════

  console.log('[Panels] 面板模块已加载');
  setTimeout(loadPanelList, 500);

  return { loadPanelList: loadPanelList, panels: panels };
})();
