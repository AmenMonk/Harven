# Set console window icon to the official Harven duck
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File set_duck_icon.ps1 "path\to\duck.ico"
param([string]$IconPath)

if (-not $IconPath -or -not (Test-Path $IconPath)) { exit }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class DuckIcon {
  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern IntPtr LoadImage(IntPtr hInstance, string lpName, uint uType, int cx, int cy, uint fuLoad);
  [DllImport("user32.dll")]
  public static extern IntPtr GetConsoleWindow();
  [DllImport("user32.dll")]
  public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
}
'@

$hwnd = [DuckIcon]::GetConsoleWindow()
if ($hwnd -eq [IntPtr]::Zero) { exit }

# IMAGE_ICON = 1, LR_LOADFROMFILE = 0x10
$big = [DuckIcon]::LoadImage([IntPtr]::Zero, $IconPath, 1, 32, 32, 0x10)
$small = [DuckIcon]::LoadImage([IntPtr]::Zero, $IconPath, 1, 16, 16, 0x10)

# WM_SETICON = 0x80 ; wParam: 1 = ICON_BIG, 0 = ICON_SMALL
if ($big -ne [IntPtr]::Zero) {
  [DuckIcon]::SendMessage($hwnd, 0x80, [IntPtr]1, $big) | Out-Null
}
if ($small -ne [IntPtr]::Zero) {
  [DuckIcon]::SendMessage($hwnd, 0x80, [IntPtr]0, $small) | Out-Null
}
