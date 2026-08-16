#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Harven AI 完整使用指南生成器
生成 12 章交互式 HTML + 集成到 docs 中心
"""
import os, datetime

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs', 'guide.html')
DESKTOP = os.path.expanduser('~/Desktop/HarvenAI_使用指南.html')

GUIDE_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title> Harven AI 哈文 — 完整使用指南</title>
<style>
:root {--primary:#667eea;--bg:#0f0f1a;--card:#1a1a2e;--text:#e0e0e0;--border:#333;--green:#22c55e;--warn:#f59e0b;--red:#ef4444}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);line-height:1.7;padding:0;margin:0}
.container{max-width:960px;margin:0 auto;padding:20px}
header{background:linear-gradient(135deg,#1a1a3e,#2d2d5e);padding:40px 20px;text-align:center;border-bottom:3px solid var(--primary)}
header h1{font-size:2em;color:#fff;margin-bottom:8px}
header p{font-size:1.1em;color:#a0a0c0}
.toc{background:var(--card);border-radius:12px;padding:20px;margin:20px 0;border:1px solid var(--border)}
.toc h2{color:var(--primary);margin-bottom:12px;font-size:1.3em}
.toc-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px}
.toc-list a{color:#a0c0ff;text-decoration:none;padding:6px 10px;border-radius:6px;display:block;font-size:0.95em;transition:.2s}
.toc-list a:hover{background:rgba(102,126,234,0.15)}
.chapter{margin:40px 0;background:var(--card);border-radius:12px;overflow:hidden;border:1px solid var(--border)}
.chapter-title{background:linear-gradient(135deg,rgba(102,126,234,0.2),rgba(102,126,234,0.05));padding:16px 20px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;user-select:none}
.chapter-title h3{margin:0;font-size:1.2em;color:var(--primary)}
.chapter-title .arrow{font-size:1.2em;transition:.3s}
.chapter-body{padding:20px;display:none}
.chapter.open .chapter-body{display:block}
.chapter.open .arrow{transform:rotate(90deg)}
h4{color:#a78bfa;margin:16px 0 8px;font-size:1.05em}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:0.9em}
th{background:rgba(102,126,234,0.2);padding:10px 12px;text-align:left;font-weight:600;color:#a78bfa;border-bottom:2px solid var(--primary)}
td{padding:10px 12px;border-bottom:1px solid var(--border)}
tr:hover td{background:rgba(102,126,234,0.05)}
code{background:rgba(102,126,234,0.15);padding:2px 6px;border-radius:4px;font-size:0.9em;color:#a0c0ff}
.note{background:rgba(245,158,11,0.1);border-left:3px solid var(--warn);padding:12px;margin:12px 0;border-radius:0 8px 8px 0;font-size:0.9em}
.warn{background:rgba(239,68,68,0.1);border-left:3px solid var(--red);padding:12px;margin:12px 0;border-radius:0 8px 8px 0;font-size:0.9em}
.tip{background:rgba(34,197,94,0.1);border-left:3px solid var(--green);padding:12px;margin:12px 0;border-radius:0 8px 8px 0;font-size:0.9em}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.8em;margin-left:6px}
.badge-local{background:rgba(34,197,94,0.2);color:#22c55e}
.badge-cloud{background:rgba(102,126,234,0.2);color:#667eea}
.badge-collab{background:rgba(245,158,11,0.2);color:#f59e0b}
footer{text-align:center;padding:30px;color:#666;font-size:0.85em;border-top:1px solid var(--border);margin-top:40px}
@media(max-width:768px){.container{padding:12px}.toc-list{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1> Harven AI 哈文 — 完整使用指南</h1>
  <p>本指南涵盖安装、对话、模型、设置、面板、生态、手机、渠道的全部功能</p>
  <p style="font-size:0.85em;color:#888;margin-top:8px">版本 v0.3.0 · 更新于 2026-08-10</p>
</header>
<div class="container">

<div class="toc">
  <h2> 目录</h2>
  <div class="toc-list">
'''

CHAPTERS = [
    ("一、哈文能帮你干什么", "ch1", [
        ("1.1 哈文是什么？",
         "哈文就像一个住在你电脑里的智能助手。你可以用打字的方式跟它聊天，它会帮你写文章、查资料、翻译外语、管理家里的开销。"
         "它不会偷看你的隐私——所有聊天记录都保存在你自己的电脑里，不用联网也能用。"),
        ("1.2 能帮我做什么？举个几个例子",
         "• <b>聊天问答</b>：有什么不懂的，直接打字问它。<br>"
         "• <b>写东西</b>：工作报告、朋友圈文案、给孩子的请假条，告诉它需求就行。<br>"
         "• <b>翻译</b>：贴一段英文进去，让它翻成中文；或者反过来。<br>"
         "• <b>家庭记账</b>：左边面板有个家庭记账，记录每天花了多少钱。<br>"
         "• <b>定时提醒</b>：让哈文在指定时间提醒你做事，比如"每天下午3点提醒我喝水"。<br>"
         "• <b>知识库</b>：把你常用的文档（合同、菜谱、学习笔记）放进去，以后直接问哈文就能找到答案。"),
        ("1.3 用哈文需要什么？",
         "一台普通电脑就行。如果你的电脑配置比较高（内存大于8GB），哈文可以用你电脑里自己的模型回答——完全免费。"
         "如果配置不高，也可以选'外脑模式'，让网上的大模型替你回答（需要申请一个免费的账号密码，叫API Key）。两种方式都能用。"),
    ]),
    ("二、怎么安装哈文", "ch2", [
        ("2.1 第一步：双击安装",
         "把哈文的安装包解压，里面有一个叫 <code>安装哈文.bat</code> 的文件。用鼠标左键双击它。"
         "这时候屏幕会弹出一个黑色的窗口，里面会显示一些文字在跑——不用担心，这是哈文在自动帮你安装需要的东西。"),
        ("2.2 安装过程中会发生什么？",
         "黑窗口里会显示一些进度提示。整个过程大概需要5-15分钟，主要看你的网速。"
         "安装完成后，你的电脑桌面上会出现一个黄色小鸭子的图标，名字叫'Harven AI'。"
         "同时浏览器会自动打开一个页面——这就是哈文的主界面。如果浏览器没自动打开，可以自己在浏览器里输入 <code>http://localhost:7000</code> 然后按回车。"),
        ("2.3 第一次打开哈文要做什么？",
         "哈文会让你输入密码。初始密码是 <code>harven2026</code>（就这8个字母和数字，不用改）。输入完点确定，就进入主界面了。"
         "然后哈文会弹出一个3步引导，帮你完成：<br>"
         "① 改密码（换成你自己好记的）<br>"
         "② 填API Key（如果你要用外脑的话）<br>"
         "③ 拉一个本地模型（如果你想用自己的电脑运行模型）<br>"
         "不用急，这个引导随时可以关掉，以后也可以补做。"),
        ("2.4 出问题了怎么办？",
         "• 黑窗口一闪就没了：可能是你的杀毒软件拦截了。关掉杀毒软件再试一次。<br>"
         "• 浏览器打不开 localhost:7000：说明哈文没启动成功。关掉黑窗口，重新双击桌面上的图标试试。<br>"
         "• 提示缺少Python：安装包里已经带了，不需要单独装。如果真缺了，重新双击安装哈文.bat就行。"),
    ]),
    ("三、怎么选模型——本地还是外脑", "ch3", [
        ("3.1 什么是"模型"？",
         "模型就是哈文的大脑。不同的模型擅长不同的事：有的擅长聊天，有的擅长写代码，有的擅长翻译。"),
        ("3.2 本地模型——用你自己电脑的计算能力",
         "<b>优点</b>：完全免费，不需要联网，你的对话内容绝对不会上传到别人那里。<br>"
         "<b>缺点</b>：回复速度取决于你电脑的配置。如果你电脑内存小于8GB，可能会比较慢。<br>"
         "<b>什么时候用</b>：聊私事、写家里的东西、翻译、简单问答。"),
        ("3.3 按你的电脑配置选模型",
         "• <b>内存4-6GB</b>：选 <code>qwen2.5:7b</code>。回复快，日常够用。<br>"
         "• <b>内存6-8GB</b>：选 <code>qwen2.5:14b</code>。更聪明一点，写文章更好。<br>"
         "• <b>内存8GB以上</b>：选 <code>deepseek-r1:14b</code>。推理能力强，适合想让它帮你想复杂问题。<br>"
         "• <b>不知道怎么选</b>：先选 qwen2.5:7b，用着觉得慢或者不够聪明再换。换模型只需要点几下鼠标。",
         "tip"),
        ("3.4 外脑模式——让网上大模型替你回答",
         "<b>优点</b>：回复快、质量高，不需要你电脑有很好的配置。<br>"
         "<b>缺点</b>：需要去网上申请一个API Key（免费的），每次回复可能有少量费用（一次对话几分钱）。<br>"
         "<b>什么时候用</b>：你电脑配置不高、或者想要更好的回复质量。"),
        ("3.5 到底选哪个？",
         "如果不太确定，可以先试试外脑模式——回复快、质量好。等用熟了再考虑要不要拉一个本地模型。两个可以随时切换，互不影响。"),
    ]),
    ("四、怎么拉一个本地模型", "ch4", [
        ("4.1 去哪里拉模型",
         "打开哈文 → 左边找到 ⚙️设置 按钮点一下 → 顶部选 模型管理 → 往下找，有一个叫"一键拉模型"的大按钮。"),
        ("4.2 拉模型要等多久？",
         "点了一键拉模型之后，哈文会开始从网上下载模型文件。模型比较大，通常2-5GB，所以需要一些时间：<br>"
         "• 网速快的话：5-10分钟<br>"
         "• 网速慢的话：15-30分钟<br>"
         "下载过程中，按钮旁边会显示一个进度百分比。你可以关掉设置页面去干别的，下载不会停。"),
        ("4.3 拉完模型之后做什么？",
         "下载完成会提示'✅ qwen2.5:14b 已就绪'。这时候：<br>"
         "① 回到对话页面<br>"
         "② 找到页面底部的"分析"按钮，看到"本地"亮起来了<br>"
         "③ 在输入框打几个字试试，比如"你好，请问今天几号"<br>"
         "④ 哈文会用你的本地模型回复你。第一次回复会慢一点点（模型需要预热），之后就好了。"),
        ("4.4 拉模型失败了怎么办？",
         "• 下载一半断了：重新点一次"一键拉模型"，会自动从断的地方继续。<br>"
         "• 提示空间不足：你的硬盘不够用了，清理一下或者换个小一点的模型。<br>"
         "• 提示连接失败：你的网络可能不太稳定，换个时间再试。"),
    ]),
    ("五、怎么用外脑模式", "ch5", [
        ("5.1 什么是API Key？",
         "API Key就像一张门禁卡。你去大模型公司（比如DeepSeek）的网站上免费注册一个账号，他们会给你一串字母数字。"
         "把这串字母数字填到哈文里，哈文就能用他们的模型帮你回答了。"),
        ("5.2 去哪里申请API Key？",
         "目前哈文支持这几家（选一个就行，不用都注册）：<br>"
         "• <b>DeepSeek</b>：去 platform.deepseek.com 注册，很简单，用手机号就行。新用户送免费额度。<br>"
         "• <b>智谱（GLM）</b>：去 open.bigmodel.cn 注册。<br>"
         "• <b>豆包</b>：去 console.volcengine.com 注册。<br>"
         "注册完成后，在网站里找到'API Key'或'API密钥'这几个字，点创建，复制生成的那串字母数字。"),
        ("5.3 怎么填到哈文里？",
         "① 打开哈文 → ⚙️设置 → 模型管理<br>"
         "② 找到你要填的那一栏（比如" DeepSeek"）<br>"
         "③ 把复制的那串字母数字粘贴到输入框里<br>"
         "④ 点 保存 按钮<br>"
         "⑤ 在"当前使用的外脑"下拉框里选 DeepSeek<br>"
         "⑥ 回到对话页面，底部选 外脑 模式。打完字，DeepSeek帮你回答！"),
        ("5.4 为什么外脑切换了还是不生效？",
         "切完外脑后等3-5秒，哈文需要一点时间切换。如果还是不对，刷新一下页面（按键盘上的F5键）。"
         "另外记得检查设置→模型管理里，你选的外脑旁边显示的是'✅已配置'而不是'❌未配置'。"),
        ("5.5 外脑要花钱吗？",
         "每个API Key都有免费额度。DeepSeek新用户送几十万token的免费额度，日常聊天用一个月没问题。"
         "用完免费额度后，充10块钱能用很久。每次对话大概几分钱，非常便宜。"),
    ]),
    ("六、设置里每个按钮是干嘛的", "ch6", [
        ("6.1  基本设置",
         "这里可以改哈文运行的端口号（一般不用动）、设置开机自动启动。如果你想让哈文每次开机自己启动，勾上就行。"),
        ("6.2  模型管理",
         "最重要的设置页。在这里你可以：<br>"
         "• 填API Key（让哈文用外脑）<br>"
         "• 拉模型（让哈文用本地模型）<br>"
         "• 切换外脑（在DeepSeek和智谱之间换）<br>"
         "• 开深度搜索（让外脑先上网搜再回答，更准确）<br>"
         "• 加自定义模型（如果你有自己的模型地址）<br>"
         "• 隐私档位（控制发给外脑的信息量）"),
        ("6.3  个人画像",
         "告诉哈文你的一些基本信息：名字、职业、兴趣爱好。填好之后，哈文回答会更符合你的情况。"
         "比如你填了职业是老师，它回答时会用更适合教学的语气。不填也可以正常用。"),
        ("6.4  外观定制",
         "改主题颜色、字体大小。手机和电脑上同步生效。如果你觉得字太小了，在这里调大就好。"),
        ("6.5  安全隐私",
         "最重要的安全设置：<br>"
         "• 改密码：把初始密码 harven2026 换成你自己好记的<br>"
         "• 隐私档位：控制用外脑时发给对方的数据量。建议选'⚖️ 平衡'，自动隐藏身份证号、手机号等敏感信息"),
        ("6.6 ⚙️ 高级功能",
         "• 硬件检测：看看你的电脑能不能跑本地模型<br>"
         "•  手机+公网：显示你的IP地址，手机上输入这个地址就能用哈文<br>"
         "•  渠道接入：把哈文接到飞书/钉钉群"),
        ("6.7  数据",
         "备份和恢复你的对话记录。建议隔一段时间备份一次，以防万一。"),
    ]),
    ("七、左边那些面板是什么", "ch7", [
        ("7.1  知识库",
         "把你常用的文件放进去——合同、菜谱、学习资料、公司手册等等。然后你跟哈文聊天时，它可以自动查阅这些文件来回答你。"
         "比如你上传了一份'公司报销制度.pdf'，以后问它'出差能报销多少'，它就会从这份文件里找答案。"
         "<br><b>支持的文件格式</b>：txt、pdf、word(docx)、markdown。每个文件不超过50MB。<br>"
         "<b>怎么用</b>：点知识库 → 建一个知识库（比如叫"工作文档"）→ 点上传按钮选文件 → 回到对话页，在顶部绑定这个知识库。"),
        ("7.2  专项培养（Agent）",
         "你可以让哈文扮演不同的角色。比如创建一个"英语老师"Agent，之后跟它聊天就都是英语练习；"
         "创建一个"代码助手"Agent，它就专注于帮你写代码。"
         "<br>创建方法：点专项培养 → 新建 → 起个名字（如"翻译官"） → 写一段说明（如"你会把中文翻译成英文"） → 保存。"
         "之后在对话中@翻译官 即可触发。"),
        ("7.3  任务管理",
         "设置定时任务，让哈文定时帮你做事。比如：<br>"
         "• 每天早上7点推送今日天气和日程<br>"
         "• 每3天提醒查看冰箱食材是否过期<br>"
         "• 每月1号提醒记账月结<br>"
         "任务需要哈文在运行状态才能执行（电脑不关机、哈文不关）。"),
        ("7.4  监控中心",
         "能看到哈文当前占用了多少内存、CPU、运行了多久。一般不用管，只有在觉得哈文变慢的时候才来看。"),
        ("7.5  HarvenHub",
         "这里是哈文的'应用商店'。别人写好的Skill插件可以在这里浏览和安装。"
         "比如有人写了一个'一键生成PPT'的Skill，你在这里搜到→安装→回到对话里告诉哈文'帮我生成一个季度汇报PPT'，它就会用这个Skill来做。"),
        ("7.6  家庭记账",
         "记录每天的收支。告诉哈文'今天买菜花了50块'，它自动记进去。月底可以问它'这个月总共花了多少'。"),
        ("7.7  居家管理 + 我的面板",
         "菜谱管理、家务清单、购物清单，跟任务面板互通。"
         "我的面板可以自己组合常用功能，比如把记账和菜谱放在一起。"),
    ]),
    ("八、手机也能用哈文", "ch8", [
        ("8.1 怎么在手机上打开",
         "电脑和手机连同一个WiFi（注意：必须是同一个WiFi，手机用流量不行）。然后：<br>"
         "① 电脑上打开哈文 → ⚙️设置 → ⚙️高级功能 → 手机+公网<br>"
         "② 里面会显示一个地址，类似 <code>192.168.1.57:7000</code><br>"
         "③ 在手机浏览器里输入这个地址 → 就能看到哈文的界面了！"),
        ("8.2 iPhone 用户注意",
         "左上角有一个 ☰ 按钮，点它拉出侧边栏，里面有设置、知识库、HarvenHub 等全部功能。"
         "如果不点 ☰ 按钮，只能看到对话区，看不到其他功能。"),
        ("8.3 手机上能做什么？不能做什么？",
         "✅ 可以：聊天、查知识库、用外脑、看记账、设任务<br>"
         "❌ 不可以：拉模型（只能在电脑上操作）。已经拉好的模型手机上可以正常用。"),
        ("8.4 为什么手机打不开？",
         "• 检查电脑和手机在同一个WiFi下<br>"
         "• 检查哈文在电脑上正常运行<br>"
         "• 关了WiFi重连试试<br>"
         "• 如果还是不行，可能是电脑防火墙拦了，关掉防火墙再试"),
    ]),
    ("九、怎么接到飞书/钉钉群", "ch9", [
        ("9.1 能把哈文加到群里？",
         "可以！你可以把哈文接到飞书群或钉钉群里，让群成员也能跟哈文对话。只需要在群里添加一个机器人，把它的Webhook地址填到哈文里就行。"),
        ("9.2 飞书群怎么接",
         "① 在电脑上打开飞书 → 进入目标群 → 群设置 → 群机器人 → 添加自定义机器人<br>"
         "② 复制生成的 Webhook URL（类似 https://open.feishu.cn/open-apis/bot/v2/hook/xxx）<br>"
         "③ 回到哈文：⚙️设置 → ⚙️高级功能 → 渠道接入 → 选"飞书机器人" → 粘贴 → 添加<br>"
         "④ 配置完成后，群里的人发消息，哈文就能看到并回复了。"),
        ("9.3 钉钉群怎么接",
         "步骤跟飞书一样，只是选"钉钉机器人"。钉钉的Webhook在群设置→智能群助手→添加机器人里找到。"),
        ("9.4 注意事项",
         "• 飞书/钉钉机器人是单向通知（哈文→群），不能像在电脑上那样双向对话<br>"
         "• 如果需要双向对话，用手机浏览器打开哈文更方便"),
    ]),
    ("十、HarvenHub 应用商店", "ch10", [
        ("10.1 什么是HarvenHub？",
         "可以理解成哈文的'应用商店'。别人写好的工具（叫Skill）和智能体（叫Agent）都在这里，你可以像手机装App一样装到你的哈文里。"),
        ("10.2 怎么逛Hub？",
         "左边面板点  HarvenHub → 进入市场页面。可以看到分类列表：<br>"
         "•  Skill：各种工具插件（写Excel、生成PPT、扫码等）<br>"
         "•  Agent：各种专业角色（英文老师、代码助手等）<br>"
         "•  Template：流程模板<br>"
         "•  Prompt：提示词模板<br>"
         "看到感兴趣的，点进去看介绍，觉得有用就点"安装"。安装前哈文会告诉你这个工具需要什么额外的软件包，缺的会自动帮你装。"),
        ("10.3 安装完怎么用？",
         "装完之后，回到对话页面，直接告诉哈文你想做什么就行。"
         "比如装了'PPT生成'Skill，就对哈文说：'帮我生成一个季度工作汇报的PPT'，它会自动调用这个Skill来完成。"),
        ("10.4 我也想写一个Skill分享",
         "如果你有编程基础，可以写Python脚本做个Skill，然后通过HarvenHub上传分享给其他人使用。"
         "怎么写Skill可以看 <code>/help skill制作</code> 或问哈文本人。"),
    ]),
    ("十一、常见问题（真实用户遇到的）", "ch11", [
        ("Q1: 为什么我打开哈文是空白的？",
         "90%的情况是因为你的浏览器缓存了旧页面。按键盘上的 Ctrl+Shift+R（三个键一起按）强制刷新。如果还不行，关掉浏览器重新打开。"),
        ("Q2: 对话记录突然消失了？",
         "可能是被自动分类到了别的分类里。在左边对话列表里找找。也可以用顶部的搜索框搜关键字。"),
        ("Q3: 外脑模式选了，回答的还是本地模型？",
         "切换外脑后等3秒。如果还是不对，检查设置→模型管理里，外脑状态是不是'✅已配置'。不是的话，说明API Key没填对。"),
        ("Q4: 忘了密码怎么办？",
         "在哈文的安装目录里找到 config.yaml 文件，用记事本打开，里面有一行 <code>admin_password: 后面就是你的密码</code>。<br>"
         "如果没改过，密码就是 harven2026。"),
        ("Q5: 外脑花了多少钱？",
         "一次对话大概1-3毛钱。充10块钱大概能用100次对话。比订一个月会员便宜得多。"),
        ("Q6: 本地模型跑得很慢怎么办？",
         "换个更小的模型（qwen2.5:7b 比 14b 快很多），或者直接切换到外脑模式。"),
        ("Q7: 安装时黑窗口报错了？",
         "截图发给我们，常见的都是网络问题或杀毒软件拦截。"),
        ("Q8: 能把哈文放在公司电脑用吗？",
         "可以，只要不把7000端口暴露到公网就行。同一办公室内的电脑互相连接没问题。"),
        ("Q9: iPhone打开哈文，看不到输入框？",
         "iPhone Safari 的底部工具栏挡住了。往上滑一点就行。或者把哈文添加到桌面（Safari分享按钮→添加到主屏幕），单独打开就不会被拦。"),
        ("Q10: 知识库上传了文件，问它说找不到？",
         "可能是文件太大或格式不对。试试把文件内容复制粘贴到一个txt文件里再上传。txt格式最稳。"),
    ]),
    ("十二、让哈文更安全更好用", "ch12", [
        ("12.1 改掉默认密码",
         "初始密码 harven2026 你应该已经知道了。去 ⚙️设置 → 安全隐私 → 改一个只有你自己知道的密码。"),
        ("12.2 不要让陌生人访问你的哈文",
         "不要把 http://你的IP:7000 这个地址发给不认识的人。只有你和家人在家里时用。"
         "如果一定要在外面用，考虑设置一个更复杂的密码。"),
        ("12.3 外脑隐私档位怎么选",
         "• <b>全量</b>：全部对话内容发给外脑，适合讨论完全不在意的事<br>"
         "• <b>平衡</b>（推荐）：自动隐藏身份证号、手机号等敏感信息，大部分情况够用<br>"
         "• <b>严格</b>：只传去敏后的摘要，最安全但回答质量可能下降"),
        ("12.4 定期备份你的数据",
         "去 ⚙️设置 → 数据 → 备份。建议每个月备份一次。备份文件保存在你的电脑里，不会上传到网上。"),
        ("12.5 更新哈文",
         "哈文会提示有新版本时，下载新安装包覆盖安装即可。不用担心数据丢失——对话记录和设置都在。"),
    ]),
]

def gen_toc():
    items = []
    for title, anchor, _ in CHAPTERS:
        items.append(f'<a href="#{anchor}"> {title}</a>')
    return '\n'.join(items)

def gen_chapter(title, anchor, sections):
    html = f'''<div class="chapter open" id="{anchor}">
  <div class="chapter-title" onclick="this.parentElement.classList.toggle('open')">
    <h3> {title}</h3>
    <span class="arrow">▶</span>
  </div>
  <div class="chapter-body">
'''
    for subtitle, content in sections:
        if subtitle.startswith('Q'):
            html += f'    <div class="note"><b>{subtitle}</b><br>{content}</div>\n'
        elif '警告' in subtitle or '注意' in subtitle:
            html += f'    <h4>⚠️ {subtitle}</h4>\n    <div class="warn">{content}</div>\n'
        elif '建议' in subtitle or '提示' in subtitle:
            html += f'    <h4> {subtitle}</h4>\n    <div class="tip">{content}</div>\n'
        else:
            html += f'    <h4>{subtitle}</h4>\n    <p>{content}</p>\n'
    html += '''  </div>
</div>'''
    return html

# 组装
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(GUIDE_HTML)
    f.write(gen_toc())
    f.write('''  </div>
</div>
''')
    for title, anchor, sections in CHAPTERS:
        f.write(gen_chapter(title, anchor, sections))
        f.write('\n')
    f.write('''<footer>
   Harven AI 哈文 v0.3.0 · 完整使用指南 · 2026-08-10<br>
  有问题？文档中心查看更多技术细节
</footer>
</div>
<script>
// 展开/折叠章节
document.querySelectorAll('.chapter-title').forEach(t => {
  t.addEventListener('click', () => t.parentElement.classList.toggle('open'));
});
// 默认展开第一个
document.querySelector('.chapter').classList.add('open');
</script>
</body>
</html>''')

# 复制到桌面
import shutil
shutil.copy(OUT, DESKTOP)
print(f"✅ 使用指南已生成: {OUT}")
print(f"  大小: {os.path.getsize(OUT)/1024:.1f} KB")
print(f"  桌面: {DESKTOP}")
print(f"  章节: {len(CHAPTERS)} 章")
