# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
HarvenHub 云端客户端
让哈文客户端连接云端 harvenhub.com 进行浏览/搜索/下载/上传
"""

import os
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)

# ★ HarvenHub是哈文官方云端服务，使用bypass绕过出站白名单
def _safe_request(method, url, **kwargs):
    """安全请求包装——绕过independence出站检查"""
    try:
        from src.core.independence import bypass_independence
        with bypass_independence():
            return requests.request(method, url, **kwargs)
    except ImportError:
        # independence模块不可用，直接请求
        return requests.request(method, url, **kwargs)

# 从 config.yaml 读取 HarvenHub 服务器地址
def _get_hub_url():
    """获取 HarvenHub 服务器地址"""
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'config.yaml')
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get('harvenhub_server_url', 'https://harvenhub.com')
    except Exception:
        return 'https://harvenhub.com'


def hub_browse(sort='latest', category=None, limit=30, offset=0):
    """浏览云端市场内容

    Returns:
        dict: {success: bool, items: list, total: int, error: str}
    """
    hub_url = _get_hub_url()
    try:
        params = {'sort': sort, 'limit': limit, 'offset': offset}
        if category:
            params['category'] = category
        resp = _safe_request('GET', f"{hub_url}/api/hub/list", params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return {
                'success': True,
                'items': data.get('items', []),
                'total': data.get('total', len(data.get('items', []))),
                'error': '',
            }
        return {'success': False, 'items': [], 'total': 0, 'error': f'HTTP {resp.status_code}'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'items': [], 'total': 0, 'error': f'无法连接到 {hub_url}，请检查网络'}
    except Exception as e:
        logger.error(f"[HarvenHub] 浏览失败: {e}")
        return {'success': False, 'items': [], 'total': 0, 'error': str(e)}


def hub_search(query, limit=30):
    """搜索云端市场

    Returns:
        dict: {success: bool, items: list, error: str}
    """
    hub_url = _get_hub_url()
    try:
        resp = _safe_request('GET', f"{hub_url}/api/hub/search", params={'q': query, 'limit': limit}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return {
                'success': True,
                'items': data.get('items', []),
                'error': '',
            }
        return {'success': False, 'items': [], 'error': f'HTTP {resp.status_code}'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'items': [], 'error': f'无法连接到 {hub_url}'}
    except Exception as e:
        logger.error(f"[HarvenHub] 搜索失败: {e}")
        return {'success': False, 'items': [], 'error': str(e)}


def hub_detail(item_id):
    """获取内容详情

    Returns:
        dict: {success: bool, item: dict, error: str}
    """
    hub_url = _get_hub_url()
    try:
        resp = _safe_request('GET', f"{hub_url}/api/hub/detail/{item_id}", timeout=15)
        if resp.status_code == 200:
            return {'success': True, 'item': resp.json(), 'error': ''}
        return {'success': False, 'item': {}, 'error': f'HTTP {resp.status_code}'}
    except Exception as e:
        logger.error(f"[HarvenHub] 获取详情失败: {e}")
        return {'success': False, 'item': {}, 'error': str(e)}


def hub_download(item_id, save_dir=None):
    """从云端下载文件

    Args:
        item_id: 内容ID
        save_dir: 保存目录（默认 data/downloads/）

    Returns:
        dict: {success: bool, file_path: str, error: str}
    """
    hub_url = _get_hub_url()
    if save_dir is None:
        save_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'downloads')
    os.makedirs(save_dir, exist_ok=True)

    try:
        resp = _safe_request('GET', f"{hub_url}/api/hub/download/{item_id}", timeout=120, stream=True)
        if resp.status_code != 200:
            return {'success': False, 'file_path': '', 'error': f'下载失败: HTTP {resp.status_code}'}

        # 从响应头获取文件名
        content_disposition = resp.headers.get('Content-Disposition', '')
        filename = f"{item_id}.md"
        # 优先用 filename*= (RFC 5987)
        import re
        m = re.search(r"filename\*=UTF-8''(.+?)(?:;|$)", content_disposition)
        if m:
            from urllib.parse import unquote
            filename = unquote(m.group(1).strip())
        else:
            m = re.search(r'filename="?([^";]+)"?', content_disposition)
            if m:
                filename = m.group(1).strip()

        file_path = os.path.join(save_dir, filename)
        with open(file_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"[HarvenHub] 下载完成: {filename} → {file_path}")
        return {'success': True, 'file_path': file_path, 'error': ''}
    except Exception as e:
        logger.error(f"[HarvenHub] 下载失败: {e}")
        return {'success': False, 'file_path': '', 'error': str(e)}


def hub_upload(file_path, item_type, title, description='', author='', category='other'):
    """上传内容到云端市场

    Args:
        file_path: 本地文件路径
        item_type: 类型（skill/agent/template/knowledge/bundle/model）
        title: 标题
        description: 描述
        author: 作者
        category: 分类

    Returns:
        dict: {success: bool, item_id: str, error: str}
    """
    hub_url = _get_hub_url()
    try:
        if not os.path.exists(file_path):
            return {'success': False, 'item_id': '', 'error': '文件不存在'}

        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f)}
            data = {
                'type': item_type,
                'title': title,
                'description': description,
                'author': author,
                'category': category,
            }
            resp = _safe_request('POST', f"{hub_url}/api/hub/upload", files=files, data=data, timeout=120)

        if resp.status_code == 200:
            result = resp.json()
            logger.info(f"[HarvenHub] 上传成功: {title}")
            return {
                'success': result.get('success', False),
                'item_id': result.get('item_id', ''),
                'error': result.get('error', ''),
            }
        return {'success': False, 'item_id': '', 'error': f'上传失败: HTTP {resp.status_code}'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'item_id': '', 'error': f'无法连接到 {hub_url}'}
    except Exception as e:
        logger.error(f"[HarvenHub] 上传失败: {e}")
        return {'success': False, 'item_id': '', 'error': str(e)}


def hub_categories():
    """获取云端分类列表

    Returns:
        dict: {success: bool, categories: list, error: str}
    """
    hub_url = _get_hub_url()
    try:
        resp = _safe_request('GET', f"{hub_url}/api/hub/categories", timeout=10)
        if resp.status_code == 200:
            return {'success': True, 'categories': resp.json().get('categories', []), 'error': ''}
        return {'success': False, 'categories': [], 'error': f'HTTP {resp.status_code}'}
    except Exception as e:
        logger.error(f"[HarvenHub] 获取分类失败: {e}")
        return {'success': False, 'categories': [], 'error': str(e)}


def hub_stats():
    """获取云端统计数据

    Returns:
        dict: {success: bool, stats: dict, error: str}
    """
    hub_url = _get_hub_url()
    try:
        resp = _safe_request('GET', f"{hub_url}/api/hub/stats", timeout=10)
        if resp.status_code == 200:
            return {'success': True, 'stats': resp.json(), 'error': ''}
        return {'success': False, 'stats': {}, 'error': f'HTTP {resp.status_code}'}
    except Exception as e:
        logger.error(f"[HarvenHub] 获取统计失败: {e}")
        return {'success': False, 'stats': {}, 'error': str(e)}


def hub_check_connection():
    """检查与云端HarvenHub的连接

    Returns:
        dict: {success: bool, url: str, version: str, error: str}
    """
    hub_url = _get_hub_url()
    try:
        resp = _safe_request('GET', f"{hub_url}/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return {
                'success': True,
                'url': hub_url,
                'version': data.get('version', 'unknown'),
                'error': '',
            }
        return {'success': False, 'url': hub_url, 'version': '', 'error': f'HTTP {resp.status_code}'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'url': hub_url, 'version': '', 'error': f'无法连接到 {hub_url}'}
    except Exception as e:
        return {'success': False, 'url': hub_url, 'version': '', 'error': str(e)}
