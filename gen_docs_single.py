#!/usr/bin/env python3
"""
生成单独的HTML文档页面
"""
import markdown
from pathlib import Path

CSS = '''
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #0d1117; color: #c9d1d9; line-height: 1.7; }
.container { max-width: 900px; margin: 0 auto; padding: 40px 24px; }
h1 { font-size: 28px; font-weight: 700; color: #f0f6fc; margin: 32px 0 16px; padding-bottom: 12px; border-bottom: 1px solid #30363d; }
h2 { font-size: 22px; font-weight: 600; color: #f0f6fc; margin: 28px 0 12px; padding-bottom: 8px; border-bottom: 1px solid #21262d; }
h3 { font-size: 18px; font-weight: 600; color: #e6edf3; margin: 24px 0 8px; }
h4 { font-size: 15px; font-weight: 600; color: #e6edf3; margin: 20px 0 6px; }
p { margin: 8px 0; }
ul, ol { margin: 8px 0 8px 24px; }
li { margin: 4px 0; }
code { background: #21262d; color: #f0883e; padding: 2px 6px; border-radius: 4px; font-size: 13px; font-family: 'SF Mono', Consolas, monospace; }
pre { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; overflow-x: auto; margin: 12px 0; }
pre code { background: none; color: #c9d1d9; padding: 0; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }
th { background: #21262d; color: #f0f6fc; padding: 8px 12px; text-align: left; border: 1px solid #30363d; }
td { padding: 8px 12px; border: 1px solid #30363d; }
tr:nth-child(even) td { background: #161b22; }
blockquote { border-left: 4px solid #58a6ff; background: #1f6feb11; padding: 12px 16px; margin: 12px 0; border-radius: 0 6px 6px 0; }
a { color: #58a6ff; }
hr { border: none; border-top: 1px solid #30363d; margin: 24px 0; }
strong { color: #f0f6fc; }
.nav { position: fixed; top: 0; left: 0; right: 0; background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px; z-index: 100; }
.nav a { color: #58a6ff; text-decoration: none; font-size: 14px; }
.container { margin-top: 60px; }
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: #0d1117; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }
'''

md = markdown.Markdown(extensions=['extra', 'codehilite', 'toc', 'tables', 'fenced_code'])

# 技术详解
tech_md = Path('H:/HarvenAI/docs/HarvenAI_技术详解_v7.md').read_text(encoding='utf-8-sig')
tech_html = md.convert(tech_md)
tech_full = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Harven AI 技术详解 v7.0</title><style>{CSS}</style></head><body>
<div class="nav"><a href="index.html">← 返回文档中心</a></div>
<div class="container">{tech_html}</div>
</body></html>'''
Path('H:/HarvenAI/docs/技术详解.html').write_text(tech_full, encoding='utf-8')
print(f'技术详解.html: {len(tech_full)/1024:.1f} KB')

# 安装指南
install_md = Path('H:/HarvenAI/HarvenAI_安装详解_v2.0.md').read_text(encoding='utf-8-sig')
install_html = md.convert(install_md)
install_full = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Harven AI 安装指南 v2.0</title><style>{CSS}</style></head><body>
<div class="nav"><a href="index.html">← 返回文档中心</a></div>
<div class="container">{install_html}</div>
</body></html>'''
Path('H:/HarvenAI/docs/安装指南.html').write_text(install_full, encoding='utf-8')
print(f'安装指南.html: {len(install_full)/1024:.1f} KB')
