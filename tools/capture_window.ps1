# 截取指定程序的窗口客户区，存成 PNG。（只做截图，像素分析交给 Python）
#   powershell -ExecutionPolicy Bypass -File capture_window.ps1 <进程名> <输出png>

param(
    [Parameter(Mandatory=$true)][string]$ProcName,
    [Parameter(Mandatory=$true)][string]$OutPath
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Cap {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X,Y; }
}
"@

$proc = Get-Process -Name $ProcName -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $proc) { Write-Host "找不到窗口进程: $ProcName" -ForegroundColor Red; exit 1 }
$hwnd = $proc.MainWindowHandle

$cr = New-Object Cap+RECT;  [void][Cap]::GetClientRect($hwnd, [ref]$cr)
$pt = New-Object Cap+POINT; $pt.X = 0; $pt.Y = 0
[void][Cap]::ClientToScreen($hwnd, [ref]$pt)
$cw = $cr.R - $cr.L; $ch = $cr.B - $cr.T

$bmp = New-Object System.Drawing.Bitmap($cw, $ch)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($pt.X, $pt.Y, 0, 0, (New-Object System.Drawing.Size($cw, $ch)))
$g.Dispose()
$bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

Write-Host "客户区 $cw x $ch，屏幕原点 ($($pt.X),$($pt.Y))，已保存 $OutPath"
