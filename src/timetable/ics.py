# -*- coding: utf-8 -*-
"""把解析出来的课程记录生成为 iCalendar (.ics) 文件。

包含两套提醒：
  ① 每节课提前 N 分钟提醒（VALARM，覆盖每一次课）；
  ② 每晚固定时间汇总「第二天要上的课」——按天生成，因此内容对该周是准确的
     （而不是写死一段循环文字，那样到学期后半段会提示已经结课的课程）。

连排合并：教务系统常把一门占用「11-13 节」的课拆成「11-12 节」和「13-13 节」两行，
这里按 同一天 + 同一门课 + 同一教室 + 节次相连/重叠 合并成一个日程，避免日历里出现
两条紧挨着的重复条目。
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .holidays import Holiday, MakeupDay
from .parser import WEEKDAY_SHORT, ParseResult, Record, compress_weeks


# --------------------------------------------------------------------- config
@dataclass
class Config:
    week1_monday: dt.date = dt.date(2026, 8, 31)
    period_times: Dict[int, Tuple[str, str]] = field(default_factory=dict)
    class_alarm_min: Optional[int] = 20        # None 或 0 = 关闭课前提醒
    night_summary: bool = True
    night_hour: int = 22
    night_minute: int = 0
    night_duration_min: int = 15
    include_remark_courses: bool = False
    merge_mode: str = "all"                    # all | artifact | none
    calendar_name: str = "课表"
    extra_description: str = ""
    # 法定节假日：这些日期不上课（用 EXDATE 从循环日程里扣掉）
    holidays: List[Holiday] = field(default_factory=list)
    # 调休上班日：通常 follow_weekday 为空，表示「按当天星期几的课表执行」
    makeup_days: List[MakeupDay] = field(default_factory=list)
    # 放假第一天前一晚，是否发一条「明天放假」的提醒
    holiday_notice: bool = True
    # 只是给人看的溯源信息（写进日志/日历名旁边，不参与计算）
    layout_id: str = ""


class IcsError(Exception):
    pass


# --------------------------------------------------------------------- iCal 基础
def escape_text(value: str) -> str:
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    return (value.replace("\r\n", "\\n").replace("\n", "\\n")
                 .replace("\r", "\\n"))


def fold(line: str) -> List[str]:
    """按 RFC 5545 折行到 <=75 字节，且不切断多字节字符。"""
    if len(line.encode("utf-8")) <= 75:
        return [line]
    out, cur, limit = [], bytearray(), 75
    for ch in line:
        enc = ch.encode("utf-8")
        if len(cur) + len(enc) > limit:
            out.append(cur.decode("utf-8"))
            cur, limit = bytearray(), 74      # 续行以一个空格开头，占 1 字节
        cur += enc
    if cur:
        out.append(cur.decode("utf-8"))
    return [out[0]] + [" " + p for p in out[1:]]


_HHMM_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$")


def _stamp(d: dt.date, hm: str) -> str:
    """日期 + "HH:MM" -> iCalendar 时间戳。

    这里再校验一次是因为 `--period-times 1=8:30-9:15`（命令行）和界面「节次时间…」
    都能把任意字符串直接塞进 period_times，绕过了版式文件那层的规范化。
    不校验的话 `"8:30"` 会被拼成 `T83000` —— 一个畸形时间，日历端要么拒收、要么排到
    别处，而程序自己毫不知情。宁可在这里明确报错。
    """
    m = _HHMM_RE.match(str(hm))
    if not m:
        raise IcsError("时间格式不对：%r（要写成 HH:MM，例如 08:30）" % (hm,))
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise IcsError("时间不是合法时刻：%r" % (hm,))
    return "%04d%02d%02dT%02d%02d00" % (d.year, d.month, d.day, h, mi)


def _date_only(d: dt.date) -> str:
    return "%04d%02d%02d" % (d.year, d.month, d.day)


# --------------------------------------------------------------------- 连排合并
def merge_contiguous(records: List[Record], mode: str = "all") -> List[Record]:
    """合并同一天、同一门课、同一教室、节次相连或重叠的记录。

    mode:
      "all"      —— 只要相连就合并（默认）。例如「1-2节 + 3-4节」合成 08:30-12:10。
      "artifact" —— 只合并导出器拆分产生的碎片，即两个区间里至少有一个是单节
                    （如「11-12节」+「13-13节」其实是 11-13 节连排）。
                    「1-2节 + 3-4节」这种两段都是完整区间的，视为两次独立上课。
      "none"     —— 不合并，完全按 PDF 的行来。
    """
    if mode == "none":
        return sorted(copy.deepcopy(records),
                      key=lambda r: (r.day, r.pfrom if r.pfrom is not None else 999))

    # 深拷贝：合并会改写 pfrom/pto/segments，绝不能污染调用方传进来的记录
    # （否则在界面上换一种合并模式重新生成，会用上一次改过的数据算出错误结果）
    records = copy.deepcopy(records)

    def can_merge(a: Record, b: Record) -> bool:
        if a.pfrom is None or b.pfrom is None:   # 没有节次，谈不上「相连」
            return False
        if b.pfrom > a.pto + 1:               # 不相连
            return False
        if mode == "all":
            return True
        if mode == "artifact":
            return (a.pfrom == a.pto) or (b.pfrom == b.pto)
        return False

    buckets: Dict[Tuple[int, str, str], List[Record]] = {}
    order: List[Tuple[int, str, str]] = []
    for r in records:
        k = (r.day, r.course, r.room)
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(r)

    merged_all: List[Record] = []
    for k in order:
        items = sorted(buckets[k], key=lambda z: ((z.pfrom if z.pfrom is not None else 999),
                                                  (z.pto if z.pto is not None else 999)))
        acc: List[Record] = []
        for r in items:
            if acc and can_merge(acc[-1], r):
                last = acc[-1]
                if last.pfrom is not None and r.pfrom is not None:
                    last.pfrom = min(last.pfrom, r.pfrom)
                    last.pto = max(last.pto, r.pto)
                have = {(tuple(s.weeks), s.teacher) for s in last.segments}
                for s in r.segments:
                    if (tuple(s.weeks), s.teacher) not in have:
                        last.segments.append(s)
                for n in r.notes:
                    if n not in last.notes:
                        last.notes.append(n)
                # 合并 PDF 自带的上下课时刻：两段都有就取外包区间；
                # 只有一段有就别猜（拼出来的时刻没有依据），退回按节次查作息表，
                # 真缺时间会由 _check_periods 明确报错，不会静默算错。
                if last.clock and r.clock:
                    last.clock = (min(last.clock[0], r.clock[0]),
                                  max(last.clock[1], r.clock[1]))
                elif last.clock or r.clock:
                    last.clock = None
                continue
            acc.append(r)
        merged_all.extend(acc)
    merged_all.sort(key=lambda r: (r.day, r.pfrom if r.pfrom is not None else 999))
    return merged_all


def _check_periods(records: List[Record], period_times: Dict[int, Tuple[str, str]]):
    """校验「每节课的时间」是否齐全。

    规则：**节次时间以用户设定为准**。所以真正缺信息的是两类记录：
      1. 有节次、但作息表里没有那一节的时间；
      2. 没有节次、也没有从 PDF 反查出节次（只有 clock 或什么都没有）——
         这种没法落到用户的时间上，必须让用户先解决，不能替他猜。
    """
    missing = sorted({p for r in records if r.has_periods()
                      for p in range(r.pfrom, r.pto + 1) if p not in period_times})
    if missing:
        raise IcsError(
            "这些节次的上下课时间还没设置：%s\n"
            "  处理办法（任选）：\n"
            "    1) 界面点「节次时间…」补全（内置通用作息默认到第 %d 节，超出部分要自己填）；\n"
            "    2) 命令行 --period-times 13=21:45-22:30,14=22:40-23:25 这样指定；\n"
            "    3) 换一份自带该校作息的版式：--list-layouts 看有哪些。"
            % ("、".join(str(m) for m in missing),
               max(period_times) if period_times else 0))
    stuck = [r for r in records if not r.has_periods()]
    if stuck:
        names = "、".join(sorted({r.course for r in stuck})[:6])
        raise IcsError(
            "这些课程没能确定是第几节，所以无法套用你设定的节次时间：%s\n"
            "  它们的原因通常是：课表格子里只印了上下课时刻（如 800-935），"
            "而该时刻对不上你设定的任何一节（允许 %d 分钟误差）。\n"
            "  处理办法：核对「节次时间…」是否与本校作息一致，或在预览表里双击这几行手动改。"
            % (names, 10))


def class_times(r: Record, period_times: Dict[int, Tuple[str, str]]) -> Tuple[str, str]:
    """这门课几点上、几点下课。

    **以用户设定的节次时间为准**：课表上印的时刻只用来反查「这是第几节」
    （parser.resolve_periods），真正落到日历上的时间来自用户自己的作息表。
    这样同一份课表在不同学校的作息下都会落到用户配置的时间上。

    只有在「有 clock 但反查不出节次」这种异常情况下才退回 clock —— 而且这种记录
    在 _check_periods 里已经被拦住了，所以正常流程走不到这里。
    """
    if r.has_periods():
        return period_times[r.pfrom][0], period_times[r.pto][1]
    if r.clock:
        return r.clock
    raise IcsError("「%s」既没有节次也没有上下课时刻，无法排进日历。" % r.course)


def build_grid(records: List[Record]) -> Dict[Tuple[int, int], List[Record]]:
    """(周次, 星期) -> 当天课程列表"""
    grid: Dict[Tuple[int, int], List[Record]] = {}
    for r in records:
        for w in r.all_weeks():
            grid.setdefault((w, r.day), []).append(r)
    for k in grid:
        grid[k].sort(key=lambda z: z.pfrom if z.pfrom is not None else 999)
    return grid


# ----------------------------------------------------------------- 节假日处理
# 注意这里没有「某一天属于哪个假期」的查询函数：全项目只用得到「这一天放不放假」，
# 所以只有 _holiday_date_set 一个集合查询。（以前那个 holiday_of() 从没被调用过，删。）
def _holiday_date_set(holidays: List[Holiday]):
    out = set()
    for h in holidays:
        out.update(h.dates())
    return out


def build_day_map(records: List[Record], cfg: Config):
    """日期 -> [(课程记录, 周次)]，已扣除法定节假日、已计入调休上班日。

    这是「哪天到底上什么课」的唯一依据：课程日程（循环 + EXDATE）和每晚汇总
    都从它推出来，保证两者永远一致。
    """
    hol = _holiday_date_set(cfg.holidays)
    day_map: Dict[dt.date, List[Tuple[Record, int]]] = {}
    cancelled: List[Tuple[dt.date, Record, int]] = []

    for r in records:
        for w in r.all_weeks():
            d = cfg.week1_monday + dt.timedelta(weeks=w - 1, days=r.day)
            if d in hol:
                cancelled.append((d, r, w))
                continue
            day_map.setdefault(d, []).append((r, w))

    makeup_added: List[Tuple[dt.date, Record, int]] = []
    for m in cfg.makeup_days:
        own = m.date.weekday()
        wd = m.follow_weekday if m.follow_weekday is not None else own
        delta = (m.date - cfg.week1_monday).days
        if delta < 0:
            continue
        week = delta // 7 + 1
        # 「按当天星期几执行」的调休日**不需要**往 day_map 里加课：
        # 那天星期几的课本来就在 FREQ=WEEKLY 的循环里，日期正好就是这一天，再加一遍
        # 就成了同一时刻两条一模一样的课（原真实课表周末没课所以一直没暴露；一旦学校
        # 周末有课，节假日前一晚的「原定课程已取消」和夜间汇总都会重复列。
        # 只有「周六补周一的课」这种 follow_weekday 与当天星期几不同的才要显式补进去。
        if wd == own:
            continue
        for r in records:
            if r.day != wd or week not in r.all_weeks():
                continue
            if m.date in hol:
                continue
            day_map.setdefault(m.date, []).append((r, week))
            makeup_added.append((m.date, r, week))

    for d in day_map:
        day_map[d].sort(key=lambda z: ((z[0].pfrom if z[0].pfrom is not None else 999),
                                       z[0].course))
    return day_map, cancelled, makeup_added


def build_ics(result: ParseResult, cfg: Config):
    records = merge_contiguous(result.records, cfg.merge_mode)
    _check_periods(records, cfg.period_times)

    lines: List[str] = []
    add = lines.append
    add("BEGIN:VCALENDAR")
    add("VERSION:2.0")
    add("PRODID:-//NWPU Timetable Tool//CN")
    add("CALSCALE:GREGORIAN")
    add("X-WR-CALNAME:" + escape_text(cfg.calendar_name))
    add("X-WR-TIMEZONE:Asia/Shanghai")
    add("BEGIN:VTIMEZONE")
    add("TZID:Asia/Shanghai")
    add("BEGIN:STANDARD")
    add("DTSTART:19700101T000000")
    add("TZOFFSETFROM:+0800")
    add("TZOFFSETTO:+0800")
    add("TZNAME:CST")
    add("END:STANDARD")
    add("END:VTIMEZONE")

    stamp_now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def vevent(uid, summary, location, description, dstart, dend,
               rrule=None, alarm_min=None, all_day=False, exdates=None):
        add("BEGIN:VEVENT")
        add("UID:" + uid)
        add("DTSTAMP:" + stamp_now)
        if all_day:
            add("DTSTART;VALUE=DATE:" + dstart)
            add("DTEND;VALUE=DATE:" + dend)
        else:
            add("DTSTART;TZID=Asia/Shanghai:" + dstart)
            add("DTEND;TZID=Asia/Shanghai:" + dend)
        if rrule:
            add("RRULE:" + rrule)
        if exdates:
            # 放假停课：从循环里剔除这些日期（RFC 5545 的 EXDATE）
            add("EXDATE;TZID=Asia/Shanghai:" + ",".join(exdates))
        add("SUMMARY:" + escape_text(summary))
        if location:
            add("LOCATION:" + escape_text(location))
        if description:
            add("DESCRIPTION:" + escape_text(description))
        add("TRANSP:OPAQUE")
        if alarm_min is not None:
            add("BEGIN:VALARM")
            add("TRIGGER:" + ("PT0M" if alarm_min <= 0 else "-PT%dM" % alarm_min))
            add("ACTION:DISPLAY")
            add("DESCRIPTION:" + escape_text(summary))
            add("END:VALARM")
        add("END:VEVENT")

    # ---------- 先把「哪天到底上什么课」算出来 ----------
    day_map, cancelled, makeup_added = build_day_map(records, cfg)
    hol = _holiday_date_set(cfg.holidays)

    # ---------- ① 课程日程 ----------
    n_class = 0
    n_instances = 0
    for i, r in enumerate(records, 1):
        weeks = r.all_weeks()
        if not weeks:
            continue
        wf = weeks[0]
        s_hm, e_hm = class_times(r, cfg.period_times)
        first = cfg.week1_monday + dt.timedelta(weeks=wf - 1, days=r.day)

        desc = ["第%s周 ｜ %s %s %s-%s" % (compress_weeks(weeks), r.day_name,
                                          r.period_desc(), s_hm, e_hm),
                "教师：" + "；".join(r.teacher_desc()),
                "地点：" + r.room]
        if r.code:
            desc.append("课程代码：" + r.code)
        if r.notes:
            desc.append("备注：" + "；".join(r.notes))
        if cfg.extra_description:
            desc.append(cfg.extra_description)

        key = "%s|%s|%s|%s|%s" % (r.course, r.day_name, r.pfrom, r.pto, r.room)
        uid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        alarm = cfg.class_alarm_min if cfg.class_alarm_min else None

        # 周次连续 -> 一个循环日程；不连续（如单双周）-> 每段一个，保持每段都连续
        for j, (a, b) in enumerate(_split_runs(weeks)):
            d0 = cfg.week1_monday + dt.timedelta(weeks=a - 1, days=r.day)
            # 该段里落在法定节假日的日期，用 EXDATE 扣掉
            ex = [_stamp(cfg.week1_monday + dt.timedelta(weeks=w - 1, days=r.day), s_hm)
                  for w in range(a, b + 1)
                  if (cfg.week1_monday + dt.timedelta(weeks=w - 1, days=r.day)) in hol]
            total = b - a + 1
            if len(ex) >= total:
                continue                     # 整段都在放假，不用生成
            rrule = "FREQ=WEEKLY;COUNT=%d" % total if total > 1 else None
            suffix = "" if j == 0 else "-%d" % j
            vevent("%s%s@nwpu.timetable" % (uid, suffix), r.course, r.room,
                   "\n".join(desc), _stamp(d0, s_hm), _stamp(d0, e_hm),
                   rrule=rrule, alarm_min=alarm, exdates=ex or None)
            n_class += 1
        n_instances += len([w for w in weeks
                            if (cfg.week1_monday
                                + dt.timedelta(weeks=w - 1, days=r.day)) not in hol])

    # ---------- ①b 调休上班日补上的课（一般为空，取决于学校安排） ----------
    n_makeup = 0
    for (d, r, w) in makeup_added:
        s_hm, e_hm = class_times(r, cfg.period_times)
        desc = ["%d月%d日（%s）调休上班，按第%d周%s课表执行"
                % (d.month, d.day, WEEKDAY_SHORT[d.weekday()], w, r.day_name),
                "%s %s-%s ｜ %s" % (r.period_desc(), s_hm, e_hm, r.room),
                "教师：" + "；".join(r.teacher_desc())]
        vevent("makeup-%s-%s@nwpu.timetable"
               % (d.isoformat(), hashlib.sha1(r.key().encode("utf-8")).hexdigest()[:8]),
               r.course, r.room, "\n".join(desc), _stamp(d, s_hm), _stamp(d, e_hm),
               rrule=None, alarm_min=cfg.class_alarm_min if cfg.class_alarm_min else None)
        n_makeup += 1

    # ---------- ② 每晚汇总第二天的课 ----------
    n_night = 0
    if cfg.night_summary and day_map:
        # 放假第一天的前一晚，发一条「明天放假」
        notice_nights = {}
        if cfg.holiday_notice:
            for h in cfg.holidays:
                notice_nights[h.start - dt.timedelta(days=1)] = h

        events_for_night = {}
        for d in day_map:
            events_for_night.setdefault(d - dt.timedelta(days=1), d)
        for night, h in notice_nights.items():
            events_for_night.setdefault(night, None)      # None 表示这条是放假提醒

        for night in sorted(events_for_night):
            target = events_for_night[night]
            start = dt.datetime.combine(night, dt.time(cfg.night_hour, cfg.night_minute))
            end = start + dt.timedelta(minutes=cfg.night_duration_min)
            if target is None:
                h = notice_nights[night]
                # 放假第一天原本要上的课，顺手列出来提示「已取消」
                same_day = [(cr, cw) for (cd, cr, cw) in cancelled
                            if cd == night + dt.timedelta(days=1)]
                desc = ["明天放假：%s" % h.label()]
                if same_day:
                    desc.append("原定课程已取消：")
                    for (cr, cw) in same_day:
                        cs, ce = class_times(cr, cfg.period_times)
                        desc.append("  · %s %s-%s %s" % (cr.course, cs, ce, cr.room))
                desc.append("祝假期愉快！")
                summary = "明天放假（%s）" % h.name
                # UID 统一用 night- 前缀：所有「晚间提醒」都是一类，便于外部工具识别
                uid = "night-holiday-%s@nwpu.timetable" % h.start.isoformat()
            else:
                classes = day_map[target]
                items = []
                for idx, (r, w) in enumerate(classes, 1):
                    cs, ce = class_times(r, cfg.period_times)
                    items.append("%d. %s-%s %s ｜ %s%s"
                                 % (idx, cs, ce, r.course, r.room,
                                    ("｜" + r.teacher_for(w)) if r.teacher_for(w) else ""))
                desc = ["明天（%d月%d日 %s）共 %d 节课："
                        % (target.month, target.day, WEEKDAY_SHORT[target.weekday()],
                           len(classes))]
                desc.extend(items)
                summary = "明天（%s）%d 节课" % (WEEKDAY_SHORT[target.weekday()],
                                             len(classes))
                uid = "night-%s@nwpu.timetable" % target.isoformat()
            vevent(uid, summary, "", "\n".join(desc),
                   "%04d%02d%02dT%02d%02d00" % (start.year, start.month, start.day,
                                                start.hour, start.minute),
                   "%04d%02d%02dT%02d%02d00" % (end.year, end.month, end.day,
                                                end.hour, end.minute),
                   alarm_min=0)
            n_night += 1

    # ---------- ③ 备注里无固定时间的课程（可选，全天日程） ----------
    n_remark = 0
    if cfg.include_remark_courses:
        for rc in result.meta.remark_courses:
            wf, wt = rc["week_from"], rc["week_to"]
            first = cfg.week1_monday + dt.timedelta(weeks=wf - 1)
            desc = ["第%d~%d周 ｜ 无固定时间地点（课表备注中的课程）" % (wf, wt)]
            if rc.get("code"):
                desc.append("课程代码：" + rc["code"])
            key = "remark|%s|%d|%d" % (rc["name"], wf, wt)
            uid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
            vevent(uid + "@nwpu.timetable", rc["name"], "", "\n".join(desc),
                   _date_only(first), _date_only(first + dt.timedelta(days=1)),
                   rrule=("FREQ=WEEKLY;COUNT=%d" % (wt - wf + 1)) if wt > wf else None,
                   alarm_min=None, all_day=True)
            n_remark += 1

    add("END:VCALENDAR")

    out: List[str] = []
    for ln in lines:
        out.extend(fold(ln))
    text = "\r\n".join(out) + "\r\n"

    stats = dict(class_events=n_class, instances=n_instances, night_events=n_night,
                 remark_events=n_remark, records=len(records),
                 bytes=len(text.encode("utf-8")))
    return text, stats


def _split_runs(weeks: List[int]) -> List[Tuple[int, int]]:
    ws = sorted(set(weeks))
    runs, start, prev = [], ws[0], ws[0]
    for w in ws[1:]:
        if w == prev + 1:
            prev = w
            continue
        runs.append((start, prev))
        start = prev = w
    runs.append((start, prev))
    return runs


def sanity_check_ics(text: str) -> dict:
    """对生成的文本做结构自检（折行、CRLF、配对）。"""
    raw = text.encode("utf-8")
    lines = [l for l in raw.split(b"\r\n") if l != b""]
    over = [i for i, l in enumerate(lines) if len(l) > 75]
    return dict(
        crlf_only=(re.sub(rb"\r\n", b"", raw).count(b"\n") == 0),
        lines=len(lines),
        over_75=len(over),
        vevent=text.count("BEGIN:VEVENT"),
        vevent_end=text.count("END:VEVENT"),
        valarm=text.count("BEGIN:VALARM"),
        balanced=(text.count("BEGIN:VEVENT") == text.count("END:VEVENT")
                  and text.count("BEGIN:VCALENDAR") == text.count("END:VCALENDAR")),
    )
