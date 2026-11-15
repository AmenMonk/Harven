# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""★ #1377（2026-11-02 定版拍板 A+B+C 全做）账单相册 → 账单模块分析管线

痛点（实测实锤 11-02）：账单照片只被归位引擎送进「账单」相册就断了——
跟记账面板之间零打通，也没有任何提示。用户期望「先拍照存着不耽误时间，
之后发给账单模块分析录入」。

本模块 = A/C 共用的同一落点：
- A：图片库账单相册「💰 发给账单模块分析」按钮 → 后台线程跑 scan_bills(source='manual')
- C：butler 心跳每日块跑 scan_bills(source='auto')（开关 bills_auto_scan 默认开）
- 抽取走 _camp_vision 统一入口（铁律 0：选谁给谁，主模型=HarvenEye→Eye，外脑→外脑视觉）
- 防重三板斧（开发者死命令「小心 a 点击确认之后 c 还提醒」）：
  ① 照片级标记：分析完成（成功/失败/重复）立即 bill_processed=1——扫描只认 0，
    A 先扫过的 C 永不再碰；C 先碰过的 A 按钮也不会重复
  ② 入库走待确认卡（assist_pending），用户把关后 confirm_pending 走 insert_records
    （记账口径齐全：kind/方向/来源/分类修正）
  ③ #913 查重：同日+同金额+近似名直接丢弃，重复票不烦用户
- 识别失败也打标记（bill_note 记原因）——不天天重试骚扰；可 reprocess_bills 手动重跑
"""
import base64
import json
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

PANEL_ID = 'accounting_daily_1'          # 记账面板（数据真家）
_CATS = '餐饮/买菜/交通/车辆/日用/孩子/医疗/人情/缴费/娱乐/宠物/绿植/维修/其他'
_BATCH_LIMIT = 30                        # 单次最多分析张数（心跳/手动同限，token 经济）

# 防并发：A 按钮和 C 心跳同时只能跑一个；抢不到锁的这次直接跳过并如实返回
_scan_lock = threading.Lock()


def _bill_prompt(today):
    return (
        "这是账单/支付凭证/购物订单的照片。请提取消费信息，输出 JSON 数组：\n"
        '[{"项目":"商家或品名","金额":85.5,"日期":"YYYY-MM-DD","分类":"分类名","方向":"支出",'
        '"备注":"补充信息（订单号/支付方式等，没有就空字符串）"}]\n'
        f"规则：分类从这些里选最贴的：{_CATS}；金额只填数字不要¥符号；"
        f"照片里看不到日期就填今天（{today}）；方向默认支出，退款/收入类才填收入；"
        "一张照片有多笔就输出多条；认不出金额的照片输出 []，绝对不要猜金额。"
        "只输出 JSON 数组，不要解释。")


def extract_bill_records(img_id):
    """单张照片 → (records:list[dict], model, err)。跟随用户当前模型阵营（_camp_vision）。"""
    from src.core import img_store
    p = img_store.get_file(img_id)
    if not p:
        return [], '', '照片文件读不出来'
    b64 = base64.b64encode(p.read_bytes()).decode('ascii')
    from src.core.panel_assist import _camp_vision_bg
    today = datetime.now().strftime('%Y-%m-%d')
    text, model, err = _camp_vision_bg(_bill_prompt(today), b64, timeout=120)
    if err or not text:
        return [], model or '', err or '视觉模型没有返回'
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end <= start:
        return [], model, '没认出账单信息'
    try:
        items = json.loads(text[start:end + 1])
    except Exception:
        return [], model, '识别结果读不懂'
    if not isinstance(items, list):
        return [], model, '识别结果读不懂'
    today_s = today
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            amt = round(float(str(it.get('金额', '')).replace('¥', '').replace('￥', '').strip()), 2)
        except (TypeError, ValueError):
            continue          # 没金额 = 没法入账，宁缺毋滥
        if amt <= 0:
            continue
        name = str(it.get('项目') or '').strip()[:60] or '账单消费'
        date = str(it.get('日期') or '').strip()[:10]
        if len(date) != 10 or date[4] != '-':
            date = today_s
        d = {'项目': name, '金额': amt, '日期': date,
             '分类': str(it.get('分类') or '其他').strip()[:10],
             '方向': '收入' if str(it.get('方向') or '').strip() == '收入' else '支出',
             '来源': f'账单照片#{img_id}',
             '备注': str(it.get('备注') or '').strip()[:100]}
        out.append(d)
    return out, model, ('' if out else '没认出能入账的金额')


def scan_bills(source='manual', ids=None):
    """主流程：取未标记账单照片 → 逐张抽取 → 查重 → 待确认卡 → 打标记。
    source: 'manual'=A 按钮 / 'auto'=C 心跳。ids=None 自动取全部未标记（默认）；
    传 ids 指定批次（测试隔离用，照片必须是未标记状态）。
    返回统计 dict（回执/日志两用）。
    ★ 标记先行：每张处理完立即 mark_bills_processed，中途崩溃也不会重扫旧照片。"""
    from src.core import img_store
    from src.core.panel_assist import add_pending
    from src.core.panel_manager import get_panel_manager

    if not _scan_lock.acquire(blocking=False):
        return {'success': False, 'error': '上一次账单分析还在跑，等它完成再说'}
    try:
        if ids is None:
            ids = img_store.unprocessed_bills(limit=_BATCH_LIMIT)
        else:
            # 指定批次也只认「未标记」的——已分析过的照片绝不二次扫描（防重死命令）
            _marked = set(img_store.unprocessed_bills(limit=5000))
            ids = [i for i in ids if i in _marked]
        if not ids:
            return {'success': True, 'total': 0, 'cards': 0, 'dup': 0, 'failed': 0,
                    'message': '账单相册没有待分析的照片'}
        pm = get_panel_manager()
        ok_imgs, failed, dup, done_ids = [], [], [], []
        for img_id in ids:
            try:
                recs, model, err = extract_bill_records(img_id)
            except Exception as e:
                recs, err = [], str(e)
            if err:
                failed.append({'id': img_id, 'reason': err})
                img_store.mark_bills_processed([img_id], note=f'失败：{err}')
                logger.info(f'[BillsScan] #{img_id} 失败跳过: {err}（已标记不再重扫）')
                continue
            fresh = []
            for d in recs:
                try:
                    if pm.find_duplicate(PANEL_ID, d):
                        dup.append(d)
                        continue          # 重复票直接丢弃（照片稍后统一标记）
                except Exception:
                    pass
                fresh.append(d)
            # ★ 每张照片一张待确认卡（可单独忽略错票）；堆积痛点由管家中心「全部入账」兜底
            if fresh:
                try:
                    add_pending(PANEL_ID, '', fresh,
                                source_text=f'💰 账单照片#{img_id}（{"手动" if source == "manual" else "心跳自动"}分析）')
                    ok_imgs.append(img_id)
                except Exception as _pe:
                    logger.error(f'[BillsScan] #{img_id} 待确认卡写入失败: {_pe}')
                    done_ids.append(img_id)   # 卡没写成也不重扫（防死循环）
                    continue
            done_ids.append(img_id)
            img_store.mark_bills_processed([img_id],
                                           note=f'已分析({source})：{len(fresh)}条入待确认，{len(recs)-len(fresh)}条重复丢弃')
        n_cards = len(ok_imgs)
        msg_parts = [f'{len(ids)} 张账单照片分析完']
        if n_cards:
            msg_parts.append(f'{n_cards} 张进了管家中心待确认（去把关入库，可单张忽略也可点「全部入账」）')
        if dup:
            msg_parts.append(f'{len(dup)} 条重复已丢弃')
        if failed:
            msg_parts.append(f'{len(failed)} 张没认出金额（已跳过，可在记账面板手动补）')
        out = {'success': True, 'total': len(ids), 'cards': n_cards,
               'dup': len(dup), 'failed': len(failed),
               'message': '，'.join(msg_parts)}
        logger.info(f'[BillsScan] {source}: {out}')
        return out
    finally:
        _scan_lock.release()


def scan_bills_async(source='manual'):
    """后台线程跑 scan_bills（A 按钮入口：避免 HTTP 请求被多张照片的视觉调用拖超时）。
    返回 (是否成功启动, 提示文案)。并发冲突由 scan_bills 的锁兜底（正在跑会如实返回）。"""
    from src.core import img_store
    ids = img_store.unprocessed_bills(limit=_BATCH_LIMIT)
    if not ids:
        return True, '账单相册没有待分析的照片（都分析过了）'
    threading.Thread(target=scan_bills, args=(source,),
                     name=f'bills-scan-{source}', daemon=True).start()
    return True, (f'已开始分析 {len(ids)} 张账单照片（用的就是你当前选的模型），'
                  f'完成后会出现在管家中心「待确认收纳盒」，去那里把关入库')
