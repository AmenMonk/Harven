/* ★ 2026-09-18 SPA 面板桥（重大修复）
   iframe 面板里的 HarvenPanel API（panel-api.js）通过 postMessage 把请求发给父页，
   由父页转发到后端 HTTP 接口。旧版 UI 的桥在 static/js/modules/app-panels.js 里，
   SPA 迁移时漏掉了 → 新版里所有面板数据读写全部 15 秒超时，面板一片空白。
   本模块补上这条桥：监听 panel-api-request → fetch 后端 → event.source 回发 panel-api-response。 */
;(function () {
  'use strict'
  if (window.__harvenPanelBridge) return
  window.__harvenPanelBridge = true

  function sendResponse(source, requestId, success, message, data) {
    if (!source) return
    try {
      source.postMessage({ type: 'panel-api-response', requestId, success, message, data }, '*')
    } catch (e) { /* 静默 */ }
  }

  window.addEventListener('message', (event) => {
    const d = event.data
    if (!d || d.type !== 'panel-api-request') return
    const requestId = d.requestId
    const action = d.action
    const panelId = d.panelId
    const params = d.params || {}

    let url = ''
    let method = 'GET'
    let body = null

    switch (action) {
      case 'query': {
        url = '/api/panels/' + panelId + '/data'
        if (params.filter) {
          const qs = Object.keys(params.filter)
            .map(k => encodeURIComponent(k) + '=' + encodeURIComponent(params.filter[k]))
            .join('&')
          if (qs) url += '?' + qs
        }
        break
      }
      case 'save':
        url = '/api/panels/' + panelId + '/data'
        method = 'POST'
        body = JSON.stringify(params.data || {})
        break
      case 'update':
        url = '/api/panels/' + panelId + '/data/' + params.rowId
        method = 'PUT'
        body = JSON.stringify(params.data || {})
        break
      case 'remove':
        url = '/api/panels/' + panelId + '/data/' + params.rowId
        method = 'DELETE'
        break
      case 'info':
        url = '/api/panels/' + panelId
        break
      default:
        sendResponse(event.source, requestId, false, '未知操作: ' + action, null)
        return
    }

    const opts = { method, headers: {} }
    if (body) {
      opts.headers['Content-Type'] = 'application/json'
      opts.body = body
    }

    fetch(url, opts)
      .then(r => r.json())
      .then(result => {
        if (action === 'query') sendResponse(event.source, requestId, true, '', result.records || [])
        else if (action === 'info') sendResponse(event.source, requestId, true, '', result.panel || {})
        else sendResponse(event.source, requestId, !!result.success, result.message || '', result.record || null)
      })
      .catch(e => sendResponse(event.source, requestId, false, e.message, null))
  })
})()
