# 构建「课表转日历」免安装版
#
#   powershell -ExecutionPolicy Bypass -File build_onedir.ps1
#
# 产物：上一级目录下的 TimetableToCalendar\  （双击里面的 TimetableToCalendar.exe 即可运行）
#
# 说明：这是 PyInstaller 的 onedir 形态 —— 一个文件夹装全部依赖。
# 相比单文件 exe，启动快（不用每次解压到临时目录），也方便查看内部文件。
#
# 落盘的文件名一律英文（中文路径在工具链、备份、云同步里容易出问题），
# 界面文字仍然是中文。
#
# 注意：本脚本必须保存为「UTF-8 带 BOM」，否则 Windows PowerShell 5.1 会按 GBK
# 解析中文导致语法错误。

param(
    [string]$SrcDir = "$PSScriptRoot\src",
    # 输出目录：默认放在本目录下的 dist\（构建产物，不纳入版本管理）
    [string]$OutDir = "$PSScriptRoot\dist"
)

Set-Location $PSScriptRoot
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory $OutDir -Force | Out-Null }
Write-Host "源码目录: $SrcDir"
Write-Host "输出目录: $OutDir"

if (-not (Test-Path "$SrcDir\main.py")) {
    Write-Host "找不到 $SrcDir\main.py" -ForegroundColor Red
    exit 1
}

python -m PyInstaller --noconfirm --onedir --windowed --clean `
  --name TimetableToCalendar `
  --exclude-module icalendar --exclude-module recurring_ical_events `
  --exclude-module matplotlib --exclude-module numpy `
  --distpath $OutDir --workpath "$PSScriptRoot\build" --specpath $PSScriptRoot `
  "$SrcDir\main.py"

if ($LASTEXITCODE -ne 0) { Write-Host "构建失败" -ForegroundColor Red; exit 1 }
Remove-Item "$PSScriptRoot\build" -Recurse -Force -ErrorAction SilentlyContinue

$app = Join-Path $OutDir "TimetableToCalendar"
if (-not (Test-Path "$app\TimetableToCalendar.exe")) {
    Write-Host "没有生成 exe" -ForegroundColor Red
    exit 1
}

# 随包附带的东西必须由脚本负责放进去。
# 注意：PyInstaller 的 --clean 会把整个输出目录重建，手工放进去的文件会被清掉，
# 所以 README / 示例课表统一放在 assets\ 里，每次构建后复制过去。
Write-Host "[附带文件]"
$assets = Join-Path $PSScriptRoot "assets"
if (Test-Path "$assets\README.md") {
    Copy-Item "$assets\README.md" "$app\README.md" -Force
    Write-Host "  README.md"
}
$sample = $null
foreach ($cand in @("$PSScriptRoot\sample-timetable.pdf",
                    "$assets\sample-timetable.pdf",
                    (Join-Path (Split-Path $PSScriptRoot -Parent) "课表.pdf"))) {
    if (Test-Path $cand) { $sample = $cand; break }
}
if ($sample) {
    Copy-Item $sample "$app\sample-timetable.pdf" -Force
    Write-Host "  sample-timetable.pdf（来自 $sample）"
} else {
    Write-Host "  （没找到示例课表，跳过；把你的课表 PDF 命名为 sample-timetable.pdf 放在项目根目录即可带上）"
}

foreach ($need in @("TimetableToCalendar.exe", "README.md")) {
    if (-not (Test-Path (Join-Path $app $need))) {
        Write-Host "警告：$app 里缺少 $need" -ForegroundColor Yellow
    }
}

$mb = [math]::Round((Get-ChildItem $app -Recurse -File |
                     Measure-Object Length -Sum).Sum / 1MB, 1)
$n = (Get-ChildItem $app -Recurse -File).Count
Write-Host ""
Write-Host "完成：$app\TimetableToCalendar.exe   （整目录 $mb MB / $n 个文件）"
