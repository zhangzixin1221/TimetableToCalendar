# 课表 PDF → 手机日历（.ics）

把**西北工业大学教务系统**导出的《学生课表》PDF，转成可以直接导入 iPhone / iPad / Mac /
Android 日历的 `.ics` 文件，并自动加上两类提醒：

1. **每节课提前 N 分钟提醒**（默认 20 分钟）
2. **每晚固定时间汇总第二天要上的所有课**（默认 22:00）

还会按**法定节假日**停课（用 iCalendar 的 `EXDATE` 从循环里精确剔除），并处理**调休**。

> Turn a NWPU (Northwestern Polytechnical University) course-timetable PDF into an
> `.ics` calendar with per-class reminders, a nightly "tomorrow's classes" summary,
> Chinese public-holiday handling, and a one-file Windows installer.

界面是中文的，程序跑起来不需要 Python。

---

## 下载与安装

到 [Releases](../../releases) 下载 **`TimetableToCalendar-Setup.exe`**（约 28 MB），双击安装。

- 默认装到 `D:\Tools\TimetableToCalendar`（没有 D 盘时用
  `%LOCALAPPDATA%\Programs\TimetableToCalendar`）
- 只写 `HKCU`，**不需要管理员权限**
- 程序文件全是英文路径（`TimetableToCalendar.exe` / `Uninstall.exe`），界面文字是中文
- 卸载：开始菜单或「设置 → 应用」，也可以直接跑安装目录里的 `Uninstall.exe`

> 首次运行会弹 SmartScreen「Windows 已保护你的电脑」——因为没有代码签名。
> 点「更多信息」→「仍要运行」即可。

## 怎么用

双击 `TimetableToCalendar.exe`，然后：

1. 「课表 PDF」选你的课表，「输出 .ics」会自动填好
2. **确认「第 1 周周一」的日期**（最关键的一步，见下文）
3. 点「解析课表」
4. 对着预览表核对一遍（有错的直接双击那一行改）
5. 点「生成 .ics」

快捷键：`F5` 解析、`Ctrl+S` 生成、`Ctrl+O` 选择文件。

### 把 .ics 导进 iPhone

1. **邮件**（最省事）：把 `.ics` 当附件发到自己邮箱 → iPhone 打开邮件 → 点附件 →
   「添加到日历」→「全部添加」
2. **iCloud 网页版**：`icloud.com/calendar` 里找导入入口
3. **AirDrop**（Mac）：隔空投送到 iPhone，用「日历」打开
4. **文件 App**：存进 iCloud Drive，iPhone 上点开 → 共享 → 日历

> 建议先新建一个叫「课表」的日历再导入，以后不想要了删掉这一个日历就干净了。

### 命令行

```powershell
TimetableToCalendar.exe --cli 课表.pdf
TimetableToCalendar.exe --cli 课表.pdf -o 我的课表.ics --week1 2026-08-31 --preset 长安校区
TimetableToCalendar.exe --cli 课表.pdf --alarm 15 --night 21:30   # 提前 15 分钟 + 每晚 21:30 汇总
TimetableToCalendar.exe --cli 课表.pdf --alarm 0 --night off       # 完全不要提醒
TimetableToCalendar.exe --cli 课表.pdf --merge artifact            # 相连节次合并策略
TimetableToCalendar.exe --cli 课表.pdf --remark-courses            # 备注课程也加为全天日程
```

`--cli -h` 可以看到全部参数。从源码跑就把 `TimetableToCalendar.exe` 换成
`python main.py`。

---

## 必须自己确认的几个点

程序只能从 PDF 里读到「第几周」，**读不到绝对日期**，也读不到上下课时间。所以：

### 1. 第 1 周周一是哪天（最重要）

填错的话所有日程都会整体偏移。判断依据：

- 看学校校历上「X 月 X 日 20XX 级本科生开课」那一行，那天就是第 1 周周一
- 交叉验证：如果校历写着 18 教学周 + 考试周，用「第 1 周周一 + 17 周」应该正好落在
  考试周前一周的周日

程序会检查你填的日期是不是周一，不是的话会提示。

### 2. 作息时间

界面里可以选预设：

| 预设 | 说明 |
|---|---|
| 西北工业大学 · 长安校区 | 1 节 08:30-09:15 … 13 节 20:40-21:25（每节 45 分钟） |
| 西北工业大学 · 友谊校区（冬季 10.1-4.30） | 每节 50 分钟，只有 1~12 节 |
| 西北工业大学 · 友谊校区（夏季 5.1-9.30） | 同上，下午与晚上整体后移 |

对不上就点「编辑节次时间…」手动改。如果某节课用到的节次没设时间，程序会明确报错
告诉你缺哪一节，而不是悄悄算错。

### 3. 相连节次要不要合并

教务系统排版时会把一门占用「11-13 节」的课拆成「11-12 节」和「13-13 节」两行。
界面里三档可选：

| 档位 | 效果 |
|---|---|
| **相连就合并**（默认） | 只要同一天、同一门课、同一教室且节次相连就合成一条。例如「1-2节 + 3-4节」= 08:30-12:10 |
| **只合并排版碎片** | 只合并「11-12节 + 13-13节」这种（其中一段是单节，明显是拆出来的），「1-2节 + 3-4节」视为两次课 |
| **不合并** | 完全按 PDF 的行 |

同一份课表，三档分别得到 18 / 22 / 26 个日程——**上课内容完全一样，只是日程条目数不同**。

### 4. 法定节假日与调休

界面里默认勾选「按法定节假日安排停课」。内置预设是国务院《关于 2026 年部分节假日安排
的通知》里与 2026 秋季学期相关的部分（中秋节 09-25~27、国庆节 10-01~07 停课；
09-20、10-10 调休）。换成别的学期就点「载入节假日文件…」，或者直接改
`src/timetable/holidays.py` 里的预设。

调休日为什么通常不需要补课：高校通行做法是调休日照常执行「当天星期几」的课表，而不是去补
别的星期的课。如果你学校是要「补某一天的课」，把 `makeup_days` 里的条目写成带
`follow_weekday`（0=周一 … 6=周日），程序会自动在那天补上对应星期的课。

放假不是把日程删掉，而是用 `EXDATE` 从循环里精确剔除那几天——日程数不变，但那几天不会
再有课、也不会再提醒。另外会在放假前一天晚上多发一条「明天放假」的提醒，并把当晚原定
要上的课列出来（标注已取消），避免那天晚上突然收不到提醒让人以为程序坏了。

---

## 解析结果一定要扫一眼

PDF 里的文字是「版面还原」出来的，绝大多数情况没问题，但有一条最容易出偏差：

- **教室和教师是靠空格分隔的**。如果某一行的教室名把教师「吃掉」了（例如教室显示成
  「实验大楼C座5楼某某某」），双击那一行改一下就行。

程序内置两个自检，解析完直接打在日志里：

- **时间冲突检测**：同一天同一时段不应该有两门不同的课。真实课表必然无冲突，
  所以一旦报冲突，几乎一定是解析串行了
- **解析失败条目**：某门课没解析出周次/节次时会明确列出来，不会静默丢掉

---

## 每个学期要手工更新两件事

| 要改的 | 在哪改 | 怎么确认 |
|---|---|---|
| **第 1 周周一** | 界面顶部「第 1 周周一」/ `--week1` | 看校历上「X月X日 20XX级本科生开课」那一行 |
| **本学期节假日安排** | 界面「载入节假日文件…」/ `--holidays-file` | 看当年国务院通知 + 学校教务处调课通知 |

节假日文件是纯文本，格式很直白（界面里有「导出模板…」按钮可以直接生成）：

```
放假 2027-04-04 2027-04-06 清明节
放假 2027-05-01 2027-05-05 劳动节
调休 2027-04-25
```

省略「执行星期几」= 那天照常执行当天星期几的课表；写成 `调休 2027-04-25 周一`
就是那天补周一的课。

### 程序会主动拦住「忘记更新」

内置的配置自检（`src/timetable/doctor.py`）专抓这类**不会报错、只会算错**的情况：

- 第 1 周周一不是周一 → 报错
- **填的日期推出来的学期和 PDF 里写的学期对不上** → 报错（专治「下学期忘了改日期」）
- **节假日预设全部落在本学期范围之外** → 报错（专治「沿用了上学期的节假日」）
- 课程用到的节次没设时间 → 报错

报错时命令行会中止（退出码 3，加 `--force` 可强制生成），界面上会弹窗让你决定。

---

## 适用范围与已知限制

- **只认西工大教务系统的《学生课表》版式**（七列表头 + 左侧节次栏 + 「(N周)(N-M节) 教室 教师」）。
  换学校 / 换教务系统就不认，程序会**明确报错退出**（「没有找到星期一…星期日表头」
  或「没有解析到任何课程」），不会硬猜出一个错误结果。
- **单双周**：PDF 里的「2~4(双)周」会被正确展开成第 2、4 周。程序不是一个无限循环加例外，
  而是按连续区间拆成若干条日程，保证每条都能精确表达。
- **相连节次合并后是一条长日程**（例如周一 08:30-12:10），中间不会留出课间空档。
- **不联网**，节假日安排要自己按当年国务院通知填写，不会自动获取。
- **教室和教师靠空格分隔识别**，个别行可能识别错，用预览表改一次即可。

---

## 从源码运行

```powershell
pip install -r src\requirements.txt
python src\main.py                 # 图形界面
python src\main.py --cli -h        # 命令行
```

## 构建安装包

需要 Python 3 + PyInstaller，以及 Windows 自带的 `csc.exe`（.NET Framework 4）。
不依赖 Inno Setup / NSIS 之类的打包工具。

```powershell
pip install -r src\requirements.txt pyinstaller
powershell -ExecutionPolicy Bypass -File build_onedir.ps1             # → dist\TimetableToCalendar\
powershell -ExecutionPolicy Bypass -File build_installer.ps1 -Clean   # → dist\TimetableToCalendar-Setup.exe
```

原理：应用本体（PyInstaller onedir 目录）先用 **7-Zip 的 LZMA2** 压到约 1/3，再把
「压缩载荷 + 官方 `7za.exe` 解压内核 + 一个 8 KB 的小卸载器」内嵌进一个用 `csc.exe`
编译的 C# WinForms 程序里，所以最终只有一个 exe。

> `-Clean` 会在打包后删掉中间产物（应用本体目录和压缩载荷），只留安装包。

## 自检与测试

```powershell
pip install -r src\requirements-dev.txt

python src\selftest.py  <你的课表.pdf>    # 端到端：解析 → 生成 → 用第三方库独立展开核对
python src\smoke_test.py <你的课表.pdf>   # 界面烟雾测试（驱动界面逻辑走完整流程）
python tools\resize_test.py               # 窗口缩放回归（含极小窗口）
python tools\resize_wm_test.py            # 窗口缩放回归（走窗口管理器，模拟拖边框）
python tools\table_fit_test.py <课表.pdf>  # 预览表列宽自适应（左右缩放不许裁掉右边的列）
python tools\check_corners.py             # 圆角控件边缘有没有暗角

# 安装包自检（不需要真的装一遍）
dist\TimetableToCalendar-Setup.exe --uitest ui.txt      # 控件清单 / 越界 / 重叠
dist\TimetableToCalendar-Setup.exe --pathcheck path.txt # 安装位置规范化规则（含幂等）
```

`selftest.py` 会做这几件事：

- 解析后检查「同一天同一时段是否出现两门不同的课」
- 对三种合并模式各生成一份 `.ics`，检查 CRLF、无超过 75 字节的行、标签配对
- **用 `recurring-ical-events` 独立展开所有循环日程**，把每晚那条汇总的内容与第二天
  实际展开出来的课程**逐条比对**（防止「提醒内容和实际课程对不上」）
- 检查同一天展开出的课程互不重叠
- 检查法定节假日当天确实没有课，且每个假期都有一条「明天放假」提醒
- 换学期场景：故意填错日期 / 沿用旧节假日，确认自检能报出对应错误

打包好的 exe 也能自检：

```powershell
TimetableToCalendar.exe --selftest 你的课表.pdf out.ics report.txt
```

报告里会写明 DPI、Pillow 圆角渲染、默认窗口尺寸、滚动条状态 —— 这些是程序化确认界面
真的按预期工作（Pillow 没打进包时会静默退回直角，必须靠这个发现）。

---

## 目录结构

```
├── src/                       应用源码
│   ├── main.py                入口（无参数=界面，--cli=命令行，--selftest=自检，--shot=截图）
│   ├── timetable/
│   │   ├── parser.py          PDF → 结构化课程记录
│   │   ├── ics.py             课程记录 → .ics（两套提醒、连排合并、节假日 EXDATE）
│   │   ├── holidays.py        法定节假日 / 调休预设 + 外部节假日文件解析
│   │   ├── doctor.py          配置自检（专抓「换学期忘改参数」）
│   │   ├── presets.py         作息时间预设
│   │   ├── ui_kit.py          界面组件库（圆角控件 + 配色 + DPI 适配）
│   │   ├── gui.py             tkinter 界面
│   │   └── cli.py             命令行
│   ├── selftest.py            端到端自检
│   └── smoke_test.py          界面烟雾测试
├── installer-src/             安装器源码（C#，用系统自带 csc.exe 编译）
│   ├── Installer.cs
│   └── Uninstaller.cs
├── tools/                     开发用的回归脚本（缩放 / 圆角 / 截图分析）
├── assets/                    随包附带的用户说明（构建时复制进产物）
├── 7z-extra/x64/7za.exe       官方 7-Zip 独立解压内核（构建安装包时需要）
├── build_onedir.ps1           构建应用本体
├── build_installer.ps1        打包成单文件安装包
└── docs/
    ├── 使用说明.md            安装包说明（面向使用者）
    └── 开发说明.md            开发笔记与踩过的坑
```

## 常见问题

**没有代码签名，SmartScreen 报警怎么办？**
正常现象。点「更多信息」→「仍要运行」。要对公分发需要买代码签名证书。

**安装位置能改吗？**
能。安装器会**自动在你选的位置下新建 `TimetableToCalendar` 子目录**
（选到 `D:\Tools` 就装到 `D:\Tools\TimetableToCalendar`），所以只选父目录就行；
卸载时如果那个父目录是安装时新建的、而且空了，会一并删掉，不留空文件夹。

**程序窗口能调整大小吗？**
能，横竖都能拖。默认约占屏幕四分之一；拉高时课程预览表跟着变高，压矮到装不下时
中间内容区出现滚动条，顶栏和底部按钮始终固定在窗口上。左右缩放时预览表的列宽会自动
压缩到各列下限，保证右边几列不会被裁掉。

**卸载后还有残留吗？**
没有。卸载器会把自己复制到 `%TEMP%` 再执行删除，所以安装目录不被任何进程占用，
目录能整个删掉。如果程序正在运行，卸载器会提示你先关掉它。

**换学期怎么用？**
只需改两处：第 1 周周一、本学期节假日。其余（周次换算、循环日程、提醒、合并）都与学期
无关。见上文「每个学期要手工更新两件事」。

## 许可

MIT，见 [LICENSE](LICENSE)。

`7z-extra/x64/7za.exe` 来自 [7-Zip](https://www.7-zip.org/) 官方 extra 包，
按其许可（LGPL + unRAR 限制）分发，仅用于解压安装载荷。
