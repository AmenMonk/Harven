#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
档案中枢（archive_hub）— 2026-09-27 #882
═══════════════════════════════════════════════════════
定案原则（定版拍板）：
1. 所有面板数据只有一个家（面板表，data/sqlite/lingzhi.db）
2. 所有查询先走中枢：听懂时间（今天/本月）→ 去对的表 → 毫秒级直查
3. 查到的数据给谁处理看用户当前选的模型——选本地给本地、选外脑给外脑；
   中枢只供数据，绝不切模型；本地答不了就诚实告诉用户答不了。
4. 时间解析纯本地规则，零 token。

一期范围：记账域（panel_accounting_daily_1）。二期再扩 FTS5 / L0~L3 调度器。
"""

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "sqlite" / "lingzhi.db"

# 记账面板（一期固定；二期由域注册表接管）
ACCOUNTING_TABLE = "panel_accounting_daily_1"

# ── 查询意图词（问账 ≠ 记账）──
_ACCOUNTING_QUERY_WORDS = (
    "花了", "花费", "花销", "用了多少", "支出多少", "收入多少",
    "开销", "消费", "查账", "账单", "多少笔", "收支",
    "支出", "收入", "账", "记账了吗", "账目",
)
# 记录意图（写入，不是查询）
_ACCOUNTING_SAVE_WORDS = ("记一笔", "记一下", "记个账", "帮我记", "记上", "记录一下")

# ═══════════════════════════════════════════════════════
# 一、时间解析（纯本地规则，零 token）
# ═══════════════════════════════════════════════════════

def parse_time_range(text: str):
    """从自然语言解析时间区间。
    返回 (start, end, label)，start/end 为 YYYY-MM-DD 字符串（含当天）。
    解析不到返回 None。"""
    if not text:
        return None
    t = text.strip()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    def mk(d1, d2, label):
        return (d1.strftime("%Y-%m-%d"), d2.strftime("%Y-%m-%d"), label)

    if re.search(r"今天|今日", t):
        return mk(now, now, "今天")
    if re.search(r"昨天|昨日", t):
        y = now - timedelta(days=1)
        return mk(y, y, "昨天")
    if re.search(r"前天", t):
        d = now - timedelta(days=2)
        return mk(d, d, "前天")
    if re.search(r"本周|这周|这个星期|这一星期|这礼拜", t):
        monday = now - timedelta(days=now.weekday())
        return mk(monday, now, "本周")
    if re.search(r"上周|上个星期|上礼拜", t):
        monday = now - timedelta(days=now.weekday() + 7)
        sunday = monday + timedelta(days=6)
        return mk(monday, sunday, "上周")
    if re.search(r"本月|这个月|这月|当月", t):
        return mk(now.replace(day=1), now, "本月")
    if re.search(r"上个月|上月", t):
        first_this = now.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        return mk(last_month_end.replace(day=1), last_month_end, "上个月")
    if re.search(r"今年|这一年", t):
        return mk(now.replace(month=1, day=1), now, "今年")
    if re.search(r"去年", t):
        y = now.year - 1
        return mk(datetime(y, 1, 1), datetime(y, 12, 31), "去年")

    m = re.search(r"(?:近|最近|过去|这)(\d{1,3})天", t)
    if m:
        n = int(m.group(1))
        n = max(1, min(n, 3650))
        return mk(now - timedelta(days=n - 1), now, f"近{n}天")

    # 具体日期：9月25日 / 2026-09-25 / 2026/9/25 / 09-25
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", t)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return mk(d, d, d.strftime("%Y年%m月%d日"))
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", t)
    if m:
        try:
            d = datetime(now.year, int(m.group(1)), int(m.group(2)))
            return mk(d, d, d.strftime("%Y年%m月%d日"))
        except ValueError:
            pass
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return mk(d, d, d.strftime("%Y年%m月%d日"))
        except ValueError:
            pass

    # 周几（本周内）
    weekdays = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    m = re.search(r"(?:本周|这周|星期|礼拜)([一二三四五六日天])", t)
    if m and m.group(1) in weekdays:
        monday = now - timedelta(days=now.weekday())
        d = monday + timedelta(days=weekdays[m.group(1)])
        return mk(d, d, f"本周{'一二三四五六日'[weekdays[m.group(1)]]}")

    return None


# ═══════════════════════════════════════════════════════
# 二、域识别（一期只做记账）
# ═══════════════════════════════════════════════════════

def detect_domain(text: str):
    """识别查询域。返回 'accounting' / 'diet' / 'health' / None。
    记录意图（记一笔）不算查询。
    ★ 2026-10-05 #1012 新增饮食域：家人过敏/忌口/喜好查间直查 family_diet_profile
    ★ 2026-10-11 #1073 新增健康域：血型/用药/疫苗/体检直查健康画像+药箱"""
    if not text:
        return None
    t = text
    if any(w in t for w in _ACCOUNTING_SAVE_WORDS):
        return None
    if any(w in t for w in ('过敏', '忌口', '口味', '喜好', '饮食档案', '不能吃', '能吃什么', '对什么过敏')):
        return "diet"
    # ★ #1073 健康域（放在 accounting 前面，健康词优先命中）
    if any(w in t for w in ('健康档案', '血型', '紧急联系人', '体检', '疫苗', '接种',
                            '体质', '过敏体质', '慢病', '健康画像',
                            '吃什么药', '在吃药', '在服什么药', '用药', '服药')):
        return "health"
    if any(w in t for w in _ACCOUNTING_QUERY_WORDS):
        return "accounting"
    return None


# ═══════════════════════════════════════════════════════
# 三、直查（毫秒级，纯 SQL）
# ═══════════════════════════════════════════════════════

def _get_conn():
    return sqlite3.connect(str(DB_PATH))


def hub_query_accounting(start: str, end: str, limit: int = 20):
    """直查记账面板表：日期过滤 + 收支汇总 + 分类明细。
    返回 dict；表不存在/无记录返回 success=False / empty 标记。"""
    conn = _get_conn()
    try:
        cols = [r[1] for r in conn.execute(
            f'PRAGMA table_info("{ACCOUNTING_TABLE}")').fetchall()]
        if not cols:
            return {"success": False, "error": "记账表不存在"}

        rows = conn.execute(
            f'''SELECT id, "日期", "方向", "金额", "分类", "项目", "备注"
                FROM {ACCOUNTING_TABLE}
                WHERE "方向" IN ('收入','支出') AND "日期" >= ? AND "日期" <= ?
                ORDER BY "日期" DESC, id DESC''',
            (start, end)).fetchall()
    finally:
        conn.close()

    records = []
    total_income = total_expense = 0.0
    by_cat = {}
    for r in rows:
        rid, date, direction, amount, cat, item, note = r
        amount = float(amount or 0)
        if direction == "收入":
            total_income += amount
        else:
            total_expense += amount
        key = (direction, cat or "其他")
        by_cat[key] = by_cat.get(key, 0.0) + amount
        records.append({
            "id": rid, "date": date, "type": direction,
            "amount": round(amount, 2), "category": cat or "其他",
            "item": item or note or "", "note": note or "",
        })

    breakdown = [
        {"type": k[0], "category": k[1], "amount": round(v, 2)}
        for k, v in sorted(by_cat.items(), key=lambda x: -x[1])
    ]
    return {
        "success": True,
        "start": start, "end": end,
        "total_count": len(records),
        "total_income": round(total_income, 2),
        "total_expense": round(total_expense, 2),
        "balance": round(total_income - total_expense, 2),
        "breakdown": breakdown,
        "records": records[:limit],
        "truncated": len(records) > limit,
    }


def hub_query(domain: str, start: str, end: str):
    """统一入口。一期记账；★ 2026-10-05 #1012 二期加饮食域。"""
    if domain == "accounting":
        return hub_query_accounting(start, end)
    if domain == "diet":
        from src.core.fridge_hub import hub_context_diet
        ctx = hub_context_diet()
        if not ctx:
            return {"success": False, "error": "饮食档案还没建（冰箱页→食谱Tab→家人饮食档案）"}
        return {"success": True, "domain": "diet", "text": ctx}
    if domain == "health":
        # ★ 2026-10-11 #1073 健康域
        from src.core.health_profile import hub_context_health
        ctx = hub_context_health()
        if not ctx:
            return {"success": False, "error": "健康档案还没建（健康档案模块→点助手补全家人信息）"}
        return {"success": True, "domain": "health", "text": ctx}
    return {"success": False, "error": f"未知域: {domain}"}


# ═══════════════════════════════════════════════════════
# 四、人话摘要 + 预查注入（给本地或外脑均可，不挑模型）
# ═══════════════════════════════════════════════════════

def _fmt_money(v):
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def hub_summary_text(result: dict, label: str) -> str:
    """把直查结果组织成简短人话（供模型转述或直接展示）。"""
    if not result.get("success"):
        return ""
    if result["total_count"] == 0:
        return f"{label}没有记账记录。"
    lines = [f"{label}共 {result['total_count']} 笔："
             f"收入 {_fmt_money(result['total_income'])} 元，"
             f"支出 {_fmt_money(result['total_expense'])} 元。"]
    exp_cats = [b for b in result["breakdown"] if b["type"] == "支出"]
    inc_cats = [b for b in result["breakdown"] if b["type"] == "收入"]
    if exp_cats:
        lines.append("支出去向：" + "、".join(
            f"{b['category']} {_fmt_money(b['amount'])}元" for b in exp_cats[:8]))
    if inc_cats:
        lines.append("收入来源：" + "、".join(
            f"{b['category']} {_fmt_money(b['amount'])}元" for b in inc_cats[:5]))
    recent = result["records"][:5]
    if recent:
        lines.append("最近几笔：" + "；".join(
            f"{r['date'][5:]} {r['item'] or r['category']} {r['type']}{_fmt_money(r['amount'])}元"
            for r in recent))
    return "\n".join(lines)


def hub_quick_context(text: str) -> str:
    """引擎/浮窗预查入口：识别到查账意图 → 直查 → 返回可注入的资料块。
    未命中返回 ''（不干扰正常对话）。
    ★ 约束：本函数只产资料，不挑模型——注入后走用户当前选的本地/外脑管线。"""
    try:
        dom = detect_domain(text)
        if dom == "diet":
            # ★ #1012 饮食档案：全量很短，直接回
            from src.core.fridge_hub import hub_context_diet
            ctx = hub_context_diet()
            if not ctx:
                return ""
            return ("【档案中枢·已查到的真实数据（请直接基于此回答，不要再说不知道）】\n" + ctx)
        if dom == "health":
            # ★ #1073 健康档案：全家画像+正在用药，同样全量很短
            from src.core.health_profile import hub_context_health
            ctx = hub_context_health()
            if not ctx:
                return ""
            return ("【档案中枢·已查到的真实数据（请直接基于此回答，不要再说不知道）】\n" + ctx)
        if dom != "accounting":
            return ""
    except Exception:
        # 中枢任何异常都不能挡住正常对话
        return ""


def hub_direct_answer(text: str):
    """浮窗/轻量入口：命中且数据简单时直接给人话答案（不经过任何模型）。
    返回 (answer_str, result_dict)；未命中返回 ('', None)。
    ★ #1012 饮食域数据简单，直接原文回。"""
    try:
        dom = detect_domain(text)
        if dom == "diet":
            from src.core.fridge_hub import hub_context_diet
            ctx = hub_context_diet()
            if ctx:
                return ctx, {'success': True, 'domain': 'diet'}
            return ("饮食档案还没建：到冰箱模块 → 食谱页 →「家人饮食档案」里把家里人添上就行。", None)
        if dom == "health":
            # ★ 2026-10-11 #1073 健康域：画像数据简单，直接原文回
            from src.core.health_profile import hub_context_health
            ctx = hub_context_health()
            if ctx:
                return ctx, {'success': True, 'domain': 'health'}
            return ("健康档案还没建：打开「健康档案」模块，把家人的血型、过敏、常用药补上就行。", None)
        if dom != "accounting":
            return "", None
        tr = parse_time_range(text)
        if tr is None:
            return "", None
        start, end, label = tr
        result = hub_query("accounting", start, end)
        if not result.get("success"):
            return "", None
        if result["total_count"] == 0:
            return f"{label}没有记账记录。", result
        answer = hub_summary_text(result, label)
        return answer, result
    except Exception:
        return "", None
