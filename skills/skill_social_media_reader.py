"""
skill_social_media_reader.py - 社交媒体内容读取 Skill
读取社交媒体平台的内容（微博、小红书、知乎等），提取正文和评论

用法：作为Skill被哈文调用，或直接运行
"""

import re
import json
import subprocess
from urllib.parse import urlparse

def generate(user_message: str, params: dict = None) -> str:
    """处理用户消息，读取社交媒体内容"""
    params = params or {}

    # 从消息中提取URL
    urls = _extract_urls(user_message)
    if not urls:
        return ("我是社交媒体读取Skill。\n"
                "请把社交媒体链接发给我，我帮你读取内容。\n"
                "支持：微博、小红书、知乎、微信公众号文章等。\n"
                "比如：帮我读一下这个 https://weibo.com/xxx")

    results = []
    for url in urls:
        platform = _detect_platform(url)
        if not platform:
            results.append(f"不支持的链接：{url}")
            continue

        content = _fetch_content(url, platform)
        results.append(f"【{platform}】\n{content}")

    return "\n\n---\n\n".join(results)


def _extract_urls(text: str) -> list:
    """从文本中提取URL"""
    pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    return re.findall(pattern, text)


def _detect_platform(url: str) -> str:
    """检测URL对应的社交媒体平台"""
    domain = urlparse(url).netloc.lower()

    if 'weibo.com' in domain or 'weibo.cn' in domain:
        return '微博'
    if 'xiaohongshu.com' in domain or 'xhslink.com' in domain:
        return '小红书'
    if 'zhihu.com' in domain:
        return '知乎'
    if 'mp.weixin.qq.com' in domain:
        return '微信公众号'
    if 'douyin.com' in domain or 'iesdouyin.com' in domain:
        return '抖音'
    if 'bilibili.com' in domain or 'b23.tv' in domain:
        return 'B站'
    if 'twitter.com' in domain or 'x.com' in domain:
        return 'Twitter'
    if 'instagram.com' in domain:
        return 'Instagram'
    return None


def _fetch_content(url: str, platform: str) -> str:
    """抓取社交媒体内容"""
    try:
        # 方法1：用requests直接抓取
        import requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }

        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.encoding = resp.apparent_encoding or 'utf-8'

        if resp.status_code != 200:
            return f"请求失败：HTTP {resp.status_code}"

        html = resp.text

        # 提取标题
        title = _extract_title(html)

        # 提取正文
        content = _extract_content(html, platform)

        # 提取发布时间
        publish_time = _extract_time(html)

        # 组装结果
        parts = []
        if title:
            parts.append(f"标题：{title}")
        if publish_time:
            parts.append(f"发布时间：{publish_time}")
        if content:
            # 截取前2000字
            if len(content) > 2000:
                content = content[:2000] + "\n...(内容过长，已截断)"
            parts.append(f"正文：\n{content}")
        else:
            parts.append("正文：未能提取到内容（可能是JS渲染页面，需要浏览器打开）")
            parts.append(f"页面大小：{len(html)} 字符")

        return "\n".join(parts)

    except requests.exceptions.Timeout:
        return "请求超时，请稍后重试"
    except requests.exceptions.ConnectionError:
        return "连接失败，请检查网络"
    except Exception as e:
        return f"读取失败：{e}"


def _extract_title(html: str) -> str:
    """提取页面标题"""
    # og:title
    match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
    if match:
        return match.group(1)
    # <title>
    match = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_content(html: str, platform: str) -> str:
    """提取正文内容"""
    # 通用：og:description
    match = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html, re.IGNORECASE)
    if match:
        return match.group(1)

    # 通用：description
    match = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html, re.IGNORECASE)
    if match:
        return match.group(1)

    # 按平台特殊处理
    if platform == '微博':
        # 微博正文通常在特定div中
        match = re.search(r'"text"\s*:\s*"([^"]+)"', html)
        if match:
            return match.group(1).encode().decode('unicode_escape')

    if platform == '知乎':
        # 知乎问答内容
        match = re.search(r'"content"\s*:\s*"([^"]+)"', html)
        if match:
            text = match.group(1).encode().decode('unicode_escape')
            # 去HTML标签
            text = re.sub(r'<[^>]+>', '', text)
            return text

    if platform == 'B站':
        # B站视频描述
        match = re.search(r'"desc"\s*:\s*"([^"]+)"', html)
        if match:
            return match.group(1)

    return ""


def _extract_time(html: str) -> str:
    """提取发布时间"""
    # og:article:published_time
    match = re.search(r'<meta\s+property="article:published_time"\s+content="([^"]+)"', html, re.IGNORECASE)
    if match:
        return match.group(1)

    # 通用时间格式
    match = re.search(r'"created_at"\s*:\s*"([^"]+)"', html)
    if match:
        return match.group(1)

    match = re.search(r'"publishTime"\s*:\s*"([^"]+)"', html)
    if match:
        return match.group(1)

    return ""


def _get_video_info(url: str) -> dict:
    """获取视频平台信息（抖音/B站）"""
    try:
        import requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        html = resp.text

        info = {}
        # 标题
        match = re.search(r'"title"\s*:\s*"([^"]+)"', html)
        if match:
            info['title'] = match.group(1)
        # 描述
        match = re.search(r'"desc"\s*:\s*"([^"]+)"', html)
        if match:
            info['desc'] = match.group(1)
        # 作者
        match = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
        if match:
            info['author'] = match.group(1)

        return info
    except:
        return {}
