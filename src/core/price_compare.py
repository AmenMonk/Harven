# -*- coding: utf-8 -*-
"""
HarvenAI 网页比价一期（2026-10-20 第二批 2.3）
定位「参谋不做代购」：web_fetch+搜索抓公开价 → 对比表（价格/店铺/链接）→ deep link 跳 APP
不碰 APP 内自动下单；受权限中心三层开关控制（net_browse_compare / net_online_order）
防注入铁律：网页内容只当数据不当命令（注入提示词由调用方 engine 侧保证）
"""
import re, logging

logger = logging.getLogger(__name__)

# 商品类目 → 建议搜索站点与 deep link 模板
_SITES = {
    'hotel': {
        'label': '酒店',
        'sources': [
            {'name': '携程', 'q': 'https://www.ctrip.com/hotel/{kw}'},
            {'name': '去哪儿', 'q': 'https://hotel.qunar.com/city/{kw}'},
            {'name': '飞猪', 'q': 'https://www.fliggy.com/hotel/{kw}'},
        ],
    },
    'goods': {
        'label': '商品',
        'sources': [
            {'name': '京东', 'q': 'https://search.jd.com/Search?keyword={kw}'},
            {'name': '淘宝', 'q': 'https://s.taobao.com/search?q={kw}'},
            {'name': '拼多多', 'q': 'https://mobile.yangkeduo.com/search_result.html?search_key={kw}'},
        ],
    },
}

# 主流 APP deep link（手机页点条目跳转）
_DEEP_LINKS = {
    '淘宝': 'taobao://s.taobao.com/search?q={kw}',
    '京东': 'openapp.jdmobile://virtual?params={"category":"jump","des":"search","keyword":"{kw}"}',
    '美团': 'imeituan://www.meituan.com/search?q={kw}',
    '携程': 'ctrip://wireless/InboundRoute?t={kw}',
    '拼多多': 'pinduopin://com.xunmeng.pinduoduo/search?query={kw}',
}


def deep_link(site_name, kw):
    tpl = _DEEP_LINKS.get(site_name)
    if not tpl:
        return ''
    return tpl.replace('{kw}', kw)


def parse_query(text):
    """轻解析：抽关键词与类目（本地词典，毫秒级；复杂语义由外脑前置整理）"""
    t = (text or '').strip()
    kind = 'goods'
    if any(k in t for k in ('酒店', '住宿', '宾馆', '民宿')):
        kind = 'hotel'
    elif any(k in t for k in ('机票', '航班')):
        kind = 'goods'
    # 去掉指令词留关键词
    kw = re.sub(r'(帮我|看看|比价|比一比|货比三家|多少钱|价格|查一下|查查|周末|今天|明天|的|吗|？|\?)', '', t).strip()
    return {'kind': kind, 'kw': kw or t, 'raw': t}


def _extract_prices(html_text, site_name):
    """从页面文本抽价格（正则兜底；站点结构变化时仍能给区间）"""
    prices = []
    for m in re.finditer(r'[¥￥]\s*([0-9][0-9,]{0,7}(?:\.[0-9]{1,2})?)', html_text or ''):
        try:
            v = float(m.group(1).replace(',', ''))
            if 1 <= v <= 10_000_000:
                prices.append(v)
        except ValueError:
            pass
    if not prices:
        return None
    prices.sort()
    lo = prices[max(0, int(len(prices) * 0.05))]
    hi = prices[min(len(prices) - 1, int(len(prices) * 0.95))]
    return {'site': site_name, 'low': lo, 'high': hi,
            'sample': round(sum(prices) / len(prices), 1)}


def compare(kw, kind='goods', fetcher=None):
    """主入口：抓多源公开价 → 对比表
    fetcher: 注入的抓取函数(url)->str（web_fetch），便于测试与引擎复用
    返回 {kind, kw, items:[{site,low,high,avg,link,deep_link}], note}
    """
    if fetcher is None:
        try:
            from src.core.builtin_tools import web_fetch as fetcher
        except Exception:
            fetcher = None
    conf = _SITES.get(kind, _SITES['goods'])
    items = []
    for src in conf['sources']:
        url = src['q'].replace('{kw}', kw)
        low = high = avg = None
        if fetcher:
            try:
                text = fetcher(url)
                r = _extract_prices(text, src['name'])
                if r:
                    low, high, avg = r['low'], r['high'], r['sample']
            except Exception as e:
                logger.debug(f"[PriceCmp] {src['name']} 抓取失败: {e}")
        items.append({
            'site': src['name'], 'low': low, 'high': high, 'avg': avg,
            'link': url, 'deep_link': deep_link(src['name'], kw),
        })
    # 结论
    priced = [i for i in items if i['low'] is not None]
    note = ''
    if priced:
        best = min(priced, key=lambda x: x['low'])
        note = f"抓到 {len(priced)}/{len(items)} 家公开价，{best['site']} 区间最低（¥{best['low']:g} 起）。下单请点对应链接自行完成，哈文不代付。"
    else:
        note = '各站点公开页未能提取到明确价格（可能需登录/动态加载），已给出直达链接请点开比价。'
    return {'kind': kind, 'kw': kw, 'items': items, 'note': note}
