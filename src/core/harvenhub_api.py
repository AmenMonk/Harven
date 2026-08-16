# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
HarvenHub API — 知识打包与发布系统
将 Harven AI 的知识（对话、知识库、记忆等）打包为可发布到 HarvenHub.com 的知识包
"""
import os
import json
import zipfile
import tempfile
import sqlite3
import re
from pathlib import Path
from datetime import datetime
import logging
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
DATA_DIR = PROJECT_ROOT / 'data'
KB_DIR = DATA_DIR / 'knowledge'
EXPORTS_DIR = DATA_DIR / 'harvenhub_exports'
MAIN_DB_PATH = PROJECT_ROOT / 'data' / 'sqlite' / 'lingzhi.db'

os.makedirs(EXPORTS_DIR, exist_ok=True)


# ==================== 隐私关键词（用于自动过滤） ====================

PRIVACY_PATTERNS = [
    # 密码/密钥
    r'password\s*[:=]\s*\S+',
    r'api[_-]?key\s*[:=]\s*\S+',
    r'secret\s*[:=]\s*\S+',
    r'token\s*[:=]\s*[\w\-\.]{20,}',
    r'access[_-]?key\s*[:=]\s*\S+',
    # 个人信息
    r'\b\d{15,18}\b',  # 身份证号
    r'\b1[3-9]\d{9}\b',  # 手机号
    r'\b[\w\.-]+@[\w\.-]+\.\w{2,}\b',  # 邮箱
    r'\b\d{3}[-.]?\d{8}\b',  # 座机号
    r'\b\d{16,19}\b',  # 银行卡号
    # IP/内网地址
    r'\b(?:192\.168|10\.|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b',
    r'\b127\.0\.0\.1\b',
    # 文件路径（可能泄露用户目录结构）
    r'[A-Z]:\\Users\\[^\\]+',
    r'/home/[^/]+/',
    # 特定敏感词
    r'(?:私钥|private\s*key|证书|certificate|access\s*code)',
]

PRIVACY_LABEL_MAP = {
    0: '身份证号',
    1: '手机号', 
    2: '邮箱',
    3: '座机号',
    4: '银行卡号',
    5: '内网IP',
    6: '本地回环',
    7: '用户目录',
    8: 'Linux主目录',
    9: '密钥/证书',
}


def _scan_privacy(text: str) -> list[dict]:
    """扫描文本中的隐私信息"""
    findings = []
    seen = set()
    for i, pattern in enumerate(PRIVACY_PATTERNS):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            snippet = match.group()
            if snippet not in seen:
                seen.add(snippet)
                findings.append({
                    'type': PRIVACY_LABEL_MAP.get(i, '敏感信息'),
                    'snippet': snippet if len(snippet) <= 50 else snippet[:47] + '...',
                    'position': match.start(),
                })
    return findings


# ==================== 数据源扫描 ====================

def _get_main_db():
    """获取主数据库连接"""
    conn = sqlite3.connect(str(MAIN_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_data_sources() -> dict:
    """获取所有可打包的数据源"""
    sources = {
        'conversations': [],
        'knowledge_base': [],
        'memories': [],
        'specializations': [],
    }

    # 1. 对话
    try:
        conn = _get_main_db()
        rows = conn.execute("""
            SELECT id, title, category, created_at,
                   (SELECT COUNT(*) FROM messages WHERE conversation_id = conversations.id) as msg_count
            FROM conversations WHERE is_archived = 0
            ORDER BY updated_at DESC LIMIT 200
        """).fetchall()
        conn.close()
        for r in rows:
            sources['conversations'].append({
                'id': r['id'],
                'title': r['title'],
                'category': r['category'] or '未分类',
                'msg_count': r['msg_count'],
                'created_at': r['created_at'],
                'selected': False,
                'privacy_risk': 'unknown',
            })
    except Exception as e:
        logger.error(f"[HarvenHub] 加载对话失败: {e}")

    # 2. 知识库
    try:
        if KB_DIR.exists():
            for f in sorted(KB_DIR.iterdir()):
                if f.is_file():
                    sources['knowledge_base'].append({
                        'id': f.name,
                        'name': f.name,
                        'size_kb': round(f.stat().st_size / 1024, 1),
                        'ext': f.suffix.lower(),
                        'selected': False,
                        'privacy_risk': 'unknown',
                    })
    except Exception as e:
        logger.error(f"[HarvenHub] 加载知识库失败: {e}")

    # 3. 记忆（认知锁定）
    try:
        conn = _get_main_db()
        rows = conn.execute(
            "SELECT key, value, source, locked_at FROM cognitive_locks ORDER BY locked_at DESC LIMIT 200"
        ).fetchall()
        conn.close()
        for r in rows:
            sources['memories'].append({
                'id': r['key'],
                'key': r['key'],
                'value_preview': r['value'][:100] if r['value'] else '',
                'source': r['source'] or '主人',
                'locked_at': r['locked_at'],
                'selected': False,
                'privacy_risk': 'unknown',
            })
    except Exception as e:
        logger.error(f"[HarvenHub] 加载记忆失败: {e}")

    # 4. 专项培养
    try:
        conn = _get_main_db()
        rows = conn.execute(
            "SELECT id, name, description, system_prompt FROM specializations ORDER BY name"
        ).fetchall()
        conn.close()
        for r in rows:
            sources['specializations'].append({
                'id': r['id'],
                'name': r['name'],
                'description': r['description'] or '',
                'has_prompt': bool(r['system_prompt']),
                'selected': False,
                'privacy_risk': 'unknown',
            })
    except Exception as e:
        logger.error(f"[HarvenHub] 加载专项培养失败: {e}")

    return sources


def get_item_content(source_type: str, item_id: str) -> str:
    """获取单个数据项的完整内容"""
    content = ''

    if source_type == 'conversations':
        conn = _get_main_db()
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (item_id,)
        ).fetchall()
        conn.close()
        parts = []
        for r in rows:
            role_label = {'user': '👤 用户', 'assistant': '🤖 Harven', 'system': '⚙️ 系统', 'thinking': '💭 思考'}.get(r['role'], r['role'])
            parts.append(f"{role_label}:\n{r['content']}")
        content = '\n\n'.join(parts)

    elif source_type == 'knowledge_base':
        fp = KB_DIR / item_id
        if fp.exists():
            try:
                content = fp.read_text(encoding='utf-8', errors='ignore')[:50000]
            except Exception:
                content = f'[二进制文件: {item_id}]'

    elif source_type == 'memories':
        conn = _get_main_db()
        row = conn.execute("SELECT value FROM cognitive_locks WHERE key = ?", (item_id,)).fetchone()
        conn.close()
        if row:
            content = row['value'] or ''

    elif source_type == 'specializations':
        conn = _get_main_db()
        row = conn.execute(
            "SELECT name, description, system_prompt FROM specializations WHERE id = ?", (item_id,)
        ).fetchone()
        conn.close()
        if row:
            parts = [f"名称: {row['name']}"]
            if row['description']:
                parts.append(f"描述: {row['description']}")
            if row['system_prompt']:
                parts.append(f"系统提示词:\n{row['system_prompt']}")
            content = '\n\n'.join(parts)

    return content


def analyze_privacy(source_type: str, item_id: str) -> dict:
    """分析单个数据项的隐私风险"""
    content = get_item_content(source_type, item_id)
    findings = _scan_privacy(content)

    risk_level = 'safe'
    if len(findings) >= 5:
        risk_level = 'high'
    elif len(findings) >= 2:
        risk_level = 'medium'
    elif len(findings) >= 1:
        risk_level = 'low'

    return {
        'id': item_id,
        'type': source_type,
        'risk_level': risk_level,
        'findings_count': len(findings),
        'findings': findings,
        'content_length': len(content),
    }


def batch_analyze(items: list[dict]) -> list[dict]:
    """批量分析多个数据项"""
    results = []
    for item in items:
        result = analyze_privacy(item.get('type', ''), item.get('id', ''))
        results.append(result)
    return results


def export_package(name: str, description: str, items: list[dict], exclude_privacy: bool = True) -> dict:
    """
    导出知识包为 ZIP 文件
    
    Args:
        name: 知识包名称
        description: 知识包描述
        items: [{'type': 'conversations'|'knowledge_base'|'memories'|'specializations', 'id': '...'}, ...]
        exclude_privacy: 是否排除隐私风险项
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = re.sub(r'[^\w\-_]', '_', name)
    zip_name = f'harvenhub_{safe_name}_{timestamp}.zip'
    zip_path = EXPORTS_DIR / zip_name

    package_manifest = {
        'name': name,
        'description': description,
        'exported_at': datetime.now().isoformat(),
        'harvenhub_version': '1.0',
        'total_items': len(items),
        'exclude_privacy': exclude_privacy,
        'items': [],
    }

    with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            source_type = item.get('type', '')
            item_id = item.get('id', '')

            # 隐私扫描
            analysis = analyze_privacy(source_type, item_id)
            risk = analysis['risk_level']

            # 如果排除隐私且风险高，跳过
            if exclude_privacy and risk == 'high':
                package_manifest['items'].append({
                    'id': item_id,
                    'type': source_type,
                    'status': 'skipped',
                    'reason': f"隐私风险: {risk} ({analysis['findings_count']} 处疑似隐私信息)",
                    'risk': risk,
                })
                continue

            if exclude_privacy and risk == 'medium':
                # 中等风险：警告标记但包含
                status = 'included_with_warning'
            else:
                status = 'included'

            # 获取内容
            content = get_item_content(source_type, item_id)
            if not content:
                package_manifest['items'].append({
                    'id': item_id,
                    'type': source_type,
                    'status': 'empty',
                    'reason': '内容为空',
                    'risk': risk,
                })
                continue

            # 写入 ZIP
            item_folder = f"{source_type}/{item_id}"
            info = zf.getinfo(item_folder + '/') if item_folder + '/' in [i.filename for i in zf.infolist()] else None
            if info is None:
                zf.writestr(f"{item_folder}/content.txt", content)

            # 如果发现了隐私信息，制作净化版
            if exclude_privacy and analysis['findings']:
                sanitized = content
                for f in analysis['findings']:
                    sanitized = sanitized.replace(f['snippet'], '[已脱敏]')
                zf.writestr(f"{item_folder}/content_sanitized.txt", sanitized)

            # 写入分析结果
            zf.writestr(f"{item_folder}/analysis.json",
                        json.dumps(analysis, ensure_ascii=False, indent=2))

            package_manifest['items'].append({
                'id': item_id,
                'type': source_type,
                'status': status,
                'risk': risk,
                'findings_count': analysis['findings_count'],
                'content_length': analysis['content_length'],
            })

        # 写入清单文件
        zf.writestr('manifest.json',
                    json.dumps(package_manifest, ensure_ascii=False, indent=2))

    # 统计
    stats = {
        'included': sum(1 for i in package_manifest['items'] if i['status'] in ('included', 'included_with_warning')),
        'skipped': sum(1 for i in package_manifest['items'] if i['status'] == 'skipped'),
        'empty': sum(1 for i in package_manifest['items'] if i['status'] == 'empty'),
        'total': len(package_manifest['items']),
    }

    return {
        'success': True,
        'file': zip_name,
        'path': str(zip_path),
        'size_kb': round(os.path.getsize(str(zip_path)) / 1024, 1),
        'manifest': package_manifest,
        'stats': stats,
    }


def get_export_history() -> list[dict]:
    """获取导出历史"""
    history = []
    if EXPORTS_DIR.exists():
        for f in sorted(EXPORTS_DIR.glob('harvenhub_*.zip'), key=lambda x: x.stat().st_mtime, reverse=True):
            history.append({
                'name': f.name,
                'size_kb': round(f.stat().st_size / 1024, 1),
                'exported_at': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
            })
    return history[:20]
