# -*- coding: utf-8 -*-
"""
Token 账本（A批A7 · 2026-09-13）
═══════════════════════════════════════════════════════════════
记录每一轮的 token 经济数据，供运行状态页（B9）展示：
  - B 方案（关键词快车道）命中次数 —— 零 token
  - FC 轮次 / 注入工具数 / 目录体积 —— 小 token
  - 工具结果瘦身节省字符 —— 历史瘦身

只记内存（进程生命周期），不写库；重启清零是有意的（看"当天/本次运行"）。
"""
import threading
import time
from collections import deque
from datetime import datetime

_lock = threading.Lock()
_events = deque(maxlen=500)   # 最近500条事件
_counters = {}                # 累计计数器

_COUNTER_KEYS = {
    'b_plan_hits',        # B方案命中（0 token）
    'fc_turns',           # FC 轮次（每轮对话）
    'fc_rounds',          # FC 工具循环轮数
    'tool_result_slimmed',          # 瘦身次数
    'tool_result_chars_saved',      # 瘦身节省字符总数
    'catalog_chars_last',           # 最近一次目录体积（字符）
}


def record(event_type, **data):
    """记录一条事件（任何异常都不外抛，账本失败不影响主流程）"""
    try:
        with _lock:
            _counters[event_type] = _counters.get(event_type, 0) + 1
            _events.append({
                'ts': datetime.now().strftime('%H:%M:%S'),
                'type': event_type,
                **data,
            })
            # 字符节省类事件额外累计
            if event_type == 'tool_result_slimmed' and 'saved_chars' in data:
                _counters['tool_result_chars_saved'] = \
                    _counters.get('tool_result_chars_saved', 0) + data['saved_chars']
    except Exception:
        pass


def summary():
    """汇总给运行状态页"""
    with _lock:
        counters = {k: _counters.get(k, 0) for k in _COUNTER_KEYS}
        recent = list(_events)[-100:]
    return {
        'counters': counters,
        'recent': recent,
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


# ══════════════════════════════════════════════════════════════
# Flask 端点（GET /api/token/ledger）
# ══════════════════════════════════════════════════════════════
try:
    from flask import Blueprint, jsonify
    bp = Blueprint('token_ledger', __name__)

    @bp.route('/api/token/ledger', methods=['GET'])
    def token_ledger_api():
        return jsonify(summary())
except Exception:  # 脱离 Flask 环境导入本模块也不报错
    bp = None
