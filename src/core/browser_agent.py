# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
浏览器自动化模块 — Harven AI
基于 Playwright，AI 可操控浏览器完成实际网页任务

支持的操作：
- 打开网页、截图
- 点击元素、填写表单
- 提取页面文本内容
- 执行任意 JavaScript
- 自然语言任务（通过 AI 规划步骤）
"""
import asyncio
import threading
import queue
import json
import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ─── 检测 Playwright 是否可用 ───
def _check_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False

PLAYWRIGHT_AVAILABLE = _check_playwright()

# ─── 浏览器任务结果 ───
class BrowserResult:
    def __init__(self, success: bool, data: Any = None, error: str = ""):
        self.success = success
        self.data = data
        self.error = error

    def __repr__(self):
        if self.success:
            return f"BrowserResult(OK, {str(self.data)[:100]})"
        return f"BrowserResult(FAIL, {self.error})"


# ─── 自然语言指令解析 ───
def parse_browser_intent(user_message: str) -> Optional[Dict]:
    """
    从用户消息中识别浏览器操作意图
    返回 {"action": ..., "target": ..., "query": ...} 或 None
    """
    msg = user_message.strip().lower()

    # 打开网址
    url_pattern = re.compile(
        r"(?:打开|访问|浏览|go to|open|navigate to|visit)\s+(https?://\S+|www\.\S+|\S+\.\w{2,4}\S*)",
        re.IGNORECASE
    )
    m = url_pattern.search(user_message)
    if m:
        url = m.group(1)
        if not url.startswith("http"):
            url = "https://" + url
        return {"action": "navigate", "url": url}

    # 搜索某内容
    search_patterns = [
        # ★ 2026-07-13: 修复"检查一下"误匹配"查一下" → 要求前面不是"检"
        (r"(?<!检)(?:搜索|帮我搜|查一下|bing搜)\s*[\"「]?(.+?)[\"」]?$", "search"),
        (r"(?:search for|look up)\s+(.+)$", "search"),
    ]
    for pattern, action in search_patterns:
        m = re.search(pattern, user_message, re.IGNORECASE)
        if m:
            return {"action": action, "query": m.group(1).strip()}

    # 截图当前页面
    if any(kw in msg for kw in ["截图", "screenshot", "截屏", "截一下", "给我看看页面"]):
        return {"action": "screenshot"}

    # 抓取页面内容
    if any(kw in msg for kw in ["读取网页", "抓取内容", "获取页面", "页面内容", "fetch page", "get content"]):
        url_m = re.search(r"https?://\S+", user_message)
        return {"action": "fetch_content", "url": url_m.group(0) if url_m else None}

    # 点击元素
    click_m = re.search(r"(?:点击|click on?)\s+[\"「]?(.+?)[\"」]", user_message, re.IGNORECASE)
    if click_m:
        return {"action": "click", "selector_text": click_m.group(1)}

    # 填写表单
    fill_m = re.search(r"(?:在|fill in?)\s*[\"「]?(.+?)[\"」]?\s*(?:里|中|处|栏|框)?\s*(?:输入|填写|写入|type|enter|input)\s*[\"「]?(.+?)[\"」]?$",
                       user_message, re.IGNORECASE)
    if fill_m:
        return {"action": "fill", "field": fill_m.group(1), "value": fill_m.group(2)}

    return None


# ─── 核心浏览器控制类 ───
class BrowserAgent:
    """
    AI 浏览器代理 — 封装 Playwright 操作
    线程安全，支持单例复用
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser = None
        self._page = None
        self._playwright = None
        self._lock = threading.Lock()
        self._screenshot_dir = Path(__file__).parent.parent.parent / "data" / "screenshots"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def is_available(self) -> bool:
        return PLAYWRIGHT_AVAILABLE

    def _ensure_browser(self):
        """确保浏览器已启动"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright 未安装。请运行：pip install playwright && playwright install chromium"
            )
        if self._browser is None or not self._browser.is_connected():
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
        if self._page is None or self._page.is_closed():
            self._page = self._browser.new_page()
            self._page.set_default_timeout(15000)

    def navigate(self, url: str) -> BrowserResult:
        """打开指定 URL（含 SSRF 防护）"""
        # SSRF 防护：URL 安全校验
        from src.utils.url_security import validate_url
        is_safe, err_msg = validate_url(url)
        if not is_safe:
            logger.debug(f"[BrowserAgent] SSRF blocked: {url} — {err_msg}")
            return BrowserResult(False, error=f"安全校验不通过: {err_msg}")

        with self._lock:
            try:
                self._ensure_browser()
                self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
                title = self._page.title()
                current_url = self._page.url
                return BrowserResult(True, {
                    "title": title,
                    "url": current_url,
                    "message": f"已打开页面：{title}"
                })
            except Exception as e:
                return BrowserResult(False, error=str(e))

    def screenshot(self, filename: str = None) -> BrowserResult:
        """截图当前页面"""
        with self._lock:
            try:
                self._ensure_browser()
                if filename is None:
                    import time
                    filename = f"screenshot_{int(time.time())}.png"
                path = self._screenshot_dir / filename
                self._page.screenshot(path=str(path), full_page=False)
                return BrowserResult(True, {
                    "path": str(path),
                    "filename": filename,
                    "message": f"截图已保存：{filename}"
                })
            except Exception as e:
                return BrowserResult(False, error=str(e))

    def get_text(self) -> BrowserResult:
        """获取页面纯文本内容"""
        with self._lock:
            try:
                self._ensure_browser()
                # 移除脚本/样式，获取可读文本
                text = self._page.evaluate("""() => {
                    const scripts = document.querySelectorAll('script, style, noscript');
                    scripts.forEach(el => el.remove());
                    return document.body ? document.body.innerText : document.documentElement.innerText;
                }""")
                # 清理多余空白
                text = re.sub(r'\n{3,}', '\n\n', text).strip()
                return BrowserResult(True, {
                    "text": text[:5000],  # 限制长度
                    "length": len(text),
                    "url": self._page.url
                })
            except Exception as e:
                return BrowserResult(False, error=str(e))

    def click(self, selector: str = None, text: str = None) -> BrowserResult:
        """点击页面元素（支持 CSS selector 或文本内容）"""
        with self._lock:
            try:
                self._ensure_browser()
                if text:
                    # 通过文本内容定位元素
                    self._page.get_by_text(text, exact=False).first.click()
                elif selector:
                    self._page.click(selector)
                else:
                    return BrowserResult(False, error="需要提供 selector 或 text")
                return BrowserResult(True, {"message": f"已点击元素"})
            except Exception as e:
                return BrowserResult(False, error=str(e))

    def fill(self, selector: str, value: str) -> BrowserResult:
        """填写表单字段"""
        with self._lock:
            try:
                self._ensure_browser()
                # 尝试 placeholder 匹配
                try:
                    self._page.get_by_placeholder(selector).fill(value)
                except Exception:
                    # 尝试 label 匹配
                    try:
                        self._page.get_by_label(selector).fill(value)
                    except Exception:
                        # CSS selector 直接填写
                        self._page.fill(selector, value)
                return BrowserResult(True, {"message": f"已填写：{value}"})
            except Exception as e:
                return BrowserResult(False, error=str(e))

    def search(self, query: str, engine: str = "bing") -> BrowserResult:
        """使用搜索引擎搜索"""
        engines = {
            "bing": f"https://www.bing.com/search?q={query}",
        }
        url = engines.get(engine, engines["bing"])
        result = self.navigate(url)
        if result.success:
            # 额外获取搜索结果文本
            text_result = self.get_text()
            if text_result.success:
                result.data["content"] = text_result.data.get("text", "")[:2000]
        return result

    def fetch_content(self, url: str) -> BrowserResult:
        """打开 URL 并抓取内容"""
        nav_result = self.navigate(url)
        if not nav_result.success:
            return nav_result
        return self.get_text()

    def evaluate_js(self, script: str) -> BrowserResult:
        """执行 JavaScript"""
        with self._lock:
            try:
                self._ensure_browser()
                result = self._page.evaluate(script)
                return BrowserResult(True, {"result": result})
            except Exception as e:
                return BrowserResult(False, error=str(e))

    def get_current_info(self) -> Dict:
        """获取当前页面信息"""
        if self._page and not self._page.is_closed():
            try:
                return {"url": self._page.url, "title": self._page.title()}
            except Exception as _e: logger.debug(f"[browser_agent] 忽略: {_e}")
        return {"url": None, "title": None}

    def close(self):
        """关闭浏览器"""
        with self._lock:
            try:
                if self._page and not self._page.is_closed():
                    self._page.close()
                if self._browser and self._browser.is_connected():
                    self._browser.close()
                if self._playwright:
                    self._playwright.stop()
            except Exception as e:
                logger.warning(f"[BrowserAgent] Close error: {e}")
            finally:
                self._page = None
                self._browser = None
                self._playwright = None


# ─── 单例 ───
_browser_agent: Optional[BrowserAgent] = None

def get_browser_agent(headless: bool = True) -> BrowserAgent:
    global _browser_agent
    if _browser_agent is None:
        _browser_agent = BrowserAgent(headless=headless)
    return _browser_agent


# ─── 高层：执行自然语言浏览器任务 ───
def execute_browser_task(user_message: str) -> Optional[str]:
    """
    接收自然语言，自动识别意图，执行浏览器操作，返回结果描述
    返回 None 表示没有识别到浏览器相关意图
    """
    intent = parse_browser_intent(user_message)
    if intent is None:
        return None

    agent = get_browser_agent()
    if not agent.is_available():
        return (
            "⚠️ 浏览器自动化功能未就绪，需要先安装 Playwright：\n"
            "```\npip install playwright\nplaywright install chromium\n```"
        )

    action = intent.get("action")
    result = None

    if action == "navigate":
        result = agent.navigate(intent["url"])
    elif action == "search":
        result = agent.search(intent["query"])
    elif action == "screenshot":
        result = agent.screenshot()
    elif action == "fetch_content":
        url = intent.get("url")
        if not url:
            return "请告诉我要抓取哪个网址的内容。"
        result = agent.fetch_content(url)
    elif action == "click":
        result = agent.click(text=intent.get("selector_text"))
    elif action == "fill":
        result = agent.fill(intent.get("field", ""), intent.get("value", ""))

    if result is None:
        return None

    if result.success:
        data = result.data or {}
        if action == "navigate":
            msg = f"🌐 已打开页面：**{data.get('title', '')}**\n📎 {data.get('url', '')}"
            if data.get("content"):
                msg += f"\n\n**页面内容摘要：**\n{data['content'][:800]}"
            return msg
        elif action == "search":
            msg = f"🔍 搜索完成：**{data.get('title', '')}**"
            if data.get("content"):
                msg += f"\n\n{data['content'][:1000]}"
            return msg
        elif action == "screenshot":
            return f"📸 截图已保存：`{data.get('filename', '')}` → `{data.get('path', '')}`"
        elif action == "fetch_content":
            text = data.get("text", "")
            return f"📄 页面内容（{data.get('url', '')}）：\n\n{text[:2000]}"
        elif action == "click":
            return f"✅ {data.get('message', '已点击')}"
        elif action == "fill":
            return f"✅ {data.get('message', '已填写')}"
        return f"✅ 操作完成：{data}"
    else:
        return f"❌ 浏览器操作失败：{result.error}"
