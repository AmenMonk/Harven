"""Harven 哈文AI 桌面快捷方式 — 最终版"""
import os, glob, subprocess

USERPROFILE = os.environ['USERPROFILE']
DESKTOP = os.path.join(USERPROFILE, 'Desktop')
TARGET = r'H:\HarvenAI\启动HarvenAI.bat'
WORKDIR = r'H:\HarvenAI'
ICON = r'H:\HarvenAI\static\icons\duck.ico'

# 删除所有旧的 Harven 相关快捷方式和 bat
for old in glob.glob(os.path.join(DESKTOP, 'Harven*')):
    if old.endswith('.lnk') or old.endswith('.bat'):
        if 'Harven 哈文AI.lnk' == os.path.basename(old):
            continue  # keep current
        os.remove(old)
        print(f'  已删除: {os.path.basename(old)}')

# 用 Unicode 转义避免编码问题
ps_code = '''
$desktop = [Environment]::GetFolderPath("Desktop")
$tmp = Join-Path $desktop "Harven_tmp.lnk"
$sh = New-Object -ComObject WScript.Shell
$sc = $sh.CreateShortcut($tmp)
$sc.TargetPath = "H:\\HarvenAI\\启动HarvenAI.bat"
$sc.WorkingDirectory = "H:\\HarvenAI"
$sc.IconLocation = "H:\\HarvenAI\\static\\icons\\duck.ico,0"
$sc.Description = "Harven AI - Local Private AI Assistant"
$sc.Save()
$newname = "Harven " + [char]0x54C8 + [char]0x6587 + "AI.lnk"
$final = Join-Path $desktop $newname
if (Test-Path $final) { Remove-Item $final -Force }
Rename-Item $tmp $final
Write-Output "DONE"
'''

ps_file = os.path.join(os.path.dirname(__file__), '_sc.ps1')
with open(ps_file, 'w', encoding='utf-8-sig') as f:
    f.write(ps_code)

result = subprocess.run(['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps_file],
                       capture_output=True, text=True, timeout=15)
print(result.stdout.strip())
os.remove(ps_file)

# 验证
matches = glob.glob(os.path.join(DESKTOP, 'Harven*AI.lnk'))
print(f'\n桌面快捷方式:')
for m in sorted(matches):
    size = os.path.getsize(m)
    print(f'  ✅ {os.path.basename(m)} ({size} bytes)')

print(f'\n图标文件: {ICON}')
if os.path.exists(ICON):
    print(f'  ✅ {os.path.getsize(ICON)} bytes')
else:
    print(f'  ❌ 不存在')
