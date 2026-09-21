# A/B 对比：同一个安装器，只差「OnPaint 里有没有清背景」，分别截图量圆角像素。
#
#   powershell -ExecutionPolicy Bypass -File ab_corners.ps1
#
# 流程：
#   1) 把 Installer.cs 里所有 g.Clear(BackColor) 注释掉 -> 构建「未修复版」-> 截图测量
#   2) 恢复源码 -> 构建「修复版」-> 截图测量
#   3) 输出对比结论

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class AB {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X,Y; }
}
"@
[void][AB]::SetProcessDPIAware()

$src = "$PSScriptRoot\src"
$cs  = "$PSScriptRoot\installer-src\Installer.cs"
$bak = "$env:TEMP\Installer.cs.bak"
$exe = Join-Path (Split-Path $PSScriptRoot -Parent) "课表转日历-安装程序.exe"
$fixedShot = "$env:TEMP\ab_fixed.png"
$brokenShot = "$env:TEMP\ab_broken.png"

Copy-Item $cs $bak -Force

function Build($label) {
    Write-Host "  构建 $label …"
    Push-Location $PSScriptRoot
    & powershell -ExecutionPolicy Bypass -File "$PSScriptRoot\build_installer.ps1" *> $null
    Pop-Location
    if (-not (Test-Path $exe)) { throw "构建失败：$label" }
}

function Capture($path) {
    Get-Process | Where-Object { $_.ProcessName -like "*安装程序*" -or $_.ProcessName -like "*课表转日历*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 400
    $p = Start-Process $exe -PassThru
    Start-Sleep -Seconds 9
    $proc = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.MainWindowHandle -eq 0) { throw "窗口没起来" }
    $hwnd = $proc.MainWindowHandle
    $cr = New-Object AB+RECT;  [void][AB]::GetClientRect($hwnd, [ref]$cr)
    $pt = New-Object AB+POINT; $pt.X = 0; $pt.Y = 0
    [void][AB]::ClientToScreen($hwnd, [ref]$pt)
    $cw = $cr.R - $cr.L; $ch = $cr.B - $cr.T
    $bmp = New-Object System.Drawing.Bitmap($cw, $ch)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($pt.X, $pt.Y, 0, 0, (New-Object System.Drawing.Size($cw, $ch)))
    $g.Dispose()
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    Write-Host "    截图 $path  ($cw x $ch)"
}

Write-Host "[1/2] 未修复版（把 g.Clear(BackColor) 全注释掉）"
$text = Get-Content $bak -Raw -Encoding UTF8
$text = $text -replace 'g\.Clear\(BackColor\);', '/*g.Clear(BackColor);*/'
[System.IO.File]::WriteAllText($cs, $text, (New-Object System.Text.UTF8Encoding $false))
Build "未修复版"
Capture $brokenShot

Write-Host "[2/2] 修复版（恢复源码）"
Copy-Item $bak $cs -Force
Build "修复版"
Capture $fixedShot

Write-Host ""
Write-Host "=== 用 Python 量两边的圆角像素 ==="
$env:PYTHONIOENCODING = 'utf-8'
Write-Host "--- 未修复版 ---"
python "$PSScriptRoot\probe_corners.py" $brokenShot
Write-Host "--- 修复版 ---"
python "$PSScriptRoot\probe_corners.py" $fixedShot
