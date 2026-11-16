---
name: searxng-search
description: 联网搜索技能 — 通过 SearXNG 搜索引擎获取实时信息（天气、新闻、股价等）
triggers:
  - 搜索
  - 搜一下
  - 查一下
  - 联网
  - 上网搜
  - 最新
  - 今天
  - 现在
  - 天气
  - 新闻
  - 股票
  - 汇率
  - 热搜
  - 实时
  - search
  - google
  - bing
enabled: false  # 2026-07-03 禁用：engine.py 已有原生 Bing 搜索，此 skill 冗余
version: "1.0"
author: Harven AI Official
license: Apache-2.0
---

# SearXNG 联网搜索技能

你拥有联网搜索能力。当用户询问需要实时信息的问题时，你可以通过以下方式搜索：

## 搜索调用方式

使用 Python 代码块执行搜索。示例：

```python
import requests
import json

# SearXNG 搜索（默认地址，用户可在 Docker 中修改）
searxng_url = "http://localhost:8888"
query = "烟台天气今天"  # 替换为实际搜索词

resp = requests.get(
    f"{searxng_url}/search",
    params={"q": query, "format": "json", "categories": "general"},
    timeout=8
)

if resp.status_code == 200:
    data = resp.json()
    results = data.get("results", [])[:5]
    for i, r in enumerate(results):
        print(f"[{i+1}] {r.get('title', '')}")
        print(f"    {r.get('content', '')[:200]}")
        print(f"    {r.get('url', '')}")
else:
    print(f"搜索失败: HTTP {resp.status_code}")
```

## 搜索规则

1. **提取搜索词**：从用户消息中提取核心搜索词，去掉"搜一下"、"帮我查"等指令词
2. **脱敏处理**：搜索词中不含用户的邮箱、手机号、身份证号等隐私信息
3. **结果处理**：
   - 只取前 5 条结果
   - 优先引用权威来源（官方网站、新闻媒体）
   - 标注信息来源和时效性
4. **诚实原则**：
   - 如果 SearXNG 未运行或连接失败，诚实告知用户
   - 不要编造搜索结果
   - 如果搜索结果与用户问题不相关，说明"搜索结果未找到直接相关信息"

## SearXNG 未运行时的处理

如果搜索代码返回连接错误，告诉用户：

"SearXNG 搜索引擎未运行。请按以下步骤启动：
1. 安装 Docker（如果还没装）
2. 运行：docker run -d -p 8888:8080 searxng/searxng
3. 等待 10 秒后重试搜索"

## 适用场景

- 实时信息：天气、新闻、股价、汇率
- 最新动态：产品发布、赛事结果
- 事实核查：验证某个说法是否准确
- 不确定的问题：需要上网确认的信息

## 不适用场景

- 代码编写（用本地知识库）
- 文档查询（用本地知识库）
- 纯闲聊（不需要搜索）
