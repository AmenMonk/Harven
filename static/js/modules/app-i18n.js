/**
 * Harven AI 前端国际化引擎 (i18n)
 * ★ 2026-08-16: 中英双语支持
 * 用法：
 *   HTML:  <span data-i18n="web.new_chat">新对话</span>
 *          <input data-i18n-ph="web.input_placeholder">
 *          <button data-i18n-title="web.help_title">
 *   JS:    HarvenI18n.t('web.new_chat')
 *          HarvenI18n.setLang('en_US')
 * 语言优先级：localStorage(harven_ui_lang) > config(language.ui_lang) > 浏览器语言
 */
(function() {
    'use strict';

    var LS_KEY = 'harven_ui_lang';
    var LS_DICT_PREFIX = 'harven_ui_lang_dict_';
    var cache = {};           // { lang: {key: text} }
    var currentLang = 'zh_CN';
    var dict = {};            // 当前语言字典
    var available = { zh_CN: '简体中文', en_US: 'English' };

    // ── 工具 ──
    function resolveFollow() {
        // 跟随浏览器/系统语言
        var nav = (navigator.language || navigator.userLanguage || 'zh-CN').toLowerCase();
        if (nav.indexOf('zh') === 0) return 'zh_CN';
        return 'en_US';
    }

    function detectInitial() {
        try {
            var saved = localStorage.getItem(LS_KEY);
            if (saved === 'follow' || saved === 'zh_CN' || saved === 'en_US') {
                return saved === 'follow' ? resolveFollow() : saved;
            }
        } catch (e) {}
        return resolveFollow();
    }

    function fetchDict(lang) {
        if (cache[lang]) return Promise.resolve(cache[lang]);
        return fetch('/api/locales/' + lang, { cache: 'no-store' })
            .then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function(j) { cache[lang] = j || {}; return cache[lang]; })
            .catch(function(e) {
                console.warn('[i18n] 加载语言包失败:', lang, e);
                return {};
            });
    }

    // ── 翻译函数 ──
    function t(key, params) {
        var text = dict[key];
        if (text === undefined && currentLang !== 'zh_CN') text = (cache['zh_CN'] || {})[key];
        if (text === undefined) return key;
        if (params) {
            Object.keys(params).forEach(function(k) {
                text = text.split('{' + k + '}').join(String(params[k]));
            });
        }
        return text;
    }

    // ── DOM 应用 ──
    function apply(root) {
        root = root || document;
        root.querySelectorAll('[data-i18n]').forEach(function(el) {
            var v = t(el.getAttribute('data-i18n'));
            if (v) el.textContent = v;
        });
        root.querySelectorAll('[data-i18n-ph]').forEach(function(el) {
            var v = t(el.getAttribute('data-i18n-ph'));
            if (v) el.setAttribute('placeholder', v);
        });
        root.querySelectorAll('[data-i18n-title]').forEach(function(el) {
            var v = t(el.getAttribute('data-i18n-title'));
            if (v) el.setAttribute('title', v);
        });
    }

    // ── 切换语言 ──
    function setLang(lang, persist) {
        if (lang === 'follow') lang = resolveFollow();
        if (!available[lang]) lang = 'zh_CN';
        currentLang = lang;
        document.documentElement.setAttribute('lang', lang === 'en_US' ? 'en' : 'zh-CN');
        return fetchDict(lang).then(function(d) {
            dict = d || {};
            apply(document);
            // 通知其他模块
            document.dispatchEvent(new CustomEvent('harven:langchange', { detail: { lang: lang } }));
            if (persist !== false) {
                try { localStorage.setItem(LS_KEY, lang); } catch (e) {}
                // 同步到后端 config（失败静默）
                fetch('/api/settings/language', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ui_lang: lang })
                }).catch(function() {});
            }
        });
    }

    // ── 快捷循环切换：中 ↔ EN ──
    function cycleLang() {
        var next = currentLang === 'zh_CN' ? 'en_US' : 'zh_CN';
        setLang(next).then(function() {
            if (typeof toast === 'function') toast(next === 'zh_CN' ? '🌐 界面语言：中文' : '🌐 UI Language: English');
        });
    }

    // ── 初始化 ──
    function init() {
        currentLang = detectInitial();
        document.documentElement.setAttribute('lang', currentLang === 'en_US' ? 'en' : 'zh-CN');
        // 先用 localStorage 缓存同步应用一遍（防闪烁），再 fetch 权威字典
        var saved = null;
        try { saved = localStorage.getItem(LS_DICT_PREFIX + currentLang); } catch (e) {}
        if (saved) { try { dict = JSON.parse(saved) || {}; } catch (e) {} }
        fetchDict(currentLang).then(function(d) {
            dict = d || {};
            try { localStorage.setItem(LS_DICT_PREFIX + currentLang, JSON.stringify(dict)); } catch (e) {}
            apply(document);
        });
        // DOM 就绪后应用（含 Jinja include 的 settings tab）
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function() { apply(document); });
        } else {
            apply(document);
        }
    }

    // ── 暴露全局 ──
    window.HarvenI18n = {
        t: t,
        apply: apply,
        setLang: setLang,
        cycleLang: cycleLang,
        init: init,
        getLang: function() { return currentLang; },
        available: available
    };

    init();
})();
