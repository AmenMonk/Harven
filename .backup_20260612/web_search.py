# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
联网搜索模块 v2.1
多搜索引擎支持：Bing / DuckDuckGo / Exa / SearXNG
"""
import re
import json
import requests
import logging
from urllib.parse import quote_plus
from typing import List, Dict, Optional

from src.core.independence import bypass_independence

logger = logging.getLogger(__name__)

# 可用搜索引擎列表
AVAILABLE_ENGINES = {
    "bing":       "Bing（国内可用，微软搜索，默认首选）",
    "duckduckgo": "DuckDuckGo（隐私友好，国内可能较慢）",
    "exa":        "Exa（语义搜索，需要 API Key）",
    "searxng":    "SearXNG（自托管元搜索，需要配置 URL）",
}

# 延迟加载 bs4
_bs4_available = None

def _has_bs4():
    global _bs4_available
    if _bs4_available is None:
        try:
            from bs4 import BeautifulSoup
            _bs4_available = True
        except ImportError:
            _bs4_available = False
    return _bs4_available


class WebSearcher:
    """联网搜索器 v2.1 — BeautifulSoup 解析 + 多引擎自动故障转移"""

    def __init__(self, timeout=10, preferred_engine: str = "auto",
                 exa_api_key: str = None, searxng_url: str = None):
        self.timeout = timeout
        self.preferred_engine = preferred_engine
        self.exa_api_key = exa_api_key
        self.searxng_url = searxng_url or "http://localhost:8080"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    # 金融/实时查询关键词（提升 Bing 搜索相关性）
    _FINANCE_KW = ['指数', '股票', '股价', '行情', '涨跌', '上证', '深证', '创业板', '科创板',
                   '纳斯达克', '道琼斯', '标普', '恒生', 'A股', '港股', '美股', '基金',
                   '汇率', '金价', '油价', '期货', '债市', '市值', '市盈率', '财报',
                   '个股', '板块', '涨停', '跌停', '开盘', '收盘', '成交量', '换手率']
    _REALTIME_KW = ['今天', '今日', '现在', '实时', '最新', '当前', '刚刚']

    def search(self, query: str, max_results: int = 5,
               engine: str = None) -> List[Dict]:
        """搜索互联网，自动故障转移"""
        target = engine or self.preferred_engine

        # 金融/实时查询增强：直接使用新浪财经 API（Bing 中文搜索不返回金融结果）
        enhanced_query = query
        if any(kw in query for kw in self._FINANCE_KW):
            if any(kw in query for kw in self._REALTIME_KW):
                # 金融查询 → 新浪财经直连
                finance_data = self._fetch_finance_sina(query)
                if finance_data:
                    # 构造一个"搜索结果"返回，格式与普通搜索一致
                    finance_results = [{
                        "title": f"金融实时数据 — {query}",
                        "url": "https://finance.sina.com.cn/",
                        "snippet": finance_data,
                        "engine": "sina_finance",
                    }]
                    logger.info(f"[WebSearch] 金融数据直连成功: {len(finance_data)}字")
                    return finance_results
                # 新浪失败才走 Bing 兜底
                enhanced_query = f"{query} site:eastmoney.com OR site:sina.com.cn 实时行情"
                logger.info(f"[WebSearch] 金融查询增强(Bing兜底): {query} → {enhanced_query}")

        # auto 模式：Bing 优先，DuckDuckGo 兜底
        if target == "auto":
            engines_to_try = ["bing", "duckduckgo"]
        elif target == "bing":
            engines_to_try = ["bing", "duckduckgo"]
        else:
            engines_to_try = [target, "bing", "duckduckgo"]

        with bypass_independence():
            for eng in engines_to_try:
                try:
                    results = self._search_with_engine(eng, enhanced_query, max_results)
                    if results:
                        logger.info(f"[WebSearch] 使用 {eng} 搜索成功，{len(results)} 条结果")
                        return results
                except Exception as e:
                    logger.warning(f"[WebSearch] {eng} 搜索失败：{e}")
                    continue

        logger.error("[WebSearch] 所有搜索引擎均失败")
        return []

    def _search_with_engine(self, engine: str, query: str, max_results: int) -> List[Dict]:
        if engine == "duckduckgo":
            return self._duckduckgo_search(query, max_results)
        elif engine == "bing":
            return self._bing_search(query, max_results)
        elif engine == "exa":
            return self._exa_search(query, max_results)
        elif engine == "searxng":
            return self._searxng_search(query, max_results)
        else:
            raise ValueError(f"未知搜索引擎：{engine}")

    # ──────────────────── DuckDuckGo ────────────────────

    def _duckduckgo_search(self, query: str, max_results: int) -> List[Dict]:
        """DuckDuckGo HTML 搜索（备用）"""
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query, "kl": "cn-zh"}
        resp = self.session.post(url, data=data, timeout=self.timeout)
        resp.raise_for_status()

        results = []
        html = resp.text
        # DuckDuckGo HTML 版结果结构：.result > .result__a (链接+标题) > .result__snippet
        link_pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL
        )
        snippet_pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>.*?</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)
        snippet_map = {u: s for u, s in snippets}

        for i, (href, title) in enumerate(links):
            if i >= max_results:
                break
            title = re.sub(r'<[^>]+>', '', title).strip()
            if not title or len(title) < 3:
                continue
            snippet = re.sub(r'<[^>]+>', '', snippet_map.get(href, "")).strip()
            results.append({"title": title, "url": href, "snippet": snippet, "engine": "duckduckgo"})
        return results

    # ──────────────────── Bing (BeautifulSoup) ────────────────────

    def _bing_search(self, query: str, max_results: int) -> List[Dict]:
        """Bing 搜索 — BeautifulSoup 解析（含3次重试，应对网络抖动）"""
        import time
        last_error = None
        for attempt in range(3):
            try:
                url = "https://cn.bing.com/search"
                params = {"q": query, "count": str(max_results), "setmkt": "zh-CN"}
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()

                if _has_bs4():
                    return self._bing_parse_bs4(resp.text, max_results)
                else:
                    return self._bing_parse_regex(resp.text, max_results)
            except Exception as e:
                last_error = e
                if attempt < 2:
                    time.sleep(1)
                    continue
        logger.warning(f"[WebSearch] Bing 搜索失败（3次重试后）: {last_error}")
        return []

    def _bing_parse_bs4(self, html: str, max_results: int) -> List[Dict]:
        """BS4 解析 Bing 搜索结果"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        results = []

        for item in soup.select('li.b_algo'):
            if len(results) >= max_results:
                break

            # 提取标题和链接
            link = item.select_one('h2 a') or item.select_one('a[href]')
            if not link:
                continue
            href = link.get('href', '')
            title = link.get_text(strip=True)

            # 跳过非搜索结果（导航、广告等）
            if not title or len(title) < 5:
                continue
            if any(kw in href.lower() for kw in ['bing.com/aclick', 'go.microsoft.com/fwlink']):
                continue  # 跳过广告

            # 提取描述
            desc_elem = (item.select_one('.b_caption p') or
                        item.select_one('.b_lineclamp2') or
                        item.select_one('p'))
            snippet = desc_elem.get_text(strip=True) if desc_elem else ''

            results.append({
                "title": title,
                "url": href,
                "snippet": snippet,
                "engine": "bing",
            })

        return results

    def _bing_parse_regex(self, html: str, max_results: int) -> List[Dict]:
        """降级方案：正则解析（bs4 不可用时）"""
        results = []
        result_pattern = re.compile(
            r'<li[^>]*class="b_algo"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>',
            re.DOTALL
        )
        for match in result_pattern.finditer(html):
            if len(results) >= max_results:
                break
            href, title, snippet = match.groups()
            title = re.sub(r'<[^>]+>', '', title).strip()
            snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            if title and len(title) > 5:
                results.append({"title": title, "url": href, "snippet": snippet, "engine": "bing"})
        return results

    # ──────────────────── Exa ────────────────────

    def _exa_search(self, query: str, max_results: int) -> List[Dict]:
        """Exa 语义搜索（需要 API Key）"""
        if not self.exa_api_key:
            raise ValueError("Exa 搜索需要 API Key（在设置中配置 exa_api_key）")

        resp = self.session.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": self.exa_api_key, "Content-Type": "application/json"},
            json={
                "query": query,
                "numResults": max_results,
                "type": "neural",
                "useAutoprompt": True,
            },
            timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("text", "")[:300],
                "engine": "exa",
                "score": item.get("score", 0),
            })
        return results

    # ──────────────────── SearXNG ────────────────────

    def _searxng_search(self, query: str, max_results: int) -> List[Dict]:
        """SearXNG 自托管元搜索引擎"""
        url = f"{self.searxng_url}/search"
        params = {"q": query, "format": "json", "categories": "general"}
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "engine": "searxng",
            })
        return results

    # ──────────────────── 金融数据直连 ────────────────────

    # 新浪财经股票代码映射
    _SINA_CODE_MAP = {
        "上证指数": "sh000001", "上证": "sh000001", "大盘": "sh000001",
        "深证成指": "sz399001", "深证": "sz399001",
        "创业板指": "sz399006", "创业板": "sz399006",
        "科创50": "sh000688", "科创板": "sh000688",
        "沪深300": "sh000300", "上证50": "sh000016",
        "中证500": "sh000905", "中证1000": "sh000852",
        "恒生指数": "hkHSI", "恒生": "hkHSI",
        "纳斯达克": "int_nasdaq", "道琼斯": "int_dji", "标普500": "int_sp500",
    }

    def _fetch_finance_sina(self, query: str) -> str:
        """新浪财经实时行情直连（纯 HTTP，无需 API Key）"""
        # 匹配股票代码
        code = None
        for name, c in self._SINA_CODE_MAP.items():
            if name in query:
                code = c
                break

        if not code:
            return ""

        try:
            if code.startswith("sh") or code.startswith("sz"):
                url = f"http://hq.sinajs.cn/list={code}"
            elif code.startswith("hk"):
                url = f"http://hq.sinajs.cn/list=rt_hk{code[2:]}" if code != "hkHSI" else "http://hq.sinajs.cn/list=int_hangseng"
            elif code.startswith("int_"):
                int_map = {"int_nasdaq": "int_nasdaq", "int_dji": "int_dji", "int_sp500": "int_sp500"}
                url = f"http://hq.sinajs.cn/list={int_map.get(code, code)}"
            else:
                return ""

            with bypass_independence():
                headers = {"Referer": "https://finance.sina.com.cn"}
                resp = self.session.get(url, headers={**self.session.headers, **headers}, timeout=8)
                resp.encoding = "gb2312"
                data = resp.text.strip()

                if not data or "FAILED" in data:
                    return ""

                return self._parse_sina_data(code, data, query)

        except Exception as e:
            logger.warning(f"[WebSearch] 新浪财经获取失败: {e}")
            return ""

    def _parse_sina_data(self, code: str, raw: str, query: str) -> str:
        """解析新浪财经返回的原始数据"""
        import datetime
        today_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        # 去除 var 前缀
        data = raw.split('"')[1] if '"' in raw else raw
        parts = data.split(",")

        try:
            if code == "sh000001" and len(parts) > 30:
                # 上证指数: 名称,今开,昨收,当前,最高,最低,...,日期,时间
                name = parts[0]
                open_p = parts[1]
                prev_close = parts[2]
                price = parts[3]
                high = parts[4]
                low = parts[5]
                volume = parts[8]  # 成交量(手)
                amount = parts[9]  # 成交额(万)
                change = float(price) - float(prev_close) if price and prev_close else 0
                change_pct = (change / float(prev_close) * 100) if prev_close and float(prev_close) != 0 else 0
                sign = "📈" if change >= 0 else "📉"
                return (
                    f"【{name}】{sign} 当前 {price} 点\n"
                    f"涨跌: {change:+.2f} ({change_pct:+.2f}%)\n"
                    f"今开: {open_p}  昨收: {prev_close}  最高: {high}  最低: {low}\n"
                    f"成交量: {volume}手  成交额: {amount}万元\n"
                    f"数据来源: 新浪财经 | 更新时间: {today_str}"
                )
            elif code == "sh000016" and len(parts) > 30:
                # 上证50
                name = parts[0]; price = parts[3]; prev_close = parts[2]
                change = float(price) - float(prev_close) if price and prev_close else 0
                change_pct = (change / float(prev_close) * 100) if prev_close and float(prev_close) != 0 else 0
                sign = "📈" if change >= 0 else "📉"
                return f"【{name}】{sign} 当前 {price} 点 | 涨跌: {change:+.2f} ({change_pct:+.2f}%) | 来源: 新浪财经 | {today_str}"
            else:
                # 通用解析：取前5个字段
                name = parts[0] if len(parts) > 0 else code
                price = parts[3] if len(parts) > 3 else "N/A"
                prev = parts[2] if len(parts) > 2 else "N/A"
                if price != "N/A" and prev != "N/A":
                    try:
                        change = float(price) - float(prev)
                        change_pct = change / float(prev) * 100
                        sign = "📈" if change >= 0 else "📉"
                        return f"【{name}】{sign} 当前 {price} | 涨跌: {change:+.2f} ({change_pct:+.2f}%) | 昨收: {prev} | 来源: 新浪财经 | {today_str}"
                    except ValueError:
                        pass
                return f"【{name}】最新数据: {data} | 来源: 新浪财经 | {today_str}"
        except Exception as e:
            logger.warning(f"[WebSearch] 新浪数据解析失败: {e}, raw={raw[:200]}")
            return f"金融数据(原始): {raw[:300]} | 来源: 新浪财经"

    # ──────────────────── 网页抓取 ────────────────────

    def fetch_page_content(self, url: str, max_chars: int = 3000) -> str:
        """抓取网页纯文本（含 SSRF 防护 + 编码自动检测）"""
        from src.utils.url_security import validate_url
        is_safe, err_msg = validate_url(url)
        if not is_safe:
            logger.debug(f"[WebSearch] SSRF blocked: {url} — {err_msg}")
            return ""

        try:
            with bypass_independence():
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()

                # 自动检测编码（修复中文网站 GBK/GB2312 乱码问题）
                if resp.encoding and resp.encoding.upper() in ('ISO-8859-1', 'LATIN-1'):
                    resp.encoding = resp.apparent_encoding

                if _has_bs4():
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    # 移除脚本和样式
                    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                        tag.decompose()
                    text = soup.get_text(separator=' ', strip=True)
                    text = re.sub(r'\s+', ' ', text)
                    return text[:max_chars]
                else:
                    text = resp.text
                    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                    text = re.sub(r'<[^>]+>', ' ', text)
                    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                    text = re.sub(r'\s+', ' ', text).strip()
                    return text[:max_chars]
        except Exception as e:
            logger.error(f"[WebSearch] Fetch failed {url}: {e}")
            return ""

    def search_and_fetch(self, query, max_results=3, fetch_top=2):
        """搜索并抓取内容"""
        results = self.search(query, max_results=max_results)
        enriched = []
        for i, result in enumerate(results):
            if i >= fetch_top:
                enriched.append(result)
                continue
            content = self.fetch_page_content(result["url"])
            enriched.append({**result, "content": content})
        return enriched


_web_searcher: Optional[WebSearcher] = None

def get_web_searcher(reload: bool = False) -> WebSearcher:
    """获取搜索器单例，支持从 config.yaml 读取配置"""
    global _web_searcher
    if _web_searcher is None or reload:
        try:
            import yaml
            from pathlib import Path as _Path
            cfg_path = _Path(__file__).parent.parent.parent / "config.yaml"
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
            search_cfg = cfg.get("web_search", {})
            _web_searcher = WebSearcher(
                preferred_engine=search_cfg.get("search_engine", "auto"),
                exa_api_key=search_cfg.get("exa_api_key"),
                searxng_url=search_cfg.get("searxng_url", "http://localhost:8080"),
            )
        except Exception:
            _web_searcher = WebSearcher()
    return _web_searcher


def search(query: str, max_results: int = 5, engine: str = None) -> List[Dict]:
    """便捷函数"""
    return get_web_searcher().search(query, max_results=max_results, engine=engine)
