"""
Harven AI - 数据保险箱 API
提供密码锁定、隐私模式、访问日志等接口
Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import logging
logger = logging.getLogger(__name__)
import os
import time
import json
import sqlite3
import hashlib
import hmac
from flask import Blueprint, request, jsonify, session
from pathlib import Path

BP = Blueprint('vault', __name__, url_prefix='/api/vault')

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
VAULT_DB = DATA_DIR / 'vault_logs.db'

# ============ 工具函数 ============

def _get_admin_password():
    """从 config.yaml 读取管理员密码（用于锁定/解锁验证）"""
    from src.utils.config import Config
    config = Config()
    # 先尝试 security.admin_password，再尝试 admin_password（兼容性）
    admin_pwd = config.get('security', {}).get('admin_password', '')
    if not admin_pwd:
        admin_pwd = config.get('admin_password', '')
    return admin_pwd

def _hash_password(pwd: str) -> str:
    """简单哈希（用于比对，不存储明文）"""
    return hashlib.sha256(pwd.encode('utf-8')).hexdigest()

def _init_vault_db():
    """初始化保险箱日志数据库"""
    if not VAULT_DB.exists():
        conn = sqlite3.connect(str(VAULT_DB))
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS access_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now', 'localtime')),
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                ip TEXT DEFAULT '',
                user_agent TEXT DEFAULT ''
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS privacy_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT,
                privacy_mode INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        conn.commit()
        conn.close()

def log_access(action: str, detail: str = ''):
    """记录访问日志"""
    _init_vault_db()
    ip = request.remote_addr or '127.0.0.1'
    ua = request.headers.get('User-Agent', '')[:256]
    try:
        conn = sqlite3.connect(str(VAULT_DB))
        c = conn.cursor()
        c.execute(
            'INSERT INTO access_logs (action, detail, ip, user_agent) VALUES (?, ?, ?, ?)',
            (action, detail, ip, ua)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Vault] 日志写入失败: {e}")

# ============ API 路由 ============

@BP.route('/status', methods=['GET'])
def get_vault_status():
    """获取保险箱状态（是否锁定、隐私模式等）"""
    locked = session.get('vault_locked', False)
    privacy_mode = session.get('privacy_mode', False)
    
    # 读取统计数据
    _init_vault_db()
    try:
        conn = sqlite3.connect(str(VAULT_DB))
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM access_logs')
        log_count = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM access_logs WHERE action LIKE "%unlock%" OR action LIKE "%lock%"')
        auth_count = c.fetchone()[0]
        conn.close()
    except:
        log_count = 0
        auth_count = 0
    
    return jsonify({
        'locked': locked,
        'privacy_mode': privacy_mode,
        'log_count': log_count,
        'auth_count': auth_count
    })

@BP.route('/lock', methods=['POST'])
def lock_vault():
    """锁定保险箱"""
    data = request.get_json(force=True, silent=True) or {}
    
    admin_pwd = _get_admin_password()
    if not admin_pwd:
        # 未设置管理员密码时，直接锁定
        session['vault_locked'] = True
        log_access('lock_success')
        return jsonify({'success': True, 'locked': True})
    
    password = data.get('password', '')
    if password != admin_pwd:
        log_access('lock_failed', f'IP: {request.remote_addr}')
        return jsonify({'error': '密码错误'}), 401
    
    session['vault_locked'] = True
    log_access('lock_success')
    return jsonify({'success': True, 'locked': True})

@BP.route('/unlock', methods=['POST'])
def unlock_vault():
    """解锁保险箱"""
    data = request.get_json(force=True, silent=True) or {}
    password = data.get('password', '')
    
    admin_pwd = _get_admin_password()
    if not admin_pwd:
        # 未设置管理员密码时，直接解锁
        session['vault_locked'] = False
        return jsonify({'success': True, 'locked': False})
    
    if password != admin_pwd:
        log_access('unlock_failed', f'IP: {request.remote_addr}')
        return jsonify({'error': '密码错误'}), 401
    
    session['vault_locked'] = False
    log_access('unlock_success')
    return jsonify({'success': True, 'locked': False})

@BP.route('/set-privacy-mode', methods=['POST'])
def set_privacy_mode():
    """设置当前对话的隐私模式"""
    # 检查是否锁定
    if session.get('vault_locked', False):
        return jsonify({'error': '保险箱已锁定，请先解锁'}), 403
    
    data = request.get_json(force=True, silent=True) or {}
    # 兼容前端可能传 enabled 或 privacy_mode 两种 key
    enabled = data.get('enabled', data.get('privacy_mode', False))
    conversation_id = data.get('conversation_id', '')
    
    session['privacy_mode'] = enabled
    
    if conversation_id:
        _init_vault_db()
        try:
            conn = sqlite3.connect(str(VAULT_DB))
            c = conn.cursor()
            c.execute(
                'INSERT OR REPLACE INTO privacy_sessions (conversation_id, privacy_mode) VALUES (?, ?)',
                (conversation_id, 1 if enabled else 0)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Vault] 隐私会话记录失败: {e}")
    
    conv_short = conversation_id[:8] if conversation_id else ""
    log_access('privacy_mode_set', f'enabled={enabled}, conv={conv_short}')
    return jsonify({'success': True, 'privacy_mode': enabled})

@BP.route('/logs', methods=['GET'])
def get_access_logs():
    """获取访问日志（需要验证）"""
    if session.get('vault_locked', False):
        return jsonify({'error': '保险箱已锁定'}), 403
    
    _init_vault_db()
    try:
        conn = sqlite3.connect(str(VAULT_DB))
        c = conn.cursor()
        c.execute('SELECT id, timestamp, action, detail FROM access_logs ORDER BY id DESC LIMIT 100')
        rows = c.fetchall()
        conn.close()
        
        logs = []
        for row in rows:
            logs.append({
                'id': row[0],
                'timestamp': row[1],
                'action': row[2],
                'detail': row[3]
            })
        
        return jsonify({'logs': logs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@BP.route('/stats', methods=['GET'])
def get_data_stats():
    """获取数据统计（对话数、知识条数等）"""
    if session.get('vault_locked', False):
        return jsonify({'error': '保险箱已锁定'}), 403
    
    stats = {}
    
    # 主数据库统计
    db_path = PROJECT_ROOT / 'data' / 'sqlite' / 'lingzhi.db'
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            
            c.execute('SELECT COUNT(*) FROM conversations')
            stats['conversations'] = c.fetchone()[0]
            
            c.execute('SELECT COUNT(*) FROM messages')
            stats['messages'] = c.fetchone()[0]
            
            conn.close()
        except:
            stats['conversations'] = 0
            stats['messages'] = 0
    
    # 隔离数据库统计
    isolated_dir = PROJECT_ROOT / 'data' / 'isolated_dbs'
    if isolated_dir.exists():
        for db_file in ['privacy.db', 'transient.db', 'commercial.db']:
            db_path = isolated_dir / db_file
            if db_path.exists():
                try:
                    conn = sqlite3.connect(str(db_path))
                    c = conn.cursor()
                    c.execute('SELECT COUNT(*) FROM sqlite_master WHERE type="table"')
                    table_count = c.fetchone()[0]
                    stats[db_file.replace('.db', '')] = f'{table_count} tables'
                    conn.close()
                except Exception as _e: logger.debug(f"[vault_routes] 忽略: {_e}")
    
    return jsonify(stats)

@BP.route('/check-lock', methods=['GET'])
def check_lock():
    """检查当前会话是否锁定（供前端轮询）"""
    locked = session.get('vault_locked', False)
    return jsonify({'locked': locked})
