"""
内容读取引擎 — B站 + 抖音
P1-5 抖音/B站内容读取 Skill
"""
import re
import json
import requests
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

# ── B站 API 基础地址 ──────────────────
BILI_BASE = "https://api.bilibili.com"


class BiliContentReader:
    """B站内容读取器
    使用 B站公开 API，无需登录即可读取大部分内容
    """
    def __init__(self, cookie: str = ""):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com"
        })
        if cookie:
            self.session.cookies.update(self._parse_cookie(cookie))

    def _parse_cookie(self, cookie_str: str) -> dict:
        result = {}
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    def get_video_info(self, bvid: str) -> Dict:
        """获取视频详细信息
        https://api.bilibili.com/x/web-interface/view?bvid=xxx
        """
        try:
            r = self.session.get(f"{BILI_BASE}/x/web-interface/view",
                                  params={"bvid": bvid}, timeout=10)
            r.raise_for_status()
            d = r.json()
            if d.get("code", -1) != 0:
                return {"success": False, "error": d.get("message", "未知错误")}
            v = d["data"]
            return {
                "success": True,
                "platform": "bilibili",
                "bvid": v.get("bvid", ""),
                "aid": v.get("aid", 0),
                "title": v.get("title", ""),
                "desc": v.get("desc", ""),
                "owner": {
                    "mid": v.get("owner", {}).get("mid", 0),
                    "name": v.get("owner", {}).get("name", ""),
                    "face": v.get("owner", {}).get("face", ""),
                },
                "stat": {
                    "view": v.get("stat", {}).get("view", 0),
                    "danmaku": v.get("stat", {}).get("danmaku", 0),
                    "reply": v.get("stat", {}).get("reply", 0),
                    "favorite": v.get("stat", {}).get("favorite", 0),
                    "coin": v.get("stat", {}).get("coin", 0),
                    "share": v.get("stat", {}).get("share", 0),
                    "like": v.get("stat", {}).get("like", 0),
                },
                "duration": v.get("duration", 0),
                "pubdate": v.get("pubdate", 0),
                "pubdate_str": datetime.fromtimestamp(v.get("pubdate", 0)).isoformat() if v.get("pubdate") else "",
                "tid": v.get("tid", 0),
                "tname": v.get("tname", ""),
                "pic": v.get("pic", ""),
                "tags": [t.get("tag_name", "") for t in v.get("tag", [])],
            }
        except Exception as e:
            logger.error(f"获取B站视频信息失败: {e}")
            return {"success": False, "error": str(e)}

    def get_video_comments(self, bvid: str, pn: int = 1, ps: int = 20) -> Dict:
        """获取视频评论
        https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&pn={pn}&ps={ps}
        """
        try:
            # 先获取 aid
            info = self.get_video_info(bvid)
            if not info.get("success"):
                return info
            aid = info["aid"]
            r = self.session.get(f"{BILI_BASE}/x/v2/reply/main",
                                  params={"type": 1, "oid": aid, "pn": pn, "ps": ps},
                                  timeout=10)
            r.raise_for_status()
            d = r.json()
            if d.get("code", -1) != 0:
                return {"success": False, "error": d.get("message", "")}
            replies = d.get("data", {}).get("replies", []) or []
            return {
                "success": True,
                "platform": "bilibili",
                "total": d.get("data", {}).get("page", {}).get("acount", 0),
                "comments": [{
                    "rpid": c.get("rpid", 0),
                    "mid": c.get("mid", 0),
                    "uname": c.get("m_name", ""),
                    "content": c.get("content", {}).get("message", ""),
                    "like": c.get("like", 0),
                    "ctime": c.get("ctime", 0),
                    "reply_count": c.get("rcount", 0),
                } for c in replies],
                "page": pn,
            }
        except Exception as e:
            logger.error(f"获取B站评论失败: {e}")
            return {"success": False, "error": str(e)}

    def get_user_info(self, mid: int) -> Dict:
        """获取用户信息
        https://api.bilibili.com/x/web-interface/card?mid=xxx
        """
        try:
            r = self.session.get(f"{BILI_BASE}/x/web-interface/card",
                                  params={"mid": mid}, timeout=10)
            r.raise_for_status()
            d = r.json()
            if d.get("code", -1) != 0:
                return {"success": False, "error": d.get("message", "")}
            c = d.get("data", {}).get("card", {})
            return {
                "success": True,
                "platform": "bilibili",
                "mid": mid,
                "name": c.get("name", ""),
                "face": c.get("face", ""),
                "sign": c.get("sign", ""),
                "level": c.get("level_info", {}).get("current_level", 0),
                "follower": d.get("data", {}).get("follower", 0),
                "following": d.get("data", {}).get("following", 0),
                "video_count": d.get("data", {}).get("video", 0),
            }
        except Exception as e:
            logger.error(f"获取B站用户信息失败: {e}")
            return {"success": False, "error": str(e)}

    def search(self, keyword: str, search_type: str = "video", page: int = 1) -> Dict:
        """搜索
        search_type: video, bili_user, media_bangumi, media_ft
        """
        try:
            r = self.session.get(f"{BILI_BASE}/x/web-interface/search/all",
                                  params={"keyword": keyword, "page": page},
                                  timeout=10)
            r.raise_for_status()
            d = r.json()
            if d.get("code", -1) != 0:
                return {"success": False, "error": d.get("message", "")}
            result = d.get("data", {}).get("result", []) or []
            return {
                "success": True,
                "platform": "bilibili",
                "keyword": keyword,
                "page": page,
                "results": [{
                    "title": item.get("title", "").replace("<em class=\"keyword\">", "").replace("</em>", ""),
                    "author": item.get("author", ""),
                    "bvid": item.get("bvid", ""),
                    "play": item.get("play", 0),
                    "video_review": item.get("video_review", 0),
                    "favorites": item.get("favorites", 0),
                    "duration": item.get("duration", ""),
                    "pubdate": item.get("pubdate", 0),
                } for item in result if isinstance(item, dict)],
            }
        except Exception as e:
            logger.error(f"B站搜索失败: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def extract_bvid(url: str) -> Optional[str]:
        """从 URL 中提取 bvid"""
        patterns = [
            r"bilibili\.com/video/([a-zA-Z0-9]+)",
            r"b23\.tv/([a-zA-Z0-9]+)",
            r"BV[a-zA-Z0-9]+",
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1) if "BV" not in p else m.group(0)
        return None


class DouyinContentReader:
    """抖音内容读取器
    抖音没有公开 API，使用移动端接口 + Web Scraping
    ⚠️ 抖音反爬较强，仅用于个人学习研究
    """
    def __init__(self, cookie: str = ""):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36",
            "Referer": "https://www.douyin.com/"
        })
        if cookie:
            self.session.cookies.update(self._parse_cookie(cookie))

    def _parse_cookie(self, cookie_str: str) -> dict:
        result = {}
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    def get_video_info(self, video_url: str) -> Dict:
        """获取抖音视频信息
        通过分享链接或视频 ID 获取信息
        ⚠️ 需要 Cookie 才能获取完整信息
        """
        try:
            # 提取视频 ID
            video_id = self._extract_video_id(video_url)
            if not video_id:
                return {"success": False, "error": "无法解析视频ID，请检查链接"}

            # 尝试通过移动端 API 获取（需要 Cookie）
            # 这里使用 Web Scraping 作为备选方案
            r = self.session.get(video_url, timeout=10, allow_redirects=True)
            r.raise_for_status()
            html = r.text

            # 从 HTML 中提取 JSON 数据
            json_match = re.search(r'<script[^>]*id="RENDER_DATA"[^>]*>([^<]+)</script>', html)
            if not json_match:
                return {"success": False, "error": "无法解析抖音页面，可能需要登录Cookie"}

            import urllib.parse
            encoded = json_match.group(1)
            decoded = urllib.parse.unquote(encoded)
            data = json.loads(decoded)

            # 提取视频信息（结构较复杂，取关键字段）
            video_info = data.get("video", {}).get("video", {})
            author = data.get("video", {}).get("author", {})

            return {
                "success": True,
                "platform": "douyin",
                "video_id": video_id,
                "title": video_info.get("desc", ""),
                "author": {
                    "name": author.get("nickname", ""),
                    "uid": author.get("uid", ""),
                    "avatar": author.get("avatar_thumb", {}).get("url_list", [""])[0] if author.get("avatar_thumb") else "",
                },
                "stats": {
                    "play": video_info.get("play_cnt", 0),
                    "like": video_info.get("liked_cnt", 0),
                    "comment": video_info.get("comment_cnt", 0),
                    "share": video_info.get("share_cnt", 0),
                },
                "cover": video_info.get("cover", {}).get("url_list", [""])[0] if video_info.get("cover") else "",
                "duration": video_info.get("duration", 0),
            }
        except Exception as e:
            logger.error(f"获取抖音视频信息失败: {e}")
            return {"success": False, "error": str(e)}

    def search(self, keyword: str) -> Dict:
        """抖音搜索（仅 Web Scraping，成功率低）
        ⚠️ 不推荐在生产环境使用
        """
        return {
            "success": False,
            "error": "抖音搜索需要登录Cookie，且反爬较强，暂不支持。建议使用B站搜索替代。"
        }

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        """从 URL 中提取抖音视频 ID"""
        patterns = [
            r"douyin\.com/video/([0-9]+)",
            r"v\.douyin\.com/([a-zA-Z0-9]+)",
            r"/share/video/([0-9]+)",
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def extract_url_from_share(share_text: str) -> Optional[str]:
        """从分享文本中提取抖音链接"""
        m = re.search(r'https?://(?:v\.)?douyin\.com/[^"\s]+', share_text)
        if m:
            return m.group(0)
        return None


class ContentReader:
    """统一内容读取引擎（Facade 模式）"""
    def __init__(self, bili_cookie: str = "", douyin_cookie: str = ""):
        self.bili = BiliContentReader(bili_cookie)
        self.douyin = DouyinContentReader(douyin_cookie)

    def read(self, url: str) -> Dict:
        """自动识别平台并读取内容"""
        if "bilibili" in url or "b23.tv" in url:
            bvid = BiliContentReader.extract_bvid(url)
            if not bvid:
                return {"success": False, "error": "无法解析B站视频ID"}
            return self.bili.get_video_info(bvid)
        elif "douyin" in url or "douyin" in url:
            return self.douyin.get_video_info(url)
        else:
            return {"success": False, "error": f"不支持的链接：{url[:50]}"}

    def read_bili(self, bvid_or_url: str) -> Dict:
        """读取 B站内容"""
        bvid = bvid_or_url if bvid_or_url.startswith("BV") else BiliContentReader.extract_bvid(bvid_or_url)
        if not bvid:
            return {"success": False, "error": "无法解析B站视频ID"}
        return self.bili.get_video_info(bvid)

    def read_douyin(self, url: str) -> Dict:
        """读取抖音内容"""
        return self.douyin.get_video_info(url)

    def search_bili(self, keyword: str, page: int = 1) -> Dict:
        """搜索 B站内容"""
        return self.bili.search(keyword, page=page)

    def get_bili_comments(self, bvid: str, pn: int = 1, ps: int = 20) -> Dict:
        """获取 B站评论"""
        return self.bili.get_video_comments(bvid, pn=pn, ps=ps)


# 全局单例
_content_reader: Optional[ContentReader] = None

def get_content_reader(bili_cookie: str = "", douyin_cookie: str = "") -> ContentReader:
    global _content_reader
    if _content_reader is None:
        _content_reader = ContentReader(bili_cookie, douyin_cookie)
    return _content_reader
