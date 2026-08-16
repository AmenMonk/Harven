# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Topic Monitor - 主题监控系统
监控用户关注的主题，自动抓取相关新闻、插件、软件等信息
"""

import os
import json
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from src.utils.logger import get_logger

logger = get_logger(__name__)

class TopicMonitor:
    """主题监控器"""
    
    def __init__(self, db_path=None):
        if db_path is None:
            # 使用过渡库（因为监控内容可能包含外界信息）
            # 项目目录: H:\WorkBuddy\LingZhi\data\sqlite\transitional.db
            project_root = Path(__file__).resolve().parent.parent.parent
            db_path = project_root / 'data' / 'sqlite' / 'transitional.db'
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        
        # RSS 源列表（可按主题扩展）
        self.rss_sources = {
            'ai_news': [
                'https://hnrss.org/frontpage',
                'https://www.reddit.com/r/artificial/.rss',
                'https://rsshub.app/github/trending/daily',
            ],
            'ai_education': [
                'https://rsshub.app/zhihu/daily',
            ],
            'tech_news': [
                'https://rsshub.app/36kr/news/latest',
                'https://rsshub.app/sspai/matrix',
            ]
        }
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        # 订阅主题表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitored_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_name TEXT NOT NULL,
            keywords TEXT NOT NULL,  -- JSON array
            sources TEXT,  -- JSON array: ['rss', 'web', 'github']
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_check_at TIMESTAMP,
            active INTEGER DEFAULT 1
        )
        """)
        
        # 监控结果表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitored_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            content TEXT,
            source_type TEXT,  -- 'rss', 'web', 'github'
            published_at TIMESTAMP,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_status INTEGER DEFAULT 0,
            FOREIGN KEY (topic_id) REFERENCES monitored_topics(id)
        )
        """)
        
        conn.commit()
        conn.close()
    
    def add_topic(self, topic_name, keywords, sources=None):
        """添加监控主题"""
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
        
        logger.info(f"添加监控主题: {topic_name}, ID={topic_id}")
        return topic_id
    
    def get_topics(self):
        """获取所有监控主题"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, topic_name, keywords, sources, created_at, last_check_at, active FROM monitored_topics ORDER BY id DESC"
        )
        rows = cursor.fetchall()
        conn.close()
        
        topics = []
        for row in rows:
            topics.append({
                'id': row[0],
                'topic_name': row[1],
                'keywords': json.loads(row[2]),
                'sources': json.loads(row[3]),
                'created_at': row[4],
                'last_check_at': row[5],
                'active': row[6]
            })
        return topics
    
    def delete_topic(self, topic_id):
        """删除监控主题"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM monitored_topics WHERE id = ?", (topic_id,))
        cursor.execute("DELETE FROM monitored_items WHERE topic_id = ?", (topic_id,))
        conn.commit()
        conn.close()
        
        logger.info(f"删除监控主题: ID={topic_id}")
    
    def fetch_rss(self, topic_id, keywords):
        """从 RSS 源抓取内容"""
        try:
            import feedparser
        except ImportError:
            logger.error("feedparser 未安装，请运行: pip install feedparser")
            return []
        
        items = []
        
        # 搜索相关的 RSS 内容
        for source_type, urls in self.rss_sources.items():
            for url in urls:
                try:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:10]:  # 每个源取前10条
                        title = entry.get('title', '')
                        summary = entry.get('summary', '')
                        
                        # 关键词匹配
                        content = (title + ' ' + summary).lower()
                        if any(kw.lower() in content for kw in keywords):
                            items.append({
                                'topic_id': topic_id,
                                'title': title,
                                'url': entry.get('link', ''),
                                'content': summary[:500],  # 截取前500字符
                                'source_type': 'rss',
                                'published_at': entry.get('published_parsed') and 
                                               datetime(*entry['published_parsed'][:6]).isoformat() or 
                                               datetime.now().isoformat()
                            })
                except Exception as e:
                    logger.error(f"RSS 抓取失败 {url}: {e}")
        
        return items
    
    def check_topic(self, topic_id):
        """检查单个主题，抓取新内容"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        # 获取主题信息
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
        
        # 去重并保存
        for item in all_items:
            # 检查是否已存在
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
        
        # 更新最后检查时间
        cursor.execute(
            "UPDATE monitored_topics SET last_check_at = CURRENT_TIMESTAMP WHERE id = ?",
            (topic_id,)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"主题检查完成: {topic_name}, 新增 {len(all_items)} 条")
        return all_items
    
    def check_all_topics(self):
        """检查所有活跃主题"""
        topics = self.get_topics()
        results = {}
        for topic in topics:
            if topic['active']:
                items = self.check_topic(topic['id'])
                results[topic['topic_name']] = len(items)
        return results
    
    def get_items(self, topic_id=None, unread_only=False, limit=50):
        """获取监控结果"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        if topic_id:
            if unread_only:
                cursor.execute(
                    """SELECT id, topic_id, title, url, content, source_type, published_at, read_status 
                    FROM monitored_items 
                    WHERE topic_id = ? AND read_status = 0 
                    ORDER BY published_at DESC LIMIT ?""",
                    (topic_id, limit)
                )
            else:
                cursor.execute(
                    """SELECT id, topic_id, title, url, content, source_type, published_at, read_status 
                    FROM monitored_items 
                    WHERE topic_id = ? 
                    ORDER BY published_at DESC LIMIT ?""",
                    (topic_id, limit)
                )
        else:
            if unread_only:
                cursor.execute(
                    """SELECT id, topic_id, title, url, content, source_type, published_at, read_status 
                    FROM monitored_items 
                    WHERE read_status = 0 
                    ORDER BY published_at DESC LIMIT ?""",
                    (limit,)
                )
            else:
                cursor.execute(
                    """SELECT id, topic_id, title, url, content, source_type, published_at, read_status 
                    FROM monitored_items 
                    ORDER BY published_at DESC LIMIT ?""",
                    (limit,)
                )
        
        rows = cursor.fetchall()
        conn.close()
        
        items = []
        for row in rows:
            items.append({
                'id': row[0],
                'topic_id': row[1],
                'title': row[2],
                'url': row[3],
                'content': row[4],
                'source_type': row[5],
                'published_at': row[6],
                'read_status': row[7]
            })
        return items
    
    def mark_as_read(self, item_id):
        """标记已读"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("UPDATE monitored_items SET read_status = 1 WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()
    
    def get_unread_count(self, topic_id=None):
        """获取未读数量"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        if topic_id:
            cursor.execute(
                "SELECT COUNT(*) FROM monitored_items WHERE topic_id = ? AND read_status = 0",
                (topic_id,)
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM monitored_items WHERE read_status = 0"
            )
        
        count = cursor.fetchone()[0]
        conn.close()
        return count

# 全局单例
_monitor = None

def get_monitor():
    """获取全局监控器实例"""
    global _monitor
    if _monitor is None:
        _monitor = TopicMonitor()
    return _monitor
