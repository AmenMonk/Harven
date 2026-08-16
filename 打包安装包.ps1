# === Harven AI 安装包打包脚本 ===
# 用法：在 HarvenAI 目录中运行
#   powershell -ExecutionPolicy Bypass -File 打包安装包.ps1

$ErrorActionPreference = "Stop"
$baseDir = $PSScriptRoot
$stagingDir = Join-Path $baseDir "_package_staging"
$zipPath = Join-Path $baseDir "HarvenAI_安装包_$(Get-Date -Format 'yyyyMMdd').zip"

Write-Host "╔════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║     Harven AI — 创建安装包           ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 清理旧的 staging 目录
if (Test-Path $stagingDir) {
    Write-Host "[清理] 删除旧的临时目录..." -ForegroundColor Yellow
    Remove-Item $stagingDir -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingDir | Out-Null

# ===== 文件复制规则 =====
# 要复制的目录/文件
$includeDirs = @(
    "src",
    "templates",
    "static",
    "plugins",
    "skills",
    "locales",
    "docs"
)

$includeRootFiles = @(
    "main.py",
    "web_app.py",
    "device_api.py",
    "model_routes.py",
    "multi_model_api.py",
    "new_features_api.py",
    "plugin_mgmt_api.py",
    "requirements.txt",
    "version.json",
    "README.md",
    "DEPLOY.md",
    "DISCLAIMER.md",
    "NOTICE",
    "LICENSE",
    "LICENSE.md",
    "LICENSES_THIRD_PARTY.md",
    "Dockerfile",
    "docker-compose.yml",
    "docker-entrypoint.sh",
    ".dockerignore",
    ".gitignore",
    "config.yaml.example",
    ".env.example",
    "一键安装启动.bat",
    "启动HarvenAI.bat",
    "启动HarvenAI_公网版.bat",
    "HarvenAI.bat",
    "启动HarvenAI.ps1",
    "打包说明.md",
    "安装指南_本地版.md",
    "HarvenAI_安装详解.md"
)

# data/ 下要保留的文件（排除 .db / .index / 快照 / 锁文件）
$dataInclude = @(
    "multi_models.example.json",
    "fallback_config.json",
    "attributions.json",
    "skills"  # 目录
)

# ===== 复制根目录文件 =====
Write-Host "[1/5] 复制根目录文件..." -ForegroundColor Green
foreach ($f in $includeRootFiles) {
    $src = Join-Path $baseDir $f
    if (Test-Path $src) {
        Copy-Item $src $stagingDir -Force
    }
}

# ===== 复制目录 =====
Write-Host "[2/5] 复制源码目录..." -ForegroundColor Green
foreach ($d in $includeDirs) {
    $src = Join-Path $baseDir $d
    $dst = Join-Path $stagingDir $d
    if (Test-Path $src) {
        Copy-Item $src $dst -Recurse -Force
    }
}

# ===== 复制 data/ 目录（精选）=====
Write-Host "[3/5] 复制 data/ 配置..." -ForegroundColor Green
$dataDst = Join-Path $stagingDir "data"
New-Item -ItemType Directory -Path $dataDst -Force | Out-Null

# 复制 data/ 下的 .json 配置文件（排除运行时数据）
Get-ChildItem (Join-Path $baseDir "data") -File -Filter "*.json" | Where-Object {
    $_.Name -notlike "*.db" -and
    $_.Name -ne "first_run.json" -and
    $_.Name -ne "imported_files.json" -and
    $_.Name -ne "dream_state.json" -and
    $_.Name -ne "device_binding.json"
} | ForEach-Object {
    Copy-Item $_.FullName $dataDst -Force
}

# 复制 data/skills/
$skillsSrc = Join-Path $baseDir "data\skills"
$skillsDst = Join-Path $dataDst "skills"
if (Test-Path $skillsSrc) {
    Copy-Item $skillsSrc $skillsDst -Recurse -Force
}

# 创建 data/ 子目录的 .gitkeep
$dataSubdirs = @("sqlite", "faiss", "knowledge", "snapshots", "isolated_dbs", "conversations", "config")
foreach ($sd in $dataSubdirs) {
    $sdPath = Join-Path $dataDst $sd
    if (-not (Test-Path $sdPath)) {
        New-Item -ItemType Directory -Path $sdPath -Force | Out-Null
    }
    $gitkeep = Join-Path $sdPath ".gitkeep"
    if (-not (Test-Path $gitkeep)) {
        New-Item -ItemType File -Path $gitkeep -Force | Out-Null
    }
}

# 复制 data/faiss/.gitkeep（不复制 .index）
$faissDst = Join-Path $dataDst "faiss"
$gitkeep = Join-Path $faissDst ".gitkeep"
if (-not (Test-Path $gitkeep)) {
    New-Item -ItemType File -Path $gitkeep -Force | Out-Null
}

# ===== 清理 Staging 中的临时文件 =====
Write-Host "[4/5] 清理临时文件..." -ForegroundColor Green

# 删除 __pycache__
Get-ChildItem $stagingDir -Directory -Filter "__pycache__" -Recurse | ForEach-Object {
    Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
}
# 删除 .pyc
Get-ChildItem $stagingDir -File -Filter "*.pyc" -Recurse | ForEach-Object {
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
# 删除 .bak 文件
Get-ChildItem $stagingDir -File -Filter "*.bak" -Recurse | ForEach-Object {
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
# 删除 .broken_* 文件
Get-ChildItem $stagingDir -File | Where-Object { $_.Name -like "*.broken_*" } | ForEach-Object {
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
# 删除 templates/index.html.bak（如果存在）
$bakFile = Join-Path $stagingDir "templates\index.html.bak"
if (Test-Path $bakFile) { Remove-Item $bakFile -Force }

# 删除乱码文件名
Get-ChildItem $stagingDir -File | Where-Object { $_.Name -match "[^a-zA-Z0-9._\-\u4e00-\u9fff]" -and $_.Name -like "*.bat" } | ForEach-Object {
    # 只删除明显乱码的文件
    if ($_.Name -notmatch "^[a-zA-Z0-9\u4e00-\u9fff]") {
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

# ===== 打包成 ZIP =====
Write-Host "[5/5] 创建 ZIP 安装包..." -ForegroundColor Green
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

# 使用 Compress-Archive（PowerShell 5.1+ 内置）
Compress-Archive -Path "$stagingDir\*" -DestinationPath $zipPath -CompressionLevel Optimal

# 清理 staging
Remove-Item $stagingDir -Recurse -Force

$zipSize = (Get-Item $zipPath).Length / 1MB
Write-Host ""
Write-Host "╔════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║          打包完成！                    ║" -ForegroundColor Green
Write-Host "╚════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  文件：$zipPath" -ForegroundColor Yellow
Write-Host "  大小：$([math]::Round($zipSize, 1)) MB" -ForegroundColor Yellow
Write-Host ""
Write-Host "📋 新电脑安装步骤：" -ForegroundColor Cyan
Write-Host "  1. 解压 ZIP 到任意目录（路径不含中文）"
Write-Host "  2. 先安装 Ollama：https://ollama.com/download/windows"
Write-Host "  3. 双击 一键安装启动.bat"
Write-Host "  4. 跟随引导页完成初始化"
Write-Host ""
