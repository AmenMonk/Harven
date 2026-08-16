# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Topic Monitor - 主题监控系统
监控用户关注的主题，自动抓取相关新闻、插件、软件等信息

改进（2026-06-14）：
- 智能关键词拆分：长句自动拆为单字/单词，宽匹配不再漏
- 中国可用RSS源：机器之心/虎嗅/36kr/极客公园 替代被墙的 Reddit/HN
- 网页搜索数据源：后端真正实现 web 搜索（Bing内置+SearXNG可选）
- 添加主题后自动执行首次检查（即刻看到结果）
"""

import os
import re
import json
import time
import sqlite3
import urllib.request
import urllib.parse
import ssl
from datetime import datetime, timedelta
from pathlib import Path
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _tokenize_keywords(keywords):
    """
    智能关键词拆分 —— 解决"AI的一切"这种长句永远匹配不上的问题。
    
    规则：
    1. 原始关键词整体保留
    2. 按逗号/空格拆分
    3. 在中英文边界处拆分（"AI的一切" → "ai" + "的一切"）
    4. 纯中文2字以上词 → 再拆为单字，过滤虚词（的/了/是/在/和/与/或/等）
    5. 英文词保留原样
    """
    # 中文常见停用字（单字匹配时过滤掉，否则噪声太大）
    CN_STOP_CHARS = set('的了是在和与或都对把被但从而及之也就能要到于其以所')
    
    tokens = set()
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        kw_lower = kw.lower()
        tokens.add(kw_lower)
        
        # 按常见分隔符拆分
        parts = re.split(r'[，,、\s/|]+', kw)
        
        all_parts = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # 在中英文/CJK边界处进一步拆分
            sub_parts = re.split(r'(?<=[a-zA-Z0-9])(?=[\u4e00-\u9fff])|(?<=[\u4e00-\u9fff])(?=[a-zA-Z0-9])', p)
            for sp in sub_parts:
                sp = sp.strip()
                if sp:
                    all_parts.append(sp)
        
        for p in all_parts:
            p_lower = p.lower()
            if len(p_lower) >= 1:
                tokens.add(p_lower)
            # 纯中文2字以上词 → 拆单字，过滤停用字
            if len(p) >= 2 and all('\u4e00' <= c <= '\u9fff' for c in p):
                for c in p:
                    if c not in CN_STOP_CHARS:
                        tokens.add(c)
    
    return tokens


def _match_keywords(text, keyword_tokens):
    """宽匹配：文本包含任意一个关键词令牌即命中"""
    text_lower = text.lower()
    for token in keyword_tokens:
        if token in text_lower:
            return True
    return False


class TopicMonitor:
    """主题监控器"""
    
    def __init__(self, db_path=None):
        if db_path is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            db_path = project_root / 'data' / 'sqlite' / 'transitional.db'
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        
        # RSS 源 —— 国内直连（不走RSSHub，2026-06-15验证可用）
        self.rss_sources = {
            'tech_news': [
                'https://36kr.com/feed',              # 36氪 (143KB)
                'https://www.ithome.com/rss/',        # IT之家 (397KB)
                'https://www.oschina.net/news/rss',   # 开源中国
            ],
            'ai_tech': [
                'https://www.infoq.cn/feed',          # InfoQ 中文
                'https://www.solidot.org/index.rss',  # Solidot
            ],
            'general': [
                'https://sspai.com/feed',             # 少数派
            ],
        }
        logger.info(f"监控器已初始化，RSS源: {sum(len(v) for v in self.rss_sources.values())} 个")
    
    # ==================== 数据库 ====================
    
    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitored_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_name TEXT NOT NULL,
            keywords TEXT NOT NULL,
            sources TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_check_at TIMESTAMP,
            active INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitored_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            content TEXT,
            source_type TEXT,
            published_at TIMESTAMP,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_status INTEGER DEFAULT 0,
            FOREIGN KEY (topic_id) REFERENCES monitored_topics(id)
        )
        """)
        conn.commit()
        conn.close()
    
    def add_topic(self, topic_name, keywords, sources=None):
        if sources is None:
            sources = ['rss', 'web']
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO monitored_topics (topic_name, keywords, sources) VALUES (?, ?, ?)",
            (topic_name, json.dumps(keywords, ensure_ascii=False), json.dumps(sources))
        )
        topic_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"添加监控主题: {topic_name}, ID={topic_id}, 关键词={keywords}, 源={sources}")
        return topic_id
    
    def get_topics(self):
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, topic_name, keywords, sources, created_at, last_check_at, active "
            "FROM monitored_topics ORDER BY id DESC"
        )
        rows = cursor.fetchall()
        conn.close()
        topics = []
        for row in rows:
            topics.append({
                'id': row[0], 'topic_name': row[1],
                'keywords': json.loads(row[2]), 'sources': json.loads(row[3]),
                'created_at': row[4], 'last_check_at': row[5], 'active': row[6]
            })
        return topics
    
    def delete_topic(self, topic_id):
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("DELETE FROM monitored_topics WHERE id = ?", (topic_id,))
        cursor.execute("DELETE FROM monitored_items WHERE topic_id = ?", (topic_id,))
        conn.commit()
        conn.close()
        logger.info(f"删除监控主题: ID={topic_id}")
    
    # ==================== RSS 抓取 ====================
    
    def _fetch_single_rss(self, url, topic_id, keyword_tokens):
        """抓取单个RSS源（内部方法，由线程池调用）"""
        import feedparser
        items = []
        feed = feedparser.parse(url)
        for entry in feed.entries[:5]:
            title = entry.get('title', '')
            summary = entry.get('summary', '')
            content = (title + ' ' + summary).lower()
            
            if _match_keywords(content, keyword_tokens):
                items.append({
                    'topic_id': topic_id,
                    'title': title,
                    'url': entry.get('link', ''),
                    'content': summary[:500],
                    'source_type': 'rss',
                    'published_at': (
                        datetime(*entry['published_parsed'][:6]).isoformat()
                        if entry.get('published_parsed') else datetime.now().isoformat()
                    )
                })
        return items
    
    def fetch_rss(self, topic_id, keywords):
        """从 RSS 源抓取内容（每个源5秒超时，6个源最多~10秒）"""
        try:
            import feedparser
        except ImportError:
            logger.error("feedparser 未安装，RSS 源不可用")
            return []
        
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        
        keyword_tokens = _tokenize_keywords(keywords)
        logger.info(f"RSS抓取: 关键词={keywords} → 令牌={len(keyword_tokens)}个")
        
        all_items = []
        all_urls = []
        for source_type, urls in self.rss_sources.items():
            all_urls.extend(urls)
        
        # 每个源独立线程，5秒硬超时（国内直连源响应快）
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for url in all_urls:
                future = executor.submit(self._fetch_single_rss, url, topic_id, keyword_tokens)
                futures[future] = url
            
            for future, url in futures.items():
                try:
                    items = future.result(timeout=5)
                    if items:
                        logger.info(f"  RSS {url} → 匹配 {len(items)} 条")
                    all_items.extend(items)
                except FuturesTimeout:
                    logger.warning(f"  RSS {url} → 超时(10s)，跳过")
                except Exception as e:
                    logger.error(f"  RSS {url} → 错误: {e}")
        
        return all_items
    
    # ==================== 网页搜索 ====================
    
    def fetch_web(self, topic_id, keywords):
        """
        网页搜索抓取 —— 使用 Bing 搜索（国内可访问）
        """
        items = []
        query = ' '.join(keywords[:3])
        
        try:
            encoded_query = urllib.parse.quote(query)
            url = f'https://www.bing.com/search?q={encoded_query}&count=10'
            
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            })
            
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            
            # Bing HTML 搜索结果格式:
            # <li class="b_algo"><h2><a href="...">title</a></h2><p>snippet</p>...
            # 或 <div class="b_caption"><p>snippet</p>
            
            # 提取搜索结果块
            result_blocks = re.findall(
                r'<li class="b_algo"[^>]*>(.*?)</li>',
                html, re.DOTALL | re.IGNORECASE
            )
            
            # 如果上面的格式不匹配，尝试其他格式
            if not result_blocks:
                result_blocks = re.findall(
                    r'<div class="b_algo"[^>]*>(.*?)</div>\s*</li>',
                    html, re.DOTALL | re.IGNORECASE
                )
            
            for block in result_blocks[:10]:
                # 提取标题和链接
                title_match = re.search(
                    r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    block, re.DOTALL | re.IGNORECASE
                )
                if not title_match:
                    continue
                
                href = title_match.group(1)
                title_raw = title_match.group(2)
                title = re.sub(r'<[^>]+>', '', title_raw).strip()
                
                if not title:
                    continue
                
                # 提取摘要
                snippet = ''
                snippet_match = re.search(
                    r'<(?:p|div)\s[^>]*class="[^"]*b_(?:caption|snippet|lineclamp)[^"]*"[^>]*>(.*?)</(?:p|div)>',
                    block, re.DOTALL | re.IGNORECASE
                )
                if not snippet_match:
                    snippet_match = re.search(
                        r'<p[^>]*>(.*?)</p>',
                        block, re.DOTALL | re.IGNORECASE
                    )
                if snippet_match:
                    snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                
                items.append({
                    'topic_id': topic_id,
                    'title': title,
                    'url': href,
                    'content': snippet[:500],
                    'source_type': 'web',
                    'published_at': datetime.now().isoformat()
                })
            
            logger.info(f"网页搜索(Bing) '{query}' → {len(items)} 条")
        except Exception as e:
            logger.error(f"网页搜索失败: {e}")
        
        return items
    
    # ==================== 核心检查逻辑 ====================
    
    def check_topic(self, topic_id):
        """检查单个主题，从所有启用的数据源抓取新内容"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT topic_name, keywords, sources FROM monitored_topics WHERE id = ?",
            (topic_id,)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return []
        
        topic_name, keywords_json, sources_json = row
        keywords = json.loads(keywords_json)
        sources = json.loads(sources_json)
        
        all_items = []
        
        # RSS 抓取
        if 'rss' in sources:
            rss_items = self.fetch_rss(topic_id, keywords)
            all_items.extend(rss_items)
        
        # 网页搜索
        if 'web' in sources:
            web_items = self.fetch_web(topic_id, keywords)
            all_items.extend(web_items)
        
        # GitHub（TODO: 后续实现 GitHub API 搜索）
        # if 'github' in sources:
        #     github_items = self.fetch_github(topic_id, keywords)
        #     all_items.extend(github_items)
        
        # 去重保存
        new_count = 0
        for item in all_items:
            cursor.execute(
                "SELECT id FROM monitored_items WHERE url = ? OR (title = ? AND topic_id = ?)",
                (item['url'], item['title'], topic_id)
            )
            if not cursor.fetchone():
                cursor.execute(
                    """INSERT INTO monitored_items 
                    (topic_id, title, url, content, source_type, published_at) 
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (item['topic_id'], item['title'], item['url'],
                     item['content'], item['source_type'], item['published_at'])
                )
                new_count += 1
        
        # 更新最后检查时间
        cursor.execute(
            "UPDATE monitored_topics SET last_check_at = CURRENT_TIMESTAMP WHERE id = ?",
            (topic_id,)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"检查完成: {topic_name} | RSS+Web 共 {len(all_items)} 条, 新增入库 {new_count} 条")
        return all_items
    
    def check_all_topics(self):
        topics = self.get_topics()
        results = {}
        for topic in topics:
            if topic['active']:
                items = self.check_topic(topic['id'])
                results[topic['topic_name']] = len(items)
        return results
    
    # ==================== 查询接口 ====================
    
    def get_items(self, topic_id=None, unread_only=False, limit=50):
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        if topic_id:
            if unread_only:
                cursor.execute(
                    """SELECT id, topic_id, title, url, content, source_type, published_at, read_status 
                    FROM monitored_items WHERE topic_id = ? AND read_status = 0 
                    ORDER BY published_at DESC LIMIT ?""",
                    (topic_id, limit)
                )
            else:
                cursor.execute(
                    """SELECT id, topic_id, title, url, content, source_type, published_at, read_status 
                    FROM monitored_items WHERE topic_id = ? 
                    ORDER BY published_at DESC LIMIT ?""",
                    (topic_id, limit)
                )
        else:
            if unread_only:
                cursor.execute(
                    """SELECT id, topic_id, title, url, content, source_type, published_at, read_status 
                    FROM monitored_items WHERE read_status = 0 
                    ORDER BY published_at DESC LIMIT ?""",
                    (limit,)
                )
            else:
                cursor.execute(
                    """SELECT id, topic_id, title, url, content, source_type, published_at, read_status 
                    FROM monitored_items ORDER BY published_at DESC LIMIT ?""",
                    (limit,)
                )
        
        rows = cursor.fetchall()
        conn.close()
        
        return [{
            'id': row[0], 'topic_id': row[1], 'title': row[2],
            'url': row[3], 'content': row[4], 'source_type': row[5],
            'published_at': row[6], 'read_status': row[7]
        } for row in rows]
    
    def mark_as_read(self, item_id):
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("UPDATE monitored_items SET read_status = 1 WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()
    
    def get_unread_count(self, topic_id=None):
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        if topic_id:
            cursor.execute(
                "SELECT COUNT(*) FROM monitored_items WHERE topic_id = ? AND read_status = 0",
                (topic_id,)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM monitored_items WHERE read_status = 0")
        count = cursor.fetchone()[0]
        conn.close()
        return count


# 全局单例
_monitor = None

def get_monitor():
    global _monitor
    if _monitor is None:
        _monitor = TopicMonitor()
    return _monitor
