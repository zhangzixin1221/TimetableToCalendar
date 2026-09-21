# 截取安装器窗口，量圆角处的像素，判断还有没有暗角
#
#   powershell -ExecutionPolicy Bypass -File check_installer_corners.ps1
#
# 判据：
#   · 按钮圆角的「最外角」像素应该是窗体底色（浅色 #F5F6FA，亮度约 246）
#   · 按钮圆角区域里的最暗像素应该就是按钮填充色（靛蓝 #4F46E5，亮度约 91）
#   · 卡片圆角外侧同理应是窗体底色
#   如果最暗值明显低于这些基准，说明圆角外是未初始化的黑，被抗锯齿混进来了 = 暗角

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X,Y; }
}
"@
[void][Win]::SetProcessDPIAware()

$ws = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $ws "课表转日历-安装程序.exe"
$out = Join-Path $env:TEMP "installer_shot.png"

Write-Host "启动安装器…"
Get-Process | Where-Object { $_.ProcessName -like "*安装程序*" } | Stop-Process -Force -ErrorAction SilentlyContinue
$p = Start-Process $exe -PassThru
Start-Sleep -Seconds 9
$proc = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
if (-not $proc -or $proc.MainWindowHandle -eq 0) { Write-Host "没有窗口" -ForegroundColor Red; exit 1 }

$hwnd = $proc.MainWindowHandle
$wr = New-Object Win+RECT;  [void][Win]::GetWindowRect($hwnd, [ref]$wr)
$cr = New-Object Win+RECT;  [void][Win]::GetClientRect($hwnd, [ref]$cr)
$pt = New-Object Win+POINT; $pt.X = 0; $pt.Y = 0
[void][Win]::ClientToScreen($hwnd, [ref]$pt)

$cw = $cr.R - $cr.L; $ch = $cr.B - $cr.T
Write-Host "窗口 $($wr.R-$wr.L)x$($wr.B-$wr.T)  客户区 $($cw)x$($ch)  客户区屏幕原点 ($($pt.X),$($pt.Y))"

$bmp = New-Object System.Drawing.Bitmap($cw, $ch)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($pt.X, $pt.Y, 0, 0, (New-Object System.Drawing.Size($cw, $ch)))
$g.Dispose()
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "已保存截图: $out"

function Lum($c) { return [math]::Round(0.299*$c.R + 0.587*$c.G + 0.114*$c.B, 1) }

function MinLum($bmp, $x0, $y0, $w, $h) {
    $min = 999; $minAt = ""
    for ($y = $y0; $y -lt $y0 + $h; $y++) {
        for ($x = $x0; $x -lt $x0 + $w; $x++) {
            if ($x -lt 0 -or $y -lt 0 -or $x -ge $bmp.Width -or $y -ge $bmp.Height) { continue }
            $l = Lum $bmp.GetPixel($x, $y)
            if ($l -lt $min) { $min = $l; $minAt = "($x,$y)" }
        }
    }
    return @($min, $minAt)
}

$scale = 2   # 192 DPI
Write-Host ""
Write-Host "=========== 圆角像素检查 ==========="

# 「立即安装」按钮：客户区坐标 (640,672) 尺寸 260x88（uitest 报告值）
$bx = 640; $by = 672
$corner = MinLum $bmp $bx ($by) 28 28
Write-Host ("按钮「立即安装」左上角 28x28：最暗亮度 {0} @ {1}" -f $corner[0], $corner[1])
$bgLum = Lum $bmp.GetPixel(6, $ch/2)
$fillLum = Lum $bmp.GetPixel(($bx + 130), ($by + 44))
Write-Host ("  参照：窗体底色亮度 {0}   按钮填充色亮度 {1}" -f $bgLum, $fillLum)

# 「取消」按钮：客户区坐标 (916,672) 尺寸 160x88
$cx = 916; $cy = 672
$corner2 = MinLum $bmp $cx $cy 28 28
Write-Host ("按钮「取消」左上角 28x28：最暗亮度 {0} @ {1}" -f $corner2[0], $corner2[1])

# 白色卡片：(40,216) 尺寸 1040x320；看它的左上角外侧
$cardCorner = MinLum $bmp 34 210 30 30
Write-Host ("卡片左上角外侧 30x30：最暗亮度 {0} @ {1}" -f $cardCorner[0], $cardCorner[1])

$bmp.Dispose()
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue

Write-Host ""
$worst = [math]::Min([double]$corner[0], [double]$corner2[0])
$limit = [math]::Round($fillLum - 12, 1)
Write-Host ("按钮圆角最暗 {0}，允许下限 {1}（填充色亮度 {2}）" -f $worst, $limit, $fillLum)
if ($worst -ge $limit) {
    Write-Host "判定：通过 - 圆角处没有暗角" -ForegroundColor Green
    exit 0
} else {
    Write-Host "判定：失败 - 圆角处仍偏暗" -ForegroundColor Red
    exit 1
}
