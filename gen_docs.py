#!/usr/bin/env python3
"""
HarvenAI 文档中心生成器
把Markdown技术文档转成带样式的HTML页面
"""
import markdown
import os
from pathlib import Path

# ===== 1. 读取Markdown文件 =====
tech_md = Path('H:/HarvenAI/docs/HarvenAI_技术详解_v7.md').read_text(encoding='utf-8-sig')
install_md = Path('H:/HarvenAI/HarvenAI_安装详解_v2.0.md').read_text(encoding='utf-8-sig')

# ===== 2. 转换Markdown → HTML =====
md = markdown.Markdown(extensions=['extra', 'codehilite', 'toc', 'tables', 'fenced_code'])
tech_html = md.convert(tech_md)
install_html = md.convert(install_md)

# ===== 3. 提取标题用于侧边栏 =====
import re
def extract_toc(md_text):
    """从Markdown提取目录"""
    toc = []
    for line in md_text.split('\n'):
        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            title = line.lstrip('#').strip()
            # 生成锚点ID
            anchor = re.sub(r'[^\w\u4e00-\u9fa5\-]', '', title).replace(' ', '-')
            toc.append({'level': level, 'title': title, 'anchor': anchor})
    return toc

tech_toc = extract_toc(tech_md)
install_toc = extract_toc(install_md)

# ===== 4. 生成侧边栏HTML =====
def gen_sidebar(toc, max_level=3):
    html = ''
    for item in toc:
        if item['level'] <= max_level:
            indent = (item['level'] - 1) * 12
            html += f'<a href="#{item["anchor"]}" class="sidebar-link" style="padding-left:{16+indent}px;">{item["title"]}</a>\n'
    return html

tech_sidebar = gen_sidebar(tech_toc)
install_sidebar = gen_sidebar(install_toc)

# ===== 5. HTML模板 =====
CSS = '''
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #0d1117; color: #c9d1d9; line-height: 1.7; }

/* 顶部导航 */
.navbar { position: fixed; top: 0; left: 0; right: 0; height: 56px; background: #161b22; border-bottom: 1px solid #30363d; display: flex; align-items: center; padding: 0 24px; z-index: 100; }
.navbar-brand { font-size: 18px; font-weight: 700; color: #58a6ff; text-decoration: none; display: flex; align-items: center; gap: 8px; }
.navbar-brand .logo { font-size: 24px; }
.navbar-links { margin-left: 32px; display: flex; gap: 4px; }
.navbar-tab { padding: 6px 14px; border-radius: 6px; color: #8b949e; text-decoration: none; font-size: 13px; cursor: pointer; transition: all 0.2s; }
.navbar-tab:hover { background: #21262d; color: #c9d1d9; }
.navbar-tab.active { background: #1f6feb; color: #fff; }
.navbar-right { margin-left: auto; display: flex; gap: 12px; align-items: center; }
.navbar-version { font-size: 12px; color: #8b949e; }
.navbar-github { color: #8b949e; text-decoration: none; font-size: 13px; padding: 6px 12px; border: 1px solid #30363d; border-radius: 6px; transition: all 0.2s; }
.navbar-github:hover { border-color: #58a6ff; color: #58a6ff; }

/* 布局 */
.layout { display: flex; margin-top: 56px; min-height: calc(100vh - 56px); }

/* 侧边栏 */
.sidebar { width: 280px; position: fixed; top: 56px; left: 0; bottom: 0; background: #161b22; border-right: 1px solid #30363d; overflow-y: auto; padding: 16px 0; }
.sidebar-title { padding: 8px 16px; font-size: 11px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
.sidebar-link { display: block; padding: 6px 16px; color: #c9d1d9; text-decoration: none; font-size: 13px; transition: all 0.15s; border-left: 2px solid transparent; }
.sidebar-link:hover { background: #21262d; color: #58a6ff; border-left-color: #58a6ff; }
.sidebar-link.active { background: #1f6feb33; color: #58a6ff; border-left-color: #58a6ff; }

/* 内容区 */
.content { flex: 1; margin-left: 280px; padding: 40px 48px; max-width: 900px; }
.page { display: none; }
.page.active { display: block; }

/* Markdown样式 */
.content h1 { font-size: 28px; font-weight: 700; color: #f0f6fc; margin: 32px 0 16px; padding-bottom: 12px; border-bottom: 1px solid #30363d; }
.content h2 { font-size: 22px; font-weight: 600; color: #f0f6fc; margin: 28px 0 12px; padding-bottom: 8px; border-bottom: 1px solid #21262d; }
.content h3 { font-size: 18px; font-weight: 600; color: #e6edf3; margin: 24px 0 8px; }
.content h4 { font-size: 15px; font-weight: 600; color: #e6edf3; margin: 20px 0 6px; }
.content p { margin: 8px 0; color: #c9d1d9; }
.content ul, .content ol { margin: 8px 0 8px 24px; }
.content li { margin: 4px 0; }
.content code { background: #21262d; color: #f0883e; padding: 2px 6px; border-radius: 4px; font-size: 13px; font-family: 'SF Mono', 'Fira Code', Consolas, monospace; }
.content pre { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; overflow-x: auto; margin: 12px 0; }
.content pre code { background: none; color: #c9d1d9; padding: 0; font-size: 13px; }
.content table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }
.content th { background: #21262d; color: #f0f6fc; padding: 8px 12px; text-align: left; border: 1px solid #30363d; font-weight: 600; }
.content td { padding: 8px 12px; border: 1px solid #30363d; color: #c9d1d9; }
.content tr:nth-child(even) td { background: #161b22; }
.content blockquote { border-left: 4px solid #58a6ff; background: #1f6feb11; padding: 12px 16px; margin: 12px 0; border-radius: 0 6px 6px 0; }
.content blockquote p { color: #8b949e; }
.content a { color: #58a6ff; text-decoration: none; }
.content a:hover { text-decoration: underline; }
.content hr { border: none; border-top: 1px solid #30363d; margin: 24px 0; }
.content strong { color: #f0f6fc; font-weight: 600; }
.content em { color: #d2a8ff; }

/* 首页卡片 */
.home-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; margin: 24px 0; }
.home-card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 24px; cursor: pointer; transition: all 0.2s; text-decoration: none; color: #c9d1d9; display: block; }
.home-card:hover { border-color: #58a6ff; transform: translateY(-2px); box-shadow: 0 4px 12px rgba(31,111,235,0.15); }
.home-card-icon { font-size: 32px; margin-bottom: 12px; }
.home-card-title { font-size: 16px; font-weight: 600; color: #f0f6fc; margin-bottom: 6px; }
.home-card-desc { font-size: 13px; color: #8b949e; }
.home-card-meta { font-size: 11px; color: #8b949e; margin-top: 8px; }

/* 搜索框 */
.search-box { width: 100%; padding: 8px 12px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #c9d1d9; font-size: 13px; margin-bottom: 12px; }
.search-box:focus { outline: none; border-color: #58a6ff; }

/* 移动端 */
@media (max-width: 768px) {
    .sidebar { display: none; }
    .content { margin-left: 0; padding: 20px; }
    .navbar-links { display: none; }
}

/* 滚动条 */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: #0d1117; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #484f58; }
'''

JS = '''
// 页面切换
function showPage(pageId) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(pageId).classList.add('active');
    
    document.querySelectorAll('.navbar-tab').forEach(t => t.classList.remove('active'));
    const tab = document.querySelector(`.navbar-tab[data-page="${pageId}"]`);
    if (tab) tab.classList.add('active');
    
    // 更新侧边栏
    document.querySelectorAll('.sidebar-content').forEach(s => s.style.display = 'none');
    const sidebar = document.getElementById('sidebar-' + pageId);
    if (sidebar) sidebar.style.display = 'block';
    
    // 滚动到顶部
    window.scrollTo(0, 0);
    
    // 更新URL hash
    history.replaceState(null, '', '#' + pageId);
}

// 侧边栏链接点击
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('sidebar-link')) {
        // 不阻止默认行为，让锚点跳转
        setTimeout(() => {
            document.querySelectorAll('.sidebar-link').forEach(l => l.classList.remove('active'));
            e.target.classList.add('active');
        }, 50);
    }
});

// 顶部Tab点击
document.querySelectorAll('.navbar-tab').forEach(tab => {
    tab.addEventListener('click', function() {
        showPage(this.dataset.page);
    });
});

// 首页卡片点击
document.querySelectorAll('.home-card').forEach(card => {
    card.addEventListener('click', function() {
        showPage(this.dataset.page);
    });
});

// 搜索过滤
function filterSidebar(input) {
    const query = input.value.toLowerCase();
    const visibleSidebar = document.querySelector('.sidebar-content[style*="block"], .sidebar-content:not([style*="none"])');
    if (!visibleSidebar) return;
    visibleSidebar.querySelectorAll('.sidebar-link').forEach(link => {
        const text = link.textContent.toLowerCase();
        link.style.display = text.includes(query) ? '' : 'none';
    });
}

// 初始化：根据URL hash显示对应页面
window.addEventListener('DOMContentLoaded', function() {
    const hash = window.location.hash.slice(1);
    if (hash && document.getElementById(hash)) {
        showPage(hash);
    } else {
        showPage('home');
    }
});
'''

# ===== 6. 组装完整HTML =====
full_html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Harven AI 文档中心</title>
<style>{CSS}</style>
</head>
<body>

<!-- 顶部导航 -->
<nav class="navbar">
    <a href="#" class="navbar-brand" onclick="showPage('home');return false;">
        <span class="logo">🦆</span>
        Harven AI 文档中心
    </a>
    <div class="navbar-links">
        <span class="navbar-tab" data-page="home">🏠 首页</span>
        <span class="navbar-tab" data-page="tech">📚 技术详解</span>
        <span class="navbar-tab" data-page="install">📦 安装指南</span>
    </div>
    <div class="navbar-right">
        <span class="navbar-version">v0.3.0</span>
        <a href="https://github.com" class="navbar-github" target="_blank">GitHub</a>
    </div>
</nav>

<!-- 布局 -->
<div class="layout">
    <!-- 侧边栏 -->
    <aside class="sidebar">
        <input type="text" class="search-box" placeholder="🔍 搜索目录..." oninput="filterSidebar(this)">
        
        <!-- 首页侧边栏 -->
        <div class="sidebar-content" id="sidebar-home" style="display:none;">
            <div class="sidebar-title">导航</div>
            <a class="sidebar-link" onclick="showPage('tech')" style="cursor:pointer;">📚 技术详解 v7.0</a>
            <a class="sidebar-link" onclick="showPage('install')" style="cursor:pointer;">📦 安装指南 v2.0</a>
        </div>
        
        <!-- 技术详解侧边栏 -->
        <div class="sidebar-content" id="sidebar-tech" style="display:none;">
            <div class="sidebar-title">技术详解 v7.0</div>
            {tech_sidebar}
        </div>
        
        <!-- 安装指南侧边栏 -->
        <div class="sidebar-content" id="sidebar-install" style="display:none;">
            <div class="sidebar-title">安装指南 v2.0</div>
            {install_sidebar}
        </div>
    </aside>
    
    <!-- 内容区 -->
    <main class="content">
        <!-- 首页 -->
        <div class="page" id="home">
            <h1>🦆 Harven AI 文档中心</h1>
            <p>欢迎来到 Harven AI 哈文文档中心。哈文是一个纯本地运行的私有AI平台，永久免费，数据主权在用户。</p>
            
            <div class="home-grid">
                <div class="home-card" data-page="tech">
                    <div class="home-card-icon">📚</div>
                    <div class="home-card-title">技术详解 v7.0</div>
                    <div class="home-card-desc">完整架构设计、29项核心功能、Skill系统、协作模式、面板系统、流程编辑器等</div>
                    <div class="home-card-meta">1437行 · 15个章节 · 2026-08-09</div>
                </div>
                
                <div class="home-card" data-page="install">
                    <div class="home-card-icon">📦</div>
                    <div class="home-card-title">安装指南 v2.0</div>
                    <div class="home-card-desc">系统要求、安装步骤、API Key获取、功能配置、常见问题</div>
                    <div class="home-card-meta">330行 · 10个章节 · 2026-08-09</div>
                </div>
                
                <div class="home-card" onclick="window.open('https://harvenhub.com', '_blank')">
                    <div class="home-card-icon">🛒</div>
                    <div class="home-card-title">HarvenHub</div>
                    <div class="home-card-desc">社区市场 — Skill/Agent/模板/知识包/模型</div>
                    <div class="home-card-meta">harvenhub.com</div>
                </div>
                
                <div class="home-card" onclick="window.open('https://github.com', '_blank')">
                    <div class="home-card-icon">💻</div>
                    <div class="home-card-title">GitHub 仓库</div>
                    <div class="home-card-desc">源代码、Issue反馈、Pull Request</div>
                    <div class="home-card-meta">Apache 2.0 开源</div>
                </div>
            </div>
            
            <h2>✨ 核心特性</h2>
            <table>
                <tr><th>特性</th><th>说明</th></tr>
                <tr><td>🏠 本地优先</td><td>所有数据存储在用户本地，不外传</td></tr>
                <tr><td>🆓 永久免费</td><td>不做付费会员，不做功能阉割</td></tr>
                <tr><td>🔧 Skill系统</td><td>10个内置Skill + 可视化流程编辑器</td></tr>
                <tr><td>🤝 多模型协作</td><td>通用模型+垂直模型+工具链式编排</td></tr>
                <tr><td>📊 面板系统</td><td>7种字段+卡片视图+统计图表</td></tr>
                <tr><td>🔒 安全防护</td><td>沙箱隔离+协作模式开关+触发词管理</td></tr>
            </table>
        </div>
        
        <!-- 技术详解 -->
        <div class="page" id="tech">
            {tech_html}
        </div>
        
        <!-- 安装指南 -->
        <div class="page" id="install">
            {install_html}
        </div>
    </main>
</div>

<script>{JS}</script>
</body>
</html>'''

# ===== 7. 写入文件 =====
output_path = Path('H:/HarvenAI/docs/index.html')
output_path.write_text(full_html, encoding='utf-8')
print(f'文档中心已生成: {output_path}')
print(f'文件大小: {len(full_html) / 1024:.1f} KB')

# 复制到桌面
import shutil
desktop_path = Path('C:/Users/usera/Desktop/HarvenAI文档中心.html')
shutil.copy2(output_path, desktop_path)
print(f'已复制到桌面: {desktop_path}')
