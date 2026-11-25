# -*- coding: utf-8 -*-
# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
哈文云端通道 + 设备配对（#803 / #804，2026-09-20）

原理：
  家里哈文一般没有公网 IP，手机在外面连不进来。
  → 哈文【主动外连】HarvenHub 的 /ws/harven WebSocket 长连接（挂着不断线）。
  → 手机把指令发给 Hub（/api/relay/send，带设备令牌），
    Hub 顺着这条长连接把指令推下来，哈文执行后把结果原路回传。

安全设计（城墙）：
  1. 全程 TLS（https→wss）。Hub 在 nginx 后面终止 TLS。
  2. 哈文身份 = harven_token（本机随机生成，只存本地 data/cloud_link.json）。
  3. 手机身份 = device_token，只有走「配对码」流程才能拿到——
     配对码由哈文本机生成（6 位、5 分钟有效、一次性），经长连接报给 Hub。
     没有哈文本机点头，任何人配不出手机令牌。
  4. 指令白名单：ping / panels.list / panels.query / kb.search / chat /
     voice（手机录音→本地转文字→回答）/ photo（手机拍照→本地看图→回答）。
     白名单外的 action 一律拒绝。
  5. 管理员密码 / API Key / 对话原文等敏感数据永不出本地；
     面板数据一次最多 50 行；chat 回答最多 2000 字。
  6. 手机令牌可随时在哈文侧吊销（解绑），离职/换机场景干净收口。

断线重连：指数退避 1s → 2s → 4s → ... 封顶 60s；恢复后自动重连并重新鉴权。
配置：config.yaml → cloud_link: {enabled: false, server_url: 'https://harvenhub.com'}
"""

import os
import re
import json
import time
import random
import secrets
from pathlib import Path
import asyncio
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# ══════════════════════════ 常量 ══════════════════════════

STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          '..', 'data', 'cloud_link.json')
STATE_PATH = os.path.normpath(STATE_PATH)

PAIR_CODE_TTL = 300          # 配对码 5 分钟有效
MAX_ROWS = 50                # 面板数据单次上限
MAX_CHAT_CHARS = 2000        # chat 回答截断
MAX_KB_RESULTS = 5
RECONNECT_MAX = 60           # 重连退避封顶（秒）

# 指令白名单
ALLOWED_ACTIONS = {'ping', 'panels.list', 'panels.query', 'panels.fields', 'panels.add',
                   'fridge.view',   # ★ #1034（2026-10-06）冰箱手机只读四 Tab：库存/临期/食谱/买菜，全部只读
                   'kb.search', 'chat',
                   'chat_stop',   # ★ #1054（2026-10-10）手机页停止按钮：停止正在生成的回答（半截入库标「（已停止）」）
                   'voice', 'voice_text', 'photo',   # #815: 手机语音/拍照（识别在家完成）；voice_text 系 #929 补漏——手机页发的就是它，漏白名单会被 403
                   'images.list', 'images.file', 'images.upload',   # ★ #927: 手机远程看图库（出门模式跳转按钮配套）；#928 T6: 手机相册导入
                   'images.replace_original',   # ★ 照片链 C2（2026-10-11）手机原图补传：回家 WiFi 后用原图升级已存压缩版
                   'images.title', 'images.set_title',   # ★ #1156（2026-10-16）照片智能命名：浮条轮询名字/分类 + 确认或改名改分类（分类变更联动重起名）
                   'inbox.list', 'inbox.add', 'inbox.resolve', 'inbox.delete',
                   'inbox.remind',   # ★ v30（2026-10-17）手机页碎片手动挂/取消提醒（自然语言时间）
                   'inbox.to_task',   # ★ v31（2026-10-17）#1159 甲案：碎片一键转正为任务（双轨打通）
                   'photo.combo',   # ★ v31（2026-10-17）#1159 确认卡多图组合：一条记录挂多张照片
                   'tasks.list',   # ★ v31（2026-10-17）#1159 服务抽屉任务直读（不再对话代查）
                   'goals.list',   # ★ 2026-10-21 手机端目标直读（B2：目标中心手机入口配套）
                   'goals.action',   # ★ 2026-10-21 手机端节点操作（完成/顺延），审核推送数据源
                   'family.members',   # ★ 2026-10-21 手机端家人健康直读（家人条摘要）
                   'kb.list',   # ★ v31（2026-10-17）#1159 服务抽屉知识库直读
                   # ★ v23（2026-10-14）随身记收件箱：流量版/直连版手机页九宫格直达；只开 4 个最小动作
                   'reminders.aggregate',   # ★ v27（2026-10-15）#1151 弹层顶部⏰提醒聚合区（随身记挂的+全家庭到期前的）
                   'butler.state', 'butler.action',   # ★ #1191（2026-10-25）手机管家六区：今日议程直读/条目操作（云端手机页 relay）
                   'lingzhu_chat',   # ★ #1400（2026-11-02）灵珠眼镜通道：Rokid Glasses → 灵珠平台 → Hub → 本机（外脑/看图全管线）
                   'assist.pending', 'assist.confirm', 'assist.ignore',   # ★ #1191 待确认收纳盒（云端手机页与桌面管家中心同一套数据）
                   'fridge.add', 'fridge.undo',   # ★ v28（2026-10-16）#1154 手机直录框：一句话直写冰箱库（L1 同一解析管线）+撤销
                   'pair.verify'}   # ★ v10（2026-10-04）配对保护 B：手机兑换配对码前，Hub 代为验管理员密码

# ★ v10 配对保护：每个配对码的密码验证失败计数（code -> 次数），连错 3 次销毁码防爆破
_pair_verify_fails = {}

MAX_AUDIO_B64 = 4_000_000     # 手机录音 base64 上限（≈60 秒 opus，实际远小于此）
MAX_PHOTO_B64 = 12_000_000    # 手机照片 base64 上限（≈9MB 原图）


def _pic_b64(path, max_side=400, quality=55):
    """★ #927 图库图片压缩成 base64（手机中转用）。失败返回 ''，绝不抛异常。"""
    try:
        import base64 as _b64
        import io as _io
        from PIL import Image as _Image
        im = _Image.open(path)
        im = im.convert('RGB')
        w, h = im.size
        if max(w, h) > max_side:
            sc = max_side / float(max(w, h))
            im = im.resize((int(w * sc), int(h * sc)), _Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, 'JPEG', quality=quality)
        return _b64.b64encode(buf.getvalue()).decode('ascii')
    except Exception as e:
        logger.debug(f'[CloudLink] 图片压缩失败 {path}: {e}')
        return ''

# ══════════════════════════ 本地状态（凭证 + 设备表 + 配对码） ══════════════════════════

_lock = threading.Lock()


def _load_state() -> dict:
    try:
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _get_harven_token() -> str:
    """取（或首次生成）哈文身份令牌。"""
    with _lock:
        st = _load_state()
        tok = st.get('harven_token')
        if not tok:
            tok = secrets.token_urlsafe(32)
            st['harven_token'] = tok
            st.setdefault('devices', [])
            _save_state(st)
        return tok


def get_harven_name() -> str:
    """哈文在 Hub 上显示的名字（默认取主机名，可在 json 里改）。"""
    st = _load_state()
    if st.get('harven_name'):
        return st['harven_name']
    import socket
    return socket.gethostname() or '我家的哈文'


# ══════════════════════════ 运行状态（供设置页展示） ══════════════════════════

_runtime = {
    'connected': False,
    'last_error': '',
    'last_connected_at': '',
    'retries': 0,
    'started_at': '',
}


def get_status() -> dict:
    """设置页展示用：通道是否开启/连接、绑定设备、待配对码剩余秒数。"""
    from src.utils.config import get_config
    cfg = get_config()
    enabled = bool(cfg.get('cloud_link.enabled', False))
    st = _load_state()
    pair_left = 0
    try:
        pc = _pair_code_get()
        if pc:
            pair_left = max(0, PAIR_CODE_TTL - int(time.time() - pc['ts']))
            if pair_left == 0:
                _pair_code_clear()
    except Exception:
        pair_left = 0
    return {
        'enabled': enabled,
        'connected': _runtime['connected'],
        'server_url': cfg.get('cloud_link.server_url', 'https://harvenhub.com'),
        'harven_name': get_harven_name(),
        'last_error': _runtime['last_error'],
        'last_connected_at': _runtime['last_connected_at'],
        'retries': _runtime['retries'],
        'devices': st.get('devices', []),
        'pair_code_left': pair_left,
        # ★ #947：Hub 运营公告（最近一条，7 天内的才显示）
        'broadcast': _latest_broadcast(),
    }


def _latest_broadcast() -> dict:
    try:
        bpath = os.path.join(os.path.dirname(STATE_PATH), 'relay_broadcast.json')
        with open(bpath, 'r', encoding='utf-8') as f:
            b = json.load(f) or {}
        ts = str(b.get('ts', ''))
        from datetime import timedelta as _td
        if ts and ts[:10] >= (datetime.now() - _td(days=7)).strftime('%Y-%m-%d'):
            return b
    except Exception:
        pass
    return {}


# ══════════════════════════ 配对码（#804 本地侧） ══════════════════════════

_pair_code = None  # {'code': '123456', 'ts': float}


def _pair_code_get():
    return _pair_code


def _pair_code_clear():
    global _pair_code
    _pair_code = None


def pair_start() -> dict:
    """生成 6 位配对码（5 分钟有效、一次性），并尽力经长连接报给 Hub。"""
    global _pair_code
    with _lock:
        code = f"{random.randint(0, 999999):06d}"
        _pair_code = {'code': code, 'ts': time.time()}
    # 若已连上 Hub，立刻广播配对码（Hub 存内存等手机来兑）
    _announce_pair_code(code)
    return {'success': True, 'pair_code': code, 'ttl': PAIR_CODE_TTL,
            'harven_name': get_harven_name()}


def _announce_pair_code(code: str):
    """经当前连接把配对码报给 Hub（未连接时静默失败——配对码只在连上时有用）。"""
    ws = _current_ws()
    if ws is None:
        return False
    try:
        fut = asyncio.run_coroutine_threadsafe(
            ws.send(json.dumps({'type': 'pair_pending', 'code': code,
                                'harven_name': get_harven_name()})),
            _loop)
        fut.result(timeout=5)
        return True
    except Exception as e:
        logger.debug(f"[CloudLink] 配对码上报失败: {e}")
        return False


def device_unbind(device_id: str) -> dict:
    """吊销手机设备（本地记录删除 + 通知 Hub 拉黑该令牌）。"""
    with _lock:
        st = _load_state()
        before = st.get('devices', [])
        st['devices'] = [d for d in before if d.get('id') != device_id]
        _save_state(st)
    _notify_unbind(device_id)
    removed = len(before) != len(st['devices'])
    return {'success': True, 'removed': removed, 'devices': st['devices']}


def _notify_unbind(device_id: str):
    ws = _current_ws()
    if ws is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            ws.send(json.dumps({'type': 'unbind_device', 'device_id': device_id})),
            _loop).result(timeout=5)
    except Exception as e:
        logger.debug(f"[CloudLink] 解绑通知失败: {e}")


# ══════════════════════════ 指令分发（白名单） ══════════════════════════

def _dispatch_nested_goals(action: str, params: dict) -> dict:
    """★ 2026-10-21 修复：tasks.list/goals.list/goals.action/family.members 四个 handler
    历史性嵌套在 inbox 块内部（12空格缩进）导致不可达（实锤 tasks.list 自 v31 起就是坏的、
    手机一直 404/未知指令）。本函数=这三个目标/家人动作的可达入口，由 _dispatch 开头重定向。"""
    if action == 'tasks.list':
        # ★ v31 服务抽屉任务直读（task_manager → structured_tasks 真家，SPA 同源）
        # ★ 2026-10-21 修复：原 handler 嵌套在 inbox 块内不可达（v31 起实锤坏），此处为可达入口
        try:
            from src.core.task_manager import get_task_manager
            tasks = get_task_manager().get_tasks(limit=30) or []
            out = [{'id': t.get('id'), 'title': t.get('title', ''),
                    'status': t.get('status', 'pending'),
                    'due': t.get('due_date') or '',
                    'created_at': t.get('created_at', '')} for t in tasks]
            return {'tasks': out}
        except Exception as e:
            return {'error': f'任务读不出来: {e}'}
    if action == 'goals.list':
        try:
            from src.core.goal_manager import list_goals, summary_card
            goals = [summary_card(g) for g in list_goals(status='active', limit=30)]
            return {'goals': goals}
        except Exception as e:
            return {'error': f'目标读不出来: {e}'}
    if action == 'goals.action':
        params = params if isinstance(params, dict) else {}
        gid = int(params.get('goal_id', 0) or 0)
        act = params.get('goal_action', '')
        nidx = int(params.get('node_idx', -1) or -1)
        if not gid or not act:
            return {'error': '缺少 goal_id/goal_action'}
        from src.core.goal_manager import (update_goal, set_node_status,
                                           defer_node, lower_target, summary_card)
        if act == 'node_done':
            g = set_node_status(gid, nidx, 'done')
        elif act == 'defer':
            from datetime import datetime as _dt, timedelta as _td
            nd = (_dt.now() + _td(days=7)).strftime('%Y-%m-%d')
            g = defer_node(gid, nidx, params.get('new_due') or nd)
        elif act == 'lower':
            g = lower_target(gid, nidx, params.get('new_target') or 1)
        elif act == 'pause':
            g = update_goal(gid, status='paused')
        else:
            return {'error': f'不支持的目标操作: {act}'}
        if g and g.get('plan') and all(n.get('status') == 'done' for n in g['plan']):
            g = update_goal(gid, status='done')
        return {'ok': True, 'goal': summary_card(g) if g else None}
    if action == 'family.members':
        try:
            from src.core.panel_manager import get_panel_manager
            out = []
            for m in get_panel_manager().get_members():
                out.append({'id': m.get('id'), 'name': m.get('name', ''),
                            'relation': m.get('relation', ''),
                            'birthday': str(m.get('birthday') or '')[:10],
                            'birthday_lunar': m.get('birthday_lunar', ''),
                            'zodiac': m.get('zodiac', ''),
                            'blood_type': m.get('blood_type', ''),
                            'allergy': m.get('allergy', ''),
                            'health_note': m.get('health_note', '')})
            return {'members': out}
        except Exception as e:
            return {'error': f'家人读不出来: {e}'}
    return {'error': '未知指令'}


def _dispatch(action: str, params: dict) -> dict:
    """执行手机指令。只允许白名单内的 action；敏感数据永不出本地。"""
    params = params or {}
    if action not in ALLOWED_ACTIONS:
        return {'error': f'指令 {action} 不在白名单，已拒绝'}
    # ★ 2026-10-21 修复：这四个动作的 handler 历史性嵌套在 inbox 块内不可达 → 重定向到独立入口
    if action in ('tasks.list', 'goals.list', 'goals.action', 'family.members'):
        return _dispatch_nested_goals(action, params)

    try:
        if action == 'ping':
            return {'pong': True, 'time': datetime.now().strftime('%H:%M:%S'),
                    'harven_name': get_harven_name()}

        # ★ #1191（2026-10-25）手机管家六区：云端手机页「今日议程/待确认」页签走 relay，
        #   底层复用 butler_agenda / panel_assist 同一套函数（数据真家唯一，桌面手机同源）
        if action == 'butler.state':
            try:
                from src.core import butler_agenda as _ba
                today = datetime.now().strftime('%Y-%m-%d')
                conn = _ba._conn()
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM butler_agenda WHERE status='open' AND date<=? ORDER BY level DESC, id DESC LIMIT 50",
                    (today,)).fetchall()]
                conn.close()
                groups = {}
                for r in rows:
                    g = groups.setdefault(r['source_panel'] or '_butler', {
                        'panel_id': r['source_panel'] or '_butler',
                        'name': _ba.DECLARATIONS.get(r['source_panel'], {}).get('name', '管家') if r['source_panel'] else '管家',
                        'items': []})
                    g['items'].append({'id': r['id'], 'title': r['title'], 'level': r['level'],
                                       'kind': r['kind'], 'date': r['date'], 'ts': r['ts']})
                return {'success': True, 'quiet': _ba._in_quiet(),
                        'urge_count': sum(1 for r in rows if r['level'] == 'urge'),
                        'total': len(rows), 'groups': list(groups.values())}
            except Exception as e:
                return {'error': f'议程读不出来: {e}'}

        if action == 'butler.action':
            try:
                from datetime import timedelta as _td2
                from src.core import butler_agenda as _ba
                item_id = params.get('id')
                op = str(params.get('op', ''))
                status = {'done': 'done', 'dismiss': 'dismissed', 'snooze': 'open'}.get(op)
                if not item_id or not status:
                    return {'error': '参数缺 id 或 op(done/dismiss/snooze)'}
                conn = _ba._conn()
                if op == 'snooze':
                    tomorrow = (datetime.now() + _td2(days=1)).strftime('%Y-%m-%d')
                    conn.execute('UPDATE butler_agenda SET date=? WHERE id=?', (tomorrow, item_id))
                else:
                    conn.execute("UPDATE butler_agenda SET status=? WHERE id=?", (status, item_id))
                conn.commit()
                conn.close()
                _ba._cache['ts'] = 0          # 立即失效路由版 5 分钟缓存
                return {'success': True}
            except Exception as e:
                return {'error': f'操作没成功: {e}'}

        if action == 'assist.pending':
            try:
                from src.core.panel_assist import list_pending
                return {'items': list_pending()}
            except Exception as e:
                return {'error': f'待确认读不出来: {e}'}

        if action == 'assist.confirm':
            try:
                from src.core.panel_assist import confirm_pending
                ok, msg = confirm_pending(int(params.get('id', 0) or 0))
                return {'success': bool(ok), 'message': msg}
            except Exception as e:
                return {'error': f'入库失败: {e}'}

        if action == 'assist.ignore':
            try:
                from src.core.panel_assist import delete_pending
                delete_pending(int(params.get('id', 0) or 0))
                return {'success': True}
            except Exception as e:
                return {'error': f'操作失败: {e}'}

        if action == 'pair.verify':
            """★ v10（2026-10-04）配对保护 B 方案：手机兑换配对码前，Hub 把 {code, password}
            转来本机验证（管理员密码不出本机，Hub 只当管道）。验三件事：
            ① 码还在本地且没过期 ② 码匹配 ③ 管理员密码正确（R1 后与管理员锁同一把）。
            密码连错 3 次销毁配对码（防爆破）。开关 security.pair_require_password=false 可关
            （兼容不带密码框的旧 APP——关掉后行为与 v9 完全一致）。"""
            from src.core.security_guard import get_admin_password
            code = str(params.get('code', ''))
            password = str(params.get('password', ''))
            pc = _pair_code_get()
            if not pc or time.time() - pc['ts'] > PAIR_CODE_TTL:
                _pair_code_clear()
                _pair_verify_fails.pop(code, None)
                return {'ok': False, 'error': '配对码无效或已过期，请回哈文重新生成'}
            if code != pc['code']:
                return {'ok': False, 'error': '配对码不匹配，请核对后重输'}
            require_pwd = True
            try:
                from src.utils.config import get_config
                require_pwd = bool((get_config().get('security') or {}).get('pair_require_password', True))
            except Exception:
                pass
            if require_pwd:
                fails = _pair_verify_fails.get(code, 0)
                if password != get_admin_password():
                    fails += 1
                    _pair_verify_fails[code] = fails
                    if fails >= 3:
                        _pair_code_clear()
                        _pair_verify_fails.pop(code, None)
                        return {'ok': False, 'error': '密码连错 3 次，配对码已作废，请回哈文重新生成'}
                    return {'ok': False, 'error': f'管理员密码不对（还剩 {3 - fails} 次机会）'}
                _pair_verify_fails.pop(code, None)
            return {'ok': True}

        if action == 'panels.list':
            from src.core.panel_manager import get_panel_manager
            panels = get_panel_manager().get_all_panels()
            counts = {}
            try:
                counts = get_panel_manager().get_record_counts() or {}
            except Exception:
                pass
            items = []
            gm = {}
            try:
                # ★ 分组映射：data/panels/_groups.json 唯一数据源（与电脑端 /api/panels 同一份账）
                import json as _json
                from pathlib import Path as _Path
                _gf = _Path(__file__).resolve().parents[2] / 'data' / 'panels' / '_groups.json'
                if _gf.exists():
                    for _g, _ids in _json.loads(_gf.read_text(encoding='utf-8')).items():
                        for _pid in _ids:
                            gm[_pid] = _g
            except Exception:
                pass
            for p in panels:
                if p.get('hidden'):
                    continue
                items.append({
                    'id': p.get('id'), 'name': p.get('name'),
                    'icon': p.get('icon', ''), 'records': counts.get(p.get('id'), 0),
                    'type': p.get('type', 'table'),   # ★ #1006: 手机端按 type=album 走相册渲染分支
                    'group': gm.get(p.get('id'), ''),   # ★ 分组透传：手机面板中心按真实组二级页
                })
            return {'panels': items}

        if action == 'panels.query':
            pid = str(params.get('panel_id', '')).strip()
            if not pid:
                return {'error': '缺少 panel_id'}
            from src.core.panel_manager import get_panel_manager
            pm = get_panel_manager()
            if not pm.panel_exists(pid):
                return {'error': f'面板 {pid} 不存在'}
            rows = pm.query_panel_data(pid, limit=MAX_ROWS)
            # 只回传字段值，不回传任何本地路径
            return {'panel_id': pid, 'rows': rows, 'max_rows': MAX_ROWS}

        # ★ #931-C1（2026-09-30）：手机页快捷录入——先拿字段定义，再提交数据
        if action == 'panels.fields':
            pid = str(params.get('panel_id', '')).strip()
            if not pid:
                return {'error': '缺少 panel_id'}
            from src.core.panel_manager import get_panel_manager
            pm = get_panel_manager()
            panel = pm.get_panel(pid)
            if not panel:
                return {'error': f'面板 {pid} 不存在'}
            # ★ #989（2026-10-05）同构渲染器：手机面板详情页按字段配置渲染
            #   （label 中文标签 + options 选项表一并回传，都是 panel.json 里用户自己的配置，无敏感信息）
            fields = []
            for f in (panel.to_dict().get('fields') or []):
                if isinstance(f, dict) and f.get('name'):
                    item = {'name': str(f['name']), 'type': str(f.get('type', 'text')),
                            'required': bool(f.get('required'))}
                    if f.get('label'):
                        item['label'] = str(f['label'])
                    opts = f.get('options')
                    if isinstance(opts, list) and opts:
                        item['options'] = [str(o) for o in opts[:40]]
                    fields.append(item)
            return {'panel_id': pid, 'fields': fields}

        if action == 'panels.add':
            pid = str(params.get('panel_id', '')).strip()
            data = params.get('data') or {}
            if not pid or not isinstance(data, dict) or not data:
                return {'error': '缺少 panel_id 或 data'}
            from src.core.panel_manager import get_panel_manager
            pm = get_panel_manager()
            panel = pm.get_panel(pid)
            if not panel:
                return {'error': f'面板 {pid} 不存在'}
            # 安全：只允许面板声明过的字段名当列名（防注入），值统一转字符串
            valid = {str(f.get('name')) for f in (panel.to_dict().get('fields') or [])
                     if isinstance(f, dict) and f.get('name')}
            clean = {str(k)[:40]: str(v)[:500] for k, v in data.items()
                     if str(k) in valid and str(v).strip()}
            if not clean:
                return {'error': '没有可保存的字段（字段名和面板对不上）'}
            row = pm.insert_panel_data(pid, clean)
            return {'ok': True, 'record_id': row.get('id'),
                    'note': '已存入面板（手机远程快捷录入）'}

        # ★ #1034（2026-10-06）冰箱手机只读四 Tab：出门模式也能看冰箱全貌。
        #   库存/临期来自 fridge_hub 现成函数，食谱/买菜来自抽取后的无请求上下文版本；
        #   拍照识菜/食谱生成等重操作仍留在电脑端与在家模式（手机端只读）。
        if action == 'fridge.view':
            from src.core import fridge_hub as _fh
            stock_out = []
            try:
                for r in _fh._stock_rows(active_only=True)[:200]:
                    stock_out.append({
                        'name': str(r.get('名称') or ''),
                        'zone': str(r.get('分区') or ''),
                        'qty': (str(r.get('数量') or '') + str(r.get('数量单位') or '')).strip(),
                        'expiry': str(r.get('到期日') or ''),
                        'days': r.get('_days'),
                        'status': str(r.get('_st') or '在存'),
                    })
            except Exception:
                pass
            try:
                lines, n_exp, n_soon = _fh.expiry_digest()
            except Exception:
                lines, n_exp, n_soon = [], 0, 0
            recipe = None
            try:
                rd = _fh.recipe_last_data()
                recipe = rd.get('recipe')
            except Exception:
                pass
            buy = {'need': [], 'regular': []}
            try:
                bd = _fh.buylist_data()
                if bd.get('success'):
                    buy = {'need': bd.get('need') or [], 'regular': bd.get('regular') or []}
            except Exception:
                pass
            return {'stock': stock_out, 'expiry': {'lines': lines, 'n_expired': n_exp, 'n_soon': n_soon},
                    'recipe': recipe, 'buylist': buy}

        # ★ v23（2026-10-14）随身记收件箱：手机九宫格直达。列表截 100 条防拖库；
        #   内容截 500 字（与本地 inbox.add 同限），id 必须是正整数。
        if action in ('inbox.list', 'inbox.add', 'inbox.resolve', 'inbox.delete'):
            from src.core import inbox as _ib
            _ib.ensure_db()
            if action == 'inbox.list':
                return {'notes': _ib.list_notes(status='open', limit=100)}
            if action == 'inbox.add':
                content = str(params.get('content', '')).strip()[:500]
                if not content:
                    return {'error': '缺少 content'}
                # ★ v27（2026-10-15）#1151-E 来源标记：手机页语音转写记 voice，其余记 mobile（白名单防乱标）
                _src = str(params.get('source', 'mobile') or 'mobile')
                if _src not in ('mobile', 'voice'):
                    _src = 'mobile'
                r = _ib.add_note(content, source=_src)
                if not r.get('success'):
                    return {'error': r.get('message', '存失败')}
                # ★ v30（2026-10-17）A. 回执带语义解析出的提醒（手机端弹气泡让用户知道会响）
                return {'ok': True, 'id': r.get('id'), 'suggest': r.get('suggest', ''),
                        'confident': r.get('confident', False),
                        'remind': r.get('remind', ''), 'message': r.get('message', '')}
            if action == 'inbox.resolve':
                try:
                    nid = int(params.get('id', 0) or 0)
                except (TypeError, ValueError):
                    return {'error': 'id 不合法'}
                if nid <= 0:
                    return {'error': '缺少 id'}
                _ib.resolve_note(nid, str(params.get('to', '') or '')[:200])
                return {'ok': True}
            if action == 'inbox.delete':
                try:
                    nid = int(params.get('id', 0) or 0)
                except (TypeError, ValueError):
                    return {'error': 'id 不合法'}
                if nid <= 0:
                    return {'error': '缺少 id'}
                _ib.delete_note(nid)
                return {'ok': True}
            if action == 'inbox.remind':
                # ★ v30（2026-10-17）C. 手机页碎片手动挂/取消提醒：text=自然语言时间
                #   （「明天17:30」「明天下午5点半」「10-18 17:30」），空=取消。
                #   走 parse_reminder_text 解析（与聊天直设同一解析器），set_remind 内部同步 home_reminders 真家。
                try:
                    nid = int(params.get('id', 0) or 0)
                except (TypeError, ValueError):
                    return {'error': 'id 不合法'}
                if nid <= 0:
                    return {'error': '缺少 id'}
                txt = str(params.get('text', '') or '').strip()[:60]
                if not txt:
                    r = _ib.set_remind(nid, '')
                    return {'ok': r.get('success', False), 'remind_at': '', 'message': '已取消提醒'}
                from src.core.home_reminder import parse_reminder_text as _prt
                info, perr = _prt(txt)
                if perr or not info or not info.get('date'):
                    return {'error': '时间没看懂，试试「明天17:30」或「明天下午5点半」'}
                try:
                    from datetime import datetime as _dt_relay
                    _rdt = _dt_relay.strptime(f"{info['date']} {info.get('time') or '09:00'}", '%Y-%m-%d %H:%M')
                except ValueError:
                    return {'error': '时间格式没解出来，再试一次'}
                r = _ib.set_remind(nid, _rdt.strftime('%Y-%m-%d %H:%M'))
                return {'ok': r.get('success', False), 'remind_at': r.get('remind_at', ''),
                        'message': r.get('message', '')}
            if action == 'inbox.to_task':
                # ★ v31（2026-10-17）#1159 任务4 甲案：碎片一键转正为任务（双轨打通）
                try:
                    nid = int(params.get('id', 0) or 0)
                except (TypeError, ValueError):
                    return {'error': 'id 不合法'}
                if nid <= 0:
                    return {'error': '缺少 id'}
                r = _ib.to_task(nid)
                if not r.get('success'):
                    return {'error': r.get('message', '转任务失败')}
                return {'ok': True, 'task_id': r.get('task_id'), 'message': r.get('message', '')}
            if action == 'tasks.list':
                # ★ v31 服务抽屉任务直读（task_manager → structured_tasks 真家，SPA 同源）
                try:
                    from src.core.task_manager import get_task_manager
                    tasks = get_task_manager().get_tasks(limit=30) or []
                    out = [{'id': t.get('id'), 'title': t.get('title', ''),
                            'status': t.get('status', 'pending'),
                            'due': t.get('due_date') or '',
                            'created_at': t.get('created_at', '')} for t in tasks]
                    return {'tasks': out}
                except Exception as e:
                    return {'error': f'任务读不出来: {e}'}
            # ★ 2026-10-21 B2: 手机端目标直读 + 节点操作（审核推送数据源）
            if action == 'goals.list':
                try:
                    from src.core.goal_manager import list_goals, summary_card
                    goals = [summary_card(g) for g in list_goals(status='active', limit=30)]
                    return {'goals': goals}
                except Exception as e:
                    return {'error': f'目标读不出来: {e}'}
            if action == 'goals.action':
                params = params if isinstance(params, dict) else {}
                gid = int(params.get('goal_id', 0) or 0)
                act = params.get('goal_action', '')
                nidx = int(params.get('node_idx', -1) or -1)
                if not gid or not act:
                    return {'error': '缺少 goal_id/goal_action'}
                from src.core.goal_manager import (update_goal, set_node_status,
                                                   defer_node, lower_target, get_goal, summary_card)
                if act == 'node_done':
                    g = set_node_status(gid, nidx, 'done')
                elif act == 'defer':
                    from datetime import datetime as _dt, timedelta as _td
                    nd = (_dt.now() + _td(days=7)).strftime('%Y-%m-%d')
                    g = defer_node(gid, nidx, params.get('new_due') or nd)
                elif act == 'lower':
                    g = lower_target(gid, nidx, params.get('new_target') or 1)
                elif act == 'pause':
                    g = update_goal(gid, status='paused')
                else:
                    return {'error': f'不支持的目标操作: {act}'}
                if g and g.get('plan') and all(n.get('status') == 'done' for n in g['plan']):
                    g = update_goal(gid, status='done')
                return {'ok': True, 'goal': summary_card(g) if g else None}
            # ★ 2026-10-21 B1: 手机端家人健康直读（关键项摘要）
            if action == 'family.members':
                try:
                    from src.core.panel_manager import get_panel_manager
                    out = []
                    for m in get_panel_manager().get_members():
                        out.append({'id': m.get('id'), 'name': m.get('name', ''),
                                    'relation': m.get('relation', ''),
                                    'birthday': str(m.get('birthday') or '')[:10],
                                    'birthday_lunar': m.get('birthday_lunar', ''),
                                    'zodiac': m.get('zodiac', ''),
                                    'blood_type': m.get('blood_type', ''),
                                    'allergy': m.get('allergy', ''),
                                    'health_note': m.get('health_note', '')})
                    return {'members': out}
                except Exception as e:
                    return {'error': f'家人读不出来: {e}'}
            if action == 'kb.list':
                # ★ v31 服务抽屉知识库直读（faiss meta 清单：条数+最近条目摘要）
                import json as _json_kb
                try:
                    meta_p = Path(__file__).resolve().parent.parent / 'data' / 'faiss' / 'knowledge_meta.json'
                    if not meta_p.exists():
                        return {'total': 0, 'items': []}
                    meta = _json_kb.load(open(meta_p, encoding='utf-8'))
                    items = []
                    for m in (meta if isinstance(meta, list) else list(meta.values()))[:8]:
                        t = str(m.get('text', '')).strip()[:48]
                        items.append({'id': m.get('id', ''), 'text': t,
                                      'src': str(m.get('source', '') or m.get('file', '') or '')[:30]})
                    return {'total': len(meta) if hasattr(meta, '__len__') else len(items), 'items': items}
                except Exception as e:
                    return {'error': f'知识库读不出来: {e}'}
            if action == 'photo.combo':
                # ★ v31（2026-10-17）#1159 任务2 确认卡多图组合：N 张照片一条记录（物品多角度）
                imgs = params.get('images') or []
                if not imgs:
                    return {'error': '缺少 images'}
                note = str(params.get('note', '')).strip()[:200]
                tag = str(params.get('tag', '')).strip()[:40]
                quality = str(params.get('quality', '')).strip()[:20]
                question = str(params.get('question', '')).strip()[:300]
                if tag and not tag.startswith('panel:'):
                    return {'error': '组合存只支持面板用途（存物品/记药箱等）'}
                panel = tag[6:] if tag.startswith('panel:') else ''
                rid = _photo_panel_record(panel, note) if panel else 0
                saved, first_img = [], None
                for im in imgs[:9]:   # ★ #1408（2026-11-03）组合上限 4→9，与桌面 #1407-A _norm_images 对齐
                    im = str(im).strip()
                    if not im:
                        continue
                    s = _save_phone_photo(im, note, tag, quality)
                    if s:
                        saved.append(s.get('id'))
                        if rid:
                            _link_photo_record(s.get('id'), rid)
                        if not first_img:
                            first_img = im
                # 第一张跑一次视觉确认（组合存主要是归档，答一句就够）
                answer = ''
                if first_img:
                    try:
                        answer = _photo_answer(first_img, (note + '，简短确认一下看到的内容') if note else '简短描述这张照片', '')
                    except Exception:
                        answer = ''
                _save_mobile_chat(f'📷 组合存{len(saved)}张' + (f'：{note}' if note else ''), answer or '已存')
                return {'ok': True, 'record_id': rid, 'count': len(saved), 'answer': answer[:800]}

        # ★ v28（2026-10-16）#1154 手机「记冰箱」直录框：与电脑聊天同一条 L1 解析管线（fridge_hub.parse），
        #   零确认秒回执；fridge.undo 撤销最近一次直录（进程内存栈，重启如实报找不到）。
        if action in ('fridge.add', 'fridge.undo'):
            from src.core import fridge_hub as _fh
            if action == 'fridge.add':
                text = str(params.get('text', '')).strip()[:200]
                if not text:
                    return {'error': '缺少 text'}
                r = _fh.quick_fridge_add(text, source='mobile')
                if r.get('fallback'):
                    # 解析不出 → 不瞎猜，提示换说法（手机端引导 chips）
                    return {'success': False, 'message': '没认出这是啥食材，试试「娃娃菜2颗」「牛奶1盒」这样的说法'}
                return {'success': True, 'message': r.get('message', ''), 'ids': r.get('ids', [])}
            r = _fh.quick_fridge_undo()
            return {'success': r.get('success', False), 'message': r.get('message', '')}

        # ★ v27（2026-10-15）#1151 弹层顶部⏰提醒聚合区：随身记挂的提醒 + 全家庭未到期提醒。
        #   与 inbox.py api_reminders 同构数据（复用其查询口径），只读不写。
        if action == 'reminders.aggregate':
            from src.core import inbox as _ib
            _ib.ensure_db()
            from datetime import datetime as _dt
            _now = _dt.now().strftime('%Y-%m-%d %H:%M')
            with _ib._lock:
                _c = _ib._conn()
                try:
                    _rows = [dict(r) for r in _c.execute(
                        "SELECT id, content, remind_at, status FROM inbox_notes "
                        "WHERE remind_at != '' AND status = 'open' ORDER BY remind_at LIMIT 50").fetchall()]
                finally:
                    _c.close()
            _home = []
            try:
                from src.core.home_reminder import list_reminders as _lr
                for _r in _lr(pending_only=True, limit=50):
                    _home.append({'id': _r.get('id'), 'title': _r.get('title'),
                                  'remind_at': _r.get('remind_at'), 'module': _r.get('module'),
                                  'repeat': _r.get('repeat'), 'ref_id': _r.get('ref_id')})
            except Exception:
                pass
            return {'now': _now, 'inbox': _rows, 'home': _home,
                    'total': len(_rows) + len(_home)}

        if action == 'kb.search':
            q = str(params.get('query', '')).strip()
            if not q:
                return {'error': '缺少 query'}
            from src.core.builtin_tools import search_knowledge
            r = search_knowledge(q)
            results = []
            for item in (r.get('results') or [])[:MAX_KB_RESULTS]:
                text = ''
                if isinstance(item, dict):
                    text = str(item.get('text') or item.get('content') or '')
                else:
                    text = str(item)
                results.append({'text': text[:300]})
            return {'query': q, 'results': results}

        if action == 'chat':
            msg = str(params.get('message', '')).strip()
            if not msg:
                return {'error': '缺少 message'}
            # ★ #1544 B2（2026-11-25）物品存入撤销口令：先于记账确认拦截（记账类词不抢）
            _sundo = _try_store_undo(msg)
            if _sundo is not None:
                _save_mobile_chat(msg, _sundo)
                return {'answer': _sundo[:MAX_CHAT_CHARS]}
            # ★ A2（2026-10-23）手机凭证确认闭环：有记账待确认时「记上/确认入账」直接入库
            _cfirm = _try_mobile_accounting_confirm(msg)
            if _cfirm is not None:
                _save_mobile_chat(msg, _cfirm)
                return {'answer': _cfirm[:MAX_CHAT_CHARS]}
            _mode = str(params.get('mode', '')).strip().lower()   # ★ #955 轻页模式开关
            if _mode not in ('local', 'cloud'):
                _mode = ''
            _raw, _saved = _chat_via_engine(msg, _mode)     # v8-①：二元组（回答, 引擎是否已落库）
            answer = _clean_answer(_raw)                    # #813-A 剥思考过程
            if not answer:
                answer = '（哈文这次没给出有效回答，请再问一次）'
            if not _saved:
                _save_mobile_chat(msg, answer)              # 仅本地兜底路径才手动落库（防双写）
            return {'answer': answer[:MAX_CHAT_CHARS]}

        if action == 'lingzhu_chat':
            # ★ #1400（2026-11-02）灵珠眼镜通道：眼镜语音/拍照 → 灵珠平台 → Hub → 本机。
            # 走完整引擎管线（记忆/知识库/FC/看图），会话落「📱 手机远程」同款通道；
            # metadata（位置/天气/时间）作为现场上下文注入——眼镜用户常问「穿什么/要不要伞」。
            # ★ #1402~#1406（当晚体验批）：A 口播前缀 / B 流式进度帧 / C 照片入库 /
            #   D 拍照开关 / E Eye 快速档预检——全部尽力而为，绝不阻断主回答。
            text = str(params.get('text', '')).strip()
            image_url = str(params.get('image_url', '')).strip()
            rid = str(params.get('_relay_req_id', '') or '')
            if not text and not image_url:
                return {'error': '缺少消息内容'}
            # ★ #1405-B（2026-11-03）协议实验：眼镜拍照指令——自定义智能体也能中途拍照。
            # 协议实锤（openclaw-lingzhu-skill 源码级）：SSE 回包发 type=tool_call 帧
            # （tool_call.handling_required=true, command=take_photo）→ 眼镜自动拍照 →
            # 灵珠把新消息（带 image_url）再 POST 回来 → 走正常识图链路。
            # 只认明确拍照口令且本条无图，防「冰箱里有什么」误触发。
            if not image_url and text and any(
                    _k in text for _k in ('拍照', '拍一张', '拍一下', '拍个照', '帮我拍', '拍张照')):
                _relay_progress(rid, '📸 准备拍照…')
                logger.info(f'[CloudLink] #1405-B 眼镜拍照指令下发: {text[:40]}')
                return {'answer': '', 'command': 'take_photo'}
            meta = params.get('metadata') or {}
            ctx = ' '.join(f'[{k}:{meta.get(k)}]' for k in ('location', 'weather', 'current_time') if meta.get(k))
            imgs = None
            _reg = None
            if image_url:
                _relay_progress(rid, '📸 眼镜照片接收中…')
                _p = _download_remote_image(image_url, 'lingzhu')
                if not _p:
                    return {'error': '眼镜照片下载失败'}
                imgs = [_p]
                # ★ #1405（D）开关 + #1404（C）入图片库：sha 去重，note 带提问语义便于相册归类
                if _lingzhu_photo_save_enabled():
                    try:
                        from src.core.img_store import register as _img_reg
                        with open(_p, 'rb') as _f:
                            _reg = _img_reg(image_data=_f.read(), source='lingzhu',
                                            note='AR眼镜拍摄' + (f'：{text[:60]}' if text else ''),
                                            auto_tag=True)
                    except Exception as _ie:
                        logger.warning(f'[CloudLink] 眼镜照片入库失败(不阻断): {_ie}')
                # ★ #1406（E）Eye 快速档预检：检出结果拼进 prompt，让视觉模型带着底座答（尽力而为）
                try:
                    from src.core.eye_client import eye_recognize_tool as _eye_rec
                    _er = _eye_rec(image_path=_p)
                    _etext = str((_er or {}).get('text') or (_er or {}).get('answer') or '').strip()
                    if _etext:
                        ctx += f'（视觉底座快速识别参考：{_etext[:150]}）'
                except Exception:
                    pass
            full_msg = LINGZHU_VOICE_PREFIX + (text or '看看这是什么') + (f'（眼镜端现场：{ctx}）' if ctx else '')
            # ★ #1403（B）流式循环替代收集式 _chat_via_engine：thinking → 进度帧，正文照常攒
            _relay_progress(rid, '🤔 哈文思考中…')
            chunks, errs = [], []
            try:
                from src.core.engine import get_conversation_engine
                eng = get_conversation_engine()
                qmode = _mobile_query_mode()
                _last_note = time.time()
                with _mobile_engine_conversation():
                    for ev in eng.send(full_msg, stream=True, query_mode=qmode, images=imgs):
                        t, content = None, ''
                        if isinstance(ev, (tuple, list)) and len(ev) >= 2:
                            t, content = ev[0], ev[1]
                        elif isinstance(ev, str) and ev.startswith('data: '):
                            try:
                                d = json.loads(ev[6:].strip())
                                t, content = d.get('type'), d.get('content', '')
                            except Exception:
                                continue
                        else:
                            continue
                        if t in ('content', 'text'):
                            chunks.append(str(content or ''))
                        elif t == 'thinking':
                            if content and time.time() - _last_note > 5:
                                _relay_progress(rid, '🔍 ' + str(content)[:40])
                                _last_note = time.time()
                        elif t == 'error':
                            errs.append(str(content or ''))
                        elif t in ('done', 'final'):
                            break
            except Exception as _le:
                logger.error(f'[CloudLink] lingzhu_chat 引擎管线异常: {_le}')
                return {'error': f'处理出错: {_le}'}
            answer = _clean_answer(''.join(chunks).strip())
            if not answer and errs:
                answer = errs[-1].strip()
            if not answer:
                answer = '（哈文这次没给出有效回答，请再问一次）'
            answer = _voice_clean(answer)          # ★ #1402（A）口播清洗
            if answer.startswith('（哈文这次没给出有效回答'):
                answer = '哈文这次没给出有效回答，请再问一次'   # 口播版不带括号注记
            if _reg and isinstance(_reg, dict) and _reg.get('id'):
                logger.info(f'[CloudLink] 眼镜照片已入库 id={_reg["id"]} (source=lingzhu)')
            return {'answer': answer[:MAX_CHAT_CHARS],
                    'img_id': (_reg or {}).get('id')}

        if action == 'chat_stop':
            # ★ 2026-10-10 #1054：手机页停止按钮——给引擎设停止标记，
            # 正在跑的 _chat_via_engine 流式循环下一块就收笔（半截入库标「（已停止）」）
            from src.core.engine import request_stop as _req_stop
            _req_stop(_ensure_mobile_conv_id())
            return {'ok': True}

        if action == 'voice':
            # #815：手机按住说话 → 音频 base64 → 本地 Whisper 转文字 → 走 chat
            audio_b64 = str(params.get('audio', '')).strip()
            fmt = str(params.get('format', 'webm'))
            if not audio_b64:
                return {'error': '缺少 audio'}
            if len(audio_b64) > MAX_AUDIO_B64:
                return {'error': '录音太长，请控制在 60 秒内'}
            _mode = str(params.get('mode', '')).strip().lower()   # ★ #955
            if _mode not in ('local', 'cloud'):
                _mode = ''
            text = _transcribe_mobile(audio_b64, fmt)
            if not text:
                return {'error': '没听清，请再按住说一次（说话时离手机近一点）'}
            _raw, _saved = _chat_via_engine(text, _mode)    # v8-①：二元组
            answer = _clean_answer(_raw)
            if not answer:
                answer = '（哈文这次没给出有效回答，请再问一次）'
            if not _saved:
                _save_mobile_chat(f'🎤 {text}', answer)     # 仅本地兜底路径才手动落库（防双写）
            return {'text': text, 'answer': answer[:MAX_CHAT_CHARS]}

        if action == 'voice_text':
            # #833：两段式第一步——只转写不回答。文字回填输入框，
            #   主脑确认/改错字后点发送才真正走 chat（旧 voice 动作保留兼容）。
            audio_b64 = str(params.get('audio', '')).strip()
            fmt = str(params.get('format', 'webm'))
            if not audio_b64:
                return {'error': '缺少 audio'}
            if len(audio_b64) > MAX_AUDIO_B64:
                return {'error': '录音太长，请控制在 60 秒内'}
            text = _transcribe_mobile(audio_b64, fmt)
            if not text:
                return {'error': '没听清，请再按住说一次（说话时离手机近一点）'}
            return {'text': text}

        if action == 'photo':
            # #815：手机拍照 → 图片 base64 → 本地视觉模型看图 → 回答
            img = str(params.get('image', '')).strip()
            q = str(params.get('question', '')).strip()
            if not img:
                return {'error': '缺少 image'}
            if len(img) > MAX_PHOTO_B64:
                return {'error': '图片太大，请重新拍一张'}
            # ★ #1156（2026-10-16）根修「照片没名字」：旧版把给 AI 的指令原文当说明存库
            #   （「把这张照片存到「孩子成长档案」面板。说明：…」），相册/弹层显示的就是这句话。
            #   现在说明=用户自己的那句话（手机端随 photo 另发 note 字段；旧版页面从 question 里提取），
            #   提取不出来就留空——空了由 AI 后台起名，绝不拿指令原文糊弄人。
            _note = _extract_photo_note(str(params.get('note', '')), q)
            # ★ #918（2026-09-27）手机拍照自动入统一图库（三大上传入口之一，开发者点名的最高频）：
            #   描述=拍照时的随口一句话（question/语音转写）→ 关键词自动分相册。失败不挡看图。
            _img_id = None
            _is_dup = False   # ★ #1301 重复图标记：去重时明确告诉用户「已收到」，不再静默
            try:
                from src.core import img_store
                # ★ #1306 手机拍照链：register 不再自动起打标线程，统一由存档后的
                #   auto_tag_full（带 pid，能补建面板记录）延迟跑，避免双重打标
                r = img_store.register(image_data=img, source='mobile', note=_note[:200],
                                       auto_tag=False)
                if r:
                    _img_id = r.get('id')
                    _is_dup = bool(r.get('duplicate'))
                    if not r.get('duplicate'):
                        logger.info(f'[CloudLink] 手机拍照已入图库 id={r["id"]}')
                    # ★ #1279 节点⑨ 队列通道：说明命中深识关键词（保质期/药品/食材…）
                    #   → 自动入深识队列，管家后台空闲时识别+议程提醒（不挡看图主链路）
                    # ★ #1308（2026-10-29 定版拍板）B：面板即意图——tag=fridge 的照片
                    #   说明写啥都直通 food 深识（实锤：「桃子 昨天」不命中词表 → 台账永远空）
                    try:
                        from src.vision import tasks as _vt
                        _tag0 = str(params.get('tag', '')).strip()
                        if _tag0.startswith('panel:'):
                            _tag0 = _tag0[6:]
                        if _tag0 in ('fridge', 'fridge_stock_1'):
                            _vt.enqueue('', kind='food', note=_note, img_id=str(_img_id or ''))
                        else:
                            _vt.maybe_enqueue_from_note('', _note, img_id=str(_img_id or ''))
                        _vt.ensure_loop()
                    except Exception as _ve:
                        logger.debug(f'[CloudLink] 深识入队跳过: {_ve}')
            except Exception as _ie:
                logger.debug(f'[CloudLink] 手机拍照入图库跳过: {_ie}')
            # ★ 2026-10-22 打通 A（定版拍板）：对话拍照+批改意图 → 走正式批改管线入库
            #   （结果卡即确认：每题对错全展示，错题自动入错题本，可随时标掌握撤销）
            # ★ #1197（2026-10-25）根因1 修复：学习类面板拍照默认走批改——
            #   确认卡选「学习成长中心/孩子成长档案」时用户常忘填说明，固定模板话术不含
            #   「批改」关键词 → 只走普通看图 → 面板啥也没记。现在 tag 指向学习面板时
            #   无关键词也默认进批改管线；默认批改没批出来自动兜底转普通看图（不拦照片）。
            #   注意：本分支不再提前 return，统一走底部照片库/图库/起名链路（批改结果也带 pid）。
            _grade_kw = ('批改', '批卷', '批作业', '判卷', '对答案', '改卷子', '检查作业', '批一下')
            _grade_explicit = any(k in q for k in _grade_kw)
            _tag_probe = str(params.get('tag', '')).strip()[:40]
            if _tag_probe.startswith('panel:'):
                _tag_probe = _tag_probe[6:]
            _is_study_photo = _tag_probe in ('study_center', 'kid_archive')
            _graded = False
            _grade_id = 0   # ★ #1253 批改照片回填定位（批改成功才有值）
            _hw_id = 0
            if _grade_explicit or _is_study_photo:
                try:
                    from src.core.study_coach import grade_homework
                    _subject = '自动'   # ★ #1252 科目自动识别（VLM 判学科，不再默认数学——语文作业记成数学实锤）
                    for _sj in ('数学', '语文', '英语', '物理', '化学'):
                        if _sj in q:
                            _subject = _sj
                            break
                    try:
                        from src.core.panel_manager import get_panel_manager as _gpm2
                        _child = ''
                        for _m in _gpm2().get_members():
                            if _m['name'] and _m['name'] in q:
                                _child = _m['name']
                                break
                    except Exception:
                        _child = ''
                    _gr = grade_homework([img], child=_child, subject=_subject, note='拍照批改')
                    if _gr.get('success'):
                        # ★ #1252 豆包式展示标准（定版拍板）：逐题都列，对错一目了然——
                        #   不再全对时只给统计没题目
                        # ★ #1302 批改方如实标注（外脑视觉/本地 Eye，选谁给谁不冒充）
                        _lines = [f"📸 {_gr.get('child','') or '孩子'}的{_gr.get('subject','')}作业 · "
                                  f"共 {_gr['total']} 题，错 {_gr['wrong']} 题，正确率 {_gr['correct_rate']}%"
                                  + (f"\n🔍 批改方：{_gr.get('vision_model','')}" if _gr.get('vision_model') else '')]
                        for _it in _gr.get('items', []):
                            _no = str(_it.get('no', '') or '').strip()
                            _qq = (_it.get('question') or '')[:24]
                            if _it.get('uncertain'):
                                _lines.append(f"❓ 第{_no}题 {_qq}——字迹待人工确认")
                            elif _it.get('wrong'):
                                _lines.append(f"❌ 第{_no}题 {_qq}：你的答案「{_it.get('student_answer','')}」→ 正确「{_it.get('correct_answer','')}」（{_it.get('kp','')}）")
                            else:
                                _lines.append(f"✅ 第{_no}题 {_qq}")
                        # ★ #1193（2026-10-25）文案诚实化：0 错题不再谎称「已入错题本」
                        if _gr.get('wrong'):
                            _lines.append('✅ 错题已存入错题本，电脑「学习成长中心」→ 错题本可查')
                        else:
                            _lines.append('✅ 本次全对，没有错题入本；成绩已记入学习档案')
                        if _gr.get('uncertain_count'):
                            _lines.append(f"⚠️ {_gr['uncertain_count']} 题字迹待人工确认，未判错")
                        answer = '\n'.join(_lines)
                        _graded = True
                        # ★ #1253 批改照片回填定位：等照片库存完把 url 挂到成绩+作业记录上
                        _grade_id = int(_gr.get('grade_id') or 0)
                        _hw_id = int(_gr.get('hw_id') or 0)
                    elif _grade_explicit:
                        # 用户明确说了批改 → 如实报告失败原因
                        answer = '⚠️ 批改失败：' + str(_gr.get('error', ''))[:120]
                        _graded = True
                    else:
                        # #1197 学习面板默认批改没批出来 → 兜底转普通看图（照片不白拍）
                        logger.info(f'[CloudLink] 学习面板默认批改未出结果，兜底转普通看图: {str(_gr.get("error", ""))[:80]}')
                except Exception as _ge:
                    if _grade_explicit:
                        logger.warning(f'[CloudLink] 对话批改失败转普通看图: {_ge}')
                    else:
                        logger.info(f'[CloudLink] 学习面板默认批改异常转普通看图: {_ge}')
            # ★ 2026-10-23 智能凭证识别（定版拍板「开发全部」）：拍照菜单/小票/发票/账单
            #   → 视觉模型提结构化（复用 extract_records 记账抽取链：词典→外脑→本地兜底）
            #   → 进待确认队列（assist_pending），收纳盒「📥 待确认」点确认入账，
            #   走 insert_records 同管线（查重/分类兜底/花销目标钩子全继承）。失败不挡看图。
            _receipt_kw = ('菜单', '发票', '小票', '票据', '收据', '账单', '记一笔', '记账')
            # ★ #1379（2026-11-02 定版拍板）误触发豁免：提问句让路 + 用户拉黑词让路
            #   A：「这发票能报销吗？」是提问不是录入，正常走看图回答
            #   B2：用户明说「以后别识别 X」的词，之后见到不再抢答
            _hit_kw = [k for k in _receipt_kw if k in (q or '')]
            if not _graded and _hit_kw:
                from src.core import panel_assist as _pa0
                if _pa0.looks_like_question(q):
                    logger.info(f'[CloudLink] #1379 提问豁免：含 {_hit_kw} 但句式是提问，转普通看图')
                    _hit_kw = []
                else:
                    _bl = _pa0.get_receipt_blocklist()
                    _blocked = [k for k in _hit_kw if k in _bl]
                    if _blocked:
                        logger.info(f'[CloudLink] #1379 黑名单豁免: {_blocked}（用户说过以后别识别）')
                        _hit_kw = []
            if not _graded and _hit_kw:
                try:
                    from src.core import panel_assist as _pa
                    _recs, _dom, _model, _err = _pa.extract_records(
                        q or '记账', 'accounting_daily_1', '', image_b64=img)
                    if not _err and _recs:
                        _pa.add_pending('accounting_daily_1', _dom, _recs,
                                        source_text='📱 拍照凭证：' + (q or '')[:80])
                        # ★ #1379 照片识别即打标记：之后无论确认还是「别记」作废，
                        #   心跳/相册按钮都不会再扫这张（防重复骚扰）
                        try:
                            if _img_id:
                                from src.core import img_store as _is
                                _is.mark_bills_processed([int(_img_id)], note='凭证已识别待确认（对话内处置）')
                        except Exception:
                            pass
                        _lines = ['🧾 凭证已识别，等你确认入库：']
                        for _r in _recs[:5]:
                            _item = _r.get('项目') or _r.get('名称') or '消费'
                            _amt = _r.get('金额', '')
                            _cat = _r.get('分类', '')
                            _lines.append(f'• {_item} {_amt} 元' + (f'（{_cat}）' if _cat else ''))
                        if len(_recs) > 5:
                            _lines.append(f'…共 {len(_recs)} 条')
                        # ★ A2（2026-10-23）手机没有收纳盒 → 主引导改为对话内回复确认
                        _lines.append('✅ 直接回复「确认入账」就入库；回复「别记」作废。电脑端也能在「收纳盒 → 📥 待确认」点确认')
                        answer = '\n'.join(_lines)
                        _save_mobile_chat(f'🧾 {q or "拍照记账"}', answer,
                                          img_id=_img_id, photo_note=_note, tag='accounting')   # ★ #1199 照片元信息入会话
                        logger.info(f'[CloudLink] 凭证识别出 {len(_recs)} 条待确认（{_model}）')
                        return {'answer': answer[:MAX_CHAT_CHARS], 'img_id': _img_id}
                    logger.info(f'[CloudLink] 凭证识别未出记录（{_err or "空"}），转普通看图')
                except Exception as _rce:
                    logger.warning(f'[CloudLink] 凭证识别失败转普通看图: {_rce}')
            # ★ #1305（2026-10-29 定版拍板）指令型拍照秒回：消息是「存/记」类指令、没提问
            #   → 跳过 VLM 看图（实锤：「记入冰箱库存」白看 4s 图只说了句已存好），直接秒回。
            #   内容描述交给后台标签链（YOLOX+CLIP，0 token 不抢道）；提问型照常看图。
            _store_done = False
            _fast_tags = []   # ★ #1544 B2 快速档同步标签（回执「识别为…」+ 直供台账链）
            if not _graded and _is_store_command(q):
                _zone = _photo_zone_name(_tag_probe)
                # ★ #1544（2026-11-25 B2）秒回带标签：同步等 Eye 快速档（0.1~1s，不走 VLM
                #   零费用不抢道），当场报「识别为：抽纸、纸巾」+ 撤销口令——修实锤「存抽纸
                #   秒回已存好但没说存了啥」。认不出如实说稍后补（#1542 VLM 补标成功后
                #   img_store._relink_panel_record 自动补建台账）。
                try:
                    from src.core.img_store import _eye_fast_tags as _eft
                    _fast_tags, _ft_err = _eft(img)
                except Exception as _fte:
                    _fast_tags, _ft_err = [], str(_fte)
                _spec = [t for t in _fast_tags
                         if t not in _GENERIC_TAG_WORDS and not t.startswith(('🏷', '⏳'))]
                _head = f"✓ 已存好{'（' + _zone + '）' if _zone else ''}"
                if _spec:
                    answer = (_head + '，识别为：' + '、'.join(_spec[:3])
                              + (f"（id={_img_id}）" if _img_id else '')
                              + ('。这张之前也收过，不重复占地方' if _is_dup else '')
                              + '\n台账如有出入，回复「撤销」可删。')
                else:
                    _fast_tags = []   # 全是泛词/认不出 → 不传标签，不建残记录（等 B3 补建）
                    answer = (_head
                              + (f'：{_note.strip()[:40]}' if (_note or '').strip() else '')
                              + (f"（id={_img_id}）" if _img_id else '')
                              + ('。这张之前也收过，不重复占地方' if _is_dup else '')
                              + '\n标签稍后自动补上，想问照片里的内容直接问我（比如「这是什么牌子」），我就细看给你讲。')
                _store_done = True
                logger.info(f'[CloudLink] 指令型拍照秒回（跳过看图）zone={_zone} '
                            f'tags={_fast_tags[:3]} err={_ft_err} q={q[:30]}')
            if not _graded and not _store_done:
                answer = _photo_answer(img, q,
                                       str(params.get('mode', '')).strip().lower())   # ★ #955 模式开关
            # ★ #1301 重复图提示：去重不再静默——重发同一张时明确告诉用户「已收到」
            #   （实锤：重发卷子被去重复用 id=134，手机端等不到回音误以为没传上来）
            if _is_dup and _img_id and not _graded and not _store_done:
                answer = f"📥 这张照片之前已收到（图库 id={_img_id}），已按你这次的指令重新看图。\n\n" + answer
            # ★ #1312/#1313（2026-10-29）：会话落库从本处下移到「建记录」之后——
            #   先建完记录再落库，answer 能带上台账记录摘要（见下方 #1312 块）
            # ★ r30（2026-10-10）确认卡用途入库：带 tag 的照片补进 panel_images 照片库
            #   （tag=fridge/account/面板id；quality=hd 原图 / compressed 已压缩）。失败不挡看图。
            _tag = str(params.get('tag', '')).strip()[:40]
            _quality = str(params.get('quality', '')).strip()[:20]
            _saved = None
            if _tag:
                _saved = _save_phone_photo(img, _note, _tag, _quality)
            # ★ #1253（2026-10-27）批改照片回填：照片库存完把 url 挂到成绩+作业记录——
            #   修「电脑上作业打不开没照片」实锤（批改链 photo 字段一直是空）
            if _saved and _grade_id:
                try:
                    from src.core.study_coach import attach_photo
                    _ap = attach_photo(_grade_id, _saved.get('url', ''), _hw_id)
                    if _ap.get('success'):
                        logger.info(f'[CloudLink] 批改照片已挂载 grade={_grade_id} hw={_hw_id} url={_saved.get("url", "")[:60]}')
                except Exception as _ape:
                    logger.debug(f'[CloudLink] 批改照片回填跳过: {_ape}')
            # ★ #1159（2026-10-17）任务1：存面板同步建记录——照片不再是孤儿
            #   （实锤：只挂照片不建记录，电脑端面板记录列表永远空 =「手机录了电脑没有」）
            # ★ 2026-10-22 修复「手机拍照录了但电脑没有记录」：tag 裸面板id（如 kid_archive）
            #   与要求的前缀格式 panel:kid_archive 不匹配 → 建记录被跳过（实锤 18:53 那次）。
            #   归一化：tag 是已安装面板 id 时自动补 panel: 前缀
            if _saved and _tag and not _tag.startswith('panel:'):
                try:
                    from src.core.panel_manager import get_panel_manager as _gpm
                    if any(p['id'] == _tag for p in _gpm().get_all_panels()):
                        _tag = 'panel:' + _tag
                except Exception:
                    pass
            #   ★ #1197 批改流跳过面板记录：grade_homework 已入学习档案/错题本，再建一条就重复了
            _rid = 0
            if _saved and _tag.startswith('panel:') and not _graded:
                # ★ #1544 B2：秒回已同步拿到的标签直供台账链（不再重复识别）；
                #   返回 -1 = 标签无可用名称（等补标成功后 img_store 侧补建）
                _rid = _photo_panel_record(_tag[6:], _note, tags=_fast_tags or None)
                if _rid and _rid > 0:
                    _link_photo_record(_saved.get('id'), _rid)
                    # ★ #1544 B3 回写图库行户口：深识队列补建台账靠 panel_id/record_id 定位
                    try:
                        if _img_id:
                            img_store._update(int(_img_id), panel_id=_tag[6:], record_id=int(_rid))
                    except Exception:
                        pass
                    # ★ #1544 B2 撤销口令户口：进程内撤销栈（10 分钟内说「撤销」可删）
                    try:
                        _STORE_UNDO_STACK.append({'rid': int(_rid), 'panel': _tag[6:],
                                                  'pid': int(_saved.get('id') or 0),
                                                  'ts': time.time()})
                        if len(_STORE_UNDO_STACK) > 10:
                            del _STORE_UNDO_STACK[:-10]
                    except Exception:
                        pass
                elif _img_id:
                    # 没建成记录也把面板户口写上（深识补出标签后按它补建）
                    try:
                        img_store._update(int(_img_id), panel_id=_tag[6:])
                    except Exception:
                        pass
            # ★ #1312（2026-10-29 2a 拍照链会话回写，定版拍板）：照片链动作只落库不进会话 →
            #   追问「这个咖啡机是什么牌子」时聊天上下文断层，模型两眼一抹黑只能翻库
            #   （咖啡机事故实锤）。建完记录把摘要并入本条回复再落库：单条消息既让用户
            #   当场知情（#1313 秒回升级），又让后续追问接得上上下文。失败不影响回复。
            if _rid and _rid > 0:   # ★ #1544 -1=等补标补建，不算已建，不报台账行
                _zone2 = _photo_zone_name(_tag_probe)
                answer = (answer.rstrip()
                          + f"\n📋 已帮你记成{_zone2 or '面板'}台账（记录 id={_rid}）"
                          + (f"：{(_note or '').strip()[:60]}" if (_note or '').strip() else ''))
            _save_mobile_chat(('📸 ' if _graded else '📷 ') + (q or ('拍照批改' if _graded else '看看这张照片')),
                              answer,
                              img_id=_img_id,
                              photo_note=_note, tag=_tag_probe if _tag_probe else None)   # ★ #1199 照片元信息入会话（追问能接上）
            # ★ #1306（2026-10-29）起名通道下线（定版拍板「起名不靠谱，删」）：
            #   改统一后台打标链 auto_tag_full（标签+CLIP归位+主镜头补建面板记录），
            #   延迟 20s 让看图回答独占 GPU 先跑完（#1248 沿革）；秒回场景立即跑不挡回复
            if _img_id:
                try:
                    img_store.auto_tag_full(_img_id, pid=(_saved or {}).get('id'),
                                            delay=0 if _store_done else 20)
                except Exception as _te:
                    logger.debug(f'[CloudLink] 统一打标调度跳过: {_te}')
            # ★ 照片链 C2（2026-10-11）：响应带 pid=照片库图行 id——手机排队原图后回家按它补传替换
            #   ★ #1156：响应另带 img_id=统一图库行 id——浮条轮询名字/改名全按它定位
            return {'answer': answer[:MAX_CHAT_CHARS], 'pid': (_saved or {}).get('id'),
                    'img_id': _img_id}

        if action == 'images.list':
            # ★ #927（2026-09-29）手机远程看图库：出门走云端也能打开「图片库」
            #   返回最新 24 张的缩略图 base64（压缩 400px/JPEG q55，单张约 20~40KB，
            #   全包 <1MB，wss 中转扛得住）。中转只转发不落原文——图片也是过路不过夜。
            from src.core import img_store
            q = str(params.get('q', '')).strip()
            album = str(params.get('album', '')).strip()
            source = str(params.get('source', '')).strip()  # ★ #928 T5：记忆相册过滤生活照来源
            items = img_store.search(q=q, album=album, source=source, limit=24)
            out = []
            for it in items:
                p = img_store.get_file(it['id'])
                if not p:
                    continue
                b64 = _pic_b64(p, max_side=400, quality=55)
                if not b64:
                    continue
                out.append({'id': it['id'], 'note': (it['note'] or '')[:50],
                            'album': it['album'], 'tags': (it['tags'] or '')[:100],
                            'thumb': 'data:image/jpeg;base64,' + b64})
                if len(out) >= 24:
                    break
            return {'images': out, 'total': len(out)}

        if action == 'images.file':
            # ★ #927 单图大图：压缩到最长边 1280（≈150~300KB），点开看细节用
            # ★ #1070（2026-10-11）聊天内图片直显：气泡缩略图带 thumb=1
            #   （400px/q55 ≈20~40KB，出门流量也轻；点开大图不带 thumb 走 1280 原逻辑）
            from src.core import img_store
            try:
                iid = int(params.get('id', 0))
            except (TypeError, ValueError):
                return {'error': 'id 不合法'}
            p = img_store.get_file(iid)
            if not p:
                return {'error': '图片不存在'}
            want_thumb = bool(params.get('thumb'))
            b64 = _pic_b64(p, max_side=400 if want_thumb else 1280,
                           quality=55 if want_thumb else 72)
            if not b64:
                return {'error': '图片读不出来'}
            return {'image': 'data:image/jpeg;base64,' + b64}

        if action == 'images.upload':
            # ★ #928 T6（2026-09-29）手机相册批量导入：出门也能把手机相册照片存进家里记忆相册。
            #   纯存库不惊动 AI（chat 通道才答话）；base64 上限沿用 photo 的 12MB。
            #   手机端已压缩（最长边 1280）再传，单张 ≈150~300KB。
            import base64 as _b64u
            raw_b64 = str(params.get('image_b64', ''))
            if not raw_b64:
                return {'error': '没有图片内容'}
            if len(raw_b64) > MAX_PHOTO_B64:
                return {'error': '图片太大，请选小一点的照片'}
            if ',' in raw_b64 and raw_b64.strip().startswith('data:'):
                raw_b64 = raw_b64.split(',', 1)[1]
            try:
                raw = _b64u.b64decode(raw_b64)
            except Exception:
                return {'error': '图片解码失败'}
            from src.core import img_store
            # ★ r30：画质标记入 tags（详情显示「高清原图/已压缩」，可按标签搜）
            _q = str(params.get('quality', '')).strip()
            _qtag = '高清原图' if _q == 'hd' else ('已压缩' if _q == 'compressed' else '')
            r = img_store.register(image_data=raw, source='mobile',
                                   album='生活照', note=str(params.get('note', '') or '')[:100],
                                   tags=[_qtag] if _qtag else None)
            if not r:
                return {'error': '入库失败'}
            # ★ 照片链 C2（2026-10-11）相册原图补传：replace_img_id=旧压缩版图行 id，
            #   原图注册成功后把旧压缩版删掉（存新删旧，一次请求完成升级）
            _rep = params.get('replace_img_id')
            if _rep:
                try:
                    img_store.remove(int(_rep))
                except (TypeError, ValueError):
                    pass
                except Exception as _re:
                    logger.debug(f'[CloudLink] 相册补传删旧图跳过: {_re}')
            # ★ #1279 节点⑨ 队列通道：相册补传说明命中深识关键词 → 自动入队（不挡返回）
            try:
                from src.vision import tasks as _vt
                _vt.maybe_enqueue_from_note('', str(params.get('note', '') or ''),
                                            img_id=str(r.get('id') or ''))
                _vt.ensure_loop()
            except Exception as _ve:
                logger.debug(f'[CloudLink] 相册深识入队跳过: {_ve}')
            return {'id': r['id'], 'url': r.get('url', '')}

        if action == 'images.replace_original':
            # ★ 照片链 C2（2026-10-11）手机原图补传：回家 WiFi 后用原图升级照片库已存压缩版。
            #   按 image_id（panel_images 图行 id）定位——手机拍照入库的图没有 record_id。
            import base64 as _b64u
            _img = str(params.get('image', '')).strip()
            if not _img:
                return {'error': '缺少 image'}
            if len(_img) > MAX_PHOTO_B64 + 200:
                return {'error': '原图太大，补传失败'}
            try:
                _iid = int(params.get('image_id') or 0)
            except (TypeError, ValueError):
                return {'error': 'image_id 不合法'}
            if not _iid:
                return {'error': '缺少 image_id'}
            from src.core import panel_images
            new = panel_images.replace_with_original(None, _img, image_id=_iid)
            if not new:
                return {'error': '没找到要升级的旧图（可能已被删除）'}
            return {'image': new}

        if action == 'images.title':
            # ★ #1306（2026-10-29）起名通道下线：浮条轮询改直读图库（note 或首个标签）。
            #   只读不烧钱——打标线程由入库链调度，这里只查状态。
            from src.core import img_store
            try:
                _iid = int(params.get('id') or 0)
            except (TypeError, ValueError):
                return {'error': 'id 不合法'}
            if not _iid:
                return {'error': '缺少 id'}
            cur = img_store.get(_iid)
            if not cur:
                return {'error': '照片不存在'}
            note = (cur.get('note') or '').strip()
            raw_tags = cur.get('tags') or []
            tags = [str(t).strip() for t in raw_tags] if isinstance(raw_tags, list) \
                else [t.strip() for t in raw_tags.split(',') if t.strip()]
            # 旧版残留的指令原文不是名字（源头已修，这只防存量脏数据）
            garbage = note.startswith('把这张照片') or note.startswith('把照片')
            if note and not garbage:
                return {'title': note[:30], 'cat': '', 'done': True, 'pending': False, 'err': ''}
            real_tags = [t for t in tags if not t.startswith('🏷')]
            if real_tags:
                return {'title': real_tags[0], 'cat': '', 'done': True, 'pending': False, 'err': ''}
            if '🏷标签失败' in tags or '🏷起名失败' in tags:   # 兼容存量失败标记
                return {'title': '', 'cat': '', 'done': True, 'pending': False,
                        'err': '没识别出内容（可在图库手动标注）'}
            return {'title': '', 'cat': '', 'done': False, 'pending': True, 'err': ''}

        if action == 'images.set_title':
            # ★ #1306 起名通道下线：用户改的名/分类直接写库（用户的话 > AI，不再联动重起名）。
            #   title 写图库 note+照片库弹层名；cat 不再限词表（用户自由），并进标签。
            from src.core import img_store
            try:
                _iid = int(params.get('id') or 0)
            except (TypeError, ValueError):
                return {'error': 'id 不合法'}
            if not _iid:
                return {'error': '缺少 id'}
            _title = str(params.get('title', '')).strip()[:60]
            _cat = str(params.get('cat', '')).strip()[:12]
            _pid = params.get('pid') or None
            if _title:
                img_store.set_note(_iid, _title)
                if _cat:
                    img_store.set_tags(_iid, [_cat], merge=True)
                if _pid:
                    try:
                        from src.core.panel_images import DB_PATH as _pdb
                        import sqlite3 as _sq
                        _c = _sq.connect(str(_pdb), timeout=15)
                        try:
                            _c.execute('UPDATE panel_images SET note=? WHERE id=?',
                                       (_title[:200], int(_pid)))
                            _c.commit()
                        finally:
                            _c.close()
                    except Exception as _pe:
                        logger.debug(f'[CloudLink] 照片库弹层名回写跳过 pid={_pid}: {_pe}')
                return {'ok': True, 'title': _title, 'cat': _cat, 'renaming': False}
            if _cat:
                img_store.set_tags(_iid, [_cat], merge=True)
                return {'ok': True, 'cat': _cat, 'renaming': False}
            return {'error': '名字和分类至少给一个'}

    except Exception as e:
        logger.error(f"[CloudLink] 指令 {action} 执行失败: {e}")
        return {'error': f'执行失败: {e}'}
    return {'error': '未知指令'}


# ══════════════════════════ chat：清洗 + 落库（#813） ══════════════════════════

# ★ #1305（2026-10-29 定版拍板）指令型拍照秒回词典：有存/记意图词、无提问词 → 跳过 VLM 看图
_STORE_CMD_KW = ('记入', '存入', '入库', '存到', '存进', '收好', '存好', '记一下', '记录一下',
                 '存一下', '拍下', '收进', '归档', '收了', '放好', '保管', '收藏')
_STORE_QUESTION_KW = ('？', '?', '什么', '怎么', '为什么', '哪个', '哪些', '多少', '几个', '谁',
                      '哪里', '哪儿', '看看', '识别', '描述', '批改', '多少钱', '介绍',
                      '帮我看', '检查', '评分', '判断', '值多少')


def _is_store_command(q):
    """★ #1305 指令型拍照判定（词典快车道，本地零网络）：是「存/记」指令且不是提问。"""
    t = (q or '').strip()
    if not t or any(k in t for k in _STORE_QUESTION_KW):
        return False
    return any(k in t for k in _STORE_CMD_KW)


def _photo_zone_name(tag_probe):
    """★ #1305 秒回文案的归属中文名：面板 id → 面板名 > 常用映射 > 原样。"""
    t = (tag_probe or '').strip()
    if not t:
        return ''
    _fixed = {'fridge': '冰箱', 'account': '记账', 'study_center': '孩子学习',
              'kid_archive': '孩子成长档案'}
    if t in _fixed:
        return _fixed[t]
    try:
        from src.core.panel_manager import get_panel_manager as _gpm
        for p in _gpm().get_all_panels():
            if p['id'] == t:
                return p.get('name', t)
    except Exception:
        pass
    return t


_THINK_BLOCK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)
_THINK_HEAD_RE = re.compile(r'^#{0,4}\s*\**\s*(?:Thinking|思考过程)\**\s*:?\s*$',
                            re.IGNORECASE | re.MULTILINE)
_ANS_HEAD_RE = re.compile(r'^#{1,6}\s*\**\s*(?:最终回答|最终回复|最终答案|Final\s*Response|回答|Answer|Response|答复)\**\s*:?\s*\n?',
                          re.IGNORECASE)


def _clean_answer(text) -> str:
    """#813-A：剥掉模型思考过程，只留最终答案（清洗在哈文侧，Hub 仍只转发）。
    覆盖三类包装：
      1. <think>…</think> 完整块（deepseek-r1 等 thinking 模型）
      2. 未闭合的 <think>（流被截断，其后全是思考）
      3. 「## Thinking / 思考过程」标题块（到下一个 Markdown 标题为止）
      4. #830-B 兜底：工具/代码执行过程块（📋/❌/📁 开头，直到空行为止）
         —— 正常情况下 #829 已把这些改成 tool_log 独立事件到不了这里，
            此兜底防旧格式残留（如幻觉 tool_calls 路径拼进正文的 list_dir 结果）。
    """
    if not text:
        return ''
    t = str(text).strip()
    # 防御：万一上游把 (text, backend, error) 三元组 str() 后传进来，先解包取首项
    if t.startswith(("('", '("')) and t.endswith(("')", '")')) and ', ' in t:
        try:
            import ast
            v = ast.literal_eval(t)
            if isinstance(v, tuple) and v and isinstance(v[0], str):
                t = v[0]
        except Exception:
            pass
    t = _THINK_BLOCK_RE.sub('', t)
    idx = t.lower().find('<think>')
    if idx != -1:
        t = t[:idx]
    m = _THINK_HEAD_RE.search(t)
    if m:
        rest = t[m.end():]
        nxt = re.search(r'^#{1,6}\s+\S', rest, re.MULTILINE)
        t = (t[:m.start()] + (rest[nxt.start():] if nxt else ''))
    t = _ANS_HEAD_RE.sub('', t)   # 开头的「## 回答」等标题也剥掉
    t = _strip_tool_blocks(t)     # #830-B：工具执行过程块兜底剥离
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    return t


_TOOL_HEAD_RE = re.compile(
    r'^\s*(?:📋|❌|📁)\s*\**[^*\n]{0,60}\**\s*(?:执行结果|执行成功|失败)')


def _strip_tool_blocks(t: str) -> str:
    """#830-B 兜底：剥掉「📋 **工具名** 执行结果：…」这类过程块。
    规则：遇到工具结果头行后，丢弃后续内容直到空行（工具 JSON/代码结果块
    内部不含空行，回答正文前通常有空行分隔）。"""
    if '执行结果' not in t and '执行成功' not in t and '失败：' not in t:
        return t
    lines = t.split('\n')
    out, skipping = [], False
    for ln in lines:
        if _TOOL_HEAD_RE.match(ln):
            skipping = True
            continue
        if skipping:
            if not ln.strip():
                skipping = False
            continue
        out.append(ln)
    return '\n'.join(out)


_mobile_conv_id = None
_mobile_conv_lock = threading.Lock()
# ★ v8-①（2026-09-29）：手机对话引擎会话串行锁——手机 chat 走 eng.send() 期间锁住，
#   防两条手机消息并发时 conversation_id 互踩（#977 中转已并发化，必须配这把锁）。
_mobile_engine_lock = threading.Lock()


def _ensure_mobile_conv_id():
    """取（或建）手机远程会话 ID。_save_mobile_chat 与引擎会话切换共用，保证同一条。"""
    global _mobile_conv_id
    with _mobile_conv_lock:
        if not _mobile_conv_id:
            from src.models.database import create_conversation
            _mobile_conv_id = create_conversation(
                title=f"📱 手机远程 · {datetime.now().strftime('%m-%d %H:%M')}")
        return _mobile_conv_id


class _mobile_engine_conversation:
    """★ v8-①：把引擎单例的 conversation_id 临时切到手机远程会话的上下文管理器。
    根因：eng.send() 内部十几处 save_message(self.conversation_id, ...) 自动落库——
    旧版手机对话走引擎后，回答存进了【电脑当前会话】，cloud_link 再手动 _save_mobile_chat
    存进「📱 手机远程」= 同一问答落两遍、电脑对话里冒出手机问答；且手机消息蹭了电脑会话
    的上下文（回答「不一样」的另一半根因）。切会话后引擎自动落库直达手机会话，一劳永逸。
    finally 恢复原会话；异常/引擎不可用一律不碰原值。"""

    def __enter__(self):
        self._locked = _mobile_engine_lock.acquire(timeout=180)   # 等上一次手机问答完
        self._prev = None
        self._switched = False
        if not self._locked:
            return self   # 拿锁失败（极端拥堵）→ 不切换，走旧落库路径兜底
        try:
            from src.core.engine import get_conversation_engine
            eng = get_conversation_engine()
            self._prev = getattr(eng, 'conversation_id', None)
            eng.conversation_id = _ensure_mobile_conv_id()
            self._switched = True
            # ★ A1（2026-10-23）根修手机失忆：引擎单例的 self.history 只在启动时加载一次，
            #   旧版只换 conversation_id 不换记忆 → 手机切到「📱手机远程」会话后 GLM 拿到的
            #   还是旧会话历史 → AI 自己提的「要不要把保险也记上」你答「记上」它却说
            #   「这是新对话的开头」。换会话必须换记忆，恢复时同理换回。
            try:
                eng._load_history()
            except Exception as _lh:
                logger.warning(f"[CloudLink] 手机历史重载失败（继续用旧记忆）: {_lh}")
        except Exception as e:
            logger.warning(f"[CloudLink] 手机引擎会话切换失败（按旧路径落库）: {e}")
            self._switched = False
        return self

    def __exit__(self, *exc):
        if self._locked and self._switched:
            try:
                from src.core.engine import get_conversation_engine
                eng = get_conversation_engine()
                eng.conversation_id = self._prev
                try:
                    eng._load_history()   # ★ A1：恢复电脑会话的同时换回电脑记忆
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"[CloudLink] 手机引擎会话恢复失败: {e}")
        if self._locked:
            _mobile_engine_lock.release()
        return False


def _save_mobile_chat(msg: str, answer: str, img_id=None, photo_note: str = '', tag: str = None):
    """手机远程对话写入会话库，电脑端「对话」里能看到。
    ★ v8-①：只兜底【没走引擎管线】的路径（本地推理兜底/离线语音/拍照看图）——
    引擎路径的落库已由引擎自动完成（会话已切到手机会话），再存就双写了。
    落库失败只记日志，绝不影响手机回复。
    ★ #1199（2026-10-25）根因3 修复：照片本身不进会话历史（base64 太大），追问时
    模型没图没摘要只能否认「不记得」。现在把照片元信息（说明/归属/图库 id）以
    摘要形式嵌进历史消息，追问「我刚才拍的啥」能接上。"""
    try:
        if img_id or photo_note or tag:
            _meta = ['📷 【刚拍了一张照片']
            if photo_note:
                _meta.append(f'内容：{photo_note}')
            if tag:
                _meta.append(f'归属：{tag}')
            if img_id:
                _meta.append(f'已存入图库（图库 id={img_id}，电脑端图片库可查）')
            msg = msg + '\n' + '，'.join(_meta) + '】'
        conv_id = _ensure_mobile_conv_id()
        from src.models.database import save_message
        # ★ #1304（2026-10-29）source 友好化：mobile_relay 裸标识曾被手机端原样渲染
        save_message(conv_id, 'user', msg, metadata={'source': '📱 手机'})
        save_message(conv_id, 'assistant', answer, metadata={'source': '📱 手机'})
        logger.info(f"[CloudLink] 📱 手机对话已落库 conv={conv_id}")
    except Exception as e:
        logger.warning(f"[CloudLink] 手机对话落库失败（不影响回复）: {e}")


# ★ #1544 B2（2026-11-25）物品存入撤销口令：进程内撤销栈（最近 10 条、10 分钟有效）——
# 拍照存入建台账后，手机对话里说「撤销」即删刚建的台账行并解绑照片（照片留图库，
# 标签补出来后可重建）。进程重启即清空（如实报无可撤销，不装傻）。
_STORE_UNDO_STACK = []


def _try_store_undo(msg: str):
    """★ #1544 B2 撤销口令拦截：短句含「撤销/撤回」且不涉记账（#1379 通道不抢）
    → 删撤销栈最近一条台账记录。命中返回回执文本，未命中返回 None（放行走引擎）。"""
    m = (msg or '').strip()
    if not m or len(m) > 12:
        return None
    if '撤销' not in m and '撤回' not in m:
        return None
    if any(k in m for k in ('入账', '记账', '账单')):
        return None   # 记账类撤销归 #1379 通道
    now = time.time()
    while _STORE_UNDO_STACK and now - _STORE_UNDO_STACK[-1].get('ts', 0) > 600:
        _STORE_UNDO_STACK.pop()   # 过期户口清掉
    if not _STORE_UNDO_STACK:
        return None
    it = _STORE_UNDO_STACK.pop()
    try:
        from src.core.panel_manager import get_panel_manager
        get_panel_manager().delete_panel_data(it['panel'], int(it['rid']))
        # 照片解绑：panel_images.record_id 清空（照片保留，等补标后可重建台账）
        try:
            if it.get('pid'):
                from src.core import panel_images as _pi
                c = _pi._conn()
                try:
                    c.execute('UPDATE panel_images SET record_id=NULL WHERE id=? AND record_id=?',
                              (int(it['pid']), int(it['rid'])))
                    c.commit()
                finally:
                    c.close()
        except Exception:
            pass
        logger.info(f"[CloudLink] #1544 撤销存入 {it['panel']}#{it['rid']}（口令触发）")
        return (f"👌 已撤销刚才存入的台账记录（id={it['rid']}），照片还在图库里。"
                '下次说「撤销」也只撤最近一条。')
    except Exception as _ue:
        logger.warning(f"[CloudLink] #1544 撤销存入异常: {_ue}")
        return '⚠️ 撤销没成功，请去电脑端对应面板里手动删那条记录。'


def _try_mobile_accounting_confirm(msg: str):
    """★ A2（2026-10-23）手机凭证确认闭环：拍照凭证进待确认队列后，手机上没有收纳盒，
    直接在对话里回「记上 / 确认入账」即入库（confirm_pending → insert_records 同管线，
    查重/分类兜底/花销目标钩子全继承）；回「别记 / 取消」即作废。
    ★ #1379（2026-11-02 定版拍板）两级豁免 + 撤销：
      B1 「别记/这是对话」= 只作废当次，关键词以后照常识别（回执明示，主动权在用户）
      B2 「以后别识别 X」= 才把词拉进黑名单（明说「以后」才动规则）
      D  「撤销刚才入账」= 删掉最近一次确认入库的台账行（10 分钟内）
    只在存在记账类（domain=accounting*）待确认条目时才拦截确认/作废，否则原样放行走引擎。
    返回回答文本（命中），None（未命中，走正常对话）。"""
    m = (msg or '').strip()
    if not m or len(m) > 30:          # 长句不拦（防误伤正常聊天的「记上一次你说…」）
        return None
    _no_kw = ('别记', '不用记', '不要记', '先不记', '不入账', '别入账', '取消', '作废',
              '这是对话', '不是账单', '不要记录', '别识别', '不用识别', '别管这张')
    _yes_kw = ('确认入账', '确认记账', '确认入库', '入账', '记上', '记吧', '记下来', '确认')
    _undo_kw = ('撤销入账', '撤销刚才', '撤销记账', '记错了，撤', '刚记错了')
    from src.core import panel_assist as _pa
    # ── B2：明说「以后」才动规则——把提到的触发词/自定义词拉黑 ──
    if len(m) <= 30 and any(w in m for w in ('以后', '之后')) \
            and any(w in m for w in ('别', '不要', '不再', '不用')) \
            and any(w in m for w in ('识别', '记', '录')):
        try:
            _receipt_kw = ('菜单', '发票', '小票', '票据', '收据', '账单', '记一笔', '记账')
            words = [k for k in _receipt_kw if k in m and k != '记账']
            # 自定义词兜底：去掉指令虚词后的残留（2~8 字才算词；语气词一并剥掉——
            #   「账单了」这种带「了」的脏词实锤进过黑名单，全面检测抓到）
            _res = re.sub(r'[，。？！?!、\s]|以后|之后|这个|那个|的|词|再|都|给|我|别|不要|不再|不用|识别|记录|记账|录入|听见|听懂|了|吧|呢|啊|呀|哦|嘛|哈', '', m)
            if 2 <= len(_res) <= 8 and not any(c.isdigit() for c in _res):
                words.append(_res)
            if words:
                added = _pa.add_receipt_blocklist(list(dict.fromkeys(words)))
                if added:
                    return (f'🚫 好的，「{"」「".join(added)}」已拉黑，之后见到这'
                            f'{"些词" if len(added) > 1 else "个词"}不再自动识别记账。'
                            f'想恢复的话去「设置 → 自动化 → 误识别黑名单」移除即可。')
        except Exception as _ble:
            logger.warning(f"[CloudLink] #1379 黑名单指令异常（放行）: {_ble}")
    # ── D：撤销最近一次确认入账（不依赖有待确认卡） ──
    if any(k in m for k in _undo_kw):
        try:
            ok, note = _pa.undo_last_posted()
            return note if ok else ('⚠️ ' + note)
        except Exception as _ue:
            logger.warning(f"[CloudLink] #1379 撤销入账异常: {_ue}")
    try:
        pend = [p for p in (_pa.list_pending() or [])
                if str(p.get('domain', '')).startswith('accounting')]
        if not pend:
            return None
        latest = pend[0]              # 只动最新一条记账待确认，其余条目不碰
        if any(k in m for k in _no_kw):
            _pa.delete_pending(latest['id'])
            logger.info(f"[CloudLink] 📱 手机作废凭证待确认 #{latest['id']}（#1379 当次豁免，关键词不动）")
            # ★ #1379-B1 回执明示豁免范围：这次不记 ≠ 关键词失效
            return ('👌 好的，这条作废了，不入账。'
                    '「发票」「记账」这些词以后还是要正常识别的——下次拍凭证说一声我就照常记；'
                    '要是想让某个词永远不识别，说「以后别识别 XX」')
        if any(k in m for k in _yes_kw):
            ok, note = _pa.confirm_pending(latest['id'])
            if not ok:
                return '⚠️ 入账没成功：' + str(note)[:100]
            _sum = []
            for r in (latest.get('records') or [])[:5]:
                _item = r.get('项目') or r.get('名称') or '消费'
                _amt = r.get('金额', '')
                _sum.append(f'{_item} {_amt} 元'.strip())
            _head = '✅ 已入账：' + ('；'.join(_sum) if _sum else '完成')
            # ★ #1379-D 回执带撤销方式
            return _head + (f'（{note}）' if note and len(note) < 40 else '') + '。记错了就说「撤销刚才入账」'
    except Exception as e:
        logger.warning(f"[CloudLink] 手机凭证确认拦截异常（放行走引擎）: {e}")
    return None


def _transcribe_mobile(audio_b64: str, fmt: str = 'webm') -> str:
    """#815：手机录音（base64）→ 临时文件 → 本地 Whisper 转文字。
    手机浏览器 MediaRecorder 一般出 audio/webm(opus) 或 audio/mp4，faster-whisper
    底层用 PyAV 解码，两种都能吃。"""
    import base64 as _b64
    import tempfile
    path = None
    try:
        ext = 'webm' if 'webm' in fmt.lower() else ('mp4' if 'mp4' in fmt.lower() or 'm4a' in fmt.lower() else 'wav')
        raw = _b64.b64decode(audio_b64)
        fd, path = tempfile.mkstemp(suffix=f'.{ext}', prefix='hv_voice_')
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
        from src.core.voice_input import get_voice_input
        text = str(get_voice_input().transcribe(path, language='zh') or '').strip()
        # whisper base 小模型中文常出繁体，转简体（zhconv 纯离线）
        try:
            from zhconv import convert as _zh
            text = _zh(text, 'zh-cn')
        except Exception:
            pass
        # ★ #929-A4：暂存最近一次转写，手机端「存下来」直接入库
        try:
            from src.core.voice_archive import stash_last_transcription
            stash_last_transcription(text)
        except Exception:
            pass
        return text
    except Exception as e:
        logger.error(f"[CloudLink] 语音转文字失败: {e}")
        return ''
    finally:
        if path:
            try:
                if 'hv_voice_' in str(path):
                    os.remove(path)
            except Exception:
                pass


# ══════════ r30（2026-10-10）确认卡用途入库：tag→面板解析 + 照片补进 panel_images ══════════
_TAG_PANEL_HINTS = {
    'fridge': ('冰箱', '食材', '食物', 'fridge', 'food'),
    'account': ('记账', '账单', '消费', '开支', 'account', 'bill'),
    'learn': ('学习', '作业', '卷子', 'study', 'learn', 'homework'),
}


def _resolve_panel_for_tag(tag: str) -> str:
    """确认卡用途 → panel_id：tag 是已装面板 id 直接用；否则按关键词在面板清单里猜；猜不中落 'mobile'。"""
    try:
        from src.core.panel_manager import get_panel_manager
        panels = get_panel_manager().get_all_panels() or []
        for p in panels:
            pid = str(p.get('id', '') if isinstance(p, dict) else getattr(p, 'id', ''))
            if pid == tag:
                return pid
        hints = _TAG_PANEL_HINTS.get(tag, (tag,))
        for p in panels:
            if isinstance(p, dict):
                hay = f"{p.get('id', '')} {p.get('name', '')}".lower()
                pid = str(p.get('id', ''))
            else:
                hay = f"{getattr(p, 'id', '')} {getattr(p, 'name', '')}".lower()
                pid = str(getattr(p, 'id', ''))
            if pid and any(h.lower() in hay for h in hints):
                return pid
    except Exception as e:
        logger.debug(f'[CloudLink] tag→面板解析跳过: {e}')
    return 'mobile'


def _extract_photo_note(raw_note, question):
    """★ #1156 照片说明根修：说明=用户自己的那句话，绝不是给 AI 的指令原文。
    优先级：手机新版随 photo 另发的 note 字段 > 旧版页面从 question 里提取「说明：…」
    > 指令式问句判非说明（留空让 AI 起名）> 普通问句原样当说明。"""
    n = (raw_note or '').strip()
    if n:
        return n[:200]
    q = (question or '').strip()
    if not q or q == '看看这张照片':
        return ''
    m = re.search(r'说明[：:]\s*(.+?)(?:，该记成|，看不清|，金额|$|。)', q)
    if m and m.group(1).strip():
        return m.group(1).strip()[:200]
    # 指令式问句（handlePhotoKey 拼的那种）不含用户说明 → 不拿它当名字
    if q.startswith(('把这张照片', '把照片', '帮我记', '记一笔')):
        return ''
    return q[:200]


_LOC_WORDS = ('主卧', '次卧', '卧室', '客厅', '餐厅', '卫生间', '洗手间', '厨房', '阳台', '书房',
              '车库', '储物间', '玄关', '冰箱', '冷冻', '冷藏', '药箱', '床头', '柜子', '抽屉', '地下室')

# ★ #1544（2026-11-25）A1 定版：标签→台账字段映射的泛词黑名单——
# 这些词当「物品名称」没有信息量（实锤 id=30：名称抄成「物品」），映射时跳过
_GENERIC_TAG_WORDS = ('物品', '东西', '物件', '物体', '杂物', '日常', '生活', '其他', '其它',
                      '物品存放', '未分类', '截图', '照片', '已压缩', '已归档')


def _tags_to_record_fields(panel_id: str, tags: list) -> dict:
    """★ #1544 A1：快速档/VLM 标签 → 台账字段映射（本地毫秒级，纯词典无网络）。

    规则（老谭拍板 A1）：
    - 名称主字段 = 首个「具体标签」：跳过泛词（物品/东西…）、跳过场所词（书房是位置不是名字）
    - 位置·房间 = 标签里的场所词（书房/客厅/厨房…_LOC_WORDS 词典）
    - 其余标签拼进备注（保留识别全貌，如「抽纸 · 纸巾/桌面」）
    没有可用名称时返回 {}（调用方自行决定不建/等补标）。
    """
    if not tags:
        return {}
    tags = [str(t).strip() for t in tags if str(t).strip()]
    loc = next((t for t in tags if t in _LOC_WORDS), '')
    name_cands = [t for t in tags
                  if t != loc and t not in _GENERIC_TAG_WORDS
                  and not t.startswith(('🏷', '⏳'))]
    if not name_cands:
        return {}
    note_rest = [t for t in tags
                 if t != name_cands[0] and t != loc and t not in _GENERIC_TAG_WORDS]
    d = {'__name__': name_cands[0]}
    if loc:
        d['__loc__'] = loc
    if note_rest:
        d['__note__'] = '/'.join(note_rest[:4])
    return d


def _photo_panel_record(panel_id: str, note: str, tags=None) -> int:
    """★ #1159（2026-10-17）任务1：存面板照片同步建记录——实锤缺口：手机「存物品」只挂照片
    （record_id=None 孤儿照片），面板记录表永远空 → 电脑端「手机录了电脑没有」。
    解析（本地毫秒级）：说明整句当名称主字段；数量/位置词典正则顺手抽。
    ★ #1544（2026-11-25）A1 定版：识别标签优先——tags 非空时走 _tags_to_record_fields 映射
    （名称=首个具体标签、位置·房间=场所标签、其余标签进备注），修实锤 id=30
    「名称抄成『物品』、书房没进位置、照片没挂上」。tags 无可用名称再退说明解析旧链。
    查不到面板或插失败返回 0（不挡照片入库）。"""
    if not panel_id:
        return 0
    # ── ★ #1544 A1：标签映射优先 ──
    if tags:
        try:
            from src.core.panel_manager import get_panel_manager
            pm = get_panel_manager()
            p = pm.get_panel(panel_id)
            if not p:
                return 0
            pd = p if isinstance(p, dict) else (p.__dict__ if hasattr(p, '__dict__') else {})
            fnames = [f.get('name', '') for f in (pd.get('fields') or [])]
            tf = (pd.get('title_field') or '')
            name_field = tf if tf in fnames else next(
                (x for x in ('名称', '物品名称', '物品', '内容', '事项', '项目', '标题', '药名', '菜名') if x in fnames), '')
            if not name_field:
                name_field = next((x for x in fnames if x and ('名称' in x or '名字' in x)), '')
            if not name_field:
                return 0
            m = _tags_to_record_fields(panel_id, tags)
            if m.get('__name__'):
                data = {name_field: m['__name__']}
                loc_f = next((x for x in fnames if x and '位置' in x and x != name_field), '')
                if m.get('__loc__') and loc_f:
                    data[loc_f] = m['__loc__']
                bf = next((x for x in fnames if x and '备注' in x and x != name_field), '')
                if m.get('__note__') and bf:
                    data[bf] = m['__note__']
                saved = pm.insert_panel_data(panel_id, data)
                rid = int(saved.get('id') or 0) if isinstance(saved, dict) else 0
                if rid:
                    logger.info(f'[CloudLink] #1544 标签映射建台账 {panel_id}#{rid}: {data}')
                return rid
            # 标签全是泛词（认不出主体）→ 返回 -1 语义「等补标补建」，不建残记录
            logger.info(f'[CloudLink] #1544 标签无可用名称（{tags[:3]}），不建残记录等补标 panel={panel_id}')
            return -1
        except Exception as e:
            logger.debug(f'[CloudLink] #1544 标签映射建台账失败（退说明链）: {e}')
    # ── 旧链：说明整句解析（保留兜底）──
    if not (note or '').strip():
        return 0
    try:
        from src.core.panel_manager import get_panel_manager
        pm = get_panel_manager()
        p = pm.get_panel(panel_id)
        if not p:
            return 0
        pd = p if isinstance(p, dict) else (p.__dict__ if hasattr(p, '__dict__') else {})
        fields = pd.get('fields') or []
        fnames = [f.get('name', '') for f in fields]
        import re as _re_rec
        note_t = note.strip()[:60]
        # 名称主字段：title_field 优先 → 常见精确名 → 含「名称/名字」的第一个字段
        #   （实锤：home_items 的主字段叫「物品名称」，写死「名称」会空转）
        tf = (pd.get('title_field') or '')
        name_field = tf if tf in fnames else next(
            (x for x in ('名称', '物品名称', '物品', '内容', '事项', '项目', '标题', '药名', '菜名') if x in fnames), '')
        if not name_field:
            name_field = next((x for x in fnames if x and ('名称' in x or '名字' in x)), '')
        if not name_field:
            return 0
        m_qty = _re_rec.search(r'(\d+(?:\.\d+)?)\s*(支|个|瓶|盒|片|包|罐|台|本|件|袋|卷|桶|剂)', note_t)
        loc = next((w for w in _LOC_WORDS if w in note_t), '')
        nm = note_t
        if m_qty and note_t[:m_qty.start()].strip():
            nm = note_t[:m_qty.start()].strip()
        if loc and nm.endswith(loc):
            nm = nm[:-len(loc)].strip()
        data = {name_field: (nm or note_t)}
        qf = next((x for x in fnames if x and '数量' in x), '')
        if qf and m_qty:
            data[qf] = m_qty.group(1) + m_qty.group(2)
        lf = next((x for x in fnames if x and '位置' in x and x != name_field), '')
        if lf and loc:
            data[lf] = loc
        saved = pm.insert_panel_data(panel_id, data)
        rid = saved.get('id') if isinstance(saved, dict) else getattr(saved, 'id', None)
        rid = int(rid or 0)
        if rid:
            logger.info(f'[CloudLink] #1159 照片建面板记录 {panel_id}#{rid}: {data}')
        return rid
    except Exception as e:
        logger.debug(f'[CloudLink] 照片建面板记录跳过({panel_id}): {e}')
        return 0


def _link_photo_record(pid: int, record_id: int):
    """★ #1159 照片挂到记录（panel_images.record_id 回填）。失败静默。"""
    if not pid or not record_id:
        return
    try:
        from src.core import panel_images
        c = panel_images._conn()
        try:
            c.execute('UPDATE panel_images SET record_id=? WHERE id=?', (int(record_id), int(pid)))
            c.commit()
        finally:
            c.close()
    except Exception as e:
        logger.debug(f'[CloudLink] 照片挂记录跳过(pid={pid}): {e}')


def _save_phone_photo(img_b64: str, note: str, tag: str, quality: str):
    """r30：确认卡带用途的照片补进 panel_images 照片库（带 tag/quality），失败绝不影响看图回答。
    ★ 照片链 C2（2026-10-11）：返回 save_image 结构（含 id）——手机原图补传按这个 id 定位；失败返回 None。"""
    try:
        from src.core import panel_images
        r = panel_images.save_image(
            _resolve_panel_for_tag(tag), img_b64, record_id=None,
            note=note[:200], member='手机', tag=tag, quality=quality)
        if r:
            logger.info(f'[CloudLink] 手机照片已入照片库 tag={tag} quality={quality or "未标记"} id={r["id"]}')
        return r
    except Exception as e:
        logger.debug(f'[CloudLink] 手机照片入库跳过: {e}')
        return None


def _photo_answer(img_dataurl_or_b64: str, question: str, mode: str = '') -> str:
    """#815：手机照片 → 看图回答。
    ★ 2026-09-24 路由改造（定版拍板 A方案）：识别方跟随手机远程当前模式（_mobile_query_mode），
    不再强制本地——
    · 外脑模式 → 图发给当前外脑（须支持视觉，不支持给提示，不偷偷换本地）
    · 本地模式 → 本地视觉模型（当前模型能看图直接用）；没有 → 提示装视觉模型（如 qwen3-vl:8b-instruct）或切外脑
    识别在家（电脑端）完成，Hub 只转发文字；图片是否出网由用户模式决定，用户自己掌控隐私。
    ★ #955：轻页模式开关——mode='local' 强制本地 / 'cloud' 强制外脑 / 空=跟随电脑当前模型。"""
    # ★ #1320 对话优先闸门：手机看图是前台动作，后台 GPU 任务让路
    try:
        from src.core import dialog_gate as _dg
        _dg.mark_active()
    except Exception:
        pass
    import base64 as _b64
    import re as _re
    import tempfile
    path = None
    try:
        m = _re.match(r'^data:image/[a-zA-Z+]+;base64,(.+)$', img_dataurl_or_b64, _re.S)
        raw = _b64.b64decode(m.group(1) if m else img_dataurl_or_b64)
        fd, path = tempfile.mkstemp(suffix='.jpg', prefix='hv_photo_')
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)

        from src.core.image_processor import normalize_image_for_vision
        # ★ 2026-09-23 大图归一化：相机原图 4MB+ Ollama 静默丢图（minicpm-v 答「无法提供」）
        path = normalize_image_for_vision(path)
        text = question or '请描述这张照片的内容。'

        # ── ★ 2026-09-24 路由决策：跟随手机远程当前模式（★ #955 轻页可显式指定） ──
        eff_mode = mode if mode in ('local', 'cloud') else _mobile_query_mode()
        # ★ #1241（2026-10-27）：HarvenEye 是本地混合模型引擎不是外脑——
        #   current_model=harveneye 时绝不能进外脑分支（PROVIDERS 查无此号 → KeyError 'base_url' 实锤），
        #   一律改走本地分支（下方本地分支对 harveneye 引擎接 Eye 带图对话，合规「选谁用谁」铁律）
        try:
            from src.core.model_manager import EYE_MODEL_ID
            if eff_mode in ('direct', 'cloud'):
                from src.core.multi_model_manager import get_multi_model_manager
                _mmx0 = get_multi_model_manager()
                if _mmx0 and (getattr(_mmx0, 'current_model', '') or '') == EYE_MODEL_ID:
                    eff_mode = 'local'
        except Exception:
            pass
        if eff_mode == 'direct':
            from src.core.multi_model_manager import get_multi_model_manager, is_local_model
            mmx = get_multi_model_manager()
            ext_id = (getattr(mmx, 'current_model', '') or '') if mmx else ''
            # ★ #1309 本地判定走唯一真家（原手抄清单漏 harveneye，同款坑一次清）
            if is_local_model(ext_id):
                return ('⚠️ 外脑模式但没有选择外脑模型。\n'
                        '请到电脑端 设置→模型管理 选择外脑，或切回 🏠本地模式 再拍。')
            try:
                mname = (mmx.config.get('models', {}).get(ext_id, {}) or {}).get('model_name', '') or ''
            except Exception:
                mname = ''
            # ★ 2026-09-26 零门槛直发（定版拍板）：删关键词预判，能不能看由外脑自己说；报错转译人话
            with open(path, 'rb') as _f:
                _b = _b64.b64encode(_f.read()).decode()
            messages = [
                {'role': 'system', 'content': '你是哈文，本地AI助手。用户用手机发来了照片，请用中文、口语化地回答关于照片的问题。'
                 '★ 事实背景（#1243 修正）：这张照片已由系统按用户拍照时选择的用途自动保存入库，不需要用户再做操作。'
                 '你负责描述照片内容、回答关于照片的问题，帮用户确认拍到了什么；用户问存没存上，如实说已按所选用途存好。'
                 '★ 防编造：你本身没有工具——不要声称「我来帮你存/我执行了记录」这类自己动手的操作，入库是系统链路自动完成的，不是你做的。'
                 '只用你能从照片里真实看到的内容回答；看不清/不确定就直说不确定，不要猜。'
                 '★ 速度铁律：回答控制在 60 字以内，口语化，别写长段落——手机拍照问答要快。'},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': text},
                    {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{_b}'}},
                ]},
            ]
            # ★ #1265（2026-10-29）：模型改显式传参，不再临时换血 mm.current_model——
            #   旧写法在整段流式消费期间（数秒~十几秒）全局 current_model 被换成 ext_id，
            #   并发的批改/其他会话读 mm 拿到临时值 → 14:02 批改误报「不支持看图」实锤
            full = ''
            for evt in mmx.generate_response(messages, stream=False, model=ext_id):
                t = evt.get('type', '') if isinstance(evt, dict) else ''
                c = evt.get('content', '') if isinstance(evt, dict) else ''
                if t == 'text':
                    full += c
                elif t == 'error':
                    from src.core.image_processor import translate_mobile_vision_error
                    return translate_mobile_vision_error(c, mname or ext_id)
            return _clean_answer(full or '（外脑没有返回内容）')

        # ── 🏠 本地看图分支 ──
        # ★ #1204（2026-10-25 开发者死命令）：只准用用户选的本地主模型，严禁扫描代选
        from src.core.model_manager import create_from_config
        from src.core.image_processor import build_image_message, model_supports_vision, installed_vision_models
        mm = create_from_config()
        vision_model = (getattr(mm, 'model', '') or '').strip()
        _cur_name = vision_model or '（未选）'

        # ★ #1241（2026-10-27）：用户当前选的主模型 = HarvenEye → 直接走 Eye 带图对话
        #   （Eye /chat 同机读图零传输；选谁用谁铁律，不回退不代选。
        #   ★ #1267：判定升级 eye_client.is_selected() 双源（yaml OR mm），五处看图分支统一）
        try:
            from src.core import eye_client
            if eye_client.is_selected():
                eye_msgs = [
                    {'role': 'system', 'content': '你是哈文，本地AI助手。用户用手机发来了照片，请用中文、口语化地回答关于照片的问题。'
                     '★ 事实背景（#1243 修正）：这张照片已由系统按用户拍照时选择的用途自动保存入库，不需要用户再做操作。'
                     '你负责描述照片内容、回答关于照片的问题，帮用户确认拍到了什么；用户问存没存上，如实说已按所选用途存好。'
                     '★ 防编造：你本身没有工具——不要声称「我来帮你存/我执行了记录」这类自己动手的操作，入库是系统链路自动完成的，不是你做的。'
                     '只用你能从照片里真实看到的内容回答；看不清/不确定就直说不确定，不要猜。'
                 '★ 速度铁律：回答控制在 60 字以内，口语化，别写长段落——手机拍照问答要快。'},
                    {'role': 'user', 'content': text},
                ]
                ok, ans = eye_client.chat(eye_msgs, timeout=90.0, image_path=path)
                if not ok:
                    return f'❌ HarvenEye 看图失败：{ans}'
                return _clean_answer(ans)
        except Exception as _e:
            logger.error(f"[CloudLink] HarvenEye 看图通道异常: {_e}")
            return f'❌ HarvenEye 看图失败：{_e}'

        try:
            if not vision_model or not model_supports_vision(vision_model, base_url=mm.base_url):
                vision_model = None
        except Exception:
            vision_model = None
        if not vision_model:
            try:
                _hint = ' / '.join(installed_vision_models(base_url=mm.base_url)[:4])
            except Exception:
                _hint = ''
            return (f'⚠️ 当前本地模型「{_cur_name}」不支持看图。\n'
                    f'1️⃣ 到 设置 → 模型中心 自己换一个能看图的本地模型（已装可选：{_hint or "还没有，需要先安装"}）\n'
                    f'2️⃣ 或切到 🤖外脑模式（选支持看图的外脑），照片发到云端识别')
        messages = [
            {'role': 'system', 'content': '你是哈文，本地AI助手。用户用手机发来了照片，请用中文、口语化地回答关于照片的问题。'
             '★ 事实背景（#1243 修正）：这张照片已由系统按用户拍照时选择的用途自动保存入库，不需要用户再做操作。'
             '你负责描述照片内容、回答关于照片的问题，帮用户确认拍到了什么；用户问存没存上，如实说已按所选用途存好。'
             '★ 防编造：你本身没有工具——不要声称「我来帮你存/我执行了记录」这类自己动手的操作，入库是系统链路自动完成的，不是你做的。'
             '只用你能从照片里真实看到的内容回答；看不清/不确定就直说不确定，不要猜。'
                 '★ 速度铁律：回答控制在 60 字以内，口语化，别写长段落——手机拍照问答要快。'},
            build_image_message(text, [path]),
        ]
        resp = mm.client.chat.completions.create(
            model=vision_model, messages=messages, stream=False, max_tokens=1024)
        return _clean_answer(resp.choices[0].message.content or '')
    except Exception as e:
        logger.error(f"[CloudLink] 看图失败: {e}")
        return f'❌ 看图失败：{e}'
    finally:
        if path:
            try:
                if 'hv_photo_' in str(path):
                    os.remove(path)
            except Exception:
                pass


def _mobile_query_mode() -> str:
    """★ #822：手机远程跟随用户当前模型——选了外脑走 direct，本地/未选走 kb_only。
    旧版调 eng.send() 不传 query_mode → 引擎按旧偏好/默认 kb_only →
    用户明明配了外脑（如 zhipu），手机回答却是「🏠 本地 · qwen2.5:14b」。
    ★ #1309（2026-10-29 重大bug 不可原谅级，开发者定性）：本地集合漏了 'harveneye'
    → 用户选本地被误判成外脑模式 → #1288 顺水推舟换成 GLM（哈文代选模型，
    踩模型选择铁律）。本地判定集合统一走 _is_local_model()，绝不再手抄清单。"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager, is_local_model
        mm = get_multi_model_manager()
        if mm is None:
            return 'kb_only'
        cur = getattr(mm, 'current_model', '') or ''
        return 'kb_only' if is_local_model(cur) else 'direct'
    except Exception:
        return 'kb_only'


def mobile_chat_stream(msg: str, mode: str = ''):
    """★ 2026-10-21 手机端流式（体感对齐豆包）：generator 逐块产出
    {'type': 'status'|'text'|'done', 'content': str}
    - status：FC 轮次/工具执行等过程事件（手机页显示「🔍 …」）
    - text：回答正文的增量块（手机页追加渲染）
    - done：收尾（含完整文本，供容错）
    复用 _mobile_engine_conversation 会话切换 + _mobile_query_mode 模式判定。"""
    try:
        from src.core.engine import get_conversation_engine
        eng = get_conversation_engine()
        qmode = _mobile_query_mode()
        if mode == 'local':
            qmode = 'kb_only'
        elif mode == 'cloud':
            qmode = 'direct'
        collected = []
        round_no = 0
        with _mobile_engine_conversation():
            for ev in eng.send(msg, stream=True, query_mode=qmode):
                t, content = None, ''
                if isinstance(ev, (tuple, list)) and len(ev) >= 2:
                    t, content = ev[0], ev[1]
                elif isinstance(ev, str) and ev.startswith('data: '):
                    try:
                        d = json.loads(ev[6:].strip())
                        t, content = d.get('type'), d.get('content', '')
                    except Exception:
                        continue
                else:
                    continue
                if t in ('content', 'text'):
                    if content:
                        collected.append(str(content))
                        yield {'type': 'text', 'content': str(content)}
                elif t in ('thinking',) and content:
                    yield {'type': 'status', 'content': str(content)[:60]}
                elif t in ('tool_start', 'tool_exec', 'fc_round'):
                    round_no += 1
                    yield {'type': 'status', 'content': f'🔍 第{round_no}步工具调用中…'}
                elif t in ('error',):
                    yield {'type': 'status', 'content': f'⚠️ {str(content)[:60]}'}
                elif t in ('done', 'final'):
                    break
        yield {'type': 'done', 'content': ''.join(collected)}
    except Exception as e:
        yield {'type': 'status', 'content': f'⚠️ {str(e)[:80]}'}
        yield {'type': 'done', 'content': ''}


def _download_remote_image(url: str, tag: str = 'lingzhu'):
    """★ #1400：下载灵珠眼镜拍照（image_url）到临时文件，返回本地路径；失败返回 None。
    限 8MB，仅 http(s)，防意外协议。"""
    try:
        import requests as _rq
        import tempfile
        if not url.startswith(('http://', 'https://')):
            return None
        r = _rq.get(url, timeout=30)
        if r.status_code != 200:
            logger.warning(f'[CloudLink] {tag} 图片下载失败 HTTP {r.status_code}')
            return None
        data = r.content
        if len(data) > 8 * 1024 * 1024:
            logger.warning(f'[CloudLink] {tag} 图片超 8MB，拒收')
            return None
        fd, path = tempfile.mkstemp(suffix='.jpg', prefix='lingzhu_')
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        return path
    except Exception as e:
        logger.warning(f'[CloudLink] {tag} 图片下载异常: {e}')
        return None


# ═══ ★ #1402~#1406（2026-11-02 晚）灵珠眼镜体验批 ═══
# A 口播文本化 / B 过程流推送 / C 照片入图片库 / D 拍照开关 / E Eye 快速档预检
LINGZHU_VOICE_PREFIX = (
    '【眼镜口播模式】这条回答将通过语音播报给戴AR眼镜的用户（无屏幕可看）。'
    '要求：短句大白话，不超过三句；禁止表格/markdown/emoji/括号注记；'
    '报清单类数据（冰箱库存/账单/待办）时逐项顿号口白，'
    '如「鸡蛋6个，还有3天过期；牛奶1盒，明天到期」。')
_relay_ctx = {}     # ★ #1403 req_id -> {'ws': ws, 'loop': loop}，进度通道（B）


def _relay_progress(req_id, note: str):
    """★ #1403（B）眼镜过程流：dispatch 处理线程把中间态推回 Hub（→SSE 中间帧→眼镜）。
    跨线程安全：executor 线程只通过 call_soon_threadsafe 把发送任务丢回事件循环。
    任何失败静默（进度是锦上添花，绝不能影响主回答）。"""
    try:
        ctx = _relay_ctx.get(str(req_id or ''))
        if not ctx:
            return
        payload = json.dumps({'type': 'relay_progress', 'req_id': str(req_id),
                              'data': {'note': str(note or '')[:120]}},
                             ensure_ascii=False, default=str)
        ctx['loop'].call_soon_threadsafe(
            asyncio.ensure_future, ctx['ws'].send(payload))
    except Exception:
        pass


def _voice_clean(s: str) -> str:
    """★ #1402（A）口播清洗：剥掉眼镜端无法展示的 markdown 残留。"""
    import re as _re
    s = _re.sub(r'\|', '，', str(s or ''))
    s = _re.sub(r'[*#`>]+', '', s)
    s = _re.sub(r'\n{2,}', '。', s)
    return s.strip()


def _lingzhu_photo_save_enabled() -> bool:
    """★ #1405（D）眼镜拍照入库总开关，config.lingzhu_photo_save，缺省开。"""
    try:
        from src.utils.config import get_config
        v = get_config().get('lingzhu_photo_save')
        return True if v is None else bool(v)
    except Exception:
        return True


def _chat_via_engine(msg: str, mode: str = '', images=None) -> tuple:
    """走完整对话管线（记忆/知识库/安全链路都保留），收集最终文本。
    引擎不可用时回落本地推理。
    ★ #818 修复：① engine.send 产出 (type, content) 元组（与 web_app 同款），
      旧版误按 SSE 字符串解析导致永远收空 → 手机远程从未真正走引擎管线；
    ② 兼容 SSE 字符串格式（防御）；③ chat_local 返回 (text, backend, error)
      三元组，旧版 str() 整包导致回答被 '(..., ollama:huatuo:latest, ...)' 污染；
    ④ 兜底传 prefer_model（config current_model），不再盲选第一个模型。
    ★ #822：显式传 query_mode（跟随用户当前模型），绝不静默回落本地。
    ★ #955：轻页模式开关——mode='local' 只走本地 / 'cloud' 只走外脑 / 空=跟随电脑当前模型。
    ★ v8-①：返回 (text, engine_saved) 二元组——引擎路径已在 _mobile_engine_conversation
      里把会话切到「📱 手机远程」，引擎自动落库直达手机会话（engine_saved=True，调用方
      别再手动存）；本地兜底路径不落库（engine_saved=False，调用方手动 _save_mobile_chat）。"""
    try:
        from src.core.engine import get_conversation_engine
        eng = get_conversation_engine()
        chunks = []
        errs = []   # ★ #1256：收集引擎明确报错（如「请切回本地模式」引导），不再半路丢弃
        qmode = _mobile_query_mode()
        if mode == 'local':
            qmode = 'kb_only'
        elif mode == 'cloud':
            qmode = 'direct'
        with _mobile_engine_conversation():
            for ev in eng.send(msg, stream=True, query_mode=qmode, images=images):
                t, content = None, ''
                if isinstance(ev, (tuple, list)) and len(ev) >= 2:
                    t, content = ev[0], ev[1]
                elif isinstance(ev, str) and ev.startswith('data: '):
                    try:
                        d = json.loads(ev[6:].strip())
                        t, content = d.get('type'), d.get('content', '')
                    except Exception:
                        continue
                else:
                    continue
                if t in ('content', 'text'):
                    chunks.append(str(content or ''))
                elif t == 'error':
                    errs.append(str(content or ''))
                elif t in ('done', 'final'):
                    break
        text = ''.join(chunks).strip()
        # ★ #1256：引擎没产出正文但有明确报错/引导时，优先透传原文——
        #   旧版只收 text，error 丢弃 → 手机端只显示笼统「没给出有效回答」，
        #   引导文案（如「切回🏠本地模式」）断在半路（#1255 手机实锤）。
        #   引擎侧 #1256 起拒答引导已落库，返回 True 防调用方重复手动落库。
        if not text and errs:
            return errs[-1].strip(), True
        # ★ #1076 B1 双保险：引擎层图片语法按 id 去重——同 id 的 ![..](/api/images/file/N)
        #   只保留首次出现。防引擎对同一回答重复 yield 时（历史双答 bug）拼接出两份
        #   相同图片语法，手机端会把同一张图渲染成两张
        if text and '/api/images/file/' in text:
            import re as _re_img
            _seen_ids = set()

            def _keep_first_img(m):
                _iid = m.group(2)
                if _iid in _seen_ids:
                    return ''
                _seen_ids.add(_iid)
                return m.group(0)

            _dedup_text = _re_img.sub(
                r'(!\[[^\]]*\]\(/api/images/file/(\d+)[^)]*\))', _keep_first_img, text)
            if _dedup_text != text:
                logger.info(f"[CloudLink] #1076 图片语法去重: "
                            f"{text.count('/api/images/file/')} -> {_dedup_text.count('/api/images/file/')}")
                text = _dedup_text
        if text:
            return text, True
        # 引擎没崩但空回复：可能没落库，交调用方走兜底+手动存
        return '', False
    except Exception as e:
        logger.warning(f"[CloudLink] 引擎对话失败，回落本地推理: {e}")
    # 兜底：本地统一推理入口（注意：返回 (text, backend, error) 三元组）
    try:
        from src.core.local_infer import chat_local
        text, backend, err = chat_local(msg, prefer_model=_preferred_local_model())
        if text:
            return str(text), False
        return f'（哈文本地模型暂时不可用：{err}）', False
    except Exception as e:
        return f'（哈文本地模型暂时不可用：{e}）', False


def _preferred_local_model():
    """兜底本地推理的偏好模型：config current_model（剥 ollama: 前缀）→
    model.default_model → 空。
    ★ 2026-09-26 审计：删除内定 qwen2.5:14b 兜底——模型必须用户自选；
    返回空时 local_infer._pick_ollama_model 自动落「用户已装列表的第一个通用模型」
    （用户机器上真实存在的模型，不是哈文内定），一个都没有则如实报错。
    （注释保留历史教训：别盲选列表第一个【华佗医疗模型】——_pick_ollama_model 已排除视觉/嵌入类）"""
    try:
        import yaml
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), 'config.yaml'), 'r', encoding='utf-8') as f:
            c = yaml.safe_load(f) or {}
        cur = str(c.get('current_model', '') or '')
        if cur:
            return cur.split(':', 1)[1] if cur.lower().startswith('ollama:') else cur
        v = str((c.get('model') or {}).get('default_model', '') or '')
        if v:
            return v
    except Exception:
        pass
    return ''


# ══════════════════════════ 长连接客户端（asyncio in daemon thread） ══════════════════════════

_loop = None            # 通道线程的事件循环
_ws_holder = {}         # {'ws': websocket | None}
_thread = None
_stop_event = threading.Event()
_relay_inflight = 0     # ★ 修①（2026-11-12）：在飞 relay 任务计数——长任务磨时心跳不误杀连接


def _relay_outbox_save(payload: dict):
    """★ 修②（2026-11-12）：回复发送失败时落盘留底（最近 50 条）。
    实锤：本地推理磨 → 心跳重连 → 旧 ws 已死 → 回复丢失 → 手机永远转圈。
    留底后可从日志/文件追溯内容，也为后续补发端点备好数据源。"""
    try:
        p = os.path.join(os.path.dirname(STATE_PATH), 'relay_outbox.json')
        box = []
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                box = json.load(f) or []
        box.append({'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), **payload})
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(box[-50:], f, ensure_ascii=False, default=str)
    except Exception:
        pass


def _current_ws():
    return _ws_holder.get('ws')


def _ws_url(server_url: str) -> str:
    u = (server_url or '').rstrip('/')
    if u.startswith('https://'):
        u = 'wss://' + u[len('https://'):]
    elif u.startswith('http://'):
        u = 'ws://' + u[len('http://'):]
    if not u:
        u = 'wss://harvenhub.com'
    return u + '/ws/harven'


def start_cloud_link():
    """在 web_app 启动时调用：enabled=true 才真正起线程。"""
    global _thread
    from src.utils.config import get_config
    cfg = get_config()
    if not bool(cfg.get('cloud_link.enabled', False)):
        logger.info("[CloudLink] 云端通道未开启（cloud_link.enabled=false），不启动")
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _runtime['started_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _thread = threading.Thread(target=_run_thread, name='cloud-link', daemon=True)
    _thread.start()
    logger.info("[CloudLink] 云端通道线程已启动")


def stop_cloud_link():
    _stop_event.set()
    _runtime['connected'] = False
    # ★ 2026-09-22 (#856): 只设标志拔不死旧连接——旧线程可能堵在 async for 收消息里永远醒不来，
    # 线程不死 start_cloud_link 会误判「还在跑」直接早退 → 关了再开永远连不上（僵尸通道，当晚实锤）。
    # 必须主动关掉 ws 把 recv 唤醒，再等旧线程真正退出。
    ws = _ws_holder.get('ws')
    if ws is not None and _loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(ws.close(), _loop).result(timeout=3)
        except Exception:
            pass
    _ws_holder['ws'] = None
    t = _thread
    if t and t.is_alive() and t is not threading.current_thread():
        t.join(timeout=5)
        if t.is_alive():
            logger.warning("[CloudLink] ⚠️ 旧通道线程 5 秒未退出（极端情况），本次先不起新线程，再拨一次开关即可")


def restart_cloud_link():
    """开关切换后重启线程。"""
    stop_cloud_link()
    start_cloud_link()


def _run_thread():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        _loop.run_until_complete(_connect_loop())
    except Exception as e:
        logger.error(f"[CloudLink] 通道线程异常退出: {e}")
        _runtime['connected'] = False
        _runtime['last_error'] = str(e)
    finally:
        try:
            _loop.close()
        except Exception:
            pass


async def _connect_loop():
    """主循环：连接 → 收发 → 断线指数退避重连。"""
    import websockets  # venv 已带 websockets 异步库，零新依赖
    from src.utils.config import get_config
    backoff = 1
    while not _stop_event.is_set():
        cfg = get_config()
        if not bool(cfg.get('cloud_link.enabled', False)):
            _runtime['connected'] = False
            await asyncio.sleep(5)
            continue
        url = _ws_url(cfg.get('cloud_link.server_url', 'https://harvenhub.com'))
        token = _get_harven_token()
        try:
            logger.info(f"[CloudLink] 连接 {url} ...")
            async with websockets.connect(url, open_timeout=10, close_timeout=3,
                                          max_size=2 * 1024 * 1024) as ws:
                _ws_holder['ws'] = ws
                # ① 鉴权报到（★ #946 顺带上报直连地址，手机/APP 试 WLAN/IPv6 直连用）
                try:
                    direct = _detect_direct_cached()
                except Exception:
                    direct = {'lan': [], 'wan6': '', 'port': 7000}
                await ws.send(json.dumps({
                    'type': 'hello', 'token': token, 'name': get_harven_name(),
                    'version': _app_version(),
                    'direct': direct,
                }))
                _runtime['connected'] = True
                _runtime['last_error'] = ''
                _runtime['retries'] = 0
                _runtime['last_connected_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                _runtime['last_pong'] = time.time()
                backoff = 1
                logger.info("[CloudLink] ✅ 已连上 Hub，手机指令可顺线下来")

                # ② 若有待用配对码，连上即补报
                pc = _pair_code_get()
                if pc and time.time() - pc['ts'] < PAIR_CODE_TTL:
                    await ws.send(json.dumps({'type': 'pair_pending', 'code': pc['code'],
                                              'harven_name': get_harven_name()}))

                # ③ 收消息循环（★ 2026-09-22 #856 加应用层心跳：
                #    25 秒没收到任何消息就发一次 {'type':'ping'}，Hub 会回 pong；
                #    70 秒仍无 pong 判定连接已死（假在线），主动断开触发重连——
                #    实锤场景：Hub 端把连接丢了，客户端却一直显示「已连接」骗人）
                while not _stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=25)
                    except asyncio.TimeoutError:
                        # ★ 修①（2026-11-12）：有在飞 relay 任务时心跳死线 70s→300s。
                        #   实锤：本地推理进程内 GGUF 持 GIL 会短暂饿事件循环，
                        #   ping 发不出→70s 无 pong 被误判死线→重连→回复写旧 ws 全丢。
                        #   连接真死（Hub 端挂了）300s 内必然有 TCP 层异常兜底，不会假在线。
                        _dead_line = 300 if _relay_inflight > 0 else 70
                        if time.time() - float(_runtime.get('last_pong', 0)) > _dead_line:
                            raise TimeoutError(f'心跳超时：{_dead_line} 秒无 pong，判定连接已死，重连')
                        try:
                            await ws.send(json.dumps({'type': 'ping', 'ts': time.time()}))
                        except Exception:
                            pass   # ★ 修①：ping 偶发发不出去不立刻炸循环，交给死线判定
                        continue
                    if raw is None:
                        break
                    await _handle_message(ws, raw)
        except asyncio.CancelledError:
            break
        except Exception as e:
            msg = str(e)
            _runtime['connected'] = False
            _runtime['last_error'] = msg
            _ws_holder['ws'] = None
            if not _stop_event.is_set():
                wait = min(backoff, RECONNECT_MAX)
                logger.warning(f"[CloudLink] 连接断开（{msg}），{wait}s 后重连")
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, RECONNECT_MAX)


def _app_version() -> str:
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), 'config.yaml'), 'r', encoding='utf-8') as f:
            import yaml
            c = yaml.safe_load(f) or {}
            return str(c.get('version', ''))
    except Exception:
        return ''


# ══════════════════════════ ★ #946 直连地址探测（三级链路：WLAN → IPv6 → 中转） ══════════════════════════

def _detect_direct() -> dict:
    """探测本机可直连地址，报给 Hub 存着，手机/APP 试直连用。
    · lan：内网 IP（UDP connect 探测，毫秒级、不出网、不发包）
    · wan6：公网 IPv6（问 api6.ipify.org，3 秒超时；没有 IPv6 就空着）
    · port：哈文本机端口（config web.port，默认 7000）
    任何一步失败都不影响主链路——直连只是加速项，中转永远兜底。"""
    out = {'lan': [], 'wan6': '', 'port': 7000}
    try:
        import socket as _sk
        s = _sk.socket(_sk.AF_INET, _sk.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))       # 不发包，只让系统选路由
            out['lan'] = [s.getsockname()[0]]
        finally:
            s.close()
    except Exception:
        pass
    try:
        import requests as _rq
        r = _rq.get('https://api6.ipify.org', timeout=3)
        v6 = (r.text or '').strip()
        if v6 and ':' in v6 and len(v6) <= 45:
            out['wan6'] = v6
    except Exception:
        pass
    try:
        import yaml as _yaml
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), 'config.yaml'), 'r', encoding='utf-8') as f:
            c = _yaml.safe_load(f) or {}
        out['port'] = int(c.get('web', {}).get('port', 7000) or 7000)
    except Exception:
        pass
    return out


# 探测结果缓存（IPv6 运营商可能几天一换，每 10 分钟刷新一次）
_direct_cache = {'at': 0.0, 'data': None}
_DIRECT_TTL = 600


def _detect_direct_cached() -> dict:
    now = time.time()
    if _direct_cache['data'] is None or now - _direct_cache['at'] > _DIRECT_TTL:
        _direct_cache['data'] = _detect_direct()
        _direct_cache['at'] = now
    return _direct_cache['data']


async def _handle_relay(ws, req_id, action, params):
    """★ #977 单条 relay 指令的并发处理：线程池执行 + 回结果。
    实锤（2026-10-04 22:01 开发者）：旧版 recv 循环逐条 await，一条对话 FC 磨 2 分钟，
    期间手机语音转写/第二条消息全部排队堵死（「转文字发不出/点击录音的感觉」）。
    现每条指令独立后台任务互不堵塞；websockets.send 并发调用安全（库内部有写锁）。
    ★ #1403（B）：注册进度通道 ctx + 把 req_id 塞进 params 副本，
    lingzhu_chat 处理线程可用 _relay_progress() 发中间态（→Hub→SSE 中间帧→眼镜）。"""
    try:
        loop = asyncio.get_event_loop()
        params = dict(params or {})
        params['_relay_req_id'] = str(req_id or '')
        _relay_ctx[str(req_id or '')] = {'ws': ws, 'loop': loop}
        global _relay_inflight
        _relay_inflight += 1   # ★ 修①：在飞计数（心跳宽限判定用）
        try:
            result = await loop.run_in_executor(None, _dispatch, action, params)
        finally:
            _relay_inflight -= 1
            _relay_ctx.pop(str(req_id or ''), None)
        ok = 'error' not in (result or {})
        # ★ 修②（2026-11-12）：回复发「当前活动连接」而非捕获的旧 ws。
        #   实锤×9：长任务磨完时连接可能已因心跳重连换新，写旧 ws 必炸
        #   （sent 1000; then received 1000）→ 回复丢失 → 手机永远转圈。
        payload = {'type': 'result', 'req_id': req_id,
                   'ok': bool(ok), 'data': result}
        cur = _ws_holder.get('ws')
        target = cur if cur is not None else ws
        sent = False
        try:
            await target.send(json.dumps(payload, ensure_ascii=False, default=str))
            sent = True
        except Exception:
            pass
        if not sent and cur is not None and cur is not ws:
            try:   # 新连接也炸 → 退回旧连接再试一次
                await ws.send(json.dumps(payload, ensure_ascii=False, default=str))
                sent = True
            except Exception:
                pass
        if not sent:
            _relay_outbox_save(payload)
            logger.warning(f"[CloudLink] 指令 {action or '?'} 回复两次发送均失败，已落 outbox 留底（req_id={req_id}）")
    except Exception as e:
        logger.error(f"[CloudLink] 指令 {action or '?'} 处理异常: {e}")
        try:
            payload = {'type': 'result', 'req_id': req_id,
                       'ok': False, 'data': {'error': f'处理出错: {e}'}}
            cur = _ws_holder.get('ws')
            await (cur if cur is not None else ws).send(
                json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            _relay_outbox_save({'type': 'result', 'req_id': req_id, 'ok': False,
                                'data': {'error': f'处理出错: {e}'}})
            pass   # 连接已断：留底 outbox（修②），手机端超时兜底（修③）


async def _handle_message(ws, raw):
    """处理 Hub 下发的消息：ping / relay 指令 / 配对完成 / 解绑回执。"""
    try:
        data = json.loads(raw)
    except Exception:
        return
    mtype = data.get('type')

    if mtype == 'ping':
        await ws.send(json.dumps({'type': 'pong', 'ts': time.time()}))

    elif mtype == 'pong':
        _runtime['last_pong'] = time.time()   # ★ 2026-09-22 (#856) 心跳回执

    elif mtype == 'relay':
        req_id = data.get('req_id', '')
        action = str(data.get('action', ''))
        params = data.get('params') or {}
        # ★ #977 并发执行：create_task 丢后台，recv 循环立刻回去收下一条——
        #   对话再慢（FC 磨几分钟）也不堵语音转写/新消息。结果由 _handle_relay 回传。
        asyncio.get_event_loop().create_task(_handle_relay(ws, req_id, action, params))

    elif mtype == 'pair_done':
        # 手机配对成功：Hub 告知设备信息，本地入设备表
        # ★ #946 顺带存 token_hash（哈希单向）——直连模式下哈文本地验手机令牌用
        device_id = str(data.get('device_id', '') or secrets.token_hex(8))
        device_name = str(data.get('device_name', '手机') )[:40]
        token_hash = str(data.get('token_hash', ''))[:64]
        with _lock:
            st = _load_state()
            devs = st.setdefault('devices', [])
            if not any(d.get('id') == device_id for d in devs):
                devs.append({'id': device_id, 'name': device_name,
                             'token_hash': token_hash,
                             'added': datetime.now().strftime('%Y-%m-%d %H:%M')})
            else:
                for d in devs:
                    if d.get('id') == device_id:
                        d['token_hash'] = token_hash   # 重配对刷新哈希
            _save_state(st)
        logger.info(f"[CloudLink] 📱 手机设备已绑定: {device_name} ({device_id})")
        _pair_code_clear()

    elif mtype == 'device_token_rotated':
        # ★ #948：手机在 Hub 轮换了令牌，同步本地哈希——直连鉴权保持有效（旧哈希作废）
        device_id = str(data.get('device_id', ''))
        token_hash = str(data.get('token_hash', ''))[:64]
        with _lock:
            st = _load_state()
            _changed = False
            for d in st.get('devices', []):
                if d.get('id') == device_id and d.get('token_hash') != token_hash:
                    d['token_hash'] = token_hash
                    _changed = True
            if _changed:
                _save_state(st)
        logger.info(f"[CloudLink] 🔑 设备 {device_id} 令牌哈希已同步（直连鉴权保持有效）")

    elif mtype == 'broadcast':
        # ★ #947：Hub 运营公告（发给所有在线哈文）。存本地 + 设置页可见
        msg = str(data.get('message', '')).strip()[:500]
        if msg:
            try:
                bpath = os.path.join(os.path.dirname(STATE_PATH), 'relay_broadcast.json')
                with open(bpath, 'w', encoding='utf-8') as f:
                    json.dump({'message': msg, 'ts': datetime.now().strftime('%Y-%m-%d %H:%M')}, f,
                              ensure_ascii=False)
            except Exception:
                pass
            logger.info(f"[CloudLink] 📢 收到 Hub 公告: {msg[:80]}")

    elif mtype == 'hello_ack':
        logger.info(f"[CloudLink] Hub 报到成功: {data.get('message', '')}")

    elif mtype == 'inbox_hint':
        # ★ #930-B3：Hub 替手机收着的离线照片，上线后取回处理（放线程池，别堵收发循环）
        n = int(data.get('count') or 0)
        logger.info(f"[CloudLink] 📥 Hub 提醒有 {n} 条离线暂存，取回处理…")
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _process_relay_inbox)

    elif mtype == 'error':
        _runtime['last_error'] = str(data.get('message', 'Hub 返回错误'))
        logger.warning(f"[CloudLink] Hub 错误: {_runtime['last_error']}")


def _process_relay_inbox():
    """★ #930-B3（2026-09-30）：取回 Hub 离线暂存并逐条处理。
    照片：入图库（自动打标）+ 看图回答 + 落「手机远程」会话（电脑端可见）。
    任何一条失败不影响其余。"""
    import requests as _rq
    try:
        from src.utils.config import get_config
        cfg = get_config()
        server = (cfg.get('cloud_link.server_url', 'https://harvenhub.com') or '').rstrip('/')
        token = _get_harven_token()
        r = _rq.get(f'{server}/api/relay/inbox', headers={'Authorization': f'Bearer {token}'}, timeout=30)
        data = r.json() if r.status_code == 200 else {}
        items = data.get('items') or []
        if not items:
            return
        for it in items:
            try:
                action = str(it.get('action', ''))
                params = it.get('params') or {}
                if action == 'photo':
                    _inbox_handle_photo(params)
                elif action == 'voice_text':
                    text = _transcribe_mobile(str(params.get('audio', '')), str(params.get('format', 'webm')))
                    if text:
                        _save_mobile_chat(f'🎤 {text}', '（这条语音是你不在家时发的，已转成文字收进会话）')
                else:
                    logger.warning(f"[CloudLink] 暂存里有不认识的 action={action}，跳过")
            except Exception as e:
                logger.error(f"[CloudLink] 暂存 #{it.get('id')} 处理失败（不影响其余）: {e}")
        logger.info(f"[CloudLink] ✅ 离线暂存处理完成 {len(items)} 条")
    except Exception as e:
        logger.error(f"[CloudLink] 取回离线暂存失败: {e}")


def _inbox_handle_photo(params: dict):
    """离线照片：入图库 + 看图回答 + 落手机远程会话"""
    img = str(params.get('image', '') or '')
    question = str(params.get('question', '') or '').strip()
    if not img:
        return
    from src.core import img_store
    reg = img_store.register(image_data=img, source='手机离线',
                             note=question or '手机离线照片', auto_tag=True)
    logger.info(f"[CloudLink] 离线照片已入图库 id={reg and reg.get('id')}")
    answer = _photo_answer(img, question or '请描述这张照片的内容，若像账单/票据/需要记账的内容，请给出关键信息。')
    _save_mobile_chat(f'📷 {question or "（离线照片）"}', answer)


# ══════════════════════════ Flask 路由（设置页用） ══════════════════════════

def register_cloud_link_routes(app):
    """在 web_app.py 里调用一次，注册 /api/cloud_link/* 路由。"""

    @app.route('/api/relay/direct/send', methods=['POST', 'OPTIONS'])
    def relay_direct_send():
        """★ #946 直连指令入口：手机/APP 不走 Hub、直接打哈文（WLAN / IPv6 可达时）。
        与 Hub 的 /api/relay/send 同款请求体 {action, params}，同一份指令白名单（_dispatch）。
        鉴权：Bearer device_token 的 sha256 对比本地设备表（配对时 Hub 发来的哈希）。
        没走 Hub = 没有限速/配额，靠 token 硬鉴权 + 白名单兜底；旧配对（无哈希）需重新配对一次。"""
        import hashlib as _hl
        from flask import request, make_response

        def _cors(resp, code=None):
            resp.headers['Access-Control-Allow-Origin'] = '*'
            resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            if code is not None:
                resp.status_code = code
            return resp

        if request.method == 'OPTIONS':
            return _cors(make_response('', 204))
        auth = request.headers.get('Authorization', '')
        token = auth[7:].strip() if auth.startswith('Bearer ') else ''
        th = _hl.sha256(token.encode('utf-8')).hexdigest() if token else ''
        st = _load_state()
        if not th or not any(d.get('token_hash') == th for d in st.get('devices', [])):
            out = make_response(json.dumps({'success': False,
                                            'error': '设备令牌无效（直连模式需要重新配对一次才生效）'}),
                                200)
            out.headers['Content-Type'] = 'application/json; charset=utf-8'
            return _cors(out)
        body = request.get_json(silent=True) or {}
        action = str(body.get('action', '')).strip()
        params = body.get('params') or {}
        result = _dispatch(action, params)   # 白名单在这里，Hub 有的能力直连一个不少
        ok = 'error' not in (result or {})
        payload = {'success': ok, 'data': result} if ok else \
                  {'success': False, 'error': str((result or {}).get('error', '执行失败'))}
        out = make_response(json.dumps(payload, ensure_ascii=False, default=str), 200)
        out.headers['Content-Type'] = 'application/json; charset=utf-8'
        return _cors(out)

    @app.route('/api/relay/ping', methods=['GET'])
    def relay_direct_ping():
        """★ #946 直连探活端点：手机/APP 试「不走 Hub 直连哈文」时打这里。
        只回 pong + 名字，零敏感信息；CORS 全开（跨域直连必需）。
        ⚠️ 开 IPv6 公网直连需在路由器/防火墙放行本端口——只放行此端口，别开别的。"""
        resp = {'pong': True, 'harven_name': get_harven_name()}
        r = json.dumps(resp, ensure_ascii=False)
        import flask
        out = flask.make_response(r, 200)
        out.headers['Content-Type'] = 'application/json; charset=utf-8'
        out.headers['Access-Control-Allow-Origin'] = '*'
        return out

    @app.route('/api/cloud_link/status', methods=['GET'])
    def cloud_link_status():
        try:
            return {'success': True, **get_status()}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @app.route('/api/cloud_link/toggle', methods=['POST'])
    def cloud_link_toggle():
        """开启/关闭云端通道。需管理员密码（与面板卸载同一把锁）。"""
        try:
            from flask import request
            body = request.get_json(silent=True) or {}
            enable = bool(body.get('enabled'))
            pwd = str(body.get('password', ''))
            from src.core.panel_manager import PanelManager
            expected = PanelManager()._get_admin_password()
            if expected and pwd != expected:
                return {'success': False, 'error': '管理员密码不对'}, 403
            from src.utils.config import get_config
            cfg = get_config()
            cfg.set('cloud_link.enabled', enable)
            cfg.save()
            if enable:
                restart_cloud_link()
            else:
                stop_cloud_link()
            return {'success': True, 'enabled': enable}
        except Exception as e:
            return {'success': False, 'error': str(e)}, 500

    @app.route('/api/cloud_link/server', methods=['POST'])
    def cloud_link_server():
        """改中转服务器地址（自建 HarvenRelay Lite 用户用）。
        需管理员密码；留空/传 official 恢复官方默认 harvenhub.com。"""
        try:
            from flask import request
            body = request.get_json(silent=True) or {}
            url = str(body.get('server_url', '')).strip().rstrip('/')
            pwd = str(body.get('password', ''))
            from src.core.panel_manager import PanelManager
            expected = PanelManager()._get_admin_password()
            if expected and pwd != expected:
                return {'success': False, 'error': '管理员密码不对'}, 403
            if url and not (url.startswith('https://') or url.startswith('http://')):
                return {'success': False, 'error': '地址要以 https:// 或 http:// 开头'}, 400
            if len(url) > 200:
                return {'success': False, 'error': '地址太长了'}, 400
            if not url:
                url = 'https://harvenhub.com'   # 留空 = 恢复官方
            from src.utils.config import get_config
            cfg = get_config()
            old = cfg.get('cloud_link.server_url', 'https://harvenhub.com')
            cfg.set('cloud_link.server_url', url)
            cfg.save()
            changed = (old != url)
            if changed and cfg.get('cloud_link.enabled'):
                restart_cloud_link()   # 换服务器了立刻重连新家
            return {'success': True, 'server_url': url, 'changed': changed}
        except Exception as e:
            return {'success': False, 'error': str(e)}, 500

    @app.route('/api/cloud_link/pair/start', methods=['POST'])
    def cloud_link_pair_start():
        try:
            if not _runtime.get('connected'):
                return {'success': False,
                        'error': '哈文还没连上云端（先开启云端通道并等它显示已连接）'}
            return pair_start()
        except Exception as e:
            return {'success': False, 'error': str(e)}, 500

    @app.route('/api/cloud_link/devices', methods=['GET'])
    def cloud_link_devices():
        try:
            st = _load_state()
            return {'success': True, 'devices': st.get('devices', [])}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @app.route('/api/cloud_link/devices/unbind', methods=['POST'])
    def cloud_link_unbind():
        try:
            from flask import request
            body = request.get_json(silent=True) or {}
            device_id = str(body.get('device_id', '')).strip()
            if not device_id:
                return {'success': False, 'error': '缺少 device_id'}
            return device_unbind(device_id)
        except Exception as e:
            return {'success': False, 'error': str(e)}, 500

    logger.info("[Startup] 云端通道路由已注册（/api/cloud_link/*）")
