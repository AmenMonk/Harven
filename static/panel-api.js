/**
 * HarvenPanel API - 面板内通过postMessage调用哈文后端
 * 在iframe沙箱中的面板使用此API进行数据读写
 */
(function() {
  'use strict';

  var _requestId = 0;
  var _pending = {};

  // 监听来自主窗口的响应
  window.addEventListener('message', function(event) {
    var data = event.data;
    if (data && data.type === 'panel-api-response') {
      var resolver = _pending[data.requestId];
      if (resolver) {
        delete _pending[data.requestId];
        if (data.success) {
          resolver.resolve(data.data);
        } else {
          resolver.reject(new Error(data.message || '未知错误'));
        }
      }
    }
  });

  function _sendRequest(action, panelId, params) {
    return new Promise(function(resolve, reject) {
      var reqId = ++_requestId;
      _pending[reqId] = { resolve: resolve, reject: reject };
      window.parent.postMessage({
        type: 'panel-api-request',
        requestId: reqId,
        action: action,
        panelId: panelId,
        params: params || {}
      }, '*');

      // 超时处理
      setTimeout(function() {
        if (_pending[reqId]) {
          delete _pending[reqId];
          reject(new Error('请求超时'));
        }
      }, 15000);
    });
  }

  window.HarvenPanel = {
    /** 查询面板数据 */
    query: function(panelId, filter) {
      return _sendRequest('query', panelId, { filter: filter || {} });
    },

    /** 新增数据 */
    save: function(panelId, data) {
      return _sendRequest('save', panelId, { data: data });
    },

    /** 更新数据 */
    update: function(panelId, rowId, data) {
      return _sendRequest('update', panelId, { rowId: rowId, data: data });
    },

    /** 删除数据 */
    remove: function(panelId, rowId) {
      return _sendRequest('remove', panelId, { rowId: rowId });
    },

    /** 获取面板信息 */
    getInfo: function(panelId) {
      return _sendRequest('info', panelId, {});
    }
  };
})();
