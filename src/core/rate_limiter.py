# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
速率限制中间件 — Token Bucket 算法

纯内存实现，无需外部依赖。支持：
- 按 IP 限流
- 按端点差异化配置
- 429 响应 + Retry-After 头
"""

import logging
logger = logging.getLogger(__name__)
import time
import threading
from collections import defaultdict
from typing import Dict, Optional, Tuple
from flask import request, jsonify


class TokenBucket:
    """令牌桶"""
    def __init__(self, rate: float, capacity: int):
        self.rate = rate          # 每秒补充令牌数
        self.capacity = capacity  # 桶容量
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def consume(self, tokens: int = 1) -> Tuple[bool, float]:
        """尝试消费令牌，返回 (是否成功, 等待秒数)"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True, 0
        else:
            wait_time = (tokens - self.tokens) / self.rate
            return False, wait_time


class RateLimiter:
    """
    速率限制器

    默认配置：
    - 聊天接口: 30次/分钟 (0.5/s)
    - 文件上传: 10次/分钟
    - 知识库查询: 60次/分钟
    - 其他API: 120次/分钟
    - 静态文件: 不限
    """

    # 端点速率配置: (rate_per_second, burst_capacity)
    ENDPOINT_LIMITS = {
        "/api/chat": (0.5, 5),          # 聊天: 0.5/s, 突发5
        "/api/chat/image": (0.2, 3),    # 图片: 0.2/s
        "/api/chat/video": (0.1, 2),    # 视频: 0.1/s
        "/api/chat/audio": (0.3, 3),    # 音频: 0.3/s
        "/api/chat/file": (0.3, 3),     # 文件: 0.3/s
        "/api/knowledge": (1.0, 10),    # 知识库: 1/s
        "/api/tasks": (0.5, 5),         # 任务: 0.5/s
        "/api/image_gen": (0.1, 2),     # 图片生成: 0.1/s
    }

    DEFAULT_LIMIT = (2.0, 20)  # 默认: 2/s

    def __init__(self):
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 300  # 5分钟清理一次过期桶
        self._last_cleanup = time.monotonic()

    def _get_bucket_key(self, ip: str, endpoint: str) -> str:
        """生成桶标识"""
        return f"{ip}:{endpoint}"

    def _get_limit(self, path: str) -> Tuple[float, int]:
        """获取端点限流配置"""
        for prefix, limit in self.ENDPOINT_LIMITS.items():
            if path.startswith(prefix):
                return limit
        return self.DEFAULT_LIMIT

    def check(self, ip: str, path: str) -> Tuple[bool, float]:
        """
        检查请求是否允许通过。

        Returns:
            (allowed, retry_after_seconds)
        """
        # 静态文件和健康检查不限流
        if path.startswith("/static") or path == "/health" or path.startswith("/favicon"):
            return True, 0

        rate, capacity = self._get_limit(path)
        bucket_key = self._get_bucket_key(ip, path)

        with self._lock:
            # 定期清理过期桶
            now = time.monotonic()
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup_expired(now)
                self._last_cleanup = now

            if bucket_key not in self._buckets:
                self._buckets[bucket_key] = TokenBucket(rate, capacity)

            bucket = self._buckets[bucket_key]
            allowed, wait_time = bucket.consume(1)
            return allowed, wait_time

    def _cleanup_expired(self, now: float):
        """清理超过 10 分钟未使用的桶"""
        expired_keys = []
        for key, bucket in self._buckets.items():
            if now - bucket.last_refill > 600:
                expired_keys.append(key)
        for key in expired_keys:
            del self._buckets[key]

    def is_rate_limited(self, ip: str) -> bool:
        """
        全局 IP 级别检查（防止单 IP 跨端点刷量）

        返回 True 表示该 IP 整体请求过量
        """
        global_key = f"{ip}:__global__"
        with self._lock:
            if global_key not in self._buckets:
                self._buckets[global_key] = TokenBucket(5.0, 30)  # 全局: 5/s
            allowed, _ = self._buckets[global_key].consume(1)
            return not allowed


# 全局单例
_rate_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    with _limiter_lock:
        if _rate_limiter is None:
            _rate_limiter = RateLimiter()
        return _rate_limiter


def rate_limit_middleware():
    """
    Flask before_request 中间件。

    用法（在 web_app.py 中）：
        from src.core.rate_limiter import rate_limit_middleware
        app.before_request(rate_limit_middleware)
    """
    # 检查配置是否启用速率限制
    try:
        from src.utils.config import Config
        cfg = Config()
        if not cfg.get('rate_limit_enabled', True):
            return None  # 速率限制已禁用，直接放行
    except Exception as _e: logger.debug(f"[rate_limiter] 忽略: {_e}")  # 配置读取失败，默认启用速率限制

    ip = request.remote_addr or "127.0.0.1"
    path = request.path
    limiter = get_rate_limiter()

    # 1. 全局 IP 级别检查
    if limiter.is_rate_limited(ip):
        return jsonify({
            "error": "请求过于频繁，请稍后再试",
            "retry_after": 5
        }), 429

    # 2. 端点级别检查
    allowed, wait_time = limiter.check(ip, path)
    if not allowed:
        retry_after = max(1, int(wait_time) + 1)
        resp = jsonify({
            "error": f"请求频率超限，请 {retry_after} 秒后重试",
            "retry_after": retry_after
        })
        resp.status_code = 429
        resp.headers["Retry-After"] = str(retry_after)
        return resp

    return None  # 放行
