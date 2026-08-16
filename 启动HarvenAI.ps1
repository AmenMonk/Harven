# Harven AI 启动脚本 (PowerShell)
# 用法：右键 → 使用 PowerShell 运行，或双击（需允许脚本执行）

$ErrorActionPreference = "Stop"
$PROJECT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$LOG_FILE = Join-Path $PROJECT_DIR "start_log.txt"

function Write-Log($msg) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp  $msg" | Tee-Object -FilePath $LOG_FILE -Append
}

Write-Log "[START] Harven AI 启动中..."

# ── Python 检测 ──────────────────────────────────
$PYTHON_EXE = $null

# 1. WorkBuddy 内置 Python（最优先）
$WB_PYTHON = "$env:USERPROFILE\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if (Test-Path $WB_PYTHON) {
    $PYTHON_EXE = $WB_PYTHON
}

# 2. PATH 中的 python（排除 WindowsApps）
if (-not $PYTHON_EXE) {
    $candidates = where.exe python 2>$null | Where-Object { $_ -notlike "*WindowsApps*" }
    if ($candidates) { $PYTHON_EXE = $candidates[0] }
}

# 3. 常见安装路径
if (-not $PYTHON_EXE) {
    $commonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe"
    )
    foreach ($p in $commonPaths) {
        if (Test-Path $p) { $PYTHON_EXE = $p; break }
    }
}

if (-not $PYTHON_EXE) {
    Write-Host "[ERROR] 未找到 Python，请先安装 Python 3.11+" -ForegroundColor Red
    Write-Host "下载: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Log "[ERROR] Python not found"
    pause
    exit 1
}

Write-Host "[OK] Python: $PYTHON_EXE" -ForegroundColor Green
Write-Log "[OK] Python: $PYTHON_EXE"

# ── 依赖检查 ────────────────────────────────────
Write-Host "[SETUP] 检查依赖..." -ForegroundColor Cyan
& $PYTHON_EXE -c "import flask" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[SETUP] 安装依赖中..." -ForegroundColor Yellow
    & $PYTHON_EXE -m pip install -r (Join-Path $PROJECT_DIR "requirements.txt") --quiet
}

# ── 启动 ────────────────────────────────────────
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
if (-not $env:OLLAMA_HOST) { $env:OLLAMA_HOST = "localhost" }
if (-not $env:OLLAMA_PORT) { $env:OLLAMA_PORT = "11434" }

Set-Location $PROJECT_DIR
Write-Host ""
Write-Host "===== Harven AI 启动中 =====" -ForegroundColor Cyan
Write-Host "日志文件: $LOG_FILE"
Write-Host ""

Write-Log "[START] 启动 web_app.py..."

# 启动 Python（Ctrl+C 可停止）
& $PYTHON_EXE -u (Join-Path $PROJECT_DIR "web_app.py") 2>&1 | Tee-Object -FilePath $LOG_FILE -Append

Write-Host ""
Write-Log "[EXIT] 退出码=$LASTEXITCODE"
pause
