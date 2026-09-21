# -*- coding: utf-8 -*-
"""配置自检：抓「换个学期继续用」时最容易犯、而且**不会报错只会算错**的几类问题。

为什么需要这个模块：
    工具本身与学期无关（周次 → 日期、循环日程、提醒都由「第 1 周周一 + 作息时间」
    推出来）。但这两个参数是**每个学期都必须手工更新**的，一旦忘了改，程序不会崩，
    只会安安静静地把所有课排到错误的日期上。这个模块负责把这类错误变成明确的警告。

主要检查：
    1. 「第 1 周周一」是不是周一；
    2. 它推出来的学期（如 2026-2027 秋）和 PDF 里写的学期对不对得上
       —— 这一条专门用来抓「忘了改日期」；
    3. 节假日预设是否落在本学期范围内
       —— 这一条专门用来抓「下学期沿用了上学期 / 上一年的节假日预设」；
    4. 课程用到的节次是否都设置了上下课时间；
    5. 放假日期是否落在有课的星期上（否则那些设置其实没起作用）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List

from .holidays import Holiday, MakeupDay
from .ics import Config
from .parser import ParseResult, WEEKDAY_SHORT


@dataclass
class Finding:
    level: str          # "error" | "warn" | "info"
    title: str
    detail: str = ""

    def __str__(self):
        tag = {"error": "错误", "warn": "警告", "info": "提示"}.get(self.level, self.level)
        return "[%s] %s%s" % (tag, self.title, ("　" + self.detail) if self.detail else "")


def expected_term(week1: dt.date) -> str:
    """由第 1 周周一推出学期名。8~12 月开学算秋季，1~7 月开学算春季。"""
    y = week1.year
    if week1.month >= 8:
        return "%d-%d 秋" % (y, y + 1)
    return "%d-%d 春" % (y - 1, y)


def term_of(week1: dt.date, n_weeks: int):
    """返回学期覆盖的日期区间与最后一天。"""
    last = week1 + dt.timedelta(weeks=n_weeks) - dt.timedelta(days=1)
    return week1, last


def diagnose(cfg: Config, result: ParseResult, n_weeks: int = None) -> List[Finding]:
    out: List[Finding] = []
    md = result.meta

    weeks = [w for r in result.records for w in r.all_weeks()]
    max_week = max(weeks) if weeks else 0
    if n_weeks is None:
        n_weeks = max_week

    # ---- 1. 第 1 周周一 ----
    if cfg.week1_monday.weekday() != 0:
        out.append(Finding(
            "error", "「第 1 周周一」%s 不是星期一" % cfg.week1_monday,
            "那天是%s。所有课程会整体偏移，请改成真正的周一。"
            % WEEKDAY_SHORT[cfg.week1_monday.weekday()]))

    # ---- 2. 学期是否与 PDF 一致（抓「忘了改日期」）----
    exp = expected_term(cfg.week1_monday)
    if md.term:
        if exp != md.term:
            out.append(Finding(
                "error",
                "日期和 PDF 里的学期对不上：PDF 是「%s」，但你填的第 1 周周一（%s）"
                "推出的是「%s」" % (md.term, cfg.week1_monday, exp),
                "这是换学期时最容易犯的错（沿用上一学期的日期）。请按校历改成本学期"
                "第 1 周的周一。"))
        else:
            out.append(Finding("info", "学期与日期一致：%s，第 1 周周一 %s"
                               % (md.term, cfg.week1_monday)))
    else:
        out.append(Finding(
            "warn", "没能从 PDF 里读出学期名，无法交叉验证日期",
            "请自行确认「第 1 周周一」是本学期第 1 周的周一（看校历上写着"
            "「X月X日 20XX级本科生开课」的那一行）。"))

    if max_week and not (10 <= max_week <= 30):
        out.append(Finding("warn", "解析出的最大周次是第 %d 周，看起来不寻常" % max_week))

    if not max_week:
        return out

    sem_start, sem_end = term_of(cfg.week1_monday, n_weeks)

    # ---- 3. 节假日预设是否落在本学期内（抓「沿用旧节假日」）----
    if cfg.holidays:
        hol_dates = [d for h in cfg.holidays for d in h.dates()]
        inside = [d for d in hol_dates if sem_start <= d <= sem_end]
        if not inside:
            out.append(Finding(
                "error",
                "启用了节假日处理，但预设的节假日（%s）全部落在本学期"
                "（%s ~ %s）之外" % ("、".join(h.label() for h in cfg.holidays),
                                   sem_start, sem_end),
                "说明这份预设不是本学期的 —— 内置预设是给 2026-2027 秋季学期用的。"
                "请更新 timetable/holidays.py，或用 --holidays-file 指定本学期的"
                "节假日文件；否则本学期的法定节假日不会被扣除。"))
        else:
            if len(inside) != len(hol_dates):
                out.append(Finding(
                    "info", "节假日里有 %d 天不在本学期范围内（已自动忽略）"
                    % (len(hol_dates) - len(inside))))
            out.append(Finding(
                "info", "节假日 %s" % "；".join(h.label() for h in cfg.holidays),
                "其中 %d 天落在本学期内，将扣除这些天的课。" % len(inside)))
    else:
        out.append(Finding(
            "warn", "没有启用节假日处理",
            "若本学期有法定节假日，放假期间的课不会被扣除（日历上会一直排着）。"
            "可用 --holidays-file 指定本学期的节假日安排。"))

    # ---- 4. 节次时间是否齐全 ----
    missing = sorted({p for r in result.records for p in range(r.pfrom, r.pto + 1)
                      if p not in cfg.period_times})
    if missing:
        out.append(Finding("error", "这些节次没有设置上下课时间：%s"
                           % "、".join(str(m) for m in missing),
                           "在界面上点「编辑节次时间…」补全，或换一个作息预设。"))

    # ---- 5. 放假日期是不是白设了 ----
    if cfg.holidays:
        class_days = {r.day for r in result.records}
        idle = []
        for h in cfg.holidays:
            ds = [d for d in h.dates() if sem_start <= d <= sem_end]
            if ds and not any(d.weekday() in class_days for d in ds):
                idle.append(h.name)
        if idle:
            out.append(Finding(
                "info", "%s 的放假日期本来就没有课" % "、".join(idle),
                "设置了也不会改变结果。"))

    # ---- 6. 调休日是否有课要补 ----
    for m in cfg.makeup_days:
        wd = m.follow_weekday if m.follow_weekday is not None else m.date.weekday()
        delta = (m.date - cfg.week1_monday).days
        if delta < 0 or m.date > sem_end:
            out.append(Finding("warn", "调休日 %s 不在本学期范围内" % m.date))
            continue
        week = delta // 7 + 1
        hit = [r for r in result.records if r.day == wd and week in r.all_weeks()]
        if hit:
            out.append(Finding(
                "info", "%d月%d日（%s）调休上班，按第%d周%s课表补 %d 节课"
                % (m.date.month, m.date.day, WEEKDAY_SHORT[m.date.weekday()],
                   week, WEEKDAY_SHORT[wd], len(hit))))
        else:
            out.append(Finding(
                "info", "%d月%d日（%s）调休上班，但第%d周%s本来就没有课，无需补课"
                % (m.date.month, m.date.day, WEEKDAY_SHORT[m.date.weekday()],
                   week, WEEKDAY_SHORT[wd])))

    return out


def has_blocking(findings: List[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
