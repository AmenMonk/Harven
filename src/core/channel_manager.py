"""
Channel Manager - 多渠道接入管理
支持飞书、钉钉、Telegram、自定义Webhook
存储：SQLite (data/sqlite/lingzhi.db)
"""
import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "sqlite" / "lingzhi.db"


def init_channel_db():
    """初始化渠道表"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            config TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()


def get_channel_manager():
    """单例获取 channel_manager"""
    if not hasattr(get_channel_manager, '_instance'):
        get_channel_manager._instance = ChannelManager()
    return get_channel_manager._instance


class ChannelManager:
    """渠道管理器 - 增删查改渠道"""

    def __init__(self):
        init_channel_db()

    def get_all_channels(self):
        """获取所有渠道"""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM channels ORDER BY created_at DESC").fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d['config'] = json.loads(d.get('config') or '{}')
            except Exception:
                d['config'] = {}
            result.append(d)
        return result

    def add_channel(self, data):
        """添加渠道"""
        channel_id = uuid.uuid4().hex[:12]
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute("""
                INSERT INTO channels (id, type, name, config, enabled)
                VALUES (?, ?, ?, ?, 1)
            """, [channel_id, data.get('type', 'webhook'), data.get('name', '未命名'),
                  json.dumps(data, ensure_ascii=False)])
            conn.commit()
            return {"success": True, "channel_id": channel_id}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def remove_channel(self, channel_id):
        """删除渠道"""
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute("DELETE FROM channels WHERE id=?", [channel_id])
            conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def get_channel(self, channel_id):
        """获取单个渠道"""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM channels WHERE id=?", [channel_id]).fetchone()
        conn.close()
        if not row:
            return None
        d = dict(row)
        try:
            d['config'] = json.loads(d.get('config') or '{}')
        except Exception:
            d['config'] = {}
        return d

    def dispatch(self, channel_id, data, headers):
        """分发消息到对应渠道 - 简单实现：由调用方处理"""
        # 实际的消息分发由webhook_receive内联处理
        return {"success": True, "channel_id": channel_id}
