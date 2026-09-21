# -*- coding: utf-8 -*-
"""课表 PDF → 手机日历 (.ics)。

模块划分：
    parser    把教务系统的《学生课表》PDF 解析成结构化课程记录
    ics       把课程记录生成 iCalendar 文件（课前提醒 + 每晚汇总）
    presets   作息时间预设
    cli       命令行入口
    gui       tkinter 图形界面
"""

__version__ = "1.0"
