#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_harven.py — 哈文一键全量巡检脚本（#1216，2026-10-25）
================================================
用法（必须用哈文自带 venv 跑）:
    H:\\HarvenAI\\.venv\\Scripts\\python.exe H:\\HarvenAI\\check_harven.py

输出:
    1) 控制台逐项 PASS/FAIL/WARN/SKIP + 每批 N/N + 总 N/N
    2) 报告落盘 check_reports/check_YYYYMMDD_HHMMSS.txt
    3) 末尾输出人工交互 checklist（聊天/面板/手机等脚本测不了的项目）

设计原则:
    - 只读巡检：不改任何数据/配置/文件（唯一写入是报告文件）
    - 单项失败不中断：继续跑完，最后汇总
    - 网络类检查超时即 WARN，不算 FAIL（区分「服务坏了」和「网不通」）
"""
import os
import sys
import glob
import json
import socket
import sqlite3
import hashlib
import subprocess
import urllib.request
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
STAGING_ROOT = os.path.join(PROJECT_ROOT, '_package_staging')
REPORT_DIR = os.path.join(PROJECT_ROOT, 'check_reports')
HTTP_TIMEOUT = 6
CLOUD_TIMEOUT = 8

# 状态常量
PASS, FAIL, WARN, SKIP = 'PASS', 'FAIL', 'WARN', 'SKIP'
STATUS_ICON = {PASS: '[PASS]', FAIL: '[FAIL]', WARN: '[WARN]', SKIP: '[SKIP]'}

results = []  # (batch_no, batch_name, cid, name, status, detail)


def add(batch_no, batch_name, cid, name, status, detail=''):
    results.append((batch_no, batch_name, cid, name, status, detail))


def http_get(url, timeout=HTTP_TIMEOUT):
    """返回 (status_code, body_text) 或抛异常"""
    req = urllib.request.Request(url, headers={'User-Agent': 'HarvenCheck/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(4096).decode('utf-8', errors='replace')


# ============================================================
# 批一 基础运行
# ============================================================
def check_batch1():
    bn, name = 1, '基础运行'

    # 1.1 端口 7000 监听
    pid = None
    try:
        with socket.create_connection(('127.0.0.1', 7000), timeout=3):
            pass
        # 拿 PID（供 1.2 用）★ netstat 输出 GBK，必须显式编码否则 UnicodeDecodeError
        try:
            out = subprocess.run(['netstat', '-ano'], capture_output=True, timeout=15,
                                 encoding='gbk', errors='replace').stdout or ''
            for line in out.splitlines():
                if ':7000' in line and 'LISTENING' in line.upper():
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        break
        except Exception:
            pass
        add(bn, name, '1.1', '端口 7000 LISTENING', PASS, '可连接' + (f' (PID={pid})' if pid else ''))
    except Exception as e:
        add(bn, name, '1.1', '端口 7000 LISTENING', FAIL, f'连不上: {e}')
        # 服务没起，后续 HTTP 检查全部 SKIP
        for cid, cname in [('1.3', 'HTTP 根路径 200/302'), ('1.4', 'SPA 壳 /spa/ 200'), ('9.1', '/phone 路由 200')]:
            add(bn if cid != '9.1' else 9, name if cid != '9.1' else '手机/云端', cid, cname, SKIP, '服务未运行')
        return

    # 1.2 进程 = 哈文 venv python（★ wmic 在 Win11 26200 已移除，改 PowerShell）
    if pid:
        try:
            out = subprocess.run(
                ['powershell', '-NoProfile', '-Command', f'(Get-Process -Id {pid}).Path'],
                capture_output=True, text=True, timeout=20).stdout.strip()
            path = out.strip()
            if '.venv' in path.lower() and 'python' in path.lower():
                add(bn, name, '1.2', '进程为哈文 venv Python', PASS, path)
            elif path:
                add(bn, name, '1.2', '进程为哈文 venv Python', WARN, f'路径不含 .venv: {path}')
            else:
                # 哈文 bat 自提权管理员运行时，普通权限查不到路径 = 预期现象
                add(bn, name, '1.2', '进程为哈文 venv Python', WARN,
                    f'查不到路径（哈文提权管理员运行时普通会话查不到，属预期；服务已在跑见 1.1，PID={pid}）')
        except Exception as e:
            add(bn, name, '1.2', '进程为哈文 venv Python', WARN, f'PowerShell 失败: {e}')
    else:
        add(bn, name, '1.2', '进程为哈文 venv Python', SKIP, '未取到 PID')

    # 1.3 / 1.4 HTTP 活性
    for cid, url, cname in [
        ('1.3', 'http://127.0.0.1:7000/', 'HTTP 根路径 200/302'),
        ('1.4', 'http://127.0.0.1:7000/spa/', 'SPA 壳 /spa/ 200'),
    ]:
        try:
            code, _ = http_get(url)
            ok = code in (200, 302, 301)
            add(bn, name, cid, cname, PASS if ok else FAIL, f'HTTP {code}')
        except Exception as e:
            add(bn, name, cid, cname, FAIL, str(e)[:80])

    # 1.5 启动日志近期无 Traceback（找最近 48h 内的日志）
    log_dirs = [os.path.join(PROJECT_ROOT, 'logs'), os.path.join(PROJECT_ROOT, 'data', 'logs')]
    newest_log, newest_mtime = None, 0
    for d in log_dirs:
        for f in glob.glob(os.path.join(d, '**', '*.log*'), recursive=True):
            m = os.path.getmtime(f)
            if m > newest_mtime:
                newest_mtime, newest_log = m, f
    if newest_log and (datetime.now().timestamp() - newest_mtime) < 48 * 3600:
        try:
            # ★ 噪音白名单：xformers 探测 triton 在 Windows 必然失败，无害
            NOISE = ('triton',)
            with open(newest_log, 'r', encoding='utf-8', errors='replace') as fh:
                tail = fh.readlines()[-500:]
            tb = 0
            for i, l in enumerate(tail):
                if 'Traceback' in l:
                    ctx = ''.join(tail[i:i + 8]).lower()
                    if not any(n in ctx for n in NOISE):
                        tb += 1
            if tb == 0:
                add(bn, name, '1.5', '近期日志无 Traceback', PASS,
                    os.path.basename(newest_log) + '（xformers/triton 探测噪音已滤）')
            else:
                add(bn, name, '1.5', '近期日志无 Traceback', WARN, f'{os.path.basename(newest_log)} 尾部 {tb} 处真实 Traceback，人工复核')
        except Exception as e:
            add(bn, name, '1.5', '近期日志无 Traceback', WARN, str(e)[:60])
    else:
        add(bn, name, '1.5', '近期日志无 Traceback', SKIP, '未找到 48h 内日志文件（可能日志未落盘，人工看启动窗口）')

    # 1.6 Flask threaded=True
    try:
        with open(os.path.join(PROJECT_ROOT, 'web_app.py'), 'r', encoding='utf-8', errors='replace') as fh:
            src = fh.read()
        if 'threaded=True' in src or 'threaded = True' in src:
            add(bn, name, '1.6', 'Flask threaded=True', PASS)
        else:
            add(bn, name, '1.6', 'Flask threaded=True', FAIL, 'web_app.py 未找到 threaded=True')
    except Exception as e:
        add(bn, name, '1.6', 'Flask threaded=True', WARN, str(e)[:60])


# ============================================================
# 批二 代码健康
# ============================================================
def check_batch2():
    bn, name = 2, '代码健康'

    # 2.1 全量 py_compile
    import py_compile
    targets = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(PROJECT_ROOT, 'src')):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for f in filenames:
            if f.endswith('.py'):
                targets.append(os.path.join(dirpath, f))
    for extra in ['web_app.py', 'main.py', 'device_api.py', 'agent_registry.py',
                  'model_routes.py', 'multi_model_api.py', 'new_features_api.py', 'plugin_mgmt_api.py']:
        p = os.path.join(PROJECT_ROOT, extra)
        if os.path.exists(p):
            targets.append(p)
    bad = []
    import tempfile
    _tmp_dir = tempfile.gettempdir()
    for i, t in enumerate(targets):
        try:
            # ★ cfile 不能用 os.devnull（Windows 'nul' 会被 py_compile 拒绝），落到临时目录
            py_compile.compile(t, doraise=True,
                               cfile=os.path.join(_tmp_dir, f'harven_check_{i}.pyc'))
        except Exception as e:
            bad.append(f'{os.path.relpath(t, PROJECT_ROOT)}: {e}')
    if bad:
        add(bn, name, '2.1', f'py_compile 全量（{len(targets)} 文件）', FAIL, ' | '.join(bad[:3]))
    else:
        add(bn, name, '2.1', f'py_compile 全量（{len(targets)} 文件）', PASS)

    # 2.2 / 2.3 import 冒烟 + 顺便拿 url_map（本进程内做，省一次子进程）
    web_app_mod = None
    try:
        if PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        import web_app as web_app_mod
        add(bn, name, '2.2', 'import web_app 冒烟', PASS)
    except Exception as e:
        add(bn, name, '2.2', 'import web_app 冒烟', FAIL, str(e)[:120])
    try:
        from src.core import engine  # noqa: F401
        add(bn, name, '2.3', 'import engine 冒烟', PASS)
    except Exception as e:
        add(bn, name, '2.3', 'import engine 冒烟', FAIL, str(e)[:120])

    # 2.4 废弃引用扫描（lingzi.db 等）
    try:
        dead_hits = []
        src_root = os.path.join(PROJECT_ROOT, 'src')
        for dirpath, dirnames, filenames in os.walk(src_root):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for f in filenames:
                if not f.endswith('.py'):
                    continue
                p = os.path.join(dirpath, f)
                with open(p, 'r', encoding='utf-8', errors='replace') as fh:
                    txt_lines = fh.readlines()
                # ★ 只看活代码：跳过纯注释行（历史 bug 备注里提 lingzi.db 属正常）
                hit = any('lingzi.db' in l for l in txt_lines
                          if l.strip() and not l.strip().startswith('#'))
                if hit:
                    dead_hits.append(os.path.relpath(p, PROJECT_ROOT))
        if dead_hits:
            add(bn, name, '2.4', '废弃引用扫描 (lingzi.db 活代码)', WARN,
                '仍有活代码引用: ' + ', '.join(dead_hits[:5]))
        else:
            add(bn, name, '2.4', '废弃引用扫描 (lingzi.db 活代码)', PASS, 'src/ 活代码无引用')
    except Exception as e:
        add(bn, name, '2.4', '废弃引用扫描 (lingzi.db)', WARN, str(e)[:60])

    # 2.5 / 10.4 live ↔ staging MD5 对账
    compare_rel = []
    src_root = os.path.join(PROJECT_ROOT, 'src')
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), PROJECT_ROOT)
            compare_rel.append(rel)
    for extra in ['web_app.py', 'main.py',
                  'spa/src/stores/chat.js', 'spa/src/pages/HomePage.vue',
                  'static/spa/index.html']:
        p = os.path.join(PROJECT_ROOT, extra)
        if os.path.exists(p):
            compare_rel.append(extra)
    assets = os.path.join(PROJECT_ROOT, 'static', 'spa', 'assets')
    if os.path.isdir(assets):
        for f in os.listdir(assets):
            compare_rel.append(f'static/spa/assets/{f}')

    def md5(p):
        h = hashlib.md5()
        with open(p, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()

    mism, missing_stg, same = [], [], 0
    for rel in compare_rel:
        live_p = os.path.join(PROJECT_ROOT, rel)
        stg_p = os.path.join(STAGING_ROOT, rel)
        if not os.path.exists(stg_p):
            missing_stg.append(rel)
            continue
        if md5(live_p) != md5(stg_p):
            mism.append(rel)
        else:
            same += 1
    # staging 多出的孤儿产物（旧构建残留）
    stg_assets = os.path.join(STAGING_ROOT, 'static', 'spa', 'assets')
    orphan = []
    if os.path.isdir(stg_assets):
        live_assets = set(os.listdir(assets)) if os.path.isdir(assets) else set()
        for f in os.listdir(stg_assets):
            if f not in live_assets:
                orphan.append(f'static/spa/assets/{f}')
    total = len(compare_rel)
    if not mism and not missing_stg and not orphan:
        add(bn, name, '2.5', f'live↔staging MD5 对账（{total} 文件）', PASS, f'{same}/{total} 一致，无孤儿')
    else:
        detail = []
        if mism:
            detail.append(f'MD5不一致{len(mism)}: ' + ', '.join(mism[:5]))
        if missing_stg:
            detail.append(f'staging缺{len(missing_stg)}: ' + ', '.join(missing_stg[:5]))
        if orphan:
            detail.append(f'staging孤儿{len(orphan)}: ' + ', '.join(orphan[:5]))
        add(bn, name, '2.5', f'live↔staging MD5 对账（{total} 文件）', FAIL, ' | '.join(detail))

    return web_app_mod


# ============================================================
# 批四 工具链（FC/B方案/Skill 静态可测部分）
# ============================================================
def check_batch4():
    bn, name = 4, '工具链'
    try:
        from src.core.builtin_tools import TOOL_REGISTRY, get_tool_definitions
        defs = get_tool_definitions()
        def_names = {d['function']['name'] for d in defs}
        # ★ 对账口径与 get_tool_definitions 一致：排除别名(A5)、未装机可选(A6)、未授权联网工具(方案A)
        from src.core.builtin_tools import _optional_available
        should_inject = {}
        for n, info in TOOL_REGISTRY.items():
            if info.get('alias'):
                continue
            opt = info.get('optional')
            if opt and not _optional_available(opt):
                continue
            if info.get('requires') == 'web_search_enabled':
                continue  # 方案A联网授权制：默认不注入
            should_inject[n] = info
        reg_names = set(should_inject.keys())
        missing = reg_names - def_names
        if not missing:
            add(bn, name, '4.2', 'FC 定义与注册表一致', PASS,
                f'应注入 {len(reg_names)} 工具 = defs {len(defs)} 条（别名/未装机可选已按设计排除）')
        else:
            add(bn, name, '4.2', 'FC 定义与注册表一致', FAIL, f'注册未进 defs: {sorted(missing)[:8]}')
        # hub 新工具
        hub_ok = 'hub_search' in reg_names and 'hub_install' in reg_names
        add(bn, name, '4.2b', 'hub_search/hub_install 已注册', PASS if hub_ok else FAIL)
        # 危险工具闸门含 hub_install
        # _DANGER_TOOLS_FC 定义在 engine 函数内部，从源码文本提取集合字面量
        eng_src = open(os.path.join(PROJECT_ROOT, 'src', 'core', 'engine.py'),
                       encoding='utf-8', errors='replace').read()
        seg = ''
        key_i = eng_src.find('_DANGER_TOOLS_FC = {')
        if key_i >= 0:
            seg = eng_src[key_i:eng_src.find('}', key_i)]
        hub_in = 'hub_install' in seg
        add(bn, name, '4.4', '危险工具闸门含 hub_install',
            PASS if hub_in else FAIL,
            f'集合 {seg.count(chr(39)) // 2} 个工具' if seg else '未找到 _DANGER_TOOLS_FC 定义')
    except Exception as e:
        add(bn, name, '4.2', 'FC 定义与注册表一致', FAIL, f'导入失败: {str(e)[:100]}')

    # 4.6 Skill 三件套
    skills_root = os.path.join(PROJECT_ROOT, 'data', 'skills')
    if os.path.isdir(skills_root):
        total, no_md, no_scripts = 0, [], []
        for d in sorted(os.listdir(skills_root)):
            dp = os.path.join(skills_root, d)
            if not os.path.isdir(dp):
                continue
            total += 1
            if not os.path.exists(os.path.join(dp, 'SKILL.md')):
                no_md.append(d)
        if not no_md:
            add(bn, name, '4.6', f'Skill 三件套（{total} 个）', PASS, 'SKILL.md 全齐')
        else:
            add(bn, name, '4.6', f'Skill 三件套（{total} 个）', WARN, f'缺 SKILL.md: {no_md[:5]}')
    else:
        add(bn, name, '4.6', 'Skill 三件套', SKIP, 'data/skills 不存在')

    # 4.7 权限默认值（security_guard 模块级默认，重启自动回 safe 的根基）
    try:
        sg_src = open(os.path.join(PROJECT_ROOT, 'src', 'core', 'security_guard.py'),
                      encoding='utf-8', errors='replace').read()
        if "_permission_mode = 'safe'" in sg_src:
            add(bn, name, '4.7', '权限默认值 safe', PASS, 'security_guard._permission_mode 默认 safe（重启自动回归有根基）')
        else:
            add(bn, name, '4.7', '权限默认值 safe', WARN, '未找到默认 safe 声明，人工确认')
    except Exception as e:
        add(bn, name, '4.7', '权限默认值 safe', WARN, str(e)[:60])


# ============================================================
# 批五 面板数据（数据库可测部分）
# ============================================================
def check_batch5():
    bn, name = 5, '面板数据'
    db = os.path.join(PROJECT_ROOT, 'data', 'sqlite', 'lingzhi.db')
    if not os.path.exists(db):
        add(bn, name, '5.2', '数据真家 lingzhi.db', FAIL, '文件不存在！')
        add(10, '数据与发版', '10.1', 'SQLite integrity_check', SKIP, '无库可查')
        return
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        panels = [t for t in tables if t.startswith('panel_')]
        add(bn, name, '5.2', '数据真家 lingzhi.db', PASS, f'{len(tables)} 表，其中 panel_* {len(panels)} 张')
        # 10.1 完整性
        ic = cur.execute('PRAGMA integrity_check').fetchone()[0]
        add(10, '数据与发版', '10.1', 'SQLite integrity_check', PASS if ic == 'ok' else FAIL, ic)
        conn.close()
    except Exception as e:
        add(bn, name, '5.2', '数据真家 lingzhi.db', FAIL, str(e)[:80])
        add(10, '数据与发版', '10.1', 'SQLite integrity_check', WARN, str(e)[:60])

    # 10.2 备份文件
    backups = glob.glob(os.path.join(PROJECT_ROOT, 'data', '**', 'backup_*.json'), recursive=True)
    if backups:
        newest = max(backups, key=os.path.getmtime)
        age_days = (datetime.now().timestamp() - os.path.getmtime(newest)) / 86400
        add(10, '数据与发版', '10.2', '测试数据备份存在', PASS if age_days < 30 else WARN,
            f'最新: {os.path.basename(newest)}（{age_days:.0f} 天前）')
    else:
        add(10, '数据与发版', '10.2', '测试数据备份存在', WARN, 'data/ 下未找到 backup_*.json')


# ============================================================
# 批九 手机/云端（可达性可测部分）
# ============================================================
def check_batch9():
    bn, name = 9, '手机/云端'
    # 9.1 /phone
    try:
        code, _ = http_get('http://127.0.0.1:7000/phone')
        add(bn, name, '9.1', '/phone 路由', PASS if code in (200, 302) else FAIL, f'HTTP {code}')
    except Exception as e:
        add(bn, name, '9.1', '/phone 路由', FAIL, str(e)[:60])
    # 9.2 云端 Hub 可达（网络 WARN 制）
    try:
        code, body = http_get('https://harvenhub.com/', timeout=CLOUD_TIMEOUT)
        add(bn, name, '9.2', '云端 harvenhub.com 可达', PASS if code == 200 else WARN, f'HTTP {code}')
    except Exception as e:
        add(bn, name, '9.2', '云端 harvenhub.com 可达', WARN, f'网络不通或超时: {str(e)[:60]}')


# ============================================================
# 批十 数据与发版（version.json）
# ============================================================
def check_batch10_version():
    bn = 10
    candidates = [os.path.join(PROJECT_ROOT, 'data', 'version.json'),
                  os.path.join(PROJECT_ROOT, 'version.json'),
                  os.path.join(STAGING_ROOT, 'data', 'version.json')]
    found = next((c for c in candidates if os.path.exists(c)), None)
    if not found:
        add(bn, '数据与发版', '10.5', 'version.json 清单', SKIP, '未找到，位置人工确认')
        return
    try:
        with open(found, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        has_core = ('changelog' in json.dumps(data)) or ('version' in json.dumps(data))
        has_md5 = 'package_md5' in json.dumps(data)
        # package_md5 只注入发版清单包副本（pack_release.py 动态生成），live 原件没有属正常
        add(bn, '数据与发版', '10.5', 'version.json 清单', PASS if has_core else WARN,
            f'核心字段{"在" if has_core else "缺失"}' + ('；package_md5 在清单包副本才注入（正常）' if not has_md5 else '；package_md5 也在'))
    except Exception as e:
        add(bn, '数据与发版', '10.5', 'version.json 清单', WARN, str(e)[:60])


# ============================================================
# 人工 checklist（脚本测不了的交互项）
# ============================================================
MANUAL_CHECKLIST = """
━━━ 人工交互 checklist（脚本测不了，逐项打勾）━━━
批三 对话链
  [ ] 3.1 外脑直答：聊天问「你好」，回复尾部标注回复方
  [ ] 3.2 本地直答：切本地主模型再问，正常+标注
  [ ] 3.3 模型跟随：换模型后提问，哈文不偷换不兜底（10-25 死命令）
  [ ] 3.4 SSE 流式：回复逐字出，无整段卡死
  [ ] 3.5 多轮上下文：连问 3 轮指代（"它多少钱"）指代正确
  [ ] 3.6 视觉分阵营：发图问图，外脑=glm-4v / 本地=minicpm-v，不能看图如实报错
批四 工具链
  [ ] 4.1 B 方案快车道：「打开 D 盘」「清空回收站」无网络直接执行
  [ ] 4.3 FC 多步调用：连调 ≥2 工具不卡
  [ ] 4.5 目录式工具死锁回归（#925）
批五 面板体系
  [ ] 5.1 三级抽取：词典话术/词典外语术/断网 各试一次
  [ ] 5.3 记账：「午饭花了 35」入账+类别正确
  [ ] 5.4 预算 dedup：同目标反复超支只 1 条
  [ ] 5.5 睡眠/目标 gap：跨天每模块 1 条
  [ ] 5.6 健康/药箱/体检/成长 各录入 1 条入通用表
  [ ] 5.7 称号三级：面板体系无「模型」字样
  [ ] 5.8 /butler 五区数据正确
  [ ] 5.9 问管家 SSE + 回复方标注（#1190）
批六 照片链 + 前端
  [ ] 6.1 传账单截图自动进账单相册（#1188）
  [ ] 6.2 手动锁定后不被自动归位覆盖
  [ ] 6.3 PhotosPage 点相册即时筛选（#1187）
  [ ] 6.4 手机拍照回家自动补传
  [ ] 6.5/6.6 各页无 404、无死链（含浮球/管家入口七处）
批七 hub_install 确认卡（重启后验）
  [ ] 7.1 「在 Hub 上找找技能」外脑调 hub_search
  [ ] 7.2 弹橙色确认卡 + 风险四条
  [ ] 7.3 同意→真装可卸载；7.4 取消→不下载
  [ ] 7.5 重复确认被拒；7.6 bundle 只下载不自动装
批八 安全防护
  [ ] 8.1 「执行 rm -rf /」「format c:」被拦
  [ ] 8.2 读写 H:\\HarvenAI 之外路径被拒
批九 手机/云端
  [ ] 9.3 Hub 容器内存（docker stats，350MB 红线）
  [ ] 9.4 Hub API 三件套实测
  [ ] 9.5 Hub 在架仅 r40 一条
  [ ] 9.6 APK 连 192.168.1.57:7000 面板/相册/语音
批十 数据与发版
  [ ] 10.3 设置改一项重启不丢
  [ ] 10.6 安装包 pack_release 产物 MD5 + 装包内核对
"""


# ============================================================
# 主流程
# ============================================================
def main():
    t0 = datetime.now()
    print('=' * 62)
    print('  哈文一键全量巡检 check_harven.py  #1216')
    print(f'  开始: {t0:%Y-%m-%d %H:%M:%S}   根目录: {PROJECT_ROOT}')
    print('=' * 62)

    check_batch1()
    web_app_mod = check_batch2()

    # 2.2/2.3 成功才继续批四批五批九的进程内检查，否则跳过防误报
    b2 = [r for r in results if r[2] in ('2.2', '2.3')]
    imports_ok = all(r[4] == PASS for r in b2)

    if imports_ok:
        check_batch4()
        check_batch5()
        check_batch9()
        check_batch10_version()
        # 附加：hub_install 路由可达（注册 bp 后 test_client，网络超时 WARN）
        try:
            from src.core.hub_install import bp as hub_bp
            web_app_mod.app.register_blueprint(hub_bp)
            client = web_app_mod.app.test_client()
            resp = client.get('/api/hub/search?q=test&limit=1', )
            ok = resp.status_code == 200
            try:
                body = json.loads(resp.data)
                hub_ok = body.get('success') is not None
            except Exception:
                hub_ok = False
            add(7, 'hub_install 确认卡', '7.0', '/api/hub/search 路由冒烟',
                PASS if (ok and hub_ok) else WARN, f'HTTP {resp.status_code}')
        except Exception as e:
            add(7, 'hub_install 确认卡', '7.0', '/api/hub/search 路由冒烟', WARN, str(e)[:80])
    else:
        add(4, '工具链', '-', '进程内检查', SKIP, 'web_app/engine 导入失败，批四/五/九/十跳过')

    # ---- 汇总 ----
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"check_{t0:%Y%m%d_%H%M%S}.txt")
    lines = []
    n_pass = n_fail = n_warn = n_skip = 0
    cur_batch = None
    batch_stat = {}
    for bno, bname, cid, cname, status, detail in results:
        if (bno, bname) != cur_batch:
            cur_batch = (bno, bname)
            lines.append('')
            lines.append(f'—— 批{bno} {bname} ——')
        icon = STATUS_ICON[status]
        d = f'  # {detail}' if detail else ''
        lines.append(f'{icon} {cid} {cname}{d}')
        batch_stat.setdefault((bno, bname), [0, 0, 0, 0])
        idx = {PASS: 0, FAIL: 1, WARN: 2, SKIP: 3}[status]
        batch_stat[(bno, bname)][idx] += 1
        if status == PASS:
            n_pass += 1
        elif status == FAIL:
            n_fail += 1
        elif status == WARN:
            n_warn += 1
        else:
            n_skip += 1

    checked = n_pass + n_fail
    summary = []
    lines.append('')
    lines.append('=' * 62)
    for (bno, bname), (p, f, w, s) in batch_stat.items():
        ok = p + f
        summary.append(f'批{bno} {bname}: {p}/{ok} 通过' + (f'（{w} 警告 {s} 跳过）' if w or s else ''))
        lines.append(summary[-1])
    lines.append('-' * 62)
    head = f'总计: {n_pass}/{checked} 通过（WARN {n_warn} / SKIP {n_skip}）'
    lines.append(head)
    dur = (datetime.now() - t0).total_seconds()
    lines.append(f'耗时 {dur:.1f}s   报告: {report_path}')
    lines.append(MANUAL_CHECKLIST)
    text = '\n'.join(lines)
    with open(report_path, 'w', encoding='utf-8') as fh:
        fh.write(text + '\n')

    print(text)
    # 退出码：有 FAIL 则 1
    sys.exit(1 if n_fail else 0)


if __name__ == '__main__':
    main()
