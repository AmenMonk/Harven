# Harven AI 一键升级脚本（#825）· 由「一键升级.bat」调用，请勿单独改名
# 用法：双击 一键升级.bat；测试用参数 -TestMode -Target <目录> -NoStart
param(
    [string]$Target = "",
    [switch]$TestMode,
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $MyInvocation.MyCommand.Path

function Info($m) { Write-Host $m -ForegroundColor Cyan }
function OK($m)   { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Bad($m)  { Write-Host $m -ForegroundColor Red }

function Get-VerText($dir) {
    try {
        $vj = Join-Path $dir "version.json"
        if (Test-Path $vj) {
            $j = Get-Content $vj -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($j.version) { return [string]$j.version }
        }
        $vf = Join-Path $dir "VERSION"
        if (Test-Path $vf) { return ((Get-Content $vf -Raw) -replace "\s", "") }
    } catch { }
    return "未知"
}

Info ""
Info "==============================================================="
Info "  哈文 Harven AI - 一键升级"
Info "  自动：关旧版 → 备份 → 覆盖 → 补依赖，全程不用手动操作"
Info "==============================================================="

# ---------- [1/6] 定位安装目录 ----------
Info "[1/6] 正在定位哈文安装目录..."
$inst = ""
if ($Target -ne "") {
    if (Test-Path (Join-Path $Target "web_app.py")) { $inst = $Target.Trim() }
    else { Bad ("  指定目录里没有 web_app.py：{0}" -f $Target); exit 1 }
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
            if ($lnk.TargetPath) {
                $d = Split-Path -Parent $lnk.TargetPath
                if ($d -and (Test-Path (Join-Path $d "web_app.py"))) { $inst = $d }
            }
        }
    } catch { }
}
if ($inst -eq "") {
    $cands = @("H:\HarvenAI", "C:\HarvenAI",
        (Join-Path $env:USERPROFILE "HarvenAI"),
        (Join-Path ([Environment]::GetFolderPath('Desktop')) "HarvenAI"))
    foreach ($c in $cands) {
        if ($c -and (Test-Path (Join-Path $c "web_app.py"))) { $inst = $c; break }
    }
}
if ($inst -eq "") {
    Warn "  没能自动找到哈文安装目录。"
    $inp = Read-Host "  请粘贴哈文安装目录完整路径（例如 H:\HarvenAI）后回车"
    if ($inp -and (Test-Path (Join-Path $inp.Trim() "web_app.py"))) { $inst = $inp.Trim() }
    else { Bad "  该目录下没有 web_app.py，无法升级。"; exit 1 }
}
OK ("  安装目录：{0}" -f $inst)

$oldV = Get-VerText $inst
$newV = Get-VerText $src
Info ("  版本：{0}  →  {1}" -f $oldV, $newV)

if ($TestMode) { OK "[测试模式] 探测完成，未做任何改动。"; exit 0 }

# ---------- [2/6] 停止运行中的哈文 ----------
Info "[2/6] 正在停止运行中的哈文..."
$stopped = 0; $failed = 0
try {
    Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.CommandLine -and ($_.CommandLine -like ("*{0}*" -f $inst)) -and ($_.Name -match '^python')
    } | ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $stopped++ } catch { $failed++ }
    }
} catch { }
try {
    $conns = Get-NetTCPConnection -LocalPort 7000 -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($p -and $p.Path -and ($p.Path -like ("*{0}*" -f $inst))) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction Stop; $stopped++ } catch { $failed++ }
        }
    }
} catch { }
Start-Sleep -Milliseconds 800
if ($failed -gt 0) {
    Warn ("  有 {0} 个进程停不掉（可能是管理员权限启动的），请手动关掉哈文窗口后重跑本脚本" -f $failed)
    exit 1
}
OK ("  已停止 {0} 个哈文进程（0 = 本来就没在运行）" -f $stopped)

# ---------- [3/6] 备份 ----------
Info "[3/6] 备份旧版代码（不含数据和模型，很快）..."
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path $inst ("_backup_" + $ts)
& robocopy $inst $backup /E /XD ".venv" "venv" "data" "logs" "__pycache__" ".git" "_backup_*" /XF "*.db" "*.log" /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { Bad ("  备份失败（代码 {0}），已中止，原文件没动。" -f $LASTEXITCODE); exit 1 }
OK ("  备份位置：{0}" -f $backup)

# ---------- [4/6] 写入新文件 ----------
Info "[4/6] 写入新版本文件..."
& robocopy $src $inst /E /XD ".venv" "venv" "python" "node_modules" "__pycache__" ".git" "logs" "_backup_*" /XF "*.log" /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) {
    Bad ("  写入失败（代码 {0}）。回退方法：把备份文件夹 {1} 里的内容复制回 {2}" -f $LASTEXITCODE, $backup, $inst)
    exit 1
}
OK "  新文件已写入（你的数据、知识库、面板记录、配对信息全部原样保留）"

# ---------- [5/6] 依赖更新 ----------
Info "[5/6] 检查依赖更新..."
try {
    $venvPy = Join-Path $inst ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) {
        $marker = Join-Path $inst ".venv\.harven_deps_installed"
        $req = Join-Path $inst "requirements.txt"
        $need = $false
        if (-not (Test-Path $marker)) { $need = $true }
        elseif ((Get-Item $req).LastWriteTime -gt (Get-Item $marker).LastWriteTime) { $need = $true }
        if ($need) {
            Warn "  依赖有更新，正在安装（可能几分钟，请别关窗口）..."
            try { & $venvPy -m pip install --quiet --disable-pip-version-check --upgrade pip 2>$null | Out-Null } catch { }
            & $venvPy -m pip install --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple -r $req
            if ($LASTEXITCODE -ne 0) {
                Warn "  清华源失败，换官方源重试..."
                & $venvPy -m pip install --disable-pip-version-check -r $req
            }
            if ($LASTEXITCODE -ne 0) {
                Warn "  个别依赖没装上，哈文仍能启动；个别新功能可能不可用（可稍后再跑一次本脚本重试）"
            } else {
                Set-Content -Path $marker -Value "installed"
                OK "  依赖已更新到最新"
            }
        } else {
            OK "  依赖无需更新"
        }
    } else {
        Warn "  没找到 .venv 虚拟环境。"
        $bundledPy = Join-Path $src "python\python.exe"
        if (Test-Path $bundledPy) {
            Warn "  检测到内置 Python，正在全新初始化（首次约 3-10 分钟）..."
            & $bundledPy -m venv (Join-Path $inst ".venv")
            & (Join-Path $inst ".venv\Scripts\python.exe") -m pip install --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple -r (Join-Path $inst "requirements.txt")
            if ($LASTEXITCODE -eq 0) {
                Set-Content -Path (Join-Path $inst ".venv\.harven_deps_installed") -Value "installed"
                OK "  全新环境已就绪"
            } else { Warn "  依赖安装有失败项，可重跑本脚本重试" }
        } else {
            Warn "  这是升级包（不带 Python）。全新安装请下载完整安装包后双击「安装哈文.bat」"
        }
    }
} catch {
    Warn ("  依赖环节出错（不影响文件升级）：{0}" -f $_.Exception.Message)
    Warn "  哈文文件已是新版；依赖可稍后重跑本脚本补装"
}

# ---------- [6/6] 收尾：配置兜底 + 快捷方式 + 启动 ----------
Info "[6/6] 收尾..."
if (-not (Test-Path (Join-Path $inst "config.yaml"))) {
    if (Test-Path (Join-Path $inst "config.example.yaml")) {
        Copy-Item (Join-Path $inst "config.example.yaml") (Join-Path $inst "config.yaml")
        OK "  config.yaml 已初始化"
    } elseif (Test-Path (Join-Path $inst "config.yaml.example")) {
        Copy-Item (Join-Path $inst "config.yaml.example") (Join-Path $inst "config.yaml")
        OK "  config.yaml 已初始化"
    }
}
if (-not (Test-Path (Join-Path $inst "data\sqlite"))) {
    New-Item -ItemType Directory -Path (Join-Path $inst "data\sqlite") -Force | Out-Null
}
try {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $lnkPath = Join-Path $desktop "Harven AI.lnk"
    if (-not (Test-Path $lnkPath)) {
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnkPath)
        $sc.TargetPath = Join-Path $inst "启动HarvenAI.bat"
        $sc.WorkingDirectory = $inst
        $sc.WindowStyle = 7
        $sc.Description = "Harven AI 哈文 - 本地AI助手"
        $icon = Join-Path $inst "static\icons\duck.ico"
        if (-not (Test-Path $icon)) { $icon = Join-Path $inst "static\icon-duck.png" }
        if (Test-Path $icon) { $sc.IconLocation = "{0},0" -f $icon }
        $sc.Save()
        OK "  桌面快捷方式已创建"
    }
} catch { }
OK ""
OK "==============================================================="
OK ("  升级完成！{0} → {1}" -f $oldV, $newV)
OK ("  出问题可回退：把 {0} 内容复制回安装目录" -f $backup)
OK "==============================================================="
if (-not $NoStart) {
    $ans = Read-Host "现在启动哈文吗？(Y/N，回车默认 Y)"
    if ($ans -ne "N" -and $ans -ne "n") {
        Start-Process -FilePath (Join-Path $inst "启动HarvenAI.bat") -WorkingDirectory $inst
        OK "哈文正在启动，浏览器访问 http://localhost:7000"
    }
}
exit 0
