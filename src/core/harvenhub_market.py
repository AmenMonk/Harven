# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
HarvenHub Community Market — 本地社区技能市场
==============================================
Harven AI 内置的本地优先技能/插件/风格包发布与发现平台。
作为未来 harvenhub.com 的前身，所有发布内容先沉淀到本地 SQLite 市场。

功能：
  - 发布：用户将本地 skill/plugin/style 发布到市场
  - 浏览：按分类、热门、最新浏览市场内容
  - 搜索：关键词搜索市场内容
  - 评分：1-5星评分 + 文字评论
  - 下载：从市场下载安装到本地
  - 管理：发布者可以更新/下架自己的内容

数据库表：
  - marketplace_items：已发布项目
  - marketplace_ratings：评分和评论
  - marketplace_categories：分类定义
"""

import os
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
DB_PATH = PROJECT_ROOT / 'data' / 'sqlite' / 'lingzhi.db'
SKILLS_DIR = PROJECT_ROOT / 'skills'
PLUGINS_DIR = PROJECT_ROOT / 'plugins'


# ================ 预设分类 ================

DEFAULT_CATEGORIES = [
    {"id": "ai-assistant", "name": "🤖 AI助手", "desc": "对话助手、角色扮演、问答"},
    {"id": "productivity", "name": "📊 效率工具", "desc": "文档处理、数据分析、自动化"},
    {"id": "development", "name": "💻 开发工具", "desc": "代码生成、调试、API集成"},
    {"id": "creative", "name": "🎨 创意设计", "desc": "图像生成、音乐、视频、3D"},
    {"id": "knowledge", "name": "📚 知识学习", "desc": "教育、研究、知识管理"},
    {"id": "lifestyle", "name": "🏠 生活助手", "desc": "健康、旅行、购物、娱乐"},
    {"id": "integration", "name": "🔗 集成连接", "desc": "IoT、IM、API、数据库连接"},
    {"id": "security", "name": "🔒 安全隐私", "desc": "加密、审计、权限管理"},
    {"id": "fun", "name": "🎮 趣味娱乐", "desc": "游戏、趣味工具、摸鱼"},
    {"id": "other", "name": "📦 其他", "desc": "未分类"},
]


# ================ 数据库初始化 ================

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_marketplace_db():
    """初始化市场相关数据库表"""
    conn = _get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS marketplace_items (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                author TEXT DEFAULT 'Anonymous',
                category_id TEXT DEFAULT 'other',
                item_type TEXT DEFAULT 'skill',  -- skill / plugin / style / knowledge
                version TEXT DEFAULT '1.0.0',
                tags TEXT DEFAULT '[]',          -- JSON array
                icon TEXT DEFAULT '',
                preview_url TEXT DEFAULT '',
                content_path TEXT DEFAULT '',     -- 本地文件路径
                content_hash TEXT DEFAULT '',
                download_count INTEGER DEFAULT 0,
                rating_avg REAL DEFAULT 0.0,
                rating_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'published', -- published / hidden / removed
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS marketplace_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                user_name TEXT DEFAULT 'Anonymous',
                score INTEGER NOT NULL CHECK(score >= 1 AND score <= 5),
                comment TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (item_id) REFERENCES marketplace_items(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS marketplace_categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_marketplace_items_category
                ON marketplace_items(category_id);
            CREATE INDEX IF NOT EXISTS idx_marketplace_items_type
                ON marketplace_items(item_type);
            CREATE INDEX IF NOT EXISTS idx_marketplace_items_status
                ON marketplace_items(status);
            CREATE INDEX IF NOT EXISTS idx_marketplace_items_rating
                ON marketplace_items(rating_avg DESC);
            CREATE INDEX IF NOT EXISTS idx_marketplace_items_created
                ON marketplace_items(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_marketplace_ratings_item
                ON marketplace_ratings(item_id);
        """)

        # 初始化预设分类
        existing = {r['id'] for r in conn.execute("SELECT id FROM marketplace_categories").fetchall()}
        for i, cat in enumerate(DEFAULT_CATEGORIES):
            if cat['id'] not in existing:
                conn.execute(
                    "INSERT INTO marketplace_categories (id, name, description, sort_order) VALUES (?,?,?,?)",
                    (cat['id'], cat['name'], cat['desc'], i)
                )
        conn.commit()
        logger.info("[HarvenHub Market] 数据库表初始化完成")
    finally:
        conn.close()


# ================ 工具函数 ================

def _compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


def _generate_item_id(name: str, author: str) -> str:
    raw = f"{author}:{name}:{datetime.now().timestamp()}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:12]


def _read_skill_content(skill_name: str) -> str | None:
    """读取技能文件内容"""
    for ext in ['.md', '.yaml', '.yml', '.py']:
        fp = SKILLS_DIR / f"{skill_name}{ext}"
        if fp.exists():
            return fp.read_text(encoding='utf-8', errors='ignore')
    return None


def _item_to_dict(row: sqlite3.Row) -> dict:
    """将数据库行转为字典"""
    d = dict(row)
    if isinstance(d.get('tags'), str):
        try:
            d['tags'] = json.loads(d['tags'])
        except (json.JSONDecodeError, TypeError):
            d['tags'] = []
    return d


# ================ 市场操作 ================

def get_categories() -> list[dict]:
    """获取所有分类"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, description FROM marketplace_categories ORDER BY sort_order"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_category_stats() -> list[dict]:
    """获取每个分类的统计（数量+热门）"""
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT c.id, c.name, c.description,
                   COUNT(mi.id) as item_count,
                   COALESCE(AVG(mi.rating_avg), 0) as avg_rating
            FROM marketplace_categories c
            LEFT JOIN marketplace_items mi ON c.id = mi.category_id
                AND mi.status = 'published'
            GROUP BY c.id
            ORDER BY c.sort_order
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def browse_items(category: str = None, item_type: str = None,
                 sort: str = 'newest', limit: int = 20, offset: int = 0) -> dict:
    """浏览市场项目

    Args:
        category: 分类ID
        item_type: 类型 (skill/plugin/style/knowledge)
        sort: 排序方式 (newest/popular/rating/downloads)
        limit: 每页数量
        offset: 偏移量
    """
    conn = _get_db()
    try:
        where = ["status = 'published'"]
        params = []

        if category:
            where.append("category_id = ?")
            params.append(category)
        if item_type:
            where.append("item_type = ?")
            params.append(item_type)

        where_clause = " AND ".join(where)

        sort_map = {
            'newest': 'created_at DESC',
            'popular': 'rating_count DESC',
            'rating': 'rating_avg DESC',
            'downloads': 'download_count DESC',
        }
        order = sort_map.get(sort, 'created_at DESC')

        total = conn.execute(
            f"SELECT COUNT(*) FROM marketplace_items WHERE {where_clause}", params
        ).fetchone()[0]

        rows = conn.execute(
            f"SELECT * FROM marketplace_items WHERE {where_clause} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()

        return {
            'items': [_item_to_dict(r) for r in rows],
            'total': total,
            'limit': limit,
            'offset': offset,
            'has_more': (offset + limit) < total,
        }
    finally:
        conn.close()


def search_market(query: str, category: str = None, item_type: str = None,
                  limit: int = 20) -> list[dict]:
    """搜索市场"""
    conn = _get_db()
    try:
        where = ["status = 'published'"]
        params = []

        if query:
            where.append("(name LIKE ? OR description LIKE ? OR tags LIKE ?)")
            q = f"%{query}%"
            params.extend([q, q, q])
        if category:
            where.append("category_id = ?")
            params.append(category)
        if item_type:
            where.append("item_type = ?")
            params.append(item_type)

        where_clause = " AND ".join(where)

        rows = conn.execute(
            f"SELECT * FROM marketplace_items WHERE {where_clause} "
            f"ORDER BY rating_avg DESC, download_count DESC LIMIT ?",
            params + [limit]
        ).fetchall()

        return [_item_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_item(item_id: str) -> dict | None:
    """获取单个项目详情（含评分列表）"""
    conn = _get_db()
    try:
        item = conn.execute(
            "SELECT * FROM marketplace_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return None

        result = _item_to_dict(item)

        # 加载评分
        ratings = conn.execute(
            "SELECT id, user_name, score, comment, created_at FROM marketplace_ratings "
            "WHERE item_id = ? ORDER BY created_at DESC LIMIT 20",
            (item_id,)
        ).fetchall()
        result['ratings'] = [dict(r) for r in ratings]

        return result
    finally:
        conn.close()


def publish_item(name: str, description: str, author: str,
                 category_id: str = 'other', item_type: str = 'skill',
                 tags: list = None, icon: str = '', version: str = '1.0.0',
                 content: str = '', content_path: str = '') -> dict:
    """发布项目到市场

    Args:
        name: 名称
        description: 描述
        author: 作者
        category_id: 分类ID
        item_type: 类型
        tags: 标签列表
        icon: 图标emoji或URL
        version: 版本号
        content: 内容（如果是新建的）
        content_path: 本地文件路径（如果是已存在的skill）
    """
    if not name.strip():
        return {'success': False, 'error': '名称不能为空'}
    if not description.strip():
        return {'success': False, 'error': '描述不能为空'}

    # 检查重名
    conn = _get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM marketplace_items WHERE name = ? AND author = ? AND status != 'removed'",
            (name.strip(), author.strip())
        ).fetchone()
        if existing:
            return {'success': False, 'error': f'"{name}" 已经发布过了，请使用更新功能'}

        item_id = _generate_item_id(name, author)

        # 如果没有指定content_path，尝试从skills目录找
        if not content_path and not content:
            skill_content = _read_skill_content(name)
            if skill_content:
                content = skill_content
            # 注意：不强制要求本地有文件，允许仅凭描述发布

        content_hash = _compute_content_hash(content) if content else ''

        conn.execute("""
            INSERT INTO marketplace_items
            (id, name, description, author, category_id, item_type,
             version, tags, icon, content_path, content_hash, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?, 'published')
        """, (
            item_id, name.strip(), description.strip(), author.strip(),
            category_id, item_type, version,
            json.dumps(tags or [], ensure_ascii=False),
            icon, content_path, content_hash
        ))
        conn.commit()

        logger.info(f"[HarvenHub Market] 发布成功: {name} by {author}")
        return {
            'success': True,
            'item_id': item_id,
            'message': f'✅ "{name}" 已成功发布到 HarvenHub 社区市场！',
        }
    except Exception as e:
        logger.error(f"[HarvenHub Market] 发布失败: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def update_item(item_id: str, **kwargs) -> dict:
    """更新已发布项目（仅发布者可用）"""
    conn = _get_db()
    try:
        item = conn.execute(
            "SELECT * FROM marketplace_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return {'success': False, 'error': '项目不存在'}

        allowed = ['name', 'description', 'category_id', 'item_type',
                   'version', 'tags', 'icon', 'content_path', 'status']
        updates = {}
        for k, v in kwargs.items():
            if k in allowed and v is not None:
                if k == 'tags' and isinstance(v, list):
                    v = json.dumps(v, ensure_ascii=False)
                updates[k] = v

        if not updates:
            return {'success': False, 'error': '无有效更新字段'}

        updates['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        set_clause = ', '.join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [item_id]
        conn.execute(
            f"UPDATE marketplace_items SET {set_clause} WHERE id = ?", values
        )
        conn.commit()

        return {'success': True, 'message': '✅ 更新成功'}
    except Exception as e:
        logger.error(f"[HarvenHub Market] 更新失败: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def rate_item(item_id: str, user_name: str, score: int, comment: str = '') -> dict:
    """评分

    Args:
        item_id: 项目ID
        user_name: 用户名
        score: 1-5分
        comment: 评论
    """
    if score < 1 or score > 5:
        return {'success': False, 'error': '评分范围为1-5'}

    conn = _get_db()
    try:
        item = conn.execute(
            "SELECT id FROM marketplace_items WHERE id = ? AND status = 'published'",
            (item_id,)
        ).fetchone()
        if not item:
            return {'success': False, 'error': '项目不存在'}

        conn.execute(
            "INSERT INTO marketplace_ratings (item_id, user_name, score, comment) VALUES (?,?,?,?)",
            (item_id, user_name.strip() or 'Anonymous', score, comment.strip())
        )

        # 更新平均分和评分数量
        stats = conn.execute(
            "SELECT COUNT(*) as cnt, AVG(score) as avg FROM marketplace_ratings WHERE item_id = ?",
            (item_id,)
        ).fetchone()

        conn.execute(
            "UPDATE marketplace_items SET rating_avg = ?, rating_count = ? WHERE id = ?",
            (round(stats['avg'], 1), stats['cnt'], item_id)
        )
        conn.commit()

        return {'success': True, 'message': '✅ 评分成功',
                'new_avg': round(stats['avg'], 1), 'new_count': stats['cnt']}
    except Exception as e:
        logger.error(f"[HarvenHub Market] 评分失败: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def increment_download(item_id: str) -> dict:
    """增加下载计数"""
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE marketplace_items SET download_count = download_count + 1 WHERE id = ?",
            (item_id,)
        )
        conn.commit()
        return {'success': True}
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


def get_market_stats() -> dict:
    """获取市场统计"""
    conn = _get_db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM marketplace_items WHERE status = 'published'"
        ).fetchone()[0]

        by_type = {}
        for row in conn.execute(
            "SELECT item_type, COUNT(*) as cnt FROM marketplace_items "
            "WHERE status = 'published' GROUP BY item_type"
        ).fetchall():
            by_type[row['item_type']] = row['cnt']

        total_downloads = conn.execute(
            "SELECT COALESCE(SUM(download_count), 0) FROM marketplace_items "
            "WHERE status = 'published'"
        ).fetchone()[0]

        total_ratings = conn.execute(
            "SELECT COUNT(*) FROM marketplace_ratings"
        ).fetchone()[0]

        return {
            'total_items': total,
            'by_type': by_type,
            'total_downloads': total_downloads,
            'total_ratings': total_ratings,
            'categories': get_category_stats(),
        }
    finally:
        conn.close()


def get_featured_items(limit: int = 6) -> list[dict]:
    """获取推荐项目（高评分+高下载）"""
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT * FROM marketplace_items
            WHERE status = 'published' AND rating_count >= 1
            ORDER BY (rating_avg * 0.6 + (download_count * 1.0 / (download_count + 10)) * 0.4) DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [_item_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_my_published(author: str) -> list[dict]:
    """获取我发布的项目"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM marketplace_items WHERE author = ? ORDER BY created_at DESC",
            (author.strip(),)
        ).fetchall()
        return [_item_to_dict(r) for r in rows]
    finally:
        conn.close()


# ================ 初始化 ================

def ensure_market_ready():
    """确保市场数据库已初始化"""
    init_marketplace_db()
