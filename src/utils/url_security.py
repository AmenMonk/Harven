"""
URL 安全校验模块 — 防止 SSRF（Server-Side Request Forgery）

对所有外部 HTTP 请求强制进行 URL 安全校验：
1. 禁止访问内网/私有 IP 段
2. 禁止访问 localhost/回环地址
3. 协议白名单（仅 http/https）
4. DNS 重绑定防护（解析后再次校验）
"""

import ipaddress
import re
import socket
from urllib.parse import urlparse
from typing import Tuple, Optional

# 禁止的 IP 地址段（RFC 1918 / RFC 6598 / RFC 6890 / 回环 / 链路本地 / 特殊用途）
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918 私有 A 类
    ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918 私有 B 类
    ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918 私有 C 类
    ipaddress.ip_network("100.64.0.0/10"),      # RFC 6598 运营商级 NAT
    ipaddress.ip_network("127.0.0.0/8"),        # 回环地址
    ipaddress.ip_network("169.254.0.0/16"),     # 链路本地
    ipaddress.ip_network("0.0.0.0/8"),          # 当前网络
    ipaddress.ip_network("224.0.0.0/4"),        # 多播 (D 类)
    ipaddress.ip_network("240.0.0.0/4"),        # 保留 (E 类)
    ipaddress.ip_network("::1/128"),            # IPv6 回环
    ipaddress.ip_network("fc00::/7"),           # IPv6 唯一本地
    ipaddress.ip_network("fe80::/10"),          # IPv6 链路本地
]

# 允许的协议白名单
_ALLOWED_PROTOCOLS = {"http", "https"}

# 已知的 DNS 重绑定攻击域名模式（如 nip.io, xip.io 等）
_DNS_REBINDING_PATTERNS = [
    re.compile(r".*\.nip\.io$", re.IGNORECASE),
    re.compile(r".*\.xip\.io$", re.IGNORECASE),
    re.compile(r".*\.sslip\.io$", re.IGNORECASE),
    re.compile(r".*\.1u\.ms$", re.IGNORECASE),
    re.compile(r".*\.edns\.me$", re.IGNORECASE),
]


def validate_url(url: str, resolve_dns: bool = True) -> Tuple[bool, Optional[str]]:
    """
    校验 URL 是否安全可访问。

    Args:
        url: 待校验的 URL
        resolve_dns: 是否解析 DNS 并进行二次 IP 校验（防止 DNS 重绑定）

    Returns:
        (is_safe, error_message): 安全则 (True, None)，不安全则 (False, 原因)
    """
    if not url or not isinstance(url, str):
        return False, "URL 为空或格式错误"

    url = url.strip()

    # 1. 协议检查
    try:
        parsed = urlparse(url)
    except Exception:
        return False, f"URL 解析失败: {url}"

    if parsed.scheme.lower() not in _ALLOWED_PROTOCOLS:
        return False, f"禁止的协议: {parsed.scheme}，仅允许 http/https"

    hostname = parsed.hostname
    if not hostname:
        return False, f"无法提取主机名: {url}"

    # 2. 禁止 DNS 重绑定攻击域名
    for pattern in _DNS_REBINDING_PATTERNS:
        if pattern.match(hostname):
            return False, f"禁止访问 DNS 重绑定域名: {hostname}"

    # 3. 主机名直接 IP 检查
    if not _is_hostname_safe(hostname):
        return False, f"禁止访问内网/私有地址: {hostname}"

    # 4. DNS 解析后二次校验
    if resolve_dns:
        try:
            resolved_ips = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for res in resolved_ips:
                ip_str = res[4][0]
                if not _is_ip_safe(ip_str):
                    return False, f"域名 {hostname} 解析到禁止的 IP: {ip_str}"
        except socket.gaierror:
            return False, f"域名解析失败: {hostname}"
        except Exception as e:
            return False, f"DNS 解析错误: {e}"

    return True, None


def _is_hostname_safe(hostname: str) -> bool:
    """检查主机名是否解析到内网 IP"""
    try:
        ip = ipaddress.ip_address(hostname)
        return _is_ip_safe(str(ip))
    except ValueError:
        # 不是 IP 地址，是域名，DNS 阶段再检查
        return True


def _is_ip_safe(ip_str: str) -> bool:
    """检查 IP 是否安全（非内网/私有）"""
    try:
        ip = ipaddress.ip_address(ip_str)
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                return False
        return True
    except ValueError:
        return False


def is_url_allowed(url: str) -> bool:
    """便捷方法：URL 是否允许访问"""
    safe, _ = validate_url(url, resolve_dns=True)
    return safe
