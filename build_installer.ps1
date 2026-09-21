# 构建「课表转日历」单文件安装包
#
#   powershell -ExecutionPolicy Bypass -File build_installer.ps1
#
# 产物：dist\TimetableToCalendar-Setup.exe   （双击即安装）
# 文件名和安装目录一律英文；界面文字仍是中文。
#
# 原理：
#   应用本体先用 7-Zip 的 LZMA2 最高压缩（比 PyInstaller 内部用的 zlib 小得多），
#   再把「压缩后的载荷 + 官方 7za.exe 解压内核 + 小卸载器」一起作为资源内嵌进
#   一个用 Windows 自带 csc.exe 编译的 C# 程序里。所以最终只有一个 exe，
#   不需要安装任何第三方安装包制作工具。
#
# 注意：本脚本必须保存为「UTF-8 带 BOM」，否则 Windows PowerShell 5.1 会按 GBK
# 解析中文导致语法错误。

param(
    # 应用本体目录（build_onedir.ps1 的产物）
    [string]$AppDir = "$PSScriptRoot\dist\TimetableToCalendar",
    # 输出文件名（默认和上面的目录放在一起）
    [string]$OutFile = "$PSScriptRoot\dist\TimetableToCalendar-Setup.exe",
    # 打包完把「应用本体目录」和压缩载荷删掉，只留安装包（交付形态只要安装包）
    [switch]$Clean
)

Set-Location $PSScriptRoot
$distDir = Split-Path $OutFile -Parent
if (-not (Test-Path $distDir)) { New-Item -ItemType Directory $distDir -Force | Out-Null }

function Die($msg) { Write-Host ""; Write-Host "错误：$msg" -ForegroundColor Red; exit 1 }

Write-Host "[1/6] 检查输入…"
if (-not (Test-Path (Join-Path $AppDir "TimetableToCalendar.exe"))) {
    Die "找不到 $AppDir\TimetableToCalendar.exe`n请先运行 build_onedir.ps1"
}
$za = Join-Path $PSScriptRoot "7z-extra\x64\7za.exe"
if (-not (Test-Path $za)) { Die "找不到解压内核：$za`n（它来自官方 7-Zip 的 extra 包）" }
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path $csc)) { Die "找不到 C# 编译器 csc.exe" }
Write-Host "    应用本体 : $AppDir"
Write-Host "    解压内核 : $za"
Write-Host "    编译器   : $csc"

Write-Host "[2/6] 准备载荷目录…"
$payload = Join-Path $PSScriptRoot "payload"
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory $payload -Force | Out-Null
Copy-Item (Join-Path $AppDir "*") $payload -Recurse -Force
$rawMB = (Get-ChildItem $payload -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("    载荷 {0:N1} MB，{1} 个文件" -f $rawMB, (Get-ChildItem $payload -Recurse -File).Count)

Write-Host "[3/6] LZMA2 最高压缩…"
$arc = Join-Path $PSScriptRoot "payload.7z"
if (Test-Path $arc) { Remove-Item $arc -Force }
Push-Location $payload
& 7z a -t7z -m0=lzma2 -mx=9 -ms=on -mmt=on $arc "*" | Out-Null
Pop-Location
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $arc)) { Die "压缩失败" }
$arcMB = (Get-Item $arc).Length / 1MB
Write-Host ("    压缩后 {0:N1} MB（压缩率 {1:N2}x）" -f $arcMB, ($rawMB / $arcMB))

Write-Host "[4/6] 编译卸载器…"
$uninst = Join-Path $PSScriptRoot "uninstaller.exe"
if (Test-Path $uninst) { Remove-Item $uninst -Force }
& $csc /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 `
    "/out:$uninst" `
    /r:System.Windows.Forms.dll /r:System.Drawing.dll `
    (Join-Path $PSScriptRoot "installer-src\Uninstaller.cs")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $uninst)) { Die "卸载器编译失败" }
Write-Host ("    卸载器 {0:N1} KB" -f ((Get-Item $uninst).Length / 1KB))

Write-Host "[5/6] 编译安装器（内嵌载荷）…"
if (Test-Path $OutFile) { Remove-Item $OutFile -Force }
& $csc /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 `
    "/out:$OutFile" `
    "/resource:$arc,payload.7z" `
    "/resource:$za,sevenza.exe" `
    "/resource:$uninst,uninstaller.exe" `
    /r:System.Windows.Forms.dll /r:System.Drawing.dll `
    (Join-Path $PSScriptRoot "installer-src\Installer.cs")
if ($LASTEXITCODE -ne 0) { Die "编译失败（csc 退出码 $LASTEXITCODE）" }
if (-not (Test-Path $OutFile)) { Die "没有生成安装程序 exe" }

Write-Host "[6/6] 体积对比"
$instMB = (Get-Item $OutFile).Length / 1MB
Write-Host ""
Write-Host ("  应用本体（未压缩）        {0,10:N1} MB" -f $rawMB)
Write-Host ("  安装包（本产物）          {0,10:N1} MB" -f $instMB)
Write-Host ("  压缩节省                  {0,10:N1} MB" -f ($rawMB - $instMB))
Write-Host ""
Write-Host "完成：$OutFile"

# 交付形态只要安装包：把中间产物清掉。以后再打包，先跑 build_onedir.ps1 重新生成应用本体。
if ($Clean) {
    Write-Host ""
    Write-Host "[清理] 删除中间产物（应用本体 + 压缩载荷）…"
    if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }
    if (Test-Path $arc) { Remove-Item $arc -Force }
    if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
    Write-Host "    只保留：$OutFile"
    Write-Host "    （下次打包先运行 build_onedir.ps1 重建应用本体）"
}

Write-Host "自检：& `"$OutFile`" --uitest ui.txt    /    --pathcheck path.txt"
