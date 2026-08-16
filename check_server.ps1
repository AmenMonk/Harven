$r = Invoke-WebRequest -Uri 'http://localhost:7000/' -UseBasicParsing
$body = $r.Content

Write-Host "=== 检查服务器返回的 HTML ==="
if ($body -match 'app-settings.js') { Write-Host "✅ 有 app-settings.js" } else { Write-Host "❌ 没有 app-settings.js" }
if ($body -match 'app-monitor.js') { Write-Host "⚠️ 还有 app-monitor.js" } else { Write-Host "✅ 没有 app-monitor.js" }
if ($body -match 'ForcePopulate') { Write-Host "✅ 有 ForcePopulate" } else { Write-Host "❌ 没有 ForcePopulate" }
if ($body -match 'build_id') { Write-Host "✅ 有 build_id" } else { Write-Host "❌ 没有 build_id" }

# 提取所有 script src
Write-Host "`n=== 所有 JS 引用 ==="
$matches = [regex]::Matches($body, '<script src="([^"]+)"')
foreach ($m in $matches) { Write-Host $m.Groups[1].Value }
