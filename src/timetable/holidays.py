# -*- coding: utf-8 -*-
"""法定节假日与调休安排预设。

数据来源：国务院办公厅《关于 2026 年部分节假日安排的通知》
（2025 年 11 月 4 日，https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm）

2026 年与 2026-2027 秋季学期（2026-08-31 ~ 2027-01-17）相关的只有两个：

    中秋节  9月25日（周五）至 27日（周日）放假，共 3 天。（无调休上班日）
    国庆节  10月1日（周四）至 7日（周三）放假调休，共 7 天。
            9月20日（周日）、10月10日（周六）上班。

调休上班日怎么算：按高校通行做法（例如哈工大本科生院 2026-09-08 通知
「9月20日（星期日）、10月10日（星期六）正常执行第三周星期日和第六周星期六的
课程安排」），**调休日照常执行「当天星期几」的课表**，并不补别的星期的课。
本课表周六、周日没有课，所以这两天不需要补课。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Holiday:
    start: dt.date
    end: dt.date
    name: str = ""

    def dates(self) -> List[dt.date]:
        out, d = [], self.start
        while d <= self.end:
            out.append(d)
            d += dt.timedelta(days=1)
        return out

    def label(self) -> str:
        if self.start == self.end:
            return "%s（%d月%d日）" % (self.name, self.start.month, self.start.day)
        return "%s（%d月%d日–%d月%d日）" % (self.name, self.start.month, self.start.day,
                                        self.end.month, self.end.day)


@dataclass
class MakeupDay:
    """调休上班日。follow_weekday 为空表示「按当天星期几的课表执行」。"""
    date: dt.date
    follow_weekday: Optional[int] = None      # 0=周一 … 6=周日
    note: str = ""


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


# 西北工业大学 2026-2027 学年秋季学期
NWPU_2026_AUTUMN = dict(
    name="西工大 2026-2027 秋（国务院 2026 年节假日安排）",
    holidays=[
        Holiday(_d("2026-09-25"), _d("2026-09-27"), "中秋节"),
        Holiday(_d("2026-10-01"), _d("2026-10-07"), "国庆节"),
    ],
    makeup_days=[
        MakeupDay(_d("2026-09-20"), None, "国庆调休上班（按第3周周日课表，本课表周日无课）"),
        MakeupDay(_d("2026-10-10"), None, "国庆调休上班（按第6周周六课表，本课表周六无课）"),
    ],
)

PRESETS = {
    NWPU_2026_AUTUMN["name"]: NWPU_2026_AUTUMN,
}

DEFAULT_PRESET = NWPU_2026_AUTUMN["name"]

# 默认带上这份安排（针对本课表的学期）
DEFAULT_HOLIDAYS = list(NWPU_2026_AUTUMN["holidays"])
DEFAULT_MAKEUP_DAYS = list(NWPU_2026_AUTUMN["makeup_days"])


def get_preset(name):
    p = PRESETS.get(name, NWPU_2026_AUTUMN)
    return dict(holidays=list(p["holidays"]), makeup_days=list(p["makeup_days"]))


def describe(holidays, makeup_days) -> str:
    bits = [h.label() for h in holidays] or ["未设置"]
    txt = "放假：" + "、".join(bits)
    if makeup_days:
        txt += "；调休上班：" + "、".join(
            "%d月%d日" % (m.date.month, m.date.day) for m in makeup_days)
    return txt


# --------------------------------------------------------------- 外部节假日文件
WEEKDAY_CN = {"周一": 0, "星期一": 0, "周二": 1, "星期二": 1, "周三": 2, "星期三": 2,
              "周四": 3, "星期四": 3, "周五": 4, "星期五": 4, "周六": 5, "星期六": 5,
              "周日": 6, "星期日": 6, "周天": 6, "星期天": 6}

HOLIDAY_FILE_TEMPLATE = """\
# 节假日安排文件（纯文本，UTF-8）
# 面向以后学期的复用：不用改代码，写一个这样的文件即可，
#   命令行：--holidays-file 这个文件
#
# 每行格式（# 开头为注释，空行忽略）：
#   放假 <开始日期> <结束日期> <名称>     例：放假 2027-04-04 2027-04-06 清明节
#   放假 <日期> <名称>                    例：放假 2027-01-01 元旦
#   调休 <日期> [执行星期几]              例：调休 2027-04-25
#       省略「执行星期几」= 那天照常执行「当天星期几」的课表（高校通行做法）
#       写明如「周一」= 那天补周一的课
#
# 日期一律用 YYYY-MM-DD。

# 放假 2027-04-04 2027-04-06 清明节
# 放假 2027-05-01 2027-05-05 劳动节
# 放假 2027-06-18 2027-06-20 端午节
# 调休 2027-04-25
"""


class HolidayFileError(Exception):
    pass


def load_holiday_file(path: str):
    """读取外部节假日文件，返回 (holidays, makeup_days)。"""
    import io

    try:
        text = io.open(path, encoding="utf-8-sig").read()
    except OSError as exc:
        raise HolidayFileError("读不到节假日文件：%s" % exc) from exc

    holidays, makeup = [], []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.replace("\t", " ").split()
        kind = parts[0]
        try:
            if kind == "放假":
                if len(parts) == 3:
                    d = _d(parts[1])
                    holidays.append(Holiday(d, d, parts[2]))
                elif len(parts) == 4:
                    holidays.append(Holiday(_d(parts[1]), _d(parts[2]), parts[3]))
                else:
                    raise HolidayFileError(
                        "第 %d 行：放假 需要「日期 名称」或「开始 结束 名称」" % lineno)
            elif kind == "调休":
                if len(parts) == 2:
                    makeup.append(MakeupDay(_d(parts[1]), None, "调休上班"))
                elif len(parts) == 3:
                    if parts[2] not in WEEKDAY_CN:
                        raise HolidayFileError(
                            "第 %d 行：星期几要写成 周一…周日" % lineno)
                    makeup.append(MakeupDay(_d(parts[1]), WEEKDAY_CN[parts[2]],
                                            "调休上班，补%s的课" % parts[2]))
                else:
                    raise HolidayFileError(
                        "第 %d 行：调休 需要「日期」或「日期 星期几」" % lineno)
            else:
                raise HolidayFileError(
                    "第 %d 行：未知关键字「%s」，只能是 放假 或 调休" % (lineno, kind))
        except ValueError as exc:
            raise HolidayFileError(
                "第 %d 行：日期格式不对（要用 YYYY-MM-DD）：%s"
                % (lineno, raw.strip())) from exc

    return holidays, makeup
