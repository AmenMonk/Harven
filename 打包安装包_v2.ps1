# === Harven AI 安装包打包脚本 v2.1 ===
# 改进：不打包.venv（5.3GB太大），改为首次运行时自动安装依赖
$ErrorActionPreference = "Stop"
$baseDir = $PSScriptRoot
$stagingDir = Join-Path $baseDir "_package_staging"
$zipPath = Join-Path $baseDir "HarvenAI_v2_$(Get-Date -Format 'yyyyMMdd').zip"

Write-Host "[Harven AI] v2.1" -ForegroundColor Cyan

# 0. 清理
if (Test-Path $stagingDir) { Remove-Item $stagingDir -Recurse -Force }
New-Item -ItemType Directory -Path $stagingDir | Out-Null

# 1. 复制源码
Write-Host "[1/6] 源码..." -ForegroundColor Green
@("src", "templates", "static", "skills") | ForEach-Object {
    $src = Join-Path $baseDir $_
    if (Test-Path $src) { Copy-Item $src (Join-Path $stagingDir $_) -Recurse -Force }
}

# 2. 根文件
Write-Host "[2/6] 根文件..." -ForegroundColor Green
@("web_app.py", "device_api.py", "model_routes.py", "multi_model_api.py",
  "new_features_api.py", "plugin_mgmt_api.py", "requirements.txt",
  "version.json", "LICENSE", "LICENSE.md", "LICENSES_THIRD_PARTY.md",
  "config.yaml.example") | ForEach-Object {
    $src = Join-Path $baseDir $_
    if (Test-Path $src) { Copy-Item $src $stagingDir -Force }
}

# 3. data/配置
Write-Host "[3/6] data/..." -ForegroundColor Green
$dataDst = Join-Path $stagingDir "data"
New-Item -ItemType Directory -Path $dataDst -Force | Out-Null
Get-ChildItem (Join-Path $baseDir "data") -File -Filter "*.json" | Where-Object {
    $_.Name -notlike "*.db" -and $_.Name -ne "first_run.json" -and
    $_.Name -ne "imported_files.json" -and $_.Name -ne "dream_state.json"
} | ForEach-Object { Copy-Item $_.FullName $dataDst -Force }
@("sqlite", "faiss", "knowledge", "snapshots", "isolated_dbs", "conversations", "config") | ForEach-Object {
    $p = Join-Path $dataDst $_
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    New-Item -ItemType File -Path (Join-Path $p ".gitkeep") -Force | Out-Null
}

# 4. 启动器+安装器+说明
Write-Host "[4/6] 启动器..." -ForegroundColor Green
Copy-Item (Join-Path $baseDir "_launcher_template.bat") (Join-Path $stagingDir "启动哈文.bat") -Force
Copy-Item (Join-Path $baseDir "_installer_template.bat") (Join-Path $stagingDir "首次安装.bat") -Force
Copy-Item (Join-Path $baseDir "_readme_template.txt") (Join-Path $stagingDir "使用说明.txt") -Force
$cfgEx = Join-Path $stagingDir "config.yaml.example"
if (Test-Path $cfgEx) { Copy-Item $cfgEx (Join-Path $stagingDir "config.yaml") -Force }

# 5. 清理
Write-Host "[5/6] 清理..." -ForegroundColor Green
Get-ChildItem $stagingDir -Directory -Filter "__pycache__" -Recurse | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $stagingDir -File -Filter "*.pyc" -Recurse | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem $stagingDir -File -Filter "*.bak" -Recurse | Remove-Item -Force -ErrorAction SilentlyContinue

# 6. ZIP
Write-Host "[6/6] ZIP..." -ForegroundColor Green
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "$stagingDir\*" -DestinationPath $zipPath -CompressionLevel Optimal
Remove-Item $stagingDir -Recurse -Force

$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "完成！ $zipPath ($zipSize MB)" -ForegroundColor Green
