# -*- coding: utf-8 -*-
"""记账分类关键词兜底 ★ 2026-09-16
所有记账落库口（engine 智能拆分 / panel_assist 小窗抽取）共用一套分类规则。
AI 抽取的分类可能选错（实锤：早餐→买菜），关键词命中时优先于 AI 结果。
返回 (分类, 方向)；方向由收入类关键词/分类决定，默认支出。
"""

# (正则, 分类) —— 顺序即优先级，最具体的放前面
_OUT_KW = [
    (r'早餐|早饭|早点', '餐饮'),
    (r'午饭|午餐|晚饭|晚餐|午饭|吃饭|外卖|面馆|下馆子|宵夜|夜宵|奶茶|咖啡', '餐饮'),
    (r'买菜|菜场|菜市场|超市|生鲜|粮油', '买菜'),
    (r'社保|水电|燃气|物业|话费|网费|宽带|房租|取暖|停车费|充电', '缴费'),
    (r'加油|油费|过路|洗车|保养|年检|轮胎|保险费', '车辆'),
    (r'公交|地铁|打车|出租|高铁|火车|机票|滴滴|车票', '交通'),
    (r'学费|兴趣班|班费|玩具|绘本|奶粉|尿不湿', '孩子'),
    (r'药|挂号|体检|看病|疫苗|门诊', '医疗'),
    (r'随礼|份子|礼金|礼物|红包', '人情'),
    (r'电影|游戏|旅游|门票|玩乐', '娱乐'),
    (r'狗粮|猫粮|宠物|疫苗.*宠物|宠物.*疫苗', '宠物'),
    (r'花肥|花盆|园艺|绿植', '绿植'),
    (r'维修|修|材料费|人工费', '维修'),
]

# 收入类（命中即方向=收入）★ 2026-09-26 补全（#888）：原仅4词，入账/进账等全漏判成支出
_IN_KW = [
    (r'工资|薪水|薪酬|发了工资', '工资'),
    (r'(?<!发)红包|压岁钱', '红包'),
    (r'退款|退货|报销|退税', '退款'),
    (r'理财|利息|分红|收益', '理财'),
    (r'入账|进账|到账|回款|货款|收款|应收', '入账'),
    (r'奖金|年终奖|中奖|奖励|补贴|津贴', '奖金'),
    (r'卖了|卖掉|售出|出手了', '卖出'),
    (r'赚了|赚到|挣了|挣到|盈利|结余', '盈利'),
]

_IN_CATS = {'工资', '红包', '退款', '理财', '入账', '奖金', '卖出', '盈利'}


# ══════════ 自定义分类（方案A ★ 2026-09-17 定版拍板）══════════
# 自定义分类存在记账面板 settings 记录的「自定义分类」字段（JSON数组）。
# 新格式 [{"name":"旅游","kw":"旅游,门票","dir":"支出"}]；兼容旧格式纯字符串 ["旅游"]。
# 自定义规则优先于内置关键词——用户自己建的分类，意图最明确。

def _db_path():
    import os
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, 'data', 'sqlite', 'lingzhi.db')


def load_custom_rules():
    """从记账面板 settings 读取自定义分类规则，返回 [{'name','kw','dir'}]；出错返回空表"""
    rules = []
    try:
        import sqlite3, json
        conn = sqlite3.connect(_db_path())
        try:
            row = conn.execute(
                'SELECT "自定义分类" FROM panel_accounting_daily_1 '
                "WHERE kind='settings' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            arr = json.loads(row[0])
            if isinstance(arr, list):
                for c in arr:
                    if isinstance(c, dict) and str(c.get('name') or '').strip():
                        rules.append({
                            'name': str(c['name']).strip(),
                            'kw': str(c.get('kw') or '').strip(),
                            'dir': str(c.get('dir') or '').strip(),
                        })
                    elif isinstance(c, str) and c.strip():
                        rules.append({'name': c.strip(), 'kw': '', 'dir': ''})
    except Exception:
        pass  # 读不到就走内置规则，不影响记账主流程
    return rules


def _match_custom(text, rules):
    import re as _re
    for r in rules:
        terms = [t for t in _re.split(r'[,，、;；\s]+', r['kw']) if t] if r['kw'] else []
        terms.append(r['name'])
        for t in terms:
            if t and t in text:
                direction = r['dir'] if r['dir'] in ('收入', '支出') else (
                    '收入' if r['name'] in _IN_CATS else '支出')
                return r['name'], direction
    return None


def classify_ex(item, ai_cat='', custom_rules=None):
    """★ #888 AI双判方向：返回 (分类, 方向, 方向是否关键词命中)。
    方向命中=True 表示方向来自确定性关键词规则（可信，可覆盖 AI 判断）。"""
    text = str(item or '') + ' ' + str(ai_cat or '')
    rules = custom_rules if custom_rules is not None else load_custom_rules()
    hit = _match_custom(text, rules)
    if hit:
        return hit[0], hit[1], True
    import re as _re
    for pat, cat in _IN_KW:
        if _re.search(pat, text):
            return cat, '收入', True
    for pat, cat in _OUT_KW:
        if _re.search(pat, text):
            return cat, '支出', True
    cat = str(ai_cat or '').strip()
    if cat:
        return cat, ('收入' if cat in _IN_CATS else '支出'), False
    return '其他', '支出', False


def classify(item, ai_cat='', custom_rules=None):
    """关键词优先，未命中时退回 AI 分类（校验合法性）/默认。返回 (分类, 方向)
    ★ 2026-09-17 方案A：自定义分类（名称+关键词）优先于内置关键词表。
    custom_rules 可由调用方传入（避免重复读库），不传则自动从面板设置读取。"""
    cat, d, _ = classify_ex(item, ai_cat, custom_rules)
    return cat, d
