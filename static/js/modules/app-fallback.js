// app-fallback.js — 保底机制前端接口
// 2026-07-03: fallback_routes.py 已归档，此模块保留兼容。
// 检测到 404 时静默禁用，不再轮询，避免控制台报错。
window.HarvenFallback = (function() {
    'use strict';
    var API = '/api/fallback';
    var _disabled = false;  // 一旦 404 就永久禁用

    async function fetchAPI(path, opts) {
        if (_disabled) return {};
        try {
            var r = await fetch(API + path, opts);
            if (!r.ok) {
                // fallback 后端不存在（已归档）→ 静默禁用
                _disabled = true;
                return {};
            }
            return r.json();
        } catch (e) {
            _disabled = true;
            return {};
        }
    }

    function renderHealth(items) {
        var el = document.getElementById('fallbackHealthList');
        if (!el || !items) return;
        el.innerHTML = items.map(function(h) {
            var icon = h.ok ? '✅' : '❌';
            return '<div style="font-size:13px;padding:4px 0;">' + icon + ' ' + h.name + ': ' + (h.ok ? h.info || '正常' : h.error || '异常') + '</div>';
        }).join('');
    }

    function renderStatus(data) {
        var el = document.getElementById('fallbackModeStatus');
        var btn = document.getElementById('fallbackToggleBtn');
        if (!el) return;
        var active = data.active || data.is_active;
        el.style.background = active ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.1)';
        el.style.color = active ? '#ef4444' : '#22c55e';
        el.textContent = active ? '⚠️ 已进入保底模式 — 核心服务不可用，使用备用策略' : '✅ 正常运行 — 所有服务可用';
        if (btn) btn.textContent = active ? '退出保底模式' : '进入保底模式';
    }

    return {
        _isDisabled: function() { return _disabled; },
        checkHealth: async function() {
            var data = await fetchAPI('/health');
            if (!_disabled) renderHealth(data.items || data.health);
            return data;
        },
        autoCheck: async function() {
            var data = await fetchAPI('/auto-check', { method: 'POST' });
            if (!_disabled) {
                renderHealth(data.items || data.health);
                if (data.status) renderStatus(data.status);
            }
            return data;
        },
        tryFix: async function() {
            var data = await fetchAPI('/try-fix', { method: 'POST' });
            return data;
        },
        toggleMode: async function() {
            var current = await fetchAPI('/status');
            if (_disabled) return current;
            var active = current.active || current.is_active;
            var path = active ? '/exit' : '/enter';
            var data = await fetchAPI(path, { method: 'POST' });
            renderStatus(data);
            return data;
        },
        refresh: async function() {
            var data = await fetchAPI('/status');
            if (!_disabled) renderStatus(data);
            return data;
        }
    };
})();

// Auto-refresh on page load（fallback 已归档时静默跳过）
document.addEventListener('DOMContentLoaded', function() {
    if (window.HarvenFallback) {
        setTimeout(function() {
            if (!window.HarvenFallback._isDisabled()) {
                HarvenFallback.checkHealth();
                HarvenFallback.refresh();
            }
        }, 1000);
    }
});
