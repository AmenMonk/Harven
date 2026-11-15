# -*- coding: utf-8 -*-
r"""
pack_update.py — 哈文增量更新包生成器（#1360，2026-11-02）

用法:
    python pack_update.py <旧版完整包zip>            # 生成更新包到桌面
    python pack_update.py <旧版完整包zip> --list     # 只列差异不出包（dry-run）

原理:
    1. 读旧版完整安装包（用户手里那份）的文件清单+sha1
    2. 按 pack_release 同一套排除规则扫 _package_staging（新版）
    3. 差异三分类: 新增/变更 → 打进更新包; 删除 → delete_list.txt
    4. 附「更新哈文.bat + apply_update.ps1」: 定位安装目录 → 关旧版
       → 备份 data/+config.yaml → 覆盖 → 删废弃文件 → 校验版本
    5. 自检: 重开更新包核对条目数, 输出 MD5/大小

铁律:
    - 排除规则与 pack_release.py 一字不差（同一套户口）
    - python/node_modules/用户数据(models/media/sqlite/downloads) 永不进更新包
      → 老包里这些路径的文件也永不进 delete_list（防误删用户数据/运行时）
    - bat 纯 ASCII + CRLF; ps1 UTF-8 BOM（中文不乱码, 同 upgrade.ps1 惯例）
"""
import os
import sys
import hashlib
import zipfile
import datetime

# 复用 pack_release 的户口（排除规则/路径），保证口径唯一
from pack_release import (STAGING, DESKTOP,
                          EXCLUDE_DIRS_ANY, EXCLUDE_DIRS_IN_DATA,
                          EXCLUDE_EXT, EXCLUDE_FILES, EXCLUDE_PATTERNS)

import fnmatch


def _excluded(rel, in_data=False):
    """rel: '/'分隔相对路径。判断是否该被排除（与 pack_release 扫描口径一致）"""
    parts = rel.split('/')
    for p in parts[:-1]:  # 目录层级
        if p in EXCLUDE_DIRS_ANY:
            return True
    # data/ 下的用户数据大件
    if parts[0] == 'data':
        for p in parts[1:-1]:
            if p in EXCLUDE_DIRS_IN_DATA:
                return True
    fname = parts[-1]
    if fname.endswith(EXCLUDE_EXT) or fname in EXCLUDE_FILES:
        return True
    if any(fnmatch.fnmatch(fname, pat) for pat in EXCLUDE_PATTERNS):
        return True
    return False


def scan_staging():
    """扫 staging，返回 {rel_path: sha1}，rel 用 '/' 分隔"""
    out = {}
    for root, dirs, files in os.walk(STAGING):
        rel_dir = os.path.relpath(root, STAGING).replace('\\', '/')
        in_data = (rel_dir == 'data' or rel_dir.startswith('data/'))
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS_ANY
                   and not (in_data and d in EXCLUDE_DIRS_IN_DATA)]
        for f in files:
            rel = (rel_dir + '/' + f) if rel_dir != '.' else f
            if _excluded(rel, in_data):
                continue
            p = os.path.join(root, f)
            h = hashlib.sha1()
            with open(p, 'rb') as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b''):
                    h.update(chunk)
            out[rel] = h.hexdigest()
    return out


def scan_old_zip(zip_path):
    """读旧完整包，返回 {rel_path: sha1}（同样按户口过滤）"""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = info.filename.replace('\\', '/').lstrip('/')
            if _excluded(rel):
                continue
            h = hashlib.sha1()
            with z.open(info) as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b''):
                    h.update(chunk)
            out[rel] = h.hexdigest()
    return out


def read_version(staging=True, zip_path=None):
    """读版本号+构建标记"""
    import json
    if staging:
        p = os.path.join(STAGING, 'version.json')
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f).get('version', '?')
    with zipfile.ZipFile(zip_path) as z:
        return json.loads(z.read('version.json').decode('utf-8')).get('version', '?')


BAT_TEMPLATE = (
    "@echo off\r\n"
    "rem Harven AI updater launcher (ASCII only, CRLF)\r\n"
    "cd /d \"%~dp0\"\r\n"
    "where powershell >nul 2>nul\r\n"
    "if errorlevel 1 (\r\n"
    "  echo PowerShell not found. Please update Harven AI manually.\r\n"
    "  pause\r\n"
    "  exit /b 1\r\n"
    ")\r\n"
    "powershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0apply_update.ps1\"\r\n"
    "pause\r\n"
)

PS1_TEMPLATE = r"""# Harven AI 增量更新脚本（#1360）· 由「更新哈文.bat」调用，请勿单独改名
# 流程: 定位安装目录 → 关旧版 → 备份 data/+config.yaml → 覆盖更新 → 删废弃文件 → 校验
param(
    [string]$Target = "",
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $MyInvocation.MyCommand.Path

function Info($m) { Write-Host $m -ForegroundColor Cyan }
function OK($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Bad($m)  { Write-Host $m -ForegroundColor Red }

Info ""
Info "==============================================================="
Info "  哈文 Harven AI - 增量更新"
Info "  自动: 关旧版 → 备份数据 → 覆盖新文件 → 校验, 数据不会丢"
Info "==============================================================="

# ---------- [1/6] 定位安装目录 ----------
Info "[1/6] 正在定位哈文安装目录..."
$inst = ""
if ($Target -ne "") {
    if (Test-Path (Join-Path $Target "web_app.py")) { $inst = $Target.Trim() }
    else { Bad ("  指定目录里没有 web_app.py: {0}" -f $Target); exit 1 }
}
if ($inst -eq "" -and (Test-Path (Join-Path $src "web_app.py")) -and (Test-Path (Join-Path $src ".venv"))) {
    $inst = $src
}
if ($inst -eq "") {
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $lnkPath = Join-Path $desktop "Harven AI.lnk"
        if (Test-Path $lnkPath) {
            $ws = New-Object -ComObject WScript.Shell
            $lnk = $ws.CreateShortcut($lnkPath)
            $work = Split-Path -Parent $lnk.WorkingDirectory
            if (Test-Path (Join-Path $work "web_app.py")) { $inst = $work }
        }
    } catch { }
}
if ($inst -eq "") {
    Bad "  没找到哈文安装目录（桌面没有 Harven AI 快捷方式）。"
    Bad "  请把本更新包解压后整体复制到哈文安装目录里再双击「更新哈文.bat」，"
    Bad "  或把安装目录路径拖到本窗口后回车重试。"
    exit 1
}
OK ("  找到: {0}" -f $inst)

# ---------- [2/6] 关闭正在运行的哈文 ----------
Info "[2/6] 正在关闭正在运行的哈文..."
try {
    # 只关本安装目录里的 web_app.py 进程，绝不误伤其他目录的哈文/Python（#1360 红线）
    $procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -match "web_app\.py" -and $_.CommandLine -like ("*" + $inst + "*") }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Info ("  已关闭进程 {0}" -f $p.ProcessId)
    }
    Start-Sleep -Seconds 2
} catch { }
OK "  完成"

# ---------- [3/6] 备份数据 ----------
Info "[3/6] 正在备份你的数据（data 目录 + 配置文件）..."
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = Join-Path $inst ("backup_update_" + $stamp)
New-Item -ItemType Directory -Path $bak -Force | Out-Null
$dataDir = Join-Path $inst "data"
if (Test-Path $dataDir) {
    robocopy $dataDir (Join-Path $bak "data") /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Bad "  data 备份失败（robocopy 退出码 $LASTEXITCODE），已中止，原样未动。"; exit 1 }
}
foreach ($cfg in @("config.yaml")) {
    $cp = Join-Path $inst $cfg
    if (Test-Path $cp) { Copy-Item $cp (Join-Path $bak $cfg) -Force }
}
OK ("  备份位置: {0}" -f $bak)

# ---------- [4/6] 覆盖更新文件 ----------
Info "[4/6] 正在写入新版本文件..."
Expand-Archive -Path (Join-Path $src "update_payload.zip") -DestinationPath $inst -Force
OK "  覆盖完成"

# ---------- [5/6] 清理废弃文件 ----------
Info "[5/6] 正在清理本版废弃的旧文件..."
$dl = Join-Path $src "delete_list.txt"
$n = 0
if (Test-Path $dl) {
    Get-Content $dl -Encoding UTF8 | ForEach-Object {
        $rel = $_.Trim()
        if ($rel -eq "" -or $rel.StartsWith("#")) { return }
        $fp = Join-Path $inst ($rel -replace '/', '\')
        if (Test-Path $fp) { Remove-Item $fp -Force -ErrorAction SilentlyContinue; $n++ }
    }
}
OK ("  清理 {0} 个废弃文件" -f $n)

# ---------- [6/6] 校验 ----------
Info "[6/6] 正在校验新版本..."
$vj = Join-Path $inst "version.json"
$ver = "未知"
try {
    $j = Get-Content $vj -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($j.version) { $ver = [string]$j.version }
} catch { }
OK ("  当前版本: v{0}" -f $ver)
Info ""
OK "==============================================================="
OK ("  更新完成! 数据备份在: {0}" -f $bak)
if (-not $NoStart) {
    Info "  正在启动哈文..."
    $bat = Join-Path $inst "启动HarvenAI.bat"
    if (Test-Path $bat) { Start-Process $bat -WorkingDirectory $inst }
    else { Warn "  没找到启动脚本，请手动双击桌面「Harven AI」启动。" }
}
OK "==============================================================="
"""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry = '--list' in sys.argv
    if not args:
        print('用法: python pack_update.py <旧版完整包zip> [--list]')
        print('示例: python pack_update.py C:/Users/usera/Desktop/HarvenAI_v0.3.1_Windows_20261101_r44.zip')
        sys.exit(1)
    old_zip = os.path.abspath(args[0])
    if not os.path.isfile(old_zip):
        print(f'[FAIL] 旧版包不存在: {old_zip}')
        sys.exit(1)

    print(f'[1/5] 扫描旧版包: {os.path.basename(old_zip)}')
    old = scan_old_zip(old_zip)
    print(f'      旧包有效文件（按户口过滤后）: {len(old)}')

    print('[2/5] 扫描新版 staging...')
    new = scan_staging()
    print(f'      新版有效文件: {len(new)}')

    changed = sorted(rel for rel, h in new.items() if rel not in old or old[rel] != h)
    deleted = sorted(rel for rel in old if rel not in new)
    print(f'[3/5] 差异: 新增/变更 {len(changed)} 个, 删除 {len(deleted)} 个')
    if changed:
        print('  --- 新增/变更 ---')
        for rel in changed[:40]:
            tag = 'NEW' if rel not in old else 'MOD'
            print(f'  [{tag}] {rel}')
        if len(changed) > 40:
            print(f'  ... 其余 {len(changed) - 40} 个略')
    if deleted:
        print('  --- 删除 ---')
        for rel in deleted[:20]:
            print(f'  [DEL] {rel}')
        if len(deleted) > 20:
            print(f'  ... 其余 {len(deleted) - 20} 个略')

    if dry:
        print('[dry-run] 只列差异，不出包。')
        return

    if not changed and not deleted:
        print('[SKIP] 新旧无差异，无需更新包。')
        return

    print('[4/5] 打更新包...')
    import re
    today = datetime.date.today().strftime('%Y%m%d')
    m_old = re.search(r'(r\d+)', os.path.basename(old_zip))
    old_tag = m_old.group(1) if m_old else 'base'
    out_name = f'HarvenAI_更新包_from_{old_tag}_{today}.zip'
    out_path = os.path.join(DESKTOP, out_name)

    # 组装：payload 先落临时文件，再连同更新器三件套打进正式包
    payload_path = out_path + '.payload.tmp'
    with zipfile.ZipFile(payload_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as pz:
        for rel in changed:
            pz.write(os.path.join(STAGING, rel), rel)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr('更新哈文.bat', BAT_TEMPLATE)
        z.writestr('apply_update.ps1', PS1_TEMPLATE.encode('utf-8-sig'))
        dl_lines = ['# 本版废弃文件清单（更新脚本自动删除；此文件之外一律不动）']
        dl_lines += deleted
        z.writestr('delete_list.txt', '\n'.join(dl_lines).encode('utf-8-sig'))
        with open(payload_path, 'rb') as pf:
            z.writestr('update_payload.zip', pf.read())
    os.remove(payload_path)

    # 自检：重开核对
    print('[5/5] 自检...')
    with zipfile.ZipFile(out_path) as z:
        names = set(z.namelist())
        bad = z.testzip()
        with z.open('update_payload.zip') as pf:
            with zipfile.ZipFile(pf) as inner:
                payload_names = inner.namelist()
    assert bad is None, f'zip 损坏: {bad}'
    assert len(payload_names) == len(changed), f'payload 条目数不符 {len(payload_names)} != {len(changed)}'
    for must in ('更新哈文.bat', 'apply_update.ps1', 'delete_list.txt', 'update_payload.zip'):
        assert must in names, f'缺更新器组件: {must}'

    md5 = hashlib.md5(open(out_path, 'rb').read()).hexdigest()
    size_mb = os.path.getsize(out_path) / 1048576
    print('')
    print('=== 出包完成 ===')
    print(f'包名: {out_name}')
    print(f'位置: {out_path}')
    print(f'大小: {size_mb:.2f} MB | MD5: {md5}')
    print(f'内容: 变更 {len(changed)} 文件 + 删除 {len(deleted)} 文件 + 更新器三件套')
    print('用户操作: 解压 → 双击「更新哈文.bat」→ 自动关旧版/备数据/覆盖/重启')


if __name__ == '__main__':
    main()
