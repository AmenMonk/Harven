#!/usr/bin/env python3
"""Harven AI MD→HTML 桌面版生成器"""
import re
import markdown
from pathlib import Path

# ★ #840: 动态项目根（输入 docs/ 下 md，输出到项目根，任意电脑都能用）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MD_PATH = PROJECT_ROOT / "docs" / "HarvenAI_完整开发文档_v6.md"
OUT_PATH = PROJECT_ROOT / "HarvenAI_技术详解.html"

with open(MD_PATH, "r", encoding="utf-8") as f:
    md_content = f.read()

# pre-process: fix raw HTML code blocks that were malformed
# convert markdown to HTML
extensions = ["tables", "fenced_code", "codehilite", "toc", "nl2br", "sane_lists"]
html_body = markdown.markdown(md_content, extensions=extensions)

# inject anchor IDs for Chinese headings
def add_anchors(html):
    # Pattern for h2/h3 headings that may contain Chinese text
    def replacer(m):
        tag = m.group(1)
        text = m.group(2)
        # Generate slug from text
        slug = re.sub(r'[^\w\s-]', '', text).strip().lower()
        slug = re.sub(r'[-\s]+', '-', slug)
        return f'<{tag} id="{slug}">{text}</{tag}>'
    return re.sub(r'<(h[23])>(.*?)</\1>', replacer, html)

html_body = add_anchors(html_body)

# escape `{{` and `}}` for HTML display (Jinja2 conflicts)
html_body = html_body.replace("{{", "&#123;&#123;").replace("}}", "&#125;&#125;")

# Full HTML template
html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Harven AI 完整技术详解 v6.0</title>
<style>
:root {{
    --bg: #0d1117;
    --bg-card: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --text-muted: #8b949e;
    --accent: #58a6ff;
    --accent-green: #3fb950;
    --accent-orange: #d2991d;
    --accent-red: #f85149;
    --code-bg: #1c2128;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.7;
    font-size: 15px;
}}
nav {{
    position: fixed;
    top: 0; left: 0;
    width: 260px;
    height: 100vh;
    background: var(--bg-card);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    padding: 20px;
    font-size: 13px;
    z-index: 100;
}}
nav h2 {{
    color: var(--accent);
    font-size: 17px;
    margin-bottom: 16px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
}}
nav a {{
    color: var(--text-muted);
    text-decoration: none;
    display: block;
    padding: 3px 0;
    transition: color 0.15s;
}}
nav a:hover {{ color: var(--accent); }}
nav a.active {{ color: var(--accent-green); font-weight: bold; }}
nav .section {{ margin-bottom: 14px; }}
nav .section-title {{
    color: var(--accent-orange);
    font-weight: bold;
    margin: 10px 0 4px;
    font-size: 13px;
}}
main {{
    margin-left: 280px;
    max-width: 960px;
    padding: 30px 40px 80px;
}}
.badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: bold;
}}
.badge-v6 {{ background: var(--accent); color: #000; }}
.badge-new {{ background: var(--accent-green); color: #000; }}
.badge-warn {{ background: var(--accent-orange); color: #000; }}
h1 {{
    font-size: 32px;
    margin-bottom: 8px;
    color: #fff;
    border-bottom: 2px solid var(--accent);
    padding-bottom: 12px;
}}
h2 {{
    font-size: 24px;
    margin: 40px 0 16px;
    color: #fff;
    border-bottom: 1px solid var(--border);
    padding-bottom: 8px;
}}
h3 {{
    font-size: 19px;
    margin: 28px 0 12px;
    color: var(--accent);
}}
h4 {{
    font-size: 16px;
    margin: 20px 0 8px;
    color: var(--accent-green);
}}
p {{ margin: 10px 0; }}
blockquote {{
    border-left: 3px solid var(--accent);
    padding: 8px 16px;
    margin: 12px 0;
    background: var(--bg-card);
    color: var(--text-muted);
    border-radius: 0 6px 6px 0;
}}
blockquote p {{ margin: 4px 0; }}
pre {{
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.5;
    margin: 12px 0;
}}
code {{
    font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
    font-size: 13px;
    background: var(--code-bg);
    padding: 2px 6px;
    border-radius: 4px;
}}
pre code {{
    background: none;
    padding: 0;
    font-size: 13px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-size: 14px;
}}
th, td {{
    border: 1px solid var(--border);
    padding: 8px 12px;
    text-align: left;
}}
th {{
    background: var(--bg-card);
    font-weight: bold;
    color: var(--accent);
}}
tr:nth-child(even) {{ background: rgba(88,166,255,0.03); }}
tr:hover {{ background: rgba(88,166,255,0.08); }}
hr {{
    border: none;
    border-top: 1px solid var(--border);
    margin: 30px 0;
}}
ul, ol {{ margin: 8px 0 8px 24px; }}
li {{ margin: 4px 0; }}
strong {{ color: #e6edf3; }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
img {{ max-width: 100%; border-radius: 8px; }}

@media (max-width: 900px) {{
    nav {{ display: none; }}
    main {{ margin-left: 0; padding: 20px; }}
}}
</style>
</head>
<body>

<nav>
<h2>Harven AI<br><span style="font-size:13px;color:var(--text-muted);">v6.0 技术详解</span></h2>
<div class="section">
<div class="section-title">项目概述</div>
<a href="#1-项目概述">1. 项目概述</a>
<a href="#" onclick="scrollToAnchor('1-1-产品定位')" style="padding-left:16px;font-size:12px;">1.1 产品定位</a>
<a href="#" onclick="scrollToAnchor('1-2-首次启动流程')" style="padding-left:16px;font-size:12px;">1.2 首次启动流程</a>
<a href="#" onclick="scrollToAnchor('1-3-技术栈')" style="padding-left:16px;font-size:12px;">1.3 技术栈</a>
<a href="#" onclick="scrollToAnchor('1-4-项目目录结构v60-验证通过72-文件')" style="padding-left:16px;font-size:12px;">1.4 目录结构</a>
</div>
<div class="section">
<div class="section-title">核心架构</div>
<a href="#2-核心架构设计">2. 核心架构</a>
<a href="#" onclick="scrollToAnchor('211-后端架构80-核心模块v60-验证通过')" style="padding-left:16px;font-size:12px;">2.1.1 后端模块</a>
<a href="#" onclick="scrollToAnchor('212-三层-ai-分工架构')" style="padding-left:16px;font-size:12px;">2.1.2 三层AI</a>
</div>
<div class="section">
<div class="section-title">查询模式</div>
<a href="#3-查询模式--协作体系v60">3. 查询模式</a>
<a href="#" onclick="scrollToAnchor('32-本地模式kb-only')" style="padding-left:16px;font-size:12px;">3.2 本地模式</a>
</div>
<div class="section">
<div class="section-title">协作体系</div>
<a href="#4-协作体系-v202026-06-26-重构">4. 协作 v2.0</a>
</div>
<div class="section">
<div class="section-title">安全体系</div>
<a href="#5-安全防御体系">5. 安全防御</a>
<a href="#" onclick="scrollToAnchor('53-诚实原则内核-moralitypy-v30')" style="padding-left:16px;font-size:12px;">5.3 诚实原则</a>
</div>
<div class="section">
<div class="section-title">功能模块</div>
<a href="#6-功能模块详解1">6. 功能模块</a>
</div>
<div class="section">
<div class="section-title">数据库</div>
<a href="#7-数据库设计全览">7. 数据库</a>
</div>
<div class="section">
<div class="section-title">API & 前端</div>
<a href="#8-api-路由全览260-端点">8. API 路由</a>
<a href="#9-前端架构详解">9. 前端架构</a>
</div>
<div class="section">
<div class="section-title">运维</div>
<a href="#11-启动流程与运维">11. 启动运维</a>
<a href="#14-开发规范与已知坑">14. 开发规范</a>
</div>
</nav>

<main>
<div style="padding-top:10px;">
<span class="badge badge-v6">v6.0</span>
<span class="badge badge-new">2026-06-26</span>
<span class="badge badge-warn">协作体系 v2.0 重构</span>
</div>
{html_body}
<hr>
<p style="text-align:center;color:var(--text-muted);padding:20px;">
Harven AI — 你的专属私有 AI，永远站在你这一边 | 永久免费 | 纯本地运行<br>
文档版本 v6.0 · 2026-06-26 · 开发者 & Harven AI
</p>
</main>

<script>
function scrollToAnchor(id) {{
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({{ behavior:'smooth', block:'start' }});
}}

// Highlight current section in nav on scroll
const navLinks = document.querySelectorAll('nav a[onclick]');
const sections = [];
navLinks.forEach(a => {{
    const id = a.getAttribute('onclick').match(/scrollToAnchor\\('([^']+)'\\)/);
    if (id) {{
        const el = document.getElementById(id[1]);
        if (el) sections.push({{el, a}});
    }}
}});

window.addEventListener('scroll', () => {{
    let current = '';
    sections.forEach(({{el, a}}) => {{
        if (el.getBoundingClientRect().top < 100) current = a;
    }});
    navLinks.forEach(a => a.classList.remove('active'));
    if (current) current.classList.add('active');
}});
</script>

</body>
</html>"""

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write(html)

print(f"✅ HTML generated: {OUT_PATH}")
print(f"   Size: {len(html):,} bytes")
