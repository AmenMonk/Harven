# -*- coding: utf-8 -*-
"""对话即录入 API 路由 ★ 2026-09-16
前缀 /api/assist：
  GET  /domains                 全部可录入域
  POST /extract                 结构化抽取（通道②页签对话框，确认卡数据源）
  POST /confirm                 确认入库（可带用户修改后的记录）
  GET  /pending                 待确认队列
  POST /pending/clear           一键清空待确认（可选 panel_id，★ S1 2026-11-12）
  POST /pending/<id>/confirm    待确认入库（可带修改）
  DELETE /pending/<id>          忽略待确认条目
  GET  /pending/count           待确认数量（面板角标）
  GET  /settings  POST /settings  默录开关等设置
  POST /classify                分类调试/首页快捷入口用
"""
import logging
import threading
from datetime import datetime

from flask import Blueprint, jsonify, request

from src.core import panel_assist as pa

logger = logging.getLogger(__name__)

bp = Blueprint('panel_assist', __name__, url_prefix='/api/assist')

# ═══ ★ 2026-09-26 开发者需求：浮窗对话流落库 ═══
# 浮窗历史原本只在浏览器 localStorage（清缓存/换设备就丢）。现在每轮对话同步写入
# 会话库，落成「🪟 面板助手」会话——电脑端「对话」里直接可查，机制同手机远程 #813-B。
# 落库失败只记日志，绝不影响浮窗回复。
_pa_conv_id = None
_pa_conv_lock = threading.Lock()


def _assist_reply_text(mode, payload, count):
    """从 assist_chat 结果里提取一句人话作为落库的助手侧内容"""
    try:
        if mode == 'records':
            return f'（记录）已整理 {count or 0} 条，等你确认入库'
        if isinstance(payload, dict):
            for _k in ('text', 'answer', 'reply', 'message'):
                if payload.get(_k):
                    return str(payload[_k])[:800]
        return str(payload)[:800]
    except Exception:
        return ''


def _save_assist_chat(msg: str, answer: str):
    """浮窗对话写入会话库「🪟 面板助手」；失败只记日志，绝不影响回复"""
    global _pa_conv_id
    if not (msg or '').strip() and not (answer or '').strip():
        return
    try:
        with _pa_conv_lock:
            if not _pa_conv_id:
                from src.models.database import create_conversation
                _pa_conv_id = create_conversation(
                    title=f"🪟 面板助手 · {datetime.now().strftime('%m-%d %H:%M')}")
            conv_id = _pa_conv_id
        from src.models.database import save_message
        save_message(conv_id, 'user', msg, metadata={'source': 'panel_assist'})
        save_message(conv_id, 'assistant', answer, metadata={'source': 'panel_assist'})
        logger.info(f"[PanelAssist] 🪟 浮窗对话已落库 conv={conv_id}")
    except Exception as e:
        logger.warning(f"[PanelAssist] 对话落库失败（不影响回复）: {e}")


@bp.route('/domains', methods=['GET'])
def domains():
    try:
        return jsonify({'success': True, 'domains': pa.list_domains()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/extract', methods=['POST'])
def extract():
    try:
        d = request.get_json(force=True, silent=True) or {}
        text = (d.get('text') or '').strip()
        image_b64 = (d.get('image_b64') or '').strip()
        if not text and not image_b64:
            return jsonify({'success': False, 'error': '请说话或拍张照片'}), 400
        panel_id = d.get('panel_id') or ''
        records, domain, model, err = pa.extract_records(
            text, panel_id, d.get('domain') or '',
            history=d.get('history') or [], image_b64=image_b64)
        if err:
            return jsonify({'success': False, 'error': err}), 200
        if domain == 'home':
            # ★ 家庭空间跨面板模式：records 实为 groups
            return jsonify({'success': True, 'groups': records, 'model': model,
                            'count': sum(len(g.get('records') or []) for g in records)})
        out = {'success': True, 'records': records, 'domain': domain,
               'model': model, 'count': len(records)}
        # ★ 2026-10-11 #1073 C1 健康预警升级：过敏红条 + 忌口/易上火黄条 + 药食冲突红条
        #   （原 #1062 C2 只查过敏，现走 health_alerts 全量档案比对，本地零 token）
        if panel_id == 'meal_diary':
            try:
                from src.core.health_profile import health_alerts, med_food_check
                alerts = []
                for rec in records:
                    blob = (rec.get('识别出的食物') or '') + ' ' + (rec.get('备注') or '')
                    alerts += health_alerts(rec.get('识别出的食物') or '', rec.get('备注') or '')
                    alerts += med_food_check(blob)
                if alerts:
                    out['alerts'] = alerts
            except Exception as _ae:
                logger.debug(f"[PanelAssist] 健康预警比对跳过: {_ae}")
        return jsonify(out)
    except Exception as e:
        logger.error(f"[PanelAssist] extract 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/confirm', methods=['POST'])
def confirm():
    try:
        d = request.get_json(force=True, silent=True) or {}
        panel_id = d.get('panel_id') or ''
        records = d.get('records') or []
        if not panel_id or not records:
            return jsonify({'success': False, 'error': '程序没收到要保存的记录，重说一次试试；反复出现就重启哈文'}), 400
        _img = d.get('image_b64')
        _img = _img.strip() if isinstance(_img, str) else _img  # ★ #1407-A 兼容多图 list
        n, err, note = pa.insert_records(panel_id, d.get('domain') or '', records, d.get('child_id'),
                                         image_b64=_img,
                                         image_quality=(d.get('image_quality') or '').strip(),
                                         image_notes=d.get('image_notes'))  # ★ #1407-A 逐图角色标注
        if err:
            return jsonify({'success': False, 'error': err}), 200
        out = {'success': True, 'inserted': n}
        if note:
            out['note'] = note  # ★ #913 例：「1 条疑似重复已自动跳过」
        # ★ #995 词典闭环：确认入库后自动沉淀叫法（确认卡=用户亲自确认过的映射）
        try:
            _words = pa._pick_lexicon_words(records)
            if _words:
                pa.add_lexicon(_words, panel_id, d.get('domain') or '', source='confirm')
        except Exception:
            pass
        return jsonify(out)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/pending', methods=['GET'])
def pending():
    try:
        return jsonify({'success': True, 'items': pa.list_pending(request.args.get('panel_id', ''))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/pending/clear', methods=['POST'])
def pending_clear():
    """★ S1（2026-11-12）一键清空待确认收纳盒。body 可选 {panel_id}（只清该面板，空=全清）。
    只删未确认卡，绝不碰已入库数据；前端按钮自带确认弹窗。"""
    try:
        d = request.get_json(silent=True) or {}
        n = pa.clear_pending((d.get('panel_id') or '').strip())
        return jsonify({'success': True, 'cleared': n})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/pending/count', methods=['GET'])
def pending_count():
    try:
        return jsonify({'success': True, 'count': pa.pending_count(request.args.get('panel_id', ''))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/pending/<int:pid>/confirm', methods=['POST'])
def pending_confirm(pid):
    try:
        d = request.get_json(force=True, silent=True) or {}
        ok, msg = pa.confirm_pending(pid, d.get('records'))
        return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/pending/<int:pid>', methods=['DELETE'])
def pending_delete(pid):
    try:
        pa.delete_pending(pid)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/settings', methods=['GET'])
def get_settings():
    try:
        return jsonify({'success': True, 'settings': pa.get_assist_settings()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/settings', methods=['POST'])
def save_settings():
    try:
        d = request.get_json(force=True, silent=True) or {}
        pa.save_assist_settings(d.get('settings') or d)
        return jsonify({'success': True, 'settings': pa.get_assist_settings()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/classify', methods=['POST'])
def classify():
    try:
        d = request.get_json(force=True, silent=True) or {}
        return jsonify({'success': True, 'result': pa.classify_home(d.get('text') or '')})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── ★ 2026-09-18 个人词典（确认卡「记住分法」沉淀，快车道 0 token 命中） ──

@bp.route('/lexicon', methods=['GET'])
def lexicon_list():
    try:
        return jsonify({'success': True, 'items': pa.list_lexicon(request.args.get('panel_id', ''))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/lexicon', methods=['POST'])
def lexicon_add():
    """body: {pattern 或 patterns:[...], panel_id, domain, source}
    也支持 {records:[...], source_text} 自动从记录值里挑叫法（省得前端动脑子）"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        panel_id = d.get('panel_id') or ''
        domain = d.get('domain') or ''
        patterns = d.get('patterns') or ([d['pattern']] if d.get('pattern') else [])
        if not patterns and d.get('records'):
            import re as _re
            skip_keys = {'日期', '时间', '备注', 'notes', 'date', 'note', '金额', '分数', 'score',
                         'minutes', '身高', '体重', 'height', 'weight', 'value'}
            for rec in d.get('records') or []:
                for k, v in (rec or {}).items():
                    v = str(v or '').strip()
                    if k in skip_keys or not v or _re.search(r'\d', v) or len(v) > 6:
                        continue
                    patterns.append(v)
        n = pa.add_lexicon(patterns, panel_id, domain, d.get('source') or 'confirm')
        if not n:
            return jsonify({'success': False, 'error': '没挑出可记的叫法（太长或带数字）'}), 200
        return jsonify({'success': True, 'added': n})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/lexicon/<int:lid>', methods=['DELETE'])
def lexicon_delete(lid):
    try:
        pa.delete_lexicon(lid)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/schema', methods=['GET'])
def schema():
    """确认卡补字段用：总览模式挂载 fields=[]，前端按解析出的域拉字段渲染"""
    try:
        sc = pa.get_domain_schema(request.args.get('panel_id', ''), request.args.get('domain', ''))
        if not sc:
            return jsonify({'success': False, 'error': '未知档案'}), 200
        return jsonify({'success': True, 'schema': {'label': sc.get('label'), 'fields': sc.get('fields', [])}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── ★ 2026-09-18 统一待办聚合（鸭头角标 + 点开处理闭环） ──

@bp.route('/alerts', methods=['GET'])
def alerts():
    """到期提醒 + 聊天待确认 一站聚合。每条带 link（哪来的回哪去）。"""
    try:
        items = []
        try:
            from src.core import home_reminder as hr
            for r in hr.due_reminders(20):
                module = r.get('module') or ''
                link = ''
                if module and module not in ('test', 'chat', 'manual'):
                    link = '/panel/' + module
                items.append({'type': 'reminder', 'id': r.get('id'), 'title': r.get('title', ''),
                              'remind_at': r.get('remind_at', ''), 'repeat': r.get('repeat', ''),
                              'module': module, 'link': link})
        except Exception as e:
            logger.debug(f"[alerts] 提醒聚合失败: {e}")
        pc = pa.pending_count(request.args.get('panel_id', ''))
        # ★ 2026-09-18 物品存放提醒：低库存 + 借出超7天未还（物品分类存放面板）
        try:
            items.extend(pa.storage_alerts())
        except Exception as e:
            logger.debug(f"[alerts] 物品存放提醒聚合失败: {e}")
        # ★ 2026-09-19 错题本提醒：下次重做到期还没掌握（学习方案C）
        try:
            items.extend(pa.mistake_alerts())
        except Exception as e:
            logger.debug(f"[alerts] 错题提醒聚合失败: {e}")
        # ★ 2026-09-19 #791 食物存放提醒：过期滞留+低库存+要补货（冰箱台账）
        try:
            items.extend(pa.fridge_alerts())
        except Exception as e:
            logger.debug(f"[alerts] 食物存放提醒聚合失败: {e}")
        # ★ 2026-09-19 #792 习惯打卡漏卡提醒（成长档案 habits 域）
        try:
            items.extend(pa.habit_alerts())
        except Exception as e:
            logger.debug(f"[alerts] 习惯打卡提醒聚合失败: {e}")
        # ★ 2026-09-19 老人关怀哨兵：血压/不适/心情/吃饭/联系频率（老人模块）
        try:
            items.extend(pa.elder_alerts())
        except Exception as e:
            logger.debug(f"[alerts] 老人关怀哨兵聚合失败: {e}")
        # ★ 2026-10-11 #1073 健康待办聚合：临期药/停药到期/疫苗/下次体检 → 收纳盒⏰全面板可见
        try:
            from src.core.health_profile import health_reminders as _hrem
            for t in _hrem(14):
                items.append({'type': 'reminder',
                              'id': 'health_' + t['date'] + '_' + str(abs(hash(t['text'])) % 100000),
                              'title': t['text'], 'remind_at': t['date'], 'repeat': '',
                              'module': 'health_file', 'link': '/panel/health_file',
                              'health': True})
        except Exception as e:
            logger.debug(f"[alerts] 健康待办聚合失败: {e}")
        return jsonify({'success': True, 'count': len(items) + pc,
                        'reminders': items, 'pending': pc})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/alerts/reminder/<int:rid>/ack', methods=['POST'])
def alert_ack(rid):
    """知道了：本轮消音（complete；重复提醒会算下一次）"""
    try:
        from src.core import home_reminder as hr
        hr.complete_reminder(rid)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/alerts/reminder/<int:rid>', methods=['DELETE'])
def alert_delete(rid):
    try:
        from src.core import home_reminder as hr
        hr.delete_reminder(rid)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/chat', methods=['POST'])
def chat():
    """★ 2026-09-16 对话框 2.0 统一双模入口：
    提醒意图→设提醒(mode=reminder)；问句→检索档案回答(mode=answer)；否则→记录抽取(mode=records)"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        text = (d.get('text') or '').strip()
        img_b64 = d.get('image_b64') or ''
        # ★ 2026-09-23 实测实锤：只发照片被「请说点什么」打回——路由层空文字直接 400，
        # 图根本没送到 assist_chat（内部本来就支持空文字+图走视觉抽取）
        if not text and not img_b64:
            return jsonify({'success': False, 'error': '说点什么，或拍张照片'}), 400
        mode, payload, model, dom, err = pa.assist_chat(
            text, d.get('panel_id') or '', d.get('domain') or '',
            history=d.get('history') or [], extra_context=d.get('extra_context') or '',
            image_b64=img_b64, page_context=d.get('page_context') or '',
            free_chat=bool(d.get('free_chat')))
        if err:
            return jsonify({'success': False, 'mode': mode, 'error': err}), 200
        out = {'success': True, 'mode': mode, 'payload': payload, 'model': model}
        if mode == 'records':
            if d.get('panel_id') == '' and isinstance(payload, list) and payload and payload[0].get('panel_id'):
                out['groups'] = payload
                out['count'] = sum(len(g.get('records') or []) for g in payload)
            else:
                out['records'] = payload
                # ★ 2026-09-18 修复：返回解析后的真实域（原先回显请求参数，空域导致前端 404 无法入库）
                out['domain'] = dom or ''
                out['count'] = len(payload or [])
        # ★ 2026-09-26 对话流落库（🪟 面板助手会话，清缓存/换设备不再丢）
        try:
            _save_assist_chat(
                (f'[照片] {text}' if (img_b64 and not text) else text),
                _assist_reply_text(mode, payload, out.get('count')))
        except Exception:
            pass
        return jsonify(out)
    except Exception as e:
        logger.error(f"[PanelAssist] chat 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/panel-ai', methods=['POST'])
def panel_ai():
    """★ 2026-09-16 features.ai 按钮：面板数据 AI 分析（本地模型，免费）"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        # ★ 2026-09-19 修A兜底：同时接受 panel_id / panelId——旧生成的页面用的是驼峰，
        # 兼容后老页面无需重新生成也能用；修C：400文案人话化+给下一步
        panel_id = d.get('panel_id') or d.get('panelId') or ''
        if not panel_id:
            return jsonify({'success': False, 'error': '程序没拿到模块标识，重启一下哈文通常能修好；还不行到「设置 → 关于」反馈'}), 400
        text, model, err = pa.panel_ai(panel_id, d.get('question') or '')
        if err:
            return jsonify({'success': False, 'error': err}), 200
        return jsonify({'success': True, 'text': text, 'model': model})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/panel-ai/last', methods=['GET'])
def panel_ai_last():
    """★ 2026-09-19 #768 回看某面板最近一次 AI 分析（面板打开时前端拉取展示）"""
    try:
        panel_id = request.args.get('panel_id', '')
        if not panel_id:
            return jsonify({'success': False, 'error': '程序没拿到模块标识，重启一下哈文通常能修好'}), 400
        r = pa.last_ai_result(panel_id)
        return jsonify({'success': True, 'result': r})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── ★ 2026-10-10 #1060~#1062 餐饮洞察三端点（A2 统计 / C1 横幅 / C3 今天吃什么） ──

@bp.route('/meal-stats', methods=['GET'])
def meal_stats_api():
    """近 N 天餐饮统计（外卖次数/金额/常点店/吃辣/热量参考，纯本地确定性）"""
    try:
        from src.core.meal_insights import meal_stats
        try:
            days = min(max(int(request.args.get('days', 30)), 1), 365)
        except ValueError:
            days = 30
        return jsonify({'success': True, 'stats': meal_stats(days)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/meal-alerts', methods=['GET'])
def meal_alerts_api():
    """C1 横幅：外卖频次 × 冰箱临期联动（确定性文案，零 token，页面打开即拉）"""
    try:
        from src.core.meal_insights import takeout_digest
        alerts = []
        tk = takeout_digest(7)
        if tk['busy']:
            alerts.append(f"🛵 近7天点了 {tk['count']} 次外卖（共 ¥{tk['amount']}），外食有点勤，有空自己做两顿")
        try:
            from src.core.fridge_hub import expiry_digest
            lines, n_exp, n_soon = expiry_digest()
            if lines:
                msg = f"🧊 冰箱有 {n_exp} 样过期、{n_soon} 样临期"
                if tk['busy']:
                    msg += "——点外卖前先看一眼冰箱，把临期的今晚吃掉"
                else:
                    msg += "，今晚优先吃临期的"
                alerts.append(msg)
        except Exception:
            pass
        return jsonify({'success': True, 'alerts': alerts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/meal-suggest', methods=['POST'])
def meal_suggest_api():
    """C3 今天吃什么：确定性素材打底（口味历史+冰箱库存+季节），模型只组织人话（跟随阵营，失败退确定性清单）"""
    try:
        from src.core.meal_insights import suggest_materials
        m = suggest_materials()
        fridge_names = '、'.join((f['name'] + (f"（{f['qty']}）" if f['qty'] else '')) for f in m['fridge'][:8]) or '（冰箱没记食材）'
        taste_names = '、'.join(t['food'] for t in m['top_tastes'][:5]) or '（近30天还没记过吃的）'
        # ★ 2026-10-11 #1073 「今天吃什么」参考体质：易上火别吃/不爱吃但要补/忌口一并注入
        health_note = ''
        try:
            from src.core.health_profile import members as _hm
            _ms = _hm()
            _segs = []
            for _x in _ms:
                _s = _x['name']
                if _x.get('avoid_eat'):
                    _s += f"易上火别吃：{_x['avoid_eat']}"
                if _x.get('dislikes'):
                    _s += f"忌口：{_x['dislikes']}"
                if _x.get('need_eat'):
                    _s += f"不爱吃但要补：{_x['need_eat']}"
                if _s != _x['name']:
                    _segs.append(_s)
            if _segs:
                health_note = '\n家人体质注意：\n' + '\n'.join(_segs) + '\n'
        except Exception:
            pass
        fallback = (f"🍂 {m['season']} · 今晚参考：\n"
                    f"冰箱现成的：{fridge_names}\n"
                    f"最近常吃：{taste_names}\n"
                    f"建议：优先做冰箱里快过期的，搭配一个最近没吃的换换口味。")
        try:
            from src.core import panel_assist as pa
            text, model, err = pa._llm_call(
                '你是家庭晚餐参谋，说话简短接地气，直接给建议不空话不绵密。',
                f"现在是{m['season']}，帮我定今晚吃什么。\n"
                f"冰箱现成食材：{fridge_names}\n"
                f"家里最近常吃：{taste_names}\n"
                + health_note +
                f"要求：给 2~3 个今晚方案（主菜+主食思路），每个一两句理由；"
                f"冰箱快过期的优先用掉；最近常吃的可以少推荐，换换花样；"
                f"有体质注意的按着来，别推荐的菜撞上。食材没提到就别硬编。",
                max_tokens=400, temperature=0.5, gate='answer_allow_cloud')
        except Exception:
            text, model, err = None, '', '模型不可用'
        if err or not text:
            return jsonify({'success': True, 'text': fallback, 'model': '本地规则'})
        return jsonify({'success': True, 'text': text.strip(), 'model': model})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── ★ 2026-09-19 #769 定时汇总三件套（手动触发/状态查询） ──

@bp.route('/panel-summary/run', methods=['POST'])
def panel_summary_run():
    """手动跑一个汇总。body: {kind: dream_weekly|accounting_monthly|kid_monthly, force: true}"""
    try:
        from src.core.panel_summary import run_summary, SUMMARY_KINDS
        d = request.get_json(force=True, silent=True) or {}
        kind = (d.get('kind') or '').strip()
        if kind not in SUMMARY_KINDS:
            return jsonify({'success': False,
                            'error': f'kind 要么是这些之一: {", ".join(SUMMARY_KINDS)}'}), 400
        text, model, err = run_summary(kind, force=bool(d.get('force', True)))
        if err:
            return jsonify({'success': False, 'error': err}), 200
        return jsonify({'success': True, 'text': text, 'model': model})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/panel-summary/state', methods=['GET'])
def panel_summary_state():
    """三件套各汇总的上次执行时间与下期时点"""
    try:
        from src.core.panel_summary import summary_state
        return jsonify({'success': True, 'state': summary_state()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── ★ 2026-09-18 分类包分享到 HarvenHub（对话造出的分类一键共享） ──

@bp.route('/share-to-hub', methods=['POST'])
def share_to_hub():
    """把一个面板打包上传 HarvenHub（type=template）。
    body: {panel_id, author?, dry_run?}
    dry_run=true 只做打包校验不上传（测试用）。"""
    import io
    import json as _json
    import tempfile
    import zipfile

    d = request.get_json(force=True, silent=True) or {}
    panel_id = (d.get('panel_id') or '').strip()
    if not panel_id or '/' in panel_id or '\\' in panel_id or '..' in panel_id:
        return jsonify({'success': False, 'error': 'panel_id 不合法'}), 400

    pj = pa._panel_json(panel_id)
    if not pj:
        return jsonify({'success': False, 'error': f'面板不存在: {panel_id}'}), 404
    fields = pj.get('fields') or []
    if not fields:
        return jsonify({'success': False, 'error': '该面板没有可分享的字段定义（内置特制面板不支持打包）'}), 400

    # 打包：纯 JSON 面板只带 panel.json；带界面的面板整个目录打包
    panel_dir = pa.PANELS_DIR / panel_id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(panel_dir.iterdir()):
            if f.is_file() and f.name != '_protection.json':
                zf.write(str(f), f'{panel_id}/{f.name}')
    buf.seek(0)

    info = {'name': pj.get('name', panel_id), 'icon': pj.get('icon', ''),
            'fields': len(fields), 'field_names': [f.get('label', f.get('name', '')) for f in fields]}

    if d.get('dry_run'):
        return jsonify({'success': True, 'dry_run': True, 'info': info,
                        'bytes': buf.getbuffer().nbytes})

    try:
        tmp = tempfile.NamedTemporaryFile(prefix=f'hub_{panel_id}_', suffix='.zip', delete=False)
        tmp.write(buf.getvalue())
        tmp.close()
        from src.core.harvenhub_client import hub_upload
        result = hub_upload(
            tmp.name, 'template',
            title=info['name'],
            description=(pj.get('description') or '') +
                        f"（哈文对话即录入分类包，字段：{'、'.join(info['field_names'][:8])}）",
            author=d.get('author') or 'Anonymous',
            category='family')
        try:
            import os as _os
            _os.unlink(tmp.name)
        except Exception:
            pass
        if result.get('success'):
            return jsonify({'success': True, 'item_id': result.get('item_id', ''), 'info': info})
        return jsonify({'success': False, 'error': result.get('error', '上传失败')}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
