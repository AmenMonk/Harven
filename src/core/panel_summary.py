# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
# -*- coding: utf-8 -*-
"""★ 2026-09-19 #769 P1 定时汇总三件套（定版拍板「全部一起做」）

梦境周总结 / 记账月报 / 孩子成长月报：
- 复用 panel_assist 的记录收集（_collect_answer_context）+ 模型调用，
  按 panel.json 顶层 ai_role 角色卡分析（十角色矩阵，#770）
- ★ 2026-09-27 开发者纠正：汇总跟随用户当前模型阵营（选外脑走外脑，选本地走本地，
  用户有权选任何模式，我们只建议不干预；隐私板块 UI 文案建议本地），不回落
- 结果写 panel_ai_history → 面板「AI 分析」框打开就能回看（#768 落库复用）
- 同时写一条 home_reminder 即时提醒 → 鸭头角标 + 首页「今天要处理」可见（通知闭环）

调度：独立守护线程每 10 分钟检查一次，状态表 panel_summary_state 防重（错过点补跑一次）。
不占用 APScheduler 任务系统（那边是 prompt→对话回调，语义不同）。

时点（错峰）：
- dream_weekly    每周一 09:30  （面板 dream_journal）
- accounting_monthly 每月1日 09:40（面板 accounting_daily_1）
- kid_monthly     每月1日 09:50（面板 kid_archive）
"""
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sqlite" / "lingzhi.db"
_CHECK_INTERVAL = 600  # 10 分钟
_lock = threading.Lock()
_thread_started = False

# kind -> (面板id, 标题, 说明)
SUMMARY_KINDS = {
    'dream_weekly': ('dream_journal', '梦境周总结', '把最近一周的梦境放在一起看情绪和压力的线索'),
    'accounting_monthly': ('accounting_daily_1', '记账月报', '上个月的钱花在哪了、和前月比有什么变化'),
    'kid_monthly': ('kid_archive', '孩子成长月报', '上个月孩子各维度的成长变化和下月建议'),
    # ★ 2026-10-05 #1011 冰箱临期每日提醒（第四件，默认关）：确定性清单（expiry_digest）+ 模型组织建议
    'fridge_expiry_daily': ('fridge_stock_1', '冰箱临期提醒', '把 7 天内到期的食材列出来，建议先吃啥'),
    # ★ 2026-10-10 #1063 B1 饮食周报（第五件，默认关）：确定性统计（meal_stats）打底 + 模型组织建议
    'meal_weekly': ('meal_diary', '饮食周报', '一周吃了啥：外卖花销、吃辣频率、热量参考和饮食建议'),
    # ★ 2026-10-11 #1073 健康周报（第六件，默认关）：健康档案记录 + 本地算好的健康待办打底
    'health_weekly': ('health_file', '健康周报', '全家一周健康动态：体检/用药/健身/成长，加上未来两周要操心的事'),
}

# ★ 2026-09-27 定版拍板：每个定时汇总各配开关（设置→面板助手），默认全关。
# 定时自动跑必须用户显式打开；手动触发（force=True）不受开关限。
# ★ 开发者纠正：汇总跟随用户当前模型阵营（外脑/本地均可，尊重用户选择，不干预）；
# 外脑阵营受「档案问答允许外脑」开关管，失败报本阵营错误不回落
SUMMARY_SWITCH_KEYS = {
    'dream_weekly': 'summary_dream_weekly',
    'accounting_monthly': 'summary_accounting_monthly',
    'kid_monthly': 'summary_kid_monthly',
    'fridge_expiry_daily': 'summary_fridge_expiry',
    'meal_weekly': 'summary_meal_weekly',
    'health_weekly': 'summary_health_weekly',
}


def _switch_open(kind):
    """该 kind 的自动汇总开关是否打开（默认关）"""
    from src.core.panel_assist import get_assist_settings
    key = SUMMARY_SWITCH_KEYS.get(kind)
    if not key:
        return False
    return get_assist_settings().get(key, 'false') == 'true'


def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_state():
    with _lock:
        c = _conn()
        try:
            c.execute("""CREATE TABLE IF NOT EXISTS panel_summary_state (
                        kind TEXT PRIMARY KEY,
                        last_run TEXT)""")
            c.commit()
        finally:
            c.close()


def _last_run(kind):
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT last_run FROM panel_summary_state WHERE kind=?", (kind,)).fetchone()
            return row[0] if row else ''
        finally:
            c.close()


def _set_last_run(kind, ts):
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT INTO panel_summary_state(kind, last_run) VALUES(?,?) "
                      "ON CONFLICT(kind) DO UPDATE SET last_run=excluded.last_run", (kind, ts))
            c.commit()
        finally:
            c.close()


def _boundary(kind, now):
    """该 kind 本期应跑的时间点（错过点补跑：now >= boundary 且 last_run < boundary 即执行）"""
    if kind == 'dream_weekly':
        monday = (now - timedelta(days=now.weekday())).date()
        return datetime(monday.year, monday.month, monday.day, 9, 30)
    if kind == 'meal_weekly':
        # ★ 2026-10-10 #1063 饮食周报：每周一 09:45（与梦境周 09:30 错峰）
        monday = (now - timedelta(days=now.weekday())).date()
        return datetime(monday.year, monday.month, monday.day, 9, 45)
    if kind == 'health_weekly':
        # ★ 2026-10-11 #1073 健康周报：每周一 09:55（继续错峰）
        monday = (now - timedelta(days=now.weekday())).date()
        return datetime(monday.year, monday.month, monday.day, 9, 55)
    if kind == 'accounting_monthly':
        return datetime(now.year, now.month, 1, 9, 40)
    if kind == 'kid_monthly':
        return datetime(now.year, now.month, 1, 9, 50)
    if kind == 'fridge_expiry_daily':
        return datetime(now.year, now.month, now.day, 9, 20)
    return None


def _period_label(kind, now):
    if kind in ('dream_weekly', 'meal_weekly', 'health_weekly'):
        monday = (now - timedelta(days=now.weekday())).date()
        sunday = monday + timedelta(days=6)
        return f"{monday.strftime('%m月%d日')}~{sunday.strftime('%m月%d日')} 这一周"
    if kind == 'fridge_expiry_daily':
        return now.strftime('%m月%d日')
    first = datetime(now.year, now.month, 1)
    last = (first - timedelta(days=1)).date()
    return f"{last.year}年{last.month}月"


def _run_fridge_expiry(force=False):
    """★ 2026-10-05 #1011 冰箱临期每日提醒（确定性清单打底，模型只做组织建议）。
    电脑通道：home_reminder（鸭头角标+今天要处理）+ panel_ai_history（冰箱页回看）；
    手机通道：手机页面板数据/AI 分析历史同步可见（D5 主动推送二期再挂）。
    没有临期项时保持安静不吵人。"""
    from src.core.fridge_hub import expiry_digest
    lines, n_exp, n_soon = expiry_digest()
    if not lines:
        return None, '', '没有临期食材，今天不提醒'
    digest = '\n'.join(lines)
    # 跟随阵营出建议（失败不丢清单：确定性部分照常提醒）
    advice, model = '', ''
    try:
        from src.core import panel_assist as pa
        text2, model, err = pa._llm_call(
            '你是家庭冰箱管家，说话简短实用，不绵密不空话。',
            '今天冰箱临期清单：\n' + digest + '\n\n用三五句话建议先吃哪几样、怎么搭着吃，不用重复清单。',
            max_tokens=400, temperature=0.4, gate='answer_allow_cloud')
        if not err and text2:
            advice = text2.strip()
    except Exception as e:
        logger.warning(f"[PanelSummary] fridge_expiry 模型建议失败（清单照发）: {e}")
    period = _period_label('fridge_expiry_daily', datetime.now())
    text = f"🧊 冰箱临期提醒（{period}）\n\n{digest}"
    if advice:
        text += f"\n\n💡 管家建议：{advice}"
    # ★ #1157（2026-10-16）食谱串联：临期清单直接引导到按库存排食谱（生成入口已有：临期优先+过敏排除）
    if n_soon or n_exp:
        text += "\n\n📋 想好怎么吃？跟我说「生成今日食谱」，临期的先安排上桌"
    try:
        from src.core import panel_assist as pa
        pa._save_ai_result('fridge_stock_1', f'【定时】冰箱临期提醒（{period}）', text, model or '本地规则')
    except Exception as e:
        logger.warning(f"[PanelSummary] fridge_expiry 落库失败: {e}")
    try:
        from src.core import home_reminder as hr
        hr.add_reminder(module='fridge_stock_1',
                        title=f"🧊 冰箱有 {len(lines)} 样要操心（过期 {n_exp} · 临期 {n_soon}）",
                        remind_at=(datetime.now() + timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M'),
                        repeat='once', source='panel_summary',
                        note=text[:500])
    except Exception as e:
        logger.warning(f"[PanelSummary] fridge_expiry 通知失败: {e}")
    if not force:
        _set_last_run('fridge_expiry_daily', datetime.now().isoformat())
    logger.info(f"[PanelSummary] fridge_expiry_daily 完成（{model or '本地规则'}，{len(lines)}项）")
    return text, model or '本地规则', ''


def run_summary(kind, force=False):
    """跑一个汇总。返回 (text, model, err)。force=True 手动触发不看时点。"""
    import json as _json
    from src.core import panel_assist as pa
    from src.core import home_reminder as hr

    if kind not in SUMMARY_KINDS:
        return None, '', f'未知汇总类型: {kind}'
    panel_id, title, desc = SUMMARY_KINDS[kind]
    now = datetime.now()

    # ★ 2026-10-05 #1011 冰箱临期提醒走专属通道（确定性清单，不走 _collect_answer_context）
    if kind == 'fridge_expiry_daily':
        if not force:
            if not _switch_open(kind):
                return None, '', '定时汇总开关未打开（设置 → 面板助手）'
            boundary = _boundary(kind, now)
            if boundary is None or now < boundary:
                return None, '', '还没到汇总时间'
            if _last_run(kind) >= boundary.isoformat():
                return None, '', '本期已经汇总过了'
        return _run_fridge_expiry(force)

    # ★ 2026-09-19 修2（体检）：汇总前先查面板存在——面板被卸载就静默停跑，
    # 不再空跑 LLM/推怪文案（实例：dream_journal 卸载后 dream_weekly 下周一会白跑）
    _panel_dir = DB_PATH.parent.parent / "panels" / panel_id
    if not _panel_dir.is_dir():
        msg = f'模块 {panel_id} 不存在（可能已卸载），汇总自动停跑'
        logger.info(f"[PanelSummary] {kind}: {msg}")
        return None, '', msg

    if not force:
        # ★ 2026-09-27：开关没开就不跑（默认关，用户在设置→面板助手自己开）
        if not _switch_open(kind):
            return None, '', '定时汇总开关未打开（设置 → 面板助手）'
        boundary = _boundary(kind, now)
        if boundary is None or now < boundary:
            return None, '', '还没到汇总时间'
        if _last_run(kind) >= boundary.isoformat():
            return None, '', '本期已经汇总过了'

    ctx = pa._collect_answer_context(panel_id)
    if len(ctx.strip()) < 30:
        return None, '', f'「{title}」的记录还太少，先记几条再来汇总'

    pj = pa._panel_json(panel_id) or {}
    role = (pj.get('ai_role') or '').strip() or '你是家庭数据分析师。'
    period = _period_label(kind, now)
    system = (f"{role}\n现在是做「{title}」的时间：{desc}。"
              "根据给出的本机面板记录做汇总：输出【数据概览】【发现/规律】【建议】三节，"
              "每节2-4条，具体、可操作、不空话。记录太少就直说样本少，不要编造。")
    prompt = f"【面板记录（{period}）】\n{ctx}\n\n请做{period}的{title}。"
    # ★ 2026-10-10 #1063 B1 饮食周报：本地算好确定性统计注入（AI 以此为准不许自己估）
    if kind == 'meal_weekly':
        try:
            from src.core.meal_insights import meal_stats as _mstats
            _st = _mstats(7)
            _tk, _sp, _cal = _st['takeout'], _st['spicy'], _st['calories']
            _shops = '、'.join(f"{s['shop']}（{s['count']}次¥{s['amount']}）" for s in _tk['shops'][:3]) or '无'
            prompt += (f"\n\n【本地已算好的确定性统计（以此为准，禁止自己再估算数字）】\n"
                       f"近7天{_st['records']}条餐饮记录；外卖{_tk['count']}次共¥{_tk['amount']}，"
                       f"常点店：{_shops}；吃辣{_sp['count']}次；"
                       f"热量：表内估算合计{_cal['diary_sum']}千卡，另有{_cal['estimated']}条词典粗估{_cal['dict_est']}千卡（仅供参考）。")
        except Exception as _me:
            logger.debug(f"[PanelSummary] 饮食统计注入跳过: {_me}")
    # ★ 2026-10-11 #1073 健康周报：本地算好的健康待办注入（未来14天要操心的事，AI 以此为准）
    if kind == 'health_weekly':
        try:
            from src.core.health_profile import health_reminders as _hrem
            _rem = _hrem(14)
            if _rem:
                _lines = '\n'.join(f"- {r['icon']} {r['text']}（{r['date']}）" for r in _rem)
                prompt += (f"\n\n【本地已算好的健康待办（未来14天，以此为准不许编造）】\n{_lines}")
        except Exception as _me:
            logger.debug(f"[PanelSummary] 健康待办注入跳过: {_me}")
    # ★ 2026-09-27 开发者纠正：跟随用户当前模型阵营直连（选外脑走外脑，选本地走本地，不干预不回落）；
    # 汇总属问答类 → gate=answer_allow_cloud
    text, model, err = pa._llm_call(system, prompt, max_tokens=900, temperature=0.4, gate='answer_allow_cloud')
    if err or not text:
        return None, model, err or '模型没有返回内容'

    # ① 落 panel_ai_history（面板 AI 分析框回看）
    try:
        pa._save_ai_result(panel_id, f'【定时】{title}（{period}）', text, model)
    except Exception as e:
        logger.warning(f"[PanelSummary] {kind} 结果落库失败: {e}")

    # ② 通知闭环：即时提醒（鸭头「今天要处理」+ 面板提醒条可见）
    brief = text.strip().splitlines()[0][:120] if text.strip() else ''
    try:
        hr.add_reminder(module=panel_id,
                        title=f"📋 {title}已生成（{period}）：{brief}",
                        remind_at=(now + timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M'),
                        repeat='once', source='panel_summary',
                        note=f'打开面板可看完整汇总；摘要：{text[:280]}')
    except Exception as e:
        logger.warning(f"[PanelSummary] {kind} 通知失败: {e}")

    if not force:
        _set_last_run(kind, now.isoformat())
    logger.info(f"[PanelSummary] {kind} 完成（{model}，{len(text)}字）")
    return text, model, ''


def summary_state():
    """各 kind 状态（状态页/测试用）"""
    now = datetime.now()
    out = {}
    for kind, (panel_id, title, _desc) in SUMMARY_KINDS.items():
        out[kind] = {'panel_id': panel_id, 'title': title,
                     'last_run': _last_run(kind),
                     'next_boundary': (_boundary(kind, now) or now).isoformat()}
    return out


def _loop():
    while True:
        try:
            now = datetime.now()
            for kind in SUMMARY_KINDS:
                try:
                    # ★ 2026-09-27：开关关着直接跳过，连 boundary 都不算（省查询）
                    if not _switch_open(kind):
                        continue
                    boundary = _boundary(kind, now)
                    if boundary and now >= boundary and _last_run(kind) < boundary.isoformat():
                        text, _model, err = run_summary(kind)
                        if err:
                            logger.warning(f"[PanelSummary] {kind} 定时执行未成功: {err}")
                except Exception as e:
                    logger.error(f"[PanelSummary] {kind} 定时执行异常: {e}")
        except Exception as e:
            logger.error(f"[PanelSummary] 汇总循环异常: {e}")
        time.sleep(_CHECK_INTERVAL)


def ensure_summary_loop():
    """启动守护线程（web_app 启动时调用一次；幂等）"""
    global _thread_started
    with _lock:
        if _thread_started:
            return
        _thread_started = True
    _init_state()
    t = threading.Thread(target=_loop, name='panel-summary-loop', daemon=True)
    t.start()
    logger.info('[PanelSummary] 定时汇总线程已启动（梦境周/记账月/成长月）')
