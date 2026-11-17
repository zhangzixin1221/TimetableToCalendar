# -*- coding: utf-8 -*-
"""法定节假日的数据模型与文件格式。

这里**只放类型和解析**，不放任何具体年份的安排：
放假与调休的安排一律由 `holiday_fetch` 联网获取（按学年自动推该取哪几个自然年），
取不到就用缓存的，再取不到就明确告诉用户"本次不处理节假日"，而不是偷偷套一份
过期的预设 —— 后者正是本项目最警惕的"不报错、只算错"。

仍然保留纯文本文件格式（`load_holiday_file`），它有三个正当用途：
  1. 内网/离线机器，或用 `--holidays-url` 指一个内网镜像；
  2. 学校自己调课，跟国务院安排不一致时手改；
  3. 回归测试要一份确定的数据（见 fixtures/holidays-test.txt）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
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
#
# 什么时候需要它：
#   * 机器不能联网 / 校园网连不上数据源；
#   * 学校自己调课，跟国务院安排不一致；
#   * 想固定一份安排，不让它随数据源变化。
# 平时直接用 --fetch-holidays（默认就会联网取）即可，不必手写这个文件。
#
# 每行格式（# 开头为注释，空行忽略）：
#   放假 <开始日期> <结束日期> <名称>     例：放假 2027-04-04 2027-04-06 清明节
#   放假 <日期> <名称>                    例：放假 2027-01-01 元旦
#   调休 <日期> [执行星期几]              例：调休 2027-04-25
#       省略「执行星期几」= 那天照常执行「当天星期几」的课表（高校通行做法）
#       写明如「周一」= 那天补周一的课
#
# 日期一律用 YYYY-MM-DD。
# 联网取到之后也可以用 --save-holidays 导出一份，再照它改。
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